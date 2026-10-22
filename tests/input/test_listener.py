"""Tests for the H1 keystroke listener's pure parts (no hook, no Tk, no keystrokes)."""
import io
import json
import threading
import time

import pytest

from src.input.harness import listener as listener_mod
from src.input.harness.listener import (
    BOT_VKS,
    LLKHF_INJECTED,
    LLKHF_UP,
    Event,
    KeyLogWriter,
    Listener,
    RawKeyEvent,
    TransitionTracker,
    VK_BY_KEY,
    _STOP,
    parse_args,
    should_block,
)
from src.input.protocol import Key

VK_LEFT, VK_UP, VK_RIGHT, VK_DOWN = 0x25, 0x26, 0x27, 0x28
VK_SPACE, VK_E, VK_LMENU = 0x20, 0x45, 0xA4
VK_LCONTROL, VK_F10 = 0xA2, 0x79


# ---- C2 VK table -----------------------------------------------------------

def test_vk_table_matches_c2():
    # Values from C2, confirmed against learn.microsoft.com virtual-key-codes (2026-10-22).
    assert VK_BY_KEY == {
        Key.LEFT: VK_LEFT,
        Key.RIGHT: VK_RIGHT,
        Key.UP: VK_UP,
        Key.DOWN: VK_DOWN,
        Key.E: VK_E,
        Key.ALT: VK_LMENU,
        Key.SPACE: VK_SPACE,
    }
    assert BOT_VKS == frozenset({VK_LEFT, VK_RIGHT, VK_UP, VK_DOWN, VK_E, VK_LMENU, VK_SPACE})


# ---- TransitionTracker ---------------------------------------------------------

def test_down_then_up():
    t = TransitionTracker()
    assert t.feed(VK_LEFT, True) is Event.DOWN
    assert t.feed(VK_LEFT, False) is Event.UP


def test_auto_repeat_is_repeat_not_transition():
    t = TransitionTracker()
    assert t.feed(VK_LEFT, True) is Event.DOWN
    assert t.feed(VK_LEFT, True) is Event.REPEAT
    assert t.feed(VK_LEFT, True) is Event.REPEAT
    assert t.feed(VK_LEFT, False) is Event.UP
    # After the up, a new down is a transition again.
    assert t.feed(VK_LEFT, True) is Event.DOWN


def test_up_without_down_is_orphan():
    t = TransitionTracker()
    assert t.feed(VK_E, False) is Event.ORPHAN_UP
    # A second up after a real down/up pair is also an orphan.
    assert t.feed(VK_E, True) is Event.DOWN
    assert t.feed(VK_E, False) is Event.UP
    assert t.feed(VK_E, False) is Event.ORPHAN_UP


def test_multiple_keys_interleaved():
    t = TransitionTracker()
    seq = [
        ((VK_LEFT, True), Event.DOWN),
        ((VK_E, True), Event.DOWN),
        ((VK_LEFT, True), Event.REPEAT),  # LEFT still held, auto-repeat
        ((VK_E, False), Event.UP),
        ((VK_LEFT, True), Event.REPEAT),
        ((VK_E, True), Event.DOWN),       # E pressed again: a transition, LEFT unaffected
        ((VK_LMENU, False), Event.ORPHAN_UP),
        ((VK_LEFT, False), Event.UP),
        ((VK_E, False), Event.UP),
        ((VK_LEFT, False), Event.ORPHAN_UP),
    ]
    for (vk, is_down), expected in seq:
        assert t.feed(vk, is_down) is expected, (vk, is_down)


# ---- JSONL writer schema (C5 keys.jsonl) ---------------------------------------

def _lines(buf):
    return [json.loads(line) for line in buf.getvalue().splitlines()]


