"""Tests for src/input/link.py (plan A task P4). The board is a scripted fake that runs on the link's loop thread."""
import asyncio
import gc
import logging
import struct
import subprocess
import sys
import threading
import time
from collections import namedtuple
from pathlib import Path

import pytest
from bleak.exc import BleakError, BleakGATTProtocolError
from returns.pipeline import is_successful

from src.input import link as L
from src.input.link import BleLink, InputError
from src.input.protocol import OP_PING, OP_PLAY, OP_RELEASE_ALL, Key, Step

REPO = Path(__file__).resolve().parents[2]
LOGGER = "src.input.link"

# ---------------------------------------------------------------- notification builders (C1 layouts)


def n_accepted(seq, t0=1000):
    return struct.pack("<BBHI", 1, 0x81, seq, t0)


def n_done(seq, status=0, n=1, fail_step=0xFF, rc=0, t0=1000):
    return struct.pack("<BBHBBhIB", 1, 0x82, seq, status, fail_step, rc, t0, n) + struct.pack(f"<{n}H", *[0] * n)


def n_rejected(seq, reason):
    return struct.pack("<BBHB", 1, 0x83, seq, reason)


def n_pong(seq, mtu=247, flags=0x0F):
    return struct.pack("<BBHHHHHBI", 1, 0x84, seq, 6, 0, 400, mtu, flags, 123)


def tap_steps(ms=10):
    return [Step(0, Key.E), Step(ms * 10, Key(0))]


def walk_steps(ms):
    return [Step(0, Key.RIGHT), Step(int(ms * 10), Key(0))]


# ---------------------------------------------------------------- fakes

Write = namedtuple("Write", "ns uuid op seq data response client")


class Device:
    address = "AA:BB:CC:DD:EE:FF"


class FakeScanner:
    def __init__(self, delay=0.0):
        self.delay = delay
        self.calls = []

    async def find_device_by_name(self, name, timeout=10.0):
        self.calls.append((name, timeout))
        await asyncio.sleep(min(self.delay, timeout))
        return Device()


class FakeClient:
    def __init__(self, board, device, disconnected_callback=None, **kwargs):
        self.board, self.device, self.disconnected_callback, self.kwargs = board, device, disconnected_callback, kwargs
        self.mtu_size = board.client_mtu
        self.cb = None
        self.notify_uuid = None
        self.connected = False
        self.disconnect_calls = 0
        self.gone = asyncio.Event()  # set when the link is torn down: ends the writes a dead_peer handler holds
        self.hold_past_disconnect = False  # True: disconnect() leaves them held (release_held() ends them)
        self.in_flight = None  # seq of the write a dead_peer handler took as the one in flight

    async def connect(self, **kwargs):
        await self.board.on_connect(self)
        self.connected = True

    async def start_notify(self, uuid, cb):
        self.notify_uuid, self.cb = uuid, cb

    async def write_gatt_char(self, uuid, data, response=None):
        data = bytes(data)
        seq = struct.unpack_from("<H", data, 2)[0]
        self.board.writes.append(Write(time.perf_counter_ns(), uuid, data[1], seq, data, response, self))
        handler = {OP_PING: self.board.ping, OP_PLAY: self.board.play, OP_RELEASE_ALL: self.board.release}[data[1]]
        await handler(self, seq, data)

    async def disconnect(self):
        self.disconnect_calls += 1
        self.connected = False
        if not self.hold_past_disconnect:
            self.gone.set()

    def notify(self, data):
        if self.cb is not None and self.connected:
            self.cb(None, bytearray(data))

    def drop(self):
        """Simulate the link going away: bleak calls disconnected_callback(client)."""
        self.connected = False
        self.disconnected_callback(self)


class FakeBoard:
    """Answers like F5: PONG inside onWrite; PLAY -> ACCEPTED, DONE after the schedule; RELEASE_ALL -> DONE 3 for
    every PLAY still pending, then its own DONE OK (n = 1)."""

    def __init__(self, client_mtu=247, pong_mtu=247, pong_flags=0x0F):
        self.client_mtu, self.pong_mtu, self.pong_flags = client_mtu, pong_mtu, pong_flags
        self.pong_script = []  # (flags, mtu) used before the defaults
        self.clients, self.writes, self.pong_seqs = [], [], []
        self.pending_plays = {}
        self.drop_abort_done = False
        self.ping, self.play, self.release = self.default_ping, self.default_play, self.default_release

    def factory(self, device, disconnected_callback=None, **kwargs):
        c = FakeClient(self, device, disconnected_callback, **kwargs)
        self.clients.append(c)
        return c

    @property
    def client(self):
        return self.clients[-1]

    def ops(self, op):
        return [w for w in self.writes if w.op == op]

    async def on_connect(self, client):
        pass

    def send_pong(self, client, seq):
        flags, mtu = self.pong_script.pop(0) if self.pong_script else (self.pong_flags, self.pong_mtu)
        self.pong_seqs.append(seq)
        client.notify(n_pong(seq, mtu, flags))

    async def default_ping(self, client, seq, data):
        self.send_pong(client, seq)

    async def default_play(self, client, seq, data):
        n = data[4]
        total = sum(struct.unpack_from("<H", data, 5 + 3 * i)[0] for i in range(n))
        loop = asyncio.get_running_loop()
        h1 = loop.call_later(0.005, client.notify, n_accepted(seq))
        h2 = loop.call_later(0.005 + total / 10_000, self._finish, client, seq, n)
        self.pending_plays[seq] = (h1, h2)

    def _finish(self, client, seq, n):
        self.pending_plays.pop(seq, None)
        client.notify(n_done(seq, 0, n))

    async def default_release(self, client, seq, data):
        asyncio.get_running_loop().call_later(0.005, self.release_now, client, seq)

    def release_now(self, client, seq):
        for s, (h1, h2) in list(self.pending_plays.items()):
            h1.cancel()
            h2.cancel()
            del self.pending_plays[s]
            if not self.drop_abort_done:
                client.notify(n_done(s, 3, 0, t0=0))
        client.notify(n_done(seq, 0, 1))


