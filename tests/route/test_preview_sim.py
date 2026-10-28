"""Tests for the route preview over the two 20-lap simulator runs (route view plan task RV-5; spec 7 checks S3, S3b).

Two runs of the FF2 fixture map, seed 1, 20 laps: the plain run and the run with node offsets
(`NoiseConfig(node_offset_px=2)`), each written to an `--out` folder. The route graph is rebuilt from the folder alone
(`map.csv` and the header's params), as the viewer will, and every logged event is compared with `preview`.

What these tests pin (pass/fail, `==` and `is`, no tolerance anywhere):
- (1) the runs themselves: the folders read back whole, the move counts of spec 7, every leg target (checked in `_steps`,
  where the previews are built, so a bad target fails the fixture with a message) and logged `toward` is a node of the
  rebuilt graph.
- (2) S3: for every `planned` event the preview's cost to go, `toward` and first move equal the logged ones (a coin-flip
  down is one of the two options); a `reached` event is an arrived preview; a `no_plan` event is a preview Failure with
  the logged message (none occurs in either run today).
- (3) S3b, the pass/fail part (RV3-D5 A2): `s3b_problems` is empty for every `planned` event, i.e. each later hop equals
  `next_move` at the landing it starts from and the preview from the earlier landing gives exactly the rest.

What they only print, because spec S3b says "reported, not required" (RV3-D5 A2):
- (4) how often the hops leave the minimum-cost `path` at an assumed landing (`follows` False);
- (5) the real-landing agreement rate: of two `planned` events in a row in a leg with a `settled` between them, how
  many times the second plan is what the first preview expected next, judged by the view's own `route_changed`
  (`src.route_view.model`, which compares `hop_route`s), so the printed rate and the view's "route changed" lines are
  one comparison. The
  one assertion on it is the exact-landing part: a pair that landed where the preview assumed must agree (by S3b), so a
  failure there is a bug in `route_changed`, `hop_route` or the pairing, not planner behaviour.

Run with `-s` to see the printed counts. The whole file is `slow`: two 20-lap runs and about 700 previews with their
sub-previews.
"""

import contextlib
import io
from dataclasses import dataclass
from pathlib import Path

import pytest
from preview_checks import follows, s3b_problems
from returns.pipeline import is_successful
from returns.result import Result

from src.classes.classes import Kanna
from src.controller.controller import MapleAgent
from src.controller.node import Node
from src.input.protocol import Key
from src.route.params import MovementParams
from src.route.planner import Move, MoveKind
from src.route.route_graph import RouteGraph
from src.route.route_preview import Preview, preview
from src.route.sim.run import SimReport, run_sim
from src.route.sim.world import NoiseConfig
from src.route.step_log import MAP_COPY, RunSteps, StepEvent, read_run
from src.route_view.model import route_changed

pytestmark = pytest.mark.slow

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
CSV = FIXTURES / "fossil_foundry_2.csv"
TERRAIN = FIXTURES / "ff2_terrain.json"

LAPS, SEED = 20, 1
# The moves of each run (spec 7 for the plain run; RV-3's PINNED_OFFSET_REPORT for the offset run).
MOVES = {"plain": 379, "offset": 356}

Pos = tuple[int, int]


@dataclass(frozen=True)
class _Run:
    name: str
    report: SimReport
    steps: RunSteps
    rg: RouteGraph
    by_pos: dict[Pos, Node]  # node position -> node of the rebuilt graph
    previews: dict[int, Result[Preview, str]]  # event index -> preview, for each planned, no_plan and reached event


def _rebuild_graph(folder: Path, header_params) -> RouteGraph:
    """The route graph from the folder alone: its `map.csv` and the header's params (as the viewer builds it)."""
    agent = MapleAgent(characterClass=Kanna())
    with contextlib.redirect_stdout(io.StringIO()):  # the CSV parser prints its progress
        loaded = agent.loadMap(str(folder / MAP_COPY))
    assert is_successful(loaded), loaded
    built = RouteGraph.build(agent.pathGraph, MovementParams(**header_params))
    assert is_successful(built), built
    return built.unwrap()


