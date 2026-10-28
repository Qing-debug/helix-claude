"""Plan Task RV-10: the N1 driver tool `view_check`. Every test but 12 uses fakes (a `FakeWin` scripted by time on a
`FakeClock` whose `sleep` advances it, recorded `say` / `ask`, fake `start_viewer` / `wait`) and shows no window, sends
no input and starts no process. Tests that call `main` without `--dry-run` patch the platform seam `_on_windows`
(PF81 m16) so they run anywhere. Test 12 reads the real desktop (Windows only) and creates nothing."""

import ast
import contextlib
import json
import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest
from returns.result import Failure, Success

from src.route import walk
from src.route.harness import view_check
from src.route.harness.view_check import CLICKS, Check, Feeder, Win32, build_feed, run_checks
from src.route.step_log import STEPS_FILE, read_run

CONSOLE, VIEW, DIALOG, OTHER_VIEW = 100, 200, 300, 400
VIEWER_PID = 4321
STYLE_LIVE, STYLE_NORMAL = 0x08000000, 0x00040000
MAPLE_OK = (True, "no MapleStory process")
FIRST_SHOW = "first show: root.state() = normal"


@pytest.fixture(autouse=True)
def _fast_feed(monkeypatch):
    monkeypatch.setattr(view_check, "FEED_PERIOD_S", 0.02)  # real seconds; the clock of the checks is fake


@pytest.fixture(scope="module")
def template(tmp_path_factory):
    out = tmp_path_factory.mktemp("n1-template")
    return out, build_feed(out, 1).unwrap()


@pytest.fixture
def run_dir(template, tmp_path):
    out = tmp_path / "n1"
    shutil.copytree(template[0], out)
    return out, list(template[1])


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += seconds


