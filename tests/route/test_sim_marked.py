"""Hong's marked Fossil Foundry 2 map in the simulator (walk-fluid plan Task 5, item 4d; Hong's QB282).

`tests/fixtures/ff2_marked.csv` is a byte copy of the map of Hong's 12 November walk (`data/route/2026-11-12T152109Z/map.csv`):
the real-ground FF2 with its six platform-end marks and the 10 px gap between its middle platforms, (78, 45) | (88, 43).
`ff2_marked_terrain.json` is the real ground (`ff2_real_ground.json`) with each platform stretched out to its end mark, so the
world's gap is x 80 -> 88 at heights 46 and 43 (3 px apart, within `side_teleport_land_dy_px`). The rope file is the FF2
fixture's (`ff2_marked.ropes.json`, the same two ropes).

Launch point across the gap: these tests hold WALKFIX169-D1's provisional option A (`RouteGraph.gap_launch` = the far node's x
minus the reach: 69 going right). If Hong picks A2 (a 3 px margin: launch 72 going right, 94 going left), the values next
to the two one-leg scenarios below change with it.
"""

import dataclasses
import shutil
from pathlib import Path

import numpy as np
import pytest
from returns.pipeline import is_successful
from test_walker_standing import Rec, _load

from src.controller.rope_file import read_ropes, rope_file_for
from src.input.input_layer import InputLayer
from src.input.protocol import Key
from src.input.timing import TimingModel
from src.route.leg_log import LegOutcome, MemorySink
from src.route.locate import attach
from src.route.params import MovementParams
from src.route.planner import MoveKind, next_move
from src.route.ropes import RopeZones
from src.route.route_graph import EdgeKind
from src.route.sim.link import SimClock, SimLink, SimPositionSource
from src.route.sim.run import REAL_GROUND_NOISE, REAL_GROUND_SIM, judge, run_sim
from src.route.sim.world import NoiseConfig, SimTerrain, SimWorld, load_terrain
from src.route.walker import RouteWalker, WalkerConfig

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
MARKED_CSV = FIXTURES / "ff2_marked.csv"
MARKED_TERRAIN = FIXTURES / "ff2_marked_terrain.json"
LEFT_END_X = 80        # where the world's mid-left ends
RIGHT_START_X = 88     # where its mid-right starts
MID_ROWS = (41, 47)    # the reads on the two middle platforms (y 46 and 43); the floor is 61, the top 24-28
WALK_PX_PER_S = MovementParams().walk_px_per_s


@pytest.fixture(scope="module")
def marked():
    return _load(MARKED_CSV)


@pytest.fixture(scope="module")
def terrain() -> SimTerrain:
    return load_terrain(MARKED_TERRAIN).unwrap()


def _two_stop_map(tmp_path: Path) -> Path:
    """The marked map with its rotation cut to the two nodes either side of the gap, (59, 46) then (100, 42): the other blue
    nodes become orange ones, every row keeps its place (the order of `node_map` can change a map's edges), and the rope
    file goes beside it. The fixture itself stays Hong's bytes."""
    rows = []
    for line in MARKED_CSV.read_text(encoding="utf-8").splitlines():
        if line.startswith("blue_node,") and '"(59, 46)"' not in line and '"(100, 42)"' not in line:
            line = "orange_node," + line[len("blue_node,"):]
        rows.append(line)
    csv = tmp_path / "two_stops.csv"
    csv.write_text("\n".join(rows) + "\n", encoding="utf-8")
    shutil.copy(FIXTURES / "ff2_marked.ropes.json", rope_file_for(csv))
    return csv


def _leg(marked, terrain, start, target, params, noise=None, seed=1):
    """One leg of the walker from `start` (a point on a platform's surface) to the node `target` in a world with `params`
    (and no noise unless given). Returns (leg record, world, observer)."""
    noise = NoiseConfig.off() if noise is None else noise
    ropes = read_ropes(rope_file_for(MARKED_CSV)).unwrap().ropes
    world = SimWorld(terrain, marked.rg.reach_h, marked.rg.reach_v, params, noise, np.random.default_rng(seed), start,
                     ropes)
    clock = SimClock()
    layer = InputLayer(SimLink(world, clock, params), TimingModel(np.random.default_rng(seed + 1)))
    positions = SimPositionSource(world, clock, noise.miss_p, np.random.default_rng(seed + 2))
    rec = Rec()
    walker = RouteWalker(marked.rg, marked.rotation, layer, positions, clock, MemorySink(), np.random.default_rng(seed + 3),
                         WalkerConfig(use_stimulus=False, leg_timeout_s=60.0), observer=rec,
                         ropes=RopeZones.build(ropes, marked.rg.params))
    leg = walker.walk_leg(1, 0, None, marked.nodes[target])
    return leg, world, rec


