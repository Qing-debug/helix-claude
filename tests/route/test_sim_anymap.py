"""Both any-map gap rules in the simulator, on a made-up map (any-map plan Task 3; Hong's anymap182-1 and anymap182-2,
H111 and H112, whose done rule is H102's: "tested in the simulator, every check passing, reviewed by a second agent").

Fossil Foundry 2 has neither case, so the map is made up here (`_write_world` writes it into `tmp_path`; no fixture files).
Four platforms and a floor, minimap 162 x 82, y grows downward (the heights are the nodes' own, the world's terrain is the same lines):

       A  x 24-40  y 30       B  x 50-66  y 34       C  x 96-112  y 30       D  x 130-146  y 30
                    (40,30) | (50,34)                          (112,30) | (130,30)
                       a 10 px gap, B 4 px lower                  an 18 px gap, same height
       floor  x 24-146  y 52 (22 px under A, C and D, 18 under B: inside Kanna's 23 px vertical reach)

The four ends facing a gap are marked (A right, B left, C right, D left). Rotation (the blue nodes in file order): a1 (24, 30),
b1 (58, 34), a2 (32, 30), b2 (66, 34), c (96, 30), d (146, 30). So each lap crosses A -> B (twice) and B -> A by the stepped
gap's teleport link (rule 1: 4 px is within `side_teleport_land_dy_px` but over the 2 px PathGraph allowance), and goes
c -> d and d -> a1 by the floor.

The wide gap is where rule 2 shows: PathGraph links C's end (112, 30) to D's start (130, 30) (18 px is within the reach 19 and
the heights agree), the world's 19 px teleport would carry across it, and before rule 2 the walker would have offered it (the
exact-A launch at C's end). Under rule 2 an 18 px teleport must cross the gap from anywhere counted as lined up, which is
16 px (`shortest_side_teleport_px - arrive_px`) at most, so no GAP edge exists and c -> d takes the floor.

The floor has a node only under nodes that are not a marked end (and fillers at 45, 80, 121): a vertical edge to a marked end
is not safe in the world, where a teleport up from 1 px short of a platform's end lands on nothing.

The world's terrain extends 4 px past each unmarked end (A to x 20, B to 70, C to 92, D to 150, the floor 18 to 152) so a
teleport up from a read 1 px beside a node still lands on the platform; the marked ends are the gap's edges and end exactly
there.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import pytest
from returns.pipeline import is_successful
from test_walker_standing import Rec

from src.classes.classes import Kanna
from src.controller.controller import MapleAgent
from src.controller.node import Node
from src.route.params import MovementParams
from src.route.route_graph import EdgeKind, RouteGraph
from src.route.sim.run import SimReport, judge, run_sim
from src.route.walker import StepObserver

A_END, B_START = (40, 30), (50, 34)      # the stepped gap (10 px, B 4 px lower)
C_END, D_START = (112, 30), (130, 30)    # the wide gap (18 px, same height)
A_ROW, B_ROW, C_ROW, D_ROW = 30, 34, 30, 30
A_SPAN, B_SPAN, C_SPAN, D_SPAN = (24, 40), (50, 66), (96, 112), (130, 146)   # the nodes' x range on each platform
LAPS = 20

_HEADER = ("# minimap_size=162,82\n"
           "nodetype,pos_in_minimap,can_fall,fall_dist_exceeds_vertical_tp,is_left_platform_edge,is_right_platform_edge\n")
# (nodetype, x, y, left end mark, right end mark); the upper nodes can fall, the floor's do not (as the FF2 fixtures)
_BLUE = [(24, 30), (58, 34), (32, 30), (66, 34), (96, 30), (146, 30)]     # a1, b1, a2, b2, c, d
_ORANGE = [(40, 30, False, True), (50, 34, True, False), (104, 30, False, False), (112, 30, False, True),
           (130, 30, True, False), (138, 30, False, False)]
_FLOOR = [24, 32, 45, 58, 66, 80, 96, 104, 121, 138, 146]
_TERRAIN = {
    "_source": "made up for tests/route/test_sim_anymap.py (any-map plan Task 3), not a measured map: A and B 10 px apart with "
               "B 4 px lower, C and D 18 px apart at one height, a floor 22 px under them",
    "minimap_size": [162, 82],
    "platforms": [
        {"name": "floor", "droppable": False, "points": [[18, 52], [152, 52]]},
        {"name": "A", "droppable": True, "points": [[20, 30], [40, 30]]},
        {"name": "B", "droppable": True, "points": [[50, 34], [70, 34]]},
        {"name": "C", "droppable": True, "points": [[92, 30], [112, 30]]},
        {"name": "D", "droppable": True, "points": [[130, 30], [150, 30]]},
    ],
}


def _write_world(folder: Path) -> tuple[Path, Path]:
    def row(kind: str, x: int, y: int, fall: bool, left: bool = False, right: bool = False) -> str:
        flag = {True: "TRUE", False: "FALSE"}
        return f'{kind},"({x}, {y})",{flag[fall]},FALSE,{flag[left]},{flag[right]}'
    rows = [row("blue_node", x, y, True) for x, y in _BLUE]
    rows += [row("orange_node", x, y, True, left, right) for x, y, left, right in _ORANGE]
    rows += [row("orange_node", x, 52, False) for x in _FLOOR]
    csv = folder / "anymap.csv"
    csv.write_text(_HEADER + "\n".join(rows) + "\n", encoding="utf-8")
    terrain = folder / "anymap_terrain.json"
    terrain.write_text(json.dumps(_TERRAIN), encoding="utf-8")
    return csv, terrain


@dataclass
class Run:
    report: SimReport
    rec: Rec


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    return _write_world(tmp_path_factory.mktemp("anymap"))


@pytest.fixture(scope="module")
def runs(world: tuple[Path, Path]) -> dict[int, Run]:
    """The 20-lap run at seeds 1 and 2 (default noise), each with the walker's events."""
    csv, terrain = world
    out = {}
    for seed in (1, 2):
        rec = Rec()
        out[seed] = Run(run_sim(csv, terrain, LAPS, seed, observer=cast(StepObserver, rec)).unwrap(), rec)
    return out