def test_writer_schema_and_values():
    buf = io.StringIO()
    w = KeyLogWriter(buf)
    w.write(RawKeyEvent(qpc_ns=1_000, vk=VK_LEFT, flags=0x01, tick_ms=500))           # down, extended
    w.write(RawKeyEvent(qpc_ns=2_000, vk=VK_LEFT, flags=0x01, tick_ms=516))           # repeat
    w.write(RawKeyEvent(qpc_ns=3_000, vk=VK_LEFT, flags=0x01 | LLKHF_UP, tick_ms=531))  # up
    w.write(RawKeyEvent(qpc_ns=4_000, vk=VK_E, flags=LLKHF_INJECTED, tick_ms=547))    # injected down
    w.write(RawKeyEvent(qpc_ns=5_000, vk=VK_SPACE, flags=LLKHF_UP, tick_ms=562))      # orphan up
    rows = _lines(buf)
    assert len(rows) == 5
    for row in rows:
        assert set(row) == {"qpc_ns", "vk", "down", "injected", "tick_ms", "repeat"}
        assert isinstance(row["qpc_ns"], int) and isinstance(row["vk"], int)
        assert isinstance(row["tick_ms"], int)
        for k in ("down", "injected", "repeat"):
            assert isinstance(row[k], bool)
    assert rows[0] == {"qpc_ns": 1000, "vk": VK_LEFT, "down": True, "injected": False,
                       "tick_ms": 500, "repeat": False}
    assert rows[1]["down"] is True and rows[1]["repeat"] is True
    assert rows[2]["down"] is False and rows[2]["repeat"] is False
    assert rows[3]["injected"] is True and rows[3]["down"] is True
    # An orphan up is logged as a plain up (C5 has no orphan field).
    assert rows[4] == {"qpc_ns": 5000, "vk": VK_SPACE, "down": False, "injected": False,
                       "tick_ms": 562, "repeat": False}


def test_writer_one_object_per_line():
    buf = io.StringIO()
    w = KeyLogWriter(buf)
    w.write(RawKeyEvent(qpc_ns=7, vk=VK_UP, flags=0, tick_ms=1))
    w.write(RawKeyEvent(qpc_ns=8, vk=VK_UP, flags=LLKHF_UP, tick_ms=2))
    text = buf.getvalue()
    assert text.endswith("\n") and text.count("\n") == 2


# ---- blocking default (PA-D7) ----------------------------------------------------

def test_parser_blocks_by_default():
    args = parse_args(["--out", "x"])
    assert args.block is True


def test_parser_no_block_flag():
    args = parse_args(["--out", "x", "--no-block"])
    assert args.block is False


def test_parser_out_and_seconds():
    args = parse_args(["--out", "data/listener-check", "--seconds", "20"])
    assert args.out == "data/listener-check"
    assert args.seconds == 20.0


@pytest.mark.parametrize("vk", [VK_LEFT, VK_RIGHT, VK_UP, VK_DOWN, VK_E, VK_LMENU, VK_SPACE])
def test_should_block_bot_vks(vk):
    assert should_block(vk, True) is True
    assert should_block(vk, False) is False


@pytest.mark.parametrize("vk", [VK_LCONTROL, VK_F10, 0x12, 0xA5, 0x41])
def test_should_not_block_other_vks(vk):
    # 0x12 VK_MENU and 0xA5 VK_RMENU are not the bot's Alt (C2 uses left Alt, VK_LMENU).
    assert should_block(vk, True) is False
    assert should_block(vk, False) is False


# ---- foreground functions with faked Win32 calls (A-PF I5) ----------------------

class FakeForeground:
    def __init__(self, foreground, set_result, moves_on_set=False):
        self.foreground = foreground
        self.set_result = set_result
        self.moves_on_set = moves_on_set
        self.set_calls = []

    def get_foreground_window(self):
        return self.foreground

    def set_foreground_window(self, hwnd):
        self.set_calls.append(hwnd)
        if self.moves_on_set:
            self.foreground = hwnd
        return self.set_result


SINK = 0x1234
OTHER = 0x9999


def _listener(fake):
    lst = Listener(out_dir="unused", block=True, foreground=fake)
    lst.sink_hwnd = SINK
    return lst


def test_sink_is_foreground_true_only_for_sink_hwnd():
    assert _listener(FakeForeground(SINK, True)).sink_is_foreground() is True
    assert _listener(FakeForeground(OTHER, True)).sink_is_foreground() is False
    assert _listener(FakeForeground(0, True)).sink_is_foreground() is False


def test_sink_is_foreground_false_before_sink_exists():
    lst = Listener(out_dir="unused", block=True, foreground=FakeForeground(0, True))
    assert lst.sink_hwnd is None
    assert lst.sink_is_foreground() is False
    assert lst.bring_sink_to_foreground() is False


