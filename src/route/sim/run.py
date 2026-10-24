"""Simulator evaluation (plan task B14, contract C11): walk the route in the simulated world and judge the run against
spec B's targets.

`run_sim` loads a map, builds the route graph and walks `laps` laps of the rotation with a real `RouteWalker` and a
real `InputLayer` on a `SimLink` into a `SimWorld`, all under one virtual `SimClock`. Its `SimReport` counts the leg
outcomes, the stalls, the named known-case legs, the recovery from injected errors and the moves. `judge` turns a
report into six pass/fail checks, and `main` is the CLI:

    python -m src.route.sim.run --laps N --seed S [--map CSV] [--terrain JSON] [--out DIR]
        [--known-case X1,Y1:X2,Y2]... [--min-single-error-legs K] [--class kanna]

Recovery metric: every leg whose world effects carry an injected error is replayed in a fresh noise-off world from
the position the leg began at (same terrain, same route graph, offsets included), and `extra = moves - nominal`,
where nominal is the replay's move count.

Where the plan is silent:
- A leg's injected errors are counted one per error, not one per move: a move with both an overshoot and a landing
  offset carries two.
- "Seeded from the leg's number": a replay's rngs are `SeedSequence(i).spawn(4)` (timing, planner, world, positions,
  as the run's), where i is the leg's index in the run's leg log (0 is the approach leg). Every leg is replayed
  whatever its outcome, the approach leg included.
- `moves`, `drops` and `teleport_downs` count the walker's moves (its `MoveEntry`s, one world effect each); `no_ops`
  counts the world effects with a NO_OP. `sim_seconds` is the virtual time from the run's start to its end and
  `mean_lap_s` the mean lap duration (0.0 with no lap).
- `noise` fails when no move was made (0/0).
- With node offsets the original CSV is loaded first (it gives the unshifted rotation positions), then one offset is
  drawn per CSV data row, in file order, before the world's first draw.
- The CSV parser's progress prints during `loadMap` are swallowed, so the CLI prints only the report and the checks.
- The CLI's `--map` and `--terrain` defaults are anchored at the repo root, so it runs from any directory; a
  `run_sim` Failure is printed to stderr and exits 2.
"""

import argparse
import ast
import contextlib
import csv
import io
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass, fields
from pathlib import Path

import numpy as np
from returns.pipeline import is_successful
from returns.result import Failure, Result, Success

from src.classes.classes import CharacterClass, Kanna
from src.controller.controller import BuildOutcome, MapleAgent
from src.controller.csv_parser import CSVParser
from src.controller.node import Node
from src.input.input_layer import InputLayer
from src.input.protocol import Key
from src.input.timing import TimingModel
from src.route.leg_log import JsonlLegLog, LapRecord, LegOutcome, LegRecord, LegSink, MemorySink
from src.route.params import MovementParams, validate
from src.route.planner import MoveKind
from src.route.route_graph import RouteGraph
from src.route.sim.link import SimClock, SimLink, SimPositionSource
from src.route.sim.world import InjectedError, MoveEffect, NoiseConfig, SimParams, SimTerrain, SimWorld, load_terrain
from src.route.walker import RouteWalker, WalkerConfig

_LEG_TIMEOUT_S = 60.0
_START_RISE_PX = 3            # the world starts on the surface at or below the first node's y minus this
_EXTRA_PER_ERROR = 3          # each injected error is allowed this many extra moves
_MIN_RECOVERED_SHARE = 0.99
_MIN_NO_OP_SHARE = 0.05
_REPO = Path(__file__).resolve().parents[3]
_CLASSES: dict[str, type[CharacterClass]] = {"kanna": Kanna}