def _planned(rec):
    """The `planned` events as (read, kind, direction, walk_ms, toward)."""
    return [(e[2], e[3], e[4], e[5], e[7]) for e in rec.of("planned")]


# --- 1. the run --------------------------------------------------------------------------------------------


def test_marked_map_run_passes():
    """Seed 2, 20 laps, the real-ground noise and teleport length, on Hong's marked map: the first run of the walker on it in
    the simulator. 6/6 checks, no leg ends `off_map`; the figures are pinned so the quick `tools/check.py` sees any drift.
    The rotation never crosses the gap (the gap tests below do), so the figures come from the platform-end rule alone."""
    result = run_sim(MARKED_CSV, MARKED_TERRAIN, 20, 2, noise=REAL_GROUND_NOISE, sim_params=REAL_GROUND_SIM)
    assert is_successful(result), result
    report = result.unwrap()
    checks = judge(report, 0)
    assert len(checks) == 6 and all(check.ok for check in checks), [check for check in checks if not check.ok]
    assert report.off_map == 0
    assert (report.moves, report.no_ops, report.drops, report.teleport_downs, round(report.sim_seconds, 4)) == (
        372, 20, 0, 44, 263.7612)


# --- 2. the gap --------------------------------------------------------------------------------------------


def test_marked_map_middle_platforms_are_two(marked):
    """The two marks facing each other split the merged middle platform: (78, 45) and (88, 43) are on different platforms,
    both ends are stops, and the sideways teleport between them is a GAP edge each way."""
    left, right = marked.nodes[(78, 45)], marked.nodes[(88, 43)]
    p_left, p_right = marked.rg.terrain.platform_index(left), marked.rg.terrain.platform_index(right)
    assert p_left != p_right
    assert marked.rg.graph.edges[left, right]["kind"] == EdgeKind.GAP
    assert marked.rg.graph.edges[right, left]["kind"] == EdgeKind.GAP
    assert marked.rg.split_ends == frozenset({(p_left, +1), (p_right, -1)})
    # the rest of the middle row is the platforms' own: (59, 46) is mid-left's, (100, 42) mid-right's
    assert marked.rg.terrain.platform_index(marked.nodes[(59, 46)]) == p_left
    assert marked.rg.terrain.platform_index(marked.nodes[(100, 42)]) == p_right


def test_marked_map_crosses_the_gap_by_teleport(tmp_path):
    """A known case from (59, 46) to (100, 42), and back, 20 laps under the real-ground noise: reached every time, and no
    planned walk on the middle platforms crosses the 80 -> 88 gap, in either direction. Hong's rotation has no leg across the
    gap, so `_two_stop_map` cuts it to these two nodes (the plan's literal `known_cases` on the file's own rotation matches no
    leg: 0 of 0). Every lap takes the GAP edge each way by a planned teleport, which is what keeps the walk out of it."""
    csv = _two_stop_map(tmp_path)
    rec = Rec()
    there, back = ((59, 46), (100, 42)), ((100, 42), (59, 46))
    result = run_sim(csv, MARKED_TERRAIN, 20, 2, noise=REAL_GROUND_NOISE, sim_params=REAL_GROUND_SIM,
                     known_cases=(there, back), observer=rec)
    report = result.unwrap()
    assert report.known_cases == {"(59, 46)->(100, 42)": (20, 20), "(100, 42)->(59, 46)": (20, 20)}
    assert (report.off_map, report.stalls, report.timeouts, report.link_down) == (0, 0, 0, 0)
    by_name = {check.name: check for check in judge(report, 0)}
    assert by_name["laps"].ok and by_name["stalls"].ok and by_name["known_cases"].ok, by_name
    # "reached" alone would pass a world that drops every crossing into the gap (the walker recovers from the floor, y 61).
    # On seed 2 exactly two crossings end there, both from a mob knockback of -6 px that pushes the character off the far
    # platform's left end into the gap (a teleport from (69.25, 46) landed (88.25, 43) correctly); with `knockback_p=0.0`
    # there are none.
    assert [e[2] for e in rec.of("settled") if e[2][1] >= 55] == [(82, 61), (101, 61)]
    mid =[p for p in _planned(rec) if MID_ROWS[0] <= p[0][1] <= MID_ROWS[1]]
    walks = [p for p in mid if p[1] == MoveKind.WALK.value]
    assert walks, "the control: the walker does walk on the middle platforms"
    for (x, _), _, direction, walk_ms, _ in walks:
        end = x + (walk_ms / 1000 * WALK_PX_PER_S) * (1 if direction == Key.RIGHT.name else -1)
        assert not (x <= LEFT_END_X and end >= RIGHT_START_X) and not (x >= RIGHT_START_X and end <= LEFT_END_X), (x, end)
    # the control for the crossing: every lap teleports right toward (88, 43) and left toward (78, 45)
    teleports = [p for p in mid if p[1] == MoveKind.TELEPORT.value]
    assert sum(p[2] == Key.RIGHT.name and p[4] == (88, 43) for p in teleports) >= 20
    assert sum(p[2] == Key.LEFT.name and p[4] == (78, 45) for p in teleports) >= 20


