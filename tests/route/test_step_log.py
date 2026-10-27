"""Tests for the step-log format and reader (route view plan task RV-1, contract V1)."""

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest
from returns.pipeline import is_successful
from returns.result import Failure

from src.route.leg_log import LegOutcome, LegRecord, MoveEntry, leg_to_json
from src.route.step_log import (
    EVENTS,
    FIELDS,
    RunHeader,
    RunSteps,
    StepEvent,
    StepTail,
    event_line,
    header_line,
    parse_line,
    read_run,
)

REPO = Path(__file__).resolve().parents[2]

# Spec 5.2, the event table, "Fields" column after `t_ns` (`run` has no `t_ns`).
SPEC_FIELDS = {
    "leg": ("lap", "leg", "from", "to"),
    "read_failed": ("why",),
    "no_plan": ("pos", "why"),
    "reached": ("pos",),
    "stall": ("best_cost_s",),
    "planned": ("pos", "kind", "dir", "walk_ms", "cost_to_go_s", "toward"),
    "sent": ("input", "reaction_ms", "waited_ms", "keys"),
    "settled": ("pos", "stopped"),
    "leg_end": ("outcome", "misses", "off_graph", "stalls"),
    "end": ("reason", "code"),
}
HEADER_KEYS = ["ev", "version", "source", "clock", "map", "map_sha256", "class", "params", "rotation", "git_head",
               "wall_ns", "perf_ns"]
# One sample value tuple per event, in FIELDS order.
SAMPLES: dict[str, tuple] = {
    "leg": (2, 3, (10, 20), (30, 40)),
    "read_failed": ("stale",),
    "no_plan": ((5, 6), "no route"),
    "reached": ((5, 6),),
    "stall": (1.25,),
    "planned": ((62, 58), "teleport", "RIGHT", None, 3.725, (81, 58)),
    "sent": ("ok", 231.0, 228.4, ((0, 2), (150, 0))),
    "settled": ((82, 58), False),
    "leg_end": ("reached", 0, 1, 2),
    "end": (None, 0),
}
PARAMS = {"arrive_px": 2, "walk_px_per_s": 8.0}


def _header(**over) -> RunHeader:
    base = dict(source="walk", clock="perf_counter_ns", map="map.csv", map_sha256="ab" * 32, character="kanna",
                params=PARAMS, rotation=((113, 60), (81, 58)), git_head="abc123", wall_ns=1_700_000_000_000_000_000,
                perf_ns=44_462_799_439_300)
    base.update(over)
    return RunHeader(**base)


def _ev_line(ev: str, t_ns: int | None = 1000) -> bytes:
    result = event_line((ev, t_ns, *SAMPLES[ev]))
    assert is_successful(result), result
    return result.unwrap()


def _header_bytes(**over) -> bytes:
    return header_line(_header(**over)).unwrap()


# 1 ---------------------------------------------------------------------------------------------------------------


def test_fields_equal_the_spec_table_in_order():
    assert list(FIELDS) == list(SPEC_FIELDS)
    for ev, names in SPEC_FIELDS.items():
        assert FIELDS[ev] == names
    assert EVENTS == tuple(SPEC_FIELDS)
    assert "run" not in EVENTS


# 2 ---------------------------------------------------------------------------------------------------------------


def test_header_line_key_order_and_round_trip():
    header = _header()
    line = header_line(header).unwrap()
    assert line.endswith(b"\n") and line.count(b"\n") == 1
    assert list(json.loads(line)) == HEADER_KEYS
    assert json.loads(line)["class"] == "kanna"
    parsed = parse_line(line).unwrap()
    assert parsed == header
    assert isinstance(parsed, RunHeader) and parsed.rotation == ((113, 60), (81, 58))
    assert parsed.wall_ns == 1_700_000_000_000_000_000 and parsed.perf_ns == 44_462_799_439_300


