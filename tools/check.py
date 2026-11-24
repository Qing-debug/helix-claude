"""One command that runs every check: lint, types, import boundaries, then the tests.

    python tools/check.py            fast loop: ruff, basedpyright, lint-imports, pytest -m "not slow"
    python tools/check.py --full     the same with the whole suite, slow tests included (run this before you commit)
    python tools/check.py --keep-going   do not stop at the first failing step; run all four and report each

Runs from any directory. Every tool runs from the repo root, because basedpyright finds the third-party packages
through `venvPath = "."` in pyproject.toml. The tools come from the environment of the running interpreter
(`Path(sys.executable).parent`), so the venv does not have to be activated:
`.venv\\Scripts\\python.exe tools/check.py` works as it is.

Prints one PASS/FAIL line per step (with the tool's last output line; for pytest, its summary line), the output of any failing step, and the total
wall time. Exits 0 if every step passed, else 1. Needs `pip install -r requirements-dev.txt`.

lint-imports runs with `--no-cache`: its cache went stale once after a module was deleted, and `--no-cache` costs
about 0.3 s. After the basedpyright step it warns if `.basedpyright/baseline.json` changed: basedpyright rewrites the
baseline when a baselined error disappears, so commit the file with your change.
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TOOLS_DIR = Path(sys.executable).parent


def _tool(name: str) -> str | None:
    """The tool's executable in the interpreter's environment (`.exe` suffix handled), or None if it is not installed."""
    return shutil.which(name, path=str(TOOLS_DIR))


def _steps(full: bool) -> list[tuple[str, list[str]]]:
    pytest_args = ["-q"] if full else ["-m", "not slow", "-q"]
    return [
        ("ruff check .", [_tool("ruff") or "ruff", "check", "."]),
        ("basedpyright", [_tool("basedpyright") or "basedpyright"]),
        ("lint-imports --no-cache", [_tool("lint-imports") or "lint-imports", "--no-cache"]),
        ("pytest" + ("" if full else ' -m "not slow"'), [sys.executable, "-m", "pytest", *pytest_args]),
    ]


def _baseline_warning() -> str | None:
    try:
        done = subprocess.run(["git", "status", "--porcelain", ".basedpyright"], cwd=REPO, capture_output=True,
                              text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode == 0 and done.stdout.strip():
        return ("WARNING: .basedpyright/ differs from git; commit baseline.json with your change "
                "(an error that was baselined disappeared, or the file was rewritten). `git status` shows it.")
    return None


def _run(cmd: list[str]) -> tuple[bool, str, float]:
    started = time.perf_counter()
    try:
        # HELIX_NO_ELEVATE: no test may relaunch Helix as administrator (a real Windows prompt on Hong's screen; H82)
        done = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              env={**os.environ, "HELIX_NO_ELEVATE": "1"})
        ok, output = done.returncode == 0, (done.stdout or "") + (done.stderr or "")
    except OSError as error:
        ok, output = False, f"could not start {cmd[0]}: {error}\nRun `pip install -r requirements-dev.txt`."
    return ok, output, time.perf_counter() - started


def _last_line(output: str) -> str:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    return lines[-1] if lines else ""


# pytest's closing line, like "797 passed, 10 skipped in 66.05s (0:01:06)" or "no tests ran in 0.01s"
_PYTEST_SUMMARY = re.compile(r"\b(\d+ (passed|failed|errors?|skipped|deselected|xfailed|xpassed)|no tests ran)\b.* in [\d.]+s")


def _summary_line(label: str, output: str) -> str:
    """The line a step shows: for pytest its own summary line (a warning on stderr can come after it, the OpenCV one
    does), else the last line of the output."""
    if label.startswith("pytest"):
        for line in reversed(output.splitlines()):
            if _PYTEST_SUMMARY.search(line):
                return line.strip("= ").strip()
    return _last_line(output)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run ruff, basedpyright, lint-imports and pytest; exit 1 on any failure.")
    parser.add_argument("--full", action="store_true", help="run the slow tests too (the whole suite)")
    parser.add_argument("--keep-going", action="store_true", help="run every step even after one fails")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    started = time.perf_counter()
    failed: list[str] = []
    for label, cmd in _steps(args.full):
        ok, output, seconds = _run(cmd)
        print(f"{'PASS' if ok else 'FAIL'}  {label}  ({seconds:.1f} s)  {_summary_line(label, output)}", flush=True)
        if not ok:
            failed.append(label)
            print(output.rstrip(), flush=True)
        if label == "basedpyright":
            warning = _baseline_warning()
            if warning:
                print(warning, flush=True)
        if not ok and not args.keep_going:
            break
    total = time.perf_counter() - started
    if failed:
        print(f"FAILED: {', '.join(failed)}  (total {total:.1f} s)")
        return 1
    print(f"ALL PASS  ({'full' if args.full else 'fast'}, total {total:.1f} s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
