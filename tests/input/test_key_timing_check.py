"""The key-timing check `tools/key_timing_check.py` and its test window `tools/capture_load_window.py` (WALKFIX Task 10A,
QB283 2b). Nothing real is opened or sent: the test window process, the capture, `soak.run_soak` and the analysis are
fakes, `soak._read_answer` is patched (and counted), and no board, link, listener or key is touched.

Windows only, as the soak tests: `soak` imports winrt at module level.
"""
import ast
import importlib
import inspect
import itertools
import json
import subprocess
import sys
import textwrap
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pytest
from returns.result import Failure, Success

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="soak imports winrt at module level")

from src.input.harness import analyze, listener, realism, soak  # noqa: E402
from src.input.protocol import Key  # noqa: E402
from src.route import runtime  # noqa: E402
from src.route.position import MinimapPositionSource  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
REAL_MAKE_POSITIONS = runtime.make_positions  # taken at import, before any test patches it
ANSWERED_Y = "answered y: MapleStory closed"


@pytest.fixture(scope="module")
def ktc():
    return importlib.import_module("tools.key_timing_check")


@pytest.fixture(scope="module")
def clw():
    return importlib.import_module("tools.capture_load_window")


# ---- the rig: every outside thing is a fake ---------------------------------------------------------------------------

@dataclass
class Entry:
    """What the fake soak does for one arm: the figures its fake analysis gives and the capture rate it fakes."""
    p50: float = 30.0
    p99: float = 75.0
    rate: float = 45.0
    delivery: str = "PASS"
    exit_code: int = 0
    alive: bool = True
    window_dies: bool = False  # the test window's process ends during the arm
    lost: bool = False         # the capture loses the minimap during the arm, and finds it again before the end
    interrupt: bool = False    # Ctrl+C lands in the arm's analysis, after its soak ended


LATE = dict(p50=40.0, p99=75.0, rate=120.0)  # p50 more than 2 ms above the baselines' 30; fast enough to be N


def scripted(n=None, five=None, ten=None, two=None, one=None, b1=None, b2=None):
    """The entries per pause, in the order the arms run them: B1 and B2 (20 ms), N (0), then the search arms."""
    script = {20: [b1 or Entry(), b2 or Entry()], 0: [n or Entry(rate=120.0)]}
    for pause, entry in ((5, five), (10, ten), (2, two), (1, one)):
        if entry is not None:
            script[pause] = [entry]
    return script


class FakeWindow:
    def __init__(self):
        self.terminated = False
        self.killed = False
        self.dead = False

    def poll(self):
        return 0 if self.dead else None

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.killed = True


class FakePositions:
    def wait_new(self, timeout_s):
        time.sleep(0.002)

    def read(self):
        return Failure("no character in this test")


@dataclass
class Call:
    pause: float
    cfg: object
    answered: object
    announced: int
    printed: str


class Rig:
    def __init__(self, ktc, monkeypatch, tmp_path, capsys, script, answer="y", first_picture=True):
        self.ktc, self.capsys, self.script, self.answer = ktc, capsys, script, answer
        self.out = tmp_path / "data" / "keytiming" / "run"
        self.answers = 0
        self.window = FakeWindow()
        self.windows_started = []
        self.pauses, self.calls, self.figures = [], [], {}
        self.printed = ""
        self.at_question = None  # what was printed when the answer was read
        self.clock = 1000.0
        self.holder = None
        self.frame = np.zeros((4, 4, 3), np.uint8)
        self.first_picture = first_picture
        monkeypatch.setattr(ktc, "DATA", tmp_path / "data")
        monkeypatch.setattr(ktc, "_start_window", self._start_window)
        monkeypatch.setattr(ktc, "_set_capture_pause", self._set_pause)
        monkeypatch.setattr(ktc, "_clock", lambda: self.clock)
        monkeypatch.setattr(ktc, "measure_arm", self._measure)
        monkeypatch.setattr(runtime, "start_capture", self._start_capture)
        monkeypatch.setattr(runtime, "make_positions", lambda holder: FakePositions())  # as the real one: a bare source
        if not first_picture:
            monkeypatch.setattr(runtime, "wait_first_frame", lambda shared, timeout_s: Failure("no picture"))
        monkeypatch.setattr(soak, "_read_answer", self._read_answer)
        monkeypatch.setattr(soak, "run_soak", self._run_soak)
        monkeypatch.setattr(soak, "_new_link", lambda: "a link")

    def run(self, *extra, out=None):
        return self.ktc.main(["--out", str(out or self.out), "--commands", "50", *extra])

    def _start_window(self, at):
        self.windows_started.append(at)
        return self.window

    def _set_pause(self, pause_ms):
        self.pauses.append(pause_ms)
        return 0.0

    def _start_capture(self, holder):
        self.holder = holder
        if self.first_picture:
            holder.minimap_frame = self.frame
        return object()

    def _read_answer(self):
        self.answers += 1
        self.printed += self.capsys.readouterr().out
        self.at_question = self.printed
        if self.answer is KeyboardInterrupt:
            raise KeyboardInterrupt
        return Success(self.answer)

    def _measure(self, run_dir):
        figures = self.figures[Path(run_dir)]
        if figures is None:
            raise KeyboardInterrupt
        return figures

    def _run_soak(self, cfg, link, start_sink, *, answered=None):
        pause = self.pauses[-1]
        self.printed += self.capsys.readouterr().out
        self.calls.append(Call(pause, cfg, answered, self.printed.count(">>> Arm"), self.printed))
        entry = self.script[pause].pop(0)
        cfg.out.mkdir(parents=True, exist_ok=True)
        self.clock += 10.0
        if entry.lost:
            self.holder.minimap_lost = True  # as minimap_follow's search_failed, under the lock in the real capture
        for _ in range(int(entry.rate * 10)):
            self.holder.minimap_frame = self.frame
        if entry.lost:
            self.holder.minimap_lost = False  # found again (publish), so only "at any point during the arm" sees it
        if entry.window_dies:
            self.window.dead = True
        self.figures[cfg.out] = (None if entry.interrupt
                                 else self.ktc.ArmFigures(entry.delivery, entry.alive, entry.p50, entry.p99, 100))
        return entry.exit_code

    def result(self):
        return json.loads((self.out / "result.json").read_text(encoding="utf-8"))

    def pauses_run(self):
        return [call.pause for call in self.calls]


