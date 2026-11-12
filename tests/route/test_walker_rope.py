"""Tests for the walker's rope rule (rope fix plan, Task 6, H88 and H93; Hong's DBG146-D1 "always blink when in reach",
D2 "Detect it, blink off", D3 "keep lining up").

The rule: a read that neither the planner nor `locate.attach` places, inside a marked rope's zone and held `rope_still_s`
(0.1 s), gets `planner.rope_move`'s moves in order: blinks (`TELEPORT`) to the nearer platform in reach, then one
`JUMP_OFF`; with the order used up the leg ends `off_map`. A read outside every zone is H85's still rule, unchanged.
Since Hong's option B for C10 (2026-11-12) a read the map DOES place counts too, when the move the walker sent from that
very read left the read the same (a move that did nothing) and the read is in a zone: the same rule, from the same hold.

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


def test_rope_count_restarts_after_the_h85_snap(ff2):
    """ROPE154-R6 m1: UP, UP, then a 0.5 s still read off the rope that H85 snaps onto a platform line (the character
    stands on a platform, so it is off the rope), then the rope again: the order starts from the blinks (H88, H93), so two
    more UPs come before the jump off (a count kept across the snap would jump off at once)."""
    snap_read = (82, 55)   # in no zone, 5 px over a floor line outside every rope zone: H85's loose attach
    assert ZONES.at(snap_read) is None and not is_successful(attach(ff2.rg, snap_read))
    rig = _rig(ff2, steps((0, Success(RIGHT_ROPE_READ)), (0.5, Success(snap_read)), (1.5, Success(RIGHT_ROPE_READ))),
               leg_timeout_s=30.0)

    record = _leg(ff2, rig, FLOOR_RIGHT)

    assert record.outcome == LegOutcome.OFF_MAP
    assert rig.layer.keys == [("teleport", "UP"), ("teleport", "UP"),
                              ("teleport", "RIGHT"),                      # H85's snap to (82, 60), the planner's move
                              ("teleport", "UP"), ("teleport", "UP"), ("jump_off", "RIGHT")]
    assert [(kind, direction) for _, kind, direction in _rope_moves(rig)] == [
        ("teleport", "UP"), ("teleport", "UP"), ("teleport", "UP"), ("teleport", "UP"), ("jump_off", "RIGHT")]
    assert rig.lines == [ROPE_LINE.format(x=107, px=106, py=49)]


class CountingZones(RopeZones):
    """The real zones that count every `at` call and say how many moves had been sent (`layer.plays`, set by the test)."""

    def __init__(self):
        super().__init__(ZONES._marks, ZONES._cells)
        self.asked = []
        self.plays_when_asked = []
        self.layer = None

    def at(self, pos):
        self.asked.append(pos)
        self.plays_when_asked.append(0 if self.layer is None else len(self.layer.plays))
        return super().at(pos)


@pytest.mark.parametrize("rule, pos", [("A1", (82, 60)), ("A2", (90, 60)), ("A3", (34, 60))])
def test_placed_read_with_a_path_asks_the_zones_only_after_a_no_op(ff2, rule, pos):
    """Review Focus 1 and the speed constraint (C10, option B): a read the planner plans from does no new work. The zone
    table is asked only for the read the last move was sent from (a move that did nothing), so never before the first
    move is sent. The scripted layer moves nothing, so every read after the first send is such a read."""
    _placed(ff2, pos, FLOOR_RIGHT)
    zones = CountingZones()
    rig = _rig(ff2, steps((0, Success(pos))), ropes=zones, leg_timeout_s=0.6)
    zones.layer = rig.layer

    record = _leg(ff2, rig, FLOOR_RIGHT)

    assert record.moves and rig.rec.of("planned")
    assert zones.asked and set(zones.asked) == {pos}, rule
    assert min(zones.plays_when_asked) >= 1, rule   # not once before the first move was sent


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


# --- C10, Hong's option B (2026-11-12): a move that did nothing on a placed read in a zone starts the rope rule -----
#
# A read the map places (within 3 px of a platform line) is planned as a platform read, but a character that hangs on a
# rope does nothing for a walk (Hong: "on a rope, pressing only Left or Right (no jump) -> nothing happens"). So when
# the move sent from a read leaves the next read the same, and the read is in a rope zone, the walker takes the rope
# rule: the same hold (`rope_still_s`), the same order (blinks, one jump off, then `off_map`). "Did nothing" is the
# walker's own test of a still character, exact equality of two reads (`_Stillness`, `_settle`), not a new threshold.

TOP_107, TOP_107_B = (106, 44), (106, 45)   # rope 107's placed top rows 44-45 (the platform line at 43 is within 3 px)
FOOT_107, FOOT_107_EDGE = (106, 60), (108, 60)   # its placed foot rows 57-61; x 108 is the zone's outer column
TOP_46, FOOT_46 = (45, 30), (45, 45)        # rope 46's placed rows 29-31 and 42-46 (read column 45)
UP, DOWN = ("teleport", "UP"), ("teleport", "DOWN")

# (read, target, the planner's move from the read, the rope rule's blink, the jump off's direction)
C10_CASES = [
    pytest.param(TOP_107, (58, 46), ("walk", "LEFT"), UP, "LEFT", id="rope107-top"),
    pytest.param(TOP_107_B, (101, 24), ("walk", "LEFT"), UP, "LEFT", id="rope107-top-second-row"),
    pytest.param(FOOT_107, FLOOR_RIGHT, ("walk", "RIGHT"), DOWN, "RIGHT", id="rope107-foot"),
    pytest.param(FOOT_107_EDGE, (101, 24), ("walk", "LEFT"), DOWN, "LEFT", id="rope107-foot-outer-column"),
    pytest.param(TOP_46, (37, 60), ("walk", "LEFT"), UP, "LEFT", id="rope46-top"),
    pytest.param(FOOT_46, (82, 60), ("walk", "RIGHT"), DOWN, "RIGHT", id="rope46-foot"),
]


def _planned_move(world, pos, dst_xy):
    """The planner's move from the placed zone read `pos`, as (kind, direction name)."""
    assert ZONES.at(pos) is not None and is_successful(attach(world.rg, pos)), pos
    move = next_move(world.rg, pos, world.nodes[dst_xy], np.random.default_rng(0)).unwrap().move
    return move.kind.value, move.direction.name


