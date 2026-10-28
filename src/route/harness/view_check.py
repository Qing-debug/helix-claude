"""N1 driver tool for the route view's no-key check (route view plan task RV-10; spec 5.7, 5.9 steps 2-3).

    python -m src.route.harness.view_check run [--out data/route_view/n1-<UTC stamp>] [--laps 3] [--dry-run]
    python -m src.route.harness.view_check after --out <the run's folder>

What it checks (spec 5.7): that the live route view never takes the foreground from the game. It starts the real
viewer exactly as a walk does (`walk._start_viewer`, `walk._wait_for_viewer`), feeds it a simulator run line by line,
and reads the foreground window while Hong clicks the view: the first show, a click on the map, on the text of the
"why" log, on its scrollbar and on the title bar, a Ctrl+C in this console, the end of the run (the no-activate style
goes, a taskbar click activates the window). `after` is run once this tool has exited and checks that the viewer
outlived its parent.

It never sends a key, a mouse event or any other input, never sets the foreground, and needs no board: every Win32
call is a read except `GenerateConsoleCtrlEvent`, which sends one Ctrl+C to this console (no key). Run it only with
MapleStory closed (the check is repeated before every click) and with Hong, or an agent on an idle PC (H15's rule),
doing the clicks. Start it with the venv's `python.exe`, so that its parent is what a walk's is (spec 5.9's job-object
note).

Run it in a console window of its own, started by hand and used for nothing else: its Ctrl+C check sends CTRL_C_EVENT
to process group 0, which reaches every process attached to this console, and a process without a handler exits. So
never run it through a pipe or `Tee-Object`, a `.bat` wrapper, an agent's shell tool, or the console of a walk (the
`tee`, the wrapper, the shell or the walk would end, and the tool's next print would fail). The viewer is not on that
console (`CREATE_NO_WINDOW`, `walk._start_viewer`): that is what the check shows. The tool does not test for this.

Exit codes: 0 every check passed · 1 a check failed or was not done · 2 usage, an `--out` outside `data/` or already
used, (without `--dry-run`) not Windows, or standard input closed so that its two questions cannot be asked (`n1.json`
is still written, with the checks not made as "not done") · 3 MapleStory is running, or the process list cannot be read.

Win32 references (Microsoft Learn, learn.microsoft.com/en-us/windows/win32/...): inputdev/nf-winuser-getasynckeystate
(`VK_LBUTTON` 0x01, the high bit is "down now"), winmsg/nf-winuser-getforegroundwindow, -enumwindows,
-iswindowvisible, -getwindowtextw, -getclassnamew, -getwindowthreadprocessid, -getcursorpos, -windowfrompoint
("Retrieves a handle to the window that contains the specified point"), -getancestor (`GA_ROOT` = 2),
-getwindowlongptrw (`GWL_EXSTYLE` = -20; `WS_EX_NOACTIVATE` 0x08000000 and `WS_EX_APPWINDOW` 0x00040000 as in
`src/route_view/win_focus.py`), processthreadsapi/nf-processthreadsapi-openprocess
(`PROCESS_QUERY_LIMITED_INFORMATION` 0x1000), -getexitcodeprocess (`STILL_ACTIVE` 259), handleapi/nf-handleapi-closehandle,
and consoleapi/nf-consoleapi-generateconsolectrlevent (`CTRL_C_EVENT` 0; process group 0 is every process attached to
the calling process's console). That a point on the title bar gives the top-level wrapper itself is inferred, not
documented: the fourth click of H-1 tests it.

Where the plan is silent (each local and reversible):
- Seams the tests patch: `Win32`, `_maple_check`, `_on_windows`, `_clock`, `_sleep`, `_say`, `_ask` and `walk._start_viewer`
  / `walk._wait_for_viewer`; `main` looks each up at call time (RV-10 / PF81 m16: a platform seam, not `sys.platform`).
- The watch thread (`n1-watch`) is helped by a synchronous `sample()` in every wait and after each question, so a run
  on a fake clock sees the same changes a real one does. The title watch's first title is the baseline, not a change.
- A MapleStory check that fails mid-run stops the feed, writes `end`, writes `n1.json` and exits 3; the checks not yet
  made are `None` ("not done") with that reason.
- `after` needs `n1.json` (exit 2 without it) and, like `run`, Windows; it does not apply the `data/` rule.
"""

