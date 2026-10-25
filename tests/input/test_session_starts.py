"""Tests for the session-start runner (src/input/harness/session_starts.py, plan A Task V2 step 2b; C5
`session_starts.jsonl`).

No board, no radio, no window: the link, the injectors, the listener (sink + keys.jsonl) and the clock are fakes.
The fake link plays the board's part: a successful tap writes the E down/up the hook would log into keys.jsonl.
"""
import json

import pytest
from returns.result import Failure, Success

from src.input.harness import faults, listener, session_starts, soak
from src.input.harness.session_starts import (BT_OFF_SECONDS, PLAN, STARTS_FIELDS, STARTS_FILE, TAP_NOT_SEEN,
                                              conditions, run_starts, tap_seen)
from src.input.link import InputError, SendReport, SessionInfo
from src.input.protocol import Accepted, Done, Key

from input_fakes import FakeClock, pong  # tests/input/input_fakes.py

S = 1_000_000_000
VK_E = listener.VK_BY_KEY[Key.E]


# ---- fakes ---------------------------------------------------------------------------------

@pytest.fixture
def clock(monkeypatch):
    fc = FakeClock()
    for mod in (session_starts, soak):
        monkeypatch.setattr(mod, "_clock", fc.clock)
        monkeypatch.setattr(mod, "_sleep", fc.sleep)
    monkeypatch.setattr(soak, "_process_names", lambda: Success(["System", "python.exe"]))
    return fc


class FakeLink:
    """BleLink stand-in. connect() takes 2 s of fake time. A play whose attempt is in `unseen` reaches no listener;
    otherwise it logs E down/up into keys.jsonl, as the hook would."""

    def __init__(self, clock, keys_path, connect_fail=(), ping_fail=(), play_fail=(), unseen=()):
        self.clock = clock
        self.keys_path = keys_path
        self.events = []
        self.attempt = 0  # set by the test's injector/close hook: the start in progress (1-based)
        self.connect_fail, self.ping_fail, self.play_fail, self.unseen = (set(connect_fail), set(ping_fail),
                                                                          set(play_fail), set(unseen))

    def close(self):
        self.attempt += 1 if not self.events or self.events[-1] != "inject" else 0
        self.events.append("close")

    def connect(self, timeout_s=30.0):
        self.events.append("connect")
        self.clock.now += 2 * S
        if self.attempt in self.connect_fail:
            return Failure("device 'Guiness' not found in scan")
        return Success(SessionInfo("AA:BB", 247, pong()))

    def ping(self):
        self.events.append("ping")
        if self.attempt in self.ping_fail:
            return Failure(InputError.ACK_TIMEOUT)
        return Success(pong())

    def play(self, steps):
        self.events.append("play")
        if self.attempt in self.play_fail:
            return Failure(InputError.REJECTED)
        n = self.clock.clock()
        if self.attempt not in self.unseen:
            with open(self.keys_path, "a", encoding="utf-8", newline="\n") as fh:
                for down in (True, False):
                    fh.write(json.dumps({"qpc_ns": self.clock.clock(), "vk": VK_E, "down": down, "injected": False,
                                         "tick_ms": 0, "repeat": False}) + "\n")
        return Success(SendReport(seq=1, sent_ns=n, written_ns=n, accepted_ns=n, done_ns=n,
                                  accepted=Accepted(1, 0),
                                  done=Done(seq=1, status=0, fail_step=None, rc=0, t0_us=0, late_us=())))

    def release_all(self):
        self.events.append("release_all")
        return Failure(InputError.NOT_CONNECTED)

    def take_pongs(self):
        return []

    def faults(self):
        return []


class FakeSink:
    def __init__(self, away=False):
        self.away = away
        self.stopped = False
        self.started_in = None

    def sink_is_foreground(self):
        return not self.away

    def bring_sink_to_foreground(self):
        return not self.away

    def stop(self):
        self.stopped = True


def _injectors(monkeypatch, link, results=None):
    """Fake injectors: each call appends ('inject', kind) to link.events, moves link.attempt on, and returns
    results[kind] (a list consumed in order) or Success."""
    calls = []

    def make(kind):
        def inject(stamps):
            link.attempt += 1
            link.events.append("inject")
            calls.append(kind)
            seq = (results or {}).get(kind)
            return seq.pop(0) if seq else Success(None)
        return inject

    monkeypatch.setattr(session_starts, "INJECTORS", {k: make(k) for k in ("reset_board", "bluetooth_off_on")})
    return calls


