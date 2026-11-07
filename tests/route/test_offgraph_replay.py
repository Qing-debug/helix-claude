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
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pytest
from returns.pipeline import is_successful

from src.classes.classes import Kanna
from src.controller.controller import MapleAgent
from src.route.locate import attach, attach_standing
from src.route.params import MovementParams
from src.route.planner import next_move
from src.route.position import foot_from_dot
from src.route.route_graph import RouteGraph
from src.route.sim.world import load_terrain
from src.route.walker import _Stillness

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


# --- 3. the loose attach on the same polls (Task 5) --------------------------------------------------------------


def test_loose_attach_on_the_no_plan_reads(rg, runs):
    """What `attach_standing` (6 px) does with the polls that `attach` (3 px) places thinly or not at all."""
    foots = [(name, i, foot_from_dot(_dot(event))) for name, i, event in _reads(runs, "no_plan")]
    placed = [pos for _, _, pos in foots if _attaches(rg, pos)]
    assert len(placed) == 1224
    assert all(is_successful(attach_standing(rg, pos)) for pos in placed)  # within 3 px implies within 6

    # 330 polls (the character standing on the top ramp at x 71..75) have no upward spare under the strict rule: one
    # pixel higher and they would be off the graph. The loose attach keeps them with 3 px to spare.
    thin = [pos for pos in placed if not _attaches(rg, (pos[0], pos[1] - 1))]
    assert len(thin) == 330
    assert all(is_successful(attach_standing(rg, (x, y - 3))) for x, y in thin)

    # the 5 mid-air reads would snap loosely too (3.1 and 3.7 px above the ramp line): what holds them off is that none
    # is read still for `standing_s` (Task 6's timer): the longest run of one position is three polls, 103 ms.
    failing = {(name, i): pos for name, i, pos in foots if not _attaches(rg, pos)}
    assert len(failing) == 5
    assert all(is_successful(attach_standing(rg, pos)) for pos in failing.values())
    longest_ms = 0
    for name, events in runs.items():
        last = first_ms = None
        for i, (t_ms, kind, x, y) in enumerate(events):
            if kind != "no_plan":
                last = None  # any other event, a `read_failed` included, ends a run
                continue
            pos = foot_from_dot((x, y))
            if pos != last:
                last, first_ms = pos, t_ms
            if (name, i) in failing:
                longest_ms = max(longest_ms, t_ms - first_ms)
    assert longest_ms == 103 < MovementParams().standing_s * 1000


# --- 4. the still rule on the same polls (Task 6) -----------------------------------------------------------------


@dataclass
class Replay:
    """What the walker's still rule would do with the logged `no_plan` polls, poll by poll."""

    placed: int = 0
    left: int = 0                  # polls waited out: not placed, still for less than `standing_s`
    off_map: int = 0               # polls at >= `standing_s` that have no line within the band
    loose: list = field(default_factory=list)   # (leg dst Node, position, snapped point) per poll attached loosely
    longest_still_s: float = 0.0   # the longest the same unplaced position was read in a row
    longest_wait_ms: int = 0       # the longest stretch of consecutive polls left off-graph in one leg