@dataclass(frozen=True)
class SimReport:
    laps_requested: int
    laps_completed: int
    legs: int
    legs_reached: int
    stalls: int
    timeouts: int
    link_down: int
    known_cases: dict[str, tuple[int, int]]  # "(x1, y1)->(x2, y2)" -> (reached, attempted), laps >= 1 only
    single_error_legs: int
    single_error_within_3: int
    multi_error_legs: int
    multi_error_within_bound: int            # extra <= 3 x (number of errors)
    moves: int
    no_ops: int
    drops: int
    teleport_downs: int
    sim_seconds: float
    mean_lap_s: float


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str


class _MarkingSink:
    """Passes every record on to `inner`. At each `write_leg` it also keeps the record, `len(world.effects)` and
    `world.pos`: the effects between two consecutive leg writes belong to that leg, and the walker starts the next
    leg right after the write, so the position kept is where the next leg begins."""

    def __init__(self, inner: LegSink, world: SimWorld) -> None:
        self._inner = inner
        self._world = world
        self.legs: list[LegRecord] = []
        self.laps: list[LapRecord] = []
        self.marks: list[int] = []
        self.ends: list[tuple[float, float]] = []

    def write_leg(self, rec: LegRecord) -> Result[None, str]:
        self.legs.append(rec)
        self.marks.append(len(self._world.effects))
        self.ends.append(self._world.pos)
        return self._inner.write_leg(rec)

    def write_lap(self, rec: LapRecord) -> Result[None, str]:
        self.laps.append(rec)
        return self._inner.write_lap(rec)


def _rngs(seed: int) -> tuple[np.random.Generator, ...]:
    """Four independent generators: timing, planner, world, positions."""
    return tuple(np.random.default_rng(s) for s in np.random.SeedSequence(seed).spawn(4))


def _load_agent(csv_path: Path, character: CharacterClass) -> Result[MapleAgent, str]:
    agent = MapleAgent(characterClass=character)
    with contextlib.redirect_stdout(io.StringIO()):
        loaded = agent.loadMap(csv_path)
    if not is_successful(loaded):
        return Failure(f"cannot load map {csv_path}: {loaded.failure()}")
    if loaded.unwrap() is not BuildOutcome.BUILT:
        return Failure(f"map {csv_path} loaded but no graph was built: {loaded.unwrap().name}")
    return Success(agent)