def test_header_null_anchors_and_null_rotation():
    header = _header(wall_ns=None, perf_ns=None, rotation=None, map_sha256=None, git_head=None, source="sim",
                     clock="sim_ns")
    line = header_line(header).unwrap()
    raw = json.loads(line)
    assert raw["wall_ns"] is None and raw["perf_ns"] is None and raw["rotation"] is None
    assert parse_line(line).unwrap() == header


def test_header_without_the_two_anchor_keys_parses_with_none():
    raw = json.loads(_header_bytes())
    del raw["wall_ns"], raw["perf_ns"]
    parsed = parse_line((json.dumps(raw) + "\n").encode()).unwrap()
    assert isinstance(parsed, RunHeader)
    assert parsed.wall_ns is None and parsed.perf_ns is None


def test_header_missing_a_key_or_wrong_version_is_a_failure():
    raw = json.loads(_header_bytes())
    del raw["params"]
    result = parse_line(json.dumps(raw).encode())
    assert isinstance(result, Failure) and "params" in result.failure()
    raw = json.loads(_header_bytes())
    raw["version"] = 2
    assert isinstance(parse_line(json.dumps(raw).encode()), Failure)


# 3 ---------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("ev", list(SPEC_FIELDS))
def test_every_event_round_trips(ev):
    line = event_line((ev, 123456789, *SAMPLES[ev])).unwrap()
    assert line.endswith(b"\n")
    assert list(json.loads(line)) == ["ev", "t_ns", *FIELDS[ev]]
    parsed = parse_line(line).unwrap()
    assert isinstance(parsed, StepEvent)
    assert parsed.ev == ev and parsed.t_ns == 123456789
    assert parsed.data == dict(zip(FIELDS[ev], SAMPLES[ev]))


def test_positions_come_back_as_tuples_and_none():
    parsed = parse_line(_ev_line("planned")).unwrap()
    assert isinstance(parsed, StepEvent)
    assert parsed.data["pos"] == (62, 58) and isinstance(parsed.data["pos"], tuple)
    assert parsed.data["toward"] == (81, 58)
    leg = parse_line(event_line(("leg", 5, 0, 0, None, (113, 60))).unwrap()).unwrap()
    assert isinstance(leg, StepEvent)
    assert leg.data["from"] is None and leg.data["to"] == (113, 60)
    sent = parse_line(_ev_line("sent")).unwrap()
    assert isinstance(sent, StepEvent)
    assert sent.data["keys"] == ((0, 2), (150, 0))
    assert all(isinstance(pair, tuple) for pair in sent.data["keys"])


def test_the_spec_example_planned_line():
    line = (b'{"ev": "planned", "t_ns": 44469522743700, "pos": [62, 58], "kind": "teleport", "dir": "RIGHT", '
            b'"walk_ms": null, "cost_to_go_s": 3.725, "toward": [81, 58]}\n')
    parsed = parse_line(line).unwrap()
    assert parsed == StepEvent("planned", 44469522743700, {
        "pos": (62, 58), "kind": "teleport", "dir": "RIGHT", "walk_ms": None, "cost_to_go_s": 3.725,
        "toward": (81, 58)})
    encoded = event_line(("planned", 44469522743700, (62, 58), "teleport", "RIGHT", None, 3.725, (81, 58)))
    assert encoded.unwrap() == line


# 4 ---------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("event", [
    ("bogus", 1, 2),
    ("reached", 1),  # one value too few
    ("reached", 1, (1, 2), "extra"),  # one value too many
    ("stall", 1, float("nan")),
    ("stall", 1, float("inf")),
    ("stall", 1, object()),
    ("reached", "t", (1, 2)),  # t_ns not an int
    ("reached", None, (1, 2)),  # null t_ns is for `settled` only
    ("reached",),
    (),
])
def test_event_line_failures_never_raise(event):
    assert isinstance(event_line(event), Failure)


