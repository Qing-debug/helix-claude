"""Runtime pieces of the supervised walk (plan task B19): the stop control, the minimap capture and the cleanup steps.

Shared by the walk CLI (`walk.py`) and the movement measurement tool (`harness/measure_moves.py`).

`StopControl` stops a run from three sources: the F12 key (a low-level keyboard hook on its own thread, G33), Ctrl+C
(a SIGINT handler) and the walk CLI's `--minutes` timer. `trigger()` only records the first trigger and sets an
event; a worker thread waits on it and calls `on_stop`, so the hook procedure returns at once and never blocks on
Bluetooth (Windows drops a low-level hook that is slow to return). `close()` undoes `start()` (PB-amend2-D1).

Windows-only names (`ctypes.wintypes`, `ctypes.WinDLL`, `ctypes.WINFUNCTYPE`) and `src.vision` are imported inside
functions only, so this module imports on Linux (plan hard rule 9).

Elevation (RT-FIX-1, H82; RT-FIX-1b): MapleStory runs as administrator, and Windows (UIPI) does not show a program
running without administrator rights the keys typed into an elevated window, so the F12 hook of a normal Helix never
hears F12 with the game in front (proved by `F12CHK`). `ensure_elevated` is what `python -m src.main` calls first: an
elevated process carries on; otherwise it starts the same command again with ShellExecute's "runas" verb (one Windows
prompt) and the caller exits. If that is declined, or `HELIX_NO_ELEVATE` is set, the app keeps running only for what
sends no key; `NOT_ELEVATED_TEXT` is what it says. The two live command-line tools (`walk.py` and
`harness/measure_moves.py`) never relaunch (RT-FIX-1-R-D1): after their usage checks they test `is_elevated` and, if it is
false, exit 11 with `NOT_ELEVATED_CLI_TEXT`.

Where the plan is silent:
- `close()` joins the worker and the hook thread for at most `_JOIN_S` each and writes a line through `log` if one
  is still alive, as Feature A's listener does (`src/input/harness/listener.py:384-387`); it never waits for ever.
- The hook swallows both the key-down and the key-up of the hotkey (PB-D2); only a key-down triggers.
- A `trigger()` before `start()` is kept: the worker that `start()` launches then calls `on_stop` at once.
- The lock is an `RLock` so a Ctrl+C on the main thread while it holds the lock cannot deadlock. If that Ctrl+C lands
  inside another `trigger()` before it marks the stop as triggered, both record and the outer call's source and time
  are the ones kept; either way the stop happens once.
"""

import enum
import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterable
from pathlib import Path

from returns.pipeline import is_successful
from returns.result import Failure, Result, Success

from src.route.character import CharacterLocator
from src.route.position import MinimapPositionSource
from src.route.walker import PositionSource

_REPO = Path(__file__).resolve().parents[2]
_ICON = _REPO / "assets" / "chr" / "chr.png"
_MASK = _REPO / "assets" / "chr" / "chr_mask.png"
_JOIN_S = 5.0          # how long close() waits for the worker and the hook thread
_HOOK_READY_S = 5.0    # how long _install_hook waits for the hook thread to install the hook
_FRAME_POLL_S = 0.05   # wait_first_frame's polling period
_NOT_INSTALLED = object()  # "start() did not install a SIGINT handler", kept apart from a previous handler of None
ELEVATE_OPT_OUT_ENV = "HELIX_NO_ELEVATE"  # set (not empty, not "0") by tests and agent runs that send no key: no relaunch
NOT_ELEVATED_TEXT = ("F12 cannot stop a walk while MapleStory is in front, because Helix is not running as "
                     "administrator. Close Helix and start it again and answer Yes if Windows asks, or start it from "
                     "an administrator terminal.")
NOT_ELEVATED_CLI_TEXT = ("Helix must run as administrator so F12 can stop the walk with MapleStory in front; start it "
                         "from an administrator terminal.")
