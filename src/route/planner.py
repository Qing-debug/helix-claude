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
from src.controller.rope_file import RopeMark
from src.input.protocol import Key
from src.route.locate import Anchor, attach
from src.route.route_graph import EdgeKind, RouteGraph, down_options, horizontal_cost_s, is_marked_end


class MoveKind(StrEnum):
    WALK = "walk"
    TELEPORT = "teleport"
    DROP = "drop"
    JUMP_OFF = "jump_off"


@dataclass(frozen=True)
class Move:
    kind: MoveKind
    direction: Key | None  # Key.LEFT/RIGHT for WALK and JUMP_OFF; LEFT/RIGHT/UP/DOWN for TELEPORT; None for DROP
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
        if on_slope:
            approach = d / rg.params.walk_px_per_s
        else:
            # QB282: the last hop onto an end that stops a teleport is a teleport, but only when one from `pos` stops
            # there (review m1: one that carries onto the next platform is not a stop on the end)
            sign = 1 if end.minimap_position[0] > pos[0] else -1
            to_end = (
                rg.is_stop_end(end, sign)
                and anchor.platform is not None
                and rg.side_landing(anchor.platform, pos[0], sign)[1] == anchor.platform
            )
            approach = horizontal_cost_s(d, rg.reach_h, rg.params, to_end=to_end)
        starts.append((end, approach))
    return starts


def _direction(px: int, goal_x: int) -> Key:
    return Key.RIGHT if goal_x > px else Key.LEFT


def _walk(rg: RouteGraph, px: int, goal_x: int) -> Move:
    d = abs(goal_x - px)
    return Move(MoveKind.WALK, _direction(px, goal_x), min(1000.0 * d / rg.params.walk_px_per_s, rg.params.max_walk_ms))


def _run(rg: RouteGraph, px: int, goal_x: int, platform: int | None) -> Move:
    """P4's off-slope rule for the character on `platform` (None: on none, so the teleport's landing is the full reach).

    The landing is where `rg.side_landing` says a teleport from `px` stops (Hong, QB282). (1) Teleport when it stops
    exactly on the platform's end node, an end that stops teleports (a mark, or a gap's split end), within `arrive_px`
    of the goal, even under `walk_under_px`; it never catches an ordinary 17-21 px move toward an unmarked node, and it
    fires when the full reach lands exactly on the end node too. (2) Walk when the goal is closer than `walk_under_px`
    (QB266: a teleport would land past it). (3) Teleport when one teleport plus the walk from its landing to the goal
    beats walking all of it, if it lands on the platform. (4) Walk. A teleport that carries onto the next platform is
    no stop on the end, so rules (1) and (3) skip it."""
    d = abs(goal_x - px)
    sign = 1 if goal_x > px else -1
    p = rg.params
    if platform is None:
        land_x, land_p = px + sign * rg.reach_h, None
    else:
        land_x, land_p = rg.side_landing(platform, px, sign)
        nodes = rg.terrain.platforms[platform]
        end = nodes[-1] if sign > 0 else nodes[0]
        if (
            land_p == platform
            and rg.is_stop_end(end, sign)
            and land_x == end.minimap_position[0]
            and abs(land_x - goal_x) <= p.arrive_px
        ):
            return Move(MoveKind.TELEPORT, _direction(px, goal_x), None)
    if d < p.walk_under_px:
        return _walk(rg, px, goal_x)
    if land_p == platform and p.teleport_s + abs(land_x - goal_x) / p.walk_px_per_s < d / p.walk_px_per_s:
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


def _past_marked_end(rg: RouteGraph, px: int, h: Node) -> bool:
    """`px` lies past an end of h's platform that the map marks, so an up or down move toward h from `px` finds no
    platform there (H17: the teleport fails). Keyed on the marks only (`is_marked_end`, ISS6-B-D1): past an unmarked
    end the platform is taken to go on (ANYMAP198-R3-D1, option B)."""
    platform = rg.terrain.platforms[rg.terrain.platform_index(h)]
    first, last = platform[0], platform[-1]
    return (px < first.minimap_position[0] and is_marked_end(first, -1)) or (
        px > last.minimap_position[0] and is_marked_end(last, 1)
    )


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
    return RouteGraph(view, rg.terrain, rg.align_px, rg.reach_h, rg.reach_v, rg.params, split_ends=rg.split_ends, teleport_links=rg.teleport_links)


