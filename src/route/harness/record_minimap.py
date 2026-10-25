"""Minimap recorder for supervised sessions (plan task B17).

    python -m src.route.harness.record_minimap --out data/recordings/<name> [--seconds 120] [--fps 10]

Run from the repo root with the game visible on the captured monitor. It starts the vision capture thread, polls the
frame that thread shares and saves each new one. It sends no keys.

`record` polls `frames()` every 1/fps s and saves each new frame object (not `is` the last one saved) as
`frame_%06d.png` in `out_dir`, converted RGB to BGR for `cv2.imwrite`, plus one line in `index.jsonl`, flushed per line:
`{"i", "t_ns", "file", "w", "h"}`. It stops when `seconds` have passed or `stop` is set, whichever comes first; the end
test runs after each poll, so a `stop` that is already set still saves the first frame. `shared_frames` is this tool's
reader of the shared frame under its lock, for `main` and for B19 (the GUI, the map editor, the controller and
`src/route/position.py` read it under the same lock).

Where the plan is silent:
- `i` counts saved frames from 0 and is the number in the file name. `t_ns` is `clock()` read just after `frames()`
  returned, so with the default clock it is `time.perf_counter_ns`, the stimulus clock of Feature A (`input_layer.py`).
  `file` is a name relative to `out_dir`, so a recording folder can be moved.
- Polls are scheduled from the start (poll k is due at start + k/fps), so the time a poll takes is not added to the
  interval. A poll that runs late is not caught up: the next one is due one interval after it, not several at once.
- `None` from `frames()` (no frame yet) is not a frame and is not counted.
- `fps` must be finite and above 0; `seconds`, when given, finite and at least 0 (0 saves the first frame only).
- An `out_dir` that already holds an `index.jsonl` is refused with a `Failure`: a second recording would overwrite the
  first one's frames. `record` returns a `Failure` for any `OSError` on `out_dir` or `index.jsonl`, and when
  `cv2.imwrite` returns False; frames already saved stay.
- `--out` must resolve to a folder inside the repo's gitignored `data/` (not `data/` itself), else `main` returns 2.
  `main` returns 1 when `record` fails or saves nothing (for example the game is not visible on the captured monitor, or
  the capture thread died), and 0 otherwise. If the capture thread ends, the recording ends with the frames so far.

Imports: `src.vision` (bettercam, Windows only) is imported inside `main` only, so this module and its tests load
anywhere (plan hard rule 9).
"""

import argparse
import json
import math
import sys
import threading
import time
from pathlib import Path
from typing import Callable

import cv2
import numpy as np
from returns.pipeline import is_successful
from returns.result import Failure, Result, Success

INDEX_NAME = "index.jsonl"
_REPO = Path(__file__).resolve().parents[3]
_DATA = _REPO / "data"


def _check_arguments(seconds: float | None, fps: float, stop: threading.Event | None) -> Result[None, str]:
    if seconds is None and stop is None:
        return Failure("give seconds or stop, otherwise the recording never ends")
    if not (math.isfinite(fps) and fps > 0):
        return Failure(f"fps must be a finite number above 0, got {fps!r}")
    if seconds is not None and not (math.isfinite(seconds) and seconds >= 0):
        return Failure(f"seconds must be a finite number of at least 0, got {seconds!r}")
    return Success(None)


