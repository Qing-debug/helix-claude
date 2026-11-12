"""VIS162-FIX: the capture notices at once that the minimap is no longer at the grabbed region, searches again, and says
when it is lost. Synthetic screens built from the real corner templates (assets/brds): a random dark background with the
four templates pasted where a minimap's border corners would be. No camera, no window; the loop tests patch the grab."""

import threading
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

import src.vision.minimap_follow as follow
import src.vision.vision_helper_lib as helper

REPO = Path(__file__).resolve().parents[2]
CORNERS = ["left_top", "right_top", "right_bot", "left_bot"]
TEMPLATES = {c: cv2.imread(str(REPO / "assets" / "brds" / f"{c}_grayscale.png"), cv2.IMREAD_GRAYSCALE) for c in CORNERS}
MASKS = {c: cv2.imread(str(REPO / "assets" / "brds" / f"{c}_mask.png"), cv2.IMREAD_GRAYSCALE) for c in CORNERS}
SCREEN_H, SCREEN_W = 300, 480
DEFAULT = dict(left=100, top=60, right=300, bottom=200)  # the border lines' first column / row


class Stop(BaseException):
    """Ends the capture loop, which has no end of its own."""


def build_screen(left=100, top=60, right=300, bottom=200, seed=1, minimap=True):
    """(gray picture, {corner: top-left of its template}). The templates sit where the border's 2 px lines pass: the
    left lines at column `left`, the right ones at `right`, the top at row `top`, the bottom at row `bottom`."""
    rng = np.random.default_rng(seed)
    gray = rng.integers(10, 120, (SCREEN_H, SCREEN_W), dtype=np.uint8)
    spots = {"left_top": (left - 3, top - 3), "right_top": (right - 11, top - 3),
             "right_bot": (right - 11, bottom - 11), "left_bot": (left - 3, bottom - 8)}
    if minimap:
        for c, (x, y) in spots.items():
            h, w = TEMPLATES[c].shape
            jitter = rng.integers(-3, 4, (h, w))  # an exact copy would score 1.0000001 and be scrubbed as > 1
            gray[y:y + h, x:x + w] = np.clip(TEMPLATES[c].astype(np.int16) + jitter, 0, 255)
    return gray, spots


def region_of(spots):
    """The minimap's region as the loop finds it: from the left-top template's centre to the right-bottom one's."""
    (lx, ly), (rx, ry) = spots["left_top"], spots["right_bot"]
    lh, lw = TEMPLATES["left_top"].shape
    rh, rw = TEMPLATES["right_bot"].shape
    return lx + lw // 2, ly + lh // 2, rx + rw // 2, ry + rh // 2


def lock_for(gray, spots):
    matched = {c: SimpleNamespace(x=x, y=y, conf_score=1.0) for c, (x, y) in spots.items()}
    return follow.make_lock(gray, matched, TEMPLATES, MASKS, CORNERS, region_of(spots))


def box_of(lock, gray):
    x1, y1, x2, y2 = lock.box
    return gray[y1:y2, x1:x2]


@pytest.fixture
def lock_and_gray():
    gray, spots = build_screen()
    return lock_for(gray, spots), gray


# ---- the per-grab check -------------------------------------------------------------------------------------------

def test_lock_box_holds_all_four_corners_and_the_old_region(lock_and_gray):
    lock, gray = lock_and_gray
    x1, y1, x2, y2 = lock.box
    assert (x1, y1, x2, y2) == (97, 57, 307, 207)
    cx1, cy1, cx2, cy2 = lock.crop
    _, spots = build_screen()
    assert (x1 + cx1, y1 + cy1, x1 + cx2, y1 + cy2) == region_of(spots)
    assert follow.still_there(lock, box_of(lock, gray))


def test_check_ignores_the_minimap_inside_and_capture_noise(lock_and_gray):
    lock, gray = lock_and_gray
    rng = np.random.default_rng(5)
    noisy = np.clip(box_of(lock, gray).astype(np.int16) + rng.integers(-3, 4, box_of(lock, gray).shape), 0, 255).astype(np.uint8)
    assert follow.still_there(lock, noisy)
    x1, y1, x2, y2 = lock.crop  # the minimap's inside changes every frame (the map, the character)
    inside = box_of(lock, gray).copy()
    inside[y1:y2, x1:x2] = rng.integers(0, 255, (y2 - y1, x2 - x1), dtype=np.uint8)
    assert follow.still_there(lock, inside)


