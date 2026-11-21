"""Tests for the walk limit (walk-fluid plan Task 1, Hong's QB266 option A): a node closer than `walk_under_px` (17 px,
one 19 px teleport minus the 2 px `arrive_px` allows) is walked to, not teleported past; longer gaps still teleport.
The limit is the setting `MovementParams.walk_under_px`, read by the planner's `_run` and by `horizontal_cost_s`
(the graph cost mirrors `_run` hop by hop)."""

from pathlib import Path

import numpy as np
import pytest
from returns.pipeline import is_successful

from src.classes.classes import Kanna
from src.controller.controller import MapleAgent
from src.controller.node import Node
from src.input.protocol import Key
from src.route.params import MovementParams
from src.route.planner import Move, MoveKind, next_move
from src.route.route_graph import RouteGraph, horizontal_cost_s
from src.route.sim.run import run_sim
from src.route.step_log import read_run

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
CSV = FIXTURES / "fossil_foundry_2.csv"
TERRAIN = FIXTURES / "ff2_terrain.json"

_LIMIT = 17  # the default of `walk_under_px`; the asserts below that name a distance are about it
_RNG = np.random.default_rng(0)


def graph_with(params: MovementParams):
    """One flat unmarked platform with nodes at x 30, 45 and 60 (15 px apart: within the 19 px reach, so terrain and
    PathGraph link them), built the way the runtime builds its graph. Returns (RouteGraph, the node at x 60)."""
    nodes = {i: Node("blue_node", (x, 50), False, False, False, False) for i, x in enumerate((30, 45, 60))}
    path_graph = MapleAgent(characterClass=Kanna()).buildPathGraphFor(nodes).unwrap()
    assert is_successful(path_graph.buildGraph())
    built = RouteGraph.build(path_graph, params)
    assert is_successful(built)
    return built.unwrap(), nodes[2]


def move_from(x: int, params: MovementParams | None = None) -> Move:
    rg, node_60 = graph_with(MovementParams() if params is None else params)
    result = next_move(rg, (x, 50), node_60, _RNG)
    assert is_successful(result), result
    move = result.unwrap().move
    assert move is not None
    return move


def test_under_the_limit_walks():  # d = 16
    assert move_from(44) == Move(MoveKind.WALK, Key.RIGHT, 1280.0)


def test_old_overshoot_range_now_walks():  # d = 13: teleported and walked back before
    assert move_from(47) == Move(MoveKind.WALK, Key.RIGHT, 1040.0)


def test_at_the_limit_teleports():  # d = 17: lands 2 px past, within arrive_px
    assert move_from(43) == Move(MoveKind.TELEPORT, Key.RIGHT, None)


def test_the_limit_is_a_setting():  # walk_under_px=13 brings the old overshoot back
    assert move_from(47, MovementParams(walk_under_px=13)).kind == MoveKind.TELEPORT


def test_the_limit_defaults_to_17():
    assert MovementParams().walk_under_px == _LIMIT


@pytest.mark.parametrize("d, expected", [(16, 1.28), (13, 1.04), (17, 0.55 + 2 / 12.5), (30, 0.55 + 11 / 12.5),
                                         (38, 1.10), (40, 1.10 + 2 / 12.5), (0, 0.0)])
def test_horizontal_cost_follows_the_limit(d, expected):
    assert horizontal_cost_s(d, 19, MovementParams()) == pytest.approx(expected, abs=1e-9)


def test_sim_teleports_no_node_under_the_limit(tmp_path):
    """On the default fixture no sideways teleport of a 3-lap run heads for a node closer than the limit."""
    out = tmp_path / "run"
    assert is_successful(run_sim(CSV, TERRAIN, 3, 1, out_dir=out))
    run = read_run(out).unwrap()
    teleports = [
        event.data for event in run.events
        if event.ev == "planned" and event.data["kind"] == "teleport" and event.data["dir"] in ("LEFT", "RIGHT")
    ]
    assert teleports, "the run has no sideways teleport, so the test checks nothing"
    for data in teleports:
        assert abs(data["toward"][0] - data["pos"][0]) >= _LIMIT, data