import argparse
import csv
import ctypes
import json
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from returns.pipeline import is_successful
from returns.result import Failure, Result, Success

from src.route import runtime, walk
from src.route.sim.run import run_sim
from src.route.step_log import MAP_COPY, STEPS_FILE, VIEW_LOG, VIEW_READY, read_run

CLICKS = ("the map (the big picture)", "the text of the 'why' log", "the scrollbar of the 'why' log",
          "the title bar of the route view (press and let go without moving the mouse)")  # RV3-D7 A + title bar
UPDATE_WAIT_S = 3.0                             # RV4 I3: the view's title must change within this while fed
FIRST_EVENTS = 10
FEED_PERIOD_S = 0.25
CLICK_WAIT_S = 20.0
TASKBAR_WAIT_S = 30.0
STYLE_WAIT_S = 5.0
WATCH_PERIOD_S = 0.02
AFTER_CLICK_S = 0.5
N1_FILE = "n1.json"
SOURCE = "source"

_REPO = Path(__file__).resolve().parents[3]
_DATA = _REPO / "data"
_FF2_MAP = _REPO / "tests" / "fixtures" / "fossil_foundry_2.csv"
_FF2_TERRAIN = _REPO / "tests" / "fixtures" / "ff2_terrain.json"
_VIEW_TITLE = "Route view"
_FIRST_SHOW = "first show: root.state() ="
_FOCUS_FAILED = "focus call failed:"
_GA_ROOT = 2
_GWL_EXSTYLE = -20
_WS_EX_NOACTIVATE = 0x08000000
_WS_EX_APPWINDOW = 0x00040000
_VK_LBUTTON = 0x01
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_STILL_ACTIVE = 259
_CTRL_C_EVENT = 0

_NAME_STARTED = "viewer started"
_NAME_READY = "ready within the walk's wait"
_NAME_FIRST_SHOW = "first show kept the foreground"
_NAME_CONSOLE = "no console window"
_NAME_UPDATES = "the view updates while live"
_NAME_CTRL_C = "Ctrl+C in the parent's console did not reach the viewer"
_NAME_STYLE = "end clears the no-activate style"
_NAME_TASKBAR = "a taskbar click activates it after end"
_NAME_AFTER = "the viewer outlived its parent"


def _click_name(target: str) -> str:
    return f"click on {target} kept the foreground"


_ALL_NAMES = (_NAME_STARTED, _NAME_READY, _NAME_FIRST_SHOW, _NAME_CONSOLE, _NAME_UPDATES,
              *(_click_name(t) for t in CLICKS), _NAME_CTRL_C, _NAME_STYLE, _NAME_TASKBAR)

_Fg = tuple[float, int, int, str, str]  # (time, hwnd, pid, title, class) of one foreground change


class _InputClosed(Exception):
    """`ask` reached the end of standard input: nobody can answer the question (`str(exc)` is the question)."""


@dataclass(frozen=True)
class Check:
    name: str
    passed: bool | None  # None: not done (no click in time, or skipped)
    detail: str


# ---- seams (tests monkeypatch these) -----------------------------------------------------------------------------------

def _on_windows() -> bool:
    return sys.platform == "win32"


def _clock() -> float:
    return time.monotonic()


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


def _say(text: str) -> None:
    print(text, flush=True)


def _ask(prompt: str) -> str:
    return input(prompt)


def _maple_check() -> tuple[bool, str]:
    """(ok, text): a copy of `soak.maple_check` (tasklist; "maple" in any process name, any case; `src.input.harness`
    is not on the route allowlist, so it is copied, not imported). Off Windows (the dry run only) there is no check."""
    if sys.platform != "win32":
        return True, "not Windows"
    try:
        done = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True, errors="replace",
                              timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"process check failed: tasklist failed: {exc}"
    if done.returncode != 0:
        return False, f"process check failed: tasklist exited {done.returncode}: {done.stderr.strip()}"
    found = [row[0] for row in csv.reader(done.stdout.splitlines()) if row and "maple" in row[0].lower()]
    if found:
        return False, f"MapleStory running: {found}"
    return True, "no MapleStory process"


def _resolve_out(out: Path) -> Result[Path, str]:
    """`out` resolved, if it is a folder inside the repo's `data/` (a copy of `record_minimap._resolve_out`)."""
    resolved = out.resolve()
    data = _DATA.resolve()
    if resolved == data or not resolved.is_relative_to(data):
        return Failure(f"--out must be a folder inside {data}, got {resolved}")
    return Success(resolved)


