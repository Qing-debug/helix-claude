"""WALKFIX Task 6 (QB283 2d), the capture side: every picture is stamped with `time.perf_counter_ns()` read right after
Windows handed the desktop image over (`frame_wait.grab_new_frame_stamped`), and the capture loop publishes the stamp
with the picture (`LostTracker.publish`). The fake camera and the loop's synthetic screen are the ones in
`test_frame_wait.py` and `test_minimap_follow.py`. Windows only, as `frame_wait` loads bettercam at module level."""
import itertools
import sys
import threading

import numpy as np
import pytest

if sys.platform != "win32":
    pytest.skip("bettercam and comtypes are Windows only", allow_module_level=True)

import src.vision.frame_wait as frame_wait  # noqa: E402
import src.vision.minimap_follow as follow  # noqa: E402
from test_frame_wait import FakeCamera  # noqa: E402
from test_minimap_follow import FakeScreen, Stop, build_screen, new_shared, region_of  # noqa: E402


@pytest.fixture
def texture(monkeypatch):
    monkeypatch.setattr(frame_wait, "_query_texture", lambda resource: "duplicated texture")


class Clock:
    """A `_clock` that reads 1 until the camera's acquire has returned, and 2 after it."""

    def __init__(self):
        self.now = 1
        self.reads = []

    def __call__(self):
        self.reads.append(self.now)
        return self.now


def clocked_camera(monkeypatch, **kwargs):
    clock = Clock()
    monkeypatch.setattr(frame_wait, "_clock", clock)
    camera = FakeCamera(**kwargs)
    duplication = camera._duplicator.duplicator
    acquire = duplication.AcquireNextFrame

    def acquire_then_tick(*args):
        acquire(*args)  # raises for an error camera, so a failed acquire never moves the clock
        clock.now = 2

    duplication.AcquireNextFrame = acquire_then_tick
    return camera, clock


def test_stamped_grab_returns_the_time_after_acquire(monkeypatch, texture):
    camera, clock = clocked_camera(monkeypatch)
    frame, t_ns = frame_wait.grab_new_frame_stamped(camera)
    assert frame is camera.frame
    assert t_ns == 2  # the clock read 1 before the acquire and 2 right after it returned
    assert clock.reads == [2]  # read once, after the acquire and not before it


def test_pointer_only_update_has_no_stamp(monkeypatch, texture):
    camera, _clock = clocked_camera(monkeypatch, present_time=0)  # LastPresentTime 0: only the pointer changed
    assert frame_wait.grab_new_frame_stamped(camera) is None
    assert camera.calls == [("acquire", 100), ("release",)]


def test_plain_grab_is_unchanged(monkeypatch, texture):
    camera, _clock = clocked_camera(monkeypatch)
    region = (10, 20, 230, 180)
    assert frame_wait.grab_new_frame(camera, region) is camera.frame  # the array alone, not a pair
    assert camera.calls == [
        ("acquire", 100),
        ("copy", "stage texture", "duplicated texture"),
        ("release",),
        ("map",),
        ("process", "mapped rect", 1920, 1080, region, 0),
        ("unmap",),
    ]
    assert frame_wait.grab_new_frame(FakeCamera(present_time=0)) is None


def test_publish_writes_frame_and_stamp_together():
    shared = new_shared()
    shared.minimap_frame_ns = None
    seen = []

    class Snapshot:
        """The lock: what `shared` holds when the writer takes it and when it lets go. A real lock underneath, because
        `publish` notifies a condition on it (QB283 2a); `acquire` and `release`, the condition's own use, are not noted."""

        def __init__(self):
            self.lock = threading.Lock()

        def acquire(self, *args):
            return self.lock.acquire(*args)

        def release(self):
            self.lock.release()

        def locked(self):
            return self.lock.locked()

        def __enter__(self):
            self.lock.acquire()
            seen.append((shared.minimap_frame, shared.minimap_frame_ns))

        def __exit__(self, *exc):
            seen.append((shared.minimap_frame, shared.minimap_frame_ns))
            self.lock.release()

    shared.minimap_frame_lock = Snapshot()
    shared.minimap_frame_new = threading.Condition(shared.minimap_frame_lock)
    frame = np.zeros((2, 2, 3), np.uint8)
    follow.LostTracker(shared, lambda _line: None).publish(frame, 123)
    assert (shared.minimap_frame, shared.minimap_frame_ns) == (frame, 123)
    assert seen == [(None, None), (frame, 123)]  # both written inside the one `with`, none before or after


def test_capture_loop_publishes_the_grab_stamp(monkeypatch):
    vision_mod = pytest.importorskip("src.vision.vision")
    gray, spots = build_screen()
    x1, y1, x2, y2 = region_of(spots)
    shared = new_shared()
    shared.minimap_frame_ns = None
    rows = []  # the (frame, stamp) pair `shared` holds before every grab
    stamps = itertools.count(1)

    def script(screen):
        with shared.minimap_frame_lock:
            rows.append((shared.minimap_frame, shared.minimap_frame_ns))
        screen.gray[y1:y2, x1:x2] = 30 + screen.n  # this grab's picture shows its own grab number

    screen = FakeScreen(gray.copy(), script, max_grabs=12, noise=0)
    monkeypatch.setattr(vision_mod.bettercam, "create", lambda **_kw: object())
    monkeypatch.setattr(vision_mod.vision, "cameraGrab", screen.grab)  # the whole-screen search: no stamp
    monkeypatch.setattr(vision_mod, "_grab_stamped", lambda camera, region: (screen.grab(camera, region), next(stamps)))
    monkeypatch.setattr(vision_mod.time, "sleep", lambda _s: None)
    with pytest.raises(Stop):
        vision_mod.capture_and_process_minimap(shared)

    pairs = []
    for frame, stamp in rows:
        if frame is not None and (not pairs or frame is not pairs[-1][0]):
            pairs.append((frame, stamp))
    assert [stamp for _, stamp in pairs] == list(range(1, len(pairs) + 1)) and len(pairs) >= 8
    for frame, stamp in pairs:  # grab 1 was the search, so the grab stamped k showed number k + 1
        assert int(frame[frame.shape[0] // 2, frame.shape[1] // 2, 0]) == 31 + stamp
