"""Tests for the V1 measurement probes (src/input/harness/probe.py, plan A Task V1).

No board, no radio, no window, no arduino-cli: the link, the listener/sink, the process list, the board list and
the clock are fakes. `ProbeLink.play()` writes the listener transitions its steps would cause (the board as a
perfect keyboard) into a shared key log that `ProbeSink.stop()` writes as the probe dir's `keys.jsonl`, so the
probe reports run analyze.py / realism.py on real C5 files.
"""
import asyncio
import dataclasses
import io
import json
import logging
import math
import random
import re
import sys
import threading

import pytest
from returns.result import Failure, Success

from src.input import input_layer
from src.input.harness import analyze, probe, soak
from src.input.harness.listener import VK_BY_KEY
from src.input.link import InputError, SendReport, SessionInfo
from src.input.protocol import Accepted, Done, Key, Pong, Step, transitions

MS = 1_000_000
ONE_PORT = json.dumps({"detected_ports": [
    {"port": {"address": "COM4", "properties": {"vid": "0x303A", "pid": "0x1001"}}},
    {"port": {"address": "COM1", "properties": {}}},
]})


# ---- fakes ---------------------------------------------------------------------------------------

class FakeClock:
    """perf_counter_ns stand-in; every read moves 1 us; sleep() advances it."""

    def __init__(self, start_ns=1_000_000_000):
        self.now = start_ns

    def clock(self):
        self.now += 1_000
        return self.now

    def sleep(self, s):
        self.now += int(s * 1e9)


def _pong(itvl=12, board_us=123_456):
    return Pong(seq=1, itvl_units=itvl, latency=0, timeout_units=100, mtu=247, bonded=True, encrypted=True,
                authenticated=False, subscribed=True, board_us=board_us)


class ProbeLink:
    """A BleLink stand-in. play() takes the schedule's duration + 20 ms of fake time and logs the listener
    transitions of its steps (1 ms after the send, at the scheduled offsets) into `keys`, unless the transition's
    running index is in `drop_keys`. `fail_plays` (1-based) fail with `fail_error`.

    Board clock (M2 phase): with `board_offset_ns` set, the board's u32 microsecond clock reads
    (qpc_ns - board_offset_ns) / 1000, every play logs a PONG stamped at its send, DONE carries
    t0_us = board time of the send + `t0_shift_us(play)` and one late_us per step (0). `grid_ns` puts every
    listener transition on the next multiple of it (the connection-event lattice).

    Key log (M5, M8): `tick(qpc_ns)` gives each event's tick_ms; `injected` sets every event's injected bit;
    `repeat=(delay_ns, period_ns)` adds OS auto-repeat downs of a held key from delay_ns after its down, every
    period_ns, before its up."""

    MAX_PLAYS = 5000

    def __init__(self, clock, keys, fail_plays=(), fail_error=InputError.LINK_LOST, drop_keys=(),
                 board_offset_ns=None, t0_shift_us=lambda play: 0, grid_ns=None, itvl=12, tick=lambda qpc: 0,
                 injected=False, repeat=None):
        self.clock = clock
        self.keys = keys
        self.events = []
        self.plays = 0
        self.fail_plays = set(fail_plays)
        self.fail_error = fail_error
        self.drop_keys = set(drop_keys)
        self.n_transitions = 0
        self.pongs = []
        self.connected = False
        self.board_offset_ns = board_offset_ns
        self.t0_shift_us = t0_shift_us
        self.grid_ns = grid_ns
        self.itvl = itvl
        self.tick = tick
        self.injected = injected
        self.repeat = repeat

    def _key(self, qpc, vk, down, repeat=False):
        return {"qpc_ns": qpc, "vk": vk, "down": down, "injected": self.injected, "tick_ms": self.tick(qpc),
                "repeat": repeat}

    def board_us(self, ns):
        return (ns - self.board_offset_ns) // 1_000 % (1 << 32)

    def _pong_at(self, ns):
        return _pong(self.itvl, 123_456 if self.board_offset_ns is None else self.board_us(ns))

    def connect(self, timeout_s=30.0):
        self.events.append("connect")
        self.connected = True
        now = self.clock.clock()
        self.pongs.append((now, self._pong_at(now)))
        return Success(SessionInfo("AA:BB", 247, self._pong_at(now)))

    def close(self):
        self.events.append("close")
        self.connected = False

    def take_pongs(self):
        out, self.pongs = self.pongs, []
        return out

    def faults(self):
        return []

    def play(self, steps):
        self.plays += 1
        assert self.plays <= self.MAX_PLAYS, "runaway probe loop"
        self.events.append("play")
        if self.plays in self.fail_plays:
            self.clock.now += 5 * MS
            return Failure(self.fail_error)
        sent = self.clock.clock()
        t0_us, late_us = 0, ()
        if self.board_offset_ns is not None:
            self.pongs.append((sent, self._pong_at(sent)))
            t0_us = (self.board_us(sent) + self.t0_shift_us(self.plays)) % (1 << 32)
            late_us = (0,) * len(steps)
        rows = []
        for t_100us, key, down in transitions(steps):
            qpc = sent + MS + t_100us * 100_000
            if self.grid_ns:
                qpc = -(-qpc // self.grid_ns) * self.grid_ns
            if self.n_transitions not in self.drop_keys:
                rows.append(self._key(qpc, VK_BY_KEY[key], down))
            self.n_transitions += 1
        if self.repeat is not None:
            delay, period = self.repeat
            for i, r in enumerate(list(rows)):
                up = next((u["qpc_ns"] for u in rows[i + 1:] if u["vk"] == r["vk"] and not u["down"]), None)
                t = r["qpc_ns"] + delay
                while r["down"] and up is not None and t < up:
                    rows.append(self._key(t, r["vk"], True, repeat=True))
                    t += period
        self.keys.extend(rows)
        self.clock.now += sum(s.delta_100us for s in steps) * 100_000 + 20 * MS
        n = self.clock.clock()
        return Success(SendReport(seq=self.plays, sent_ns=sent, written_ns=sent + 1_000, accepted_ns=sent + 2_000,
                                  done_ns=n, accepted=Accepted(self.plays, 0),
                                  done=Done(seq=self.plays, status=0, fail_step=None, rc=0, t0_us=t0_us,
                                            late_us=late_us)))

    def release_all(self):
        self.events.append("release_all")
        return Success(None)


class ProbeSink:
    """H1's Listener stand-in: in front unless `away_from_command` (1-based run_command count) is reached; stop()
    appends the shared key log to <run dir>/keys.jsonl (the real listener opens it with "a", listener.py:254).
    `click` False: the start wait never sees the sink."""

    def __init__(self, run_dir, keys, click=True, away_from_check=None):
        self.run_dir = run_dir
        self.keys = keys
        self.click = click
        self.away_from_check = away_from_check
        self.checks = 0
        self.stopped = False

    def sink_is_foreground(self):
        self.checks += 1
        if self.away_from_check is not None and self.checks >= self.away_from_check:
            return False
        return self.click

    def bring_sink_to_foreground(self):
        return self.sink_is_foreground()

    def stop(self):
        self.stopped = True
        with open(self.run_dir / "keys.jsonl", "a", encoding="utf-8", newline="\n") as fh:
            for row in self.keys:
                fh.write(json.dumps(row) + "\n")


class Rig:
    """One fake link + sink per probe, as probe.py makes them; records every one made."""

    def __init__(self, clock, link_kw=None, sink_kw=None):
        self.clock = clock
        self.link_kw = link_kw or {}
        self.sink_kw = sink_kw or {}
        self.links = []
        self.sinks = []
        self._keys = None

    def new_link(self):
        self._keys = []
        link = ProbeLink(self.clock, self._keys, **self.link_kw)
        self.links.append(link)
        return link

    def start_sink(self, run_dir):
        sink = ProbeSink(run_dir, self._keys, **self.sink_kw)
        self.sinks.append(sink)
        return sink


@pytest.fixture
def clock(monkeypatch):
    fc = FakeClock()
    for mod in (soak, input_layer, probe):
        monkeypatch.setattr(mod, "_clock", fc.clock)
        monkeypatch.setattr(mod, "_sleep", fc.sleep)
    monkeypatch.setattr(soak, "_process_names", lambda: Success(["System", "python.exe"]))
    monkeypatch.setattr(probe, "_board_list_json", lambda cli: Success(ONE_PORT))
    return fc


def _rows(path):
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh]


