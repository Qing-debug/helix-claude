"""Low-level keyboard listener and test sink window for the input harness (plan A, Task H1).

A WH_KEYBOARD_LL hook on its own thread timestamps every key event with perf_counter_ns and pushes it to a
queue; a writer thread drains the queue into ``keys.jsonl`` (C5). A small Tk window, "Helix input test sink",
runs on a third thread and is where the board's keys are meant to land. By default the hook blocks the seven bot
keys (C2) after recording them, so they reach no window procedure (PA-D7); ``--no-block`` turns that off.

Win32 facts used here (Microsoft Learn, read 2026-10-22):
- SetWindowsHookExW(int idHook, HOOKPROC lpfn, HINSTANCE hmod, DWORD dwThreadId) -> HHOOK, NULL on failure;
  WH_KEYBOARD_LL = 13, global only. The hook proc runs on the installing thread, which must pump messages.
- KBDLLHOOKSTRUCT {DWORD vkCode, scanCode, flags, time; ULONG_PTR dwExtraInfo}; flags bit 4 (0x10) =
  injected, bit 7 = transition state (1 = released).
- SetForegroundWindow(HWND) -> BOOL; the system restricts which processes may set the foreground, so its return
  value is not trusted here: GetForegroundWindow decides.
- FlashWindowEx(PFLASHWINFO) flashes a window and "does not change the active state of the window"; its return value
  is the caption's previous active state, not success. FLASHWINFO {UINT cbSize; HWND hwnd; DWORD dwFlags; UINT uCount;
  DWORD dwTimeout}; FLASHW_ALL 0x3 = caption and taskbar button, FLASHW_TIMERNOFG 0xC = flash continuously until the
  window comes to the foreground; dwTimeout 0 = the default caret blink rate (Microsoft Learn, read 2026-10-22).

Run: ``python -m src.input.harness.listener --out data/listener-check --seconds 20 [--no-block]``
"""
from __future__ import annotations

import argparse
import ctypes
import enum
import json
import queue
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path
from typing import NamedTuple, Protocol, TextIO

from src.input.protocol import Key

# ---- constants -------------------------------------------------------------------

WH_KEYBOARD_LL = 13
HC_ACTION = 0
WM_QUIT = 0x0012
PM_NOREMOVE = 0x0000
GA_ROOT = 2
LLKHF_INJECTED = 0x10
LLKHF_UP = 0x80

SINK_TITLE = "Helix input test sink"
SINK_LABEL = "Helix test window\nclick here once, then don't type"   # plain words for the operator (A-LF)
SINK_BG = "#ffe600"                                                   # bright yellow, black text
SINK_MIN_W, SINK_MIN_H = 600, 300
FLASHW_ALL = 0x00000003
FLASHW_TIMERNOFG = 0x0000000C
KEYS_FILE = "keys.jsonl"
_STARTUP_TIMEOUT_S = 5.0

# C2: the Windows VK each bot key arrives as in the LL hook (values confirmed against
# learn.microsoft.com/en-us/windows/win32/inputdev/virtual-key-codes, 2026-10-22).
VK_BY_KEY: dict[Key, int] = {
    Key.LEFT: 0x25,   # VK_LEFT
    Key.RIGHT: 0x27,  # VK_RIGHT
    Key.UP: 0x26,     # VK_UP
    Key.DOWN: 0x28,   # VK_DOWN
    Key.E: 0x45,      # E key
    Key.ALT: 0xA4,    # VK_LMENU (left Alt; arrives as WM_SYSKEYDOWN/UP)
    Key.SPACE: 0x20,  # VK_SPACE
}
BOT_VKS: frozenset[int] = frozenset(VK_BY_KEY.values())