async def hang(*args):
    """A write that never completes but ends at once when cancelled."""
    await asyncio.Event().wait()


def dead_peer(queued_cancel=False):
    """A board handler for a write to a peer that is gone, as bleak runs it on WinRT: winrt's wrap_async
    (winrt/runtime/_internals.py:237-243) answers a cancel with `op.cancel(); await event.wait(); raise`, so a cancel
    is held until the op completes, and the op completes only when the link is torn down (A-V2link). With
    queued_cancel, only the first write is in flight and a cancel ends a later (queued) one at once: the model that
    reproduced the live ~0.97 s detection (A-V2link repro row 2)."""

    async def handler(client, seq, data):
        in_flight = client.in_flight is None
        if in_flight:
            client.in_flight = seq
        try:
            await client.gone.wait()
        except asyncio.CancelledError:
            if in_flight or not queued_cancel:
                await client.gone.wait()
            raise
        raise OSError("peer gone")

    return handler


held = dead_peer()


def release_held(lk, client):
    """End the writes a dead_peer handler still holds (hold_past_disconnect), so no task is left pending."""
    lk._loop.call_soon_threadsafe(client.gone.set)


def make_link(board, scanner=None):
    return BleLink(client_factory=board.factory, scanner=scanner or FakeScanner())


def connected_link(board):
    link = make_link(board)
    r = link.connect(timeout_s=5.0)
    assert is_successful(r), r
    return link


class Bg:
    """Run fn on its own thread; record the result and when it returned."""

    def __init__(self, fn, *args):
        self.result, self.t_end = None, None
        self.thread = threading.Thread(target=self._run, args=(fn, args), daemon=True)
        self.thread.start()

    def _run(self, fn, args):
        self.result = fn(*args)
        self.t_end = time.perf_counter()

    def join(self, timeout=5.0):
        self.thread.join(timeout)
        assert not self.thread.is_alive()
        return self.result


def wait_until(cond, timeout=3.0):
    end = time.perf_counter() + timeout
    while time.perf_counter() < end:
        if cond():
            return True
        time.sleep(0.005)
    return cond()


@pytest.fixture
def board():
    return FakeBoard()


@pytest.fixture
def link(board):
    lk = connected_link(board)
    yield lk
    lk.close()


# ---------------------------------------------------------------- import


