"""Soak-log analysis: listener liveness, delivery, latency, faults, stuck keys, connection (plan A, Task H3).

Reads a C5 run dir (``commands.jsonl``, ``keys.jsonl``, ``faults.jsonl``, ``link_faults.jsonl``) and, with
``--starts``, V2's ``session_starts.jsonl``. Each check prints PASS/FAIL (or INFO / n/a when it is reported, not
gated) with its numbers and the spec target verbatim; the CLI exits non-zero on any FAIL.

Importable for V1 (probe.py) and H4 (realism.py), A-PF-D3 / A-PF m7: ``load_run``, ``command_windows``,
``match_delivery`` (per-command windows plus the ``difflib`` alignment, whose ``Alignment.pairs`` maps each
expected transition index, i.e. the schedule step index, to the observed listener transition), ``m7_split`` and
``percentile``.

Definitions used (plan A H3):
- A command is a ``commands.jsonl`` row other than the ``FOCUS_LOST`` row (C5). It is OK (DONE OK) when its
  ``error`` is null; any other row is a failed command (PA-rev I6).
- ``window_k = [stimulus_ns(k) + 110 ms, stimulus_ns(k+1) + 110 ms)``, the last one open-ended (PA-rev m12).
- Observed = listener transitions, i.e. ``keys.jsonl`` rows with ``repeat`` false, sorted by ``qpc_ns``.
- A run with no injected fault is one whose ``faults.jsonl`` is empty or absent.
- Percentiles are nearest-rank (the smallest value with at least p% of the values at or below it).

Run: ``python -m src.input.harness.analyze <run dir> [--starts <session_starts.jsonl>]``
"""
from __future__ import annotations

import argparse
import bisect
import difflib
import json
import math
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from src.input.harness.listener import BOT_VKS, VK_BY_KEY
from src.input.protocol import Key, Step, transitions

Row = dict[str, Any]

NS_PER_MS = 1_000_000
NS_PER_H = 3_600 * 1_000_000_000
WINDOW_OFFSET_NS = 110 * NS_PER_MS          # REACTION's lower bound (C4); window start after stimulus (PA-rev m12)
LISTENER_DEAD_N = 5
LATENCY_P99_TARGET_NS = 100 * NS_PER_MS
FAULT_REPORT_LIMIT_NS = 1_000_000_000
CONNECTED_SPAN_TARGET_NS = 2 * NS_PER_H
STARTS_EXPECTED = {"plain": 8, "reset_board": 6, "bluetooth_off_on": 6}
FOCUS_LOST = "FOCUS_LOST"
KEY_BY_VK: dict[int, Key] = {vk: k for k, vk in VK_BY_KEY.items()}

# spec docs/superpowers/specs/2026-10-21-p1a-input-over-bluetooth-design.md, targets table (L56-L61)
TARGET_DELIVERY = ("0 dropped, extra, or out-of-order key events over a soak of ≥10,000 commands mixing single "
                   "presses, holds and chords (0 in 10,000 bounds the true error rate below 0.03% at 95% confidence).")
TARGET_FAULTS = ("Every injected fault (board reset mid-soak, PC Bluetooth off) is reported to the caller within "
                 "1 s. 0 silent losses.")
TARGET_LATENCY = ("p99 from command sent to key-down seen by the PC listener ≤ 100 ms. Provisional: re-set once "
                  "first measured.")
TARGET_STARTS = ("Session start connects 20/20 times without re-pairing, including after a board reset and after "
                 "the PC's Bluetooth is toggled.")
TARGET_DROPS = "A connected session holds 2 h with no drop."


# ---- loading ---------------------------------------------------------------------------

def read_jsonl(path: Path) -> list[Row]:
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


@dataclass(frozen=True)
class RunLog:
    commands: list[Row]               # every commands.jsonl row in file order, the FOCUS_LOST row included
    keys: list[Row]                   # keys.jsonl sorted by qpc_ns (stable; file order is write order)
    faults: list[Row]                 # faults.jsonl, [] when absent
    link_faults: list[Row] | None     # link_faults.jsonl, None when absent


def load_run(run_dir: Path | str) -> RunLog:
    run_dir = Path(run_dir)
    faults_p, link_p = run_dir / "faults.jsonl", run_dir / "link_faults.jsonl"
    return RunLog(
        commands=read_jsonl(run_dir / "commands.jsonl"),
        keys=sorted(read_jsonl(run_dir / "keys.jsonl"), key=lambda e: e["qpc_ns"]),
        faults=read_jsonl(faults_p) if faults_p.exists() else [],
        link_faults=read_jsonl(link_p) if link_p.exists() else None,
    )


