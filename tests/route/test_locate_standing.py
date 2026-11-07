"""Tests for `locate.attach_standing` and Hong's two numbers (off-graph fix plan, Task 5, OFFP141; QB213).

`attach_standing` is the loose attach for a character that has stood still for `standing_s` where the strict rules
(A1-A3) cannot place it: it snaps the read onto the nearest platform line within `standing_band_px`, above or below.
Positions are in foot reference (3 px below the dot's centre, Task 4). FF2 platforms used here (fixture map):
floor (0) y 60 around x 90; mid-left (1) y 46 at x 58; top (2) with the ramp (66, 28)-(79, 24); mid-right (3).
"""

import dataclasses
import math
from fractions import Fraction
from pathlib import Path

import pytest
from returns.pipeline import is_successful
from returns.result import Failure, Success

from src.classes.classes import Kanna
from src.controller.controller import MapleAgent
from src.controller.node import Node
from src.route.locate import attach, attach_standing
from src.route.params import MovementParams, validate
from src.route.route_graph import RouteGraph

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "fossil_foundry_2.csv"


@pytest.fixture(scope="module")
def ff2() -> RouteGraph:
    agent = MapleAgent(characterClass=Kanna())
    assert is_successful(agent.loadMap(FIXTURE))
    built = RouteGraph.build(agent.pathGraph, MovementParams())
    assert is_successful(built)
    return built.unwrap()


def _synthetic(points) -> RouteGraph:
    """A RouteGraph over nodes at `points` (in this node_map order), built the way the runtime builds it."""
    nodes = {i: Node("blue_node", xy, False, False, False, False) for i, xy in enumerate(points)}
    pg = MapleAgent(characterClass=Kanna()).buildPathGraphFor(nodes).unwrap()
    assert is_successful(pg.buildGraph())
    built = RouteGraph.build(pg, MovementParams())
    assert is_successful(built)
    return built.unwrap()


def _line_y(left: Node, right: Node, px: int) -> Fraction:
    (lx, ly), (rx, ry) = left.minimap_position, right.minimap_position
    return ly + Fraction(ry - ly) * (px - lx) / (rx - lx)


# ---- Hong's numbers ----

def test_defaults_are_hongs():
    p = MovementParams()
    assert p.standing_s == 0.5
    assert p.standing_band_px == 6
    assert is_successful(validate(p))
    refused_band = validate(MovementParams(standing_band_px=0))
    assert not is_successful(refused_band) and "standing_band_px" in refused_band.failure()
    refused_s = validate(MovementParams(standing_s=0))
    assert not is_successful(refused_s) and "standing_s" in refused_s.failure()


def test_old_step_logs_still_build_params():
    # a saved run's step-log header holds the params of its day: the view builds MovementParams(**header.params)
    # (src/route_view/model.py:274), and a run made before this change has neither new key (Review Focus 1)
    old = {k: v for k, v in dataclasses.asdict(MovementParams()).items() if k not in ("standing_s", "standing_band_px")}
    assert "standing_s" not in old and "standing_band_px" not in old
    built = MovementParams(**old)
    assert built == MovementParams()
    assert (built.standing_s, built.standing_band_px) == (0.5, 6)


# ---- FF2 ----

def test_above_the_ramp_snaps_to_its_line(ff2):
    # y_line(70) on (66,28)-(79,24) is 26.77
    assert attach_standing(ff2, (70, 22)) == Success((70, 27))
    anchor = attach(ff2, (70, 27))
    assert is_successful(anchor)
    assert anchor.unwrap().platform == 2


def test_below_the_line_snaps_up_to_it(ff2):
    assert attach_standing(ff2, (70, 31)) == Success((70, 27))  # 4.23 px under the ramp


def test_band_edges(ff2):
    assert attach_standing(ff2, (90, 54)) == Success((90, 60))  # exactly 6 px above the floor line
    assert attach_standing(ff2, (90, 66)) == Success((90, 60))  # exactly 6 px under it
    assert attach_standing(ff2, (90, 53)) == Failure(
        "standing at (90, 53), more than 6 px from any platform of the map")
    assert attach_standing(ff2, (90, 67)) == Failure(
        "standing at (90, 67), more than 6 px from any platform of the map")


def test_the_failure_message_names_the_params_band():
    rg = _synthetic([(0, 10), (10, 10)])
    rg.params = dataclasses.replace(rg.params, standing_band_px=2)
    assert attach_standing(rg, (5, 12)) == Success((5, 10))
    assert attach_standing(rg, (5, 13)) == Failure("standing at (5, 13), more than 2 px from any platform of the map")