def _kinds(rig):
    return [kind for _, kind in rig.layer.plays]


@pytest.mark.parametrize("pos, dst, first, blink, jump", C10_CASES)
def test_a_move_that_does_nothing_on_a_placed_rope_read_blinks(ff2, pos, dst, first, blink, jump):
    """The first move is the planner's (a walk the rope ignores); the read stays, and after the hold the rope rule
    blinks toward the nearer platform. The leg goes on from the platform (the source then shows the target)."""
    assert _planned_move(ff2, pos, dst) == first
    rig = _rig(ff2, steps((0, Success(pos)), (0.32, Success(dst))), leg_timeout_s=3.0)

    record = _leg(ff2, rig, dst)

    assert record.outcome == LegOutcome.REACHED and rig.lines == []
    assert _kinds(rig) == [first[0], "teleport"] and rig.layer.keys == [blink]
    t_walk, t_blink = rig.layer.plays[0][0], rig.layer.plays[1][0]
    assert t_blink - t_walk >= ROPE_STILL_NS   # the hold: the settle, then 0.1 s of the same read
    assert _rope_moves(rig) == [(t_blink, "teleport", blink[1])]
    planned = rig.rec.of("planned")
    assert planned[0][7] is not None and planned[1][7] is None   # the walk is the planner's, the blink is the rule's


@pytest.mark.parametrize("pos, dst, first, blink, jump", C10_CASES)
def test_the_whole_order_runs_from_a_placed_rope_read_then_the_planned_move_again(ff2, pos, dst, first, blink, jump):
    """ROPE158-C10B-p2-D1 (option B). Nothing ever moves: the planner's move, two blinks, one jump off toward the target.
    The order is then used up, but the read is one the map places, so the walker does not end the run: it resets the order
    and sends the planner's move again, and the order runs again (a count reset by every placed read would never reach the
    jump; a count that was not reset would end the run, as before this ruling)."""
    rig = _rig(ff2, steps((0, Success(pos))), leg_timeout_s=10.0)

    record = _leg(ff2, rig, dst)

    assert record.outcome == LegOutcome.TIMED_OUT and rig.lines == []
    assert rig.rec.of("leg_end")[0][2] != "off_map"
    cycle = [first[0], "teleport", "teleport", "jump_off"]
    assert len(rig.layer.plays) >= 3 * len(cycle)
    assert _kinds(rig)[:3 * len(cycle)] == cycle * 3
    assert rig.layer.keys[:6] == [blink, blink, ("jump_off", jump)] * 2
    rope = [(k, d) for _, k, d in _rope_moves(rig)]
    assert rope[:6] == [(*blink, ), (*blink, ), ("jump_off", jump)] * 2
    planned = rig.rec.of("planned")   # after the jump off, the next planned move is the planner's again (`toward` set)
    assert [e[7] is not None for e in planned[:8]] == [True, False, False, False, True, False, False, False]