def test_import_performs_no_io():
    code = "import threading, src.input.link as l; print(threading.active_count(), l.READY_TIMEOUT_S, l.PONG_BUFFER_MAX)"
    out = subprocess.run([sys.executable, "-c", code], cwd=REPO, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert out.stdout.split() == ["1", "5.0", "4096"]


def test_input_error_value_is_its_name():
    assert [e.value for e in InputError] == [e.name for e in InputError]
    assert str(InputError.LINK_LOST) == "LINK_LOST"


# ---------------------------------------------------------------- play


def test_happy_path(link, board):
    r = link.play(tap_steps())
    assert is_successful(r), r
    rep = r.unwrap()
    (w,) = board.ops(OP_PLAY)
    assert rep.seq == w.seq == rep.accepted.seq == rep.done.seq
    assert rep.sent_ns <= rep.written_ns
    assert rep.sent_ns <= rep.accepted_ns <= rep.done_ns
    assert rep.done.status == 0
    assert all(x.response is True and x.uuid == L.CMD_UUID for x in board.writes)
    assert board.client.notify_uuid == L.NOTIFY_UUID


@pytest.mark.parametrize("reason", [1, 2, 3, 4, 5, 6])
def test_rejected_reasons_map_to_rejected(link, board, reason):
    async def play(client, seq, data):
        client.notify(n_rejected(seq, reason))

    board.play = play
    assert link.play(tap_steps()).failure() == InputError.REJECTED


@pytest.mark.parametrize("status,err", [(1, InputError.NOTIFY_FAILED), (3, InputError.ABORTED),
                                        (2, InputError.ACK_TIMEOUT), (9, InputError.ACK_TIMEOUT)])
def test_done_status_mapping(link, board, caplog, status, err):
    async def play(client, seq, data):
        loop = asyncio.get_running_loop()
        loop.call_later(0.005, client.notify, n_accepted(seq))
        loop.call_later(0.010, client.notify, n_done(seq, status, 1, fail_step=0 if status == 1 else 0xFF))

    board.play = play
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        assert link.play(tap_steps()).failure() == err
    if status in (2, 9):
        assert any(rec.levelno == logging.WARNING and "status" in rec.getMessage() for rec in caplog.records)


def test_missing_accepted_is_ack_timeout_within_0_8s(link, board):
    async def play(client, seq, data):
        pass

    board.play = play
    t = time.perf_counter()
    assert link.play(tap_steps()).failure() == InputError.ACK_TIMEOUT
    assert time.perf_counter() - t < 0.8


def test_disconnect_during_long_play_is_link_lost_within_1s(link, board):
    bg = Bg(link.play, walk_steps(3000))
    assert wait_until(lambda: board.ops(OP_PLAY))
    time.sleep(0.2)
    t = time.perf_counter()
    board.client.drop()
    assert bg.join().failure() == InputError.LINK_LOST
    assert bg.t_end - t < 1.0
    assert [e for _, e in link.faults()] == [InputError.LINK_LOST]


@pytest.mark.parametrize("handler", [hang, held, dead_peer(queued_cancel=True)],
                         ids=["cancel_ends_write", "writes_held", "in_flight_write_held"])
def test_keepalive_pong_loss_is_fault_within_0_8s_of_the_last_pong(link, board, handler):
    """Designed: the next tick's PING (<= 250 ms after the last PONG) + its 500 ms limit, also when the write to the
    dead peer ignores its cancel (A-LK); the old code took ~1.0 s (in_flight_write_held) or never ended (writes_held)."""
    board.ping = handler
    assert wait_until(lambda: link.faults(), timeout=2.0)
    assert [e for _, e in link.faults()] == [InputError.LINK_LOST]
    last_pong_ns = link.take_pongs()[-1][0]
    assert (link.faults()[0][0] - last_pong_ns) / 1e9 <= 0.8


def test_too_long_against_client_mtu():
    board = FakeBoard(client_mtu=23)  # mtu - 3 = 20 bytes: a 6-step PLAY is 23 bytes
    lk = connected_link(board)
    try:
        assert lk.play(tap_steps()).unwrap().done.status == 0  # 11 bytes fit
        six = [Step(0, Key.E), Step(10, Key(0)), Step(10, Key.E), Step(10, Key(0)), Step(10, Key.E), Step(10, Key(0))]
        assert lk.play(six).failure() == InputError.TOO_LONG
        assert len(board.ops(OP_PLAY)) == 1
    finally:
        lk.close()


@pytest.mark.parametrize("steps", [
    [Step(10, Key.E if i % 2 == 0 else Key(0)) for i in range(17)],
    [Step(0, Key.E), Step(50_000, Key(0)), Step(0, Key.E), Step(50_001, Key(0))],
    [Step(0, Key.E), Step(65_536, Key(0))],
], ids=["17_steps", "sum_100001", "delta_65536"])
def test_length_rules_are_too_long_without_write(link, board, steps):
    assert link.play(steps).failure() == InputError.TOO_LONG
    assert board.ops(OP_PLAY) == []


@pytest.mark.parametrize("steps", [
    [Step(0, Key.E | Key.ALT), Step(10, Key(0))],
    [Step(0, Key.E), Step(10, Key(0)), Step(10, Key.E)],
], ids=["two_keys", "last_mask_nonzero"])
def test_shape_rules_are_rejected_with_one_error_record(link, board, caplog, steps):
    with caplog.at_level(logging.ERROR, logger=LOGGER):
        assert link.play(steps).failure() == InputError.REJECTED
    assert len([r for r in caplog.records if r.levelno == logging.ERROR and r.name == LOGGER]) == 1
    assert board.ops(OP_PLAY) == []


def test_seq_wraps_and_pending_table_uses_wrapped_value(link, board):
    link._seq = 0xFFFF  # next seq taken is (0xFFFF + 1) & 0xFFFF
    r = link.play(tap_steps())
    assert is_successful(r)
    assert board.ops(OP_PLAY)[-1].seq == 0 == r.unwrap().seq


def test_ping_returns_pong(link):
    assert link.ping().unwrap().subscribed is True


# ---------------------------------------------------------------- early notifications (PA-rev3a N1)


def test_early_pong_for_connect_and_keepalive(board):
    async def ping(client, seq, data):
        board.send_pong(client, seq)
        await asyncio.sleep(0.05)  # the write completes after its PONG

    board.ping = ping
    lk = connected_link(board)
    try:
        time.sleep(0.7)
        assert lk.faults() == []
    finally:
        lk.close()


@pytest.mark.parametrize("with_done", [False, True])
def test_early_accepted_and_done(link, board, with_done):
    async def play(client, seq, data):
        client.notify(n_accepted(seq))
        if with_done:
            client.notify(n_done(seq, 0, 2))
        else:
            asyncio.get_running_loop().call_later(0.03, client.notify, n_done(seq, 0, 2))
        await asyncio.sleep(0.02)

    board.play = play
    rep = link.play(tap_steps()).unwrap()
    assert rep.accepted_ns < rep.written_ns
    assert rep.sent_ns <= rep.accepted_ns <= rep.done_ns
    if with_done:
        assert rep.done_ns < rep.written_ns


def test_early_release_all_done(link, board):
    async def release(client, seq, data):
        client.notify(n_done(seq, 0, 1))
        await asyncio.sleep(0.02)

    board.release = release
    rep = link.release_all().unwrap()
    assert rep.done_ns < rep.written_ns
    assert rep.accepted is None and rep.accepted_ns is None


# ---------------------------------------------------------------- connect (PA-adj1-D1, PA-adj1c-D1, m10)


def test_connect_waits_for_subscribed(board):
    board.pong_script = [(0x07, 247)] * 3
    lk = make_link(board)
    try:
        info = lk.connect(timeout_s=5.0).unwrap()
        assert info.pong.subscribed is True
        assert len(board.ops(OP_PING)) >= 4
        assert info.mtu == board.client.mtu_size and info.address == Device.address
        assert board.client.kwargs["winrt"] == {"use_cached_services": False}
    finally:
        lk.close()


def test_connect_never_subscribed_fails_naming_subscribed(board, monkeypatch):
    monkeypatch.setattr(L, "READY_TIMEOUT_S", 0.3)
    board.pong_flags = 0x07
    lk = make_link(board)
    t = time.perf_counter()
    r = lk.connect(timeout_s=5.0)
    assert time.perf_counter() - t < 0.8
    assert "subscribed=False" in r.failure()
    assert board.client.disconnect_calls == 1


@pytest.mark.parametrize("mtu,ok", [(23, False), (55, False), (56, True)])
def test_connect_requires_pong_mtu_56(board, monkeypatch, mtu, ok):
    monkeypatch.setattr(L, "READY_TIMEOUT_S", 0.3)
    board.pong_mtu = mtu
    lk = make_link(board)
    r = lk.connect(timeout_s=5.0)
    try:
        assert is_successful(r) is ok
        if not ok:
            assert f"mtu={mtu}" in r.failure() and board.client.disconnect_calls == 1
    finally:
        lk.close()


def test_connect_not_bonded_fails(board, monkeypatch):
    monkeypatch.setattr(L, "READY_TIMEOUT_S", 0.3)
    board.pong_flags = 0x0E
    r = make_link(board).connect(timeout_s=5.0)
    assert "bonded=False" in r.failure()
    assert board.client.disconnect_calls == 1


def test_connect_no_pong(board, monkeypatch):
    monkeypatch.setattr(L, "READY_TIMEOUT_S", 0.3)
    board.ping = hang
    r = make_link(board).connect(timeout_s=5.0)
    assert "no PONG" in r.failure()
    assert board.client.disconnect_calls == 1


def test_connect_one_deadline(board):
    board.on_connect = hang
    scanner = FakeScanner(delay=0.6)
    lk = make_link(board, scanner)
    t = time.perf_counter()
    r = lk.connect(timeout_s=1.0)
    assert time.perf_counter() - t < 1.3
    assert "connect" in r.failure()
    assert scanner.calls[0][1] <= 1.0
    assert board.client.kwargs["timeout"] <= 0.4
    assert board.client.disconnect_calls == 1


# ---------------------------------------------------------------- pre-emption (PA-adj2-D1, m4, I2(vi))


@pytest.mark.parametrize("drop_abort_done", [False, True])
def test_release_all_preempts_play(link, board, drop_abort_done):
    board.drop_abort_done = drop_abort_done
    bg = Bg(link.play, walk_steps(3000))
    assert wait_until(lambda: board.ops(OP_PLAY))
    time.sleep(0.5)
    t = time.perf_counter()
    rel = link.release_all()
    t_rel = time.perf_counter()
    assert bg.join().failure() == InputError.ABORTED
    assert board.ops(OP_RELEASE_ALL)  # written while the PLAY was pending: the play lock was bypassed
    assert t_rel - t < 0.5 and bg.t_end - t < 0.5
    rep = rel.unwrap()
    assert rep.accepted is None and rep.accepted_ns is None


@pytest.mark.parametrize("release_done", [True, False], ids=["release_done", "release_done_lost"])
def test_release_all_aborts_a_registered_unwritten_play(link, board, release_done):
    parked, gate = threading.Event(), threading.Event()

    async def before_write(seq):
        parked.set()
        await asyncio.get_running_loop().run_in_executor(None, gate.wait)

    async def silent_release(client, seq, data):
        pass

    if not release_done:  # the abort must not depend on the RELEASE_ALL's DONE arriving
        board.release = silent_release
    link._before_write = before_write
    bg = Bg(link.play, tap_steps())
    assert parked.wait(2.0)
    assert is_successful(link.release_all()) is release_done
    gate.set()
    assert bg.join().failure() == InputError.ABORTED
    assert board.ops(OP_PLAY) == []


def test_play_written_after_release_all_completes_normally(link, board):
    held = []

    async def release(client, seq, data):
        held.append((client, seq))

    async def play(client, seq, data):
        rc, rseq = held[0]
        rc.notify(n_done(rseq, 0, 1))
        loop = asyncio.get_running_loop()
        loop.call_later(0.005, client.notify, n_accepted(seq))
        loop.call_later(0.010, client.notify, n_done(seq, 0, 2))

    board.release, board.play = release, play
    bg_rel = Bg(link.release_all)
    assert wait_until(lambda: held)
    bg_play = Bg(link.play, tap_steps())
    assert is_successful(bg_rel.join()) and is_successful(bg_play.join())
    assert board.ops(OP_RELEASE_ALL)[0].ns < board.ops(OP_PLAY)[0].ns


# ---------------------------------------------------------------- release_all error mapping (I2)


def test_release_all_write_raises(link, board):
    async def release(client, seq, data):
        raise RuntimeError("write failed")

    board.release = release
    assert link.release_all().failure() == InputError.WRITE_FAILED


@pytest.mark.parametrize("handler", [hang, held], ids=["cancel_ends_write", "write_held"])
def test_release_all_write_never_completes(link, board, handler):
    board.release = handler
    t = time.perf_counter()
    bg = Bg(link.release_all)
    assert bg.join().failure() == InputError.WRITE_FAILED
    assert bg.t_end - t < 0.8


@pytest.mark.parametrize("note,err", [(lambda s: n_done(s, 1, 1, fail_step=0, rc=-1), InputError.NOTIFY_FAILED),
                                      (lambda s: n_rejected(s, 4), InputError.REJECTED),
                                      (None, InputError.ACK_TIMEOUT)], ids=["notify_failed", "rejected", "no_done"])
def test_release_all_answers(link, board, note, err):
    async def release(client, seq, data):
        if note is not None:
            asyncio.get_running_loop().call_later(0.005, client.notify, note(seq))

    board.release = release
    assert link.release_all().failure() == err


def test_release_all_before_connect(board):
    assert make_link(board).release_all().failure() == InputError.NOT_CONNECTED


def test_two_release_alls_are_independent(link, board):
    calls = []

    async def release(client, seq, data):
        calls.append(seq)
        if len(calls) == 2:
            asyncio.get_running_loop().call_later(0.005, client.notify, n_done(seq, 0, 1))

    board.release = release
    first = Bg(link.release_all)
    assert wait_until(lambda: calls)
    second = Bg(link.release_all)
    assert is_successful(second.join())
    assert first.join().failure() == InputError.ACK_TIMEOUT


# ---------------------------------------------------------------- session state (m3, PA-rev3a-D2)


def assert_dead(lk, board):
    n = len(board.writes)
    assert lk.play(tap_steps()).failure() == InputError.NOT_CONNECTED
    assert lk.release_all().failure() == InputError.NOT_CONNECTED
    assert lk.ping().failure() == InputError.NOT_CONNECTED
    assert len(board.writes) == n


def assert_reconnects(lk, board):
    n_clients = len(board.clients)
    assert is_successful(lk.connect(timeout_s=5.0))
    assert len(board.clients) == n_clients + 1
    assert is_successful(lk.play(tap_steps()))


def test_after_keepalive_miss(board):
    lk = connected_link(board)
    try:
        board.ping = hang
        assert wait_until(lambda: lk.faults(), timeout=1.0)
        assert [e for _, e in lk.faults()] == [InputError.LINK_LOST]
        assert wait_until(lambda: board.client.disconnect_calls == 1)
        assert_dead(lk, board)
        board.ping = board.default_ping
        assert_reconnects(lk, board)
        assert len(lk.faults()) == 1  # append-only, not cleared by connect()
    finally:
        lk.close()


def test_close_fails_pending_play_without_fault(board):
    lk = connected_link(board)
    try:
        bg = Bg(lk.play, walk_steps(3000))
        assert wait_until(lambda: board.ops(OP_PLAY))
        lk.close()
        assert bg.join().failure() == InputError.NOT_CONNECTED
        assert lk.faults() == []
        assert board.client.disconnect_calls == 1
        lk.close()  # second close: no-op
        assert board.client.disconnect_calls == 1
        assert_dead(lk, board)
        assert_reconnects(lk, board)
    finally:
        lk.close()


def test_close_with_play_parked_before_write(board):
    lk = connected_link(board)
    parked, gate = threading.Event(), threading.Event()

    async def before_write(seq):
        parked.set()
        await asyncio.get_running_loop().run_in_executor(None, gate.wait)

    lk._before_write = before_write
    bg = Bg(lk.play, tap_steps())
    assert parked.wait(2.0)
    lk.close()
    gate.set()
    assert bg.join().failure() == InputError.NOT_CONNECTED
    assert board.ops(OP_PLAY) == []
    assert lk.faults() == []


def test_close_before_connect_is_noop(board):
    lk = make_link(board)
    lk.close()
    assert board.clients == [] and lk.faults() == []


def test_old_client_disconnect_after_reconnect_is_ignored(board):
    lk = connected_link(board)
    try:
        lk.close()
        assert is_successful(lk.connect(timeout_s=5.0))
        board.clients[0].drop()
        time.sleep(0.05)
        assert lk.faults() == []
        assert is_successful(lk.play(tap_steps()))
    finally:
        lk.close()


def test_connect_on_live_session_fails(link, board):
    r = link.connect(timeout_s=5.0)
    assert "already connected" in r.failure()
    assert len(board.clients) == 1
    assert is_successful(link.play(tap_steps()))


# ---------------------------------------------------------------- keepalive timing (m11)


def test_keepalive_schedule_and_blocked_write(link, board):
    async def slow_ping(client, seq, data):
        asyncio.get_running_loop().call_later(0.2, board.send_pong, client, seq)

    n0 = len(board.ops(OP_PING))
    board.ping = slow_ping
    time.sleep(1.6)
    starts = [w.ns for w in board.ops(OP_PING)[n0:]]
    gaps = [(b - a) / 1e6 for a, b in zip(starts, starts[1:])]
    assert len(gaps) >= 4 and all(200 <= g <= 300 for g in gaps), gaps

    blocked = []

    async def block(client, seq, data):
        blocked.append(time.perf_counter_ns())
        await held(client, seq, data)

    bg = Bg(link.play, walk_steps(3000))
    assert wait_until(lambda: board.ops(OP_PLAY))
    board.ping = block
    assert wait_until(lambda: link.faults(), timeout=1.5)
    fault_ns = link.faults()[0][0]
    assert (fault_ns - blocked[0]) / 1e9 < 0.8
    assert bg.join().failure() == InputError.LINK_LOST
    assert bg.t_end * 1e9 - blocked[0] < 0.8e9  # perf_counter and perf_counter_ns share a clock


# ---------------------------------------------------------------- soft vs hard keepalive misses (A-FCdbg-D1 -> E)


async def protocol_error(client, seq, data):
    """The board answered the write with an ATT error: 0x11 Insufficient Resources after a burst (A-FCdbg)."""
    raise BleakGATTProtocolError(0x11)


async def no_pong(client, seq, data):
    """The write completes (the board answered it), but no PONG comes."""


async def unreachable(client, seq, data):
    raise BleakError("Could not write value bytearray(b'') to characteristic 0013: Unreachable")


def recording(*handlers):
    """A ping handler that records each write's start (perf_counter_ns) in .starts. Write i uses handlers[i], the
    last one for every later write."""
    starts = []

    async def handler(client, seq, data):
        starts.append(time.perf_counter_ns())
        await handlers[min(len(starts), len(handlers)) - 1](client, seq, data)

    handler.starts = starts
    return handler


def soft_miss_warnings(caplog):
    return [r for r in caplog.records
            if r.name == LOGGER and r.levelno == logging.WARNING and "soft miss" in r.getMessage()]


SOFT = pytest.mark.parametrize("soft", [protocol_error, no_pong], ids=["att_error", "no_pong"])


@SOFT
def test_one_soft_keepalive_miss_is_a_warning_not_a_fault(link, board, caplog, soft):
    """T1/T2: the board answered the PING's write, so the link is alive: one WARNING, no fault, the session stays."""
    handler = recording(soft, board.default_ping)
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        board.ping = handler
        assert wait_until(lambda: len(handler.starts) >= 4)  # the soft PING's 0.5 s limit has passed
        time.sleep(0.1)
    assert link.faults() == []
    assert board.client.disconnect_calls == 0
    assert is_successful(link.play(tap_steps()))
    assert len(soft_miss_warnings(caplog)) == 1
    assert not any("link fault" in r.getMessage() for r in caplog.records)


@SOFT
def test_third_soft_keepalive_miss_in_a_row_is_link_lost(link, board, soft):
    """T3: K = 3. Every PING soft: LINK_LOST at the 3rd soft miss, the only fault; a pending play returns LINK_LOST."""
    bg = Bg(link.play, walk_steps(3000))
    assert wait_until(lambda: board.ops(OP_PLAY))
    handler = recording(soft)
    board.ping = handler
    assert wait_until(lambda: link.faults(), timeout=2.0)
    assert bg.join().failure() == InputError.LINK_LOST
    time.sleep(0.3)  # a further tick would have written by now
    assert fault_errors(link) == [InputError.LINK_LOST]
    fault_ns, t0 = link.faults()[0][0], handler.starts[0]
    if soft is no_pong:  # 3rd soft PING starts at t0 + 2 x 250 ms and misses at its 500 ms limit
        expected = t0 + (2 * L.KEEPALIVE_PERIOD_S + L.PING_TIMEOUT_S) * 1e9
        assert expected - 0.05e9 <= fault_ns <= expected + 0.15e9, (fault_ns - t0) / 1e9
    else:  # the write fails at once: the fault follows the 3rd write, and no 4th PING is written
        assert len(handler.starts) == 3, len(handler.starts)
        assert handler.starts[2] <= fault_ns < handler.starts[2] + 0.1e9


@SOFT
def test_a_pong_resets_the_soft_miss_count(link, board, soft):
    """T4: soft and answered PINGs alternating for >= 8 ticks never end the session."""
    handler = recording(*[soft, board.default_ping] * 6)
    board.ping = handler
    assert wait_until(lambda: len(handler.starts) >= 11, timeout=4.0)
    time.sleep(0.6)
    assert link.faults() == []
    assert is_successful(link.play(tap_steps()))


def test_keepalive_write_unreachable_is_a_hard_miss(link, board):
    """T5: a write error other than BleakGATTProtocolError (bleak raises plain BleakError for UNREACHABLE /
    ACCESS_DENIED) is a hard miss: LINK_LOST at once, no soft-miss count."""
    handler = recording(unreachable)
    board.ping = handler
    assert wait_until(lambda: link.faults(), timeout=1.0)
    assert fault_errors(link) == [InputError.LINK_LOST]
    assert (link.faults()[0][0] - handler.starts[0]) / 1e9 < 0.3


def test_hard_miss_after_a_soft_miss_does_not_wait_for_the_count(link, board):
    """T6: one soft miss, then every write blocked (dead peer): LINK_LOST at the first blocked PING's 500 ms limit.
    Bound 0.7 s, not 0.8: a hard miss that went through the count would end the session at the 2nd blocked PING's
    limit, 0.75 s after the first blocked write."""
    handler = recording(protocol_error, held)
    board.ping = handler
    assert wait_until(lambda: link.faults(), timeout=2.0)
    assert fault_errors(link) == [InputError.LINK_LOST]
    assert (link.faults()[0][0] - handler.starts[1]) / 1e9 < 0.7


# ---------------------------------------------------------------- PONG log (A-PF-D2)


def test_take_pongs(link, board):
    time.sleep(0.8)  # >= 3 keepalive ticks
    pongs = link.take_pongs()
    seqs = [p.seq for _, p in pongs]
    assert len(seqs) >= 4 and seqs == board.pong_seqs[:len(seqs)]
    stamps = [ns for ns, _ in pongs]
    assert stamps == sorted(stamps)
    start = {w.seq: w.ns for w in board.ops(OP_PING)}
    assert all(ns >= start[p.seq] for ns, p in pongs)

    later = link.take_pongs()
    assert [p.seq for _, p in later] == board.pong_seqs[len(seqs):len(seqs) + len(later)]

    link.take_pongs()
    board.client.notify(n_pong(60_000))  # no PING pending with that seq: dropped by the seq filter
    time.sleep(0.05)
    assert 60_000 not in [p.seq for _, p in link.take_pongs()]


def test_pong_buffer_bound(board, monkeypatch, caplog):
    monkeypatch.setattr(L, "PONG_BUFFER_MAX", 3)
    monkeypatch.setattr(L, "KEEPALIVE_PERIOD_S", 100.0)  # keep keepalive PONGs out of the count
    lk = connected_link(board)  # 1 PONG
    try:
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            for _ in range(4):
                assert is_successful(lk.ping())
            pongs = lk.take_pongs()
        assert [p.seq for _, p in pongs] == board.pong_seqs[-3:]
        assert len(board.pong_seqs) == 5
        assert len([r for r in caplog.records if r.levelno == logging.WARNING and r.name == LOGGER]) == 1
    finally:
        lk.close()


# ---------------------------------------------------------------- seq filter and decode failures (rows 15, 17)


def test_late_done_completes_nothing(link, board):
    plays = []

    async def play(client, seq, data):
        plays.append(seq)
        loop = asyncio.get_running_loop()
        if len(plays) == 1:
            loop.call_later(0.005, client.notify, n_accepted(seq))  # DONE withheld
        else:
            client.notify(n_done(plays[0], 0, 2))  # play 1's late DONE
            loop.call_later(0.005, client.notify, n_accepted(seq))
            loop.call_later(0.010, client.notify, n_done(seq, 0, 2))

    board.play = play
    assert link.play(tap_steps()).failure() == InputError.ACK_TIMEOUT
    rep = link.play(tap_steps()).unwrap()
    assert rep.seq == plays[1] == rep.done.seq


def test_malformed_frames_are_logged_and_dropped(link, board, caplog):
    bad_version = b"\x02\x81" + struct.pack("<HI", 0, 0)
    truncated = b"\x01\x81\x00"

    async def play(client, seq, data):
        loop = asyncio.get_running_loop()
        loop.call_later(0.002, client.notify, bad_version)
        loop.call_later(0.003, client.notify, truncated)
        loop.call_later(0.005, client.notify, n_accepted(seq))
        loop.call_later(0.010, client.notify, n_done(seq, 0, 2))

    board.play = play
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        assert is_successful(link.play(tap_steps()))
    warns = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING and r.name == LOGGER]
    assert len(warns) == 2
    assert bad_version.hex() in warns[0] and truncated.hex() in warns[1]


