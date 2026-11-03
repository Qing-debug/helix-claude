"""Suite-wide safety nets."""

import pytest

from src.route import runtime


@pytest.fixture(autouse=True)
def _no_windows_admin_prompt(monkeypatch):
    """No test may raise the Windows prompt that relaunches Helix as administrator (H82, RT-FIX-1b). `_shell_execute_runas`
    is the one line that does; a test that reaches it fails loudly. A test that needs the real function (to check the call
    it makes against a fake `ctypes.WinDLL`) takes `runtime._shell_execute_runas` at import time, before this runs."""

    def refuse(file: str, params: str, directory: str) -> int:
        raise AssertionError(
            f"a test reached the Windows administrator prompt (ShellExecuteW runas {file!r} {params!r}): patch "
            "runtime.is_elevated, runtime.ensure_elevated, walk._is_elevated or the RouteTab `elevated=` keyword")

    monkeypatch.setattr(runtime, "_shell_execute_runas", refuse)