def _replay(rg, runs, *, shift, misses_reset) -> Replay:
    """Walk each run's events in order as the walker does: a placed poll, a plan (`planned`, `reached`), a new leg
    and a failed read reset the timer. The replay does not reset after its own loose attaches (no move is replayed),
    so every poll at or after `standing_s` of one position counts as attached (OFFP141-RR N3). The fixture keeps no
    `why` for a `read_failed`, so it is either a STALE read (the timer goes on) or a miss (it restarts): `misses_reset`
    says which the replay takes every one to be."""
    nodes = {node.minimap_position: node for node in rg.graph.nodes}
    out = Replay()
    for events in runs.values():
        still, dst, first_ms, last_ms = _Stillness(), None, None, None
        for t_ms, kind, x, y in events:
            if kind == "no_plan":
                pos = foot_from_dot((x, y)) if shift else (x, y)
                if _attaches(rg, pos):
                    out.placed += 1
                    still.reset()
                    first_ms = last_ms = None
                    continue
                seconds = still.see(pos, t_ms * 1_000_000)
                out.longest_still_s = max(out.longest_still_s, seconds)
                if seconds >= rg.params.standing_s:
                    snapped = attach_standing(rg, pos)
                    if is_successful(snapped):
                        out.loose.append((dst, pos, snapped.unwrap()))
                    else:
                        out.off_map += 1
                    first_ms = last_ms = None
                else:
                    out.left += 1
                    first_ms = t_ms if first_ms is None else first_ms
                    last_ms = t_ms
                    out.longest_wait_ms = max(out.longest_wait_ms, last_ms - first_ms)
            elif kind == "read_failed":
                if misses_reset:
                    still.reset()   # a `read_failed` between two polls does not end a wait either way
            else:
                still.reset()
                first_ms = last_ms = None
                if kind == "leg":
                    dst = nodes[(x, y)]
    return out


def test_option_c_replay(rg, runs):
    """OFFD140's "shift 3 + still" row: with part 1's shift the still rule has nothing left to do on these logs."""
    for misses_reset in (True, False):   # every `read_failed` a miss (the plan's replay), or every one a STALE read
        out = _replay(rg, runs, shift=True, misses_reset=misses_reset)
        assert out.placed + out.left == 1229
        assert (out.left, len(out.loose), out.off_map) == (5, 0, 0)
        assert out.longest_still_s < MovementParams().standing_s
        assert out.longest_wait_ms <= 210


def test_rule_alone_replay(rg, runs):
    """OFFD140's "still 0.5 s, band 6" row, on the logged positions as they are (no shift): the still rule alone."""
    # The figures are OFFD140's, whose replay let the timer run through every logged `read_failed`: they are STALE
    # reads, which the walker does not count. All 244 `read_failed` events of the fixture are `why: stale` (OFFG-R6
    # checked them against the `steps.jsonl` of the 8 source runs; the fixture itself keeps no `why`). Were each a miss
    # that restarts the timer, 817 polls would attach and 412 wait, the longest wait 1.9 s (see the next test): the
    # same rule, a stricter reading of the log.
    out = _replay(rg, runs, shift=False, misses_reset=False)
    print(f"rule alone: placed {out.placed}, loose {len(out.loose)}, left {out.left}, off_map {out.off_map}, "
          f"longest wait {out.longest_wait_ms} ms, longest still {out.longest_still_s:.3f} s")
    assert out.placed == 0
    assert (len(out.loose), out.left, out.off_map) == (1027, 202, 0)
    assert round(out.longest_wait_ms / 1000, 1) == 1.3

    top = attach(rg, (49, 28)).unwrap().platform
    offsets = []
    for dst, pos, snapped in out.loose:
        assert attach(rg, snapped).unwrap().platform == top, (pos, snapped)
        offsets.append(abs(snapped[1] - pos[1]))
        assert is_successful(next_move(rg, snapped, dst, np.random.default_rng(0))), (pos, snapped, dst)
    assert 3 <= min(offsets) and max(offsets) <= 6   # OFFD140: 3.2-5.5 px from the line, here rounded to whole px


def test_rule_alone_replay_with_every_read_failed_a_miss(rg, runs):
    """The same replay, each logged `read_failed` taken as a miss that restarts the timer: fewer polls attach, none is
    off the map, and every snapped point still plans."""
    out = _replay(rg, runs, shift=False, misses_reset=True)
    assert (len(out.loose), out.left, out.off_map) == (817, 412, 0)
    assert round(out.longest_wait_ms / 1000, 1) == 1.9
    assert all(is_successful(next_move(rg, snapped, dst, np.random.default_rng(0))) for dst, _, snapped in out.loose)