def _session(run_dir):
    with open(run_dir / "session.json", encoding="utf-8") as fh:
        return json.load(fh)


def _cfg(tmp_path, probes=("delivery",), seed=5):
    return probe.ProbeConfig(probes=tuple(probes), out=tmp_path / "run", seed=seed, cli="arduino-cli")


def _run(tmp_path, clock, probes=("delivery",), rig=None, **kw):
    rig = rig if rig is not None else Rig(clock)
    code = probe.run_probes(_cfg(tmp_path, probes, **kw), rig.new_link, rig.start_sink)
    return code, rig


def _dummy(name, n=2, report=None):
    """A registered-probe stand-in: `n` taps of E, then `report(run_dir)` (default: one line)."""
    def commands(ctx):
        for _ in range(n):
            ctx.issue(soak.Command("tap", (Key.E,)))
    return probe.Probe(name, commands, report or (lambda run_dir: probe.ProbeReport([f"{name} ran"])))


@pytest.fixture
def two_dummies(monkeypatch):
    probes = {"one": _dummy("one"), "two": _dummy("two")}
    monkeypatch.setattr(probe, "PROBES", probes)
    return probes


# ---- step 2: the board port (hard rule 3) --------------------------------------------------------

def test_board_ports_keeps_only_vid_303a_pid_1001_any_case():
    text = json.dumps({"detected_ports": [
        {"port": {"address": "COM4", "properties": {"vid": "0x303a", "pid": "0x1001"}}},
        {"port": {"address": "COM5", "properties": {"vid": "0x303A", "pid": "0x0002"}}},
        {"port": {"address": "COM6", "properties": None}},
        {"port": {"address": "COM7"}},
    ]})
    assert probe.board_ports(text) == ["COM4"]


@pytest.mark.parametrize("ports", [[], ["COM4", "COM9"]])
def test_board_port_is_a_failure_unless_exactly_one(monkeypatch, ports):
    text = json.dumps({"detected_ports": [{"port": {"address": p, "properties": {"vid": "0x303A", "pid": "0x1001"}}}
                                          for p in ports]})
    monkeypatch.setattr(probe, "_board_list_json", lambda cli: Success(text))
    got = probe.board_port("cli")
    assert isinstance(got, Failure) and str(len(ports)) in got.failure() and text in got.failure()


def test_board_port_success_and_cli_failure(monkeypatch):
    monkeypatch.setattr(probe, "_board_list_json", lambda cli: Success(ONE_PORT))
    assert probe.board_port("cli") == Success("COM4")
    monkeypatch.setattr(probe, "_board_list_json", lambda cli: Failure("arduino-cli exited 1"))
    assert probe.board_port("cli") == Failure("arduino-cli exited 1")


def test_board_list_json_runs_the_cli_and_reports_a_missing_exe():
    got = probe._board_list_json("C:/no/such/arduino-cli.exe")
    assert isinstance(got, Failure) and "arduino-cli" in got.failure()


# ---- steps 1/2 and the foreground check: refusals ------------------------------------------------------

def test_maplestory_running_is_blocked_before_any_link_listener_or_dir(tmp_path, clock, monkeypatch):
    monkeypatch.setattr(soak, "_process_names", lambda: Success(["MapleStory.exe"]))
    code, rig = _run(tmp_path, clock)
    assert code == probe.EXIT_BLOCKED
    assert rig.links == [] and rig.sinks == [] and not (tmp_path / "run").exists()


def test_no_board_port_is_blocked_before_any_link_listener_or_dir(tmp_path, clock, monkeypatch):
    monkeypatch.setattr(probe, "_board_list_json", lambda cli: Success(json.dumps({"detected_ports": []})))
    code, rig = _run(tmp_path, clock)
    assert code == probe.EXIT_BLOCKED
    assert rig.links == [] and rig.sinks == [] and not (tmp_path / "run").exists()


def test_sink_never_in_front_is_blocked_with_nothing_sent_and_later_probes_not_run(tmp_path, clock, two_dummies,
                                                                                   capsys):
    rig = Rig(clock, sink_kw={"click": False})
    code, rig = _run(tmp_path, clock, probes=("one", "two"), rig=rig)
    assert code == probe.EXIT_BLOCKED
    link = rig.links[0]
    assert "connect" not in link.events and "play" not in link.events
    assert rig.sinks[0].stopped and len(rig.links) == 1
    assert _session(tmp_path / "run" / "one")["end_ns"] is not None
    assert not (tmp_path / "run" / "two").exists()
    err = capsys.readouterr().err
    assert "BLOCKED" in err and "not run: two" in err


# ---- the runner: per-probe dirs, links, listeners ---------------------------------------------------------

def test_each_probe_gets_its_own_link_listener_and_complete_run_dir(tmp_path, clock, two_dummies, capsys):
    code, rig = _run(tmp_path, clock, probes=("one", "two"))
    assert code == probe.EXIT_OK
    assert len(rig.links) == 2 and len(rig.sinks) == 2
    for name, link, sink in zip(("one", "two"), rig.links, rig.sinks):
        d = tmp_path / "run" / name
        assert sink.run_dir == d and sink.stopped
        assert link.events == ["connect", "play", "play", "close"]  # no release_all on a normal end
        for f in ("commands.jsonl", "pongs.jsonl", "link_faults.jsonl", "faults.jsonl", "link.log", "session.json",
                  "keys.jsonl", "report.txt"):
            assert (d / f).exists(), (name, f)
        rows = _rows(d / "commands.jsonl")
        assert [r["seq"] for r in rows] == [0, 1] and all(r["stimulus_ns"] is not None for r in rows)
        s = _session(d)
        assert s["seed"] == 5 and s["connected_ns"] is not None and s["process_check"] == "no MapleStory process"
        assert (d / "report.txt").read_text(encoding="utf-8") == f"{name} ran\n"
    out = capsys.readouterr().out
    assert "one ran" in out and "two ran" in out


def test_the_report_runs_on_closed_files(tmp_path, clock, monkeypatch):
    seen = {}

    def report(run_dir):
        seen["session"] = _session(run_dir)  # written by RunWriter.close()
        seen["keys"] = _rows(run_dir / "keys.jsonl")  # written by the sink's stop()
        return probe.ProbeReport(["ok"])
    monkeypatch.setattr(probe, "PROBES", {"one": _dummy("one", report=report)})
    code, _ = _run(tmp_path, clock, probes=("one",))
    assert code == probe.EXIT_OK
    assert seen["session"]["end_ns"] is not None and len(seen["keys"]) == 4  # 2 taps: down + up each


def test_probe_streams_depend_on_the_seed_and_the_probe_name_only():
    def draw(seed, name):
        return [g.integers(1 << 30) for g in probe.probe_streams(seed, name)]
    assert draw(5, "two") == draw(5, "two")
    assert draw(5, "two") != draw(6, "two") and draw(5, "two") != draw(5, "one")


def test_exit_codes_blocked_and_stop_are_3_and_4_and_distinct_from_the_soaks():
    # the run sheet and the operator rely on these literals (A-V1-Ra m2)
    assert probe.EXIT_BLOCKED == 3 and probe.EXIT_STOP == 4
    soak_codes = {soak.EXIT_OK, soak.EXIT_ABORT, soak.EXIT_REFUSED, soak.EXIT_INTERRUPTED}
    assert soak_codes == {0, 1, 2, 130} and not soak_codes & {probe.EXIT_BLOCKED, probe.EXIT_STOP}


def test_a_reused_run_dir_starts_each_probes_keys_jsonl_fresh(tmp_path, clock, two_dummies):
    # A-V1-Ra m1: the listener appends to keys.jsonl; after a reboot the old qpc_ns can be larger than this run's,
    # so stale lines would land in the last windows as extras / dropped deliveries
    stale = json.dumps({"qpc_ns": 10**18, "vk": 69, "down": True, "injected": False, "tick_ms": 0, "repeat": False})
    for name in ("one", "two"):
        d = tmp_path / "run" / name
        d.mkdir(parents=True)
        (d / "keys.jsonl").write_text(stale + "\n", encoding="utf-8")
    code, _ = _run(tmp_path, clock, probes=("one", "two"))
    assert code == probe.EXIT_OK
    for name in ("one", "two"):
        text = (tmp_path / "run" / name / "keys.jsonl").read_text(encoding="utf-8")
        assert stale not in text and len(text.splitlines()) == 4  # this run's two taps: down + up each


