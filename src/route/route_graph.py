"""Typed, costed route graph over PathGraph plus inferred terrain (plan contract C5).

Every PathGraph edge is kept and typed; the terrain links PathGraph lacks (slopes, near-aligned vertical moves)
are added. Each edge carries a `kind` and an estimated `cost_s` in seconds, so a shortest path is the fastest
route under the movement parameters.
"""

from enum import StrEnum

import networkx as nx
from returns.pipeline import is_successful
from returns.result import Failure, Result, Success

from src.controller.node import Node
from src.controller.path_graph import PathGraph
from src.route.params import MovementParams
from src.route.terrain import LinkKind, Terrain, infer_terrain


class EdgeKind(StrEnum):
    WALK = "walk"
    SLOPE = "slope"
    UP = "up"
    DOWN = "down"
    GAP = "gap"  # a sideways teleport from one platform onto another, across a platform-end mark (QB282)


_TERRAIN_KIND = {
    LinkKind.LEVEL: EdgeKind.WALK,
    LinkKind.SLOPE: EdgeKind.SLOPE,
    LinkKind.UP: EdgeKind.UP,
    LinkKind.DOWN: EdgeKind.DOWN,
}


def is_marked_end(node: Node, sign: int) -> bool:
    """The map marks `node` as the end of its platform toward `sign` (+1 right, otherwise left). A mark that is None
    (a `Node` built without the argument) is no mark."""
    return bool(node.is_right_platform_edge) if sign > 0 else bool(node.is_left_platform_edge)


def horizontal_cost_s(dx_px: float, reach_h: int, p: MovementParams, *, to_end: bool = False) -> float:
    """Seconds to cover `dx_px` horizontally, hop by hop as the planner's `_run` moves: a distance under
    `walk_under_px` (QB266) is walked; otherwise one teleport is taken when it plus the walk of what is left beats
    walking all of it, and the rule applies again from where it lands. A teleport that overshoots is walked back from,
    never preferred to walking a short distance, so there is no "overshoot and walk back" option below the limit.

    `to_end` (QB282): the run ends on a platform end that stops a teleport, so the last hop, when what is left is
    within `reach_h`, is a teleport that stops there, even under `walk_under_px`. Callers pass `to_end` only when a
    teleport from the start x stops on the own platform (`RouteGraph.side_landing`), which is exact for a single hop
    (`dx_px <= reach_h`); for a longer run, whether the last hop would carry over is not priced (the planner's move
    choice is exact, the graph cost stays a heuristic, as it always was across a walk run).

    The price is of one edge, so it cannot see the run the planner decides over (`planner._walk_run_end`, T1R m2):
    two 15 px WALK edges in a row cost 2 x 1.20 s here, while the planner teleports once and walks the rest of the 30 px
    (0.55 s + 0.88 s, 1.43 s). The same edge would have to cost 1.20 s as a run of one and about 0.72 s in a run of two,
    so no price of the edge matches both; the cost of a run of walk edges can be above or below what the planner plays
    (above for edges under `walk_under_px`; below for e.g. two 17 px edges: 1.42 s here, 1.75 s played)."""
    d, total = abs(dx_px), 0.0
    v, t = p.walk_px_per_s, p.teleport_s
    while d > 0:
        if to_end and d <= reach_h:
            total += t
            d = 0.0
            break
        if d >= p.walk_under_px and t + abs(d - reach_h) / v < d / v:
            total += t
            d = abs(d - reach_h)
        else:
            break
    return total + d / v


def _sign(dx: int) -> int:
    return (dx > 0) - (dx < 0)


