"""Tests for src.route.terrain (plan contract C4): FF2 fixture expectations and synthetic rule cases."""

from pathlib import Path

import pytest
from returns.pipeline import is_successful

from src.controller.csv_parser import CSVParser
from src.controller.node import Node
from src.route.params import MovementParams
from src.route.terrain import Link, LinkKind, Terrain, infer_terrain

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "fossil_foundry_2.csv"

ALIGN, REACH_H, REACH_V = 2, 19, 23

UP_LINKS = [
    ((113, 60), (118, 43)), ((48, 61), (48, 45)), ((105, 42), (101, 24)), ((99, 60), (97, 43)),
    ((48, 45), (49, 28)), ((74, 45), (79, 24)), ((97, 43), (101, 24)), ((105, 60), (105, 42)),
]
DOWN_LINKS = [
    ((49, 28), (48, 45)), ((101, 24), (105, 42)), ((105, 42), (105, 60)), ((48, 45), (48, 61)),
    ((42, 28), (37, 60)), ((79, 24), (74, 45)), ((109, 23), (105, 42)), ((118, 43), (113, 60)),
    ((97, 43), (99, 60)),
]
PLATFORMS = [
    [(37, 60), (48, 61), (65, 60), (82, 60), (99, 60), (105, 60), (113, 60), (129, 60)],
    [(48, 45), (58, 46), (74, 45)],
    [(42, 28), (49, 28), (66, 28), (79, 24), (90, 24), (101, 24), (109, 23)],
    [(97, 43), (105, 42), (118, 43)],
]


def _ff2_nodes():
    node_map = {}
    assert is_successful(CSVParser(CSV=FIXTURE).parseCSV([], node_map))
    return list(node_map.values())


def _ff2_terrain(nodes=None, params=None):
    nodes = nodes if nodes is not None else _ff2_nodes()
    result = infer_terrain(nodes, ALIGN, REACH_H, REACH_V, params or MovementParams())
    assert is_successful(result)
    return result.unwrap()


def _pos(link):
    return link.src.minimap_position, link.dst.minimap_position


def _node(x, y, can_fall=False, left_edge=False, right_edge=False):
    return Node("blue_node", (x, y), can_fall, False, left_edge, right_edge)


def _links(nodes, reach_h=REACH_H, reach_v=REACH_V, kinds=None):
    result = infer_terrain(nodes, ALIGN, reach_h, reach_v, MovementParams())
    assert is_successful(result)
    terrain = result.unwrap()
    return [l for l in terrain.links if kinds is None or l.kind in kinds]


def test_ff2_slope_links_are_exactly_the_28_24_pair():
    terrain = _ff2_terrain()
    slopes = [_pos(l) for l in terrain.links if l.kind == LinkKind.SLOPE]
    assert sorted(slopes) == sorted([((66, 28), (79, 24)), ((79, 24), (66, 28))])


def test_ff2_vertical_links_are_exactly_the_17_expected():
    terrain = _ff2_terrain()
    ups = [_pos(l) for l in terrain.links if l.kind == LinkKind.UP]
    downs = [_pos(l) for l in terrain.links if l.kind == LinkKind.DOWN]
    assert len(ups) == 8 and len(downs) == 9
    assert sorted(ups) == sorted(UP_LINKS)
    assert sorted(downs) == sorted(DOWN_LINKS)


def test_ff2_blocked_drop_through_middle_platform_is_not_emitted():
    terrain = _ff2_terrain()
    assert ((66, 28), (65, 60)) not in [_pos(l) for l in terrain.links]


def test_ff2_platforms_in_order_and_platform_index():
    nodes = _ff2_nodes()
    terrain = _ff2_terrain(nodes)
    assert [[n.minimap_position for n in p] for p in terrain.platforms] == PLATFORMS
    node_79_24 = next(n for n in nodes if n.minimap_position == (79, 24))
    assert terrain.platform_index(node_79_24) == 2


def test_platform_index_raises_key_error_for_unknown_node():
    terrain = _ff2_terrain()
    with pytest.raises(KeyError):
        terrain.platform_index(_node(1, 1))


def test_ff2_no_level_or_slope_link_spans_74_45_to_97_43():
    terrain = _ff2_terrain()
    horizontal = {frozenset(_pos(l)) for l in terrain.links if l.kind in (LinkKind.LEVEL, LinkKind.SLOPE)}
    assert frozenset({(74, 45), (97, 43)}) not in horizontal