def test_malformed_frame_instead_of_accepted_is_ack_timeout(link, board):
    async def play(client, seq, data):
        asyncio.get_running_loop().call_later(0.005, client.notify, b"\x01\x81\x00")

    board.play = play
    assert link.play(tap_steps()).failure() == InputError.ACK_TIMEOUT


# ---------------------------------------------------------------- faults() beyond LINK_LOST (A-P4-R I1, A-P4-R-D1)


def fault_errors(lk):
    return [e for _, e in lk.faults()]


def answering(*notes):
    """A board handler that writes successfully and sends each notification builder (seq -> bytes), 5 ms apart."""

    async def handler(client, seq, data):
        loop = asyncio.get_running_loop()
        for i, note in enumerate(notes, 1):
            loop.call_later(0.005 * i, client.notify, note(seq))

    return handler


async def write_raises(client, seq, data):
    raise RuntimeError("write failed")


def test_missing_accepted_is_a_fault_and_the_session_stays_live(link, board):
    board.play = answering()
    assert link.play(tap_steps()).failure() == InputError.ACK_TIMEOUT
    assert fault_errors(link) == [InputError.ACK_TIMEOUT]
    board.play = board.default_play
    assert is_successful(link.play(tap_steps()))
    link.close()
    assert fault_errors(link) == [InputError.ACK_TIMEOUT]  # close() adds none


