"""Simulated world for route walking (plan task B9, contract C11).

A terrain of platforms (polylines sorted by x), movement physics for teleport, walk and drop, and injected errors
(no-op, overshoot, landing offset) drawn from one seeded `numpy` `Generator`. Positions are minimap pixels with a
top-left origin (y grows downward). Each move updates `pos` at once and appends a `MoveEffect`; `position_at`
replays the last effect over time (start until the schedule ends, then a linear settle to the end).
"""

import bisect
import json
import math
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

import numpy as np
from returns.pipeline import is_successful
from returns.result import Failure, Result, Success

from src.input.protocol import Key

_ON_SURFACE_PX = 0.5        # a position is on a platform when its surface is within this many px of y
_SIDE_TELEPORT_RISE_PX = 6  # a sideways teleport lands on the first surface at or below y minus this
_WALK_OFF_PX = 0.5          # walking off a platform end looks for the surface this far past the end


@dataclass(frozen=True)
class SimPlatform:
    name: str
    droppable: bool
    points: tuple[tuple[float, float], ...]

    def surface_y(self, x: float) -> float | None:
        """Linear interpolation of the polyline at x; None outside [x0, xn]."""
        xs = [p[0] for p in self.points]
        if x < xs[0] or x > xs[-1]:
            return None
        i = bisect.bisect_left(xs, x)
        if xs[i] == x:
            return self.points[i][1]
        (x0, y0), (x1, y1) = self.points[i - 1], self.points[i]
        return y0 + (y1 - y0) * (x - x0) / (x1 - x0)


@dataclass(frozen=True)
class SimTerrain:
    minimap_size: tuple[int, int]
    platforms: tuple[SimPlatform, ...]

    def surface_below(self, x: float, y_from: float) -> tuple[int, float] | None:
        """The platform (index, y) with the smallest surface y >= y_from at x; None if none."""
        best: tuple[int, float] | None = None
        for i, platform in enumerate(self.platforms):
            y = platform.surface_y(x)
            if y is not None and y >= y_from and (best is None or y < best[1]):
                best = (i, y)
        return best


def _is_number(v: object) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _parse_platform(i: int, raw: object) -> Result[SimPlatform, str]:
    if not isinstance(raw, dict):
        return Failure(f"platform {i} is not an object")
    for key in ("name", "droppable", "points"):
        if key not in raw:
            return Failure(f"platform {i} is missing key {key!r}")
    name, droppable, raw_points = raw["name"], raw["droppable"], raw["points"]
    if not isinstance(name, str):
        return Failure(f"platform {i}: name must be a string")
    if not isinstance(droppable, bool):
        return Failure(f"platform {name!r}: droppable must be a bool")
    if not isinstance(raw_points, list) or len(raw_points) < 2:
        return Failure(f"platform {name!r}: points must be a list of at least 2 [x, y] pairs")
    points: list[tuple[float, float]] = []
    for p in raw_points:
        if not isinstance(p, list) or len(p) != 2 or not all(_is_number(v) for v in p):
            return Failure(f"platform {name!r}: point {p!r} is not an [x, y] pair of numbers")
        if points and p[0] <= points[-1][0]:
            return Failure(f"platform {name!r}: points must be sorted by strictly increasing x")
        points.append((float(p[0]), float(p[1])))
    return Success(SimPlatform(name, droppable, tuple(points)))


