"""Tests for src.route.sim.run (plan task B14): the simulator evaluation against spec B's targets.

Tests (1)-(6) are the plan's, numbered as in the plan; every run passes the plan's two known cases. The rest pin what
the plan leaves to the builder: `judge`'s 0/0 recovery rule and check order on hand-built reports, the JSONL out-dir,
and hard rules 4 and 5 on the module's source.
"""

import hashlib
import json
import re
import time
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from returns.pipeline import is_successful

import src.route.sim.recovery as recovery_module
import src.route.sim.report as report_module
import src.route.sim.run as run_module
from src.classes.classes import Kanna
from src.controller.controller import MapleAgent
from src.controller.node import Node
from src.route.params import MovementParams
from src.route.route_graph import EdgeKind, RouteGraph
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
        known_cases={}, single_error_legs=100, single_error_within_bound=99, multi_error_legs=0,
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
    (dict(single_error_legs=0, single_error_within_bound=0), 0, True),     # 0/0 passes only with a minimum of 0
    (dict(single_error_legs=0, single_error_within_bound=0), 1, False),
    (dict(single_error_legs=100, single_error_within_bound=98), 100, False),
    (dict(single_error_legs=99, single_error_within_bound=99), 100, False),  # too few single-error legs
    (dict(multi_error_legs=100, multi_error_within_bound=99), 100, True),
    (dict(multi_error_legs=100, multi_error_within_bound=98), 100, False),
])
def test_judge_recovery_shares_and_minimum(changes, minimum, ok):
    checks = _by_name(judge(_good_report(**changes), min_single_error_legs=minimum))
    assert checks["recovery"].ok is ok


@pytest.mark.parametrize("changes, name", [
    (dict(laps_completed=9), "laps"), (dict(legs_reached=70), "laps"), (dict(timeouts=1), "laps"),
    (dict(link_down=1), "laps"), (dict(off_map=1), "laps"), (dict(stalls=1), "stalls"),
    (dict(known_cases={"a->b": (10, 10), "c->d": (9, 10)}), "known_cases"),
    (dict(no_ops=49), "noise"), (dict(moves=0, no_ops=0), "noise"),
    (dict(drops=0, drop_only_edges=2), "abilities"), (dict(teleport_downs=0), "abilities"),
])
def test_judge_fails_only_the_check_a_bad_field_belongs_to(changes, name):
    checks = judge(_good_report(**changes))
    assert [c.name for c in checks if not c.ok] == [name]


# The `abilities` check after H87 (C4): teleports down > 0, and drops > 0 only when a rotation leg's cheapest path uses a
# drop-only DOWN edge (ROPE151-T1-D1: the plan counted every such edge, and Fossil Foundry 2 has one no leg uses).

def test_abilities_check_without_drop_only_edges():
    check = _by_name(judge(_good_report(drops=0, teleport_downs=5, drop_only_edges=0)))["abilities"]
    assert check.ok
    assert check.detail == ("0 drops, 5 teleports down "
                            "(need teleports > 0, and drops > 0 when 0 drop-only edges > 0)")


def test_abilities_check_with_drop_only_edges():
    check = _by_name(judge(_good_report(drops=0, teleport_downs=5, drop_only_edges=2)))["abilities"]
    assert not check.ok
    assert _by_name(judge(_good_report(drops=1, teleport_downs=5, drop_only_edges=2)))["abilities"].ok


def test_abilities_check_needs_teleports_down():
    for drop_only_edges in (0, 2):
        check = _by_name(judge(_good_report(drops=3, teleport_downs=0, drop_only_edges=drop_only_edges)))["abilities"]
        assert not check.ok


def _route_graph(nodes):
    """A RouteGraph over `nodes` (index -> Node), built the way the runtime builds it."""
    path_graph = MapleAgent(characterClass=Kanna()).buildPathGraphFor(nodes).unwrap()
    assert is_successful(path_graph.buildGraph())
    return RouteGraph.build(path_graph, MovementParams()).unwrap()


def test_drop_only_edges_counts_the_ones_a_rotation_leg_uses():
    nodes = {0: Node("blue_node", (10, 10), True, False, False, False),
             1: Node("blue_node", (10, 40), False, False, False, False)}  # 30 px down: beyond a teleport, a drop reaches
    rg = _route_graph(nodes)
    assert rg.graph.edges[nodes[0], nodes[1]]["kind"] == EdgeKind.DOWN and 30 > rg.reach_v
    assert report_module._drop_only_edges_used(rg, (nodes[0], nodes[1])) == 1  # leg 0 -> 1 drops; leg 1 -> 0 has no path
    assert report_module._drop_only_edges_used(rg, (nodes[1],)) == 0           # a rotation that never leaves the floor