def test_review_items_print_and_a_stop_ends_the_run(tmp_path, clock, monkeypatch, capsys):
    stop = probe.ProbeReport(["measured"], review=["writes can reorder"], stop="reorder seen")
    monkeypatch.setattr(probe, "PROBES", {"one": _dummy("one", report=lambda d: stop), "two": _dummy("two")})
    code, rig = _run(tmp_path, clock, probes=("one", "two"))
    assert code == probe.EXIT_STOP
    assert len(rig.links) == 1 and not (tmp_path / "run" / "two").exists()
    out, err = capsys.readouterr()
    assert "[review] one: writes can reorder" in out
    assert "reorder seen" in err and "not run: two" in err
    assert "[review] one: writes can reorder" in (tmp_path / "run" / "one" / "report.txt").read_text(encoding="utf-8")


# ---- aborts: FOCUS_LOST, link loss, MapleStory mid-probe, Ctrl-C ------------------------------------------------

def test_focus_lost_stops_like_the_soak_and_names_the_probes_not_run(tmp_path, clock, two_dummies, capsys):
    # check 1: wait_for_click's bring; 2: command 0's focus check; 3: command 1's -> away, never back
    rig = Rig(clock, sink_kw={"away_from_check": 3})
    code, rig = _run(tmp_path, clock, probes=("one", "two"), rig=rig)
    assert code == probe.EXIT_ABORT
    d = tmp_path / "run" / "one"
    rows = _rows(d / "commands.jsonl")
    assert rows[-1]["status"] == "FOCUS_LOST" and rows[-1]["seq"] == 1 and len(rows) == 2
    assert rig.links[0].events == ["connect", "play", "release_all", "close"]
    assert rig.sinks[0].stopped and _session(d)["end_ns"] is not None
    assert len(rig.links) == 1 and not (tmp_path / "run" / "two").exists()
    assert not (d / "report.txt").exists()
    err = capsys.readouterr().err
    assert "[review] FOCUS_LOST in probe one" in err and "not run: two" in err


def test_link_loss_aborts_with_release_all(tmp_path, clock, two_dummies, capsys):
    rig = Rig(clock, link_kw={"fail_plays": {1}, "fail_error": InputError.LINK_LOST})
    code, rig = _run(tmp_path, clock, probes=("one", "two"), rig=rig)
    assert code == probe.EXIT_ABORT
    assert rig.links[0].events == ["connect", "play", "release_all", "close"]
    rows = _rows(tmp_path / "run" / "one" / "commands.jsonl")
    assert rows[-1]["error"] == "LINK_LOST"
    assert "not run: two" in capsys.readouterr().err


def test_a_non_link_failure_is_logged_and_the_probe_goes_on(tmp_path, clock, two_dummies):
    rig = Rig(clock, link_kw={"fail_plays": {1}, "fail_error": InputError.REJECTED})
    code, rig = _run(tmp_path, clock, probes=("one",), rig=rig)
    assert code == probe.EXIT_OK
    rows = _rows(tmp_path / "run" / "one" / "commands.jsonl")
    assert [r["error"] for r in rows] == ["REJECTED", None]


def test_maplestory_appearing_mid_probe_aborts(tmp_path, clock, two_dummies, monkeypatch):
    calls = {"n": 0}

    def names():
        calls["n"] += 1
        return Success(["MapleStory.exe"] if calls["n"] > 2 else ["System"])  # 1 precheck, 2 probe start
    monkeypatch.setattr(soak, "_process_names", names)

    rig = Rig(clock)
    orig_new = rig.new_link

    def new_link():
        link = orig_new()
        base = link.play

        def play(steps):
            clock.now += 61 * 1_000_000_000  # each command past the 60 s re-check period
            return base(steps)
        link.play = play
        return link
    code = probe.run_probes(_cfg(tmp_path, ("one", "two")), new_link, rig.start_sink)
    assert code == probe.EXIT_ABORT
    assert "MapleStory" in _session(tmp_path / "run" / "one")["process_check"]
    assert rig.links[0].events[-2:] == ["release_all", "close"]


def test_ctrl_c_releases_and_closes(tmp_path, clock, monkeypatch):
    def commands(ctx):
        raise KeyboardInterrupt
    monkeypatch.setattr(probe, "PROBES", {"one": probe.Probe("one", commands, lambda d: probe.ProbeReport([]))})
    code, rig = _run(tmp_path, clock, probes=("one",))
    assert code == probe.EXIT_INTERRUPTED
    assert rig.links[0].events == ["connect", "release_all", "close"] and rig.sinks[0].stopped


def test_listener_start_failure_aborts_without_connecting(tmp_path, clock, two_dummies):
    rig = Rig(clock)

    def start_sink(run_dir):
        raise OSError("hook not installed")
    code = probe.run_probes(_cfg(tmp_path, ("one",)), rig.new_link, start_sink)
    assert code == probe.EXIT_ABORT and "connect" not in rig.links[0].events


def test_connect_failure_aborts(tmp_path, clock, two_dummies):
    rig = Rig(clock)
    orig = rig.new_link

    def new_link():
        link = orig()
        link.connect = lambda timeout_s=30.0: Failure("no device named 'Guiness' found")
        return link
    code = probe.run_probes(_cfg(tmp_path, ("one",)), new_link, rig.start_sink)
    assert code == probe.EXIT_ABORT and "play" not in rig.links[0].events


# ---- probe: every command type (delivery) ------------------------------------------------------------------

def test_delivery_issues_every_command_type_twenty_times(tmp_path, clock):
    code, rig = _run(tmp_path, clock)
    assert code == probe.EXIT_OK
    rows = _rows(tmp_path / "run" / "delivery" / "commands.jsonl")
    assert len(rows) == 7 * 20 + 20 + 4 * 20 + 20 + 20
    by = {}
    for r in rows:
        by.setdefault(r["action"], []).append(r)
    assert {a: len(v) for a, v in by.items()} == {"tap": 140, "walk": 20, "teleport": 80, "drop": 20, "sequence": 20}
    assert all(r["intended_ms"] == 1000.0 for r in by["walk"])
    assert all(r["status"] == "OK" for r in rows)
    first_keys = [r["steps"][0][1] for r in by["tap"]]
    assert sorted(set(first_keys)) == sorted(int(k) for k in soak.TAP_KEYS)
    assert all(first_keys.count(int(k)) == 20 for k in soak.TAP_KEYS)
    tele = [r["steps"][0][1] for r in by["teleport"]]  # the direction goes down first (C3 teleport: dir + Alt)
    assert all(tele.count(int(k)) == 20 for k in soak.ARROWS)
    for r in by["sequence"]:
        downs = sum(1 for prev, cur in zip([[0, 0]] + r["steps"], r["steps"]) if cur[1] & ~prev[1])
        assert downs == 4


def test_delivery_report_passes_on_a_perfect_board(tmp_path, clock, capsys):
    code, _ = _run(tmp_path, clock)
    assert code == probe.EXIT_OK
    report = (tmp_path / "run" / "delivery" / "report.txt").read_text(encoding="utf-8")
    assert "Delivery: PASS" in report and "Listener liveness: PASS" in report
    assert "delivery probe: PASS" in report and "[review]" not in report


def test_delivery_report_fails_with_a_review_item_on_a_dropped_key(tmp_path, clock, capsys):
    rig = Rig(clock, link_kw={"drop_keys": {7}})
    code, _ = _run(tmp_path, clock, rig=rig)
    assert code == probe.EXIT_OK  # reported, not a stop (plan V1 step 3)
    report = (tmp_path / "run" / "delivery" / "report.txt").read_text(encoding="utf-8")
    assert "Delivery: FAIL" in report and "dropped 1" in report
    assert "[review] delivery:" in report


def test_delivery_is_seeded(tmp_path, clock):
    _run(tmp_path / "a", clock, seed=9)
    _run(tmp_path / "b", clock, seed=9)
    a = _rows(tmp_path / "a" / "run" / "delivery" / "commands.jsonl")
    b = _rows(tmp_path / "b" / "run" / "delivery" / "commands.jsonl")
    assert [r["steps"] for r in a] == [r["steps"] for r in b]


