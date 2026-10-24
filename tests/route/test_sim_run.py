"""Tests for src.route.sim.run (plan task B14): the simulator evaluation against spec B's targets.

Tests (1)-(6) are the plan's, numbered as in the plan; every run passes the plan's two known cases. The rest pin what
the plan leaves to the builder: `judge`'s 0/0 recovery rule and check order on hand-built reports, the JSONL out-dir,
and hard rules 4 and 5 on the module's source.
"""

import json
import re
import time
from dataclasses import replace
from pathlib import Path

import pytest
from returns.pipeline import is_successful

import src.route.sim.run as run_module
from src.route.sim.run import SimReport, judge, main, run_sim
from src.route.sim.world import NoiseConfig, SimParams

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
CSV = FIXTURES / "fossil_foundry_2.csv"
TERRAIN = FIXTURES / "ff2_terrain.json"

KNOWN = [((49, 28), (101, 24)), ((101, 24), (105, 42))]
KNOWN_KEYS = ["(49, 28)->(101, 24)", "(101, 24)->(105, 42)"]
CHECKS = ["laps", "stalls", "known_cases", "recovery", "noise", "abilities"]
COMPLETION = ["laps", "stalls", "known_cases"]


def _run(laps, seed=1, **kwargs):
    kwargs.setdefault("known_cases", KNOWN)
    result = run_sim(CSV, TERRAIN, laps, seed, **kwargs)
    assert is_successful(result), result
    return result.unwrap()


def _by_name(checks):
    return {c.name: c for c in checks}


def _assert_pass(report, names, **judge_kwargs):
    checks = _by_name(judge(report, **judge_kwargs))
    failed = [checks[n] for n in names if not checks[n].ok]
    assert not failed, (failed, report)


@pytest.fixture(scope="module")
def thirty_laps():
    began = time.perf_counter()
    report = _run(30, noise=replace(NoiseConfig(), no_op_p=0.08))
    return report, time.perf_counter() - began


# (1)
def test_1_thirty_laps_pass_every_check_in_under_a_minute(thirty_laps):
    report, seconds = thirty_laps
    checks = judge(report, min_single_error_legs=10)
    assert [c.name for c in checks] == CHECKS
    assert all(c.ok for c in checks), checks
    assert report.known_cases == {KNOWN_KEYS[0]: (30, 30), KNOWN_KEYS[1]: (30, 30)}
    assert report.laps_requested == report.laps_completed == 30
    assert seconds < 60


# (2)
def test_2_same_seed_twice_gives_equal_reports():
    assert _run(4, seed=7) == _run(4, seed=7)


# (3)
@pytest.mark.parametrize("walk_px_per_s", [6.5, 9.5])
def test_3_a_walk_speed_the_planner_does_not_assume_still_completes(walk_px_per_s):
    report = _run(20, sim_params=SimParams(walk_px_per_s=walk_px_per_s))
    _assert_pass(report, COMPLETION)
    assert report.known_cases == {KNOWN_KEYS[0]: (20, 20), KNOWN_KEYS[1]: (20, 20)}


# (3b)
def test_3b_node_offsets_still_complete():
    report = _run(20, noise=replace(NoiseConfig(), node_offset_px=2))
    _assert_pass(report, COMPLETION)
    assert report.known_cases == {KNOWN_KEYS[0]: (20, 20), KNOWN_KEYS[1]: (20, 20)}


# (4)
def test_4_too_few_no_ops_fail_the_noise_check():
    report = _run(5, noise=NoiseConfig(no_op_p=0.01))
    assert not _by_name(judge(report))["noise"].ok


# (5)
def test_5_cli_prints_one_line_per_check_and_exits_0_iff_all_pass(capsys):
    code = main(["--laps", "2", "--seed", "1", "--min-single-error-legs", "0", "--known-case", "49,28:101,24"])
    lines = capsys.readouterr().out.splitlines()
    verdicts = [line for line in lines if line.startswith(("PASS ", "FAIL "))]
    assert len(verdicts) == 6
    assert [v.split()[1].rstrip(":") for v in verdicts] == CHECKS
    assert (code == 0) == all(v.startswith("PASS ") for v in verdicts)


# (6)
def test_6_a_known_case_that_is_not_a_rotation_leg_is_never_attempted_and_fails():
    report = _run(2, known_cases=[((113, 60), (48, 61))])
    assert report.known_cases == {"(113, 60)->(48, 61)": (0, 0)}
    assert not _by_name(judge(report))["known_cases"].ok


