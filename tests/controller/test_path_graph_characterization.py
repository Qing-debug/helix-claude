"""Characterization tests for PathGraph: pins today's edges on the FF2 fixture, today's edge rules on
small synthetic graphs, and the two known FF2 rotation legs that have no path. A failure here means the
behaviour changed, not that the test is wrong."""
from pathlib import Path

import networkx as nx
import pytest
from returns.pipeline import is_successful
from returns.result import Success

from src.classes.classes import Kanna
from src.controller.controller import BuildOutcome, MapleAgent
from src.controller.node import Node
from src.controller.path_graph import Path as GraphPath
from src.controller.path_graph import PathGraph

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "fossil_foundry_2.csv"

# The 40 edges the unchanged PathGraph builds for FF2 with Kanna (19 px horizontal reach, 23 px vertical
# reach, allowances 2/2), as (source position, target position).
EXPECTED_FF2_EDGES = [
    ((101, 24), (109, 23)), ((101, 24), (90, 24)), ((105, 42), (105, 60)), ((105, 42), (118, 43)),
    ((105, 42), (97, 43)), ((105, 60), (105, 42)), ((105, 60), (113, 60)), ((105, 60), (99, 60)),
    ((109, 23), (101, 24)), ((113, 60), (105, 60)), ((113, 60), (129, 60)), ((118, 43), (105, 42)),
    ((129, 60), (113, 60)), ((37, 60), (48, 61)), ((42, 28), (49, 28)), ((48, 45), (48, 61)),
    ((48, 45), (49, 28)), ((48, 45), (58, 46)), ((48, 61), (37, 60)), ((48, 61), (48, 45)),
    ((48, 61), (65, 60)), ((49, 28), (42, 28)), ((49, 28), (48, 45)), ((49, 28), (66, 28)),
    ((58, 46), (48, 45)), ((58, 46), (74, 45)), ((65, 60), (48, 61)), ((65, 60), (82, 60)),
    ((66, 28), (49, 28)), ((74, 45), (58, 46)), ((79, 24), (90, 24)), ((82, 60), (65, 60)),
    ((82, 60), (99, 60)), ((90, 24), (101, 24)), ((90, 24), (79, 24)), ((97, 43), (105, 42)),
    ((97, 43), (99, 60)), ((99, 60), (105, 60)), ((99, 60), (82, 60)), ((99, 60), (97, 43)),
]

# The FF2 rotation is a cycle of 7 legs (the last blue node leads back to the first). Today two of them have
# no path in the graph; every other leg has one.
NO_PATH_LEGS = {((49, 28), (101, 24)), ((101, 24), (105, 42))}


@pytest.fixture(scope="module")
def ff2():
    # One MapleAgent shared by every test in this module (saves rebuilding FF2): tests must only read it.
    # A test that mutates the agent (e.g. connectCharacterNode) needs its own agent.
    agent = MapleAgent(characterClass=Kanna())
    assert agent.loadMap(FIXTURE) == Success(BuildOutcome.BUILT)
    by_pos = {n.minimap_position: n for n in agent.node_map.values()}
    assert len(by_pos) == 21
    return agent, by_pos


def _position_edges(graph):
    return [(u.minimap_position, v.minimap_position) for u, v in graph.edges()]


def _rotation_legs(agent):
    rotation = [n.minimap_position for n in agent.map_rotation_order]
    return list(zip(rotation, rotation[1:] + rotation[:1]))


def _node(x, y, left_edge=False, right_edge=False):
    return Node(nodetype="blue_node", minimap_pos=(x, y), can_fall=False, fall_dist_exceeds_vertical_tp=False,
                is_left_platform_edge=left_edge, is_right_platform_edge=right_edge)


def _build(*nodes):
    """Build a PathGraph (allowances 2/2, reach 19/23) over `nodes`, inserted into node_map in the order given."""
    node_map = {n.uuid: n for n in nodes}
    pg = PathGraph(node_map=node_map, edge_y_alignment_allowance=2, edge_x_alignment_allowance=2,
                   max_horizontal_traversal_dist=19, max_vertical_traversal_dist=23)
    assert is_successful(pg.buildGraph())
    return pg


