"""Tests for the soak-log analysis (plan A, Task H3), on synthetic C5 run dirs."""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

from src.input.harness import analyze
from src.input.harness.listener import VK_BY_KEY
from src.input.protocol import Key

MS = 1_000_000
S = 1_000 * MS
H = 3_600 * S
SPACING = S                      # stimulus_ns of command i (0-based) = i * SPACING by default
SEND_AFTER = 200 * MS            # stimulus -> send
LATENCY = 20 * MS                # send -> first key-down

VK = {k: VK_BY_KEY[k] for k in Key}


# ---- synthetic log builders ---------------------------------------------------------

def tap(key: Key, hold_ms: int = 100) -> list[list[int]]:
    return [[0, int(key)], [hold_ms * 10, 0]]


def chord(k1: Key, k2: Key, off_ms: int = 20, hold_ms: int = 100, lag_ms: int = 10) -> list[list[int]]:
    return [[0, int(k1)], [off_ms * 10, int(k1 | k2)], [hold_ms * 10, int(k1)], [lag_ms * 10, 0]]


def ok_cmd(seq: int, steps=None, *, stim: int | None = None, written: int = 1 * MS,
           accepted: int = 5 * MS, done: int = 400 * MS) -> dict:
    stim = (seq - 1) * SPACING if stim is None else stim
    sent = stim + SEND_AFTER
    return {"seq": seq, "action": "tap", "intended_ms": None, "stimulus_ns": stim,
            "steps": steps if steps is not None else tap(Key.E),
            "samples": [], "reaction_ms": 200.0, "reaction_index": 0, "waited_ms": 200.0,
            "sent_ns": sent, "written_ns": sent + written, "accepted_ns": sent + accepted,
            "done_ns": sent + done, "t0_us": 0, "late_us": [0, 0], "status": "OK", "error": None,
            "bytes": "00"}


def failed_cmd(seq: int, error: str = "ACK_TIMEOUT", *, stim: int | None = None) -> dict:
    stim = (seq - 1) * SPACING if stim is None else stim
    row = {k: None for k in ok_cmd(seq)}
    row.update(seq=seq, action="tap", stimulus_ns=stim, status=error, error=error)
    return row


def focus_lost_row(seq: int) -> dict:
    row = {k: None for k in ok_cmd(seq)}
    row.update(seq=seq, action="tap", status="FOCUS_LOST", error="FOCUS_LOST")
    return row


