"""Tests for the teleport links (any-map plan Task 2, Hong's anymap182-1, H111): from every platform end that stops a
sideways teleport (marked, or an end of a gap), the walker's route step asks `side_landing` where a teleport from that end
lands; when that is another platform and no edge goes yet from the end node to a node of it, the step adds a GAP edge to
that platform's near node, so a gap whose two sides differ by 3 or 4 px in height (more than PathGraph's 2 px allowance,
within `side_teleport_land_dy_px`) is crossed. The far end becomes a stop and gets the link back (ANYMAP194-D3).

Map `step(dy)`: platform A is (24, 50) and (40, 50), with (40, 50) marked as a right end; platform B is (50, 50 + dy),
marked as a left end, and (66, 50 + dy). `step(2)` is test_platform_ends' map M."""

from pathlib import Path

import numpy as np
import pytest
from returns.pipeline import is_successful
from test_gap_cap import FAR_NODE, decide_result
from test_platform_ends import TP_R, Built, Row, decide, walk
from test_route_preview import _first_hop_problems

from src.classes.classes import Kanna
from src.controller.controller import MapleAgent
from src.input.protocol import Key
from src.route.params import MovementParams
from src.route.planner import next_move
from src.route.route_graph import EdgeKind, RouteGraph

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def step(dy: int, *, a_right: bool = True, b_left: bool = True) -> list[Row]:
    return [(24, 50, False, False), (40, 50, False, a_right), (50, 50 + dy, b_left, False), (66, 50 + dy, False, False)]


def test_a_2_px_step_keeps_the_pathgraph_edge_and_adds_nothing():
    built = Built(step(2))  # PathGraph links (40, 50)-(50, 52) itself (dy 2 <= its 2 px allowance)
    assert built.rg.teleport_links == frozenset()
    assert built.kind(40, 50) == EdgeKind.GAP and built.kind(50, 40) == EdgeKind.GAP


@pytest.mark.parametrize("dy", [3, 4, -3, -4])
def test_a_3_or_4_px_step_gets_a_teleport_link_both_ways(dy):
    built = Built(step(dy))
    n40, n50 = built.n[40], built.n[50]
    assert built.rg.teleport_links == {(n40, n50), (n50, n40)}
    assert built.kind(40, 50) == EdgeKind.GAP and built.kind(50, 40) == EdgeKind.GAP
    # launch 34 lands at 53 (9 px walked), launch 56 lands at 37 (9 px walked): 9 / 12.5 + 0.55 each way
    assert built.cost(40, 50) == pytest.approx(1.27, abs=0.005) and built.cost(50, 40) == pytest.approx(1.27, abs=0.005)
    assert built.rg.split_ends == {(built.p(24), +1), (built.p(50), -1)}


@pytest.mark.parametrize("dy", [3, 4])
def test_the_planner_crosses_a_3_or_4_px_step(dy):  # before Task 2: "no path from (24, 50) to (66, 5x)"
    built = Built(step(dy))
    assert decide(built, (24, 50), 66).move == walk(Key.RIGHT, 800.0)  # to the launch 34
    assert decide(built, (34, 50), 66).move == TP_R
    assert decide(built, (66, 50 + dy), 24).move == walk(Key.LEFT, 800.0)  # to the launch 56


def test_a_5_px_step_gets_no_link():  # Hong's rule: more than 4 px off, the teleport stops at the end
    built = Built(step(5))
    assert built.rg.teleport_links == frozenset()
    assert decide_result(built, (24, 50), 66) == "no path from (24, 50) to (66, 55)"


@pytest.mark.parametrize("a_right, b_left", [(True, False), (False, True)])
def test_one_marked_side_is_enough(a_right, b_left):  # a 3 px step with one facing end marked: both links (D3, as N1)
    built = Built(step(3, a_right=a_right, b_left=b_left))
    n40, n50 = built.n[40], built.n[50]
    assert built.p(40) != built.p(50)  # the one mark splits the terrain
    assert built.rg.teleport_links == {(n40, n50), (n50, n40)}
    assert built.kind(40, 50) == EdgeKind.GAP and built.kind(50, 40) == EdgeKind.GAP
    assert built.rg.split_ends == {(built.p(24), +1), (built.p(50), -1)}
    assert decide(built, (34, 50), 66).move == TP_R


def test_no_mark_no_link():  # an unmarked end stops no teleport (Review Focus 3): the step is one sloped platform
    built = Built(step(3, a_right=False, b_left=False))
    assert len(built.terrain.platforms) == 1
    assert built.rg.teleport_links == frozenset() and built.rg.split_ends == frozenset()
    assert all(kind != EdgeKind.GAP for _, _, kind in built.rg.graph.edges(data="kind"))
    assert built.kind(40, 50) == EdgeKind.SLOPE  # terrain's link, as before Task 2


def test_the_height_range_is_the_setting():  # the links follow `side_teleport_land_dy_px`, no 4 written anywhere
    narrow = Built(step(3), MovementParams(side_teleport_land_dy_px=2))
    assert narrow.rg.teleport_links == frozenset()
    assert decide_result(narrow, (24, 50), 66) == "no path from (24, 50) to (66, 53)"
    wide = Built(step(5), MovementParams(side_teleport_land_dy_px=5))
    assert wide.rg.teleport_links == {(wide.n[40], wide.n[50]), (wide.n[50], wide.n[40])}
    assert wide.kind(40, 50) == EdgeKind.GAP and wide.kind(50, 40) == EdgeKind.GAP


