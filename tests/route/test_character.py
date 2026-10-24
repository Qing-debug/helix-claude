"""Tests for src.route.character (plan task B15): the locator finds the character icon on a synthetic
minimap frame, or returns a Failure; it never returns a wrong position.

Frames are 162 x 82 RGB: a (30, 30, 30) background with seeded per-pixel noise in [-10, 10], three grey
(120, 120, 120) 1-px horizontal lines (the platforms), and the icon from assets/chr/ pasted where mask > 0.
Tests 1-6 are the plan's six numbered tests; 7-11 pin the scrub, the RGB reading, the exclusion square and the
argument checks.
"""

from math import hypot
from pathlib import Path

import cv2
import numpy as np
import pytest
from returns.pipeline import is_successful

from src.route.character import CharacterLocator

ASSETS = Path(__file__).resolve().parent.parent.parent / "assets" / "chr"
ICON_PATH = ASSETS / "chr.png"
MASK_PATH = ASSETS / "chr_mask.png"

FRAME_W, FRAME_H = 162, 82
LINE_ROWS = (28, 45, 60)


def _load_icon():
    icon_bgr = cv2.imread(str(ICON_PATH), cv2.IMREAD_COLOR)
    mask = cv2.imread(str(MASK_PATH), cv2.IMREAD_GRAYSCALE)
    assert icon_bgr is not None and mask is not None
    return cv2.cvtColor(icon_bgr, cv2.COLOR_BGR2RGB), mask > 0


ICON_RGB, ICON_ON = _load_icon()
ICON_H, ICON_W = ICON_ON.shape
CENTRE_DX, CENTRE_DY = ICON_W // 2, ICON_H // 2  # the (5, 5) offset from top-left to centre


def _background(seed: int) -> np.ndarray:
    noise = np.random.default_rng(seed).integers(-10, 11, size=(FRAME_H, FRAME_W, 1))
    frame = np.repeat(30 + noise, 3, axis=2).astype(np.uint8)
    frame[list(LINE_ROWS), :, :] = 120
    return frame


def _paste(frame: np.ndarray, x: int, y: int) -> np.ndarray:
    """Paste the icon where mask > 0 with its top-left at (x, y); whatever falls outside the frame is cut off."""
    x0, y0 = max(x, 0), max(y, 0)
    x1, y1 = min(x + ICON_W, FRAME_W), min(y + ICON_H, FRAME_H)
    on = ICON_ON[y0 - y:y1 - y, x0 - x:x1 - x]
    frame[y0:y1, x0:x1][on] = ICON_RGB[y0 - y:y1 - y, x0 - x:x1 - x][on]
    return frame


@pytest.fixture(scope="module")
def locator() -> CharacterLocator:
    result = CharacterLocator.from_files(ICON_PATH, MASK_PATH)
    assert is_successful(result)
    return result.unwrap()


def test_1_from_files_loads_the_asset_pngs_and_fails_on_a_missing_path():
    assert is_successful(CharacterLocator.from_files(ICON_PATH, MASK_PATH))
    missing = ASSETS / "no_such_file.png"
    for icon, mask in ((missing, MASK_PATH), (ICON_PATH, missing), (missing, missing)):
        result = CharacterLocator.from_files(icon, mask)
        assert not is_successful(result)
        assert isinstance(result.failure(), str)


def test_2_reports_the_pasted_top_left_plus_the_centre_offset(locator):
    rng = np.random.default_rng(2)
    max_x, max_y = FRAME_W - ICON_W, FRAME_H - ICON_H
    corners = [(0, 0), (max_x, 0), (0, max_y), (max_x, max_y)]  # the icon touching each frame corner
    randoms = [(int(rng.integers(0, max_x + 1)), int(rng.integers(0, max_y + 1))) for _ in range(46)]
    for seed, (x, y) in enumerate(corners + randoms):
        result = locator.locate(_paste(_background(seed), x, y))
        assert is_successful(result), f"seed {seed} at {(x, y)}: {result}"
        assert result.unwrap() == (x + CENTRE_DX, y + CENTRE_DY), f"seed {seed}"


