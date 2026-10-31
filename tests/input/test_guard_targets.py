"""Pins the seams the MapleStory-guard tests patch, so the guard cannot move behind them unnoticed.

The harness tools that send keys ask "Is MapleStory closed? y/n" first (hard rule 2, decision H60). Their tests never
read the real console: they replace `soak._read_answer` with `monkeypatch.setattr`. That works only while
`soak.maple_check` reads `_read_answer` from the global names of `soak`. If the guard moved to another module and `soak`
re-exported it, the patch would still be accepted, the guard tests would block on real input (or read whatever stdin
holds), and they would pass or fail by what happens to be typed. This file fails first. It pins where things are; it
moves and changes nothing.

Not run: no harness tool, no listener, no link, no board, no key. `_read_answer` is patched in every test that calls
`maple_check`, so `input()` is never reached either. The harness modules import Windows-only code (ctypes `WinDLL`,
winrt) at module level, so this file imports them inside a fixture and skips on other platforms (plan hard rule 9).
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
# _sleep` (test_probe patches them on `probe`); soak's `_read_answer` is patched by test_soak, test_probe,
# test_session_starts and test_soak_v2_faults.
PATCHED = {
    PROBE: ("PROBES", "_board_list_json", "_new_link", "_start_sink", "run_probes", "_clock", "_sleep"),
    SOAK: ("_read_answer", "_new_link", "_start_sink", "_clock", "_sleep", "check_focus"),
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

def test_maple_check_lives_in_soak_and_reads_the_answer_from_its_own_globals(modules):
    soak = modules[SOAK]
    assert soak.maple_check.__module__ == SOAK
    assert "_read_answer" in soak.maple_check.__code__.co_names


def test_the_other_tools_call_the_guard_through_soak(modules):
    """session_starts and probe's `run_probes` (step 1) read `soak.maple_check` at call time, so patching
    `soak._read_answer` reaches them. probe's `_check_processes` only records the answer: it never asks."""
    check = modules[SESSION_STARTS]._check_processes
    assert check.__module__ == SESSION_STARTS
    assert {"soak", "maple_check"} <= set(check.__code__.co_names)
    assert {"soak", "maple_check"} <= set(modules[PROBE].run_probes.__code__.co_names)  # probe step 1
    probe_names = set(modules[PROBE]._check_processes.__code__.co_names)
    assert "maple_check" not in probe_names and "_read_answer" not in probe_names
    assert "maple_check" in modules[SOAK]._check_processes.__code__.co_names


def test_every_key_sending_entry_point_calls_its_check_processes(modules):
    assert "_check_processes" in modules[SOAK].run_soak.__code__.co_names
    assert "_check_processes" in modules[PROBE]._run_probe.__code__.co_names
    assert "_check_processes" in modules[SESSION_STARTS].run_starts.__code__.co_names


# ---- the guard with `_read_answer` patched (no console, no key) ------------------------------------------------------

@pytest.mark.parametrize("answer, ok", [("", False), ("yes", False), ("n", False), ("N", False), (" n ", False),
                                        ("y", True), (" Y ", True)])
def test_maple_check_passes_only_y_after_stripping(modules, monkeypatch, answer, ok):
    soak = modules[SOAK]
    monkeypatch.setattr(soak, "_read_answer", lambda: Success(answer))
    passed, text = soak.maple_check()
    assert passed is ok
    if ok:
        assert text == "answered y: MapleStory closed"
    else:
        assert text.startswith("refused: MapleStory not confirmed closed (answer:")
        assert text == f"refused: MapleStory not confirmed closed (answer: {answer.strip()})"


def test_maple_check_refuses_when_there_is_no_answer(modules, monkeypatch):
    soak = modules[SOAK]
    monkeypatch.setattr(soak, "_read_answer", lambda: Failure("none"))
    assert soak.maple_check() == (False, "refused: MapleStory not confirmed closed (answer: none)")


