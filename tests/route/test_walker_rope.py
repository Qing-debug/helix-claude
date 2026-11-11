"""Tests for the walker's rope rule (rope fix plan, Task 6, H88 and H93; Hong's DBG146-D1 "always blink when in reach",
D2 "Detect it, blink off", D3 "keep lining up").

The rule: a read that neither the planner nor `locate.attach` places, inside a marked rope's zone and held `rope_still_s`
(0.1 s), gets `planner.rope_move`'s moves in order: blinks (`TELEPORT`) to the nearer platform in reach, then one
`JUMP_OFF`; with the order used up the leg ends `off_map`. A read outside every zone is H85's still rule, unchanged.

The rigs are `test_walker_standing`'s: a `SimClock`, a scripted position source (`fn(elapsed_ns) -> Result`) and a
recording layer that moves nothing, on the Fossil Foundry 2 fixture map and its rope file (rope 46: top 28, bottom 46,
read column 45; rope 107: top 43, bottom 61, read column 106). The walker's moves toward a placed read are the planner's
and are told apart from a rope move by the observer's `planned` event: a rope move has `toward` None.
"""

import json
import math
from types import SimpleNamespace

import numpy as np
import pytest
from returns.pipeline import is_successful
from returns.result import Success
from test_walker_standing import (
    CSV,
    OFF_MAP_LINE,
    POLL_NS,
    STANDING_NS,
    Rec,
    RecordingLayer,
    S,
    ScriptedSource,
    _load,
    _write_map,
    steps,
)

from src.controller.rope_file import read_ropes, rope_file_for
from src.route import walker as walker_module
from src.route.leg_log import JsonlLegLog, LegOutcome, MemorySink
from src.route.locate import attach
from src.route.params import MovementParams
from src.route.planner import next_move
from src.route.ropes import RopeZones
from src.route.sim.link import SimClock
from src.route.walker import RouteWalker, WalkerConfig

MARKS = read_ropes(rope_file_for(CSV)).unwrap().ropes
ZONES = RopeZones.build(MARKS, MovementParams())
LEFT_ROPE, RIGHT_ROPE = 0, 1
RIGHT_ROPE_READ = (106, 49)   # 6 px under the mid-right platform's row (43), 12 over the floor's (61)
LEFT_ROPE_READ = (46, 35)     # 7 px under the top platform's row (28), 11 over the mid-left's (46): the rope-46 column
MID_RIGHT = (112, 42)         # on the mid-right platform: placed, with a path to the floor node
FLOOR_RIGHT = (113, 60)
ROPE_LINE = "the character is on the rope at x {x}, at ({px}, {py}), and could not get off it; ending the run"
ROPE_STILL_NS = round(MovementParams().rope_still_s * S)


class RopeLayer(RecordingLayer):
    """`RecordingLayer` that also takes `jump_off` and keeps each key move as (kind, direction name)."""

    def __init__(self, clock):
        super().__init__(clock)
        self.keys = []

    def teleport(self, direction, stimulus_ns=None):
        self.keys.append(("teleport", direction.name))
        return super().teleport(direction, stimulus_ns)

    def jump_off(self, direction, stimulus_ns=None):
        self.keys.append(("jump_off", direction.name))
        return self._play("jump_off")


@pytest.fixture(scope="module")
def ff2():
    return _load(CSV)


def _rig(world, fn, *, ropes=ZONES, leg_timeout_s=3.0, stop_at_s=None, sink=None, **walker_args):
    """A walker over `world` with the rope zones `ropes` (none: the keyword left out), a scripted source `fn`, a rope
    layer and an observer."""
    clock = SimClock()
    source = ScriptedSource(clock, fn)
    layer = RopeLayer(clock)
    rec, lines = Rec(), []
    config = WalkerConfig(use_stimulus=False, leg_timeout_s=leg_timeout_s)
    walker = RouteWalker(world.rg, world.rotation, layer, source, clock, MemorySink() if sink is None else sink,
                         np.random.default_rng(0), config, observer=rec, log=lines.append,
                         **({} if ropes is None else {"ropes": ropes}), **walker_args)
    if stop_at_s is not None:
        source.on_read = lambda elapsed: walker._stop.set() if elapsed >= round(stop_at_s * S) else None
    return SimpleNamespace(walker=walker, clock=clock, source=source, layer=layer, rec=rec, lines=lines)


