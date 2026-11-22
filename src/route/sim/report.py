"""The simulator's report and its judgement (split out of `sim/run.py`, CLEAN222-W1-2): `SimReport`, what `run_sim`
returns, and `judge`, which turns it into six pass/fail checks.

Where the plan is silent:
- `moves`, `drops`, `jump_offs` and `teleport_downs` count the walker's moves (its `MoveEntry`s, one world effect
  each); `no_ops` counts the world effects with a NO_OP. `sim_seconds` is the virtual time from the run's start to
  its end and `mean_lap_s` the mean lap duration (0.0 with no lap).
- `noise` fails when no move was made (0/0).
"""

from collections.abc import Sequence
from dataclasses import dataclass

from returns.pipeline import is_successful

from src.controller.node import Node
from src.route.leg_log import LegOutcome, LegRecord
from src.route.planner import _offered
from src.route.route_graph import EdgeKind, RouteGraph
from src.route.sim.recovery import _EXTRA_PER_ERROR

_MIN_RECOVERED_SHARE = 0.99
_MIN_NO_OP_SHARE = 0.05


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
    single_error_within_bound: int           # extra <= `_extra_allowed`, as for the multi-error legs below
    multi_error_legs: int
    multi_error_within_bound: int            # extra <= `_extra_allowed` (3 x the errors, a no-op that starts the rope order adds its moves)
    moves: int
    no_ops: int
    drops: int
    teleport_downs: int
    sim_seconds: float
    mean_lap_s: float
    knockbacks: int = 0                      # world effects with a mob knockback (`--real-ground` turns them on)
    off_map: int = 0                         # legs that ended `off_map`: stood 0.5 s more than 6 px from every platform
    drop_only_edges: int = 0                 # drop-only DOWN edges (dy > reach_v) on a rotation leg's cheapest path (H87)
    rope_grabs: int = 0                      # times the character grabbed a rope (the map's rope file; H88)
    jump_offs: int = 0                       # the walker's moves of kind `jump_off` (Alt+Left/Right off a rope; H88)


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str


def _drop_only_edges_used(rg: RouteGraph, rotation: Sequence[Node]) -> int:
    """How many distinct DOWN edges with dy > `rg.reach_v` (only a drop makes them, H87) lie on the cheapest path, in the
    graph the planner uses (`_offered`), of a rotation leg: the drops this run's route needs. Fossil Foundry 2's map has
    one such edge that no rotation leg uses, so it is not counted (ROPE151-T1-D1, provisional)."""
    offered = _offered(rg)
    used: set[tuple[Node, Node]] = set()
    for src, dst in zip(rotation, (*rotation[1:], rotation[0])):
        path = offered.path(src, dst)
        if not is_successful(path):
            continue
        hops = path.unwrap()
        used.update(
            (u, v) for u, v in zip(hops, hops[1:])
            if offered.graph[u][v]["kind"] == EdgeKind.DOWN
            and abs(v.minimap_position[1] - u.minimap_position[1]) > rg.reach_v)
    return len(used)


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

def judge(report: SimReport, min_single_error_legs: int = 100) -> list[Check]:
    """The six checks, in order: laps, stalls, known_cases, recovery, noise, abilities."""
    r = report
    checks = [
        Check("laps",
              r.laps_completed == r.laps_requested and r.legs_reached == r.legs and r.timeouts == 0
              and r.link_down == 0 and r.off_map == 0,
              f"{r.laps_completed}/{r.laps_requested} laps, {r.legs_reached}/{r.legs} legs reached, "
              f"{r.timeouts} timed out, {r.link_down} link down, {r.off_map} off map"),
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
                     and r.single_error_within_bound / r.single_error_legs >= _MIN_RECOVERED_SHARE)
    multi_ok = r.multi_error_legs == 0 or r.multi_error_within_bound / r.multi_error_legs >= _MIN_RECOVERED_SHARE
    checks.append(Check(
        "recovery", single_ok and multi_ok,
        f"{r.single_error_within_bound}/{r.single_error_legs} single-error legs within {_EXTRA_PER_ERROR} extra moves "
        f"(need >= {min_single_error_legs} legs and {_MIN_RECOVERED_SHARE:.0%}); {r.multi_error_within_bound}/"
        f"{r.multi_error_legs} multi-error legs within {_EXTRA_PER_ERROR} extra moves per error; a no-op that starts the "
        "rope order may also cost the order's moves"))

    no_op_share = r.no_ops / r.moves if r.moves else 0.0
    checks.append(Check("noise", r.moves > 0 and no_op_share >= _MIN_NO_OP_SHARE,
                        f"{r.no_ops}/{r.moves} moves were no-ops ({no_op_share:.2%}, need >= {_MIN_NO_OP_SHARE:.0%})"))
    checks.append(Check("abilities", r.teleport_downs > 0 and (r.drops > 0 or r.drop_only_edges == 0),
                        f"{r.drops} drops, {r.teleport_downs} teleports down (need teleports > 0, and drops > 0 when "
                        f"{r.drop_only_edges} drop-only edges > 0)"))
    return checks
