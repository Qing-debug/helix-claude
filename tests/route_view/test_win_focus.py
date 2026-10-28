"""Tests of the route view's Win32 focus calls (route view plan task RV-8, tests 10-14; spec 5.7).

Test 10 uses a real withdrawn Tk root and the real Win32 calls on Windows (nothing is shown: no `show_first` here, a
shown window is N1's job). The others patch `sys.platform` and `ctypes.WinDLL` and run anywhere.
"""

import ast
import ctypes
import sys
import tkinter as tk

import pytest
from returns.pipeline import is_successful
from returns.result import Failure, Success
from view_runs import REPO_ROOT
from view_tk import destroy_all, open_root

from src.route_view import win_focus

ROUTE_VIEW_SRC = REPO_ROOT / "src" / "route_view"


def _real_user32():
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetParent.argtypes = [ctypes.c_void_p]
    user32.GetParent.restype = ctypes.c_void_p
    user32.GetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int]
    user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    return user32


# 10 ---------------------------------------------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 calls")
def test_10_the_style_goes_on_the_wrapper_that_exists_after_an_idle_pass_and_survives_title_changes():
    root = open_root()  # tk.Tk() + withdraw(), as __main__ builds it
    try:
        tk.Label(root, text="route view").pack()
        assert int(root.wm_frame(), 0) == root.winfo_id()  # the fact the guard is for (RV6 I1)
        assert win_focus.wrapper_hwnd(root) == Failure("no wrapper yet")
        root.update_idletasks()
        user32 = _real_user32()
        found = win_focus.wrapper_hwnd(root)
        assert is_successful(found), found
        h = found.unwrap()
        assert h == user32.GetParent(root.winfo_id()) and h != root.winfo_id()
        assert root.state() == "withdrawn"

        def ex_style() -> int:
            return user32.GetWindowLongPtrW(h, win_focus.GWL_EXSTYLE)

        assert win_focus.set_live_style(h, True) == Success(None)
        assert ex_style() & win_focus.WS_EX_NOACTIVATE and not ex_style() & win_focus.WS_EX_APPWINDOW
        for text in ("Route view · LIVE · a", "Route view · LIVE · b", "Route view · LIVE · c"):
            root.title(text)  # what the live ticks do
        root.update()
        assert win_focus.wrapper_hwnd(root) == Success(h)  # the same wrapper: the style was not dropped (RV8 n1)
        assert ex_style() & win_focus.WS_EX_NOACTIVATE
        assert win_focus.set_live_style(h, False) == Success(None)
        assert not ex_style() & win_focus.WS_EX_NOACTIVATE and ex_style() & win_focus.WS_EX_APPWINDOW
        assert root.state() == "withdrawn"
    finally:
        destroy_all([root])


# 11 ---------------------------------------------------------------------------------------------------------------


class _Root:
    def __init__(self):
        self.calls = []

    def wm_frame(self):
        self.calls.append("wm_frame")
        return "0x10"

    def winfo_id(self):
        self.calls.append("winfo_id")
        return 0x20

    def deiconify(self):
        self.calls.append("deiconify")


def test_11_off_windows_every_call_is_a_failure_before_ctypes_is_touched(monkeypatch):
    def no_dll(*args, **kwargs):
        raise AssertionError("ctypes.WinDLL called")

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(ctypes, "WinDLL", no_dll, raising=False)
    root = _Root()
    assert win_focus.wrapper_hwnd(root) == Failure("not Windows")
    assert win_focus.set_live_style(1234, True) == Failure("not Windows")
    assert win_focus.set_live_style(1234, False) == Failure("not Windows")
    assert win_focus.show_first(root, 1234) == Failure("not Windows")
    assert root.calls == []


# 12 ---------------------------------------------------------------------------------------------------------------


