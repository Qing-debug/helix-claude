"""Tests for src.route.planner (plan contract C7): FF2 move choices for rules P1-P5, the mix band, and rng use."""

from pathlib import Path

import numpy as np
import pytest
from returns.pipeline import is_successful

from src.classes.classes import Kanna
from src.controller.controller import MapleAgent
from src.controller.node import Node
from src.input.protocol import Key
from src.route.locate import attach
from src.route.params import MovementParams
from src.route.planner import Decision, Move, MoveKind, next_move
from src.route.route_graph import EdgeKind, RouteGraph

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "fossil_foundry_2.csv"

TELEPORT_DOWN = Move(MoveKind.TELEPORT, Key.DOWN, None)
DROP = Move(MoveKind.DROP, None, None)


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


def _rebuild(agent, **params):
    """The FF2 RouteGraph under changed parameters (`next_move` reads `rg.params`)."""
    result = RouteGraph.build(agent.pathGraph, MovementParams(**params))
    assert is_successful(result)
    return result.unwrap()


def _decide(rg, pos, target, rng=None):
    rng = np.random.default_rng(0) if rng is None else rng
    result = next_move(rg, pos, target, rng)
    assert is_successful(result), result
    return result.unwrap()


def _synthetic(points, can_fall=False):
    """A RouteGraph over nodes at `points` (in this node_map order), built the way the runtime builds it."""
    nodes = {i: Node("blue_node", xy, can_fall, False, False, False) for i, xy in enumerate(points)}
    pg = MapleAgent(characterClass=Kanna()).buildPathGraphFor(nodes).unwrap()
    assert is_successful(pg.buildGraph())
    rg = RouteGraph.build(pg, MovementParams())
    assert is_successful(rg)
    return nodes, rg.unwrap()


def _rng_untouched(rng):
    return rng.random() == np.random.default_rng(0).random()


# ---- FF2 (plan Task B7 tests 1-8) ----

def test_arrived_within_arrive_px_of_the_target_p1(ff2):
    agent, rg = ff2
    assert _decide(rg, (104, 43), _node(agent, (105, 42))) == Decision(None, 0.0, None)


def test_known_case_2_walks_level_with_the_target_teleports_down_then_walks_right(ff2):
    agent, _ = ff2
    rg = _rebuild(agent, ability_mix_band=0)
    target = _node(agent, (105, 42))
    # B14-inv-D1 case 1: the top platform spans x 105, so the DOWN edge (101,24) -> (105,42) is made from x 105
    first = _decide(rg, (101, 24), target)
    assert first.move == Move(MoveKind.WALK, Key.RIGHT, 500.0)
    assert first.toward is target
    assert first.cost_to_go_s == pytest.approx(1.05)
    lined_up = _decide(rg, (105, 23), target)
    assert lined_up.move == TELEPORT_DOWN
    assert lined_up.toward is target
    assert lined_up.cost_to_go_s == pytest.approx(0.5 + 1.05)  # approach from x 105 back to the start (101,24)
    landed = _decide(rg, (101, 42), target)
    assert landed.move.kind == MoveKind.WALK
    assert landed.move.direction == Key.RIGHT
    assert landed.move.walk_ms == pytest.approx(500.0)
    assert landed.toward is target


def test_known_case_1_teleports_to_the_slope_walks_it_then_teleports_on(ff2):
    agent, rg = ff2
    target = _node(agent, (101, 24))

    run = _decide(rg, (49, 28), target)  # run 49 -> 66, d = 17
    assert run.move == Move(MoveKind.TELEPORT, Key.RIGHT, None)
    assert run.toward is _node(agent, (66, 28))

    slope_start = _decide(rg, (66, 28), target)  # the SLOPE edge, 13 px at 8 px/s
    assert slope_start.move.kind == MoveKind.WALK
    assert slope_start.move.direction == Key.RIGHT
    assert slope_start.move.walk_ms == pytest.approx(1625.0)
    assert slope_start.toward is _node(agent, (79, 24))

    on_slope = _decide(rg, (72, 26), target)  # 7 px of slope left
    assert on_slope.move.kind == MoveKind.WALK
    assert on_slope.move.direction == Key.RIGHT
    assert on_slope.move.walk_ms == pytest.approx(875.0)
    assert on_slope.toward is _node(agent, (79, 24))

    after = _decide(rg, (79, 24), target)  # run 79 -> 101 over the WALK edges, d = 22
    assert after.move == Move(MoveKind.TELEPORT, Key.RIGHT, None)
    assert after.toward is target


