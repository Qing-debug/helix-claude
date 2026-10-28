"""The route view's Win32 focus calls (route view plan task RV-8; spec 5.7).

The live window must never take focus from the game: it gets `WS_EX_NOACTIVATE` (and loses `WS_EX_APPWINDOW`) on Tk's
wrapper window before its first show, is first shown with `ShowWindow(SW_SHOWNOACTIVATE)`, and gets the styles back at
`end`. Every `ctypes` use is inside a function (spec 5.7: S6 and cloud runs import this package on Linux), and each
function returns `Failure("not Windows")` off Windows before touching `ctypes`. Nothing here raises.

Sources (Microsoft Learn, fetched 2026-10-27):
- https://learn.microsoft.com/en-us/windows/win32/winmsg/extended-window-styles: `WS_EX_NOACTIVATE` 0x08000000L "A
  top-level window created with this style does not become the foreground window when the user clicks it. [...] The
  window does not appear on the taskbar by default. To force the window to appear on the taskbar, use the
  WS_EX_APPWINDOW style."; `WS_EX_APPWINDOW` 0x00040000L.
- https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setwindowpos: the SWP_ values below, "If you
  have changed certain window data using SetWindowLong, you must call SetWindowPos for the changes to take effect. Use
  the following combination for uFlags: SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED"; "If the function
  fails, the return value is zero."
- https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-showwindow: `SW_SHOWNOACTIVATE` 4 "similar to
  SW_SHOWNORMAL, except that the window is not activated"; "If the window was previously hidden, the return value is
  zero." (so the return value is never an error).
- https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setwindowlongptrw: zero with a nonzero
  last error is a failure, after clearing the last error first; `GWL_EXSTYLE` -20.
- Tk's `wm frame` (https://www.tcl-lang.org/man/tcl8.6/TkCmd/wm.htm): "If window has not been reparented by the
  window manager then the command returns the platform specific window identifier for window." Before Tk's first idle
  pass a withdrawn root has no wrapper yet, so `wrapper_hwnd` refuses rather than return the inner window (RV6 I1).
"""

import sys
import tkinter as tk
from typing import Any

from returns.result import Failure, Result, Success

GWL_EXSTYLE = -20
WS_EX_NOACTIVATE = 0x08000000
WS_EX_APPWINDOW = 0x00040000
SW_SHOWNOACTIVATE = 4
SWP_NOSIZE, SWP_NOMOVE, SWP_NOZORDER, SWP_NOACTIVATE, SWP_FRAMECHANGED = 0x1, 0x2, 0x4, 0x10, 0x20
FIRST_SHOW = "noactivate"  # spec 5.7's fallback is "deiconify": the one-line change if N1's first-show part fails

_NOT_WINDOWS = "not Windows"


def _user32() -> Any:
    """user32 with the last error kept per call (`use_last_error=True`) and the four functions' types set."""
    import ctypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    hwnd, long_ptr = ctypes.c_void_p, ctypes.c_ssize_t
    user32.GetWindowLongPtrW.argtypes = [hwnd, ctypes.c_int]
    user32.GetWindowLongPtrW.restype = long_ptr
    user32.SetWindowLongPtrW.argtypes = [hwnd, ctypes.c_int, long_ptr]
    user32.SetWindowLongPtrW.restype = long_ptr
    user32.SetWindowPos.argtypes = [hwnd, hwnd, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
    user32.SetWindowPos.restype = ctypes.c_int
    user32.ShowWindow.argtypes = [hwnd, ctypes.c_int]
    user32.ShowWindow.restype = ctypes.c_int
    return user32


def wrapper_hwnd(root: Any) -> Result[int, str]:
    """The HWND of Tk's wrapper window (`wm frame`), the window Windows activates; `Failure("no wrapper yet")` while
    `wm frame` still names the root's own window (before Tk's first idle pass)."""
    if sys.platform != "win32":
        return Failure(_NOT_WINDOWS)
    try:
        frame = int(root.wm_frame(), 0)
        inner = int(root.winfo_id())
    except (tk.TclError, ValueError, TypeError) as err:
        return Failure(f"cannot read the wrapper window: {err}")
    if frame == inner:
        return Failure("no wrapper yet")
    return Success(frame)


def set_live_style(hwnd: int, live: bool) -> Result[None, str]:
    """Live: add `WS_EX_NOACTIVATE`, drop `WS_EX_APPWINDOW`; not live: the reverse. Then `SetWindowPos` with
    `SWP_FRAMECHANGED` so the change takes effect, moving, sizing, reordering and activating nothing."""
    if sys.platform != "win32":
        return Failure(_NOT_WINDOWS)
    import ctypes

    try:
        user32 = _user32()
        ctypes.set_last_error(0)
        style = user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
        error = ctypes.get_last_error()
        if style == 0 and error != 0:
            return Failure(f"GetWindowLongPtrW failed: error {error}")
        new = (style | WS_EX_NOACTIVATE) & ~WS_EX_APPWINDOW if live else (style & ~WS_EX_NOACTIVATE) | WS_EX_APPWINDOW
        ctypes.set_last_error(0)
        previous = user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, new)
        error = ctypes.get_last_error()
        if previous == 0 and error != 0:
            return Failure(f"SetWindowLongPtrW failed: error {error}")
        flags = SWP_NOSIZE | SWP_NOMOVE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED
        if not user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, flags):
            return Failure(f"SetWindowPos failed: error {ctypes.get_last_error()}")
    except (OSError, AttributeError, ctypes.ArgumentError) as err:
        return Failure(f"user32 call failed: {err}")
    return Success(None)


def show_first(root: Any, hwnd: int) -> Result[None, str]:
    """The live window's first show. `FIRST_SHOW == "noactivate"`: `ShowWindow(hwnd, SW_SHOWNOACTIVATE)`, a Success
    whatever it returns (its return value is the previous visibility); `"deiconify"`: Tk's `root.deiconify()`."""
    if sys.platform != "win32":
        return Failure(_NOT_WINDOWS)
    if FIRST_SHOW == "deiconify":
        root.deiconify()
        return Success(None)
    import ctypes

    try:
        _user32().ShowWindow(hwnd, SW_SHOWNOACTIVATE)
    except (OSError, AttributeError, ctypes.ArgumentError) as err:
        return Failure(f"ShowWindow failed: {err}")
    return Success(None)
