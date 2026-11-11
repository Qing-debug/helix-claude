"""Tests for src.route.route_preview (plan Task RV-4, contract V4): the hand-built agreement table (spec S3 part 3)
and S3b on it, the starts, `max_hops`, purity, the coin flip, the landing rule, and the FF2 sweep.

The expected values in the table were computed at planning time by a prototype of V4's rule (RV3b notes). A literal
that disagrees with V4's rule text is a plan error to report, not a reason to change the rule.
"""

import inspect
import sys
import time
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from preview_checks import follows, s3b_problems
from returns.pipeline import is_successful
from test_planner import _node, _synthetic  # PF81 m10: imported flat from the frozen file, not copied

import src.route.planner as planner
from src.classes.classes import Kanna
from src.controller.controller import MapleAgent
from src.controller.node import Node
from src.input.protocol import Key
from src.route.params import MovementParams
from src.route.planner import Decision, Move, MoveKind, next_move
from src.route.route_graph import RouteGraph
from src.route.route_preview import preview

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "fossil_foundry_2.csv"

R, L = Key.RIGHT, Key.LEFT
TP_R = Move(MoveKind.TELEPORT, Key.RIGHT, None)
TP_L = Move(MoveKind.TELEPORT, Key.LEFT, None)
TP_UP = Move(MoveKind.TELEPORT, Key.UP, None)
TP_DOWN = Move(MoveKind.TELEPORT, Key.DOWN, None)
DROP = Move(MoveKind.DROP, None, None)


def walk(direction, ms):
    return Move(MoveKind.WALK, direction, float(ms))


# ---- graphs ----

def _ff2(csv=FIXTURE, **params):
    """(agent, rg): the FF2 map (or an edited copy) under `MovementParams(**params)`."""
    agent = MapleAgent(characterClass=Kanna())
    assert is_successful(agent.loadMap(csv))
    result = RouteGraph.build(agent.pathGraph, MovementParams(**params))
    assert is_successful(result)
    return agent, result.unwrap()


def _edited_ff2(tmp_path, *replacements):
    """The FF2 text with each `(old, new)` replaced (each `old` occurs once), written as test_planner.py:327-330."""
    text = FIXTURE.read_text()
    for old, new in replacements:
        assert text.count(old) == 1, old
        text = text.replace(old, new)
    edited = tmp_path / "ff2_edited.csv"
    edited.write_text(text, newline="\n")
    return edited


def _synthetic_with(points, can_fall=False, **params):
    """`test_planner._synthetic` under `MovementParams(**params)`: the frozen helper builds with the defaults only
    (PF81 m10 imports it instead of copying the plan's `_synthetic(points, can_fall, **params)`)."""
    nodes, rg = _synthetic(points, can_fall)
    if params:
        pg = MapleAgent(characterClass=Kanna()).buildPathGraphFor(nodes).unwrap()
        assert is_successful(pg.buildGraph())
        result = RouteGraph.build(pg, MovementParams(**params))
        assert is_successful(result)
        rg = result.unwrap()
    return nodes, rg


def ff2(*replacements, **params):
    return ("ff2", replacements, tuple(sorted(params.items())))


def syn(points, can_fall=False, **params):
    return ("syn", tuple(points), can_fall, tuple(sorted(params.items())))


FF2 = ff2()
STRANGER = "a node not in the graph"  # T16's target, made when the graphs are built (not at import)


class Fail:
    def __init__(self, message):
        self.message = message


ARRIVED = "arrived"

# ---- the table: (id, graph, pos, target, expected); a target or `toward` is an FF2 position or `#i` as the int i ----
# expected: Fail(message), ARRIVED, or (cost, [(options, toward, landing), ...])

