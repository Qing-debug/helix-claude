"""Tests for src.route.route_graph (plan contract C5): FF2 graph shape, edge kinds, costs, paths and failures."""

import re
from pathlib import Path

import networkx as nx
import pytest
from returns.pipeline import is_successful

from src.classes.classes import Kanna
from src.controller.controller import MapleAgent
from src.controller.node import Node
from src.controller.path_graph import PathGraph
from src.route.params import MovementParams
from src.route.route_graph import EdgeKind, RouteGraph, down_options, horizontal_cost_s

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "fossil_foundry_2.csv"
ROUTE_GRAPH_SRC = Path(__file__).resolve().parents[2] / "src" / "route" / "route_graph.py"

REACH_H = 19
P = MovementParams()

ADDED_EDGES = [
    ((66, 28), (79, 24), EdgeKind.SLOPE), ((79, 24), (66, 28), EdgeKind.SLOPE),
    ((113, 60), (118, 43), EdgeKind.UP), ((101, 24), (105, 42), EdgeKind.DOWN),
    ((105, 42), (101, 24), EdgeKind.UP), ((74, 45), (79, 24), EdgeKind.UP),
    ((42, 28), (37, 60), EdgeKind.DOWN), ((79, 24), (74, 45), EdgeKind.DOWN),
    ((109, 23), (105, 42), EdgeKind.DOWN), ((118, 43), (113, 60), EdgeKind.DOWN),
    ((97, 43), (101, 24), EdgeKind.UP),
]


@pytest.fixture(scope="module")
def ff2():
    agent = MapleAgent(characterClass=Kanna())
    assert is_successful(agent.loadMap(FIXTURE))
    result = RouteGraph.build(agent.pathGraph, MovementParams())
    assert is_successful(result)
    return agent, result.unwrap()


def _node(agent, xy):
    return next(n for n in agent.node_map.values() if n.minimap_position == xy)


def _pos(u, v):
    return u.minimap_position, v.minimap_position


def _expected_path_graph_kind(u, v):
    dx = abs(v.minimap_position[0] - u.minimap_position[0])
    dy = v.minimap_position[1] - u.minimap_position[1]
    if dx >= abs(dy):
        return EdgeKind.WALK
    return EdgeKind.UP if dy < 0 else EdgeKind.DOWN


def test_ff2_builds_with_21_nodes_51_edges_strongly_connected(ff2):
    _, rg = ff2
    assert rg.graph.number_of_nodes() == 21
    assert rg.graph.number_of_edges() == 51
    assert nx.is_strongly_connected(rg.graph)


def test_ff2_all_path_graph_edges_present_with_their_kinds(ff2):
    agent, rg = ff2
    path_edges = {(u, v) for u, v in agent.pathGraph.graph.edges()}
    assert len(path_edges) == 40
    for u, v in path_edges:
        assert rg.graph.has_edge(u, v), _pos(u, v)
        assert rg.graph[u][v]["kind"] == _expected_path_graph_kind(u, v), _pos(u, v)


def test_ff2_added_edges_are_the_two_slopes_and_nine_vertical_links(ff2):
    agent, rg = ff2
    path_edges = {(u, v) for u, v in agent.pathGraph.graph.edges()}
    added = [(u, v) for u, v in rg.graph.edges() if (u, v) not in path_edges]
    assert len(added) == 11
    assert sorted((*_pos(u, v), rg.graph[u][v]["kind"]) for u, v in added) == sorted(ADDED_EDGES)


def test_ff2_slope_crossing_path_exists(ff2):
    agent, rg = ff2
    result = rg.path(_node(agent, (49, 28)), _node(agent, (101, 24)))
    assert is_successful(result)
    nodes = result.unwrap()
    slope = (_node(agent, (66, 28)), _node(agent, (79, 24)))
    steps = list(zip(nodes, nodes[1:]))
    assert slope in steps
    assert rg.graph[slope[0]][slope[1]]["kind"] == EdgeKind.SLOPE