def test_3_a_frame_without_the_icon_is_a_miss_never_a_position(locator):
    for seed in range(1000, 1020):
        result = locator.locate(_background(seed))
        assert not is_successful(result), f"seed {seed}: {result}"
        assert result.failure() == "miss", f"seed {seed}"


def test_4_two_icons_far_apart_are_ambiguous(locator):
    rng = np.random.default_rng(4)
    max_x, max_y = FRAME_W - ICON_W, FRAME_H - ICON_H
    for seed in range(2000, 2020):
        while True:
            a = (int(rng.integers(0, max_x + 1)), int(rng.integers(0, max_y + 1)))
            b = (int(rng.integers(0, max_x + 1)), int(rng.integers(0, max_y + 1)))
            if max(abs(a[0] - b[0]), abs(a[1] - b[1])) >= 20:
                break
        frame = _paste(_paste(_background(seed), *a), *b)
        result = locator.locate(frame)
        assert not is_successful(result), f"seed {seed}: icons at {a} and {b} gave {result}"
        assert result.failure() == "ambiguous", f"seed {seed}"


def test_5_an_icon_half_outside_the_frame_is_a_miss_or_within_3_px(locator):
    rng = np.random.default_rng(5)
    max_x, max_y = FRAME_W - ICON_W, FRAME_H - ICON_H
    half = ICON_W // 2
    cases = []
    for _ in range(6):  # each side, then each corner, with the icon exactly half outside
        cases += [
            (-half, int(rng.integers(0, max_y + 1))),
            (FRAME_W - half, int(rng.integers(0, max_y + 1))),
            (int(rng.integers(0, max_x + 1)), -half),
            (int(rng.integers(0, max_x + 1)), FRAME_H - half),
        ]
    cases += [(-half, -half), (FRAME_W - half, -half), (-half, FRAME_H - half), (FRAME_W - half, FRAME_H - half)]
    for seed, (x, y) in enumerate(cases, start=3000):
        result = locator.locate(_paste(_background(seed), x, y))
        if is_successful(result):
            cx, cy = result.unwrap()
            error = hypot(cx - (x + CENTRE_DX), cy - (y + CENTRE_DY))
            assert error <= 3.0, f"seed {seed} at {(x, y)}: reported {(cx, cy)}, {error:.1f} px from the centre"
        else:
            assert isinstance(result.failure(), str)


def test_6_a_float_frame_or_a_tiny_frame_is_a_failure(locator):
    frame = _paste(_background(0), 40, 20)
    bad_frames = {
        "float64": frame.astype(np.float64),
        "float32 in 0..1": frame.astype(np.float32) / 255.0,
        "5x5x3 uint8": np.zeros((5, 5, 3), np.uint8),
        "5x5 grey": np.zeros((5, 5), np.uint8),
        "grey frame": frame[:, :, 0],
        "four channels": np.concatenate([frame, frame[:, :, :1]], axis=2),
        "narrower than the icon": frame[:, : ICON_W - 1],
        "shorter than the icon": frame[: ICON_H - 1],
        "not an array": frame.tolist(),
    }
    for name, bad in bad_frames.items():
        result = locator.locate(bad)
        assert not is_successful(result), name
        assert isinstance(result.failure(), str), name
    assert is_successful(locator.locate(frame))  # the same frame, well formed, is found


def test_7_flat_frames_are_a_miss_not_an_exception(locator):
    """Flat patches give masked scores of nan/inf; the scrub turns them into a miss."""
    for value in (0, 1, 30, 255):
        result = locator.locate(np.full((FRAME_H, FRAME_W, 3), value, np.uint8))
        assert not is_successful(result), value
        assert result.failure() == "miss", value
    black = np.zeros((FRAME_H, FRAME_W, 3), np.uint8)
    assert locator.locate(_paste(black, 70, 30)).unwrap() == (70 + CENTRE_DX, 30 + CENTRE_DY)


