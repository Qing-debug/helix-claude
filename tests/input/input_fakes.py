"""Shared fakes for the input tests. Test modules import them with `from input_fakes import ...` (pytest puts this
directory on sys.path; there is no conftest).

`FakeClock` and `pong` were byte-identical copies in test_session_starts.py and test_soak_v2_faults.py; `FakeLink`
and `report` moved from test_input_layer.py unchanged. A fake that behaves differently stays in its own file:
test_input_layer.py `Clock` (fixed time, logs sleeps), the test_session_starts.py / test_soak_v2_faults.py link
fakes (connect/close, keys.jsonl writes, link drops), test_soak.py `DriverLink`, test_probe.py `ProbeLink`.
"""
from collections.abc import Sequence

from returns.result import Result, Success

from src.input.input_layer import Link
from src.input.link import InputError, SendReport
from src.input.protocol import Done, Pong, Step

S = 1_000_000_000


class FakeClock:
    """A `_clock` / `_sleep` pair on one fake time: every `clock()` read moves time on 1 µs (strictly increasing
    stamps), `sleep(s)` moves it on `s` seconds."""

    def __init__(self, start_ns=S):
        self.now = start_ns

    def clock(self):
        self.now += 1_000  # strictly increasing stamps
        return self.now

    def sleep(self, s):
        self.now += int(s * 1e9)


def pong():
    """A PONG that passes `BleLink.connect()`'s checks: bonded, encrypted, subscribed, mtu 247."""
    return Pong(seq=1, itvl_units=12, latency=0, timeout_units=100, mtu=247, bonded=True, encrypted=True,
                authenticated=False, subscribed=True, board_us=1)


def report(seq=7):
    """A successful SendReport (DONE status 0) with fixed stamps 1-4."""
    return SendReport(seq=seq, sent_ns=1, written_ns=2, accepted_ns=3, done_ns=4, accepted=None,
                      done=Done(seq=seq, status=0, fail_step=None, rc=0, t0_us=0, late_us=()))


class FakeLink:
    """A `Link` that records calls in order into `events` (pass a list to share it, e.g. with a clock's sleeps) and
    returns `play_result` / `release_result` (set them to fail)."""

    def __init__(self, events=None):
        self.events = [] if events is None else events
        self.play_result: Result[SendReport, InputError] = Success(report(7))
        self.release_result: Result[SendReport, InputError] = Success(report(8))

    @property
    def calls(self):
        return [e for e in self.events if e[0] in ("play", "release_all")]

    def play(self, steps: Sequence[Step]) -> Result[SendReport, InputError]:
        self.events.append(("play", tuple(steps)))
        return self.play_result

    def release_all(self) -> Result[SendReport, InputError]:
        self.events.append(("release_all",))
        return self.release_result


def _satisfies_link() -> Link:
    """Never called: lets a type checker run on this file confirm FakeLink satisfies `Link`."""
    return FakeLink()