def is_focus_lost(row: Row) -> bool:
    return row.get("status") == FOCUS_LOST


def is_ok(row: Row) -> bool:
    return row.get("error") is None and not is_focus_lost(row)


def commands_of(run: RunLog) -> list[Row]:
    """The rows that are commands for every H3/H4 check: all but the FOCUS_LOST row (C5)."""
    return [r for r in run.commands if not is_focus_lost(r)]


def expected_transitions(row: Row) -> list[tuple[int, bool]]:
    """(vk, down) per schedule transition of an OK row, in order (protocol.transitions, C2 VKs)."""
    steps = [Step(delta, Key(mask)) for delta, mask in row["steps"]]
    return [(VK_BY_KEY[k], down) for _, k, down in transitions(steps)]


def command_windows(commands: Sequence[Row]) -> list[tuple[int, float]]:
    """``[stimulus_ns(k) + 110 ms, stimulus_ns(k+1) + 110 ms)`` per command; the last runs to the end (inf)."""
    starts = [c["stimulus_ns"] + WINDOW_OFFSET_NS for c in commands]
    return [(s, starts[i + 1] if i + 1 < len(starts) else math.inf) for i, s in enumerate(starts)]


# ---- alignment -------------------------------------------------------------------------

@dataclass(frozen=True)
class Alignment:
    dropped: int
    extra: int
    out_of_order: int
    pairs: dict[int, int]             # expected index -> observed index, for the 'equal' opcodes
    extra_events: tuple[tuple[int, bool], ...] = ()   # the (vk, down) counted in `extra`, grouped by (vk, down)


def align(expected: Sequence[tuple[int, bool]], observed: Sequence[tuple[int, bool]]) -> Alignment:
    """Align with difflib opcodes. An event both missing and surplus within one command is out-of-order."""
    sm = difflib.SequenceMatcher(None, list(expected), list(observed), autojunk=False)
    missing: Counter = Counter()
    surplus: Counter = Counter()
    pairs: dict[int, int] = {}
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            pairs.update(zip(range(i1, i2), range(j1, j2)))
        else:
            missing.update(expected[i1:i2])
            surplus.update(observed[j1:j2])
    moved = sum((missing & surplus).values())
    extras = surplus - missing
    return Alignment(sum(missing.values()) - moved, sum(surplus.values()) - moved, moved, pairs,
                     tuple(extras.elements()))


# ---- listener liveness and delivery matching --------------------------------------------

@dataclass(frozen=True)
class Liveness:
    dead_from_index: int | None       # index into the command list; None when the listener stayed alive
    dead_from_seq: int | None
    last_event_ns: int | None         # qpc_ns of the last keys.jsonl row (transition or repeat)


def listener_liveness(commands: Sequence[Row], windows: Sequence[tuple[int, float]], keys: Sequence[Row]) -> Liveness:
    """LISTENER_DEAD: >= 5 consecutive DONE OK commands with no keys.jsonl event from the first one's window
    start to the end of the log (PA-rev I4: silence followed by any event is not a dead hook)."""
    last = keys[-1]["qpc_ns"] if keys else None
    first_ok, run_len = None, 0      # first DONE OK command of the silent tail; DONE OK run length in it
    for i, (row, (start, _)) in enumerate(zip(commands, windows)):
        if last is not None and start <= last:
            first_ok, run_len = None, 0
        elif is_ok(row):
            first_ok = i if first_ok is None else first_ok
            run_len += 1
            if run_len >= LISTENER_DEAD_N:
                # A Failure inside the silent tail breaks the run of 5 but not the silence: the DONE OK commands
                # before it are as unmeasured as the ones after (A-H3-R m1).
                return Liveness(first_ok, commands[first_ok]["seq"], last)
        else:
            run_len = 0
    return Liveness(None, None, last)


@dataclass(frozen=True)
class CommandMatch:
    seq: int
    row: Row
    window: tuple[int, float]
    observed: list[Row]               # listener transitions in the window, by qpc_ns
    alignment: Alignment | None       # None for a failed command
    first_down_ns: int | None         # first observed key-down in the window at or after sent_ns (A-H3-R m4)


@dataclass(frozen=True)
class DeliveryMatch:
    commands: list[CommandMatch]      # measured commands, file order (before LISTENER_DEAD; FOCUS_LOST excluded)
    unmeasured: list[Row]             # commands from LISTENER_DEAD's seq on
    outside: list[Row]                # transitions before the first window (extras of no command)
    liveness: Liveness