@pytest.fixture
def make_rig(ktc, monkeypatch, tmp_path, capsys):
    def make(script=None, **kw):
        return Rig(ktc, monkeypatch, tmp_path, capsys, script if script is not None else scripted(), **kw)
    return make


# ---- the question and the stops before the arms ------------------------------------------------------------------------

def test_asks_once_for_all_arms(make_rig):
    rig = make_rig()
    assert rig.run() == 0
    assert rig.pauses_run() == [20, 0, 20]
    assert rig.answers == 1
    assert [call.answered for call in rig.calls] == [ANSWERED_Y] * 3  # the one answer's text goes to every soak


def test_asks_once_even_when_the_search_runs(make_rig):
    rig = make_rig(scripted(n=Entry(**LATE), five=Entry(**LATE), ten=Entry()))
    assert rig.run() == 0
    assert len(rig.calls) == 5 and rig.answers == 1


def test_refusal_stops_before_any_soak(make_rig, capsys):
    rig = make_rig(answer="n")
    assert rig.run() == soak.EXIT_REFUSED
    assert rig.calls == [] and rig.answers == 1
    assert rig.window.terminated
    assert "refused: MapleStory not confirmed closed (answer: n)" in capsys.readouterr().err
    assert rig.pauses == []  # no arm ran, so the capture's pause was never touched


def test_no_picture_stops_before_the_question(make_rig, capsys):
    rig = make_rig(first_picture=False)
    assert rig.run() == 3
    assert rig.answers == 0 and rig.calls == []
    assert rig.window.terminated
    err = capsys.readouterr().err
    assert "the capture did not find the test window's minimap: put the window on the second screen " in err
    assert "(python tools/capture_load_window.py --at X,Y) and start again" in err


def test_the_real_make_positions_reaches_the_stand_in(make_rig, ktc, monkeypatch):
    """C1: `runtime.make_positions` gives a bare source (or a `Failure`), not a `Success`. The real one loads only the
    icon files; the stand-in is a recorder that starts no thread, and no first picture stops main right after it."""
    rig = make_rig(first_picture=False)
    monkeypatch.setattr(runtime, "make_positions", REAL_MAKE_POSITIONS)  # the rig's fake replaced by the real one
    given = []

    class Recorder:
        def __init__(self, positions):
            given.append(positions)

        def start(self):
            pass

        def stop(self):
            pass

    monkeypatch.setattr(ktc, "WalkerStandIn", Recorder)
    assert rig.run() == 3
    assert [type(p) for p in given] == [MinimapPositionSource]
    assert rig.window.terminated and rig.answers == 0


def test_a_character_icon_failure_stops_before_the_window(make_rig, monkeypatch, capsys):
    rig = make_rig()
    monkeypatch.setattr(runtime, "make_positions", lambda holder: Failure("character icon: missing"))
    assert rig.run() == soak.EXIT_ABORT
    assert rig.windows_started == [] and rig.answers == 0 and rig.calls == []
    assert "error: character icon: missing" in capsys.readouterr().err