def test_cost_to_go_is_the_approach_plus_the_graph_cost(ff2):
    agent, rg = ff2
    target = _node(agent, (101, 24))
    # on the slope the approach to the right end is |dx| / V = 7 / 8
    on_slope = _decide(rg, (72, 26), target)
    assert on_slope.cost_to_go_s == pytest.approx(7 / 8 + rg.cost_to(_node(agent, (79, 24)), target).unwrap())
    at_node = _decide(rg, (79, 24), target)
    assert at_node.cost_to_go_s == pytest.approx(rg.cost_to(_node(agent, (79, 24)), target).unwrap())


def test_on_the_slope_the_nearer_end_towards_the_target_is_the_goal(ff2):
    agent, rg = ff2
    decision = _decide(rg, (72, 26), _node(agent, (49, 28)))  # target is left, so the left end is the start
    assert decision.move.kind == MoveKind.WALK  # a slope is always walked
    assert decision.move.direction == Key.LEFT
    assert decision.move.walk_ms == pytest.approx(750.0)
    assert decision.toward is _node(agent, (66, 28))


@pytest.mark.parametrize(
    "pos, kind, walk_ms",
    [
        ((82, 60), MoveKind.TELEPORT, None),  # d = 17
        ((88, 60), MoveKind.WALK, 1375.0),  # d = 11
        ((86, 60), MoveKind.TELEPORT, None),  # d = 13
        ((95, 60), MoveKind.WALK, 500.0),  # d = 4
    ],
)
def test_p4_teleport_or_walk_by_the_formula(ff2, pos, kind, walk_ms):
    agent, rg = ff2
    target = _node(agent, (99, 60))
    decision = _decide(rg, pos, target)
    assert decision.move.kind == kind
    assert decision.move.direction == Key.RIGHT
    if walk_ms is None:
        assert decision.move.walk_ms is None
    else:
        assert decision.move.walk_ms == pytest.approx(walk_ms)
    assert decision.toward is target


def test_p4_walks_left_when_the_goal_is_to_the_left(ff2):
    agent, rg = ff2
    decision = _decide(rg, (104, 60), _node(agent, (99, 60)))
    assert decision.move.kind == MoveKind.WALK
    assert decision.move.direction == Key.LEFT
    assert decision.move.walk_ms == pytest.approx(625.0)


def test_up_edge_teleports_up(ff2):
    agent, rg = ff2
    decision = _decide(rg, (48, 61), _node(agent, (58, 46)))
    assert decision.move == Move(MoveKind.TELEPORT, Key.UP, None)
    assert decision.toward is _node(agent, (48, 45))


def test_mix_band_splits_teleport_and_drop_evenly_and_never_else(ff2):
    agent, rg = ff2
    target = _node(agent, (105, 42))
    rng = np.random.default_rng(1)
    moves = [_decide(rg, (105, 23), target, rng).move for _ in range(2000)]  # teleport 0.55, drop 0.65 <= 0.715
    assert set(moves) == {TELEPORT_DOWN, DROP}
    assert 0.45 <= moves.count(DROP) / len(moves) <= 0.55


def test_band_zero_never_drops(ff2):
    agent, _ = ff2
    rg = _rebuild(agent, ability_mix_band=0)
    target = _node(agent, (105, 42))
    rng = np.random.default_rng(1)
    assert all(_decide(rg, (105, 23), target, rng).move == TELEPORT_DOWN for _ in range(200))


