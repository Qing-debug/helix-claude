"""RT-FIX-1 (H82): Helix runs elevated so that F12 reaches it with MapleStory in front.

`runtime.ensure_elevated` (elevated → carry on; else relaunch with ShellExecute "runas"; declined or opted out → say so),
`src.main.main`'s use of it, the elevated child's start-up (run for real, unelevated, with only the Windows call
faked), and the walk CLI's refusal (exit 11, no relaunch, no key sent when it is not elevated). Every Windows call is
replaced: no test here asks Windows who is elevated, raises a Windows prompt or starts a process."""

import ctypes
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from returns.pipeline import is_successful
from returns.result import Failure, Success

import test_walk_cli
from src.route import runtime, walk

sim = test_walk_cli.sim  # the B19 rig's fixture (bound by assignment, as test_walk_view.py does)
_REAL_SHELL_EXECUTE_RUNAS = runtime._shell_execute_runas  # noqa: SLF001  (before tests/conftest.py replaces it)


@pytest.fixture(autouse=True)
def _no_opt_out(monkeypatch):
    """The checks of `ensure_elevated` start from "not opted out"; a test sets the variable itself."""
    monkeypatch.delenv(runtime.ELEVATE_OPT_OUT_ENV, raising=False)


class FakeShell32:
    """What `ctypes.WinDLL("shell32")` returns: `IsUserAnAdmin` answers `admin`, `ShellExecuteW` notes its call and
    answers `shell_result`; the argtypes and restype that the code sets are kept."""

    def __init__(self, admin=0, shell_result=33):
        self.admin = admin
        self.IsUserAnAdmin = lambda: self.admin
        self.calls = []

        def shell_execute(*args):
            self.calls.append(args)
            return shell_result

        self.ShellExecuteW = _Callable(shell_execute)


class _Callable:
    def __init__(self, func):
        self._func = func
        self.argtypes = None
        self.restype = None

    def __call__(self, *args):
        return self._func(*args)


@pytest.fixture
def fake_shell32(monkeypatch):
    made = []

    def make(admin=0, shell_result=33):
        shell32 = FakeShell32(admin, shell_result)
        names = []

        def win_dll(name, **options):
            names.append((name, options))
            return shell32

        monkeypatch.setattr(ctypes, "WinDLL", win_dll, raising=False)  # absent off Windows
        shell32.dlls = names
        made.append(shell32)
        return shell32

    return make


# ---- is_elevated and the one line that raises the Windows prompt ------------------------------------------------------

@pytest.mark.parametrize(("admin", "expected"), [(1, True), (0, False)])
def test_is_elevated_reads_is_user_an_admin(fake_shell32, admin, expected):
    shell32 = fake_shell32(admin=admin)
    assert runtime.is_elevated() is expected
    assert shell32.dlls[0][0] == "shell32"


def test_is_elevated_reads_a_failed_call_as_not_elevated(monkeypatch):
    def broken(name, **options):
        raise OSError("no shell32")

    monkeypatch.setattr(ctypes, "WinDLL", broken, raising=False)
    assert runtime.is_elevated() is False


def test_shell_execute_runas_calls_shell_execute_w_with_the_runas_verb(fake_shell32):
    shell32 = fake_shell32(shell_result=42)

    code = _REAL_SHELL_EXECUTE_RUNAS("C:\\py\\python.exe", "-m src.main", "C:\\repo")

    assert code == 42
    assert shell32.calls == [(None, "runas", "C:\\py\\python.exe", "-m src.main", "C:\\repo", 1)]  # 1 = SW_SHOWNORMAL
    assert shell32.ShellExecuteW.restype is ctypes.c_void_p  # a small code, never read as a pointer
    assert len(shell32.ShellExecuteW.argtypes) == 6


def test_shell_execute_runas_reads_a_none_result_as_zero(fake_shell32):
    fake_shell32(shell_result=None)
    assert _REAL_SHELL_EXECUTE_RUNAS("p", "", "d") == 0