# ---- CLI ------------------------------------------------------------------------------------------------

def test_cli_defaults_and_probe_selection():
    cfg = probe.config_from_args(probe.parse_args(["--seed", "3"]))
    assert cfg.probes == probe.default_probes() == ("delivery", "m2", "m4", "m5", "m6", "m8", "m7",
                                                        "release_mid_hold", "release_ordering")
    assert cfg.seed == 3
    assert cfg.out.parent == probe.PROBE_ROOT and cfg.cli == probe.CLI_DEFAULT
    cfg = probe.config_from_args(probe.parse_args(["--probes", "delivery", "--run", "r1", "--cli", "x.exe"]))
    assert cfg.probes == ("delivery",) and cfg.out == probe.PROBE_ROOT / "r1" and cfg.cli == "x.exe"


def test_cli_refuses_an_unknown_probe_before_anything_runs(monkeypatch, capsys):
    monkeypatch.setattr(probe, "_new_link", lambda: pytest.fail("no link may be made"))
    assert probe.main(["--probes", "delivery,nope"]) == probe.EXIT_REFUSED
    assert "nope" in capsys.readouterr().err


def test_main_runs_the_selected_probes(tmp_path, clock, monkeypatch):
    rig = Rig(clock)
    monkeypatch.setattr(probe, "_new_link", rig.new_link)
    monkeypatch.setattr(probe, "_start_sink", rig.start_sink)
    monkeypatch.setattr(probe, "PROBES", {"one": _dummy("one"), "two": _dummy("two")})
    assert probe.main(["--probes", "two", "--out", str(tmp_path / "x"), "--seed", "1"]) == probe.EXIT_OK
    assert (tmp_path / "x" / "two" / "report.txt").exists() and not (tmp_path / "x" / "one").exists()


def test_main_prints_reports_verbatim_through_a_cp1252_stdout(tmp_path, clock, monkeypatch):
    # A-V1live-D1: a redirected stdout is cp1252 on Windows and the reports quote analyze's "≥"/"≤" targets, so the
    # first report print crashed (UnicodeEncodeError) without PYTHONIOENCODING=utf-8 in the environment
    rig = Rig(clock)
    monkeypatch.setattr(probe, "_new_link", rig.new_link)
    monkeypatch.setattr(probe, "_start_sink", rig.start_sink)
    monkeypatch.setattr(probe, "PROBES", {"one": _dummy("one", report=lambda run_dir: probe.ProbeReport(
        ["target: ≥10,000 commands, p99 ≤ 100 ms"]))})
    buf = io.BytesIO()
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(buf, encoding="cp1252"))
    assert probe.main(["--probes", "one", "--out", str(tmp_path / "x"), "--seed", "1"]) == probe.EXIT_OK
    sys.stdout.flush()
    assert "target: ≥10,000 commands, p99 ≤ 100 ms" in buf.getvalue().decode("utf-8")


# ---- piece 2: shared helpers --------------------------------------------------------------------------

def test_histogram_bins_counts_and_out_of_range():
    lines = probe.histogram("h", [-1.0, 0.0, 0.5, 1.0, 2.0, 5.0], 0.0, 2.0, 2)
    assert lines[0].startswith("h: n 6") and "below 0: 1, above 2: 1" in lines[0]
    assert lines[1].split()[2:4] == ["1.000)", "2"] and lines[2].split()[2:4] == ["2.000]", "2"]
    assert probe.histogram("h", [], 0.0, 1.0, 4) == ["h: n/a n 0"]


def _p(qpc_ns, board_us, itvl=12):
    return {"qpc_ns": qpc_ns, "board_us": board_us, "itvl_units": itvl}


def test_board_send_ns_takes_the_tightest_pong_across_the_u32_wrap():
    sent = 10_000 * MS
    # board time of `sent` is 2^32 - 100 us; t0 is 300 us later (past the wrap), first step 0, late 50 us
    row = {"sent_ns": sent, "t0_us": 200, "late_us": [50, 0], "steps": [[0, 1], [500, 0]]}
    pongs = [_p(sent, (1 << 32) - 100),                    # on time: send = sent + 350 us
             _p(sent + 2 * MS, (1 << 32) - 100 + 1_000),   # 1 ms delivery delay: would say sent + 1.35 ms
             _p(sent + 6_000 * MS, 5)]                     # outside the 5 s window
    assert probe.board_send_ns(row, pongs) == sent + 350_000
    assert probe.board_send_ns({**row, "late_us": []}, pongs) is None
    assert probe.board_send_ns(row, pongs[2:]) is None


def test_ci_ms_at_uses_the_last_pong_at_or_before():
    pongs = [_p(10, 0, itvl=6), _p(20, 0, itvl=12)]
    assert probe.ci_ms_at(pongs, 5) == 7.5 and probe.ci_ms_at(pongs, 15) == 7.5 and probe.ci_ms_at(pongs, 20) == 15.0
    assert probe.ci_ms_at([], 5) is None


# ---- piece 2: M2 (jitter on / off) ------------------------------------------------------------------------

def _board_rig(clock, **kw):
    # the board's u32 microsecond clock wraps 50 ms after the fake clock starts
    return Rig(clock, link_kw={"board_offset_ns": clock.now - ((1 << 32) - 50_000) * 1_000, **kw})


def _report(tmp_path, name):
    return (tmp_path / "run" / name / "report.txt").read_text(encoding="utf-8")


def test_m2_and_m2_nojitter_send_the_same_100_commands_of_holds_chords_and_gaps(tmp_path, clock):
    code, _ = _run(tmp_path / "a", clock, probes=("m2",))
    assert code == probe.EXIT_OK
    code, _ = _run(tmp_path / "b", clock, probes=("m2_nojitter",))
    assert code == probe.EXIT_OK
    a = _rows(tmp_path / "a" / "run" / "m2" / "commands.jsonl")
    b = _rows(tmp_path / "b" / "run" / "m2_nojitter" / "commands.jsonl")
    # same action and the same keys pressed in the same order (the step masks differ only by the drawn timings)
    shape = [[(r["action"], [vk for vk, down in analyze.expected_transitions(r) if down]) for r in rows]
             for rows in (a, b)]
    assert len(a) == probe.M2_N and shape[0] == shape[1]
    assert {r["action"] for r in a} == {"tap", "teleport", "sequence", "drop"}
    types = {s["type"] for r in a for s in r["samples"]}
    assert {"hold", "chord_offset", "gap"} <= types and "hold_clipped" not in types


def test_m2_phase_locked_to_one_point_is_a_review_item_with_jitter_on(tmp_path, clock):
    code, _ = _run(tmp_path, clock, probes=("m2",), rig=_board_rig(clock))
    assert code == probe.EXIT_OK
    rep = _report(tmp_path, "m2")
    assert "START_JITTER on" in rep and "measured - chosen hold (unpaired 0): n " in rep
    assert "measured - chosen chord_offset (unpaired 0): n 50" in rep
    assert "measured - chosen gap (unpaired 0): n 50" in rep
    # perfect board: key-down 1 ms after the send, CI 15 ms -> every phase is 1/15 of the CI
    assert "board send -> first key-down: n 100 min 1.000 ms p50 1.000 ms" in rep
    assert "mean resultant length R 1.000" in rep
    assert "[review] m2: phase with START_JITTER on is not U(0, CI)" in rep


def test_m2_phase_spread_over_the_ci_passes_and_jitter_off_never_reviews(tmp_path, clock):
    spread = {"t0_shift_us": lambda play: (play * 7_919) % 15_000}
    code, _ = _run(tmp_path / "on", clock, probes=("m2",), rig=_board_rig(clock, **spread))
    assert code == probe.EXIT_OK
    rep = _report(tmp_path / "on", "m2")
    assert "phase vs U(0, CI): KS D" in rep and "[review]" not in rep
    code, _ = _run(tmp_path / "off", clock, probes=("m2_nojitter",), rig=_board_rig(clock))
    rep = _report(tmp_path / "off", "m2_nojitter")
    assert "START_JITTER off" in rep and "R 1.000" in rep and "[review]" not in rep


