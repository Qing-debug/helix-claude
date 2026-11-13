"""Tests for the platform-end marks (walk-fluid plan Task 2, Hong's QB282 rule for item 4a).

The walker reads the map's platform-end marks to know where a platform ends. A mark on either facing end splits the
terrain there, a sideways teleport across such a split is a GAP edge, and `RouteGraph.side_landing` is the one place
that says where a sideways teleport stops: at the end of the platform it is launched from, or, when another platform
starts within the reach at about the same height (`side_teleport_land_dy_px`), on that platform.

Map M (Kanna, defaults; neighbours 16 px apart, inside the 19 px reach that terrain and PathGraph links need):
platform A is (24, 50) and (40, 50), with (40, 50) marked as a right end; platform B is (50, 52), marked as a left
end, and (66, 52). M0 has the same four nodes with no marks, MN with every mark None (the `Node` default)."""

from pathlib import Path

import pytest
from returns.pipeline import is_successful

from src.classes.classes import Kanna
from src.controller.controller import MapleAgent
from src.controller.node import Node
from src.route import planner
from src.route.params import MovementParams, validate
from src.route.route_graph import EdgeKind, RouteGraph, _side_kind, horizontal_cost_s, is_marked_end
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
