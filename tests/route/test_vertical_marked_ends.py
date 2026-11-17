"""An up or down move is not made from past a marked end of the target's platform (ANYMAP198-R3-D1, option B, ruled as
Hong; his game fact H17: "If there is no pixel above/below kanna to teleport to, then the teleport will fail").

P4 counts the character lined up within `arrive_px` (2 px) of an UP/DOWN edge's launch x (B14-inv-D1). With the target at
a marked platform end and the platform stood on running on past it, a read 1-2 px past the mark sent the move onto nothing,
the read did not change and the walker sent it again until the leg timed out. P5 now walks to the launch x first, the
check its GAP branch has (review I2). It is keyed on the marks (`is_marked_end`), not on node positions: past an unmarked
end the platform is taken to go on (ISS6-B-D1), so the unmarked fixtures, and both CLAUDE.md sim smokes, are unchanged.

Hong's map is `tests/fixtures/ff2_marked.csv`; its three UP edges to a marked end are (88, 60) -> (88, 43) (a left end),
(123, 43) -> (124, 23) and (129, 60) -> (129, 43) (right ends). It has no DOWN edge to a marked end, so the DOWN cases are
made-up maps. The route preview mirrors `next_move`, so every case also checks that the two agree."""

import numpy as np
import pytest
from returns.pipeline import is_successful
from test_route_preview import _first_hop_problems
from test_sim_marked import MARKED_CSV, MARKED_TERRAIN, _leg, _planned
from test_walker_standing import _load

from src.classes.classes import Kanna
from src.controller.controller import MapleAgent
from src.controller.node import Node
from src.input.protocol import Key
from src.route.leg_log import LegOutcome
from src.route.params import MovementParams
from src.route.planner import Move, MoveKind, _launch_x, next_move
from src.route.route_graph import RouteGraph
from src.route.sim.run import REAL_GROUND_SIM
from src.route.sim.world import load_terrain

L, R = Key.LEFT, Key.RIGHT
TP_UP = Move(MoveKind.TELEPORT, Key.UP, None)
TP_DOWN = Move(MoveKind.TELEPORT, Key.DOWN, None)
DROP = Move(MoveKind.DROP, None, None)


def walk(direction: Key, px: int) -> Move:
    return Move(MoveKind.WALK, direction, 1000.0 * px / MovementParams().walk_px_per_s)


@pytest.fixture(scope="module")
def hong():
    return _load(MARKED_CSV)


def _decide(rg, pos, target):
    result = next_move(rg, pos, target, np.random.default_rng(0))
    assert is_successful(result), result
    return result.unwrap()


# --- 1. Hong's map ---------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("read", "target", "move"), [
    ((86, 61), (88, 43), walk(R, 2)),   # the floor under (88, 43), a marked left end
    ((87, 61), (88, 43), walk(R, 1)),
    ((125, 43), (124, 23), walk(L, 1)),  # the middle-right platform under (124, 23), a marked right end
    ((126, 43), (124, 23), walk(L, 2)),
    ((130, 61), (129, 43), walk(L, 1)),  # the floor under (129, 43), a marked right end
    ((131, 61), (129, 43), walk(L, 2)),
])
def test_hongs_map_walks_to_the_launch_from_past_a_marked_end(hong, read, target, move):
    """Lined up (within 2 px of the launch, the target's own x) but past its marked end: walk onto the launch first."""
    decision = _decide(hong.rg, read, hong.nodes[target])
    assert decision.move == move
    assert decision.toward is hong.nodes[target]
    assert _first_hop_problems(hong.rg, read, hong.nodes[target]) == []


@pytest.mark.parametrize(("read", "target"), [
    *(((x, 61), (88, 43)) for x in (88, 89, 90)),
    *(((x, 43), (124, 23)) for x in (122, 123, 124)),
    *(((x, 61), (129, 43)) for x in (127, 128, 129)),
])
def test_hongs_map_teleports_up_from_the_mark_and_inside_it(hong, read, target):
    decision = _decide(hong.rg, read, hong.nodes[target])
    assert decision.move == TP_UP and decision.toward is hong.nodes[target]
    assert _first_hop_problems(hong.rg, read, hong.nodes[target]) == []