class FakeWin:
    """The reads of `Win32`, scripted by the fake clock. `clicks[k]` is what Hong does at prompt k: "view",
    "none", "elsewhere_then_view", "view_takes_fg" (the view stays the foreground), "dialog_takes_fg" or
    "held_view_takes_fg" (the button is held 2 s and the view takes the foreground just after the release).
    `back`: "never" is a console that Hong does not click when asked. After `end` the extended style reads
    `end_style` (default: normal) from `style_delay` fake seconds on (live before)."""

    def __init__(self, clock: FakeClock, out: Path, *, clicks=("view",) * 4, title="changing", taskbar=True,
                 ctrl_c="deliver", alive=True, fg_moves=(), extra=None, gone_after=None, back="click",
                 end_style=None, style_delay=0.0) -> None:
        self.clock, self.out = clock, out
        self.clicks, self.title_mode, self.taskbar, self.ctrl_c, self.alive = list(clicks), title, taskbar, ctrl_c, alive
        self.fg_moves = list(fg_moves)  # (from, to, hwnd) in fake seconds
        self.extra = dict(extra or {})  # visible once the view is shown
        self.gone_after = dict(gone_after or {})  # hwnd -> time after which window_info gives (0, "", "")
        self.info = {CONSOLE: (1111, "console", "ConsoleWindowClass"), VIEW: (VIEWER_PID, "Route view", "TkTopLevel"),
                     DIALOG: (999, "Dialog", "#32770"), OTHER_VIEW: (VIEWER_PID, "Tk debug", "TkChild")}
        self.shown = False
        self.prompts = 0
        self.presses: list[tuple[float, float, int]] = []
        self.taken: tuple[float, int] | None = None
        self.back_at: float | None = None
        self.taskbar_at: float | None = None
        self.calls: list[str] = []
        self.back, self.end_style, self.style_delay = back, end_style, style_delay
        self.ended_seen: float | None = None
        self.prompt_log: list[tuple[float, int]] = []  # (time, foreground) at each click prompt

    def on_say(self, text: str) -> None:
        t = self.clock.t
        if text.startswith("Click this console"):
            self.back_at = None if self.back == "never" else t + 0.3
        elif text.startswith("The route view now has a taskbar button"):
            self.taskbar_at = t + 0.5 if self.taskbar else None
        elif text.startswith("Click "):
            plan = self.clicks[self.prompts]
            self.prompts += 1
            self.prompt_log.append((t, self.foreground()))
            if plan != "none":
                self.presses.append((t + (1.0 if plan == "elsewhere_then_view" else 0.3),
                                     t + {"elsewhere_then_view": 1.2, "held_view_takes_fg": 2.3}.get(plan, 0.5), VIEW))
            if plan == "elsewhere_then_view":
                self.presses.append((t + 0.3, t + 0.5, CONSOLE))
            if plan in ("view_takes_fg", "dialog_takes_fg", "held_view_takes_fg"):
                self.taken = (t + (2.4 if plan == "held_view_takes_fg" else 0.5),
                              DIALOG if plan == "dialog_takes_fg" else VIEW)
                self.back_at = None

    def foreground(self) -> int:
        t = self.clock.t
        for start, end, hwnd in self.fg_moves:
            if start <= t < end:
                return hwnd
        if self.taken and t >= self.taken[0] and (self.back_at is None or t < self.back_at):
            return self.taken[1]
        if self.taskbar_at is not None and t >= self.taskbar_at:
            return VIEW
        return CONSOLE

    def visible_windows(self):
        windows = {CONSOLE: self.info[CONSOLE]}
        if self.shown:
            windows[VIEW] = self.info[VIEW]
            windows.update(self.extra)
        return windows

    def title(self, hwnd: int) -> str:
        if hwnd != VIEW or not self.shown:
            return ""
        if self.title_mode == "changing":
            return f"Route view LIVE step {int(self.clock.t / view_check.FEED_PERIOD_S)}"
        if isinstance(self.title_mode, tuple):  # ("once", T)
            return "Route view A" if self.clock.t < self.title_mode[1] else "Route view B"
        return "Route view LIVE"

    def window_info(self, hwnd: int):
        if hwnd in self.gone_after and self.clock.t >= self.gone_after[hwnd]:
            return 0, "", ""
        return self.info.get(hwnd, (0, "", ""))

    def left_down(self) -> bool:
        return any(a <= self.clock.t < b for a, b, _ in self.presses)

    def left_down_on(self, hwnd: int) -> bool:
        return any(a <= self.clock.t < b and under == hwnd for a, b, under in self.presses)

    def ex_style(self, hwnd: int) -> int:
        ended = read_run(self.out).map(lambda run: run.ended).value_or(False)
        if not ended:
            return STYLE_LIVE
        if self.ended_seen is None:
            self.ended_seen = self.clock.t
        if self.clock.t - self.ended_seen < self.style_delay:
            return STYLE_LIVE
        return STYLE_NORMAL if self.end_style is None else self.end_style

    def process_alive(self, pid: int) -> bool:
        return self.alive

    def ctrl_c_own_console(self) -> None:
        if self.ctrl_c == "deliver":
            signal.raise_signal(signal.SIGINT)


def _fakes(win: FakeWin, out: Path, *, ready=True, start_fail=False, log=(FIRST_SHOW,), poll=lambda: None):
    proc = SimpleNamespace(pid=999, poll=poll)

    def start(folder: Path):
        if start_fail:
            return Failure("no python")
        (folder / "view.log").write_text("".join(line + "\n" for line in log), encoding="utf-8")
        return proc

    def wait(folder: Path, _proc) -> bool:
        if not ready:
            return False
        (folder / "view.ready").write_text(str(VIEWER_PID), encoding="ascii")
        win.shown = True
        return True

    return start, wait


def _tools(win: FakeWin, clock: FakeClock, answers, ask_advance: float = 0.0):
    said: list[str] = []
    asked: list[str] = []
    replies = list(answers)

    def say(text: str) -> None:
        said.append(text)
        win.on_say(text)

    def ask(prompt: str) -> str:
        asked.append(prompt)
        clock.t += ask_advance
        return replies.pop(0) if replies else ""

    return said, asked, say, ask