def test_the_suite_wide_guard_refuses_the_real_prompt():
    with pytest.raises(AssertionError, match="Windows administrator prompt"):
        runtime._shell_execute_runas("python.exe", "-m src.main", "C:\\")  # noqa: SLF001


# ---- relaunch_elevated ----------------------------------------------------------------------------------------------------

@pytest.fixture
def launched(monkeypatch, tmp_path):
    """Replace `_shell_execute_runas`; `launched.result` is what it returns, `launched.calls` what it was asked."""
    state = SimpleNamespace(calls=[], result=33)

    def shell_execute(file, params, directory):
        state.calls.append((file, params, directory))
        return state.result

    monkeypatch.setattr(runtime, "_shell_execute_runas", shell_execute)
    monkeypatch.setattr(sys, "executable", "C:\\repo\\.venv\\Scripts\\python.exe")
    monkeypatch.setattr(sys, "orig_argv", ["python", "-u", "-m", "src.main"])
    monkeypatch.chdir(tmp_path)
    return state


def test_relaunch_runs_the_module_from_the_repo_root_through_the_bootstrap(launched, tmp_path):
    result = runtime.relaunch_elevated()

    repo = str(runtime._REPO)  # noqa: SLF001
    assert is_successful(result)
    assert launched.calls == [("C:\\repo\\.venv\\Scripts\\python.exe",
                              subprocess.list2cmdline(["-u", "-c", runtime._ELEVATED_BOOTSTRAP, repo, "src.main"]),  # noqa: SLF001
                              repo)]
    assert repo != str(tmp_path)  # the folder it was started in does not matter
    assert (runtime._REPO / "src" / "main.py").is_file()  # noqa: SLF001  (an absolute path to the real repo root)


@pytest.mark.parametrize("code", [33, 42, 1000])
def test_relaunch_succeeds_above_32(launched, code):
    launched.result = code
    assert is_successful(runtime.relaunch_elevated())


def test_relaunch_declined_is_a_failure_that_says_so(launched):
    launched.result = 5  # SE_ERR_ACCESSDENIED: the Windows prompt was declined
    result = runtime.relaunch_elevated()

    assert isinstance(result, Failure) and "declined" in result.failure()


@pytest.mark.parametrize("code", [0, 2, 31, 32])
def test_relaunch_failure_names_the_code(launched, code):
    launched.result = code
    result = runtime.relaunch_elevated()

    assert isinstance(result, Failure) and f"returned {code}" in result.failure()


@pytest.mark.parametrize(("executable", "orig_argv"), [
    ("", ["python", "-m", "src.main"]),
    ("python.exe", []),
    ("python.exe", ["python"]),
    ("python.exe", ["python", "script.py"]),
    ("python.exe", ["python", "-c", "pass"]),
    ("python.exe", ["python", "-m"]),  # `-m` with no module
    # the elevated copy's own command line (the bootstrap, no `-m`): if it were somehow not elevated it cannot start
    # another one, so a relaunch loop is impossible
    ("python.exe", ["python", "-u", "-c", runtime._ELEVATED_BOOTSTRAP, "C:\\repo", "src.main"]),  # noqa: SLF001
])
def test_relaunch_without_a_module_command_line_launches_nothing(launched, monkeypatch, executable, orig_argv):
    monkeypatch.setattr(sys, "executable", executable)
    monkeypatch.setattr(sys, "orig_argv", orig_argv)

    assert isinstance(runtime.relaunch_elevated(), Failure)
    assert launched.calls == []


# ---- the elevated child, run for real as Windows would start it: a fresh folder, no PYTHON* variables ----------------------

def _child_env() -> dict[str, str]:
    """The parent's environment without the variables that could help Python find `src` (the elevated child is started by
    a service and is not known to inherit the environment, RT-FIX-1-R I1); the rest (SystemRoot, ...) stays."""
    return {k: v for k, v in os.environ.items() if not k.upper().startswith("PYTHON")}