def _side_kind(terrain: Terrain, u: Node, v: Node) -> EdgeKind:
    """The kind of a side edge u -> v: GAP when `u` and `v` are on different terrain platforms and the end of `u`'s
    platform toward `v`, or the start of `v`'s platform, is a marked end; otherwise WALK, as before the marks. An edge
    between two platforms with no mark at either facing end stays WALK (review I4): terrain links only the nearest
    walkable node on each side and PathGraph chooses its edges by its own rule, so such an edge can exist."""
    sign = _sign(v.minimap_position[0] - u.minimap_position[0])
    pu, pv = terrain.platform_index(u), terrain.platform_index(v)
    if sign == 0 or pu == pv:
        return EdgeKind.WALK
    end_of_u = terrain.platforms[pu][-1 if sign > 0 else 0]
    start_of_v = terrain.platforms[pv][0 if sign > 0 else -1]
    if is_marked_end(end_of_u, sign) or is_marked_end(start_of_v, -sign):
        return EdgeKind.GAP
    return EdgeKind.WALK


def down_options(dy_px: float, src_can_fall: bool, reach_v: int, p: MovementParams) -> dict[str, float]:
    """Ways to go down `dy_px`, name -> seconds. A teleport needs the drop within vertical reach; a drop needs the
    source node to allow falling. May be empty."""
    options: dict[str, float] = {}
    if dy_px <= reach_v:
        options["teleport"] = p.teleport_s
    if src_can_fall:
        options["drop"] = p.drop_base_s + dy_px / p.fall_px_per_s
    return options