def test_the_patch_on_soak_reaches_session_starts_check(modules, monkeypatch):
    """`_check_processes` aborts on `n` and passes on `y`: the soak patch took effect, not the console."""
    module = modules[SESSION_STARTS]
    monkeypatch.setattr(modules[SOAK], "_read_answer", lambda: Success("n"))
    with pytest.raises(module._Abort):
        module._check_processes()
    monkeypatch.setattr(modules[SOAK], "_read_answer", lambda: Success("y"))
    assert module._check_processes() is None


def test_the_patch_on_soak_reaches_probe_step_1_before_any_link_or_board_port(modules, monkeypatch, tmp_path):
    """With `Success("n")` `run_probes` exits 2 and never reaches `_new_link` or `_board_list_json`."""
    soak, probe = modules[SOAK], modules[PROBE]

    def never(*args, **kwargs):
        pytest.fail("reached past probe's step 1")
    monkeypatch.setattr(soak, "_read_answer", lambda: Success("n"))
    monkeypatch.setattr(probe, "_new_link", never)
    monkeypatch.setattr(probe, "_board_list_json", never)
    cfg = probe.ProbeConfig(probes=("delivery",), out=tmp_path / "run", seed=1, cli="arduino-cli")
    assert probe.run_probes(cfg, probe._new_link, probe._start_sink) == soak.EXIT_REFUSED
    assert not (tmp_path / "run").exists()


def test_probe_check_processes_only_records_the_answer(modules, monkeypatch):
    soak, probe = modules[SOAK], modules[PROBE]

    def never():
        pytest.fail("probe's _check_processes must not ask")
    monkeypatch.setattr(soak, "_read_answer", never)
    writer = types.SimpleNamespace(session={})
    assert probe._check_processes(writer, "t") is None
    assert writer.session["process_check"] == "t"


# ---- no tool lists the running programs (Hong's anti-cheat standing fact, H56) -------------------------------------------

PROCESS_LISTERS = ("tasklist", "get-process")


def _process_lister_calls(source: str) -> list[int]:
    """Line numbers of `subprocess.<anything>(...)` calls whose arguments hold a string naming a process lister."""
    found = []
    for node in ast.walk(ast.parse(source)):
        is_subprocess_call = (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                              and isinstance(node.func.value, ast.Name) and node.func.value.id == "subprocess")
        if is_subprocess_call:
            strings = [n.value.lower() for arg in [*node.args, *(k.value for k in node.keywords)]
                       for n in ast.walk(arg) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
            if any(lister in text for text in strings for lister in PROCESS_LISTERS):
                found.append(node.lineno)
    return found


def test_no_tool_lists_processes(modules):
    for name in (SOAK, PROBE, SESSION_STARTS):
        source = Path(modules[name].__file__).read_text(encoding="utf-8")
        assert "tasklist" not in source.lower(), name
        assert _process_lister_calls(source) == [], name


# ---- the question is asked once: no re-check is left in any tool (spec 6.1, decision H60) -------------------------------

def test_no_mid_run_recheck_left(modules):
    soak, probe = modules[SOAK], modules[PROBE]
    assert not hasattr(soak, "PROCESS_CHECK_PERIOD_NS")
    for fn in (soak.run_soak, probe._run_probe, modules[SESSION_STARTS].run_starts):
        code = fn.__code__  # the closure's name: a local, or a cell variable once a nested function uses it
        assert "periodic_check" not in code.co_varnames and "periodic_check" not in code.co_cellvars, fn.__qualname__
    assert "periodic_check" not in probe.ProbeContext.__init__.__code__.co_varnames
    assert "_periodic_check" not in probe.ProbeContext.command.__code__.co_names
    assert "tick" not in soak.wait_for_click.__code__.co_varnames  # nothing is left to give it


def test_the_process_lister_scan_can_fail():
    listing = 'import subprocess\nsubprocess.run(["tasklist", "/FO", "CSV"])\n'
    powershell = 'import subprocess\nsubprocess.run(["powershell", "-Command", "Get-Process"])\n'
    harmless = 'import subprocess\nsubprocess.run(["git", "rev-parse", "HEAD"])\nx = "Get-Process"\n'
    assert _process_lister_calls(listing) == [2]
    assert _process_lister_calls(powershell) == [2]
    assert _process_lister_calls(harmless) == []
