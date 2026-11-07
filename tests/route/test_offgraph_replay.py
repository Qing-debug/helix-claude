"""The 3 px shift replayed on Hong's recorded Fossil Foundry 2 reads (off-graph fix plan, Task 4, OFFP141; the figures are
OFFD140's "shift 3" row).

`ff2_live_reads.json` holds the positions the planner logged in eight saved runs: the dot's centre, before any shift.
Replaying them through `locate.attach` on the fixture map's route graph shows, in both directions, what reading the
character 3 px lower (`foot_from_dot`) does:
- the 1229 polls the walker could not place (it waited on them for the rest of the run) all attach but 5, and those 5
  are the character in the air (no wait that matters);
- the reads the walker did place (`planned`, `reached`) still attach but 3, which are mid-fall reads, so the walker now
  waits for the landing instead of planning a drop from the air.
"""

import json
from collections import Counter
from pathlib import Path

import pytest
from returns.pipeline import is_successful

from src.classes.classes import Kanna
from src.controller.controller import MapleAgent
from src.route.locate import attach
from src.route.params import MovementParams
from src.route.position import foot_from_dot
from src.route.route_graph import RouteGraph
from src.route.sim.world import load_terrain

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
MAP = FIXTURES / "fossil_foundry_2.csv"
LIVE_READS = FIXTURES / "ff2_live_reads.json"
REAL_GROUND = FIXTURES / "ff2_real_ground.json"


@pytest.fixture(scope="module")
def rg() -> RouteGraph:
    agent = MapleAgent(characterClass=Kanna())
    assert is_successful(agent.loadMap(MAP))
    built = RouteGraph.build(agent.pathGraph, MovementParams())
    assert is_successful(built)
    return built.unwrap()


@pytest.fixture(scope="module")
def runs() -> dict[str, list[list]]:
    """run folder name -> its events [t_ms, kind, x, y] in order."""
    data = json.loads(LIVE_READS.read_text(encoding="utf-8"))
    return {run["run"]: run["events"] for run in data["runs"]}


def _reads(runs, kind):
    """Every event of `kind` as (run, index in the run's events, event)."""
    return [(name, i, event) for name, events in runs.items() for i, event in enumerate(events) if event[1] == kind]


def _dot(event) -> tuple[int, int]:
    return event[2], event[3]


def _attaches(rg, pos) -> bool:
    return is_successful(attach(rg, pos))


# --- 1. the reads the walker could not place ------------------------------------------------------------------


def test_today_all_1229_fail(rg, runs):
    no_plans = _reads(runs, "no_plan")
    assert len(no_plans) == 1229
    assert [not _attaches(rg, _dot(event)) for _, _, event in no_plans] == [True] * 1229


def test_shift_leaves_five_mid_air(rg, runs):
    no_plans = _reads(runs, "no_plan")
    failing = [(name, i, event) for name, i, event in no_plans if not _attaches(rg, foot_from_dot(_dot(event)))]
    assert len(failing) == 5
    assert Counter(_dot(event) for _, _, event in failing) == {(69, 21): 3, (67, 21): 2}

    # the longest wait: consecutive failing polls of one leg (a `read_failed` between two is skipped; a `leg`, a
    # `planned`, a `reached`, a `settled` or a poll that attaches ends the run)
    longest_ms = 0
    for events in runs.values():
        first_ms = last_ms = None
        for t_ms, kind, x, y in events:
            if kind == "read_failed":
                continue
            if kind == "no_plan" and not _attaches(rg, foot_from_dot((x, y))):
                first_ms = t_ms if first_ms is None else first_ms
                last_ms = t_ms
                longest_ms = max(longest_ms, last_ms - first_ms)
            else:
                first_ms = last_ms = None
    assert longest_ms <= 210, f"the walker would wait {longest_ms} ms on the longest run (OFFD140: 0.21 s)"

    # each of the 5 is in the air: at least 2 px above the ground under it (OFFD140 measured 4 px on its own profile)
    platforms = load_terrain(REAL_GROUND).unwrap().platforms
    for _, _, event in failing:
        x, y = foot_from_dot(_dot(event))
        surfaces = [p.surface_y(x) for p in platforms if p.surface_y(x) is not None]
        assert surfaces, f"no platform under x={x}"
        assert all(surface - y >= 2 for surface in surfaces), (event, y, surfaces)


# --- 2. the reads the walker did place --------------------------------------------------------------------------


def test_shift_keeps_the_good_reads(rg, runs):
    planned, reached = _reads(runs, "planned"), _reads(runs, "reached")
    assert (len(planned), len(reached)) == (159, 55)
    for name, i, event in planned + reached:
        assert _attaches(rg, _dot(event)), (name, i, event)

    lost = [(name, i, event) for name, i, event in planned + reached
            if not _attaches(rg, foot_from_dot(_dot(event)))]
    assert sorted((event[1], _dot(event)) for _, _, event in lost) == [
        ("planned", (103, 44)), ("planned", (105, 44)), ("planned", (107, 24))]

    # each is a mid-fall read: the walker settled early, and 50 ms later the character had dropped 2-3 px more. In the
    # same leg, the last `settled` read before it is at the same x, 2-3 px higher, and only `read_failed` events sit
    # between the two (the shape of every `planned` in the log: settled, read_failed, planned).
    expected_settled = {(107, 24): (107, 22), (105, 44): (105, 41), (103, 44): (103, 41)}
    for name, i, event in lost:
        j = i - 1
        while runs[name][j][1] == "read_failed":
            j -= 1
        before = runs[name][j]
        assert before[1] == "settled", f"past any `read_failed`, {event} follows {before}, not a settled read"
        assert _dot(before) == expected_settled[_dot(event)], (event, before)
        assert 0 < event[0] - before[0] <= 60, (event, before)

    # no read that attaches both before and after the shift changes platform
    for name, i, event in planned + reached:
        after = attach(rg, foot_from_dot(_dot(event)))
        if is_successful(after):
            assert after.unwrap().platform == attach(rg, _dot(event)).unwrap().platform, (name, i, event)