@pytest.mark.parametrize("call,notes", [
    ("play", ()),
    ("play", (n_accepted,)),
    ("play", (n_accepted, lambda s: n_done(s, 2, 1))),
    ("release_all", ()),
], ids=["play_no_accepted", "play_no_done", "play_done_status_2", "release_all_no_done"])
def test_ack_timeout_is_recorded_as_a_fault(link, board, call, notes):
    setattr(board, "release" if call == "release_all" else "play", answering(*notes))
    r = link.release_all() if call == "release_all" else link.play(tap_steps())
    assert r.failure() == InputError.ACK_TIMEOUT
    assert fault_errors(link) == [InputError.ACK_TIMEOUT]


@pytest.mark.parametrize("handler,err", [
    (write_raises, InputError.WRITE_FAILED),
    (answering(n_accepted, lambda s: n_done(s, 1, 1, fail_step=0)), InputError.NOTIFY_FAILED),
    (answering(lambda s: n_rejected(s, 4)), InputError.REJECTED),
], ids=["write_failed", "notify_failed", "rejected"])
def test_other_failures_are_not_faults(link, board, handler, err):
    board.play = handler
    assert link.play(tap_steps()).failure() == err
    assert link.faults() == []


@pytest.mark.parametrize("handler", [hang, held, answering()], ids=["write_blocked", "write_held", "no_pong"])
def test_ping_ack_timeout_is_a_fault_and_the_session_stays_live(board, monkeypatch, handler):
    monkeypatch.setattr(L, "KEEPALIVE_PERIOD_S", 100.0)  # after connect(), only this ping() writes
    lk = connected_link(board)
    try:
        board.ping = handler
        t = time.perf_counter()
        bg = Bg(lk.ping)
        assert bg.join().failure() == InputError.ACK_TIMEOUT
        assert bg.t_end - t < 0.6
        assert fault_errors(lk) == [InputError.ACK_TIMEOUT]
        board.ping = board.default_ping
        assert is_successful(lk.ping())
        assert is_successful(lk.play(tap_steps()))
        assert fault_errors(lk) == [InputError.ACK_TIMEOUT]
    finally:
        lk.close()