def sink_rect(screen_w: int, screen_h: int) -> tuple[int, int, int, int]:
    """(w, h, x, y) of the sink window: a quarter of the screen width (at least 600x300), centred, never off the
    top-left. ``wm geometry`` takes x/y after a ``+`` as pixels from the left / top of the screen (Tk wm docs)."""
    w = max(SINK_MIN_W, screen_w // 4)
    h = max(SINK_MIN_H, w // 2)
    return w, h, max(0, (screen_w - w) // 2), max(0, (screen_h - h) // 2)


def should_block(vk: int, block: bool) -> bool:
    """True if the hook should swallow this key (one of the seven bot keys, blocking on; PA-D7)."""
    return block and vk in BOT_VKS


# ---- pure parts: transition tracking and the keys.jsonl writer --------------------

class Event(enum.Enum):
    DOWN = "down"
    UP = "up"
    REPEAT = "repeat"        # a down while already down: OS auto-repeat, not a transition
    ORPHAN_UP = "orphan_up"  # an up for a key not seen down (e.g. held before the listener started)


class TransitionTracker:
    """Classifies (vk, is_down) events per key. Pure; one instance per listener."""

    def __init__(self) -> None:
        self._down: set[int] = set()

    def feed(self, vk: int, is_down: bool) -> Event:
        if is_down:
            if vk in self._down:
                return Event.REPEAT
            self._down.add(vk)
            return Event.DOWN
        if vk in self._down:
            self._down.discard(vk)
            return Event.UP
        return Event.ORPHAN_UP


class RawKeyEvent(NamedTuple):
    qpc_ns: int   # time.perf_counter_ns() taken first thing in the hook callback
    vk: int       # KBDLLHOOKSTRUCT.vkCode
    flags: int    # KBDLLHOOKSTRUCT.flags
    tick_ms: int  # KBDLLHOOKSTRUCT.time (tick-based, 10-16 ms resolution)


class KeyLogWriter:
    """Writes RawKeyEvents as C5 ``keys.jsonl`` lines:
    {"qpc_ns", "vk", "down", "injected", "tick_ms", "repeat"}. An orphan up logs as a plain up."""

    def __init__(self, fh: TextIO) -> None:
        self._fh = fh
        self._tracker = TransitionTracker()

    def write(self, ev: RawKeyEvent) -> None:
        down = not (ev.flags & LLKHF_UP)
        kind = self._tracker.feed(ev.vk, down)
        row = {
            "qpc_ns": ev.qpc_ns,
            "vk": ev.vk,
            "down": down,
            "injected": bool(ev.flags & LLKHF_INJECTED),
            "tick_ms": ev.tick_ms,
            "repeat": kind is Event.REPEAT,
        }
        self._fh.write(json.dumps(row) + "\n")


# ---- Win32 bindings ------------------------------------------------------------------

LRESULT = ctypes.c_ssize_t
HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),  # ULONG_PTR
    ]


class FLASHWINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("hwnd", wintypes.HWND),
        ("dwFlags", wintypes.DWORD),
        ("uCount", wintypes.UINT),
        ("dwTimeout", wintypes.DWORD),
    ]


_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

_user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
_user32.SetWindowsHookExW.restype = wintypes.HHOOK
_user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
_user32.CallNextHookEx.restype = LRESULT
_user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
_user32.UnhookWindowsHookEx.restype = wintypes.BOOL
_user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
_user32.GetMessageW.restype = wintypes.BOOL
_user32.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT,
                                 wintypes.UINT]
_user32.PeekMessageW.restype = wintypes.BOOL
_user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
_user32.PostThreadMessageW.restype = wintypes.BOOL
_user32.GetForegroundWindow.argtypes = []
_user32.GetForegroundWindow.restype = wintypes.HWND
_user32.SetForegroundWindow.argtypes = [wintypes.HWND]
_user32.SetForegroundWindow.restype = wintypes.BOOL
_user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
_user32.GetAncestor.restype = wintypes.HWND
_user32.FlashWindowEx.argtypes = [ctypes.POINTER(FLASHWINFO)]
_user32.FlashWindowEx.restype = wintypes.BOOL
_kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
_kernel32.GetModuleHandleW.restype = wintypes.HMODULE


def flash_window(hwnd: int) -> None:
    """Flash the window's caption and taskbar button until it comes to the foreground, so the operator can find it.
    Does not activate it; the return value (the caption's previous state) says nothing about success, so it is dropped."""
    info = FLASHWINFO(ctypes.sizeof(FLASHWINFO), hwnd, FLASHW_ALL | FLASHW_TIMERNOFG, 0, 0)
    _user32.FlashWindowEx(ctypes.byref(info))


class Foreground(Protocol):
    def get_foreground_window(self) -> int: ...
    def set_foreground_window(self, hwnd: int) -> bool: ...


class User32Foreground:
    """The real GetForegroundWindow / SetForegroundWindow."""

    def get_foreground_window(self) -> int:
        return _user32.GetForegroundWindow() or 0

    def set_foreground_window(self, hwnd: int) -> bool:
        return bool(_user32.SetForegroundWindow(hwnd))


# ---- the listener ----------------------------------------------------------------------

_STOP = object()