def test_ff2_links_list_walk_links_before_vertical_links():
    kinds = [l.kind for l in _ff2_terrain().links]
    first_vertical = next(i for i, k in enumerate(kinds) if k in (LinkKind.UP, LinkKind.DOWN))
    assert all(k in (LinkKind.LEVEL, LinkKind.SLOPE) for k in kinds[:first_vertical])
    assert all(k in (LinkKind.UP, LinkKind.DOWN) for k in kinds[first_vertical:])


def test_step_of_dy_7_over_dx_20_is_not_a_slope():
    # reach widened to 30 so the dy rule (7 > 6), not the reach, is what rejects it
    assert _links([_node(0, 0), _node(20, 7)], reach_h=30, kinds=(LinkKind.SLOPE, LinkKind.LEVEL)) == []


def test_step_of_dy_5_over_dx_8_is_not_a_slope_for_grade():
    assert _links([_node(0, 0), _node(8, 5)], kinds=(LinkKind.SLOPE, LinkKind.LEVEL)) == []


def test_step_of_dy_5_over_dx_10_is_a_slope_both_ways():
    a, b = _node(0, 0), _node(10, 5)
    links = _links([a, b], kinds=(LinkKind.SLOPE, LinkKind.LEVEL))
    assert [(l.src, l.dst, l.kind) for l in links] == [(a, b, LinkKind.SLOPE), (b, a, LinkKind.SLOPE)]


def test_aligned_neighbour_is_a_level_link():
    a, b = _node(0, 0), _node(10, 2)
    links = _links([a, b], kinds=(LinkKind.SLOPE, LinkKind.LEVEL))
    assert [l.kind for l in links] == [LinkKind.LEVEL, LinkKind.LEVEL]


def test_horizontal_link_picks_nearest_and_ties_go_to_earliest():
    a, far, near1, near2 = _node(0, 0), _node(15, 0), _node(5, 0), _node(-5, 0)
    # right of a: near1 (5) beats far (15); left of a: only near2. Tie check: equal |dx| on one side.
    links = [l for l in _links([a, far, near1, near2]) if l.src is a]
    assert [(l.dst, l.kind) for l in links] == [(near2, LinkKind.LEVEL), (near1, LinkKind.LEVEL)]
    t1, t2 = _node(5, 0), _node(5, 1)
    links = [l for l in _links([_node(0, 0), t1, t2]) if l.src.minimap_position == (0, 0)]
    assert [l.dst for l in links] == [t1]


def test_drop_of_dy_30_from_non_falling_node_is_not_linked_but_from_falling_node_is_down():
    top_no, bottom = _node(0, 0, can_fall=False), _node(2, 30)
    assert _links([top_no, bottom], kinds=(LinkKind.DOWN,)) == []
    top_yes = _node(0, 0, can_fall=True)
    links = _links([top_yes, bottom], kinds=(LinkKind.DOWN,))
    assert [(l.src, l.dst) for l in links] == [(top_yes, bottom)]


def test_target_flagged_right_platform_edge_to_the_left_of_source_is_rejected():
    src = _node(60, 10)
    plain = [src, _node(58, 30)]
    assert len(_links(plain, kinds=(LinkKind.DOWN,))) == 1
    flagged = [src, _node(58, 30, right_edge=True)]
    assert _links(flagged, kinds=(LinkKind.DOWN,)) == []


def test_empty_nodes_is_failure():
    assert not is_successful(infer_terrain([], ALIGN, REACH_H, REACH_V, MovementParams()))


@pytest.mark.parametrize("bad", [(1.5, 2), (1, 2, 3), None, (1,), "ab"])
def test_non_int_pair_position_is_failure(bad):
    nodes = [_node(0, 0)]
    nodes.append(Node("blue_node", bad, False, False, False, False))
    assert not is_successful(infer_terrain(nodes, ALIGN, REACH_H, REACH_V, MovementParams()))


def test_infer_terrain_is_deterministic():
    nodes = _ff2_nodes()
    first, second = _ff2_terrain(nodes), _ff2_terrain(nodes)
    assert first.links == second.links
    assert first.platforms == second.platforms
    assert isinstance(first, Terrain) and all(isinstance(l, Link) for l in first.links)