def test_cheaper_drop_outside_the_band_is_chosen_every_time(ff2):
    agent, _ = ff2
    rg = _rebuild(agent, ability_mix_band=0, teleport_s=0.9)  # teleport 0.9 > drop 0.65 by more than the band
    target = _node(agent, (105, 42))
    rng = np.random.default_rng(1)
    assert all(_decide(rg, (105, 23), target, rng).move == DROP for _ in range(50))


def test_a_node_that_cannot_fall_always_teleports_down():
    nodes, rg = _synthetic([(10, 10), (10, 28)], can_fall=False)  # 18 px straight below, within teleport reach
    assert rg.graph.edges[nodes[0], nodes[1]]["kind"] == EdgeKind.DOWN
    rng = np.random.default_rng(1)
    assert all(_decide(rg, (10, 10), nodes[1], rng).move == TELEPORT_DOWN for _ in range(200))
    rng = np.random.default_rng(0)
    _decide(rg, (10, 10), nodes[1], rng)
    assert _rng_untouched(rng)  # a single option needs no draw


def test_a_node_that_can_fall_with_both_options_inside_the_band_mixes():
    nodes, rg = _synthetic([(10, 10), (10, 28)], can_fall=True)
    rng = np.random.default_rng(1)
    moves = {_decide(rg, (10, 10), nodes[1], rng).move for _ in range(200)}
    assert moves == {TELEPORT_DOWN, DROP}


def test_off_graph_position_is_a_failure(ff2):
    agent, rg = ff2
    result = next_move(rg, (86, 37), _node(agent, (99, 60)), np.random.default_rng(0))
    assert not is_successful(result)
    assert result.failure().startswith("off-graph")


def test_the_attach_failure_is_returned_unchanged(ff2):
    agent, rg = ff2
    pos = (86, 37)
    result = next_move(rg, pos, _node(agent, (99, 60)), np.random.default_rng(0))
    assert result.failure() == attach(rg, pos).failure()


def test_walk_ms_is_capped_at_max_walk_ms(ff2):
    agent, _ = ff2
    rg = _rebuild(agent, max_walk_ms=1000)
    decision = _decide(rg, (37, 60), _node(agent, (48, 61)))  # an 11 px walk would be 1375 ms
    assert decision.move.kind == MoveKind.WALK
    assert decision.move.walk_ms == 1000.0


def test_slope_walk_ms_is_capped_at_max_walk_ms(ff2):
    agent, _ = ff2
    rg = _rebuild(agent, max_walk_ms=1000)
    decision = _decide(rg, (66, 28), _node(agent, (101, 24)))  # the 13 px slope would be 1625 ms
    assert decision.move.kind == MoveKind.WALK
    assert decision.move.walk_ms == 1000.0


def test_node_off_the_surface_counts_as_arrived_on_its_platform(ff2):
    agent, rg = ff2
    # (105,45) is 3 px below the node: |dy| 3 > arrive_px 2, but A2 puts it on platform 3, the node's platform
    assert _decide(rg, (105, 45), _node(agent, (105, 42))) == Decision(None, 0.0, None)


def test_position_too_far_below_the_node_is_off_graph_never_arrived(ff2):
    agent, rg = ff2
    result = next_move(rg, (105, 46), _node(agent, (105, 42)), np.random.default_rng(0))
    assert not is_successful(result)
    assert result.failure().startswith("off-graph")


def test_p4_counts_a_position_below_a_node_on_its_platform_as_at_the_node(ff2):
    agent, rg = ff2
    decision = _decide(rg, (48, 64), _node(agent, (58, 46)))  # 3 px below (48,61), floor platform by A2
    assert decision.move == Move(MoveKind.TELEPORT, Key.UP, None)
    assert decision.toward is _node(agent, (48, 45))


# ---- failures (contract P2, P3) ----

def test_a_target_outside_the_route_graph_is_a_failure(ff2):
    _, rg = ff2
    stranger = Node("blue_node", (113, 60), False, False, False, False)
    result = next_move(rg, (113, 60), stranger, np.random.default_rng(0))
    assert not is_successful(result)
    assert "not in the route graph" in result.failure()