@pytest.mark.parametrize("set_result", [True, False])
def test_bring_returns_recheck_when_focus_moves(set_result):
    fake = FakeForeground(OTHER, set_result, moves_on_set=True)
    assert _listener(fake).bring_sink_to_foreground() is True
    assert fake.set_calls == [SINK]


@pytest.mark.parametrize("set_result", [True, False])
def test_bring_returns_recheck_when_focus_stays(set_result):
    # SetForegroundWindow's return value is not trusted: GetForegroundWindow decides.
    fake = FakeForeground(OTHER, set_result, moves_on_set=False)
    assert _listener(fake).bring_sink_to_foreground() is False
    assert fake.set_calls == [SINK]


# ---- writer thread (A-H1-R m8, m5): no hook, no Tk --------------------------------

def _run_writer(lst):
    th = threading.Thread(target=lst._writer_thread, daemon=True)
    th.start()
    return th


def test_writer_thread_appends_drains_before_stop_and_uses_lf(tmp_path):
    keys = tmp_path / "keys.jsonl"
    keys.write_bytes(b'{"old": 1}\n')  # append mode: an existing file is kept
    lst = Listener(out_dir=tmp_path, block=True, foreground=FakeForeground(0, True))
    events = [RawKeyEvent(10 * (i + 1), VK_LEFT, 0 if i % 2 == 0 else LLKHF_UP, i) for i in range(6)]
    for ev in events:
        lst._queue.put(ev)
    lst._queue.put(_STOP)
    th = _run_writer(lst)  # queue is already full: every event is ahead of _STOP
    th.join(5)
    assert not th.is_alive()
    raw = keys.read_bytes()
    assert b"\r" not in raw  # newline="\n": LF on Windows too
    lines = raw.split(b"\n")
    assert lines[0] == b'{"old": 1}' and lines[-1] == b""
    rows = [json.loads(line) for line in lines[1:-1]]
    assert [r["qpc_ns"] for r in rows] == [ev.qpc_ns for ev in events]


def test_writer_thread_flushes_when_queue_goes_idle(tmp_path):
    lst = Listener(out_dir=tmp_path, block=True, foreground=FakeForeground(0, True))
    th = _run_writer(lst)
    keys = tmp_path / "keys.jsonl"
    try:
        lst._queue.put(RawKeyEvent(1, VK_E, 0, 1))
        deadline = time.monotonic() + 5
        data = b""
        while time.monotonic() < deadline and not data.endswith(b"\n"):
            time.sleep(0.01)
            data = keys.read_bytes() if keys.exists() else b""
        assert data.endswith(b"\n")  # visible on disk before the writer is told to stop
    finally:
        lst._queue.put(_STOP)
        th.join(5)
    assert not th.is_alive()


# ---- start-up failures reach start() (A-H1-R m3, m4): fake hook thread, no Win32, no Tk window ---------

def _fast_startup(monkeypatch):
    monkeypatch.setattr(listener_mod, "_STARTUP_TIMEOUT_S", 1.0)  # a red run must not sit for 5 s per wait


def _fake_hook_ok(lst):
    def hook():
        lst._hook_ready.set()
    lst._hook_thread = hook  # shadows the bound method: no SetWindowsHookExW


def _fake_sink_ok(lst):
    def sink():
        lst.sink_hwnd = SINK
        lst._sink_ready.set()
    lst._sink_thread = sink  # shadows the bound method: no Tk


def test_start_raises_oserror_when_the_keys_file_cannot_be_opened(tmp_path, monkeypatch):
    _fast_startup(monkeypatch)
    (tmp_path / "keys.jsonl").mkdir()  # open(..., "a") on a directory fails
    lst = Listener(out_dir=tmp_path, block=True, foreground=FakeForeground(0, True))
    _fake_hook_ok(lst)
    _fake_sink_ok(lst)
    with pytest.raises(OSError, match="keys.jsonl"):
        lst._start()


def test_start_raises_oserror_carrying_the_sink_threads_error(tmp_path, monkeypatch):
    _fast_startup(monkeypatch)

    def no_display(*args, **kwargs):
        raise RuntimeError("no display for the sink")

    monkeypatch.setattr("tkinter.Tk", no_display)
    lst = Listener(out_dir=tmp_path, block=True, foreground=FakeForeground(0, True))
    _fake_hook_ok(lst)
    with pytest.raises(OSError, match="no display for the sink"):
        lst._start()