def _run_child(monkeypatch, orig_argv, repo, folder, stdin=""):
    """Ask `relaunch_elevated` for the command line it would hand to ShellExecute for `orig_argv` with `repo` as the repo
    root, then start that command line for real (unelevated: only the Windows call is faked) in `folder`."""
    seen = []
    monkeypatch.setattr(runtime, "_shell_execute_runas", lambda file, params, directory: seen.append((file, params, directory)) or 33)
    monkeypatch.setattr(runtime, "_REPO", repo)
    monkeypatch.setattr(sys, "orig_argv", orig_argv)
    assert is_successful(runtime.relaunch_elevated())
    file, params, directory = seen[0]
    assert directory == str(repo)
    return subprocess.run(f'"{file}" {params}', cwd=folder, env=_child_env(), input=stdin, capture_output=True, text=True,
                          timeout=60, check=False)


@pytest.mark.slow
def test_the_child_runs_the_module_as_main_from_the_repo_root_in_whatever_folder_it_starts(monkeypatch, tmp_path):
    repo = tmp_path / "re po"  # a space: the path has to survive the quoting too
    repo.mkdir()
    (repo / "probe_mod.py").write_text(
        "import json, os, sys\nprint(json.dumps([__name__, os.getcwd(), sys.path[0], sys.argv[0], sys.argv[1:]]))\n",
        encoding="utf-8")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    done = _run_child(monkeypatch, ["python", "-u", "-m", "probe_mod", "--a", "b c", ""], repo, elsewhere)

    assert done.returncode == 0, done.stderr
    name, cwd, path0, argv0, argv = json.loads(done.stdout)
    assert name == "__main__"
    assert Path(cwd).resolve() == repo.resolve() and Path(path0).resolve() == repo.resolve()  # the repo root, not `elsewhere`
    assert Path(argv0).resolve() == (repo / "probe_mod.py").resolve()
    assert argv == ["--a", "b c", ""]  # the arguments survive the quoting


@pytest.mark.slow
def test_the_child_imports_src_from_a_folder_that_is_not_the_repo(monkeypatch, tmp_path):
    folder = tmp_path / "system32 stand-in"
    folder.mkdir()

    done = _run_child(monkeypatch, ["python", "-m", "src.route.walk", "--help"], runtime._REPO, folder)  # noqa: SLF001

    assert done.returncode == 0, done.stderr
    assert "usage: python -m src.route.walk" in done.stdout


@pytest.mark.slow
def test_an_exception_in_the_child_prints_the_traceback_and_waits_for_enter(monkeypatch, tmp_path):
    (tmp_path / "boom_mod.py").write_text("raise RuntimeError('kaboom')\n", encoding="utf-8")

    done = _run_child(monkeypatch, ["python", "-m", "boom_mod"], tmp_path, tmp_path, stdin="\n")

    assert done.returncode == 1
    assert "RuntimeError: kaboom" in done.stderr
    assert "Press Enter to close this window." in done.stdout  # the window would otherwise close with the traceback


# ---- ensure_elevated: the four outcomes -----------------------------------------------------------------------------------

@pytest.fixture
def decide(monkeypatch):
    """`decide(elevated=..., relaunch=...)` replaces `is_elevated` and `relaunch_elevated`, returns the calls made."""
    def set_up(*, elevated, relaunch=None):
        calls = []
        monkeypatch.setattr(runtime, "is_elevated", lambda: elevated)

        def fake_relaunch():
            calls.append("relaunch")
            return relaunch

        monkeypatch.setattr(runtime, "relaunch_elevated", fake_relaunch)
        return calls

    return set_up


def test_elevated_means_no_relaunch(decide):
    calls = decide(elevated=True, relaunch=Success(None))
    assert runtime.ensure_elevated() is runtime.Elevation.ELEVATED
    assert calls == []


def test_elevated_ignores_the_opt_out(decide, monkeypatch):
    calls = decide(elevated=True)
    monkeypatch.setenv(runtime.ELEVATE_OPT_OUT_ENV, "1")
    assert runtime.ensure_elevated() is runtime.Elevation.ELEVATED
    assert calls == []


