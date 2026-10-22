"""Tests for the soak run writer and focus policy (src/input/harness/soak.py, plan A Task H2; C5 log format).

No board, no radio, no window: the link, the sink and the clock are fakes. The clock and sleep are the module's
seams (`soak._clock`, `soak._sleep`), monkeypatched as in test_input_layer.py.
"""
import json
import logging
import time
from collections import Counter

import numpy as np
import pytest
from returns.pipeline import is_successful
from returns.result import Failure, Success

from src.input import actions, input_layer
from src.input.harness import listener, soak
from src.input.harness.faults import FaultStamps
from src.input.harness.soak import (COMMAND_FIELDS, FOCUS_LOST, FOCUS_RECHECKS, KS_TYPES, MIN_SAMPLES_PER_TYPE,
                                    RunWriter, SoakConfig, command_row, fault_indexes, fault_row, focus_lost_row,
                                    plan_mix, pong_flags, run_command, run_soak, sample_counts)
from src.input.input_layer import ActionRecord
from src.input.link import InputError, SendReport, SessionInfo
from src.input.protocol import Accepted, Done, Key, Pong, encode_play
from src.input.timing import Sample, TimingModel, TimingType

JSONL = ("commands.jsonl", "pongs.jsonl", "link_faults.jsonl", "faults.jsonl")


# ---- fakes ---------------------------------------------------------------------------------

class FakeClock:
    """perf_counter_ns stand-in; sleep() advances it."""

    def __init__(self, start_ns=1_000_000_000):
        self.now = start_ns
        self.sleeps = []

    def clock(self):
        self.now += 1_000  # every read moves 1 us, so stamps taken in order are strictly increasing
        return self.now

    def sleep(self, s):
        self.sleeps.append(s)
        self.now += int(s * 1e9)


@pytest.fixture
def fake_clock(monkeypatch):
    fc = FakeClock()
    monkeypatch.setattr(soak, "_clock", fc.clock)
    monkeypatch.setattr(soak, "_sleep", fc.sleep)
    return fc


class FakeLink:
    """take_pongs() drains `pongs`; faults() returns a copy of the append-only `fault_log`."""

    def __init__(self):
        self.pongs = []
        self.fault_log = []

    def take_pongs(self):
        out, self.pongs = self.pongs, []
        return out

    def faults(self):
        return list(self.fault_log)


class FakeSink:
    """The sink is in front unless `away`; it comes back on the `back_on`-th bring_sink_to_foreground() call."""

    def __init__(self, away=False, back_on=None, clock=None, events=None):
        self.away = away
        self.back_on = back_on
        self.brings = []  # clock value at each bring call
        self._clock = clock
        self.events = events if events is not None else []

    def sink_is_foreground(self):
        return not self.away

    def bring_sink_to_foreground(self):
        self.brings.append(self._clock.now if self._clock else None)
        self.events.append(("bring",))
        if self.back_on is not None and len(self.brings) >= self.back_on:
            self.away = False
        return self.sink_is_foreground()


def _pong(bonded=True, encrypted=True, authenticated=False, subscribed=True, itvl=12, mtu=247):
    return Pong(seq=1, itvl_units=itvl, latency=0, timeout_units=100, mtu=mtu, bonded=bonded,
                encrypted=encrypted, authenticated=authenticated, subscribed=subscribed, board_us=123_456)


def _record(seq=3, reaction=True, walk_ms=None):
    model = TimingModel(np.random.default_rng(0))
    plan = actions.walk(model, Key.LEFT, walk_ms).unwrap() if walk_ms else actions.tap(model, Key.E)
    sample = Sample(TimingType.REACTION, 230.5, 9, None, None, None) if reaction else None
    report = SendReport(seq=seq, sent_ns=10, written_ns=20, accepted_ns=30, done_ns=40,
                        accepted=Accepted(seq, 1000),
                        done=Done(seq=seq, status=0, fail_step=None, rc=0, t0_us=1000, late_us=(5, 7)))
    return ActionRecord(plan, sample, 12.5 if reaction else 0.0, report)


def _rows(path):
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh]


def _session(run_dir):
    with open(run_dir / "session.json", encoding="utf-8") as fh:
        return json.load(fh)


def _writer(tmp_path, link=None):
    return RunWriter(tmp_path / "run", link if link is not None else FakeLink(), seed=7, git_sha="abc")


# ---- commands.jsonl rows (C5) ----------------------------------------------------------------

def test_success_row_has_c5_fields_from_the_record():
    rec = _record()
    row = command_row(4, "tap", 999, Success(rec))
    assert list(row) == list(COMMAND_FIELDS)
    assert list(COMMAND_FIELDS) == ["seq", "action", "intended_ms", "stimulus_ns", "steps", "samples", "reaction_ms",
                                    "reaction_index", "waited_ms", "sent_ns", "written_ns", "accepted_ns", "done_ns",
                                    "t0_us", "late_us", "status", "error", "bytes"]
    assert row["seq"] == 4 and row["action"] == "tap" and row["stimulus_ns"] == 999
    assert row["intended_ms"] is None
    assert row["steps"] == [[s.delta_100us, int(s.mask)] for s in rec.plan.steps]
    assert row["samples"] == [{"type": str(s.type), "chosen_ms": s.ms, "wire_100us": s.wire_100us,
                               "index": s.index, "step_from": s.step_from, "step_to": s.step_to}
                              for s in rec.plan.samples]
    assert row["samples"][0]["type"] == "hold"
    assert (row["reaction_ms"], row["reaction_index"], row["waited_ms"]) == (230.5, 9, 12.5)
    assert (row["sent_ns"], row["written_ns"], row["accepted_ns"], row["done_ns"]) == (10, 20, 30, 40)
    assert (row["t0_us"], row["late_us"]) == (1000, [5, 7])
    assert row["status"] == "OK"
    assert row["error"] is None  # H3 counts a row as succeeded exactly when error is null
    assert row["bytes"] == encode_play(3, rec.plan.steps).unwrap().hex()
    json.dumps(row)  # serialisable as is