# The walk times assume the planner's walk speed of 12.5 px/s (H81): a walk of d px is 80 * d ms. The table was first
# computed at 8 px/s (a walk of d px was 125 * d ms) and every literal below was recomputed for 12.5.
CASES = [
    ("T1", FF2, (49, 28), (101, 24), (3.51, [
        (TP_R, (66, 28), (68, 27)),
        (walk(R, 880), (79, 24), (79, 24)),
        (TP_R, (101, 24), (98, 24)),
        (walk(R, 240), (101, 24), (101, 24)),
    ])),
    ("T2", ff2(ability_mix_band=0), (101, 24), (105, 42), (0.87, [
        (walk(R, 320), (105, 42), (105, 24)),
        (TP_DOWN, (105, 42), (105, 42)),
    ])),
    ("T3", FF2, (101, 24), (105, 42), (0.87, [
        (walk(R, 320), (105, 42), (105, 24)),
        (TP_DOWN, (105, 42), (105, 42)),  # was COIN (DROP, TP_DOWN) before H87: a teleport in reach is always taken
    ])),
    ("T4", FF2, (104, 43), (105, 42), ARRIVED),
    ("T5", FF2, (105, 45), (105, 42), ARRIVED),
    ("T6", syn([(10, 10), (10, 28)]), (10, 10), 1, (0.55, [
        (TP_DOWN, 1, (10, 28)),
    ])),
    ("T7", syn([(10, 10), (10, 28)], can_fall=True, ability_mix_band=0), (10, 10), 1, (0.55, [
        (TP_DOWN, 1, (10, 28)),
    ])),
    ("T8", ff2(teleport_s=0.9), (105, 23), (105, 42), (1.54, [
        (TP_DOWN, (105, 42), (105, 42)),  # was DROP at 1.29 s: with H87 the teleport in reach wins although it costs more
    ])),
    ("T9", ff2(('"(79, 24)"', '"(79, 22)"')), (77, 25), (101, 24), (1.92, [
        (TP_R, (101, 24), (96, 24)),
        (walk(R, 400), (101, 24), (101, 24)),
    ])),
    ("T10", ff2(('"(97, 43)"', '"(97, 45)"'), ('"(105, 42)"', '"(105, 40)"'), ability_mix_band=0),
     (99, 24), (105, 40), (0.87, [
        (walk(R, 480), (105, 40), (105, 24)),
        (TP_DOWN, (105, 40), (105, 40)),
    ])),
    ("T11", syn([(10, 10), (25, 10), (29, 28), (45, 28), (5, 45), (20, 45), (35, 45), (50, 45)], can_fall=True),
     (25, 10), 2, (6.5533333, [
        (TP_L, 4, (6, 10)),
        (walk(R, 320), 4, (10, 10)),
        (DROP, 4, (10, 45)),
        (TP_R, 3, (29, 45)),
        (TP_R, 3, (48, 45)),
        (walk(L, 240), 3, (45, 45)),
        (TP_UP, 3, (45, 28)),
        (TP_L, 2, (26, 28)),
        (walk(R, 240), 2, (29, 28)),
    ])),
    ("T12", syn([(10, 60), (25, 60), (29, 50), (45, 50), (6, 38), (18, 38), (30, 38)]), (25, 60), 2, (4.29, [
        (TP_L, 4, (6, 60)),
        (walk(R, 320), 4, (10, 60)),
        (TP_UP, 4, (10, 38)),
        (TP_R, 2, (29, 38)),
        (TP_DOWN, 2, (29, 50)),
    ])),
    ("T13", FF2, (86, 37), (99, 60), Fail("off-graph: (86, 37)")),
    ("T14", syn([(10, 10), (100, 10)]), (10, 10), 1, Fail("no path from (10, 10) to (100, 10)")),
    ("T15", syn([(10, 10), (25, 10), (29, 28), (45, 28)]), (25, 10), 2, Fail("no path from (25, 10) to (29, 28)")),
    ("T16", FF2, (113, 60), STRANGER, Fail("target (113, 60) is not in the route graph")),
    ("T17", syn([(10, 10), (25, 10), (40, 10), (29, 28), (45, 28)]), (25, 10), 3, (0.87, [
        (walk(R, 320), 3, (29, 10)),
        (TP_DOWN, 3, (29, 28)),
    ])),
    ("T18", syn([(10, 28), (25, 28), (20, 10), (29, 10)]), (21, 28), 3, (1.19, [
        (walk(R, 320), 3, (25, 28)),
        (TP_UP, 3, (25, 10)),
        (walk(R, 320), 3, (29, 10)),
    ])),
    ("T19", FF2, (48, 61), (58, 46), (1.35, [
        (TP_UP, (48, 45), (48, 45)),
        (walk(R, 800), (58, 46), (58, 46)),
    ])),
    ("T20", ff2(max_walk_ms=500), (37, 60), (48, 61), (0.88, [
        (walk(R, 500), (48, 61), (43, 61)),
        (walk(R, 400), (48, 61), (48, 61)),
    ])),
    ("T21", ff2(max_walk_ms=500), (66, 28), (101, 24), (2.8, [
        (walk(R, 500), (79, 24), (72, 26)),
        (walk(R, 500), (79, 24), (78, 24)),
        (TP_R, (101, 24), (97, 24)),
        (walk(R, 320), (101, 24), (101, 24)),
    ])),
    ("T22", FF2, (72, 26), (49, 28), (1.19, [
        (walk(L, 480), (66, 28), (66, 28)),
        (TP_L, (49, 28), (47, 28)),
    ])),
    ("T23", FF2, (82, 60), (99, 60), (0.71, [
        (TP_R, (99, 60), (101, 60)),
    ])),
    ("T24", FF2, (48, 64), (58, 46), (1.35, [
        (TP_UP, (48, 45), (48, 45)),
        (walk(R, 800), (58, 46), (58, 46)),
    ])),
    ("D1", FF2, (129, 60), (97, 43), (2.62, [
        (TP_L, (105, 42), (110, 60)),
        (walk(L, 400), (105, 42), (105, 60)),
        (TP_UP, (105, 42), (105, 42)),
        (walk(L, 640), (97, 43), (97, 43)),
    ])),
    # D2: the hops leave `path`. The first hop walks to x 101 on the (101,42) piece, and the re-plan from there (4.51 s,
    # against 4.69 s for the other start) goes down to (99,60) and along the bottom, where `path` (4.37 s) went via
    # (101,24) and the upper route. Not a tie. The literals are the preview's at 12.5 px/s, each checked by S3b against
    # `next_move`, not derived by hand (added for review finding WALKSPD-R m1).
    ("D2", FF2, (105, 42), (58, 46), (4.37, [
        (walk(L, 320), (101, 24), (101, 42)),
        (TP_DOWN, (99, 60), (101, 60)),  # was COIN (DROP, TP_DOWN) before H87
        (TP_L, (48, 45), (82, 60)),
        (TP_L, (48, 45), (63, 60)),
        (TP_L, (48, 45), (44, 61)),
        (walk(R, 320), (48, 45), (48, 61)),
        (TP_UP, (48, 45), (48, 45)),
        (walk(R, 800), (58, 46), (58, 46)),
    ])),
]
# Where the hops leave `path` (RV3-D5 A2): S3b still holds, and `follows` is False.
LEAVES_PATH = {"D2"}
# Where two routes cost exactly the same, `rg.path` breaks the tie on its own and the hops may break it the other way, so
# `follows` is not asserted for these; `test_d1_is_an_exact_cost_tie...` pins the tie instead.
TIED = {"D1"}
IDS = [case[0] for case in CASES]
EXPECTED = {case[0]: case[4] for case in CASES}
ROUTES = [case_id for case_id in IDS if isinstance(EXPECTED[case_id], tuple)]  # non-Failure, non-arrived