# 1. The FF2 edge set -----------------------------------------------------------------------------------

def test_ff2_has_exactly_the_40_expected_edges(ff2):
    agent, _ = ff2
    assert len(EXPECTED_FF2_EDGES) == 40 and len(set(EXPECTED_FF2_EDGES)) == 40
    graph = agent.pathGraph.graph
    assert graph.number_of_edges() == 40
    assert sorted(_position_edges(graph)) == sorted(EXPECTED_FF2_EDGES)


# 2. Edge weights ---------------------------------------------------------------------------------------

def test_ff2_edge_weights_are_dx_for_horizontal_and_dy_for_vertical(ff2):
    agent, by_pos = ff2
    graph = agent.pathGraph.graph
    assert graph[by_pos[(82, 60)]][by_pos[(99, 60)]][0]["weight"] == 17
    assert graph[by_pos[(48, 61)]][by_pos[(48, 45)]][0]["weight"] == 16

    allowance = 2
    for u, v, data in graph.edges(data=True):
        dx = abs(u.minimap_position[0] - v.minimap_position[0])
        dy = abs(u.minimap_position[1] - v.minimap_position[1])
        horizontal = dy <= allowance < dx
        vertical = dx <= allowance < dy
        assert horizontal != vertical, f"edge {u.minimap_position}->{v.minimap_position} is not clearly one axis"
        assert data["weight"] == (dx if horizontal else dy)


# 3. The two known FF2 cases: no path today -------------------------------------------------------------

@pytest.mark.parametrize("leg", [
    ((113, 60), (82, 60)), ((82, 60), (48, 61)), ((48, 61), (58, 46)), ((58, 46), (49, 28)),
    ((49, 28), (101, 24)), ((101, 24), (105, 42)), ((105, 42), (113, 60)),
])
def test_ff2_rotation_leg_path(ff2, leg):
    agent, by_pos = ff2
    assert leg in _rotation_legs(agent)
    src, dst = by_pos[leg[0]], by_pos[leg[1]]
    assert nx.has_path(agent.pathGraph.graph, src, dst) is (leg not in NO_PATH_LEGS)


# 4. Synthetic graphs: reach, alignment, ties, platform edges -------------------------------------------

def test_horizontal_reach_20_px_gets_no_edge_and_19_px_does():
    far = _build(_node(100, 50), _node(120, 50))
    assert far.graph.number_of_edges() == 0

    near_left, near_right = _node(100, 50), _node(119, 50)
    near = _build(near_left, near_right)
    assert set(near.graph.edges()) == {(near_left, near_right), (near_right, near_left)}
    assert near.graph.number_of_edges() == 2
    assert near.graph[near_left][near_right][0]["weight"] == 19
    assert near.graph[near_right][near_left][0]["weight"] == 19


def test_vertical_reach_24_px_gets_no_edge_and_23_px_does():
    far = _build(_node(50, 76), _node(50, 100))
    assert far.graph.number_of_edges() == 0

    top, bottom = _node(50, 77), _node(50, 100)
    near = _build(top, bottom)
    assert near.graph.number_of_edges() == 2
    assert near.graph.has_edge(bottom, top) and near.graph.has_edge(top, bottom)
    assert near.graph[bottom][top][0]["weight"] == 23
    assert near.graph[top][bottom][0]["weight"] == 23


def test_alignment_3_px_off_axis_gets_no_edge_and_2_px_does():
    # horizontal candidate: y differs by 3 (no edge) or 2 (edge)
    assert _build(_node(50, 50), _node(60, 53)).graph.number_of_edges() == 0
    left, right = _node(50, 50), _node(60, 52)
    aligned = _build(left, right)
    assert aligned.graph.has_edge(left, right) and aligned.graph.has_edge(right, left)

    # vertical candidate: x differs by 3 (no edge) or 2 (edge)
    assert _build(_node(50, 50), _node(53, 30)).graph.number_of_edges() == 0
    below, above = _node(50, 50), _node(52, 30)
    aligned = _build(below, above)
    assert aligned.graph.has_edge(below, above) and aligned.graph.has_edge(above, below)


