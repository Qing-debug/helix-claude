"""Typed, costed route graph over PathGraph plus inferred terrain (plan contract C5).

Every PathGraph edge is kept and typed; the terrain links PathGraph lacks (slopes, near-aligned vertical moves)
are added. Each edge carries a `kind` and an estimated `cost_s` in seconds, so a shortest path is the fastest
route under the movement parameters.
"""

import math
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


_TERRAIN_KIND = {
    LinkKind.LEVEL: EdgeKind.WALK,
    LinkKind.SLOPE: EdgeKind.SLOPE,
    LinkKind.UP: EdgeKind.UP,
    LinkKind.DOWN: EdgeKind.DOWN,
}


def horizontal_cost_s(dx_px: float, reach_h: int, p: MovementParams) -> float:
    """Seconds to cover `dx_px` horizontally: the cheapest of walking all of it, teleporting down then walking the
    remainder, or overshooting with one more teleport and walking back."""
    d = abs(dx_px)
    n = math.floor(d / reach_h)
    r = d - n * reach_h
    v, t = p.walk_px_per_s, p.teleport_s
    return min(d / v, n * t + r / v, (n + 1) * t + ((n + 1) * reach_h - d) / v)


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

    def __init__(
        self,
        graph: nx.DiGraph,
        terrain: Terrain,
        align_px: int,
        reach_h: int,
        reach_v: int,
        params: MovementParams,
    ) -> None:
        self.graph = graph
        self.terrain = terrain
        self.align_px = align_px
        self.reach_h = reach_h
        self.reach_v = reach_v
        self.params = params

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

        graph = nx.DiGraph()
        graph.add_nodes_from(nodes)
        rg = cls(graph, terrain, align_px, reach_h, reach_v, params)
        for u, v in path_graph.graph.edges():
            if graph.has_edge(u, v):
                continue
            dx = abs(v.minimap_position[0] - u.minimap_position[0])
            dy = v.minimap_position[1] - u.minimap_position[1]
            if dx >= abs(dy):
                kind = EdgeKind.WALK
            else:
                kind = EdgeKind.UP if dy < 0 else EdgeKind.DOWN
            rg._add_edge(u, v, kind)
        for link in terrain.links:
            if not graph.has_edge(link.src, link.dst):
                rg._add_edge(link.src, link.dst, _TERRAIN_KIND[link.kind])
        return Success(rg)

    def _edge_cost(self, u: Node, v: Node, kind: EdgeKind) -> float | None:
        """Seconds for the move u -> v, or None when no ability can make it."""
        p = self.params
        dx = abs(v.minimap_position[0] - u.minimap_position[0])
        dy = abs(v.minimap_position[1] - u.minimap_position[1])
        if kind == EdgeKind.WALK:
            return horizontal_cost_s(dx, self.reach_h, p)
        if kind == EdgeKind.SLOPE:
            return dx / p.walk_px_per_s
        if kind == EdgeKind.UP:
            return p.teleport_s + dx / p.walk_px_per_s
        options = down_options(dy, bool(u.can_fall), self.reach_v, p)
        if not options:
            return None
        return min(options.values()) + dx / p.walk_px_per_s

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
