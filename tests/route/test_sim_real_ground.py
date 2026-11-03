"""Tests for the real-ground simulator run (off-graph fix plan, Task 3, OFFP141): the preset `--real-ground` stands for
(Fossil Foundry 2's real ground, the dot 3 px above the foot, the live teleport length, mob knockback), and the proof
that today's walker freezes on the top slope under it.

The two runs below walk 20 laps and take seconds, so they are `slow` and share one run (`frozen_run`). Task 4 removes the
`xfail` mark of `test_real_ground_run_passes` and deletes `test_today_freezes_on_the_top_slope`.
"""

import json
import statistics
from pathlib import Path

import pytest
from returns.pipeline import is_successful
from returns.result import Failure

import src.route.sim.run as run_module
from src.classes.classes import Kanna
from src.route.sim.run import REAL_GROUND_NOISE, REAL_GROUND_SIM, REAL_GROUND_TERRAIN, judge, main, run_sim
from src.route.sim.world import NoiseConfig, SimParams

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
CSV = FIXTURES / "fossil_foundry_2.csv"
LIVE_READS = FIXTURES / "ff2_live_reads.json"


class _Recorder:
    """A `StepObserver` that keeps every call as (method name, arguments): all nine methods, spelled out."""

    def __init__(self):
        self.calls = []

    def leg(self, *args):
        self.calls.append(("leg", args))

    def read_failed(self, *args):
        self.calls.append(("read_failed", args))

    def no_plan(self, *args):
        self.calls.append(("no_plan", args))

    def reached(self, *args):
        self.calls.append(("reached", args))

    def stall(self, *args):
        self.calls.append(("stall", args))

    def planned(self, *args):
        self.calls.append(("planned", args))

    def sent(self, *args):
        self.calls.append(("sent", args))

    def settled(self, *args):
        self.calls.append(("settled", args))

    def leg_end(self, *args):
        self.calls.append(("leg_end", args))


@pytest.fixture(scope="module")
def frozen_run():
    """The real-ground run of today's walker, 20 laps, seed 2, with a recording observer: (report, calls)."""
    recorder = _Recorder()
    result = run_sim(CSV, REAL_GROUND_TERRAIN, 20, 2, noise=REAL_GROUND_NOISE, sim_params=REAL_GROUND_SIM,
                     observer=recorder)
    assert is_successful(result), result
    return result.unwrap(), recorder.calls


def _legs(calls):
    """The calls split into one list per leg: from its `leg` call to its `leg_end` call."""
    legs = []
    for name, args in calls:
        if name == "leg":
            legs.append([])
        legs[-1].append((name, args))
    return legs


# --- 1. the preset -------------------------------------------------------------------------------------------


def test_preset_teleport_matches_the_recordings():
    pairs = json.loads(LIVE_READS.read_text(encoding="utf-8"))["side_teleports"]
    median = statistics.median(abs(x_to - x_from) for x_from, x_to in pairs)
    expected = median - Kanna().max_horizontal_traversal_dist
    assert REAL_GROUND_SIM.side_teleport_extra_px == expected
    assert REAL_GROUND_SIM.side_teleport_extra_px >= 1, (
        "with an extra of 0 the sim lands at x 68 from 49, which attaches on today's code, so the before-proof "
        "(test_today_freezes_on_the_top_slope) cannot fail; OFFD140 saw exactly this")
    assert REAL_GROUND_SIM.dot_above_foot_px == 3
    assert REAL_GROUND_NOISE.knockback_p == 0.02
    assert REAL_GROUND_TERRAIN == FIXTURES / "ff2_real_ground.json"
    assert REAL_GROUND_TERRAIN.is_file()


# --- 2. the runs ---------------------------------------------------------------------------------------------


@pytest.mark.slow
@pytest.mark.xfail(strict=True, reason="today's walker freezes on the top slope; Task 4 removes this mark")
def test_real_ground_run_passes(frozen_run):
    report, _ = frozen_run
    checks = judge(report, 0)
    assert all(check.ok for check in checks), [check for check in checks if not check.ok]


