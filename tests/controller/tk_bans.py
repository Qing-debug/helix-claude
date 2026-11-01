"""The Tk names the Route tab never uses (spec 5.12 rules 2-3; route tab plan RT-8a). `focus_problems` is a copy of
the one in tests/route_view/test_view_window.py (the function above `test_10`; test folders do not import each other,
tests/CLAUDE.md), extended with what the plan's Global Constraints ban and that one lacks: `Toplevel` and `Combobox` as
a name or an attribute, the icon calls, a called `.focus()`, and the `-toolwindow` and `-fullscreen` constants beside
`-topmost`. `test_route_tab.py` runs it over `src/gui/route_tab.py`."""
import ast

_FOCUS_ATTRS = {"focus_force", "focus_set", "lift", "tkraise", "grab_set"}
_WRAPPER_CALLS = {"resizable", "wm_resizable", "overrideredirect", "wm_overrideredirect", "transient", "wm_transient",
                  "attributes", "wm_attributes", "iconbitmap", "wm_iconbitmap", "iconphoto", "wm_iconphoto"}
_DIALOG_MODULES = {"messagebox", "filedialog", "simpledialog"}
_WINDOW_NAMES = {"Toplevel", "Combobox"}  # a new window: a Toplevel, or the drop-down list a Combobox posts
_STYLE_CONSTANTS = {"-topmost", "-toolwindow", "-fullscreen"}


def focus_problems(source: str) -> list[str]:
    """What in `source` takes focus, opens a dialog or a new window, or changes a window's style or icon."""
    problems = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Attribute) and node.attr in _FOCUS_ATTRS | _WINDOW_NAMES:
            problems.append(f"line {node.lineno}: .{node.attr}")
        if isinstance(node, ast.Name) and node.id in _WINDOW_NAMES:
            problems.append(f"line {node.lineno}: {node.id}")
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in _STYLE_CONSTANTS:
            problems.append(f"line {node.lineno}: '{node.value}'")
        if isinstance(node, ast.Import):
            problems += [f"line {node.lineno}: import {a.name}" for a in node.names
                         if a.name.split(".")[0] == "tkinter" and a.name.split(".")[-1] in _DIALOG_MODULES]
        if isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[0] == "tkinter":
            if node.module.split(".")[-1] in _DIALOG_MODULES:
                problems.append(f"line {node.lineno}: from {node.module}")
            problems += [f"line {node.lineno}: from {node.module} import {a.name}" for a in node.names
                         if a.name in _DIALOG_MODULES | _WINDOW_NAMES]
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and (func.attr in _WRAPPER_CALLS or func.attr == "focus"):
                problems.append(f"line {node.lineno}: .{func.attr}(...)")
            if node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value == "wm":
                problems.append(f"line {node.lineno}: call(\"wm\", ...)")
    return problems
