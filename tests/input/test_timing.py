"""Tests for src/input/timing.py (plan A, Task P2; contracts C3 and C4)."""
import math
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from scipy import stats

from src.input.timing import MODEL, TimingModel, TimingType, truncated_cdf

REPO = Path(__file__).parents[2]
N = 20_000
SEED = 20261022

# Every MODEL type except WALK (no single CDF: it depends on the caller's T).
CDF_TYPES = [t for t in TimingType if t in MODEL and t is not TimingType.WALK]
SINGLE_DRAW = {
    TimingType.HOLD: lambda m: m.hold(),
    TimingType.GAP: lambda m: m.gap(),
    TimingType.CHORD_OFFSET: lambda m: m.chord_offset(),
    TimingType.RELEASE_LAG: lambda m: m.release_lag(),
    TimingType.REACTION: lambda m: m.reaction(),
}
# (low, high) support of each truncated type, C4 table.
BOUNDS = {
    TimingType.HOLD: (60.0, 250.0),
    TimingType.GAP: (60.0, 1000.0),
    TimingType.CHORD_OFFSET: (8.0, 100.0),
    TimingType.RELEASE_LAG: (0.0, 60.0),
    TimingType.REACTION: (110.0, 1000.0),
}


def model(seed=SEED):
    return TimingModel(np.random.default_rng(seed))


def draw_many(t, n=N, seed=SEED):
    m = model(seed)
    return np.array([SINGLE_DRAW[t](m) for _ in range(n)])


def truncated_mean(t):
    """Mean of the truncated distribution, by numerical integration of the survival function."""
    lo, hi = BOUNDS[t]
    x = np.linspace(lo, hi, 200_001)
    return lo + np.trapezoid(1.0 - truncated_cdf(t)(x), x)


# ---- (a) determinism and the draws counter -------------------------------------------------------

def six_calls(m):
    return [m.hold(), m.gap(), m.chord_offset(), m.release_lag(), m.reaction(), m.walk(1000.0)]


def test_same_seed_same_sequence():
    a, b = model(7), model(7)
    seq_a = [six_calls(a) for _ in range(50)]
    seq_b = [six_calls(b) for _ in range(50)]
    assert seq_a == seq_b


def test_different_seed_different_sequence():
    assert six_calls(model(7)) != six_calls(model(8))


def test_draws_starts_at_zero_and_rises_by_one_per_call():
    m = model()
    assert m.draws == 0
    expected = 0
    for call in (m.hold, m.gap, m.chord_offset, m.release_lag, m.reaction, lambda: m.walk(1000.0)):
        call()
        expected += 1
        assert m.draws == expected


def test_draws_is_read_only():
    m = model()
    with pytest.raises(AttributeError):
        m.draws = 5


class CountingRng:
    """Delegates to a real Generator and counts the primitive draws the model makes."""

    def __init__(self, seed):
        self._g = np.random.default_rng(seed)
        self.primitive_calls = 0

    def __getattr__(self, name):
        attr = getattr(self._g, name)
        if not callable(attr):
            return attr

        def counted(*args, **kwargs):
            self.primitive_calls += 1
            return attr(*args, **kwargs)

        return counted


def test_draws_counts_values_returned_not_rejected_candidates():
    # walk(0.0): result must be >= 0 and the candidate is N(0, 65), so half are rejected.
    rng = CountingRng(SEED)
    m = TimingModel(rng)
    for _ in range(300):
        m.walk(0.0)
    assert m.draws == 300
    assert rng.primitive_calls > 300, "no candidate was rejected; the test does not exercise rejection"


class ScriptedRng:
    """Replays fixed candidates, so rejection is certain rather than statistical."""

    def __init__(self, **streams):
        self._it = {k: iter(v) for k, v in streams.items()}

    def lognormal(self, mean, sigma):
        return next(self._it["lognormal"])

    def normal(self, loc, scale):
        return next(self._it["normal"])

    def exponential(self, scale):
        return next(self._it["exponential"])


