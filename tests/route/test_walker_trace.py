"""Every call the walker makes, in order, on the simulator's legs (cleanup piece W1-7, R20; Hong's H140 and H141).

W1-7 splits `RouteWalker.walk_leg` into private helpers inside walker.py, extract-method only. This test was written
and its digests pinned on the code before the split (f65ab67), so it shows the split changed no call. Each scenario runs
`run_sim` with recording fakes around what every walker gets (the run's walker and each recovery replay's, built through
`sim_run.RouteWalker` and `sim_recovery.RouteWalker`): the clock (each `now_ns` and the value it gave, each `sleep` and
its seconds), the position source (each read and its result), the rope zones (each `at` and `mark`), the `SimLink` under
the `InputLayer` (each `play` with its steps and result, each `release_all` on the walker's thread), the observer (each
V2 call and its arguments), `log` (each line), `wait_frame` (each timeout), the leg sink (each record written), each
`walk_leg` record and `run` result with the planner rng's state after it, and the walker module's own calls of
`next_move`, `attach`, `attach_standing`, `rope_move` and `execute` (patched where the walker uses them, tests/CLAUDE.md).
The `SimReport` closes the trace. A trace that differs in any call, argument, result or order changes its digest. On a
mismatch the trace is written to pytest's tmp_path; run this file on the old commit with `HELIX_TRACE_DIR=<folder>` set
to get the old traces, then diff the two.

The scenarios: seed 1 and seed 2 on Fossil Foundry 2 (seed 1 with and without an observer), seed 2 on its real ground
(the root CLAUDE.md smoke, with the rope file's marks), seed 1 with many no-ops and rope grabs (the rope rule) and a
`wait_frame` waiter, seed 2 with faults (an observer that raises once, stale reads, a refused send, a lost link), reads
held still where the map places them (stalls, a leg timeout) and just off a platform (the still rule's snap) with two
injected "no path" plans, a read far off the map (`off_map`), and a stop seen at each of the loop's three stop checks.
"""

import hashlib
import itertools
import os
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import pytest
from returns.pipeline import is_successful
from returns.result import Failure, Result, Success

import src.route.sim.recovery as sim_recovery
import src.route.sim.run as sim_run
import src.route.walker as walker_module
from src.input.link import InputError, SendReport
from src.input.protocol import Step
from src.route.leg_log import LapRecord, LegRecord, LegSink
from src.route.ports import STALE, Clock, PositionSource
from src.route.ropes import RopeZones
from src.route.sim.link import SimLink
from src.route.sim.run import REAL_GROUND_NOISE, REAL_GROUND_SIM, run_sim
from src.route.sim.world import NoiseConfig, SimParams
from src.route.walker import RouteWalker, RunSummary

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
CSV = FIXTURES / "fossil_foundry_2.csv"          # with fossil_foundry_2.ropes.json beside it
DEFAULT_TERRAIN = FIXTURES / "ff2_terrain.json"
REAL_TERRAIN = FIXTURES / "ff2_real_ground.json"


def _plain(value: object) -> str:
    """A stable text for a traced value: a `Result` as its side and its value, anything else as its repr. Nothing
    traced holds a `Node` (its repr would carry its uuid)."""
    if isinstance(value, Result):
        side = "Success" if is_successful(value) else "Failure"
        inner = value.unwrap() if is_successful(value) else value.failure()
        return f"{side}({_plain(inner)})"
    if isinstance(value, tuple):
        return "(" + ", ".join(_plain(v) for v in value) + ")"
    return repr(value)


class _Trace:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def add(self, who: str, what: str, *args: object) -> None:
        self.lines.append(" ".join((who, what, *(_plain(a) for a in args))))


@dataclass(frozen=True)
class _Case:
    terrain: Path = DEFAULT_TERRAIN
    laps: int = 20
    seed: int = 1
    noise: NoiseConfig = NoiseConfig()
    sim_params: SimParams = SimParams()
    observer: bool = True
    observer_fails_at: int = 0          # the observer's Nth call raises (after it is recorded)
    stop_on: tuple[str, int] = ("", 0)  # ("sent", N): the run walker's `stop_now` in the observer's Nth `sent` call;
    #                                     ("next_move", N): in the Nth `next_move` that returns a move
    waiter: bool = False                # every walker gets a `wait_frame` that sleeps the timeout on the sim clock
    stale_every: int = 0                # every Nth read of the run walker is STALE and the source is not asked
    held_reads: tuple[tuple[int, int, tuple[int, int]], ...] = ()  # (first, last, pos): those reads of the run walker
    #                                     (counting from 1) give pos and the source is not asked
    no_path_at: tuple[int, ...] = ()    # those `next_move` calls (counting from 1) return Failure("no path (test)")
    link_faults: tuple[tuple[int, InputError], ...] = ()  # (N, error): the run's link fails its Nth play


