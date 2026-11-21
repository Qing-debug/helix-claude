"""Tests for the route preview after the walk limit (walk-fluid plan Task 1, QB266 A; rulings FLUID173-T1b-D2 = A).

The walk limit turned the one table case that teleported past a platform end (T11's 15 px gap) into a walk, so
`test_route_preview.py` has no case left for the landing rule "a teleport past a platform end lands at the end node's y"
and none whose hops leave `rg.path`. These two tests keep both in the default (not slow) run."""

from pathlib import Path

import numpy as np
import pytest
from preview_checks import follows, s3b_problems
from returns.pipeline import is_successful
from test_planner import _synthetic  # imported flat from the frozen file, as test_route_preview.py does

import src.route.planner as planner
from src.classes.classes import Kanna
from src.controller.controller import MapleAgent
from src.input.protocol import Key
from src.route.params import MovementParams
from src.route.planner import Move, MoveKind, next_move
from src.route.route_graph import RouteGraph
from src.route.route_preview import preview

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "fossil_foundry_2.csv"
TP_L = Move(MoveKind.TELEPORT, Key.LEFT, None)


def test_a_teleport_past_a_platform_end_over_the_walk_limit_lands_at_the_end_nodes_y():
    """V4, left/right bullet. Two nodes 17 px apart (the walk limit: a teleport is taken, 19 px reach) on one platform:
    the teleport lands at x 8, 2 px past the end node (10, 10), where no platform has a surface, so the y is the end
    node's. The character reads at (27, 11), one row below its line, so the y 10 is the end node's and not the read's."""
    nodes, rg = _synthetic([(10, 10), (27, 10)])
    assert rg.params.walk_under_px == 17 and rg.reach_h == 19
    platform = rg.terrain.platforms[rg.terrain.platform_index(nodes[0])]
    assert planner._surface_ys(platform, 27 - rg.reach_h) == []

    result = preview(rg, (27, 11), nodes[0])

    assert is_successful(result)
    pv = result.unwrap()
    assert len(pv.hops) == 1 and pv.hops[0].options == (TP_L,)
    assert pv.hops[0].landing == (8, 10)
    assert s3b_problems(rg, pv, nodes[0]) == []
    assert next_move(rg, (27, 11), nodes[0], np.random.default_rng(0)).unwrap().move == TP_L


@pytest.fixture(scope="module")
def ff2_graph():
    agent = MapleAgent(characterClass=Kanna())
    assert is_successful(agent.loadMap(FIXTURE))
    built = RouteGraph.build(agent.pathGraph, MovementParams())
    assert is_successful(built)
    return agent, built.unwrap()


@pytest.mark.parametrize("a, b", [((109, 23), (48, 45)), ((42, 28), (118, 43))], ids=["109-23_to_48-45", "42-28_to_118-43"])
def test_a_preview_whose_hops_leave_the_path_still_agrees_with_next_move(ff2_graph, a, b):
    """RV3-D5 A2, kept in the default run (the table has no such case since QB266, only the slow FF2 sweep pins the set):
    each hop is `next_move`'s own move (S3b holds), yet the hops do not follow `rg.path`."""
    agent, rg = ff2_graph
    target = next(node for node in agent.node_map.values() if node.minimap_position == b)

    result = preview(rg, a, target)

    assert is_successful(result)
    pv = result.unwrap()
    assert pv.hops and s3b_problems(rg, pv, target) == []
    assert not follows(pv.path, pv.hops)