def _run(tmp_path, clock, monkeypatch, sink=None, results=None, **link_kw):
    run = tmp_path / "starts"
    run.mkdir(exist_ok=True)
    link = FakeLink(clock, run / listener.KEYS_FILE, **link_kw)
    calls = _injectors(monkeypatch, link, results)
    sink = sink if sink is not None else FakeSink()

    def start_sink(run_dir):
        sink.started_in = run_dir
        return sink

    code = run_starts(run, link, start_sink, seed=3)
    return code, link, sink, calls, run


def _rows(run):
    with open(run / STARTS_FILE, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh]


# ---- the plan --------------------------------------------------------------------------------

def test_plan_is_8_plain_6_reset_6_bluetooth_and_bt_off_is_10_s():
    assert conditions() == ["plain"] * 8 + ["reset_board"] * 6 + ["bluetooth_off_on"] * 6
    assert PLAN == (("plain", 8), ("reset_board", 6), ("bluetooth_off_on", 6))
    assert BT_OFF_SECONDS == 10.0


def test_default_injectors_call_reset_board_and_bluetooth_off_on_10(monkeypatch):
    seen = []
    monkeypatch.setattr(faults, "reset_board", lambda stamps: seen.append(("reset", stamps)) or Success(None))
    monkeypatch.setattr(faults, "bluetooth_off_on",
                        lambda secs, stamps: seen.append(("bt", secs)) or Success(None))
    stamps = faults.FaultStamps()
    session_starts.INJECTORS["reset_board"](stamps)
    session_starts.INJECTORS["bluetooth_off_on"](faults.FaultStamps())
    assert seen == [("reset", stamps), ("bt", 10.0)]


# ---- one row per start (C5) ----------------------------------------------------------------------

def test_twenty_starts_write_one_c5_row_each(tmp_path, clock, monkeypatch):
    code, link, sink, calls, run = _run(tmp_path, clock, monkeypatch)
    assert code == soak.EXIT_OK
    rows = _rows(run)
    assert len(rows) == 20
    assert all(tuple(r) == STARTS_FIELDS for r in rows)
    assert [r["attempt"] for r in rows] == list(range(1, 21))
    assert [r["condition"] for r in rows] == conditions()
    assert all(r["result"] == "Success" and r["error"] is None for r in rows)
    assert all(r["seconds"] == pytest.approx(2.0, abs=1e-4) for r in rows)  # connect() took 2 s of fake time
    assert all(r["start_ns"] < r["end_ns"] for r in rows)
    assert all(a["end_ns"] < b["start_ns"] for a, b in zip(rows, rows[1:]))
    assert calls == ["reset_board"] * 6 + ["bluetooth_off_on"] * 6
    assert sink.started_in == run and sink.stopped
    assert (run / STARTS_FILE).read_bytes().count(b"\r") == 0


def test_each_start_is_inject_then_close_connect_ping_tap(tmp_path, clock, monkeypatch):
    _, link, *_ = _run(tmp_path, clock, monkeypatch)
    plain, injected = ["close", "connect", "ping", "play"], ["inject", "close", "connect", "ping", "play"]
    # the runner ends with close() (finally), and release_all only on an abort
    assert link.events == plain * 8 + injected * 12 + ["close"]


# ---- Failures ---------------------------------------------------------------------------------------

def test_injector_failure_is_a_failure_row_and_connect_is_not_called(tmp_path, clock, monkeypatch):
    msg = "expected exactly one 0x303a/0x1001 port, found 0 ports []; all ports listed: ['COM1']"
    code, link, _, calls, run = _run(tmp_path, clock, monkeypatch,
                                     results={"reset_board": [Success(None), Failure(msg)]})
    rows = _rows(run)
    assert code == soak.EXIT_OK and len(rows) == 20  # never skipped, never retried
    assert calls == ["reset_board"] * 6 + ["bluetooth_off_on"] * 6
    bad = rows[9]  # attempt 10: the second reset start
    assert (bad["attempt"], bad["condition"], bad["result"], bad["error"], bad["seconds"]) == (
        10, "reset_board", "Failure", msg, None)
    assert link.events.count("connect") == 19
    # nothing but the injector ran for attempt 10: the next start begins with its own injector
    i = [n for n, e in enumerate(link.events) if e == "inject"][1]
    assert link.events[i:i + 2] == ["inject", "inject"]
    assert all(r["result"] == "Success" for n, r in enumerate(rows) if n != 9)