def test_failure_row_nulls_everything_but_seq_action_stimulus_status_error():
    row = command_row(5, "teleport", 777, Failure(InputError.ACK_TIMEOUT))
    assert list(row) == list(COMMAND_FIELDS)
    assert (row["seq"], row["action"], row["stimulus_ns"]) == (5, "teleport", 777)
    assert row["status"] == row["error"] == "ACK_TIMEOUT"
    for k in COMMAND_FIELDS:
        if k not in ("seq", "action", "stimulus_ns", "status", "error"):
            assert row[k] is None, k


def test_reaction_index_is_the_records_reaction_index_and_null_without_one():
    assert command_row(1, "tap", 1, Success(_record(reaction=True)))["reaction_index"] == 9
    no_reaction = command_row(1, "tap", None, Success(_record(reaction=False)))
    assert no_reaction["reaction_index"] is None and no_reaction["reaction_ms"] is None
    assert command_row(1, "tap", 1, Failure(InputError.LINK_LOST))["reaction_index"] is None


def test_intended_ms_on_every_walk_row_and_null_elsewhere():
    ok = command_row(1, "walk", 1, Success(_record(walk_ms=1500.0)), intended_ms=1500.0)
    failed = command_row(2, "walk", 1, Failure(InputError.TOO_LONG), intended_ms=2500.0)
    lost = focus_lost_row(3, "walk", intended_ms=800.0)
    assert (ok["intended_ms"], failed["intended_ms"], lost["intended_ms"]) == (1500.0, 2500.0, 800.0)
    assert command_row(4, "tap", 1, Success(_record()))["intended_ms"] is None
    assert focus_lost_row(5, "jump")["intended_ms"] is None


def test_focus_lost_row_nulls():
    row = focus_lost_row(6, "drop")
    assert list(row) == list(COMMAND_FIELDS)
    assert (row["seq"], row["action"]) == (6, "drop")
    assert row["status"] == row["error"] == FOCUS_LOST == "FOCUS_LOST"
    for k in COMMAND_FIELDS:
        if k not in ("seq", "action", "status", "error"):
            assert row[k] is None, k


# ---- the run writer ---------------------------------------------------------------------------

def test_open_creates_every_file_empty_and_lines_end_in_lf(tmp_path, fake_clock):
    link = FakeLink()
    with _writer(tmp_path, link) as w:
        run = w.run_dir
        for name in JSONL + ("link.log",):
            assert (run / name).exists(), name
        w.write_command(command_row(0, "tap", 1, Success(_record())))
        w.write_fault({"kind": "reset_board", "injected_ns": None})
        link.pongs.append((5, _pong()))
        link.fault_log.append((6, InputError.LINK_LOST))
        w.drain()
    for name in JSONL + ("session.json",):
        data = (run / name).read_bytes()
        assert data and b"\r\n" not in data, name
    assert _rows(run / "faults.jsonl") == [{"kind": "reset_board", "injected_ns": None}]


def test_pongs_reach_pongs_jsonl_once_each_in_order_with_flags_rebuilt(tmp_path, fake_clock):
    link = FakeLink()
    a, b, c = _pong(itvl=12), _pong(itvl=9, authenticated=True), _pong(bonded=False, subscribed=False)
    with _writer(tmp_path, link) as w:
        link.pongs += [(100, a), (200, b)]
        w.drain()
        w.drain()  # nothing new: nothing written twice
        link.pongs.append((300, c))
    rows = _rows(w.run_dir / "pongs.jsonl")  # c drained by close() (exit path)
    assert [r["qpc_ns"] for r in rows] == [100, 200, 300]
    assert list(rows[0]) == ["qpc_ns", "itvl_units", "latency", "timeout_units", "mtu", "flags", "board_us"]
    assert rows[0] == {"qpc_ns": 100, "itvl_units": 12, "latency": 0, "timeout_units": 100, "mtu": 247,
                       "flags": 11, "board_us": 123_456}  # bonded + encrypted + subscribed
    assert [r["flags"] for r in rows] == [11, 15, 2]
    assert pong_flags(_pong(bonded=False, encrypted=False, authenticated=True, subscribed=False)) == 4


def test_link_faults_logged_in_order_including_one_just_before_ctrl_c(tmp_path, fake_clock):
    link = FakeLink()
    sink = FakeSink()
    with pytest.raises(KeyboardInterrupt):
        with _writer(tmp_path, link) as w:
            run_command(w, sink, 0, "tap", lambda s: Success(_record()))
            link.fault_log.append((1_111, InputError.LINK_LOST))  # between two commands
            run_command(w, sink, 1, "tap", lambda s: Success(_record()))

            def third(_s):
                link.fault_log.append((2_222, InputError.ACK_TIMEOUT))  # during the third
                return Failure(InputError.ACK_TIMEOUT)

            run_command(w, sink, 2, "tap", third)
            link.fault_log.append((3_333, InputError.LINK_LOST))
            raise KeyboardInterrupt
    assert _rows(w.run_dir / "link_faults.jsonl") == [{"qpc_ns": 1_111, "error": "LINK_LOST"},
                                                      {"qpc_ns": 2_222, "error": "ACK_TIMEOUT"},
                                                      {"qpc_ns": 3_333, "error": "LINK_LOST"}]


def test_no_fault_gives_an_empty_link_faults_file(tmp_path, fake_clock):
    with _writer(tmp_path) as w:
        run_command(w, FakeSink(), 0, "tap", lambda s: Success(_record()))
    assert (w.run_dir / "link_faults.jsonl").read_bytes() == b""


def test_ctrl_c_still_writes_session_json(tmp_path, fake_clock):
    with pytest.raises(KeyboardInterrupt):
        with _writer(tmp_path) as w:
            run_command(w, FakeSink(), 0, "tap", lambda s: Success(_record()))
            raise KeyboardInterrupt
    s = _session(w.run_dir)
    assert s["end_ns"] is not None and s["end_ns"] > s["start_ns"]
    assert s["focus_pauses"] == []


