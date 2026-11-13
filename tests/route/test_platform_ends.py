"""Tests for the platform-end marks (walk-fluid plan Task 2, Hong's QB282 rule for item 4a).

The walker reads the map's platform-end marks to know where a platform ends. A mark on either facing end splits the
terrain there, a sideways teleport across such a split is a GAP edge, and `RouteGraph.side_landing` is the one place
that says where a sideways teleport stops: at the end of the platform it is launched from, or, when another platform
starts within the reach at about the same height (`side_teleport_land_dy_px`), on that platform.

Map M (Kanna, defaults; neighbours 16 px apart, inside the 19 px reach that terrain and PathGraph links need):
platform A is (24, 50) and (40, 50), with (40, 50) marked as a right end; platform B is (50, 52), marked as a left
end, and (66, 52). M0 has the same four nodes with no marks, MN with every mark None (the `Node` default)."""

from pathlib import Path

import networkx as nx
import numpy as np
import pytest
from returns.pipeline import is_successful
from test_route_preview import _first_hop_problems

from src.classes.classes import Kanna
from src.controller.controller import MapleAgent
from src.controller.node import Node
from src.input.protocol import Key
from src.route import planner
from src.route.params import MovementParams, validate
from src.route.planner import Move, MoveKind, next_move
from src.route.route_graph import EdgeKind, RouteGraph, _side_kind, horizontal_cost_s, is_marked_end
from src.route.route_preview import preview
from src.route.terrain import Terrain

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "fossil_foundry_2.csv"

# Platform sizes of tests/fixtures/fossil_foundry_2.csv (it has no marks), computed at 026d636 and unchanged by Task 1:
# its terrain, platforms and edge count must stay what they are whatever the marks code does (Review Focus 1)
BASELINE_PLATFORM_SIZES = [8, 3, 7, 3]
BASELINE_EDGE_COUNT = 51

Row = tuple[int, int, bool | None, bool | None]  # x, y, left-end mark, right-end mark


def _map(a_right: bool, b_left: bool, *, off: bool | None = False, b_y: int = 52, extra: tuple[Row, ...] = ()) -> list[Row]:
    """M with the marks asked for (`off` is what an unmarked node holds: False from a CSV, None from `Node()`)."""
    return [
        (24, 50, off, off),
        (40, 50, off, True if a_right else off),
        (50, b_y, True if b_left else off, off),
        (66, b_y, off, off),
        *extra,
    ]


M = _map(True, True)
M0 = _map(False, False)
MN = _map(False, False, off=None)
M_right_mark_only = _map(True, False)
M_left_mark_only = _map(False, True)


def M_b_at(y: int) -> list[Row]:
    return _map(True, True, b_y=y)


class Built:
    """A map built the way the runtime builds it (PathGraph, then RouteGraph). `n[x]` is the node at x."""

    def __init__(self, rows: list[Row], params: MovementParams | None = None) -> None:
        self.nodes = [Node("blue_node", (x, y), False, False, left, right) for x, y, left, right in rows]
        self.n = {node.minimap_position[0]: node for node in self.nodes}  # by x: a map with two nodes at one x uses `nodes`
        path_graph = MapleAgent(characterClass=Kanna()).buildPathGraphFor(dict(enumerate(self.nodes))).unwrap()
        assert is_successful(path_graph.buildGraph())
        built = RouteGraph.build(path_graph, MovementParams() if params is None else params)
        assert is_successful(built)
        self.rg = built.unwrap()
        self.terrain = self.rg.terrain

    def p(self, x: int) -> int:
        """Index of the platform of the node at x."""
        return self.terrain.platform_index(self.n[x])

    def kind(self, x_from: int, x_to: int) -> EdgeKind:
        return self.rg.graph.edges[self.n[x_from], self.n[x_to]]["kind"]

    def cost(self, x_from: int, x_to: int) -> float:
        return self.rg.graph.edges[self.n[x_from], self.n[x_to]]["cost_s"]


