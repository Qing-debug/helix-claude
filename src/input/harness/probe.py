"""Measurement probes for the input link (plan A, Task V1): fixed scripts through `InputLayer` and the listener.

No copies (A-PF-D3): the C5 run writer, the focus policy and the start wait come from `soak.py` (H2), Delivery
matching and the M7 split from `analyze.py` (H3), the sample pairing from `realism.py` (H4).

A run is ``data/probe/<run>/``; each probe writes a complete C5 run dir ``data/probe/<run>/<probe>/`` with its own
`BleLink`, listener (``keys.jsonl``) and `RunWriter`, so analyze.py / realism.py read it unchanged. Per run:

1. Step 1, MapleStory check (hard rule 2, `soak.maple_check`) and step 2, the board port (hard rule 3: exactly one
   port with VID 0x303A / PID 0x1001 in ``arduino-cli board list --format json``), before any listener, link or
   run dir exists. Either one failing: BLOCKED (exit 3).
2. Per probe, in order: open the writer, MapleStory check, start the listener in the probe dir, the start wait
   (`soak.wait_for_click`: one bring-to-foreground try, then the operator clicks the sink; a timeout is BLOCKED),
   connect, then the probe's commands. Each command goes through `soak.run_command` (focus policy, `stimulus_ns`,
   the C5 row, a drain); the MapleStory check repeats every 60 s.
3. A command that ends `FOCUS_LOST` or with a link loss (NOT_CONNECTED / LINK_LOST), a MapleStory process, a
   failed connect or listener, or Ctrl-C stops probe.py like the soak (PA-rev m15, A-H2-R-D2): `release_all()`,
   `session.json` written, link closed, listener stopped, non-zero exit; the probes after it are not run and are
   named on stderr.
4. After a probe's files are closed its report runs on them; its lines go to stdout and ``report.txt``. A report
   may carry `[review]` items and a `stop` (e.g. a silent drop): a stop ends the run after that probe (exit 4).

Run: ``python -m src.input.harness.probe [--probes delivery,...] [--run NAME | --out DIR] [--seed N] [--cli EXE]``
or ``python -m src.input.harness.probe --run-sheet`` (prints the V1 live run sheet; runs nothing).
"""
from __future__ import annotations

import argparse
import bisect
import contextlib
import datetime
import json
import logging
import math
import subprocess
import sys
import threading
import time
import zlib
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence

import numpy as np
from returns.pipeline import is_successful
from returns.result import Failure, Result, Success
from scipy import stats

from src.input.harness import analyze, listener, realism, soak
from src.input.harness.soak import (EXIT_ABORT, EXIT_INTERRUPTED, EXIT_OK, EXIT_REFUSED, FOCUS_RECHECKS,
                                    START_WAIT_S, Command, CommandOutcome, RunWriter)
from src.input.actions import Plan
from src.input.input_layer import ActionRecord, InputLayer
from src.input.link import BleLink, InputError, SendReport, SessionInfo
from src.input.protocol import MAX_STEPS, Key, Step, validate_schedule
from src.input.timing import TimingModel, TimingType, truncated_cdf

# Seams the tests monkeypatch (as soak.py); _clock is perf_counter_ns, the clock of every C5 file.
_clock = time.perf_counter_ns
_sleep = time.sleep

EXIT_BLOCKED = 3  # a precondition failed (MapleStory, board port, sink never in front); nothing was sent in that probe
EXIT_STOP = 4  # a probe's report asked to stop (plan V1: "stop and report")

PROBE_ROOT = Path("data/probe")
CLI_DEFAULT = ("C:/Users/Qing_programming/AppData/Local/Programs/Arduino IDE/resources/app/lib/backend/resources/"
               "arduino-cli.exe")  # plan A's $CLI
BOARD_VID, BOARD_PID = "0x303a", "0x1001"  # the board's native USB-Serial/JTAG (hard rule 3)


# ---- step 2: the board port (hard rule 3) ----------------------------------------------------------

def _board_list_json(cli: str) -> Result[str, str]:
    """stdout of ``<cli> board list --format json``."""
    try:
        out = subprocess.run([cli, "board", "list", "--format", "json"], capture_output=True, text=True,
                             errors="replace", timeout=60)
    except (OSError, subprocess.SubprocessError) as e:
        return Failure(f"arduino-cli board list failed: {e}")
    if out.returncode != 0:
        return Failure(f"arduino-cli board list exited {out.returncode}: {out.stderr.strip()}")
    return Success(out.stdout)


def board_ports(text: str) -> list[str]:
    """Addresses of the detected ports with VID 0x303A and PID 0x1001 (any case), hard rule 3's filter."""
    out = []
    for p in json.loads(text).get("detected_ports", []):
        port = p.get("port") or {}
        props = port.get("properties") or {}
        if props.get("vid", "").lower() == BOARD_VID and props.get("pid", "").lower() == BOARD_PID:
            out.append(port["address"])
    return out


def board_port(cli: str) -> Result[str, str]:
    """The one board port, or why there is not exactly one (with the board list quoted)."""
    listed = _board_list_json(cli)
    if not is_successful(listed):
        return listed
    text = listed.unwrap()
    try:
        ports = board_ports(text)
    except (ValueError, KeyError, AttributeError) as e:
        return Failure(f"cannot read arduino-cli board list output ({e}): {text}")
    if len(ports) != 1:
        return Failure(f"expected exactly one board port (VID 0x303A / PID 0x1001), found {len(ports)} {ports}; "
                       f"arduino-cli board list --format json: {text}")
    return Success(ports[0])


# ---- probes ------------------------------------------------------------------------------------

@dataclass(frozen=True)
class ProbeReport:
    lines: list[str]  # printed and written to report.txt
    review: list[str] = field(default_factory=list)  # [review] items for the task report
    stop: str | None = None  # why probe.py must stop after this probe (None: go on)


class ProbeAbort(Exception):
    """Stops probe.py like the soak's abort path (release_all, session.json, non-zero exit)."""

    def __init__(self, message: str, code: int = EXIT_ABORT) -> None:
        super().__init__(message)
        self.code = code


class ProbeContext:
    """What a probe's `commands(ctx)` drives: the link, the InputLayer on the probe's model, the writer, the sink
    and the probe's own generator. `command()` / `issue()` number the rows from 0."""

    def __init__(self, name: str, run_dir: Path, writer: RunWriter, sink: soak.Sink, link: BleLink,
                 layer: InputLayer, rng: np.random.Generator, session: SessionInfo,
                 periodic_check: Callable[[], None]) -> None:
        self.name = name
        self.run_dir = run_dir
        self.writer = writer
        self.sink = sink
        self.link = link
        self.layer = layer
        self.rng = rng
        self.session = session
        self._periodic_check = periodic_check
        self.seq = 0

    def command(self, action: str, call: Callable[[int], Result[ActionRecord, InputError]],
                intended_ms: float | None = None) -> CommandOutcome:
        """`soak.run_command` with the next seq; FOCUS_LOST and a link loss raise `ProbeAbort` (H2's rules)."""
        self._periodic_check()
        seq = self.seq
        outcome = soak.run_command(self.writer, self.sink, seq, action, call, intended_ms)
        self.seq += 1
        if outcome.focus_lost:
            raise ProbeAbort(f"FOCUS_LOST in probe {self.name} at seq {seq}: the sink was not back after "
                             f"{FOCUS_RECHECKS} s")
        if not is_successful(outcome.result) and outcome.result.failure() in soak.LINK_LOSS:
            raise ProbeAbort(f"{outcome.result.failure()} in probe {self.name} at seq {seq}: the link was lost")
        return outcome

    def issue(self, cmd: Command) -> CommandOutcome:
        """One soak `Command` through `soak.dispatch`."""
        return self.command(cmd.action, lambda s: soak.dispatch(self.layer, cmd, s), cmd.intended_ms)