@pytest.mark.parametrize(("read", "target", "move"), [
    *(((x, 61), (51, 46), TP_UP) for x in (49, 50, 51, 52, 53)),  # a middle node: the window lies inside its platform
    *(((x, 60), (32, 46), TP_UP) for x in (34, 35, 36)),  # B14-inv-D1 case 2: from the floor's end, under (32, 46)'s span
    *(((x, 43), (129, 60), TP_DOWN) for x in (127, 128, 129)),  # down to an unmarked end of the floor
    *(((x, 46), (34, 60), TP_DOWN) for x in (32, 33, 34)),
])
def test_hongs_map_other_up_and_down_moves_are_unchanged(hong, read, target, move):
    decision = _decide(hong.rg, read, hong.nodes[target])
    assert decision.move == move and decision.toward is hong.nodes[target]
    assert _first_hop_problems(hong.rg, read, hong.nodes[target]) == []


@pytest.mark.parametrize(("start", "target"), [((130, 61), (129, 43)), ((86, 61), (88, 43)), ((126, 43), (124, 23))])
def test_hongs_world_reaches_the_marked_end_with_one_walk_and_one_teleport(hong, start, target):
    """In the simulated world of Hong's map (platforms end exactly at their marks), with no noise: the walker walks onto
    the launch and teleports up once. Before the fix it sent TELEPORT UP from `start` onto nothing, the world put the
    character back where it was, and the same move went out again until the 60 s leg timeout."""
    terrain = load_terrain(MARKED_TERRAIN).unwrap()
    surface = terrain.surface_below(start[0], start[1] - 3)
    assert surface is not None and surface[1] == start[1]
    leg, world, rec = _leg(hong, terrain, (float(start[0]), surface[1]), target, REAL_GROUND_SIM)
    assert leg.outcome == LegOutcome.REACHED and leg.stalls == 0
    planned = _planned(rec)
    assert [(p[1], p[2]) for p in planned] == [(MoveKind.WALK.value, (L if start[0] > target[0] else R).name),
                                               (MoveKind.TELEPORT.value, Key.UP.name)]
    assert planned[1][0][0] == target[0]
    assert [e.kind for e in world.effects] == ["walk", "teleport"]


# --- 2. made-up maps: UP and DOWN, left and right marks, and the same maps unmarked -----------------------------------
# A lower platform (y 60, or 30 for the DOWN maps' upper one) runs 20 -> 52 under or over a two-node platform 18 px away
# whose end at x 36 carries the mark; the vertical edges are at x 36 and 52 (36 and 20 for a right mark).

Row = tuple[int, int, bool, bool]  # x, y, left-end mark, right-end mark


def _build(rows: list[Row], can_fall: bool = False):
    nodes = {i: Node("blue_node", (x, y), can_fall, False, left, right) for i, (x, y, left, right) in enumerate(rows)}
    path_graph = MapleAgent(characterClass=Kanna()).buildPathGraphFor(nodes).unwrap()
    assert is_successful(path_graph.buildGraph())
    built = RouteGraph.build(path_graph, MovementParams())
    assert is_successful(built)
    return nodes, built.unwrap()


def _two_rows(stood_y: int, other_y: int, mark: str, marked: bool) -> list[Row]:
    """Row 3 is the target: the end at x 36 of the platform at `other_y`, its `mark` ("left" or "right") set or not."""
    stood = [(20, stood_y, False, False), (36, stood_y, False, False), (52, stood_y, False, False)]
    if mark == "left":
        return [*stood, (36, other_y, marked, False), (52, other_y, False, False)]
    return [*stood, (36, other_y, False, marked), (20, other_y, False, False)]


@pytest.mark.parametrize(("stood_y", "other_y", "can_fall", "vertical"), [
    (60, 42, False, TP_UP),
    (30, 48, False, TP_DOWN),
    (10, 45, True, DROP),  # 35 px down, beyond the teleport's reach: the same check holds for a drop on the DOWN edge
], ids=["up", "down", "drop"])
@pytest.mark.parametrize("mark", ["left", "right"])
def test_made_up_maps_walk_from_past_a_marked_end_and_move_from_inside(stood_y, other_y, can_fall, vertical, mark):
    nodes, rg = _build(_two_rows(stood_y, other_y, mark, True), can_fall)
    target = nodes[3]
    past = (34, 35) if mark == "left" else (38, 37)
    inside = (36, 37, 38) if mark == "left" else (34, 35, 36)
    toward_launch = R if mark == "left" else L
    for x in past:
        decision = _decide(rg, (x, stood_y), target)
        assert decision.move == walk(toward_launch, abs(x - 36)), x
        assert decision.toward is target
        assert _first_hop_problems(rg, (x, stood_y), target) == [], x
    for x in inside:
        decision = _decide(rg, (x, stood_y), target)
        assert decision.move == vertical and decision.toward is target, x
        assert _first_hop_problems(rg, (x, stood_y), target) == [], x


