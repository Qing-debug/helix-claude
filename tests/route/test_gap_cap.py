"""Tests for the gap cap (any-map plan Task 1, Hong's anymap182-2, H112): a gap is offered only when the shortest
recorded sideways teleport (`shortest_side_teleport_px`, 18) crosses it from anywhere the planner counts as lined up
on the launch, so a gap of up to `shortest_side_teleport_px - arrive_px` (16 px for Kanna) between the near platform's
end and the far platform's start keeps its GAP edge and a wider one has none.

Map `gap(w)`: platform A is (24, 50) and (40, 50), with (40, 50) marked as a right end; platform B is (40 + w, 50),
marked as a left end, and (56 + w, 50). PathGraph links (40, 50) and B's near node for every w up to the 19 px reach;
the route step drops the GAP edge when w is over the cap."""

import pytest
from returns.pipeline import is_successful
from test_platform_ends import RNG, TP_R, Built, Row, decide

from src.route.params import MovementParams
from src.route.planner import next_move
from src.route.route_graph import EdgeKind


def gap(w: int) -> list[Row]:
    return [(24, 50, False, False), (40, 50, False, True), (40 + w, 50, True, False), (56 + w, 50, False, False)]


def decide_result(built: Built, pos: tuple[int, int], target_x: int) -> str:
    """The planner's `Failure` text for the move from `pos` toward the node at `target_x` (the counterpart of `decide`)."""
    result = next_move(built.rg, pos, built.n[target_x], RNG)
    assert not is_successful(result), result
    return result.failure()


@pytest.mark.parametrize("w, cost", [(10, 9 / 12.5 + 0.55), (16, 3 / 12.5 + 0.55)])  # 1.27 s and 0.79 s
def test_a_gap_up_to_16_px_is_offered(w, cost):
    built = Built(gap(w))
    assert built.kind(40, 40 + w) == EdgeKind.GAP and built.kind(40 + w, 40) == EdgeKind.GAP
    assert built.cost(40, 40 + w) == pytest.approx(cost) and built.cost(40 + w, 40) == pytest.approx(cost)


@pytest.mark.parametrize("w", [17, 18, 19])
def test_a_gap_of_17_to_19_px_is_not_offered(w):  # PathGraph links them (node gap <= 19); the route step drops them
    built = Built(gap(w))
    a, b = built.p(24), built.p(40 + w)
    assert not built.rg.graph.has_edge(built.n[40], built.n[40 + w])
    assert not built.rg.graph.has_edge(built.n[40 + w], built.n[40])
    assert built.rg.gap_launch(built.n[40], built.n[40 + w]) is None
    assert built.rg.gap_launch(built.n[40 + w], built.n[40]) is None
    assert built.rg.split_ends == {(a, +1), (b, -1)}  # the ends still stop a teleport
    assert built.rg.side_landing(a, 28, +1) == (40, a)
    assert decide_result(built, (24, 50), 56 + w) == f"no path from (24, 50) to ({56 + w}, 50)"  # the route is "no path"


def test_the_cap_is_computed_from_the_settings():  # no 16 anywhere: shortest 19 offers 17, shortest 17 drops 16
    assert Built(gap(17), MovementParams(shortest_side_teleport_px=19)).kind(40, 57) == EdgeKind.GAP
    built = Built(gap(16), MovementParams(shortest_side_teleport_px=17))
    assert not built.rg.graph.has_edge(built.n[40], built.n[56])


@pytest.mark.parametrize("x", [32, 33, 34, 35, 36])  # the whole lined-up range of launch 34 on gap(10)
def test_every_lined_up_x_is_crossed_by_an_18_px_teleport(x):
    built = Built(gap(10))
    assert x + MovementParams().shortest_side_teleport_px >= 50
    assert decide(built, (x, 50), 66).move == TP_R


# Review Focus 2: B's near node (50, 53) is 3 px below A's end, so PathGraph links (40, 50) to B's far node (58, 50), 18 px
# away, and not to the near node. The cap is measured to the near node (10 px from A's end), not to the edge's far node:
# from 38, the far edge of the lined-up range of launch 40, an 18 px teleport reaches B's start at 50, so the edge stays.
FAR_NODE = [(24, 50, False, False), (40, 50, False, True), (50, 53, True, False), (58, 50, False, False)]


def test_the_cap_is_measured_to_the_far_platforms_start_not_to_the_edges_far_node():
    built = Built(FAR_NODE)
    assert built.p(50) == built.p(58) != built.p(40)  # B is one platform (a slope), split from A by the mark
    assert not built.rg.graph.has_edge(built.n[40], built.n[50])  # PathGraph does not link the 3 px step itself
    assert built.kind(40, 58) == EdgeKind.GAP and built.kind(58, 40) == EdgeKind.GAP
    assert built.rg.gap_launch(built.n[40], built.n[58]) == 40  # A2's 42, clamped onto A's end
    assert built.rg.gap_launch(built.n[58], built.n[40]) == 56  # A2: 40 + 16, inside B