@dataclass(frozen=True)
class Probe:
    name: str
    commands: Callable[[ProbeContext], None]
    report: Callable[[Path], ProbeReport]  # runs on the closed probe dir
    model: Callable[[np.random.Generator], TimingModel] = TimingModel
    duration: str = ""  # expected duration for the run sheet (inferred)


def probe_streams(seed: int, name: str) -> tuple[np.random.Generator, np.random.Generator]:
    """(timing model, probe's own draws) for one probe: a function of the seed and the probe's name only, so a
    probe run alone draws what it draws in a full run."""
    timing, own = np.random.SeedSequence([seed, zlib.crc32(name.encode())]).spawn(2)
    return np.random.default_rng(timing), np.random.default_rng(own)


# ---- probe: every command type (plan V1 step 3, first bullet) ---------------------------------------

DELIVERY_N = 20
DELIVERY_WALK_MS = 1000.0
DELIVERY_SEQUENCE_LEN = 4


def delivery_commands(ctx: ProbeContext) -> None:
    """Tap each of the 7 keys x20, walk 1 s x20 (Left / Right alternating), teleport each direction x20, drop x20,
    sequence of 4 x20 (keys drawn with repeats from soak's SEQUENCE_KEYS, no Alt)."""
    for key in soak.TAP_KEYS:
        for _ in range(DELIVERY_N):
            ctx.issue(Command("tap", (key,)))
    for i in range(DELIVERY_N):
        ctx.issue(Command("walk", (soak.WALK_KEYS[i % len(soak.WALK_KEYS)],), DELIVERY_WALK_MS))
    for direction in soak.ARROWS:
        for _ in range(DELIVERY_N):
            ctx.issue(Command("teleport", (direction,)))
    for _ in range(DELIVERY_N):
        ctx.issue(Command("drop"))
    for _ in range(DELIVERY_N):
        idx = ctx.rng.integers(len(soak.SEQUENCE_KEYS), size=DELIVERY_SEQUENCE_LEN)
        ctx.issue(Command("sequence", tuple(soak.SEQUENCE_KEYS[i] for i in idx)))


def delivery_report(run_dir: Path) -> ProbeReport:
    """H3's Listener liveness and Delivery checks on this probe's dir. Expected: PASS, no failed command."""
    run = analyze.load_run(run_dir)
    dm = analyze.match_delivery(run)
    checks = [analyze.check_liveness(dm), analyze.check_delivery(run, dm)]
    lines = [line for c in checks for line in c.render()]
    counts = Counter(r["action"] for r in analyze.commands_of(run))
    lines.append("commands: " + ", ".join(f"{a} {n}" for a, n in counts.items()))
    passed = not any(c.failed for c in checks)
    lines.append(f"delivery probe: {'PASS' if passed else 'FAIL'} (expected: listener transitions equal the "
                 "commanded transitions exactly, no failed command)")
    review = [] if passed else ["every-command-type probe: " + "; ".join(f"{c.name} {c.verdict} {c.summary}"
                                                                         for c in checks if c.failed)]
    return ProbeReport(lines, review)


# ---- shared report helpers ------------------------------------------------------------------------

def summary(values: Sequence[float], unit: str = " ms") -> str:
    """n, min, p50, p99 (H3's nearest-rank percentile), max."""
    if not values:
        return "n/a n 0"
    p = analyze.percentile
    return (f"n {len(values)} min {min(values):.3f}{unit} p50 {p(values, 50):.3f}{unit} p99 {p(values, 99):.3f}{unit} "
            f"max {max(values):.3f}{unit}")


def histogram(title: str, values: Sequence[float], lo: float, hi: float, bins: int, unit: str = " ms") -> list[str]:
    """A text histogram over [lo, hi] in `bins` equal bins (numpy's: the last bin includes hi), plus the counts
    outside the range."""
    if not values:
        return [f"{title}: n/a n 0"]
    v = np.asarray(values, dtype=float)
    counts, edges = np.histogram(v, bins=bins, range=(lo, hi))
    peak = max(int(counts.max()), 1)
    lines = [f"{title}: {summary(values, unit)}; below {lo:g}: {int((v < lo).sum())}, above {hi:g}: "
             f"{int((v > hi).sum())}"]
    for i, c in enumerate(counts):
        close = "]" if i == len(counts) - 1 else ")"
        lines.append(f"  [{edges[i]:8.3f}, {edges[i + 1]:8.3f}{close} {int(c):5d} {'#' * round(40 * int(c) / peak)}")
    return lines


def _pongs(run_dir: Path) -> list[analyze.Row]:
    path = run_dir / soak.PONGS_FILE
    return sorted(analyze.read_jsonl(path), key=lambda p: p["qpc_ns"]) if path.exists() else []


def ci_ms_at(pongs: Sequence[analyze.Row], ns: int) -> float | None:
    """The CI in force at `ns`: the last PONG at or before it (else the first), in ms (C1's 1.25 ms units)."""
    if not pongs:
        return None
    i = bisect.bisect_right([p["qpc_ns"] for p in pongs], ns) - 1
    return pongs[max(i, 0)]["itvl_units"] * realism.CI_UNIT_MS


# ---- probes M2 (jitter on / off): delivered-minus-chosen histograms and the phase -------------------

M2_N = 100
M2_SEQUENCE_LEN = 3
PHASE_PONG_WINDOW_NS = 5_000_000_000  # PONGs within ±5 s of a command's sent_ns map its board time to the PC
PHASE_KS_ALPHA = 0.01
_U32 = 1 << 32


def m2_commands(ctx: ProbeContext) -> None:
    """100 commands cycling tap, teleport, sequence of 3 distinct keys, drop: holds, chord offsets and in-action
    gaps. Fixed (no draws from the probe's generator), so `m2` and `m2_nojitter` send the same commands."""
    keys = soak.SEQUENCE_KEYS
    for i in range(M2_N):
        k = i // 4
        if i % 4 == 0:
            ctx.issue(Command("tap", (keys[k % len(keys)],)))
        elif i % 4 == 1:
            ctx.issue(Command("teleport", (soak.ARROWS[k % len(soak.ARROWS)],)))
        elif i % 4 == 2:
            ctx.issue(Command("sequence", tuple(keys[(k + j) % len(keys)] for j in range(M2_SEQUENCE_LEN))))
        else:
            ctx.issue(Command("drop"))


def _wrap_us(d: int) -> int:
    """A difference of two u32 board times (esp_timer_get_time() truncated to 32 bits) as a signed int."""
    return (d + (1 << 31)) % _U32 - (1 << 31)


def board_send_ns(row: analyze.Row, pongs: Sequence[analyze.Row]) -> int | None:
    """PC-clock time the board sent the command's first key report: board time ``t0_us`` + the first step's delta +
    ``late_us[0]``, aligned through the PONGs within ±5 s of ``sent_ns`` as the minimum over them of
    ``qpc_ns + (send_us - board_us)`` (each PONG's ``qpc_ns`` is late by its own delivery delay >= 0, so the
    minimum is the tightest). None without ``t0_us``, ``late_us`` or such a PONG."""
    late = row.get("late_us") or []
    if row.get("t0_us") is None or not late or not row.get("steps") or row.get("sent_ns") is None:
        return None
    send_us = row["t0_us"] + row["steps"][0][0] * 100 + late[0]
    near = [p for p in pongs if abs(p["qpc_ns"] - row["sent_ns"]) <= PHASE_PONG_WINDOW_NS]
    if not near:
        return None
    return min(p["qpc_ns"] + _wrap_us(send_us - p["board_us"]) * 1_000 for p in near)


@dataclass(frozen=True)
class PhaseSample:
    seq: int
    latency_ms: float  # first key-down - board send (PC clock)
    ci_ms: float

    @property
    def fraction(self) -> float:
        """(latency mod CI) / CI, in [0, 1)."""
        return (self.latency_ms % self.ci_ms) / self.ci_ms