class Built:
    """One case on its built graph: `node(spec)` turns an FF2 position or an `#i` index into the graph's node."""

    def __init__(self, rg, pos, target, node):
        self.rg, self.pos, self.target, self.node = rg, pos, target, node


def _build(graph, tmp_path_factory):
    if graph[0] == "ff2":
        _, replacements, params = graph
        csv = _edited_ff2(tmp_path_factory.mktemp("ff2"), *replacements) if replacements else FIXTURE
        agent, rg = _ff2(csv, **dict(params))

        def node(spec):
            if spec == STRANGER:
                return Node("blue_node", (113, 60), False, False, False, False)
            return _node(agent, spec)

        return rg, node
    _, points, can_fall, params = graph
    nodes, rg = _synthetic_with(points, can_fall, **dict(params))
    return rg, nodes.__getitem__


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """Each case's graph (built once per distinct graph), position and target node, by case id."""
    graphs = {}
    cases = {}
    for case_id, graph, pos, target, _ in CASES:
        if graph not in graphs:
            graphs[graph] = _build(graph, tmp_path_factory)
        rg, node = graphs[graph]
        cases[case_id] = Built(rg, pos, node(target), node)
    return cases


def _preview(case):
    result = preview(case.rg, case.pos, case.target)
    assert is_successful(result), result
    return result.unwrap()