def drive(run_dir, *, answers=("n", ""), ask_advance=0.0, maple=lambda: MAPLE_OK, ready=True, start_fail=False,
          log=(FIRST_SHOW,), left=None, poll=lambda: None, **win_kw):
    """`left`: a function of the fake time giving what `Feeder.remaining` returns (None: the real one)."""
    out, lines = run_dir
    clock = FakeClock()
    win = FakeWin(clock, out, **win_kw)
    start, wait = _fakes(win, out, ready=ready, start_fail=start_fail, log=log, poll=poll)
    said, asked, say, ask = _tools(win, clock, answers, ask_advance)
    patched = mock.patch.object(Feeder, "remaining", lambda _self: left(clock.t)) if left else contextlib.nullcontext()
    with patched:
        checks, facts = run_checks(out, lines, win=win, start_viewer=start, wait=wait, ask=ask, say=say, clock=clock,
                                   sleep=clock.sleep, maple=maple)
    return SimpleNamespace(checks=checks, by={c.name: c for c in checks}, facts=facts, win=win, said=said, asked=asked,
                           out=out)


def drive_main(tmp_path, monkeypatch, template, extra=(), *, answers=("n", ""), ready=True, maple=None, **win_kw):
    data = tmp_path / "data"
    out = data / "route_view" / "n1"
    clock = FakeClock()
    win = FakeWin(clock, out, **win_kw)
    start, wait = _fakes(win, out, ready=ready)
    said, asked, say, ask = _tools(win, clock, answers)

    def build(folder: Path, laps: int):
        shutil.copytree(template[0], folder, dirs_exist_ok=True)
        return Success(list(template[1]))

    monkeypatch.setattr(view_check, "_DATA", data)
    monkeypatch.setattr(view_check, "build_feed", build)
    monkeypatch.setattr(view_check, "_on_windows", lambda: True)
    monkeypatch.setattr(view_check, "Win32", lambda: win)
    monkeypatch.setattr(view_check, "_maple_check", maple or (lambda: MAPLE_OK))
    monkeypatch.setattr(view_check, "_clock", clock)
    monkeypatch.setattr(view_check, "_sleep", clock.sleep)
    monkeypatch.setattr(view_check, "_say", say)
    monkeypatch.setattr(view_check, "_ask", ask)
    monkeypatch.setattr(walk, "_start_viewer", start)
    monkeypatch.setattr(walk, "_wait_for_viewer", wait)
    code = view_check.main(["run", "--out", str(out), *extra])
    return SimpleNamespace(code=code, out=out, win=win, said=said)


def _passed(result) -> list:
    return [c.passed for c in result.checks]


# 1 ------------------------------------------------------------------------------------------------------------------

def test_1_build_feed_copies_the_map_and_starts_steps_with_the_header_and_ten_events(template):
    out, lines = template
    source = out / "source"
    assert (out / "map.csv").read_bytes() == (source / "map.csv").read_bytes()
    raw = (source / STEPS_FILE).read_bytes().splitlines(keepends=True)
    assert (out / STEPS_FILE).read_bytes() == b"".join(raw[:11])
    assert lines == raw[11:] and lines[-1] == raw[-1] and b'"end"' in lines[-1]


def test_1_build_feed_fails_on_a_source_that_is_too_short(tmp_path, monkeypatch):
    monkeypatch.setattr(view_check, "FIRST_EVENTS", 100000)
    failed = build_feed(tmp_path / "n1", 1)
    assert isinstance(failed, Failure) and "too short" in failed.failure()


# 2 ------------------------------------------------------------------------------------------------------------------

def test_2_feeder_stops_and_write_end_completes_the_source_byte_for_byte(template, tmp_path):
    out, lines = template
    path = tmp_path / STEPS_FILE
    path.write_bytes((out / STEPS_FILE).read_bytes())
    feeder = Feeder(path, lines, 0.01)
    feeder.start()
    while feeder.fed < 3:
        pass
    feeder.stop()
    fed = feeder.fed
    assert 3 <= fed < len(lines) - 1 and feeder.remaining() == len(lines) - 1 - fed
    feeder.write_end()
    assert feeder.remaining() == 0
    assert path.read_bytes() == (out / "source" / STEPS_FILE).read_bytes()
    feeder.write_end()  # once only
    assert path.read_bytes() == (out / "source" / STEPS_FILE).read_bytes()