class Listener:
    """Hook thread + writer thread + Tk sink thread. Build it with ``start()``."""

    def __init__(self, out_dir: str | Path, block: bool = True, foreground: Foreground | None = None) -> None:
        self.out_path = Path(out_dir) / KEYS_FILE
        self.block = block
        self.sink_hwnd: int | None = None
        self._fg: Foreground = foreground if foreground is not None else User32Foreground()
        self._queue: queue.SimpleQueue = queue.SimpleQueue()
        self._hook_tid: int | None = None
        self._hook_error: str | None = None
        self._writer_error: str | None = None
        self._sink_error: str | None = None
        self._writer_ready = threading.Event()
        self._hook_ready = threading.Event()
        self._sink_ready = threading.Event()
        self._sink_stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._stopped = False

    # -- foreground (A-PF I5) --
    def sink_is_foreground(self) -> bool:
        hwnd = self.sink_hwnd
        return hwnd is not None and self._fg.get_foreground_window() == hwnd

    def bring_sink_to_foreground(self) -> bool:
        """Ask for the foreground (no synthetic input, no AttachThreadInput); the re-check decides."""
        hwnd = self.sink_hwnd
        if hwnd is None:
            return False
        self._fg.set_foreground_window(hwnd)  # return value not trusted (PA-rev3b m-d)
        return self.sink_is_foreground()

    # -- threads --
    def _hook_thread(self) -> None:
        self._hook_tid = threading.get_native_id()
        msg = wintypes.MSG()
        # Create this thread's message queue now, so stop()'s PostThreadMessageW cannot miss it.
        _user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_NOREMOVE)
        put = self._queue.put
        block = self.block
        hook = None

        def proc(n_code: int, w_param: int, l_param: int) -> int:
            t = time.perf_counter_ns()
            if n_code == HC_ACTION:
                kb = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                vk = kb.vkCode
                put(RawKeyEvent(t, vk, kb.flags, kb.time))
                if should_block(vk, block):
                    return 1  # recorded; keep it from every window procedure (PA-D7)
            return _user32.CallNextHookEx(hook, n_code, w_param, l_param)

        c_proc = HOOKPROC(proc)  # keep a reference for the hook's lifetime
        hook = _user32.SetWindowsHookExW(WH_KEYBOARD_LL, c_proc, _kernel32.GetModuleHandleW(None), 0)
        if not hook:
            self._hook_error = f"SetWindowsHookExW failed, GetLastError={ctypes.get_last_error()}"
            self._hook_ready.set()
            return
        self._hook_ready.set()
        try:
            while _user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                pass  # no windows on this thread; the hook is called from inside GetMessageW
        finally:
            _user32.UnhookWindowsHookEx(hook)

    def _writer_thread(self) -> None:
        try:
            self.out_path.parent.mkdir(parents=True, exist_ok=True)
            fh = open(self.out_path, "a", encoding="utf-8", newline="\n")  # LF on Windows too (m5)
        except OSError as e:
            self._writer_error = f"cannot open {self.out_path}: {e}"
            self._writer_ready.set()
            return
        self._writer_ready.set()
        with fh:
            writer = KeyLogWriter(fh)
            while True:
                item = self._queue.get()
                if item is _STOP:
                    break
                writer.write(item)
                if self._queue.empty():
                    fh.flush()

    def _sink_thread(self) -> None:
        root = None
        try:
            import tkinter as tk

            root = tk.Tk()
            root.title(SINK_TITLE)
            # Tk's default WM_DELETE_WINDOW handler destroys the window ("At start-up Tk installs a handler for this
            # protocol, which responds by destroying the window", tcl-lang.org/man/tcl8.6/TkCmd/wm.htm), so the
            # root.destroy() below raised TclError. Closing now ends the thread the way stop() does; the window still
            # goes away, so the focus check goes False and the soak / probe FOCUS_LOST abort is unchanged.
            root.protocol("WM_DELETE_WINDOW", self._sink_stop.set)
            # Easy to find (A-LF): big, centred on the primary screen, above other windows, bright, plain words.
            w, h, x, y = sink_rect(root.winfo_screenwidth(), root.winfo_screenheight())
            root.geometry(f"{w}x{h}+{x}+{y}")
            root.configure(bg=SINK_BG)
            root.attributes("-topmost", True)  # "displays above all other windows" (Tk wm docs)
            # Labels only: no focusable Button/Checkbutton/Radiobutton/Entry/Text widget (H1 test-window rule).
            tk.Label(root, text=SINK_LABEL, font=("Segoe UI", 24, "bold"), bg=SINK_BG, fg="black", justify="center",
                     wraplength=w - 40).pack(expand=True, fill="both")
            root.update()
            self.sink_hwnd = _user32.GetAncestor(root.winfo_id(), GA_ROOT) or None
            if self.sink_hwnd is not None:
                flash_window(self.sink_hwnd)  # flashes only; does not activate, so the click-wait still sees a click
        except Exception as e:  # TclError, RuntimeError, ...: start() re-raises it as an OSError (m4)
            self._sink_error = f"test sink window failed: {e!r}"
            self._sink_ready.set()
            if root is not None:
                try:
                    root.destroy()
                except Exception:
                    pass
            return
        self._sink_ready.set()

        def poll() -> None:
            if self._sink_stop.is_set():
                root.quit()
            else:
                root.after(50, poll)

        root.after(50, poll)
        root.mainloop()
        root.destroy()
        del root

    def _spawn(self, target, name: str) -> None:
        th = threading.Thread(target=target, name=name, daemon=True)
        self._threads.append(th)
        th.start()

    def _start(self) -> None:
        self.out_path.parent.mkdir(parents=True, exist_ok=True)
        # The writer goes first and alone: if it fails, no hook exists yet, so stop() has nothing to unhook (N1).
        self._spawn(self._writer_thread, "listener-writer")
        if not self._writer_ready.wait(_STARTUP_TIMEOUT_S) or self._writer_error:
            err = self._writer_error or "writer thread did not start"
            self.stop()  # fewer than 3 threads: returns at once
            self._queue.put(_STOP)  # a writer that is merely late must still exit
            raise OSError(err)
        self._spawn(self._hook_thread, "listener-hook")
        self._spawn(self._sink_thread, "listener-sink")
        if not self._hook_ready.wait(_STARTUP_TIMEOUT_S) or self._hook_error:
            err = self._hook_error or "hook thread did not start"
            self.stop()
            raise OSError(err)
        if not self._sink_ready.wait(_STARTUP_TIMEOUT_S) or self.sink_hwnd is None:
            err = self._sink_error or "test sink window did not come up"
            self.stop()
            raise OSError(err)

    def stop(self) -> None:
        """Unhook, drain the queue into keys.jsonl, close the sink. Safe to call twice."""
        if self._stopped:
            return
        self._stopped = True
        if len(self._threads) < 3:  # never started
            return
        hook_th, sink_th = self._threads[1], self._threads[2]
        # A failed SetWindowsHookExW returns its thread at once: nothing to quit, and posting to the gone thread fails.
        if (self._hook_tid is not None and self._hook_error is None
                and not _user32.PostThreadMessageW(self._hook_tid, WM_QUIT, 0, 0)):
            print("could not post WM_QUIT to the hook thread", file=sys.stderr, flush=True)
        hook_th.join(_STARTUP_TIMEOUT_S)
        if hook_th.is_alive():
            print("hook thread did not exit; the bot keys may stay blocked until the process ends",
                  file=sys.stderr, flush=True)
        self._queue.put(_STOP)
        self._threads[0].join(_STARTUP_TIMEOUT_S)
        self._sink_stop.set()
        sink_th.join(_STARTUP_TIMEOUT_S)