def match_delivery(run: RunLog) -> DeliveryMatch:
    cmds = commands_of(run)
    windows = command_windows(cmds)
    live = listener_liveness(cmds, windows, run.keys)
    starts = [w[0] for w in windows]
    per: list[list[Row]] = [[] for _ in cmds]
    outside: list[Row] = []
    for e in run.keys:
        if e["repeat"]:
            continue
        i = bisect.bisect_right(starts, e["qpc_ns"]) - 1
        (outside if i < 0 else per[i]).append(e)
    n_measured = len(cmds) if live.dead_from_index is None else live.dead_from_index
    matches = []
    for i in range(n_measured):
        row, obs = cmds[i], per[i]
        al = align(expected_transitions(row), [(e["vk"], e["down"]) for e in obs]) if is_ok(row) else None
        sent = row["sent_ns"]      # a Failure may have no sent_ns; nothing bot-made goes down before it is sent
        first_down = next((e["qpc_ns"] for e in obs if e["down"] and (sent is None or e["qpc_ns"] >= sent)), None)
        matches.append(CommandMatch(row["seq"], row, windows[i], obs, al, first_down))
    return DeliveryMatch(matches, cmds[n_measured:], outside, live)


# ---- statistics --------------------------------------------------------------------------

def percentile(values: Sequence[float], p: float) -> float:
    """Nearest-rank percentile of a non-empty sequence."""
    s = sorted(values)
    return s[max(0, math.ceil(p / 100 * len(s)) - 1)]


def m7_split(matches: Sequence[CommandMatch]) -> dict[str, tuple[float, float] | None]:
    """M7 split (V1 M7, PA-fix1c-D1-adj-D1, PA-rev3a-D1): p50/p99 in ms over DONE OK commands of write
    (written − sent), board accept (accepted − sent), board to listener (first key-down − accepted) and total
    (first key-down − sent). None for a part with no values."""
    parts: dict[str, list[float]] = {"write": [], "board accept": [], "board to listener": [], "total": []}
    for m in matches:
        r = m.row
        if not is_ok(r):
            continue
        sent, acc, down = r["sent_ns"], r["accepted_ns"], m.first_down_ns
        parts["write"].append((r["written_ns"] - sent) / NS_PER_MS)
        if acc is not None:
            parts["board accept"].append((acc - sent) / NS_PER_MS)
        if down is not None:
            parts["total"].append((down - sent) / NS_PER_MS)
            if acc is not None:
                parts["board to listener"].append((down - acc) / NS_PER_MS)
    return {k: (percentile(v, 50), percentile(v, 99)) if v else None for k, v in parts.items()}


# ---- checks -----------------------------------------------------------------------------

@dataclass(frozen=True)
class Check:
    name: str
    verdict: str                      # "PASS" | "FAIL" (gated) or "INFO" | "n/a" (reported, not gated)
    summary: str
    details: list[str] = field(default_factory=list)

    @property
    def failed(self) -> bool:
        return self.verdict == "FAIL"

    def render(self) -> list[str]:
        head = f"{self.name}: {self.verdict} {self.summary}".rstrip()
        return [head] + [f"  {d}" for d in self.details]


def _seqs(rows: Sequence[Row]) -> str:
    return ", ".join(str(r["seq"]) for r in rows)


def _events(events: Iterable[tuple[int, bool]]) -> str:
    """``E 0x45 down`` for a bot key, ``0x41 down (not a bot key)`` for any other (an operator or the OS)."""
    out = []
    for vk, down in events:
        label = f"0x{vk:02X} {'down' if down else 'up'}"
        out.append(f"{KEY_BY_VK[vk].name} {label}" if vk in KEY_BY_VK else f"{label} (not a bot key)")
    return ", ".join(out)


def check_liveness(dm: DeliveryMatch) -> Check:
    live = dm.liveness
    if live.dead_from_seq is None:
        return Check("Listener liveness", "PASS", f"no LISTENER_DEAD (target: no run of ≥{LISTENER_DEAD_N} "
                     "DONE OK commands with no listener event to the end of keys.jsonl)")
    last = "none" if live.last_event_ns is None else live.last_event_ns
    return Check("Listener liveness", "FAIL",
                 f"LISTENER_DEAD from seq {live.dead_from_seq} (last listener event qpc_ns {last})",
                 [f"{len(dm.unmeasured)} commands unmeasured (excluded from Delivery, Latency, Faults); "
                  "the run is invalid for the soak targets"])


