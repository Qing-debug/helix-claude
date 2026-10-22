"""Soak-log analysis: timing realism and delivered timing, the PAr-D1 criteria (plan A, Task H4; ruling C).

Reads a C5 run dir (``commands.jsonl``, ``keys.jsonl``, optional ``pongs.jsonl``). Gated checks print PASS/FAIL;
reported values print INFO / n/a; the CLI exits non-zero on any FAIL.

- Timing realism (spec:L60 as amended): a KS test per type (hold, gap, chord offset, reaction) on the values the
  timing layer chose (``samples[].chosen_ms``, never ``wire_100us``; for reaction the drawn R ``reaction_ms``, never
  ``waited_ms``), n >= 1 000, PASS if p >= 0.01. HOLD's input includes every ``hold_clipped`` sample's drawn float
  (C4, PA-rev m13). Gap runs: the chosen GAP values in soak order; FAIL if any window of 5 consecutive values occurs
  twice (exact float equality).
- Delivered timing (spec:L59 as amended): every HOLD, WALK, ``hold_clipped``, CHORD_OFFSET and GAP sample of a
  measured DONE OK command is paired with the listener transitions its ``step_from``/``step_to`` map to through H3's
  alignment (``Alignment.pairs``: schedule step index -> observed transition). Reference = ``chosen_ms``, except
  ``hold_clipped``: its scheduled (clipped) interval in ``steps`` (C4). Each type is its own share; PASS if
  ``|measured - reference| <= 20 ms`` for >= 95% of its pairs; an unpairable sample is a miss. ``hold_clipped`` with
  no pairs is not gated. Chord order: FAIL on any chord whose second key's down is seen before (or without) the first
  key's down, read from the observed transitions by VK (an inversion is not in ``pairs``).
- ``pair_samples`` / ``pair_command`` are importable for V1's M2 / M4 histograms (A-PF-D3). Pairing reuses H3's
  ``load_run`` / ``match_delivery`` / ``Alignment.pairs`` (A-PF m7); percentiles are H3's nearest-rank.

Run: ``python -m src.input.harness.realism <run dir>``
"""
from __future__ import annotations

import argparse
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import numpy as np
from scipy import stats

from src.input.harness.analyze import (NS_PER_MS, Check, CommandMatch, DeliveryMatch, Row, commands_of,
                                       expected_transitions, is_ok, load_run, match_delivery, percentile,
                                       read_jsonl)
from src.input.timing import TimingType, truncated_cdf

T = TimingType
KS_TYPES = (T.HOLD, T.GAP, T.CHORD_OFFSET, T.REACTION)
DELIVERED_TYPES = (T.HOLD, T.WALK, T.HOLD_CLIPPED, T.CHORD_OFFSET, T.GAP)
KS_MIN_N = 1_000
KS_ALPHA = 0.01
GAP_RUN = 5
DELIVERED_TOL_MS = 20.0
DELIVERED_SHARE_PCT = 95
COLLAPSE_MS = 1.0
CI_UNIT_MS = 1.25                 # PONG itvl is in 1.25 ms units (C1)

# spec docs/superpowers/specs/2026-10-21-p1a-input-over-bluetooth-design.md, targets table (L59-L60, as amended)
TARGET_KS = ("fits its model (KS test, α = 0.01, ≥1,000 samples each), tested on the values the timing layer "
             "chose")
TARGET_GAP_RUNS = "No run of ≥5 consecutive chosen gaps repeats anywhere in the soak."
TARGET_DELIVERED = "within ±20 ms of the value the timing layer chose, for ≥95% of each type"
TARGET_CHORD = "The second key of every chord is never seen before the first."


# ---- chosen values -----------------------------------------------------------------------

def chosen_samples(commands: Sequence[Row]) -> dict[TimingType, list[Row]]:
    """Chosen samples per type, in soak order. HOLD also holds every ``hold_clipped`` sample (C4, PA-rev m13);
    REACTION gets one sample-shaped row per command, ``chosen_ms`` = its drawn R (``reaction_ms``). Rows with null
    ``samples`` / ``reaction_ms`` (Failure, FOCUS_LOST; C5) add nothing."""
    out: dict[TimingType, list[Row]] = {t: [] for t in TimingType}
    for r in commands:
        for s in r.get("samples") or []:
            t = TimingType(s["type"])
            out[t].append(s)
            if t == T.HOLD_CLIPPED:
                out[T.HOLD].append(s)
        if r.get("reaction_ms") is not None:
            out[T.REACTION].append({"type": T.REACTION.value, "chosen_ms": r["reaction_ms"], "wire_100us": None,
                                    "index": r.get("reaction_index"), "step_from": None, "step_to": None})
    return out