def test_ready_wait_pings_add_no_faults(board, monkeypatch):
    monkeypatch.setattr(L, "READY_TIMEOUT_S", 0.3)
    board.ping = hang
    lk = make_link(board)
    assert "no PONG" in lk.connect(timeout_s=5.0).failure()
    assert lk.faults() == []


# ---------------------------------------------------------------- public ping() failures (A-P4-D2 -> A', A-P4-R m2)


def test_ping_write_failure_is_returned_and_the_session_stays_live(board, monkeypatch):
    monkeypatch.setattr(L, "KEEPALIVE_PERIOD_S", 100.0)
    lk = connected_link(board)
    try:
        board.ping = write_raises
        assert lk.ping().failure() == InputError.WRITE_FAILED
        assert lk.faults() == []  # WRITE_FAILED is not a fault (A-P4-R-D1)
        board.ping = board.default_ping
        assert is_successful(lk.ping())
        assert is_successful(lk.play(tap_steps()))
        assert board.client.disconnect_calls == 0
    finally:
        lk.close()


@pytest.mark.parametrize("end,err,faults", [
    ("drop", InputError.LINK_LOST, [InputError.LINK_LOST]),
    ("close", InputError.NOT_CONNECTED, []),
])
@pytest.mark.parametrize("handler", [answering(), held], ids=["no_pong", "write_held"])
def test_ping_pending_when_the_session_ends(board, monkeypatch, end, err, faults, handler):
    """no_pong: the write completes, the PONG never comes. write_held: the write never completes, not even at the
    disconnect, and ignores its cancel (A-LK; plan P4: a ping() blocked in its write returns within its 0.5 s)."""
    monkeypatch.setattr(L, "KEEPALIVE_PERIOD_S", 100.0)
    lk = connected_link(board)
    client = board.client
    client.hold_past_disconnect = True
    try:
        board.ping = handler
        n0 = len(board.ops(OP_PING))
        bg = Bg(lk.ping)
        assert wait_until(lambda: len(board.ops(OP_PING)) > n0)
        t = time.perf_counter()
        board.client.drop() if end == "drop" else lk.close()
        assert bg.join().failure() == err
        assert bg.t_end - t < 0.3  # not the 0.5 s ping limit
        assert fault_errors(lk) == faults
    finally:
        lk.close()
        release_held(lk, client)