def test_reset_board_repeats_the_port_lookup_on_every_start(tmp_path, clock, monkeypatch):
    """The real faults.reset_board (subprocess faked): each reset start lists the boards again; zero matching
    ports gives a Failure row with the lookup's message and no esptool run, and connect() is not called."""
    run = tmp_path / "starts"
    run.mkdir()
    link = FakeLink(clock, run / listener.KEYS_FILE)
    lookups, esptool = [], []
    board_list = json.dumps({"detected_ports": [{"port": {"address": "COM3", "properties": {}}}]})
    monkeypatch.setattr(faults, "_board_list_json", lambda: lookups.append(1) or Success(board_list))
    monkeypatch.setattr(faults, "_stream", lambda *a, **k: esptool.append(a) or Failure("should not run"))
    monkeypatch.setattr(session_starts, "INJECTORS", {
        "reset_board": session_starts.INJECTORS["reset_board"],
        "bluetooth_off_on": lambda stamps: Success(None)})
    code = run_starts(run, link, lambda d: FakeSink(), seed=3, plan=(("reset_board", 3),))
    rows = _rows(run)
    assert code == soak.EXIT_OK
    assert len(lookups) == 3 and esptool == []
    assert all(r["result"] == "Failure" and "expected exactly one 0x303a/0x1001 port, found 0" in r["error"]
               and r["seconds"] is None for r in rows)
    assert "connect" not in link.events


def test_a_reused_out_starts_keys_jsonl_fresh(tmp_path, clock, monkeypatch):
    # A-V2prep-R m2: the listener appends to keys.jsonl; stale E lines from an earlier run (after a reboot their
    # qpc_ns can be larger than this run's) must not make an unseen tap "seen"
    run = tmp_path / "starts"
    run.mkdir()
    _keys(run / listener.KEYS_FILE, [(10**18, VK_E, True, False), (10**18 + 1, VK_E, False, False)])
    code, _, _, _, run = _run(tmp_path, clock, monkeypatch, unseen=range(1, 21))
    assert code == 0
    assert [r["error"] for r in _rows(run)] == ["TAP_NOT_SEEN"] * 20
    assert (run / listener.KEYS_FILE).read_text(encoding="utf-8") == ""


def test_unseen_tap_gives_tap_not_seen(tmp_path, clock, monkeypatch):
    _, _, _, _, run = _run(tmp_path, clock, monkeypatch, unseen={4})
    rows = _rows(run)
    assert (rows[3]["result"], rows[3]["error"]) == ("Failure", TAP_NOT_SEEN)
    assert rows[3]["seconds"] == pytest.approx(2.0, abs=1e-4)
    assert rows[3]["end_ns"] - rows[3]["start_ns"] >= session_starts.TAP_SEEN_TIMEOUT_S * S  # it waited
    assert sum(r["result"] == "Failure" for r in rows) == 1


def test_connect_ping_and_tap_failures_give_their_error_and_stop_the_start(tmp_path, clock, monkeypatch):
    _, link, _, _, run = _run(tmp_path, clock, monkeypatch, connect_fail={2}, ping_fail={3}, play_fail={5})
    rows = _rows(run)
    assert rows[1]["error"] == "device 'Guiness' not found in scan" and rows[1]["seconds"] is not None
    assert rows[2]["error"] == "ACK_TIMEOUT"
    assert rows[4]["error"] == "REJECTED"
    assert [r["result"] for r in rows[:5]] == ["Success", "Failure", "Failure", "Success", "Failure"]
    # attempt 2: connect failed, so no ping and no tap
    assert link.events[4:8] == ["close", "connect", "close", "connect"]


# ---- tap seen (keys.jsonl) ----------------------------------------------------------------------------

def _keys(path, rows):
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        for qpc, vk, down, repeat in rows:
            fh.write(json.dumps({"qpc_ns": qpc, "vk": vk, "down": down, "injected": False, "tick_ms": 0,
                                 "repeat": repeat}) + "\n")


def test_tap_seen_needs_down_then_up_of_e_after_the_tap(tmp_path):
    p = tmp_path / "keys.jsonl"
    _keys(p, [(10, VK_E, True, False), (20, VK_E, False, False)])  # before the tap: stale
    assert not tap_seen(p, VK_E, 100)
    _keys(p, [(110, VK_E, True, False)])  # down only
    assert not tap_seen(p, VK_E, 100)
    _keys(p, [(110, VK_E, False, False), (120, VK_E, True, False)])  # up before down
    assert not tap_seen(p, VK_E, 100)
    _keys(p, [(110, VK_E + 1, True, False), (120, VK_E + 1, False, False)])  # another key
    assert not tap_seen(p, VK_E, 100)
    _keys(p, [(110, VK_E, True, False), (115, VK_E, True, True), (120, VK_E, False, False)])
    assert tap_seen(p, VK_E, 100)


