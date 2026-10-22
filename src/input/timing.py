"""Human timing model for the input layer (plan A, task P2; contract C4).

Pure; numpy only at runtime. Every value is drawn from one seeded numpy Generator, independently,
and returned as the generator's raw float in milliseconds (no quantisation here; the action
builders encode to 100 us). Truncation is by rejection, so the reference distribution for a
Kolmogorov-Smirnov test is the truncated CDF (F(x) - F(a)) / (F(b) - F(a)) on [a, b].
"""
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum

import numpy as np


class TimingType(StrEnum):
    HOLD = "hold"
    GAP = "gap"
    CHORD_OFFSET = "chord_offset"
    RELEASE_LAG = "release_lag"
    REACTION = "reaction"
    WALK = "walk"
    # Set by the actions' clip rule (C4); the value is drawn by hold(). Not a MODEL key.
    HOLD_CLIPPED = "hold_clipped"


@dataclass(frozen=True)
class TruncSpec:
    """One row of the C4 table. `params` depend on `family`:

    lognormal: mu_ln, sigma_ln (of the underlying normal), loc (added shift, ms)
    uniform:   low, high
    exgaussian: mu, sigma, tau (normal mu/sigma plus exponential of mean tau)
    walk:      sqrt_k, sigma_i, min_frac (SD = sqrt(k T^2 + sigma_i^2), result >= min_frac * T)
    `lo`/`hi` are the truncation bounds in ms (None where the row depends on T, i.e. WALK).
    RELEASE_LAG's 0/60 are the uniform's support, not a truncation.
    """

    family: str
    params: dict[str, float] = field(default_factory=dict)
    lo: float | None = None
    hi: float | None = None


def _lognormal_params(mean: float, sd: float) -> tuple[float, float]:
    """(mu_ln, sigma_ln) of the underlying normal for a lognormal with the given mean and SD."""
    sigma2 = math.log(1.0 + (sd / mean) ** 2)
    return math.log(mean) - sigma2 / 2.0, math.sqrt(sigma2)


_HOLD_MU, _HOLD_SIGMA = _lognormal_params(116.0, 24.0)
_GAP_MU, _GAP_SIGMA = _lognormal_params(160.0, 90.0)

# C4 table, single source for sampling and for the KS CDFs. Keys: every TimingType but HOLD_CLIPPED.
MODEL: dict[TimingType, TruncSpec] = {
    TimingType.HOLD: TruncSpec(
        "lognormal", {"mu_ln": _HOLD_MU, "sigma_ln": _HOLD_SIGMA, "loc": 0.0}, 60.0, 250.0
    ),
    # 60 + lognormal; the bounds apply to the total
    TimingType.GAP: TruncSpec(
        "lognormal", {"mu_ln": _GAP_MU, "sigma_ln": _GAP_SIGMA, "loc": 60.0}, 60.0, 1000.0
    ),
    TimingType.CHORD_OFFSET: TruncSpec(
        "lognormal", {"mu_ln": math.log(25.0), "sigma_ln": 0.5, "loc": 0.0}, 8.0, 100.0
    ),
    TimingType.RELEASE_LAG: TruncSpec("uniform", {"low": 0.0, "high": 60.0}, 0.0, 60.0),
    TimingType.REACTION: TruncSpec(
        "exgaussian", {"mu": 195.0, "sigma": 20.0, "tau": 35.0}, 110.0, 1000.0
    ),
    TimingType.WALK: TruncSpec("walk", {"sqrt_k": 0.04, "sigma_i": 65.0, "min_frac": 0.5}),
}


@dataclass(frozen=True)
class Sample:
    """One logged draw (C3). `index` is TimingModel.draws just before the value was drawn."""

    type: TimingType
    ms: float
    index: int
    wire_100us: int | None
    step_from: int | None
    step_to: int | None


class TimingModel:
    def __init__(self, rng: np.random.Generator) -> None:
        self._rng = rng
        self._draws = 0

    @property
    def draws(self) -> int:
        """Values returned so far by the six methods (rejected candidates not counted)."""
        return self._draws

    def _candidate(self, spec: TruncSpec) -> float:
        p = spec.params
        if spec.family == "lognormal":
            return p["loc"] + float(self._rng.lognormal(mean=p["mu_ln"], sigma=p["sigma_ln"]))
        if spec.family == "exgaussian":
            return float(self._rng.normal(p["mu"], p["sigma"]) + self._rng.exponential(p["tau"]))
        raise AssertionError(f"no candidate sampler for family {spec.family!r}")

    def _truncated(self, t: TimingType) -> float:
        spec = MODEL[t]
        while True:
            x = self._candidate(spec)
            if spec.lo <= x <= spec.hi:
                break
        self._draws += 1
        return x

    def hold(self) -> float:
        return self._truncated(TimingType.HOLD)

    def gap(self) -> float:
        return self._truncated(TimingType.GAP)

    def chord_offset(self) -> float:
        return self._truncated(TimingType.CHORD_OFFSET)

    def release_lag(self) -> float:
        p = MODEL[TimingType.RELEASE_LAG].params
        x = float(self._rng.uniform(p["low"], p["high"]))
        self._draws += 1
        return x

    def reaction(self) -> float:
        return self._truncated(TimingType.REACTION)

    def walk(self, intended_ms: float) -> float:
        if not (math.isfinite(intended_ms) and intended_ms >= 0):
            raise ValueError(f"intended_ms must be finite and >= 0, got {intended_ms!r}")
        p = MODEL[TimingType.WALK].params
        sd = math.sqrt(p["sqrt_k"] ** 2 * intended_ms**2 + p["sigma_i"] ** 2)
        floor = p["min_frac"] * intended_ms
        while True:
            x = intended_ms + float(self._rng.normal(0.0, sd))
            if x >= floor:
                break
        self._draws += 1
        return x


def truncated_cdf(t: TimingType) -> Callable[[np.ndarray], np.ndarray]:
    """CDF of `t`'s truncated distribution, for KS tests and analysis. Imports scipy lazily.

    Not defined for WALK (it depends on the caller's T) or HOLD_CLIPPED (not a MODEL key).
    """
    if t not in MODEL or t == TimingType.WALK:
        raise ValueError(f"truncated_cdf is not defined for {t!s}")
    from scipy import stats

    spec = MODEL[t]
    p = spec.params
    if spec.family == "lognormal":
        dist = stats.lognorm(s=p["sigma_ln"], scale=math.exp(p["mu_ln"]), loc=p["loc"])
    elif spec.family == "exgaussian":
        dist = stats.exponnorm(K=p["tau"] / p["sigma"], loc=p["mu"], scale=p["sigma"])
    else:  # uniform
        dist = stats.uniform(loc=p["low"], scale=p["high"] - p["low"])
    f_lo, f_hi = dist.cdf(spec.lo), dist.cdf(spec.hi)

    def cdf(x: np.ndarray) -> np.ndarray:
        return np.clip((dist.cdf(x) - f_lo) / (f_hi - f_lo), 0.0, 1.0)

    return cdf
