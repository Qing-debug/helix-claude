"""P1: host-side wire protocol v1, driven by the firmware's golden vectors.

firmware/test/vectors.json is the single source shared with the C++ host test (plan C1, F4). Shape (A-F4-R-D1):
a top-level JSON array; a valid PLAY lists `expect.steps`; the schedule-rule rejects and the pc_only entry carry
`steps` at top level.
"""
import json
import struct
from pathlib import Path

import pytest
from returns.pipeline import is_successful

from src.input.protocol import (
    Accepted,
    Done,
    Key,
    Pong,
    Rejected,
    Step,
    decode_notification,
    encode_ping,
    encode_play,
    encode_release_all,
    transitions,
    validate_schedule,
)

VECTORS_PATH = Path(__file__).resolve().parents[2] / "firmware" / "test" / "vectors.json"
VECTORS = json.loads(VECTORS_PATH.read_text(encoding="utf-8"))

# Substring each schedule-rule reject's Failure message must contain. The check order is the one in
# firmware protocol.h:40-45 (A-F4-R choice 2): step count, then per step (bit 7, per-step delta, exactly one key),
# then last mask, then total delta. A vector that breaks several rules names the first one in that order.
SCHEDULE_REJECT_RULE = {
    "reject_n_0": "step count",
    "reject_n_17": "step count",  # also breaks "last mask": step count comes first
    "reject_bit7_set": "bit 7",  # also changes no key among bits 0-6: bit 7 comes first
    "reject_two_keys_one_step": "exactly one key",
    "reject_step_changes_no_key": "exactly one key",
    "reject_last_mask_not_0": "last mask",
    "reject_sum_delta_over_100000": "total delta",
}

WIRE_ONLY_SKIP_REASON = (
    "wire-only reject (bad version, bad length or unknown op): only the board parses commands; "
    "the PC encoders never produce such a frame, so there is no PC-side function to run"
)


def classify(v):
    if v.get("pc_only"):
        return "pc_only"
    expect = v["expect"]
    if "kind" in expect:
        return "notification"
    if "op" in expect:
        return {1: "play", 2: "release_all", 3: "ping"}[expect["op"]]
    if "reject" in expect:
        return "schedule_reject" if "steps" in v else "wire_reject"
    raise AssertionError(f"unclassifiable vector {v['name']}")


def to_steps(pairs):
    return [Step(d, Key(m)) for d, m in pairs]


def vector_params():
    params = []
    for v in VECTORS:
        marks = [pytest.mark.skip(reason=WIRE_ONLY_SKIP_REASON)] if classify(v) == "wire_reject" else []
        params.append(pytest.param(v, id=v["name"], marks=marks))
    return params


def vectors_of(kind):
    return [pytest.param(v, id=v["name"]) for v in VECTORS if classify(v) == kind]


def failure_msg(result):
    assert not is_successful(result), f"expected Failure, got {result}"
    return result.failure()


def expected_notification(v):
    e = v["expect"]
    kind, seq = e["kind"], e["seq"]
    if kind == 0x81:
        return Accepted(seq=seq, t0_us=e["t0_us"])
    if kind == 0x82:
        assert e["n"] == len(e["late_us"])
        return Done(seq=seq, status=e["status"], fail_step=None if e["fail_step"] == 255 else e["fail_step"],
                    rc=e["rc"], t0_us=e["t0_us"], late_us=tuple(e["late_us"]))
    if kind == 0x83:
        return Rejected(seq=seq, reason=e["reason"])
    if kind == 0x84:
        f = e["flags"]
        return Pong(seq=seq, itvl_units=e["itvl"], latency=e["latency"], timeout_units=e["timeout"], mtu=e["mtu"],
                    bonded=bool(f & 1), encrypted=bool(f & 2), authenticated=bool(f & 4), subscribed=bool(f & 8),
                    board_us=e["board_us"])
    raise AssertionError(f"unknown kind {kind}")


# --- every vector, both entry shapes --------------------------------------------------------------------------

def test_every_vector_is_classified():
    assert len(VECTORS) == 39
    kinds = {classify(v) for v in VECTORS}
    assert kinds == {"play", "release_all", "ping", "schedule_reject", "wire_reject", "pc_only", "notification"}


@pytest.mark.parametrize("v", vector_params())
def test_vector(v):
    kind = classify(v)
    if kind == "play":
        got = encode_play(v["expect"]["seq"], to_steps(v["expect"]["steps"]))
        assert is_successful(got), got
        assert got.unwrap() == bytes.fromhex(v["hex"])
    elif kind == "release_all":
        assert encode_release_all(v["expect"]["seq"]) == bytes.fromhex(v["hex"])
    elif kind == "ping":
        assert encode_ping(v["expect"]["seq"]) == bytes.fromhex(v["hex"])
    elif kind == "notification":
        got = decode_notification(bytes.fromhex(v["hex"]))
        assert is_successful(got), got
        assert got.unwrap() == expected_notification(v)
    elif kind == "schedule_reject":
        steps = to_steps(v["steps"])
        rule = SCHEDULE_REJECT_RULE[v["name"]]
        assert rule in failure_msg(validate_schedule(steps))
        assert rule in failure_msg(encode_play(1, steps))
    elif kind == "pc_only":
        steps = to_steps(v["steps"])
        rule = v["expect"]["error"]
        assert rule in failure_msg(validate_schedule(steps))
        assert rule in failure_msg(encode_play(1, steps))
    else:
        pytest.fail(f"no PC-side check for {kind}")