def load_terrain(path: Path) -> Result[SimTerrain, str]:
    """Read a terrain fixture: {"minimap_size": [W, H], "platforms": [{"name", "droppable", "points"}, ...]}."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return Failure(f"cannot read terrain {path}: {e}")
    if not isinstance(data, dict):
        return Failure("terrain must be a JSON object")
    for key in ("minimap_size", "platforms"):
        if key not in data:
            return Failure(f"terrain is missing key {key!r}")
    size = data["minimap_size"]
    if not (isinstance(size, list) and len(size) == 2
            and all(isinstance(v, int) and not isinstance(v, bool) and v > 0 for v in size)):
        return Failure(f"minimap_size must be [W, H] with positive ints, got {size!r}")
    if not isinstance(data["platforms"], list):
        return Failure("platforms must be a list")
    platforms: list[SimPlatform] = []
    for i, raw in enumerate(data["platforms"]):
        result = _parse_platform(i, raw)
        if not is_successful(result):
            return result
        platforms.append(result.unwrap())
    return Success(SimTerrain((size[0], size[1]), tuple(platforms)))


@dataclass(frozen=True)
class SimParams:
    walk_px_per_s: float = 8.0
    teleport_settle_s: float = 0.35
    drop_settle_s: float = 0.25
    fall_px_per_s: float = 60.0
    link_latency_s: float = 0.03


@dataclass(frozen=True)
class NoiseConfig:
    no_op_p: float = 0.06
    overshoot_p: float = 0.05
    overshoot_px: tuple[int, int] = (2, 4)
    landing_p: float = 0.10
    landing_px: tuple[int, int] = (1, 2)
    miss_p: float = 0.01
    node_offset_px: int = 0      # B14 shifts each map node's y by U{-k..k} before planning; the terrain stays

    @staticmethod
    def off() -> "NoiseConfig":
        """Every probability 0."""
        return NoiseConfig(no_op_p=0.0, overshoot_p=0.0, landing_p=0.0, miss_p=0.0)


class InjectedError(StrEnum):
    NO_OP = "no_op"
    OVERSHOOT = "overshoot"
    LANDING = "landing"


@dataclass(frozen=True)
class MoveEffect:
    kind: str                                  # "teleport" | "walk" | "drop" | "none"
    start: tuple[float, float]
    end: tuple[float, float]
    t_end_ns: int
    settle_s: float
    injected: tuple[InjectedError, ...]


@dataclass
class _Draw:
    """The injected errors drawn for one move and the px they add."""
    injected: list[InjectedError] = field(default_factory=list)
    overshoot_px: int = 0
    landing_px: int = 0      # signed


class SimWorld:
    def __init__(self, terrain: SimTerrain, reach_h: int, reach_v: int, params: SimParams, noise: NoiseConfig,
                 rng: np.random.Generator, start: tuple[float, float]) -> None:
        self.terrain = terrain
        self.reach_h = reach_h
        self.reach_v = reach_v
        self.params = params
        self.noise = noise
        self.rng = rng
        if self._platform_at(start) is None:
            raise ValueError(f"start {start!r} is not within {_ON_SURFACE_PX} px of any platform surface")
        self.pos: tuple[float, float] = (float(start[0]), float(start[1]))
        self.effects: list[MoveEffect] = []

    # --- helpers --------------------------------------------------------------------------------------------

    def _platform_at(self, pos: tuple[float, float]) -> int | None:
        """The platform whose surface at pos x is within 0.5 px of pos y (the closest one); None if none."""
        best: tuple[int, float] | None = None
        for i, platform in enumerate(self.terrain.platforms):
            y = platform.surface_y(pos[0])
            if y is not None and abs(y - pos[1]) <= _ON_SURFACE_PX and (best is None or abs(y - pos[1]) < best[1]):
                best = (i, abs(y - pos[1]))
        return None if best is None else best[0]

    def _no_op(self) -> bool:
        return bool(self.rng.random() < self.noise.no_op_p)

    def _draw(self, overshoot: bool, landing: bool) -> _Draw:
        """One rng.random() per applicable probability (overshoot, then landing), then the px of each hit."""
        hit_overshoot = overshoot and bool(self.rng.random() < self.noise.overshoot_p)
        hit_landing = landing and bool(self.rng.random() < self.noise.landing_p)
        draw = _Draw()
        if hit_overshoot:
            lo, hi = self.noise.overshoot_px
            draw.overshoot_px = int(self.rng.integers(lo, hi + 1))
            draw.injected.append(InjectedError.OVERSHOOT)
        if hit_landing:
            lo, hi = self.noise.landing_px
            sign = 1 if self.rng.random() < 0.5 else -1
            draw.landing_px = sign * int(self.rng.integers(lo, hi + 1))
            draw.injected.append(InjectedError.LANDING)
        return draw

    def _record(self, kind: str, end: tuple[float, float], now_ns: int, settle_s: float,
                injected: tuple[InjectedError, ...]) -> MoveEffect:
        effect = MoveEffect(kind, self.pos, (float(end[0]), float(end[1])), now_ns, settle_s, injected)
        self.pos = effect.end
        self.effects.append(effect)
        return effect

    def _no_op_effect(self, now_ns: int) -> MoveEffect:
        return self._record("none", self.pos, now_ns, 0.0, (InjectedError.NO_OP,))

    # --- moves ----------------------------------------------------------------------------------------------

    def teleport(self, direction: Key, now_ns: int) -> MoveEffect:
        if direction not in (Key.LEFT, Key.RIGHT, Key.UP, Key.DOWN):
            return self.nothing(now_ns)
        if self._no_op():
            return self._no_op_effect(now_ns)
        x, y = self.pos
        sideways = direction in (Key.LEFT, Key.RIGHT)
        draw = self._draw(overshoot=sideways, landing=True)
        injected = tuple(draw.injected)
        fall_px = self.params.fall_px_per_s
        if sideways:
            sign = 1 if direction == Key.RIGHT else -1
            x_new = x + sign * (self.reach_h + draw.overshoot_px) + draw.landing_px
            y_from = y - _SIDE_TELEPORT_RISE_PX
        elif direction == Key.UP:
            x_new = x + draw.landing_px
            y_from = y - self.reach_v
        else:
            x_new = x + draw.landing_px
            y_from = y + 1
        landing = self.terrain.surface_below(x_new, y_from)
        if landing is None:
            return self._record("teleport", self.pos, now_ns, self.params.teleport_settle_s, injected)
        y_land = landing[1]
        if direction == Key.DOWN:
            fall = max(0.0, (y_land - y) - self.reach_v)
        else:
            fall = max(0.0, y_land - y)
        return self._record("teleport", (x_new, y_land), now_ns, self.params.teleport_settle_s + fall / fall_px,
                            injected)

    def walk(self, direction: Key, held_ms: float, now_ns: int) -> MoveEffect:
        if direction not in (Key.LEFT, Key.RIGHT):
            return self.nothing(now_ns)
        if self._no_op():
            return self._no_op_effect(now_ns)
        x, y = self.pos
        draw = self._draw(overshoot=True, landing=False)
        injected = tuple(draw.injected)
        current = self._platform_at(self.pos)
        if current is None:
            return self._record("walk", self.pos, now_ns, 0.0, injected)
        platform = self.terrain.platforms[current]
        sign = 1 if direction == Key.RIGHT else -1
        x_new = x + sign * (held_ms / 1000 * self.params.walk_px_per_s + draw.overshoot_px)
        x_first, x_last = platform.points[0][0], platform.points[-1][0]
        if x_new > x_last or x_new < x_first:
            x_end = x_last if x_new > x_last else x_first
            below = self.terrain.surface_below(x_end + sign * _WALK_OFF_PX, y + 1)
            # No surface past the end (the map's edge): stop at the end of the current platform.
            end = (x_end, platform.surface_y(x_end)) if below is None else (x_end, below[1])
        else:
            end = (x_new, platform.surface_y(x_new))
        settle_s = max(0.0, end[1] - y) / self.params.fall_px_per_s
        return self._record("walk", end, now_ns, settle_s, injected)

    def drop(self, now_ns: int) -> MoveEffect:
        if self._no_op():
            return self._no_op_effect(now_ns)
        current = self._platform_at(self.pos)
        if current is None or not self.terrain.platforms[current].droppable:
            return self._record("none", self.pos, now_ns, 0.0, ())
        x, y = self.pos
        draw = self._draw(overshoot=False, landing=True)
        injected = tuple(draw.injected)
        x_new = x + draw.landing_px
        landing = self.terrain.surface_below(x_new, y + 1)
        if landing is None:
            return self._record("drop", self.pos, now_ns, self.params.drop_settle_s, injected)
        y_land = landing[1]
        settle_s = self.params.drop_settle_s + (y_land - y) / self.params.fall_px_per_s
        return self._record("drop", (x_new, y_land), now_ns, settle_s, injected)

    def nothing(self, now_ns: int) -> MoveEffect:
        """A schedule that moves nothing: no draw, no move."""
        return self._record("none", self.pos, now_ns, 0.0, ())

    def position_at(self, t_ns: int) -> tuple[float, float]:
        if not self.effects:
            return self.pos
        last = self.effects[-1]
        if t_ns < last.t_end_ns:
            return last.start
        settle_ns = last.settle_s * 1e9
        elapsed = t_ns - last.t_end_ns
        if elapsed >= settle_ns:
            return last.end
        frac = elapsed / settle_ns
        return (last.end[0], last.start[1] + (last.end[1] - last.start[1]) * frac)