def test_session_json_fields(tmp_path, fake_clock):
    with RunWriter(tmp_path / "r1", FakeLink(), seed=42, git_sha="deadbeef") as w:
        w.record_connect(SessionInfo("AA:BB", 247, _pong(itvl=12)))
        w.record_connect(SessionInfo("AA:BB", 185, _pong(itvl=24)))  # a reconnect does not overwrite
        w.session["process_check"] = "no MapleStory process"
    s = _session(w.run_dir)
    assert s["run_id"] == "r1" and s["seed"] == 42 and s["git_sha"] == "deadbeef"
    assert s["mtu"] == 247 and s["ci_ms"] == 15.0  # 12 x 1.25 ms (C1 PONG itvl units)
    assert s["pong"] == {"itvl_units": 12, "latency": 0, "timeout_units": 100, "mtu": 247, "flags": 11,
                         "board_us": 123_456}
    assert s["process_check"] == "no MapleStory process"
    assert s["start_ns"] < s["end_ns"] and s["focus_pauses"] == []


def test_git_head_is_a_commit_sha():
    sha = soak.git_head()
    assert sha is not None and len(sha) == 40 and all(c in "0123456789abcdef" for c in sha)


# ---- link.log -----------------------------------------------------------------------------------

def test_link_log_gets_warnings_with_perf_ns_and_not_info(tmp_path):
    logger = logging.getLogger("src.input.link")
    before_handlers = list(logger.handlers)
    with _writer(tmp_path) as w:
        t0 = time.perf_counter_ns()
        logger.warning("undecodable notification: %s", "deadbeef")
        t1 = time.perf_counter_ns()
        logger.info("just info")
    assert logger.handlers == before_handlers  # removed at close
    lines = (w.run_dir / "link.log").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    created, perf_ns, level, *msg = lines[0].split(" ")
    assert float(created) > 0 and "." in created and len(created.split(".")[1]) == 6
    assert t0 <= int(perf_ns) <= t1
    assert level == "WARNING" and " ".join(msg) == "undecodable notification: deadbeef"


def test_link_log_is_overwritten_when_the_run_dir_is_reused(tmp_path):
    # A-H2-R M1: link.log is opened like the .jsonl files and session.json ('w'), so it holds one run only
    logger = logging.getLogger("src.input.link")
    with _writer(tmp_path):
        logger.warning("first run")
    with _writer(tmp_path) as w:
        logger.warning("second run")
    lines = (w.run_dir / "link.log").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1 and lines[0].endswith("WARNING second run")


def test_link_log_handler_removed_on_ctrl_c(tmp_path):
    logger = logging.getLogger("src.input.link")
    before_handlers = list(logger.handlers)
    with pytest.raises(KeyboardInterrupt):
        with _writer(tmp_path):
            raise KeyboardInterrupt
    assert logger.handlers == before_handlers


# ---- focus policy (PA-rev m15) ------------------------------------------------------------------

def test_sink_back_on_third_recheck(tmp_path, fake_clock):
    events = []
    sink = FakeSink(away=True, back_on=3, clock=fake_clock, events=events)
    stimuli = []

    def call(s):
        events.append(("play",))
        stimuli.append(s)
        return Success(_record())

    with _writer(tmp_path) as w:
        out = run_command(w, sink, 0, "tap", call)
    assert not out.focus_lost and out.result is not None
    assert events == [("bring",), ("bring",), ("bring",), ("play",)]  # nothing sent during the pause
    assert fake_clock.sleeps == [1.0, 1.0, 1.0]
    pauses = _session(w.run_dir)["focus_pauses"]
    assert len(pauses) == 1
    start, end = pauses[0]
    assert end - start >= 3 * 1_000_000_000
    assert stimuli[0] > end
    assert _rows(w.run_dir / "commands.jsonl")[0]["stimulus_ns"] == stimuli[0]


def test_sink_never_back_gives_focus_lost(tmp_path, fake_clock):
    events = []
    sink = FakeSink(away=True, clock=fake_clock, events=events)
    with _writer(tmp_path) as w:
        out = run_command(w, sink, 4, "walk", lambda s: pytest.fail("nothing may be sent"), intended_ms=900.0)
        assert out.focus_lost and out.result is None and out.returned_ns is None
    assert len(sink.brings) == FOCUS_RECHECKS == 10
    assert fake_clock.sleeps == [1.0] * 10
    rows = _rows(w.run_dir / "commands.jsonl")
    assert rows == [focus_lost_row(4, "walk", intended_ms=900.0)]
    s = _session(w.run_dir)
    assert s["focus_pauses"] == []  # the failed pause is not a pause that ended with the sink back
    assert s["end_ns"] > sink.brings[-1]


def test_no_pause_gives_empty_focus_pauses_and_no_recheck(tmp_path, fake_clock):
    sink = FakeSink()
    with _writer(tmp_path) as w:
        out = run_command(w, sink, 0, "jump", lambda s: Success(_record()))
    assert out.result is not None and out.returned_ns is not None
    assert sink.brings == [] and fake_clock.sleeps == []
    assert _session(w.run_dir)["focus_pauses"] == []


def test_run_command_logs_the_failure_row_and_its_return_time(tmp_path, fake_clock):
    with _writer(tmp_path) as w:
        out = run_command(w, FakeSink(), 9, "sequence", lambda s: Failure(InputError.LINK_LOST))
    row = _rows(w.run_dir / "commands.jsonl")[0]
    assert row == command_row(9, "sequence", row["stimulus_ns"], Failure(InputError.LINK_LOST))
    assert out.returned_ns > row["stimulus_ns"]
    assert out.row == row


# ==== piece 2b: mix, fault rows, driver, CLI ======================================================

ARROWS = {Key.LEFT, Key.RIGHT, Key.UP, Key.DOWN}
S = 1_000_000_000


# ---- mix ----------------------------------------------------------------------------------------