def test_tap_seen_does_not_count_an_os_repeat_as_the_down(tmp_path):
    # A-V2prep-R m1: the tap's real down is stale (before the tap); only an OS repeat of E falls after it
    p = tmp_path / "keys.jsonl"
    _keys(p, [(10, VK_E, True, False), (115, VK_E, True, True), (120, VK_E, False, False)])
    assert not tap_seen(p, VK_E, 100)


def test_tap_seen_ignores_a_partly_written_line_and_a_missing_file(tmp_path):
    p = tmp_path / "keys.jsonl"
    assert not tap_seen(p, VK_E, 0)
    _keys(p, [(110, VK_E, True, False), (120, VK_E, False, False)])
    with open(p, "a", encoding="utf-8") as fh:
        fh.write('{"qpc_ns": 130, "vk"')
    assert tap_seen(p, VK_E, 100)


# ---- safety: MapleStory check, focus, abort path ------------------------------------------------------

def test_maplestory_at_start_aborts_before_the_listener_any_reset_or_key(tmp_path, clock, monkeypatch):
    monkeypatch.setattr(soak, "_process_names", lambda: Success(["MapleStory.exe"]))
    code, link, sink, calls, run = _run(tmp_path, clock, monkeypatch)
    assert code == soak.EXIT_ABORT
    assert sink.started_in is None and calls == []
    assert "connect" not in link.events and "play" not in link.events
    assert _rows(run) == []


def test_maplestory_before_a_start_aborts_before_its_reset_and_releases(tmp_path, clock, monkeypatch):
    checks = []

    def names():
        checks.append(1)
        return Success(["MapleStory.exe"] if len(checks) == 10 else ["System"])  # 1 at start + 1 per start

    monkeypatch.setattr(soak, "_process_names", names)
    code, link, sink, calls, run = _run(tmp_path, clock, monkeypatch)
    assert code == soak.EXIT_ABORT
    assert len(_rows(run)) == 8 and calls == []  # the check before attempt 9 (the first reset) found it
    assert link.events[-2:] == ["release_all", "close"] and sink.stopped


def test_focus_never_back_aborts_without_a_tap(tmp_path, clock, monkeypatch):
    real_check = soak.check_focus

    def check_focus(s):  # the sink leaves for good after the start wait
        s.away = True
        return real_check(s)

    monkeypatch.setattr(soak, "check_focus", check_focus)
    code, link, _, _, run = _run(tmp_path, clock, monkeypatch)
    assert code == soak.EXIT_ABORT
    assert "play" not in link.events and "release_all" in link.events
    assert _rows(run) == []


def test_listener_start_failure_aborts_before_connect(tmp_path, clock, monkeypatch):
    run = tmp_path / "starts"
    link = FakeLink(clock, run / listener.KEYS_FILE)
    _injectors(monkeypatch, link)

    def start_sink(run_dir):
        raise OSError("SetWindowsHookExW failed")

    assert run_starts(run, link, start_sink, seed=3) == soak.EXIT_ABORT
    assert "connect" not in link.events


def test_ctrl_c_releases_closes_and_keeps_the_rows_written(tmp_path, clock, monkeypatch):
    run = tmp_path / "starts"
    run.mkdir()
    link = FakeLink(clock, run / listener.KEYS_FILE)
    _injectors(monkeypatch, link)
    real_ping = link.ping

    def ping():
        if link.attempt == 3:
            raise KeyboardInterrupt
        return real_ping()

    link.ping = ping
    sink = FakeSink()
    assert run_starts(run, link, lambda d: sink, seed=3) == soak.EXIT_INTERRUPTED
    assert len(_rows(run)) == 2
    assert link.events[-2:] == ["release_all", "close"] and sink.stopped


# ---- CLI ---------------------------------------------------------------------------------------------

def test_main_runs_into_out_with_the_cli_seams(tmp_path, clock, monkeypatch):
    out = tmp_path / "cli"
    out.mkdir()
    link = FakeLink(clock, out / listener.KEYS_FILE)
    _injectors(monkeypatch, link)
    monkeypatch.setattr(session_starts, "_new_link", lambda: link)
    monkeypatch.setattr(session_starts, "_start_sink", lambda d: FakeSink())
    assert session_starts.main(["--out", str(out), "--seed", "4"]) == soak.EXIT_OK
    assert len(_rows(out)) == 20


def test_default_out_is_under_data_starts():
    args = session_starts.parse_args([])
    assert args.out is None and args.seed is None
    assert session_starts.default_out().parent.as_posix() == "data/starts"
