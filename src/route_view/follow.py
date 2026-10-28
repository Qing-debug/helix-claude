"""Following a run folder that a walk is still writing (route view plan task RV-8; spec 5.4, 5.7, 5.9 step 3).

`Follower` reads the complete new lines of `steps.jsonl` and `frames/index.jsonl` since its last poll (a line without
its newline waits for the next poll). `LiveController` polls it every `POLL_MS` from Tk's event loop, grows the model,
redraws only when something changed (decoding only the newest frame), says so when no step has come for `SILENT_S`,
and at `end` switches the window to replay: the no-activate style is cleared, a hidden window is shown.

Where the plan is silent (each decided here, local and reversible):
- The controller owns the `Follower`; `__main__` builds the controller before the claim and calls `update()` (one poll,
  no scheduling) as its synchronous first poll, then sets `hidden` / `styled_hwnd` once the claim and the first show
  are done (PF81 m11: the first poll is the controller's own, nothing is read twice).
- "A new line" for the silence clock is a new step event (a bad line or a frame is not a step).
- Leaving the silence clears the status line (the `--replay` hint) before the redraw, which sets any read error again.
- An error is printed once per distinct message (a repeat of the last one is not printed again).
- Events read before any header (only possible when line 1 is bad) cannot be drawn and are dropped; frames read
  before the header are kept for the model.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from returns.pipeline import is_successful
from returns.result import Result

from src.route.step_log import STEPS_FILE, RunHeader, StepEvent, StepTail
from src.route_view import win_focus
from src.route_view.model import FRAME_INDEX, FRAMES_DIR, RunModel, read_frame_index
from src.route_view.window import RouteViewWindow

POLL_MS = 100
SILENT_S = 10.0  # spec 5.4
LIVE = "LIVE"
SILENT_MODE = "LIVE · no new steps for 10 s: the walk is still running or has crashed"  # spec 5.4, word for word
REPLAY_HINT = "if the walk has ended, open this folder with --replay to step through it"  # RV4-D1 A


class Focus(Protocol):
    """`win_focus`'s three functions (tests pass a fake)."""

    def wrapper_hwnd(self, root: Any) -> Result[int, str]: ...
    def set_live_style(self, hwnd: int, live: bool) -> Result[None, str]: ...
    def show_first(self, root: Any, hwnd: int) -> Result[None, str]: ...


@dataclass(frozen=True)
class Polled:
    header: RunHeader | None  # the file's first header, on the poll that read it
    events: list[StepEvent]
    frames: list[tuple[int, str]]  # new complete index lines (read_frame_index on the new bytes, V5)
    bad_lines: int  # StepTail.bad_lines (a `run` line after line 1 is one, V1) + frame-index bad lines, totals so far


class Follower:
    def __init__(self, folder: Path) -> None:
        folder = Path(folder)
        self._tail = StepTail(folder / STEPS_FILE)
        self._index = folder / FRAMES_DIR / FRAME_INDEX
        self._index_offset = 0
        self._index_bad = 0

    def poll(self) -> Polled:
        """The new complete lines of both files; never raises, a missing file gives nothing."""
        header: RunHeader | None = None
        events: list[StepEvent] = []
        for item in self._tail.poll():
            if isinstance(item, RunHeader):
                header = item  # StepTail passes a header only from line 1
            else:
                events.append(item)
        return Polled(header, events, self._poll_index(), self._tail.bad_lines + self._index_bad)

    def _poll_index(self) -> list[tuple[int, str]]:
        try:
            with self._index.open("rb") as handle:
                handle.seek(self._index_offset)
                data = handle.read()
        except OSError:
            return []
        end = data.rfind(b"\n") + 1  # complete lines only; a torn last line is read again next time
        if end == 0:
            return []
        self._index_offset += end
        frames, bad = read_frame_index(data[:end])
        self._index_bad += bad
        return frames


class LiveController:
    def __init__(self, root: Any, window: RouteViewWindow, folder: Path, *, repo_root: Path,
                 current_git_head: str | None, focus: Focus = win_focus, clock: Callable[[], float] = time.monotonic,
                 hidden: bool, styled_hwnd: int | None, follower: Follower | None = None) -> None:
        self._root = root
        self.window = window
        self._folder = Path(folder)
        self._repo_root = repo_root
        self._current_git_head = current_git_head
        self._focus = focus
        self._clock = clock
        self.hidden = hidden  # the walk took the claim: the window stays withdrawn until `end`
        self.styled_hwnd = styled_hwnd  # the wrapper that got the no-activate style; cleared at `end`
        self.follower = follower if follower is not None else Follower(self._folder)
        self.ended = False
        self.bad_lines = 0
        self._last_step = clock()
        self._silent = False
        self._background: str | None = None  # the newest frame file read so far
        self._retry = False  # the last redraw could not read its picture: try again next tick
        self._early_frames: list[tuple[int, str]] = []
        self._printed: str | None = None

    def tick(self) -> None:
        """One poll, then the next one in `POLL_MS` unless the run has ended."""
        self.update()
        if not self.ended:
            self._root.after(POLL_MS, self.tick)

    def update(self) -> None:
        """One poll and redraw; never raises (hard rule 5): an error goes to the status line and stdout."""
        if self.ended:
            return
        try:
            self._update()
        except Exception as err:
            self._report(f"follow error: {type(err).__name__}: {err}")

    def _update(self) -> None:
        polled = self.follower.poll()
        window = self.window
        changed = self._retry
        if polled.header is not None and window.model is None:
            model = RunModel.open(self._folder, polled.header, "steps", repo_root=self._repo_root,
                                  current_git_head=self._current_git_head)
            model.add_frames(self._early_frames)
            self._early_frames = []
            window.set_model(model)
            changed = True
        model = window.model
        now = self._clock()
        if polled.events:
            self._last_step = now
            if model is not None:
                model.extend(polled.events)
                changed = True
        if polled.frames:
            if model is not None:
                model.add_frames(polled.frames)
            else:
                self._early_frames.extend(polled.frames)
            self._background = polled.frames[-1][1]
            changed = True
        ended = model is not None and model.ended
        silent = not ended and now - self._last_step >= SILENT_S
        if silent != self._silent:
            self._silent = silent
            window.set_mode(SILENT_MODE if silent else LIVE)
            window.set_status(REPLAY_HINT if silent else "")
        if changed:
            window.sync_why()
            last = len(model.events) - 1 if model is not None and model.events else 0
            window.show(last, background=self._background)  # one decode at most, the newest frame (spec 5.4)
            self._retry = not window.background_ok
        if polled.bad_lines != self.bad_lines:
            self.bad_lines = polled.bad_lines
            window.set_status(f"{self.bad_lines} bad lines skipped")
        if ended:
            self._end()

    def _end(self) -> None:
        """`end` read (spec 5.4, 5.7): stop following, give the window its normal styles back, show it, replay."""
        self.ended = True
        if self.styled_hwnd is not None:
            cleared = self._focus.set_live_style(self.styled_hwnd, False)
            if not is_successful(cleared):
                self._report(f"focus call failed: {cleared.failure()}")
            self._root.withdraw()  # hidden and shown again so the taskbar sees the change (spec 5.7, part of N1)
            self._root.deiconify()
        elif self.hidden:
            self._root.deiconify()  # never styled: shown for the first time, already in replay (spec 5.9 step 3)
        self.window.set_live(False)  # the title's mode becomes `ended: <reason>`

    def _report(self, text: str) -> None:
        self.window.set_status(text)
        if text != self._printed:
            print(text, flush=True)  # stdout goes to view.log
            self._printed = text