_SHELL_EXECUTE_OK = 32     # ShellExecuteW returns a value above this on success, an error code up to it otherwise
_SE_ERR_ACCESSDENIED = 5   # ... and this one when the Windows prompt is declined
_SW_SHOWNORMAL = 1
# What the elevated copy runs instead of `-m <module>` (RT-FIX-1b, I1). Windows starts a "runas" child through the AppInfo
# service, and whether it gets the folder ShellExecute was given (or the parent's environment) is not documented, so the
# child must not depend on either to find `src`. argv: <repo root> <module> <the module's arguments>. It changes to the
# repo root, puts it first on sys.path, and runs the module as `__main__` as `-m` would; an uncaught exception prints its
# traceback and waits for Enter, so the new console window does not vanish with it.
_ELEVATED_BOOTSTRAP = (
    "import os,runpy,sys;"
    "sys.excepthook=lambda*a:(sys.__excepthook__(*a),input('Press Enter to close this window.'));"
    "r,m=sys.argv[1:3];os.chdir(r);sys.path.insert(0,r);sys.argv=['']+sys.argv[3:];"
    "runpy.run_module(m,run_name='__main__',alter_sys=True)")


def _print_error(text: str) -> None:
    """The `log` default of `run_cleanup`, `StopControl` and the hook: the line on stderr, as the CLI prints it."""
    print(text, file=sys.stderr)


