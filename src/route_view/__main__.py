"""The route view's command line: `python -m src.route_view --follow <run folder>` (route view plan, contract V6).

RV-7 opens every folder as a replay. RV-8 adds following a folder a walk is still writing, `--ready` and `--replay`;
until then those two are parsed and do nothing. Windows calls and Tk both happen inside `main`, never at import.
"""
import time

_T0 = time.perf_counter()  # the start of "window built in <s> s" (L4 reads it from view.log): before the heavy imports

import argparse  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
import tkinter as tk  # noqa: E402
from collections.abc import Callable  # noqa: E402
from pathlib import Path  # noqa: E402

from returns.pipeline import is_successful  # noqa: E402

from src.route_view.model import load_folder  # noqa: E402
from src.route_view.window import RouteViewWindow  # noqa: E402

_REPO = Path(__file__).resolve().parents[2]


def _git_head() -> str | None:
    """`git rev-parse HEAD` in the repo root, or None on any error. A copy of `walk.py:181-187` (the viewer may not
    import `walk.py`, spec 5.8). `subprocess.run`'s `timeout` kills the child and raises `TimeoutExpired`, a
    `SubprocessError` (Python 3.14 `subprocess.py:169`, `run` :512-527)."""
    try:
        done = subprocess.run(["git", "rev-parse", "HEAD"], cwd=_REPO, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    head = done.stdout.strip()
    return head if done.returncode == 0 and head else None


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m src.route_view",
        description="Show what the route walker planned and why, from a run folder of steps.jsonl (or legs.jsonl). "
                    "Never open a replay while a walk runs: clicking it takes focus from the game.")
    parser.add_argument("--follow", required=True, metavar="FOLDER", help="the run folder to show")
    parser.add_argument("--ready", metavar="PATH", help="write PATH once the window is up (walk --view passes it)")
    parser.add_argument("--replay", action="store_true",
                        help="open as a replay even if the run has no end line (a walk that crashed or was killed); "
                             "not with --ready")
    return parser


def main(argv: list[str] | None = None, *, make_root: Callable[[], tk.Tk] = tk.Tk, run_loop: bool = True) -> int:
    args = _parser().parse_args(argv)
    try:
        root = make_root()
    except tk.TclError as err:
        print(f"error: cannot open a window: {err}", file=sys.stderr)
        return 2
    root.withdraw()
    folder = Path(args.follow)
    loaded = load_folder(folder, repo_root=_REPO, current_git_head=_git_head())
    if not is_successful(loaded):
        print(f"error: {loaded.failure()}", file=sys.stderr)
        root.destroy()
        return 2
    window = RouteViewWindow(root, folder, loaded.unwrap(), live=False)  # shows the last step
    print(f"window built in {time.perf_counter() - _T0:.2f} s", flush=True)
    root.deiconify()
    if run_loop:
        root.mainloop()
    del window  # referenced until here: its `shown_photo` must outlive the mainloop, and Tk's callbacks are no contract
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