def test_every_schedule_reject_has_a_rule_name():
    names = {v["name"] for v in VECTORS if classify(v) == "schedule_reject"}
    assert names == set(SCHEDULE_REJECT_RULE)


@pytest.mark.parametrize("v", vectors_of("schedule_reject"))
def test_reject_vector_steps_match_its_frame(v):
    # M2 guard (A-F4-R): the C++ test never looks at a reject vector's top-level "steps", so check here that they
    # equal the frame body the board rejects: n byte == len(steps) and the packed pairs == the step bytes.
    frame = bytes.fromhex(v["hex"])
    assert frame[4] == len(v["steps"])
    assert b"".join(struct.pack("<HB", d, m) for d, m in v["steps"]) == frame[5:]


def test_valid_play_vectors_are_valid_schedules():
    for v in VECTORS:
        if classify(v) == "play":
            assert is_successful(validate_schedule(to_steps(v["expect"]["steps"]))), v["name"]


# --- encoders: edge behaviour ---------------------------------------------------------------------------------

def test_per_step_delta_cap_never_raises_struct_error():
    walk_ok = [Step(0, Key.RIGHT), Step(65535, Key(0))]
    assert is_successful(encode_play(1, walk_ok))
    for bad_delta in (65536, 1 << 40, -1):
        steps = [Step(0, Key.RIGHT), Step(bad_delta, Key(0))]
        assert "per-step delta" in failure_msg(validate_schedule(steps))
        assert "per-step delta" in failure_msg(encode_play(1, steps))


def test_out_of_range_mask_bits_never_raise_struct_error():
    for bad_mask in (128, 256, 0x1FF):
        steps = [Step(0, Key(bad_mask)), Step(100, Key(0))]
        assert "bit 7" in failure_msg(validate_schedule(steps))
        assert "bit 7" in failure_msg(encode_play(1, steps))


def test_total_delta_boundary():
    at_limit = [Step(34465, Key.E), Step(65535, Key(0))]  # 100 000
    over = [Step(34466, Key.E), Step(65535, Key(0))]  # 100 001
    assert is_successful(validate_schedule(at_limit))
    assert "total delta" in failure_msg(validate_schedule(over))


def test_seq_wraps_to_u16():
    assert encode_ping(0x10001) == encode_ping(1)
    assert encode_release_all(65536) == encode_release_all(0)
    steps = [Step(0, Key.E), Step(800, Key(0))]
    assert encode_play(65537, steps).unwrap() == encode_play(1, steps).unwrap()


# --- decoder --------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("v", vectors_of("notification"))
def test_notification_truncated_overlong_or_wrong_version_is_failure(v):
    frame = bytes.fromhex(v["hex"])
    for cut in range(len(frame)):
        assert not is_successful(decode_notification(frame[:cut])), f"truncated to {cut} bytes decoded"
    assert not is_successful(decode_notification(frame + b"\x00")), "trailing byte decoded"
    assert not is_successful(decode_notification(bytes([2]) + frame[1:])), "wrong version decoded"
    assert not is_successful(decode_notification(bytes([0]) + frame[1:])), "version 0 decoded"


def test_unknown_notification_kind_is_failure():
    assert not is_successful(decode_notification(bytes([1, 0x85, 0, 0, 0])))
    assert not is_successful(decode_notification(bytes([1, 0x01, 0, 0, 0])))  # a command op is not a notification kind


def test_done_with_declared_n_longer_than_data_is_failure():
    frame = bytearray.fromhex("0182010000ff000078563412020c005901")  # n = 2, 13 + 2*2 bytes
    frame[12] = 3
    assert not is_successful(decode_notification(bytes(frame)))


def test_rejected_reason_6_decodes():
    got = decode_notification(bytes.fromhex("01830d0006")).unwrap()
    assert got == Rejected(seq=13, reason=6)


def test_pong_flags_subscribed_bit():
    sub = decode_notification(bytes.fromhex("01841400060000002c01f7000fefcdab89")).unwrap()  # flags 0x0F
    assert (sub.subscribed, sub.bonded, sub.encrypted, sub.authenticated) == (True, True, True, True)
    not_sub = decode_notification(bytes.fromhex("018415000c000400900105020701000000")).unwrap()  # flags 0x07
    assert (not_sub.subscribed, not_sub.bonded, not_sub.encrypted, not_sub.authenticated) == (False, True, True, True)


def test_pong_reserved_flag_bits_are_ignored():
    frame = bytearray.fromhex("01841400060000002c01f7000fefcdab89")
    frame[12] = 0xF0  # only reserved bits 4-7 set
    got = decode_notification(bytes(frame)).unwrap()
    assert (got.bonded, got.encrypted, got.authenticated, got.subscribed) == (False, False, False, False)


# --- transitions ----------------------------------------------------------------------------------------------

def test_transitions_of_teleport_vector():
    v = next(v for v in VECTORS if v["name"] == "play_teleport_left")
    steps = to_steps(v["expect"]["steps"])
    c, h, lag = 300, 800, 200  # chord offset, hold, release lag (100 us units); step 0 delta is 0
    assert [s.delta_100us for s in steps] == [0, c, h, lag]
    assert transitions(steps) == [(0, Key.LEFT, True), (c, Key.E, True), (c + h, Key.E, False),
                                  (c + h + lag, Key.LEFT, False)]


def test_transitions_tap():
    steps = [Step(0, Key.E), Step(800, Key(0))]
    assert transitions(steps) == [(0, Key.E, True), (800, Key.E, False)]
