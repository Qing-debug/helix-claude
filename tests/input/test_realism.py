"""Tests for timing realism and delivered timing (plan A, Task H4; PAr-D1 ruling C), on synthetic C5 run dirs."""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import numpy as np
import pytest

from src.input import actions
from src.input.harness import realism
from src.input.harness.listener import VK_BY_KEY
from src.input.protocol import Key
from src.input.timing import TimingModel, TimingType

MS = 1_000_000
SPACING = 5_000 * MS            # stimulus_ns of command i (0-based); longer than any action + reaction
LATENCY = 20 * MS               # send -> first key-down
VK = {k: VK_BY_KEY[k] for k in Key}
T = TimingType


# ---- builders ------------------------------------------------------------------------

def model(seed: int = 0) -> TimingModel:
    return TimingModel(np.random.default_rng(seed))


# A fixed seed fixes each KS outcome. Seed 0 gives p 0.16-0.52 per type at n = 1 000; seed 7 gives p ~0.001 for all
# three lognormal types at once (they transform the same normal stream), a 1-in-100 draw, not a model fault.
def draw(t: TimingType, n: int, seed: int = 0) -> list[float]:
    m = model(seed)
    fn = {T.HOLD: m.hold, T.GAP: m.gap, T.CHORD_OFFSET: m.chord_offset, T.REACTION: m.reaction}[t]
    return [fn() for _ in range(n)]


def sample(t: TimingType, ms: float, *, wire: int | None = None, frm: int | None = 0, to: int | None = 1) -> dict:
    return {"type": t.value, "chosen_ms": ms, "wire_100us": round(ms * 10) if wire is None else wire,
            "index": 0, "step_from": frm, "step_to": to}


def plan_row(seq: int, plan: actions.Plan, *, reaction_ms: float = 200.0, waited_ms: float | None = None,
             intended_ms: float | None = None) -> dict:
    stim = (seq - 1) * SPACING
    waited = reaction_ms if waited_ms is None else waited_ms
    sent = stim + round(waited * MS)
    return {"seq": seq, "action": plan.name, "intended_ms": intended_ms, "stimulus_ns": stim,
            "steps": [[s.delta_100us, int(s.mask)] for s in plan.steps],
            "samples": [{"type": s.type.value, "chosen_ms": s.ms, "wire_100us": s.wire_100us, "index": s.index,
                         "step_from": s.step_from, "step_to": s.step_to} for s in plan.samples],
            "reaction_ms": reaction_ms, "reaction_index": 0, "waited_ms": waited,
            "sent_ns": sent, "written_ns": sent + MS, "accepted_ns": sent + 5 * MS, "done_ns": sent + 400 * MS,
            "t0_us": 0, "late_us": [0] * len(plan.steps), "status": "OK", "error": None, "bytes": "00"}


def raw_row(seq: int, steps: list[list[int]], samples: list[dict], **kw) -> dict:
    row = plan_row(seq, actions.tap(model(), Key.E), **kw)
    row.update(steps=steps, samples=samples)
    return row


def focus_lost_row(seq: int) -> dict:
    row = {k: None for k in plan_row(1, actions.tap(model(), Key.E))}
    row.update(seq=seq, action="tap", status="FOCUS_LOST", error="FOCUS_LOST")
    return row


