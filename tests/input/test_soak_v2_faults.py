"""V2 step 4 readiness (plan A, Task V2): the fault soak runs through today's soak CLI unchanged.

`python -m src.input.harness.soak --commands 1000 --short --seed 42 --faults reset_board=5,bluetooth_off_on=5`
with the CLI's own seams faked (link, listener, process list, clock) and the real `faults.reset_board` /
`faults.bluetooth_off_on` replaced by fakes that drop the link, so the default INJECTORS path is the one tested.
No board, no radio, no window.
"""
import json

from returns.result import Failure, Success

from src.input import input_layer
from src.input.harness import faults, soak
from src.input.link import InputError, SendReport, SessionInfo
from src.input.protocol import Accepted, Done, Pong

S = 1_000_000_000
ARGV = ["--commands", "1000", "--short", "--seed", "42", "--faults", "reset_board=5,bluetooth_off_on=5"]


class Clock:
    def __init__(self):
        self.now = S

    def clock(self):
        self.now += 1_000
        return self.now

    def sleep(self, s):
        self.now += int(s * 1e9)


def _pong():
    return Pong(seq=1, itvl_units=12, latency=0, timeout_units=100, mtu=247, bonded=True, encrypted=True,
                authenticated=False, subscribed=True, board_us=1)


class Link:
    """A link the injectors drop: faults() gets a LINK_LOST 0.5 s after the injection, plays fail NOT_CONNECTED
    until close() + connect()."""

    def __init__(self, clock):
        self.clock = clock
        self.connected = False
        self.events = []
        self.fault_log = []

    def connect(self, timeout_s=30.0):
        if self.connected:
            return Failure("already connected")
        self.events.append("connect")
        self.connected = True
        return Success(SessionInfo("AA:BB", 247, _pong()))

    def close(self):
        self.events.append("close")
        self.connected = False

    def drop(self, at_ns):
        self.connected = False
        self.fault_log.append((at_ns, InputError.LINK_LOST))

    def play(self, steps):
        self.events.append("play")
        self.clock.now += S // 2
        if not self.connected:
            return Failure(InputError.NOT_CONNECTED)
        n = self.clock.clock()
        return Success(SendReport(seq=1, sent_ns=n, written_ns=n, accepted_ns=n, done_ns=n, accepted=Accepted(1, 0),
                                  done=Done(seq=1, status=0, fail_step=None, rc=0, t0_us=0, late_us=())))

    def release_all(self):
        self.events.append("release_all")
        return Failure(InputError.NOT_CONNECTED)

    def take_pongs(self):
        return []

    def faults(self):
        return list(self.fault_log)


class Sink:
    stopped = False

    def sink_is_foreground(self):
        return True

    def bring_sink_to_foreground(self):
        return True

    def stop(self):
        self.stopped = True


def _setup(monkeypatch, tmp_path):
    clock = Clock()
    link = Link(clock)
    calls = []
    for mod in (soak, input_layer):
        monkeypatch.setattr(mod, "_clock", clock.clock)
        monkeypatch.setattr(mod, "_sleep", clock.sleep)
    monkeypatch.setattr(soak, "_process_names", lambda: Success(["System"]))
    monkeypatch.setattr(soak, "_new_link", lambda: link)
    monkeypatch.setattr(soak, "_start_sink", lambda run_dir: Sink())

    def reset_board(stamps):
        calls.append(("reset_board",))
        stamps.launch_ns = clock.clock()
        clock.now += 3 * S  # esptool launch -> reset line
        stamps.injected_ns = clock.clock()
        link.drop(stamps.injected_ns + S // 2)
        clock.now += S
        link.events.append("inject")
        return Success(None)

    def bluetooth_off_on(seconds, stamps):
        calls.append(("bluetooth_off_on", seconds))
        stamps.injected_ns = clock.clock()
        link.drop(stamps.injected_ns + S // 2)
        clock.now += int(seconds * S)
        link.events.append("inject")
        return Success(None)

    monkeypatch.setattr(faults, "reset_board", reset_board)
    monkeypatch.setattr(faults, "bluetooth_off_on", bluetooth_off_on)
    return link, calls


def _rows(path):
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh]


def test_v2_step4_fault_soak_runs_through_todays_cli(tmp_path, monkeypatch):
    link, calls = _setup(monkeypatch, tmp_path)
    out = tmp_path / "soak"
    assert soak.main(ARGV + ["--out", str(out)]) == soak.EXIT_OK
    assert len(_rows(out / "commands.jsonl")) == 1000
    assert sorted(calls) == [("bluetooth_off_on", 5.0)] * 5 + [("reset_board",)] * 5  # bluetooth_off_on(5)
    rows = _rows(out / "faults.jsonl")
    assert sorted(r["kind"] for r in rows) == ["bluetooth_off_on"] * 5 + ["reset_board"] * 5
    for r in rows:  # every fault observed and reported within 1 s by the link's LINK_LOST (C5)
        assert r["injected_ns"] is not None and r["error"] == "LINK_LOST"
        assert r["reported_ns"] == r["fault_seen_ns"] and r["reported_ns"] - r["injected_ns"] <= S
        assert (r["launch_ns"] is not None) == (r["kind"] == "reset_board")
    # after each fault: the next command (fails, not an abort), then close() + connect() (PA-rev3a-D2)
    ev = link.events
    for i in (n for n, e in enumerate(ev) if e == "inject"):
        assert ev[i + 1:i + 4] == ["play", "close", "connect"]
    assert ev.count("connect") == 11 and "release_all" not in ev
    assert len(_rows(out / "link_faults.jsonl")) == 10


def test_v2_step4_without_short_is_refused_before_any_link(tmp_path, monkeypatch):
    link, calls = _setup(monkeypatch, tmp_path)
    argv = [a for a in ARGV if a != "--short"] + ["--out", str(tmp_path / "soak")]
    assert soak.main(argv) == soak.EXIT_REFUSED
    assert link.events == [] and calls == [] and not (tmp_path / "soak").exists()