def _first_hop_problems(rg, pos, target):
    """Test 1's check at one position: `preview` against `next_move` (seed 0), exactly; and every two-option hop's
    `from_pos` gives, over seeds 0..49, only those two moves and both."""
    where = f"{pos} -> {target.minimap_position}"
    result = preview(rg, pos, target)
    planned = next_move(rg, pos, target, np.random.default_rng(0))
    if not is_successful(planned):
        if is_successful(result) or result.failure() != planned.failure():
            return [f"{where}: next_move fails with {planned.failure()!r}, preview gives {result}"]
        return []
    if not is_successful(result):
        return [f"{where}: next_move succeeds, preview fails with {result.failure()!r}"]
    d, pv = planned.unwrap(), result.unwrap()
    if d.move is None:
        ok = d == Decision(None, 0.0, None) and pv.arrived
        return [] if ok else [f"{where}: next_move is {d}, preview arrived {pv.arrived}"]
    problems = []
    hop = pv.hops[0] if pv.hops else None
    if pv.arrived or hop is None:
        return [f"{where}: next_move makes {d.move}, the preview has no hop"]
    if d.move not in hop.options:
        problems.append(f"{where}: next_move makes {d.move}, hop 0 offers {hop.options}")
    if hop.toward is not d.toward:
        problems.append(f"{where}: next_move heads for {d.toward.minimap_position}, hop 0 for "
                        f"{hop.toward.minimap_position}")
    if hop.cost_to_go_s != d.cost_to_go_s or pv.cost_to_go_s != d.cost_to_go_s:
        problems.append(f"{where}: next_move costs {d.cost_to_go_s!r}, hop 0 {hop.cost_to_go_s!r}, "
                        f"the preview {pv.cost_to_go_s!r}")
    for k, hop in enumerate(pv.hops):
        if len(hop.options) == 2:
            seen = {next_move(rg, hop.from_pos, target, np.random.default_rng(seed)).unwrap().move
                    for seed in range(50)}
            if seen != set(hop.options):
                problems.append(f"{where}, hop {k}: next_move over seeds 0..49 makes {seen}, the hop offers "
                                f"{hop.options}")
    return problems


# ---- 1. S3, first hop, exact ----

@pytest.mark.parametrize("case_id", IDS)
def test_s3_the_first_hop_equals_next_move_exactly(built, case_id):
    case = built[case_id]
    assert _first_hop_problems(case.rg, case.pos, case.target) == []


def test_s3_no_hop_of_the_table_has_two_options_after_h87(built):
    # a hop with two options was the coin flip; `_first_hop_problems`' seed check only runs on such a hop
    found = []
    for case_id in IDS:
        case = built[case_id]
        result = preview(case.rg, case.pos, case.target)
        hops = result.unwrap().hops if is_successful(result) else ()
        found += [(case_id, k) for k, hop in enumerate(hops) if len(hop.options) == 2]
    assert found == []


# ---- 2. the table's literals ----

def _expected_hops(case, hops):
    return [((options,) if isinstance(options, Move) else options, case.node(toward).minimap_position, landing)
            for options, toward, landing in hops]


@pytest.mark.parametrize("case_id", IDS)
def test_the_table_literals(built, case_id):
    case, expected = built[case_id], EXPECTED[case_id]
    result = preview(case.rg, case.pos, case.target)
    if isinstance(expected, Fail):
        assert not is_successful(result)
        assert result.failure() == expected.message
        return
    pv = result.unwrap()
    assert pv.pos == case.pos and pv.target is case.target
    if expected == ARRIVED:
        assert pv.arrived
        assert (pv.starts, pv.start, pv.path, pv.hops, pv.cost_to_go_s) == ((), None, (), (), 0.0)
        assert pv.complete is True  # the docstring's "arrived is complete"
        return
    cost, hops = expected
    assert not pv.arrived
    assert [(hop.options, hop.toward.minimap_position, hop.landing) for hop in pv.hops] == _expected_hops(case, hops)
    assert pv.cost_to_go_s == pytest.approx(cost)
    assert pv.complete is True
    assert [hop.from_pos for hop in pv.hops] == [case.pos] + [hop.landing for hop in pv.hops[:-1]]


# ---- 3. the six planner branches are really taken (spec S3) ----

_BRANCH_LINES = {
    152: "return None",
    174: "return Move(MoveKind.TELEPORT, Key.DOWN, None)",  # `_go_down`: the teleport reaches (H87)
    175: "return Move(MoveKind.DROP, None, None)",  # `_go_down`: only a drop does
    196: "return Failure(anchor_result.failure())",
    208: 'return Failure(f"no path from ',
    238: "g_next = path[_walk_run_end(rg, path, gi + 1)]",
}