def test_not_elevated_relaunches_and_says_so(decide):
    calls = decide(elevated=False, relaunch=Success(None))
    lines = []
    assert runtime.ensure_elevated(log=lines.append) is runtime.Elevation.RELAUNCHED
    assert calls == ["relaunch"] and lines == []


def test_a_declined_relaunch_is_declined_and_logs_the_reason(decide):
    calls = decide(elevated=False, relaunch=Failure("the Windows prompt was declined (ShellExecuteW returned 5)"))
    lines = []

    assert runtime.ensure_elevated(log=lines.append) is runtime.Elevation.DECLINED
    assert calls == ["relaunch"]
    assert lines == ["could not start Helix as administrator: the Windows prompt was declined (ShellExecuteW returned 5)"]


def test_a_declined_relaunch_logs_on_stderr_by_default(decide, capsys):
    decide(elevated=False, relaunch=Failure("no"))
    assert runtime.ensure_elevated() is runtime.Elevation.DECLINED
    assert "could not start Helix as administrator: no" in capsys.readouterr().err


@pytest.mark.parametrize("value", ["1", "yes", "true", "anything"])
def test_the_opt_out_means_no_relaunch(decide, monkeypatch, value):
    calls = decide(elevated=False, relaunch=Success(None))
    monkeypatch.setenv(runtime.ELEVATE_OPT_OUT_ENV, value)

    assert runtime.ensure_elevated() is runtime.Elevation.OPTED_OUT
    assert calls == []


@pytest.mark.parametrize("value", ["", "0"])
def test_an_empty_or_zero_opt_out_is_not_one(decide, monkeypatch, value):
    calls = decide(elevated=False, relaunch=Success(None))
    monkeypatch.setenv(runtime.ELEVATE_OPT_OUT_ENV, value)

    assert runtime.ensure_elevated() is runtime.Elevation.RELAUNCHED
    assert calls == ["relaunch"]


def test_the_opt_out_name_is_the_documented_one():
    assert runtime.ELEVATE_OPT_OUT_ENV == "HELIX_NO_ELEVATE"


# ---- the walk CLI: no relaunch (RT-FIX-1b, ruling RT-FIX-1-R-D1) ------------------------------------------------------------

@pytest.fixture
def no_relaunch(monkeypatch):
    """Anything that would start a relaunch fails the test (on top of the suite-wide guard on the Windows call)."""

    def refuse(*args, **kwargs):
        raise AssertionError("the walk CLI must not relaunch itself")

    monkeypatch.setattr(runtime, "ensure_elevated", refuse)
    monkeypatch.setattr(runtime, "relaunch_elevated", refuse)


def test_walk_asks_runtime_for_its_default_check(monkeypatch):
    monkeypatch.setattr(runtime, "is_elevated", lambda: True)
    assert walk._is_elevated() is True  # noqa: SLF001
    monkeypatch.setattr(runtime, "is_elevated", lambda: False)
    assert walk._is_elevated() is False  # noqa: SLF001


def test_cli_elevated_walks(sim, monkeypatch, no_relaunch):
    asked = []
    monkeypatch.setattr(walk, "_is_elevated", lambda: asked.append(1) or True)
    assert sim.main("--laps", "1") == 0
    assert asked == [1] and sim.capture_calls == 1


@pytest.mark.parametrize("opt_out", [None, "1"])
def test_cli_not_elevated_refuses_with_exit_11_and_never_relaunches(sim, monkeypatch, capsys, no_relaunch, opt_out):
    monkeypatch.setattr(walk, "_is_elevated", lambda: False)
    if opt_out is not None:
        monkeypatch.setenv(runtime.ELEVATE_OPT_OUT_ENV, opt_out)  # the opt-out never lets a live walk past the check

    assert sim.main("--laps", "1") == 11

    assert capsys.readouterr().err == f"error: {runtime.NOT_ELEVATED_CLI_TEXT}\n"
    assert sim.capture_calls == 0 and sim.link_calls == 0 and not sim.out.exists()  # no capture, no link, no key


