"""RT-FIX-1 (H82): Helix runs elevated so that F12 reaches it with MapleStory in front.

`runtime.ensure_elevated` (elevated → carry on; else relaunch with ShellExecute "runas"; declined or opted out → say so),
the walk CLI's use of it (exit 11 and no key sent when it is not elevated) and `src.main.main`'s. Every Windows call is
replaced: no test here asks Windows who is elevated, raises a Windows prompt or starts a process."""

import ctypes
import subprocess
import sys
from types import SimpleNamespace

import pytest
from returns.pipeline import is_successful
from returns.result import Failure, Success

import test_walk_cli
from src.route import runtime, walk

sim = test_walk_cli.sim  # the B19 rig's fixture (bound by assignment, as test_walk_view.py does)


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

    code = runtime._shell_execute_runas("C:\\py\\python.exe", "-m src.main", "C:\\repo")

    assert code == 42
    assert shell32.calls == [(None, "runas", "C:\\py\\python.exe", "-m src.main", "C:\\repo", 1)]  # 1 = SW_SHOWNORMAL
    assert shell32.ShellExecuteW.restype is ctypes.c_void_p  # a small code, never read as a pointer
    assert len(shell32.ShellExecuteW.argtypes) == 6


def test_shell_execute_runas_reads_a_none_result_as_zero(fake_shell32):
    fake_shell32(shell_result=None)
    assert runtime._shell_execute_runas("p", "", "d") == 0


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
    state.cwd = str(tmp_path)
    return state


def test_relaunch_uses_the_same_interpreter_arguments_and_directory(launched):
    result = runtime.relaunch_elevated()

    assert is_successful(result)
    assert launched.calls == [("C:\\repo\\.venv\\Scripts\\python.exe", "-u -m src.main", launched.cwd)]


def test_relaunch_quotes_an_argument_with_a_space(launched, monkeypatch):
    monkeypatch.setattr(sys, "orig_argv", ["python", "-m", "src.route.walk", "--map", "maps/my map.csv", "--supervised"])
    runtime.relaunch_elevated()

    params = launched.calls[0][1]
    assert params == '-m src.route.walk --map "maps/my map.csv" --supervised'
    assert subprocess.list2cmdline(sys.orig_argv[1:]) == params  # the quoting the OS command line parser undoes


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


@pytest.mark.parametrize(("executable", "orig_argv"), [("", ["python"]), ("python.exe", [])])
def test_relaunch_without_a_known_command_line_launches_nothing(launched, monkeypatch, executable, orig_argv):
    monkeypatch.setattr(sys, "executable", executable)
    monkeypatch.setattr(sys, "orig_argv", orig_argv)

    assert isinstance(runtime.relaunch_elevated(), Failure)
    assert launched.calls == []


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


# ---- the walk CLI ---------------------------------------------------------------------------------------------------------

def test_walk_asks_runtime_for_its_default_check(monkeypatch):
    monkeypatch.setattr(runtime, "ensure_elevated", lambda: runtime.Elevation.OPTED_OUT)
    assert walk._ensure_elevated() is runtime.Elevation.OPTED_OUT


def test_cli_elevated_walks(sim, monkeypatch):
    asked = []
    monkeypatch.setattr(walk, "_ensure_elevated", lambda: asked.append(1) or runtime.Elevation.ELEVATED)
    assert sim.main("--laps", "1") == 0
    assert asked == [1] and sim.capture_calls == 1


def test_cli_relaunched_ends_here_with_exit_0_and_walks_nothing(sim, monkeypatch, capsys):
    monkeypatch.setattr(walk, "_ensure_elevated", lambda: runtime.Elevation.RELAUNCHED)

    assert sim.main("--laps", "1") == 0

    assert "as administrator" in capsys.readouterr().out
    assert sim.capture_calls == 0 and sim.link_calls == 0 and not sim.out.exists()  # nothing started, nothing written


@pytest.mark.parametrize("elevation", [runtime.Elevation.DECLINED, runtime.Elevation.OPTED_OUT])
def test_cli_not_elevated_refuses_with_exit_11(sim, monkeypatch, capsys, elevation):
    monkeypatch.setattr(walk, "_ensure_elevated", lambda: elevation)

    assert sim.main("--laps", "1") == 11

    err = capsys.readouterr().err
    assert runtime.NOT_ELEVATED_TEXT in err and err.startswith("error: ")
    assert sim.capture_calls == 0 and sim.link_calls == 0 and not sim.out.exists()  # no capture, no link, no key


@pytest.mark.parametrize("extra", [["--laps", "0"], ["--minutes", "0"], ["--seed", "-1"]])
def test_cli_usage_errors_come_before_the_elevation_check(sim, monkeypatch, extra):
    asked = []
    monkeypatch.setattr(walk, "_ensure_elevated", lambda: asked.append(1) or runtime.Elevation.DECLINED)

    assert sim.main(*extra) == 2
    assert asked == []  # a typo never raises the Windows prompt


def test_cli_without_supervised_is_refused_before_the_elevation_check(sim, monkeypatch):
    asked = []
    monkeypatch.setattr(walk, "_ensure_elevated", lambda: asked.append(1) or runtime.Elevation.DECLINED)

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


def test_main_opted_out_runs_quietly(monkeypatch, capsys):
    app = _fake_app(monkeypatch)
    _run_main(app, monkeypatch, runtime.Elevation.OPTED_OUT)

    assert app.start_notices == [None]
    assert capsys.readouterr().err == ""