def test_s3_the_table_takes_the_six_planner_branches(built):
    lines = Path(planner.__file__).read_text(encoding="utf-8").splitlines()
    for number, text in _BRANCH_LINES.items():  # a moved line fails here, not by tracing the wrong statement
        assert lines[number - 1].strip().startswith(text), (number, lines[number - 1])
        if number != 208:
            assert lines[number - 1].strip() == text, (number, lines[number - 1])

    planner_file = planner.__file__
    recorded = set()

    def local(frame, event, arg):
        if event == "line":
            recorded.add((frame.f_code.co_filename, frame.f_lineno))
        return local

    def global_trace(frame, event, arg):  # 'call': a local tracer for planner.py's frames only
        return local if frame.f_code.co_filename == planner_file else None

    starts = []
    for case_id in IDS:
        case = built[case_id]
        result = preview(case.rg, case.pos, case.target)
        hops = result.unwrap().hops if is_successful(result) else ()
        starts += [(case, case.pos)] + [(case, hop.from_pos) for hop in hops]
    previous = sys.gettrace()
    sys.settrace(global_trace)
    try:
        for case, pos in starts:
            next_move(case.rg, pos, case.target, np.random.default_rng(0))
    finally:
        sys.settrace(previous)
    assert {(planner_file, number) for number in _BRANCH_LINES} <= recorded, sorted(
        number for number in _BRANCH_LINES if (planner_file, number) not in recorded
    )


# ---- 4. starts ----

def test_starts_on_a_slope_segment_are_both_ends_left_before_right(built):
    case = built["T22"]
    rg, node = case.rg, case.node
    left, right = node((66, 28)), node((79, 24))
    offered = planner._offered(rg)  # V4: cost_to on the offered graph (FF2 offers every edge, so it is rg itself)
    c1 = offered.cost_to(left, case.target).unwrap()
    c2 = offered.cost_to(right, case.target).unwrap()
    assert c1 == rg.cost_to(left, case.target).unwrap() and c2 == rg.cost_to(right, case.target).unwrap()
    pv = _preview(case)
    assert [(s.node, s.cost_s) for s in pv.starts] == [(left, 6 / rg.params.walk_px_per_s + c1), (right, 7 / rg.params.walk_px_per_s + c2)]
    assert pv.start is left


def test_a_node_position_has_one_start_at_its_cost_to_the_target(built):
    case = built["T1"]
    pv = _preview(case)
    start = case.node((49, 28))
    assert [(s.node, s.cost_s) for s in pv.starts] == [(start, case.rg.cost_to(start, case.target).unwrap())]
    assert pv.start is start


def test_a_start_cost_tie_goes_to_the_left_start():
    # two lower nodes, one a lift under each end of the upper platform, the target in the middle of it, and the
    # character midway between the lower nodes: both starts cost 2.55 s, and the left one is chosen (V4, _starts order)
    nodes, rg = _synthetic([(10, 40), (26, 40), (10, 20), (18, 20), (26, 20)])
    pv = preview(rg, (18, 40), nodes[3]).unwrap()
    assert [s.node for s in pv.starts] == [nodes[0], nodes[1]]
    assert pv.starts[0].cost_s == pv.starts[1].cost_s
    assert pv.start is nodes[0] and pv.path[0] is nodes[0]


@pytest.mark.parametrize("case_id", ROUTES)
def test_the_path_runs_from_the_start_to_the_target(built, case_id):
    case = built[case_id]
    pv = _preview(case)
    assert pv.path[0] is pv.start and pv.path[-1] is case.target
    assert pv.start in [s.node for s in pv.starts]


# ---- 5. max_hops ----

def test_max_hops_stops_the_expansion_and_keeps_the_hops_so_far(built):
    case = built["T1"]
    full = _preview(case)
    assert len(full.hops) == 4 and full.complete

    def capped(n):
        return preview(case.rg, case.pos, case.target, max_hops=n).unwrap()

    one = capped(1)
    assert one.hops == full.hops[:1] and one.complete is False
    assert capped(4) == full
    zero = capped(0)
    assert zero.hops == () and zero.complete is False
    others = ("pos", "target", "arrived", "starts", "start", "cost_to_go_s", "path")
    for pv in (one, zero):
        assert all(getattr(pv, name) == getattr(full, name) for name in others)


# ---- 6. pure and draw-free ----