def test_out_must_be_under_data(make_rig, tmp_path):
    rig = make_rig()
    assert rig.run(out=tmp_path / "elsewhere" / "run") == soak.EXIT_REFUSED
    assert rig.run(out=tmp_path / "data") == soak.EXIT_REFUSED  # data/ itself is not a folder inside it
    assert rig.windows_started == [] and rig.answers == 0 and rig.calls == []


def test_an_out_that_already_holds_files_is_refused(make_rig):
    rig = make_rig()
    rig.out.mkdir(parents=True)
    (rig.out / "result.json").write_text("{}", encoding="utf-8")
    assert rig.run() == soak.EXIT_REFUSED
    assert rig.windows_started == [] and rig.calls == []


def test_too_few_commands_are_refused_before_the_window_starts(make_rig):
    rig = make_rig()
    assert rig.ktc.main(["--out", str(rig.out), "--commands", "0"]) == soak.EXIT_REFUSED
    assert rig.windows_started == []


def test_at_is_passed_to_the_window(make_rig):
    rig = make_rig()
    assert rig.run("--at", "3840,0") == 0
    assert rig.windows_started == ["3840,0"]


# ---- the arms ------------------------------------------------------------------------------------------------------------

def test_no_search_when_n_is_on_time(make_rig):
    rig = make_rig()
    assert rig.run() == 0
    assert rig.pauses_run() == [20, 0, 20]
    result = rig.result()
    assert result["result"] == 0 and "stopped" not in result
    assert [arm["later"] for arm in result["arms"]] == [None, False, None]  # the baselines are not judged
    assert rig.pauses[-1] == 0.0 and rig.window.terminated


def test_search_order(make_rig):
    """N, 5 and 2 are later and 10 is not: 5 is later, so 10 is tried and 2 and 1 never are."""
    rig = make_rig(scripted(n=Entry(**LATE), five=Entry(**LATE), two=Entry(**LATE), ten=Entry()))
    assert rig.run() == 0
    assert rig.pauses_run() == [20, 0, 20, 5, 10]
    result = rig.result()
    assert result["result"] == 10
    assert [arm["later"] for arm in result["arms"]] == [None, True, None, True, False]


def test_search_goes_down_when_5_is_on_time(make_rig):
    rig = make_rig(scripted(n=Entry(**LATE), five=Entry(), two=Entry(), one=Entry(**LATE)))
    assert rig.run() == 0
    assert rig.pauses_run() == [20, 0, 20, 5, 2, 1]
    assert rig.result()["result"] == 2  # 1 is later, 2 is not


def test_search_stops_at_5_when_2_is_later(make_rig):
    rig = make_rig(scripted(n=Entry(**LATE), five=Entry(), two=Entry(**LATE)))
    assert rig.run() == 0
    assert rig.pauses_run() == [20, 0, 20, 5, 2]
    assert rig.result()["result"] == 5


def test_the_smallest_pause_is_1(make_rig):
    rig = make_rig(scripted(n=Entry(**LATE), five=Entry(), two=Entry(), one=Entry()))
    assert rig.run() == 0
    assert rig.result()["result"] == 1


def test_10_later_gives_todays_20(make_rig):
    rig = make_rig(scripted(n=Entry(**LATE), five=Entry(**LATE), ten=Entry(**LATE)))
    assert rig.run() == 0
    assert rig.pauses_run() == [20, 0, 20, 5, 10]
    assert rig.result()["result"] == 20


def test_later_by_p99_alone_counts(make_rig):
    rig = make_rig(scripted(n=Entry(p50=30.0, p99=86.0, rate=120.0), five=Entry(), two=Entry(), one=Entry()))
    assert rig.run() == 0
    assert rig.pauses_run() == [20, 0, 20, 5, 2, 1]  # N later, 5 on time: down to 1
    assert rig.result()["result"] == 1


def test_inconclusive_when_baselines_disagree(make_rig):
    rig = make_rig(scripted(b2=Entry(p50=33.0)))  # 3 ms apart, more than 2
    assert rig.run() == 0
    assert rig.pauses_run() == [20, 0, 20]  # no search
    result = rig.result()
    assert result["result"] == "inconclusive" and "baseline" in result["reason"]


def test_inconclusive_when_baselines_disagree_on_p99(make_rig):
    rig = make_rig(scripted(b2=Entry(p99=86.0)))  # 11 ms apart, more than 10
    assert rig.run() == 0
    assert rig.result()["result"] == "inconclusive"