def key(qpc: int, vk: int, down: bool, *, repeat: bool = False, injected: bool = False) -> dict:
    return {"qpc_ns": qpc, "vk": vk, "down": down, "injected": injected, "tick_ms": qpc // MS, "repeat": repeat}


def events_for(row: dict, latency: int = LATENCY) -> list[dict]:
    """The listener events a perfect delivery of `row` produces, first key-down `latency` after sent_ns."""
    out, t, prev = [], row["sent_ns"] + latency, 0
    for delta, mask in row["steps"]:
        t += delta * 100_000
        for k in Key:
            if (prev ^ mask) & k:
                out.append(key(t, VK[k], bool(mask & k)))
        prev = mask
    return out


def perfect(n: int, spacing: int = SPACING) -> tuple[list[dict], list[dict]]:
    cmds = [ok_cmd(i + 1, stim=i * spacing) for i in range(n)]
    return cmds, [e for c in cmds for e in events_for(c)]


def write_jsonl(path: Path, rows) -> None:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def write_run(tmp_path: Path, commands, keys, *, faults=None, link_faults=(), pongs=None) -> Path:
    """faults=None leaves faults.jsonl absent; link_faults=None leaves link_faults.jsonl absent; pongs=None leaves
    pongs.jsonl absent."""
    run = tmp_path / "run"
    run.mkdir()
    write_jsonl(run / "commands.jsonl", commands)
    write_jsonl(run / "keys.jsonl", sorted(keys, key=lambda e: e["qpc_ns"]))
    if faults is not None:
        write_jsonl(run / "faults.jsonl", faults)
    if link_faults is not None:
        write_jsonl(run / "link_faults.jsonl", link_faults)
    if pongs is not None:
        write_jsonl(run / "pongs.jsonl", [{"qpc_ns": t, "board_us": t // 1000} for t in pongs])
    return run


def fault(injected: int | None, reported: int | None, kind: str = "bluetooth_off_on", error="LINK_LOST") -> dict:
    """A PC-side fault (no worst-case check); reset_board faults come from reset_fault()."""
    return {"injected_ns": injected, "kind": kind, "reported_ns": reported,
            "error": None if reported is None else error, "launch_ns": None,
            "fault_seen_ns": reported, "failure_ns": reported}


def checks(run: Path, starts: Path | None = None) -> dict[str, analyze.Check]:
    return {c.name: c for c in analyze.analyze(run, starts)}


def text(c: analyze.Check) -> str:
    return "\n".join(c.render())


# ---- Delivery ------------------------------------------------------------------------

def test_perfect_run_passes_delivery_latency_and_liveness(tmp_path):
    cmds, keys = perfect(5)
    cmds[2]["steps"] = chord(Key.LEFT, Key.E)
    keys = [e for c in cmds for e in events_for(c)]
    res = checks(write_run(tmp_path, cmds, keys))
    assert res["Listener liveness"].verdict == "PASS"
    assert res["Delivery"].verdict == "PASS"
    assert "dropped 0 extra 0 out-of-order 0" in text(res["Delivery"])
    assert res["Latency"].verdict == "PASS"


def test_one_dropped_event_fails_delivery(tmp_path):
    cmds, keys = perfect(3)
    keys.pop(3)                                   # command 2's E up
    res = checks(write_run(tmp_path, cmds, keys))
    assert res["Delivery"].verdict == "FAIL"
    assert "dropped 1 extra 0 out-of-order 0" in text(res["Delivery"])


def test_one_extra_event_fails_delivery(tmp_path):
    cmds, keys = perfect(3)
    t = cmds[1]["sent_ns"] + 300 * MS
    keys += [key(t, VK[Key.SPACE], True)]
    res = checks(write_run(tmp_path, cmds, keys))
    assert res["Delivery"].verdict == "FAIL"
    assert "dropped 0 extra 1 out-of-order 0" in text(res["Delivery"])


def test_one_swap_is_out_of_order_not_drop_plus_extra(tmp_path):
    cmds = [ok_cmd(1), ok_cmd(2, chord(Key.LEFT, Key.E)), ok_cmd(3)]
    keys = [e for c in cmds for e in events_for(c)]
    i = next(n for n, e in enumerate(keys) if e["vk"] == VK[Key.LEFT] and e["down"])
    a, b = keys[i], keys[i + 1]                   # LEFT down, E down -> E down, LEFT down
    keys[i], keys[i + 1] = dict(b, qpc_ns=a["qpc_ns"]), dict(a, qpc_ns=b["qpc_ns"])
    res = checks(write_run(tmp_path, cmds, keys))
    assert res["Delivery"].verdict == "FAIL"
    assert "dropped 0 extra 0 out-of-order 1" in text(res["Delivery"])


def test_repeat_only_extra_rows_pass(tmp_path):
    cmds, keys = perfect(3)
    down = keys[0]
    keys.append(key(down["qpc_ns"] + 50 * MS, down["vk"], True, repeat=True))
    res = checks(write_run(tmp_path, cmds, keys))
    assert res["Delivery"].verdict == "PASS"


def test_injected_event_fails_delivery(tmp_path):
    cmds, keys = perfect(3)
    keys[2] = dict(keys[2], injected=True)
    res = checks(write_run(tmp_path, cmds, keys))
    assert res["Delivery"].verdict == "FAIL"
    assert "injected" in text(res["Delivery"])


def test_event_before_first_window_is_extra(tmp_path):
    cmds, keys = perfect(2)
    keys.append(key(50 * MS, VK[Key.SPACE], True))   # before stimulus(1) + 110 ms
    res = checks(write_run(tmp_path, cmds, keys))
    assert "extra 1" in text(res["Delivery"])


def write_session(run: Path, **fields) -> None:
    (run / "session.json").write_text(json.dumps({"run_id": "t", "start_ns": 0, "end_ns": 1, **fields}),
                                      encoding="utf-8")


def test_a_h2_r_d1_events_before_connected_ns_are_ignored_and_counted_on_their_own_line(tmp_path):
    """A-H2-R-D1 (ruling A): the operator's keys before the first connect() are not the board's."""
    cmds, keys = perfect(2)
    keys += [key(30 * MS, VK[Key.SPACE], True), key(40 * MS, VK[Key.SPACE], False)]   # before connect
    run = write_run(tmp_path, cmds, keys)
    write_session(run, connected_ns=50 * MS)
    res = checks(run)["Delivery"]
    assert res.verdict == "PASS"
    assert "extra 0" in res.summary
    assert "ignored before connect: 2" in text(res)


def test_a_h2_r_d1_event_at_or_after_connected_ns_outside_a_window_is_still_an_extra(tmp_path):
    cmds, keys = perfect(2)
    keys += [key(30 * MS, VK[Key.SPACE], True), key(50 * MS, VK[Key.SPACE], False)]   # 30 ms before, 50 ms == connect
    run = write_run(tmp_path, cmds, keys)
    write_session(run, connected_ns=50 * MS)
    res = checks(run)["Delivery"]
    assert res.verdict == "FAIL"
    assert "extra 1" in res.summary
    assert "ignored before connect: 1" in text(res)
    assert "extra outside any command window: 1" in text(res)


@pytest.mark.parametrize("session", [None, {}, {"connected_ns": None}], ids=["no file", "no key", "null"])
def test_a_h2_r_d1_without_a_connected_ns_every_early_event_is_an_extra(tmp_path, session):
    cmds, keys = perfect(2)
    keys.append(key(30 * MS, VK[Key.SPACE], True))
    run = write_run(tmp_path, cmds, keys)
    if session is not None:
        write_session(run, **session)
    res = checks(run)["Delivery"]
    assert res.verdict == "FAIL"
    assert "extra 1" in res.summary
    assert "ignored before connect" not in text(res)


def test_alignment_pairs_expected_to_observed_indexes():
    a = analyze.align([(1, True), (2, True), (2, False), (1, False)],
                      [(2, True), (1, True), (2, False), (1, False)])
    assert (a.dropped, a.extra, a.out_of_order) == (0, 0, 1)
    assert a.pairs[2] == 2 and a.pairs[3] == 3 and len(a.pairs) == 3


def test_match_delivery_exposes_per_command_windows_and_pairs(tmp_path):
    cmds, keys = perfect(3)
    m = analyze.match_delivery(analyze.load_run(write_run(tmp_path, cmds, keys)))
    assert [c.seq for c in m.commands] == [1, 2, 3]
    assert m.commands[0].window == (110 * MS, SPACING + 110 * MS)
    assert m.commands[2].window[1] == float("inf")
    assert m.commands[1].alignment.pairs == {0: 0, 1: 1}
    assert m.commands[1].observed[0]["qpc_ns"] == cmds[1]["sent_ns"] + LATENCY


# ---- Listener liveness ---------------------------------------------------------------

def test_listener_silent_to_end_is_listener_dead_and_unmeasured(tmp_path):
    cmds, _ = perfect(10)
    keys = [e for c in cmds[:4] for e in events_for(c)]   # silent after command seq 4
    res = checks(write_run(tmp_path, cmds, keys))
    live = res["Listener liveness"]
    assert live.verdict == "FAIL"
    assert f"LISTENER_DEAD from seq 5 (last listener event qpc_ns {keys[-1]['qpc_ns']})" in text(live)
    assert "dropped 0 extra 0 out-of-order 0" in text(res["Delivery"])
    assert res["Delivery"].verdict == "PASS"
    assert "unmeasured 6" in text(res["Delivery"])
    assert "over 4 commands" in text(res["Latency"])


def test_six_silent_commands_then_events_resume_are_drops(tmp_path):
    cmds, _ = perfect(10)
    keys = [e for c in cmds[:3] + cmds[9:] for e in events_for(c)]
    res = checks(write_run(tmp_path, cmds, keys))
    assert res["Listener liveness"].verdict == "PASS"
    assert "LISTENER_DEAD from" not in text(res["Listener liveness"])
    assert res["Delivery"].verdict == "FAIL"
    assert "dropped 12 extra 0" in text(res["Delivery"])


def test_four_missing_commands_at_end_are_drops_not_dead(tmp_path):
    cmds, _ = perfect(10)
    keys = [e for c in cmds[:6] for e in events_for(c)]
    res = checks(write_run(tmp_path, cmds, keys))
    assert res["Listener liveness"].verdict == "PASS"
    assert res["Delivery"].verdict == "FAIL"
    assert "dropped 8 extra 0" in text(res["Delivery"])


# ---- Failed commands, FOCUS_LOST -----------------------------------------------------

def test_events_in_failed_command_window_are_not_extras(tmp_path):
    cmds = [ok_cmd(1), failed_cmd(2), ok_cmd(3)]
    keys = events_for(cmds[0]) + events_for(cmds[2])
    t = cmds[1]["stimulus_ns"] + 300 * MS
    keys += [key(t, VK[Key.E], True), key(t + 80 * MS, VK[Key.E], False)]
    res = checks(write_run(tmp_path, cmds, keys, faults=[fault(SPACING, SPACING + 500 * MS)]))
    d = text(res["Delivery"])
    assert "dropped 0 extra 0 out-of-order 0" in d
    assert "events of failed commands: ACK_TIMEOUT 2" in d
    m = analyze.match_delivery(analyze.load_run(tmp_path / "run"))
    assert m.commands[2].alignment.dropped == 0 and len(m.commands[2].observed) == 2


def test_failed_command_fails_delivery_in_no_fault_run(tmp_path):
    cmds = [ok_cmd(1), failed_cmd(2), ok_cmd(3)]
    keys = events_for(cmds[0]) + events_for(cmds[2])
    res = checks(write_run(tmp_path, cmds, keys))
    assert res["Delivery"].verdict == "FAIL"
    assert "FAILED seq 2 ACK_TIMEOUT" in text(res["Delivery"])


def test_failed_command_does_not_gate_delivery_in_fault_soak(tmp_path):
    cmds = [ok_cmd(1), failed_cmd(2), ok_cmd(3)]
    keys = events_for(cmds[0]) + events_for(cmds[2])
    res = checks(write_run(tmp_path, cmds, keys, faults=[fault(SPACING, SPACING + 500 * MS)]))
    d = text(res["Delivery"])
    assert res["Delivery"].verdict == "PASS"
    assert "failed commands: ACK_TIMEOUT 1 (seq 2)" in d
    assert "FAILED seq" not in d


def test_focus_lost_last_row_is_reported_and_in_no_window(tmp_path):
    cmds, keys = perfect(2)
    cmds.append(focus_lost_row(3))
    res = checks(write_run(tmp_path, cmds, keys))
    assert "FOCUS_LOST at seq 3" in text(res["FOCUS_LOST"])
    assert res["Delivery"].verdict == "PASS"              # not a failed command
    m = analyze.match_delivery(analyze.load_run(tmp_path / "run"))
    assert [c.seq for c in m.commands] == [1, 2]
    assert m.commands[1].window[1] == float("inf")        # the command before it runs to the end
    assert "over 2 commands" in text(res["Latency"])


def test_no_focus_lost_row_no_focus_lost_check(tmp_path):
    cmds, keys = perfect(2)
    assert "FOCUS_LOST" not in checks(write_run(tmp_path, cmds, keys))


# ---- Latency and the M7 split --------------------------------------------------------

def test_percentile_is_nearest_rank():
    assert analyze.percentile([4.0, 1.0, 3.0, 2.0], 50) == 2.0
    assert analyze.percentile([float(i) for i in range(1, 101)], 99) == 99.0
    assert analyze.percentile([7.0], 99) == 7.0


def test_latency_p99_exactly_at_target_passes(tmp_path):
    cmds = [ok_cmd(i + 1) for i in range(3)]
    keys = [e for c in cmds for e in events_for(c, latency=100 * MS)]
    res = checks(write_run(tmp_path, cmds, keys))
    assert res["Latency"].verdict == "PASS"
    assert "p99 100.000 ms" in text(res["Latency"])


def test_latency_p99_over_target_fails(tmp_path):
    cmds = [ok_cmd(i + 1) for i in range(3)]
    keys = [e for c in cmds for e in events_for(c, latency=100 * MS + 1)]
    res = checks(write_run(tmp_path, cmds, keys))
    assert res["Latency"].verdict == "FAIL"


def test_latency_skips_command_with_no_key_down_and_failed_commands(tmp_path):
    cmds = [ok_cmd(1), ok_cmd(2), failed_cmd(3), ok_cmd(4)]
    keys = events_for(cmds[0]) + events_for(cmds[3])          # command 2 fully dropped
    res = checks(write_run(tmp_path, cmds, keys, faults=[fault(SPACING, SPACING + 10 * MS)]))
    lat = text(res["Latency"])
    assert "over 2 commands" in lat and "no key-down seen: 1 (seq 2)" in lat


def test_latency_with_no_samples_fails(tmp_path):
    res = checks(write_run(tmp_path, [failed_cmd(1)], [], faults=[fault(0, 10 * MS)]))
    assert res["Latency"].verdict == "FAIL"


def test_m7_split_four_values(tmp_path):
    # write = written - sent; board accept = accepted - sent (> 0 even when accepted < written);
    # board to listener = first key-down - accepted; total = first key-down - sent.
    cmds = [ok_cmd(1, written=3 * MS, accepted=2 * MS), ok_cmd(2, written=1 * MS, accepted=6 * MS)]
    keys = events_for(cmds[0], latency=20 * MS) + events_for(cmds[1], latency=30 * MS)
    m = analyze.match_delivery(analyze.load_run(write_run(tmp_path, cmds, keys)))
    split = analyze.m7_split(m.commands)
    assert split == {"write": (1.0, 3.0), "board accept": (2.0, 6.0),
                     "board to listener": (18.0, 24.0), "total": (20.0, 30.0)}
    lat = text(checks(tmp_path / "run")["Latency"])
    assert "M7 board accept p50 2.000 ms p99 6.000 ms" in lat


# ---- Faults and stuck keys -----------------------------------------------------------

def test_fault_reported_within_1s_passes(tmp_path):
    cmds, keys = perfect(3)
    res = checks(write_run(tmp_path, cmds, keys, faults=[fault(SPACING, 2 * SPACING)]))
    assert res["Faults"].verdict == "PASS"


def test_late_fault_report_fails(tmp_path):
    cmds, keys = perfect(3)
    res = checks(write_run(tmp_path, cmds, keys, faults=[fault(SPACING, 2 * SPACING + 1)]))
    assert res["Faults"].verdict == "FAIL"
    assert "1000.000 ms" in text(res["Faults"])


def test_silent_loss_after_fault_fails_faults(tmp_path):
    cmds, keys = perfect(4)
    keys = [e for e in keys if e["qpc_ns"] < 2 * SPACING or e["qpc_ns"] > 3 * SPACING]  # command 3 lost
    res = checks(write_run(tmp_path, cmds, keys, faults=[fault(SPACING, SPACING + 10 * MS)]))
    assert res["Faults"].verdict == "FAIL"
    assert "silent loss after fault: seq 3" in text(res["Faults"])


def test_null_fault_entry_fails_with_kind_and_stuck_na(tmp_path):
    cmds, keys = perfect(3)
    res = checks(write_run(tmp_path, cmds, keys,
                           faults=[fault(None, None, kind="bluetooth_off_on"), fault(SPACING, SPACING + 10 * MS)]))
    assert res["Faults"].verdict == "FAIL"
    assert "bluetooth_off_on" in text(res["Faults"])
    stuck = text(res["Stuck keys"])
    assert "STUCK n/a" in stuck and "STUCK 0" in stuck and "total 0" in stuck


def test_stuck_right_after_fault(tmp_path):
    cmds, keys = perfect(3)
    keys.append(key(SPACING + 300 * MS, VK[Key.RIGHT], True))          # never released
    res = checks(write_run(tmp_path, cmds, keys, faults=[fault(SPACING, SPACING + 500 * MS)]))
    assert "STUCK 1 RIGHT" in text(res["Stuck keys"])
    assert "total 1" in text(res["Stuck keys"])
    assert res["Stuck keys"].verdict == "INFO"


def test_all_keys_up_after_fault_is_stuck_0(tmp_path):
    cmds, keys = perfect(3)
    res = checks(write_run(tmp_path, cmds, keys, faults=[fault(SPACING, SPACING + 500 * MS)]))
    assert "STUCK 0" in text(res["Stuck keys"])


def test_stuck_uses_stimulus_of_next_command_even_when_it_failed(tmp_path):
    cmds = [ok_cmd(1), ok_cmd(2), failed_cmd(3), ok_cmd(4)]
    keys = events_for(cmds[0]) + events_for(cmds[1]) + events_for(cmds[3])
    keys += [key(SPACING + 900 * MS, VK[Key.RIGHT], True), key(2 * SPACING + 50 * MS, VK[Key.RIGHT], False)]
    res = checks(write_run(tmp_path, cmds, keys, faults=[fault(SPACING + 400 * MS, SPACING + 500 * MS)]))
    assert "STUCK 1 RIGHT" in text(res["Stuck keys"])     # replay stops at stimulus(3) = 2 s, Right still down


def test_stuck_replays_to_end_when_only_focus_lost_follows(tmp_path):
    cmds, keys = perfect(2)
    cmds.append(focus_lost_row(3))
    keys += [key(3 * SPACING, VK[Key.LEFT], True)]       # pressed after the last command, never released
    res = checks(write_run(tmp_path, cmds, keys, faults=[fault(SPACING, SPACING + 500 * MS)]))
    assert "STUCK 1 LEFT" in text(res["Stuck keys"])
    assert "over 2 commands" in text(res["Latency"])


def test_no_faults_means_faults_and_stuck_na(tmp_path):
    cmds, keys = perfect(2)
    res = checks(write_run(tmp_path, cmds, keys))
    assert res["Faults"].verdict == "n/a" and res["Stuck keys"].verdict == "n/a"


# ---- Connection ----------------------------------------------------------------------

def starts_rows(n_plain=8, n_reset=6, n_bt=6) -> list[dict]:
    conds = ["plain"] * n_plain + ["reset_board"] * n_reset + ["bluetooth_off_on"] * n_bt
    return [{"attempt": i + 1, "condition": c, "result": "Success", "error": None, "seconds": 1.0 + i / 10,
             "start_ns": i * S, "end_ns": i * S + S} for i, c in enumerate(conds)]


def test_starts_20_success_passes(tmp_path):
    p = tmp_path / "session_starts.jsonl"
    write_jsonl(p, starts_rows())
    c = analyze.check_connection_starts(p)
    assert c.verdict == "PASS"
    assert "plain 8" in text(c) and "reset_board 6" in text(c) and "bluetooth_off_on 6" in text(c)
    assert "seconds p50 1.900 max 2.900" in text(c)


def test_starts_with_one_failure_fails_and_prints_it(tmp_path):
    rows = starts_rows()
    rows[10].update(result="Failure", error="connect timed out")
    p = tmp_path / "session_starts.jsonl"
    write_jsonl(p, rows)
    c = analyze.check_connection_starts(p)
    assert c.verdict == "FAIL"
    assert "attempt 11 reset_board connect timed out" in text(c)


def test_starts_19_rows_fails(tmp_path):
    p = tmp_path / "session_starts.jsonl"
    write_jsonl(p, starts_rows(n_plain=7))
    assert analyze.check_connection_starts(p).verdict == "FAIL"


def test_no_starts_is_na(tmp_path):
    cmds, keys = perfect(2)
    c = checks(write_run(tmp_path, cmds, keys))["Connection starts"]
    assert c.verdict == "n/a" and "Connection starts: n/a" in text(c)


def span_run(tmp_path, hours: float, *, link_faults=(), faults=None) -> Path:
    cmds = [ok_cmd(1, stim=0), ok_cmd(2, stim=int(hours * H) - 2 * S)]
    cmds[1]["done_ns"] = int(hours * H)
    keys = [e for c in cmds for e in events_for(c)]
    return write_run(tmp_path, cmds, keys, link_faults=link_faults, faults=faults)


def test_drops_none_over_2_1h_passes(tmp_path):
    c = checks(span_run(tmp_path, 2.1))["Connection drops"]
    assert c.verdict == "PASS" and "2.10 h" in text(c)


def test_one_link_lost_fails_and_is_printed(tmp_path):
    c = checks(span_run(tmp_path, 2.1, link_faults=[{"qpc_ns": 123456789, "error": "LINK_LOST"}]))["Connection drops"]
    assert c.verdict == "FAIL" and "LINK_LOST qpc_ns 123456789" in text(c)


def test_only_ack_timeout_is_zero_drops(tmp_path):
    c = checks(span_run(tmp_path, 2.1, link_faults=[{"qpc_ns": 5, "error": "ACK_TIMEOUT"}]))["Connection drops"]
    assert c.verdict == "PASS" and "drops 0" in text(c)


def test_zero_drops_over_1_9h_fails(tmp_path):
    c = checks(span_run(tmp_path, 1.9))["Connection drops"]
    assert c.verdict == "FAIL" and "1.90 h" in text(c)


def test_drops_in_fault_soak_are_reported_not_gated(tmp_path):
    lf = [{"qpc_ns": 10, "error": "LINK_LOST"}, {"qpc_ns": 20, "error": "LINK_LOST"}]
    c = checks(span_run(tmp_path, 0.5, link_faults=lf, faults=[fault(5, 15)]))["Connection drops"]
    assert c.verdict == "INFO" and "drops 2" in text(c)


def test_missing_link_faults_fails(tmp_path):
    c = checks(span_run(tmp_path, 2.1, link_faults=None))["Connection drops"]
    assert c.verdict == "FAIL"


def test_span_excludes_focus_lost_row(tmp_path):
    cmds = [ok_cmd(1, stim=0), ok_cmd(2, stim=int(2.1 * H) - 2 * S), focus_lost_row(3)]
    cmds[1]["done_ns"] = int(2.1 * H)
    run = write_run(tmp_path, cmds, [e for c in cmds[:2] for e in events_for(c)])
    assert checks(run)["Connection drops"].verdict == "PASS"


# ---- CLI -----------------------------------------------------------------------------

def test_cli_exit_0_on_clean_run_and_prints_checks(tmp_path, capsys):
    cmds, keys = perfect(3, spacing=int(1.1 * H))
    run = write_run(tmp_path, cmds, keys)
    p = tmp_path / "session_starts.jsonl"
    write_jsonl(p, starts_rows())
    assert analyze.main([str(run), "--starts", str(p)]) == 0
    out = capsys.readouterr().out
    for line in ("Listener liveness: PASS", "Delivery: PASS", "Latency: PASS", "Connection starts: PASS",
                 "Connection drops: PASS"):
        assert line in out


def test_cli_exit_nonzero_on_any_fail(tmp_path, capsys):
    cmds, keys = perfect(3, spacing=int(1.1 * H))
    keys.pop(0)
    assert analyze.main([str(write_run(tmp_path, cmds, keys))]) != 0
    assert "Delivery: FAIL" in capsys.readouterr().out


def test_cli_prints_targets_verbatim_through_a_cp1252_stdout(tmp_path, monkeypatch):
    buf = io.BytesIO()
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(buf, encoding="cp1252"))
    cmds, keys = perfect(2)
    analyze.main([str(write_run(tmp_path, cmds, keys))])
    sys.stdout.flush()
    assert "≥10,000 commands" in buf.getvalue().decode("utf-8")


@pytest.mark.parametrize("verdict", ["INFO", "n/a"])
def test_non_gating_verdicts_do_not_fail(verdict):
    assert not analyze.Check("x", verdict, "s").failed


# ---- H3 review minors m1-m5 (A-H34-fix) ---------------------------------------------

def test_m1_failed_command_inside_a_dead_stretch_does_not_make_earlier_silent_commands_drops(tmp_path):
    cmds, _ = perfect(10)
    cmds[4] = failed_cmd(5)                              # a Failure among the silent OK commands
    keys = [e for c in cmds[:2] for e in events_for(c)]  # listener dies after seq 2
    res = checks(write_run(tmp_path, cmds, keys))
    assert f"LISTENER_DEAD from seq 3 (last listener event qpc_ns {keys[-1]['qpc_ns']})" in text(res["Listener liveness"])
    assert "dropped 0 extra 0 out-of-order 0" in text(res["Delivery"])
    assert "unmeasured 8" in text(res["Delivery"])


def test_m1_a_failure_still_breaks_the_run_of_five(tmp_path):
    cmds, _ = perfect(10)
    cmds[3] = failed_cmd(4)
    cmds[8] = failed_cmd(9)                              # OK runs in the silent tail: seq 3, seq 5-8 (4), seq 10: never 5 in a row
    keys = [e for c in cmds[:2] for e in events_for(c)]
    res = checks(write_run(tmp_path, cmds, keys))
    assert res["Listener liveness"].verdict == "PASS"


def fault_lines(c: analyze.Check) -> list[str]:
    return [d for d in c.details if d.startswith("fault ")]


def test_m2_silent_loss_after_the_later_fault_does_not_fail_the_earlier_fault(tmp_path):
    cmds, keys = perfect(7)
    keys = [e for e in keys if not 5 * S <= e["qpc_ns"] < 6 * S]          # command 6 lost
    faults = [fault(1 * S, 1 * S + 10 * MS), fault(4 * S, 4 * S + 10 * MS)]
    res = checks(write_run(tmp_path, cmds, keys, faults=faults))["Faults"]
    assert res.verdict == "FAIL"
    f1, f2 = fault_lines(res)
    assert ": PASS" in f1 and ": FAIL" in f2
    assert text(res).count("silent loss after fault: seq 6") == 1
    assert "1/2 faults pass" in res.summary


def test_m2_silent_loss_between_two_faults_fails_only_the_earlier_one(tmp_path):
    cmds, keys = perfect(7)
    keys = [e for e in keys if not 2 * S <= e["qpc_ns"] < 3 * S]          # command 3 lost (done 2.6 s)
    faults = [fault(1 * S, 1 * S + 10 * MS), fault(4 * S, 4 * S + 10 * MS)]
    res = checks(write_run(tmp_path, cmds, keys, faults=faults))["Faults"]
    f1, f2 = fault_lines(res)
    assert ": FAIL" in f1 and ": PASS" in f2
    assert "1/2 faults pass" in res.summary


def test_silent_loss_after_a_fault_with_null_reported_ns_is_printed(tmp_path):
    """A-H4-R fyi: the null fault FAILs on its own, but its silent loss must still be listed (under that fault)."""
    cmds, keys = perfect(7)
    keys = [e for e in keys if not 5 * S <= e["qpc_ns"] < 6 * S]          # command 6 lost (done 5.6 s)
    faults = [fault(1 * S, 1 * S + 10 * MS), fault(4 * S, None)]
    res = checks(write_run(tmp_path, cmds, keys, faults=faults))["Faults"]
    assert res.verdict == "FAIL"
    f1, f2 = fault_lines(res)
    assert ": PASS" in f1 and ": FAIL" in f2
    assert text(res).count("silent loss after fault: seq 6") == 1
    assert "1/2 faults pass" in res.summary


def test_fault_with_null_injected_ns_and_a_lost_command_fails_without_a_silent_loss_line(tmp_path):
    """A-H34-fix2-R finding 2: the null-injected_ns guard in check_faults (no fault moment, nothing to blame)."""
    cmds, keys = perfect(7)
    keys = [e for e in keys if not 5 * S <= e["qpc_ns"] < 6 * S]          # command 6 lost
    res = checks(write_run(tmp_path, cmds, keys, faults=[fault(None, None)]))
    assert res["Faults"].verdict == "FAIL"
    assert "0/1 faults pass" in res["Faults"].summary
    assert "silent loss after fault" not in text(res["Faults"])
    assert res["Delivery"].verdict == "FAIL"


def test_m3_extra_events_are_printed_with_their_vk(tmp_path):
    cmds, keys = perfect(3)
    t = cmds[1]["sent_ns"] + 300 * MS
    keys += [key(t, 0x41, True), key(t + MS, 0x41, False),              # non-bot A inside seq 2's window
             key(50 * MS, 0x0D, True),                                  # non-bot Enter before the first window
             key(cmds[2]["sent_ns"] + 300 * MS, VK[Key.E], True)]      # a bot key
    res = text(checks(write_run(tmp_path, cmds, keys))["Delivery"])
    assert "0x41 down (not a bot key)" in res and "0x41 up (not a bot key)" in res
    assert "extra outside any command window: 1" in res and "0x0D down (not a bot key)" in res
    assert "E 0x45 down" in res and "E 0x45 down (not a bot key)" not in res
    assert "extra 4" in res                                              # totals unchanged: 2 + 1 outside + 1


def test_m4_stray_key_before_sent_ns_is_not_the_first_down(tmp_path):
    cmds, keys = perfect(3)
    keys.append(key(cmds[1]["sent_ns"] - 50 * MS, 0x41, True))           # inside seq 2's window, before it was sent
    dm = analyze.match_delivery(analyze.load_run(write_run(tmp_path, cmds, keys)))
    m = dm.commands[1]
    assert m.first_down_ns == cmds[1]["sent_ns"] + LATENCY
    assert all(x.first_down_ns - x.row["sent_ns"] >= 0 for x in dm.commands)
    assert analyze.m7_split(dm.commands)["total"] == (20.0, 20.0)


def test_m4_command_with_only_a_pre_send_key_down_has_no_first_down(tmp_path):
    cmds, keys = perfect(2)
    keys = [e for e in keys if e["qpc_ns"] < SPACING] + [key(cmds[1]["sent_ns"] - 50 * MS, 0x41, True)]
    dm = analyze.match_delivery(analyze.load_run(write_run(tmp_path, cmds, keys)))
    assert dm.commands[1].first_down_ns is None


SPEC = Path(__file__).resolve().parents[2] / "docs" / "superpowers" / "specs" / "2026-10-21-p1a-input-over-bluetooth-design.md"


@pytest.mark.parametrize("name", ["TARGET_DELIVERY", "TARGET_FAULTS", "TARGET_LATENCY"])
def test_m5_gated_targets_appear_verbatim_in_the_spec(name):
    assert f"| {getattr(analyze, name)} |" in SPEC.read_text(encoding="utf-8")   # the whole table cell


def test_m5_printed_checks_carry_the_trailing_spec_text(tmp_path):
    cmds, keys = perfect(2)
    res = checks(write_run(tmp_path, cmds, keys))
    assert "bounds the true error rate below 0.03% at 95% confidence)." in text(res["Delivery"])
    assert "Provisional: re-set once first measured." in text(res["Latency"])


# ---- reset_board worst case (A-LF, A-V2stamp Q3 C1): last PONG (proof of life) -> first report after esptool started ---

LIMIT_MS = 1000


def reset_run(tmp_path, *, gated_ms: float, worst_ms: float, pongs=True, launch_ns="default") -> tuple[Path, dict]:
    """One reset_board fault. esptool starts at 1 s; the board still answers for another 250 ms (the last PONG, as in
    v2-faults-3 where PONGs stop 476-582 ms after the launch); the first report (a LINK_LOST) comes `worst_ms` after
    that last PONG; esptool's reset line (the stamp) 2.5 s after the launch; the next command's NOT_CONNECTED
    `gated_ms` after the stamp. So the stamp is later than the first report, as in v2-faults-3."""
    launch = 1 * S
    pong = launch + 250 * MS
    first_report = pong + round(worst_ms * MS)
    injected = launch + 2500 * MS
    reported = injected + round(gated_ms * MS)
    row = {"injected_ns": injected, "kind": "reset_board", "reported_ns": reported, "error": "NOT_CONNECTED",
           "launch_ns": launch if launch_ns == "default" else launch_ns, "fault_seen_ns": None, "failure_ns": reported}
    cmds, keys = perfect(8)
    return write_run(tmp_path, cmds, keys, faults=[row], link_faults=[{"qpc_ns": first_report, "error": "LINK_LOST"}],
                     pongs=[launch - 5 * MS, pong] if pongs else None), row


@pytest.mark.parametrize("gated_ms, worst_ms, verdict", [
    (587.5, 966.7, "PASS"),        # v2-faults-3 fault 1
    (600, 1000, "PASS"),           # exactly the limit passes (<=)
    (600, 1000.001, "FAIL"),       # the gated figure is fine; the worst case is 1 us over
    (600, 1400, "FAIL"),
    (1000.001, 900, "FAIL"),       # the worst case is fine; the gated figure is 1 us over
])
def test_reset_fault_passes_only_if_the_gated_and_the_worst_case_figures_are_both_within_1s(
        tmp_path, gated_ms, worst_ms, verdict):
    res = checks(reset_run(tmp_path, gated_ms=gated_ms, worst_ms=worst_ms)[0])["Faults"]
    assert res.verdict == verdict
    (line,) = fault_lines(res)
    assert f": {verdict} reported after {gated_ms:.3f} ms" in line      # the existing gated figure, unchanged
    assert f"worst case {worst_ms:.3f} ms" in line                      # the new figure, next to it
    assert f"{1 if verdict == 'PASS' else 0}/1 faults pass" in res.summary


def test_the_worst_case_counts_a_report_that_came_before_the_stamp(tmp_path):
    # The gated figure (stamp -> next command's NOT_CONNECTED) is 0.6 s, so it passes; the link was reported lost
    # 1.4 s after the last PONG, before esptool's reset line. Only the worst case sees that.
    res = checks(reset_run(tmp_path, gated_ms=600, worst_ms=1400)[0])["Faults"]
    assert res.verdict == "FAIL"
    assert "reported after 600.000 ms" in text(res) and "worst case 1400.000 ms" in text(res)


@pytest.mark.parametrize("why, kw", [
    ("pongs.jsonl missing", {"pongs": False}),
    ("no launch_ns", {"launch_ns": None}),
])
def test_reset_fault_whose_worst_case_cannot_be_computed_fails_and_says_why(tmp_path, why, kw):
    res = checks(reset_run(tmp_path, gated_ms=600, worst_ms=900, **kw)[0])["Faults"]
    assert res.verdict == "FAIL"
    assert f"worst case n/a ({why})" in text(res)


def test_reset_fault_with_no_pong_before_the_first_report_fails(tmp_path):
    run, row = reset_run(tmp_path, gated_ms=600, worst_ms=900)
    first_report = row["launch_ns"] + 250 * MS + 900 * MS
    write_jsonl(run / "pongs.jsonl", [{"qpc_ns": first_report + 5 * MS, "board_us": 1}])   # only a later PONG
    res = checks(run)["Faults"]
    assert res.verdict == "FAIL" and "worst case n/a (no PONG before the first report)" in text(res)


def test_a_pong_after_esptool_started_still_counts_as_proof_of_life(tmp_path):
    # The board answers for ~250-580 ms after esptool starts, so "esptool started" is not the fault moment. Measuring
    # from the last PONG before the launch would give 1255 ms here and fail a reset the board survived until later.
    res = checks(reset_run(tmp_path, gated_ms=600, worst_ms=950)[0])["Faults"]
    assert res.verdict == "PASS" and "worst case 950.000 ms" in text(res)


def test_the_worst_case_falls_back_to_the_failure_when_no_link_fault_was_logged(tmp_path):
    run, row = reset_run(tmp_path, gated_ms=600, worst_ms=900)
    write_jsonl(run / "link_faults.jsonl", [])
    res = checks(run)["Faults"]
    # last PONG 1.25 s; first report = failure_ns = launch 1 s + 2.5 s + 0.6 s = 4.1 s -> 2850 ms
    assert res.verdict == "FAIL" and "worst case 2850.000 ms" in text(res)


def test_a_bluetooth_fault_has_no_worst_case_and_needs_no_pongs(tmp_path):
    cmds, keys = perfect(3)
    res = checks(write_run(tmp_path, cmds, keys, faults=[fault(SPACING, 2 * SPACING)]))["Faults"]
    assert res.verdict == "PASS" and "worst case" not in text(res)


def test_the_stamp_and_the_gated_figure_are_unchanged_by_the_worst_case(tmp_path):
    # Ruling (orch 21): the reset stamp stays at esptool's reset line; the worst case is an additional, stricter gate.
    run, row = reset_run(tmp_path, gated_ms=587.5, worst_ms=966.7)
    res = checks(run)["Faults"]
    assert f"reported after {(row['reported_ns'] - row['injected_ns']) / MS:.3f} ms (NOT_CONNECTED)" in text(res)