def _on(read: tuple[int, int], row: int, span: tuple[int, int]) -> bool:
    """A read on the platform at `row` whose nodes span `span`: within 3 px of that x range (an arrival is counted within 2 px,
    and a landing offset moves it 1-2 more) and within 4 px of its y (a read right after a move is still in the fall: a
    platform 4 px lower than the last reads 32 first)."""
    return abs(read[1] - row) <= 4 and span[0] - 3 <= read[0] <= span[1] + 3


def _planned_from(rec: Rec, row: int, span: tuple[int, int]) -> list[tuple]:
    """The planned moves whose read is on the platform at `row` with nodes over `span`."""
    return [e for e in rec.of("planned") if _on(e[2], row, span)]


def _sideways(rec: Rec, row: int, span: tuple[int, int], direction: str) -> list[tuple[tuple[int, int], tuple[int, int] | None]]:
    """Each planned sideways teleport sent from a read on that platform (`_on`), as (the read, the read after it). The second
    read is the walker's next `settled` one, 0.2 s after the move: x is where the teleport landed, y may still be in the fall."""
    out = []
    events = rec.events
    for i, e in enumerate(events):
        if e[0] == "planned" and e[3] == "teleport" and e[4] == direction and _on(e[2], row, span):
            settled = next((s for s in events[i + 1:] if s[0] == "settled"), None)
            out.append((e[2], None if settled is None else settled[2]))
    return out


def _crossings(rec: Rec) -> tuple[int, int]:
    """How many planned sideways teleports crossed the stepped gap: (right, left) = (from A to B, from B to A). A teleport
    that did nothing (a no-op) is not a crossing."""
    right = _sideways(rec, A_ROW, A_SPAN, "RIGHT")
    left = _sideways(rec, B_ROW, B_SPAN, "LEFT")
    return (sum(s is not None and s[0] >= B_START[0] for _, s in right), sum(s is not None and s[0] <= A_END[0] for _, s in left))


# --- 0. the map is what it says ----------------------------------------------------------------------------


def _node(agent: MapleAgent, pos: tuple[int, int]) -> Node:
    assert agent.pathGraph is not None
    (node,) = [n for n in agent.pathGraph.node_map.values() if n.minimap_position == pos]
    return node