def import_time_ctypes(source: str) -> list[str]:
    """Every `ctypes` import and every `ctypes` name that runs at import time (outside a function body)."""
    problems: list[str] = []

    def visit(node: ast.AST, in_function: bool) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for outside in (*node.decorator_list, node.args, *([node.returns] if node.returns else [])):
                visit(outside, in_function)
            for statement in node.body:
                visit(statement, True)
            return
        if isinstance(node, ast.Lambda):
            visit(node.args, in_function)
            visit(node.body, True)
            return
        if not in_function:
            if isinstance(node, ast.Import):
                problems.extend(f"line {node.lineno}: import {a.name}" for a in node.names
                                if a.name.split(".")[0] == "ctypes")
            if isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[0] == "ctypes":
                problems.append(f"line {node.lineno}: from {node.module}")
            if isinstance(node, ast.Name) and node.id == "ctypes":
                problems.append(f"line {node.lineno}: ctypes")
        for child in ast.iter_child_nodes(node):
            visit(child, in_function)

    visit(ast.parse(source), False)
    return problems


def test_12_ctypes_is_used_only_inside_functions():
    sources = sorted(ROUTE_VIEW_SRC.glob("*.py"))
    assert {p.name for p in sources} >= {"__main__.py", "follow.py", "win_focus.py", "window.py", "model.py"}
    for path in sources:
        assert import_time_ctypes(path.read_text(encoding="utf-8")) == [], path.name
    assert "ctypes" in (ROUTE_VIEW_SRC / "win_focus.py").read_text(encoding="utf-8")  # it is used, inside functions


def test_12_the_walk_finds_a_planted_sample():
    planted = "\n".join([
        "import ctypes",
        "from ctypes import wintypes",
        "import ctypes.wintypes as w",
        "DLL = ctypes.WinDLL('user32')",
        "def f(dll=ctypes.windll):",
        "    import ctypes",
        "    return ctypes.WinDLL('user32')",
        "class C:",
        "    size = ctypes.sizeof(ctypes.c_void_p)",
        "    def g(self):",
        "        return ctypes.c_int(1)",
        "h = lambda: ctypes.c_int(2)",
    ])
    found = import_time_ctypes(planted)
    assert {int(p.split(":")[0].split()[1]) for p in found} == {1, 2, 3, 4, 5, 9}, found
    assert import_time_ctypes("def f():\n    import ctypes\n    return ctypes.c_int(0)\n") == []


# 13, 14: a scripted user32 ----------------------------------------------------------------------------------------


class _Fn:
    def __init__(self, dll, name):
        self.dll, self.name = dll, name

    def __call__(self, *args):
        self.dll.log.append((self.name, args))
        result, error = self.dll.script.get(self.name, (1, 0))
        if error:
            self.dll.last_error = error
        return result


class _User32:
    """Stands in for `ctypes.WinDLL("user32", use_last_error=True)`: each function returns its scripted
    `(result, last error)`; `ctypes.set_last_error`/`get_last_error` are patched to this object's copy."""

    def __init__(self, script=None):
        self.script = dict(script or {})
        self.log = []
        self.last_error = 0
        self.opened = []

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        fn = _Fn(self, name)
        object.__setattr__(self, name, fn)
        return fn


@pytest.fixture
def user32(monkeypatch):
    dll = _User32()

    def win_dll(name, use_last_error=False):
        dll.opened.append((name, use_last_error))
        return dll

    def set_last_error(value):
        dll.log.append(("set_last_error", (value,)))
        old, dll.last_error = dll.last_error, value
        return old

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(ctypes, "WinDLL", win_dll, raising=False)
    monkeypatch.setattr(ctypes, "set_last_error", set_last_error, raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: dll.last_error, raising=False)
    return dll