def check_ks(t: TimingType, samples: Sequence[Row]) -> Check:
    """KS of the samples' ``chosen_ms`` against ``truncated_cdf(t)``; n < 1 000 -> FAIL (insufficient samples)."""
    values = np.array([s["chosen_ms"] for s in samples], dtype=float)
    name = f"KS {t.value}"
    if len(values) < KS_MIN_N:
        return Check(name, "FAIL", f"(insufficient samples) n {len(values)} < {KS_MIN_N} (target: {TARGET_KS})")
    res = stats.kstest(values, truncated_cdf(t))
    verdict = "PASS" if res.pvalue >= KS_ALPHA else "FAIL"
    return Check(name, verdict, f"D {res.statistic:.4f} p {res.pvalue:.4g} over {len(values)} chosen values "
                 f"(target: {TARGET_KS})")


def gap_stream(commands: Sequence[Row]) -> list[float]:
    """The chosen GAP values (gaps within one action) in soak order."""
    return [s["chosen_ms"] for r in commands for s in r.get("samples") or [] if s["type"] == T.GAP.value]


def check_gap_runs(gaps: Sequence[float]) -> Check:
    first: dict[tuple[float, ...], int] = {}
    repeats: list[tuple[int, int]] = []
    for i in range(len(gaps) - GAP_RUN + 1):
        w = tuple(gaps[i:i + GAP_RUN])
        if w in first:
            repeats.append((first[w], i))
        else:
            first[w] = i
    details = [f"gaps {a}..{a + GAP_RUN - 1} repeat at {b}..{b + GAP_RUN - 1}" for a, b in repeats[:10]]
    return Check("Gap runs", "FAIL" if repeats else "PASS",
                 f"{len(repeats)} repeated runs of {GAP_RUN} over {len(gaps)} chosen gaps (target: {TARGET_GAP_RUNS})",
                 details)


# ---- delivered timing: pairing (importable for V1) ------------------------------------------

@dataclass(frozen=True)
class Pair:
    seq: int
    type: TimingType                  # the sample's own type (hold_clipped stays hold_clipped)
    sample: Row                       # the C5 samples[] entry
    reference_ms: float               # chosen_ms; for hold_clipped the scheduled (clipped) interval
    measured_ms: float | None         # listener interval step_from -> step_to; None when a transition is unpaired

    @property
    def hit(self) -> bool:
        return self.measured_ms is not None and abs(self.measured_ms - self.reference_ms) <= DELIVERED_TOL_MS


def scheduled_ms(steps: Sequence[Sequence[int]], step_from: int, step_to: int) -> float:
    """The interval steps[step_from] -> steps[step_to] spans in the schedule, in ms."""
    return sum(delta for delta, _ in steps[step_from + 1:step_to + 1]) / 10


def pair_command(m: CommandMatch) -> list[Pair]:
    """One Pair per HOLD / WALK / hold_clipped / CHORD_OFFSET / GAP sample of a DONE OK command (none for a failed
    one). Measured = qpc difference of the observed transitions H3's alignment pairs with the two steps."""
    if m.alignment is None:
        return []
    out = []
    for s in m.row["samples"]:
        t = TimingType(s["type"])
        if t not in DELIVERED_TYPES:
            continue
        frm, to = s["step_from"], s["step_to"]
        ref = scheduled_ms(m.row["steps"], frm, to) if t == T.HOLD_CLIPPED else s["chosen_ms"]
        a, b = m.alignment.pairs.get(frm), m.alignment.pairs.get(to)
        measured = None if a is None or b is None else (m.observed[b]["qpc_ns"] - m.observed[a]["qpc_ns"]) / NS_PER_MS
        out.append(Pair(m.seq, t, s, ref, measured))
    return out


def pair_samples(dm: DeliveryMatch) -> list[Pair]:
    """Pairs of every measured command (H3's ``match_delivery``: before LISTENER_DEAD, FOCUS_LOST excluded)."""
    return [p for m in dm.commands for p in pair_command(m)]


def check_delivered(t: TimingType, pairs: Sequence[Pair]) -> Check:
    mine = [p for p in pairs if p.type == t]
    name = f"Delivered timing {t.value}"
    ref = "its scheduled (clipped) interval" if t == T.HOLD_CLIPPED else "chosen_ms"
    if not mine:
        if t == T.HOLD_CLIPPED:
            return Check(name, "n/a", "hold_clipped n = 0 (not gated)")
        return Check(name, "FAIL", f"no pairs (target: {TARGET_DELIVERED})")
    hits = sum(p.hit for p in mine)
    unpaired = sum(p.measured_ms is None for p in mine)
    verdict = "PASS" if hits * 100 >= DELIVERED_SHARE_PCT * len(mine) else "FAIL"
    misses = [p for p in mine if not p.hit]
    details = [f"seq {p.seq}: " + ("unpaired" if p.measured_ms is None else
                                   f"measured {p.measured_ms:.3f} ms vs {p.reference_ms:.3f} ms") for p in misses[:10]]
    if len(misses) > 10:
        details.append(f"... {len(misses) - 10} more misses")
    return Check(name, verdict, f"{hits}/{len(mine)} within ±{DELIVERED_TOL_MS:g} ms of {ref} "
                 f"({100 * hits / len(mine):.2f}%), unpaired {unpaired} (target: {TARGET_DELIVERED})", details)