def test_the_setting_defaults_to_4_and_is_validated():
    assert MovementParams().side_teleport_land_dy_px == 4
    assert is_successful(validate(MovementParams()))
    assert not is_successful(validate(MovementParams(side_teleport_land_dy_px=0)))


def test_is_marked_end_reads_the_mark_for_its_side():
    assert is_marked_end(Node("blue_node", (1, 1), False, False, False, True), +1)
    assert not is_marked_end(Node("blue_node", (1, 1), False, False, False, True), -1)
    assert is_marked_end(Node("blue_node", (1, 1), False, False, True, False), -1)
    assert not is_marked_end(Node("blue_node", (1, 1)), +1) and not is_marked_end(Node("blue_node", (1, 1)), -1)  # None


def test_marks_split_the_platforms():
    assert len(Built(M).terrain.platforms) == 2 and len(Built(M0).terrain.platforms) == 1
    assert len(Built(MN).terrain.platforms) == 1  # Review Focus 4: a None mark is no mark
    assert Built(MN).rg.split_ends == frozenset() and Built(M0).rg.split_ends == frozenset()


def test_the_drop_never_links_past_the_nearest():
    """(40, 50) is a marked right end: its link to the nearest node on the right (45, 50) is dropped, and it does not
    reach for (55, 50), 15 px away and inside the reach, instead."""
    built = Built([(24, 50, False, False), (40, 50, False, True), (45, 50, False, False), (55, 50, False, False)])
    assert [[node.minimap_position for node in platform] for platform in built.terrain.platforms] == [
        [(24, 50), (40, 50)], [(45, 50), (55, 50)]]
    assert not any(link.src is built.n[40] and link.dst is built.n[55] for link in built.terrain.links)


def test_the_aligned_pair_across_the_gap_is_a_gap_edge():  # PathGraph links (40,50)-(50,52): dy 2 <= align 2
    built = Built(M)
    assert built.kind(40, 50) == EdgeKind.GAP and built.kind(50, 40) == EdgeKind.GAP
    assert built.cost(40, 50) == pytest.approx(9 / 12.5 + 0.55)  # walk 40 -> 31, teleport to 50
    assert built.cost(50, 40) == pytest.approx(9 / 12.5 + 0.55)  # walk 50 -> 59, teleport to 40


def test_side_landing():
    built = Built(M)
    rg, a, b = built.rg, built.p(24), built.p(50)
    assert rg.side_landing(a, 40, -1) == (21, a)  # A's left end is not marked: full reach, as today
    assert rg.side_landing(a, 28, +1) == (40, a)  # B starts 22 px away: stops at the marked end
    assert rg.side_landing(a, 33, +1) == (52, b)  # B starts 17 px away, 2 px lower: carries onto B
    assert rg.side_landing(b, 60, -1) == (50, b)  # A's end is 20 px away: stops at B's marked left end
    assert rg.side_landing(b, 55, -1) == (36, a)  # A's end 15 px away, 2 px higher: carries onto A
    assert Built(M0).rg.side_landing(0, 33, +1) == (52, 0)  # no marks: full reach, as today


def test_a_platform_more_than_4_px_off_does_not_count():  # 4 px off carries, 5 px off stops
    carries, stops = Built(M_b_at(54)), Built(M_b_at(55))
    assert carries.rg.side_landing(carries.p(24), 33, +1)[1] == carries.p(50)
    assert stops.rg.side_landing(stops.p(24), 33, +1) == (40, stops.p(24))


def test_the_height_range_is_the_setting():
    built = Built(M_b_at(55), MovementParams(side_teleport_land_dy_px=5))
    assert built.rg.side_landing(built.p(24), 33, +1)[1] == built.p(50)


def test_the_nearest_platform_wins():
    """A ends at (40, 50), a marked right end. C starts at (46, 50) and D at (50, 54), both inside the reach of a
    teleport from x 33 and within 4 px of the end's height: C is nearer, so the teleport lands on C, not D."""
    built = Built([(24, 50, False, False), (40, 50, False, True), (50, 54, True, False), (46, 50, True, False)])
    c, d = built.p(46), built.p(50)
    assert c != d != built.p(24) and built.rg.side_landing(built.p(24), 33, +1) == (46, c)


