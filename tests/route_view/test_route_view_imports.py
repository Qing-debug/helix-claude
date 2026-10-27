"""The route view's import boundaries (route view plan task RV-6, tests 12 and 13; spec 5.8).

`lint-imports` enforces the two contracts; these tests pin their module lists and check at run time that importing the
model does not pull in Tk, the game-input layer, the walker or the GUI.
"""

import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

BOUNDARIES = [
    "src.gui", "src.main", "src.vision", "src.rune_solver", "src.input.input_layer", "src.input.link",
    "src.input.actions", "src.input.timing", "src.input.harness", "src.route.walker", "src.route.walk",
    "src.route.moves", "src.route.runtime", "src.route.sim",
]
LEAF_SOURCES = ["src.route", "src.controller", "src.gui", "src.main", "src.vision", "src.input", "src.classes",
                "src.rune_solver"]


def _contracts():
    config = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    return {c["name"]: c for c in config["tool"]["importlinter"]["contracts"]}


def test_12_pyproject_has_both_route_view_contracts_with_exactly_these_modules():
    contracts = _contracts()
    boundaries = contracts["route_view boundaries"]
    assert boundaries["type"] == "forbidden" and boundaries["source_modules"] == ["src.route_view"]
    assert boundaries["forbidden_modules"] == BOUNDARIES
    leaf = contracts["route_view is a leaf"]
    assert leaf["type"] == "forbidden" and leaf["forbidden_modules"] == ["src.route_view"]
    assert leaf["source_modules"] == LEAF_SOURCES
    assert "allow_indirect_imports" not in boundaries and "allow_indirect_imports" not in leaf  # default: False


def test_12_every_src_input_module_but_protocol_is_forbidden():
    input_dir = REPO / "src" / "input"
    names = {p.stem for p in input_dir.glob("*.py") if p.stem != "__init__"}
    names |= {p.name for p in input_dir.iterdir() if p.is_dir() and p.name != "__pycache__"}
    forbidden = {m for m in _contracts()["route_view boundaries"]["forbidden_modules"] if m.startswith("src.input.")}
    assert forbidden == {f"src.input.{name}" for name in names - {"protocol"}}


@pytest.mark.slow
def test_13_importing_the_model_pulls_in_no_tk_input_layer_walker_or_gui():
    code = (
        "import sys; sys.path.insert(0, sys.argv[1]); import src.route_view.model; "
        "bad = [m for m in ('tkinter', 'src.input.input_layer', 'src.route.walker', 'src.gui') if m in sys.modules]; "
        "print(','.join(bad)); sys.exit(1 if bad else 0)"
    )
    done = subprocess.run([sys.executable, "-I", "-c", code, str(REPO)], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stdout + done.stderr