@pytest.mark.parametrize("line", [
    b"not json at all\n",
    b'["ev", "leg"]\n',
    b'{"t_ns": 1}\n',
    b'{"ev": "bogus", "t_ns": 1}\n',
    b'{"ev": "reached", "t_ns": 1}\n',  # missing field
    b'{"ev": "reached", "t_ns": 1, "pos": [1]}\n',  # bad position
    b'{"ev": "stall", "t_ns": 1, "best_cost_s": NaN}\n',
    b'\xff\xfe{"ev": "end"}\n',
    b"\n",
])
def test_parse_line_failures_never_raise(line):
    assert isinstance(parse_line(line), Failure)


# 5 ---------------------------------------------------------------------------------------------------------------


def test_tail_missing_file_then_appends_and_partial_lines(tmp_path):
    path = tmp_path / "steps.jsonl"
    tail = StepTail(path)
    assert tail.poll() == []
    with path.open("ab") as f:
        f.write(_header_bytes() + _ev_line("reached") + _ev_line("stall"))
    got = tail.poll()
    assert [type(x) for x in got] == [RunHeader, StepEvent, StepEvent]
    assert tail.poll() == []
    line = _ev_line("read_failed")
    with path.open("ab") as f:
        f.write(line[:10])
    assert tail.poll() == []
    with path.open("ab") as f:
        f.write(line[10:] + _ev_line("end"))
    got = tail.poll()
    assert [x.ev for x in got if isinstance(x, StepEvent)] == ["read_failed", "end"]
    assert tail.bad_lines == 0


def test_tail_skips_a_bad_line_between_good_ones(tmp_path):
    path = tmp_path / "steps.jsonl"
    path.write_bytes(_header_bytes() + _ev_line("reached") + b"garbage\n" + _ev_line("stall"))
    tail = StepTail(path)
    got = tail.poll()
    assert [x.ev for x in got if isinstance(x, StepEvent)] == ["reached", "stall"]
    assert tail.bad_lines == 1


def test_tail_strips_a_carriage_return(tmp_path):
    path = tmp_path / "steps.jsonl"
    path.write_bytes(_header_bytes().rstrip(b"\n") + b"\r\n" + _ev_line("reached").rstrip(b"\n") + b"\r\n")
    got = StepTail(path).poll()
    assert [type(x) for x in got] == [RunHeader, StepEvent]


def test_a_second_run_line_is_a_bad_line_for_tail_and_read_run(tmp_path):
    path = tmp_path / "steps.jsonl"
    path.write_bytes(_header_bytes() + _ev_line("reached") + _header_bytes(source="sim") + _ev_line("stall"))
    tail = StepTail(path)
    got = tail.poll()
    assert [type(x) for x in got] == [RunHeader, StepEvent, StepEvent]
    assert got[0] == _header()
    assert tail.bad_lines == 1
    run = read_run(tmp_path).unwrap()
    assert run.header == _header()
    assert [e.ev for e in run.events] == ["reached", "stall"]
    assert run.bad_lines == 1


# 6 ---------------------------------------------------------------------------------------------------------------


def test_read_run_steps_ended_and_not_ended(tmp_path):
    (tmp_path / "steps.jsonl").write_bytes(_header_bytes() + _ev_line("reached") + _ev_line("end"))
    run = read_run(tmp_path).unwrap()
    assert isinstance(run, RunSteps)
    assert run.origin == "steps" and run.ended is True and run.bad_lines == 0
    assert run.header == _header() and [e.ev for e in run.events] == ["reached", "end"]
    (tmp_path / "steps.jsonl").write_bytes(_header_bytes() + _ev_line("reached"))
    run = read_run(tmp_path).unwrap()
    assert run.ended is False and run.origin == "steps"


def test_read_run_first_line_not_a_header_is_a_failure(tmp_path):
    (tmp_path / "steps.jsonl").write_bytes(_ev_line("reached") + _header_bytes())
    result = read_run(tmp_path)
    assert isinstance(result, Failure) and "steps.jsonl" in result.failure()


# 7 ---------------------------------------------------------------------------------------------------------------