def test_no_path_to_the_target_is_a_failure():
    nodes, rg = _synthetic([(10, 10), (100, 10)])  # 90 px apart: two separate platforms, no edge
    result = next_move(rg, (10, 10), nodes[1], np.random.default_rng(0))
    assert not is_successful(result)
    assert result.failure() == "no path from (10, 10) to (100, 10)"


# ---- rng use (test 9) ----

@pytest.mark.parametrize(
    "pos, target_xy, params",
    [
        ((104, 43), (105, 42), {}),
        ((105, 23), (105, 42), {"ability_mix_band": 0}),  # two options, outside the band: the cheaper one
        ((101, 24), (105, 42), {}),  # the walk to the launch x of the DOWN edge
        ((101, 42), (105, 42), {"ability_mix_band": 0}),
        ((49, 28), (101, 24), {}),
        ((66, 28), (101, 24), {}),
        ((72, 26), (101, 24), {}),
        ((79, 24), (101, 24), {}),
        ((82, 60), (99, 60), {}),
        ((88, 60), (99, 60), {}),
        ((86, 60), (99, 60), {}),
        ((95, 60), (99, 60), {}),
        ((48, 61), (58, 46), {}),
        ((86, 37), (99, 60), {}),
        ((37, 60), (48, 61), {"max_walk_ms": 1000}),
        ((105, 45), (105, 42), {}),
        ((105, 46), (105, 42), {}),
        ((48, 64), (58, 46), {}),
    ],
)
def test_rng_is_untouched_outside_the_in_band_down_choice(ff2, pos, target_xy, params):
    agent, _ = ff2
    rg = _rebuild(agent, **params)
    rng = np.random.default_rng(0)
    next_move(rg, pos, _node(agent, target_xy), rng)
    assert _rng_untouched(rng)


def test_rng_is_used_for_the_in_band_down_choice(ff2):
    agent, rg = ff2
    rng = np.random.default_rng(0)
    next_move(rg, (105, 23), _node(agent, (105, 42)), rng)
    assert not _rng_untouched(rng)


# ---- P5 WALK edge at a slope-segment start (test 10) ----

def test_p5_walk_edge_after_a_slope_segment_start_advances_over_the_walk_edges(tmp_path):
    text = FIXTURE.read_text()
    assert text.count('"(79, 24)"') == 1
    moved = tmp_path / "ff2_moved_node.csv"
    moved.write_text(text.replace('"(79, 24)"', '"(79, 22)"'), newline="\n")
    agent = MapleAgent(characterClass=Kanna())
    assert is_successful(agent.loadMap(moved))
    result = RouteGraph.build(agent.pathGraph, MovementParams())
    assert is_successful(result)
    rg = result.unwrap()

    top = _node(agent, (79, 22))
    mid = _node(agent, (90, 24))
    target = _node(agent, (101, 24))
    # the preconditions this case is about: (77,25) is on the (66,28)-(79,22) slope segment, at the (79,22) node by
    # the platform test, and the path onward is two WALK edges
    anchor = attach(rg, (77, 25)).unwrap()
    assert (anchor.node, anchor.left, anchor.right) == (None, _node(agent, (66, 28)), top)
    assert rg.path(top, target).unwrap() == (top, mid, target)
    assert rg.graph.edges[top, mid]["kind"] == EdgeKind.WALK and rg.graph.edges[mid, target]["kind"] == EdgeKind.WALK

    decision = _decide(rg, (77, 25), target)
    assert decision.move == Move(MoveKind.TELEPORT, Key.RIGHT, None)  # d = 24
    assert decision.toward is target


# ---- UP/DOWN moves line up before they are made (B14-inv-D1, PLF48) ----

def test_case_1_walks_until_level_with_the_target_x_then_teleports_down():
    nodes, rg = _synthetic([(10, 10), (25, 10), (40, 10), (29, 28), (45, 28)])  # the top spans x 29
    assert rg.graph.edges[nodes[1], nodes[3]]["kind"] == EdgeKind.DOWN
    target = nodes[3]
    first = _decide(rg, (25, 10), target)  # at (25,10), the old rule fired from here
    assert first.move == Move(MoveKind.WALK, Key.RIGHT, 500.0)
    assert first.toward is target
    assert _decide(rg, (29, 10), target).move == TELEPORT_DOWN
    assert _decide(rg, (27, 10), target).move == TELEPORT_DOWN  # within arrive_px of x 29: the walk rule is unchanged