def test_a_tie_takes_the_lower_platform_index():
    """C (46, 50) and D (46, 53) both start 13 px from x 33 (same x, 3 px apart in height, no terrain link between
    them): the platform with the lower index wins, here the one whose node comes first in the map, D."""
    built = Built([(24, 50, False, False), (40, 50, False, True), (46, 53, True, False), (46, 50, True, False)])
    d, c = built.terrain.platform_index(built.nodes[2]), built.terrain.platform_index(built.nodes[3])
    assert d < c and built.rg.side_landing(built.p(24), 33, +1) == (46, d)


def test_gap_launch():  # asked only for existing GAP edges; exact A (WALKFIX169-D1): the launch is `v.x - sign * 19`
    built = Built(M)
    n = built.n
    # A2 (3 px margin, WALKFIX172-D1 = B): 34 and 56 (walk 6, teleport, walk back 3); the GAP cost stays 9 / 12.5 + 0.55
    assert built.rg.gap_launch(n[40], n[50]) == 31 and built.rg.gap_launch(n[50], n[40]) == 59


@pytest.mark.parametrize("one_sided", [M_right_mark_only, M_left_mark_only])  # re-review N1: only (40,50) marked /
def test_one_mark_is_enough_for_a_gap_edge(one_sided):                        # only (50,52) marked
    built = Built(one_sided)
    rg, n, a, b = built.rg, built.n, built.p(24), built.p(50)
    assert built.kind(40, 50) == EdgeKind.GAP and built.kind(50, 40) == EdgeKind.GAP
    assert rg.split_ends == {(a, +1), (b, -1)}
    assert rg.is_stop_end(n[40], +1) and rg.is_stop_end(n[50], -1) and not rg.is_stop_end(n[24], -1)
    assert rg.side_landing(a, 28, +1) == (40, a) and rg.side_landing(b, 60, -1) == (50, b)  # the unmarked end stops too
    assert rg.gap_launch(n[40], n[50]) == 31 and rg.gap_launch(n[50], n[40]) == 59  # A2 (WALKFIX172-D1 = B): 34 and 56
    assert is_successful(rg.cost_to(n[24], n[66])) and is_successful(rg.cost_to(n[66], n[24]))  # both ways still reachable


def test_is_stop_end_is_only_true_at_the_end_of_a_platform():
    built = Built(M)
    n, rg = built.n, built.rg
    assert not rg.is_stop_end(n[24], +1) and not rg.is_stop_end(n[40], -1)  # the right end is not the left end
    assert not rg.is_stop_end(n[66], -1) and not rg.is_stop_end(n[50], +1)
    assert rg.is_stop_end(n[66], +1) is False  # B's right end has no mark and no gap leaves from it


def test_unmarked_split_stays_walk():  # review I4: two terrain platforms, no mark at either facing end
    unmarked, marked = Built(M0), Built(M)
    n = unmarked.n
    t = Terrain(links=(), platforms=((n[24], n[40]), (n[50], n[66])))  # built by hand from M0's unmarked nodes
    assert _side_kind(t, n[40], n[50]) == EdgeKind.WALK and _side_kind(t, n[50], n[40]) == EdgeKind.WALK
    assert _side_kind(marked.terrain, marked.n[40], marked.n[50]) == EdgeKind.GAP
    assert _side_kind(marked.terrain, marked.n[24], marked.n[40]) == EdgeKind.WALK  # same platform
    assert unmarked.kind(40, 50) == EdgeKind.WALK and unmarked.rg.split_ends == frozenset()  # one platform, as today


def test_fixture_map_is_unchanged():  # tests/fixtures/fossil_foundry_2.csv has no marks
    agent = MapleAgent(characterClass=Kanna())
    assert is_successful(agent.loadMap(FIXTURE))
    rg = RouteGraph.build(agent.pathGraph, MovementParams()).unwrap()
    assert all(kind != EdgeKind.GAP for _, _, kind in rg.graph.edges(data="kind")) and rg.split_ends == frozenset()
    assert [len(p) for p in rg.terrain.platforms] == BASELINE_PLATFORM_SIZES
    assert rg.graph.number_of_edges() == BASELINE_EDGE_COUNT