# ---------------------------------------------------------------- a write blocked at session end (A-P4-R I2, P4 :1084-1086)


@pytest.mark.parametrize("call", ["play", "release_all"])
def test_command_blocked_in_its_write_fails_at_once_on_link_lost(link, board, call):
    blocked = []

    async def block_ping(client, seq, data):
        blocked.append(time.perf_counter())
        await held(client, seq, data)

    board.ping = block_ping
    board.play = board.release = held
    assert wait_until(lambda: blocked)
    time.sleep(0.3)  # the keepalive miss lands 0.5 s after the first blocked PING: this command is mid-write by then
    bg = Bg(link.play, tap_steps()) if call == "play" else Bg(link.release_all)
    assert wait_until(lambda: link.faults(), timeout=1.5)
    fault_s = link.faults()[0][0] / 1e9  # perf_counter and perf_counter_ns share a clock
    assert bg.join().failure() == InputError.LINK_LOST
    assert bg.t_end - fault_s < 0.1  # not the 0.5 s WRITE_TIMEOUT_S


@pytest.mark.parametrize("end", ["close", "drop"])
@pytest.mark.parametrize("handler", [hang, held], ids=["cancel_ends_write", "write_held"])
def test_play_blocked_in_its_write_fails_at_once_when_the_session_ends(link, board, end, handler):
    """write_held: the write ignores its cancel and outlives the disconnect (A-LK)."""
    client = board.client
    client.hold_past_disconnect = True
    board.play = handler
    bg = Bg(link.play, tap_steps())
    try:
        assert wait_until(lambda: board.ops(OP_PLAY))
        t = time.perf_counter()
        client.drop() if end == "drop" else link.close()
        assert bg.join().failure() == (InputError.LINK_LOST if end == "drop" else InputError.NOT_CONNECTED)
        assert bg.t_end - t < 0.1
    finally:
        release_held(link, client)