def phase_samples(dm: analyze.DeliveryMatch, pongs: Sequence[analyze.Row]) -> tuple[list[PhaseSample], int]:
    """One per measured DONE OK command with a key-down seen; the count of those with no board send time."""
    out, skipped = [], 0
    for m in dm.commands:
        if m.alignment is None or m.first_down_ns is None:
            continue
        send = board_send_ns(m.row, pongs)
        if send is None:
            skipped += 1
            continue
        out.append(PhaseSample(m.seq, (m.first_down_ns - send) / analyze.NS_PER_MS, ci_ms_at(pongs, m.row["sent_ns"])))
    return out, skipped


def m2_report(run_dir: Path, jitter: bool = True) -> ProbeReport:
    """Measured - chosen histograms (realism's pairing) for holds, chord offsets and in-action gaps; measured hold
    mod CI; the phase (first key-down - board send) mod CI against the U(0, CI) lattice model (KS) and its
    concentration (mean resultant length R: 0 uniform, 1 locked to one point of the connection event)."""
    run = analyze.load_run(run_dir)
    dm = analyze.match_delivery(run)
    pairs = realism.pair_samples(dm)
    pongs = _pongs(run_dir)
    state = "on (HEAD firmware)" if jitter else "off (probe-only -DSTART_JITTER=0 image, as flashed by the operator)"
    lines = [f"M2, START_JITTER {state}: {len(analyze.commands_of(run))} commands, measured {len(dm.commands)}"]
    T = TimingType
    for t in (T.HOLD, T.CHORD_OFFSET, T.GAP):
        mine = [p for p in pairs if p.type == t]
        diffs = [p.measured_ms - p.reference_ms for p in mine if p.measured_ms is not None]
        lines += histogram(f"measured - chosen {t.value} (unpaired {len(mine) - len(diffs)})", diffs, -40.0, 40.0, 16)
    ci_of = {m.seq: ci_ms_at(pongs, m.row["sent_ns"]) for m in dm.commands if m.alignment is not None}
    hold_frac = [(p.measured_ms % ci_of[p.seq]) / ci_of[p.seq] for p in pairs
                 if p.type == T.HOLD and p.measured_ms is not None and ci_of.get(p.seq)]
    lines += histogram("measured hold mod CI (fraction of CI)", hold_frac, 0.0, 1.0, 10, unit="")
    phases, skipped = phase_samples(dm, pongs)
    review = []
    if phases:
        fr = np.array([s.fraction for s in phases])
        lines += histogram("phase: (first key-down - board send) mod CI (fraction of CI)", list(fr), 0.0, 1.0, 10,
                           unit="")
        lines.append("board send -> first key-down: " + summary([s.latency_ms for s in phases]))
        ks = stats.kstest(fr, "uniform", args=(0.0, 1.0))
        r = float(abs(np.mean(np.exp(2j * np.pi * fr))))
        lines.append(f"phase vs U(0, CI): KS D {ks.statistic:.4f} p {ks.pvalue:.4g}; mean resultant length R {r:.3f}; "
                     f"no board send time: {skipped} (reported, not gated)")
        if jitter and ks.pvalue < PHASE_KS_ALPHA:
            review.append(f"phase with START_JITTER on is not U(0, CI): KS p {ks.pvalue:.4g} < {PHASE_KS_ALPHA:g}, "
                          f"R {r:.3f} over {len(phases)} commands")
    else:
        lines.append(f"phase: n/a (no DONE OK command with a key-down, t0_us, late_us and a PONG within 5 s; "
                     f"skipped {skipped})")
    return ProbeReport(lines, review)


# ---- probe M4: fixed chord offsets ------------------------------------------------------------------

M4_OFFSETS_MS = (2.0, 5.0, 10.0, 15.0, 20.0, 30.0, 40.0)
M4_N = 50
M4_COLLAPSE_SHARE = 0.5  # "most model offsets collapse" (plan V1 M4)


class FixedChordModel(TimingModel):
    """Probe-only (M4): `chord_offset()` calls the base draw first, so `draws` still advances (C3), then returns the
    next fixed offset, cycling M4_OFFSETS_MS."""

    def __init__(self, rng: np.random.Generator) -> None:
        super().__init__(rng)
        self._k = 0

    def chord_offset(self) -> float:
        super().chord_offset()
        value = M4_OFFSETS_MS[self._k % len(M4_OFFSETS_MS)]
        self._k += 1
        return value


def m4_commands(ctx: ProbeContext) -> None:
    """7 offsets x 50 teleports, interleaved (the model cycles the offsets), direction cycling the 4 arrows."""
    for i in range(M4_N * len(M4_OFFSETS_MS)):
        ctx.issue(Command("teleport", (soak.ARROWS[i % len(soak.ARROWS)],)))


def m4_model_mass() -> dict[float, float]:
    """Mass of the model's CHORD_OFFSET distribution nearest each fixed offset (bins split at the midpoints)."""
    cdf = truncated_cdf(TimingType.CHORD_OFFSET)
    v = M4_OFFSETS_MS
    edges = [-math.inf] + [(a + b) / 2 for a, b in zip(v, v[1:])] + [math.inf]
    return {x: float(cdf(np.array(edges[i + 1])) - cdf(np.array(edges[i]))) for i, x in enumerate(v)}


def m4_expected_collapse(shares: dict[float, float]) -> float | None:
    """The collapse share the model's own offsets would see: sum of mass x measured collapse share per fixed
    offset; None if an offset has no measured pair."""
    mass = m4_model_mass()
    if any(x not in shares for x in mass):
        return None
    return sum(mass[x] * shares[x] for x in mass)


def m4_report(run_dir: Path) -> ProbeReport:
    """Observed offset per commanded one (realism's pairing); `[review]` against spec:L15 if most of the model's
    offsets (median 25 ms) would collapse into one connection event (measured < realism.COLLAPSE_MS)."""
    run = analyze.load_run(run_dir)
    dm = analyze.match_delivery(run)
    pairs = [p for p in realism.pair_samples(dm) if p.type == TimingType.CHORD_OFFSET]
    lines = [f"M4: {len(analyze.commands_of(run))} teleports, fixed chord offsets {', '.join(f'{x:g}' for x in M4_OFFSETS_MS)} ms"]
    shares: dict[float, float] = {}
    for x in M4_OFFSETS_MS:
        mine = [p for p in pairs if math.isclose(p.reference_ms, x)]
        got = [p.measured_ms for p in mine if p.measured_ms is not None]
        n_c = sum(v < realism.COLLAPSE_MS for v in got)
        if got:
            shares[x] = n_c / len(got)
        pct = f"{100 * n_c / len(got):.1f}%" if got else "n/a"
        lines.append(f"commanded {x:g} ms: collapsed (< {realism.COLLAPSE_MS:g} ms) {n_c}/{len(got)} ({pct}), "
                     f"unpaired {len(mine) - len(got)}; measured {summary(got)}")
    lines += realism.check_chord_order(dm).render()
    expected = m4_expected_collapse(shares)
    review = []
    if expected is None:
        lines.append("model-weighted collapse share: n/a (an offset has no measured pair)")
    else:
        mass = m4_model_mass()
        lines.append(f"model-weighted collapse share: {100 * expected:.1f}% (each offset's share weighted by the "
                     "model's CHORD_OFFSET mass nearest it: " + ", ".join(f"{x:g} ms {100 * m:.1f}%"
                                                                          for x, m in mass.items()) + ")")
        if expected > M4_COLLAPSE_SHARE:
            review.append(f"against spec:L15 (chord offsets: the two keys never land in the same instant): "
                          f"{100 * expected:.1f}% of the model's chord offsets (median 25 ms) would collapse into one "
                          "connection event (PAr §7)")
    return ProbeReport(lines, review)