_SCENARIOS = {
    "ff2_seed1": _Case(),
    "ff2_seed1_no_observer": _Case(observer=False),
    "real_ground_seed2": _Case(REAL_TERRAIN, seed=2, noise=REAL_GROUND_NOISE, sim_params=REAL_GROUND_SIM),
    "ropes_seed1_waiter": _Case(laps=10, noise=NoiseConfig(no_op_p=0.5, rope_grab_p=0.5), waiter=True),
    "faults_seed2": _Case(seed=2, observer_fails_at=300, stale_every=23,
                          link_faults=((40, InputError.REJECTED), (41, InputError.ACK_TIMEOUT),
                                       (120, InputError.LINK_LOST))),
    "held_seed1": _Case(laps=10, waiter=True, held_reads=((100, 800, (90, 60)), (1200, 1260, (95, 55))),
                        no_path_at=(60, 61, 405)),
    "off_map_seed2": _Case(seed=2, held_reads=((300, 10**9, (5, 5)),)),
    "stop_at_top_seed1": _Case(stop_on=("settled", 30)),
    "stop_in_settle_seed1": _Case(stop_on=("sent", 40)),
    "stop_before_send_seed2": _Case(seed=2, stop_on=("next_move", 60)),
}

# name: (trace lines, sha-256 of the lines joined by "\n"), taken on f65ab67, before the split
_PINS: dict[str, tuple[int, str]] = {
    "faults_seed2": (3417, "09c7d5b18b0c31c6bf89187f2fb4d04fec6bfc15678e22820abe3276317092a8"),
    "ff2_seed1": (10751, "cb3a8702046d34e663f9364735f489b846a78958713d3a9e7f82a810900debed"),
    "ff2_seed1_no_observer": (8582, "48287c81c5958a954ef8a2be23df40119b481fd81340fca96566f1bbd3799c8b"),
    "held_seed1": (10443, "ba3d3243de0b999d049f8cfcd3c1aa3b5487febe5c22b26d43a96259df2ca3e8"),
    "off_map_seed2": (2196, "0b5b793eef261077f3b4300e38ff7740d50ed639606abafded8bf76dc18482ad"),
    "real_ground_seed2": (11277, "e86849c6f0b40589f0d9e6ed05141b88ddaeb26f323ac7d61cda6379baad49c2"),
    "ropes_seed1_waiter": (9183, "6756b13e5bddbfd1167a77892772fde0677068a521ac7e2dcc9612acdb8a8120"),
    "stop_at_top_seed1": (985, "945f02e875e4d3671fb000fc2dbcd565d9f8b8cf662e8c8e41cf35077f61b5f0"),
    "stop_before_send_seed2": (1671, "8e61950d5d5060dcfc28ad1c2b82d1bc530e2d5bf74734f10e676c876a8b6478"),
    "stop_in_settle_seed1": (1335, "b995c66b1246a324ef8b571e102b5e1f8d807e161c84fc8cd643cba0c810f411"),
}


class _Clock:
    def __init__(self, inner: Clock, trace: _Trace, tag: str) -> None:
        self._inner, self._trace, self._tag = inner, trace, tag

    def now_ns(self) -> int:
        t = self._inner.now_ns()
        self._trace.add(self._tag, "now_ns", t)
        return t

    def sleep(self, seconds: float) -> None:
        self._trace.add(self._tag, "sleep", seconds)
        self._inner.sleep(seconds)


class _Positions:
    def __init__(self, inner: PositionSource, trace: _Trace, tag: str, case: _Case | None) -> None:
        self._inner, self._trace, self._tag, self._case = inner, trace, tag, case
        self._reads = 0

    def read(self) -> Result[tuple[int, int], str]:
        self._reads += 1
        n, case = self._reads, self._case
        held = None if case is None else next((pos for a, b, pos in case.held_reads if a <= n <= b), None)
        got: Result[tuple[int, int], str]
        if case is not None and case.stale_every and n % case.stale_every == 0:
            got = Failure(STALE)
        elif held is not None:
            got = Success(held)
        else:
            got = self._inner.read()
        self._trace.add(self._tag, "read", got)
        return got


class _Ropes:
    def __init__(self, inner: RopeZones, trace: _Trace, tag: str) -> None:
        self._inner, self._trace, self._tag = inner, trace, tag

    def at(self, pos: tuple[int, int]) -> int | None:
        got = self._inner.at(pos)
        self._trace.add(self._tag, "ropes.at", pos, got)
        return got

    def mark(self, index: int):
        got = self._inner.mark(index)
        self._trace.add(self._tag, "ropes.mark", index, got)
        return got