# 3 ------------------------------------------------------------------------------------------------------------------

def test_3_dry_run_feeds_everything_and_reads_it_back_without_win32(tmp_path, monkeypatch, capsys):
    data = tmp_path / "data"
    monkeypatch.setattr(view_check, "_DATA", data)

    def no_win32():
        raise AssertionError("Win32 was constructed in a dry run")

    monkeypatch.setattr(view_check, "Win32", no_win32)
    monkeypatch.setattr(view_check, "_maple_check", lambda: MAPLE_OK)
    assert view_check.main(["run", "--dry-run", "--laps", "1", "--out", str(data / "route_view" / "n1")]) == 0
    assert "events fed and read back equal" in capsys.readouterr().out


def test_3_usage_errors_exit_2_with_their_message_before_anything_is_built(tmp_path, monkeypatch, template, capsys):
    data = tmp_path / "data"
    monkeypatch.setattr(view_check, "_DATA", data)
    monkeypatch.setattr(view_check, "_maple_check", lambda: MAPLE_OK)

    def not_built(*_args):
        raise AssertionError("build_feed ran for a usage error")

    monkeypatch.setattr(view_check, "build_feed", not_built)  # an exit 2 must come from the check, not a failed build

    def run(*argv):
        code = view_check.main(list(argv))
        return code, capsys.readouterr().err

    used = data / "route_view" / "used"
    shutil.copytree(template[0], used)
    code, err = run("run", "--dry-run", "--out", str(tmp_path / "elsewhere"))
    assert code == 2 and "--out must be a folder inside" in err
    code, err = run("run", "--dry-run", "--out", str(used))  # already holds steps.jsonl and source
    assert code == 2 and "already exists" in err and "choose a new --out" in err
    code, err = run("run", "--dry-run", "--laps", "0", "--out", str(data / "route_view" / "n3"))
    assert code == 2 and "--laps must be at least 1, got 0" in err
    monkeypatch.setattr(view_check, "_on_windows", lambda: False)
    code, err = run("run", "--out", str(data / "route_view" / "n2"))  # not Windows, no --dry-run
    assert code == 2 and "run it on Windows" in err and not (data / "route_view" / "n2").exists()
    assert run("nonsense")[0] == 2


# 4 ------------------------------------------------------------------------------------------------------------------

def test_4_all_pass(tmp_path, monkeypatch, template, capsys):
    ran = drive_main(tmp_path, monkeypatch, template)
    record = json.loads((ran.out / "n1.json").read_text(encoding="utf-8"))
    assert ran.code == 0
    assert len(record["checks"]) == 12 and all(c["passed"] is True for c in record["checks"]), record["checks"]
    facts = record["facts"]
    assert (facts["fg0"], facts["view_hwnd"], facts["viewer_pid"], facts["redirector_pid"]) == (CONSOLE, VIEW, 4321, 999)
    assert facts["first_show_state"] == FIRST_SHOW and facts["focus_error"] is None and facts["lines_fed"] >= 0  # real time
    assert len(facts["title_changes"]) > 1 and facts["fg_changes"][-1]["hwnd"] == VIEW
    assert read_run(ran.out).unwrap().ended
    out = capsys.readouterr().out
    assert out.count("PASS") == 12 and "after --out" in out
    prompts = [t for t in ran.said if t.startswith("Click ")]
    assert len(prompts) == 4 and all(target in text for target, text in zip(CLICKS, prompts, strict=True))


# 5 ------------------------------------------------------------------------------------------------------------------

def test_5_a_click_that_leaves_the_foreground_on_the_view_fails_and_the_console_is_asked_back(tmp_path, monkeypatch,
                                                                                            template):
    ran = drive_main(tmp_path, monkeypatch, template, clicks=("view", "view_takes_fg", "view", "view"))
    record = json.loads((ran.out / "n1.json").read_text(encoding="utf-8"))
    passed = [c["passed"] for c in record["checks"]]
    assert ran.code == 1
    assert passed[5:9] == [True, False, True, True]
    assert "the route view took the foreground" in record["checks"][6]["detail"]
    prompts = [t for t in ran.said if t.startswith("Click ")]
    assert prompts.index("Click this console to bring it back to the front") == 2  # after the second, before the third
    assert len(prompts) == 5


