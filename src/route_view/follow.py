"""Following a run folder that a walk is still writing (route view plan task RV-8; route view spec 5.4; route tab spec 5.8).

`Follower` reads the complete new lines of `steps.jsonl` and `frames/index.jsonl` since its last poll (a line without
its newline waits for the next poll). `LiveController` polls it every `POLL_MS` from Tk's event loop, grows the model,
redraws only when something changed (decoding only the newest frame), says so when no step has come for `SILENT_S`,
and at `end` stops following and leaves the window as last drawn, still live (no replay bar, the live picture kept),
with its title's mode set to `ended: <reason>` (route tab H68, H69, plan RT-8c; it makes no call on the root). With a
`live_frame` callable (route tab spec 5.8, plan choice 12) the picture behind the view is the newest frame it returns,
not a recorded file. `stop()` ends the ticks: after it `tick()` and `update()` return at once, because a tick already
queued with `after` can still fire.

Where the plan is silent (each decided here, local and reversible):
- The controller owns the `Follower`; a caller that wants a synchronous first poll calls `update()` (one poll, no
  scheduling) before `tick()` (PF81 m11: the first poll is the controller's own, nothing is read twice).
- "A new line" for the silence clock is a new step event (a bad line or a frame is not a step).
- Leaving the silence clears the status line (`REPLAY_HINT`) before the redraw, which sets any read error again.
- An error is printed once per distinct message (a repeat of the last one is not printed again).
- An event the model rejects is tried again on the next tick, then skipped, and the status line says how many were
  skipped (RV-9 review m2, orchestrator ruling 88); the lines behind it go in.
- Events read before any header (only possible when line 1 is bad) cannot be drawn and are dropped; frames read
  before the header are kept for the model.
"""

import time
import tkinter as tk
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from src.route.step_log import STEPS_FILE, RunHeader, StepEvent, StepTail
from src.route_view.model import FRAME_INDEX, FRAMES_DIR, RunModel, read_frame_index
from src.route_view.window import RouteViewWindow