@pytest.mark.parametrize(
    "method, streams, expected",
    [
        ("hold", dict(lognormal=[1.0, 500.0, 100.0]), 100.0),  # [60, 250]: 1.0 and 500.0 rejected
        ("gap", dict(lognormal=[2000.0, 40.0]), 100.0),  # 60 + x, [60, 1000]: 2060 rejected
        ("chord_offset", dict(lognormal=[1.0, 500.0, 25.0]), 25.0),  # [8, 100]
        # [110, 1000]: normal + exponential = 60 and 1500 rejected
        ("reaction", dict(normal=[50.0, 1500.0, 200.0], exponential=[10.0, 0.0, 30.0]), 230.0),
    ],
)
def test_rejected_candidates_are_not_returned_or_counted(method, streams, expected):
    m = TimingModel(ScriptedRng(**streams))
    assert getattr(m, method)() == expected
    assert m.draws == 1


# ---- (b) bounds and raw floats -------------------------------------------------------------------

@pytest.mark.parametrize("t", list(BOUNDS), ids=lambda t: t.value)
def test_samples_within_bounds(t):
    lo, hi = BOUNDS[t]
    xs = draw_many(t, 5000)
    assert xs.min() >= lo
    assert xs.max() <= hi
    assert (xs.max() - xs.min()) > 0


@pytest.mark.parametrize("t", list(BOUNDS), ids=lambda t: t.value)
def test_samples_are_raw_floats_not_quantised(t):
    m = model()
    xs = [SINGLE_DRAW[t](m) for _ in range(200)]
    assert all(type(x) is float for x in xs)
    # a 100 us (0.1 ms) quantisation would make x * 10 an integer
    assert any(abs(x * 10 - round(x * 10)) > 1e-6 for x in xs)


def test_walk_is_raw_float_and_at_least_half_t():
    m = model()
    xs = [m.walk(1000.0) for _ in range(500)]
    assert all(type(x) is float for x in xs)
    assert min(xs) >= 500.0
    assert any(abs(x * 10 - round(x * 10)) > 1e-6 for x in xs)


def test_walk_with_zero_and_small_t_respects_floor():
    m = model()
    assert min(m.walk(0.0) for _ in range(500)) >= 0.0
    assert min(m.walk(100.0) for _ in range(500)) >= 50.0


@pytest.mark.parametrize("bad", [-1.0, float("nan"), float("inf")])
def test_walk_rejects_nonsense_intended_ms(bad):
    with pytest.raises(ValueError):
        model().walk(bad)


# ---- (c) the sampler matches its own CDF ---------------------------------------------------------

@pytest.mark.parametrize("t", CDF_TYPES, ids=lambda t: t.value)
def test_sampler_matches_truncated_cdf(t):
    res = stats.kstest(draw_many(t), truncated_cdf(t))
    assert res.pvalue > 0.01, f"{t}: KS statistic {res.statistic:.4f}, p={res.pvalue:.4g}"


def test_ks_is_sensitive_to_a_wrong_cdf():
    # guard against a vacuous KS: the HOLD samples must NOT match GAP's CDF
    res = stats.kstest(draw_many(TimingType.HOLD), truncated_cdf(TimingType.GAP))
    assert res.pvalue < 1e-6


def test_walk_matches_normal_cdf_built_from_model():
    p = MODEL[TimingType.WALK].params
    T = 1000.0
    sd = math.sqrt(p["sqrt_k"] ** 2 * T**2 + p["sigma_i"] ** 2)
    m = model()
    xs = np.array([m.walk(T) for _ in range(N)])
    # truncation at T/2 is 6.5 SD away, so a plain normal CDF is the reference
    res = stats.kstest(xs, stats.norm(loc=T, scale=sd).cdf)
    assert res.pvalue > 0.01, f"KS statistic {res.statistic:.4f}, p={res.pvalue:.4g}"


def test_truncated_cdf_shape():
    for t in CDF_TYPES:
        lo, hi = BOUNDS[t]
        f = truncated_cdf(t)
        x = np.linspace(lo - 50, hi + 50, 2001)
        y = f(x)
        assert y[0] == 0.0 and y[-1] == 1.0
        assert np.all(np.diff(y) >= 0)
        assert f(np.array([lo]))[0] == pytest.approx(0.0, abs=1e-12)
        assert f(np.array([hi]))[0] == pytest.approx(1.0, abs=1e-12)


def test_truncated_cdf_undefined_for_walk_and_hold_clipped():
    with pytest.raises(ValueError):
        truncated_cdf(TimingType.WALK)
    with pytest.raises(ValueError):
        truncated_cdf(TimingType.HOLD_CLIPPED)