def test_short_line_up_still_crosses(marked, terrain):
    """Review I3 + I2: from x 67, 2 px short of the launch (69 = 88 - the reach 19), the walker walks to the launch first
    and then teleports onto the node (88, 43): reached, nothing off the map, no fall to the floor, no stall.
    A2 (WALKFIX169-D1; the margin 3 px): P4 lines 67 up to 72 (5 px, more than `arrive_px`), so the teleport starts at 72."""
    surface = terrain.surface_below(67, 43)    # the way run_sim starts a world: the surface at or below the node's y - 3
    assert surface is not None
    start = (67.0, surface[1])
    assert start == (67.0, 46.0)
    leg, world, rec = _leg(marked, terrain, start, (100, 42), REAL_GROUND_SIM)
    assert leg.outcome == LegOutcome.REACHED and leg.stalls == 0 and rec.of("stall") == []
    planned = _planned(rec)
    assert planned[0][:3] == ((67, 46), MoveKind.WALK.value, Key.RIGHT.name) and planned[0][4] == (88, 43)
    teleports = [p for p in planned if p[1] == MoveKind.TELEPORT.value]
    assert [(p[0], p[2], p[4]) for p in teleports] == [((69, 46), Key.RIGHT.name, (88, 43))]   # starts at x 69
    assert [e.kind for e in world.effects] == ["walk", "teleport", "walk"]
    landing = world.effects[1]
    assert round(landing.start[0]) == 69 and landing.start[1] == 46.0
    assert landing.end[1] == 43.0 and landing.end[0] == pytest.approx(landing.start[0] + marked.rg.reach_h)
    assert RIGHT_START_X <= landing.end[0] < RIGHT_START_X + 1                      # on the node (88, 43), not 2 px past it
    assert all(e.end[1] in (46.0, 43.0) for e in world.effects) and world.pos[1] == 43.0   # never the floor (61)


def test_short_teleport_stops_at_the_end_and_replans(marked, terrain):
    """Review I3: the live teleport is 18-19 px (64 recorded jumps), so one of 18 px from the launch (69) falls short of
    (88, 43): 69 + 18 = 87, and Hong's rule stops it at the end of mid-left, (80, 46), not in the gap and not on the floor.
    The read there is placed by `locate` (2 px past the marked end node (78, 45)), and the planner plans from it: back
    to the launch, a walk left. Exact A never carries an 18 px teleport over (it would walk back and retry until the 10 s
    `stall_s` trips), so this pins that the failure is safe and leaves the margin to option A2.
    A2: from x 70 (2 short of 72, the worst P4 accepts) the 18 px teleport lands at (88, 43) on the mid-right platform, and
    from 74 with 19 px at (93, 43) (both confirmed in this simulator, noise off)."""
    params = dataclasses.replace(REAL_GROUND_SIM, side_teleport_extra_px=-1.0)   # a plain frozen dataclass: no sign check
    world = SimWorld(terrain, marked.rg.reach_h, marked.rg.reach_v, params, NoiseConfig.off(), np.random.default_rng(0),
                     (69.0, 46.0))
    effect = world.teleport(Key.RIGHT, 0)
    assert effect.end == (float(LEFT_END_X), 46.0) and world.pos == effect.end
    assert effect.settle_s == pytest.approx(params.teleport_settle_s)    # no fall
    clock = SimClock(10_000_000_000)
    read = SimPositionSource(world, clock, 0.0, np.random.default_rng(0)).read().unwrap()
    assert read == (LEFT_END_X, 46)
    assert attach(marked.rg, read).unwrap().node.minimap_position == (78, 45)      # placed, not off the map
    decision = next_move(marked.rg, read, marked.nodes[(100, 42)], np.random.default_rng(0))
    move = decision.unwrap().move
    assert move.kind == MoveKind.WALK and move.direction == Key.LEFT
    assert move.walk_ms == pytest.approx((LEFT_END_X - 69) / WALK_PX_PER_S * 1000)   # back to the launch, x 69