def test_case_1_walks_until_level_with_the_target_x_then_teleports_up():
    nodes, rg = _synthetic([(10, 10), (25, 10), (20, 28), (29, 28)])  # the lower platform spans x 25
    target = nodes[1]
    first = _decide(rg, (29, 28), target)
    assert first.move == Move(MoveKind.WALK, Key.LEFT, 500.0)
    assert first.toward is target
    assert _decide(rg, (25, 28), target).move == Move(MoveKind.TELEPORT, Key.UP, None)


def test_case_2_walks_to_the_platform_end_when_the_floor_below_it_is_the_target_platform():
    nodes, rg = _synthetic([(10, 10), (25, 10), (20, 28), (29, 28)])  # the top ends at x 25, before x 29
    assert rg.graph.edges[nodes[1], nodes[3]]["kind"] == EdgeKind.DOWN
    target = nodes[3]
    first = _decide(rg, (21, 10), target)
    assert first.move == Move(MoveKind.WALK, Key.RIGHT, 500.0)  # to the end at x 25, not to x 29
    assert first.toward is target
    assert _decide(rg, (25, 10), target).move == TELEPORT_DOWN  # (20,28)-(29,28) is straight below x 25


def test_case_2_the_move_is_not_offered_when_another_platform_is_below_the_end():
    # the top ends at x 25; below x 25 is the floor, not the (29,28)-(45,28) platform, so DOWN (25,10) -> (29,28) is
    # not offered and the route goes round: drop at (10,10), along the floor, teleport up at x 45
    points = [(10, 10), (25, 10), (29, 28), (45, 28), (5, 45), (20, 45), (35, 45), (50, 45)]
    nodes, rg = _synthetic(points, can_fall=True)
    assert rg.graph.edges[nodes[1], nodes[2]]["kind"] == EdgeKind.DOWN  # in the route graph, but not offered
    decision = _decide(rg, (25, 10), nodes[2])
    assert decision.move == Move(MoveKind.TELEPORT, Key.LEFT, None)  # run to (10,10), d = 15
    assert decision.toward is nodes[4]
    assert _decide(rg, (10, 10), nodes[2]).move == DROP


def test_case_2_with_no_other_route_is_no_path():
    nodes, rg = _synthetic([(10, 10), (25, 10), (29, 28), (45, 28)])
    assert rg.graph.has_edge(nodes[1], nodes[2])
    result = next_move(rg, (25, 10), nodes[2], np.random.default_rng(0))
    assert not is_successful(result)
    assert result.failure() == "no path from (25, 10) to (29, 28)"


def test_split_mid_platform_no_longer_sends_the_character_down_from_the_wrong_x(tmp_path):
    # seed 34 of plan test 3b: offsets (97,43)->(97,45) and (105,42)->(105,40) split the mid-right platform, and the
    # old rule dropped from x 99 onto the lone (97,45) piece, which sent the character back up, forever
    text = FIXTURE.read_text()
    moved = tmp_path / "ff2_split.csv"
    moved.write_text(text.replace('"(97, 43)"', '"(97, 45)"').replace('"(105, 42)"', '"(105, 40)"'), newline="\n")
    agent = MapleAgent(characterClass=Kanna())
    assert is_successful(agent.loadMap(moved))
    rg = RouteGraph.build(agent.pathGraph, MovementParams(ability_mix_band=0)).unwrap()
    lone, target = _node(agent, (97, 45)), _node(agent, (105, 40))
    assert (lone,) in rg.terrain.platforms  # the split
    first = _decide(rg, (99, 24), target)
    assert first.move == Move(MoveKind.WALK, Key.RIGHT, 750.0)  # to x 105
    assert _decide(rg, (105, 23), target).move == TELEPORT_DOWN
