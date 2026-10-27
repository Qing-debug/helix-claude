"""Route preview: the route the planner would take from a position to a target, and the move on each hop (spec 5.3,
plan contract V4).

`preview(rg, pos, target)` gives the candidate starts with their costs, the chosen start, the minimum-cost path and
the hops: hop 0 is the move `next_move` makes from `pos`, and hop k the move it makes from the point where hop k-1 is
assumed to land (the landing rule, V4). It draws nothing: where `next_move` would toss a coin between a drop and a
teleport down, the hop holds both options.

`_plan` is this module's copy of `next_move`'s rules P1-P5 (`planner.py:190-247`), statement for statement, and calls
the planner's private helpers (RV1-D4 A, H53; RV-11 notes the seam in `src/route/CLAUDE.md`); `planner.py` is never
edited. It differs only in what it returns (the options, `toward`, the total, the starts, the path and the move's
aim) and in the coin flip, where it calls `_go_down` with two stub generators (RV3-D4 A).
`tests/route/test_route_preview.py` pins it against `next_move` (checks S3 and S3b).

Where the plan is silent (each decided here):
- An arrived preview is `complete=True`: its only position, `pos`, is arrived.
- An expansion that stops early keeps `cost_to_go_s` as the plan's total at `pos` (`max_hops=0` included).
- A left/right landing x moves in the move's own direction (`Key.RIGHT` adds, `Key.LEFT` subtracts).
- Two surface pieces equally near `py`: the first that `_surface_ys` lists.
- Both ends of the aim node's platform equally near the landing x: the platform's first (left) end.
- The from-anchor's platform is left out of the landing order when the anchor has none (`attach` always sets one).
- An up/down landing with no surface at the launch x either (`_launch_x` never gives such an x) is `h` itself.
- An aim node missing from `path` (never so today) counts as at index 0: no earlier path platform is tried.
- The stub generators are two classes, one per value, each defining `random()` and nothing else.
- V4's past-end landing (the end node's y) is a stated assumption: 2 of the FF2 sweep's 7 would fall (RV3-D5 notes).
"""

from dataclasses import dataclass
from typing import cast

import numpy as np
from returns.pipeline import is_successful
from returns.result import Failure, Result, Success

from src.controller.node import Node
from src.input.protocol import Key
from src.route.locate import attach
from src.route.planner import (
    Move,
    MoveKind,
    _edge_kind,
    _go_down,
    _is_at,
    _launch_x,
    _offered,
    _on_slope_segment,
    _run,
    _starts,
    _surface_ys,
    _walk,
    _walk_run_end,
)
from src.route.route_graph import EdgeKind, RouteGraph


@dataclass(frozen=True)
class StartOption:
    node: Node
    cost_s: float  # approach + rg.cost_to(node, target) on the offered graph (planner.py:206-210)


@dataclass(frozen=True)
class Hop:
    options: tuple[Move, ...]  # one Move; two for a coin-flip down: (Move(DROP), Move(TELEPORT, DOWN)), in that order
    toward: Node  # Decision.toward for this move
    from_pos: tuple[int, int]  # the position this hop is planned from (pos, or the previous hop's landing)
    landing: tuple[int, int]  # the position the preview assumes after it (the landing rule)
    cost_to_go_s: float  # the planner's total from from_pos (bit-equal to next_move's)


@dataclass(frozen=True)
class Preview:
    pos: tuple[int, int]
    target: Node
    arrived: bool  # P1 held at pos: no hops, no starts, start None, cost 0.0, path ()
    starts: tuple[StartOption, ...]  # every candidate start with a path, left before right (_starts order)
    start: Node | None  # the chosen start (min cost, ties left before right)
    cost_to_go_s: float  # hops[0].cost_to_go_s, or 0.0 when arrived
    path: tuple[Node, ...]  # rg.path(start, target) on the offered graph, from pos only
    hops: tuple[Hop, ...]  # hops[0] from pos; hops[k] from hops[k-1].landing
    complete: bool  # the last landing is arrived (P1); False when max_hops stopped it or a plan failed


