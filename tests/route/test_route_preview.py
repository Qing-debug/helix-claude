"""Tests for src.route.route_preview (plan Task RV-4, contract V4): the hand-built agreement table (spec S3 part 3)
and S3b on it, the starts, `max_hops`, purity, the coin flip, the landing rule, and the FF2 sweep.

The expected values in the table were computed at planning time by a prototype of V4's rule (RV3b notes). A literal
that disagrees with V4's rule text is a plan error to report, not a reason to change the rule.
"""

import inspect
import sys
import time
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
COIN = (DROP, TP_DOWN)  # a coin-flip down hop's two options, in V4's order


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

CASES = [
    ("T1", FF2, (49, 28), (101, 24), (5.175, [
        (TP_R, (66, 28), (68, 27)),
        (walk(R, 1375), (79, 24), (79, 24)),
        (TP_R, (101, 24), (98, 24)),
        (walk(R, 375), (101, 24), (101, 24)),
    ])),
    ("T2", ff2(ability_mix_band=0), (101, 24), (105, 42), (1.05, [
        (walk(R, 500), (105, 42), (105, 24)),
        (TP_DOWN, (105, 42), (105, 42)),
    ])),
    ("T3", FF2, (101, 24), (105, 42), (1.05, [
        (walk(R, 500), (105, 42), (105, 24)),
        (COIN, (105, 42), (105, 42)),
    ])),
    ("T4", FF2, (104, 43), (105, 42), ARRIVED),
    ("T5", FF2, (105, 45), (105, 42), ARRIVED),
    ("T6", syn([(10, 10), (10, 28)]), (10, 10), 1, (0.55, [
        (TP_DOWN, 1, (10, 28)),
    ])),
    ("T7", syn([(10, 10), (10, 28)], can_fall=True, ability_mix_band=0), (10, 10), 1, (0.55, [
        (TP_DOWN, 1, (10, 28)),
    ])),
    ("T8", ff2(ability_mix_band=0, teleport_s=0.9), (105, 23), (105, 42), (1.65, [
        (DROP, (105, 42), (105, 42)),
    ])),
    ("T9", ff2(('"(79, 24)"', '"(79, 22)"')), (77, 25), (101, 24), (3.0, [
        (TP_R, (101, 24), (96, 24)),
        (walk(R, 625), (101, 24), (101, 24)),
    ])),
    ("T10", ff2(('"(97, 43)"', '"(97, 45)"'), ('"(105, 42)"', '"(105, 40)"'), ability_mix_band=0),
     (99, 24), (105, 40), (1.05, [
        (walk(R, 750), (105, 40), (105, 24)),
        (TP_DOWN, (105, 40), (105, 40)),
    ])),
    ("T11", syn([(10, 10), (25, 10), (29, 28), (45, 28), (5, 45), (20, 45), (35, 45), (50, 45)], can_fall=True),
     (25, 10), 2, (7.8583333, [
        (TP_L, 4, (6, 10)),
        (walk(R, 500), 4, (10, 10)),
        (DROP, 4, (10, 45)),
        (TP_R, 3, (29, 45)),
        (TP_R, 3, (48, 45)),
        (walk(L, 375), 3, (45, 45)),
        (TP_UP, 3, (45, 28)),
        (TP_L, 2, (26, 28)),
        (walk(R, 375), 2, (29, 28)),
    ])),
    ("T12", syn([(10, 60), (25, 60), (29, 50), (45, 50), (6, 38), (18, 38), (30, 38)]), (25, 60), 2, (5.625, [
        (TP_L, 4, (6, 60)),
        (walk(R, 500), 4, (10, 60)),
        (TP_UP, 4, (10, 38)),
        (TP_R, 2, (29, 38)),
        (TP_DOWN, 2, (29, 50)),
    ])),
    ("T13", FF2, (86, 37), (99, 60), Fail("off-graph: (86, 37)")),
    ("T14", syn([(10, 10), (100, 10)]), (10, 10), 1, Fail("no path from (10, 10) to (100, 10)")),
    ("T15", syn([(10, 10), (25, 10), (29, 28), (45, 28)]), (25, 10), 2, Fail("no path from (25, 10) to (29, 28)")),
    ("T16", FF2, (113, 60), STRANGER, Fail("target (113, 60) is not in the route graph")),
    ("T17", syn([(10, 10), (25, 10), (40, 10), (29, 28), (45, 28)]), (25, 10), 3, (1.05, [
        (walk(R, 500), 3, (29, 10)),
        (TP_DOWN, 3, (29, 28)),
    ])),
    ("T18", syn([(10, 28), (25, 28), (20, 10), (29, 10)]), (21, 28), 3, (1.55, [
        (walk(R, 500), 3, (25, 28)),
        (TP_UP, 3, (25, 10)),
        (walk(R, 500), 3, (29, 10)),
    ])),
    ("T19", FF2, (48, 61), (58, 46), (1.8, [
        (TP_UP, (48, 45), (48, 45)),
        (walk(R, 1250), (58, 46), (58, 46)),
    ])),
    ("T20", ff2(max_walk_ms=1000), (37, 60), (48, 61), (1.375, [
        (walk(R, 1000), (48, 61), (45, 61)),
        (walk(R, 375), (48, 61), (48, 61)),
    ])),
    ("T21", ff2(max_walk_ms=1000), (66, 28), (101, 24), (4.375, [
        (walk(R, 1000), (79, 24), (74, 26)),
        (walk(R, 625), (79, 24), (79, 24)),
        (TP_R, (101, 24), (98, 24)),
        (walk(R, 375), (101, 24), (101, 24)),
    ])),
    ("T22", FF2, (72, 26), (49, 28), (1.55, [
        (walk(L, 750), (66, 28), (66, 28)),
        (TP_L, (49, 28), (47, 28)),
    ])),
    ("T23", FF2, (82, 60), (99, 60), (0.8, [
        (TP_R, (99, 60), (101, 60)),
    ])),
    ("T24", FF2, (48, 64), (58, 46), (1.8, [
        (TP_UP, (48, 45), (48, 45)),
        (walk(R, 1250), (58, 46), (58, 46)),
    ])),
    ("D1", FF2, (129, 60), (97, 43), (3.475, [
        (TP_L, (97, 43), (110, 60)),
        (walk(L, 625), (105, 42), (105, 60)),
        (TP_UP, (105, 42), (105, 42)),
        (walk(L, 1000), (97, 43), (97, 43)),
    ])),
]
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


