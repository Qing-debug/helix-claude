"""The rope finder (C12, H92): the ropes on the minimap, from the per-pixel median of a few seconds of frames. Pure:
numpy only, no Tk, no `src.route` import. The rule is OUTLINE147's, made relative to the palette."""
from collections.abc import Iterable, Sequence

import numpy as np
from returns.result import Failure, Result, Success

from src.controller.rope_file import NODE_ABOVE_GROUND_PX, RopeMark

ROPE_BLUE_DROP = 10          # a rope pixel is at least this much bluer-dark than both horizontal neighbours ...
BACKGROUND_GREY_MAX = 120    # ... and both neighbours are background (grey below this)
GROUND_GREY_MIN = 180        # a platform's row: both neighbours at least this grey (tools/derive_real_ground.py:54)
MIN_ROPE_ROWS = 6            # a rope is a run of at least this many rows in one column
SAME_ROPE_PX = 2             # columns this close, or closer, are one rope when their row ranges overlap
_SIZE_CHANGED = "the minimap changed size while ropes were being found; try again"
_NO_FRAMES = "no minimap frames were collected, so ropes cannot be found; try again"

_Candidate = tuple[int, int, int, float]   # (x, top_y, bottom_y, mean blue over the run)


def median_frame(frames: Sequence[np.ndarray]) -> Result[np.ndarray, str]:
    """The per-pixel median (uint8, RGB) of frames of one shape."""
    if not frames:
        return Failure(_NO_FRAMES)
    if len({frame.shape for frame in frames}) != 1:
        return Failure(_SIZE_CHANGED)
    return Success(np.rint(np.median(np.stack(frames), axis=0)).astype(np.uint8))


def find_ropes(frame: np.ndarray) -> list[RopeMark]:
    """C12's rule on one RGB frame, sorted by x. A column pixel is rope when its blue is at least ROPE_BLUE_DROP below
    the blue of both horizontal neighbours and both neighbours are background. A rope is a run of at least
    MIN_ROPE_ROWS such rows in one column whose run meets a platform above and below (a row whose neighbours are both
    at least GROUND_GREY_MIN grey). The top platform row is the top of its bright band, the bottom one the band's top
    where the walk down first meets it; top_y and bottom_y are those rows minus NODE_ABOVE_GROUND_PX (top_y at least 0).
    Columns within SAME_ROPE_PX are one rope when their rows overlap; ropes stacked in a column stay separate."""
    blue = frame[:, :, 2].astype(np.int32)
    height, width = blue.shape
    if width < 3:
        return []
    grey = _grey(frame)
    left, centre, right = blue[:, :-2], blue[:, 1:-1], blue[:, 2:]
    grey_left, grey_right = grey[:, :-2], grey[:, 2:]
    dark = ((centre + ROPE_BLUE_DROP <= left) & (centre + ROPE_BLUE_DROP <= right)
            & (grey_left < BACKGROUND_GREY_MAX) & (grey_right < BACKGROUND_GREY_MAX))
    bright = (grey_left >= GROUND_GREY_MIN) & (grey_right >= GROUND_GREY_MIN)

    candidates: list[_Candidate] = []
    for column in range(dark.shape[1]):
        x = column + 1
        for start, end in _runs(dark[:, column]):
            if end - start + 1 < MIN_ROPE_ROWS:
                continue
            top = _platform_row(bright[:, column], range(start - 1, -1, -1))
            bottom = _platform_row(bright[:, column], range(end + 1, height))
            if top is None or bottom is None:
                continue
            top = _band_top(bright[:, column], top)
            mean_blue = float(blue[start:end + 1, x].mean())
            top_y = max(0, top - NODE_ABOVE_GROUND_PX)   # a platform in rows 0-2: read_ropes refuses a negative top_y
            candidates.append((x, top_y, bottom - NODE_ABOVE_GROUND_PX, mean_blue))

    groups: list[list[_Candidate]] = []
    for candidate in sorted(candidates):
        for group in groups:
            if any(_same_rope(candidate, member) for member in group):
                group.append(candidate)
                break
        else:
            groups.append([candidate])
    ropes: list[RopeMark] = []
    for group in groups:
        x, top_y, bottom_y, _ = min(group, key=lambda c: (c[3], c[0]))   # the darkest column is the rope's x
        ropes.append(RopeMark(x, top_y, bottom_y))
    return ropes


def _same_rope(a: _Candidate, b: _Candidate) -> bool:
    """Columns within SAME_ROPE_PX whose rows overlap; ropes stacked in one column only touch at the platform between."""
    return abs(a[0] - b[0]) <= SAME_ROPE_PX and a[1] < b[2] and b[1] < a[2]


def _grey(frame: np.ndarray) -> np.ndarray:
    """Luma of an RGB frame, weighted as OpenCV's grayscale is (tools/derive_real_ground.py reads grey that way)."""
    rgb = frame.astype(np.float64)
    return 0.299 * rgb[:, :, 0] + 0.587 * rgb[:, :, 1] + 0.114 * rgb[:, :, 2]


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """The first and last row of each run of True in one column."""
    runs: list[tuple[int, int]] = []
    start: int | None = None
    for row, on in enumerate(mask):
        if on and start is None:
            start = row
        elif not on and start is not None:
            runs.append((start, row - 1))
            start = None
    if start is not None:
        runs.append((start, len(mask) - 1))
    return runs


def _platform_row(bright_column: np.ndarray, rows: Iterable[int]) -> int | None:
    """The first row, in the order given, whose neighbours are both bright."""
    for row in rows:
        if bright_column[row]:
            return row
    return None


def _band_top(bright_column: np.ndarray, row: int) -> int:
    """The top row of the bright band that `row` belongs to: a platform is 2 to 3 bright rows, and the ground row is the
    band's top (ROPE151-T2-D1, ruling B). Walking up from the rope meets the band's lower edge first."""
    while row > 0 and bright_column[row - 1]:
        row -= 1
    return row