def check_delivery(run: RunLog, dm: DeliveryMatch) -> Check:
    ok = [m for m in dm.commands if m.alignment is not None]
    failed = [m for m in dm.commands if m.alignment is None]
    dropped = sum(m.alignment.dropped for m in ok)
    extra = sum(m.alignment.extra for m in ok) + len(dm.outside)
    moved = sum(m.alignment.out_of_order for m in ok)
    injected = sum(1 for e in run.keys if e["injected"])
    no_fault = not run.faults
    details = [f"seq {m.seq}: dropped {m.alignment.dropped} extra {m.alignment.extra} "
               f"out-of-order {m.alignment.out_of_order}"
               + (f" (extra: {_events(m.alignment.extra_events)})" if m.alignment.extra_events else "")
               for m in ok if m.alignment.dropped or m.alignment.extra or m.alignment.out_of_order]
    if dm.outside:
        details.append(f"extra outside any command window: {len(dm.outside)} "
                       f"({_events((e['vk'], e['down']) for e in dm.outside)})")
    if injected:
        details.append(f"injected events: {injected} (every observed event must have injected=false)")
    if failed:
        if no_fault:
            details += [f"FAILED seq {m.seq} {m.row['error']}" for m in failed]
        else:
            by_err: dict[str, list[Row]] = {}
            for m in failed:
                by_err.setdefault(m.row["error"], []).append(m.row)
            details += [f"failed commands: {err} {len(rows)} (seq {_seqs(rows)})" for err, rows in by_err.items()]
        ev: Counter = Counter()
        for m in failed:
            ev[m.row["error"]] += len(m.observed)
        details.append("events of failed commands: " + ", ".join(f"{e} {n}" for e, n in ev.items()))
    passed = not (dropped or extra or moved or injected or (no_fault and failed))
    summary = (f"dropped {dropped} extra {extra} out-of-order {moved} over {len(ok)} DONE OK commands, "
               f"failed {len(failed)}, unmeasured {len(dm.unmeasured)} (target: {TARGET_DELIVERY})")
    return Check("Delivery", "PASS" if passed else "FAIL", summary, details)


def check_latency(dm: DeliveryMatch) -> Check:
    ok = [m for m in dm.commands if m.alignment is not None]
    lat = [m.first_down_ns - m.row["sent_ns"] for m in ok if m.first_down_ns is not None]
    details = []
    no_down = [m.row for m in ok if m.first_down_ns is None]
    if no_down:
        details.append(f"no key-down seen: {len(no_down)} (seq {_seqs(no_down)})")
    for name, v in m7_split(dm.commands).items():
        details.append(f"M7 {name} " + ("n/a" if v is None else f"p50 {v[0]:.3f} ms p99 {v[1]:.3f} ms")
                       + " (reported, not gated)")
    if not lat:
        return Check("Latency", "FAIL", f"no samples (target: {TARGET_LATENCY})", details)
    p99 = percentile(lat, 99)
    verdict = "PASS" if p99 <= LATENCY_P99_TARGET_NS else "FAIL"
    return Check("Latency", verdict, f"p99 {p99 / NS_PER_MS:.3f} ms over {len(lat)} commands "
                 f"(target: {TARGET_LATENCY})", details)


def check_faults(run: RunLog, dm: DeliveryMatch) -> Check:
    if not run.faults:
        return Check("Faults", "n/a", "no faults.jsonl entries (no injected fault)")
    details, bad = [], 0
    injected = sorted(f["injected_ns"] for f in run.faults if f["injected_ns"] is not None)
    for n, f in enumerate(run.faults, 1):
        inj, rep = f["injected_ns"], f["reported_ns"]
        # "after the fault" ends where the next fault is injected: a loss after fault k+1 is fault k+1's (A-H3-R m2)
        until = next((t for t in injected if inj is not None and t > inj), math.inf)
        lost = [] if inj is None else [
            m.row for m in dm.commands if m.alignment is not None and m.alignment.dropped
            and m.row["done_ns"] is not None and inj < m.row["done_ns"] <= until]
        if inj is None or rep is None:
            bad += 1
            details.append(f"fault {n} {f['kind']}: FAIL injected_ns {inj} reported_ns {rep} "
                           "(fault moment not observed, or fault never reported)")
            if lost:      # the fault FAILs regardless; its silent loss is still listed (A-H4-R fyi)
                details.append(f"silent loss after fault: seq {_seqs(lost)}")
            continue
        late = rep - inj > FAULT_REPORT_LIMIT_NS
        bad += bool(late or lost)
        details.append(f"fault {n} {f['kind']}: {'FAIL' if late or lost else 'PASS'} reported after "
                       f"{(rep - inj) / NS_PER_MS:.3f} ms ({f['error']})")
        if lost:
            details.append(f"silent loss after fault: seq {_seqs(lost)}")
    verdict = "FAIL" if bad else "PASS"
    return Check("Faults", verdict, f"{len(run.faults) - bad}/{len(run.faults)} faults pass (target: {TARGET_FAULTS})",
                 details)


