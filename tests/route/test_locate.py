"""Tests for src.route.locate (plan contract C6): FF2 attach cases for rules A1-A4, plus synthetic tie-break cases."""

from pathlib import Path

import pytest
from returns.pipeline import is_successful

from src.classes.classes import Kanna
from src.controller.controller import MapleAgent
from src.controller.node import Node
from src.route.locate import Anchor, attach
from src.route.params import MovementParams
from src.route.route_graph import RouteGraph

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "fossil_foundry_2.csv"


@pytest.fixture(scope="module")
def ff2():
    agent = MapleAgent(characterClass=Kanna())
    assert is_successful(agent.loadMap(FIXTURE))
    result = RouteGraph.build(agent.pathGraph, MovementParams())
    assert is_successful(result)
    return agent, result.unwrap()


def _node(agent, xy):
    node = next((n for n in agent.node_map.values() if n.minimap_position == xy), None)
    assert node is not None, f"no node at {xy}"
    return node


def _attach(rg, pos):
    result = attach(rg, pos)
    assert is_successful(result), result
    return result.unwrap()


def _synthetic(points):
    """A RouteGraph over nodes at `points` (in this node_map order), built the way the runtime builds it."""
    nodes = {i: Node("blue_node", xy, False, False, False, False) for i, xy in enumerate(points)}
    pg = MapleAgent(characterClass=Kanna()).buildPathGraphFor(nodes).unwrap()
    assert is_successful(pg.buildGraph())
    rg = RouteGraph.build(pg, MovementParams())
    assert is_successful(rg)
    return nodes, rg.unwrap()


# ---- FF2 (plan Task B6 tests) ----

def test_exact_node_position_attaches_to_the_node_a1(ff2):
    agent, rg = ff2
    n = _node(agent, (101, 24))
    assert _attach(rg, (101, 24)) == Anchor(node=n, platform=2, left=None, right=None)


def test_position_within_align_of_a_node_attaches_to_it_a1(ff2):
    agent, rg = ff2
    n = _node(agent, (101, 24))
    assert _attach(rg, (102, 25)) == Anchor(node=n, platform=2, left=None, right=None)


def test_position_on_the_slope_attaches_to_the_segment_a2(ff2):
    agent, rg = ff2
    # y_interp(72) on (66,28)-(79,24) is 26.15
    anchor = _attach(rg, (72, 26))
    assert anchor == Anchor(None, 2, _node(agent, (66, 28)), _node(agent, (79, 24)))


def test_position_between_two_nodes_attaches_to_the_segment_a2(ff2):
    agent, rg = ff2
    # y_interp(102) on (97,43)-(105,42) is 42.375
    anchor = _attach(rg, (102, 42))
    assert anchor == Anchor(None, 3, _node(agent, (97, 43)), _node(agent, (105, 42)))


def test_position_two_pixels_from_a_node_attaches_to_the_node_a1(ff2):
    agent, rg = ff2
    n = _node(agent, (105, 42))
    anchor = _attach(rg, (103, 42))
    assert anchor.node is n
    assert anchor.platform == 3
    assert anchor.left is None and anchor.right is None


def test_position_beyond_the_right_end_of_a_platform_a3(ff2):
    agent, rg = ff2
    anchor = _attach(rg, (132, 60))
    assert anchor == Anchor(None, 0, _node(agent, (129, 60)), None)


def test_position_beyond_the_left_end_within_reach_a3(ff2):
    agent, rg = ff2
    anchor = _attach(rg, (20, 60))  # 17 px left of (37,60), reach 19
    assert anchor == Anchor(None, 0, None, _node(agent, (37, 60)))


def test_position_beyond_reach_of_the_left_end_is_off_graph(ff2):
    _, rg = ff2
    result = attach(rg, (15, 60))  # 22 px left of (37,60), reach 19
    assert not is_successful(result)
    assert result.failure().startswith("off-graph")


def test_mid_air_position_between_platforms_is_off_graph(ff2):
    _, rg = ff2
    result = attach(rg, (86, 37))
    assert not is_successful(result)
    assert result.failure().startswith("off-graph")
    assert "(86, 37)" in result.failure()


