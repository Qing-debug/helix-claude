import dataclasses
import math
import re
from pathlib import Path

import pytest
from returns.result import Failure, Success

from src.route.params import MovementParams, validate


def test_defaults_match_contract_c3():
    p = MovementParams()
    assert p.arrive_px == 2
    assert p.slope_max_dy_px == 6
    assert p.slope_max_grade == 0.5
    assert p.near_x_px == 5
    assert p.attach_y_px == 3
    assert p.walk_px_per_s == 8.0
    assert p.teleport_s == 0.55
    assert p.drop_base_s == 0.35
    assert p.fall_px_per_s == 60.0
    assert p.max_walk_ms == 4000.0
    assert p.ability_mix_band == 0.3
    assert p.progress_eps_s == 0.05
    assert p.stall_s == 10.0
    assert p.poll_s == 0.05
    assert p.min_settle_s == 0.15
    assert p.settle_timeout_s == 2.0


def test_validate_defaults_returns_same_object():
    p = MovementParams()
    result = validate(p)
    assert isinstance(result, Success)
    assert result.unwrap() is p


@pytest.mark.parametrize(
    "field, kwargs",
    [
        ("arrive_px", {"arrive_px": 0}),
        ("slope_max_dy_px", {"slope_max_dy_px": 0}),
        ("near_x_px", {"near_x_px": -1}),
        ("attach_y_px", {"attach_y_px": 0}),
        ("slope_max_grade", {"slope_max_grade": 0}),
        ("slope_max_grade", {"slope_max_grade": 1.5}),
        ("walk_px_per_s", {"walk_px_per_s": float("nan")}),
        ("walk_px_per_s", {"walk_px_per_s": float("inf")}),
        ("teleport_s", {"teleport_s": -1}),
        ("drop_base_s", {"drop_base_s": 0.0}),
        ("fall_px_per_s", {"fall_px_per_s": 0.0}),
        ("max_walk_ms", {"max_walk_ms": 0.0}),
        ("progress_eps_s", {"progress_eps_s": -0.01}),
        ("stall_s", {"stall_s": float("-inf")}),
        ("poll_s", {"poll_s": 0.0}),
        ("ability_mix_band", {"ability_mix_band": -0.1}),
        ("ability_mix_band", {"ability_mix_band": float("nan")}),
        ("max_walk_ms", {"max_walk_ms": 5001.0}),
        ("min_settle_s", {"min_settle_s": 2.0, "settle_timeout_s": 2.0}),
    ],
)
def test_validate_rejects_bad_field_and_names_it(field, kwargs):
    result = validate(MovementParams(**kwargs))
    assert isinstance(result, Failure)
    assert field in result.failure()


def test_ability_mix_band_zero_is_valid():
    assert isinstance(validate(MovementParams(ability_mix_band=0)), Success)


def test_slope_max_grade_one_and_max_walk_5000_are_valid():
    assert isinstance(validate(MovementParams(slope_max_grade=1.0)), Success)
    assert isinstance(validate(MovementParams(max_walk_ms=5000.0)), Success)


def test_params_are_frozen():
    p = MovementParams()
    with pytest.raises(dataclasses.FrozenInstanceError):
        p.arrive_px = 3  # type: ignore[misc]


def test_params_source_has_no_teleport_reach_literals():
    src = (Path(__file__).resolve().parents[2] / "src" / "route" / "params.py").read_text()
    assert re.search(r"(?<![\w.])(19|23)(?![\w.])", src) is None
