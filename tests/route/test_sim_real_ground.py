"""Tests for the real-ground simulator run (off-graph fix plan, Tasks 3-4, OFFP141): the preset `--real-ground` stands for
(Fossil Foundry 2's real ground, the dot 3 px above the foot, the live teleport length, mob knockback), and the run the
walker passes under it. Task 3 built the run and proved, with a strict xfail, that the walker froze on the top slope
(at x 68, leg (49, 28) -> (101, 24)); Task 4's 3 px shift lifted the freeze, so the run is a plain test now.
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


# --- 1. the preset -------------------------------------------------------------------------------------------


def test_preset_teleport_matches_the_recordings():
    pairs = json.loads(LIVE_READS.read_text(encoding="utf-8"))["side_teleports"]
    median = statistics.median(abs(x_to - x_from) for x_from, x_to in pairs)
    expected = median - Kanna().max_horizontal_traversal_dist
    assert REAL_GROUND_SIM.side_teleport_extra_px == expected
    assert REAL_GROUND_SIM.side_teleport_extra_px >= 1, (
        "with an extra of 0 the sim lands at x 68 from 49, which attached on the unshifted code, so the run would "
        "never have shown the freeze on the slope (Task 3); OFFD140 saw exactly this")
    assert REAL_GROUND_SIM.dot_above_foot_px == 3
    assert REAL_GROUND_NOISE.knockback_p == 0.02
    assert REAL_GROUND_TERRAIN == FIXTURES / "ff2_real_ground.json"
    assert REAL_GROUND_TERRAIN.is_file()


# --- 2. the runs ---------------------------------------------------------------------------------------------


def test_real_ground_run_passes():
    result = run_sim(CSV, REAL_GROUND_TERRAIN, 20, 2, noise=REAL_GROUND_NOISE, sim_params=REAL_GROUND_SIM)
    assert is_successful(result), result
    report = result.unwrap()
    checks = judge(report, 0)
    assert all(check.ok for check in checks), [check for check in checks if not check.ok]
    assert report.knockbacks > 0, "the preset's noise (knockback_p) must reach the report; 8 on seed 2"


def test_report_counts_the_knockbacks():
    old_terrain = FIXTURES / "ff2_terrain.json"
    quiet = run_sim(CSV, old_terrain, 1, 2, noise=NoiseConfig(knockback_p=0.0)).unwrap()
    shoved = run_sim(CSV, old_terrain, 1, 2, noise=NoiseConfig(knockback_p=0.3)).unwrap()
    assert quiet.knockbacks == 0
    assert shoved.knockbacks > 0
    assert (shoved.knockbacks, shoved.moves) == (5, 20)  # seeded: five shoves in a lap of 20 moves


def test_off_map_is_counted(tmp_path):
    """Off-graph fix Task 7: a run whose character stands 10 px under every line of the map (the world's floor is
    lower than the map says) ends its first leg `off_map` (QB213: more than 6 px from every platform). The report
    counts that leg, and `judge` fails `laps` and says so (QB215)."""
    nodes = ["# minimap_size=162,82",
             "nodetype,pos_in_minimap,can_fall,fall_dist_exceeds_vertical_tp,is_left_platform_edge,is_right_platform_edge"]
    nodes += [f'blue_node,"({x}, 50)",FALSE,FALSE,FALSE,FALSE' for x in (40, 60, 80)]
    csv = tmp_path / "map.csv"
    csv.write_text("\n".join(nodes) + "\n", encoding="utf-8")
    terrain = tmp_path / "terrain.json"
    terrain.write_text(json.dumps({"minimap_size": [162, 82], "platforms": [
        {"name": "floor", "droppable": False, "points": [[30, 60], [100, 60]]}]}), encoding="utf-8")

    result = run_sim(csv, terrain, 1, 1)

    assert is_successful(result), result
    report = result.unwrap()
    assert report.off_map == 1
    assert (report.laps_completed, report.legs_reached, report.timeouts, report.link_down) == (0, 0, 0, 0)
    laps = judge(report, 0)[0]
    assert laps.name == "laps" and not laps.ok
    assert laps.detail.endswith("0 timed out, 0 link down, 1 off map")


def test_runs_without_an_off_map_leg_report_none():
    result = run_sim(CSV, REAL_GROUND_TERRAIN, 1, 2, noise=REAL_GROUND_NOISE, sim_params=REAL_GROUND_SIM)
    report = result.unwrap()
    assert report.off_map == 0
    assert judge(report, 0)[0].detail.endswith("0 link down, 0 off map")


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