class _DrawLow:
    """Stands in for `np.random.Generator` in `_go_down`: `random()` only, so any other draw raises AttributeError."""

    def random(self) -> float:
        return 0.0


class _DrawHigh:
    """Stands in for `np.random.Generator` in `_go_down`: `random()` only, so any other draw raises AttributeError."""

    def random(self) -> float:
        return 0.99


_LOW = cast(np.random.Generator, _DrawLow())  # a coin flip gives "drop" (planner.py:176)
_HIGH = cast(np.random.Generator, _DrawHigh())  # a coin flip gives "teleport"


@dataclass(frozen=True)
class _Across:
    """A left/right move's aim: the goal x the planner used, and the node whose platform holds it."""

    x: int
    node: Node


@dataclass(frozen=True)
class _Edge:
    """An up or down move's aim: the edge g -> h it makes."""

    g: Node
    h: Node


@dataclass(frozen=True)
class _Plan:
    """What `next_move` decides at one position, with what the preview needs besides: one option, or the two of a
    coin flip; `toward` and `total` as the Decision's; the starts, the chosen start, the path; the move's aim; and the
    platform `attach` gave the position."""

    options: tuple[Move, ...]
    toward: Node
    total: float
    starts: tuple[StartOption, ...]
    start: Node
    path: tuple[Node, ...]
    aim: _Across | _Edge
    platform: int | None


def _plan(rg: RouteGraph, pos: tuple[int, int], target: Node) -> Result[_Plan | None, str]:
    """`next_move` at `pos` (`planner.py:190-247`, statement for statement), or None when arrived (P1)."""
    if target not in rg.graph:
        return Failure(f"target {target.minimap_position!r} is not in the route graph")
    px, py = pos
    anchor_result = attach(rg, pos)
    anchor = anchor_result.value_or(None)

    # P1
    if _is_at(rg, pos, target, anchor):
        return Success(None)
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
    starts = tuple(StartOption(node, cost_s) for cost_s, node in candidates)
    platform = anchor.platform

    def planned(options: tuple[Move, ...], toward: Node, aim: _Across | _Edge) -> Result[_Plan | None, str]:
        return Success(_Plan(options, toward, total, starts, start, path, aim, platform))

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
                return planned((move,), h, _Across(launch, g))
    if launch is None and not _is_at(rg, pos, g, anchor):
        move = _walk(rg, px, g.minimap_position[0]) if on_slope else _run(rg, px, g.minimap_position[0])
        return planned((move,), g, _Across(g.minimap_position[0], g))

    # P5: at g, or lined up on the launch x of g's UP/DOWN edge (g is not the target, or P1 would have applied)
    h = path[gi + 1]
    kind = _edge_kind(rg, g, h)
    if kind == EdgeKind.SLOPE:
        return planned((_walk(rg, px, h.minimap_position[0]),), h, _Across(h.minimap_position[0], h))
    if kind == EdgeKind.WALK:  # only when P4 kept g at the start of a slope segment
        g_next = path[_walk_run_end(rg, path, gi + 1)]
        return planned((_run(rg, px, g_next.minimap_position[0]),), g_next, _Across(g_next.minimap_position[0], g_next))
    if kind == EdgeKind.UP:
        return planned((Move(MoveKind.TELEPORT, Key.UP, None),), h, _Edge(g, h))
    # the coin flip without a draw: equal when `_go_down` takes no draw, (DROP, TELEPORT DOWN) when it would toss
    low, high = _go_down(rg, g, h, _LOW), _go_down(rg, g, h, _HIGH)
    return planned((low,) if low == high else (low, high), h, _Edge(g, h))