def _leg(world, rig, dst_xy):
    return rig.walker.walk_leg(1, 0, None, world.nodes[dst_xy])


def _rope_moves(rig):
    """The `planned` events that were rope moves (`toward` None), as (t_ns, kind, direction name)."""
    return [(e[1], e[3], e[4]) for e in rig.rec.of("planned") if e[7] is None]


def _placed(world, pos, dst_xy):
    """`pos` is read as on the map and has a path to `dst_xy`: the rope zones are none of its business."""
    assert is_successful(attach(world.rg, pos)), pos
    assert is_successful(next_move(world.rg, pos, world.nodes[dst_xy], np.random.default_rng(0))), pos


# --- the rule ------------------------------------------------------------------------------------------------


def test_rope_read_blinks_up_after_a_tenth_of_a_second(ff2):
    assert ZONES.at(RIGHT_ROPE_READ) == RIGHT_ROPE and not is_successful(attach(ff2.rg, RIGHT_ROPE_READ))
    _placed(ff2, MID_RIGHT, FLOOR_RIGHT)
    rig = _rig(ff2, steps((0, Success(RIGHT_ROPE_READ)), (0.2, Success(MID_RIGHT))), leg_timeout_s=1.0)

    record = _leg(ff2, rig, FLOOR_RIGHT)

    assert record.outcome != LegOutcome.OFF_MAP and rig.lines == []
    assert rig.layer.keys[0] == ("teleport", "UP")
    assert rig.layer.plays[0][0] - rig.source.t0 == ROPE_STILL_NS < STANDING_NS   # 0.1 s of the read, not 0.5 s
    assert _rope_moves(rig) == [(rig.layer.plays[0][0], "teleport", "UP")]
    assert [e for e in rig.rec.of("planned") if e[1] > rig.layer.plays[0][0] and e[7] is not None]  # the leg went on


def test_rope_46_read_at_its_column_gets_a_rope_move(ff2):
    """ROPE148R m-3: the rope-46 hanging read seen live was at x 46, the column. 7 px up to `top_y` 28, 11 down to 46."""
    assert ZONES.at(LEFT_ROPE_READ) == LEFT_ROPE and not is_successful(attach(ff2.rg, LEFT_ROPE_READ))
    rig = _rig(ff2, steps((0, Success(LEFT_ROPE_READ)), (0.2, Success((58, 46)))), leg_timeout_s=1.0)

    record = _leg(ff2, rig, (58, 46))

    assert rig.layer.keys == [("teleport", "UP")]
    assert rig.layer.plays[0][0] - rig.source.t0 == ROPE_STILL_NS
    assert record.outcome != LegOutcome.OFF_MAP


def test_two_blinks_then_jump_off_then_off_map(ff2):
    rig = _rig(ff2, steps((0, Success(RIGHT_ROPE_READ))), leg_timeout_s=30.0)

    result = rig.walker.run()

    assert is_successful(result)
    summary = result.unwrap()
    assert (summary.laps_completed, summary.legs, summary.reason) == (0, 1, "off_map")
    assert rig.layer.keys == [("teleport", "UP"), ("teleport", "UP"), ("jump_off", "RIGHT")]
    assert rig.rec.of("leg_end")[0][2] == "off_map"
    assert rig.lines == [ROPE_LINE.format(x=107, px=106, py=49)]
    assert rig.layer.releases >= 1