def test_preview_takes_no_generator():
    assert list(inspect.signature(preview).parameters) == ["rg", "pos", "target", "max_hops"]


def test_preview_is_repeatable_and_leaves_the_route_graph_unchanged(built):
    case = built["T3"]
    assert _preview(case) == _preview(case) == _preview(case)

    def snapshot(rg):
        return list(rg.graph.nodes), [(u, v, dict(data)) for u, v, data in rg.graph.edges(data=True)]

    graphs = {id(c.rg): c.rg for c in built.values()}
    before = {key: snapshot(rg) for key, rg in graphs.items()}
    for case_id in IDS:
        case = built[case_id]
        preview(case.rg, case.pos, case.target)
    assert {key: snapshot(rg) for key, rg in graphs.items()} == before


# ---- 7. a down hop's options (the coin flip until H87) ----

def test_a_down_hop_has_one_option_a_teleport_in_reach_else_a_drop(built):
    # H87: there is no coin flip, so no hop offers (DROP, TP_DOWN); T3 and T8 held the flip and a dear teleport before
    assert _preview(built["T3"]).hops[1].options == (TP_DOWN,)
    assert _preview(built["T2"]).hops[1].options == (TP_DOWN,)
    assert [hop.options for hop in _preview(built["T7"]).hops] == [(TP_DOWN,)]
    assert [hop.options for hop in _preview(built["T8"]).hops] == [(TP_DOWN,)]
    assert _preview(built["T11"]).hops[2].options == (DROP,)  # 35 px down, beyond a teleport's reach


# ---- 8. the landing rule, case by case ----

def test_landing_a_teleport_may_pass_its_aim_onto_a_slope(built):
    # V4, left/right bullet: a TELEPORT lands at px + reach_h (it may pass ax = 66); the y is the surface of the aim
    # node's platform at lx, the slope segment (66,28)-(79,24) at x 68, rounded
    case = built["T1"]
    hop = _preview(case).hops[0]
    assert hop.toward is case.node((66, 28))
    assert 68 == 49 + case.rg.reach_h
    y = 28 + (24 - 28) * (68 - 66) / (79 - 66)
    assert y == pytest.approx(27.38, abs=0.01) and round(y) == 27
    assert hop.landing == (68, 27)


def test_landing_a_teleport_past_a_platform_end_takes_the_end_nodes_y(built):
    # V4, left/right bullet: no platform in the order has a surface at x 6, so the y is that of the end of the aim
    # node's platform nearest x 6, the end node (10,10)
    case = built["T11"]
    hop = _preview(case).hops[0]
    assert 6 == 25 - case.rg.reach_h
    assert planner._surface_ys(case.rg.terrain.platforms[case.rg.terrain.platform_index(case.node(0))], 6) == []
    assert hop.landing == (6, case.node(0).minimap_position[1]) == (6, 10)


def test_landing_a_capped_walk_stops_short_of_its_aim(built):
    # V4, left/right bullet: 1000 * 11 / 12.5 = 880 > max_walk_ms 500, so lx = px + round(max_walk_ms * v / 1000)
    case = built["T20"]
    hop = _preview(case).hops[0]
    assert hop.options == (walk(R, 500),)
    assert hop.landing[0] == 43 == 37 + round(500 * 12.5 / 1000)


def test_landing_an_up_move_off_the_node_is_on_the_upper_platform_at_px(built):
    # V4, up/down bullet: px 25 != h.x 29, so the landing is (25, surface of h's platform at x 25)
    case = built["T18"]
    hop = _preview(case).hops[1]
    assert hop.options == (TP_UP,) and hop.from_pos == (25, 28)
    surface = planner._surface_ys(case.rg.terrain.platforms[case.rg.terrain.platform_index(case.node(3))], 25)
    assert surface == [10.0]
    assert hop.landing == (25, 10)


def test_landing_on_the_aim_nodes_x_is_the_nodes_own_position(built):
    # V4, both bullets: lx == an.x (or px == h.x) gives the node's own position
    case = built["T19"]
    hop = _preview(case).hops[0]
    assert hop.landing == case.node((48, 45)).minimap_position


# ---- 9. S3b on the table, exact (RV3-D5 A2) ----

@pytest.mark.parametrize("case_id", ROUTES)
def test_s3b_every_later_hop_agrees_with_next_move(built, case_id):
    case = built[case_id]
    pv = _preview(case)
    assert s3b_problems(case.rg, pv, case.target) == []
    if case_id not in TIED:
        assert follows(pv.path, pv.hops) is (case_id not in LEAVES_PATH)