def test_13_first_show_is_noactivate_and_deiconify_is_its_one_line_fallback(user32, monkeypatch):
    assert win_focus.FIRST_SHOW == "noactivate"
    root = _Root()
    assert win_focus.show_first(root, 1234) == Success(None)
    assert user32.log == [("ShowWindow", (1234, win_focus.SW_SHOWNOACTIVATE))] and root.calls == []
    assert user32.opened == [("user32", True)]
    user32.log.clear()
    monkeypatch.setattr(win_focus, "FIRST_SHOW", "deiconify")
    assert win_focus.show_first(root, 1234) == Success(None)
    assert root.calls == ["deiconify"] and user32.log == []


def test_14_showwindow_returns_the_previous_visibility_never_an_error(user32):
    root = _Root()
    user32.script["ShowWindow"] = (0, 0)  # the window was hidden: the first show's normal answer
    assert win_focus.show_first(root, 1234) == Success(None) and root.calls == []
    user32.script["ShowWindow"] = (24, 0)
    assert win_focus.show_first(root, 1234) == Success(None) and root.calls == []


def test_14_set_live_style_reads_zero_with_the_last_error_and_sets_the_right_bits(user32):
    flags = (win_focus.SWP_NOSIZE | win_focus.SWP_NOMOVE | win_focus.SWP_NOZORDER | win_focus.SWP_NOACTIVATE
             | win_focus.SWP_FRAMECHANGED)
    old = 0x100 | win_focus.WS_EX_APPWINDOW
    user32.script["GetWindowLongPtrW"] = (old, 0)
    user32.script["SetWindowLongPtrW"] = (old, 0)
    assert win_focus.set_live_style(1234, True) == Success(None)
    assert user32.log == [
        ("set_last_error", (0,)), ("GetWindowLongPtrW", (1234, win_focus.GWL_EXSTYLE)),
        ("set_last_error", (0,)), ("SetWindowLongPtrW", (1234, win_focus.GWL_EXSTYLE, 0x100 | win_focus.WS_EX_NOACTIVATE)),
        ("SetWindowPos", (1234, None, 0, 0, 0, 0, flags)),
    ]
    assert flags == 0x37
    user32.log.clear()
    user32.script["GetWindowLongPtrW"] = (0x100 | win_focus.WS_EX_NOACTIVATE, 0)
    assert win_focus.set_live_style(1234, False) == Success(None)
    assert user32.log[3] == ("SetWindowLongPtrW", (1234, win_focus.GWL_EXSTYLE, 0x100 | win_focus.WS_EX_APPWINDOW))

    user32.script["GetWindowLongPtrW"] = (0, 0)  # an ex-style of 0 is a real value
    user32.script["SetWindowLongPtrW"] = (0, 0)  # the previous value was 0 and the call worked
    assert win_focus.set_live_style(1234, True) == Success(None)

    user32.last_error = 7  # stale: set_last_error(0) before each call must clear it
    assert win_focus.set_live_style(1234, True) == Success(None)

    user32.script["SetWindowLongPtrW"] = (0, 5)
    failed = win_focus.set_live_style(1234, True)
    assert not is_successful(failed) and "5" in failed.failure() and "SetWindowLongPtrW" in failed.failure()

    user32.script["SetWindowLongPtrW"] = (0x100, 0)
    user32.script["GetWindowLongPtrW"] = (0, 1400)
    failed = win_focus.set_live_style(1234, True)
    assert not is_successful(failed) and "1400" in failed.failure() and "GetWindowLongPtrW" in failed.failure()

    user32.script["GetWindowLongPtrW"] = (0x100, 0)
    user32.script["SetWindowPos"] = (0, 5)
    user32.last_error = 0
    failed = win_focus.set_live_style(1234, True)
    assert not is_successful(failed) and "SetWindowPos" in failed.failure() and "5" in failed.failure()


def test_wrapper_hwnd_reads_wm_frame_as_a_number(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    root = _Root()
    assert win_focus.wrapper_hwnd(root) == Success(0x10)
    root.wm_frame = lambda: "0x20"  # type: ignore[method-assign]
    assert win_focus.wrapper_hwnd(root) == Failure("no wrapper yet")