def test_rope_count_restarts_on_a_placed_read(ff2):
    """UP, then a placed read, then the rope again: the count is 0 again, so three UPs come before the jump off (a count
    that had not restarted would jump off after two)."""
    _placed(ff2, MID_RIGHT, FLOOR_RIGHT)
    rig = _rig(ff2, steps((0, Success(RIGHT_ROPE_READ)), (0.2, Success(MID_RIGHT)), (1.5, Success(RIGHT_ROPE_READ))),
               leg_timeout_s=30.0)

    record = _leg(ff2, rig, FLOOR_RIGHT)

    assert record.outcome == LegOutcome.OFF_MAP
    assert [(kind, direction) for _, kind, direction in _rope_moves(rig)] == [
        ("teleport", "UP"), ("teleport", "UP"), ("teleport", "UP"), ("jump_off", "RIGHT")]
    assert any(e[7] is not None for e in rig.rec.of("planned"))   # the placed read had moves of its own in between


class CountingZones(RopeZones):
    """The real zones that count every `at` call."""

    def __init__(self):
        super().__init__(ZONES._marks, ZONES._cells)
        self.asked = []

    def at(self, pos):
        self.asked.append(pos)
        return super().at(pos)


@pytest.mark.parametrize("rule, pos", [("A1", (82, 60)), ("A2", (90, 60)), ("A3", (34, 60))])
def test_placed_read_with_a_path_never_asks_the_zones(ff2, rule, pos):
    """Review Focus 1 and the speed constraint: a read the planner plans from does no new work."""
    _placed(ff2, pos, FLOOR_RIGHT)
    zones = CountingZones()
    rig = _rig(ff2, steps((0, Success(pos))), ropes=zones, leg_timeout_s=0.6)

    record = _leg(ff2, rig, FLOOR_RIGHT)

    assert zones.asked == [], rule
    assert record.moves and rig.rec.of("planned")


TWO_COLUMNS = [(40, 60), (80, 60), (40, 10), (80, 10)]
A2_COLUMNS = [(40, 60), (60, 60), (40, 10), (80, 10)]   # a floor segment between two close nodes, nothing above it


@pytest.mark.parametrize("rule, pos, nodes", [("A1", (81, 60), TWO_COLUMNS), ("A2", (50, 60), A2_COLUMNS),
                                              ("A3", (90, 60), TWO_COLUMNS)])
def test_placed_read_without_a_path_never_asks_the_zones(tmp_path, rule, pos, nodes):
    """The same for a read `attach` places but the planner finds no path from (waiting is as today)."""
    world = _load(_write_map(tmp_path / "two.csv", nodes))
    dst = world.nodes[(80, 10)]
    assert is_successful(attach(world.rg, pos)), rule
    assert "no path" in next_move(world.rg, pos, dst, np.random.default_rng(0)).failure()
    zones = CountingZones()
    rig = _rig(world, steps((0, Success(pos))), ropes=zones, leg_timeout_s=1.0)

    record = _leg(world, rig, (80, 10))

    assert zones.asked == [], rule
    assert record.outcome == LegOutcome.TIMED_OUT and rig.layer.plays == [] and record.off_graph > 10