def test_drop_only_edges_ignores_an_edge_no_rotation_leg_uses():
    """Fossil Foundry 2's map has a DOWN edge of 32 px that its rotation never takes: the check must not ask for a drop."""
    agent = MapleAgent(characterClass=Kanna())
    assert is_successful(agent.loadMap(CSV))
    rg = RouteGraph.build(agent.pathGraph, MovementParams()).unwrap()
    assert any(kind == EdgeKind.DOWN and abs(v.minimap_position[1] - u.minimap_position[1]) > rg.reach_v
               for u, v, kind in rg.graph.edges(data="kind"))
    assert report_module._drop_only_edges_used(rg, tuple(agent.map_rotation_order)) == 0


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
    assert report.single_error_within_bound <= report.single_error_legs
    assert report.multi_error_within_bound <= report.multi_error_legs


@pytest.mark.parametrize("module", [run_module, recovery_module, report_module], ids=lambda m: m.__name__)
def test_run_module_writes_no_teleport_reach_and_no_map_position(module):
    """The sim/run modules (run, and since the split recovery and report) write no teleport reach and no map position."""
    source = Path(module.__file__).read_text(encoding="utf-8")
    assert re.search(r"(?<![\w.])(19|23)(?![\w.])", source) is None
    assert re.search(r"\(\s*\d+\s*,\s*\d+\s*\)", source) is None


def test_offset_copy_of_the_fixture_is_byte_identical_and_draws_once_per_data_row(tmp_path):
    """The node-offset copy (`_write_offset_csv`) of Fossil Foundry 2 with a fixed generator: the bytes are pinned (taken
    from the code before the sim/run split, CLEAN222-W1-2), and it draws exactly one `integers(-k, k + 1)` per data
    row, in file order, so the world's generator is left where it was before the split."""
    rng = np.random.default_rng(5)
    run_module._write_offset_csv(CSV, tmp_path / "ff2.csv", 2, rng)
    digest = hashlib.sha256((tmp_path / "ff2.csv").read_bytes()).hexdigest()
    assert digest == "c11b6b036c2a365f2060754a9126cb3924e42ad5ef23b5e03d92c43c093946a6"
    data_rows = sum(1 for line in CSV.read_text(encoding="utf-8").splitlines() if not line.startswith("#")) - 1
    twin = np.random.default_rng(5)
    for _ in range(data_rows):
        twin.integers(-2, 3)
    assert rng.integers(0, 1 << 30) == twin.integers(0, 1 << 30)


def test_offset_copy_moves_comments_ahead_of_the_header_and_writes_lf(tmp_path):
    """Every `#` line (an indented one and one after the rows too) goes ahead of the header, unchanged; CRLF becomes LF;
    each position is rewritten as `(x, y + draw)` with the draws in file order."""
    src = tmp_path / "small.csv"
    src.write_bytes(
        b"# minimap_size=10,20\r\n  # indented note\r\n"
        b"nodetype,pos_in_minimap,can_fall,fall_dist_exceeds_vertical_tp,is_left_platform_edge,is_right_platform_edge\r\n"
        b'blue_node,"(1, 2)",FALSE,FALSE,TRUE,FALSE\r\norange_node,"(30, 40)",TRUE,FALSE,FALSE,TRUE\r\n'
        b'# tail comment\r\nblue_node,"(5,6)",FALSE,TRUE,FALSE,FALSE\r\n')
    rng = np.random.default_rng(11)   # its first three integers(-3, 4) draws are -3, -3, 2
    run_module._write_offset_csv(src, tmp_path / "out.csv", 3, rng)
    assert (tmp_path / "out.csv").read_bytes() == (
        b"# minimap_size=10,20\n  # indented note\n# tail comment\n"
        b"nodetype,pos_in_minimap,can_fall,fall_dist_exceeds_vertical_tp,is_left_platform_edge,is_right_platform_edge\n"
        b'blue_node,"(1, -1)",FALSE,FALSE,TRUE,FALSE\norange_node,"(30, 37)",TRUE,FALSE,FALSE,TRUE\n'
        b'blue_node,"(5, 8)",FALSE,TRUE,FALSE,FALSE\n')
    assert rng.integers(0, 1 << 30) == 536095522