@pytest.mark.parametrize("first_inserted", ["a", "b"])
@pytest.mark.parametrize("direction, source_pos, tie_a_pos, tie_b_pos", [
    ("left", (100, 50), (90, 50), (90, 51)),
    ("right", (100, 50), (110, 50), (110, 51)),
    ("up", (50, 50), (50, 40), (51, 40)),
    ("down", (50, 50), (50, 60), (51, 60)),
])
def test_tie_between_equidistant_candidates_goes_to_the_first_inserted(
        direction, source_pos, tie_a_pos, tie_b_pos, first_inserted):
    source, tie_a, tie_b = _node(*source_pos), _node(*tie_a_pos), _node(*tie_b_pos)
    nodes = [source, tie_a, tie_b] if first_inserted == "a" else [source, tie_b, tie_a]
    winner = tie_a if first_inserted == "a" else tie_b

    pg = _build(*nodes)

    # the source's only outgoing edge is in `direction`, to the candidate inserted first
    assert [(u, v) for u, v in pg.graph.out_edges(source)] == [(source, winner)], direction


def test_vertical_edge_to_a_left_platform_edge_only_from_a_source_with_x_at_or_right_of_it():
    target = _node(50, 30, left_edge=True)
    right_of = _node(51, 50)
    left_of = _node(49, 50)

    pg = _build(target, right_of, left_of)
    assert pg.graph.has_edge(right_of, target)          # source x 51 >= target x 50
    assert not pg.graph.has_edge(left_of, target)       # source x 49 < target x 50

    equal = _node(50, 50)
    pg_equal = _build(target, equal)
    assert pg_equal.graph.has_edge(equal, target)       # source x 50 >= target x 50 (equality counts)


# 4b. Vertical edges: the closest node that passes the platform-edge rule (decision H17, path_graph.py:319-411) ----

# Per direction (up = smaller y, down = larger y): the source at y 50, a nearer candidate 5 px away and a farther
# plain candidate 15 px away. x offsets stay inside the alignment allowance of 2.
_VERTICAL = {"up": -1, "down": 1}


def _vertical_case(direction, source_x, near_x, far_x, near_flags):
    sign = _VERTICAL[direction]
    source = _node(source_x, 50)
    near = _node(near_x, 50 + sign * 5, **near_flags)
    far = _node(far_x, 50 + sign * 15)
    return source, near, far


@pytest.mark.parametrize("direction", ["up", "down"])
def test_invalid_nearest_vertical_node_is_skipped_for_a_valid_farther_node(direction):
    # Before H17 the closest node was chosen first and only then checked, so this source had no edge at all.
    # Now a candidate that fails the platform-edge rule is skipped. Source x 10 is left of the nearer left-edge
    # node at x 11, so that node is skipped and the plain node 15 px away gets the edge.
    source, near, far = _vertical_case(direction, 10, 11, 10, {"left_edge": True})
    pg = _build(source, near, far)
    assert list(pg.graph.out_edges(source)) == [(source, far)]
    assert pg.graph[source][far][0]["weight"] == 15

    # Same for the mirrored right-edge rule: source x 12 is right of the nearer right-edge node at x 11.
    source, near, far = _vertical_case(direction, 12, 11, 12, {"right_edge": True})
    pg = _build(source, near, far)
    assert list(pg.graph.out_edges(source)) == [(source, far)]
    assert pg.graph[source][far][0]["weight"] == 15