# 6 ------------------------------------------------------------------------------------------------------------------

def test_6_no_click_in_time_is_not_done_and_a_click_elsewhere_is_counted(tmp_path, monkeypatch, template, capsys):
    ran = drive_main(tmp_path, monkeypatch, template, clicks=("none", "elsewhere_then_view", "view", "view"))
    record = json.loads((ran.out / "n1.json").read_text(encoding="utf-8"))
    first, second = record["checks"][5], record["checks"][6]
    assert ran.code == 1 and first["passed"] is None and "no click" in first["detail"]
    assert second["passed"] is True and "1 click(s) elsewhere ignored" in second["detail"]
    assert "NOT DONE" in capsys.readouterr().out


# 7 ------------------------------------------------------------------------------------------------------------------

def test_7_a_second_new_window_fails_no_console_window_and_so_does_the_answer_y(run_dir):
    console = (5555, r"C:\Python\python.exe", "ConsoleWindowClass")
    seen = drive(run_dir, extra={500: console})
    failed = seen.by["no console window"]
    assert failed.passed is False and r"C:\Python\python.exe" in failed.detail and "ConsoleWindowClass" in failed.detail
    answered = drive(run_dir, answers=("y", ""))
    assert answered.by["no console window"].passed is False and "answered y" in answered.by["no console window"].detail


# 8 ------------------------------------------------------------------------------------------------------------------

def test_8_a_viewer_that_is_not_ready_leaves_the_rest_not_done_and_end_is_written(tmp_path, monkeypatch, template):
    ran = drive_main(tmp_path, monkeypatch, template, ready=False)
    record = json.loads((ran.out / "n1.json").read_text(encoding="utf-8"))
    passed = [c["passed"] for c in record["checks"]]
    assert ran.code == 1 and passed[:2] == [True, False] and passed[2:] == [None] * 10
    assert "stays hidden until end" in record["checks"][5]["detail"]
    assert read_run(ran.out).unwrap().ended


def test_8_a_viewer_that_cannot_start_is_a_failed_check(run_dir):
    seen = drive(run_dir, start_fail=True)
    assert _passed(seen)[0] is False and seen.by["viewer started"].detail == "no python"
    assert all(c.passed is None for c in seen.checks[1:]) and len(seen.checks) == 12


# 9 ------------------------------------------------------------------------------------------------------------------

def test_9_maplestory_running_before_anything_exits_3_and_writes_nothing(tmp_path, monkeypatch, template):
    ran = drive_main(tmp_path, monkeypatch, template, maple=lambda: (False, "MapleStory running: ['MapleStory.exe']"))
    assert ran.code == 3 and not ran.out.exists()


def test_9_maplestory_found_before_the_second_click_stops_writes_end_and_exits_3(tmp_path, monkeypatch, template):
    calls = []

    def maple():
        calls.append(1)
        return MAPLE_OK if len(calls) < 3 else (False, "MapleStory running: ['MapleStory.exe']")

    ran = drive_main(tmp_path, monkeypatch, template, maple=maple)
    record = json.loads((ran.out / "n1.json").read_text(encoding="utf-8"))
    assert ran.code == 3 and len(calls) == 3 and read_run(ran.out).unwrap().ended
    assert "MapleStory" in record["facts"]["stopped_by_maple"]
    assert [c["passed"] for c in record["checks"][5:]] == [True] + [None] * 6
    assert sum(1 for t in ran.said if t.startswith("Click ")) == 1


# 10 -----------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize(("mode", "alive", "passed", "words"), [
    ("deliver", True, True, "still running"), ("deliver", False, False, "ended"),
    ("none", True, False, "the Ctrl+C was not delivered")])
