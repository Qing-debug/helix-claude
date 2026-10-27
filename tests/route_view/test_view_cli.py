"""Tests of the route view's command line (route view plan task RV-7, tests 11-13; contract V6, replay half).

`main` is called in-process with a Tk root the test gives it (`make_root`), withdrawn, `run_loop=False`: no mainloop and
nothing shown (`deiconify` is replaced by a recorder). Test 13 starts the real command in a subprocess.
"""

import ast
import re
import subprocess
import sys

import pytest
from view_runs import GIT_HEAD, REPO_ROOT, make_folder
from view_tk import destroy_all, open_root

from src.route_view import __main__ as cli


class FakeRoot:
    """Stands in for a Tk root that fails to load: records what `main` does to it."""

    def __init__(self):
        self.calls = []

    def withdraw(self):
        self.calls.append("withdraw")

    def deiconify(self):
        self.calls.append("deiconify")

    def destroy(self):
        self.calls.append("destroy")


@pytest.fixture
def no_git(monkeypatch):
    """`main` asks git for the head; a fixed answer keeps the in-process tests off the subprocess."""
    monkeypatch.setattr(cli, "_git_head", lambda: GIT_HEAD)


def test_11_a_folder_with_no_run_is_an_error_and_shows_no_window(tmp_path, capsys, no_git):
    empty = tmp_path / "empty"
    empty.mkdir()
    root = FakeRoot()
    code = cli.main(["--follow", str(empty)], make_root=lambda: root, run_loop=False)
    out, err = capsys.readouterr()
    assert code == 2 and out == ""
    assert err.startswith("error: ") and str(empty) in err, err
    assert root.calls == ["withdraw", "destroy"]


def test_11_a_root_that_cannot_open_is_an_error(capsys, tmp_path, no_git):
    import tkinter as tk

    def no_display():
        raise tk.TclError("no display name and no $DISPLAY environment variable")

    assert cli.main(["--follow", str(tmp_path)], make_root=no_display, run_loop=False) == 2
    out, err = capsys.readouterr()
    assert out == "" and err == "error: cannot open a window: no display name and no $DISPLAY environment variable\n"


def test_11_the_follow_option_is_required(capsys):
    with pytest.raises(SystemExit) as stop:
        cli.main([], make_root=FakeRoot, run_loop=False)
    assert stop.value.code == 2 and "--follow" in capsys.readouterr().err


def test_12_a_run_folder_opens_a_window_on_its_last_step(tmp_path, capsys, monkeypatch, no_git):
    folder = make_folder(tmp_path)
    root = open_root()
    try:
        shown = []
        monkeypatch.setattr(root, "deiconify", lambda: shown.append("deiconify"))
        code = cli.main(["--follow", str(folder)], make_root=lambda: root, run_loop=False)
        out, err = capsys.readouterr()
        assert code == 0 and err == ""
        lines = out.splitlines()
        assert len(lines) == 1 and re.fullmatch(r"window built in \d+\.\d\d s", lines[0]), out
        assert shown == ["deiconify"]
        assert root.state() == "withdrawn"  # only the recorder was called: nothing was shown
        assert root.title().startswith("Route view · ended: max_laps")
        assert root.title().endswith("· ended")  # the last step is `end`
    finally:
        destroy_all([root])


def test_12_the_time_starts_right_after_the_docstring():
    """The first two statements after the docstring are `import time` and `_T0 = time.perf_counter()` (plan RV-7)."""
    tree = ast.parse((REPO_ROOT / "src" / "route_view" / "__main__.py").read_text(encoding="utf-8"))
    docstring, first, second = tree.body[:3]
    assert isinstance(docstring, ast.Expr) and isinstance(docstring.value, ast.Constant)
    assert ast.dump(first) == ast.dump(ast.parse("import time").body[0])
    assert ast.dump(second) == ast.dump(ast.parse("_T0 = time.perf_counter()").body[0])
    assert cli._T0 > 0


def test_git_head_is_the_repo_head_or_none(monkeypatch):
    head = cli._git_head()
    assert head is None or re.fullmatch(r"[0-9a-f]{40}", head)
    expected = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True).stdout.strip()
    assert head == (expected or None)

    def broken(*args, **kwargs):
        raise subprocess.TimeoutExpired("git", 10)

    monkeypatch.setattr(cli.subprocess, "run", broken)
    assert cli._git_head() is None

    def missing(*args, **kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(cli.subprocess, "run", missing)
    assert cli._git_head() is None


@pytest.mark.slow
def test_13_help_names_the_options_and_the_focus_warning():
    done = subprocess.run([sys.executable, "-m", "src.route_view", "--help"], cwd=REPO_ROOT, capture_output=True,
                          text=True, timeout=120)
    assert done.returncode == 0, done.stderr
    for option in ("--follow", "--ready", "--replay"):
        assert option in done.stdout
    # argparse fills the description with textwrap.fill at the subprocess's 80 columns, so the phrase may wrap (RV6 m3)
    assert ("Never open a replay while a walk runs: clicking it takes focus from the game."
            in " ".join(done.stdout.split()))