def test_position_below_the_slope_beyond_attach_y_is_not_on_the_platform(ff2):
    _, rg = ff2
    # y_interp(72) is 26.15, so (72,30) is 3.85 px below the slope (attach_y_px is 3)
    result = attach(rg, (72, 30))
    assert not is_successful(result)
    assert result.failure().startswith("off-graph")


def test_position_just_inside_attach_y_below_the_slope_attaches_a2(ff2):
    agent, rg = ff2
    # y_interp(72) is 26.154; (72,29) is 2.85 below, inside the allowance (attach_y_px is 3)
    anchor = _attach(rg, (72, 29))
    assert anchor == Anchor(None, 2, _node(agent, (66, 28)), _node(agent, (79, 24)))


# ---- tie-breaks (contract rules A1-A3) ----

def test_a1_picks_the_nearest_node_by_euclidean_distance():
    nodes, rg = _synthetic([(12, 10), (10, 10)])
    anchor = _attach(rg, (10, 11))
    assert anchor.node is nodes[1]  # distance 1, against 2.2 for the other node


def test_a1_distance_is_euclidean_not_manhattan():
    # from (10,10): (12,10) is 2.0 away, (11,11) is 1.41 away; by Manhattan distance both are 2
    nodes, rg = _synthetic([(12, 10), (11, 11)])
    assert _attach(rg, (10, 10)).node is nodes[1]


def test_a1_distance_tie_goes_to_the_earlier_node_in_node_map_order():
    nodes, rg = _synthetic([(12, 10), (10, 10)])
    assert _attach(rg, (11, 10)).node is nodes[0]
    nodes, rg = _synthetic([(10, 10), (12, 10)])
    assert _attach(rg, (11, 10)).node is nodes[0]


def test_a2_equal_distance_at_a_shared_node_picks_the_leftmost_segment():
    nodes, rg = _synthetic([(0, 10), (10, 10), (20, 10)])
    # 3 px below the middle node: A1 needs <= 2, both segments reach it at distance 3
    anchor = _attach(rg, (10, 13))
    assert anchor == Anchor(None, 0, nodes[0], nodes[1])


def test_a2_equal_distance_across_platforms_picks_the_lower_platform_index():
    # the platform at y=16 comes first in node_map order, so it is platform 0
    nodes, rg = _synthetic([(0, 16), (10, 16), (20, 16), (0, 10), (10, 10), (20, 10)])
    assert len(rg.terrain.platforms) == 2
    anchor = _attach(rg, (10, 13))
    assert anchor == Anchor(None, 0, nodes[0], nodes[1])


def test_a2_prefers_the_smaller_vertical_offset_over_the_lower_platform_index():
    # y=17 layer first (platform 0), y=12 layer second (platform 1); 5 px apart and 8 px node spacing, so unlinked
    nodes, rg = _synthetic([(0, 17), (8, 17), (16, 17), (0, 12), (8, 12), (16, 12)])
    assert len(rg.terrain.platforms) == 2
    anchor = _attach(rg, (4, 14))  # 2 px below the y=12 layer, 3 px above the y=17 layer
    assert anchor == Anchor(None, 1, nodes[3], nodes[4])


def test_a3_equal_distance_picks_the_lower_platform_index():
    nodes, rg = _synthetic([(30, 10), (10, 10)])  # 20 apart: beyond reach, two one-node platforms
    assert len(rg.terrain.platforms) == 2
    anchor = _attach(rg, (20, 10))
    assert anchor == Anchor(None, 0, None, nodes[0])


def test_a3_smallest_distance_wins_over_platform_index():
    nodes, rg = _synthetic([(30, 10), (10, 10)])
    anchor = _attach(rg, (18, 10))  # 8 px right of (10,10), 12 px left of (30,10)
    assert anchor == Anchor(None, 1, nodes[1], None)


def test_a3_vertical_offset_beyond_attach_y_is_off_graph():
    _, rg = _synthetic([(30, 10), (10, 10)])
    assert not is_successful(attach(rg, (20, 14)))


def test_same_x_segment_in_a_platform_does_not_break_attach():
    # (10,10) and (10,16) share x and sit on one platform through (0,13) (a slope to each)
    nodes, rg = _synthetic([(0, 13), (10, 10), (10, 16)])
    assert len(rg.terrain.platforms) == 1
    anchor = _attach(rg, (10, 13))
    assert anchor.platform == 0
