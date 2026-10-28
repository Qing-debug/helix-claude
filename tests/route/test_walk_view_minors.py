"""RV-9 review minors m1 and m4 (route-view plan task RV-10, first commit): `walk.main` records how it was left without
looking at an exception its caller is still handling, and the two walk-side branches the RV-9 tests did not reach
(the walk's claim failing with an OSError other than FileExistsError; `_start_viewer` without the Windows flags).
Uses the B19 rig and RV-9's fake viewer as they are (flat imports, `tests/CLAUDE.md`)."""

import subprocess

import pytest
import test_walk_cli
from returns.result import Failure
from test_walk_cli import _lines
from test_walk_view import LATE, FakeProc, _frame, _viewer

from src.route import walk
from src.route.step_log import STEPS_FILE, VIEW_CLAIM, VIEW_LOG

sim = test_walk_cli.sim  # the B19 rig's fixture (bound by assignment, as test_walk_view.py does)


def _end_line(out) -> dict:
    last = _lines(out / STEPS_FILE)[-1]
    assert last["ev"] == "end"
    return last


def test_m1_a_normal_return_inside_a_callers_except_block_records_its_code(sim):
    sim.frame = _frame()
    try:
        raise ValueError("the caller is handling this")
    except ValueError:
        code = sim.main("--laps", "1")
    assert code == 0
    last = _end_line(sim.out)
    assert (last["reason"], last["code"]) == ("max_laps", 0)


def test_m1_a_nonzero_return_inside_a_callers_except_block_records_its_code(sim, monkeypatch):
    sim.frame = _frame()
    monkeypatch.setattr(walk.RouteWalker, "run", lambda self: Failure("boom"))
    try:
        raise ValueError("the caller is handling this")
    except ValueError:
        code = sim.main("--laps", "1")
    assert code == 6
    last = _end_line(sim.out)
    assert (last["reason"], last["code"]) == ("error", 6)


def test_m1_an_exception_leaving_main_inside_a_callers_except_block_still_records_null(sim, monkeypatch):
    sim.frame = _frame()

    def interrupted(self):
        raise KeyboardInterrupt

    monkeypatch.setattr(walk.RouteWalker, "run", interrupted)
    try:
        raise ValueError("the caller is handling this")
    except ValueError:
        with pytest.raises(KeyboardInterrupt):
            sim.main("--laps", "1")
    last = _end_line(sim.out)
    assert (last["reason"], last["code"]) == (None, None)


def test_m4_a_claim_failing_with_another_oserror_is_a_late_line_and_no_claim(sim, monkeypatch, capsys):
    sim.frame = _frame()
    fake = _viewer(monkeypatch, sim, "never")
    monkeypatch.setattr(walk, "_VIEW_READY_S", 0.2)
    monkeypatch.setattr(walk, "_VIEW_CLAIM_EXTRA_S", 5.0)  # an OSError must not wait this out (only FileExists does)
    monkeypatch.setattr(walk, "_VIEW_POLL_S", 0.01)
    real_open = open

    def open_claim_fails(path, *args, **kwargs):
        if str(path).endswith(VIEW_CLAIM):
            raise PermissionError("denied")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(walk, "open", open_claim_fails, raising=False)  # `open` is a builtin; this shadows it in walk
    assert sim.main("--laps", "1", "--view") == 0
    assert isinstance(fake.proc, FakeProc) and fake.outs == [sim.out]
    assert not (sim.out / VIEW_CLAIM).exists()
    assert capsys.readouterr().err.count(LATE.format(s=0.2)) == 1
    last = _end_line(sim.out)
    assert (last["reason"], last["code"]) == ("max_laps", 0)


def test_m4_without_the_windows_spawn_flags_the_start_is_a_failure_and_no_log_is_opened(tmp_path, monkeypatch):
    monkeypatch.delattr(subprocess, "CREATE_NO_WINDOW", raising=False)  # absent already off Windows
    failed = walk._start_viewer(tmp_path)
    assert isinstance(failed, Failure) and failed.failure().startswith("AttributeError")
    assert not (tmp_path / VIEW_LOG).exists()