@pytest.mark.parametrize(("stood_y", "other_y", "can_fall", "vertical"), [
    (60, 42, False, TP_UP),
    (30, 48, False, TP_DOWN),
    (10, 45, True, DROP),
], ids=["up", "down", "drop"])
@pytest.mark.parametrize("mark", ["left", "right"])
def test_made_up_maps_without_the_mark_move_from_the_whole_window(stood_y, other_y, can_fall, vertical, mark):
    """Keyed on the marks (ISS6-B-D1): an unmarked end is taken to have platform past it, so nothing changes there."""
    nodes, rg = _build(_two_rows(stood_y, other_y, mark, False), can_fall)
    for x in (34, 35, 36, 37, 38):
        decision = _decide(rg, (x, stood_y), nodes[3])
        assert decision.move == vertical and decision.toward is nodes[3], x
        assert _first_hop_problems(rg, (x, stood_y), nodes[3]) == [], x


# --- 3. B14-inv-D1 case 2: the launch is the end of the platform stood on, not the target's own x -----------------------
# When the platform stood on ends before the target's x, the move is made from that platform's end (`_launch_x` gives the
# end's x). Every case above launches from the target's own x, so a walk that aimed at the target's x instead of the launch
# passed them all (ANYMAP199-R5 m1). Here the end is at x 40 (the launch), and the target is 1 px past it, at x 41, one px
# nearer in y than the marked end M at x 39, so terrain's finder (nearest |dy|, then |dx|) links the end to the target and
# not to M; M and the target are one platform, M its first node (a right mark is the mirror: M at 41, the target at 39). A
# read 2 px on M's side of the end is lined up (P4's `arrive_px`) and past M's mark: the walk has to cover the 2 px to the
# end, not the 3 px to the target. A made-up map: a real one needs two nodes of the upper platform within 2 px of each
# other.


def _case2_rows(stood_y: int, m_y: int, h_y: int, mark: str, marked: bool) -> list[Row]:
    if mark == "left":
        return [(30, stood_y, False, False), (40, stood_y, False, False), (39, m_y, marked, False), (41, h_y, False, False)]
    return [(40, stood_y, False, False), (50, stood_y, False, False), (41, m_y, False, marked), (39, h_y, False, False)]


@pytest.mark.parametrize(("stood_y", "m_y", "h_y", "vertical"), [
    (60, 42, 43, TP_UP),
    (30, 48, 47, TP_DOWN),
], ids=["up", "down"])
@pytest.mark.parametrize("mark", ["left", "right"])
@pytest.mark.parametrize("marked", [True, False], ids=["marked", "unmarked"])
def test_a_launch_at_the_end_stood_on_is_walked_to_not_the_targets_x(stood_y, m_y, h_y, vertical, mark, marked):
    nodes, rg = _build(_case2_rows(stood_y, m_y, h_y, mark, marked))
    target, end = nodes[3], nodes[1 if mark == "left" else 0]
    assert rg.graph.has_edge(end, target) and _launch_x(rg, end, target) == 40 != target.minimap_position[0]  # case 2
    past, inside = ((38,), (39, 40)) if mark == "left" else ((42,), (41, 40))
    toward_launch = R if mark == "left" else L
    for x in past:
        decision = _decide(rg, (x, stood_y), target)
        # marked: onto the launch (2 px), not onto the target's x (3 px); unmarked: the platform is taken to go on
        assert decision.move == (walk(toward_launch, 2) if marked else vertical), x
        assert decision.toward is target
        assert _first_hop_problems(rg, (x, stood_y), target) == [], x
    for x in inside:
        decision = _decide(rg, (x, stood_y), target)
        assert decision.move == vertical and decision.toward is target, x
        assert _first_hop_problems(rg, (x, stood_y), target) == [], x