def test_s3_the_table_has_two_option_hops_for_the_seed_check(built):
    assert [k for k, hop in enumerate(_preview(built["T3"]).hops) if len(hop.options) == 2] == [1]


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
    172: "choice = next(iter(options))",
    178: "choice = cheap",
    201: "return Failure(anchor_result.failure())",
    213: 'return Failure(f"no path from ',
    243: "g_next = path[_walk_run_end(rg, path, gi + 1)]",
}


def test_s3_the_table_takes_the_six_planner_branches(built):
    lines = Path(planner.__file__).read_text(encoding="utf-8").splitlines()
    for number, text in _BRANCH_LINES.items():  # a moved line fails here, not by tracing the wrong statement
        assert lines[number - 1].strip().startswith(text), (number, lines[number - 1])
        if number != 213:
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
    assert [(s.node, s.cost_s) for s in pv.starts] == [(left, 6 / 8 + c1), (right, 7 / 8 + c2)]
    assert pv.start is left


def test_a_node_position_has_one_start_at_its_cost_to_the_target(built):
    case = built["T1"]
    pv = _preview(case)
    start = case.node((49, 28))
    assert [(s.node, s.cost_s) for s in pv.starts] == [(start, case.rg.cost_to(start, case.target).unwrap())]
    assert pv.start is start


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


# ---- 7. the coin flip's options ----

def test_the_coin_flip_gives_both_options_drop_first_and_a_clear_choice_one(built):
    assert _preview(built["T3"]).hops[1].options == (DROP, TP_DOWN)
    assert _preview(built["T2"]).hops[1].options == (TP_DOWN,)
    assert [hop.options for hop in _preview(built["T7"]).hops] == [(TP_DOWN,)]
    assert [hop.options for hop in _preview(built["T8"]).hops] == [(DROP,)]


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
    # V4, left/right bullet: 1000 * 11 / 8 = 1375 > max_walk_ms 1000, so lx = px + round(max_walk_ms * v / 1000)
    case = built["T20"]
    hop = _preview(case).hops[0]
    assert hop.options == (walk(R, 1000),)
    assert hop.landing[0] == 45 == 37 + round(1000 * 8 / 1000)


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
    # D1 is an exact cost tie, not a re-route: from (129,60) both routes to (97,43) cost 3.475 s (via (99,60) and up
    # at x 97, or via (105,42) and up at x 105), and from hop 1's landing (110,60) both cost 2.175 s. `rg.path` breaks
    # the tie one way from (129,60) and the other way from (105,60), so `path` goes via (99,60) while the hops go via
    # (105,42). The moves still agree at every landing, which `s3b_problems` pins.
    assert follows(pv.path, pv.hops) is (case_id != "D1")


# ---- 10. FF2 sweep ----

NOT_FOLLOWING = {
    ((129, 60), (97, 43)),  # D1, the tie above
    ((105, 42), (66, 28)),  # hop 2's re-plan from the UP's launch x goes via (97,43), down and along the bottom
    ((118, 43), (66, 28)),
    ((109, 23), (49, 28)),  # the re-plan from (105,24) goes via (101,24) and the upper route
    ((109, 23), (42, 28)),
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
            if not is_successful(result) or result.unwrap().arrived:
                continue
            pv = result.unwrap()
            problems += s3b_problems(rg, pv, b)
            if not follows(pv.path, pv.hops):
                not_following.add((a.minimap_position, b.minimap_position))
    print(f"FF2 sweep: {pairs} ordered pairs in {time.perf_counter() - began:.2f} s")
    assert pairs == 420
    assert problems == []
    assert not_following == NOT_FOLLOWING  # a change is a behaviour change: report it with the pair's hops