def test_baselines_within_the_tolerances_are_valid(make_rig):
    rig = make_rig(scripted(b2=Entry(p50=32.0, p99=85.0)))  # 2 and 10 ms apart: the edge is inside
    assert rig.run() == 0
    assert rig.result()["result"] == 0


def test_inconclusive_when_the_capture_did_not_run_fast(make_rig):
    rig = make_rig(scripted(n=Entry(rate=89.0)))  # B1 is 45 a second; twice that is 90
    assert rig.run() == 0
    assert rig.pauses_run() == [20, 0, 20]
    result = rig.result()
    assert result["result"] == "inconclusive" and "pictures" in result["reason"]


def test_inconclusive_when_an_arm_fails_delivery(make_rig):
    rig = make_rig(scripted(b2=Entry(delivery="FAIL")))
    assert rig.run() == 0
    assert rig.result()["result"] == "inconclusive" and "Delivery" in rig.result()["reason"]


def test_inconclusive_when_a_search_arm_fails_delivery(make_rig):
    rig = make_rig(scripted(n=Entry(**LATE), five=Entry(delivery="FAIL")))
    assert rig.run() == 0
    assert rig.pauses_run() == [20, 0, 20, 5]
    assert rig.result()["result"] == "inconclusive"


def test_a_missed_click_stops_with_the_arms_so_far_kept(make_rig, capsys):
    rig = make_rig(scripted(n=Entry(exit_code=soak.EXIT_ABORT)))
    assert rig.run() == 1
    assert rig.pauses_run() == [20, 0]  # no third run_soak
    assert rig.answers == 1 and rig.window.terminated
    assert (rig.out / "1-pause20ms" / "arm.json").exists()
    assert not (rig.out / "2-pause0ms" / "arm.json").exists()
    result = rig.result()
    assert result["stopped"] == "arm 2: 1"
    assert "result" not in result and len(result["arms"]) == 1
    last = [line for line in capsys.readouterr().out.splitlines() if line.strip()][-1]
    assert "arm 2" in last and "kept" in last


def test_ctrl_c_outside_a_soak_stops_with_the_arms_so_far_kept(make_rig, capsys):
    """m5: a Ctrl+C in arm 2's analysis (after its soak ended) stops the check as a soak's own Ctrl+C would."""
    rig = make_rig(scripted(n=Entry(interrupt=True)))
    assert rig.run() == soak.EXIT_ABORT
    assert rig.pauses_run() == [20, 0]
    result = rig.result()
    assert result["stopped"] == f"arm 2: {soak.EXIT_INTERRUPTED}"
    assert "result" not in result and len(result["arms"]) == 1
    assert rig.pauses[-1] == 0.0 and rig.window.terminated  # the pause put back, the window closed
    last = [line for line in capsys.readouterr().out.splitlines() if line.strip()][-1]
    assert last.startswith("arm 2 stopped: Ctrl+C;") and "kept" in last


def test_ctrl_c_at_the_question_writes_nothing(make_rig):
    """The question is before any arm: nothing is written, so the same --out can be used again (review adjudication C)."""
    rig = make_rig(answer=KeyboardInterrupt)
    with pytest.raises(KeyboardInterrupt):
        rig.run()
    assert not rig.out.exists() and rig.calls == [] and rig.window.terminated


def test_the_console_click_line_comes_before_the_question(make_rig, ktc):
    """m1: Tk can give the hidden test window the keyboard as it starts, so the console asks for a click first."""
    rig = make_rig()
    assert rig.run() == 0
    assert rig.answers == 1 and ktc.CLICK_CONSOLE_TEXT + "\n" in rig.at_question


def test_each_arm_announces_itself(make_rig):
    rig = make_rig(scripted(n=Entry(**LATE), five=Entry(**LATE), ten=Entry()))
    assert rig.run() == 0
    assert [call.announced for call in rig.calls] == [1, 2, 3, 4, 5]  # one line before each run_soak
    for k, call in enumerate(rig.calls, start=1):
        line = (f">>> Arm {k} of up to 6 (pause {call.pause} ms) is starting. If the window titled "
                f"'{listener.SINK_TITLE}' does not come to the front by itself, click it now (mouse only, no keys). "
                f"The soak waits up to {soak.START_WAIT_S:g} s for it.")
        assert call.printed.endswith(line + "\n") or line in call.printed, line
        assert "\a" + line in call.printed  # with the bell


def test_every_arm_has_the_same_commands_and_seed_and_its_own_folder(make_rig):
    rig = make_rig(scripted(n=Entry(**LATE), five=Entry(**LATE), ten=Entry()))
    assert rig.run("--seed", "7") == 0
    cfgs = [call.cfg for call in rig.calls]
    assert {(c.commands, c.seed, c.short) for c in cfgs} == {(50, 7, True)}
    assert [c.out.name for c in cfgs] == ["1-pause20ms", "2-pause0ms", "3-pause20ms", "4-pause5ms", "5-pause10ms"]
    assert all(c.out.parent == rig.out for c in cfgs)