def _legs_folder(tmp_path: Path, map_path: str = "maps/ff2.csv") -> Path:
    session = {"seed": 1, "params": PARAMS, "leg_timeout_s": 30.0, "map": map_path, "map_sha256": "cd" * 32,
               "git_head": "deadbeef", "start_utc": "2026-10-27T10:00:00+00:00", "class": "kanna", "laps": 1,
               "minutes": None, "record": False}
    (tmp_path / "session.json").write_text(json.dumps(session), encoding="utf-8")
    leg0 = LegRecord(
        lap=0, leg=0, src=None, dst=(113, 60), start_ns=1_000, end_ns=9_000, outcome=LegOutcome.REACHED,
        moves=(MoveEntry(2_000, (62, 58), "teleport", "RIGHT", None, 3.725, "ok", (82, 58)),
               MoveEntry(5_000, (82, 58), "walk", "RIGHT", 1500.0, 1.2, "ok", (113, 60))),
        misses=0, off_graph=0, stalls=0)
    leg1 = LegRecord(
        lap=1, leg=1, src=(113, 60), dst=(81, 58), start_ns=10_000, end_ns=19_000, outcome=LegOutcome.LINK_DOWN,
        moves=(MoveEntry(11_000, (113, 60), "walk", "LEFT", 900.0, 2.0, "ok", (100, 60)),
               MoveEntry(14_000, (100, 60), "walk", "LEFT", 800.0, 1.0, "LINK_LOST", None)),
        misses=1, off_graph=2, stalls=3)
    with (tmp_path / "legs.jsonl").open("ab") as f:
        for rec in (leg0, leg1):
            f.write((json.dumps(leg_to_json(rec)) + "\n").encode())
    return tmp_path


def test_legs_only_folder_rebuilds_header_and_events(tmp_path):
    run = read_run(_legs_folder(tmp_path)).unwrap()
    h = run.header
    assert h.source == "legs" and h.map == "maps/ff2.csv" and h.map_sha256 == "cd" * 32
    assert h.character == "kanna" and h.params == PARAMS and h.git_head == "deadbeef"
    assert h.rotation is None and h.wall_ns is None and h.perf_ns is None
    assert run.origin == "legs" and run.ended is True and run.bad_lines == 0
    assert [e.ev for e in run.events] == [
        "leg", "planned", "settled", "planned", "settled", "leg_end",
        "leg", "planned", "settled", "planned", "leg_end"]  # no `settled` after the LINK_DOWN leg's last move
    leg, planned, settled = run.events[:3]
    assert leg.t_ns == 1_000 and leg.data == {"lap": 0, "leg": 0, "from": None, "to": (113, 60)}
    assert planned.t_ns == 2_000
    assert planned.data == {"pos": (62, 58), "kind": "teleport", "dir": "RIGHT", "walk_ms": None,
                            "cost_to_go_s": 3.725, "toward": None}
    assert settled.t_ns is None and settled.data == {"pos": (82, 58), "stopped": False}
    leg_end = run.events[5]
    assert leg_end.t_ns == 9_000
    assert leg_end.data == {"outcome": "reached", "misses": 0, "off_graph": 0, "stalls": 0}
    last = run.events[-1]
    assert last.t_ns == 19_000
    assert last.data == {"outcome": "link_down", "misses": 1, "off_graph": 2, "stalls": 3}


# 8 ---------------------------------------------------------------------------------------------------------------


def test_read_run_empty_folder_names_the_folder_and_both_files(tmp_path):
    result = read_run(tmp_path)
    assert isinstance(result, Failure)
    message = result.failure()
    assert str(tmp_path) in message and "steps.jsonl" in message and "legs.jsonl" in message


def test_legs_only_without_session_json_names_it(tmp_path):
    (tmp_path / "legs.jsonl").write_bytes(b"")
    result = read_run(tmp_path)
    assert isinstance(result, Failure) and "session.json" in result.failure()


# 9 ---------------------------------------------------------------------------------------------------------------


