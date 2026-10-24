"""Localisation: join a measured minimap position to the route graph (plan contract C6).

`attach` turns a minimap pixel into an `Anchor` by the first of four rules that applies: on a node (A1), on a
platform segment between two nodes (A2), just beyond the end of a platform (A3), else off-graph (A4). Positions
are minimap pixels with a top-left origin (y grows downward).
"""

from dataclasses import dataclass
from fractions import Fraction

from returns.result import Failure, Result, Success

from src.controller.node import Node
from src.route.route_graph import RouteGraph


@dataclass(frozen=True)
class Anchor:
    node: Node | None  # the character is at this node
    platform: int | None  # index into terrain.platforms
    left: Node | None  # segment ends (None beyond a platform end)
    right: Node | None


def _on_node(rg: RouteGraph, px: int, py: int) -> Anchor | None:
    """A1. The nearest node (Euclidean) with both |dx| and |dy| within `align_px`; ties go to the earlier node."""
    best: Node | None = None
    best_d2 = 0
    for node in rg.graph.nodes:  # `RouteGraph.build` adds nodes in node_map order
        x, y = node.minimap_position
        dx, dy = px - x, py - y
        if abs(dx) > rg.align_px or abs(dy) > rg.align_px:
            continue
        d2 = dx * dx + dy * dy
        if best is None or d2 < best_d2:
            best, best_d2 = node, d2
    if best is None:
        return None
    return Anchor(best, rg.terrain.platform_index(best), None, None)


def _on_segment(rg: RouteGraph, px: int, py: int) -> Anchor | None:
    """A2. The platform segment (L, R), Lx <= px <= Rx, whose interpolated y is within `attach_y_px` of py and
    nearest to it; ties go to the lower platform index, then the leftmost segment.

    The offset is an exact `Fraction`, so a tie or a position exactly `attach_y_px` away is not decided by float
    rounding. A segment whose ends share an x is a vertical step, not ground to stand on, and has no y to
    interpolate, so it is skipped.
    """
    best: Anchor | None = None
    best_offset = Fraction(0)
    for index, platform in enumerate(rg.terrain.platforms):
        for left, right in zip(platform, platform[1:]):
            lx, ly = left.minimap_position
            rx, ry = right.minimap_position
            if rx == lx or not lx <= px <= rx:
                continue
            offset = abs(py - (ly + Fraction(ry - ly) * (px - lx) / (rx - lx)))
            if offset <= rg.params.attach_y_px and (best is None or offset < best_offset):
                best, best_offset = Anchor(None, index, left, right), offset
    return best


def _beyond_end(rg: RouteGraph, px: int, py: int) -> Anchor | None:
    """A3. A platform's first node F with 0 < Fx - px <= `reach_h`, or last node Z with 0 < px - Zx <= `reach_h`,
    whose y is within `attach_y_px` of py; the smallest |dx| wins, ties go to the lower platform index."""
    best: Anchor | None = None
    best_dx = 0
    for index, platform in enumerate(rg.terrain.platforms):
        first, last = platform[0], platform[-1]
        fx, fy = first.minimap_position
        zx, zy = last.minimap_position
        candidates = (
            (fx - px, fy, Anchor(None, index, None, first)),
            (px - zx, zy, Anchor(None, index, last, None)),
        )
        for dx, y, anchor in candidates:
            within_reach = 0 < dx <= rg.reach_h
            if within_reach and abs(py - y) <= rg.params.attach_y_px and (best is None or dx < best_dx):
                best, best_dx = anchor, dx
    return best


def attach(rg: RouteGraph, pos: tuple[int, int]) -> Result[Anchor, str]:
    """Anchor a measured minimap position `pos` to `rg`.

    Returns `Failure("off-graph: (x, y)")` when no rule applies.
    """
    px, py = pos
    for rule in (_on_node, _on_segment, _beyond_end):
        anchor = rule(rg, px, py)
        if anchor is not None:
            return Success(anchor)
    return Failure(f"off-graph: ({px}, {py})")