def test_10_ctrl_c_in_the_parents_console(run_dir, mode, alive, passed, words):
    before = signal.getsignal(signal.SIGINT)
    seen = drive(run_dir, ctrl_c=mode, alive=alive)
    check = seen.by["Ctrl+C in the parent's console did not reach the viewer"]
    assert check.passed is passed and words in check.detail
    assert signal.getsignal(signal.SIGINT) is before


# 11 -----------------------------------------------------------------------------------------------------------------

def test_11_the_tool_never_sends_input():
    forbidden = {"SendInput", "keybd_event", "mouse_event", "SetCursorPos", "SetForegroundWindow", "SetActiveWindow",
                 "SetFocus", "BringWindowToTop", "PostMessageW", "PostMessageA", "SendMessageW", "SendMessageA",
                 "BlockInput", "BleLink", "InputLayer", "execute"}
    tree = ast.parse(Path(view_check.__file__).read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.alias):
            names.add(node.name.split(".")[-1])
    assert names & forbidden == set()


# 12 -----------------------------------------------------------------------------------------------------------------

@pytest.mark.slow  # starts a subprocess (hard rule 9)
@pytest.mark.skipif(sys.platform != "win32", reason="reads the Windows desktop")
def test_12_the_real_reads(tmp_path):
    win = Win32()
    assert win.process_alive(os.getpid()) is True
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    assert win.process_alive(child.pid) is False
    assert isinstance(win.foreground(), int)
    windows = win.visible_windows()
    assert isinstance(windows, dict) and all(
        isinstance(k, int) and isinstance(v[0], int) and isinstance(v[1], str) and isinstance(v[2], str)
        for k, v in windows.items())
    if windows:
        hwnd, (pid, _title, cls) = next(iter(windows.items()))
        assert isinstance(win.title(hwnd), str)
        info = win.window_info(hwnd)
        assert (info[0], info[2]) == (pid, cls) or info == (0, "", "")  # the window may have closed between reads
    assert win.window_info(0) == (0, "", "")
    assert isinstance(win.left_down(), bool) and win.left_down_on(0) is False
    assert isinstance(win.ex_style(win.foreground()), int)


# 13 -----------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize(("alive", "shown", "code"), [(True, True, 0), (False, True, 1), (True, False, 1)])
def test_13_after(tmp_path, monkeypatch, alive, shown, code):
    (tmp_path / "n1.json").write_text(json.dumps({"checks": [], "facts": {"viewer_pid": 4321}}), encoding="utf-8")
    fake = SimpleNamespace(process_alive=lambda pid: alive and pid == 4321,
                           visible_windows=lambda: {VIEW: (4321, "Route view", "TkTopLevel")} if shown else {})
    monkeypatch.setattr(view_check, "_on_windows", lambda: True)
    monkeypatch.setattr(view_check, "Win32", lambda: fake)
    assert view_check.main(["after", "--out", str(tmp_path)]) == code
    record = json.loads((tmp_path / "n1.json").read_text(encoding="utf-8"))
    assert record["after"]["passed"] is (code == 0) and record["facts"] == {"viewer_pid": 4321}


def test_13_after_without_n1_json_exits_2(tmp_path, monkeypatch):
    monkeypatch.setattr(view_check, "_on_windows", lambda: True)
    assert view_check.main(["after", "--out", str(tmp_path)]) == 2


# 14 -----------------------------------------------------------------------------------------------------------------

def test_14_a_title_that_never_changes_while_fed_is_false_with_both_titles(run_dir):
    seen = drive(run_dir, title="constant", log=("first show: root.state() = withdrawn",))
    check = seen.by["the view updates while live"]
    assert check.passed is False and check.detail.startswith("no title change while fed")
    assert check.detail.count("Route view LIVE") == 2
    assert seen.facts["first_show_state"] == "first show: root.state() = withdrawn"


def test_14_a_changing_title_with_the_answer_n_is_false_answered_n(run_dir):
    seen = drive(run_dir, answers=("n", "n"))
    check = seen.by["the view updates while live"]
    assert check.passed is False and check.detail.startswith("answered n")
    assert seen.facts["first_show_state"] == FIRST_SHOW


