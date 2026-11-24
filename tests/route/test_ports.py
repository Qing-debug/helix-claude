"""The walker's shared basics live in `src/route/ports.py` and `walker.py` re-exports them (cleanup piece W1-6, R30).

The frozen tests, the simulator, the session, the walk CLI and `tools/route_tab_sim.py` still import these names from
`src.route.walker`; the re-export must be the very object `ports` defines, not a copy.
"""

import ast
from pathlib import Path

import pytest

from src.route import ports, walker

REPO = Path(__file__).resolve().parents[2]
NAMES = ("STALE", "Clock", "PositionSource", "RealClock", "StepObserver")


@pytest.mark.parametrize("name", NAMES)
def test_walker_re_exports_the_ports_object(name):
    assert getattr(walker, name) is getattr(ports, name)


def test_stale_keeps_its_value():
    assert ports.STALE == "stale"


def test_the_names_are_defined_only_in_ports():
    defined = {}
    for path in sorted((REPO / "src").rglob("*.py")):
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            if isinstance(node, ast.ClassDef):
                names = [node.name]
            elif isinstance(node, ast.Assign):
                names = [t.id for t in node.targets if isinstance(t, ast.Name)]
            else:
                continue
            for name in names:
                if name in NAMES:
                    defined.setdefault(name, []).append(path.relative_to(REPO).as_posix())
    assert defined == {name: ["src/route/ports.py"] for name in NAMES}