def test_falling_read_in_a_zone_gets_no_rope_move(ff2):
    """Review Focus 3: y + 3 at every poll through rope 107's zone. The read never holds 0.1 s, so no key is sent."""
    falling = [(106, 47 + 3 * k) for k in range(4)]
    for pos in falling:
        assert ZONES.at(pos) == RIGHT_ROPE and not is_successful(attach(ff2.rg, pos)), pos
    rig = _rig(ff2, lambda elapsed: Success((106, 47 + 3 * (elapsed // POLL_NS))), leg_timeout_s=0.19)

    record = _leg(ff2, rig, FLOOR_RIGHT)

    assert [e[2] for e in rig.rec.of("no_plan")] == falling
    assert record.outcome == LegOutcome.TIMED_OUT and rig.layer.plays == [] and rig.lines == []


def test_off_zone_still_read_is_h85(ff2):
    """(90, 35) is in no zone: H85's line and `off_map`, no key. 5 px over a floor line outside every zone: H85's loose
    attach (the target is reached from it)."""
    assert ZONES.at((90, 35)) is None and ZONES.at((82, 55)) is None
    rig = _rig(ff2, steps((0, Success((90, 35)))), leg_timeout_s=30.0)

    record = _leg(ff2, rig, FLOOR_RIGHT)

    assert record.outcome == LegOutcome.OFF_MAP
    assert rig.lines == [OFF_MAP_LINE.format(x=90, y=35)] and rig.layer.plays == []
    first_no_plan = rig.rec.of("no_plan")[0][1]
    assert STANDING_NS <= rig.rec.of("leg_end")[0][1] - first_no_plan < STANDING_NS + POLL_NS

    loose = _rig(ff2, steps((0, Success((82, 55)))), leg_timeout_s=5.0)
    assert _leg(ff2, loose, (82, 60)).outcome == LegOutcome.REACHED
    assert loose.layer.plays == [] and loose.lines == []


def test_stop_wins_on_a_rope(ff2, monkeypatch):
    """Review Focus 2: the flag set during the 0.1 s of the hold, and set between `rope_move` and the send."""
    during = _rig(ff2, steps((0, Success(RIGHT_ROPE_READ))), stop_at_s=0.07)
    assert _leg(ff2, during, FLOOR_RIGHT).outcome == LegOutcome.STOPPED
    assert during.layer.plays == [] and during.lines == []

    between = _rig(ff2, steps((0, Success(RIGHT_ROPE_READ))))
    real = walker_module.rope_move

    def stopping(*args):
        between.walker._stop.set()   # after the rope move is chosen, before the send
        return real(*args)

    monkeypatch.setattr(walker_module, "rope_move", stopping)
    assert _leg(ff2, between, FLOOR_RIGHT).outcome == LegOutcome.STOPPED
    assert between.layer.plays == [] and between.rec.of("sent") == [] and between.lines == []


def test_rope_move_is_logged_like_a_move(ff2, tmp_path):
    """The observer sees `planned` (a rope move: `toward` None), then `sent` and `settled`; the `MoveEntry` has a finite
    cost (0.0 before the leg's first plan, where `best` is infinite) and the leg line is strict JSON."""
    opened = JsonlLegLog.open(tmp_path / "run", fsync=False)
    assert is_successful(opened)
    log = opened.unwrap()
    rig = _rig(ff2, steps((0, Success(RIGHT_ROPE_READ)), (0.2, Success(MID_RIGHT))), leg_timeout_s=1.0, sink=log)

    record = _leg(ff2, rig, FLOOR_RIGHT)

    first = rig.rec.of("planned")[0]
    assert (first[3], first[4], first[7]) == ("teleport", "UP", None) and first[2] == RIGHT_ROPE_READ
    names = [e[0] for e in rig.rec.events]
    assert names.index("planned") < names.index("sent") < names.index("settled")
    entry = record.moves[0]
    assert (entry.kind, entry.direction, entry.pos, entry.input) == ("teleport", "UP", RIGHT_ROPE_READ, "ok")
    assert math.isfinite(entry.cost_to_go_s) and entry.cost_to_go_s == 0.0 and first[6] == 0.0
    assert is_successful(log.write_leg(record))
    line = (tmp_path / "run" / "legs.jsonl").read_text(encoding="utf-8").splitlines()[0]
    assert json.loads(line, parse_constant=_refuse)["moves"][0]["kind"] == "teleport"
    log.close()


def _refuse(constant):
    raise AssertionError(f"not strict JSON: {constant}")


def test_no_ropes_is_today(ff2):
    """A walker built without `ropes` (`RopeZones.EMPTY`) is H85's: the same read gets no rope move at 0.1 s."""
    rig = _rig(ff2, steps((0, Success(RIGHT_ROPE_READ))), ropes=None, leg_timeout_s=2.0)

    _leg(ff2, rig, FLOOR_RIGHT)

    assert rig.walker._ropes is RopeZones.EMPTY
    assert _rope_moves(rig) == []
    assert not [k for k in rig.layer.keys if k[0] == "jump_off"]
    assert not rig.layer.plays or rig.layer.plays[0][0] - rig.source.t0 >= STANDING_NS