class _Sink:
    def __init__(self, inner: LegSink, trace: _Trace, tag: str) -> None:
        self._inner, self._trace, self._tag = inner, trace, tag

    def write_leg(self, rec: LegRecord) -> Result[None, str]:
        self._trace.add(self._tag, "write_leg", rec)
        return self._inner.write_leg(rec)

    def write_lap(self, rec: LapRecord) -> Result[None, str]:
        self._trace.add(self._tag, "write_lap", rec)
        return self._inner.write_lap(rec)


class _Observer:
    def __init__(self, trace: _Trace, case: _Case, walkers: list[RouteWalker]) -> None:
        self._trace, self._case, self._walkers = trace, case, walkers
        self._calls = 0
        self._by_name: dict[str, int] = {}

    def _rec(self, name: str, *args: object) -> None:
        self._calls += 1
        self._by_name[name] = self._by_name.get(name, 0) + 1
        self._trace.add("obs", name, *args)
        if (name, self._by_name[name]) == self._case.stop_on:
            self._walkers[0].stop_now()
        if self._calls == self._case.observer_fails_at:
            raise RuntimeError("observer fault")

    def leg(self, t_ns: int, lap: int, leg: int, src: tuple[int, int] | None, dst: tuple[int, int]) -> None:
        self._rec("leg", t_ns, lap, leg, src, dst)

    def read_failed(self, t_ns: int, why: str) -> None:
        self._rec("read_failed", t_ns, why)

    def no_plan(self, t_ns: int, pos: tuple[int, int], why: str) -> None:
        self._rec("no_plan", t_ns, pos, why)

    def reached(self, t_ns: int, pos: tuple[int, int]) -> None:
        self._rec("reached", t_ns, pos)

    def stall(self, t_ns: int, best_cost_s: float) -> None:
        self._rec("stall", t_ns, best_cost_s)

    def planned(self, t_ns: int, pos: tuple[int, int], kind: str, direction: str | None, walk_ms: float | None,
                cost_to_go_s: float, toward: tuple[int, int] | None) -> None:
        self._rec("planned", t_ns, pos, kind, direction, walk_ms, cost_to_go_s, toward)

    def sent(self, t_ns: int, input_name: str, reaction_ms: float | None, waited_ms: float | None,
             keys: tuple[tuple[int, int], ...]) -> None:
        self._rec("sent", t_ns, input_name, reaction_ms, waited_ms, keys)

    def settled(self, t_ns: int, pos: tuple[int, int] | None, stopped: bool) -> None:
        self._rec("settled", t_ns, pos, stopped)

    def leg_end(self, t_ns: int, outcome: str, misses: int, off_graph: int, stalls: int) -> None:
        self._rec("leg_end", t_ns, outcome, misses, off_graph, stalls)


def _link_class(trace: _Trace, case: _Case) -> type[SimLink]:
    """A `SimLink` that records its calls. The first one built is the run's link and gets the case's faults (through
    `SimLink.fail_next`). A `release_all` is recorded as ok or failed only, and only on the walker's thread: the stop
    guard's (every 50 ms of wall time, from its own thread) comes when the wall clock says, and each one moves the
    link's sequence number."""
    made = itertools.count()

    class _Link(SimLink):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            number = next(made)
            self.tag = f"L{number}"
            self.faults = dict(case.link_faults) if number == 0 else {}

        def play(self, steps: Sequence[Step]) -> Result[SendReport, InputError]:
            fault = self.faults.get(self.plays + 1)
            if fault is not None:
                self.fail_next.append(fault)
            got = super().play(steps)
            trace.add(self.tag, "play", tuple((s.delta_100us, int(s.mask)) for s in steps), got)
            return got

        def release_all(self) -> Result[SendReport, InputError]:
            got = super().release_all()
            if threading.current_thread() is threading.main_thread():
                trace.add(self.tag, "release_all", "ok" if is_successful(got) else got.failure())
            return got

    return _Link