def test_mix_proportions_and_contents_for_10000_commands():
    mix = plan_mix(123, 10_000)
    share = {a: n / len(mix) for a, n in Counter(c.action for c in mix).items()}
    for action, p in {"tap": .25, "walk": .20, "teleport": .25, "drop": .10, "jump": .05, "sequence": .15}.items():
        assert abs(share[action] - p) < 0.015, (action, share[action])
    for c in mix:
        if c.action == "tap":
            assert len(c.keys) == 1 and c.keys[0] in ARROWS | {Key.E, Key.SPACE, Key.ALT}
        elif c.action == "walk":
            assert len(c.keys) == 1 and c.keys[0] in {Key.LEFT, Key.RIGHT} and 300 <= c.intended_ms <= 3000
        elif c.action == "teleport":
            assert len(c.keys) == 1 and c.keys[0] in ARROWS
        elif c.action == "sequence":
            assert 2 <= len(c.keys) <= 5 and set(c.keys) <= ARROWS | {Key.E, Key.SPACE}  # never Alt
        else:
            assert c.keys == ()
        assert (c.intended_ms is not None) == (c.action == "walk")
    seq_lens = Counter(len(c.keys) for c in mix if c.action == "sequence")
    assert set(seq_lens) == {2, 3, 4, 5}
    assert {k for c in mix if c.action == "tap" for k in c.keys} == ARROWS | {Key.E, Key.SPACE, Key.ALT}


def test_mix_is_seeded():
    assert plan_mix(5, 300) == plan_mix(5, 300)
    assert plan_mix(5, 300) != plan_mix(6, 300)
    assert plan_mix(5, 300)[:100] == plan_mix(5, 100)  # a longer run starts with the same commands


def test_10000_commands_give_1000_chosen_samples_per_ks_type_gap_included():
    mix = plan_mix(1, 10_000)
    counts = sample_counts(mix)
    assert set(KS_TYPES) == {TimingType.HOLD, TimingType.GAP, TimingType.CHORD_OFFSET, TimingType.RELEASE_LAG,
                             TimingType.REACTION, TimingType.WALK}
    assert all(counts[t] >= MIN_SAMPLES_PER_TYPE == 1000 for t in KS_TYPES), counts
    assert counts[TimingType.REACTION] == 10_000
    # The counts match what the builders actually draw (a clipped sequence hold is still a HOLD draw).
    model = TimingModel(np.random.default_rng(0))
    builders = {"tap": lambda c: actions.tap(model, c.keys[0]),
                "walk": lambda c: actions.walk(model, c.keys[0], c.intended_ms).unwrap(),
                "teleport": lambda c: actions.teleport(model, c.keys[0]),
                "drop": lambda c: actions.drop(model), "jump": lambda c: actions.jump(model),
                "sequence": lambda c: actions.sequence(model, c.keys).unwrap()}
    built = Counter()
    for c in mix:
        for s in builders[c.action](c).samples:
            built[TimingType.HOLD if s.type == TimingType.HOLD_CLIPPED else s.type] += 1
    built[TimingType.REACTION] = len(mix)
    assert {t: built[t] for t in KS_TYPES} == {t: counts[t] for t in KS_TYPES}


def test_fault_indexes_are_seeded_distinct_and_never_the_first_command():
    a = fault_indexes(9, 1000, {"reset_board": 5, "bluetooth_off_on": 5})
    assert a == fault_indexes(9, 1000, {"bluetooth_off_on": 5, "reset_board": 5})
    assert len(a) == 10 and 0 not in a and all(0 < i < 1000 for i in a)
    assert Counter(a.values()) == {"reset_board": 5, "bluetooth_off_on": 5}
    assert a != fault_indexes(10, 1000, {"reset_board": 5, "bluetooth_off_on": 5})
    assert fault_indexes(9, 1000, {}) == {}


# ---- faults.jsonl rows (C5) -----------------------------------------------------------------------

FAULT_FIELDS = ["injected_ns", "kind", "reported_ns", "error", "launch_ns", "fault_seen_ns", "failure_ns"]


def test_fault_row_reports_the_earlier_of_fault_seen_and_failure():
    st = FaultStamps(launch_ns=100, injected_ns=1_000)
    link_faults = [(900, InputError.ACK_TIMEOUT), (1_700, InputError.LINK_LOST), (1_800, InputError.LINK_LOST)]
    row = fault_row("reset_board", st, link_faults, (2_400, InputError.NOT_CONNECTED))
    assert list(row) == FAULT_FIELDS
    assert row == {"injected_ns": 1_000, "kind": "reset_board", "reported_ns": 1_700, "error": "LINK_LOST",
                   "launch_ns": 100, "fault_seen_ns": 1_700, "failure_ns": 2_400}
    row = fault_row("bluetooth_off_on", FaultStamps(None, 1_000), [(900, InputError.LINK_LOST)],
                    (1_300, InputError.NOT_CONNECTED))
    assert (row["fault_seen_ns"], row["failure_ns"], row["reported_ns"], row["error"]) == (
        None, 1_300, 1_300, "NOT_CONNECTED")
    row = fault_row("reset_board", FaultStamps(5, 1_000), [], None)
    assert (row["fault_seen_ns"], row["failure_ns"], row["reported_ns"], row["error"]) == (None, None, None, None)


def test_fault_row_with_null_injected_ns_nulls_the_stamps_after_it():
    row = fault_row("reset_board", FaultStamps(launch_ns=50, injected_ns=None), [(70, InputError.LINK_LOST)],
                    (80, InputError.NOT_CONNECTED))
    assert row == {"injected_ns": None, "kind": "reset_board", "reported_ns": None, "error": None,
                   "launch_ns": 50, "fault_seen_ns": None, "failure_ns": None}


# ---- driver fakes ---------------------------------------------------------------------------------