# ---- raw plans (M5, M6): fixed schedules no InputLayer builder makes --------------------------------------

def raw_plan(name: str, steps: Sequence[Step]) -> Plan:
    """A Plan of fixed steps and no samples (nothing is drawn for them). ValueError if C1's schedule rules refuse
    it: that is a probe bug, never a board result."""
    checked = validate_schedule(steps)
    if not is_successful(checked):
        raise ValueError(f"{name}: {checked.failure()}")
    return Plan(name, tuple(steps), ())


def play_raw(ctx: ProbeContext, plan: Plan) -> CommandOutcome:
    """One raw plan as a command named after the plan, through `ctx.command` (focus policy, `stimulus_ns`, the C5
    row) and InputLayer's own play path: a REACTION draw and wait from the stimulus (>= 110 ms, so analyze's
    windows apply), `link.play`, and `release_all` after an uncertain failure (PA-adj1b-D1). `_act` is that path's
    entry; no public InputLayer call takes a Plan, and copying it is not allowed (A-PF-D3)."""
    return ctx.command(plan.name, lambda s: ctx.layer._act(plan, s))


# ---- probe M5: 3 s arrow holds (OS auto-repeat, the injected bit) ------------------------------------------

M5_N = 8  # each arrow twice
M5_HOLD_100US = 30_000  # 3 s


def m5_commands(ctx: ProbeContext) -> None:
    """8 holds of exactly 3 s, cycling the 4 arrows (a raw plan: walk() draws its duration)."""
    for i in range(M5_N):
        arrow = soak.ARROWS[i % len(soak.ARROWS)]
        play_raw(ctx, raw_plan("arrow_hold", (Step(0, arrow), Step(M5_HOLD_100US, Key(0)))))


def m5_report(run_dir: Path) -> ProbeReport:
    """Per hold: the OS repeat downs of the held key between its down and up, the first one's delay after the down,
    their spacing; the injected bit over every event of the probe (a board keyboard must never set it)."""
    run = analyze.load_run(run_dir)
    dm = analyze.match_delivery(run)
    repeats = [e for e in run.keys if e["repeat"]]
    per_hold, first, spacing, held = [], [], [], []
    for m in dm.commands:
        if m.alignment is None:
            continue
        vk = analyze.expected_transitions(m.row)[0][0]
        down = next((e["qpc_ns"] for e in m.observed if e["vk"] == vk and e["down"]), None)
        if down is None:
            continue
        up = next((e["qpc_ns"] for e in m.observed if e["vk"] == vk and not e["down"] and e["qpc_ns"] > down), None)
        mine = [e["qpc_ns"] for e in repeats if e["vk"] == vk and down < e["qpc_ns"] and (up is None or e["qpc_ns"] < up)]
        per_hold.append(len(mine))
        if mine:
            first.append((mine[0] - down) / analyze.NS_PER_MS)
        spacing += [(b - a) / analyze.NS_PER_MS for a, b in zip(mine, mine[1:])]
        if up is not None:
            held.append((up - down) / analyze.NS_PER_MS)
    injected = sum(1 for e in run.keys if e["injected"])
    lines = [f"M5: {len(analyze.commands_of(run))} arrow holds of {M5_HOLD_100US / 10_000:g} s, measured "
             f"{len(per_hold)} (DONE OK with the held key's down seen)",
             "repeat downs per hold: " + summary(per_hold, unit=""),
             "first repeat after the down: " + summary(first),
             "repeat spacing: " + summary(spacing),
             "measured hold (down -> up): " + summary(held),
             f"injected events: {injected} of {len(run.keys)} (a board keyboard never sets the injected bit)"]
    review = [lines[-1]] if injected else []
    return ProbeReport(lines, review)


# ---- probe M6: 16 zero-delta steps (NOTIFY_FAILED vs a silent drop) ----------------------------------------

M6_N = 50
M6_STEPS = tuple(Step(0, Key.E if i % 2 == 0 else Key(0)) for i in range(MAX_STEPS))  # E down/up x8, all deltas 0
_SEQ_LIST_MAX = 10


def m6_commands(ctx: ProbeContext) -> None:
    plan = raw_plan("e_burst", M6_STEPS)
    for _ in range(M6_N):
        play_raw(ctx, plan)


def _seq_list(seqs: Sequence[int]) -> str:
    more = f", ... {len(seqs) - _SEQ_LIST_MAX} more" if len(seqs) > _SEQ_LIST_MAX else ""
    return ", ".join(str(s) for s in seqs[:_SEQ_LIST_MAX]) + more


def m6_report(run_dir: Path) -> ProbeReport:
    """NOTIFY_FAILED count vs the reports (listener transitions) seen; a DONE OK command with a report missing is a
    silent drop, which fails spec Goal 2: stop. A listener silent for >= 5 DONE OK commands (H3's LISTENER_DEAD)
    cannot rule one out: stop too."""
    run = analyze.load_run(run_dir)
    dm = analyze.match_delivery(run)
    rows = analyze.commands_of(run)
    nf = str(InputError.NOTIFY_FAILED)
    others = Counter(r["error"] for r in rows if r["error"] is not None and r["error"] != nf)
    n_nf = sum(1 for r in rows if r["error"] == nf)
    lines = [f"M6: {len(rows)} commands of {len(M6_STEPS)} zero-delta steps (E down / up alternating): DONE OK "
             f"{sum(1 for r in rows if analyze.is_ok(r))}, NOTIFY_FAILED {n_nf}, other failures "
             f"{sum(others.values())}" + (f" {dict(others)}" if others else "")]
    ok = [m for m in dm.commands if m.alignment is not None]
    seen = sum(len(m.observed) for m in ok)
    expected = sum(len(analyze.expected_transitions(m.row)) for m in ok)
    lines.append(f"reports seen on DONE OK commands: {seen} of {expected} expected (listener transitions in each "
                 "command's window)")
    lines.append("reports seen on NOTIFY_FAILED commands: "
                 + summary([len(m.observed) for m in dm.commands if m.row["error"] == nf], unit=""))
    drops = [m for m in ok if m.alignment.dropped]
    lines.append(f"silent drops (DONE OK, a report missing): {len(drops)}"
                 + (f" (seq {_seq_list([m.seq for m in drops])})" if drops else ""))
    lines.append(f"extra {sum(m.alignment.extra for m in ok)}, out of order "
                 f"{sum(m.alignment.out_of_order for m in ok)} on DONE OK commands (reported, not gated)")
    review, stop = [], None
    if drops:
        review.append(f"silent drop: {len(drops)} DONE OK command(s) missing {sum(m.alignment.dropped for m in drops)} "
                      f"report(s) (seq {_seq_list([m.seq for m in drops])}); fails spec Goal 2")
        stop = "silent drop in M6 (fails spec Goal 2)"
    if dm.unmeasured:
        lines.append(f"unmeasured (listener silent from seq {dm.liveness.dead_from_seq}): {len(dm.unmeasured)}")
        review.append(f"no key event for >= {analyze.LISTENER_DEAD_N} DONE OK commands from seq "
                      f"{dm.liveness.dead_from_seq}: a dead listener or every report dropped; a silent drop cannot be "
                      "ruled out")
        stop = stop or "M6 listener silent: a silent drop cannot be ruled out"
    return ProbeReport(lines, review, stop)


# ---- probe M8: KBDLLHOOKSTRUCT.time (tick_ms) vs qpc_ns ------------------------------------------------

M8_N = 100  # m8 sends m7's 100 taps
M8_GRANULARITY_MS = (10.0, 16.0)
M8_TICK_TOLERANCE_MS = 1.0  # tick_ms is a whole ms count (a 15.625 ms grid reads as 15/16 ms steps): the grid fit's
#                             tolerance, and the slack above the band's top
M8_FIT_RANGE_MS = (1.0, 40.0)  # periods tried, 1 us apart, largest first