# ---- the feed ---------------------------------------------------------------------------------------------------------

def build_feed(out: Path, laps: int) -> Result[list[bytes], str]:
    """Run the simulator on the FF2 fixtures into `out/source`, copy its `map.csv` to `out/map.csv` and write
    `out/steps.jsonl` as the source's header line plus its first `FIRST_EVENTS` event lines. Returns the lines still to
    feed, the source's `end` line last (raw bytes, newline kept)."""
    source = out / SOURCE
    try:
        source.mkdir(parents=True, exist_ok=True)
        ran = run_sim(_FF2_MAP, _FF2_TERRAIN, laps, 1, out_dir=source)
        if not is_successful(ran):
            return Failure(f"the simulator failed: {ran.failure()}")
        (out / MAP_COPY).write_bytes((source / MAP_COPY).read_bytes())
        raw = (source / STEPS_FILE).read_bytes().splitlines(keepends=True)
        header, events = raw[0], raw[1:]
        if len(events) <= FIRST_EVENTS + 1 or b'"end"' not in events[-1]:
            return Failure(f"the simulator run is too short to feed ({len(events)} events); use more --laps")
        (out / STEPS_FILE).write_bytes(header + b"".join(events[:FIRST_EVENTS]))
        return Success(events[FIRST_EVENTS:])
    except (OSError, IndexError) as exc:
        return Failure(f"cannot build the feed: {type(exc).__name__}: {exc}")


class Feeder:
    """A daemon thread `n1-feed` that appends one of `lines` to `path` every `period_s` (binary append, closed after
    each line). The last line (`end`) is never fed by the thread: `write_end` appends what is left, `end` last."""

    def __init__(self, path: Path, lines: list[bytes], period_s: float) -> None:
        self._path = path
        self._body = lines[:-1]
        self._end = lines[-1]
        self._period_s = period_s
        self._fed = 0
        self._ended = False
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="n1-feed", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join()

    @property
    def fed(self) -> int:
        return self._fed

    def remaining(self) -> int:
        """Lines not fed yet, `end` excluded (0: the feed has run out)."""
        return len(self._body) - self._fed

    def write_end(self) -> None:
        """Append the lines not fed yet, `end` last (once: a second call writes nothing)."""
        if self._ended:
            return
        self._ended = True
        with self._path.open("ab") as handle:
            handle.write(b"".join(self._body[self._fed:]) + self._end)
        self._fed = len(self._body)

    def _loop(self) -> None:
        while self._fed < len(self._body) and not self._stop.wait(self._period_s):
            with self._path.open("ab") as handle:
                handle.write(self._body[self._fed])
            self._fed += 1


# ---- Win32 reads ------------------------------------------------------------------------------------------------------

