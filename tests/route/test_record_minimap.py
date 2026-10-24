"""Tests for src.route.harness.record_minimap (plan task B17): the minimap recorder.

`record` is driven by a fake `frames`, a fake `clock` and a fake `sleep`, so nothing here waits, captures the screen or
starts a thread, and every recording path is under pytest's `tmp_path`. `src.vision` is never imported (`main` imports
it inside the function, and no test calls `main` past its argument checks). Tests 1-5 are the plan's five (colours,
the `main` exit code, stop, and no-arguments); the rest pin what the plan leaves open: the poll schedule, None frames,
bad arguments, a recording that already exists, a failed write, and `shared_frames` (with a fake shared object and no
capture thread; the plan's end-to-end test of it is B19's).
"""

import json
import subprocess
import sys
import threading
from pathlib import Path

import cv2
import numpy as np
import pytest
from returns.pipeline import is_successful
from returns.result import Failure, Success

from src.route.harness import record_minimap
from src.route.harness.record_minimap import main, record, shared_frames

REPO = Path(__file__).resolve().parent.parent.parent
FRAME_H, FRAME_W = 6, 8
START_NS = 5_000_000_000


class FakeTime:
    """A virtual clock that only `sleep` (or a test) moves, and a `sleep` that records what it was asked."""

    def __init__(self, start_ns: int = START_NS) -> None:
        self.now = start_ns
        self.sleeps: list[float] = []

    def clock(self) -> int:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += round(seconds * 1e9)


def _rgb(seed: int) -> np.ndarray:
    """An RGB frame whose three channels differ, so a channel swap would show."""
    return np.random.default_rng(seed).integers(0, 256, size=(FRAME_H, FRAME_W, 3), dtype=np.uint8)


def _lines(out_dir: Path) -> list[dict]:
    text = (out_dir / "index.jsonl").read_text(encoding="utf-8")
    assert text == "" or text.endswith("\n")
    return [json.loads(line) for line in text.splitlines()]


def _read_rgb(path: Path) -> np.ndarray:
    bgr = cv2.imread(str(path), cv2.IMREAD_COLOR)
    assert bgr is not None, path
    return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)


class Feed:
    """A `frames` callable that answers from a list (the last answer repeats) and counts its calls."""

    def __init__(self, answers) -> None:
        self.answers = list(answers)
        self.calls = 0

    def __call__(self):
        answer = self.answers[min(self.calls, len(self.answers) - 1)]
        self.calls += 1
        return answer


# ---- the plan's five -------------------------------------------------------------------------------------------------

def test_1_five_new_frames_then_the_same_one_make_five_files_and_five_lines(tmp_path):
    fake = FakeTime()
    frames = [_rgb(i) for i in range(5)]
    feed = Feed(frames)  # five new arrays, then the fifth again
    out = tmp_path / "rec"
    result = record(feed, out, seconds=1.0, fps=10, clock=fake.clock, sleep=fake.sleep)
    assert result == Success(5)
    assert sorted(p.name for p in out.iterdir()) == ["frame_000000.png", "frame_000001.png", "frame_000002.png",
                                                     "frame_000003.png", "frame_000004.png", "index.jsonl"]
    assert _lines(out) == [{"i": i, "t_ns": START_NS + i * 100_000_000, "file": f"frame_{i:06d}.png",
                            "w": FRAME_W, "h": FRAME_H} for i in range(5)]
    for i, frame in enumerate(frames):
        assert np.array_equal(_read_rgb(out / f"frame_{i:06d}.png"), frame)


@pytest.mark.parametrize("kind", ["pure red", "all channels differ"])
def test_2_colours_round_trip(tmp_path, kind):
    if kind == "pure red":
        frame = np.zeros((FRAME_H, FRAME_W, 3), dtype=np.uint8)
        frame[..., 0] = 255  # RGB red
    else:
        frame = _rgb(2)
    fake = FakeTime()
    assert record(Feed([frame]), tmp_path / "rec", seconds=0.0, fps=10, clock=fake.clock, sleep=fake.sleep) == Success(1)
    back = _read_rgb(tmp_path / "rec" / "frame_000000.png")
    assert np.array_equal(back, frame)
    if kind == "pure red":
        assert tuple(back[0, 0]) == (255, 0, 0)
        raw_bgr = cv2.imread(str(tmp_path / "rec" / "frame_000000.png"), cv2.IMREAD_COLOR)
        assert tuple(raw_bgr[0, 0]) == (0, 0, 255)  # on disk it is an ordinary PNG: OpenCV reads it as BGR