def test_8_the_frame_is_read_as_rgb_not_bgr():
    """A red | blue icon greys to 76 | 29 as RGB and to 29 | 76 as BGR; with the channels read the wrong way
    round the template would correlate at -1 instead of 1. (The real icon cannot show it: its two greys differ by
    up to 47 levels under the mask but correlate at 0.9998, so TM_CCOEFF_NORMED scores it about 1 either way.)"""
    icon_rgb = np.zeros((ICON_H, ICON_W, 3), np.uint8)
    icon_rgb[:, :ICON_W // 2] = (255, 0, 0)
    icon_rgb[:, ICON_W // 2:] = (0, 0, 255)
    locator = CharacterLocator(cv2.cvtColor(icon_rgb, cv2.COLOR_RGB2GRAY), np.full((ICON_H, ICON_W), 255, np.uint8))
    frame = np.full((FRAME_H, FRAME_W, 3), 40, np.uint8)
    frame[20:20 + ICON_H, 50:50 + ICON_W] = icon_rgb
    assert locator.locate(frame).unwrap() == (50 + CENTRE_DX, 20 + CENTRE_DY)


def test_9_exclusion_px_keeps_shifts_of_the_best_match_from_counting_as_a_second_match():
    """A smooth 16 x 16 blob scores about 0.95 one pixel off its best position and about 0.2 six pixels off, so
    with min_margin 0.2 the one-pixel shifts are a second match unless the exclusion square covers them."""
    size = 16
    yy, xx = np.mgrid[0:size, 0:size]
    blob = (40 + 200 * np.exp(-((xx - 7.5) ** 2 + (yy - 7.5) ** 2) / (2 * 4.0 ** 2))).astype(np.uint8)
    noise = np.random.default_rng(1).integers(-2, 3, size=(FRAME_H, FRAME_W, 1))
    frame = np.repeat(40 + noise, 3, axis=2).astype(np.uint8)
    frame[20:20 + size, 50:50 + size] = blob[:, :, None]
    mask = np.full((size, size), 255, np.uint8)
    assert CharacterLocator(blob, mask, min_margin=0.2, exclusion_px=0).locate(frame).failure() == "ambiguous"
    found = CharacterLocator(blob, mask, min_margin=0.2, exclusion_px=5).locate(frame)
    assert found.unwrap() == (50 + size // 2, 20 + size // 2)


def test_10_from_files_with_a_mask_of_another_size_is_a_failure(tmp_path):
    cv2.imwrite(str(tmp_path / "small_mask.png"), np.full((5, 5), 255, np.uint8))
    result = CharacterLocator.from_files(ICON_PATH, tmp_path / "small_mask.png")
    assert not is_successful(result)
    assert isinstance(result.failure(), str)


@pytest.mark.parametrize(
    "icon, mask, kwargs",
    [
        (np.zeros((10, 10, 3), np.uint8), np.full((10, 10), 255, np.uint8), {}),
        (np.zeros((10, 10), np.float32), np.full((10, 10), 255, np.uint8), {}),
        (np.zeros((0, 0), np.uint8), np.zeros((0, 0), np.uint8), {}),
        (np.zeros((10, 10), np.uint8), np.full((9, 10), 255, np.uint8), {}),
        (np.zeros((10, 10), np.uint8), np.zeros((10, 10), np.uint8), {}),
        (np.zeros((10, 10), np.uint8), np.full((10, 10), 255, np.uint8), {"min_score": 0.0}),
        (np.zeros((10, 10), np.uint8), np.full((10, 10), 255, np.uint8), {"min_score": 1.5}),
        (np.zeros((10, 10), np.uint8), np.full((10, 10), 255, np.uint8), {"min_margin": -0.1}),
        (np.zeros((10, 10), np.uint8), np.full((10, 10), 255, np.uint8), {"exclusion_px": -1}),
    ],
)
def test_11_constructor_rejects_bad_arguments(icon, mask, kwargs):
    with pytest.raises(ValueError):
        CharacterLocator(icon, mask, **kwargs)