class Win32:
    """The real reads (tests pass a fake with the same methods). ctypes and every Windows name only inside functions."""

    def __init__(self) -> None:
        from ctypes import wintypes

        user = ctypes.WinDLL("user32", use_last_error=True)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        hwnd, dword, boolean = wintypes.HWND, wintypes.DWORD, wintypes.BOOL
        self._enum_proc = ctypes.WINFUNCTYPE(boolean, hwnd, wintypes.LPARAM)
        self._dword = dword
        self._point = wintypes.POINT
        user.GetForegroundWindow.argtypes = []
        user.GetForegroundWindow.restype = hwnd
        user.EnumWindows.argtypes = [self._enum_proc, wintypes.LPARAM]
        user.EnumWindows.restype = boolean
        user.IsWindowVisible.argtypes = [hwnd]
        user.IsWindowVisible.restype = boolean
        user.GetWindowTextW.argtypes = [hwnd, wintypes.LPWSTR, ctypes.c_int]
        user.GetWindowTextW.restype = ctypes.c_int
        user.GetClassNameW.argtypes = [hwnd, wintypes.LPWSTR, ctypes.c_int]
        user.GetClassNameW.restype = ctypes.c_int
        user.GetWindowThreadProcessId.argtypes = [hwnd, ctypes.POINTER(dword)]
        user.GetWindowThreadProcessId.restype = dword
        user.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
        user.GetCursorPos.restype = boolean
        user.WindowFromPoint.argtypes = [wintypes.POINT]
        user.WindowFromPoint.restype = hwnd
        user.GetAncestor.argtypes = [hwnd, wintypes.UINT]
        user.GetAncestor.restype = hwnd
        user.GetAsyncKeyState.argtypes = [ctypes.c_int]
        user.GetAsyncKeyState.restype = ctypes.c_short
        user.GetWindowLongPtrW.argtypes = [hwnd, ctypes.c_int]
        user.GetWindowLongPtrW.restype = ctypes.c_ssize_t
        kernel.OpenProcess.argtypes = [dword, boolean, dword]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(dword)]
        kernel.GetExitCodeProcess.restype = boolean
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = boolean
        kernel.GenerateConsoleCtrlEvent.argtypes = [dword, dword]
        kernel.GenerateConsoleCtrlEvent.restype = boolean
        self._user: Any = user
        self._kernel: Any = kernel

    def foreground(self) -> int:
        return int(self._user.GetForegroundWindow() or 0)

    def visible_windows(self) -> dict[int, tuple[int, str, str]]:
        """hwnd -> (pid, title, class) of the visible top-level windows."""
        found: dict[int, tuple[int, str, str]] = {}

        def visit(handle: int | None, _lparam: int) -> bool:
            if handle and self._user.IsWindowVisible(handle):
                found[int(handle)] = self.window_info(int(handle))
            return True

        self._user.EnumWindows(self._enum_proc(visit), 0)
        return found

    def title(self, hwnd: int) -> str:
        """GetWindowTextW; "" if the window is gone."""
        buffer = ctypes.create_unicode_buffer(512)
        copied = self._user.GetWindowTextW(hwnd, buffer, 512)
        return buffer.value if copied else ""

    def window_info(self, hwnd: int) -> tuple[int, str, str]:
        """(pid, title, class) of any window, visible or not; (0, "", "") if it is gone."""
        if not hwnd:
            return 0, "", ""
        pid = self._dword(0)
        if not self._user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid)):
            return 0, "", ""
        name = ctypes.create_unicode_buffer(256)
        self._user.GetClassNameW(hwnd, name, 256)
        return int(pid.value), self.title(hwnd), name.value

    def left_down(self) -> bool:
        return bool(self._user.GetAsyncKeyState(_VK_LBUTTON) & 0x8000)

    def left_down_on(self, hwnd: int) -> bool:
        """The left button is down and the window under the cursor has `hwnd` as its root."""
        if not self.left_down():
            return False
        point = self._point()
        if not self._user.GetCursorPos(ctypes.byref(point)):
            return False
        under = self._user.WindowFromPoint(point)
        return bool(under) and int(self._user.GetAncestor(under, _GA_ROOT) or 0) == hwnd

    def ex_style(self, hwnd: int) -> int:
        return int(self._user.GetWindowLongPtrW(hwnd, _GWL_EXSTYLE))

    def process_alive(self, pid: int) -> bool:
        handle = self._kernel.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            code = self._dword(0)
            return bool(self._kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == _STILL_ACTIVE
        finally:
            self._kernel.CloseHandle(handle)

    def ctrl_c_own_console(self) -> None:
        """One Ctrl+C to every process attached to this console (process group 0). No key is sent. Raises `OSError`
        (`ctypes.WinError` of the last error) if the call fails; its return value is a BOOL."""
        if not self._kernel.GenerateConsoleCtrlEvent(_CTRL_C_EVENT, 0):
            raise ctypes.WinError(ctypes.get_last_error())


# ---- the checks -------------------------------------------------------------------------------------------------------

class _Watch:
    """`n1-watch`: every `WATCH_PERIOD_S` the foreground window, and (once the view is known) the view's title. Only
    changes are kept. `sample()` is also called by the procedure itself, under the lock, so a fake clock is followed."""

    def __init__(self, win: Any, clock: Callable[[], float], fg0: int) -> None:
        self._win = win
        self._clock = clock
        self._lock = threading.Lock()
        self._last_fg = fg0
        self._view: int | None = None
        self._remaining: Callable[[], int] = lambda: 0
        self._last_title: str | None = None
        self.fg_changes: list[_Fg] = []
        self.titles: list[tuple[float, str, int]] = []  # (time, title, lines the feeder had left); the first is the baseline
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="n1-watch", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join()

    def set_view(self, hwnd: int, remaining: Callable[[], int]) -> None:
        with self._lock:
            self._view = hwnd
            self._remaining = remaining

    def sample(self) -> None:
        with self._lock:
            now = self._clock()
            fg = self._win.foreground()
            if fg != self._last_fg:
                self._last_fg = fg
                pid, title, cls = self._win.window_info(fg)
                self.fg_changes.append((now, fg, pid, title, cls))
            if self._view is not None:
                title = self._win.title(self._view)
                if title != self._last_title:
                    self._last_title = title
                    self.titles.append((now, title, self._remaining()))

    def _loop(self) -> None:
        while not self._stop.wait(WATCH_PERIOD_S):
            try:
                self.sample()
            except Exception:  # a failed read must not end the watch; the procedure's own samples report real faults
                continue


def _describe(change: _Fg) -> str:
    return f"'{change[3]}' (class {change[4]}, pid {change[2]})"


def _log_line(out: Path, prefix: str) -> str | None:
    try:
        text = (out / VIEW_LOG).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for line in text.splitlines():
        if line.startswith(prefix):
            return line.strip()
    return None


def _fg_verdict(win: Any, watch: _Watch, since: int, fg0: int, viewer_pid: int) -> tuple[bool | None, str]:
    """The foreground rule (RV8 n2). The watch's changes from index `since`, plus the foreground now if it is not
    `fg0` and not among them, minus those to `fg0` itself. Any left in a window of `viewer_pid` -> False (the view took
    the foreground); any other left -> None (another program's window says nothing about the view); else True."""
    watch.sample()
    seen = list(watch.fg_changes[since:])
    now = win.foreground()
    if now != fg0 and not any(change[1] == now for change in seen):
        seen.append((0.0, now, *win.window_info(now)))
    off = [change for change in seen if change[1] != fg0]
    mine = [change for change in off if change[2] == viewer_pid]
    if mine:
        return False, "the route view took the foreground: " + ", ".join(_describe(c) for c in mine)
    if off:
        return None, ("another window took the foreground; run again: " + ", ".join(_describe(c) for c in off))
    return True, "the foreground stayed on the console"


def run_checks(out: Path, lines: list[bytes], *, win: Any, start_viewer: Callable[[Path], Any],
               wait: Callable[[Path, Any], bool], ask: Callable[[str], str] = input, say: Callable[[str], None] = print,
               clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep,
               maple: Callable[[], tuple[bool, str]] = _maple_check) -> tuple[list[Check], dict[str, Any]]:
    """Spec 5.7's procedure, in its order. `lines`: `build_feed`'s result, the lines the `Feeder` appends. Returns the
    checks (every name in `_ALL_NAMES`, in order; a precondition that failed makes the checks it blocks `None` with the
    reason) and the facts for `n1.json` (`stopped_by_maple` when a MapleStory check failed)."""
    checks: list[Check] = []
    facts: dict[str, Any] = {}
    t0 = clock()
    fg0 = win.foreground()
    before = win.visible_windows()
    facts["fg0"] = fg0
    say("Keep this console in front. Do not click anything until you are asked.")
    watch = _Watch(win, clock, fg0)
    watch.start()
    feeder: Feeder | None = None
    viewer_pid = 0

    def nap(seconds: float) -> None:
        end = clock() + seconds
        while (left := end - clock()) > 0:
            sleep(min(WATCH_PERIOD_S, left))
            watch.sample()

    def ask_hong(prompt: str) -> str:
        try:
            return ask(prompt)
        except EOFError as exc:  # standard input is closed: an unanswered question must not read as an answer
            raise _InputClosed(prompt) from exc

    def skip_rest(reason: str) -> None:
        have = {check.name for check in checks}
        checks.extend(Check(name, None, reason) for name in _ALL_NAMES if name not in have)

    def finish() -> None:
        if feeder is not None:
            feeder.stop()
            facts.setdefault("lines_fed", feeder.fed)  # what the thread fed; step 6 has set it before `write_end`
            feeder.write_end()
        try:
            watch.sample()
        finally:
            watch.stop()
        facts["fg_changes"] = [{"t": round(t - t0, 3), "hwnd": h, "pid": p, "title": ti, "class": c}
                               for t, h, p, ti, c in watch.fg_changes]
        facts["title_changes"] = [{"t": round(t - t0, 3), "title": ti, "lines_left": left}
                                  for t, ti, left in watch.titles]

    try:
        proc = start_viewer(out)
        if isinstance(proc, Failure):
            checks.append(Check(_NAME_STARTED, False, str(proc.failure())))
            skip_rest("the viewer did not start")
            return checks, facts
        checks.append(Check(_NAME_STARTED, True, "started as a walk starts it"))
        facts["redirector_pid"] = proc.pid
        ready = wait(out, proc)
        feeder = Feeder(out / STEPS_FILE, lines, FEED_PERIOD_S)
        feeder.start()
        if not ready:
            checks.append(Check(_NAME_READY, False, "view.ready did not appear in the walk's wait"))
            skip_rest("the viewer was not ready: it stays hidden until end (spec 5.9 step 3)")
            return checks, facts
        checks.append(Check(_NAME_READY, True, "view.ready appeared in the walk's wait"))

        # step 3: the view window and the first show
        try:
            viewer_pid = int((out / VIEW_READY).read_text(encoding="ascii").strip())
        except (OSError, ValueError) as exc:
            skip_rest(f"view.ready holds no pid: {type(exc).__name__}: {exc}")
            return checks, facts
        facts["viewer_pid"] = viewer_pid
        deadline = clock() + STYLE_WAIT_S
        view = 0
        while not view:
            found = [h for h, (pid, title, _cls) in win.visible_windows().items()
                     if pid == viewer_pid and title.startswith(_VIEW_TITLE)]
            if found:
                view = found[0]
            elif clock() >= deadline:
                break
            else:
                nap(WATCH_PERIOD_S)
        if not view:
            skip_rest(f"no visible window of pid {viewer_pid} with a title starting '{_VIEW_TITLE}'")
            return checks, facts
        facts["view_hwnd"] = view
        watch.set_view(view, feeder.remaining)
        nap(1.0)
        verdict, detail = _fg_verdict(win, watch, 0, fg0, viewer_pid)
        checks.append(Check(_NAME_FIRST_SHOW, verdict, detail))
        new = {h: info for h, info in win.visible_windows().items() if h not in before}
        others = [f"'{t}' (class {c}, pid {p})" for h, (p, t, c) in new.items() if h != view]
        answer = ask_hong("Did any other window (a console) appear when the route view opened? [y/N] ")
        watch.sample()
        saw_console = answer.strip().lower().startswith("y")
        console_ok = set(new) == {view} and not saw_console
        if console_ok:
            console_detail = "no other new window"
        else:
            console_detail = "; ".join(
                ([f"answered y ({answer.strip()})"] if saw_console else [])
                + ([f"other new windows: {', '.join(others)}"] if others else [])
                + ([] if view in new else ["the view window was not new"]))
        checks.append(Check(_NAME_CONSOLE, console_ok, console_detail))

        # the view updates while live (RV4 I3, RV6 m2)
        if not any(left > 0 for _t, _title, left in watch.titles[1:]):
            wait_end = clock() + UPDATE_WAIT_S
            while clock() < wait_end and not any(left > 0 for _t, _title, left in watch.titles[1:]):
                nap(WATCH_PERIOD_S)
        feed_left = feeder.remaining()  # as the wait ends: Hong's answers below take time and must not decide it
        shows = ask_hong("Does the route view show the map and change as lines arrive? [Y/n] ")
        watch.sample()
        said_no = shows.strip().lower().startswith("n")
        changed = any(left > 0 for _t, _title, left in watch.titles[1:])
        first = _log_line(out, _FIRST_SHOW)
        facts["first_show_state"] = first
        facts["focus_error"] = _log_line(out, _FOCUS_FAILED)
        titles = [title for _t, title, _left in watch.titles]
        tail = (f"; first title '{titles[0] if titles else ''}', last title '{titles[-1] if titles else ''}'; "
                f"view.log: {first or 'no first-show line'}")
        update: bool | None
        if changed:
            update, how = (False, "answered n") if said_no else (True, "the title changed while lines were fed")
        elif feed_left == 0:
            update, how = None, "the feed finished before a title change was seen; run again with more --laps"
        else:
            update, how = False, "no title change while fed"
        checks.append(Check(_NAME_UPDATES, update, how + tail))

        # step 4: the four clicks
        for target in CLICKS:
            ok, text = maple()
            if not ok:
                facts["stopped_by_maple"] = text
                skip_rest(f"stopped: {text}")
                return checks, facts
            say(f"Click {target} in the route view once, then let go of the mouse. ({CLICK_WAIT_S:g} s)")
            since = len(watch.fg_changes)
            watch.sample()
            ignored = 0
            was_down = False
            clicked = False
            limit = clock() + CLICK_WAIT_S
            while clock() < limit:
                if win.left_down_on(view):
                    clicked = True
                    break
                down = win.left_down()
                ignored += 1 if down and not was_down else 0
                was_down = down
                nap(WATCH_PERIOD_S)
            if not clicked:
                checks.append(Check(_click_name(target), None, f"no click on the view within {CLICK_WAIT_S:g} s"
                                    + (f" ({ignored} click(s) elsewhere ignored)" if ignored else "")))
                continue
            release = clock() + CLICK_WAIT_S
            while win.left_down() and clock() < release:
                nap(WATCH_PERIOD_S)
            nap(AFTER_CLICK_S)
            verdict, detail = _fg_verdict(win, watch, since, fg0, viewer_pid)
            checks.append(Check(_click_name(target), verdict,
                                detail + (f" ({ignored} click(s) elsewhere ignored)" if ignored else "")))
            if verdict is not True and win.foreground() != fg0:
                say("Click this console to bring it back to the front")
                back = clock() + CLICK_WAIT_S
                while win.foreground() != fg0 and clock() < back:
                    nap(WATCH_PERIOD_S)

        # step 5: Ctrl+C in this console
        hits: list[int] = []
        previous = signal.signal(signal.SIGINT, lambda signum, _frame: hits.append(signum))
        send_error: OSError | None = None
        try:
            try:
                win.ctrl_c_own_console()
            except OSError as exc:
                send_error = exc
            else:
                nap(1.0)
        finally:
            if previous is not None:
                signal.signal(signal.SIGINT, previous)
        if send_error is not None:
            checks.append(Check(_NAME_CTRL_C, False, f"GenerateConsoleCtrlEvent failed: {send_error}"))
        elif not hits:
            checks.append(Check(_NAME_CTRL_C, False, "the Ctrl+C was not delivered"))
        else:
            alive = proc.poll() is None and win.process_alive(viewer_pid)
            checks.append(Check(_NAME_CTRL_C, alive, "the viewer was still running" if alive else "the viewer ended"))

        # step 6: end
        if feeder is not None:
            feeder.stop()
            facts["lines_fed"] = feeder.fed
            feeder.write_end()
        style = win.ex_style(view)
        limit = clock() + STYLE_WAIT_S
        while not (style & _WS_EX_APPWINDOW and not style & _WS_EX_NOACTIVATE) and clock() < limit:
            nap(WATCH_PERIOD_S)
            style = win.ex_style(view)
        cleared = bool(style & _WS_EX_APPWINDOW) and not style & _WS_EX_NOACTIVATE
        checks.append(Check(_NAME_STYLE, cleared, f"extended style {style:#x}"))
        say("The route view now has a taskbar button. Click it once.")
        limit = clock() + TASKBAR_WAIT_S
        while win.foreground() != view and clock() < limit:
            nap(WATCH_PERIOD_S)
        activated = win.foreground() == view
        checks.append(Check(_NAME_TASKBAR, True if activated else None,
                            "the view became the foreground window" if activated
                            else f"no taskbar click within {TASKBAR_WAIT_S:g} s"))
        return checks, facts
    except _InputClosed as closed:
        facts["stopped_by_no_input"] = f"standard input is closed; could not ask: {str(closed).strip()}"
        skip_rest("stopped: standard input is closed, so the question could not be asked")
        return checks, facts
    finally:
        finish()
        skip_rest("not reached")


# ---- the CLI ----------------------------------------------------------------------------------------------------------

def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m src.route.harness.view_check",
                                     description="N1: the route view's no-key check (needs Hong at the mouse).")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="start the viewer, feed it, and ask for four clicks")
    run.add_argument("--out", type=Path, default=None, help="folder inside data/ (default data/route_view/n1-<UTC>)")
    run.add_argument("--laps", type=int, default=3, help="simulator laps to feed (default 3)")
    run.add_argument("--dry-run", action="store_true", help="build the feed and read it back; no viewer, no Win32")
    after = commands.add_parser("after", help="after `run` has exited: did the viewer outlive its parent?")
    after.add_argument("--out", type=Path, required=True)
    return parser