@pytest.mark.parametrize("as_path", [False, True], ids=["/tmp/x", "under tmp_path"])
def test_3_main_refuses_an_out_dir_outside_data_with_exit_2(tmp_path, as_path):
    target = tmp_path / "x" if as_path else Path("/tmp/x")
    existed = target.exists()
    assert main(["--out", str(target)]) == 2
    assert target.exists() == existed  # main never creates it


def test_4_stop_event_ends_the_recording(tmp_path):
    fake = FakeTime()
    stop = threading.Event()
    calls = []

    def frames():
        calls.append(1)
        if len(calls) == 3:
            stop.set()
        return _rgb(len(calls))  # a new array on every call

    out = tmp_path / "rec"
    assert record(frames, out, seconds=None, fps=10, clock=fake.clock, sleep=fake.sleep, stop=stop) == Success(3)
    assert len(calls) == 3
    assert len(list(out.glob("frame_*.png"))) == 3
    assert [line["i"] for line in _lines(out)] == [0, 1, 2]
    assert fake.sleeps == [pytest.approx(0.1)] * 2  # the poll that set it is the last: no sleep after it


def test_5_neither_seconds_nor_stop_is_a_failure_and_writes_nothing(tmp_path):
    out = tmp_path / "rec"
    feed = Feed([_rgb(0)])
    result = record(feed, out, seconds=None, fps=10, stop=None)
    assert isinstance(result, Failure)
    assert not out.exists()
    assert feed.calls == 0


# ---- when the recording ends -----------------------------------------------------------------------------------------

def test_6_a_stop_already_set_still_saves_the_first_frame(tmp_path):
    fake = FakeTime()
    stop = threading.Event()
    stop.set()
    feed = Feed([_rgb(0)])
    out = tmp_path / "rec"
    assert record(feed, out, seconds=None, fps=10, clock=fake.clock, sleep=fake.sleep, stop=stop) == Success(1)
    assert feed.calls == 1
    assert fake.sleeps == []
    assert len(_lines(out)) == 1


def test_7_the_end_test_runs_after_each_poll_and_the_first_of_seconds_and_stop_wins(tmp_path):
    # seconds ends it: polls at 0, 0.5 and 1.0 s (fps 2); the poll at 1.0 s is made, then the end test passes
    fake = FakeTime()
    feed = Feed([_rgb(i) for i in range(10)])
    assert record(feed, tmp_path / "a", seconds=1.0, fps=2, clock=fake.clock, sleep=fake.sleep) == Success(3)
    assert feed.calls == 3
    assert fake.sleeps == [pytest.approx(0.5)] * 2
    # stop ends it first: stop is set on the 2nd poll, long before 100 s
    fake = FakeTime()
    stop = threading.Event()
    calls = []

    def frames():
        calls.append(1)
        if len(calls) == 2:
            stop.set()
        return _rgb(len(calls))

    assert record(frames, tmp_path / "b", seconds=100.0, fps=10, clock=fake.clock, sleep=fake.sleep,
                  stop=stop) == Success(2)
    # seconds ends it first: stop is given but never set
    fake = FakeTime()
    assert record(Feed([_rgb(i) for i in range(10)]), tmp_path / "c", seconds=0.2, fps=10, clock=fake.clock,
                  sleep=fake.sleep, stop=threading.Event()) == Success(3)


# ---- the poll schedule -----------------------------------------------------------------------------------------------

def test_8_polls_are_scheduled_from_the_start_not_from_the_end_of_the_previous_poll(tmp_path):
    fake = FakeTime()
    polled_at = []

    def frames():
        polled_at.append(fake.now)
        fake.now += 30_000_000  # reading and saving take 30 ms of the 100 ms interval
        return _rgb(len(polled_at))

    assert record(frames, tmp_path / "rec", seconds=0.5, fps=10, clock=fake.clock, sleep=fake.sleep) == Success(6)
    assert [t - START_NS for t in polled_at] == [i * 100_000_000 for i in range(6)]
    assert fake.sleeps == [pytest.approx(0.07)] * 5