def _tick_grid_ms(diffs: Sequence[int]) -> float:
    """The largest period g in M8_FIT_RANGE_MS such that every tick difference is within M8_TICK_TOLERANCE_MS of a
    multiple of g. A submultiple of the true period fits too, which taking the largest g rules out. Integer-ms ticks
    always fit the 1 ms floor of the range."""
    lo_us, hi_us = (round(v * 1000) for v in M8_FIT_RANGE_MS)
    for us in range(hi_us, lo_us, -1):
        g = us / 1000
        if all(abs(d - round(d / g) * g) <= M8_TICK_TOLERANCE_MS for d in diffs):
            return g
    return M8_FIT_RANGE_MS[0]


def m8_report(run_dir: Path) -> ProbeReport:
    """The tick_ms granularity is a grid fit on tick_ms differences (_tick_grid_ms), not on tick_ms - qpc_ns: over
    BLE-HID input tick_ms trails the hook's qpc_ns by a spread of about two ticks, not one (A-M8), so the residual's
    p99 - p1 reads about 2x the granularity. The residual and the step counts are reported as information.
    Expected 10-16 ms (listener.RawKeyEvent)."""
    run = analyze.load_run(run_dir)
    keys = run.keys
    head = f"M8: {len(keys)} key events (transitions and repeats) of {len(analyze.commands_of(run))} taps"
    if len(keys) < 2:
        return ProbeReport([head, "M8 tick_ms granularity: n/a (fewer than 2 key events)"],
                           ["tick_ms granularity: n/a, fewer than 2 key events"])
    t0, q0 = keys[0]["tick_ms"], keys[0]["qpc_ns"]
    # tick_ms is a u32 millisecond count: _wrap_us's signed u32 difference applies unchanged
    diffs = [_wrap_us(e["tick_ms"] - t0) for e in keys]
    resid = [d - (e["qpc_ns"] - q0) / analyze.NS_PER_MS for d, e in zip(diffs, keys)]
    steps = Counter(_wrap_us(b["tick_ms"] - a["tick_ms"]) for a, b in zip(keys, keys[1:]))
    nonzero = sorted((d, n) for d, n in steps.items() if d != 0)
    lines = [head,
             "tick_ms - qpc_ns (ms, from the first event): " + summary(resid),
             "nonzero tick steps between events (smallest 8, ms x count): "
             + ", ".join(f"{d} x{n}" for d, n in nonzero[:8]) + f"; zero steps {steps.get(0, 0)}"]
    distinct = set(diffs) - {0}
    if len(distinct) < 2:  # one offset fits every g that divides it: nothing to fit
        lines.append("M8 tick_ms granularity: n/a (tick_ms showed fewer than 2 distinct offsets from the first event)")
        return ProbeReport(lines, ["tick_ms granularity: n/a, tick_ms showed fewer than 2 distinct offsets from the "
                                   "first event"])
    est = _tick_grid_ms(sorted(distinct))
    lo, hi = M8_GRANULARITY_MS
    ok = lo <= est <= hi + M8_TICK_TOLERANCE_MS
    lines.append(f"M8 tick_ms granularity: {'CONFIRMED' if ok else 'NOT CONFIRMED'} ~{est:.3f} ms (grid fit on "
                 f"tick_ms differences; expected {lo:g}-{hi:g} ms, +{M8_TICK_TOLERANCE_MS:g} ms for whole-ms ticks)")
    review = [] if ok else [f"tick_ms granularity ~{est:.3f} ms is outside {lo:g}-{hi:g} ms "
                            f"(+{M8_TICK_TOLERANCE_MS:g}): KBDLLHOOKSTRUCT.time is not the 10-16 ms tick "
                            "listener.RawKeyEvent assumes"]
    return ProbeReport(lines, review)


# ---- probe M7 (first look): latency and its split ---------------------------------------------------

M7_N = 100


def m7_commands(ctx: ProbeContext) -> None:
    """100 taps cycling the 7 tap keys."""
    for i in range(M7_N):
        ctx.issue(Command("tap", (soak.TAP_KEYS[i % len(soak.TAP_KEYS)],)))


def m7_report(run_dir: Path) -> ProbeReport:
    """H3's Latency check (sent_ns -> first key-down p99 against the provisional target) with analyze.m7_split's
    write / board accept / board to listener / total p50/p99 (reported, not gated)."""
    dm = analyze.match_delivery(analyze.load_run(run_dir))
    check = analyze.check_latency(dm)
    review = [f"Latency {check.verdict}: {check.summary}"] if check.failed else []
    return ProbeReport(check.render(), review)


# ---- release-all probes: release_all() from a second thread (PA-adj2-D1 mid-hold, PA-rev m5 ordering) ------

RELEASES_FILE = "releases.jsonl"  # the probe's per-trial log next to the C5 files (analyze.py ignores it)
RELEASE_THREAD = "probe-release"
RELEASE_MID_N = 20  # per case
RELEASE_MID_WALK_MS = 3000.0
RELEASE_MID_DELAY_S = (0.5, 2.5)  # after the PLAY write starts
# Right + E both down for 3 s (C1: one key change per step, so the second down is a 0-delta step)
RELEASE_CHORD_STEPS = (Step(0, Key.RIGHT), Step(0, Key.RIGHT | Key.E), Step(30_000, Key.RIGHT), Step(0, Key(0)))
RELEASE_ORDER_N = 50
RELEASE_ORDER_DELAY_S = (0.0, 0.02)
RELEASE_ORDER_STEPS = (Step(0, Key.E), Step(10_000, Key(0)))  # a 1 s E hold
RELEASE_ORDER_MARGIN_MS = 5.0  # a key-down later than the release's done_ns + one CI + this is a reorder
RELEASE_ORDER_HOLD_MAX_MS = 500.0  # a reordered PLAY would hold E the full 1 s after the release


def release_trial(ctx: ProbeContext, send: Callable[[], CommandOutcome], delay_s: float,
                  release: Callable[[], Result[SendReport, InputError]]) -> CommandOutcome:
    """One command (`send()`, on this thread) and `release()` from a second thread `delay_s` after the command's PLAY
    write starts. The start is P4's `_before_write` seam (PA-rev m4) set on this probe's link instance: BleLink._play
    awaits it on the link's loop just before the write, after the REACTION wait. A command that ends without a
    PLAY write (e.g. FOCUS_LOST) gets no release. The trial goes to releases.jsonl."""
    written = threading.Event()
    stamps: dict = {}

    async def before_write(seq: int) -> None:
        stamps.setdefault("write_ns", _clock())
        written.set()

    def second() -> None:
        written.wait()
        if "write_ns" not in stamps:  # woken by the command's end, not by a write
            return
        _sleep(delay_s)
        stamps["release_call_ns"] = _clock()
        stamps["release"] = release()
        stamps["release_returned_ns"] = _clock()

    ctx.link._before_write = before_write
    thread = threading.Thread(target=second, name=RELEASE_THREAD, daemon=True)
    thread.start()
    try:
        outcome = send()
    finally:
        written.set()
        thread.join()
    released = stamps.get("release")
    rep = released.unwrap() if released is not None and is_successful(released) else None
    row = {"seq": outcome.row["seq"], "action": outcome.row["action"], "delay_ms": delay_s * 1000,
           "write_ns": stamps.get("write_ns"), "release_call_ns": stamps.get("release_call_ns"),
           "release_returned_ns": stamps.get("release_returned_ns"),
           "release_status": None if released is None else ("OK" if rep is not None else str(released.failure())),
           "release_sent_ns": rep.sent_ns if rep is not None else None,
           "release_done_ns": rep.done_ns if rep is not None else None}
    with open(ctx.run_dir / RELEASES_FILE, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(row) + "\n")
    return outcome