@pytest.mark.parametrize("d, expected", [(8, 0.55), (25, 1.10), (17, 0.55), (0, 0.0)])
def test_cost_to_a_marked_end(d, expected):  # the last hop onto an end node is a teleport, even under 17 px (Task 3)
    assert horizontal_cost_s(d, 19, MovementParams(), to_end=True) == pytest.approx(expected)


def test_carry_over_is_not_priced_as_a_stop():
    """On M, `side_landing(a, 33, +1)` carries onto B, so a WALK edge from a node at x 33 to (40, 50) costs the walk,
    7 / 12.5, not the 0.55 of a teleport that stops on the end node."""
    assert horizontal_cost_s(7, 19, MovementParams(), to_end=False) == pytest.approx(7 / 12.5)
    built = Built(_map(True, True, extra=((33, 50, False, False),)))
    assert built.rg.side_landing(built.p(33), 33, +1) == (52, built.p(50))
    assert built.kind(33, 40) == EdgeKind.WALK
    assert built.cost(33, 40) == pytest.approx(7 / 12.5)
    assert Built(M).cost(24, 40) == pytest.approx(0.55)  # from 24 the teleport stops on the end node: priced as one


def test_offered_keeps_the_split_ends(monkeypatch):
    """`planner._offered` builds a view of the graph without the UP/DOWN edges that have no launch point, and the view
    must carry `split_ends`, or its `is_stop_end` and `side_landing` lose the split."""
    built = Built(_map(True, True, extra=((26, 40, False, False),)))  # a platform above A: an UP edge exists
    assert any(kind in (EdgeKind.UP, EdgeKind.DOWN) for _, _, kind in built.rg.graph.edges(data="kind"))
    monkeypatch.setattr(planner, "_launch_x", lambda rg, g, h: None)
    view = planner._offered(built.rg)
    assert view is not built.rg
    assert view.split_ends == built.rg.split_ends and view.split_ends
    assert view.side_landing(built.p(24), 28, +1) == built.rg.side_landing(built.p(24), 28, +1) == (40, built.p(24))


# ---- Task 3: the planner plays the landing rule (QB282) ----

RNG = np.random.default_rng(0)
TP_R = Move(MoveKind.TELEPORT, Key.RIGHT, None)
TP_L = Move(MoveKind.TELEPORT, Key.LEFT, None)
M19 = [(21, 50, False, False), (40, 50, False, True)]  # one platform, 19 px long, its right end marked
FLAT = [(30, 50, False, False), (45, 50, False, False), (60, 50, False, False)]  # Task 1's unmarked platform
# A (24, 50), (40, 50) marked right; C (70, 52) marked left, (86, 52): C starts 30 px from A's end, out of reach
M_far = [(24, 50, False, False), (40, 50, False, True), (70, 52, True, False), (86, 52, False, False)]


def walk(direction: Key, ms: float) -> Move:
    return Move(MoveKind.WALK, direction, ms)


def decide(built: Built, pos: tuple[int, int], target_x: int):
    result = next_move(built.rg, pos, built.n[target_x], RNG)
    assert is_successful(result), result
    return result.unwrap()


def test_teleport_stops_on_the_marked_end_even_under_17():  # d = 12, B is 22 px away
    d = decide(Built(M), (28, 50), 40)
    assert d.move == TP_R
    assert d.cost_to_go_s == pytest.approx(0.55)  # `_starts` prices the approach to the end node as the teleport


def test_without_the_mark_the_same_move_walks():
    assert decide(Built(M0), (28, 50), 40).move == walk(Key.RIGHT, 960.0)


def test_an_unmarked_split_end_stops_the_teleport_too():  # re-review N1: only (50, 52) is marked, (40, 50) is not
    assert decide(Built(M_left_mark_only), (28, 50), 40).move == TP_R