def _walker_factory(trace: _Trace, case: _Case, walkers: list[RouteWalker]) -> Callable[..., RouteWalker]:
    """Builds each walker as `run_sim` and the recovery replays ask, with every input wrapped to record its calls."""

    class _Walker(RouteWalker):
        t_tag = ""
        t_rng = None

        def walk_leg(self, lap, leg, src, dst) -> LegRecord:
            record = super().walk_leg(lap, leg, src, dst)
            trace.add(self.t_tag, "walk_leg", lap, leg, record)
            return record

        def run(self) -> Result[RunSummary, str]:
            got = super().run()
            trace.add(self.t_tag, "run", got, self.t_rng.bit_generator.state)
            return got

    def make(rg, rotation, layer, positions, clock, sink, rng, config, *, observer=None,
             ropes=RopeZones.EMPTY) -> RouteWalker:
        tag = f"W{len(walkers)}"
        trace.add(tag, "built", len(rotation), config)

        def wait(timeout_s: float) -> None:
            trace.add(tag, "wait_frame", timeout_s)
            clock.sleep(timeout_s)

        walker = _Walker(rg, rotation, layer, _Positions(positions, trace, tag, case if not walkers else None),
                         _Clock(clock, trace, tag), _Sink(sink, trace, tag), rng, config, observer=observer,
                         log=lambda text: trace.add(tag, "log", text), ropes=_Ropes(ropes, trace, tag),
                         wait_frame=wait if case.waiter else None)
        walker.t_tag, walker.t_rng = tag, rng
        walkers.append(walker)
        return walker

    return make


def _patch_walker_calls(monkeypatch: pytest.MonkeyPatch, trace: _Trace, case: _Case,
                        walkers: list[RouteWalker]) -> None:
    """Record the walker module's calls of the planner, `locate` and `moves`, where walker.py looks them up."""
    real_next, real_attach = walker_module.next_move, walker_module.attach
    real_standing, real_rope, real_execute = walker_module.attach_standing, walker_module.rope_move, walker_module.execute
    calls = {"next_move": 0, "moves": 0}

    def next_move(rg, pos, target, rng):
        calls["next_move"] += 1
        got = real_next(rg, pos, target, rng)
        if calls["next_move"] in case.no_path_at:
            got = Failure("no path (test)")
        if is_successful(got):
            d = got.unwrap()
            shown = (d.move, d.cost_to_go_s, None if d.toward is None else d.toward.minimap_position)
            trace.add("planner", "next_move", pos, target.minimap_position, "Success", shown)
            if d.move is not None:
                calls["moves"] += 1
                if case.stop_on == ("next_move", calls["moves"]):
                    walkers[0].stop_now()
        else:
            trace.add("planner", "next_move", pos, target.minimap_position, got)
        return got

    def attach(rg, pos):
        got = real_attach(rg, pos)
        trace.add("locate", "attach", pos, is_successful(got), None if is_successful(got) else got.failure())
        return got

    def attach_standing(rg, pos):
        got = real_standing(rg, pos)
        trace.add("locate", "attach_standing", pos, got)
        return got

    def rope_move(mark, pos, target, tries, reach_v, blink_tries):
        got = real_rope(mark, pos, target, tries, reach_v, blink_tries)
        trace.add("planner", "rope_move", mark, pos, target.minimap_position, tries, reach_v, blink_tries, got)
        return got

    def execute(layer, move, stimulus_ns):
        got = real_execute(layer, move, stimulus_ns)
        trace.add("moves", "execute", move, stimulus_ns, "ok" if is_successful(got) else got.failure())
        return got

    for name, fake in (("next_move", next_move), ("attach", attach), ("attach_standing", attach_standing),
                       ("rope_move", rope_move), ("execute", execute)):
        monkeypatch.setattr(walker_module, name, fake)


def _trace_of(name: str, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    case = _SCENARIOS[name]
    trace = _Trace()
    walkers: list[RouteWalker] = []
    factory = _walker_factory(trace, case, walkers)
    monkeypatch.setattr(sim_run, "RouteWalker", factory)        # the run's walker
    monkeypatch.setattr(sim_recovery, "RouteWalker", factory)   # the replays' walkers (`_replay_moves`)
    link = _link_class(trace, case)
    monkeypatch.setattr(sim_run, "SimLink", link)
    monkeypatch.setattr(sim_recovery, "SimLink", link)
    _patch_walker_calls(monkeypatch, trace, case, walkers)
    observer = _Observer(trace, case, walkers) if case.observer else None
    report = run_sim(CSV, case.terrain, case.laps, case.seed, noise=case.noise, sim_params=case.sim_params,
                     observer=observer)
    assert is_successful(report), report
    trace.add("sim", "report", report.unwrap())
    return trace.lines


@pytest.mark.parametrize("name", sorted(_SCENARIOS))
def test_the_walker_makes_the_same_calls_in_the_same_order(name, monkeypatch, tmp_path):
    lines = _trace_of(name, monkeypatch)
    text = "\n".join(lines)
    out = os.environ.get("HELIX_TRACE_DIR")
    folder = Path(out) if out else tmp_path
    got = (len(lines), hashlib.sha256(text.encode()).hexdigest())
    if out or got != _PINS.get(name):
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{name}.trace.txt").write_text(text + "\n", encoding="utf-8")
    assert got == _PINS.get(name), f"the walker's calls changed: trace in {folder / f'{name}.trace.txt'}"
