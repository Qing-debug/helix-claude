"""Tk helpers for the route view tests (route view plan tasks RV-7 and RV-8).

A flat test helper, imported as `from view_tk import ...` (`tests/CLAUDE.md`, "Layout"); no `test_` prefix, so pytest
does not collect it. A test opens a real Tk root, withdraws it at once and never runs a mainloop, so nothing is shown
and nothing takes focus (as `tests/controller/test_gui_startup.py`).
"""

import time
import tkinter as tk
from collections.abc import Callable

import pytest


def open_root() -> tk.Tk:
    """A new withdrawn Tk root. On the dev PC, Tk now and then fails to read its own init files for a moment
    (`test_gui_startup.py:35-44`), so try four times 0.3 s apart and skip the test if Tk cannot open at all."""
    for attempt in range(4):
        try:
            root = tk.Tk()
        except tk.TclError as err:
            if attempt == 3:
                pytest.skip(f"Tk cannot open a display here: {err}")
            time.sleep(0.3)
        else:
            root.withdraw()
            return root
    raise AssertionError("unreachable")


def record_after(root: tk.Tk) -> list[tuple[int, Callable[[], object] | None]]:
    """Replace `root.after` by a recorder of `(delay_ms, callback)`; nothing is ever scheduled. Returns the list."""
    calls: list[tuple[int, Callable[[], object] | None]] = []

    def after(ms, func=None, *args):
        calls.append((ms, func))
        return f"after#{len(calls)}"

    root.after = after  # type: ignore[method-assign]
    return calls


def destroy_all(roots: list[tk.Tk]) -> None:
    for root in roots:
        try:
            root.destroy()
        except tk.TclError:
            pass
