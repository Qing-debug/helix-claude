"""Pins the seams the MapleStory-guard tests patch, so the guard cannot move behind them unnoticed.

The harness tools that send keys check for a MapleStory process first (hard rule 2). Their tests never run the real
check: they replace `soak._process_names` with `monkeypatch.setattr`. That works only while `soak.maple_check` reads
`_process_names` from the global names of `soak`. If the guard moved to another module and `soak` re-exported it, the
patch would still be accepted, the guard tests would run the real `tasklist`, and they would pass or fail by what
happens to be running. This file fails first. It pins where things are; it moves and changes nothing.

Not run: no harness tool, no listener, no link, no board, no key. `_process_names` is patched in every test that calls
`maple_check`, so `tasklist` never runs either. The harness modules import Windows-only code (ctypes `WinDLL`, winrt)
at module level, so this file imports them inside a fixture and skips on other platforms (plan hard rule 9).
"""

import ast
import importlib
import sys
import types
from pathlib import Path

import pytest
from returns.result import Failure, Success

pytestmark = pytest.mark.skipif(sys.platform != "win32",
                                reason="the harness modules import Windows-only ctypes and winrt names at module level")

SOAK, PROBE, SESSION_STARTS, INPUT_LAYER = (
    "src.input.harness.soak", "src.input.harness.probe", "src.input.harness.session_starts", "src.input.input_layer")

# Every name a test in tests/input patches with `monkeypatch.setattr(<module>, "<name>", ...)` on these modules and
# that a guard test relies on: the probe set is `PROBES, _board_list_json, _new_link, _start_sink, run_probes, _clock,
# _sleep` (test_probe patches them on `probe`); soak's `_process_names` is patched by test_soak, test_probe,
# test_session_starts and test_soak_v2_faults.
PATCHED = {
    PROBE: ("PROBES", "_board_list_json", "_new_link", "_start_sink", "run_probes", "_clock", "_sleep"),
    SOAK: ("_process_names", "_new_link", "_start_sink", "_clock", "_sleep", "check_focus"),
    SESSION_STARTS: ("INJECTORS", "_new_link", "_start_sink", "_clock", "_sleep"),
    INPUT_LAYER: ("_clock", "_sleep"),
}
SEAMS = [(module, name) for module, names in PATCHED.items() for name in names]


@pytest.fixture(scope="module")
def modules():
    return {name: importlib.import_module(name) for name in PATCHED}


def _defined_in_own_source(module) -> set[str]:
    """Top-level names the module's own source binds with `def`, `class` or an assignment (not with an import)."""
    found = set()
    for node in ast.parse(Path(module.__file__).read_text(encoding="utf-8")).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            found.add(node.name)
        elif isinstance(node, ast.Assign):
            found.update(target.id for target in node.targets if isinstance(target, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            found.add(node.target.id)
    return found


@pytest.mark.parametrize("module_name, name", SEAMS)
def test_a_patched_name_is_defined_in_the_module_the_tests_patch(modules, module_name, name):
    module = modules[module_name]
    assert hasattr(module, name), f"{module_name}.{name} is gone; the tests that patch it fail or patch nothing"
    assert name in _defined_in_own_source(module), (
        f"{module_name}.{name} is not defined in {Path(module.__file__).name}; a patch there would not reach its readers")


# ---- the guard itself, by name lookup -------------------------------------------------------------------------------

def test_maple_check_lives_in_soak_and_reads_process_names_from_its_own_globals(modules):
    soak = modules[SOAK]
    assert soak.maple_check.__module__ == SOAK
    assert "_process_names" in soak.maple_check.__code__.co_names


def test_the_other_tools_call_the_guard_through_soak(modules):
    """probe and session_starts read `soak.maple_check` at call time, so patching `soak._process_names` reaches them."""
    for name in (PROBE, SESSION_STARTS):
        check = modules[name]._check_processes
        assert check.__module__ == name
        assert {"soak", "maple_check"} <= set(check.__code__.co_names), name
    assert {"soak", "maple_check"} <= set(modules[PROBE].run_probes.__code__.co_names)  # probe step 1
    assert "maple_check" in modules[SOAK]._check_processes.__code__.co_names


def test_every_key_sending_entry_point_calls_its_check_processes(modules):
    assert "_check_processes" in modules[SOAK].run_soak.__code__.co_names
    assert "_check_processes" in modules[PROBE]._run_probe.__code__.co_names
    assert "_check_processes" in modules[SESSION_STARTS].run_starts.__code__.co_names


# ---- the guard with `_process_names` patched (no tasklist, no key) ---------------------------------------------------

def test_maple_check_reports_the_game_when_process_names_lists_it(modules, monkeypatch):
    soak = modules[SOAK]
    monkeypatch.setattr(soak, "_process_names", lambda: Success(["System", "MapleStory.exe"]))
    ok, text = soak.maple_check()
    assert ok is False
    assert "MapleStory" in text


def test_maple_check_passes_a_list_without_the_game(modules, monkeypatch):
    soak = modules[SOAK]
    monkeypatch.setattr(soak, "_process_names", lambda: Success(["System", "python.exe"]))
    assert soak.maple_check() == (True, "no MapleStory process")


def test_maple_check_is_closed_when_the_process_list_cannot_be_read(modules, monkeypatch):
    soak = modules[SOAK]
    monkeypatch.setattr(soak, "_process_names", lambda: Failure("tasklist failed: boom"))
    ok, text = soak.maple_check()
    assert ok is False
    assert "boom" in text


@pytest.mark.parametrize("module_name, abort_name", [(PROBE, "ProbeAbort"), (SESSION_STARTS, "_Abort")])
def test_the_patch_on_soak_reaches_the_other_tools_checks(modules, monkeypatch, module_name, abort_name):
    """`_check_processes` aborts with the game listed and passes without it: the soak patch took effect, not `tasklist`."""
    module = modules[module_name]
    writer = types.SimpleNamespace(session={})
    args = (writer,) if module_name == PROBE else ()
    monkeypatch.setattr(modules[SOAK], "_process_names", lambda: Success(["MapleStory.exe"]))
    with pytest.raises(getattr(module, abort_name)):
        module._check_processes(*args)
    monkeypatch.setattr(modules[SOAK], "_process_names", lambda: Success(["System"]))
    assert module._check_processes(*args) is None
