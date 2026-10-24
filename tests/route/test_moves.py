"""Tests for src.route.moves (plan task B8; contract C8): `execute` makes exactly one layer call and returns its Result."""

import pytest
from returns.result import Failure, Success

from src.input.input_layer import InputLayer
from src.input.link import InputError, SendReport
from src.input.protocol import Done, Key
from src.route.moves import execute
from src.route.planner import Move, MoveKind

WALK = Move(MoveKind.WALK, Key.LEFT, 750.0)
TELEPORT_UP = Move(MoveKind.TELEPORT, Key.UP, None)
DROP = Move(MoveKind.DROP, None, None)


# Copies of the helpers in tests/input/test_input_layer.py (plan G16); copied, not imported.
class StubModel:
    """Fixed values per method; `draws` counts the values returned, as TimingModel.draws does."""

    def __init__(self, hold=100.0, gap=150.0, chord_offset=25.0, release_lag=30.0, reaction=230.0, walk=None):
        self._values = dict(hold=hold, gap=gap, chord_offset=chord_offset, release_lag=release_lag,
                            reaction=reaction)
        self._walk = walk
        self.draws = 0

    def _next(self, name):
        self.draws += 1
        return self._values[name]

    def hold(self):
        return self._next("hold")

    def gap(self):
        return self._next("gap")

    def chord_offset(self):
        return self._next("chord_offset")

    def release_lag(self):
        return self._next("release_lag")

    def reaction(self):
        return self._next("reaction")

    def walk(self, intended_ms):
        self.draws += 1
        return float(intended_ms) if self._walk is None else self._walk


def _report(seq=7):
    return SendReport(seq=seq, sent_ns=1, written_ns=2, accepted_ns=3, done_ns=4, accepted=None,
                      done=Done(seq=seq, status=0, fail_step=None, rc=0, t0_us=0, late_us=()))


class FakeLink:
    """Records calls in order into `events`."""

    def __init__(self, events=None):
        self.events = [] if events is None else events
        self.play_result = Success(_report(7))
        self.release_result = Success(_report(8))

    @property
    def calls(self):
        return [e for e in self.events if e[0] in ("play", "release_all")]

    def play(self, steps):
        self.events.append(("play", tuple(steps)))
        return self.play_result

    def release_all(self):
        self.events.append(("release_all",))
        return self.release_result


class RecordingLayer:
    """Stands in for InputLayer: records each call with its arguments and returns a distinct sentinel per method."""

    def __init__(self):
        self.calls = []
        self.sentinels = {"walk": Success("walk result"), "teleport": Success("teleport result"),
                          "drop": Success("drop result")}

    def walk(self, direction, intended_ms, stimulus_ns=None):
        self.calls.append(("walk", direction, intended_ms, stimulus_ns))
        return self.sentinels["walk"]

    def teleport(self, direction, stimulus_ns=None):
        self.calls.append(("teleport", direction, stimulus_ns))
        return self.sentinels["teleport"]

    def drop(self, stimulus_ns=None):
        self.calls.append(("drop", stimulus_ns))
        return self.sentinels["drop"]


@pytest.mark.parametrize("stimulus_ns", [123, None])
@pytest.mark.parametrize(
    "move, expected",
    [
        (WALK, lambda s: [("walk", Key.LEFT, 750.0, s)]),
        (TELEPORT_UP, lambda s: [("teleport", Key.UP, s)]),
        (DROP, lambda s: [("drop", s)]),
    ],
    ids=["walk", "teleport", "drop"],
)
def test_each_kind_makes_exactly_one_call_with_its_arguments(move, expected, stimulus_ns):
    layer = RecordingLayer()

    execute(layer, move, stimulus_ns)

    assert layer.calls == expected(stimulus_ns)


@pytest.mark.parametrize("move, name", [(WALK, "walk"), (TELEPORT_UP, "teleport"), (DROP, "drop")],
                         ids=["walk", "teleport", "drop"])
def test_returns_the_layers_result_object(move, name):
    layer = RecordingLayer()

    result = execute(layer, move, 123)

    assert result is layer.sentinels[name]


@pytest.mark.parametrize("move", [WALK, TELEPORT_UP, DROP], ids=["walk", "teleport", "drop"])
def test_real_layer_link_lost_comes_back_as_the_failure(move):
    link = FakeLink()
    link.play_result = Failure(InputError.LINK_LOST)
    layer = InputLayer(link, StubModel())

    result = execute(layer, move, None)

    assert result == Failure(InputError.LINK_LOST)
    assert [e[0] for e in link.calls] == ["play"]