def release_mid_hold_commands(ctx: ProbeContext) -> None:
    """walk(RIGHT, 3000) x20, then a raw 3 s Right + E hold x20; each released 0.5-2.5 s into the hold."""
    for _ in range(RELEASE_MID_N):
        cmd = Command("walk", (Key.RIGHT,), RELEASE_MID_WALK_MS)
        release_trial(ctx, lambda: ctx.issue(cmd), float(ctx.rng.uniform(*RELEASE_MID_DELAY_S)),
                      ctx.layer.release_all)
    chord = raw_plan("chord_hold", RELEASE_CHORD_STEPS)
    for _ in range(RELEASE_MID_N):
        release_trial(ctx, lambda: play_raw(ctx, chord), float(ctx.rng.uniform(*RELEASE_MID_DELAY_S)),
                      ctx.layer.release_all)


def release_ordering_commands(ctx: ProbeContext) -> None:
    """A 1 s E hold x50, each with link.release_all() 0-20 ms after its PLAY write starts."""
    plan = raw_plan("e_hold", RELEASE_ORDER_STEPS)
    for _ in range(RELEASE_ORDER_N):
        release_trial(ctx, lambda: play_raw(ctx, plan), float(ctx.rng.uniform(*RELEASE_ORDER_DELAY_S)),
                      ctx.link.release_all)


def _release_trials(run_dir: Path) -> list[tuple[analyze.Row, analyze.Row | None, list[analyze.Row]]]:
    """(command row, its releases.jsonl row or None, the key events in its window) per command. Windows are
    analyze's: from stimulus_ns + WINDOW_OFFSET_NS to the next command's."""
    run = analyze.load_run(run_dir)
    rows = analyze.commands_of(run)
    path = run_dir / RELEASES_FILE
    rel = {r["seq"]: r for r in analyze.read_jsonl(path)} if path.exists() else {}
    qpcs = [e["qpc_ns"] for e in run.keys]
    out = []
    for i, row in enumerate(rows):
        lo = bisect.bisect_left(qpcs, row["stimulus_ns"] + analyze.WINDOW_OFFSET_NS)
        hi = (bisect.bisect_left(qpcs, rows[i + 1]["stimulus_ns"] + analyze.WINDOW_OFFSET_NS) if i + 1 < len(rows)
              else len(qpcs))
        out.append((row, rel.get(row["seq"]), run.keys[lo:hi]))
    return out


def _left_down(events: Sequence[analyze.Row]) -> set[int]:
    """The vks down at the end of `events` (an OS repeat is a down)."""
    down: set[int] = set()
    for e in events:
        (down.add if e["down"] else down.discard)(e["vk"])
    return down


def _statuses(rows: Sequence[analyze.Row], expected: str, of: int | None = None) -> str:
    """'<count of `expected`>[ of <of>][ (other: <status> <count>, ...)]' over the rows' errors (OK for none)."""
    c = Counter(r["error"] or "OK" for r in rows)
    other = ", ".join(f"{k} {v}" for k, v in c.items() if k != expected)
    return f"{c.get(expected, 0)}" + (f" of {of}" if of is not None else "") + (f" (other: {other})" if other else "")


def release_mid_hold_report(run_dir: Path) -> ProbeReport:
    """Per case: release_all() call -> the listener's last up after it (all keys up) p50 / max, the pending call's
    result (expected ABORTED) and keys left down. Reported, not gated (PA-adj2-D1): a miss is a [review] item."""
    trials = _release_trials(run_dir)
    lines = ["Release-all mid-hold (PA-adj2-D1): release_all() from a second thread "
             f"{RELEASE_MID_DELAY_S[0]:g}-{RELEASE_MID_DELAY_S[1]:g} s after the PLAY write; reported, not gated"]
    review = []
    for action in ("walk", "chord_hold"):
        mine = [t for t in trials if t[0]["action"] == action]
        latency, left, no_up, rel_ok = [], 0, 0, 0
        for row, rel, events in mine:
            call = rel["release_call_ns"] if rel is not None else None
            rel_ok += rel is not None and rel["release_status"] == "OK"
            stuck = bool(_left_down(events))
            left += stuck
            ups = [e["qpc_ns"] for e in events if not e["down"] and call is not None and e["qpc_ns"] >= call]
            if not ups:
                no_up += 1
            elif not stuck:
                latency.append((max(ups) - call) / analyze.NS_PER_MS)
        aborted = sum(1 for row, _, _ in mine if row["error"] == str(InputError.ABORTED))
        lines.append(f"{action}: {len(mine)} trials, play ABORTED {_statuses([t[0] for t in mine], 'ABORTED')}, "
                     f"release_all OK {rel_ok}, keys left down {left}, no up after the call {no_up}")
        lines.append(f"{action} release_all() -> all keys up: " + summary(latency))
        if aborted != len(mine) or left:
            review.append(f"{action}: {len(mine) - aborted} of {len(mine)} pending call(s) did not return "
                          f"Failure(ABORTED), {left} trial(s) left a key down")
    return ProbeReport(lines, review)


def release_ordering_report(run_dir: Path) -> ProbeReport:
    """Expected every time: play() ABORTED, release_all() OK, no key-down later than the release's done_ns + one CI
    (the PONG in force) + 5 ms, every E hold < 500 ms (none left down). Any other outcome: the writes can reorder,
    stop (PA-rev m5). E-up - done_ns is reported only (PC-side path skew, PA-rev3b m-b)."""
    trials = _release_trials(run_dir)
    pongs = _pongs(run_dir)
    e_vk = listener.VK_BY_KEY[Key.E]
    rel_ok = late = long_hold = left = bad = 0
    holds, e_up, kinds = [], [], Counter()
    for row, rel, events in trials:
        reasons = []
        if row["error"] != str(InputError.ABORTED):
            reasons.append(f"play {row['error'] or 'OK'}")
        done = rel["release_done_ns"] if rel is not None and rel["release_status"] == "OK" else None
        if done is None:
            reasons.append("release_all not OK")
        else:
            rel_ok += 1
            ci = ci_ms_at(pongs, done) or 0.0
            limit = done + (ci + RELEASE_ORDER_MARGIN_MS) * analyze.NS_PER_MS
            if any(e["down"] and e["qpc_ns"] > limit for e in events):
                late += 1
                reasons.append("key-down past the margin")
            ups = [e["qpc_ns"] for e in events if e["vk"] == e_vk and not e["down"]]
            if ups:
                e_up.append((ups[-1] - done) / analyze.NS_PER_MS)
        down_ns, too_long = None, False
        for e in events:
            if e["vk"] != e_vk:
                continue
            if e["down"]:
                down_ns = down_ns if down_ns is not None else e["qpc_ns"]
            elif down_ns is not None:
                holds.append((e["qpc_ns"] - down_ns) / analyze.NS_PER_MS)
                too_long |= holds[-1] >= RELEASE_ORDER_HOLD_MAX_MS
                down_ns = None
        if too_long:
            long_hold += 1
            reasons.append(f"E hold >= {RELEASE_ORDER_HOLD_MAX_MS:g} ms")
        if down_ns is not None:
            left += 1
            reasons.append("E left down")
        kinds.update(reasons)
        bad += bool(reasons)
    n = len(trials)
    lines = [f"Release-all ordering (PA-rev m5): {n} trials of a {RELEASE_ORDER_STEPS[1].delta_100us / 10_000:g} s "
             f"E hold, link.release_all() from a second thread {1000 * RELEASE_ORDER_DELAY_S[0]:g}-"
             f"{1000 * RELEASE_ORDER_DELAY_S[1]:g} ms after the PLAY write starts",
             f"play ABORTED {_statuses([t[0] for t in trials], 'ABORTED', n)}; release_all OK {rel_ok} of {n}",
             f"key-downs past done_ns + CI + {RELEASE_ORDER_MARGIN_MS:g} ms: {late} trial(s)",
             f"E holds >= {RELEASE_ORDER_HOLD_MAX_MS:g} ms: {long_hold} trial(s); E left down: {left} trial(s)",
             "E hold (down -> up): " + summary(holds),
             "E-up - release done_ns: " + summary(e_up) + " (reported, not gated: PC-side path skew, PA-rev3b m-b)",
             f"release-all ordering: as expected in {n - bad} of {n}"
             + (f"; other outcomes: {', '.join(f'{k} {v}' for k, v in kinds.items())}" if bad else "")]
    if not bad:
        return ProbeReport(lines)
    review = [f"the writes can reorder: {bad} of {n} trial(s) not as expected ("
              + ", ".join(f"{k} {v}" for k, v in kinds.items()) + "); against P4's abort rules (PA-fix1c ordering)"]
    return ProbeReport(lines, review, stop=f"release-all ordering: {bad} of {n} trial(s) show a reorder or another "
                                           "outcome (P4's abort rules)")


