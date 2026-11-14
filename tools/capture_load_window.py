"""The test window of the key-timing check (WALKFIX Task 10A, QB283 2b): a minimap that changes on every redraw, so the
capture thread has a new picture to take at every display frame and runs flat out.

    python tools/capture_load_window.py [--at X,Y]

It runs as a process of its own, as the game would. `tools/key_timing_check.py` starts it, and ends it when the check is
over; a double click on it closes it by hand. It sends no key and has no board.

What it shows: the four real corner pieces of the minimap border (`assets/brds/*_grayscale.png`, drawn where their masks
are set, with a small fixed noise, since a copy of a template scores a hair above 1 and the capture scrubs such a score)
around an inside of INSIDE_W x INSIDE_H px, which is a fixed textured background (never black) with a 5 px dot that moves
1 px per redraw. The border is the same on every redraw, so the capture's follow check (`src/vision/minimap_follow.py`)
holds and every picture is published. `load_picture(t)` builds the picture for redraw number t.

The window is borderless and always on top, and is not activated when it is shown or clicked: no focus call, and the
window style `WS_EX_NOACTIVATE` on Tk's wrapper window (`apply_no_activate`), which Windows documents as "does not become
the foreground window when the user clicks it". One path is left (review m1): Tk activates a process's first window when it
makes that window's wrapper (Tk 8.6.15 win/tkWinWm.c:2317-2319), during `update_idletasks` in `run_window`, while the
window is still hidden and before the style is added, so the console can lose the keyboard at start-up; the check asks for
a click on the console before its question. Not proved on a live screen (no window was opened when it was written). DPI-aware before Tk starts, so the corners are drawn 1:1 on screen. Redrawn with `after(1)`; Tk ticks that
at about 1 kHz on the dev PC (measured), above any display's frame rate.

`--at X,Y` places the window's top-left corner on the virtual screen; the default is the right edge of the primary screen
(x = its width, y = 0), where a second screen on the right begins.
"""
import argparse
import ctypes
import functools
import sys
from pathlib import Path

import cv2
import numpy as np

REPO = Path(__file__).resolve().parents[1]
BRDS = REPO / "assets" / "brds"
CORNERS = ("left_top", "right_top", "right_bot", "left_bot")

INSIDE_W, INSIDE_H = 200, 140  # the minimap's inside, between the border lines
LEFT, TOP = 3, 3               # the border lines' first column and row in the picture
RIGHT, BOTTOM = LEFT + INSIDE_W, TOP + INSIDE_H
# Top-left of each corner piece, from the border lines: the offsets tests/vision/test_minimap_follow.py's screens use.
SPOTS = {"left_top": (LEFT - 3, TOP - 3), "right_top": (RIGHT - 11, TOP - 3),
         "right_bot": (RIGHT - 11, BOTTOM - 11), "left_bot": (LEFT - 3, BOTTOM - 8)}
PICTURE_W, PICTURE_H = RIGHT - 11 + 18, BOTTOM - 8 + 15  # the union of the four pieces: 210 x 150

OUTSIDE_GRAY = 24
BACKGROUND_RANGE = (40, 110)  # the inside's fixed texture; never black (`isMinimapBlack`)
CORNER_NOISE = 3              # +-3 gray levels on the masked border pixels, fixed
SEED = 1
DOT_SIZE = 5
DOT_COLOR = (255, 200, 0)
DOT_MARGIN = 20               # the dot stays this far inside the border lines, clear of every corner piece
DOT_ROW_STEP = 10             # after a pass across, the dot goes this many rows down
REDRAW_MS = 1

GWL_EXSTYLE = -20
WS_EX_NOACTIVATE = 0x08000000


@functools.cache
def _base() -> np.ndarray:
    """The picture without the dot: the same on every redraw. Never handed out, only copied."""
    rng = np.random.default_rng(SEED)
    base = np.full((PICTURE_H, PICTURE_W, 3), OUTSIDE_GRAY, np.uint8)
    texture = rng.integers(*BACKGROUND_RANGE, (INSIDE_H, INSIDE_W), dtype=np.uint8)
    base[TOP:BOTTOM, LEFT:RIGHT] = texture[:, :, None]
    for corner in CORNERS:
        piece = cv2.imread(str(BRDS / f"{corner}_grayscale.png"), cv2.IMREAD_GRAYSCALE)
        mask = cv2.imread(str(BRDS / f"{corner}_mask.png"), cv2.IMREAD_GRAYSCALE)
        if piece is None or mask is None:
            raise FileNotFoundError(f"the border pieces of {corner} are missing in {BRDS}")
        ys, xs = np.nonzero(mask > 0)
        noise = rng.integers(-CORNER_NOISE, CORNER_NOISE + 1, len(ys))
        x, y = SPOTS[corner]
        base[y + ys, x + xs] = np.clip(piece[ys, xs].astype(np.int16) + noise, 0, 255).astype(np.uint8)[:, None]
    return base