@dataclass(frozen=True)
class ChordOrder:
    seq: int
    first_vk: int
    second_vk: int
    first_down_ns: int | None         # first observed down of that VK in the command's window at or after sent_ns
    second_down_ns: int | None

    @property
    def violation(self) -> bool:
        return self.second_down_ns is not None and (self.first_down_ns is None
                                                    or self.second_down_ns < self.first_down_ns)


def chord_orders(dm: DeliveryMatch) -> list[ChordOrder]:
    """One entry per CHORD_OFFSET sample of a measured DONE OK command, read from ``observed`` by VK."""
    out = []
    for m in dm.commands:
        if m.alignment is None:
            continue
        exp = expected_transitions(m.row)
        for s in m.row["samples"]:
            if s["type"] != T.CHORD_OFFSET.value:
                continue
            v1, v2 = exp[s["step_from"]][0], exp[s["step_to"]][0]
            sent = m.row["sent_ns"]      # nothing bot-made goes down before it is sent (A-H3-R m4, A-H4-R n1)
            d1 = next((e["qpc_ns"] for e in m.observed if e["vk"] == v1 and e["down"] and e["qpc_ns"] >= sent), None)
            d2 = next((e["qpc_ns"] for e in m.observed if e["vk"] == v2 and e["down"] and e["qpc_ns"] >= sent), None)
            out.append(ChordOrder(m.seq, v1, v2, d1, d2))
    return out


def check_chord_order(dm: DeliveryMatch) -> Check:
    chords = chord_orders(dm)
    bad = [c for c in chords if c.violation]
    unseen = [c for c in chords if c.second_down_ns is None]
    details = [f"seq {c.seq}: second key {c.second_vk:#04x} down at {c.second_down_ns}, first key {c.first_vk:#04x} "
               f"down at {c.first_down_ns}" for c in bad]
    if unseen:
        details.append(f"second key-down not seen (Delivery drop, not an order check): {len(unseen)} "
                       f"(seq {', '.join(str(c.seq) for c in unseen[:10])})")
    return Check("Chord order", "FAIL" if bad else "PASS",
                 f"violations {len(bad)} over {len(chords)} chords (target: {TARGET_CHORD})", details)


# ---- reported, not gated (PAr-D1.md:109-111) ------------------------------------------------

def _p50_p99(values: Sequence[float]) -> str:
    return f"p50 {percentile(values, 50):.3f} ms p99 {percentile(values, 99):.3f} ms n {len(values)}"


def reaction_residual(dm: DeliveryMatch) -> Check:
    """First key-down − stimulus_ns − R per measured DONE OK command whose wait was not clamped (C4, PA-rev m23)."""
    vals, clamped = [], 0
    for m in dm.commands:
        r = m.row
        if m.alignment is None or r["reaction_ms"] is None:
            continue
        if not r["waited_ms"] > 0:
            clamped += 1
            continue
        if m.first_down_ns is not None:
            vals.append((m.first_down_ns - r["stimulus_ns"]) / NS_PER_MS - r["reaction_ms"])
    head = _p50_p99(vals) if vals else "n/a n 0"
    return Check("Reaction residual", "INFO", f"{head}, clamped {clamped} excluded (first key-down − stimulus − R, "
                 "the send → key-down latency; reported, not gated)")


def gaps_between_actions(dm: DeliveryMatch) -> Check:
    """Last observed key-up of command k → first observed key-down of command k+1 (consecutive measured OK)."""
    vals = []
    for prev, nxt in zip(dm.commands, dm.commands[1:]):
        if prev.alignment is None or nxt.alignment is None or nxt.first_down_ns is None:
            continue
        up = next((e["qpc_ns"] for e in reversed(prev.observed) if not e["down"]), None)
        if up is not None:
            vals.append((nxt.first_down_ns - up) / NS_PER_MS)
    head = f"{_p50_p99(vals)} min {min(vals):.3f} ms" if vals else "n/a n 0"
    return Check("Gaps between actions", "INFO", f"{head} (last key-up → next action's first key-down, measured; "
                 "reported, not gated)")