def test_abandoned_writes_leave_no_unretrieved_task_exception(board):
    """A PING returns on its PONG without waiting for its write (A-LK); a write that fails after that is retrieved,
    so asyncio never reports "Task exception was never retrieved" (nor a pending task destroyed)."""
    async def pong_then_fail(client, seq, data):
        board.send_pong(client, seq)
        await asyncio.sleep(0.02)
        raise RuntimeError("write failed after its PONG")

    lk = connected_link(board)
    reported = []
    lk._loop.set_exception_handler(lambda loop, ctx: reported.append(ctx.get("message")))
    try:
        board.ping = pong_then_fail
        assert is_successful(lk.ping())
        time.sleep(0.6)  # >= 2 keepalive ticks, each a PING whose write fails after its PONG
        assert lk.faults() == []
    finally:
        lk.close()
    time.sleep(0.05)
    del lk
    gc.collect()
    assert reported == []


# ---------------------------------------------------------------- connect() returns Failure, never raises (A-P4-R m1)


def test_connect_returns_failure_when_the_client_factory_raises(board):
    calls = []

    def factory(device, **kwargs):
        calls.append(device)
        if len(calls) == 1:
            raise RuntimeError("no adapter")
        return board.factory(device, **kwargs)

    lk = BleLink(client_factory=factory, scanner=FakeScanner())
    try:
        r = lk.connect(timeout_s=5.0)
        assert "client failed" in r.failure() and "no adapter" in r.failure()
        assert board.clients == [] and lk.faults() == []
        assert is_successful(lk.connect(timeout_s=5.0))  # the link is still usable
    finally:
        lk.close()


def test_connect_returns_failure_and_disconnects_when_mtu_size_raises(board):
    class MtuRaises(FakeClient):
        @property
        def mtu_size(self):
            raise RuntimeError("no mtu")

        @mtu_size.setter
        def mtu_size(self, value):
            pass

    def factory(device, disconnected_callback=None, **kwargs):
        board.clients.append(MtuRaises(board, device, disconnected_callback, **kwargs))
        return board.client

    lk = BleLink(client_factory=factory, scanner=FakeScanner())
    r = lk.connect(timeout_s=5.0)
    assert "mtu_size failed" in r.failure() and "no mtu" in r.failure()
    assert board.client.disconnect_calls == 1
    assert lk.faults() == []
    assert lk.play(tap_steps()).failure() == InputError.NOT_CONNECTED


def test_every_reply_is_logged_at_debug_with_its_loop_time(link, board, caplog):
    """probe --link-debug (A-M6diag): each decoded reply is one DEBUG `rx` record, stamped with the ns the loop used."""
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        rep = link.play(tap_steps()).unwrap()
    rx = [m for m in (rec.getMessage() for rec in caplog.records if rec.levelno == logging.DEBUG)
          if m.startswith("rx ")]
    assert any(m.startswith("rx Accepted(") and m.endswith(f"ns={rep.accepted_ns}") for m in rx), rx
    assert any(m.startswith("rx Done(") and m.endswith(f"ns={rep.done_ns}") for m in rx), rx


def test_rx_records_stay_below_warning(link, board, caplog):
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        assert is_successful(link.play(tap_steps()))
    assert not [rec for rec in caplog.records if rec.getMessage().startswith("rx ")]