def start(out_dir: str | Path, block: bool = True) -> Listener:
    """Start the hook, writer and sink threads; return once the hook is installed and the sink exists.
    Raises OSError if the writer, the hook or the sink does not come up, each within 5 s."""
    sys.setswitchinterval(0.001)  # cut GIL delay to the hook thread (inferred benefit; small per V1 m8 data, A-M8)
    lst = Listener(out_dir, block=block)
    lst._start()
    return lst


# ---- CLI ---------------------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="python -m src.input.harness.listener",
                                description="Log key events to <out>/keys.jsonl with a test sink window.")
    p.add_argument("--out", required=True, help="run directory; keys.jsonl is appended there")
    p.add_argument("--seconds", type=float, default=20.0, help="how long to listen (default 20)")
    p.add_argument("--no-block", dest="block", action="store_false",
                   help="pass the seven bot keys on to windows instead of blocking them (PA-D7)")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    lst = start(args.out, block=args.block)
    try:
        fg = lst.bring_sink_to_foreground()
        print(f"sink hwnd=0x{lst.sink_hwnd:X} foreground={fg} block={args.block} "
              f"listening {args.seconds:g} s -> {lst.out_path}", flush=True)
        if not fg:
            print(f"sink did not take the foreground; click the window titled '{SINK_TITLE}'",
                  file=sys.stderr, flush=True)
        time.sleep(args.seconds)
        print(f"sink foreground at end={lst.sink_is_foreground()}", flush=True)
    finally:
        lst.stop()
    with open(lst.out_path, encoding="utf-8") as fh:
        print(f"{sum(1 for _ in fh)} lines in {lst.out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