def test_the_pause_is_set_before_each_arm_and_restored(make_rig):
    rig = make_rig()
    assert rig.run() == 0
    assert rig.pauses == [20, 0, 20, 0.0]  # the arms' pauses, then the value that was there


def test_arm_json_has_the_figures(make_rig):
    rig = make_rig()
    assert rig.run() == 0
    first = json.loads((rig.out / "1-pause20ms" / "arm.json").read_text(encoding="utf-8"))
    assert first["pause_ms"] == 20 and first["pictures_per_s"] == 45.0
    assert first["delivery"] == "PASS" and first["residual_p50_ms"] == 30.0 and first["residual_p99_ms"] == 75.0
    assert first["window_alive"] is True and first["minimap_lost"] is False
    second = json.loads((rig.out / "2-pause0ms" / "arm.json").read_text(encoding="utf-8"))
    assert second["pause_ms"] == 0 and second["pictures_per_s"] == 120.0


# ---- the load must be there all through every arm (review I1) ----------------------------------------------------------

def test_inconclusive_when_the_window_dies_in_a_search_arm(make_rig):
    rig = make_rig(scripted(n=Entry(**LATE), five=Entry(window_dies=True)))  # 5 on time, but without its load
    assert rig.run() == 0
    assert rig.pauses_run() == [20, 0, 20, 5]  # not on to 2: the search stops
    result = rig.result()
    assert result["result"] == "inconclusive" and "arm 4" in result["reason"] and "window" in result["reason"]
    assert result["arms"][3]["window_alive"] is False and result["arms"][2]["window_alive"] is True


def test_inconclusive_when_the_minimap_is_lost_for_a_while_in_a_search_arm(make_rig):
    rig = make_rig(scripted(n=Entry(**LATE), five=Entry(lost=True)))  # lost and found again inside the arm
    assert rig.run() == 0
    assert rig.pauses_run() == [20, 0, 20, 5]
    result = rig.result()
    assert result["result"] == "inconclusive" and "arm 4" in result["reason"] and "minimap" in result["reason"]
    arm4 = json.loads((rig.out / "4-pause5ms" / "arm.json").read_text(encoding="utf-8"))
    assert arm4["minimap_lost"] is True


def test_inconclusive_with_no_search_when_n_loses_the_minimap(make_rig):
    rig = make_rig(scripted(n=Entry(rate=120.0, lost=True)))  # fast enough, on time, but not all of it under load
    assert rig.run() == 0
    assert rig.pauses_run() == [20, 0, 20]
    result = rig.result()
    assert result["result"] == "inconclusive" and "arm 2" in result["reason"] and "minimap" in result["reason"]


def test_inconclusive_when_the_window_dies_in_b2(make_rig):
    rig = make_rig(scripted(b2=Entry(window_dies=True)))
    assert rig.run() == 0
    result = rig.result()
    assert result["result"] == "inconclusive" and "arm 3" in result["reason"] and "window" in result["reason"]


def test_the_frame_notes_a_lost_minimap_until_the_next_reset(ktc):
    holder = ktc.CountingFrame()
    assert holder.minimap_lost is False and holder.was_lost is False
    holder.minimap_lost = True
    holder.minimap_lost = False
    assert holder.was_lost  # lost at any point since the reset, though found again
    holder.reset()
    assert not holder.was_lost
    holder.minimap_lost = True
    holder.reset()
    assert holder.was_lost  # still lost when the arm starts: the arm has no load from its start


def test_the_picture_counter_counts_pictures_and_resets(ktc):
    holder = ktc.CountingFrame()
    assert holder.pictures == 0 and holder.minimap_frame is None
    frame = np.zeros((2, 2, 3), np.uint8)
    holder.minimap_frame = frame
    holder.minimap_frame = frame.copy()
    assert holder.pictures == 2 and holder.minimap_frame is not None
    holder.reset()
    assert holder.pictures == 0 and holder.minimap_frame is not None  # the last picture stays, only the count restarts


# ---- rule details the review's mutants found unpinned (m2) ----------------------------------------------------------

@pytest.mark.parametrize("b1, b2", [(Entry(p50=32.0), Entry(p50=30.0)), (Entry(p50=30.0), Entry(p50=32.0))])
def test_the_p50_bar_is_the_larger_baseline(make_rig, b1, b2):
    """N at 33.5 is within 2 ms of the larger baseline (32) and more than 2 above the smaller (30)."""
    rig = make_rig(scripted(b1=b1, b2=b2, n=Entry(p50=33.5, rate=120.0)))
    assert rig.run() == 0
    assert rig.pauses_run() == [20, 0, 20] and rig.result()["result"] == 0