class DriverLink(FakeLink):
    """A BleLink stand-in for the driver: each play() takes 1 s of fake time; `fail_plays` (1-based) fail with
    `fail_error`. More than MAX_PLAYS plays is a runaway loop: the test fails instead of hanging."""

    MAX_PLAYS = 1000

    def __init__(self, clock, fail_plays=(), connect_ok=True, connect_fails_after=None,
                 fail_error=InputError.LINK_LOST):
        super().__init__()
        self.clock = clock
        self.events = []
        self.connected = False
        self.plays = 0
        self.fail_plays = set(fail_plays)
        self.fail_error = fail_error
        self.connect_ok = connect_ok
        self.connects = 0
        self.connect_fails_after = connect_fails_after

    def connect(self, timeout_s=30.0):
        self.events.append("connect")
        self.connects += 1
        if not self.connect_ok or (self.connect_fails_after is not None and self.connects > self.connect_fails_after):
            return Failure("no device named 'Guiness' found")
        self.connected = True
        self.pongs.append((self.clock.clock(), _pong()))
        return Success(SessionInfo("AA:BB", 247, _pong()))

    def close(self):
        self.events.append("close")
        self.connected = False

    def play(self, steps):
        self.plays += 1
        assert self.plays <= self.MAX_PLAYS, "runaway soak loop"
        self.events.append("play")
        self.clock.now += S
        if not self.connected:
            return Failure(InputError.NOT_CONNECTED)
        if self.plays in self.fail_plays:
            return Failure(self.fail_error)
        n = self.clock.clock()
        return Success(SendReport(seq=self.plays, sent_ns=n - 3, written_ns=n - 2, accepted_ns=n - 1, done_ns=n,
                                  accepted=Accepted(self.plays, 0),
                                  done=Done(seq=self.plays, status=0, fail_step=None, rc=0, t0_us=0, late_us=())))

    def release_all(self):
        self.events.append("release_all")
        return Failure(InputError.NOT_CONNECTED)


class DriverSink(FakeSink):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.started_in = None
        self.stopped = False

    def stop(self):
        self.stopped = True


@pytest.fixture
def driver_clock(monkeypatch, fake_clock):
    monkeypatch.setattr(input_layer, "_clock", fake_clock.clock)
    monkeypatch.setattr(input_layer, "_sleep", fake_clock.sleep)
    monkeypatch.setattr(soak, "_process_names", lambda: Success(["System", "python.exe"]))
    return fake_clock


def _cfg(tmp_path, commands=5, **kw):
    kw.setdefault("seed", 11)
    return SoakConfig(commands=commands, out=tmp_path / "run", short=True, **kw)


def _start(sink):
    def start(run_dir):
        sink.started_in = run_dir
        return sink
    return start


def _run(tmp_path, clock, commands=5, link=None, sink=None, **kw):
    link = link if link is not None else DriverLink(clock)
    sink = sink if sink is not None else DriverSink()
    code = run_soak(_cfg(tmp_path, commands, **kw), link, _start(sink))
    return code, link, sink


# ---- driver ---------------------------------------------------------------------------------------

def test_driver_runs_n_commands_and_writes_the_c5_files(tmp_path, driver_clock):
    code, link, sink = _run(tmp_path, driver_clock, commands=6)
    run = tmp_path / "run"
    assert code == 0
    rows = _rows(run / "commands.jsonl")
    assert [r["seq"] for r in rows] == list(range(6))
    assert [r["action"] for r in rows] == [c.action for c in plan_mix(11, 6)]
    assert all(r["status"] == "OK" and r["error"] is None for r in rows)
    walks = [(r, c) for r, c in zip(rows, plan_mix(11, 6)) if c.action == "walk"]
    assert all(r["intended_ms"] == c.intended_ms for r, c in walks)
    assert link.events == ["connect"] + ["play"] * 6 + ["close"]  # no release_all on a normal end
    assert sink.started_in == run and sink.stopped
    s = _session(run)
    assert s["seed"] == 11 and s["mtu"] == 247 and s["process_check"] == "no MapleStory process"
    assert len(_rows(run / "pongs.jsonl")) == 1
    for name in JSONL + ("link.log", "session.json"):
        assert (run / name).exists(), name


def test_session_connected_ns_is_stamped_right_after_the_first_connect(tmp_path, driver_clock, monkeypatch):
    # A-H2-R-D1 → A: H3 starts Delivery's window at the first connect, not at the listener's start
    link, calls = DriverLink(driver_clock), []
    real_connect, returned = link.connect, []
    link.connect = lambda timeout_s=30.0: (r := real_connect(timeout_s), returned.append(driver_clock.now))[0]
    monkeypatch.setitem(soak.INJECTORS, "reset_board", _injector(driver_clock, link, calls))
    code, _, _ = _run(tmp_path, driver_clock, link=link, commands=6, faults={"reset_board": 1})
    assert code == 0 and len(returned) == 2  # the first connect and the reconnect after the fault
    rows = _rows(tmp_path / "run" / "commands.jsonl")
    s = _session(tmp_path / "run")
    assert returned[0] < s["connected_ns"] < rows[0]["stimulus_ns"]  # the first connect; the reconnect keeps it
    assert s["start_ns"] < s["connected_ns"] < s["end_ns"]


def test_session_connected_ns_is_null_without_a_connect(tmp_path, driver_clock):
    code, _, _ = _run(tmp_path, driver_clock, link=DriverLink(driver_clock, connect_ok=False))
    assert code == soak.EXIT_ABORT and _session(tmp_path / "run")["connected_ns"] is None


def test_maplestory_at_start_aborts_before_connect(tmp_path, driver_clock, monkeypatch):
    monkeypatch.setattr(soak, "_process_names", lambda: Success(["explorer.exe", "MapleStory.exe"]))
    code, link, sink = _run(tmp_path, driver_clock)
    assert code != 0
    assert "connect" not in link.events and "play" not in link.events
    assert sink.started_in is None  # the listener (which blocks the bot keys) was never started
    assert "release_all" in link.events
    assert "MapleStory.exe" in _session(tmp_path / "run")["process_check"]


def test_process_list_failure_aborts(tmp_path, driver_clock, monkeypatch):
    monkeypatch.setattr(soak, "_process_names", lambda: Failure("tasklist failed"))
    code, link, _ = _run(tmp_path, driver_clock)
    assert code != 0 and "play" not in link.events