def _surface_y(rg: RouteGraph, platform: int, x: int, py: int) -> int | None:
    """The surface of platform `platform` at `x`: the piece nearest `py`, rounded; None when it has none at `x`."""
    ys = _surface_ys(rg.terrain.platforms[platform], x)
    if not ys:
        return None
    return round(min(ys, key=lambda y: abs(y - py)))


def _landing_horizontal(
    rg: RouteGraph, from_pos: tuple[int, int], move: Move, aim: _Across, path: tuple[Node, ...], platform: int | None
) -> tuple[int, int]:
    """V4's landing for a WALK or TELEPORT left or right from `from_pos`, aimed at `aim.x` on `aim.node`'s platform;
    `platform` is the from-anchor's platform."""
    px, py = from_pos
    p = rg.params
    sign = 1 if move.direction == Key.RIGHT else -1
    if move.kind == MoveKind.TELEPORT:
        lx = px + sign * rg.reach_h
    elif 1000 * abs(aim.x - px) / p.walk_px_per_s <= p.max_walk_ms:
        lx = aim.x
    else:
        lx = px + sign * round(p.max_walk_ms * p.walk_px_per_s / 1000)
    an = aim.node
    if lx == an.minimap_position[0]:
        return an.minimap_position
    terrain = rg.terrain
    ai = next((i for i, node in enumerate(path) if node is an), 0)
    order = [terrain.platform_index(an), *(terrain.platform_index(node) for node in reversed(path[:ai]))]
    if platform is not None:
        order.append(platform)
    for index in order:
        y = _surface_y(rg, index, lx, py)
        if y is not None:
            return (lx, y)
    own = terrain.platforms[terrain.platform_index(an)]
    end = min((own[0], own[-1]), key=lambda node: abs(node.minimap_position[0] - lx))
    return (lx, end.minimap_position[1])


def _landing_vertical(rg: RouteGraph, from_pos: tuple[int, int], aim: _Edge) -> tuple[int, int]:
    """V4's landing for a TELEPORT UP, TELEPORT DOWN or DROP on the edge g -> h, made from `from_pos`."""
    px, py = from_pos
    h = aim.h
    own = rg.terrain.platform_index(h)
    for x in (px, _launch_x(rg, aim.g, h)):
        if x is None:
            continue
        if x == h.minimap_position[0]:
            return h.minimap_position
        y = _surface_y(rg, own, x, py)
        if y is not None:
            return (x, y)
    return h.minimap_position


def preview(rg: RouteGraph, pos: tuple[int, int], target: Node, max_hops: int = 64) -> Result[Preview, str]:
    """The planner's route from `pos` to `target` and the move on each hop (V4).

    `Failure` exactly when `next_move` fails, with its message (target not in the graph, off-graph, no path). Takes
    no generator and draws nothing. The expansion re-plans at each landing until a plan is arrived
    (`complete=True`), a plan fails or `max_hops` hops exist (`complete=False`; the hops so far are kept).
    """
    first = _plan(rg, pos, target)
    if not is_successful(first):
        return Failure(first.failure())
    plan = first.unwrap()
    if plan is None:
        return Success(Preview(pos, target, True, (), None, 0.0, (), (), True))
    hops: list[Hop] = []
    from_pos, step = pos, plan
    complete = False
    while len(hops) < max_hops:
        aim = step.aim
        if isinstance(aim, _Edge):
            landing = _landing_vertical(rg, from_pos, aim)
        else:
            landing = _landing_horizontal(rg, from_pos, step.options[0], aim, step.path, step.platform)
        hops.append(Hop(step.options, step.toward, from_pos, landing, step.total))
        result = _plan(rg, landing, target)
        if not is_successful(result):
            break
        following = result.unwrap()
        if following is None:
            complete = True
            break
        from_pos, step = landing, following
    return Success(
        Preview(
            pos=pos,
            target=target,
            arrived=False,
            starts=plan.starts,
            start=plan.start,
            cost_to_go_s=plan.total,
            path=plan.path,
            hops=tuple(hops),
            complete=complete,
        )
    )
