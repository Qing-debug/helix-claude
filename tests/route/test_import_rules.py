"""The P1b plan's hard rules for `src/route/`, checked by reading the source (no module is imported).

Plan: docs/superpowers/plans/2026-10-23-p1b-route-walking.md, "Hard rules for every task".
- Rule 4, "Reach stays in one place": no module under `src/route/` writes 19 or 23 as an integer literal.
- Rule 6, "Imports": every module imports only the standard library, numpy, networkx, returns, cv2,
  `src.input.{protocol,timing,actions,input_layer,link}`, `src.controller.*`, `src.classes.*` and `src.route.*`.
  The exceptions are `src/route/harness/*`, `src/route/runtime.py` and `src/route/walk.py` (not `walker.py`), which may
  also import `src.vision` and Windows-only modules, and use `ctypes.windll` and friends.
- Linux-collectable (rule 6 plus rule 9's Linux collection list): those Windows-only imports and ctypes names are
  allowed only inside functions, never at module level, so every route test file still collects on Linux.

Only static `import` statements are read. `__import__` and `importlib.import_module` calls are not seen.
Relative imports are resolved to their absolute name before the rules apply. Every `.py` file under `src/route/` is
scanned, `__init__.py` files included.
"""

import ast
import sys
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ROUTE = REPO / "src" / "route"

THIRD_PARTY = frozenset({"numpy", "networkx", "returns", "cv2"})
CONTROLLER_PREFIXES = ("src.controller", "src.classes", "src.route")
INPUT_MODULES = ("protocol", "timing", "actions", "input_layer", "link")
# Windows-only modules, by top-level name. Most of these are in `sys.stdlib_module_names` on every platform, so the
# stdlib test must not see them first.
WINDOWS_ONLY_TOP = frozenset({
    "msvcrt", "winreg", "winsound", "_winapi", "nt",
    "bettercam", "dxcam",
    "win32api", "win32con", "win32gui", "win32process", "win32file", "win32com", "pywintypes", "pythoncom",
    "comtypes", "pywinauto", "winrt",
})
CTYPES_WINDOWS_NAMES = frozenset({"windll", "oledll", "WinDLL", "OleDLL", "WINFUNCTYPE"})
VISION_PREFIX = "src.vision"
EXEMPT_FILES = frozenset({"runtime.py", "walk.py"})  # directly under src/route/; "walker.py" is not one of them
LITERALS = (19, 23)


def _under(name: str, prefix: str) -> bool:
    return name == prefix or name.startswith(prefix + ".")


def is_windows_exempt(rel: str) -> bool:
    """True for `src/route/harness/*`, `src/route/runtime.py` and `src/route/walk.py` (`rel` is repo-relative, with `/`)."""
    parts = Path(rel).relative_to("src/route").parts
    return parts[0] == "harness" or (len(parts) == 1 and parts[0] in EXEMPT_FILES)


def _package_parts(rel: str) -> list[str]:
    """The dotted package of a repo-relative file path: `src/route/sim/link.py` -> ['src', 'route', 'sim']."""
    parts = list(Path(rel).with_suffix("").parts)
    return parts[:-1]  # the package is the folder, for an `__init__.py` as for any other module


def _is_windows_only(module: str) -> bool:
    if module.split(".")[0] in WINDOWS_ONLY_TOP or _under(module, "ctypes.wintypes"):
        return True
    return module.startswith("ctypes.") and module.split(".")[1] in CTYPES_WINDOWS_NAMES


def _allowed_everywhere(module: str) -> bool:
    top = module.split(".")[0]
    if top in THIRD_PARTY or top in sys.stdlib_module_names:
        return True
    if any(_under(module, prefix) for prefix in CONTROLLER_PREFIXES):
        return True
    return any(_under(module, f"src.input.{name}") for name in INPUT_MODULES)


@dataclass(frozen=True)
class ImportRef:
    module: str  # absolute dotted name; `from a import b` gives `a.b`
    line: int
    at_import_time: bool  # not inside a def: the line runs when the module is imported


@dataclass(frozen=True)
class NameUse:
    name: str
    line: int
    at_import_time: bool