def test_m2_hold_mod_ci_on_the_connection_event_lattice(tmp_path, clock):
    code, _ = _run(tmp_path, clock, probes=("m2",), rig=_board_rig(clock, grid_ns=15 * MS))
    assert code == probe.EXIT_OK
    head = next(line for line in _report(tmp_path, "m2").splitlines() if line.startswith("measured hold mod CI"))
    assert "max 0.000" in head  # every measured hold is a whole number of 15 ms connection events


def test_m2_phase_is_na_without_board_times(tmp_path, clock):
    code, _ = _run(tmp_path, clock, probes=("m2",))
    assert code == probe.EXIT_OK and "phase: n/a" in _report(tmp_path, "m2")


# ---- piece 2: M4 (fixed chord offsets) ------------------------------------------------------------------

def test_fixed_chord_model_calls_the_base_draw_first_then_cycles_the_offsets():
    import numpy as np
    from src.input.timing import TimingModel
    fixed, base = probe.FixedChordModel(np.random.default_rng(1)), TimingModel(np.random.default_rng(1))
    got = [fixed.chord_offset() for _ in range(8)]
    for _ in range(8):
        base.chord_offset()
    assert got == list(probe.M4_OFFSETS_MS) + [probe.M4_OFFSETS_MS[0]]
    assert fixed.draws == base.draws == 8 and fixed.hold() == base.hold()  # same stream position (C3)


def test_m4_teleports_fifty_per_fixed_offset(tmp_path, clock):
    code, _ = _run(tmp_path, clock, probes=("m4",))
    assert code == probe.EXIT_OK
    rows = _rows(tmp_path / "run" / "m4" / "commands.jsonl")
    offs = [s["chosen_ms"] for r in rows for s in r["samples"] if s["type"] == "chord_offset"]
    assert {r["action"] for r in rows} == {"teleport"} and len(rows) == 350
    assert {x: offs.count(x) for x in set(offs)} == {x: 50 for x in probe.M4_OFFSETS_MS}
    rep = _report(tmp_path, "m4")
    assert "commanded 2 ms: collapsed (< 1 ms) 0/50 (0.0%), unpaired 0" in rep
    assert "model-weighted collapse share: 0.0%" in rep and "Chord order: PASS" in rep and "[review]" not in rep


def test_m4_reviews_against_spec_l15_when_most_model_offsets_collapse(tmp_path, clock):
    code, _ = _run(tmp_path, clock, probes=("m4",), rig=Rig(clock, link_kw={"grid_ns": 100 * MS}))
    assert code == probe.EXIT_OK
    assert "[review] m4: against spec:L15" in _report(tmp_path, "m4")


def test_m4_expected_collapse_weights_by_the_model_mass():
    mass = probe.m4_model_mass()
    assert abs(sum(mass.values()) - 1) < 1e-9 and mass[2.0] == mass[5.0] == 0.0  # the model's offsets are >= 8 ms
    assert probe.m4_expected_collapse({x: 1.0 for x in probe.M4_OFFSETS_MS}) == pytest.approx(1.0)
    only_small = {x: (1.0 if x <= 15 else 0.0) for x in probe.M4_OFFSETS_MS}
    assert probe.m4_expected_collapse(only_small) == pytest.approx(mass[10.0] + mass[15.0])
    assert probe.m4_expected_collapse({2.0: 1.0}) is None


# ---- piece 2: M7 -----------------------------------------------------------------------------------

def test_m7_prints_latency_and_the_m7_split(tmp_path, clock):
    code, _ = _run(tmp_path, clock, probes=("m7",))
    assert code == probe.EXIT_OK
    rows = _rows(tmp_path / "run" / "m7" / "commands.jsonl")
    assert len(rows) == probe.M7_N and {r["action"] for r in rows} == {"tap"}
    rep = _report(tmp_path, "m7")
    assert "Latency: PASS p99 1.000 ms over 100 commands" in rep
    for part in ("write", "board accept", "board to listener", "total"):
        assert f"M7 {part} p50" in rep
    assert "[review]" not in rep


# ---- piece 2: CLI and the run sheet --------------------------------------------------------------------

def test_m2_nojitter_runs_alone(monkeypatch, capsys):
    monkeypatch.setattr(probe, "run_probes", lambda *a: pytest.fail("must refuse before running"))
    assert probe.main(["--probes", "m2,m2_nojitter"]) == probe.EXIT_REFUSED
    assert "m2_nojitter runs alone" in capsys.readouterr().err
    assert "m2_nojitter" not in probe.default_probes()


def test_run_sheet_prints_every_step_and_executes_nothing(monkeypatch, capsys):
    def boom(*a, **k):
        pytest.fail("the run sheet must execute nothing")
    monkeypatch.setattr(probe.subprocess, "run", boom)
    monkeypatch.setattr(probe, "run_probes", boom)
    monkeypatch.setattr(probe, "_new_link", boom)
    monkeypatch.setattr(probe, "_start_sink", boom)
    monkeypatch.setattr(soak, "_process_names", boom)
    assert probe.main(["--run-sheet"]) == probe.EXIT_OK
    out = capsys.readouterr().out
    assert ('--probes delivery,m2,m4,m5,m6,m8,m7,release_mid_hold,release_ordering --run "$RUN" '
            '--seed "$SEED"') in out
    assert ('"$CLI" compile -b "$FQBN" -u -p "$PORT" --output-dir $REPO/firmware/build/probe-nojitter '
            '--build-property "compiler.cpp.extra_flags=-DSTART_JITTER=0" $REPO/firmware/Helix_BLE') in out
    assert '"$CLI" compile -b "$FQBN" -u -p "$PORT" --output-dir $REPO/firmware/build $REPO/firmware/Helix_BLE' in out
    assert "build.extra_flags" not in out
    # order: jitter-off flash, then m2_nojitter, then the HEAD reflash; a MapleStory check and port lookup each flash
    i_off, i_run, i_head = (out.index("probe-nojitter"), out.index("--probes m2_nojitter"),
                            out.index("--output-dir $REPO/firmware/build $REPO"))
    assert i_off < i_run < i_head
    assert out.count('PORT=$("$CLI" board list --format json') == 2 and out.count("Get-Process") == 2


def test_run_sheet_states_its_preconditions(capsys):
    # A-V1-Ra m4 / Rb m2-m4: the printed sheet is what the operator follows
    assert probe.main(["--run-sheet"]) == probe.EXIT_OK
    out = capsys.readouterr().out
    # run from the main tree (it has the .venv) with p1a-v1 merged
    assert "the main tree, which has the .venv, with p1a-v1 merged into its branch" in out
    assert "run from the main tree (REPO) with p1a-v1 merged into its branch" in out
    # the board must already run HEAD's firmware, else steps 2e-2g (MapleStory check, PORT lookup, flash) first
    assert "running HEAD's firmware (else do steps 2e-2g first)" in out
    # step 2 only after step 1 exited 0; otherwise stop and report
    assert "Only after step 1 exited 0; on exit 1, 3 or 4 stop and report" in out
    assert out.index("before step 1") < out.index("1. Probes on HEAD's firmware") < out.index("2. M2 with START_JITTER")


# ---- piece 3: raw plans through the InputLayer -------------------------------------------------------------

def test_raw_plan_refuses_an_invalid_schedule_and_logs_no_samples():
    with pytest.raises(ValueError):
        probe.raw_plan("open", (Step(0, Key.E),))  # the last mask must be 0
    plan = probe.raw_plan("e_burst", probe.M6_STEPS)
    assert len(plan.steps) == 16 and plan.samples == ()


# ---- piece 3: M5 (3 s arrow hold) --------------------------------------------------------------------------

