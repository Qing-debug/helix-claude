"""RV-9 review minor m1 (route-view plan task RV-10, first commit): `walk.main` records how it was left without
looking at an exception its caller is still handling. (Its m4 tests, on the viewer start, went with the viewer in
route-tab task RT-2a.) Uses the B19 rig and RV-9's `_frame` as they are (flat imports, `tests/CLAUDE.md`)."""

import pytest
import test_walk_cli
from returns.result import Failure
from test_walk_cli import _lines
from test_walk_view import _frame

from src.route import walk
from src.route.step_log import STEPS_FILE

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