class _Scan(ast.NodeVisitor):
    def __init__(self, rel: str) -> None:
        self.package = _package_parts(rel)
        self.defs = 0
        self.imports: list[ImportRef] = []
        self.windows_names: list[NameUse] = []
        self.int_literals: list[tuple[int, int]] = []  # (line, value)

    def _in_def(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        # Decorators and argument defaults run when the `def` statement runs (at import time for a module-level def);
        # the rest (annotations, which are lazy on 3.14, and the body) counts as inside the def.
        args = node.args
        for expr in (*node.decorator_list, *args.defaults, *args.kw_defaults):
            if expr is not None:  # kw_defaults holds None for a keyword-only argument without a default
                self.visit(expr)
        self.defs += 1
        for child in (*args.posonlyargs, *args.args, args.vararg, *args.kwonlyargs, args.kwarg, node.returns,
                      *node.type_params, *node.body):
            if child is not None:
                self.visit(child)
        self.defs -= 1

    visit_FunctionDef = visit_AsyncFunctionDef = _in_def

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.imports.append(ImportRef(alias.name, node.lineno, self.defs == 0))

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        base = ".".join(self.package[:len(self.package) - (node.level - 1)]) if node.level else ""
        if node.module:
            base = f"{base}.{node.module}" if base else node.module
        for alias in node.names:
            self.imports.append(ImportRef(f"{base}.{alias.name}" if base else alias.name, node.lineno, self.defs == 0))

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr in CTYPES_WINDOWS_NAMES:
            self.windows_names.append(NameUse(node.attr, node.lineno, self.defs == 0))
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id in CTYPES_WINDOWS_NAMES:
            self.windows_names.append(NameUse(node.id, node.lineno, self.defs == 0))

    def visit_Constant(self, node: ast.Constant) -> None:
        if type(node.value) is int and node.value in LITERALS:
            self.int_literals.append((node.lineno, node.value))


def _scan(rel: str, source: str) -> _Scan:
    scan = _Scan(rel)
    scan.visit(ast.parse(source, filename=rel))
    return scan


# ---- the rules, each over one file's source ----------------------------------------------------------------------------

def import_allowlist_violations(rel: str, source: str) -> list[str]:
    """Plan rule 6: only allowlisted imports; `src.vision`, Windows-only modules and ctypes Windows names only in the exempt files."""
    exempt = is_windows_exempt(rel)
    scan = _scan(rel, source)
    found = []
    for ref in scan.imports:
        windows_only = _is_windows_only(ref.module)
        if windows_only or _under(ref.module, VISION_PREFIX):
            if not exempt:
                kind = "a Windows-only module" if windows_only else "src.vision"
                found.append(f"{rel}:{ref.line}: imports {ref.module} ({kind}; only harness/*, runtime.py and walk.py may)")
        elif not _allowed_everywhere(ref.module):
            found.append(f"{rel}:{ref.line}: imports {ref.module}, which is not on the plan rule 6 allowlist")
    if not exempt:
        found += [f"{rel}:{use.line}: uses {use.name} (a Windows API; only harness/*, runtime.py and walk.py may)"
                  for use in scan.windows_names]
    return found


def no_reach_literal_violations(rel: str, source: str) -> list[str]:
    """Plan rule 4: no integer literal 19 or 23."""
    return [f"{rel}:{line}: integer literal {value} (teleport reach is read from the CharacterClass)"
            for line, value in _scan(rel, source).int_literals]


def module_level_windows_violations(rel: str, source: str) -> list[str]:
    """Linux-collectable: `src.vision`, Windows-only imports and ctypes Windows names only inside functions, in every file."""
    scan = _scan(rel, source)
    found = [f"{rel}:{ref.line}: module-level import of {ref.module}; import it inside the function that needs it"
             for ref in scan.imports
             if ref.at_import_time and (_is_windows_only(ref.module) or _under(ref.module, VISION_PREFIX))]
    found += [f"{rel}:{use.line}: module-level use of {use.name}; use it inside the function that needs it"
              for use in scan.windows_names if use.at_import_time]
    return found


# ---- the tree ---------------------------------------------------------------------------------------------------------

def route_files() -> list[Path]:
    return sorted(ROUTE.rglob("*.py"))


def _violations(rule) -> list[str]:
    found = []
    for path in route_files():
        found += rule(path.relative_to(REPO).as_posix(), path.read_text(encoding="utf-8"))
    return found


def test_the_scan_sees_the_route_modules():
    names = {path.name for path in route_files()}
    assert {"planner.py", "walker.py", "route_graph.py", "record_minimap.py", "world.py"} <= names


def test_route_modules_import_only_the_plan_rule_6_allowlist():
    assert _violations(import_allowlist_violations) == []


def test_route_modules_write_no_teleport_reach_literal():
    assert _violations(no_reach_literal_violations) == []


def test_route_modules_import_no_vision_or_windows_api_at_module_level():
    assert _violations(module_level_windows_violations) == []


# ---- the rules catch what they say (injected sources; the repo's files are not touched) ----------------------------------

WALKER = "src/route/walker.py"
WALK = "src/route/walk.py"
HARNESS = "src/route/harness/record_minimap.py"


def test_exempt_files_are_harness_runtime_and_walk_only():
    assert is_windows_exempt("src/route/harness/record_minimap.py")
    assert is_windows_exempt("src/route/harness/deeper/x.py")
    assert is_windows_exempt("src/route/runtime.py")
    assert is_windows_exempt("src/route/walk.py")
    assert not is_windows_exempt("src/route/walker.py")
    assert not is_windows_exempt("src/route/sim/runtime.py")
    assert not is_windows_exempt("src/route/sim/link.py")


def test_clean_sources_pass_every_rule():
    source = ("from __future__ import annotations\nimport math\nimport numpy as np\nfrom returns.result import Success\n"
              "from src.controller.node import Node\nfrom src.input.protocol import Key\nfrom src.input import link\n"
              "from src.route.params import MovementParams\nx = 20\ny = 19.5\nz = '19'\nok = True\n")
    assert import_allowlist_violations(WALKER, source) == []
    assert no_reach_literal_violations(WALKER, source) == []
    assert module_level_windows_violations(WALKER, source) == []


def test_allowlist_rejects_a_gui_import():
    found = import_allowlist_violations(WALKER, "import src.gui.gui\n")
    assert len(found) == 1 and "src.gui.gui" in found[0] and found[0].startswith(f"{WALKER}:1:")


def test_allowlist_rejects_imports_outside_it():
    for source in ("from src.gui import gui\n", "import src.main\n", "from src.input.harness import soak\n",
                   "from src.input import harness\n", "import requests\n", "import src.input\n",
                   "from src import vision\n"):
        assert import_allowlist_violations(WALKER, source) != [], source


def test_allowlist_accepts_the_input_modules_by_either_spelling():
    for name in ("protocol", "timing", "actions", "input_layer", "link"):
        assert import_allowlist_violations(WALKER, f"import src.input.{name}\n") == []
        assert import_allowlist_violations(WALKER, f"from src.input import {name}\n") == []
        assert import_allowlist_violations(WALKER, f"from src.input.{name} import X\n") == []


def test_allowlist_resolves_relative_imports():
    assert import_allowlist_violations("src/route/sim/link.py", "from . import world\nfrom ..params import MovementParams\n") == []
    assert import_allowlist_violations("src/route/sim/link.py", "from ...gui import gui\n") != []
    assert import_allowlist_violations("src/route/__init__.py", "from . import walker\n") == []


def test_vision_and_windows_modules_are_allowed_only_in_the_exempt_files():
    for source in ("from src.vision.vision import capture\n", "import src.vision.vision\n", "import bettercam\n",
                   "import msvcrt\n", "from ctypes import wintypes\n", "import ctypes.wintypes\n", "import win32api\n",
                   "from ctypes import windll\n"):
        assert import_allowlist_violations(WALKER, source) != [], source
        assert import_allowlist_violations(WALK, source) == [], source
        assert import_allowlist_violations(HARNESS, source) == [], source


def test_allowlist_rejects_ctypes_windows_names_outside_the_exempt_files():
    source = "import ctypes\ndef f():\n    return ctypes.WinDLL('user32'), ctypes.windll, ctypes.WINFUNCTYPE\n"
    assert len(import_allowlist_violations(WALKER, source)) == 3
    assert import_allowlist_violations(WALK, source) == []


def test_module_level_rule_rejects_an_import_that_runs_at_import_time():
    for source in ("import src.vision.vision\n", "from src.vision import vision\n", "import bettercam\n",
                   "from ctypes import wintypes\n", "import winreg\n",
                   "try:\n    import msvcrt\nexcept ImportError:\n    msvcrt = None\n",
                   "class A:\n    import bettercam\n",
                   "import ctypes\nuser32 = ctypes.WinDLL('user32')\n",
                   "import ctypes\nHOOK = ctypes.WINFUNCTYPE(None)\n"):
        assert module_level_windows_violations(HARNESS, source) != [], source
        assert module_level_windows_violations(WALK, source) != [], source


def test_module_level_rule_accepts_the_same_imports_inside_functions():
    source = ("import ctypes\n"
              "def a():\n    import src.vision.vision\n    from ctypes import wintypes\n    return ctypes.windll\n"
              "async def b():\n    import bettercam\n"
              "class C:\n    def m(self):\n        import winreg\n")
    assert module_level_windows_violations(HARNESS, source) == []


def test_module_level_rule_rejects_a_windows_name_in_a_decorator():
    source = "import ctypes\n@ctypes.WINFUNCTYPE(None)\ndef cb():\n    pass\n"
    found = module_level_windows_violations(HARNESS, source)
    assert len(found) == 1 and found[0].startswith(f"{HARNESS}:2:") and "WINFUNCTYPE" in found[0]


def test_module_level_rule_rejects_a_windows_name_in_an_argument_default():
    for source in ("import ctypes\ndef f(h=ctypes.windll):\n    pass\n",
                   "import ctypes\ndef f(*, h=ctypes.windll):\n    pass\n"):
        found = module_level_windows_violations(HARNESS, source)
        assert len(found) == 1 and found[0].startswith(f"{HARNESS}:2:") and "windll" in found[0], source


def test_literal_rule_rejects_int_19_and_23_in_any_form():
    for source in ("x = 19\n", "y = -23\n", "def f(a=23):\n    return a + 19\n", "z = [1, (19,)]\n"):
        assert no_reach_literal_violations(WALKER, source) != [], source
    assert len(no_reach_literal_violations(WALKER, "def f(a=23):\n    return a + 19\n")) == 2


def test_literal_rule_ignores_other_numbers_strings_and_bools():
    for source in ("x = 190\n", "y = 2.3\n", "z = 19.0\n", "s = '19 and 23'\n", "b = True\n", "# 19\n", "w = 0x14\n"):
        assert no_reach_literal_violations(WALKER, source) == [], source
