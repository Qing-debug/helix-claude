"""Wire protocol v1, PC side (plan C1 / C3). Pure: no I/O. Mirrors firmware/Helix_BLE/protocol.h.

All multi-byte fields are little-endian. Shared golden vectors: firmware/test/vectors.json.
"""
import struct
from collections.abc import Sequence
from dataclasses import dataclass
from enum import IntFlag

from returns.pipeline import is_successful
from returns.result import Failure, Result, Success

VER = 1
OP_PLAY, OP_RELEASE_ALL, OP_PING = 0x01, 0x02, 0x03
KIND_ACCEPTED, KIND_DONE, KIND_REJECTED, KIND_PONG = 0x81, 0x82, 0x83, 0x84

MAX_STEPS = 16
MAX_STEP_DELTA = 65_535  # u16 field; PC-side per-step cap (PA-adj1-D2)
MAX_TOTAL_DELTA = 100_000  # 10 s in 100 us units
_FAIL_STEP_NONE = 0xFF

_HEADER = struct.Struct("<BBH")  # ver, op/kind, seq
_STEP = struct.Struct("<HB")  # delta_100us, mask
_ACCEPTED = struct.Struct("<BBHI")
_DONE_HEAD = struct.Struct("<BBHBBhIB")  # ... status, fail_step, rc, t0_us, n
_REJECTED = struct.Struct("<BBHB")
_PONG = struct.Struct("<BBHHHHHBI")


class Key(IntFlag):
    LEFT = 1
    RIGHT = 2
    UP = 4
    DOWN = 8
    E = 16
    ALT = 32
    SPACE = 64


@dataclass(frozen=True)
class Step:
    delta_100us: int
    mask: Key


@dataclass(frozen=True)
class Accepted:
    seq: int
    t0_us: int


@dataclass(frozen=True)
class Done:
    seq: int
    status: int
    fail_step: int | None
    rc: int
    t0_us: int
    late_us: tuple[int, ...]


@dataclass(frozen=True)
class Rejected:
    seq: int
    reason: int


@dataclass(frozen=True)
class Pong:
    seq: int
    itvl_units: int
    latency: int
    timeout_units: int
    mtu: int
    bonded: bool
    encrypted: bool
    authenticated: bool
    subscribed: bool  # flags bit3
    board_us: int


def validate_schedule(steps: Sequence[Step]) -> Result[None, str]:
    """Return the first failure, in the order proto::parse checks (protocol.h:40-45):
    (1) step count; (2) per step, in order: bit 7, per-step delta, exactly one key changed; (3) last mask; (4) total delta."""
    n = len(steps)
    if not 1 <= n <= MAX_STEPS:
        return Failure(f"step count {n} outside 1..{MAX_STEPS}")
    prev = 0
    total = 0
    for i, s in enumerate(steps):
        mask = int(s.mask)
        if mask & ~0x7F:
            return Failure(f"step {i}: mask bit 7 (and above) must be 0, got {mask:#x}")
        if not 0 <= s.delta_100us <= MAX_STEP_DELTA:
            return Failure(f"step {i}: per-step delta {s.delta_100us} outside 0..{MAX_STEP_DELTA}")
        changed = prev ^ mask
        if changed == 0 or changed & (changed - 1):
            return Failure(f"step {i}: must change exactly one key, changed mask {changed:#x}")
        prev = mask
        total += s.delta_100us
    if prev != 0:
        return Failure(f"last mask must be 0 (closed schedule), got {prev:#x}")
    if total > MAX_TOTAL_DELTA:
        return Failure(f"total delta {total} exceeds {MAX_TOTAL_DELTA} (10 s)")
    return Success(None)


def encode_play(seq: int, steps: Sequence[Step]) -> Result[bytes, str]:
    checked = validate_schedule(steps)
    if not is_successful(checked):
        return Failure(checked.failure())
    body = b"".join(_STEP.pack(s.delta_100us, int(s.mask)) for s in steps)
    return Success(_HEADER.pack(VER, OP_PLAY, seq & 0xFFFF) + bytes([len(steps)]) + body)


def encode_release_all(seq: int) -> bytes:
    return _HEADER.pack(VER, OP_RELEASE_ALL, seq & 0xFFFF)


def encode_ping(seq: int) -> bytes:
    return _HEADER.pack(VER, OP_PING, seq & 0xFFFF)


def decode_notification(data: bytes) -> Result[Accepted | Done | Rejected | Pong, str]:
    if len(data) < _HEADER.size:
        return Failure(f"notification too short: {len(data)} bytes")
    ver, kind, seq = _HEADER.unpack_from(data)
    if ver != VER:
        return Failure(f"unsupported notification version {ver}")
    if kind == KIND_ACCEPTED:
        if len(data) != _ACCEPTED.size:
            return Failure(f"ACCEPTED must be {_ACCEPTED.size} bytes, got {len(data)}")
        return Success(Accepted(seq, _ACCEPTED.unpack(data)[3]))
    if kind == KIND_DONE:
        if len(data) < _DONE_HEAD.size:
            return Failure(f"DONE too short: {len(data)} bytes")
        _, _, _, status, fail_step, rc, t0_us, n = _DONE_HEAD.unpack_from(data)
        if len(data) != _DONE_HEAD.size + 2 * n:
            return Failure(f"DONE with n={n} must be {_DONE_HEAD.size + 2 * n} bytes, got {len(data)}")
        late = struct.unpack_from(f"<{n}H", data, _DONE_HEAD.size)
        return Success(Done(seq, status, None if fail_step == _FAIL_STEP_NONE else fail_step, rc, t0_us, late))
    if kind == KIND_REJECTED:
        if len(data) != _REJECTED.size:
            return Failure(f"REJECTED must be {_REJECTED.size} bytes, got {len(data)}")
        return Success(Rejected(seq, _REJECTED.unpack(data)[3]))
    if kind == KIND_PONG:
        if len(data) != _PONG.size:
            return Failure(f"PONG must be {_PONG.size} bytes, got {len(data)}")
        _, _, _, itvl, latency, timeout, mtu, flags, board_us = _PONG.unpack(data)
        return Success(Pong(seq, itvl, latency, timeout, mtu, bool(flags & 1), bool(flags & 2), bool(flags & 4),
                            bool(flags & 8), board_us))
    return Failure(f"unknown notification kind {kind:#x}")


def transitions(steps: Sequence[Step]) -> list[tuple[int, Key, bool]]:
    """(t_100us_from_start, key, is_down) per key change, in order. Exactly one per step in a valid schedule."""
    out: list[tuple[int, Key, bool]] = []
    prev = Key(0)
    t = 0
    for s in steps:
        t += s.delta_100us
        for key in Key:
            if (prev ^ s.mask) & key:
                out.append((t, key, bool(s.mask & key)))
        prev = s.mask
    return out