@pytest.mark.parametrize("dx, dy", [(1, 0), (0, 1), (-1, -1), (2, 2), (30, 0), (0, 25), (-60, 40)])
def test_check_fails_when_the_minimap_moved(lock_and_gray, dx, dy):
    lock, _ = lock_and_gray
    moved, _ = build_screen(left=100 + dx, top=60 + dy, right=300 + dx, bottom=200 + dy)
    assert not follow.still_there(lock, box_of(lock, moved))


def test_check_fails_when_the_minimap_was_resized(lock_and_gray):
    lock, _ = lock_and_gray
    for kwargs in (dict(right=320), dict(bottom=230), dict(left=120), dict(top=80)):
        resized, _ = build_screen(**{**DEFAULT, **kwargs})
        assert not follow.still_there(lock, box_of(lock, resized)), kwargs


def test_check_fails_when_the_minimap_is_gone(lock_and_gray):
    lock, _ = lock_and_gray
    gone, _ = build_screen(minimap=False)
    assert not follow.still_there(lock, box_of(lock, gone))


@pytest.mark.parametrize("value", [0, 24, 228, 240, 255])
def test_check_fails_under_a_flat_window(lock_and_gray, value):
    """Dark, border-bright and light windows over the whole minimap (228 is the border's own gray: the hardest)."""
    lock, gray = lock_and_gray
    covered = box_of(lock, gray).copy()
    covered[:] = value
    assert not follow.still_there(lock, covered)


@pytest.mark.parametrize("corner", CORNERS)
def test_check_fails_when_one_corner_is_covered(lock_and_gray, corner):
    lock, gray = lock_and_gray
    left, top, _, _ = lock.box
    x, y = build_screen()[1][corner]
    h, w = TEMPLATES[corner].shape
    covered = box_of(lock, gray).copy()
    covered[y - top:y - top + h, x - left:x - left + w] = 24  # a small dark window over that corner only
    assert not follow.still_there(lock, covered)


def test_check_fails_for_a_picture_of_another_size(lock_and_gray):
    lock, gray = lock_and_gray
    assert not follow.still_there(lock, box_of(lock, gray)[:-1])


def test_a_reject_prints_only_when_verbose(capsys):
    matched = {c: SimpleNamespace(x=0, y=0, conf_score=1.0) for c in CORNERS}  # one spot: left x >= right x
    assert not helper.isMatchedTemplateLocValid(matched, CORNERS, 0.95, verbose=False)
    assert capsys.readouterr().out == ""
    assert not helper.isMatchedTemplateLocValid(matched, CORNERS, 0.95)
    assert "invalid" in capsys.readouterr().out


# ---- the lost flag and its lines ------------------------------------------------------------------------------------

def new_shared():
    return SimpleNamespace(minimap_frame_lock=threading.Lock(), minimap_frame=None, minimap_lost=False)


def test_tracker_says_lost_once_and_found_once():
    shared, said = new_shared(), []
    tracker = follow.LostTracker(shared, said.append)
    first = np.zeros((2, 2, 3), np.uint8)
    tracker.publish(first)
    for _ in range(50):  # fifty failed searches
        tracker.search_failed()
    assert said == [follow.LOST_LINE] and shared.minimap_lost and shared.minimap_frame is first
    tracker.publish(np.ones((2, 2, 3), np.uint8))
    tracker.publish(np.ones((2, 2, 3), np.uint8))
    assert said == [follow.LOST_LINE, follow.FOUND_AGAIN_LINE] and not shared.minimap_lost
    tracker.search_failed()
    assert said[-1] == follow.LOST_LINE and said.count(follow.LOST_LINE) == 2