def test_maplestory_found_by_the_60_s_check_aborts_and_calls_release_all(tmp_path, driver_clock, monkeypatch):
    checks = []

    def names():
        checks.append(driver_clock.now)
        return Success(["MapleStory.exe"] if len(checks) >= 2 else ["System"])

    monkeypatch.setattr(soak, "_process_names", names)
    code, link, _ = _run(tmp_path, driver_clock, commands=200)
    assert code != 0
    assert len(checks) == 2 and checks[1] - checks[0] >= 60 * S
    rows = _rows(tmp_path / "run" / "commands.jsonl")
    assert 30 < len(rows) < 200  # ~1.3 s per command: the second check came after about 60 s
    assert link.events[-2:] == ["release_all", "close"]
    assert link.events.count("play") == len(rows)


def test_focus_never_back_aborts_with_release_all_and_no_play(tmp_path, driver_clock):
    sink = DriverSink(clock=driver_clock)  # in front at start (the start wait passes), lost once connected
    link = DriverLink(driver_clock)
    real_connect = link.connect

    def connect(timeout_s=30.0):
        sink.away = True
        return real_connect(timeout_s)

    link.connect = connect
    code, link, _ = _run(tmp_path, driver_clock, link=link, sink=sink)
    assert code != 0
    assert "play" not in link.events and "release_all" in link.events
    assert len(sink.brings) == 1 + FOCUS_RECHECKS  # the start-up bring, then the 10 re-checks
    rows = _rows(tmp_path / "run" / "commands.jsonl")
    assert len(rows) == 1 and rows[0]["status"] == FOCUS_LOST
    s = _session(tmp_path / "run")
    assert s["focus_pauses"] == [] and s["end_ns"] > sink.brings[-1]


def test_ctrl_c_calls_release_all_and_writes_session_json(tmp_path, driver_clock):
    link = DriverLink(driver_clock)
    real_play = link.play

    def play(steps):
        if link.plays == 2:
            raise KeyboardInterrupt
        return real_play(steps)

    link.play = play
    code, _, sink = _run(tmp_path, driver_clock, link=link)
    assert code == 130
    assert link.events[-2:] == ["release_all", "close"] and sink.stopped
    assert _session(tmp_path / "run")["end_ns"] is not None


def test_connect_failure_aborts(tmp_path, driver_clock):
    code, link, _ = _run(tmp_path, driver_clock, link=DriverLink(driver_clock, connect_ok=False))
    assert code != 0 and "play" not in link.events and "release_all" in link.events


@pytest.mark.parametrize("err", [InputError.LINK_LOST, InputError.NOT_CONNECTED])
def test_a_link_loss_outside_a_fault_aborts_the_soak(tmp_path, driver_clock, err):
    # A-H2-R-D2 → A: an unplanned drop ends the run through the normal abort path (it never recovers by itself)
    link = DriverLink(driver_clock, fail_plays={3}, fail_error=err)
    code, _, sink = _run(tmp_path, driver_clock, link=link, commands=10)
    rows = _rows(tmp_path / "run" / "commands.jsonl")
    assert code == soak.EXIT_ABORT
    assert len(rows) == 3 and rows[-1]["error"] == str(err)
    assert link.events[-2:] == ["release_all", "close"] and sink.stopped
    assert _session(tmp_path / "run")["end_ns"] is not None


# ---- start: wait for the operator's click on the sink (orchestrator ruling 18) ---------------------

class ClickSink(DriverSink):
    """Not in front (bring_sink_to_foreground() fails, as for an agent-launched process) until the fake clock
    reaches `click_ns` (the operator's click); `click_ns=None` never clicks."""

    def __init__(self, clock, click_ns=None):
        super().__init__(away=True, clock=clock)
        self.click_ns = click_ns
        self.polls = 0

    def sink_is_foreground(self):
        self.polls += 1
        return self.click_ns is not None and self._clock.now >= self.click_ns


def test_start_waits_for_the_click_then_connects_and_runs(tmp_path, driver_clock, capsys):
    click_ns = driver_clock.now + 7 * S
    sink = ClickSink(driver_clock, click_ns=click_ns)
    link = DriverLink(driver_clock)
    connect_at = []
    real_connect = link.connect
    sleeps_before_connect = []
    link.connect = lambda timeout_s=30.0: (connect_at.append(driver_clock.now),
                                           sleeps_before_connect.extend(driver_clock.sleeps),
                                           real_connect(timeout_s))[2]
    code, _, _ = _run(tmp_path, driver_clock, commands=3, link=link, sink=sink)
    assert code == 0
    assert len(sink.brings) == 1  # one SetForegroundWindow try; the wait only re-checks GetForegroundWindow
    assert connect_at[0] >= click_ns  # nothing goes to the board before the click
    rows = _rows(tmp_path / "run" / "commands.jsonl")
    assert len(rows) == 3 and rows[0]["stimulus_ns"] > click_ns
    assert link.events == ["connect"] + ["play"] * 3 + ["close"]
    # polled every START_POLL_S; seen at the first poll after the click
    assert sleeps_before_connect == [soak.START_POLL_S] * int(7 / soak.START_POLL_S)
    assert _session(tmp_path / "run")["focus_pauses"] == []  # the start wait is not a command's focus pause
    err = capsys.readouterr().err
    assert listener.SINK_TITLE in err and "press no keys" in err and f"{soak.START_WAIT_S:g} s" in err


def test_start_wait_times_out_cleanly_without_connecting(tmp_path, driver_clock):
    sink = ClickSink(driver_clock)  # never clicked
    start_ns = driver_clock.now
    code, link, _ = _run(tmp_path, driver_clock, link=DriverLink(driver_clock), sink=sink)
    assert code == soak.EXIT_ABORT
    assert "connect" not in link.events and "play" not in link.events
    assert link.events == ["release_all", "close"]  # the abort path, as for any other abort
    assert sink.stopped  # the listener (and its key blocking) ends with the run
    assert _rows(tmp_path / "run" / "commands.jsonl") == []
    s = _session(tmp_path / "run")
    assert s["end_ns"] - start_ns >= soak.START_WAIT_S * S
    assert sum(driver_clock.sleeps) <= soak.START_WAIT_S + soak.START_POLL_S  # bounded: no wait past the timeout