def test_the_cli_text_says_what_to_do():
    assert "administrator terminal" in runtime.NOT_ELEVATED_CLI_TEXT and "F12" in runtime.NOT_ELEVATED_CLI_TEXT


@pytest.mark.parametrize("extra", [["--laps", "0"], ["--minutes", "0"], ["--seed", "-1"]])
def test_cli_usage_errors_come_before_the_elevation_check(sim, monkeypatch, extra):
    asked = []
    monkeypatch.setattr(walk, "_is_elevated", lambda: asked.append(1) or False)

    assert sim.main(*extra) == 2
    assert asked == []


def test_cli_without_supervised_is_refused_before_the_elevation_check(sim, monkeypatch):
    asked = []
    monkeypatch.setattr(walk, "_is_elevated", lambda: asked.append(1) or False)

    assert walk.main(["--map", str(test_walk_cli.FIXTURE), "--out", str(sim.out)]) == 2
    assert asked == []


def test_cli_exit_11_is_documented():
    assert "11 Helix is not running as administrator" in (walk.__doc__ or "")


# ---- the app: src.main.main -----------------------------------------------------------------------------------------------

def _fake_app(monkeypatch):
    """`src.main` with the capture, the controller and the window replaced; `app.events` records what started."""
    import src.main as main_module

    app = SimpleNamespace(events=[], module=main_module, start_notices=[])

    def capture(shared):
        app.events.append("capture")

    class FakeController:
        def __init__(self, **options):
            app.events.append("controller")

    class FakeGui:
        def __init__(self, **options):
            app.events.append("gui")

        def start(self, startup_notice=None):
            app.events.append("start")
            app.start_notices.append(startup_notice)

    monkeypatch.setattr(main_module, "capture_and_process_minimap", capture)
    monkeypatch.setattr(main_module, "Controller", FakeController)
    monkeypatch.setattr(main_module, "MapleGUI", FakeGui)
    return app


def _run_main(app, monkeypatch, elevation):
    monkeypatch.setattr(runtime, "ensure_elevated", lambda: elevation)
    app.module.main()


def test_main_elevated_starts_the_app_without_a_notice(monkeypatch, capsys):
    app = _fake_app(monkeypatch)
    _run_main(app, monkeypatch, runtime.Elevation.ELEVATED)

    assert app.start_notices == [None] and "start" in app.events
    assert capsys.readouterr().err == ""


def test_main_relaunched_starts_nothing(monkeypatch, capsys):
    app = _fake_app(monkeypatch)
    _run_main(app, monkeypatch, runtime.Elevation.RELAUNCHED)

    assert app.events == []  # no capture thread, no controller, no window
    assert "as administrator" in capsys.readouterr().out


def test_main_declined_warns_in_the_terminal_and_hands_the_console_box_the_notice(monkeypatch, capsys):
    app = _fake_app(monkeypatch)
    _run_main(app, monkeypatch, runtime.Elevation.DECLINED)

    assert app.start_notices == [runtime.NOT_ELEVATED_TEXT]
    assert runtime.NOT_ELEVATED_TEXT in capsys.readouterr().err


def test_main_opted_out_warns_like_a_declined_start(monkeypatch, capsys):
    app = _fake_app(monkeypatch)
    _run_main(app, monkeypatch, runtime.Elevation.OPTED_OUT)

    assert app.start_notices == [runtime.NOT_ELEVATED_TEXT]  # the tab refuses Start either way, so say why
    assert runtime.NOT_ELEVATED_TEXT in capsys.readouterr().err


def test_the_app_text_covers_a_declined_prompt_and_a_failed_start():
    text = runtime.NOT_ELEVATED_TEXT
    assert "not running as administrator" in text and "answer Yes if Windows asks" in text and "administrator terminal" in text