def record(frames: Callable[[], np.ndarray | None], out_dir: Path, seconds: float | None, fps: float,
           clock: Callable[[], int] = time.perf_counter_ns, sleep: Callable[[float], None] = time.sleep,
           stop: threading.Event | None = None) -> Result[int, str]:
    """Save the new frames `frames()` returns, `fps` polls a second, until `seconds` pass or `stop` is set.

    `frames` returns an RGB uint8 H x W x 3 array or None. Returns `Success(number of frames saved)`.
    """
    checked = _check_arguments(seconds, fps, stop)
    if not is_successful(checked):
        return checked
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return Failure(f"cannot create {out_dir}: {e}")
    try:
        index = open(out_dir / INDEX_NAME, "x", encoding="utf-8")
    except FileExistsError:
        return Failure(f"{out_dir / INDEX_NAME} already exists; refusing to overwrite a recording")
    except OSError as e:
        return Failure(f"cannot create {out_dir / INDEX_NAME}: {e}")

    interval_ns = round(1e9 / fps)
    limit_ns = None if seconds is None else round(seconds * 1e9)
    saved = 0
    last = None  # a reference, not an id(): once a frame is freed, a new frame can be given its id
    with index:
        start = clock()
        due = start
        while True:
            frame = frames()
            t_ns = clock()
            if frame is not None and frame is not last:
                name = f"frame_{saved:06d}.png"
                if not cv2.imwrite(str(out_dir / name), cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)):
                    return Failure(f"cannot write {out_dir / name} ({saved} frames saved before it)")
                height, width = frame.shape[:2]
                index.write(json.dumps({"i": saved, "t_ns": t_ns, "file": name, "w": width, "h": height}) + "\n")
                index.flush()
                last = frame
                saved += 1
            if stop is not None and stop.is_set():
                break
            if limit_ns is not None and clock() - start >= limit_ns:
                break
            due += interval_ns
            wait_ns = due - clock()
            if wait_ns > 0:
                sleep(wait_ns / 1e9)
            else:
                due = clock()  # running late: the next poll is one interval from now, not a burst to catch up
    return Success(saved)


def shared_frames(shared) -> Callable[[], np.ndarray | None]:
    """A `frames` callable over `shared.minimap_frame`, read under `shared.minimap_frame_lock`.

    It returns the shared reference itself, never a copy. The capture thread (src/vision/vision.py) assigns a new array
    to `minimap_frame` for every frame, so `record`'s `is` test sees a frame once; a copy would make every poll new.
    """
    def read() -> np.ndarray | None:
        with shared.minimap_frame_lock:
            return shared.minimap_frame
    return read


def _resolve_out(out: Path) -> Result[Path, str]:
    """`out` resolved, if it is a folder inside the repo's `data/`."""
    resolved = out.resolve()
    data = _DATA.resolve()
    if resolved == data or not resolved.is_relative_to(data):
        return Failure(f"--out must be a folder inside {data}, got {resolved}")
    return Success(resolved)


class _Shared:
    """What the capture thread needs of `SharedInfo` (src/main.py), without importing the GUI that module pulls in."""

    def __init__(self) -> None:
        self.minimap_frame_lock = threading.Lock()
        self.minimap_frame = None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m src.route.harness.record_minimap",
                                     description="Record the minimap frames the vision thread captures. Sends no keys.")
    parser.add_argument("--out", type=Path, required=True,
                        help="recording folder, inside the repo's data/ (for example data/recordings/ff2_2026-10-24)")
    parser.add_argument("--seconds", type=float, default=120.0, help="how long to record (default 120)")
    parser.add_argument("--fps", type=float, default=10.0, help="polls per second (default 10)")
    args = parser.parse_args(argv)

    out = _resolve_out(args.out)
    if not is_successful(out):
        print(f"error: {out.failure()}", file=sys.stderr)
        return 2
    checked = _check_arguments(args.seconds, args.fps, None)
    if not is_successful(checked):
        print(f"error: {checked.failure()}", file=sys.stderr)
        return 2

    from src.vision.vision import capture_and_process_minimap  # Windows only (bettercam); not at module level

    shared = _Shared()
    capture_ended = threading.Event()

    def capture() -> None:
        try:
            capture_and_process_minimap(shared)
        finally:
            capture_ended.set()  # it loops forever, so reaching here means it raised: stop recording, keep the frames

    threading.Thread(target=capture, name="record-capture", daemon=True).start()
    result = record(shared_frames(shared), out.unwrap(), args.seconds, args.fps, stop=capture_ended)
    if not is_successful(result):
        print(f"error: {result.failure()}", file=sys.stderr)
        return 1
    saved = result.unwrap()
    print(f"{saved} frames in {out.unwrap()}")
    if saved == 0:
        print("error: no frame was captured; is the game visible on the captured monitor?", file=sys.stderr)
        return 1
    if capture_ended.is_set():
        print("warning: the capture thread ended early; the recording is shorter than --seconds", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