def _steps(events, by_pos):
    """`(index, event, target)` for every planned, no_plan and reached event; `target` is the node of its leg's `to`."""
    target = None
    for i, ev in enumerate(events):
        if ev.ev == "leg":
            target = by_pos.get(ev.data["to"])
            assert target is not None, f"event {i} (leg): its target {ev.data['to']} is not a node of the rebuilt graph"
        elif ev.ev in ("planned", "no_plan", "reached"):
            assert target is not None, f"event {i} ({ev.ev}) comes before any leg"
            yield i, ev, target


def _logged_move(ev: StepEvent) -> Move:
    """The move a `planned` event logged (V2 logs `move.direction.name`; `Key` is an IntFlag, so `Key["RIGHT"]`)."""
    direction = ev.data["dir"]
    return Move(MoveKind(ev.data["kind"]), Key[direction] if direction is not None else None, ev.data["walk_ms"])


def _make_run(name: str, root: Path, noise: NoiseConfig) -> _Run:
    folder = root / name
    ran = run_sim(CSV, TERRAIN, LAPS, SEED, noise=noise, out_dir=folder)
    assert is_successful(ran), ran
    read = read_run(folder)
    assert is_successful(read), read
    steps = read.unwrap()
    rg = _rebuild_graph(folder, steps.header.params)
    by_pos = {node.minimap_position: node for node in rg.graph.nodes()}
    assert len(by_pos) == rg.graph.number_of_nodes(), "two nodes of the rebuilt graph share a position"
    previews = {
        i: preview(rg, ev.data["pos"], target) for i, ev, target in _steps(steps.events, by_pos)
    }
    return _Run(name, ran.unwrap(), steps, rg, by_pos, previews)


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    root = tmp_path_factory.mktemp("preview_sim")
    return (
        _make_run("plain", root, NoiseConfig()),
        _make_run("offset", root, NoiseConfig(node_offset_px=2)),
    )


def _planned(run: _Run):
    """`(index, event, target, preview)` for every planned event; its preview must be a Success."""
    for i, ev, target in _steps(run.steps.events, run.by_pos):
        if ev.ev == "planned":
            got = run.previews[i]
            assert is_successful(got), f"{run.name} event {i} pos {ev.data['pos']}: preview failed: {got.failure()}"
            yield i, ev, target, got.unwrap()


def _where(run: _Run, i: int, ev: StepEvent, target: Node) -> str:
    return f"{run.name} event {i} pos {ev.data['pos']} target {target.minimap_position}"


def _assert_none(problems: list[str], what: str) -> None:
    assert not problems, f"{what}: {len(problems)} mismatches, the first 10:\n" + "\n".join(problems[:10])


# (1)
def test_1_the_runs_read_back_whole_with_the_planned_counts_and_every_position_a_node(runs):
    for run in runs:
        events = run.steps.events
        assert run.steps.origin == "steps" and run.steps.ended and run.steps.bad_lines == 0, run.name
        assert events[-1].ev == "end" and events[-1].data["reason"] == "max_laps", run.name
        counts = {ev: sum(e.ev == ev for e in events) for ev in ("planned", "no_plan", "reached")}
        assert counts["planned"] == run.report.moves == MOVES[run.name], (run.name, counts, run.report.moves)
        toward = [e.data["toward"] for e in events if e.ev == "planned"]
        assert all(pos in run.by_pos for pos in toward), run.name
        coin = sum(len(pv.hops[0].options) == 2 for _, _, _, pv in _planned(run))
        print(f"inputs ({run.name}): planned {counts['planned']}, no_plan {counts['no_plan']}, "
              f"reached {counts['reached']}; previews whose hop 1 has two options {coin}")


