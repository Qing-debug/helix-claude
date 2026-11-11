"""The rope zones (C1, H88): where a still off-graph read counts as "on this rope".

A map's rope file (`src/controller/rope_file.py`) marks each rope by its column and the two platform rows it joins. The
walker asks `RopeZones.at(pos)` only for a read that `next_move` and `locate.attach` have both failed to place, so the
lookup is one dict get on a read that was off the map anyway and no work at all on one the map places. The table is
built once, from every cell of every zone, because a lookup in it is about 157 ns where a test of each mark's box is
slower and grows with the marks (OUTLINE147).
"""

from collections.abc import Sequence
from typing import ClassVar

from src.controller.rope_file import ON_ROPE_DX, RopeMark
from src.route.params import MovementParams


class RopeZones:
    """The marks of a map and a cell table from each zone cell to the index of the mark it belongs to."""

    EMPTY: ClassVar["RopeZones"]   # a map with no rope file: `at` is always None

    def __init__(self, marks: tuple[RopeMark, ...], cells: dict[tuple[int, int], int]) -> None:
        self._marks = marks
        self._cells = cells

    @classmethod
    def build(cls, marks: Sequence[RopeMark], params: MovementParams) -> "RopeZones":
        """C1: x within `rope_zone_half_px` of the rope's read x (`column + ON_ROPE_DX`) and `top_y < y <= bottom_y`.
        A cell in two zones keeps the first mark."""
        half = params.rope_zone_half_px
        cells: dict[tuple[int, int], int] = {}
        for i, mark in enumerate(marks):
            read_x = mark.x + ON_ROPE_DX
            for x in range(read_x - half, read_x + half + 1):
                for y in range(mark.top_y + 1, mark.bottom_y + 1):
                    cells.setdefault((x, y), i)
        return cls(tuple(marks), cells)

    def at(self, pos: tuple[int, int]) -> int | None:
        """The index of the rope whose zone holds `pos`, or None."""
        return self._cells.get(pos)

    def mark(self, i: int) -> RopeMark:
        return self._marks[i]


RopeZones.EMPTY = RopeZones((), {})