def test_m5_holds_each_arrow_3_s_and_reports_repeats_and_their_spacing(tmp_path, clock):
    rig = Rig(clock, link_kw={"repeat": (500 * MS, 33 * MS)})
    code, _ = _run(tmp_path, clock, probes=("m5",), rig=rig)
    assert code == probe.EXIT_OK
    rows = _rows(tmp_path / "run" / "m5" / "commands.jsonl")
    assert len(rows) == probe.M5_N and {r["action"] for r in rows} == {"arrow_hold"}
    assert all(r["status"] == "OK" and r["reaction_ms"] >= 110 for r in rows)  # analyze's windows apply
    held = [r["steps"][0][1] for r in rows]
    assert all(r["steps"] == [[0, r["steps"][0][1]], [30_000, 0]] for r in rows)
    assert all(held.count(int(k)) == probe.M5_N // 4 for k in soak.ARROWS)
    rep = _report(tmp_path, "m5")
    # repeats at 500 + 33 k ms while 500 + 33 k < 3000: 76 per hold, 75 spacings each
    assert f"repeat downs per hold: n {probe.M5_N} min 76.000 p50 76.000" in rep
    assert "first repeat after the down: n 8 min 500.000 ms p50 500.000 ms" in rep
    assert "repeat spacing: n 600 min 33.000 ms p50 33.000 ms" in rep
    assert "measured hold (down -> up): n 8 min 3000.000 ms" in rep
    assert "injected events: 0 of " in rep and "[review]" not in rep


def test_m5_reports_no_repeat_and_reviews_an_injected_bit(tmp_path, clock):
    code, _ = _run(tmp_path, clock, probes=("m5",), rig=Rig(clock, link_kw={"injected": True}))
    assert code == probe.EXIT_OK
    rep = _report(tmp_path, "m5")
    assert "repeat downs per hold: n 8 min 0.000" in rep and "repeat spacing: n/a n 0" in rep
    assert "[review] m5: injected events: 16 of 16" in rep


# ---- piece 3: M6 (16 zero-delta E steps) -------------------------------------------------------------------

def test_m6_sends_fifty_16_step_zero_delta_e_bursts(tmp_path, clock):
    code, _ = _run(tmp_path, clock, probes=("m6",))
    assert code == probe.EXIT_OK
    rows = _rows(tmp_path / "run" / "m6" / "commands.jsonl")
    assert len(rows) == probe.M6_N and {r["action"] for r in rows} == {"e_burst"}
    assert all(r["steps"] == [[0, int(Key.E)], [0, 0]] * 8 and r["samples"] == [] for r in rows)
    assert all(r["reaction_ms"] >= 110 for r in rows)
    rep = _report(tmp_path, "m6")
    assert "DONE OK 50, NOTIFY_FAILED 0, other failures 0" in rep
    assert "reports seen on DONE OK commands: 800 of 800 expected" in rep
    assert "silent drops (DONE OK, a report missing): 0" in rep and "[review]" not in rep


def test_m6_counts_notify_failed_and_the_input_layer_releases_after_it(tmp_path, clock):
    rig = Rig(clock, link_kw={"fail_plays": {3}, "fail_error": InputError.NOTIFY_FAILED})
    code, rig = _run(tmp_path, clock, probes=("m6",), rig=rig)
    assert code == probe.EXIT_OK  # NOTIFY_FAILED reaches the caller: counted, not a stop
    assert rig.links[0].events[:5] == ["connect", "play", "play", "play", "release_all"]
    rep = _report(tmp_path, "m6")
    assert "DONE OK 49, NOTIFY_FAILED 1, other failures 0" in rep
    assert "reports seen on NOTIFY_FAILED commands: n 1 min 0.000" in rep
    assert "silent drops (DONE OK, a report missing): 0" in rep


def test_m6_silent_drop_stops_probe_py(tmp_path, clock, monkeypatch, capsys):
    monkeypatch.setattr(probe, "PROBES", {"m6": probe.PROBES["m6"], "two": _dummy("two")})
    code, rig = _run(tmp_path, clock, probes=("m6", "two"), rig=Rig(clock, link_kw={"drop_keys": {5}}))
    assert code == probe.EXIT_STOP and len(rig.links) == 1
    rep = _report(tmp_path, "m6")
    assert "silent drops (DONE OK, a report missing): 1 (seq 0)" in rep
    assert "[review] m6: silent drop" in rep
    err = capsys.readouterr().err
    assert "silent drop" in err and "not run: two" in err


def test_m6_a_silent_listener_stops_too(tmp_path, clock):
    rig = Rig(clock, link_kw={"drop_keys": set(range(16 * probe.M6_N))})
    code, _ = _run(tmp_path, clock, probes=("m6",), rig=rig)
    assert code == probe.EXIT_STOP
    assert "unmeasured (listener silent from seq 0): 50" in _report(tmp_path, "m6")


# ---- piece 3: M8 (tick_ms granularity) ---------------------------------------------------------------------

def _ticks(step_ms):
    return lambda qpc: int(math.floor(qpc / MS / step_ms) * step_ms)


def test_m8_confirms_a_tick_granularity_of_10_to_16_ms(tmp_path, clock):
    code, _ = _run(tmp_path, clock, probes=("m8",), rig=Rig(clock, link_kw={"tick": _ticks(15.625)}))
    assert code == probe.EXIT_OK
    rows = _rows(tmp_path / "run" / "m8" / "commands.jsonl")
    assert len(rows) == probe.M8_N and {r["action"] for r in rows} == {"tap"}
    rep = _report(tmp_path, "m8")
    assert "M8 tick_ms granularity: CONFIRMED" in rep and "nonzero tick steps between events" in rep
    assert "[review]" not in rep


def test_m8_reviews_a_granularity_above_the_upper_bound(tmp_path, clock):
    # A-V1-Rb m1: ~20 ms ticks are past 16 ms + the 1 ms tolerance for whole-ms ticks
    code, _ = _run(tmp_path, clock, probes=("m8",), rig=Rig(clock, link_kw={"tick": _ticks(20)}))
    assert code == probe.EXIT_OK
    rep = _report(tmp_path, "m8")
    est = float(re.search(r"M8 tick_ms granularity: NOT CONFIRMED ~([\d.]+) ms", rep).group(1))
    assert 17 < est < 21 and "[review] m8: tick_ms granularity" in rep


def test_m8_reviews_a_granularity_outside_10_to_16_ms(tmp_path, clock):
    code, _ = _run(tmp_path, clock, probes=("m8",), rig=Rig(clock, link_kw={"tick": _ticks(1)}))
    assert code == probe.EXIT_OK
    rep = _report(tmp_path, "m8")
    assert "M8 tick_ms granularity: NOT CONFIRMED" in rep and "[review] m8: tick_ms granularity" in rep


def _lagged_ticks(step_ms, max_lag_ticks, seed=7):
    """tick_ms on a `step_ms` grid that trails each event by a random 0..max_lag_ticks ticks: BLE-HID input live
    (A-M8: tick_ms trails the hook's qpc_ns by ~2 ticks, so tick_ms - qpc_ns spans ~2 ticks, not 1)."""
    rng = random.Random(seed)
    return lambda qpc: int(math.floor((qpc / MS - rng.uniform(0, max_lag_ticks * step_ms)) / step_ms) * step_ms)


def test_m8_confirms_the_granularity_through_up_to_two_ticks_of_arrival_lag(tmp_path, clock):
    # the live V1 try-3 failure: 15.625 ms ticks, p99 - p1 of tick_ms - qpc_ns read ~27 ms (A-M8)
    code, _ = _run(tmp_path, clock, probes=("m8",), rig=Rig(clock, link_kw={"tick": _lagged_ticks(15.625, 2)}))
    assert code == probe.EXIT_OK
    rep = _report(tmp_path, "m8")
    est = float(re.search(r"M8 tick_ms granularity: CONFIRMED ~([\d.]+) ms", rep).group(1))
    assert 15.5 < est < 15.75 and "[review]" not in rep


def test_m8_a_tick_that_never_advances_is_n_a_and_reviewed(tmp_path, clock):
    code, _ = _run(tmp_path, clock, probes=("m8",))  # the rig's default tick_ms is 0 for every event
    assert code == probe.EXIT_OK
    rep = _report(tmp_path, "m8")
    assert "M8 tick_ms granularity: n/a" in rep and "[review] m8: tick_ms granularity: n/a" in rep
    assert "CONFIRMED" not in rep


# ---- piece 4: release-all probes (a second thread) ---------------------------------------------------------

class ReleaseLink(ProbeLink):
    """A BleLink for the release-all probes. play() stamps its send, runs the `_before_write` hook the probe set on
    this instance (P4's m4 seam, as BleLink._play awaits it before the write) and then, as a board holding the keys,
    waits for the second thread's release_all(): the schedule's transitions before the release call are logged;
    the release lifts every key still down at its done_ns + `up_shift_ns` (unless `lift` is False) and the play
    ends Failure(ABORTED). `reorder`: the PLAY reaches the board after the RELEASE_ALL, so its whole schedule plays
    from the release's done_ns + 50 ms and the play ends Success. release_all() is done 4 ms after its call.

    One reason at a time for release_ordering_report (each leaves the others clean): `play_ok` the play ends Success
    with nothing else wrong; `release_fail` release_all() returns that InputError; `late_down_ns` an extra short E
    tap at done_ns + that (past done_ns + CI + 5 ms); `up_shift_ns` 600 ms = a 600 ms E hold; `lift` False = E left
    down. `reorder_aborted`: the realistic reorder, the schedule plays 50 ms after done_ns but the PC marked the
    play ABORTED on the RELEASE_ALL's DONE (P4's abort rules)."""

    def __init__(self, clock, keys, reorder=False, lift=True, up_shift_ns=-1 * MS, play_ok=False, release_fail=None,
                 late_down_ns=None, reorder_aborted=False, **kw):
        super().__init__(clock, keys, **kw)
        self.reorder = reorder
        self.play_ok = play_ok
        self.release_fail = release_fail
        self.late_down_ns = late_down_ns
        self.reorder_aborted = reorder_aborted
        self.lift = lift
        self.up_shift_ns = up_shift_ns
        self.released = threading.Event()
        self.release_call_ns = self.release_done_ns = None
        self.release_threads = []

    async def _before_write(self, seq):
        pass

    def play(self, steps):
        self.plays += 1
        assert self.plays <= self.MAX_PLAYS, "runaway probe loop"
        self.events.append("play")
        self.released.clear()
        sent = self.clock.clock()
        asyncio.run(self._before_write(self.plays))
        assert self.released.wait(10), "no release_all() from the second thread"
        rel, done = self.release_call_ns, self.release_done_ns
        trans = [(t * 100_000, VK_BY_KEY[k], d) for t, k, d in transitions(steps)]
        if self.reorder or self.reorder_aborted:
            rows = [self._key(done + 50 * MS + t, vk, d) for t, vk, d in trans]
        else:
            rows = [self._key(sent + MS + t, vk, d) for t, vk, d in trans if sent + MS + t < rel]
            held = [vk for vk in dict.fromkeys(r["vk"] for r in rows)
                    if [r["down"] for r in rows if r["vk"] == vk][-1]]
            if self.lift:
                rows += [self._key(done + self.up_shift_ns, vk, False) for vk in held]
            if self.late_down_ns is not None:
                e = VK_BY_KEY[Key.E]
                rows += [self._key(done + self.late_down_ns, e, True),
                         self._key(done + self.late_down_ns + 2 * MS, e, False)]
        self.keys.extend(rows)
        self.clock.now = max([self.clock.now] + [r["qpc_ns"] for r in rows]) + 20 * MS
        if not self.reorder and not self.play_ok:
            return Failure(InputError.ABORTED)
        return Success(SendReport(seq=self.plays, sent_ns=sent, written_ns=sent + 1_000, accepted_ns=sent + 2_000,
                                  done_ns=self.clock.clock(), accepted=Accepted(self.plays, 0),
                                  done=Done(seq=self.plays, status=0, fail_step=None, rc=0, t0_us=0, late_us=())))

    def release_all(self):
        self.events.append("release_all")
        self.release_threads.append(threading.current_thread().name)
        self.release_call_ns = self.clock.clock()
        self.release_done_ns = self.release_call_ns + 4 * MS
        self.released.set()
        if self.release_fail is not None:
            return Failure(self.release_fail)
        return Success(SendReport(seq=0, sent_ns=self.release_call_ns, written_ns=self.release_call_ns + 1_000,
                                  accepted_ns=None, done_ns=self.release_done_ns, accepted=None,
                                  done=Done(seq=0, status=0, fail_step=None, rc=0, t0_us=0, late_us=())))


class ReleaseRig(Rig):
    def new_link(self):
        self._keys = []
        link = ReleaseLink(self.clock, self._keys, **self.link_kw)
        self.links.append(link)
        return link


def _releases(tmp_path, name):
    return _rows(tmp_path / "run" / name / probe.RELEASES_FILE)


def test_release_mid_hold_walks_right_and_chords_right_e_with_a_release_from_a_second_thread(tmp_path, clock):
    code, rig = _run(tmp_path, clock, probes=("release_mid_hold",), rig=ReleaseRig(clock))
    assert code == probe.EXIT_OK
    rows = _rows(tmp_path / "run" / "release_mid_hold" / "commands.jsonl")
    n = probe.RELEASE_MID_N
    assert [r["action"] for r in rows] == ["walk"] * n + ["chord_hold"] * n
    assert all(r["intended_ms"] == 3000.0 for r in rows[:n]) and all(r["error"] == "ABORTED" for r in rows)
    rel = _releases(tmp_path, "release_mid_hold")
    assert [r["seq"] for r in rel] == list(range(2 * n)) and all(r["release_status"] == "OK" for r in rel)
    delays = [(r["release_call_ns"] - r["write_ns"]) / MS for r in rel]
    assert all(500 <= d <= 2500.01 for d in delays) and len(set(round(d) for d in delays)) > 10
    # every release_all() ran on the second thread, none on the probe's (no abort path either)
    assert len(rig.links[0].release_threads) == 2 * n and "MainThread" not in rig.links[0].release_threads
    rep = _report(tmp_path, "release_mid_hold")
    assert f"walk: {n} trials, play ABORTED {n}, release_all OK {n}, keys left down 0, no up after the call 0" in rep
    assert f"chord_hold: {n} trials, play ABORTED {n}" in rep
    # release call -> last up of a held key: done (call + 4 ms) - 1 ms = 3 ms (+1 us: the fake clock's read)
    assert "walk release_all() -> all keys up: n 20 min 3.001 ms p50 3.001 ms p99 3.001 ms max 3.001 ms" in rep
    assert "chord_hold release_all() -> all keys up: n 20 min 3.001 ms" in rep
    assert "[review]" not in rep


def test_release_mid_hold_chord_holds_right_and_e_together():
    plan = probe.raw_plan("chord_hold", probe.RELEASE_CHORD_STEPS)
    assert [(t, k, d) for t, k, d in transitions(plan.steps)] == [
        (0, Key.RIGHT, True), (0, Key.E, True), (30_000, Key.E, False), (30_000, Key.RIGHT, False)]


def test_release_mid_hold_reviews_a_key_left_down_but_does_not_stop(tmp_path, clock):
    code, _ = _run(tmp_path, clock, probes=("release_mid_hold",), rig=ReleaseRig(clock, link_kw={"lift": False}))
    assert code == probe.EXIT_OK
    rep = _report(tmp_path, "release_mid_hold")
    assert "keys left down 20, no up after the call 20" in rep
    assert "[review] release_mid_hold: walk: " in rep and "[review] release_mid_hold: chord_hold: " in rep


def test_release_ordering_e_holds_released_by_a_second_thread_within_20_ms(tmp_path, clock):
    code, rig = _run(tmp_path, clock, probes=("release_ordering",), rig=ReleaseRig(clock))
    assert code == probe.EXIT_OK
    rows = _rows(tmp_path / "run" / "release_ordering" / "commands.jsonl")
    n = probe.RELEASE_ORDER_N
    assert len(rows) == n and {r["action"] for r in rows} == {"e_hold"}
    assert all(r["error"] == "ABORTED" and r["stimulus_ns"] is not None for r in rows)
    rel = _releases(tmp_path, "release_ordering")
    delays = [(r["release_call_ns"] - r["write_ns"]) / MS for r in rel]
    assert len(rel) == n and all(0 <= d <= 20.01 for d in delays)
    assert "MainThread" not in rig.links[0].release_threads
    rep = _report(tmp_path, "release_ordering")
    assert f"play ABORTED {n} of {n}" in rep and f"release_all OK {n} of {n}" in rep
    # a trial released before the E-down (delay < 1 ms on the fake) has no E event at all
    assert "E-up - release done_ns: n " in rep and " min -1.000 ms p50 -1.000 ms p99 -1.000 ms max -1.000 ms" in rep
    assert "key-downs past done_ns + CI + 5 ms: 0" in rep and "E holds >= 500 ms: 0" in rep
    assert "release-all ordering: as expected in 50 of 50" in rep and "[review]" not in rep


def test_release_ordering_a_reordered_play_stops_with_a_review_item(tmp_path, clock, monkeypatch, capsys):
    monkeypatch.setattr(probe, "PROBES", {"release_ordering": probe.PROBES["release_ordering"], "two": _dummy("two")})
    rig = ReleaseRig(clock, link_kw={"reorder": True})
    code, _ = _run(tmp_path, clock, probes=("release_ordering", "two"), rig=rig)
    assert code == probe.EXIT_STOP and len(rig.links) == 1
    rep = _report(tmp_path, "release_ordering")
    assert "play ABORTED 0 of 50 (other: OK 50)" in rep
    assert "key-downs past done_ns + CI + 5 ms: 50" in rep and "E holds >= 500 ms: 50" in rep
    assert "[review] release_ordering: the writes can reorder" in rep and "P4's abort rules" in rep
    err = capsys.readouterr().err
    assert "not run: two" in err


def _ordering_outcomes(rep):
    """The reasons on the report's 'other outcomes:' line, as {reason: trials}."""
    tail = [ln for ln in rep.splitlines() if ln.startswith("release-all ordering:")][0].partition("other outcomes: ")[2]
    return {k: int(v) for k, v in re.findall(r"(.+?) (\d+)(?:, |$)", tail)}


@pytest.mark.parametrize("reason, link_kw", [
    ("play OK", {"play_ok": True}),
    ("release_all not OK", {"release_fail": InputError.WRITE_FAILED}),
    ("key-down past the margin", {"late_down_ns": 50 * MS}),
    ("E hold >= 500 ms", {"up_shift_ns": 600 * MS}),
    ("E left down", {"lift": False}),
], ids=["play-not-aborted", "release-all-failed", "late-key-down", "long-e-hold", "e-left-down"])
def test_release_ordering_stops_for_each_reason_alone(tmp_path, clock, reason, link_kw):
    # A-V1-Rb I1: plan V1 "any other outcome ... stop"; each per-trial reason, the others clean, must stop on its own
    code, _ = _run(tmp_path, clock, probes=("release_ordering",), rig=ReleaseRig(clock, link_kw=link_kw))
    rep = _report(tmp_path, "release_ordering")
    kinds = _ordering_outcomes(rep)
    assert kinds.keys() == {reason} and 0 < kinds[reason] <= probe.RELEASE_ORDER_N
    assert code == probe.EXIT_STOP
    assert "[review] release_ordering: the writes can reorder" in rep and f"{reason} {kinds[reason]}" in rep


def test_release_ordering_stops_on_the_realistic_reorder_that_the_pc_marked_aborted(tmp_path, clock):
    # P4's abort rules: the PC ends the play ABORTED on the RELEASE_ALL's DONE, the board still plays the 1 s E hold
    # 50 ms after done_ns: play ABORTED and release OK, so only the key-down and the hold betray it (A-V1-Rb I1)
    rig = ReleaseRig(clock, link_kw={"reorder_aborted": True})
    code, _ = _run(tmp_path, clock, probes=("release_ordering",), rig=rig)
    rep = _report(tmp_path, "release_ordering")
    n = probe.RELEASE_ORDER_N
    assert f"play ABORTED {n} of {n}" in rep and f"release_all OK {n} of {n}" in rep
    assert _ordering_outcomes(rep) == {"key-down past the margin": n, "E hold >= 500 ms": n}
    assert code == probe.EXIT_STOP and "[review] release_ordering: the writes can reorder" in rep


def test_release_ordering_an_e_up_late_past_done_ns_is_reported_not_gated(tmp_path, clock):
    # E-up 30 ms after done_ns (PC-side path skew): reported only; the E hold is still far below 500 ms
    rig = ReleaseRig(clock, link_kw={"up_shift_ns": 30 * MS})
    code, _ = _run(tmp_path, clock, probes=("release_ordering",), rig=rig)
    assert code == probe.EXIT_OK
    rep = _report(tmp_path, "release_ordering")
    assert " min 30.000 ms p50 30.000 ms p99 30.000 ms max 30.000 ms (reported, not gated" in rep
    assert "[review]" not in rep


def test_release_probe_focus_lost_aborts_without_a_second_thread_release(tmp_path, clock):
    rig = ReleaseRig(clock, sink_kw={"away_from_check": 2})
    code, _ = _run(tmp_path, clock, probes=("release_ordering",), rig=rig)
    assert code == probe.EXIT_ABORT
    # only the abort path's release_all (on the probe's thread); the second thread never released
    assert rig.links[0].release_threads == ["MainThread"] and rig.links[0].plays == 0
    path = tmp_path / "run" / "release_ordering" / probe.RELEASES_FILE
    assert not path.exists() or _rows(path) == []
    assert [t for t in threading.enumerate() if t.name == probe.RELEASE_THREAD] == []


def test_release_trial_on_a_real_ble_link_aborts_a_written_play(tmp_path):
    # P4's seam on a real BleLink (test_link's FakeBoard answers as F5): the hook runs before the PLAY write, the
    # second thread's release_all() covers the written PLAY, the board's DONE 3 ends it ABORTED.
    from types import SimpleNamespace
    from test_link import FakeBoard, connected_link
    from src.input.protocol import OP_PLAY
    board = FakeBoard()
    lk = connected_link(board)
    try:
        ctx = SimpleNamespace(link=lk, run_dir=tmp_path)
        row = {"seq": 7, "action": "e_hold"}
        out = probe.release_trial(ctx, lambda: soak.CommandOutcome(row, lk.play(probe.RELEASE_ORDER_STEPS), None),
                                  0.01, lk.release_all)
        assert out.result.failure() == InputError.ABORTED and len(board.ops(OP_PLAY)) == 1
        (rel,) = _rows(tmp_path / probe.RELEASES_FILE)
        assert rel["seq"] == 7 and rel["release_status"] == "OK"
        assert rel["write_ns"] + 10 * MS <= rel["release_call_ns"] < rel["release_done_ns"]
    finally:
        lk.close()

# ---- --link-debug (A-M6diag) --------------------------------------------------------------------------

def _logging_dummy(name):
    """A probe that logs one DEBUG and one WARNING record on the link logger between two taps."""
    def commands(ctx):
        ctx.issue(soak.Command("tap", (Key.E,)))
        logging.getLogger(soak.LINK_LOGGER).debug("rx from %s", name)
        logging.getLogger(soak.LINK_LOGGER).warning("warn from %s", name)
        ctx.issue(soak.Command("tap", (Key.E,)))
    return probe.Probe(name, commands, lambda run_dir: probe.ProbeReport([f"{name} ran"]))


def test_link_debug_is_off_by_default_and_parsed():
    assert probe.config_from_args(probe.parse_args([])).link_debug is False
    assert probe.config_from_args(probe.parse_args(["--link-debug", "--probes", "m6"])).link_debug is True


@pytest.mark.parametrize("on", [False, True])
def test_link_debug_writes_each_probes_debug_records_to_its_own_file(tmp_path, clock, monkeypatch, on):
    monkeypatch.setattr(probe, "PROBES", {"one": _logging_dummy("one"), "two": _logging_dummy("two")})
    logger = logging.getLogger(soak.LINK_LOGGER)
    level, handlers = logger.level, list(logger.handlers)
    cfg = dataclasses.replace(_cfg(tmp_path, ("one", "two")), link_debug=on)
    rig = Rig(clock)
    assert probe.run_probes(cfg, rig.new_link, rig.start_sink) == probe.EXIT_OK
    for name, other in (("one", "two"), ("two", "one")):
        run_dir = cfg.out / name
        link_log = (run_dir / soak.LINK_LOG_FILE).read_text(encoding="utf-8")
        assert f"warn from {name}" in link_log and "rx from" not in link_log  # link.log stays WARNING (C5)
        debug = run_dir / probe.LINK_DEBUG_FILE
        assert debug.exists() == on
        if on:
            lines = debug.read_text(encoding="utf-8").splitlines()
            assert any(line.endswith(f"DEBUG rx from {name}") for line in lines)
            assert any(line.endswith(f"WARNING warn from {name}") for line in lines)
            assert not any(other in line for line in lines)
            assert all(int(line.split()[1]) > 0 for line in lines)  # perf_ns stamped, link.log's format
    assert logger.level == level and logger.handlers == handlers