# (2)
def test_2_s3_the_preview_agrees_with_every_logged_event_exactly(runs):
    problems: list[str] = []
    for run in runs:
        for i, ev, target in _steps(run.steps.events, run.by_pos):
            where, got = _where(run, i, ev, target), run.previews[i]
            if ev.ev == "no_plan":
                if is_successful(got):
                    problems.append(f"{where}: logged no_plan {ev.data['why']!r}, the preview succeeded")
                elif got.failure() != ev.data["why"]:
                    problems.append(f"{where}: preview failed with {got.failure()!r}, logged {ev.data['why']!r}")
                continue
            if not is_successful(got):
                problems.append(f"{where}: logged {ev.ev}, the preview failed: {got.failure()}")
                continue
            pv = got.unwrap()
            if ev.ev == "reached":
                if not pv.arrived:
                    problems.append(f"{where}: logged reached, the preview is not arrived")
                continue
            logged = ev.data["cost_to_go_s"]
            if pv.arrived or not pv.hops:
                problems.append(f"{where}: logged planned, the preview is arrived or has no hops")
                continue
            hop = pv.hops[0]
            if pv.cost_to_go_s != logged:
                problems.append(f"{where}: cost to go {pv.cost_to_go_s!r}, logged {logged!r}")
            if hop.cost_to_go_s != logged:
                problems.append(f"{where}: hop 1 cost to go {hop.cost_to_go_s!r}, logged {logged!r}")
            if hop.toward.minimap_position != ev.data["toward"]:
                problems.append(f"{where}: toward {hop.toward.minimap_position}, logged {ev.data['toward']}")
            if _logged_move(ev) not in hop.options:
                problems.append(f"{where}: logged move {_logged_move(ev)}, hop 1 offers {hop.options}")
    _assert_none(problems, "S3 over both sim runs")


# (3)
def test_3_s3b_every_later_hop_equals_next_move_at_its_landing_exactly(runs):
    problems: list[str] = []
    for run in runs:
        previews = hops = 0
        for i, ev, target, pv in _planned(run):
            previews += 1
            hops += len(pv.hops)
            problems += [f"{_where(run, i, ev, target)}: {p}" for p in s3b_problems(run.rg, pv, target)]
        print(f"S3b ({run.name}): {previews} previews, {hops} hops checked")
    _assert_none(problems, "S3b over both sim runs")


# (4)
def test_4_departures_from_path_are_reported_only(runs):
    for run in runs:
        previews = list(_planned(run))
        departures = sum(not follows(pv.path, pv.hops) for _, _, _, pv in previews)
        print(f"hops leave the planned route at an assumed landing: {departures} of {len(previews)} previews "
              f"({run.name})")
        # PF81 m1: reported, not asserted (a count can only be checked against itself); this pins that every
        # planned event was looked at
        assert len(previews) == sum(e.ev == "planned" for e in run.steps.events) > 0


def _pairs(events) -> list[tuple[int, int]]:
    """V5's "Route changed" pairs as `(P index, Q index)`: two `planned` events in a row inside one leg, with no
    `no_plan` or `reached` between them and at least one `settled` between them (a `settled` with pos None counts)."""
    pairs: list[tuple[int, int]] = []
    last: int | None = None
    settled = False
    for i, ev in enumerate(events):
        if ev.ev == "planned":
            if last is not None and settled:
                pairs.append((last, i))
            last, settled = i, False
        elif ev.ev == "settled":
            settled = True
        elif ev.ev in ("leg", "leg_end", "no_plan", "reached"):
            last, settled = None, False
    return pairs


# (5)
def test_5_the_real_landing_agreement_rate_and_the_exact_landing_pairs(runs):
    problems: list[str] = []
    for run in runs:
        events = run.steps.events
        pairs = _pairs(events)
        agreeing = exact = length_only = 0
        for p_index, q_index in pairs:
            q_ev = events[q_index]
            pv_p, pv_q = run.previews[p_index].unwrap(), run.previews[q_index].unwrap()
            changed = route_changed(pv_p, _logged_move(q_ev), q_ev.data["toward"], pv_q)
            agreeing += not changed
            if not changed and _logged_move(q_ev) not in pv_p.hops[1].options:
                length_only += 1  # (kind, direction) as expected, the walk's length is not (RV6 m6)
            if q_ev.data["pos"] == pv_p.hops[0].landing:
                exact += 1
                if changed:
                    problems.append(f"{run.name} events {p_index} -> {q_index}: landed on the assumed "
                                    f"{pv_p.hops[0].landing} and route_changed is True")
        print(f"real-landing agreement: {agreeing} of {len(pairs)} pairs ({run.name})")
        print(f"pairs that landed exactly where the preview assumed: {exact} ({run.name})")
        print(f"agreeing pairs whose walk length differs: {length_only} ({run.name})")
        assert len(pairs) > 0, run.name
    _assert_none(problems, "pairs that landed exactly on the assumed landing")