def test_issue_6_example_s_links_up_to_t_past_the_unreachable_left_end_m():
    # The issue-6 example: S(70,60) on the floor, M1(72,50) the left end of a middle platform that starts 2 px
    # right of S (so Kanna at S has no pixel of it above her), T(70,40) on the top platform. S links up to T and
    # T links down to S; the links that already existed are unchanged.
    f1, s, f2 = _node(56, 60), _node(70, 60), _node(86, 60)
    m1, m2 = _node(72, 50, left_edge=True), _node(88, 50, right_edge=True)
    t1, t, t2 = _node(56, 40), _node(70, 40), _node(84, 40)
    pg = _build(f1, s, f2, m1, m2, t1, t, t2)

    assert not pg.graph.has_edge(s, m1)
    assert pg.graph.has_edge(s, t) and pg.graph[s][t][0]["weight"] == 20
    assert not pg.graph.has_edge(t, m1)                  # T x 70 < M1 x 72: still not a valid drop onto M1
    assert pg.graph.has_edge(t, s) and pg.graph[t][s][0]["weight"] == 20
    assert set(pg.graph.out_edges(s)) == {(s, f1), (s, f2), (s, t)}
    assert set(pg.graph.out_edges(t)) == {(t, t1), (t, t2), (t, s)}
    assert pg.graph.has_edge(m1, s) and pg.graph.has_edge(m1, t)   # M1 x 72 is within 2 px of S and T
    assert pg.graph.number_of_edges() == 19              # 17 before H17, plus S->T and T->S


@pytest.mark.parametrize("direction", ["up", "down"])
def test_valid_nearest_vertical_node_wins_over_a_farther_one(direction):
    sign = _VERTICAL[direction]
    # nearer left-edge node at x 10, source x 11 (>= 10) -> valid, so the edge goes to it and not to `far`.
    source, near, far = _vertical_case(direction, 11, 10, 11, {"left_edge": True})
    pg = _build(source, near, far)
    assert list(pg.graph.out_edges(source)) == [(source, near)]
    assert pg.graph[source][near][0]["weight"] == 5
    assert (near.minimap_position[1] - source.minimap_position[1]) * sign > 0


@pytest.mark.parametrize("direction", ["up", "down"])
def test_vertical_edge_to_a_right_platform_edge_only_from_a_source_with_x_at_or_left_of_it(direction):
    sign = _VERTICAL[direction]
    target_y = 50 + sign * 20
    target = _node(50, target_y, right_edge=True)
    left_of = _node(49, 50)
    right_of = _node(51, 50)
    equal = _node(50, 50)

    pg = _build(target, left_of, right_of)
    assert pg.graph.has_edge(left_of, target)           # source x 49 <= target x 50
    assert not pg.graph.has_edge(right_of, target)      # source x 51 > target x 50

    pg_equal = _build(target, equal)
    assert pg_equal.graph.has_edge(equal, target)       # source x 50 <= target x 50 (equality counts)


@pytest.mark.parametrize("direction", ["up", "down"])
def test_vertical_edge_to_a_node_flagged_both_platform_edges_is_valid_from_either_side(direction):
    sign = _VERTICAL[direction]
    target = _node(50, 50 + sign * 20, left_edge=True, right_edge=True)
    for source_x in (49, 50, 51):
        source = _node(source_x, 50)
        assert _build(target, source).graph.has_edge(source, target), source_x


# 5. getPathToGoalNode on an unreachable goal -----------------------------------------------------------

def test_get_path_to_goal_node_is_empty_for_an_unreachable_goal(ff2):
    agent, by_pos = ff2
    path = agent.pathGraph.getPathToGoalNode(by_pos[(49, 28)], by_pos[(101, 24)])
    assert isinstance(path, GraphPath)
    assert path == GraphPath() and len(path) == 0

    path = agent.pathGraph.getPathToGoalNode(by_pos[(101, 24)], by_pos[(105, 42)])
    assert isinstance(path, GraphPath) and len(path) == 0


def test_get_path_to_goal_node_ends_at_the_goal_for_a_reachable_leg(ff2):
    agent, by_pos = ff2
    goal = by_pos[(82, 60)]
    path = agent.pathGraph.getPathToGoalNode(by_pos[(113, 60)], goal)
    assert len(path) >= 1 and path[-1] is goal