def measured_ks_d(pairs: Sequence[Pair], dm: DeliveryMatch) -> Check:
    """KS D of the measured values per type (CI / jitter diagnostic, PAr-D1 C). Reaction measured = first key-down
    − stimulus_ns."""
    measured: dict[TimingType, list[float]] = {t: [p.measured_ms for p in pairs if p.type == t
                                                   and p.measured_ms is not None] for t in KS_TYPES}
    measured[T.REACTION] = [(m.first_down_ns - m.row["stimulus_ns"]) / NS_PER_MS for m in dm.commands
                            if m.alignment is not None and m.first_down_ns is not None]
    parts = []
    for t in KS_TYPES:
        v = measured[t]
        parts.append(f"{t.value} D {stats.kstest(np.array(v), truncated_cdf(t)).statistic:.4f} n {len(v)}"
                     if v else f"{t.value} n/a n 0")
    return Check("Measured KS D", "INFO", ", ".join(parts) + " (CI/jitter diagnostic; reported, not gated)")


def chord_collapse(pairs: Sequence[Pair]) -> Check:
    offs = [p.measured_ms for p in pairs if p.type == T.CHORD_OFFSET and p.measured_ms is not None]
    n = sum(v < COLLAPSE_MS for v in offs)
    pct = f"{100 * n / len(offs):.2f}%" if offs else "n/a"
    return Check("Chord collapse", "INFO", f"{n}/{len(offs)} chord offsets measured < {COLLAPSE_MS:g} ms ({pct}; "
                 "reported, not gated)")


def walk_error(commands: Sequence[Row]) -> Check:
    errs = [s["chosen_ms"] - r["intended_ms"] for r in commands if is_ok(r) and r.get("intended_ms") is not None
            for s in r["samples"] if s["type"] == T.WALK.value]
    mean = f"{statistics.fmean(errs):.3f} ms" if errs else "n/a"
    sd = f"{statistics.stdev(errs):.3f} ms" if len(errs) >= 2 else "n/a"
    return Check("WALK error", "INFO", f"mean {mean} SD {sd} n {len(errs)} (chosen_ms − intended_ms per Success "
                 "WALK row; reported, not gated)")


def ci_history(run_dir: Path | str) -> Check:
    path = Path(run_dir) / "pongs.jsonl"
    if not path.exists():
        return Check("CI history", "INFO", "pongs.jsonl missing (reported, not gated)")
    pongs = read_jsonl(path)
    segs: list[Row] = []
    for p in pongs:
        if not segs or p["itvl_units"] != segs[-1]["itvl_units"]:
            segs.append(p)
    details = [f"qpc_ns {p['qpc_ns']}: CI {p['itvl_units'] * CI_UNIT_MS:.2f} ms" for p in segs[:50]]
    if len(segs) > 50:
        details.append(f"... {len(segs) - 50} more changes")
    start = f"CI {segs[0]['itvl_units'] * CI_UNIT_MS:.2f} ms at start" if segs else "no PONGs"
    return Check("CI history", "INFO", f"{len(pongs)} PONGs, {start}, {max(len(segs) - 1, 0)} changes "
                 "(reported, not gated)", details)


def sample_counts(chosen: dict[TimingType, list[Row]], commands: Sequence[Row], dm: DeliveryMatch) -> Check:
    counts = ", ".join(f"{t.value} {len(chosen[t])}" for t in TimingType if t != T.HOLD_CLIPPED)
    return Check("Sample counts", "INFO", f"chosen: {counts} (hold includes hold_clipped)",
                 [f"hold_clipped count {len(chosen[T.HOLD_CLIPPED])}",
                  f"commands {len(commands)}, DONE OK {sum(is_ok(r) for r in commands)}, measured {len(dm.commands)}, "
                  f"unmeasured {len(dm.unmeasured)} (after LISTENER_DEAD; see analyze.py)"])


# ---- entry points ----------------------------------------------------------------------

def realism(run_dir: Path | str) -> list[Check]:
    run = load_run(run_dir)
    cmds = commands_of(run)
    dm = match_delivery(run)
    chosen = chosen_samples(cmds)
    pairs = pair_samples(dm)
    checks = [check_ks(t, chosen[t]) for t in KS_TYPES]
    checks.append(check_gap_runs(gap_stream(cmds)))
    checks += [check_delivered(t, pairs) for t in DELIVERED_TYPES]
    checks.append(check_chord_order(dm))
    checks += [reaction_residual(dm), gaps_between_actions(dm), measured_ks_d(pairs, dm), chord_collapse(pairs),
               walk_error(cmds), ci_history(run_dir), sample_counts(chosen, cmds, dm)]
    return checks


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m src.input.harness.realism", description=__doc__.splitlines()[0])
    ap.add_argument("run_dir", type=Path, help="soak run dir (C5)")
    args = ap.parse_args(argv)
    checks = realism(args.run_dir)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")   # targets quote "≥"/"±"; a redirected stdout is cp1252 on Windows
    for c in checks:
        print("\n".join(c.render()))
    return 1 if any(c.failed for c in checks) else 0


if __name__ == "__main__":
    sys.exit(main())
