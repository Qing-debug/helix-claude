"""Planner: the next move toward a target node, and the choice of ability (plan contract C7).

`next_move` anchors a measured position to the route graph, picks the cheaper end of the segment the character
stands on as the start, follows the minimum-cost path, and turns its first stretch into one move: a walk, a
teleport or a drop. It plans one move at a time; the walker re-plans from the measured position after each settle.
"""

from dataclasses import dataclass
from enum import StrEnum

import networkx as nx
import numpy as np
from returns.pipeline import is_successful
from returns.result import Failure, Result, Success

from src.controller.node import Node
from src.input.protocol import Key
from src.route.locate import Anchor, attach
from src.route.route_graph import EdgeKind, RouteGraph, down_options, horizontal_cost_s


class MoveKind(StrEnum):
    WALK = "walk"
    TELEPORT = "teleport"
    DROP = "drop"


@dataclass(frozen=True)
class Move:
    kind: MoveKind
    direction: Key | None  # Key.LEFT/RIGHT for WALK; LEFT/RIGHT/UP/DOWN for TELEPORT; None for DROP
    walk_ms: float | None  # only for WALK


@dataclass(frozen=True)
class Decision:
    move: Move | None  # None: arrived
    cost_to_go_s: float  # 0.0 when arrived
    toward: Node | None  # the node this move heads for; None when arrived


def _edge_kind(rg: RouteGraph, u: Node, v: Node) -> EdgeKind:
    return rg.graph.edges[u, v]["kind"]


def _is_at(rg: RouteGraph, pos: tuple[int, int], node: Node, anchor: Anchor | None) -> bool:
    """The character is at `node`: within `arrive_px` in x, and within `arrive_px` in y or on the node's platform
    (hand-placed nodes may sit a few px above or below the surface the character stands on)."""
    node_x, node_y = node.minimap_position
    arrive = rg.params.arrive_px
    if abs(pos[0] - node_x) > arrive:
        return False
    if abs(pos[1] - node_y) <= arrive:
        return True
    return anchor is not None and anchor.platform == rg.terrain.platform_index(node)


def _on_slope_segment(rg: RouteGraph, anchor: Anchor) -> bool:
    """The anchor is on a segment whose two ends are joined by a SLOPE edge, in either direction."""
    left, right = anchor.left, anchor.right
    if left is None or right is None:
        return False
    return any(
        rg.graph.has_edge(u, v) and _edge_kind(rg, u, v) == EdgeKind.SLOPE for u, v in ((left, right), (right, left))
    )


def _starts(rg: RouteGraph, pos: tuple[int, int], anchor: Anchor, on_slope: bool) -> list[tuple[Node, float]]:
    """The nodes to plan from, each with the cost of getting there from `pos` (left before right)."""
    if anchor.node is not None:
        return [(anchor.node, 0.0)]
    starts = []
    for end in (anchor.left, anchor.right):
        if end is None:
            continue
        d = abs(pos[0] - end.minimap_position[0])
        approach = d / rg.params.walk_px_per_s if on_slope else horizontal_cost_s(d, rg.reach_h, rg.params)
        starts.append((end, approach))
    return starts


def _direction(px: int, goal_x: int) -> Key:
    return Key.RIGHT if goal_x > px else Key.LEFT


def _walk(rg: RouteGraph, px: int, goal_x: int) -> Move:
    d = abs(goal_x - px)
    return Move(MoveKind.WALK, _direction(px, goal_x), min(1000.0 * d / rg.params.walk_px_per_s, rg.params.max_walk_ms))


def _run(rg: RouteGraph, px: int, goal_x: int) -> Move:
    """P4's off-slope rule: teleport when one teleport plus the walk to the goal beats walking all of it."""
    d = abs(goal_x - px)
    p = rg.params
    if p.teleport_s + abs(d - rg.reach_h) / p.walk_px_per_s < d / p.walk_px_per_s:
        return Move(MoveKind.TELEPORT, _direction(px, goal_x), None)
    return _walk(rg, px, goal_x)


def _walk_run_end(rg: RouteGraph, path: tuple[Node, ...], i: int) -> int:
    """Index of the last node reached from `path[i]` by consecutive WALK edges."""
    while i + 1 < len(path) and _edge_kind(rg, path[i], path[i + 1]) == EdgeKind.WALK:
        i += 1
    return i


def _surface_ys(platform: tuple[Node, ...], x: int) -> list[float]:
    """The y of each piece of `platform` at `x`: the interpolated y of every segment spanning x (both ends of a
    vertical step), or the node's y for a one-node platform at x."""
    if len(platform) == 1:
        node_x, node_y = platform[0].minimap_position
        return [float(node_y)] if node_x == x else []
    ys: list[float] = []
    for p, q in zip(platform, platform[1:]):
        (lx, ly), (rx, ry) = p.minimap_position, q.minimap_position
        if not lx <= x <= rx:
            continue
        ys.extend((ly, ry) if lx == rx else (ly + (ry - ly) * (x - lx) / (rx - lx),))
    return ys


def _piece_beyond(rg: RouteGraph, x: int, y: int, up: bool, own: int) -> int | None:
    """Index of the platform nearest to (x, y) straight above it (`up`) or below it, other than platform `own`."""
    best: tuple[float, int] | None = None
    for index, platform in enumerate(rg.terrain.platforms):
        if index == own:
            continue
        for surface_y in _surface_ys(platform, x):
            gap = y - surface_y if up else surface_y - y
            if gap > 0 and (best is None or gap < best[0]):
                best = (gap, index)
    return None if best is None else best[1]