def test_carry_over_is_not_a_stop_on_the_end():  # from 33 the teleport carries onto B
    d = decide(Built(M), (33, 50), 40)
    assert d.move == walk(Key.RIGHT, 560.0)
    assert d.cost_to_go_s == pytest.approx(7 / 12.5)  # review m1: not priced as a stop on the end


def test_lower_platform_does_not_count():  # B at y 56: from 33 the teleport stops at 40
    assert decide(Built(M_b_at(56)), (33, 50), 40).move.kind == MoveKind.TELEPORT


def test_crossing_the_gap_lines_up_then_teleports():
    built = Built(M)
    d1 = decide(built, (25, 50), 66)
    assert d1.move == walk(Key.RIGHT, 480.0) and d1.toward is built.n[50]  # to the launch x 31 (A2: 720.0 to x 34)
    d2 = decide(built, (31, 50), 66)  # A2: from (34, 50)
    assert d2.move == TP_R and d2.toward is built.n[50]  # a GAP edge plays as a sideways teleport, not a down move
    assert d2.cost_to_go_s == pytest.approx(0.72 + (0.72 + 0.55) + 1.28)  # approach to (40, 50) + GAP edge + 16 px walked


def test_crossing_the_gap_leftwards():  # B's launch toward A is 40 + 19 = 59 (A2: 56)
    built = Built(M)
    assert decide(built, (59, 52), 24).move == TP_L  # P5 with sign -1
    d1 = decide(built, (63, 52), 24)
    assert d1.move == walk(Key.LEFT, 320.0) and d1.toward is built.n[40]  # 4 px to the launch (A2: 7 px, 560.0)


def test_no_walk_crosses_the_gap():  # every x on A toward every B node, and from B toward A
    built = Built(M)
    for x in range(24, 41):
        for target in (50, 66):
            move = decide(built, (x, 50), target).move
            assert not (move.kind == MoveKind.WALK and move.direction == Key.RIGHT
                        and x + 12.5 * (move.walk_ms or 0) / 1000 > 40 + 2), (x, target, move)
    for x in range(50, 67):
        for target in (24, 40):
            move = decide(built, (x, 52), target).move
            assert not (move.kind == MoveKind.WALK and move.direction == Key.LEFT
                        and x - 12.5 * (move.walk_ms or 0) / 1000 < 50 - 2), (x, target, move)


def test_read_two_px_past_a_marked_end_plans():  # Review Focus 5
    built = Built(M)
    assert is_successful(next_move(built.rg, (42, 50), built.n[24], RNG))


def test_a_read_past_a_stop_end_with_nothing_in_reach_walks_back():
    """`side_landing` from a px already past the stop end with no platform in reach is (end.x, platform): a landing
    behind the character (Task 2's wording, kept). The planner never asks it that way round: a goal on the platform
    lies behind the read, so `_run` asks toward the other end (not a stop), and `_starts` gives `to_end` False for an
    end that is behind (its `sign` points away from it). So the read plans like any other and walks back onto the
    platform."""
    built = Built(M_far)
    a = built.p(24)
    assert built.rg.side_landing(a, 42, +1) == (40, a)  # behind the character, as Task 2 returns it
    assert decide(built, (43, 50), 40).move == walk(Key.LEFT, 240.0)
    assert decide(built, (43, 50), 24).move.kind == MoveKind.TELEPORT  # d = 19 toward the unmarked left end


@pytest.mark.parametrize("x", [43, 42, 41])  # review I1: d = 17, 18, 19 on an unmarked platform, toward (60, 50)
def test_raised_limit_walks_17_to_19(x):
    assert decide(Built(FLAT), (x, 50), 60).move.kind == MoveKind.TELEPORT  # the default limit: Task 1's range
    raised = Built(FLAT, MovementParams(walk_under_px=20))
    assert decide(raised, (x, 50), 60).move.kind == MoveKind.WALK  # the end-node rule does not fire: 60 is no marked end


def test_raised_limit_keeps_the_end_node_teleport():  # Hong (QB282): "even under the 17 px limit"
    raised = Built(M, MovementParams(walk_under_px=20))
    assert decide(raised, (28, 50), 40).move == TP_R


