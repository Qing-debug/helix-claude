"""Terrain inference for route walking (plan contract C4).

From the hand-authored node layout alone, infer which nodes are connected by a walk (level or slope), which
by a near-aligned vertical move, and which nodes share a platform. Positions are minimap pixels with a
top-left origin (y grows downward).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from returns.result import Failure, Result, Success

from src.controller.node import Node
from src.controller.path_graph import PathGraph
from src.route.params import MovementParams


class LinkKind(StrEnum):
    LEVEL = "level"
    SLOPE = "slope"
    UP = "up"
    DOWN = "down"


@dataclass(frozen=True)
class Link:
    src: Node
    dst: Node
    kind: LinkKind


@dataclass(frozen=True)
class Terrain:
    links: tuple[Link, ...]
    platforms: tuple[tuple[Node, ...], ...]

    def platform_index(self, node: Node) -> int:
        """Index of the platform holding `node`. Raises KeyError for a node not in any platform."""
        for i, platform in enumerate(self.platforms):
            if any(member is node for member in platform):
                return i
        raise KeyError(node)


def _is_int(v: object) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _bad_position(node: Node) -> bool:
    pos = node.minimap_position
    return not (isinstance(pos, tuple) and len(pos) == 2 and _is_int(pos[0]) and _is_int(pos[1]))


def _walk_links(
    nodes: Sequence[Node], align_px: int, reach_h: int, params: MovementParams
) -> list[tuple[int, int, LinkKind]]:
    """R1. Returns (src index, dst index, kind) in emission order."""
    out = []
    for i, a in enumerate(nodes):
        ax, ay = a.minimap_position
        for side in (-1, 1):
            best = None  # (|dx|, index)
            for j, b in enumerate(nodes):
                if j == i:
                    continue
                dx = b.minimap_position[0] - ax
                if side * dx <= 0 or abs(dx) > reach_h:
                    continue
                dy = abs(b.minimap_position[1] - ay)
                walkable = dy <= align_px or (
                    dy <= params.slope_max_dy_px and dy <= params.slope_max_grade * abs(dx)
                )
                if walkable and (best is None or abs(dx) < best[0]):
                    best = (abs(dx), j)
            if best is not None:
                dy = abs(nodes[best[1]].minimap_position[1] - ay)
                out.append((i, best[1], LinkKind.LEVEL if dy <= align_px else LinkKind.SLOPE))
    return out


def _platforms(nodes: Sequence[Node], walk: list[tuple[int, int, LinkKind]]) -> list[list[int]]:
    """R2. Connected components of the undirected walk-link graph, as lists of node indices."""
    parent = list(range(len(nodes)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i, j, _ in walk:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[max(ri, rj)] = min(ri, rj)  # the root is the earliest member
    groups: dict[int, list[int]] = {}
    for i in range(len(nodes)):
        groups.setdefault(find(i), []).append(i)
    # the root is the earliest member, so sorting by it orders the platforms by their earliest member
    return [
        sorted(members, key=lambda k: (nodes[k].minimap_position[0], k))
        for _, members in sorted(groups.items())
    ]


def _is_blocked(a: Node, b: Node, nodes: Sequence[Node], platforms: list[list[int]], own: set[int]) -> bool:
    """True if some consecutive pair of a third platform passes strictly between a and b at a's x."""
    ax, ay = a.minimap_position
    by = b.minimap_position[1]
    lo, hi = min(ay, by), max(ay, by)
    for pi, members in enumerate(platforms):
        if pi in own:
            continue
        for p_idx, q_idx in zip(members, members[1:]):
            px, py = nodes[p_idx].minimap_position
            qx, qy = nodes[q_idx].minimap_position
            if not (px <= ax <= qx):
                continue
            if px == qx:  # same x: the platform is a vertical step here, test both ends
                ys = (py, qy)
            else:
                ys = (py + (qy - py) * (ax - px) / (qx - px),)
            if any(lo < y < hi for y in ys):
                return True
    return False


def _vertical_links(
    nodes: Sequence[Node],
    reach_v: int,
    params: MovementParams,
    platforms: list[list[int]],
    platform_of: dict[int, int],
) -> list[Link]:
    """R3. Near-aligned UP/DOWN links in emission order (per node: UP, then DOWN)."""
    vertical: list[Link] = []
    for i, a in enumerate(nodes):
        ax, ay = a.minimap_position
        for kind in (LinkKind.UP, LinkKind.DOWN):
            best = None  # (|dy|, |dx|, index)
            for j, b in enumerate(nodes):
                if j == i:
                    continue
                bx, by = b.minimap_position
                if abs(bx - ax) > params.near_x_px:
                    continue
                if kind == LinkKind.UP:
                    ok = by < ay and abs(by - ay) <= reach_v
                else:
                    ok = by > ay and (abs(by - ay) <= reach_v or a.can_fall)
                if not ok:
                    continue
                key = (abs(by - ay), abs(bx - ax))
                if best is None or key < best[:2]:
                    best = (*key, j)
            if best is None:
                continue
            b = nodes[best[2]]
            if platform_of[best[2]] == platform_of[i]:
                continue
            if _is_blocked(a, b, nodes, platforms, {platform_of[i], platform_of[best[2]]}):
                continue
            if not PathGraph._isVerticalEdgeValid(a, b):
                continue
            vertical.append(Link(a, b, kind))
    return vertical


def infer_terrain(
    nodes: Sequence[Node], align_px: int, reach_h: int, reach_v: int, params: MovementParams
) -> Result[Terrain, str]:
    """Infer walk links (R1), platforms (R2) and vertical links (R3). Failure for no nodes or a bad position."""
    if len(nodes) == 0:
        return Failure("nodes is empty")
    for n in nodes:
        if _bad_position(n):
            return Failure(f"minimap_position must be a 2-tuple of ints, got {n.minimap_position!r}")

    walk = _walk_links(nodes, align_px, reach_h, params)
    platforms = _platforms(nodes, walk)
    platform_of = {k: pi for pi, members in enumerate(platforms) for k in members}
    vertical = _vertical_links(nodes, reach_v, params, platforms, platform_of)

    links = tuple(Link(nodes[i], nodes[j], kind) for i, j, kind in walk) + tuple(vertical)
    return Success(Terrain(links, tuple(tuple(nodes[k] for k in members) for members in platforms)))
