"""Tests for the RV-1 review fixes in the step-log reader and encoder (route view plan task RV-2's brief, judge 2's
m1-m3): `read_run` never raises on a deeply nested `session.json`, `StepTail.poll` checks results with
`is_successful`, and `event_line` refuses positions and keys that are not ints, as `parse_line` does."""

import ast
import json
from pathlib import Path

import pytest
from returns.pipeline import is_successful

from src.route import step_log
from src.route.step_log import event_line, parse_line, read_run

_DEEP = 100_000  # deep enough that json.loads (and a plain repr) runs out of recursion


def _deep_list() -> list:
    deep: list = []
    for _ in range(_DEEP):
        deep = [deep]
    return deep


# ---- m1: read_run never raises --------------------------------------------------------------------------------


def test_read_run_with_a_deeply_nested_session_json_is_a_failure_not_a_raise(tmp_path):
    (tmp_path / "legs.jsonl").write_bytes(b"")
    (tmp_path / "session.json").write_bytes(b"[" * _DEEP + b"]" * _DEEP)
    result = read_run(tmp_path)
    assert not is_successful(result)
    assert "session.json" in result.failure()


# ---- m2: StepTail.poll checks with is_successful (hard rule 5) ------------------------------------------------


def test_step_tail_poll_checks_parse_results_with_is_successful():
    tree = ast.parse(Path(step_log.__file__).read_text(encoding="utf-8"))
    tail = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "StepTail")
    poll = next(n for n in tail.body if isinstance(n, ast.FunctionDef) and n.name == "poll")
    called = set()
    for node in ast.walk(poll):
        if isinstance(node, ast.Call):
            func = node.func
            called.add(func.id if isinstance(func, ast.Name) else getattr(func, "attr", None))
    assert "is_successful" in called
    assert "value_or" not in called


# ---- m3: event_line refuses what parse_line refuses -----------------------------------------------------------


@pytest.mark.parametrize("event", [
    ("reached", 1, (1.5, 2)),
    ("reached", 1, (1, 2.0)),
    ("reached", 1, (True, 2)),
    ("reached", 1, (1, 2, 3)),
    ("reached", 1, (1,)),
    ("reached", 1, "1,2"),
    ("no_plan", 1, [1.0, 2.0], "off the graph"),
    ("planned", 1, (1, 2), "walk", "LEFT", 100.0, 1.0, (3.5, 4)),
    ("planned", 1, (1.5, 2), "walk", "LEFT", 100.0, 1.0, None),
    ("leg", 1, 0, 0, (1, 2.0), (3, 4)),
    ("leg", 1, 0, 0, None, (3, 4.0)),
    ("settled", 1, (0.5, 0), False),
    ("sent", 1, "ok", None, 0.0, ((0, 2.5),)),
    ("sent", 1, "ok", None, 0.0, ((0, 2, 3),)),
    ("sent", 1, "ok", None, 0.0, (None,)),
    ("sent", 1, "ok", None, 0.0, "0,2"),
], ids=lambda e: repr(e))
def test_event_line_refuses_a_position_or_key_that_is_not_int(event):
    result = event_line(event)
    assert not is_successful(result)
    assert not is_successful(parse_line(_unchecked_line(event)))  # the reader refuses the same line


def _unchecked_line(event) -> bytes:
    """The line `event` would make without event_line's shape checks (what RV-1's encoder wrote)."""
    ev, t_ns, *values = event
    obj = {"ev": ev, "t_ns": t_ns, **dict(zip(step_log.FIELDS[ev], values))}
    return (json.dumps(obj) + "\n").encode()


@pytest.mark.parametrize("event", [
    ("reached", 1, (1, 2)),
    ("reached", 1, [1, 2]),
    ("leg", 1, 0, 0, None, (3, 4)),
    ("planned", 1, (1, 2), "walk", "LEFT", 100.0, 1.0, None),
    ("sent", 1, "ok", None, 0.0, ()),
    ("sent", 1, "ok", 230.5, 228.0, ((0, 2), (1500, 0))),
    ("sent", 1, "ok", None, 0.0, [[0, 2]]),
], ids=lambda e: repr(e))
def test_event_line_still_takes_int_positions_and_keys_as_tuples_or_lists(event):
    line = event_line(event)
    assert is_successful(line)
    assert is_successful(parse_line(line.unwrap()))


@pytest.mark.parametrize("make", [
    lambda deep: ("reached", 1, deep),
    lambda deep: ("sent", 1, "ok", None, 0.0, deep),
    lambda deep: (deep, 1),
    lambda deep: ("reached", deep, (1, 2)),
], ids=["deep pos", "deep keys", "deep ev", "deep t_ns"])
def test_event_line_with_a_deeply_nested_value_is_a_failure_not_a_raise(make):
    assert not is_successful(event_line(make(_deep_list())))