def test_the_map_has_the_link_and_not_the_wide_gap(world):
    """The graph the walker builds: rule 1 links A's end and B's start both ways (PathGraph has no edge: 4 px apart in height),
    and rule 2 leaves no GAP edge across the 18 px gap although PathGraph links the two ends."""
    agent = MapleAgent(characterClass=Kanna())
    assert is_successful(agent.loadMap(world[0]))
    assert agent.pathGraph is not None
    a_end, b_start, c_end, d_start = (_node(agent, p) for p in (A_END, B_START, C_END, D_START))
    assert not agent.pathGraph.graph.has_edge(a_end, b_start) and not agent.pathGraph.graph.has_edge(b_start, a_end)
    assert agent.pathGraph.graph.has_edge(c_end, d_start) and agent.pathGraph.graph.has_edge(d_start, c_end)
    rg = RouteGraph.build(agent.pathGraph, MovementParams()).unwrap()
    assert rg.teleport_links == {(a_end, b_start), (b_start, a_end)}
    gaps = {(u.minimap_position, v.minimap_position) for u, v, kind in rg.graph.edges(data="kind") if kind == EdgeKind.GAP}
    assert gaps == {(A_END, B_START), (B_START, A_END)}


# --- 1. the stepped gap is crossed -------------------------------------------------------------------------


@pytest.mark.parametrize("seed", [1, 2])
def test_the_stepped_gap_is_crossed(runs, seed):
    """Every leg is reached and 6/6 checks pass (the rotation crosses the stepped gap three times a lap, and before rule 1 the
    walker had no path for these legs). The walker teleports right from A and lands on B, and left from B and lands on A,
    every lap; reads are the walker's, so a teleport that did nothing (a no-op) is counted but is not a crossing."""
    run = runs[seed]
    report = run.report
    checks = judge(report, 0)
    assert len(checks) == 6 and all(check.ok for check in checks), [check for check in checks if not check.ok]
    assert report.off_map == 0 and report.legs_reached == report.legs == LAPS * 6 + 1   # six legs a lap and the approach leg
    crossed_right, crossed_left = _crossings(run.rec)
    assert crossed_right >= 2 * LAPS and crossed_left >= LAPS     # a1 -> b1 and a2 -> b2, and b1 -> a2, each lap


# --- 2. the wide gap is walked round -----------------------------------------------------------------------


@pytest.mark.parametrize("seed", [1, 2])
def test_the_wide_gap_is_walked_round(world, runs, seed):
    """c -> d is reached every lap and no walker teleport crosses the 18 px gap, in either direction: not one planned sideways
    teleport from C ends on D or from D ends on C (the world's teleport is 19 px and would carry across, as PathGraph's own
    edge between the ends says); the control is that the route takes the floor, a teleport down from C and up onto D."""
    csv, terrain = world
    run = runs[seed]
    right = _sideways(run.rec, C_ROW, C_SPAN, "RIGHT")
    left = _sideways(run.rec, D_ROW, D_SPAN, "LEFT")
    assert all(settled is not None and settled[0] <= C_END[0] for _, settled in right), right
    assert all(settled is not None and settled[0] >= D_START[0] for _, settled in left), left
    assert D_START not in [e[7] for e in _planned_from(run.rec, C_ROW, C_SPAN)]   # no move on C heads for D's start
    downs = [e for e in _planned_from(run.rec, C_ROW, C_SPAN) if e[3] == "teleport" and e[4] == "DOWN"]
    ups = [e for e in run.rec.of("planned") if e[3] == "teleport" and e[4] == "UP" and e[7][1] == D_ROW and e[7][0] >= D_START[0]]
    assert len(downs) >= LAPS and len(ups) >= LAPS                       # the way round, every lap
    known = run_sim(csv, terrain, LAPS, seed, known_cases=(((96, 30), (146, 30)),)).unwrap()
    assert known.known_cases == {"(96, 30)->(146, 30)": (LAPS, LAPS)}    # c -> d reached on every lap


# --- 3. the figures ----------------------------------------------------------------------------------------


def test_made_up_map_run_is_pinned(runs):
    """Seed 2, 20 laps, default noise: the figures are pinned so the quick `tools/check.py` sees any drift (as
    `test_sim_marked.py` does for Hong's marked map). Seed 1 gave 567 moves, 32 no-ops, 64 teleports down, 343.6805 s."""
    report = runs[2].report
    assert (report.legs, report.moves, report.no_ops, report.drops, report.teleport_downs, round(report.sim_seconds, 4)) == (
        121, 572, 42, 0, 63, 350.8201)
    assert _crossings(runs[2].rec) == (40, 20)    # A to B and B to A: 2 and 1 a lap, none lost to a no-op that mattered
