"""Runtime pieces of the supervised walk (plan task B19): the stop control and the minimap capture.

`StopControl` stops a run from three sources: the F12 key (a low-level keyboard hook on its own thread, G33), Ctrl+C
(a SIGINT handler) and the walk CLI's `--minutes` timer. `trigger()` only records the first trigger and sets an
event; a worker thread waits on it and calls `on_stop`, so the hook procedure returns at once and never blocks on
Bluetooth (Windows drops a low-level hook that is slow to return). `close()` undoes `start()` (PB-amend2-D1).

Windows-only names (`ctypes.wintypes`, `ctypes.WinDLL`, `ctypes.WINFUNCTYPE`) and `src.vision` are imported inside
functions only, so this module imports on Linux (plan hard rule 9).

Where the plan is silent:
- `close()` joins the worker and the hook thread for at most `_JOIN_S` each and prints a line to stderr if one is
  still alive, as Feature A's listener does (`src/input/harness/listener.py:384-387`); it never waits for ever.
- The hook swallows both the key-down and the key-up of the hotkey (PB-D2); only a key-down triggers.
- A `trigger()` before `start()` is kept: the worker that `start()` launches then calls `on_stop` at once.
"""

import signal
import sys
import threading
import time
from collections.abc import Callable

from returns.result import Failure, Result, Success

_JOIN_S = 5.0          # how long close() waits for the worker and the hook thread
_HOOK_READY_S = 5.0    # how long _install_hook waits for the hook thread to install the hook
_FRAME_POLL_S = 0.05   # wait_first_frame's polling period
_NOT_INSTALLED = object()  # "start() did not install a SIGINT handler", kept apart from a previous handler of None


class StopControl:
    def __init__(self, on_stop: Callable[[], None], hotkey_vk: int = 0x7B) -> None:  # 0x7B = F12 (PB-D2)
        self._on_stop = on_stop
        self._hotkey_vk = hotkey_vk
        self._lock = threading.Lock()  # makes the first trigger() and the first close() one step each
        self._wake = threading.Event()  # set by the first trigger() and by close(); the worker waits on it
        self._triggered = False
        self._closed = False
        self._worker: threading.Thread | None = None
        self._remove: Callable[[], None] | None = None
        self._sigint_handler: Callable[[int, object], None] | None = None  # the bound method start() installed
        self._prev_sigint: object = _NOT_INSTALLED
        self.trigger_ns: int | None = None
        self.trigger_source: str | None = None

    def start(self) -> Result[None, str]:
        """Install the hotkey hook, then the SIGINT handler, then start the worker. Never raises."""
        try:
            self._remove = self._install_hook()
        except OSError as exc:
            return Failure(f"hook: {exc}")
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
        return _install_keyboard_hook(self._hotkey_vk, self.trigger)

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
            print("stop control: the worker thread did not end (on_stop is still running)", file=sys.stderr)

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

    @staticmethod
    def _step(name: str, step: Callable[[], None]) -> None:
        try:
            step()
        except Exception as exc:
            print(f"stop control: close {name} failed: {type(exc).__name__}: {exc}", file=sys.stderr)


def _install_keyboard_hook(vk: int, on_key: Callable[[], None]) -> Callable[[], None]:
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
            print("stop control: could not post WM_QUIT to the hook thread", file=sys.stderr)
        thread.join(_JOIN_S)
        if thread.is_alive():
            print("stop control: the hook thread did not exit; F12 may stay swallowed until the process ends",
                  file=sys.stderr)

    if not ready.wait(_HOOK_READY_S) or state["error"] is not None:
        error = state["error"] or "hook thread did not start"
        remove()
        raise OSError(error)
    return remove


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