@pytest.mark.parametrize("x, walk_ms", [(29, 160.0), (30, 80.0)])  # review I2: inside arrive_px of launch 31, but the
def test_short_of_the_launch_walks_to_it(x, walk_ms):               # teleport would stop at 40 (A2: x 29 / 30 walk 400.0 / 320.0)
    built = Built(M)
    d = decide(built, (x, 50), 66)
    assert d.move == walk(Key.RIGHT, walk_ms) and d.toward is built.n[50]  # the I2 guard's walk (P5), toward the GAP edge's far node


@pytest.mark.parametrize("walk_under_px", [17, 20])  # re-review m-a: the full reach lands exactly on the marked end
def test_full_reach_onto_a_marked_end_teleports(walk_under_px):  # node (d = 19), even with the raised limit
    built = Built(M19, MovementParams(walk_under_px=walk_under_px))
    assert decide(built, (21, 50), 40).move == TP_R


def test_run_without_a_platform_uses_the_full_reach():  # m-f: rule (1) needs `platform is not None`
    built = Built(M)
    assert planner._run(built.rg, 28, 40, None) == walk(Key.RIGHT, 960.0)  # no platform: no end node, d = 12 is walked
    assert planner._run(built.rg, 28, 40, built.p(24)) == TP_R


@pytest.mark.parametrize("one_sided", [M_right_mark_only, M_left_mark_only])  # re-review N1
def test_one_sided_mark_crosses_the_gap(one_sided):
    built = Built(one_sided)
    assert decide(built, (25, 50), 66).move == walk(Key.RIGHT, 480.0)  # A2: 720.0 to x 34
    d = decide(built, (31, 50), 66)  # A2: from (34, 50)
    assert d.move == TP_R and d.toward is built.n[50]


def test_preview_lands_sideways_teleports_by_side_landing():  # re-review m-b
    built = Built(M)
    assert preview(built.rg, (31, 50), built.n[66]).unwrap().hops[0].landing == (50, 52)  # A2: from (34, 50), (53, 52)
    assert preview(built.rg, (28, 50), built.n[40]).unwrap().hops[0].landing == (40, 50)  # stops at the end node


def test_preview_matches_next_move_on_marked_map():
    built = Built(M)
    problems: list[str] = []
    for x, y, targets in [(x, 50, (50, 66)) for x in range(24, 41)] + [(x, 52, (24, 40)) for x in range(50, 67)]:
        for target in targets:
            problems += _first_hop_problems(built.rg, (x, y), built.n[target])
    assert problems == []


def test_no_move_across_the_gap_goes_down():  # T2R fyi: a GAP edge must not fall into `_go_down` (M has no down edge at all)
    built = Built(M)
    for x, y, targets in [(x, 50, (50, 66)) for x in range(24, 41)] + [(x, 52, (24, 40)) for x in range(50, 67)]:
        for target in targets:
            move = decide(built, (x, y), target).move
            assert move.kind != MoveKind.DROP and not (move.kind == MoveKind.TELEPORT and move.direction == Key.DOWN), (x, target, move)


def test_a_walk_edge_onto_another_platform_is_not_priced_as_a_stop_on_its_end():
    """T2R m1: an unmarked split (review I4 keeps such an edge WALK) onto a one-node platform whose only mark is its
    right mark. `is_stop_end(v, +1)` holds for that node, but the teleport from `u` would not stop on the end of `u`'s
    own platform, so the edge costs the walk, 6 / 12.5, not 0.55."""
    base = Built(M0)
    u40, u46 = base.n[40], Node("blue_node", (46, 50), False, False, False, True)
    terrain = Terrain(links=(), platforms=((base.n[24], u40), (u46,)))
    rg = RouteGraph(nx.DiGraph(), terrain, base.rg.align_px, base.rg.reach_h, base.rg.reach_v, base.rg.params)
    assert rg.is_stop_end(u46, +1)
    assert rg._edge_cost(u40, u46, EdgeKind.WALK) == pytest.approx(6 / 12.5)