@pytest.mark.slow
def test_today_freezes_on_the_top_slope(frozen_run):
    """The approach and lap 1's legs 0-3 are reached; then the teleport right from the top platform's left end puts the
    character on the ramp, the read there (dot pixels, 3 px above the node line) is on no segment, and the walker waits
    for the rest of the run: every later leg times out."""
    report, calls = frozen_run
    legs = _legs(calls)
    failed = [leg for leg in legs if leg[-1][1][1] != "reached"]
    assert legs.index(failed[0]) == 5 and len(legs) - len(failed) == 5
    assert (report.legs, report.legs_reached, report.timeouts, report.moves) == (141, 5, 136, 9)

    first = failed[0]
    (_, (_, lap, leg, src, dst)), (_, (_, outcome, misses, off_graph, stalls)) = first[0], first[-1]
    assert (lap, leg, src, dst) == (1, 4, (49, 28), (101, 24))
    assert (outcome, misses, off_graph, stalls) == ("timed_out", 9, 1184, 5)

    planned = [args for name, args in first if name == "planned"]
    assert [(a[1], a[2], a[3]) for a in planned] == [((48, 25), "teleport", "RIGHT")]  # the only move of the leg
    no_plans = [args for name, args in first if name == "no_plan"]
    assert len(no_plans) == 1184
    assert no_plans[0][1] == (68, 24)  # the node line is 27 at x 68; the read is the dot, 3 px above it
    assert {pos for _, pos, _ in no_plans} == {(68, 24)}  # it never moves again
    assert {why for _, _, why in no_plans} == {"off-graph: (68, 24)"}
    on_the_ramp = [pos for _, pos, _ in no_plans if 66 <= pos[0] <= 79 and pos[1] <= 25]
    assert len(on_the_ramp) > len(no_plans) / 2


def test_report_counts_the_knockbacks():
    old_terrain = FIXTURES / "ff2_terrain.json"
    quiet = run_sim(CSV, old_terrain, 1, 2, noise=NoiseConfig(knockback_p=0.0)).unwrap()
    shoved = run_sim(CSV, old_terrain, 1, 2, noise=NoiseConfig(knockback_p=0.3)).unwrap()
    assert quiet.knockbacks == 0
    assert shoved.knockbacks > 0
    assert (shoved.knockbacks, shoved.moves) == (5, 20)  # seeded: five shoves in a lap of 20 moves


# --- 3. the flag ---------------------------------------------------------------------------------------------


def test_real_ground_flag_conflicts_with_terrain():
    with pytest.raises(SystemExit) as raised:
        main(["--laps", "1", "--seed", "2", "--real-ground", "--terrain", "x.json"])
    assert raised.value.code == 2


def test_real_ground_flag_is_ff2_only():
    with pytest.raises(SystemExit) as raised:
        main(["--laps", "1", "--seed", "2", "--real-ground", "--map", "other.csv"])
    assert raised.value.code == 2


def test_real_ground_flag_with_the_ff2_map_passes_the_three_presets(monkeypatch):
    seen = {}

    def spy(map_csv, terrain, laps, seed, **kwargs):
        seen.update(map_csv=Path(map_csv), terrain=Path(terrain), laps=laps, seed=seed, **kwargs)
        return Failure("stub")

    monkeypatch.setattr(run_module, "run_sim", spy)
    for extra in ([], ["--map", str(CSV)]):  # the default map, then the FF2 fixture named outright
        seen.clear()
        assert main(["--laps", "1", "--seed", "2", "--real-ground", *extra]) == 2  # the stub's Failure, not a usage error
        assert seen["terrain"] == REAL_GROUND_TERRAIN
        assert seen["noise"] == REAL_GROUND_NOISE
        assert seen["sim_params"] == REAL_GROUND_SIM


def test_without_the_flag_the_cli_uses_the_old_terrain_and_the_defaults(monkeypatch):
    seen = {}

    def spy(map_csv, terrain, laps, seed, **kwargs):
        seen.update(terrain=Path(terrain), **kwargs)
        return Failure("stub")

    monkeypatch.setattr(run_module, "run_sim", spy)
    assert main(["--laps", "1", "--seed", "2"]) == 2
    assert seen["terrain"] == FIXTURES / "ff2_terrain.json"
    assert seen["noise"] == NoiseConfig()
    assert seen["sim_params"] == SimParams()
