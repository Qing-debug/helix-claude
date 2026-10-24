"""Character locator for route walking (plan task B15).

Finds the character icon on the shared minimap frame and reports its centre in minimap pixels (top-left
origin), or a Failure saying why it will not: "miss" (no good match) or "ambiguous" (a second, separate
match scores almost as well). A wrong position is worse than none, so the walker treats a Failure as
"position unknown".

Method: masked `cv2.TM_CCOEFF_NORMED` on greyscale. The shared frame is RGB (vision.py converts BGR to RGB
for display), so the frame is greyed with `COLOR_RGB2GRAY` and the template with `COLOR_BGR2GRAY` on the
BGR image that `cv2.imread` returns; both give the same grey for the same colour. `getCharacterCoords`
(controller.py) uses `TM_CCORR_NORMED`, which does not subtract the mean: a flat background of any
brightness scores about 0.88 against this icon, so the background and a real match are only about 0.1 apart.
`TM_CCOEFF_NORMED` subtracts the masked mean from both sides, so background scores up to about 0.6 (0.44 to
0.62 on the synthetic frames) and a match scores 1.0. The measured scores of both methods on the synthetic
frames of tests/route/test_character.py are in the B15 commit body.
"""

from pathlib import Path

import cv2
import numpy as np
from returns.result import Failure, Result, Success

# Masked correlation divides by the energy of the image patch; on flat patches that gives nan, inf, or (for
# TM_CCORR_NORMED, vision.py) values above 1. Those are not scores, so they become _SCRUBBED. A perfect match
# can come out a rounding error above 1 (float32), so the ceiling has a little slack: scrubbing at exactly 1
# would turn the best possible match into a miss.
_SCRUBBED = -1.0
_SCORE_CEILING = 1.0 + 1e-3


class CharacterLocator:
    def __init__(
        self,
        icon_gray: np.ndarray,
        mask: np.ndarray,
        min_score: float = 0.95,
        min_margin: float = 0.02,
        exclusion_px: int = 5,
    ) -> None:
        """`icon_gray` and `mask` are 2-D uint8 arrays of one shape; the match uses the pixels where mask > 0.

        `min_score`: the best match must reach it. `min_margin`, `exclusion_px`: no other match outside the
        (2 * exclusion_px + 1)-square around the best one may score within `min_margin` of it.
        Raises ValueError for arguments that cannot work (programmer error).
        """
        if not (isinstance(icon_gray, np.ndarray) and icon_gray.dtype == np.uint8
                and icon_gray.ndim == 2 and icon_gray.size > 0):
            raise ValueError("icon_gray must be a non-empty 2-D uint8 array")
        if not (isinstance(mask, np.ndarray) and mask.dtype == np.uint8 and mask.shape == icon_gray.shape):
            raise ValueError("mask must be a uint8 array of the same shape as icon_gray")
        if not mask.any():
            raise ValueError("mask selects no pixels")
        if not 0.0 < min_score <= 1.0:
            raise ValueError("min_score must be in (0, 1]")
        if not min_margin >= 0.0:
            raise ValueError("min_margin must be >= 0")
        if not (isinstance(exclusion_px, (int, np.integer)) and not isinstance(exclusion_px, bool)
                and exclusion_px >= 0):
            raise ValueError("exclusion_px must be an int >= 0")
        self._icon = np.ascontiguousarray(icon_gray).copy()
        self._mask = np.ascontiguousarray(mask).copy()
        self._min_score = float(min_score)
        self._min_margin = float(min_margin)
        self._exclusion_px = int(exclusion_px)

    @classmethod
    def from_files(cls, icon_path: Path, mask_path: Path) -> Result["CharacterLocator", str]:
        icon_bgr = cv2.imread(str(icon_path), cv2.IMREAD_COLOR)
        if icon_bgr is None:
            return Failure(f"cannot read the icon image {icon_path}")
        mask = cv2.imread(str(mask_path), cv2.IMREAD_GRAYSCALE)
        if mask is None:
            return Failure(f"cannot read the mask image {mask_path}")
        # Not IMREAD_GRAYSCALE: the PNG decoder's own colour-to-grey differs from cvtColor by up to 15 grey
        # levels on this icon, and locate() greys the frame with cvtColor.
        icon_gray = cv2.cvtColor(icon_bgr, cv2.COLOR_BGR2GRAY)
        try:
            return Success(cls(icon_gray, mask))
        except ValueError as error:
            return Failure(str(error))

    def locate(self, minimap_rgb: np.ndarray) -> Result[tuple[int, int], str]:
        """The centre of the icon on `minimap_rgb` (uint8, H x W x 3, RGB) as (x, y), or a Failure."""
        problem = self._frame_problem(minimap_rgb)
        if problem is not None:
            return Failure(problem)
        icon_h, icon_w = self._icon.shape
        gray = cv2.cvtColor(minimap_rgb, cv2.COLOR_RGB2GRAY)
        scores = cv2.matchTemplate(gray, self._icon, cv2.TM_CCOEFF_NORMED, mask=self._mask)
        scores[~np.isfinite(scores) | (scores > _SCORE_CEILING)] = _SCRUBBED
        _, best, _, (left, top) = cv2.minMaxLoc(scores)  # the best match's top-left corner
        if best < self._min_score:
            return Failure("miss")
        reach = self._exclusion_px
        scores[max(0, top - reach):top + reach + 1, max(0, left - reach):left + reach + 1] = _SCRUBBED
        if float(scores.max()) >= best - self._min_margin:
            return Failure("ambiguous")
        # the centre of the icon, as getCharacterCoords reports it
        return Success((left + icon_w // 2, top + icon_h // 2))

    def _frame_problem(self, frame: object) -> str | None:
        if not (isinstance(frame, np.ndarray) and frame.dtype == np.uint8 and frame.ndim == 3
                and frame.shape[2] == 3):
            described = (f"{frame.dtype} {frame.shape}" if isinstance(frame, np.ndarray) else type(frame).__name__)
            return f"bad frame: need a uint8 H x W x 3 array, got {described}"
        icon_h, icon_w = self._icon.shape
        if frame.shape[0] < icon_h or frame.shape[1] < icon_w:
            return f"bad frame: {frame.shape[1]} x {frame.shape[0]} is smaller than the {icon_w} x {icon_h} icon"
        return None