def test_tracker_before_the_first_picture_leaves_the_flag_alone():
    shared, said = new_shared(), []
    tracker = follow.LostTracker(shared, said.append)
    for _ in range(5):
        tracker.search_failed()
    assert said == [follow.NOT_FOUND_LINE] and not shared.minimap_lost
    tracker.publish(np.zeros((2, 2, 3), np.uint8))
    assert said == [follow.NOT_FOUND_LINE, follow.FOUND_LINE]


def test_tracker_writes_the_frame_and_the_flag_under_the_lock():
    shared = new_shared()
    seen = []

    class Spy:
        def __enter__(self):
            seen.append("in")

        def __exit__(self, *exc):
            seen.append("out")

    shared.minimap_frame_lock = Spy()
    tracker = follow.LostTracker(shared, lambda _line: None)
    tracker.publish(np.zeros((2, 2, 3), np.uint8))
    tracker.search_failed()
    assert seen == ["in", "out", "in", "out"]


# ---- the capture loop itself (fake grab, no camera) ---------------------------------------------------------------

class FakeScreen:
    """Stands in for `cameraGrab`: crops `gray` to the region asked for, with a little capture noise; `script(screen)`
    runs before every grab and may change `gray` (the minimap moves, a window covers it); `Stop` ends the loop."""

    def __init__(self, gray, script=None, max_grabs=80, noise=2):
        self.gray, self.script, self.max_grabs, self.noise = gray, script, max_grabs, noise
        self.n = 0
        self.whole_screen_grabs = []
        self.rng = np.random.default_rng(7)

    def grab(self, _camera, region=None):
        self.n += 1
        if self.n > self.max_grabs:
            raise Stop
        if self.script is not None:
            self.script(self)
        if region is None:
            self.whole_screen_grabs.append(self.n)
            picture = self.gray
        else:
            picture = self.gray[region[1]:region[3], region[0]:region[2]]
        if self.noise:
            picture = np.clip(picture.astype(np.int16) + self.rng.integers(-self.noise, self.noise + 1, picture.shape), 0, 255)
        return cv2.cvtColor(picture.astype(np.uint8), cv2.COLOR_GRAY2BGR)


@pytest.fixture
def run_loop(monkeypatch, capsys):
    vision_mod = pytest.importorskip("src.vision.vision")  # needs Windows and bettercam

    def run(screen, shared):
        sleeps = []
        monkeypatch.setattr(vision_mod.bettercam, "create", lambda **_kw: object())
        monkeypatch.setattr(vision_mod.vision, "cameraGrab", screen.grab)
        monkeypatch.setattr(vision_mod.time, "sleep", sleeps.append)
        with pytest.raises(Stop):
            vision_mod.capture_and_process_minimap(shared)
        lines = [line.strip() for line in capsys.readouterr().out.splitlines()]
        return sleeps, lines

    return run


def crop_rgb(gray, spots):
    x1, y1, x2, y2 = region_of(spots)
    return cv2.cvtColor(gray[y1:y2, x1:x2], cv2.COLOR_GRAY2RGB)


def close_to(frame, expected):
    return frame.shape == expected.shape and np.abs(frame.astype(int) - expected.astype(int)).mean() < 4


class Watch:
    """Records, before every grab, which picture `shared` holds and whether it says lost."""

    def __init__(self, shared):
        self.shared = shared
        self.rows = []  # (grab number, frame object, lost)

    def __call__(self, screen):
        with self.shared.minimap_frame_lock:
            self.rows.append((screen.n, self.shared.minimap_frame, self.shared.minimap_lost))

    def distinct_frames(self):
        out = []
        for _, frame, _ in self.rows:
            if frame is not None and (not out or frame is not out[-1]):
                out.append(frame)
        return out


def test_loop_publishes_a_changing_minimap_without_searching_again(run_loop):
    gray, spots = build_screen()
    shared = new_shared()
    watch = Watch(shared)
    x1, y1, x2, y2 = region_of(spots)

    def script(screen):
        screen.gray[y1:y2, x1:x2] = screen.rng.integers(0, 255, (y2 - y1, x2 - x1), dtype=np.uint8)  # the inside changes
        watch(screen)

    screen = FakeScreen(gray.copy(), script, max_grabs=40)
    _, lines = run_loop(screen, shared)
    assert screen.whole_screen_grabs == [1]
    assert not shared.minimap_lost and len(watch.distinct_frames()) >= 38
    assert not [line for line in lines if line.startswith("Minimap")]