def test_14_no_change_and_the_answer_n_is_no_title_change_while_fed(run_dir):
    seen = drive(run_dir, title="constant", answers=("n", "n"))
    assert seen.by["the view updates while live"].detail.startswith("no title change while fed")


def test_14_no_first_show_line_in_the_log_is_none_and_said(run_dir):
    seen = drive(run_dir, log=())
    assert seen.facts["first_show_state"] is None and seen.facts["focus_error"] is None
    assert "no first-show line" in seen.by["the view updates while live"].detail


def test_14_a_title_that_changes_once_during_a_slow_answer_is_true(run_dir):
    seen = drive(run_dir, title=("once", 5.0), ask_advance=30.0)
    assert seen.by["the view updates while live"].passed is True


def test_14_a_feed_that_ran_out_is_not_done_never_false(run_dir, monkeypatch):
    monkeypatch.setattr(Feeder, "remaining", lambda self: 0)
    seen = drive(run_dir, title="constant")
    check = seen.by["the view updates while live"]
    assert check.passed is None and "the feed finished" in check.detail


def test_14_a_feed_that_ran_out_only_after_the_questions_is_judged_at_the_end_of_the_wait(run_dir):
    # RV-10 review m2: the feed had 5 lines left when the wait ended and 0 after Hong's slow answers (plan step 3:
    # "before the wait ended"), so a view whose title never changed is False, not NOT DONE.
    seen = drive(run_dir, title="constant", ask_advance=30.0, left=lambda t: 5 if t < 40.0 else 0)
    check = seen.by["the view updates while live"]
    assert check.passed is False and check.detail.startswith("no title change while fed")


def test_14_lines_fed_is_what_the_thread_fed_not_the_lines_write_end_added(run_dir, monkeypatch):
    # RV-10 review m1: with a period no test waits for, the thread feeds nothing; `write_end` then appends them all.
    monkeypatch.setattr(view_check, "FEED_PERIOD_S", 1000.0)
    seen = drive(run_dir)
    assert seen.facts["lines_fed"] == 0 and read_run(seen.out).unwrap().ended


def test_14_lines_fed_is_also_recorded_when_the_viewer_was_not_ready(run_dir, monkeypatch):
    monkeypatch.setattr(view_check, "FEED_PERIOD_S", 1000.0)
    seen = drive(run_dir, ready=False)
    assert seen.facts["lines_fed"] == 0 and read_run(seen.out).unwrap().ended


def test_14_a_focus_call_failure_in_the_log_is_copied_into_the_facts(run_dir):
    seen = drive(run_dir, log=(FIRST_SHOW, "focus call failed: no wrapper yet"))
    assert seen.facts["focus_error"] == "focus call failed: no wrapper yet"


# 15 -----------------------------------------------------------------------------------------------------------------

def test_15_a_dialog_of_another_program_during_the_first_show_is_not_done_not_failed(run_dir):
    seen = drive(run_dir, fg_moves=[(0.3, 0.5, DIALOG)])
    check = seen.by["first show kept the foreground"]
    assert check.passed is None and "Dialog" in check.detail and "#32770" in check.detail and "pid 999" in check.detail
    assert "ConsoleWindowClass" not in check.detail  # the return to the console is not another window
    assert seen.by["no console window"].passed is True


def test_15_a_dialog_that_closed_again_is_still_named_from_the_change(run_dir):
    seen = drive(run_dir, fg_moves=[(0.3, 0.5, DIALOG)], gone_after={DIALOG: 0.6})
    check = seen.by["first show kept the foreground"]
    assert check.passed is None and "'Dialog' (class #32770, pid 999)" in check.detail
    assert seen.by["no console window"].passed is True


def test_15_a_return_to_the_console_inside_a_click_span_is_not_another_window(run_dir):
    seen = drive(run_dir, fg_moves=[(0.3, 1.5, DIALOG)])  # the dialog goes away while the first click is awaited
    assert seen.by["first show kept the foreground"].passed is None
    assert seen.by[f"click on {CLICKS[0]} kept the foreground"].passed is True