# ---- (d) moments ---------------------------------------------------------------------------------

@pytest.mark.parametrize(
    "t, tol",
    [(TimingType.HOLD, 2.0), (TimingType.GAP, 5.0), (TimingType.REACTION, 3.0)],
    ids=lambda v: getattr(v, "value", v),
)
def test_mean_matches_truncated_distribution_mean(t, tol):
    assert abs(draw_many(t).mean() - truncated_mean(t)) < tol


def test_truncated_means_are_near_c4_means():
    # truncation moves the C4 means only slightly (HOLD 116, GAP 220, REACTION 230)
    assert truncated_mean(TimingType.HOLD) == pytest.approx(116, abs=3)
    assert truncated_mean(TimingType.GAP) == pytest.approx(220, abs=8)
    assert truncated_mean(TimingType.REACTION) == pytest.approx(230, abs=3)


# ---- (e) WALK ------------------------------------------------------------------------------------

def test_walk_sd_for_t_1000():
    m = model()
    xs = np.array([m.walk(1000.0) for _ in range(N)])
    assert xs.std(ddof=1) == pytest.approx(76.3, abs=3)
    assert xs.min() >= 500.0
    assert xs.mean() == pytest.approx(1000.0, abs=3)


def test_model_walk_numbers_are_c4s():
    w = MODEL[TimingType.WALK]
    assert w.params == {"sqrt_k": 0.04, "sigma_i": 65.0, "min_frac": 0.5}
    assert math.sqrt(0.04**2 * 1000.0**2 + 65.0**2) == pytest.approx(76.32, abs=0.01)


# ---- MODEL pins C4 -------------------------------------------------------------------------------

def test_model_keys_are_every_type_except_hold_clipped():
    assert set(MODEL) == set(TimingType) - {TimingType.HOLD_CLIPPED}


def test_model_values_match_c4_table():
    hold, gap, chord = MODEL[TimingType.HOLD], MODEL[TimingType.GAP], MODEL[TimingType.CHORD_OFFSET]
    assert (hold.family, hold.lo, hold.hi) == ("lognormal", 60.0, 250.0)
    assert hold.params["sigma_ln"] == pytest.approx(0.2047, abs=5e-5)
    assert hold.params["mu_ln"] == pytest.approx(4.7326, abs=5e-5)
    assert (gap.family, gap.lo, gap.hi, gap.params["loc"]) == ("lognormal", 60.0, 1000.0, 60.0)
    assert gap.params["sigma_ln"] == pytest.approx(0.5243, abs=5e-5)
    assert gap.params["mu_ln"] == pytest.approx(4.9377, abs=5e-5)
    assert (chord.family, chord.lo, chord.hi) == ("lognormal", 8.0, 100.0)
    assert chord.params["mu_ln"] == pytest.approx(math.log(25))
    assert chord.params["sigma_ln"] == 0.5
    rl = MODEL[TimingType.RELEASE_LAG]
    assert (rl.family, rl.params) == ("uniform", {"low": 0.0, "high": 60.0})
    r = MODEL[TimingType.REACTION]
    assert (r.family, r.lo, r.hi) == ("exgaussian", 110.0, 1000.0)
    assert r.params == {"mu": 195.0, "sigma": 20.0, "tau": 35.0}


# ---- numpy parameter meaning and the lazy scipy import -------------------------------------------

def test_rng_lognormal_takes_parameters_of_the_underlying_normal():
    # numpy docs: "the mean and standard deviation are not the values for the distribution itself,
    # but of the underlying normal distribution". So log(x) has mean mu and SD sigma.
    x = np.random.default_rng(1).lognormal(mean=4.7326, sigma=0.2047, size=200_000)
    assert np.log(x).mean() == pytest.approx(4.7326, abs=0.005)
    assert np.log(x).std() == pytest.approx(0.2047, abs=0.005)


def test_importing_timing_does_not_import_scipy():
    code = (
        "import sys; sys.path.insert(0, sys.argv[1]);"
        "import src.input.timing;"
        "assert 'scipy' not in sys.modules, 'scipy imported at module load'"
    )
    r = subprocess.run([sys.executable, "-I", "-c", code, str(REPO)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
