"""Tests for the V1 measurement probes (src/input/harness/probe.py, plan A Task V1).

No board, no radio, no window, no arduino-cli: the link, the listener/sink, the process list, the board list and
the clock are fakes. `ProbeLink.play()` writes the listener transitions its steps would cause (the board as a
perfect keyboard) into a shared key log that `ProbeSink.stop()` writes as the probe dir's `keys.jsonl`, so the
probe reports run analyze.py / realism.py on real C5 files.
"""
import json

import pytest
from returns.result import Failure, Success

from src.input import input_layer
from src.input.harness import probe, soak
from src.input.harness.listener import VK_BY_KEY
from src.input.link import InputError, SendReport, SessionInfo
from src.input.protocol import Accepted, Done, Key, Pong, transitions

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


def _pong(itvl=12):
    return Pong(seq=1, itvl_units=itvl, latency=0, timeout_units=100, mtu=247, bonded=True, encrypted=True,
                authenticated=False, subscribed=True, board_us=123_456)


class ProbeLink:
    """A BleLink stand-in. play() takes the schedule's duration + 20 ms of fake time and logs the listener
    transitions of its steps (1 ms after the send, at the scheduled offsets) into `keys`, unless the transition's
    running index is in `drop_keys`. `fail_plays` (1-based) fail with `fail_error`."""

    MAX_PLAYS = 5000

    def __init__(self, clock, keys, fail_plays=(), fail_error=InputError.LINK_LOST, drop_keys=()):
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

    def connect(self, timeout_s=30.0):
        self.events.append("connect")
        self.connected = True
        self.pongs.append((self.clock.clock(), _pong()))
        return Success(SessionInfo("AA:BB", 247, _pong()))

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
        for t_100us, key, down in transitions(steps):
            if self.n_transitions not in self.drop_keys:
                self.keys.append({"qpc_ns": sent + MS + t_100us * 100_000, "vk": VK_BY_KEY[key], "down": down,
                                  "injected": False, "tick_ms": 0, "repeat": False})
            self.n_transitions += 1
        self.clock.now += sum(s.delta_100us for s in steps) * 100_000 + 20 * MS
        n = self.clock.clock()
        return Success(SendReport(seq=self.plays, sent_ns=sent, written_ns=sent + 1_000, accepted_ns=sent + 2_000,
                                  done_ns=n, accepted=Accepted(self.plays, 0),
                                  done=Done(seq=self.plays, status=0, fail_step=None, rc=0, t0_us=0, late_us=())))

    def release_all(self):
        self.events.append("release_all")
        return Success(None)


class ProbeSink:
    """H1's Listener stand-in: in front unless `away_from_command` (1-based run_command count) is reached; stop()
    writes the shared key log into <run dir>/keys.jsonl. `click` False: the start wait never sees the sink."""

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
        with open(self.run_dir / "keys.jsonl", "w", encoding="utf-8", newline="\n") as fh:
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
    for mod in (soak, input_layer):
        monkeypatch.setattr(mod, "_clock", fc.clock)
        monkeypatch.setattr(mod, "_sleep", fc.sleep)
    monkeypatch.setattr(probe, "_clock", fc.clock)
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
    assert cfg.probes == tuple(probe.PROBES) and cfg.seed == 3
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