def test_an_unplaced_read_with_the_order_used_up_still_ends_off_map(ff2):
    """ROPE158-C10B-p2-D1: the reset is for a placed read only. The order is used up on a placed read (the planner's
    move, two blinks, the jump off), then the read becomes one the map cannot place (the character hangs 6 px under the
    platform): the order stays used up, and the leg ends `off_map` with the rope's line, as today."""
    assert ZONES.at(RIGHT_ROPE_READ) == RIGHT_ROPE and not is_successful(attach(ff2.rg, RIGHT_ROPE_READ))
    holder = []
    rig = _rig(ff2, lambda elapsed: Success(RIGHT_ROPE_READ if len(holder[0].layer.plays) >= 4 else TOP_107),
               leg_timeout_s=30.0)
    holder.append(rig)

    record = _leg(ff2, rig, (58, 46))

    assert record.outcome == LegOutcome.OFF_MAP
    assert _kinds(rig) == ["walk", "teleport", "teleport", "jump_off"]
    assert rig.layer.keys == [UP, UP, ("jump_off", "LEFT")]
    assert rig.lines == [ROPE_LINE.format(x=107, px=106, py=49)]


def test_the_order_starts_again_when_the_placed_read_moves(ff2):
    """A blink that did nothing, then the read moves to another placed zone cell (the planner's move from it, a walk
    that does nothing again): the count started again, so two more blinks come before the jump off. A count kept
    across the move would jump off after one."""
    rig = _rig(ff2, steps((0, Success(TOP_107)), (0.32, Success(TOP_107_B))), leg_timeout_s=10.0)

    record = _leg(ff2, rig, (58, 46))

    assert record.outcome == LegOutcome.TIMED_OUT   # the order used up on a placed read resets (ROPE158-C10B-p2-D1)
    assert _kinds(rig)[:6] == ["walk", "teleport", "walk", "teleport", "teleport", "jump_off"]
    assert rig.layer.keys[:4] == [UP, UP, UP, ("jump_off", "LEFT")]


def test_a_placed_rope_read_whose_move_moves_the_character_is_unchanged(ff2):
    """From the top of rope 107 toward the floor node the planner's move is a teleport down: the read changes, so the
    rope rule is not asked and the zones are not even looked at."""
    assert _planned_move(ff2, TOP_107, FLOOR_RIGHT) == DOWN
    zones = CountingZones()
    rig = _rig(ff2, steps((0, Success(TOP_107)), (0.05, Success(FLOOR_RIGHT))), ropes=zones, leg_timeout_s=3.0)
    zones.layer = rig.layer

    record = _leg(ff2, rig, FLOOR_RIGHT)

    assert record.outcome == LegOutcome.REACHED
    assert rig.layer.keys == [DOWN] and _kinds(rig) == ["teleport"] and _rope_moves(rig) == []
    assert zones.asked == []


@pytest.mark.parametrize("pos", [(82, 60), (90, 60), (34, 60), (70, 24)])
def test_a_no_op_outside_every_zone_is_unchanged(ff2, pos):
    """A move that does nothing on a placed read in no zone: the same moves at the same times as a walker with no rope
    file, no rope move, no `off_map`."""
    assert ZONES.at(pos) is None
    _placed(ff2, pos, FLOOR_RIGHT)
    with_zones = _rig(ff2, steps((0, Success(pos))), leg_timeout_s=2.0)
    without = _rig(ff2, steps((0, Success(pos))), ropes=None, leg_timeout_s=2.0)

    record = _leg(ff2, with_zones, FLOOR_RIGHT)
    _leg(ff2, without, FLOOR_RIGHT)

    assert record.outcome == LegOutcome.TIMED_OUT and with_zones.lines == []
    assert with_zones.layer.plays == without.layer.plays and len(with_zones.layer.plays) > 3
    assert _rope_moves(with_zones) == [] and with_zones.rec.of("planned")
    assert not [k for k in with_zones.layer.keys if k[0] == "jump_off" or k[1] in ("UP", "DOWN")]


def test_a_map_with_no_rope_file_ignores_a_placed_no_op(ff2):
    """The same read as the top of rope 107, with no zones: the walk is repeated, as before the rope fix."""
    rig = _rig(ff2, steps((0, Success(TOP_107))), ropes=None, leg_timeout_s=2.0)

    record = _leg(ff2, rig, (58, 46))

    assert record.outcome == LegOutcome.TIMED_OUT
    assert set(_kinds(rig)) == {"walk"} and len(rig.layer.plays) > 3 and _rope_moves(rig) == []


def test_stop_wins_during_the_hold_after_a_placed_no_op(ff2):
    """The flag set while the rope rule waits its 0.1 s (after the settle): no blink is sent."""
    rig = _rig(ff2, steps((0, Success(TOP_107))), stop_at_s=0.25)

    record = _leg(ff2, rig, (58, 46))

    assert record.outcome == LegOutcome.STOPPED and _kinds(rig) == ["walk"] and rig.lines == []