def test_15_another_window_of_the_viewers_process_fails_it(run_dir):
    seen = drive(run_dir, fg_moves=[(0.3, 0.5, OTHER_VIEW)])
    check = seen.by["first show kept the foreground"]
    assert check.passed is False and "Tk debug" in check.detail


def test_15_the_view_taking_the_foreground_and_giving_it_back_still_fails_it(run_dir):
    seen = drive(run_dir, fg_moves=[(0.3, 0.5, VIEW)])
    assert seen.by["first show kept the foreground"].passed is False


def test_15_a_click_span_with_only_another_programs_window_is_none_and_the_console_is_asked_back(run_dir):
    seen = drive(run_dir, clicks=("view", "dialog_takes_fg", "view", "view"))
    check = seen.by[f"click on {CLICKS[1]} kept the foreground"]
    assert check.passed is None and "Dialog" in check.detail
    assert "Click this console to bring it back to the front" in seen.said
    assert seen.by[f"click on {CLICKS[2]} kept the foreground"].passed is True


# RV-10 review m4: tests for the mutants that survived ------------------------------------------------------------------

def test_5_a_foreground_still_on_the_view_from_an_earlier_click_fails_the_next_ones_and_the_wait_is_bounded(run_dir):
    seen = drive(run_dir, clicks=("view", "view_takes_fg", "view", "view"), back="never")
    for target in CLICKS[1:]:
        check = seen.by[f"click on {target} kept the foreground"]
        assert check.passed is False and "the route view took the foreground" in check.detail
    times = [t for t, _fg in seen.win.prompt_log]
    assert times[2] - times[1] >= view_check.CLICK_WAIT_S  # the console was never clicked: the wait ran its full length


def test_5_the_next_prompt_waits_until_the_console_is_the_foreground_again(run_dir):
    seen = drive(run_dir, clicks=("view", "view_takes_fg", "view", "view"))
    assert [fg for _t, fg in seen.win.prompt_log] == [CONSOLE] * 4
    times = [t for t, _fg in seen.win.prompt_log]
    assert times[2] - times[1] < view_check.CLICK_WAIT_S


def test_6_a_click_held_down_is_judged_after_the_button_is_up(run_dir):
    seen = drive(run_dir, clicks=("view", "held_view_takes_fg", "view", "view"))
    check = seen.by[f"click on {CLICKS[1]} kept the foreground"]
    assert check.passed is False and "the route view took the foreground" in check.detail


def test_10_a_viewer_whose_process_has_exited_fails_the_ctrl_c_check(run_dir):
    seen = drive(run_dir, poll=lambda: 1)
    check = seen.by["Ctrl+C in the parent's console did not reach the viewer"]
    assert check.passed is False and "ended" in check.detail


def test_14_a_title_that_changes_inside_the_update_wait_is_true(run_dir):
    seen = drive(run_dir, title=("once", 2.5), left=lambda _t: 5)  # the change comes 1.5 s into the 3 s wait
    assert seen.by["the view updates while live"].passed is True


@pytest.mark.parametrize("end_style", [0x0, STYLE_LIVE, STYLE_LIVE | STYLE_NORMAL])
def test_16_the_style_check_needs_the_appwindow_bit_and_no_noactivate_bit(run_dir, end_style):
    seen = drive(run_dir, end_style=end_style)
    check = seen.by["end clears the no-activate style"]
    assert check.passed is False and check.detail == f"extended style {end_style:#x}"


def test_16_the_style_is_polled_until_the_viewer_has_changed_it(run_dir):
    seen = drive(run_dir, style_delay=1.5)
    check = seen.by["end clears the no-activate style"]
    assert check.passed is True and check.detail == f"extended style {STYLE_NORMAL:#x}"


def test_16_no_taskbar_click_in_time_is_not_done(run_dir):
    seen = drive(run_dir, taskbar=False)
    check = seen.by["a taskbar click activates it after end"]
    assert check.passed is None and "no taskbar click within 30 s" in check.detail


def test_check_is_a_plain_record():
    assert Check("a", None, "b") == Check("a", None, "b")