@pytest.mark.parametrize("b1, b2", [(Entry(p99=85.0), Entry(p99=75.0)), (Entry(p99=75.0), Entry(p99=85.0))])
def test_the_p99_bar_is_the_larger_baseline(make_rig, b1, b2):
    """N at 94 is within 10 ms of the larger baseline (85) and more than 10 above the smaller (75)."""
    rig = make_rig(scripted(b1=b1, b2=b2, n=Entry(p99=94.0, rate=120.0)))
    assert rig.run() == 0
    assert rig.pauses_run() == [20, 0, 20] and rig.result()["result"] == 0


@pytest.mark.parametrize("n", [Entry(**LATE, delivery="FAIL"), Entry(**LATE, alive=False)])
def test_inconclusive_with_no_search_when_n_is_not_usable(make_rig, n):
    rig = make_rig(scripted(n=n))  # late, so a check that skipped N would search
    assert rig.run() == 0
    assert rig.pauses_run() == [20, 0, 20]
    result = rig.result()
    assert result["result"] == "inconclusive" and "arm 2" in result["reason"]


def test_a_capture_exactly_twice_as_fast_is_fast_enough(make_rig):
    rig = make_rig(scripted(n=Entry(rate=90.0)))  # B1 is 45 a second; twice that is the edge, inside
    assert rig.run() == 0
    assert rig.result()["result"] == 0


# ---- the rule for "later" ------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("p50, p99, later", [
    (32.0, 85.0, False),     # exactly 2 ms and 10 ms above: not later
    (32.01, 85.0, True),
    (32.0, 85.01, True),
    (20.0, 60.0, False),
])
def test_keys_later_rule(ktc, p50, p99, later):
    assert ktc.keys_later(p50, p99, 30.0, 75.0) is later


def test_an_arm_that_failed_delivery_or_lost_the_listener_is_not_usable(ktc):
    assert ktc.ArmFigures("PASS", True, 30.0, 75.0, 10).usable
    assert not ktc.ArmFigures("FAIL", True, 30.0, 75.0, 10).usable
    assert not ktc.ArmFigures("PASS", False, 30.0, 75.0, 10).usable
    assert not ktc.ArmFigures("PASS", True, None, None, 0).usable


# ---- the analysis of one arm -----------------------------------------------------------------------------------------------

def _make_run_dir(path: Path) -> Path:
    """Eight tap-E commands, one a second. R is 150 ms; the residual of command i is 20 + i ms, except command 3, whose
    wait was clamped (waited_ms 0) and whose key-down is 400 ms late: both rules must leave it out."""
    path.mkdir(parents=True)
    vk, mask = analyze.VK_BY_KEY[Key.E], int(Key.E)
    commands, keys = [], []
    for i in range(8):
        stimulus = (i + 1) * 1_000_000_000
        sent = stimulus + 150_000_000
        late_ms = 400 if i == 3 else 20 + i
        down = sent + late_ms * 1_000_000
        commands.append({"seq": i, "action": "tap", "intended_ms": None, "stimulus_ns": stimulus,
                         "steps": [[0, mask], [800, 0]], "reaction_ms": 150.0,
                         "waited_ms": 0.0 if i == 3 else 150.0, "sent_ns": sent, "written_ns": sent + 1,
                         "accepted_ns": sent + 2, "done_ns": down + 80_000_000, "status": "OK", "error": None})
        keys.append({"qpc_ns": down, "vk": vk, "down": True, "repeat": False, "injected": False})
        keys.append({"qpc_ns": down + 80_000_000, "vk": vk, "down": False, "repeat": False, "injected": False})
    for name, rows in (("commands.jsonl", commands), ("keys.jsonl", keys)):
        (path / name).write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return path


def test_the_residual_is_realisms_rule(ktc, tmp_path):
    run_dir = _make_run_dir(tmp_path / "arm")
    figures = ktc.measure_arm(run_dir)
    dm = analyze.match_delivery(analyze.load_run(run_dir))
    summary = realism.reaction_residual(dm).summary
    assert figures.p50_ms == 24.0 and figures.p99_ms == 27.0 and figures.n == 7  # nearest rank over 20,21,22,24..27
    assert summary.startswith(f"p50 {figures.p50_ms:.3f} ms p99 {figures.p99_ms:.3f} ms n {figures.n}, clamped 1 excluded")
    assert figures.delivery == "PASS" and figures.listener_alive and figures.usable