def _go_down(rg: RouteGraph, g: Node, h: Node, rng: np.random.Generator) -> Move:
    """P5's DOWN edge (H87): teleport down whenever a teleport reaches the target platform; drop only when it cannot.
    Nothing is drawn from `rng`, which stays in the signature for `plan_move`'s callers. `_offered` and the graph
    cost have already left out an edge with no option, so a move that is not a teleport here is a drop."""
    options = down_options(abs(h.minimap_position[1] - g.minimap_position[1]), bool(g.can_fall), rg.reach_v, rg.params)
    if "teleport" in options:
        return Move(MoveKind.TELEPORT, Key.DOWN, None)
    return Move(MoveKind.DROP, None, None)


def rope_move(mark: RopeMark, pos: tuple[int, int], target: Node, tries: int, reach_v: float,
              blink_tries: int) -> Move | None:
    """The `tries`-th move of the rope rule (H88, H93) for a character read at `pos` on `mark`'s rope, or None when the
    order is used up. Pure: no rng, no graph.

    When a blink is in reach (`gap <= reach_v`), the order is `blink_tries` blinks toward the closer platform
    (`TELEPORT DOWN`, or `TELEPORT UP` when the platform above is strictly closer, a tie goes down) and then one
    `JUMP_OFF` toward the target's x (C3: right when level); with no blink in reach it is the one `JUMP_OFF`.
    The gaps are `pos.y - mark.top_y` up and `mark.bottom_y - pos.y` down.
    """
    up, down = pos[1] - mark.top_y, mark.bottom_y - pos[1]
    direction, gap = (Key.UP, up) if up < down else (Key.DOWN, down)
    blinks = blink_tries if gap <= reach_v else 0
    if tries < blinks:
        return Move(MoveKind.TELEPORT, direction, None)
    if tries == blinks:
        return Move(MoveKind.JUMP_OFF, Key.LEFT if target.minimap_position[0] < pos[0] else Key.RIGHT, None)
    return None


@dataclass(frozen=True)
class PlanDetail:
    """What the route preview shows of one `plan_move` decision with a move, besides its `Decision`: the candidate
    starts as (cost, node), left before right; the chosen start; the path; the move's aim; and the platform `attach`
    gave the position. The aim is the goal x `aim_x` of a move left or right, on `aim_node`'s platform, or for an up or
    down move (`aim_x` None) the edge `aim_node` -> the Decision's `toward`."""

    candidates: list[tuple[float, Node]]
    start: Node
    path: tuple[Node, ...]
    aim_x: int | None
    aim_node: Node
    platform: int | None