def _launch_x(rg: RouteGraph, g: Node, h: Node) -> int | None:
    """Where the UP/DOWN move g -> h is made from (B14-inv-D1), or None when the planner does not offer it.

    (1) g's platform spans h's x: from h's x. (2) It ends before h's x: from the platform end nearest h, but only if
    the nearest platform straight above (UP) or below (DOWN) that end is h's platform.
    """
    own = rg.terrain.platform_index(g)
    platform = rg.terrain.platforms[own]
    hx = h.minimap_position[0]
    first, last = platform[0], platform[-1]
    if first.minimap_position[0] <= hx <= last.minimap_position[0]:
        return hx
    end = last if hx > last.minimap_position[0] else first
    ex, ey = end.minimap_position
    up = _edge_kind(rg, g, h) == EdgeKind.UP
    if _piece_beyond(rg, ex, ey, up, own) == rg.terrain.platform_index(h):
        return ex
    return None


def _offered(rg: RouteGraph) -> RouteGraph:
    """`rg` without the UP/DOWN edges that have no launch point, so no path takes them."""
    blocked = {
        (u, v)
        for u, v, kind in rg.graph.edges(data="kind")
        if kind in (EdgeKind.UP, EdgeKind.DOWN) and _launch_x(rg, u, v) is None
    }
    if not blocked:
        return rg
    view = nx.subgraph_view(rg.graph, filter_edge=lambda u, v: (u, v) not in blocked)
    return RouteGraph(view, rg.terrain, rg.align_px, rg.reach_h, rg.reach_v, rg.params)


def _go_down(rg: RouteGraph, g: Node, h: Node, rng: np.random.Generator) -> Move:
    """P5's DOWN edge: teleport down or drop. Within `ability_mix_band` of each other the two are a coin flip."""
    options = down_options(abs(h.minimap_position[1] - g.minimap_position[1]), bool(g.can_fall), rg.reach_v, rg.params)
    if len(options) == 1:
        choice = next(iter(options))
    else:
        cheap, dear = sorted(options, key=options.__getitem__)  # a tie keeps "teleport" first
        if options[dear] <= options[cheap] * (1 + rg.params.ability_mix_band):
            choice = "drop" if rng.random() < 0.5 else "teleport"
        else:
            choice = cheap
    if choice == "drop":
        return Move(MoveKind.DROP, None, None)
    return Move(MoveKind.TELEPORT, Key.DOWN, None)


def next_move(rg: RouteGraph, pos: tuple[int, int], target: Node, rng: np.random.Generator) -> Result[Decision, str]:
    """The next move from `pos` toward `target`, or `Decision(None, 0.0, None)` when the character is at `target`.

    `Failure` when `target` is not in the route graph, `pos` is off the graph (`attach`'s message, unchanged), or no
    path leads to `target`. `rng` is drawn from only to choose between teleporting down and dropping.
    """
    if target not in rg.graph:
        return Failure(f"target {target.minimap_position!r} is not in the route graph")
    px, py = pos
    anchor_result = attach(rg, pos)
    anchor = anchor_result.value_or(None)

    # P1
    if _is_at(rg, pos, target, anchor):
        return Success(Decision(None, 0.0, None))
    # P2
    if anchor is None:
        return Failure(anchor_result.failure())

    # P3: the cheapest start, ties left before right, over the moves the planner offers
    rg = _offered(rg)
    on_slope = _on_slope_segment(rg, anchor)
    candidates = []
    for start, approach in _starts(rg, pos, anchor, on_slope):
        cost = rg.cost_to(start, target)
        if is_successful(cost):
            candidates.append((approach + cost.unwrap(), start))
    if not candidates:
        tx, ty = target.minimap_position
        return Failure(f"no path from ({px}, {py}) to ({tx}, {ty})")
    total, start = min(candidates, key=lambda c: c[0])
    path_result = rg.path(start, target)
    if not is_successful(path_result):  # cost_to succeeded on the same graph, so this does not happen
        return Failure(path_result.failure())
    path = path_result.unwrap()

    # P4: on a slope segment the goal is the start itself, otherwise the end of the WALK run from the start
    gi = 0 if on_slope else _walk_run_end(rg, path, 0)
    g = path[gi]
    # P4: when g's next edge is UP/DOWN and the character is on g's platform, the goal is that move's launch x
    # (B14-inv-D1), so the character lines up before it moves; otherwise the goal is g
    launch = None
    if gi + 1 < len(path) and anchor.platform == rg.terrain.platform_index(g):
        h = path[gi + 1]
        if _edge_kind(rg, g, h) in (EdgeKind.UP, EdgeKind.DOWN):
            launch = _launch_x(rg, g, h)  # never None here: `_offered` dropped the edges without one
            if launch is not None and abs(px - launch) > rg.params.arrive_px:
                move = _walk(rg, px, launch) if on_slope else _run(rg, px, launch)
                return Success(Decision(move, total, h))
    if launch is None and not _is_at(rg, pos, g, anchor):
        move = _walk(rg, px, g.minimap_position[0]) if on_slope else _run(rg, px, g.minimap_position[0])
        return Success(Decision(move, total, g))

    # P5: at g, or lined up on the launch x of g's UP/DOWN edge (g is not the target, or P1 would have applied)
    h = path[gi + 1]
    kind = _edge_kind(rg, g, h)
    if kind == EdgeKind.SLOPE:
        return Success(Decision(_walk(rg, px, h.minimap_position[0]), total, h))
    if kind == EdgeKind.WALK:  # only when P4 kept g at the start of a slope segment
        g_next = path[_walk_run_end(rg, path, gi + 1)]
        return Success(Decision(_run(rg, px, g_next.minimap_position[0]), total, g_next))
    if kind == EdgeKind.UP:
        return Success(Decision(Move(MoveKind.TELEPORT, Key.UP, None), total, h))
    return Success(Decision(_go_down(rg, g, h, rng), total, h))
