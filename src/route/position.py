"""Minimap position source for route walking (plan task B16).

`MinimapPositionSource` is the walker's `PositionSource` over the minimap frame the capture thread shares
(`SharedInfo` in src/main.py: `minimap_frame`, an RGB array or None, guarded by `minimap_frame_lock`). Under the
lock, `read` takes the frame: None is `Failure("no frame")`; the same object as the frame it last copied is
`Failure(STALE)`, no new frame yet; anything else is copied. The locator runs on the copy after the lock is released,
so the capture thread never waits on template matching, and its result is returned unchanged.

The shared object is duck-typed, so this module imports neither src.main nor src.vision.

Where the plan is silent:
- A None read does not forget the last frame copied, so that frame would still be stale if it came back. The capture
  thread (src/vision/vision.py) publishes a new array for every frame and never sets None.
"""

from returns.result import Failure, Result

from src.route.character import CharacterLocator
from src.route.walker import STALE


class MinimapPositionSource:  # implements walker.PositionSource
    def __init__(self, shared, locator: CharacterLocator) -> None:
        """`shared` has `minimap_frame_lock` (a lock) and `minimap_frame` (an RGB uint8 H x W x 3 array, or None)."""
        self._shared = shared
        self._locator = locator
        # A reference, not an id(): once a frame is freed, a new frame can be given its id.
        self._last_frame = None

    def read(self) -> Result[tuple[int, int], str]:
        with self._shared.minimap_frame_lock:
            frame = self._shared.minimap_frame
            if frame is None:
                return Failure("no frame")
            if frame is self._last_frame:
                return Failure(STALE)
            copy = frame.copy()
            self._last_frame = frame
        return self._locator.locate(copy)