# Choices the plan leaves open --------------------------------------------------------------------------------------

def _good_report(**changes):
    report = SimReport(
        laps_requested=10, laps_completed=10, legs=71, legs_reached=71, stalls=0, timeouts=0, link_down=0,
        known_cases={}, single_error_legs=100, single_error_within_3=99, multi_error_legs=0,
        multi_error_within_bound=0, moves=1000, no_ops=50, drops=1, teleport_downs=1, sim_seconds=1.0,
        mean_lap_s=0.1,
    )
    return replace(report, **changes)


def test_judge_passes_a_good_report_and_says_none_listed_without_known_cases():
    checks = judge(_good_report())
    assert [c.name for c in checks] == CHECKS
    assert all(c.ok for c in checks), checks
    assert _by_name(checks)["known_cases"].detail == "none listed"


@pytest.mark.parametrize("changes, minimum, ok", [
    (dict(single_error_legs=0, single_error_within_3=0), 0, True),     # 0/0 passes only with a minimum of 0
    (dict(single_error_legs=0, single_error_within_3=0), 1, False),
    (dict(single_error_legs=100, single_error_within_3=98), 100, False),
    (dict(single_error_legs=99, single_error_within_3=99), 100, False),  # too few single-error legs
    (dict(multi_error_legs=100, multi_error_within_bound=99), 100, True),
    (dict(multi_error_legs=100, multi_error_within_bound=98), 100, False),
])
def test_judge_recovery_shares_and_minimum(changes, minimum, ok):
    checks = _by_name(judge(_good_report(**changes), min_single_error_legs=minimum))
    assert checks["recovery"].ok is ok


@pytest.mark.parametrize("changes, name", [
    (dict(laps_completed=9), "laps"), (dict(legs_reached=70), "laps"), (dict(timeouts=1), "laps"),
    (dict(link_down=1), "laps"), (dict(stalls=1), "stalls"),
    (dict(known_cases={"a->b": (10, 10), "c->d": (9, 10)}), "known_cases"),
    (dict(no_ops=49), "noise"), (dict(moves=0, no_ops=0), "noise"),
    (dict(drops=0), "abilities"), (dict(teleport_downs=0), "abilities"),
])
def test_judge_fails_only_the_check_a_bad_field_belongs_to(changes, name):
    checks = judge(_good_report(**changes))
    assert [c.name for c in checks if not c.ok] == [name]


def test_out_dir_gets_one_jsonl_line_per_leg_and_lap(tmp_path):
    report = _run(1, out_dir=tmp_path / "run")
    legs = (tmp_path / "run" / "legs.jsonl").read_text(encoding="utf-8").splitlines()
    laps = (tmp_path / "run" / "laps.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(legs) == report.legs
    assert len(laps) == report.laps_completed == 1
    assert [json.loads(line)["type"] for line in legs] == ["leg"] * report.legs


def test_a_missing_map_or_terrain_is_a_failure_not_an_exception(tmp_path):
    missing_map = run_sim(tmp_path / "no_such_map.csv", TERRAIN, 1, 1)
    missing_terrain = run_sim(CSV, tmp_path / "no_such_terrain.json", 1, 1)
    assert not is_successful(missing_map) and missing_map.failure()
    assert not is_successful(missing_terrain) and missing_terrain.failure()


def test_run_counts_are_consistent_with_the_lap_structure():
    laps = 3
    rotation_nodes = sum(line.startswith("blue_node") for line in CSV.read_text(encoding="utf-8").splitlines())
    report = _run(laps)
    assert report.legs == 1 + laps * rotation_nodes
    assert report.legs_reached == report.legs
    assert report.no_ops < report.moves
    assert report.single_error_legs + report.multi_error_legs <= report.legs
    assert report.single_error_within_3 <= report.single_error_legs
    assert report.multi_error_within_bound <= report.multi_error_legs


def test_run_module_writes_no_teleport_reach_and_no_map_position():
    source = Path(run_module.__file__).read_text(encoding="utf-8")
    assert re.search(r"(?<![\w.])(19|23)(?![\w.])", source) is None
    assert re.search(r"\(\s*\d+\s*,\s*\d+\s*\)", source) is None