class StopControl:
    def __init__(self, on_stop: Callable[[], None], hotkey_vk: int = 0x7B, *,  # 0x7B = F12 (PB-D2)
                 log: Callable[[str], None] = _print_error) -> None:
        self._on_stop = on_stop
        self._hotkey_vk = hotkey_vk
        self._log = log  # the app run's log goes to the console box (route tab H71); the CLI's prints on stderr
        # Makes the first trigger() and the first close() one step each. Reentrant because Python runs the SIGINT
        # handler on the main thread between bytecodes, also while the main thread holds this lock (in trigger() from
        # walk's lost-leg sink, or in close()): a plain Lock would deadlock there (B19-R M1).
        self._lock = threading.RLock()
        self._wake = threading.Event()  # set by the first trigger() and by close(); the worker waits on it
        self._triggered = False
        self._closed = False
        self._worker: threading.Thread | None = None
        self._remove: Callable[[], None] | None = None
        self._sigint_handler: Callable[[int, object], None] | None = None  # the bound method start() installed
        self._prev_sigint: object = _NOT_INSTALLED
        self.trigger_ns: int | None = None
        self.trigger_source: str | None = None

    def start(self, sigint: bool = True) -> Result[None, str]:
        """Install the hotkey hook, then the SIGINT handler (only with `sigint`; the app has no console and
        `signal.signal` fails off the main thread), then start the worker. Never raises."""
        try:
            self._remove = self._install_hook()
        except OSError as exc:
            return Failure(f"hook: {exc}")
        if sigint:
            handler = self._on_sigint  # stored: close() compares the installed handler with this very object
            try:
                previous = signal.signal(signal.SIGINT, handler)
            except ValueError as exc:  # signal.signal works only on the main thread
                return Failure(f"sigint: {exc}")
            self._sigint_handler = handler
            self._prev_sigint = previous
        self._worker = threading.Thread(target=self._work, name="stop-control", daemon=True)
        self._worker.start()
        return Success(None)

    def trigger(self, source: str = "hotkey") -> None:
        """Ask for a stop. Idempotent and returns at once; only the first call records its time and source."""
        with self._lock:
            if self._triggered:
                return
            self._triggered = True
            self.trigger_ns = time.perf_counter_ns()
            self.trigger_source = source
        self._wake.set()

    def close(self) -> None:
        """Undo `start()`: end the worker, remove the hook, then restore SIGINT. Never raises; idempotent."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._step("worker", self._end_worker)
        self._step("hook", self._remove_hook)
        self._step("sigint", self._restore_sigint)

    def _install_hook(self) -> Callable[[], None]:
        """The seam tests replace. Installs the hotkey hook on its own thread; returns remove()."""
        return _install_keyboard_hook(self._hotkey_vk, self.trigger, log=self._log)

    def _on_sigint(self, signum: int, frame: object) -> None:
        self.trigger("sigint")

    def _work(self) -> None:
        self._wake.wait()
        with self._lock:
            triggered = self._triggered
        if triggered:
            self._on_stop()

    def _end_worker(self) -> None:
        worker = self._worker
        if worker is None:
            return
        self._wake.set()  # with no trigger the worker returns without calling on_stop
        worker.join(_JOIN_S)
        if worker.is_alive():
            self._log("stop control: the worker thread did not end (on_stop is still running)")

    def _remove_hook(self) -> None:
        remove, self._remove = self._remove, None
        if remove is not None:
            remove()

    def _restore_sigint(self) -> None:
        if self._prev_sigint is _NOT_INSTALLED or threading.current_thread() is not threading.main_thread():
            return
        if signal.getsignal(signal.SIGINT) is self._sigint_handler:  # leave a handler installed after ours alone
            previous = self._prev_sigint
            signal.signal(signal.SIGINT, signal.default_int_handler if previous is None else previous)  # pyright: ignore[reportArgumentType]
        self._prev_sigint = _NOT_INSTALLED

    def _step(self, name: str, step: Callable[[], None]) -> None:
        try:
            step()
        except Exception as exc:
            self._log(f"stop control: close {name} failed: {type(exc).__name__}: {exc}")


def _install_keyboard_hook(vk: int, on_key: Callable[[], None], *,
                           log: Callable[[str], None] = _print_error) -> Callable[[], None]:
    """A WH_KEYBOARD_LL hook on a daemon thread that calls `on_key()` on each key-down of `vk` and swallows that key.

    The pattern of Feature A's listener (`src/input/harness/listener.py:255-285`, `:368-388`). Raises `OSError`, after
    its own teardown, if the hook is not installed within `_HOOK_READY_S`.
    """
    import ctypes
    from ctypes import wintypes

    wh_keyboard_ll, hc_action, wm_quit, pm_noremove = 13, 0, 0x0012, 0x0000
    wm_keydown, wm_syskeydown = 0x0100, 0x0104
    lresult = ctypes.c_ssize_t
    hookproc = ctypes.WINFUNCTYPE(lresult, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

    class Kbdllhookstruct(ctypes.Structure):
        _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD), ("flags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.SetWindowsHookExW.argtypes = [ctypes.c_int, hookproc, wintypes.HINSTANCE, wintypes.DWORD]
    user32.SetWindowsHookExW.restype = wintypes.HHOOK
    user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
    user32.CallNextHookEx.restype = lresult
    user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
    user32.UnhookWindowsHookEx.restype = wintypes.BOOL
    user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
    user32.GetMessageW.restype = wintypes.BOOL
    user32.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT,
                                    wintypes.UINT]
    user32.PeekMessageW.restype = wintypes.BOOL
    user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.PostThreadMessageW.restype = wintypes.BOOL
    kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    kernel32.GetModuleHandleW.restype = wintypes.HMODULE

    ready = threading.Event()
    state: dict[str, object] = {"tid": None, "error": None}

    def run() -> None:
        state["tid"] = threading.get_native_id()
        msg = wintypes.MSG()
        user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, pm_noremove)  # create the queue before WM_QUIT is posted
        hook = None

        def proc(n_code: int, w_param: int, l_param: int) -> int:
            if n_code == hc_action:
                kb = ctypes.cast(l_param, ctypes.POINTER(Kbdllhookstruct)).contents
                if kb.vkCode == vk:
                    if w_param in (wm_keydown, wm_syskeydown):
                        on_key()  # trigger(): a lock and an Event, returns at once
                    return 1  # swallow the hotkey (PB-D2)
            return user32.CallNextHookEx(hook, n_code, w_param, l_param)

        c_proc = hookproc(proc)  # keep a reference for the hook's lifetime
        hook = user32.SetWindowsHookExW(wh_keyboard_ll, c_proc, kernel32.GetModuleHandleW(None), 0)
        if not hook:
            state["error"] = f"SetWindowsHookExW failed, GetLastError={ctypes.get_last_error()}"
            ready.set()
            return
        ready.set()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                pass  # the hook is called from inside GetMessageW
        finally:
            user32.UnhookWindowsHookEx(hook)

    thread = threading.Thread(target=run, name="stop-hook", daemon=True)
    thread.start()

    def remove() -> None:
        tid = state["tid"]
        if thread.is_alive() and tid is not None and not user32.PostThreadMessageW(tid, wm_quit, 0, 0):
            log("stop control: could not post WM_QUIT to the hook thread")
        thread.join(_JOIN_S)
        if thread.is_alive():
            log("stop control: the hook thread did not exit; F12 may stay swallowed until the process ends")

    if not ready.wait(_HOOK_READY_S) or state["error"] is not None:
        error = state["error"] or "hook thread did not start"
        remove()
        raise OSError(error)
    return remove


# ---- elevation (RT-FIX-1, H82) ----------------------------------------------------------------------------------------

class Elevation(enum.Enum):
    """How `ensure_elevated` left the process."""
    ELEVATED = "elevated"      # already running as administrator: carry on
    RELAUNCHED = "relaunched"  # an elevated copy of this command was started: this process must exit
    OPTED_OUT = "opted_out"    # not elevated, and `HELIX_NO_ELEVATE` says not to relaunch (tests, runs that send no key)
    DECLINED = "declined"      # not elevated and the relaunch was declined or failed


def is_elevated() -> bool:
    """True when this process runs elevated (as administrator). `IsUserAnAdmin` (shell32) reads the process's own token;
    it returns false for a normal process of an administrator account and true for the same account elevated, as
    the F12 check saw on this PC (H82). Microsoft marks the function "may be
    altered or unavailable in subsequent versions" and recommends `CheckTokenMembership`, which needs a SID built by
    hand; a failed call reads as not elevated."""
    import ctypes

    try:
        return bool(ctypes.WinDLL("shell32").IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def _shell_execute_runas(file: str, params: str, directory: str) -> int:
    """`ShellExecuteW(NULL, "runas", file, params, directory, SW_SHOWNORMAL)`: Windows asks the user for consent and
    starts `file` elevated. Returns ShellExecuteW's value: above 32 on success, else an error code (5 when the prompt
    is declined). The only line that raises a real Windows prompt; tests replace it."""
    import ctypes
    from ctypes import wintypes

    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    shell32.ShellExecuteW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                      wintypes.LPCWSTR, ctypes.c_int]
    shell32.ShellExecuteW.restype = ctypes.c_void_p  # an HINSTANCE that is really a small code; never a pointer
    return int(shell32.ShellExecuteW(None, "runas", file, params, directory, _SW_SHOWNORMAL) or 0)


def _split_module_command() -> tuple[list[str], str, list[str]] | None:
    """`sys.orig_argv[1:]` as (interpreter options, module, the module's arguments) when this process was started as
    `python [options] -m module [args]`, else None (a script, `-c`, the elevated copy's own bootstrap, or no argv)."""
    argv = sys.orig_argv[1:]
    if "-m" not in argv:
        return None
    at = argv.index("-m")
    if at + 1 >= len(argv):
        return None
    return argv[:at], argv[at + 1], argv[at + 2:]


def relaunch_elevated() -> Result[None, str]:
    """Start this same command again elevated: the same interpreter (`sys.executable`), the same options and module
    arguments (from `sys.orig_argv`, so a `-u` or `-X` flag survives), run through `_ELEVATED_BOOTSTRAP` from the repo
    root instead of `-m`, so it finds `src` whatever folder Windows gives the elevated child (RT-FIX-1b, I1). Success
    means Windows started it (the caller then exits); a Failure says why not. The elevated copy's own command line is
    the bootstrap's (no `-m`), so a copy that somehow is not elevated gets a Failure here and cannot start another."""
    command = _split_module_command() if sys.executable else None
    if command is None:
        return Failure("the command that started this process is not known (it was not started as python -m <module>)")
    options, module, args = command
    # list2cmdline is undocumented but is the quoting subprocess itself uses on Windows
    params = subprocess.list2cmdline([*options, "-c", _ELEVATED_BOOTSTRAP, str(_REPO), module, *args])
    code = _shell_execute_runas(sys.executable, params, str(_REPO))
    if code > _SHELL_EXECUTE_OK:
        return Success(None)
    if code == _SE_ERR_ACCESSDENIED:
        return Failure("the Windows prompt was declined (ShellExecuteW returned 5)")
    return Failure(f"Windows did not start it (ShellExecuteW returned {code})")


def ensure_elevated(*, log: Callable[[str], None] = _print_error) -> Elevation:
    """The start-up check of the app: elevated → `ELEVATED`; `HELIX_NO_ELEVATE` set → `OPTED_OUT`
    (nothing is launched); else `relaunch_elevated`, giving `RELAUNCHED` or, with its reason through `log`, `DECLINED`."""
    if is_elevated():
        return Elevation.ELEVATED
    if os.environ.get(ELEVATE_OPT_OUT_ENV, "") not in ("", "0"):
        return Elevation.OPTED_OUT
    relaunched = relaunch_elevated()
    if is_successful(relaunched):
        return Elevation.RELAUNCHED
    log(f"could not start Helix as administrator: {relaunched.failure()}")
    return Elevation.DECLINED


def check_out(out: Path, names: Iterable[str]) -> Result[None, str]:
    """B19-D2: refuse an `--out` that is a file or already holds one of `names` (paths relative to `out`).

    Shared by the walk CLI and the movement measurement tool; the first name found is the one reported.
    """
    if out.exists() and not out.is_dir():
        return Failure(f"{out} is not a folder; choose a new --out")
    for name in names:
        path = out / name
        if path.exists():
            return Failure(f"{path} already exists; choose a new --out")
    return Success(None)


class SharedFrame:
    """What the capture thread and the position source need of `SharedInfo` (src/main.py may not be imported here)."""

    def __init__(self) -> None:
        self.minimap_frame_lock = threading.Lock()
        self.minimap_frame = None


def make_positions(shared) -> PositionSource | Failure[str]:
    """The character's position on `shared`'s minimap frames, or a `Failure` when the character icon does not load."""
    located = CharacterLocator.from_files(_ICON, _MASK)
    if not is_successful(located):
        return Failure(f"character icon: {located.failure()}")
    return MinimapPositionSource(shared, located.unwrap())


def run_cleanup(steps: Iterable[tuple[str, Callable[[], object]]], *,
                log: Callable[[str], None] = _print_error) -> None:
    """Run every cleanup step in order (plan B19). A step that raises writes `cleanup failed: <name>: ...` through
    `log` (by default on stderr) and the next step still runs; a `BaseException` (KeyboardInterrupt, SystemExit) is
    held and the first one re-raised after every step ran."""
    held: BaseException | None = None
    for name, step in steps:
        try:
            step()
        except Exception as exc:
            log(f"cleanup failed: {name}: {type(exc).__name__}: {exc}")
        except BaseException as exc:  # KeyboardInterrupt, SystemExit: hold it, keep going
            if held is None:
                held = exc
    if held is not None:
        raise held


def start_capture(shared) -> threading.Thread:
    """Start the minimap capture thread (`src.vision`'s loop) writing into `shared`, as `src/main.py` does."""
    from src.vision.vision import capture_and_process_minimap

    thread = threading.Thread(target=capture_and_process_minimap, args=(shared,), name="minimap-capture", daemon=True)
    thread.start()
    return thread


def wait_first_frame(shared, timeout_s: float) -> Result[None, str]:
    """Wait until `shared.minimap_frame` is set (read under its lock), polling; `Failure` after `timeout_s`."""
    deadline = time.monotonic() + timeout_s
    while True:
        with shared.minimap_frame_lock:
            frame = shared.minimap_frame
        if frame is not None:
            return Success(None)
        if time.monotonic() >= deadline:
            return Failure(f"no minimap frame within {timeout_s:g} s (is the game visible on the captured monitor?)")
        time.sleep(_FRAME_POLL_S)