def _write_offset_csv(src: Path, dst: Path, k: int, rng: np.random.Generator) -> None:
    """Copy the CSV's data rows to `dst` with each node's y shifted by `rng.integers(-k, k + 1)`, in file order.
    `src` has already passed `loadMap`, so every position parses."""
    pos_field = CSVParser().CSV_fields["pos_in_minimap"]
    with open(src, newline="") as f:
        reader = csv.DictReader(line for line in f if not line.lstrip().startswith("#"))
        fieldnames = reader.fieldnames
        rows = list(reader)
    with open(dst, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            x, y = ast.literal_eval(row[pos_field])
            row[pos_field] = str((x, y + int(rng.integers(-k, k + 1))))
            writer.writerow(row)


def _leg_nodes(rotation: Sequence[Node], rec: LegRecord) -> tuple[Node | None, Node]:
    """The (src, dst) nodes of a leg record: lap 0 is the approach to rotation[0], leg i of a lap is rotation[i] ->
    rotation[(i + 1) mod n]."""
    if rec.lap == 0:
        return None, rotation[0]
    n = len(rotation)
    return rotation[rec.leg], rotation[(rec.leg + 1) % n]


def _replay_moves(rg: RouteGraph, rotation: Sequence[Node], terrain: SimTerrain, sim_params: SimParams,
                  rec: LegRecord, number: int, start: tuple[float, float]) -> int:
    """The moves the leg takes in a fresh noise-off world from `start` (the nominal count)."""
    rng_timing, rng_planner, rng_world, rng_positions = _rngs(number)
    off = NoiseConfig.off()
    world = SimWorld(terrain, rg.reach_h, rg.reach_v, sim_params, off, rng_world, start)
    clock = SimClock()
    layer = InputLayer(SimLink(world, clock, sim_params), TimingModel(rng_timing))
    positions = SimPositionSource(world, clock, off.miss_p, rng_positions)
    config = WalkerConfig(use_stimulus=False, leg_timeout_s=_LEG_TIMEOUT_S)
    walker = RouteWalker(rg, rotation, layer, positions, clock, MemorySink(), rng_planner, config)
    src, dst = _leg_nodes(rotation, rec)
    return len(walker.walk_leg(rec.lap, rec.leg, src, dst).moves)


@dataclass(frozen=True)
class _Recovery:
    single: int = 0
    single_within: int = 0
    multi: int = 0
    multi_within: int = 0


def _recovery(rg: RouteGraph, rotation: Sequence[Node], terrain: SimTerrain, sim_params: SimParams,
              sink: _MarkingSink, effects: Sequence[MoveEffect], start: tuple[float, float]) -> _Recovery:
    single = single_within = multi = multi_within = 0
    for i, rec in enumerate(sink.legs):
        first = sink.marks[i - 1] if i else 0
        errors = sum(len(e.injected) for e in effects[first:sink.marks[i]])
        if errors == 0:
            continue
        began = sink.ends[i - 1] if i else start
        extra = len(rec.moves) - _replay_moves(rg, rotation, terrain, sim_params, rec, i, began)
        if errors == 1:
            single += 1
            single_within += extra <= _EXTRA_PER_ERROR
        else:
            multi += 1
            multi_within += extra <= _EXTRA_PER_ERROR * errors
    return _Recovery(single, single_within, multi, multi_within)


def _known_cases(legs: Sequence[LegRecord], csv_rotation: Sequence[tuple[int, int]],
                 known_cases: Sequence[tuple[tuple[int, int], tuple[int, int]]]) -> dict[str, tuple[int, int]]:
    """(reached, attempted) per named leg, over laps >= 1, matched by rotation index on the unshifted positions."""
    n = len(csv_rotation)
    counts: dict[str, tuple[int, int]] = {}
    for src, dst in known_cases:
        src, dst = (int(src[0]), int(src[1])), (int(dst[0]), int(dst[1]))
        indices = {i for i in range(n) if csv_rotation[i] == src and csv_rotation[(i + 1) % n] == dst}
        matched = [rec for rec in legs if rec.lap >= 1 and rec.leg in indices]
        reached = sum(rec.outcome == LegOutcome.REACHED for rec in matched)
        counts[f"{src}->{dst}"] = (reached, len(matched))
    return counts


def run_sim(map_csv: Path, terrain: Path, laps: int, seed: int, noise: NoiseConfig = NoiseConfig(),
            sim_params: SimParams = SimParams(), params: MovementParams = MovementParams(),
            out_dir: Path | None = None,
            known_cases: Sequence[tuple[tuple[int, int], tuple[int, int]]] = (),
            character: CharacterClass | None = None) -> Result[SimReport, str]:
    """Walk `laps` laps of the map's rotation in the simulated world and report on them (`character` None ->
    `Kanna()`). `Failure` when the params, terrain or map do not load, or the map has no rotation."""
    checked = validate(params)
    if not is_successful(checked):
        return Failure(f"bad movement params: {checked.failure()}")
    character = Kanna() if character is None else character
    rng_timing, rng_planner, rng_world, rng_positions = _rngs(seed)

    terrain_loaded = load_terrain(terrain)
    if not is_successful(terrain_loaded):
        return Failure(terrain_loaded.failure())
    sim_terrain = terrain_loaded.unwrap()

    loaded = _load_agent(Path(map_csv), character)
    if not is_successful(loaded):
        return Failure(loaded.failure())
    agent = loaded.unwrap()
    csv_rotation = tuple(node.minimap_position for node in agent.map_rotation_order)
    if not csv_rotation:
        return Failure(f"map {map_csv} has no blue node: the rotation is empty")
    if noise.node_offset_px > 0:
        with tempfile.TemporaryDirectory() as tmp:
            shifted = Path(tmp) / Path(map_csv).name
            _write_offset_csv(Path(map_csv), shifted, noise.node_offset_px, rng_world)
            loaded = _load_agent(shifted, character)
        if not is_successful(loaded):
            return Failure(f"with node offsets: {loaded.failure()}")
        agent = loaded.unwrap()

    built = RouteGraph.build(agent.pathGraph, params)
    if not is_successful(built):
        return Failure(built.failure())
    rg = built.unwrap()
    rotation = tuple(agent.map_rotation_order)

    x0, y0 = csv_rotation[0]
    surface = sim_terrain.surface_below(x0, y0 - _START_RISE_PX)
    if surface is None:
        return Failure(f"no terrain surface at or below the first rotation node {csv_rotation[0]}")
    start = (float(x0), surface[1])

    world = SimWorld(sim_terrain, rg.reach_h, rg.reach_v, sim_params, noise, rng_world, start)
    clock = SimClock()
    layer = InputLayer(SimLink(world, clock, sim_params), TimingModel(rng_timing))
    positions = SimPositionSource(world, clock, noise.miss_p, rng_positions)
    log: JsonlLegLog | None = None
    if out_dir is not None:
        opened = JsonlLegLog.open(Path(out_dir), fsync=False)
        if not is_successful(opened):
            return Failure(opened.failure())
        log = opened.unwrap()
    sink = _MarkingSink(MemorySink() if log is None else log, world)
    config = WalkerConfig(use_stimulus=False, leg_timeout_s=_LEG_TIMEOUT_S, max_laps=laps)
    walker = RouteWalker(rg, rotation, layer, positions, clock, sink, rng_planner, config)
    began_ns = clock.now_ns()
    try:
        ran = walker.run()
    finally:
        if log is not None:
            log.close()
    if not is_successful(ran):
        return Failure(ran.failure())
    summary = ran.unwrap()
    sim_seconds = (clock.now_ns() - began_ns) / 1e9

    recovery = _recovery(rg, rotation, sim_terrain, sim_params, sink, world.effects, start)
    legs = sink.legs
    entries = [m for rec in legs for m in rec.moves]
    lap_seconds = [(lap.end_ns - lap.start_ns) / 1e9 for lap in sink.laps]
    return Success(SimReport(
        laps_requested=laps,
        laps_completed=summary.laps_completed,
        legs=summary.legs,
        legs_reached=sum(rec.outcome == LegOutcome.REACHED for rec in legs),
        stalls=sum(rec.stalls for rec in legs),
        timeouts=sum(rec.outcome == LegOutcome.TIMED_OUT for rec in legs),
        link_down=sum(rec.outcome == LegOutcome.LINK_DOWN for rec in legs),
        known_cases=_known_cases(legs, csv_rotation, known_cases),
        single_error_legs=recovery.single,
        single_error_within_3=recovery.single_within,
        multi_error_legs=recovery.multi,
        multi_error_within_bound=recovery.multi_within,
        moves=len(entries),
        no_ops=sum(InjectedError.NO_OP in e.injected for e in world.effects),
        drops=sum(m.kind == MoveKind.DROP for m in entries),
        teleport_downs=sum(m.kind == MoveKind.TELEPORT and m.direction == Key.DOWN.name for m in entries),
        sim_seconds=sim_seconds,
        mean_lap_s=sum(lap_seconds) / len(lap_seconds) if lap_seconds else 0.0,
    ))


def judge(report: SimReport, min_single_error_legs: int = 100) -> list[Check]:
    """The six checks, in order: laps, stalls, known_cases, recovery, noise, abilities."""
    r = report
    checks = [
        Check("laps",
              r.laps_completed == r.laps_requested and r.legs_reached == r.legs and r.timeouts == 0
              and r.link_down == 0,
              f"{r.laps_completed}/{r.laps_requested} laps, {r.legs_reached}/{r.legs} legs reached, "
              f"{r.timeouts} timed out, {r.link_down} link down"),
        Check("stalls", r.stalls == 0, f"{r.stalls} stalls"),
    ]

    if r.known_cases:
        ok = all(reached == attempted == r.laps_completed for reached, attempted in r.known_cases.values())
        cases = ", ".join(f"{name} {reached}/{attempted}" for name, (reached, attempted) in r.known_cases.items())
        checks.append(Check("known_cases", ok, f"{cases} reached/attempted (need {r.laps_completed} each)"))
    else:
        checks.append(Check("known_cases", True, "none listed"))

    if r.single_error_legs == 0:
        single_ok = min_single_error_legs == 0
    else:
        single_ok = (r.single_error_legs >= min_single_error_legs
                     and r.single_error_within_3 / r.single_error_legs >= _MIN_RECOVERED_SHARE)
    multi_ok = r.multi_error_legs == 0 or r.multi_error_within_bound / r.multi_error_legs >= _MIN_RECOVERED_SHARE
    checks.append(Check(
        "recovery", single_ok and multi_ok,
        f"{r.single_error_within_3}/{r.single_error_legs} single-error legs within {_EXTRA_PER_ERROR} extra moves "
        f"(need >= {min_single_error_legs} legs and {_MIN_RECOVERED_SHARE:.0%}); {r.multi_error_within_bound}/"
        f"{r.multi_error_legs} multi-error legs within {_EXTRA_PER_ERROR} extra moves per error"))

    no_op_share = r.no_ops / r.moves if r.moves else 0.0
    checks.append(Check("noise", r.moves > 0 and no_op_share >= _MIN_NO_OP_SHARE,
                        f"{r.no_ops}/{r.moves} moves were no-ops ({no_op_share:.2%}, need >= {_MIN_NO_OP_SHARE:.0%})"))
    checks.append(Check("abilities", r.drops > 0 and r.teleport_downs > 0,
                        f"{r.drops} drops, {r.teleport_downs} teleports down (need both > 0)"))
    return checks


def _known_case(text: str) -> tuple[tuple[int, int], tuple[int, int]]:
    """argparse type for X1,Y1:X2,Y2."""
    try:
        a, b = text.split(":")
        (x1, y1), (x2, y2) = (tuple(int(v) for v in part.split(",")) for part in (a, b))
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected X1,Y1:X2,Y2, got {text!r}") from None
    return (x1, y1), (x2, y2)


def main(argv: list[str] | None = None) -> int:
    fixtures = _REPO / "tests" / "fixtures"
    parser = argparse.ArgumentParser(prog="python -m src.route.sim.run",
                                     description="Walk the route in the simulated world and judge the run.")
    parser.add_argument("--laps", type=int, required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--map", type=Path, default=fixtures / "fossil_foundry_2.csv")
    parser.add_argument("--terrain", type=Path, default=fixtures / "ff2_terrain.json")
    parser.add_argument("--out", type=Path, default=None, help="write legs.jsonl and laps.jsonl here")
    parser.add_argument("--known-case", type=_known_case, action="append", metavar="X1,Y1:X2,Y2",
                        help="a rotation leg that must be reached in every lap (repeatable)")
    parser.add_argument("--min-single-error-legs", type=int, default=100)
    parser.add_argument("--class", dest="character", choices=sorted(_CLASSES), default="kanna")
    args = parser.parse_args(argv)

    result = run_sim(args.map, args.terrain, args.laps, args.seed, out_dir=args.out,
                     known_cases=args.known_case or (), character=_CLASSES[args.character]())
    if not is_successful(result):
        print(f"error: {result.failure()}", file=sys.stderr)
        return 2
    report = result.unwrap()
    for f in fields(report):
        print(f"{f.name}: {getattr(report, f.name)}")
    checks = judge(report, args.min_single_error_legs)
    for check in checks:
        print(f"{'PASS' if check.ok else 'FAIL'} {check.name}: {check.detail}")
    return 0 if all(check.ok for check in checks) else 1


if __name__ == "__main__":
    sys.exit(main())
