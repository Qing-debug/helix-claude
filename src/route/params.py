import math
from dataclasses import dataclass, fields

from returns.result import Failure, Result, Success


@dataclass(frozen=True)
class MovementParams:
    arrive_px: int = 2             # leg target reached when |dx| <= 2 and |dy| <= 2 (MapleAgent visit radius, G22)
    slope_max_dy_px: int = 6       # DC2
    slope_max_grade: float = 0.5   # DC2: |dy| <= grade * |dx|
    near_x_px: int = 5             # DC3
    attach_y_px: int = 3           # DC5
    walk_px_per_s: float = 8.0     # ESTIMATE, unmeasured; B20/B21 measure it
    teleport_s: float = 0.55       # ESTIMATE: keys + animation of one teleport
    drop_base_s: float = 0.35      # ESTIMATE: keys + start of a drop
    fall_px_per_s: float = 60.0    # ESTIMATE
    max_walk_ms: float = 4000.0    # one walk is at most this; longer distances are re-planned
    ability_mix_band: float = 0.3  # PB-D3; 0 makes the down choice deterministic
    progress_eps_s: float = 0.05   # a new best cost-to-go must beat the old by more than this
    stall_s: float = 10.0          # specB:L64
    poll_s: float = 0.05           # position poll interval while settling or waiting
    min_settle_s: float = 0.15     # wait at least this after a move before settling
    settle_timeout_s: float = 2.0


_MAX_WALK_MS_LIMIT = 5000.0  # a WALK draw at T = 5000 has SD ~210 ms, far below the 6553.5 ms per-step cap (G12/G14)


def _is_int(v: object) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def validate(p: MovementParams) -> Result[MovementParams, str]:
    for f in fields(p):
        v = getattr(p, f.name)
        if f.type in (int, "int"):
            if not _is_int(v) or v < 1:
                return Failure(f"{f.name} must be an int >= 1, got {v!r}")
        elif f.name == "ability_mix_band":
            if not _is_real(v) or v < 0:
                return Failure(f"{f.name} must be finite and >= 0, got {v!r}")
        else:
            if not _is_real(v) or v <= 0:
                return Failure(f"{f.name} must be finite and > 0, got {v!r}")
    if p.slope_max_grade > 1:
        return Failure(f"slope_max_grade must be <= 1, got {p.slope_max_grade!r}")
    if p.max_walk_ms > _MAX_WALK_MS_LIMIT:
        return Failure(f"max_walk_ms must be <= {_MAX_WALK_MS_LIMIT}, got {p.max_walk_ms!r}")
    if not p.min_settle_s < p.settle_timeout_s:
        return Failure(
            f"min_settle_s ({p.min_settle_s!r}) must be < settle_timeout_s ({p.settle_timeout_s!r})"
        )
    return Success(p)


def _is_real(v: object) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
