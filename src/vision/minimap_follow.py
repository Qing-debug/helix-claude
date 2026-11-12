"""Following the minimap (VIS162-FIX): the check, on every grab, that the minimap is still where it was found, and the
lost flag with its console lines. Only cv2 and numpy, no camera, so tests drive it with arrays (`vision.py` is the loop).

A lock is made from the whole-screen picture the search just matched on. For each of the four border corners it keeps the
gray value of every pixel the corner's mask selects (the border line itself, never the minimap's inside). The loop then
grabs `lock.box`, the minimap plus its four corner templates, and `still_there` asks whether those pixels are still what
they were. Comparing with the pixels seen at lock time, and not scoring the templates again, is deliberate: the corner
score of 0.95 passes about 66% of the positions on a screen with no minimap (VIS161-DBG), so a score cannot tell "still
there" from "something flat is there". The border is drawn by the game and does not change while the minimap stays.
"""

from typing import NamedTuple

import numpy as np

PIXEL_TOLERANCE = 8  # gray levels a masked corner pixel may differ from the pixel seen at lock time (capture noise)
MAX_CHANGED_FRACTION = 0.04  # share of a corner's masked pixels that may differ by more: one pixel of 37 to 49

NOT_FOUND_LINE = "Minimap not found yet: searching for it."
FOUND_LINE = "Minimap found."
LOST_LINE = "Minimap lost: searching for it."
FOUND_AGAIN_LINE = "Minimap found again."


class _Corner(NamedTuple):
    rows: np.ndarray  # row of each masked pixel in the box's gray picture
    cols: np.ndarray  # column of each
    base: np.ndarray  # its gray value at lock time, int16


class MinimapLock(NamedTuple):
    box: tuple[int, int, int, int]  # (x1, y1, x2, y2) on the screen: what the loop grabs
    crop: tuple[int, int, int, int]  # the minimap itself, inside the box: the region the loop grabbed before the check
    corners: tuple[_Corner, ...]


def make_lock(screen_gray, matched, templates, masks, corners, region) -> MinimapLock:
    """`matched[corner]` has the top-left `x`, `y` of the corner's template on `screen_gray` (the whole-screen gray
    picture the match ran on); `region` is the minimap's (x1, y1, x2, y2), from the corner centres."""
    boxes = {c: (matched[c].x, matched[c].y, templates[c].shape[1], templates[c].shape[0]) for c in corners}  # x, y, w, h
    left = min(x for x, _, _, _ in boxes.values())
    top = min(y for _, y, _, _ in boxes.values())
    right = max(x + w for x, _, w, _ in boxes.values())
    bottom = max(y + h for _, y, _, h in boxes.values())
    parts = []
    for c in corners:
        x, y, _, _ = boxes[c]
        ys, xs = np.nonzero(masks[c] > 0)
        parts.append(_Corner(ys + (y - top), xs + (x - left), screen_gray[ys + y, xs + x].astype(np.int16)))
    x1, y1, x2, y2 = region
    return MinimapLock((left, top, right, bottom), (x1 - left, y1 - top, x2 - left, y2 - top), tuple(parts))


def still_there(lock: MinimapLock, box_gray) -> bool:
    """True when every corner's masked pixels are (almost) what they were at lock time. `box_gray` is the gray picture
    of `lock.box`. Costs four small fancy-index reads, a few microseconds."""
    left, top, right, bottom = lock.box
    if box_gray.shape != (bottom - top, right - left):
        return False
    for corner in lock.corners:
        now = box_gray[corner.rows, corner.cols].astype(np.int16)
        if np.count_nonzero(np.abs(now - corner.base) > PIXEL_TOLERANCE) > MAX_CHANGED_FRACTION * now.size:
            return False
    return True


class LostTracker:
    """The capture thread's one writer of the lost flag and of the search lines. `shared` is the `SharedInfo` (anything
    with `minimap_frame_lock`, `minimap_frame` and `minimap_lost`). A failed search says so once per stretch, not once
    per failed search; the first picture published after it says the minimap is back."""

    def __init__(self, shared, say=print):
        self._shared = shared
        self._say = say
        self._searching = False  # a search failed and nothing has been published since
        self._published_before = False

    def search_failed(self) -> None:
        """The whole-screen search found no minimap. The picture in `shared` stays as it was (the walker reads it as no
        new frame); the lost flag is raised only if a picture was published before, so the window's "not found yet"
        before the first picture is left alone."""
        if self._searching:
            return
        self._searching = True
        if self._published_before:
            with self._shared.minimap_frame_lock:
                self._shared.minimap_lost = True
            self._say(LOST_LINE)
        else:
            self._say(NOT_FOUND_LINE)

    def publish(self, frame_rgb) -> None:
        """Store the new picture and clear the lost flag, together under the lock (the window reads both)."""
        with self._shared.minimap_frame_lock:
            self._shared.minimap_frame = frame_rgb
            self._shared.minimap_lost = False
        if self._searching:
            self._searching = False
            self._say(FOUND_AGAIN_LINE if self._published_before else FOUND_LINE)
        self._published_before = True