def test_an_arm_with_no_residuals_has_none(ktc, tmp_path):
    run_dir = tmp_path / "empty"
    run_dir.mkdir()
    for name in ("commands.jsonl", "keys.jsonl"):
        (run_dir / name).write_text("", encoding="utf-8")
    figures = ktc.measure_arm(run_dir)
    assert figures.p50_ms is None and figures.p99_ms is None and figures.n == 0 and not figures.usable


# ---- the capture pause ---------------------------------------------------------------------------------------------------------

def test_the_pause_reaches_the_capture_loop(ktc, monkeypatch):
    vision_mod = pytest.importorskip("src.vision.vision")  # needs Windows and bettercam
    monkeypatch.setattr(vision_mod, "THREAD_SLEEP_DURATION_S", 0.0)
    assert ktc._set_capture_pause(5) == 0.0
    assert vision_mod.THREAD_SLEEP_DURATION_S == 0.005
    assert ktc._set_capture_pause(0.0) == pytest.approx(5.0)
    assert vision_mod.THREAD_SLEEP_DURATION_S == 0.0


# ---- the test window --------------------------------------------------------------------------------------------------------------

def test_load_picture_changes_every_redraw_and_is_never_black(clw):
    height, width = clw.PICTURE_H, clw.PICTURE_W
    pictures = [clw.load_picture(t) for t in range(400)]
    assert all(p.shape == (height, width, 3) and p.dtype == np.uint8 for p in pictures)
    assert all(not np.array_equal(a, b) for a, b in zip(pictures, pictures[1:]))
    assert all(int(p.max()) > 0 and float((p == 0).all(axis=2).mean()) < 0.5 for p in pictures)
    assert clw.load_picture(5) is not clw.load_picture(5)  # a fresh array each call: nobody can change the base picture
    assert np.array_equal(clw.load_picture(5), clw.load_picture(5))


def test_the_dot_moves_one_pixel_per_redraw(clw):
    def dot_x(t):
        picture = clw.load_picture(t)
        cols = np.nonzero((picture[:, :, 0] == 255) & (picture[:, :, 2] == 0))[1]
        return int(cols.min())
    assert [dot_x(t + 1) - dot_x(t) for t in range(10)] == [1] * 10


def test_load_picture_is_found_by_the_capture(ktc, clw, monkeypatch, capsys):
    """The real capture loop (`capture_and_process_minimap`), with the grab replaced by a screen that shows
    `load_picture(t)` and moves t on at every grab: it finds the minimap with one search, follows it (the follow check
    `still_there` holds from one picture to the next, or the loop would search again), and publishes a picture per
    grab that is never black."""
    vision_mod = pytest.importorskip("src.vision.vision")  # needs Windows and bettercam
    screen_h, screen_w, x0, y0 = 300, 480, 60, 40
    rng = np.random.default_rng(1)
    background = np.repeat(rng.integers(10, 120, (screen_h, screen_w, 1), dtype=np.uint8), 3, axis=2)
    state = {"t": 0, "grabs": 0, "whole_screen": []}

    class Stop(BaseException):
        pass

    def grab(_camera, region=None):
        state["grabs"] += 1
        if state["grabs"] > 40:
            raise Stop
        state["t"] += 1
        screen = background.copy()
        picture = clw.load_picture(state["t"])
        screen[y0:y0 + picture.shape[0], x0:x0 + picture.shape[1]] = picture[:, :, ::-1]  # RGB to the camera's BGR
        if region is None:
            state["whole_screen"].append(state["grabs"])
            return screen
        return screen[region[1]:region[3], region[0]:region[2]]

    stamps = itertools.count(1)
    monkeypatch.setattr(vision_mod.bettercam, "create", lambda **_kw: object())
    monkeypatch.setattr(vision_mod.vision, "cameraGrab", grab)
    monkeypatch.setattr(vision_mod, "_grab_stamped", lambda camera, region: (grab(camera, region), next(stamps)))
    monkeypatch.setattr(vision_mod.time, "sleep", lambda _s: None)
    holder = ktc.CountingFrame()
    holder.minimap_lost = False
    with pytest.raises(Stop):
        vision_mod.capture_and_process_minimap(holder)
    lines = [line.strip() for line in capsys.readouterr().out.splitlines()]
    assert state["whole_screen"] == [1]  # one search at the start, none after: the lock held from t to t + 1
    assert holder.pictures == 39 and not holder.minimap_lost
    assert not [line for line in lines if line.startswith("Minimap")]  # never "not found" or "lost"


def test_the_window_position_default_and_at(clw):
    assert clw.parse_at("3840,0") == (3840, 0)
    assert clw.parse_at(" -1920 , 120 ") == (-1920, 120)
    for bad in ("3840", "a,b", "1,2,3", ""):
        with pytest.raises(Exception):
            clw.parse_at(bad)
    assert clw.default_at(3840) == (3840, 0)  # the right of the primary screen, at the top


