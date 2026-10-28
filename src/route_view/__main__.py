"""The route view's command line: `python -m src.route_view --follow <run folder>` (route view plan, contract V6).

A folder whose `steps.jsonl` ends with `end`, or that has only `legs.jsonl`, opens as a replay; `--replay` opens any
folder as one. Else the view follows the folder live (RV-8): with `--ready` it first claims the live show
(`view.claim`), shows its window without activating it (spec 5.7), then writes `--ready`; a viewer that finds the claim
taken follows hidden and shows its window only at `end` (spec 5.9 step 3). Windows calls and Tk both happen inside
`main`, never at import.
"""
import time

_T0 = time.perf_counter()  # the start of "window built in <s> s" (L4 reads it from view.log): before the heavy imports

import argparse  # noqa: E402
import os  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
import tkinter as tk  # noqa: E402
from collections.abc import Callable  # noqa: E402
from pathlib import Path  # noqa: E402

from returns.pipeline import is_successful  # noqa: E402

from src.route.step_log import STEPS_FILE, VIEW_CLAIM  # noqa: E402
from src.route_view import win_focus  # noqa: E402
from src.route_view.follow import Focus, LiveController  # noqa: E402
from src.route_view.model import load_folder  # noqa: E402
from src.route_view.window import RouteViewWindow  # noqa: E402

CLICK_WARNING = "clicking here takes focus from the game"  # RV1-D6's fallback line

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


def main(argv: list[str] | None = None, *, make_root: Callable[[], tk.Tk] = tk.Tk, run_loop: bool = True,
         focus: Focus = win_focus, clock: Callable[[], float] = time.monotonic) -> int:
    args = _parser().parse_args(argv)
    if args.replay and args.ready is not None:
        print("error: --replay cannot be used with --ready", file=sys.stderr)
        return 2
    try:
        root = make_root()
    except tk.TclError as err:
        print(f"error: cannot open a window: {err}", file=sys.stderr)
        return 2
    root.withdraw()
    folder = Path(args.follow)
    head = _git_head()
    if args.replay or not (folder / STEPS_FILE).is_file():
        loaded = load_folder(folder, repo_root=_REPO, current_git_head=head)  # a replay (RV-7), whatever --ready says
        if not is_successful(loaded):
            print(f"error: {loaded.failure()}", file=sys.stderr)
            root.destroy()
            return 2
        window = RouteViewWindow(root, folder, loaded.unwrap(), live=False)  # shows the last step
        _built()
        root.deiconify()
    else:
        window = RouteViewWindow(root, folder, None, live=True)
        live = LiveController(root, window, folder, repo_root=_REPO, current_git_head=head, focus=focus, clock=clock,
                              hidden=False, styled_hwnd=None)
        live.update()  # the walk wrote the header before it started the viewer (spec 5.9 step 1)
        if live.ended:  # a finished folder: a replay, no claim, no style, --ready ignored (plan choice 9)
            _built()
            root.deiconify()
        else:
            root.update_idletasks()  # Tk makes its wrapper window here (RV6 I1); the root stays withdrawn
            _built()
            if args.ready is not None and not _claim(folder / VIEW_CLAIM):
                live.hidden = True  # the walk holds the claim: follow hidden, show only at `end` (spec 5.9 step 3)
            else:
                live.styled_hwnd = _first_show(root, window, focus)
                print(f"first show: root.state() = {root.state()}", flush=True)
                if args.ready is not None:
                    _write_ready(Path(args.ready))
            live.tick()
    if run_loop:
        root.mainloop()
    return 0


def _built() -> None:
    print(f"window built in {time.perf_counter() - _T0:.2f} s", flush=True)


def _claim(path: Path) -> bool:
    """Spec 5.9 step 3: create `view.claim`; False when it exists (the walk took it) or on any OSError (the safe
    side: a viewer that cannot claim never shows its window while the walk runs)."""
    try:
        open(path, "x").close()
    except OSError:
        return False
    return True


def _first_show(root: tk.Tk, window: RouteViewWindow, focus: Focus) -> int | None:
    """The style on the wrapper, then the show without activating (spec 5.7), nothing else in between. If a call fails:
    `focus call failed: <error>`, Tk's plain `deiconify` and the warning line (RV1-D6's fallback). Returns the wrapper
    whose style `end` must clear (also when setting it failed: it may be half set), or None."""
    found = focus.wrapper_hwnd(root)
    hwnd = found.unwrap() if is_successful(found) else None
    shown = found if hwnd is None else focus.set_live_style(hwnd, True).bind(lambda _: focus.show_first(root, hwnd))
    if not is_successful(shown):
        print(f"focus call failed: {shown.failure()}", flush=True)
        root.deiconify()
        window.add_warning(CLICK_WARNING)
    return hwnd


def _write_ready(path: Path) -> None:
    """`<ready>.tmp` holding this process's pid, then `os.replace` onto `path`; an OSError is printed (the walk then
    waits its bounded time, spec 5.9 step 3)."""
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(str(os.getpid()), encoding="ascii")
        os.replace(tmp, path)
    except OSError as err:
        print(f"cannot write {path}: {err}", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
