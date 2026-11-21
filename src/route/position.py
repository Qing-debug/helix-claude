"""Minimap position source for route walking (plan task B16).

`MinimapPositionSource` is the walker's `PositionSource` over the minimap frame the capture thread shares
(`SharedInfo` in src/main.py: `minimap_frame`, an RGB array or None, guarded by `minimap_frame_lock`). Under the
lock, `read` takes the frame: None is `Failure("no frame")`; the same object as the frame it last copied is
`Failure(STALE)`, no new frame yet; anything else is copied. The locator runs on the copy after the lock is released,
so the capture thread never waits on template matching. The point it reports is the locator's, moved `DOT_TO_FOOT_PX`
down (`foot_from_dot`); a failure is returned unchanged.

`wait_new` is the walker's idle wait (QB283 2a): it returns when a frame newer than the last one `read` copied is there,
or after `timeout_s`. The capture thread's `LostTracker.publish` notifies `minimap_frame_new`, a condition on the same
lock, after every new frame.

Under the same lock `read` also takes `minimap_frame_ns`, the stamp the capture thread published with that frame, and
after the locator succeeds it keeps it in `frame_ns` (QB283 2d; see the attribute's comment).

The shared object is duck-typed, so this module imports neither src.main nor src.vision.

Where the plan is silent:
- A None read does not forget the last frame copied, so that frame would still be stale if it came back. The capture
  thread (src/vision/vision.py) publishes a new array for every frame and never sets None.
"""

from returns.pipeline import is_successful
from returns.result import Failure, Result

from src.route.character import CharacterLocator
from src.route.walker import STALE

# The dot's centre is 6 px above the ground, and the map editor's node circle (radius 3) rests on the ground, so a
# node sits 3 px below the dot's centre. Every position source reports that point, the reference the map's nodes use
# (Hong's QB213, off-graph fix plan OFFP141).
DOT_TO_FOOT_PX: int = 3


def foot_from_dot(dot: tuple[int, int]) -> tuple[int, int]:
    """The point `DOT_TO_FOOT_PX` below the dot's centre `dot` (minimap pixels, y grows downward)."""
    x, y = dot
    return x, y + DOT_TO_FOOT_PX


class MinimapPositionSource:  # implements walker.PositionSource
    def __init__(self, shared, locator: CharacterLocator) -> None:
        """`shared` has `minimap_frame_lock` (a lock), `minimap_frame` (an RGB uint8 H x W x 3 array, or None) and
        `minimap_frame_ns` (the stamp of that frame, an int, or None before the first); `wait_new` also needs
        `minimap_frame_new`, a `threading.Condition` on `minimap_frame_lock`."""
        self._shared = shared
        self._locator = locator
        # A reference, not an id(): once a frame is freed, a new frame can be given its id.
        self._last_frame = None
        # The stamp of the picture of the last successful `read` (QB283 2d): `time.perf_counter_ns()` when Windows handed
        # that picture over, the clock of `RealClock.now_ns` and of the keys' stimulus times. None until a read succeeds,
        # and a read that fails (no frame, stale, or the locator's failure) leaves it as it was. The walker's thread sets
        # it in `read` and the step log reads it on that same thread, so it needs no lock.
        self.frame_ns: int | None = None

    def read(self) -> Result[tuple[int, int], str]:
        with self._shared.minimap_frame_lock:
            frame = self._shared.minimap_frame
            if frame is None:
                return Failure("no frame")
            if frame is self._last_frame:
                return Failure(STALE)
            copy = frame.copy()
            stamp = self._shared.minimap_frame_ns  # in the same `with`, so it is the stamp of this frame
            self._last_frame = frame
        located = self._locator.locate(copy).map(foot_from_dot)
        if is_successful(located):
            self.frame_ns = stamp
        return located

    def wait_new(self, timeout_s: float) -> None:
        """Return when `minimap_frame` is not the frame the last `read` copied, or after `timeout_s`; at once when a
        newer frame is already there. Called on the walker's thread, the one that calls `read`. It takes no copy and
        runs no locator: the next `read` does. The timeout is not reported: the next `read` says STALE if none came."""
        cond = self._shared.minimap_frame_new
        with cond:
            cond.wait_for(lambda: self._shared.minimap_frame is not self._last_frame, timeout_s)