def test_d1_is_an_exact_cost_tie_and_its_hops_follow_one_of_the_two_routes(built):
    # D1 is an exact tie at 12.5 px/s (H81) as it was at 8 px/s: from (129,60) the routes via (99,60) and via (105,42) both
    # cost 2.62 s (3.475 s at 8 px/s; the same two routes tie from (113,60) and (105,60) too). `rg.path` and the planner
    # each break a tie on their own (WALKSPD-R found `rg.path` going via (99,60) from (113,60) and via (105,42) from the
    # other two starts), so neither is asserted to match the other: the hops need only follow one of the two tied routes,
    # which holds whichever way either side breaks it.
    # The moves agree at every landing, which `s3b_problems` pins in the test above.
    case = built["D1"]
    rg, pv = case.rg, _preview(case)
    start = pv.start
    costs, routes = [], []
    for mid in ((99, 60), (105, 42)):
        mid_node = case.node(mid)
        first, second = rg.path(start, mid_node).unwrap(), rg.path(mid_node, case.target).unwrap()
        routes.append(first + second[1:])
        costs.append(rg.cost_to(start, mid_node).unwrap() + rg.cost_to(mid_node, case.target).unwrap())
    best = rg.cost_to(start, case.target).unwrap()
    assert costs[0] == costs[1] == best, "D1 is no longer a tie: pick another tied case, or drop the TIED special case"
    assert any(follows(route, pv.hops) for route in routes)


def test_s3b_problems_and_follows_report_a_wrong_hop_a_cut_expansion_and_a_stray_path(built):
    case = built["T1"]
    pv = _preview(case)
    assert s3b_problems(case.rg, pv, case.target) == []
    wrong = replace(pv.hops[2], options=(TP_L,))
    assert s3b_problems(case.rg, replace(pv, hops=(*pv.hops[:2], wrong, *pv.hops[3:])), case.target)
    assert s3b_problems(case.rg, replace(pv, hops=pv.hops[:-1], complete=False), case.target)
    assert follows(pv.path, pv.hops)
    assert not follows(pv.path[:1], pv.hops)  # a hop whose `toward` is not on the path
    assert not follows(tuple(reversed(pv.path)), pv.hops)  # hops going back down the path


# ---- 10. FF2 sweep ----

# The set at 12.5 px/s (H81); at 8 px/s it held nine pairs, among them D1 (the tie: still a tie at 12.5, but `path` and the
# hops now break it the same way from (129,60)) and the pairs to (66,28) and (42,28). D2 is the first pair below.
NOT_FOLLOWING = {
    ((105, 42), (58, 46)),  # the first hop walks to x 101 on the (101,42) piece; the re-plan from there goes down to
    ((118, 43), (58, 46)),  # (99,60) and along the bottom, where `path` went via (101,24) and the upper route
    ((109, 23), (48, 45)),  # the re-plan from (105,24) goes via (79,24) and the upper route, where `path` went down
    ((65, 60), (90, 24)),  # two teleports right overshoot the launch x 97; (103,60) anchors on (105,60)
    ((65, 60), (97, 43)),
    ((65, 60), (101, 24)),
    ((65, 60), (109, 23)),
}


@pytest.mark.slow
def test_ff2_sweep_every_ordered_node_pair():
    agent, rg = _ff2()
    nodes = list(agent.node_map.values())
    began = time.perf_counter()
    pairs, problems, not_following = 0, [], set()
    for a in nodes:
        for b in nodes:
            if a is b:
                continue
            pairs += 1
            problems += _first_hop_problems(rg, a.minimap_position, b)
            result = preview(rg, a.minimap_position, b)
            if not is_successful(result) or result.unwrap().arrived:  # V4: every FF2 pair has a route and a hop
                problems.append(f"{a.minimap_position} -> {b.minimap_position}: no hops: {result}")
                continue
            pv = result.unwrap()
            problems += s3b_problems(rg, pv, b)
            if not follows(pv.path, pv.hops):
                not_following.add((a.minimap_position, b.minimap_position))
    print(f"FF2 sweep: {pairs} ordered pairs in {time.perf_counter() - began:.2f} s")
    assert pairs == 420
    assert problems == []
    assert not_following == NOT_FOLLOWING  # a change is a behaviour change: report it with the pair's hops