@pytest.mark.slow
def test_importing_step_log_imports_no_other_src_module():
    code = (
        "import sys; sys.path.insert(0, sys.argv[1]);"
        "before = {m for m in sys.modules if m == 'src' or m.startswith('src.')};"
        "import src.route.step_log;"
        "after = {m for m in sys.modules if m == 'src' or m.startswith('src.')};"
        "print(sorted(after - before))"
    )
    out = subprocess.run([sys.executable, "-I", "-c", code, str(REPO)], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == str(sorted({"src", "src.route", "src.route.step_log"}))


# 10 --------------------------------------------------------------------------------------------------------------


def test_torn_last_line_is_skipped(tmp_path):
    events = [_ev_line(ev) for ev in ("leg", "planned", "sent", "settled", "reached")]
    (tmp_path / "steps.jsonl").write_bytes(_header_bytes() + b"".join(events[:4]) + events[4].rstrip(b"\n"))
    run = read_run(tmp_path).unwrap()
    assert [e.ev for e in run.events] == ["leg", "planned", "sent", "settled"]
    assert run.ended is False and run.bad_lines == 0  # a torn last line is skipped, not counted


# 11 --------------------------------------------------------------------------------------------------------------


def test_legs_only_map_path_that_does_not_exist_is_still_a_success(tmp_path):
    assert is_successful(read_run(_legs_folder(tmp_path, map_path="gone/missing.csv")))


@pytest.mark.parametrize("key", ["params", "class"])
def test_legs_only_session_without_a_key_names_it(tmp_path, key):
    _legs_folder(tmp_path)
    session = json.loads((tmp_path / "session.json").read_text())
    del session[key]
    (tmp_path / "session.json").write_text(json.dumps(session))
    result = read_run(tmp_path)
    assert isinstance(result, Failure)
    assert repr(key) in result.failure() and "session.json" in result.failure()


# 12 --------------------------------------------------------------------------------------------------------------


@pytest.mark.slow
def test_read_run_of_50000_events_is_fast(tmp_path):
    lines = [_header_bytes()]
    for i in range(50_000):
        ev = ("planned", "sent", "settled", "reached", "stall")[i % 5]
        lines.append(_ev_line(ev, t_ns=i))
    (tmp_path / "steps.jsonl").write_bytes(b"".join(lines))
    start = time.perf_counter()
    run = read_run(tmp_path).unwrap()
    elapsed = time.perf_counter() - start
    assert len(run.events) == 50_000 and run.bad_lines == 0
    assert elapsed < 2.0, f"read_run took {elapsed:.2f} s"


@pytest.mark.slow
def test_tail_poll_reads_only_the_new_line(tmp_path, monkeypatch):
    path = tmp_path / "steps.jsonl"
    path.write_bytes(_header_bytes() + b"".join(_ev_line("reached") for _ in range(2_000)))
    tail = StepTail(path)
    assert len(tail.poll()) == 2_001
    new = _ev_line("stall")
    with path.open("ab") as f:
        f.write(new)

    read_sizes: list[int] = []
    real_open = Path.open

    class Spy:
        def __init__(self, handle):
            self._handle = handle

        def __enter__(self):
            self._handle.__enter__()
            return self

        def __exit__(self, *exc):
            return self._handle.__exit__(*exc)

        def seek(self, *args):
            return self._handle.seek(*args)

        def read(self, *args):
            data = self._handle.read(*args)
            read_sizes.append(len(data))
            return data

    monkeypatch.setattr(Path, "open", lambda self, *a, **k: Spy(real_open(self, *a, **k)))
    got = tail.poll()
    assert [e.ev for e in got if isinstance(e, StepEvent)] == ["stall"]
    assert read_sizes == [len(new)]


def test_legs_only_leg_record_with_a_null_to_is_a_bad_line(tmp_path):
    _legs_folder(tmp_path)
    lines = (tmp_path / "legs.jsonl").read_bytes().splitlines(keepends=True)
    record = json.loads(lines[0])
    record["to"] = None
    (tmp_path / "legs.jsonl").write_bytes((json.dumps(record) + "\n").encode() + lines[1])
    run = read_run(tmp_path).unwrap()
    assert run.bad_lines == 1
    assert [e.ev for e in run.events] == ["leg", "planned", "settled", "planned", "leg_end"]  # only the second leg