def test_9_a_slow_poll_is_not_followed_by_a_burst_to_catch_up(tmp_path):
    fake = FakeTime()
    polled_at = []

    def frames():
        polled_at.append(fake.now)
        if len(polled_at) == 1:
            fake.now += 550_000_000  # one very slow poll, 5.5 intervals
        return _rgb(len(polled_at))

    assert record(frames, tmp_path / "rec", seconds=1.0, fps=10, clock=fake.clock, sleep=fake.sleep) == Success(7)
    assert polled_at[1] - START_NS == 550_000_000  # the 2nd poll follows the slow one at once: the schedule is re-anchored
    assert [b - a for a, b in zip(polled_at[1:], polled_at[2:])] == [100_000_000] * 5, polled_at  # then, normal spacing
    assert fake.sleeps == [pytest.approx(0.1)] * 5


# ---- what counts as a new frame --------------------------------------------------------------------------------------

def test_10_none_is_not_a_frame_and_the_numbering_stays_dense(tmp_path):
    fake = FakeTime()
    a, b = _rgb(1), _rgb(2)
    out = tmp_path / "rec"
    assert record(Feed([None, None, a, a, None, b]), out, seconds=0.5, fps=10, clock=fake.clock,
                  sleep=fake.sleep) == Success(2)
    assert [(line["i"], line["file"]) for line in _lines(out)] == [(0, "frame_000000.png"), (1, "frame_000001.png")]
    assert [line["t_ns"] - START_NS for line in _lines(out)] == [200_000_000, 500_000_000]  # when each was polled


def test_11_identity_not_content_makes_a_frame_new(tmp_path):
    fake = FakeTime()
    a = _rgb(1)
    out = tmp_path / "rec"
    assert record(Feed([a, a, a.copy(), a.copy()]), out, seconds=0.3, fps=10, clock=fake.clock,
                  sleep=fake.sleep) == Success(3)
    assert [np.array_equal(_read_rgb(out / f"frame_{i:06d}.png"), a) for i in range(3)] == [True] * 3


# ---- arguments, the output directory and failures --------------------------------------------------------------------

@pytest.mark.parametrize("fps", [0, -1, float("nan"), float("inf")], ids=str)
def test_12_a_bad_fps_is_a_failure_and_writes_nothing(tmp_path, fps):
    out = tmp_path / "rec"
    assert isinstance(record(Feed([_rgb(0)]), out, seconds=1.0, fps=fps), Failure)
    assert not out.exists()


@pytest.mark.parametrize("seconds", [-1.0, float("nan"), float("inf")], ids=str)
def test_13_a_bad_seconds_is_a_failure_and_writes_nothing(tmp_path, seconds):
    out = tmp_path / "rec"
    stop = threading.Event()
    stop.set()  # stop alone would be enough; the bad seconds is still refused
    assert isinstance(record(Feed([_rgb(0)]), out, seconds=seconds, fps=10, stop=stop), Failure)
    assert not out.exists()


def test_14_out_dir_is_created_with_its_parents(tmp_path):
    fake = FakeTime()
    out = tmp_path / "a" / "b" / "rec"
    assert record(Feed([_rgb(0)]), out, seconds=0.0, fps=10, clock=fake.clock, sleep=fake.sleep) == Success(1)
    assert out.is_dir() and (out / "frame_000000.png").is_file()


def test_15_an_existing_recording_is_not_overwritten(tmp_path):
    out = tmp_path / "rec"
    out.mkdir()
    (out / "index.jsonl").write_text('{"i": 0}\n', encoding="utf-8")
    (out / "frame_000000.png").write_bytes(b"old")
    fake = FakeTime()
    result = record(Feed([_rgb(0)]), out, seconds=0.0, fps=10, clock=fake.clock, sleep=fake.sleep)
    assert isinstance(result, Failure)
    assert (out / "index.jsonl").read_text(encoding="utf-8") == '{"i": 0}\n'
    assert (out / "frame_000000.png").read_bytes() == b"old"


def test_16_a_failed_png_write_is_a_failure_and_leaves_no_index_line_for_it(tmp_path, monkeypatch):
    out = tmp_path / "rec"
    monkeypatch.setattr(record_minimap.cv2, "imwrite", lambda path, img: False)  # what cv2.imwrite returns on failure
    fake = FakeTime()
    result = record(Feed([_rgb(0)]), out, seconds=0.0, fps=10, clock=fake.clock, sleep=fake.sleep)
    assert isinstance(result, Failure)
    assert _lines(out) == []