def test_start_wait_returns_at_once_when_the_sink_is_already_in_front(tmp_path, driver_clock):
    code, link, sink = _run(tmp_path, driver_clock, commands=2)
    assert code == 0 and len(sink.brings) == 1
    assert driver_clock.sleeps.count(soak.START_POLL_S) == 0


def test_maplestory_during_the_start_wait_aborts_before_connect(tmp_path, driver_clock, monkeypatch):
    checks = []

    def names():
        checks.append(driver_clock.now)
        return Success(["MapleStory.exe"] if len(checks) >= 2 else ["System"])

    monkeypatch.setattr(soak, "_process_names", names)
    sink = ClickSink(driver_clock, click_ns=driver_clock.now + 100 * S)
    code, link, _ = _run(tmp_path, driver_clock, sink=sink)
    assert code == soak.EXIT_ABORT
    assert len(checks) == 2 and 60 * S <= checks[1] - checks[0] < 61 * S  # the 60 s check runs during the wait
    assert "connect" not in link.events and "release_all" in link.events
    assert "MapleStory.exe" in _session(tmp_path / "run")["process_check"]


# ---- --min-hours: H3's connected span (K3) --------------------------------------------------------

def _span(rows):
    rows = [r for r in rows if r["status"] != FOCUS_LOST]
    dones = [r["done_ns"] for r in rows if r["done_ns"] is not None]
    return dones[-1] - rows[0]["stimulus_ns"] if dones else 0


def test_min_hours_keeps_issuing_after_n_until_the_span_reaches_h(tmp_path, driver_clock):
    h = 10 * S / 3600e9  # 10 s of connected span
    code, _, _ = _run(tmp_path, driver_clock, commands=3, min_hours=h)
    rows = _rows(tmp_path / "run" / "commands.jsonl")
    assert code == 0 and len(rows) > 3
    assert _span(rows) >= 10 * S > _span(rows[:-1])  # stopped at the first command that reached H


def test_min_hours_reached_first_runs_on_to_n(tmp_path, driver_clock):
    code, _, _ = _run(tmp_path, driver_clock, commands=8, min_hours=1e-9)
    assert code == 0 and len(_rows(tmp_path / "run" / "commands.jsonl")) == 8


def test_min_hours_uses_the_span_to_the_last_done_not_the_wall_clock(tmp_path, driver_clock):
    link = DriverLink(driver_clock, fail_plays=range(3, 11),  # plays 3..10 fail: null done_ns
                      fail_error=InputError.REJECTED)  # not a link loss, which would abort (A-H2-R-D2)
    code, _, _ = _run(tmp_path, driver_clock, link=link, commands=2, min_hours=5 * S / 3600e9)
    rows = _rows(tmp_path / "run" / "commands.jsonl")
    assert code == 0 and len(rows) == 11
    assert all(r["done_ns"] is None for r in rows[2:10]) and rows[10]["done_ns"] is not None
    first = rows[0]["stimulus_ns"]
    assert rows[9]["stimulus_ns"] - first >= 5 * S  # the wall clock passed H during the failures...
    assert _span(rows[:10]) < 5 * S <= _span(rows)  # ...but the connected span reached it only at the end


@pytest.mark.parametrize("min_hours", [0.0, 10 * S / 3600e9])
def test_a_run_in_which_no_command_succeeds_ends_after_n_commands(tmp_path, driver_clock, min_hours):
    # A-H2-R I1: no Success means no done_ns, so there is no span to test; the run ends at N (it already failed)
    link = DriverLink(driver_clock, fail_plays=range(1, DriverLink.MAX_PLAYS + 1),
                      fail_error=InputError.REJECTED)
    code, _, _ = _run(tmp_path, driver_clock, link=link, commands=3, min_hours=min_hours)
    rows = _rows(tmp_path / "run" / "commands.jsonl")
    assert code == 0 and len(rows) == 3
    assert all(r["error"] == "REJECTED" and r["done_ns"] is None for r in rows)


# ---- faults during the run ------------------------------------------------------------------------

def _injector(clock, link, calls, seen_after_s=None, set_stamp=True):
    def inject(stamps):
        calls.append(link.plays)
        stamps.launch_ns = clock.clock()
        clock.now += S // 2
        if set_stamp:
            stamps.injected_ns = clock.clock()
            link.connected = False
            if seen_after_s is not None:
                link.fault_log.append((stamps.injected_ns + int(seen_after_s * S), InputError.LINK_LOST))
        clock.now += S
        return Success(None) if set_stamp else Failure("found 0 ports")
    return inject


def test_fault_seen_before_the_command_failure_is_reported(tmp_path, driver_clock, monkeypatch):
    link, calls = DriverLink(driver_clock), []
    monkeypatch.setitem(soak.INJECTORS, "reset_board", _injector(driver_clock, link, calls, seen_after_s=0.7))
    code, _, _ = _run(tmp_path, driver_clock, link=link, commands=20, faults={"reset_board": 1})
    assert code == 0 and len(calls) == 1
    [row] = _rows(tmp_path / "run" / "faults.jsonl")
    assert list(row) == FAULT_FIELDS and row["kind"] == "reset_board"
    assert row["fault_seen_ns"] - row["injected_ns"] == int(0.7 * S)
    assert row["failure_ns"] - row["injected_ns"] >= int(1.4 * S)  # 1 s of injector + 1 s play, after the stamp
    assert row["reported_ns"] == row["fault_seen_ns"] and row["error"] == "LINK_LOST"
    assert row["launch_ns"] < row["injected_ns"]
    # one command after the fault (it fails), then close() + connect(), then the run goes on
    i = link.events.index("close")
    assert link.events[i - 1:i + 2] == ["play", "close", "connect"]
    assert link.events.count("play") == 20 and link.events[-1] == "close"
    rows = _rows(tmp_path / "run" / "commands.jsonl")
    assert len(rows) == 20 and sum(r["error"] == "NOT_CONNECTED" for r in rows) == 1
    assert rows[calls[0]]["error"] == "NOT_CONNECTED"  # the command right after the injection