def test_stacked_platforms_take_the_nearest(ff2):
    # x=60: mid-left's line is 45.875, the floor's 60.29, the top's 28
    assert attach_standing(ff2, (60, 40)) == Success((60, 46))
    # 6.1 px under mid-left's line, 8.3 px above the floor's: neither is within 6
    assert attach_standing(ff2, (60, 52)) == Failure(
        "standing at (60, 52), more than 6 px from any platform of the map")


def test_outside_every_platform(ff2):
    assert attach_standing(ff2, (20, 60)) == Failure(
        "standing at (20, 60), more than 6 px from any platform of the map")


def test_beyond_the_end_gets_no_loose_attach(ff2):
    # strict A3 places a position just beyond a platform's end within reach; the loose attach looks at segments only
    assert is_successful(attach(ff2, (132, 60)))
    assert not is_successful(attach_standing(ff2, (132, 60)))


def test_snapped_point_reattaches_to_the_same_platform(ff2):
    band = ff2.params.standing_band_px
    platforms = ff2.terrain.platforms
    checked = 0
    for index, platform in enumerate(platforms):
        for left, right in zip(platform, platform[1:]):
            if left.minimap_position[0] == right.minimap_position[0]:
                continue  # a vertical segment has no line
            for px in range(left.minimap_position[0], right.minimap_position[0] + 1):
                line = _line_y(left, right, px)
                for dy in range(-6, 7):
                    py = math.floor(line + Fraction(1, 2)) + dy
                    if abs(py - line) > band:
                        continue  # the rounded base is up to half a pixel off the line: the far edge can leave the band
                    # which platforms have a line within the band here: on FF2 only this one, so it is the one chosen
                    candidates = {
                        i for i, p in enumerate(platforms) for lo, hi in zip(p, p[1:])
                        if lo.minimap_position[0] != hi.minimap_position[0]
                        and lo.minimap_position[0] <= px <= hi.minimap_position[0]
                        and abs(py - _line_y(lo, hi, px)) <= band
                    }
                    assert candidates == {index}, (px, py, candidates)

                    snapped = attach_standing(ff2, (px, py))
                    assert is_successful(snapped), (px, py, snapped)
                    sx, sy = snapped.unwrap()
                    assert sx == px
                    assert abs(sy - line) <= Fraction(1, 2), (px, py, sy, float(line))
                    anchor = attach(ff2, (sx, sy))
                    assert is_successful(anchor), (px, py, (sx, sy))
                    assert anchor.unwrap().platform == index, (px, py, (sx, sy))
                    checked += 1
    assert checked > 2000  # about 250 columns of platform, 13 offsets each


# ---- tie-breaks and the exact arithmetic (synthetic maps) ----

def test_equal_offset_across_platforms_picks_the_lower_platform_index():
    rg = _synthetic([(0, 16), (10, 16), (20, 16), (0, 10), (10, 10), (20, 10)])
    assert len(rg.terrain.platforms) == 2
    assert rg.terrain.platforms[0][0].minimap_position[1] == 16  # y=16 comes first in node_map order
    assert attach_standing(rg, (10, 13)) == Success((10, 16))  # 3 px from each line


def test_the_nearer_line_wins_over_the_lower_platform_index():
    rg = _synthetic([(0, 17), (8, 17), (16, 17), (0, 12), (8, 12), (16, 12)])
    assert len(rg.terrain.platforms) == 2
    assert rg.terrain.platforms[0][0].minimap_position[1] == 17  # y=17 is platform 0, the farther line
    assert attach_standing(rg, (4, 14)) == Success((4, 12))  # 2 px from the y=12 line, 3 px from the y=17 line


def test_the_line_rounds_half_up_not_to_even():
    rg = _synthetic([(0, 10), (2, 11)])
    assert len(rg.terrain.platforms) == 1
    assert attach_standing(rg, (1, 10)) == Success((1, 11))  # the line is at 10.5: half up is 11, round() says 10
    rg = _synthetic([(0, 11), (2, 12)])
    assert attach_standing(rg, (1, 12)) == Success((1, 12))  # 11.5 -> 12


def test_a_vertical_segment_is_skipped():
    # (10,10) and (10,16) share x and sit on one platform through (0,13) (as in test_locate)
    rg = _synthetic([(0, 13), (10, 10), (10, 16)])
    assert len(rg.terrain.platforms) == 1
    assert attach_standing(rg, (10, 13)) == Success((10, 10))  # the slope's end; the same-x segment has no line