def test_no_activate_is_added_to_the_wrapper_window(clw):
    calls = []

    class User32:
        def GetParent(self, hwnd):
            calls.append(("parent", hwnd))
            return 77

        def GetWindowLongPtrW(self, hwnd, index):
            calls.append(("get", hwnd, index))
            return 0x88  # topmost and tool window, as Tk gives an override-redirect window

        def SetWindowLongPtrW(self, hwnd, index, value):
            calls.append(("set", hwnd, index, value))
            return 0x88

    clw.apply_no_activate(User32(), 5)
    assert ("set", 77, -20, 0x88 | 0x08000000) in calls  # GWL_EXSTYLE of the parent, with WS_EX_NOACTIVATE added
    assert not [c for c in calls if c[0] == "set" and c[1] == 5]


def test_nothing_rebuilds_the_wrapper_after_the_style_is_added(clw):
    """m3: Tk keeps its own copy of the wrapper's ex-style; a wrapper rebuild (a later -toolwindow, -fullscreen or
    overrideredirect change) drops WS_EX_NOACTIVATE silently (tkWinWm.c:3245). So no wm attribute, overrideredirect or
    raw `wm` call comes after `apply_no_activate` in `run_window`."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(clw.run_window)))
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
    applied = [c.lineno for c in calls if isinstance(c.func, ast.Name) and c.func.id == "apply_no_activate"]
    assert len(applied) == 1

    def wrapper_call(c):
        if isinstance(c.func, ast.Attribute) and c.func.attr in {"attributes", "wm_attributes", "overrideredirect",
                                                                 "wm_overrideredirect"}:
            return True
        return bool(c.args) and isinstance(c.args[0], ast.Constant) and c.args[0].value == "wm"

    before = [c.lineno for c in calls if wrapper_call(c) and c.lineno < applied[0]]
    after = [c.lineno for c in calls if wrapper_call(c) and c.lineno > applied[0]]
    assert before and after == []  # the scan sees the calls that set the window up, and none after the style


def test_only_the_key_timing_check_hands_soak_an_answer():
    """m4: `answered=` skips soak's question. Only `tools/key_timing_check.py` may pass it (the text `maple_check` gave
    it in the same function, pinned by test_asks_once_for_all_arms), and only soak.py names the accepted text. Every .py
    under src/ and tools/ is scanned for a call with an `answered` keyword, the name `ANSWERED_Y`, or that text."""
    problems = []
    for path in sorted([*(REPO / "src").rglob("*.py"), *(REPO / "tools").rglob("*.py")]):
        rel = path.relative_to(REPO).as_posix()
        if rel == "src/input/harness/soak.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (isinstance(node, ast.Call) and any(kw.arg == "answered" for kw in node.keywords)
                    and rel != "tools/key_timing_check.py"):
                problems.append(f"{rel}:{node.lineno}: answered=")
            if ((isinstance(node, ast.Name) and node.id == "ANSWERED_Y")
                    or (isinstance(node, ast.Attribute) and node.attr == "ANSWERED_Y")
                    or (isinstance(node, ast.alias) and node.name == "ANSWERED_Y")):
                problems.append(f"{rel}: ANSWERED_Y")
            if isinstance(node, ast.Constant) and node.value == soak.ANSWERED_Y:
                problems.append(f"{rel}:{node.lineno}: the accepted text")
    assert problems == []


def test_the_window_file_and_the_check_name_no_banned_call():
    """The scan in tests/test_anti_cheat_names.py covers both files; this pins that they are in its scope."""
    sys.path.insert(0, str(REPO / "tests"))  # a flat import of the scan's module, as when pytest collects the whole tree
    try:
        anti = importlib.import_module("test_anti_cheat_names")
    finally:
        sys.path.remove(str(REPO / "tests"))
    names = {p.relative_to(REPO).as_posix() for p in anti.scanned_files(REPO)}
    assert {"tools/key_timing_check.py", "tools/capture_load_window.py"} <= names
    assert anti.banned_names([REPO / "tools" / "key_timing_check.py", REPO / "tools" / "capture_load_window.py"]) == []


@pytest.mark.slow
def test_the_window_runs_as_its_own_process_and_refuses_a_bad_at():
    """`--at` is checked before any window is made, so this opens nothing."""
    done = subprocess.run([sys.executable, str(REPO / "tools" / "capture_load_window.py"), "--at", "nope"],
                          capture_output=True, text=True, timeout=60)
    assert done.returncode == 2 and "--at" in done.stderr