def test_without_a_faults_entry_reported_is_the_command_failure(tmp_path, driver_clock, monkeypatch):
    link, calls = DriverLink(driver_clock), []
    monkeypatch.setitem(soak.INJECTORS, "bluetooth_off_on", _injector(driver_clock, link, calls))
    code, _, _ = _run(tmp_path, driver_clock, link=link, commands=10, faults={"bluetooth_off_on": 1})
    [row] = _rows(tmp_path / "run" / "faults.jsonl")
    assert code == 0 and row["kind"] == "bluetooth_off_on"
    assert row["fault_seen_ns"] is None and row["reported_ns"] == row["failure_ns"] is not None
    assert row["error"] == "NOT_CONNECTED"


def test_injector_failure_before_the_fault_writes_a_null_entry(tmp_path, driver_clock, monkeypatch):
    link, calls = DriverLink(driver_clock), []
    monkeypatch.setitem(soak.INJECTORS, "reset_board", _injector(driver_clock, link, calls, set_stamp=False))
    code, _, _ = _run(tmp_path, driver_clock, link=link, commands=10, faults={"reset_board": 1})
    [row] = _rows(tmp_path / "run" / "faults.jsonl")
    assert code == 0
    assert row["kind"] == "reset_board" and row["launch_ns"] is not None
    assert all(row[k] is None for k in ("injected_ns", "fault_seen_ns", "failure_ns", "reported_ns", "error"))


def test_reconnect_failure_after_a_fault_aborts(tmp_path, driver_clock, monkeypatch):
    link, calls = DriverLink(driver_clock, connect_fails_after=1), []
    monkeypatch.setitem(soak.INJECTORS, "reset_board", _injector(driver_clock, link, calls, seen_after_s=0.2))
    code, _, _ = _run(tmp_path, driver_clock, link=link, commands=10, faults={"reset_board": 1})
    assert code != 0
    assert len(_rows(tmp_path / "run" / "faults.jsonl")) == 1
    assert link.events[-3:] == ["connect", "release_all", "close"]


# ---- CLI --------------------------------------------------------------------------------------------

@pytest.fixture
def cli(monkeypatch, driver_clock):
    made = []

    def new_link():
        made.append(DriverLink(driver_clock))
        return made[-1]

    monkeypatch.setattr(soak, "_new_link", new_link)
    monkeypatch.setattr(soak, "_start_sink", lambda run_dir: DriverSink())
    return made


def test_cli_refuses_too_few_commands_without_short_before_any_key(tmp_path, cli):
    assert soak.main(["--commands", "200", "--seed", "1", "--out", str(tmp_path / "r")]) != 0
    assert cli == [] and not (tmp_path / "r").exists()  # no link made, nothing sent, no run dir


def test_cli_short_runs_200_commands(tmp_path, cli):
    assert soak.main(["--commands", "200", "--short", "--seed", "1", "--out", str(tmp_path / "r")]) == 0
    assert len(_rows(tmp_path / "r" / "commands.jsonl")) == 200
    assert cli[0].events.count("play") == 200


def test_cli_accepts_10000_without_short():
    assert soak.validate(soak.config_from_args(soak.parse_args(["--commands", "10000", "--seed", "1"]))) is None


def test_cli_out_writes_the_c5_files_and_one_seed_gives_one_action_sequence(tmp_path, cli):
    for name in ("a", "b"):
        assert soak.main(["--commands", "40", "--short", "--seed", "77", "--out", str(tmp_path / name)]) == 0
    for name in JSONL + ("link.log", "session.json"):
        assert (tmp_path / "a" / name).exists(), name
    actions_a = [r["action"] for r in _rows(tmp_path / "a" / "commands.jsonl")]
    actions_b = [r["action"] for r in _rows(tmp_path / "b" / "commands.jsonl")]
    assert actions_a == actions_b == [c.action for c in plan_mix(77, 40)]
    assert _session(tmp_path / "a")["seed"] == 77


def test_cli_faults_same_indexes_on_two_runs_close_then_connect_one_entry_each(tmp_path, cli, monkeypatch,
                                                                                driver_clock):
    at = []

    def fake(kind):
        def inject(stamps):
            at.append((kind, cli[-1].plays))
            stamps.injected_ns = driver_clock.clock()
            return Success(None)
        return inject

    monkeypatch.setitem(soak.INJECTORS, "reset_board", fake("reset_board"))
    monkeypatch.setitem(soak.INJECTORS, "bluetooth_off_on", fake("bluetooth_off_on"))
    for name in ("a", "b"):
        argv = ["--commands", "50", "--short", "--seed", "3", "--faults", "reset_board=1,bluetooth_off_on=1",
                "--out", str(tmp_path / name)]
        assert soak.main(argv) == 0
    assert len(at) == 4 and at[:2] == at[2:]
    expected = fault_indexes(3, 50, {"reset_board": 1, "bluetooth_off_on": 1})
    assert sorted((plays, kind) for kind, plays in at[:2]) == sorted(expected.items())
    for link, name in zip(cli, ("a", "b")):
        assert link.events.count("close") == 3 and link.events.count("connect") == 3  # 2 faults + the end
        assert [e for e in link.events if e in ("close", "connect")][:4] == ["connect", "close", "connect", "close"]
        assert len(_rows(tmp_path / name / "faults.jsonl")) == 2


def test_cli_rejects_unknown_fault_kinds_and_bad_counts():
    for bad in ("reset=1", "reset_board=x", "reset_board", "reset_board=-1"):
        with pytest.raises(SystemExit):
            soak.parse_args(["--commands", "10", "--faults", bad])


def test_cli_refuses_more_faults_than_commands_after_the_first(tmp_path, cli):
    argv = ["--commands", "3", "--short", "--faults", "reset_board=3", "--out", str(tmp_path / "r")]
    assert soak.main(argv) != 0 and cli == []


def test_process_names_lists_this_process():
    names = soak._process_names()
    assert is_successful(names)
    assert any("python" in n.lower() for n in names.unwrap())