def check_stuck_keys(run: RunLog) -> Check:
    if not run.faults:
        return Check("Stuck keys", "n/a", "no faults.jsonl entries")
    cmds = commands_of(run)
    trans = [e for e in run.keys if not e["repeat"]]
    details, total = [], 0
    for n, f in enumerate(run.faults, 1):
        rep = f["reported_ns"]
        if rep is None:
            details.append(f"fault {n} {f['kind']}: STUCK n/a (reported_ns null)")
            continue
        ref = next((c["stimulus_ns"] for c in cmds if c["stimulus_ns"] > rep), math.inf)
        down: set[int] = set()
        for e in trans:
            if e["qpc_ns"] >= ref:
                break
            (down.add if e["down"] else down.discard)(e["vk"])
        stuck = sorted(down & BOT_VKS)
        total += len(stuck)
        names = " ".join(KEY_BY_VK[vk].name for vk in stuck)
        details.append(f"fault {n} {f['kind']}: STUCK {len(stuck)} {names}".rstrip())
    return Check("Stuck keys", "INFO", f"total {total} (reported, not gated; any n > 0 is a [review] item)", details)


def check_connection_starts(path: Path | str | None) -> Check:
    if path is None:
        return Check("Connection starts", "n/a", "")
    path = Path(path)
    if not path.exists():
        return Check("Connection starts", "FAIL", f"{path} not found (target: {TARGET_STARTS})")
    rows = read_jsonl(path)
    counts = Counter(r["condition"] for r in rows)
    fails = [r for r in rows if r["result"] != "Success"]
    passed = len(rows) == 20 and dict(counts) == STARTS_EXPECTED and not fails
    per = ", ".join(f"{c} {counts.get(c, 0)}" for c in STARTS_EXPECTED)
    details = [f"Failure attempt {r['attempt']} {r['condition']} {r['error']}" for r in fails]
    secs = [r["seconds"] for r in rows if r["seconds"] is not None]
    if secs:
        details.append(f"seconds p50 {percentile(secs, 50):.3f} max {max(secs):.3f}")
    return Check("Connection starts", "PASS" if passed else "FAIL",
                 f"{len(rows) - len(fails)}/{len(rows)} Success ({per}) (target: {TARGET_STARTS})", details)


def check_connection_drops(run: RunLog) -> Check:
    if run.link_faults is None:
        return Check("Connection drops", "FAIL", f"link_faults.jsonl missing (no drop data) (target: {TARGET_DROPS})")
    drops = [r for r in run.link_faults if r["error"] == "LINK_LOST"]
    details = [f"LINK_LOST qpc_ns {r['qpc_ns']}" for r in drops]
    cmds = commands_of(run)
    dones = [c["done_ns"] for c in cmds if c["done_ns"] is not None]
    span = dones[-1] - cmds[0]["stimulus_ns"] if cmds and dones else 0
    summary = f"drops {len(drops)} over a connected span of {span / NS_PER_H:.2f} h"
    if run.faults:
        return Check("Connection drops", "INFO", f"{summary} (fault soak: reported, not gated)", details)
    passed = not drops and span >= CONNECTED_SPAN_TARGET_NS
    return Check("Connection drops", "PASS" if passed else "FAIL", f"{summary} (target: {TARGET_DROPS})", details)


def analyze(run_dir: Path | str, starts: Path | str | None = None) -> list[Check]:
    run = load_run(run_dir)
    dm = match_delivery(run)
    checks = [check_liveness(dm)]
    fl = next((r for r in run.commands if is_focus_lost(r)), None)
    if fl is not None:
        checks.append(Check("FOCUS_LOST", "INFO", f"FOCUS_LOST at seq {fl['seq']} (the run ended early, H2 "
                            "focus policy)"))
    checks += [check_delivery(run, dm), check_latency(dm), check_faults(run, dm), check_stuck_keys(run),
               check_connection_starts(starts), check_connection_drops(run)]
    return checks


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m src.input.harness.analyze", description=__doc__.splitlines()[0])
    ap.add_argument("run_dir", type=Path, help="soak run dir (C5)")
    ap.add_argument("--starts", type=Path, default=None, help="session_starts.jsonl (V2) for Connection starts")
    args = ap.parse_args(argv)
    checks = analyze(args.run_dir, args.starts)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")   # targets quote "≥"/"≤"; a redirected stdout is cp1252 on Windows
    for c in checks:
        print("\n".join(c.render()))
    return 1 if any(c.failed for c in checks) else 0


if __name__ == "__main__":
    sys.exit(main())