class RouteGraph:
    graph: nx.DiGraph
    terrain: Terrain
    align_px: int
    reach_h: int
    reach_v: int
    params: MovementParams
    split_ends: frozenset[tuple[int, int]]

    def __init__(
        self,
        graph: nx.DiGraph,
        terrain: Terrain,
        align_px: int,
        reach_h: int,
        reach_v: int,
        params: MovementParams,
        split_ends: frozenset[tuple[int, int]] = frozenset(),
    ) -> None:
        self.graph = graph
        self.terrain = terrain
        self.align_px = align_px
        self.reach_h = reach_h
        self.reach_v = reach_v
        self.params = params
        # (platform index, sign) for both ends of every gap a mark types GAP (re-review N1), collected before pricing,
        # so also the ends of a GAP edge that `_edge_cost` then drops for having no launch (T2R m2)
        self.split_ends = split_ends

    @classmethod
    def build(cls, path_graph: PathGraph, params: MovementParams) -> Result["RouteGraph", str]:
        if path_graph.graph.number_of_nodes() == 0:
            return Failure("path graph has no nodes; call buildGraph() first")
        nodes = list(path_graph.node_map.values())
        if set(path_graph.graph.nodes()) != set(nodes):
            return Failure("path graph nodes do not match its node_map")

        align_px = path_graph.edge_y_alignment_allowance
        reach_h = path_graph.max_horizontal_traversal_dist
        reach_v = path_graph.max_vertical_traversal_dist
        terrain_result = infer_terrain(nodes, align_px, reach_h, reach_v, params)
        if not is_successful(terrain_result):
            return Failure(f"terrain inference failed: {terrain_result.failure()}")
        terrain = terrain_result.unwrap()

        # a first pass types the PathGraph edges and collects both platform ends of every gap typed GAP (an edge that
        # pricing later drops for having no launch point still counts: the ends stay stops, T2R m2); the graph is
        # built with them, and only then are the edges priced, because the GAP and WALK costs call `side_landing`
        typed: list[tuple[Node, Node, EdgeKind]] = []
        seen: set[tuple[Node, Node]] = set()
        split: set[tuple[int, int]] = set()
        for u, v in path_graph.graph.edges():
            if (u, v) in seen:
                continue
            seen.add((u, v))
            dx = abs(v.minimap_position[0] - u.minimap_position[0])
            dy = v.minimap_position[1] - u.minimap_position[1]
            if dx >= abs(dy):
                kind = _side_kind(terrain, u, v)
                if kind == EdgeKind.GAP:
                    sign = _sign(v.minimap_position[0] - u.minimap_position[0])
                    split.add((terrain.platform_index(u), sign))
                    split.add((terrain.platform_index(v), -sign))
            else:
                kind = EdgeKind.UP if dy < 0 else EdgeKind.DOWN
            typed.append((u, v, kind))

        graph = nx.DiGraph()
        graph.add_nodes_from(nodes)
        rg = cls(graph, terrain, align_px, reach_h, reach_v, params, frozenset(split))
        for u, v, kind in typed:
            rg._add_edge(u, v, kind)
        for link in terrain.links:
            if not graph.has_edge(link.src, link.dst):
                rg._add_edge(link.src, link.dst, _TERRAIN_KIND[link.kind])
        return Success(rg)

    def is_stop_end(self, node: Node, sign: int) -> bool:
        """`node` is the end of its terrain platform toward `sign` and that end stops a sideways teleport: it is marked
        (`is_marked_end`) or it is an end of a gap a mark types GAP (`split_ends`, so the unmarked end of a gap whose
        other side is marked counts too, re-review N1; also when no teleport launch lands across that gap, T2R m2:
        the teleport still stops at the end of the platform it is on). The one test of "a platform's end" that
        `side_landing`, the WALK cost's `to_end` and the planner use."""
        p = self.terrain.platform_index(node)
        platform = self.terrain.platforms[p]
        if node is not (platform[-1] if sign > 0 else platform[0]):
            return False
        return is_marked_end(node, sign) or (p, sign) in self.split_ends

    def side_landing(self, platform: int, px: int, sign: int) -> tuple[int, int]:
        """`(x, platform index)` where a sideways teleport from `px` on `platform` toward `sign` (+1 right, -1 left)
        stops (Hong, QB282): it goes the full reach, unless the end of the platform toward `sign` is a stop
        (`is_stop_end`) that the full reach passes. Then it stops at that end, or, when another platform starts within
        the reach in that direction and within `side_teleport_land_dy_px` of the end's height, it lands on that
        platform (the full reach, clamped into that platform's span); with several the nearest to `px` wins, a tie goes
        to the lower platform index. A much lower platform does not count: the teleport stops at the edge."""
        nodes = self.terrain.platforms[platform]
        end = nodes[-1] if sign > 0 else nodes[0]
        end_x, end_y = end.minimap_position
        full = px + sign * self.reach_h
        if not self.is_stop_end(end, sign) or sign * (full - end_x) <= 0:
            return full, platform
        best: tuple[int, int] | None = None  # (distance from px, platform index)
        for index, other in enumerate(self.terrain.platforms):
            if index == platform:
                continue
            near_x, near_y = (other[0] if sign > 0 else other[-1]).minimap_position
            distance = sign * (near_x - px)
            if (
                sign * (near_x - end_x) > 0
                and 0 < distance <= self.reach_h
                and abs(near_y - end_y) <= self.params.side_teleport_land_dy_px
                and (best is None or distance < best[0])
            ):
                best = (distance, index)
        if best is None:
            return end_x, platform
        landing = self.terrain.platforms[best[1]]
        return min(max(full, landing[0].minimap_position[0]), landing[-1].minimap_position[0]), best[1]

    def gap_launch(self, u: Node, v: Node) -> int | None:
        """The x on `u`'s platform to teleport from so that the teleport lands on `v`'s platform, or None when none does.

        This is the one place where the launch-point question (WALKFIX169-D1, QB284) is answered: Hong's option A2
        (walkfix170-1, "A with a margin: line up 3 px nearer the gap, so the teleport lands on the node or up to 5 px
        past it"). The launch is `v.x - sign * (reach_h - margin)`, with margin = `arrive_px` (the planner counts the
        character lined up within it) + `reach_h` - `shortest_side_teleport_px` (the shortest recorded teleport),
        3 px for Kanna (WALKFIX172-D1). When that x lies past the end of `u`'s platform toward `v` (`v` more than
        `reach_h - margin` px past that end), the margin cannot be had and the launch is exact A's, `v.x - sign *
        reach_h`, as before A2 (gaps that wide wait on Hong, ANYMAP181-D2). The target is clamped into `u`'s platform
        and checked with `side_landing`."""
        sign = _sign(v.minimap_position[0] - u.minimap_position[0])
        platform = self.terrain.platform_index(u)
        nodes = self.terrain.platforms[platform]
        first_x, last_x = nodes[0].minimap_position[0], nodes[-1].minimap_position[0]
        margin = self.params.arrive_px + self.reach_h - self.params.shortest_side_teleport_px
        target = v.minimap_position[0] - sign * (self.reach_h - margin)  # A2
        if sign * (target - (last_x if sign > 0 else first_x)) > 0:
            target = v.minimap_position[0] - sign * self.reach_h  # exact A, past the end
        target = min(max(target, first_x), last_x)
        if self.side_landing(platform, target, sign)[1] == self.terrain.platform_index(v):
            return target
        return None

    def _edge_cost(self, u: Node, v: Node, kind: EdgeKind) -> float | None:
        """Seconds for the move u -> v, or None when no ability can make it."""
        p = self.params
        dx = abs(v.minimap_position[0] - u.minimap_position[0])
        dy = abs(v.minimap_position[1] - u.minimap_position[1])
        if kind == EdgeKind.GAP:
            launch = self.gap_launch(u, v)
            if launch is None:
                return None
            sign = _sign(v.minimap_position[0] - u.minimap_position[0])
            landing_x = self.side_landing(self.terrain.platform_index(u), launch, sign)[0]
            walked = abs(u.minimap_position[0] - launch) + abs(landing_x - v.minimap_position[0])
            return walked / p.walk_px_per_s + p.teleport_s
        if kind == EdgeKind.WALK:
            sign = _sign(v.minimap_position[0] - u.minimap_position[0])
            platform = self.terrain.platform_index(u)
            # review m1: a teleport from `u` that would carry onto the next platform is not a stop on the end
            to_end = (
                sign != 0
                and self.terrain.platform_index(v) == platform  # T2R m1: a walk onto another platform stops on no end
                and self.is_stop_end(v, sign)
                and self.side_landing(platform, u.minimap_position[0], sign)[1] == platform
            )
            return horizontal_cost_s(dx, self.reach_h, p, to_end=to_end)
        if kind == EdgeKind.SLOPE:
            return dx / p.walk_px_per_s
        if kind == EdgeKind.UP:
            return p.teleport_s + dx / p.walk_px_per_s
        options = down_options(dy, bool(u.can_fall), self.reach_v, p)  # H87: the teleport's cost when it reaches
        if "teleport" in options:
            return options["teleport"] + dx / p.walk_px_per_s
        if "drop" in options:
            return options["drop"] + dx / p.walk_px_per_s
        return None

    def _add_edge(self, u: Node, v: Node, kind: EdgeKind) -> None:
        cost = self._edge_cost(u, v, kind)
        if cost is not None:
            self.graph.add_edge(u, v, kind=kind, cost_s=cost)

    def _check_nodes(self, src: Node, dst: Node) -> str | None:
        for name, node in (("src", src), ("dst", dst)):
            if node not in self.graph:
                return f"{name} node {getattr(node, 'minimap_position', node)!r} is not in the route graph"
        return None

    def path(self, src: Node, dst: Node) -> Result[tuple[Node, ...], str]:
        """Minimum total `cost_s` path, including `src` and `dst`."""
        problem = self._check_nodes(src, dst)
        if problem is not None:
            return Failure(problem)
        try:
            return Success(tuple(nx.shortest_path(self.graph, src, dst, weight="cost_s")))
        except nx.NetworkXNoPath:
            return Failure(f"no path from {src.minimap_position} to {dst.minimap_position}")

    def cost_to(self, src: Node, dst: Node) -> Result[float, str]:
        """Total `cost_s` of the minimum-cost path from `src` to `dst`."""
        problem = self._check_nodes(src, dst)
        if problem is not None:
            return Failure(problem)
        try:
            return Success(float(nx.shortest_path_length(self.graph, src, dst, weight="cost_s")))
        except nx.NetworkXNoPath:
            return Failure(f"no path from {src.minimap_position} to {dst.minimap_position}")