POLL_MS = 100
SILENT_S = 10.0  # spec 5.4
LIVE = "LIVE"
SILENT_MODE = "LIVE · no new steps for 10 s: the walk is still running or has crashed"  # spec 5.4, word for word
REPLAY_HINT = "if the walk has ended or crashed, see the console; the replay opens when it ends"  # plan choice 11


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
    def __init__(self, root: tk.Misc, window: RouteViewWindow, folder: Path, *, repo_root: Path,
                 current_git_head: str | None, clock: Callable[[], float] = time.monotonic,
                 live_frame: Callable[[], np.ndarray | None] | None = None) -> None:
        self._root = root
        self.window = window
        self._folder = Path(folder)
        self._repo_root = repo_root
        self._current_git_head = current_git_head
        self._clock = clock
        self._live_frame = live_frame
        self._frame: np.ndarray | None = None  # the array the kept picture was made from (a new object is a change)
        self._picture: Image.Image | None = None  # the kept live picture, passed on every `show` (plan choice 12)
        self.follower = Follower(self._folder)
        self.ended = False
        self.bad_lines = 0
        self._last_step = clock()
        self._silent = False
        self._background: str | None = None  # the newest frame file read so far
        self._retry = False  # the last redraw could not read its picture: try again next tick
        # Lines read but not yet in the model (RV-8 review m2): they stay here until they are, so an error later in
        # a tick loses none of them. Frames read before the header wait here for the model too.
        self._pending: list[StepEvent] = []
        self._failed: StepEvent | None = None  # the event the model rejected last tick: one more try, then it is skipped
        self._skipped = 0  # events skipped so far (RV-9 review m2)
        self._frames: list[tuple[int, str]] = []
        self._end_read = False  # an `end` line has been read (whether or not it reached the model)
        self._end_reason: str | None = None  # that line's reason, for the title (the model may not have taken it)
        self._printed: str | None = None
        self._stopped = False

    def stop(self) -> None:
        """No further tick is scheduled, and `update()` and `tick()` return at once from here on. Idempotent. A tick
        already queued with `after` can fire after the owner destroyed the window; it finds this flag and returns."""
        self._stopped = True

    def tick(self) -> None:
        """One poll, then the next one in `POLL_MS` unless the run has ended or `stop()` was called."""
        self.update()
        if not self.ended and not self._stopped:
            self._root.after(POLL_MS, self.tick)

    def update(self) -> None:
        """One poll and redraw; never raises (hard rule 5): an error goes to the status line and stdout. Lines the
        failed poll read stay pending for the next one; but if `end` was read, the run ends at once (the window is
        left as last drawn, RV-8 review m2): the walk is over, and the same error may come every tick."""
        if self.ended or self._stopped:
            return
        try:
            self._update()
        except Exception as err:
            self._report(f"follow error: {type(err).__name__}: {err}")
            if self._end_read:
                try:
                    self._end()
                except Exception as late:
                    self._report(f"follow error: {type(late).__name__}: {late}")

    def _update(self) -> None:
        polled = self.follower.poll()
        new_picture = self._read_live_frame()
        for event in polled.events:
            if event.ev == "end":
                self._end_read, self._end_reason = True, event.data.get("reason")
        self._pending.extend(polled.events)
        if polled.frames:
            self._background = polled.frames[-1][1]
        window = self.window
        changed = self._retry or new_picture
        if polled.header is not None and window.model is None:
            model = RunModel.open(self._folder, polled.header, "steps", repo_root=self._repo_root,
                                  current_git_head=self._current_git_head)
            model.add_frames(self._frames)
            self._frames = []
            if self._picture is not None:
                window.show(0, picture=self._picture)  # so set_model's own redraw keeps it and reads no file
            window.set_model(model)
            changed = True
        model = window.model
        now = self._clock()
        if polled.events:
            self._last_step = now
        self._frames.extend(polled.frames)
        if polled.frames:
            changed = True
        if model is not None and self._frames:
            model.add_frames(self._frames)
            self._frames = []
            changed = True
        if model is None:
            self._pending.clear()  # events read before any header are dropped (only if line 1 is bad)
        elif self._pending:
            changed = True
            done = 0
            try:
                for event in self._pending:  # one at a time: after an error only the lines from it on stay pending
                    try:
                        model.extend((event,))
                    except Exception as err:
                        if event is not self._failed:  # the first rejection: it stays at the head for the next tick
                            self._failed = event
                            raise
                        self._failed = None  # rejected again: skipped, so one bad event cannot freeze the view
                        self._skipped += 1
                        window.set_status(f"skipped {self._skipped} event{'s' if self._skipped != 1 else ''} the "
                                          f"view could not read (last: {type(err).__name__}: {err})")
                    done += 1
            finally:
                del self._pending[:done]
        ended = model is not None and model.ended
        silent = not ended and now - self._last_step >= SILENT_S
        if silent != self._silent:
            self._silent = silent
            window.set_mode(SILENT_MODE if silent else LIVE)
            window.set_status(REPLAY_HINT if silent else "")
        if changed:
            window.sync_why()
            last = len(model.events) - 1 if model is not None and model.events else 0
            # One decode at most, the newest frame (spec 5.4); a live picture, when there is one, wins over the file.
            window.show(last, background=self._background, picture=self._picture)
            self._retry = not window.background_ok
        if polled.bad_lines != self.bad_lines:
            self.bad_lines = polled.bad_lines
            window.set_status(f"{self.bad_lines} bad lines skipped")
        if ended:
            self._end()

    def _read_live_frame(self) -> bool:
        """Spec 5.8, plan choice 12: ask `live_frame` for the newest frame. A new array object makes a new picture (and
        counts as a change); the same object, or `None`, keeps the picture made so far. True when there is a new one.
        It runs after the poll consumed the files, so it must not raise: an error in the callable or in `fromarray` is
        reported (status line and console) and the picture made so far stays (RT-5 review I1)."""
        if self._live_frame is None:
            return False
        try:
            frame = self._live_frame()
            if frame is None or frame is self._frame:
                return False
            picture = Image.fromarray(frame)
        except Exception as err:
            self._report(f"follow error: live frame: {type(err).__name__}: {err}")
            return False
        self._frame, self._picture = frame, picture
        return True

    def _end(self) -> None:
        """`end` read (spec 5.4; route tab spec 5.2, H68, H69): stop following and leave the window as last drawn. It
        stays live, so no replay bar shows and its last live picture, panel and why lines stay; only the title's mode
        part becomes `ended: <reason>` (`ended: no reason` for an `end` without one), as a replay's title reads. No
        call on the root and no callback: the owner learns of the end from `ended`. Runs at most once: if a call in it
        raises, `update`'s error path does not run it again (RV-9 review m3)."""
        if self.ended:
            return
        self.ended = True
        self.window.set_mode(f"ended: {self._end_reason or 'no reason'}")

    def _report(self, text: str) -> None:
        self.window.set_status(text)
        if text != self._printed:
            print(text, flush=True)  # the console of whoever started the app (or the walk)
            self._printed = text