def test_ff2_down_edge_from_101_24_to_105_42_costs_teleport_plus_walk(ff2):
    agent, rg = ff2
    src, dst = _node(agent, (101, 24)), _node(agent, (105, 42))
    result = rg.path(src, dst)
    assert is_successful(result)
    assert result.unwrap() == (src, dst)
    assert rg.graph[src][dst]["kind"] == EdgeKind.DOWN
    assert rg.graph[src][dst]["cost_s"] == pytest.approx(1.05)
    cost = rg.cost_to(src, dst)
    assert is_successful(cost) and cost.unwrap() == pytest.approx(1.05)


def test_ff2_every_rotation_leg_has_a_path(ff2):
    agent, rg = ff2
    order = agent.map_rotation_order
    assert len(order) == 7
    for a, b in zip(order, order[1:] + order[:1]):
        assert is_successful(rg.path(a, b)), _pos(a, b)


def test_cost_to_self_is_zero_and_path_is_the_node(ff2):
    agent, rg = ff2
    n = _node(agent, (82, 60))
    assert rg.path(n, n).unwrap() == (n,)
    assert rg.cost_to(n, n).unwrap() == 0


@pytest.mark.parametrize("d, expected", [
    (4, 0.5), (13, 1.3), (17, 0.8), (19, 0.55), (23, 1.05), (30, 1.925), (40, 1.35),
])
def test_horizontal_cost_s_matches_the_contract_values(d, expected):
    assert horizontal_cost_s(d, REACH_H, P) == pytest.approx(expected, abs=1e-9)


def test_down_options_teleport_and_drop():
    opts = down_options(18, True, 23, P)
    assert set(opts) == {"teleport", "drop"}
    assert opts["teleport"] == pytest.approx(0.55)
    assert opts["drop"] == pytest.approx(0.65)


def test_down_options_beyond_reach_without_fall_is_empty():
    assert down_options(30, False, 23, P) == {}


def test_down_options_beyond_reach_with_fall_is_drop_only():
    opts = down_options(30, True, 23, P)
    assert set(opts) == {"drop"}
    assert opts["drop"] == pytest.approx(0.85)


def test_build_fails_when_the_path_graph_was_not_built():
    nodes = {i: Node("blue_node", (10 * i, 10), False, False, False, False) for i in range(3)}
    pg = PathGraph(nodes, 2, 2, REACH_H, 23)
    assert not is_successful(RouteGraph.build(pg, MovementParams()))


def test_build_fails_when_node_map_and_graph_nodes_differ(ff2):
    agent, _ = ff2
    pg = PathGraph(dict(agent.node_map), 2, 2, REACH_H, 23)
    assert is_successful(pg.buildGraph())
    pg.node_map.pop(next(iter(pg.node_map)))
    assert not is_successful(RouteGraph.build(pg, MovementParams()))


def test_path_and_cost_to_fail_for_a_node_not_in_the_graph(ff2):
    agent, rg = ff2
    stranger = Node("blue_node", (1, 1), False, False, False, False)
    known = _node(agent, (82, 60))
    for result in (rg.path(known, stranger), rg.path(stranger, known),
                   rg.cost_to(known, stranger), rg.cost_to(stranger, known)):
        assert not is_successful(result)


def test_path_fails_when_there_is_no_route(ff2):
    agent, rg = ff2
    nodes = {i: Node("blue_node", (10 * i, 10), False, False, False, False) for i in range(2)}
    nodes[2] = Node("blue_node", (500, 500), False, False, False, False)
    pg = PathGraph(nodes, 2, 2, REACH_H, 23)
    assert is_successful(pg.buildGraph())
    isolated = RouteGraph.build(pg, MovementParams()).unwrap()
    result = isolated.path(nodes[0], nodes[2])
    assert not is_successful(result)
    assert "no path" in result.failure()


def test_route_graph_source_has_no_19_or_23_literal():
    source = ROUTE_GRAPH_SRC.read_text(encoding="utf-8")
    assert re.search(r"(?<![\w.])(19|23)(?![\w.])", source) is None
