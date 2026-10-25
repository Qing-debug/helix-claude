"""The PC's protocol constants match the firmware's (CW-15).

Reads firmware/Helix_BLE/protocol.h and Helix_BLE.ino as text (nothing is compiled or flashed), so a wire change fails
here on whichever side was forgotten. The golden vectors (firmware/test/vectors.json) pin the byte layouts; this pins the
named values: version, opcodes, notification kinds, DONE status and REJECTED reason codes, PONG flag bits, the size
limits, the characteristic UUIDs and the advertised device name.
"""
import ast
import inspect
import re
from pathlib import Path

import pytest

from src.input import link, protocol
from src.input.protocol import DoneStatus, RejectReason

FIRMWARE = Path(__file__).resolve().parents[2] / "firmware" / "Helix_BLE"
PROTOCOL_H = (FIRMWARE / "protocol.h").read_text(encoding="utf-8")
SKETCH = (FIRMWARE / "Helix_BLE.ino").read_text(encoding="utf-8")


def _c_int(expr: str, names: dict[str, int]) -> int:
    """Evaluate a C integer constant expression made of int literals, names in `names`, `+` and `*`."""
    def ev(node: ast.expr) -> int:
        if isinstance(node, ast.Constant) and type(node.value) is int:
            return node.value
        if isinstance(node, ast.Name):
            return names[node.id]
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            return ev(node.left) + ev(node.right)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
            return ev(node.left) * ev(node.right)
        raise ValueError(f"unsupported C expression {expr!r}")
    return ev(ast.parse(expr.strip(), mode="eval").body)


def _enum(name: str) -> dict[str, int]:
    """Members of `enum <name> : uint8_t { A = 1, B = 2 };` in protocol.h (the braces may span lines)."""
    m = re.search(rf"\benum\s+{name}\s*:\s*\w+\s*\{{(.*?)\}};", PROTOCOL_H, re.S)
    assert m, f"enum {name} not found in protocol.h"
    body = re.sub(r"//[^\n]*", "", m.group(1))
    members = {}
    for item in filter(None, (part.strip() for part in body.split(","))):
        key, value = (s.strip() for s in item.split("="))
        members[key] = _c_int(value, members)
    return members


def _constexprs() -> dict[str, int]:
    """Every `constexpr <type> A = <expr>, B = <expr>;` in protocol.h, in file order (later ones may use earlier ones)."""
    values: dict[str, int] = {}
    for decl in re.findall(r"\bconstexpr\s+\w+\s+([^;(]*);", PROTOCOL_H):
        for item in decl.split(","):
            key, value = (s.strip() for s in item.split("=", 1))
            values[key] = _c_int(value, values)
    return values


def _define_string(name: str) -> str:
    m = re.search(rf'^#define\s+{name}\s+"([^"]*)"', SKETCH, re.M)
    assert m, f"#define {name} not found in Helix_BLE.ino"
    return m.group(1)


CONST = _constexprs()


def test_the_parsers_find_what_they_check():
    assert {"VER", "MAX_STEPS", "MAX_CMD", "KIND_ACCEPTED", "KIND_DONE", "KIND_REJECTED", "KIND_PONG",
            "MAX_SUM_DELTA"} <= CONST.keys()
    for name in ("Op", "Reject", "PongFlag", "DoneStatus"):
        assert _enum(name)


def test_c_int_evaluates_the_forms_protocol_h_uses():
    assert _c_int("0x81", {}) == 0x81
    assert _c_int("5 + 3 * MAX_STEPS", {"MAX_STEPS": 16}) == 53
    with pytest.raises(ValueError):
        _c_int("1 << 3", {})


def test_version_opcodes_and_notification_kinds_match():
    assert protocol.VER == CONST["VER"]
    assert {"PLAY": protocol.OP_PLAY, "RELEASE_ALL": protocol.OP_RELEASE_ALL, "PING": protocol.OP_PING} == _enum("Op")
    assert (protocol.KIND_ACCEPTED, protocol.KIND_DONE, protocol.KIND_REJECTED, protocol.KIND_PONG) == (
        CONST["KIND_ACCEPTED"], CONST["KIND_DONE"], CONST["KIND_REJECTED"], CONST["KIND_PONG"])


def test_done_status_matches():
    assert {m.name: m.value for m in DoneStatus} == _enum("DoneStatus")


def test_reject_reason_matches():
    assert {m.name: m.value for m in RejectReason} == _enum("Reject")


def test_pong_flag_bits_decode_to_the_named_fields():
    fields = {"BONDED": "bonded", "ENCRYPTED": "encrypted", "AUTHENTICATED": "authenticated", "SUBSCRIBED": "subscribed"}
    flags = _enum("PongFlag")
    assert flags.keys() == fields.keys()
    for flag, bit in flags.items():
        raw = protocol._PONG.pack(protocol.VER, protocol.KIND_PONG, 7, 24, 0, 400, 247, bit, 0)
        pong = protocol.decode_notification(raw).unwrap()
        assert {f: getattr(pong, f) for f in fields.values()} == {f: f == fields[flag] for f in fields.values()}, flag


def test_size_limits_match():
    assert protocol.MAX_STEPS == CONST["MAX_STEPS"]
    assert protocol.play_len(protocol.MAX_STEPS) == CONST["MAX_CMD"] == 53
    assert protocol.MAX_TOTAL_DELTA == CONST["MAX_SUM_DELTA"]
    assert link.MIN_PONG_MTU == CONST["MAX_CMD"] + 3 == 56  # ATT write header


def test_play_len_is_the_encoded_length():
    for n in (2, 4, protocol.MAX_STEPS):  # a closed schedule toggles E, so it has an even number of steps
        steps = [protocol.Step(10, protocol.Key.E if i % 2 == 0 else protocol.Key(0)) for i in range(n)]
        assert len(protocol.encode_play(1, steps).unwrap()) == protocol.play_len(n), n


def test_characteristic_uuids_match():
    assert link.CMD_UUID == _define_string("KEYBOARDINPUTCHARACTERISTIC_UUID")
    assert link.NOTIFY_UUID == _define_string("PCRESPONSECHARACTERISTIC_UUID")


def test_device_name_matches():
    names = re.findall(r'\bsetName\(\s*"([^"]*)"\s*\)', SKETCH)
    assert names == [link.DEVICE_NAME]
    assert inspect.signature(link.BleLink).parameters["name"].default == link.DEVICE_NAME