# A (24, 50), (40, 50) marked right; B (48, 53) marked left, (50, 53) marked right; C (58, 53), (74, 53). From A's end both
# B (8 px on) and C (18 px on) start within the reach and 3 px of its height; PathGraph links B and C (50 <-> 58, GAP) and
# neither to A
NEAR2: list[Row] = [
    (24, 50, False, False),
    (40, 50, False, True),
    (48, 53, True, False),
    (50, 53, False, True),
    (58, 53, False, False),
    (74, 53, False, False),
]


def test_the_nearest_platform_wins():
    built = Built(NEAR2)
    a, b = built.p(24), built.p(48)
    assert built.rg.side_landing(a, 40, +1)[1] == b
    assert built.rg.teleport_links == {(built.n[40], built.n[48]), (built.n[48], built.n[40])}  # one link, to the nearer
    assert not built.rg.graph.has_edge(built.n[40], built.n[58])
    # launch 32 lands at 50 (B's far end, the full reach clamped): 8 + 2 px walked; back: launch 50 lands at 31, 2 + 9 px
    assert built.cost(40, 48) == pytest.approx(10 / 12.5 + 0.55) and built.cost(48, 40) == pytest.approx(11 / 12.5 + 0.55)
    assert built.kind(50, 58) == EdgeKind.GAP and built.kind(58, 50) == EdgeKind.GAP  # PathGraph's gap, untouched


# A (24, 50), (40, 50) marked right; B (57, 53) marked left, (73, 53): B starts 17 px past A's end, 3 px lower
WIDE17: list[Row] = [(24, 50, False, False), (40, 50, False, True), (57, 53, True, False), (73, 53, False, False)]


def test_a_wide_step_is_capped_by_task_1():  # the landing rule links it; the gap cap (H112) leaves no edge
    built = Built(WIDE17)
    n40, n57 = built.n[40], built.n[57]
    assert built.rg.teleport_links == {(n40, n57), (n57, n40)}
    assert not built.rg.graph.has_edge(n40, n57) and not built.rg.graph.has_edge(n57, n40)
    assert built.rg.gap_launch(n40, n57) is None and built.rg.gap_launch(n57, n40) is None
    assert built.rg.split_ends == {(built.p(24), +1), (built.p(57), -1)}  # the ends still stop a teleport
    assert decide_result(built, (24, 50), 73) == "no path from (24, 50) to (73, 53)"


def test_an_existing_edge_to_the_landing_platform_blocks_the_link():  # Review Focus 4
    """test_gap_cap's FAR_NODE: PathGraph links A's end (40, 50) to B's far node (58, 50), so the teleport from A's end,
    which lands on B, adds nothing and that edge stays as it was. From B's left end (50, 53) no edge goes to A, so the
    link back from it is added, to A's near node (40, 50): launch 56, landing 37, 6 + 3 px walked."""
    built = Built([*FAR_NODE])
    n40, n50 = built.n[40], built.n[50]
    assert built.rg.side_landing(built.p(40), 40, +1)[1] == built.p(50)
    assert built.rg.teleport_links == {(n50, n40)}
    assert not built.rg.graph.has_edge(n40, n50)
    assert built.kind(40, 58) == EdgeKind.GAP and built.cost(40, 58) == pytest.approx(0.55)  # as before Task 2
    assert built.kind(58, 40) == EdgeKind.GAP and built.cost(58, 40) == pytest.approx(0.95)
    assert built.kind(50, 40) == EdgeKind.GAP and built.cost(50, 40) == pytest.approx(9 / 12.5 + 0.55)


@pytest.mark.parametrize(
    "name, edge_count, gap_edges",
    # the edge counts and GAP edges as before Task 2 (probed at 30a5a5c); neither map has a 3 or 4 px step at a mark
    [("fossil_foundry_2.csv", 51, set()), ("ff2_marked.csv", 82, {((78, 45), (88, 43)), ((88, 43), (78, 45))})],
)
def test_the_fixtures_get_no_link(name, edge_count, gap_edges):  # Review Focus 1
    agent = MapleAgent(characterClass=Kanna())
    assert is_successful(agent.loadMap(FIXTURES / name))
    assert agent.pathGraph is not None
    rg = RouteGraph.build(agent.pathGraph, MovementParams()).unwrap()
    assert rg.teleport_links == frozenset()
    gaps = {(u.minimap_position, v.minimap_position) for u, v, kind in rg.graph.edges(data="kind") if kind == EdgeKind.GAP}
    assert gaps == gap_edges
    assert rg.graph.number_of_edges() == edge_count


@pytest.mark.parametrize("dy", [3, 4])
def test_preview_matches_next_move_on_a_stepped_map(dy):  # as test_preview_matches_next_move_on_marked_map on map M
    built = Built(step(dy))
    problems: list[str] = []
    for x, y, targets in [(x, 50, (50, 66)) for x in range(24, 41)] + [(x, 50 + dy, (24, 40)) for x in range(50, 67)]:
        for target in targets:
            # without a crossing, preview and next_move both fail with "no path" and `_first_hop_problems` calls that agreement
            # (ANYMAP198-R2 m1): so the test also needs next_move to succeed, which it does only through the teleport link
            assert is_successful(next_move(built.rg, (x, y), built.n[target], np.random.default_rng(0))), (x, y, target)
            problems += _first_hop_problems(built.rg, (x, y), built.n[target])
    assert problems == []
