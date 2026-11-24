"""Cleanup piece W1-8 (R13, Hong's H140): `next_move` and the route preview's `_plan` share one plan routine.

Pinned on the old code (406bf21, where `route_preview._plan` was a statement-for-statement copy of `next_move`), over
Fossil Foundry 2 and Hong's marked copy of it, with every node of the map as the target and a 3 px grid of positions
around its nodes: `next_move`'s result under two rng seeds (the move, the cost to go and the node it heads for, or the
failure's message) with the generator's state after each call; `_plan`'s result (options, toward, total, the starts,
the chosen start, the path, the aim and the platform); and the whole `preview` (starts, path, and every hop's options,
toward, from, landing and cost). The same three over every case of `test_route_preview.py`'s hand-built table on its
own graph, at the case's position and the 7 x 7 px square around it, which reaches the branches the two maps' grids do
not (a target not in the graph, no path, P5's WALK after a slope start and the `GAP` guard's walk). Each set of lines
is pinned as (line count, sha256). On a mismatch the lines are written to pytest's tmp_path, or to `HELIX_TRACE_DIR`
when it is set, for a diff against the old commit's lines. No Node is repr'd (its uuid): nodes are written as their
minimap positions.
"""

import hashlib
import os
from pathlib import Path

import numpy as np
import pytest
from returns.pipeline import is_successful
from test_route_preview import CASES, _build

from src.classes.classes import Kanna
from src.controller.controller import MapleAgent
from src.route.params import MovementParams
from src.route.planner import next_move
from src.route.route_graph import RouteGraph
from src.route.route_preview import _Across, _plan, preview

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
SEEDS = (0, 1)


def _at(node):
    return None if node is None else node.minimap_position


def _move(move):
    return (move.kind.value, None if move.direction is None else move.direction.name, repr(move.walk_ms))


def _state(rng):
    state = rng.bit_generator.state
    return (state["state"]["state"], state["state"]["inc"], state["has_uint32"], state["uinteger"])


def _starts(starts):
    return [(_at(option.node), repr(option.cost_s)) for option in starts]


def _decision(result):
    if not is_successful(result):
        return ("failure", result.failure())
    d = result.unwrap()
    return (None if d.move is None else _move(d.move), repr(d.cost_to_go_s), _at(d.toward))


def _planned(result):
    if not is_successful(result):
        return ("failure", result.failure())
    p = result.unwrap()
    if p is None:
        return None
    aim = ("across", p.aim.x, _at(p.aim.node)) if isinstance(p.aim, _Across) else ("edge", _at(p.aim.g), _at(p.aim.h))
    path = [_at(n) for n in p.path]
    return ([_move(m) for m in p.options], _at(p.toward), repr(p.total), _starts(p.starts), _at(p.start), path, aim,
            p.platform)


def _previewed(result):
    if not is_successful(result):
        return ("failure", result.failure())
    pv = result.unwrap()
    hops = [([_move(m) for m in h.options], _at(h.toward), h.from_pos, h.landing, repr(h.cost_to_go_s)) for h in pv.hops]
    return (pv.pos, _at(pv.target), pv.arrived, _starts(pv.starts), _at(pv.start), repr(pv.cost_to_go_s),
            [_at(n) for n in pv.path], hops, pv.complete)


def _at_pos(rg, pos, target, rngs):
    """The pinned lines at one position: `next_move` under each seed, `_plan` and `preview`."""
    where = f"{_at(target)} {pos}"
    lines = [f"next_move {seed} {where} {_decision(next_move(rg, pos, target, rng))} {_state(rng)}"
             for seed, rng in rngs.items()]
    lines.append(f"_plan {where} {_planned(_plan(rg, pos, target))}")
    lines.append(f"preview {where} {_previewed(preview(rg, pos, target))}")
    return lines


def _rngs():
    rngs = {seed: np.random.default_rng(seed) for seed in SEEDS}
    return rngs, [f"seed {seed} {_state(rng)}" for seed, rng in rngs.items()]


def _map_lines(csv):
    agent = MapleAgent(characterClass=Kanna())
    assert is_successful(agent.loadMap(csv))
    rg = RouteGraph.build(agent.pathGraph, MovementParams()).unwrap()
    nodes = list(agent.pathGraph.node_map.values())
    xs = [n.minimap_position[0] for n in nodes]
    ys = [n.minimap_position[1] for n in nodes]
    grid = [(x, y) for x in range(min(xs) - 6, max(xs) + 7, 3) for y in range(min(ys) - 6, max(ys) + 7, 3)]
    rngs, lines = _rngs()
    for target in nodes:
        for pos in grid:
            lines += _at_pos(rg, pos, target, rngs)
    return lines


def _table_lines(tmp_path_factory):
    graphs = {}
    rngs, lines = _rngs()
    for case_id, graph, (x, y), target, _ in CASES:
        if graph not in graphs:
            graphs[graph] = _build(graph, tmp_path_factory)
        rg, node = graphs[graph]
        target_node = node(target)
        lines.append(f"case {case_id}")
        for dx in range(-3, 4):
            for dy in range(-3, 4):
                lines += _at_pos(rg, (x + dx, y + dy), target_node, rngs)
    return lines


def _check(name, lines, tmp_path):
    text = "\n".join(lines) + "\n"
    got = (len(lines), hashlib.sha256(text.encode()).hexdigest())
    if got != PINS[name]:
        out = Path(os.environ.get("HELIX_TRACE_DIR") or tmp_path)
        out.mkdir(parents=True, exist_ok=True)
        (out / f"{name}.lines").write_text(text, encoding="utf-8", newline="\n")
        pytest.fail(f"{name}: got {got}, pinned {PINS[name]}; lines written to {out}")


PINS = {
    "fossil_foundry_2.csv": (49982, "b845ac50694ec090815bf6c6785a055e92e897cf68cfb23f46984c24b463da0d"),
    "ff2_marked.csv": (70450, "076996ff3910b95ab3e1c018a4fa22d08fd65b1322f8d8118e79caa64d633552"),
    "table": (5912, "e798749428a55cd3ef4745d447356946719672ed9111423cbe37827826428a5d"),
}


@pytest.mark.slow
@pytest.mark.parametrize("name", ["fossil_foundry_2.csv", "ff2_marked.csv"])
def test_next_move_plan_and_preview_are_unchanged_on_every_target_and_grid_position(name, tmp_path):
    _check(name, _map_lines(FIXTURES / name), tmp_path)


@pytest.mark.slow
def test_next_move_plan_and_preview_are_unchanged_around_every_case_of_the_preview_table(tmp_path_factory, tmp_path):
    _check("table", _table_lines(tmp_path_factory), tmp_path)