PROBES: dict[str, Probe] = {
    "delivery": Probe("delivery", delivery_commands, delivery_report, duration="~3-4 min"),
    "m2": Probe("m2", m2_commands, m2_report, duration="~1-1.5 min"),
    "m2_nojitter": Probe("m2_nojitter", m2_commands, lambda d: m2_report(d, jitter=False), duration="~1-1.5 min"),
    "m4": Probe("m4", m4_commands, m4_report, model=FixedChordModel, duration="~3 min"),
    "m5": Probe("m5", m5_commands, m5_report, duration="~30 s"),
    "m6": Probe("m6", m6_commands, m6_report, duration="~15 s"),
    "m8": Probe("m8", m7_commands, m8_report, duration="~45 s"),
    "m7": Probe("m7", m7_commands, m7_report, duration="~45 s"),
    "release_mid_hold": Probe("release_mid_hold", release_mid_hold_commands, release_mid_hold_report,
                              duration="~1.5 min"),
    "release_ordering": Probe("release_ordering", release_ordering_commands, release_ordering_report,
                              duration="~15 s"),
}
RUN_ALONE = ("m2_nojitter",)  # needs the probe-only flash before and the HEAD reflash after (plan V1 M2)


def default_probes() -> tuple[str, ...]:
    """Every registered probe, in order, except those that must run alone."""
    return tuple(n for n in PROBES if n not in RUN_ALONE)


# ---- the V1 live run sheet (printed, never executed) -----------------------------------------------

PLAN_VARS = (
    "REPO=/c/Users/Qing_programming/Desktop/helix-claude-clone   # plan A's value: the main tree, which has the "
    ".venv, with p1a-v1 merged into its branch (p1a-input) before the run",
    "PY=$REPO/.venv/Scripts/python.exe",
    'CLI="/c/Users/Qing_programming/AppData/Local/Programs/Arduino IDE/resources/app/lib/backend/resources/'
    'arduino-cli.exe"',
    "FQBN=esp32:esp32:esp32s3:USBMode=hwcdc,CDCOnBoot=cdc",
    "RUN=v1-<YYYYmmdd>; SEED=<N>   # the same two for every probe.py call below",
)
MAPLE_CHECK = ("Get-Process | Where-Object { $_.ProcessName -match 'maple' } | Select-Object ProcessName, Id   "
               "# PowerShell (hard rule 2); expected: no output, else stop BLOCKED")
PORT_LOOKUP = ('PORT=$("$CLI" board list --format json | "$PY" -c "import json,sys; print(\' \'.join(p[\'port\']'
               '[\'address\'] for p in json.load(sys.stdin)[\'detected_ports\'] if (p[\'port\'].get(\'properties\') '
               'or {}).get(\'vid\',\'\').lower()==\'0x303a\' and p[\'port\'][\'properties\'].get(\'pid\',\'\')'
               '.lower()==\'0x1001\'))"); echo "PORT=[$PORT]"   # hard rule 3; expected exactly one port, else stop '
               'BLOCKED and quote "$CLI" board list')
FLASH_NOJITTER = ('"$CLI" compile -b "$FQBN" -u -p "$PORT" --output-dir $REPO/firmware/build/probe-nojitter '
                  '--build-property "compiler.cpp.extra_flags=-DSTART_JITTER=0" $REPO/firmware/Helix_BLE')
FLASH_HEAD = '"$CLI" compile -b "$FQBN" -u -p "$PORT" --output-dir $REPO/firmware/build $REPO/firmware/Helix_BLE'


def _probe_call(names: Sequence[str]) -> str:
    return f'"$PY" -m src.input.harness.probe --probes {",".join(names)} --run "$RUN" --seed "$SEED"'


def run_sheet() -> list[str]:
    """The V1 live run sheet: order, commands, expected duration per probe, operator actions."""
    first = default_probes()
    lines = [
        "V1 live run sheet (printed by probe.py --run-sheet; probe.py executes none of it)",
        "Shell: Git Bash at the repo root of $REPO (below), except the PowerShell MapleStory check. Variables "
        "(plan A):",
        *[f"  {v}" for v in PLAN_VARS],
        "Operator before step 1: run from the main tree (REPO) with p1a-v1 merged into its branch; MapleStory "
        "closed; board on its native USB port and running HEAD's firmware (else do steps 2e-2g first); 'Guiness' "
        "paired; session unlocked; nothing else will take the foreground; type no secrets (keys.jsonl logs every "
        "key).",
        "1. Probes on HEAD's firmware (probe.py runs the MapleStory check and the board-port lookup itself; exit 3 = "
        "BLOCKED, stop):",
        f"   {_probe_call(first)}",
        "   Per probe a sink window opens; if the console prints '>>> CLICK the window ...', click it once and touch "
        "no key until the probe's report prints. Expected durations (inferred, not measured; each plus ~10-30 s "
        "connect and start wait): " + ", ".join(f"{n} {PROBES[n].duration or 'n/a'}" for n in first) + ".",
        "   Exit 1 = abort ([review] if FOCUS_LOST; re-running is the orchestrator's call), 4 = a report asked to "
        "stop (m6: a silent drop; release_ordering: the writes can reorder; the probes after it are not run). "
        "Reports: data/probe/$RUN/<probe>/report.txt.",
        "2. M2 with START_JITTER off (PA-rev I8). Only after step 1 exited 0; on exit 1, 3 or 4 stop and report (the "
        "board is still on HEAD's firmware):",
        f"   a. {MAPLE_CHECK}",
        f"   b. {PORT_LOOKUP}",
        f"   c. {FLASH_NOJITTER}   # probe-only image; expected exit 0 (~1-2 min)",
        f"   d. {_probe_call(RUN_ALONE)}   # {PROBES['m2_nojitter'].duration}; click the sink as in step 1",
        f"   e. {MAPLE_CHECK}",
        f"   f. {PORT_LOOKUP}",
        f"   g. {FLASH_HEAD}   # reflash HEAD (V3 step 3's command) before any other probe; expected exit 0",
        "   Whatever d exits with, do e-g before anything else: the board must not keep the probe-only image. "
        "Nothing is committed (firmware/build is gitignored).",
    ]
    return lines



# ---- the runner --------------------------------------------------------------------------------

@dataclass(frozen=True)
class ProbeConfig:
    probes: tuple[str, ...]
    out: Path
    seed: int
    cli: str = CLI_DEFAULT
    link_debug: bool = False


LINK_DEBUG_FILE = "link-debug.log"  # --link-debug only (A-M6diag); link.log (WARNING, C5) is unchanged


@contextlib.contextmanager
def _link_debug_log(run_dir: Path | None):
    """With a run dir: every `src.input.link` record at DEBUG and up also goes to ``run_dir / LINK_DEBUG_FILE``, in
    link.log's format (perf_ns stamped, so it lines up with commands.jsonl); the logger's level is restored after."""
    if run_dir is None:
        yield
        return
    run_dir.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(run_dir / LINK_DEBUG_FILE, mode="w", encoding="utf-8")
    handler.setLevel(logging.DEBUG)
    handler.addFilter(soak._stamp_perf_ns)
    handler.setFormatter(logging.Formatter(soak.LINK_LOG_FORMAT))
    logger = logging.getLogger(soak.LINK_LOGGER)
    level = logger.level
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    try:
        yield
    finally:
        logger.removeHandler(handler)
        logger.setLevel(level)
        handler.close()