def test_loop_follows_a_moved_minimap_and_never_publishes_the_wrong_spot(run_loop):
    a, spots_a = build_screen()
    b, spots_b = build_screen(left=160, top=90, right=360, bottom=230)
    shared = new_shared()
    watch = Watch(shared)

    def script(screen):
        if screen.n == 12:
            screen.gray = b  # the minimap moves between two grabs
        watch(screen)

    screen = FakeScreen(a, script, max_grabs=30)
    _, lines = run_loop(screen, shared)
    expected_a, expected_b = crop_rgb(a, spots_a), crop_rgb(b, spots_b)
    frames = watch.distinct_frames()
    assert all(close_to(f, expected_a) or close_to(f, expected_b) for f in frames)  # no frame of the wrong spot
    first_b = next(n for n, frame, _ in watch.rows if frame is not None and close_to(frame, expected_b))
    assert first_b - 12 <= 3  # the new spot is followed within a search and a grab, not after 22 s
    assert close_to(shared.minimap_frame, expected_b) and not shared.minimap_lost
    assert screen.whole_screen_grabs == [1, 13]  # one search at the start, one straight after the move
    assert not [line for line in lines if "lost" in line or "not found" in line]


def vanish_background(screen):
    screen.gray = build_screen(minimap=False)[0]


def vanish_dark_window(screen):
    screen.gray = build_screen()[0]
    screen.gray[40:230, 80:330] = 24


def vanish_light_window(screen):
    screen.gray = build_screen()[0]
    screen.gray[40:230, 80:330] = 240


@pytest.mark.parametrize("vanish", [vanish_background, vanish_dark_window, vanish_light_window])
def test_loop_goes_lost_keeps_the_last_frame_and_comes_back(run_loop, vanish):
    a, _ = build_screen()
    shared = new_shared()
    watch = Watch(shared)

    def script(screen):
        if screen.n == 10:
            vanish(screen)
        if screen.n == 40:
            screen.gray = a
        watch(screen)

    screen = FakeScreen(a, script, max_grabs=60)
    _, lines = run_loop(screen, shared)
    last_before = next(frame for n, frame, _ in watch.rows if n == 10)
    lost_rows = [(n, frame, lost) for n, frame, lost in watch.rows if 13 <= n < 40]
    assert lost_rows and all(lost and frame is last_before for _, frame, lost in lost_rows)  # frozen frame, flag up
    assert not shared.minimap_lost and shared.minimap_frame is not last_before  # back, with a new picture
    said = [line for line in lines if line.startswith("Minimap")]
    assert said == [follow.LOST_LINE, follow.FOUND_AGAIN_LINE]  # one each, not one per failed search
    assert not [line for line in lines if "invalid" in line or "failed" in line]


def test_loop_before_the_first_minimap_says_so_once(run_loop):
    a, _ = build_screen()
    shared = new_shared()
    watch = Watch(shared)

    def script(screen):
        if screen.n == 15:
            screen.gray = a
        watch(screen)

    screen = FakeScreen(build_screen(minimap=False)[0], script, max_grabs=30)
    _, lines = run_loop(screen, shared)
    assert [line for line in lines if line.startswith("Minimap")] == [follow.NOT_FOUND_LINE, follow.FOUND_LINE]
    assert not any(lost for _, _, lost in watch.rows) and shared.minimap_frame is not None


def test_loop_keeps_the_black_frame_early_exit(run_loop):
    """The corners are there but the minimap's inside is pure black: nothing is published and the loop waits the
    retry delay before it searches again, as before."""
    gray, spots = build_screen()
    x1, y1, x2, y2 = region_of(spots)
    gray[y1:y2, x1:x2] = 0
    shared = new_shared()
    screen = FakeScreen(gray, max_grabs=12, noise=0)
    vision_mod = pytest.importorskip("src.vision.vision")
    sleeps, _ = run_loop(screen, shared)
    assert shared.minimap_frame is None
    assert vision_mod.MINIMAP_DETECTION_RETRY_DELAY_S in sleeps