def load_picture(t: int) -> np.ndarray:
    """The window's picture for redraw number `t`, an RGB uint8 array of PICTURE_H x PICTURE_W x 3, new on each call.
    The dot is at one column further on at every `t` (and back at the start after a pass), over a fixed background."""
    picture = _base().copy()
    span = INSIDE_W - 2 * DOT_MARGIN - DOT_SIZE + 1
    rows = INSIDE_H - 2 * DOT_MARGIN - DOT_SIZE + 1
    x = LEFT + DOT_MARGIN + t % span
    y = TOP + DOT_MARGIN + (t // span * DOT_ROW_STEP) % rows
    picture[y:y + DOT_SIZE, x:x + DOT_SIZE] = DOT_COLOR
    return picture


def parse_at(text: str) -> tuple[int, int]:
    """`X,Y` as two integers (negative is allowed: a screen to the left or above the primary one)."""
    parts = text.split(",")
    try:
        if len(parts) != 2:
            raise ValueError
        return int(parts[0]), int(parts[1])
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected X,Y as two whole numbers (for example 3840,0), got {text!r}") from None


def default_at(primary_width: int) -> tuple[int, int]:
    return primary_width, 0


def apply_no_activate(user32, hwnd) -> None:
    """Add `WS_EX_NOACTIVATE` to the top-level window of `hwnd`: its parent, Tk's wrapper window, which holds the style
    (an override-redirect Tk window has one, seen with `GetParent` on a withdrawn window)."""
    top = user32.GetParent(hwnd) or hwnd
    user32.SetWindowLongPtrW(top, GWL_EXSTYLE, user32.GetWindowLongPtrW(top, GWL_EXSTYLE) | WS_EX_NOACTIVATE)


def _user32():
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetParent.argtypes = [wintypes.HWND]
    user32.GetParent.restype = wintypes.HWND
    user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.GetSystemMetrics.argtypes = [ctypes.c_int]
    user32.GetSystemMetrics.restype = ctypes.c_int
    return user32


def _make_dpi_aware() -> None:
    """Per-monitor DPI awareness (2), before Tk starts, so a pixel of the picture is a pixel on screen."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        pass  # already set, or an older Windows: the picture may be scaled and the capture will not find it


def run_window(at: tuple[int, int] | None) -> None:
    import tkinter as tk

    from PIL import Image, ImageTk

    _make_dpi_aware()
    user32 = _user32()
    x, y = at if at is not None else default_at(user32.GetSystemMetrics(0))  # 0 = SM_CXSCREEN, the primary screen's width
    root = tk.Tk()
    root.withdraw()
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.geometry(f"{PICTURE_W}x{PICTURE_H}{x:+d}{y:+d}")
    photo = ImageTk.PhotoImage(Image.fromarray(load_picture(0)), master=root)
    label = tk.Label(root, image=photo, borderwidth=0, highlightthickness=0, padx=0, pady=0)
    label.pack()
    root.update_idletasks()
    apply_no_activate(user32, root.winfo_id())  # before the window is shown, so showing or clicking it does not activate it
    # No wm attribute or overrideredirect call after this line: Tk keeps its own copy of the wrapper's ex-style, and a
    # wrapper rebuild (a later -toolwindow, -fullscreen or overrideredirect change) drops WS_EX_NOACTIVATE silently
    # (tkWinWm.c:3245; review m3). test_nothing_rebuilds_the_wrapper_after_the_style_is_added pins it.
    label.bind("<Double-Button-1>", lambda _event: root.destroy())  # the way out by hand: a mouse click, no key
    redraw = {"t": 0}

    def tick() -> None:
        redraw["t"] += 1
        photo.paste(Image.fromarray(load_picture(redraw["t"])))
        root.after(REDRAW_MS, tick)

    root.deiconify()
    root.after(REDRAW_MS, tick)
    root.mainloop()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="A test minimap window that changes on every redraw (the key-timing check's load).")
    parser.add_argument("--at", type=parse_at, default=None, metavar="X,Y",
                        help="where the window's top-left corner goes (default: the right edge of the primary screen, y 0)")
    args = parser.parse_args(argv)
    run_window(args.at)
    return 0


if __name__ == "__main__":
    sys.exit(main())