def _check_processes(writer: RunWriter) -> None:
    ok, text = soak.maple_check()
    writer.session["process_check"] = text
    if not ok:
        raise ProbeAbort(text)


def _run_probe(p: Probe, cfg: ProbeConfig, link: BleLink, start_sink: Callable[[Path], soak._SoakSink]) -> int:
    """One probe into ``cfg.out / p.name``; returns EXIT_OK when its commands ran to the end."""
    timing_rng, own_rng = probe_streams(cfg.seed, p.name)
    layer = InputLayer(link, p.model(timing_rng))
    sink = None
    ended = False
    with RunWriter(cfg.out / p.name, link, run_id=f"{cfg.out.name}/{p.name}", seed=cfg.seed) as writer:
        try:
            _check_processes(writer)
            last_check_ns = _clock()

            def periodic_check() -> None:
                nonlocal last_check_ns
                if _clock() - last_check_ns >= soak.PROCESS_CHECK_PERIOD_NS:
                    _check_processes(writer)
                    last_check_ns = _clock()

            (writer.run_dir / listener.KEYS_FILE).write_bytes(b"")  # the listener appends: start on a fresh file
            try:
                sink = start_sink(writer.run_dir)
            except OSError as e:
                raise ProbeAbort(f"listener did not start: {e}") from e
            if not soak.wait_for_click(sink, periodic_check):  # PA-rev m15 foreground check, before connect
                raise ProbeAbort(f"BLOCKED: the sink did not take the foreground within {START_WAIT_S:g} s; every "
                                 "command would end FOCUS_LOST", EXIT_BLOCKED)
            connected = link.connect()
            if not is_successful(connected):
                raise ProbeAbort(f"connect failed: {connected.failure()}")
            writer.record_connect(connected.unwrap())
            ctx = ProbeContext(p.name, writer.run_dir, writer, sink, link, layer, own_rng, connected.unwrap(),
                               periodic_check)
            p.commands(ctx)
            ended = True
            return EXIT_OK
        finally:
            if not ended:  # any abort, Ctrl-C or unexpected error: lift every key first
                released = layer.release_all()
                if not is_successful(released):
                    print(f"release_all: {released.failure()}", file=sys.stderr, flush=True)
            link.close()
            if sink is not None:
                sink.stop()


def _emit_report(p: Probe, run_dir: Path, report: ProbeReport) -> None:
    lines = report.lines + [f"[review] {p.name}: {r}" for r in report.review]
    with open(run_dir / "report.txt", "w", encoding="utf-8", newline="\n") as fh:
        fh.writelines(line + "\n" for line in lines)
    print(f"== probe {p.name} -> {run_dir}", flush=True)
    for line in lines:
        print(line, flush=True)


def run_probes(cfg: ProbeConfig, new_link: Callable[[], BleLink],
               start_sink: Callable[[Path], soak._SoakSink]) -> int:
    """Steps 1-2, then each probe in `cfg.probes` order; returns the exit code."""
    ok, text = soak.maple_check()
    if not ok:
        print(f"BLOCKED (step 1, hard rule 2): {text}", file=sys.stderr, flush=True)
        return EXIT_BLOCKED
    port = board_port(cfg.cli)
    if not is_successful(port):
        print(f"BLOCKED (step 2, hard rule 3): {port.failure()}", file=sys.stderr, flush=True)
        return EXIT_BLOCKED
    print(f"{text}; board port {port.unwrap()} (VID 0x303A / PID 0x1001)", flush=True)
    for i, name in enumerate(cfg.probes):
        p = PROBES[name]
        rest = ", ".join(cfg.probes[i + 1:]) or "none"
        try:
            with _link_debug_log(cfg.out / name if cfg.link_debug else None):
                _run_probe(p, cfg, new_link(), start_sink)
        except ProbeAbort as e:
            tag = "[review] " if "FOCUS_LOST" in str(e) else ""
            print(f"{tag}{e} (probe.py stopped in probe {name}; not run: {rest})", file=sys.stderr, flush=True)
            return e.code
        except KeyboardInterrupt:
            print(f"probe.py interrupted (Ctrl-C) in probe {name}; not run: {rest}", file=sys.stderr, flush=True)
            return EXIT_INTERRUPTED
        report = p.report(cfg.out / name)
        _emit_report(p, cfg.out / name, report)
        if report.stop is not None:
            print(f"probe.py stopped after probe {name}: {report.stop}; not run: {rest}", file=sys.stderr,
                  flush=True)
            return EXIT_STOP
    print(f"probes done: {', '.join(cfg.probes)} -> {cfg.out}", flush=True)
    return EXIT_OK


# ---- CLI ---------------------------------------------------------------------------------------------

# Seams: the tests replace these with fakes.
_new_link: Callable[[], BleLink] = BleLink


def _start_sink(run_dir: Path) -> soak._SoakSink:
    return listener.start(run_dir)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="python -m src.input.harness.probe",
                                description="V1 measurement probes on the board; one C5 run dir per probe.")
    p.add_argument("--probes", default=",".join(default_probes()),
                   help=f"comma-separated, run in this order (default: {','.join(default_probes())}; "
                        f"{', '.join(RUN_ALONE)} only alone)")
    p.add_argument("--run", default=None, help="run name: data/probe/<run>/ (default: YYYYmmdd-HHMMSS)")
    p.add_argument("--out", default=None, help="run directory (overrides --run)")
    p.add_argument("--seed", type=int, default=None, help="timing and probe seed (default: a fresh one, logged)")
    p.add_argument("--cli", default=CLI_DEFAULT, help="arduino-cli.exe for the board-port check (hard rule 3)")
    p.add_argument("--link-debug", action="store_true",
                   help="also write <probe dir>/link-debug.log: src.input.link at DEBUG, incl. every reply received")
    p.add_argument("--run-sheet", action="store_true", help="print the V1 live run sheet and exit (runs nothing)")
    return p.parse_args(argv)


def config_from_args(args: argparse.Namespace) -> ProbeConfig:
    seed = args.seed if args.seed is not None else int(np.random.SeedSequence().entropy)
    if args.out:
        out = Path(args.out)
    else:
        out = PROBE_ROOT / (args.run or datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
    probes = tuple(s.strip() for s in args.probes.split(",") if s.strip())
    return ProbeConfig(probes=probes, out=out, seed=seed, cli=args.cli, link_debug=args.link_debug)


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")   # reports quote "≥"/"≤"; a redirected stdout is cp1252 on Windows
    args = parse_args(argv)
    if args.run_sheet:
        for line in run_sheet():
            print(line, flush=True)
        return EXIT_OK
    cfg = config_from_args(args)
    unknown = [n for n in cfg.probes if n not in PROBES]
    if unknown or not cfg.probes:
        print(f"refused: unknown probe(s) {unknown}; known: {', '.join(PROBES)}", file=sys.stderr, flush=True)
        return EXIT_REFUSED
    alone = [n for n in cfg.probes if n in RUN_ALONE]
    if alone and len(cfg.probes) > 1:
        print(f"refused: {', '.join(alone)} runs alone (a probe-only flash comes before it and the HEAD reflash after "
              "it; see --run-sheet)", file=sys.stderr, flush=True)
        return EXIT_REFUSED
    print(f"probes: {', '.join(cfg.probes)}, seed {cfg.seed} -> {cfg.out}", flush=True)
    return run_probes(cfg, _new_link, _start_sink)


if __name__ == "__main__":
    sys.exit(main())
