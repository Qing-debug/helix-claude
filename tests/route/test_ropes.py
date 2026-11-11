"""Tests for `src.route.ropes` (rope fix plan, Task 6, C1): the zone of each marked rope as a cell table.

The marks are Fossil Foundry 2's two, from `tests/fixtures/fossil_foundry_2.ropes.json`: rope 46 (top 28, bottom 46, read
column 45) and rope 107 (top 43, bottom 61, read column 106). A character on a rope reads 1 px left of its column
(`ON_ROPE_DX`).
"""

import time
from pathlib import Path

import pytest

from src.controller.rope_file import RopeMark, read_ropes, rope_file_for
from src.route.params import MovementParams
from src.route.ropes import RopeZones

CSV = Path(__file__).resolve().parent.parent / "fixtures" / "fossil_foundry_2.csv"
MARKS = read_ropes(rope_file_for(CSV)).unwrap().ropes
LEFT, RIGHT = 0, 1   # rope 46 and rope 107


def _zones(**changes) -> RopeZones:
    return RopeZones.build(MARKS, MovementParams(**changes))


def test_the_fixture_has_the_two_measured_ropes():
    assert MARKS == (RopeMark(46, 28, 46), RopeMark(107, 43, 61))


@pytest.mark.parametrize("pos", [(106, 46), (106, 48), (106, 49), (104, 46), (108, 46), (106, 61), (106, 44)])
def test_reads_on_rope_107_are_in_its_zone(pos):
    assert _zones().at(pos) == RIGHT


@pytest.mark.parametrize("pos", [(103, 46), (109, 46), (106, 43), (106, 62), (106, 30), (107, 70)])
def test_reads_outside_rope_107_are_in_no_zone(pos):
    """x 2 px either side of the read column is in, 3 px is not; `top_y` itself is out, `bottom_y` is in."""
    assert _zones().at(pos) is None


@pytest.mark.parametrize("pos", [(45, 30), (45, 40), (46, 35), (43, 35), (47, 35), (45, 46), (45, 29)])
def test_reads_on_rope_46_are_in_its_zone(pos):
    """(46, 35): the one rope-46 hanging read seen live was at x 46, the column, not the column - 1 (ROPE148R m-3)."""
    assert _zones().at(pos) == LEFT


@pytest.mark.parametrize("pos", [(45, 28), (45, 47), (42, 35), (48, 35)])
def test_reads_outside_rope_46_are_in_no_zone(pos):
    assert _zones().at(pos) is None


def test_the_off_map_test_read_of_h85_is_in_no_zone():
    assert _zones().at((90, 35)) is None


def test_the_empty_zones_never_hold_a_read():
    for pos in [(106, 49), (45, 35), (0, 0), (90, 35)]:
        assert RopeZones.EMPTY.at(pos) is None


def test_mark_returns_the_mark_of_the_index():
    zones = _zones()
    assert zones.mark(LEFT) == MARKS[0] and zones.mark(RIGHT) == MARKS[1]


def test_the_half_width_comes_from_the_params():
    assert _zones(rope_zone_half_px=1).at((104, 46)) is None
    assert _zones(rope_zone_half_px=1).at((105, 46)) == RIGHT
    assert _zones(rope_zone_half_px=3).at((103, 46)) == RIGHT


def test_a_cell_in_two_zones_keeps_the_first_mark():
    marks = (RopeMark(50, 10, 40), RopeMark(53, 20, 60))   # read columns 49 and 52: x 50..51 is in both
    zones = RopeZones.build(marks, MovementParams())
    assert zones.at((50, 30)) == 0 and zones.at((51, 30)) == 0
    assert zones.at((54, 30)) == 1 and zones.at((47, 30)) == 0
    assert zones.at((52, 50)) == 1                      # below the first rope's bottom_y 40: only the second


def test_no_marks_build_an_empty_table():
    assert RopeZones.build((), MovementParams()).at((106, 49)) is None


def test_rope_lookup_is_cheap():
    """10^5 lookups on a mix of in-zone and out-of-zone reads: about 0.02 s expected, a loose bound for a slow box."""
    zones = _zones()
    reads = [(106, 49), (45, 35), (90, 35), (106, 55), (20, 20), (104, 46), (109, 46), (46, 35)] * 12_500
    assert len(reads) == 100_000
    at = zones.at
    start = time.perf_counter()
    for pos in reads:
        at(pos)
    assert time.perf_counter() - start < 0.5