def key(qpc: int, vk: int, down: bool, *, repeat: bool = False) -> dict:
    return {"qpc_ns": qpc, "vk": vk, "down": down, "injected": False, "tick_ms": qpc // MS, "repeat": repeat}


def events_for(row: dict, shift: dict[int, int] | None = None) -> list[dict]:
    """A perfect delivery of `row` (first transition LATENCY after sent_ns); `shift` adds ns to transition i."""
    out, t, prev = [], row["sent_ns"] + LATENCY, 0
    for delta, mask in row["steps"]:
        t += delta * 100_000
        for k in Key:
            if (prev ^ mask) & k:
                out.append(key(t + (shift or {}).get(len(out), 0), VK[k], bool(mask & k)))
        prev = mask
    return out


def write_jsonl(path: Path, rows) -> None:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def write_run(tmp_path: Path, commands, keys, *, pongs=None, name: str = "run") -> Path:
    run = tmp_path / name
    run.mkdir()
    write_jsonl(run / "commands.jsonl", commands)
    write_jsonl(run / "keys.jsonl", sorted(keys, key=lambda e: e["qpc_ns"]))
    if pongs is not None:
        write_jsonl(run / "pongs.jsonl", pongs)
    return run


def soak(teleports: int = 1_000, sequences: int = 400, walks: int = 50, seed: int = 7) -> list[dict]:
    """A simulated soak from the real builders: every KS type reaches >= 1 000 chosen samples."""
    m = model(seed)
    plans: list[tuple[actions.Plan, float | None]] = []
    plans += [(actions.teleport(m, Key.LEFT), None) for _ in range(teleports)]
    plans += [(actions.sequence(m, [Key.LEFT, Key.E, Key.SPACE, Key.UP]).unwrap(), None) for _ in range(sequences)]
    plans += [(actions.walk(m, Key.RIGHT, 1_000.0).unwrap(), 1_000.0) for _ in range(walks)]
    return [plan_row(i + 1, p, reaction_ms=m.reaction(), intended_ms=t) for i, (p, t) in enumerate(plans)]


def by_name(checks, name: str):
    return next(c for c in checks if c.name == name)


# ---- timing realism: KS on chosen values ----------------------------------------------

@pytest.mark.parametrize("t", [T.HOLD, T.GAP, T.CHORD_OFFSET, T.REACTION])
def test_ks_passes_on_draws_from_the_model(t):
    c = realism.check_ks(t, [sample(t, v) for v in draw(t, 1_000)])
    assert c.verdict == "PASS", c.summary


@pytest.mark.parametrize("t", [T.HOLD, T.GAP, T.CHORD_OFFSET, T.REACTION])
def test_ks_fails_on_draws_from_a_shifted_model(t):
    c = realism.check_ks(t, [sample(t, v * 1.2) for v in draw(t, 1_000)])
    assert c.verdict == "FAIL", c.summary


def test_ks_reads_chosen_ms_not_wire_100us():
    good = draw(T.CHORD_OFFSET, 1_000)
    assert realism.check_ks(T.CHORD_OFFSET, [sample(T.CHORD_OFFSET, v, wire=round(v * 12)) for v in good]).verdict == "PASS"
    assert realism.check_ks(T.CHORD_OFFSET, [sample(T.CHORD_OFFSET, v * 1.2, wire=round(v * 10))
                                             for v in good]).verdict == "FAIL"


def test_ks_under_1000_samples_fails_as_insufficient():
    c = realism.check_ks(T.HOLD, [sample(T.HOLD, v) for v in draw(T.HOLD, 999)])
    assert c.verdict == "FAIL"
    assert "FAIL (insufficient samples)" in c.render()[0]


def test_hold_clipped_chosen_ms_is_in_holds_ks_input():
    steps = [[0, int(Key.E)], [650, 0], [150, int(Key.E)], [1000, 0]]
    samples = [sample(T.HOLD_CLIPPED, 105.0, frm=0, to=1), sample(T.GAP, 80.0, frm=0, to=2),
               sample(T.HOLD, 100.0, frm=2, to=3)]
    chosen = realism.chosen_samples([raw_row(1, steps, samples)])
    assert sorted(s["chosen_ms"] for s in chosen[T.HOLD]) == [100.0, 105.0]
    assert [s["chosen_ms"] for s in chosen[T.GAP]] == [80.0]


def test_reaction_ks_uses_reaction_ms_not_waited_ms():
    r = draw(T.REACTION, 1_000)
    tap = actions.tap(model(), Key.E)
    rows = [plan_row(i + 1, tap, reaction_ms=v, waited_ms=v * 0.5) for i, v in enumerate(r)]
    chosen = realism.chosen_samples(rows)[T.REACTION]
    assert [s["chosen_ms"] for s in chosen] == r
    assert realism.check_ks(T.REACTION, chosen).verdict == "PASS"
    swapped = [plan_row(i + 1, tap, reaction_ms=v * 0.5, waited_ms=v) for i, v in enumerate(r)]
    assert realism.check_ks(T.REACTION, realism.chosen_samples(swapped)[T.REACTION]).verdict == "FAIL"


def test_gap_runs_pass_on_draws_and_fail_on_a_planted_repeat():
    gaps = draw(T.GAP, 2_000)
    assert realism.check_gap_runs(gaps).verdict == "PASS"
    planted = gaps[:1_000] + gaps[10:15] + gaps[1_000:]
    c = realism.check_gap_runs(planted)
    assert c.verdict == "FAIL"
    assert "1 repeated" in c.summary


def test_gap_runs_a_repeated_run_of_4_passes():
    gaps = draw(T.GAP, 200)
    assert realism.check_gap_runs(gaps[:100] + gaps[10:14] + gaps[100:]).verdict == "PASS"


def test_gap_stream_is_in_soak_order_across_commands(tmp_path):
    rows = soak(teleports=0, sequences=3, walks=0)
    expected = [s["chosen_ms"] for r in rows for s in r["samples"] if s["type"] == "gap"]
    assert realism.gap_stream(rows) == expected
    assert len(expected) == 9


# ---- delivered timing -----------------------------------------------------------------

def pairs_at(t: TimingType, hits: int, misses: int) -> list[realism.Pair]:
    hit = [realism.Pair(1, t, sample(t, 100.0), 100.0, 115.0) for _ in range(hits)]
    miss = [realism.Pair(1, t, sample(t, 100.0), 100.0, 125.0) for _ in range(misses)]
    return hit + miss


@pytest.mark.parametrize("t", [T.HOLD, T.WALK, T.HOLD_CLIPPED, T.CHORD_OFFSET, T.GAP])
def test_delivered_share_96_percent_passes_94_fails(t):
    assert realism.check_delivered(t, pairs_at(t, 96, 4)).verdict == "PASS"
    assert realism.check_delivered(t, pairs_at(t, 94, 6)).verdict == "FAIL"


def test_delivered_tolerance_is_inclusive_at_20_ms():
    assert realism.Pair(1, T.HOLD, sample(T.HOLD, 100.0), 100.0, 120.0).hit
    assert realism.Pair(1, T.HOLD, sample(T.HOLD, 100.0), 100.0, 79.999).hit is False
    assert realism.Pair(1, T.HOLD, sample(T.HOLD, 100.0), 100.0, None).hit is False


def test_walk_at_94_percent_fails_its_own_share_while_hold_is_at_100(tmp_path):
    m = model()
    rows, keys = [], []
    for i in range(100):
        rows.append(plan_row(i + 1, actions.tap(m, Key.E)))
        keys += events_for(rows[-1])
    for i in range(100):
        rows.append(plan_row(101 + i, actions.walk(m, Key.LEFT, 1_000.0).unwrap(), intended_ms=1_000.0))
        keys += events_for(rows[-1], {1: 40 * MS} if i < 6 else None)
    checks = realism.realism(write_run(tmp_path, rows, keys))
    assert by_name(checks, "Delivered timing hold").verdict == "PASS"
    assert "100/100" in by_name(checks, "Delivered timing hold").summary
    walk = by_name(checks, "Delivered timing walk")
    assert walk.verdict == "FAIL"
    assert "94/100" in walk.summary


def test_hold_clipped_is_paired_against_its_scheduled_interval(tmp_path):
    # E down 0, E up 65 ms (hold drawn 105 ms, clipped to the next down 80 ms minus 15 ms), E down 80, E up 180.
    steps = [[0, int(Key.E)], [650, 0], [150, int(Key.E)], [1000, 0]]
    samples = [sample(T.HOLD_CLIPPED, 105.0, wire=1050, frm=0, to=1), sample(T.GAP, 80.0, frm=0, to=2),
               sample(T.HOLD, 100.0, frm=2, to=3)]
    row = raw_row(1, steps, samples)
    run = write_run(tmp_path, [row], events_for(row))
    pairs = realism.pair_samples(realism.match_delivery(realism.load_run(run)))
    clipped = next(p for p in pairs if p.type == T.HOLD_CLIPPED)
    assert clipped.reference_ms == pytest.approx(65.0)
    assert clipped.measured_ms == pytest.approx(65.0)
    assert clipped.measured_ms == pytest.approx(clipped.sample["chosen_ms"] - 40.0)
    assert clipped.hit
    assert by_name(realism.realism(run), "Delivered timing hold_clipped").verdict == "PASS"


def test_hold_clipped_with_no_pairs_is_not_gated():
    c = realism.check_delivered(T.HOLD_CLIPPED, [])
    assert c.verdict == "n/a"
    assert "hold_clipped n = 0" in c.summary
    assert not c.failed


def test_other_shares_with_no_pairs_fail():
    assert realism.check_delivered(T.GAP, []).verdict == "FAIL"


def test_a_missing_transition_is_a_miss(tmp_path):
    m = model()
    rows = [plan_row(i + 1, actions.tap(m, Key.E)) for i in range(20)]
    keys = [e for r in rows for e in events_for(r)]
    keys.pop(1)                                   # command 1's key-up
    pairs = realism.pair_samples(realism.match_delivery(realism.load_run(write_run(tmp_path, rows, keys))))
    holds = [p for p in pairs if p.type == T.HOLD]
    assert len(holds) == 20
    assert holds[0].measured_ms is None and not holds[0].hit
    assert sum(p.hit for p in holds) == 19


def test_repeats_are_not_transitions_for_pairing(tmp_path):
    row = plan_row(1, actions.tap(model(), Key.E))
    ev = events_for(row)
    ev.append(key(ev[0]["qpc_ns"] + 5 * MS, VK[Key.E], True, repeat=True))
    pairs = realism.pair_samples(realism.match_delivery(realism.load_run(write_run(tmp_path, [row], ev))))
    assert pairs[0].measured_ms == pytest.approx(row["samples"][0]["wire_100us"] / 10)


# ---- chord order ----------------------------------------------------------------------

def test_one_inverted_chord_fails_chord_order(tmp_path):
    m = model()
    rows = [plan_row(i + 1, actions.teleport(m, Key.LEFT)) for i in range(10)]
    keys = []
    for i, r in enumerate(rows):
        ev = events_for(r)
        if i == 3:                                # E down seen before LEFT down
            ev[0]["qpc_ns"], ev[1]["qpc_ns"] = ev[1]["qpc_ns"], ev[0]["qpc_ns"]
        keys += ev
    c = by_name(realism.realism(write_run(tmp_path, rows, keys)), "Chord order")
    assert c.verdict == "FAIL"
    assert "violations 1" in c.summary
    assert any("seq 4" in d for d in c.details)


def test_chord_order_passes_on_ordered_chords(tmp_path):
    m = model()
    rows = [plan_row(i + 1, actions.drop(m)) for i in range(10)]
    keys = [e for r in rows for e in events_for(r)]
    c = by_name(realism.realism(write_run(tmp_path, rows, keys)), "Chord order")
    assert c.verdict == "PASS"
    assert "violations 0 over 10 chords" in c.summary


# ---- FOCUS_LOST, reported values, CLI ---------------------------------------------------

def test_a_focus_lost_last_row_changes_nothing(tmp_path):
    m = model()
    rows = [plan_row(i + 1, actions.teleport(m, Key.UP), reaction_ms=m.reaction()) for i in range(30)]
    keys = [e for r in rows for e in events_for(r)]
    without = realism.realism(write_run(tmp_path, rows, keys, name="a"))
    with_fl = realism.realism(write_run(tmp_path, rows + [focus_lost_row(31)], keys, name="b"))
    assert with_fl == without


def test_reaction_residual_excludes_clamped_waits(tmp_path):
    tap = actions.tap(model(), Key.E)
    rows = [plan_row(1, tap, reaction_ms=200.0, waited_ms=150.0),   # residual = 150 + 20 - 200 = -30 ms
            plan_row(2, tap, reaction_ms=200.0, waited_ms=0.0)]     # clamped: excluded
    keys = [e for r in rows for e in events_for(r)]
    c = by_name(realism.realism(write_run(tmp_path, rows, keys)), "Reaction residual")
    assert c.verdict == "INFO"
    assert "p50 -30.000 ms" in c.summary and "n 1" in c.summary and "clamped 1" in c.summary


def test_walk_error_stats_and_chord_collapse_and_ci_history(tmp_path):
    steps = [[0, int(Key.LEFT)], [12_000, 0]]
    rows = [raw_row(1, steps, [sample(T.WALK, 1_200.0, frm=0, to=1)], intended_ms=1_000.0),
            raw_row(2, steps, [sample(T.WALK, 1_200.0, frm=0, to=1)], intended_ms=1_100.0)]
    chord = plan_row(3, actions.teleport(model(), Key.UP))
    rows.append(chord)
    keys = [e for r in rows for e in events_for(r)]
    collapsed = events_for(chord)
    keys = [e for e in keys if e not in collapsed]
    collapsed[1]["qpc_ns"] = collapsed[0]["qpc_ns"] + MS // 2      # second key 0.5 ms after the first
    keys += collapsed
    pong = {"qpc_ns": 0, "itvl_units": 12, "latency": 0, "timeout_units": 100, "mtu": 247, "flags": 15, "board_us": 0}
    pongs = [pong, dict(pong, qpc_ns=10), dict(pong, qpc_ns=20, itvl_units=24)]
    checks = realism.realism(write_run(tmp_path, rows, keys, pongs=pongs))
    walk = by_name(checks, "WALK error")
    assert "mean 150.000 ms" in walk.summary and "SD 70.711 ms" in walk.summary and "n 2" in walk.summary
    assert "1/1" in by_name(checks, "Chord collapse").summary
    ci = by_name(checks, "CI history")
    assert "15.00 ms" in ci.summary + " ".join(ci.details) and "30.00 ms" in " ".join(ci.details)


def test_cli_exit_0_on_a_simulated_soak(tmp_path, capsys):
    rows = soak()
    keys = [e for r in rows for e in events_for(r)]
    assert realism.main([str(write_run(tmp_path, rows, keys))]) == 0
    out = capsys.readouterr().out
    for line in ("KS hold: PASS", "KS gap: PASS", "KS chord_offset: PASS", "KS reaction: PASS", "Gap runs: PASS",
                 "Delivered timing hold: PASS", "Delivered timing walk: PASS", "Delivered timing chord_offset: PASS",
                 "Delivered timing gap: PASS", "Chord order: PASS", "hold_clipped n = 0", "Sample counts: INFO"):
        assert line in out


def test_cli_exit_nonzero_on_any_fail(tmp_path, capsys):
    m = model()
    rows = [plan_row(i + 1, actions.tap(m, Key.E)) for i in range(5)]
    keys = [e for r in rows for e in events_for(r)]
    assert realism.main([str(write_run(tmp_path, rows, keys))]) != 0      # n < 1 000 for every KS type
    assert "KS hold: FAIL (insufficient samples)" in capsys.readouterr().out


def test_cli_survives_a_cp1252_stdout(tmp_path, monkeypatch):
    buf = io.BytesIO()
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(buf, encoding="cp1252"))
    rows = [plan_row(1, actions.tap(model(), Key.E))]
    realism.main([str(write_run(tmp_path, rows, events_for(rows[0])))])
    sys.stdout.flush()
    assert "≥" in buf.getvalue().decode("utf-8")


def test_stray_key_before_sent_ns_does_not_skew_reaction_residual_or_measured_reaction(tmp_path):
    """A-H3-R m4 carried into H4 (A-H4 concern 3): the first key-down is taken at or after sent_ns."""
    tap = actions.tap(model(), Key.E)
    rows = [plan_row(1, tap, reaction_ms=200.0, waited_ms=150.0)]
    keys = [e for r in rows for e in events_for(r)]
    stray = {"qpc_ns": rows[0]["sent_ns"] - 20 * MS, "vk": 0x41, "down": True, "injected": False,
             "tick_ms": 0, "repeat": False}
    out = realism.realism(write_run(tmp_path, rows, keys + [stray]))
    assert "p50 -30.000 ms" in by_name(out, "Reaction residual").summary     # not -70 ms (the stray 20 ms before sent)
