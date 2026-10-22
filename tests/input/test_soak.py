"""Tests for the soak run writer and focus policy (src/input/harness/soak.py, plan A Task H2; C5 log format).

No board, no radio, no window: the link, the sink and the clock are fakes. The clock and sleep are the module's
seams (`soak._clock`, `soak._sleep`), monkeypatched as in test_input_layer.py.
"""
import json
import logging
import time

import numpy as np
import pytest
from returns.result import Failure, Success

from src.input import actions
from src.input.harness import soak
from src.input.harness.soak import (COMMAND_FIELDS, FOCUS_LOST, FOCUS_RECHECKS, RunWriter, command_row,
                                    focus_lost_row, pong_flags, run_command)
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
