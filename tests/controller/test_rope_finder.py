"""The rope finder (C12, H92) on synthetic minimap frames built from the measured palette: background (38, 66, 95),
rope pixel (38, 58, 73), platform rows (238, 255, 255). A rope is a run of rows in one column; its top and bottom are
the platform rows it meets, minus 3 px (NODE_ABOVE_GROUND_PX)."""
import numpy as np
from returns.pipeline import is_successful

from src.controller.rope_file import RopeMark
from src.controller.rope_finder import find_ropes, median_frame

BACKGROUND = (38, 66, 95)
ROPE = (38, 58, 73)
DARKER_ROPE = (38, 58, 60)
PLATFORM = (238, 255, 255)
YELLOW = (255, 255, 0)
HEIGHT, WIDTH = 50, 60
TOP, MID, FLOOR = 10, 30, 44


def _frame(platform_rows, ropes, dots=(), *, bright_block=None) -> np.ndarray:
    """ropes: (x, first_row, last_row, colour); dots: (x0, x1, y0, y1) patches of yellow drawn last."""
    frame = np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8)
    frame[:] = BACKGROUND
    for x, first, last, colour in ropes:
        frame[first:last + 1, x] = colour
    for row in platform_rows:
        frame[row, :] = PLATFORM
    for x0, x1, y0, y1 in dots:
        frame[y0:y1 + 1, x0:x1 + 1] = YELLOW
    if bright_block is not None:
        x0, x1, y0, y1 = bright_block
        frame[y0:y1 + 1, x0:x1 + 1] = PLATFORM
    return frame


def test_two_ropes_between_three_platforms_are_found_exactly():
    frame = _frame((TOP, MID, FLOOR), [(20, TOP + 1, MID - 1, ROPE), (40, MID + 1, FLOOR - 1, ROPE)])
    assert find_ropes(frame) == [RopeMark(20, 7, 27), RopeMark(40, 27, 41)]


def test_rope_covered_by_a_dot_in_one_frame_of_five_is_found_in_the_median_only():
    clear = _frame((TOP, MID, FLOOR), [(40, MID + 1, FLOOR - 1, ROPE)])
    covered = _frame((TOP, MID, FLOOR), [(40, MID + 1, FLOOR - 1, ROPE)], dots=((39, 41, 35, 38),))
    assert find_ropes(covered) == []
    assert find_ropes(median_frame([clear, clear, covered, clear, clear]).unwrap()) == [RopeMark(40, 27, 41)]


def test_a_two_row_platform_gives_the_top_of_its_band():
    # the band is rows 9 and 10; its top row is 9, so top_y is 6 (not 7, the band's lower edge less 3)
    frame = _frame((9, 10, MID), [(20, 11, MID - 1, ROPE)])
    assert find_ropes(frame) == [RopeMark(20, 6, 27)]


def test_a_five_row_run_is_not_a_rope():
    assert find_ropes(_frame((TOP, 16), [(20, 11, 15, ROPE)])) == []


def test_a_dark_column_with_no_platform_below_is_not_a_rope():
    assert find_ropes(_frame((TOP,), [(20, TOP + 1, MID - 1, ROPE)])) == []


def test_a_dark_column_inside_a_bright_area_is_not_a_rope():
    # the floor block's edge: column 36 has a bright neighbour at 35, so it is not background on both sides. The frame
    # has platform rows above and below, so only the "both neighbours are background" rule (C12) rejects the column.
    frame = _frame((TOP, MID), [(36, TOP + 1, MID - 1, ROPE)], bright_block=(30, 35, TOP, MID))
    assert find_ropes(frame) == []


def test_a_platform_in_the_top_rows_gives_a_top_y_of_zero_not_a_negative_one():
    # platform row 2: 2 - 3 would be -1, which read_ropes refuses
    assert find_ropes(_frame((2, MID), [(20, 3, MID - 1, ROPE)])) == [RopeMark(20, 0, 27)]


def test_two_ropes_stacked_in_one_column_are_two_ropes():
    # floor -> mid and mid -> top share the column and the mid platform: their rows touch but do not overlap
    frame = _frame((TOP, MID, FLOOR), [(20, TOP + 1, MID - 1, ROPE), (20, MID + 1, FLOOR - 1, ROPE)])
    assert find_ropes(frame) == [RopeMark(20, 7, 27), RopeMark(20, 27, 41)]


def test_dark_columns_two_apart_with_one_clear_column_between_are_one_rope():
    frame = _frame((TOP, MID), [(20, TOP + 1, MID - 1, DARKER_ROPE), (22, TOP + 1, MID - 1, ROPE)])
    assert find_ropes(frame) == [RopeMark(20, 7, 27)]


def test_median_of_frames_of_two_shapes_is_a_failure():
    result = median_frame([np.zeros((5, 6, 3), np.uint8), np.zeros((5, 7, 3), np.uint8)])
    assert not is_successful(result)
    assert result.failure() == "the minimap changed size while ropes were being found; try again"


def test_median_of_no_frames_is_a_failure_with_its_own_message():
    result = median_frame([])
    assert not is_successful(result)
    assert result.failure() == "no minimap frames were collected, so ropes cannot be found; try again"


def test_median_is_per_pixel_uint8():
    frames = [np.full((2, 2, 3), value, np.uint8) for value in (10, 200, 20)]
    median = median_frame(frames).unwrap()
    assert median.dtype == np.uint8
    assert (median == 20).all()