def test_17_an_out_dir_that_cannot_be_created_is_a_failure(tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("a file", encoding="utf-8")
    result = record(Feed([_rgb(0)]), blocker / "rec", seconds=0.0, fps=10)
    assert isinstance(result, Failure)


def test_18_each_index_line_is_flushed_when_written_not_at_close(tmp_path):
    fake = FakeTime()
    out = tmp_path / "rec"
    index = out / "index.jsonl"  # created before the first poll
    seen = []

    def frames():
        # what a reader (or a crash) would find on disk before this poll: every earlier line, complete
        seen.append(len(index.read_text(encoding="utf-8").splitlines()))
        return _rgb(len(seen))

    assert record(frames, out, seconds=0.3, fps=10, clock=fake.clock, sleep=fake.sleep) == Success(4)
    assert seen == [0, 1, 2, 3]


# ---- main and the module's imports -----------------------------------------------------------------------------------

def test_19_main_needs_out():
    with pytest.raises(SystemExit) as raised:
        main([])
    assert raised.value.code == 2


@pytest.mark.parametrize("args, ok", [
    (["--out", "data/recordings/x"], True),
    (["--out", "data/x"], True),
    (["--out", str(REPO / "data" / "recordings" / "x")], True),
    (["--out", "data"], False),
    (["--out", "data/../x"], False),
    (["--out", "datax/y"], False),
    (["--out", "/tmp/x"], False),
])
def test_20_out_must_resolve_under_the_repos_data_dir(monkeypatch, args, ok):
    monkeypatch.chdir(REPO)  # `--out` is relative to the repo root, as the CLI is run from there
    result = record_minimap._resolve_out(Path(args[1]))
    assert is_successful(result) == ok
    if ok:
        assert result.unwrap() == (REPO / args[1]).resolve()


def test_21_importing_the_module_does_not_import_src_vision_or_bettercam():
    code = ("import sys; sys.path.insert(0, sys.argv[1]); import src.route.harness.record_minimap; "
            "bad = [m for m in ('src.vision.vision', 'bettercam') if m in sys.modules]; "
            "sys.exit('imported: ' + ', '.join(bad) if bad else 0)")
    done = subprocess.run([sys.executable, "-I", "-c", code, str(REPO)], capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr


# ---- shared_frames (a fake shared object; no capture thread) ---------------------------------------------------------

class TrackingLock:
    """A wrapper around a `threading.Lock` (which cannot be subclassed on 3.14) that counts acquires and says if held."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.acquires = 0

    def held(self) -> bool:
        return self._lock.locked()

    def __enter__(self) -> "TrackingLock":
        self._lock.acquire()
        self.acquires += 1
        return self

    def __exit__(self, *exc_info) -> None:
        self._lock.release()


class FakeShared:
    """Duck-types `SharedInfo` (src/main.py); reading `minimap_frame` notes whether the lock was held."""

    def __init__(self) -> None:
        self.minimap_frame_lock = TrackingLock()
        self._frame = None
        self.held_at_read: list[bool] = []

    @property
    def minimap_frame(self):
        self.held_at_read.append(self.minimap_frame_lock.held())
        return self._frame

    @minimap_frame.setter
    def minimap_frame(self, value) -> None:
        self._frame = value


def test_22_shared_frames_returns_the_shared_reference_under_the_lock():
    shared = FakeShared()
    read = shared_frames(shared)
    assert read() is None
    frame = _rgb(1)
    shared.minimap_frame = frame
    assert read() is frame and read() is frame  # the reference itself, never a copy
    other = frame.copy()  # equal content, published as a new object
    shared.minimap_frame = other
    assert read() is other and read() is not frame
    assert shared.held_at_read == [True] * 5  # every read of the attribute was under the lock
    assert shared.minimap_frame_lock.acquires == 5  # and one acquire per call
    assert not shared.minimap_frame_lock.held()


def test_23_record_over_shared_frames_saves_a_frame_once_per_publication(tmp_path):
    shared = FakeShared()
    fake = FakeTime()
    published = [_rgb(1), _rgb(2), _rgb(3)]
    sleeps = 0

    def sleep(seconds):  # the "capture thread" publishes a new array every second poll
        nonlocal sleeps
        fake.sleep(seconds)
        sleeps += 1
        if sleeps % 2 == 0 and sleeps // 2 < len(published):
            shared.minimap_frame = published[sleeps // 2]

    shared.minimap_frame = published[0]
    out = tmp_path / "rec"
    assert record(shared_frames(shared), out, seconds=0.5, fps=10, clock=fake.clock, sleep=sleep) == Success(3)
    for i, frame in enumerate(published):
        assert np.array_equal(_read_rgb(out / f"frame_{i:06d}.png"), frame)