def _report(checks: list[Check]) -> bool:
    for check in checks:
        label = "NOT DONE" if check.passed is None else ("PASS" if check.passed else "FAIL")
        print(f"{label}  {check.name}: {check.detail}")
    return all(check.passed is True for check in checks)


def _dry_run(out: Path, lines: list[bytes]) -> int:
    Feeder(out / STEPS_FILE, lines, 0.0).write_end()
    fed = read_run(out)
    source = read_run(out / SOURCE)
    if not is_successful(fed) or not is_successful(source):
        print(f"dry run: cannot read back: {fed.failure() if not is_successful(fed) else source.failure()}")
        return 1
    got, want = fed.unwrap(), source.unwrap()
    if got.header != want.header:
        print(f"dry run: the header differs: {got.header!r} != {want.header!r}")
        return 1
    for index, (a, b) in enumerate(zip(got.events, want.events, strict=False)):
        if a != b:
            print(f"dry run: event {index} differs: {a!r} != {b!r}")
            return 1
    if len(got.events) != len(want.events) or not got.ended:
        print(f"dry run: {len(got.events)} events read back of {len(want.events)}, ended {got.ended}")
        return 1
    print(f"dry run: {len(got.events)} events fed and read back equal")
    return 0


def _run(args: argparse.Namespace) -> int:
    default = _DATA / "route_view" / f"n1-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    resolved = _resolve_out(args.out if args.out is not None else default)
    if not is_successful(resolved):
        print(f"error: {resolved.failure()}", file=sys.stderr)
        return 2
    out = resolved.unwrap()
    checked = runtime.check_out(out, (STEPS_FILE, SOURCE))
    if not is_successful(checked):
        print(f"error: {checked.failure()}", file=sys.stderr)
        return 2
    if args.laps < 1:
        print(f"error: --laps must be at least 1, got {args.laps}", file=sys.stderr)
        return 2
    if not args.dry_run and not _on_windows():
        print("error: the check reads Windows windows; run it on Windows (or use --dry-run)", file=sys.stderr)
        return 2
    ok, text = _maple_check()
    if not ok:
        print(text, file=sys.stderr)
        return 3
    built = build_feed(out, args.laps)
    if not is_successful(built):
        print(f"error: {built.failure()}", file=sys.stderr)
        return 2
    lines = built.unwrap()
    if args.dry_run:
        return _dry_run(out, lines)
    checks, facts = run_checks(out, lines, win=Win32(), start_viewer=walk._start_viewer, wait=walk._wait_for_viewer,
                               ask=_ask, say=_say, clock=_clock, sleep=_sleep, maple=_maple_check)
    (out / N1_FILE).write_text(json.dumps({"checks": [asdict(c) for c in checks], "facts": facts}, indent=2) + "\n",
                               encoding="utf-8")
    passed = _report(checks)
    if "stopped_by_maple" in facts:
        print(facts["stopped_by_maple"], file=sys.stderr)
        return 3
    if "stopped_by_no_input" in facts:
        print("error: standard input is closed, so the two questions cannot be asked; run this tool in a console "
              "window of its own (see the module docstring)", file=sys.stderr)
        return 2
    print(f"Now let this tool exit, then run: python -m src.route.harness.view_check after --out {out}")
    return 0 if passed else 1


def _after(args: argparse.Namespace) -> int:
    path = args.out / N1_FILE
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        viewer_pid = int(record["facts"]["viewer_pid"])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"error: cannot read the viewer's pid from {path}: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    if not _on_windows():
        print("error: the check reads Windows windows; run it on Windows", file=sys.stderr)
        return 2
    win = Win32()
    alive = win.process_alive(viewer_pid)
    shown = any(pid == viewer_pid and title.startswith(_VIEW_TITLE)
                for pid, title, _cls in win.visible_windows().values())
    check = Check(_NAME_AFTER, alive and shown, f"process alive {alive}, route view window visible {shown}")
    record["after"] = asdict(check)
    path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    passed = _report([check])
    print("You can close the route view now.")
    return 0 if passed else 1


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
    except SystemExit as exc:  # argparse error or --help: return its code, do not raise
        return exc.code if isinstance(exc.code, int) else 2
    return _run(args) if args.command == "run" else _after(args)


if __name__ == "__main__":
    sys.exit(main())