def plan_move(
    rg: RouteGraph, pos: tuple[int, int], target: Node, rng: np.random.Generator, detail: list[PlanDetail] | None = None
) -> Result[Decision, str]:
    """`next_move`'s rules P1-P5: the one routine `next_move` and the route preview share (cleanup piece W1-8, R13,
    Hong's H140), so the preview cannot drift from the planner. Given a `detail` list, a decision with a move also
    appends its `PlanDetail`, which the preview asks for; `next_move` passes none, so the walker's path builds nothing
    for the preview. `rng` only reaches `_go_down`, which draws nothing (H87)."""
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
    # P4: when g's next edge is UP/DOWN (a launch x, B14-inv-D1) or GAP (`gap_launch`, QB282) and the character is on
    # g's platform, the goal is that move's launch x, so the character lines up before it moves; otherwise the goal is g
    launch = None
    if gi + 1 < len(path) and anchor.platform == rg.terrain.platform_index(g):
        h = path[gi + 1]
        edge_kind = _edge_kind(rg, g, h)
        if edge_kind in (EdgeKind.UP, EdgeKind.DOWN):
            launch = _launch_x(rg, g, h)  # never None here: `_offered` dropped the edges without one
        elif edge_kind == EdgeKind.GAP:
            launch = rg.gap_launch(g, h)  # never None: `_edge_cost` added no GAP edge without a launch
        if launch is not None and abs(px - launch) > rg.params.arrive_px:
            move = _walk(rg, px, launch) if on_slope else _run(rg, px, launch, anchor.platform)
            if detail is not None:
                detail.append(PlanDetail(candidates, start, path, launch, g, anchor.platform))
            return Success(Decision(move, total, h))
    if launch is None and not _is_at(rg, pos, g, anchor):
        goal_x = g.minimap_position[0]
        move = _walk(rg, px, goal_x) if on_slope else _run(rg, px, goal_x, anchor.platform)
        if detail is not None:
            detail.append(PlanDetail(candidates, start, path, goal_x, g, anchor.platform))
        return Success(Decision(move, total, g))

    # P5: at g, or lined up on the launch x of g's UP/DOWN/GAP edge (g is not the target, or P1 would have applied)
    h = path[gi + 1]
    kind = _edge_kind(rg, g, h)
    if kind == EdgeKind.SLOPE:
        if detail is not None:
            detail.append(PlanDetail(candidates, start, path, h.minimap_position[0], h, anchor.platform))
        return Success(Decision(_walk(rg, px, h.minimap_position[0]), total, h))
    if kind == EdgeKind.WALK:  # only when P4 kept g at the start of a slope segment
        g_next = path[_walk_run_end(rg, path, gi + 1)]
        if detail is not None:
            detail.append(PlanDetail(candidates, start, path, g_next.minimap_position[0], g_next, anchor.platform))
        return Success(Decision(_run(rg, px, g_next.minimap_position[0], anchor.platform), total, g_next))
    if kind in (EdgeKind.UP, EdgeKind.DOWN) and _past_marked_end(rg, px, h):
        # P4 counts the character lined up within `arrive_px` of the launch, so the read may lie 1-2 px past a marked end
        # of h's platform, with nothing above or below to land on: walk to the launch x first, as the GAP branch does
        launch_x = _launch_x(rg, g, h)  # never None: `_offered` dropped the edges without one
        if detail is not None:
            aim_x = h.minimap_position[0] if launch_x is None else launch_x
            detail.append(PlanDetail(candidates, start, path, aim_x, g, anchor.platform))
        return Success(Decision(_walk(rg, px, h.minimap_position[0] if launch_x is None else launch_x), total, h))
    if kind == EdgeKind.UP:
        if detail is not None:
            detail.append(PlanDetail(candidates, start, path, None, g, anchor.platform))
        return Success(Decision(Move(MoveKind.TELEPORT, Key.UP, None), total, h))
    if kind == EdgeKind.GAP:
        # a sideways teleport across the gap, only when the planner's own landing rule carries it onto h's platform from
        # here (review I2: P4 counts the character lined up within `arrive_px` of the launch, from 1-2 px short of it
        # a teleport may stop at the end); otherwise walk to the launch x
        sign = 1 if h.minimap_position[0] > px else -1
        if anchor.platform is not None and rg.side_landing(anchor.platform, px, sign)[1] == rg.terrain.platform_index(h):
            if detail is not None:
                detail.append(PlanDetail(candidates, start, path, h.minimap_position[0], h, anchor.platform))
            return Success(Decision(Move(MoveKind.TELEPORT, Key.RIGHT if sign > 0 else Key.LEFT, None), total, h))
        launch_x = rg.gap_launch(g, h)  # never None: `_edge_cost` added no GAP edge without a launch
        if detail is not None:
            aim_x = g.minimap_position[0] if launch_x is None else launch_x
            detail.append(PlanDetail(candidates, start, path, aim_x, g, anchor.platform))
        return Success(Decision(_walk(rg, px, g.minimap_position[0] if launch_x is None else launch_x), total, h))
    if detail is not None:
        detail.append(PlanDetail(candidates, start, path, None, g, anchor.platform))
    return Success(Decision(_go_down(rg, g, h, rng), total, h))


def next_move(rg: RouteGraph, pos: tuple[int, int], target: Node, rng: np.random.Generator) -> Result[Decision, str]:
    """The next move from `pos` toward `target`, or `Decision(None, 0.0, None)` when the character is at `target`.

    `Failure` when `target` is not in the route graph, `pos` is off the graph (`attach`'s message, unchanged), or no
    path leads to `target`. `rng` is no longer drawn from (H87: a down move teleports when a teleport reaches, else
    drops); the parameter stays for the walker, the simulator and the route preview. The rules are `plan_move`'s.
    """
    return plan_move(rg, pos, target, rng)
