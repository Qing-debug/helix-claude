"""BLE link to the board (plan A task P4; contracts C1, C3).

The bleak event loop runs on a daemon thread. Every public method, from any thread, submits a coroutine to that loop
and blocks on its result, so all link state (the seq counter, the pending table keyed by seq, the session, the fault
and PONG logs) is read and written on the loop thread only. Nothing runs at import.
"""
import asyncio
import logging
import threading
import time
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from bleak import BleakClient, BleakScanner
from returns.pipeline import is_successful
from returns.result import Failure, Result, Success

from src.input.protocol import (MAX_STEP_DELTA, MAX_STEPS, MAX_TOTAL_DELTA, Accepted, Done, Pong, Rejected, Step,
                                decode_notification, encode_ping, encode_play, encode_release_all)

log = logging.getLogger("src.input.link")

CMD_UUID = "61adcd67-20e9-4c38-b1d0-879445c5b732"
NOTIFY_UUID = "89f7c2ca-89b5-4234-905c-d4a931feaa3b"

SCAN_TIMEOUT_S = 10.0
READY_TIMEOUT_S = 5.0  # a choice, not measured (F5 step 5: Windows subscribes right after it connects)
READY_PING_PERIOD_S = 0.1
MIN_PONG_MTU = 56  # longest command 53 bytes + 3 (C1 size limits)
KEEPALIVE_PERIOD_S = 0.25
PING_TIMEOUT_S = 0.5  # write + PONG
WRITE_TIMEOUT_S = 0.5
ACCEPT_TIMEOUT_S = 0.3  # after the write completes
DONE_MARGIN_S = 0.3  # PLAY: schedule duration + this; RELEASE_ALL: this, after the write
PONG_BUFFER_MAX = 4096

_PLAY, _RELEASE, _PING = "play", "release_all", "ping"


class InputError(StrEnum):
    """Value = name, so str(), .value and .name all serialize as e.g. "LINK_LOST" (C5 link_faults.jsonl)."""

    NOT_CONNECTED = "NOT_CONNECTED"
    WRITE_FAILED = "WRITE_FAILED"
    REJECTED = "REJECTED"
    NOTIFY_FAILED = "NOTIFY_FAILED"
    ABORTED = "ABORTED"
    ACK_TIMEOUT = "ACK_TIMEOUT"
    LINK_LOST = "LINK_LOST"
    TOO_LONG = "TOO_LONG"


@dataclass(frozen=True)
class SendReport:
    seq: int
    sent_ns: int
    written_ns: int
    accepted_ns: int | None  # None for RELEASE_ALL
    done_ns: int
    accepted: Accepted | None  # None for RELEASE_ALL
    done: Done


@dataclass(frozen=True)
class SessionInfo:
    address: str
    mtu: int
    pong: Pong


@dataclass(eq=False)
class _Pending:
    kind: str
    seq: int
    final: asyncio.Future  # the command's Result, set once by _resolve
    write_started: bool = False
    sent_ns: int = 0
    written_ns: int = 0
    accepted: Accepted | None = None
    accepted_ns: int | None = None
    done: Done | None = None
    done_ns: int = 0
    accepted_evt: asyncio.Event = field(default_factory=asyncio.Event)
    done_evt: asyncio.Event = field(default_factory=asyncio.Event)
    covers: list["_Pending"] = field(default_factory=list)  # RELEASE_ALL: PLAYs written before it ran

    def takes(self, msg) -> bool:
        if self.kind == _PING:
            return isinstance(msg, Pong)
        if self.kind == _PLAY:
            return isinstance(msg, (Accepted, Done, Rejected))
        return isinstance(msg, (Done, Rejected))


@dataclass(eq=False)
class _Session:
    client: object
    mtu: int
    start: float  # loop.time() at connect()'s Success
    keepalive: asyncio.Task | None = None
    tasks: set = field(default_factory=set)
    writes: set = field(default_factory=set)  # asyncio.Timeout of each PLAY / RELEASE_ALL write in progress


def _unmet(pong: Pong) -> list[str]:
    unmet = [f"{name}=False" for name in ("bonded", "encrypted", "subscribed") if not getattr(pong, name)]
    if pong.mtu < MIN_PONG_MTU:
        unmet.append(f"mtu={pong.mtu}")
    return unmet


class BleLink:
    def __init__(self, name: str = "Guiness", client_factory=BleakClient, scanner=BleakScanner) -> None:
        self._name = name
        self._client_factory = client_factory
        self._scanner = scanner
        self._seq = 0
        self._pending: dict[int, _Pending] = {}
        self._session: _Session | None = None
        self._connecting = False
        self._disconnect_task: asyncio.Task | None = None
        self._faults: list[tuple[int, InputError]] = []
        self._pongs: deque[tuple[int, Pong]] = deque()
        self._pong_overflow_warned = False
        self._play_lock = asyncio.Lock()
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, name="BleLink-loop", daemon=True)
        self._thread.start()

    # ------------------------------------------------------------ public, blocking bridge

    def _call(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()

    def connect(self, timeout_s: float = 30.0) -> Result[SessionInfo, str]:
        return self._call(self._connect(timeout_s))

    def play(self, steps: Sequence[Step]) -> Result[SendReport, InputError]:
        return self._call(self._play(steps))

    def release_all(self) -> Result[SendReport, InputError]:
        return self._call(self._release_all())

    def ping(self) -> Result[Pong, InputError]:
        return self._call(self._public_ping())

    def faults(self) -> list[tuple[int, InputError]]:
        return self._call(self._read(lambda: list(self._faults)))

    def take_pongs(self) -> list[tuple[int, Pong]]:
        return self._call(self._read(self._drain_pongs))

    def close(self) -> None:
        self._call(self._close())

    async def _read(self, fn):
        return fn()

    # ------------------------------------------------------------ pending table

    def _next_seq(self) -> int:
        self._seq = (self._seq + 1) & 0xFFFF
        return self._seq

    def _register(self, kind: str) -> _Pending:
        seq = self._next_seq()
        entry = _Pending(kind, seq, self._loop.create_future())
        self._pending[seq] = entry
        return entry

    def _resolve(self, entry: _Pending, result) -> None:
        if not entry.final.done():
            entry.final.set_result(result)
        if self._pending.get(entry.seq) is entry:
            del self._pending[entry.seq]

    async def _wait_evt(self, entry: _Pending, evt: asyncio.Event, timeout: float) -> None:
        """Wait for evt, at most timeout; return early if the command is resolved (fault, abort, REJECTED...)."""
        if evt.is_set() or entry.final.done():
            return
        waiter = asyncio.ensure_future(evt.wait())
        await asyncio.wait({waiter, entry.final}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
        waiter.cancel()

    async def _before_write(self, seq: int) -> None:
        """Test seam (PA-rev m4): tests replace it on the instance to park a registered PLAY before its write."""

    # ------------------------------------------------------------ notifications and disconnects

    def _on_loop(self) -> bool:
        return threading.current_thread() is self._thread

    def _on_notify(self, _char, data: bytearray) -> None:
        if not self._on_loop():
            self._loop.call_soon_threadsafe(self._on_notify, _char, data)
            return
        now = time.perf_counter_ns()
        raw = bytes(data)
        decoded = decode_notification(raw)
        if not is_successful(decoded):
            log.warning("dropping undecodable notification %s: %s", raw.hex(), decoded.failure())
            return
        msg = decoded.unwrap()
        entry = self._pending.get(msg.seq)
        if entry is None or not entry.takes(msg):
            log.debug("dropping %r: no pending command takes it", msg)
            return
        if isinstance(msg, Pong):
            self._log_pong(now, msg)
            self._resolve(entry, Success(msg))
        elif isinstance(msg, Accepted):
            entry.accepted, entry.accepted_ns = msg, now
            entry.accepted_evt.set()
        elif isinstance(msg, Rejected):
            log.warning("seq=%d %s REJECTED reason=%d", msg.seq, entry.kind, msg.reason)
            self._resolve(entry, Failure(InputError.REJECTED))
        else:
            self._on_done(entry, msg, now)

    def _on_done(self, entry: _Pending, done: Done, now: int) -> None:
        if entry.kind == _RELEASE:
            # The board answers every earlier command before this DONE: PLAYs it covered that are still pending
            # (their DONE status 3 was lost) are aborted now. One that already has its DONE OK is left alone.
            for play in entry.covers:
                if play.done is None:
                    self._resolve(play, Failure(InputError.ABORTED))
        if done.status == 0:
            entry.done, entry.done_ns = done, now
            entry.done_evt.set()
        elif done.status == 1:
            self._resolve(entry, Failure(InputError.NOTIFY_FAILED))
        elif done.status == 3 and entry.kind == _PLAY:
            self._resolve(entry, Failure(InputError.ABORTED))
        else:
            log.warning("seq=%d %s DONE with unexpected status %d: treated as ACK_TIMEOUT", done.seq, entry.kind,
                        done.status)
            self._fault(InputError.ACK_TIMEOUT)
            self._resolve(entry, Failure(InputError.ACK_TIMEOUT))

    def _log_pong(self, ns: int, pong: Pong) -> None:
        self._pongs.append((ns, pong))
        dropped = 0
        while len(self._pongs) > PONG_BUFFER_MAX:
            self._pongs.popleft()
            dropped += 1
        if dropped and not self._pong_overflow_warned:
            self._pong_overflow_warned = True
            log.warning("PONG log full (%d entries): dropping the oldest until take_pongs()", PONG_BUFFER_MAX)

    def _drain_pongs(self) -> list[tuple[int, Pong]]:
        out = list(self._pongs)
        self._pongs.clear()
        self._pong_overflow_warned = False
        return out

    def _on_disconnected(self, client) -> None:
        if not self._on_loop():
            self._loop.call_soon_threadsafe(self._on_disconnected, client)
            return
        if self._session is not None and client is self._session.client:
            self._end_session(InputError.LINK_LOST)
        else:
            log.debug("ignoring disconnect of a client that is not the live session's")

    # ------------------------------------------------------------ session

    def _fault(self, error: InputError) -> None:
        """Append to faults(): LINK_LOST (ends the session) or ACK_TIMEOUT (a missed ACCEPTED, DONE or PONG; A-P4-R-D1)."""
        self._faults.append((time.perf_counter_ns(), error))

    def _end_session(self, fault: InputError | None) -> None:
        """LINK_LOST (fault) or close() (None): stop the keepalive, fail every pending command, disconnect."""
        session = self._session
        if session is None:
            return
        self._session = None
        if fault is not None:
            self._fault(fault)
            log.warning("link fault: %s", fault)
        session.keepalive.cancel()
        for cm in list(session.writes):  # a write blocked on a vanished peer must not hold its command (P4 :1086)
            if not cm.expired():  # one whose own timeout has fired is already unwinding; reschedule() would raise
                cm.reschedule(self._loop.time())
        for entry in list(self._pending.values()):
            self._resolve(entry, Failure(fault or InputError.NOT_CONNECTED))
        self._disconnect_task = self._loop.create_task(self._disconnect(session.client))

    async def _disconnect(self, client) -> None:
        try:
            await client.disconnect()
        except Exception as e:  # noqa: BLE001 - logged and ignored by design (P4 session state)
            log.warning("disconnect failed: %r", e)

    async def _close(self) -> None:
        if self._session is None:
            return
        self._end_session(None)
        await self._disconnect_task

    async def _connect(self, timeout_s: float) -> Result[SessionInfo, str]:
        if self._session is not None:
            return Failure("already connected")
        if self._connecting:
            return Failure("connect already in progress")
        self._connecting = True
        try:
            return await self._connect_once(timeout_s)
        finally:
            self._connecting = False

    async def _connect_once(self, timeout_s: float) -> Result[SessionInfo, str]:
        if self._disconnect_task is not None:
            await self._disconnect_task
            self._disconnect_task = None
        loop = self._loop
        deadline = loop.time() + timeout_s

        def remaining() -> float:
            return deadline - loop.time()

        if remaining() <= 0:
            return Failure("timeout in scan")
        try:
            device = await self._scanner.find_device_by_name(self._name, timeout=min(SCAN_TIMEOUT_S, remaining()))
        except Exception as e:  # noqa: BLE001 - reported as the connect Failure
            return Failure(f"scan failed: {e!r}")
        if device is None:
            return Failure(f"device {self._name!r} not found in scan")
        if remaining() <= 0:
            return Failure("timeout in scan")
        client = self._client_factory(device, disconnected_callback=self._on_disconnected, timeout=remaining(),
                                      winrt={"use_cached_services": False})
        opened = await self._open(client, remaining)
        if not is_successful(opened):
            await self._disconnect(client)
            return opened
        pong = opened.unwrap()
        session = _Session(client, client.mtu_size, loop.time())
        self._session = session
        session.keepalive = loop.create_task(self._keepalive(session))
        return Success(SessionInfo(device.address, session.mtu, pong))

    async def _open(self, client, remaining) -> Result[Pong, str]:
        stages = (("connect", client.connect), ("start_notify", lambda: client.start_notify(NOTIFY_UUID, self._on_notify)))
        for stage, start in stages:
            if remaining() <= 0:
                return Failure(f"timeout in {stage}")
            try:
                await asyncio.wait_for(start(), remaining())
            except TimeoutError:
                return Failure(f"timeout in {stage}")
            except Exception as e:  # noqa: BLE001 - reported as the connect Failure
                return Failure(f"{stage} failed: {e!r}")
        return await self._ready_wait(client, remaining)

    async def _ready_wait(self, client, remaining) -> Result[Pong, str]:
        """PING every 100 ms until a PONG is bonded, encrypted, subscribed and has mtu >= 56 (PA-adj1-D1, PA-adj1c-D1)."""
        loop = self._loop
        ready_deadline = loop.time() + min(READY_TIMEOUT_S, remaining())
        last: Pong | None = None
        while (left := ready_deadline - loop.time()) > 0:
            tick_end = loop.time() + READY_PING_PERIOD_S
            r = await self._ping_cmd(client, min(PING_TIMEOUT_S, left))
            if is_successful(r):
                last = r.unwrap()
                if not _unmet(last):
                    return Success(last)
            await asyncio.sleep(max(0.0, min(tick_end, ready_deadline) - loop.time()))
        return Failure("session not ready: " + ("no PONG" if last is None else " ".join(_unmet(last))))

    async def _keepalive(self, session: _Session) -> None:
        """Tick k fires at session start + k x period by the clock; each tick's PING runs as its own task."""
        loop = self._loop
        k = 0
        while self._session is session:
            k = max(k + 1, int((loop.time() - session.start) / KEEPALIVE_PERIOD_S) + 1)
            await asyncio.sleep(max(0.0, session.start + k * KEEPALIVE_PERIOD_S - loop.time()))
            if self._session is not session:
                return
            task = loop.create_task(self._keepalive_ping(session))
            session.tasks.add(task)
            task.add_done_callback(session.tasks.discard)

    async def _keepalive_ping(self, session: _Session) -> None:
        r = await self._ping_cmd(session.client, PING_TIMEOUT_S)
        if not is_successful(r) and self._session is session:
            self._end_session(InputError.LINK_LOST)

    # ------------------------------------------------------------ commands

    async def _ping_cmd(self, client, limit: float) -> Result[Pong, InputError]:
        """Register, then one limit over write + PONG (PA-rev m11). A PONG may arrive before the write returns."""
        entry = self._register(_PING)
        entry.sent_ns = time.perf_counter_ns()
        try:
            async with asyncio.timeout(limit):
                await client.write_gatt_char(CMD_UUID, encode_ping(entry.seq), response=True)
                await asyncio.shield(entry.final)
        except TimeoutError:
            self._resolve(entry, Failure(InputError.ACK_TIMEOUT))
        except Exception as e:  # noqa: BLE001 - a failed write is this PING's result
            log.debug("PING seq=%d write failed: %r", entry.seq, e)
            self._resolve(entry, Failure(InputError.WRITE_FAILED))
        return entry.final.result()

    async def _public_ping(self) -> Result[Pong, InputError]:
        """A failure is returned, never ends the session (A-P4-D2); a missed PONG is an ACK_TIMEOUT fault (A-P4-R I1)."""
        session = self._session
        if session is None:
            return Failure(InputError.NOT_CONNECTED)
        r = await self._ping_cmd(session.client, PING_TIMEOUT_S)
        if self._session is session and not is_successful(r) and r.failure() == InputError.ACK_TIMEOUT:
            self._fault(InputError.ACK_TIMEOUT)
        return r

    async def _write(self, entry: _Pending, session: _Session, data: bytes) -> bool:
        """Write with response inside WRITE_TIMEOUT_S, cut short when the session ends (A-P4-R I2); kept inline, so it
        has started when write_started is set. False if the command is already resolved or the write failed."""
        try:
            async with asyncio.timeout(WRITE_TIMEOUT_S) as cm:
                session.writes.add(cm)
                try:
                    await session.client.write_gatt_char(CMD_UUID, data, response=True)
                finally:
                    session.writes.discard(cm)
        except Exception as e:  # noqa: BLE001 - TimeoutError included: both are WRITE_FAILED
            log.debug("%s seq=%d write failed: %r", entry.kind, entry.seq, e)
            self._resolve(entry, Failure(InputError.WRITE_FAILED))  # no-op if a fault or answer resolved it first
            return False
        entry.written_ns = time.perf_counter_ns()
        return not entry.final.done()

    async def _await_stage(self, entry: _Pending, evt: asyncio.Event, timeout: float) -> bool:
        await self._wait_evt(entry, evt, timeout)
        if entry.final.done():
            return False
        if not evt.is_set():
            self._fault(InputError.ACK_TIMEOUT)
            self._resolve(entry, Failure(InputError.ACK_TIMEOUT))
            return False
        return True

    async def _play(self, steps: Sequence[Step]) -> Result[SendReport, InputError]:
        if self._session is None:
            return Failure(InputError.NOT_CONNECTED)
        async with self._play_lock:
            session = self._session
            if session is None:
                return Failure(InputError.NOT_CONNECTED)
            deltas = [s.delta_100us for s in steps]
            if (len(steps) > MAX_STEPS or sum(deltas) > MAX_TOTAL_DELTA or any(d > MAX_STEP_DELTA for d in deltas)
                    or 5 + 3 * len(steps) > session.mtu - 3):
                return Failure(InputError.TOO_LONG)
            seq = self._next_seq()
            encoded = encode_play(seq, steps)
            if not is_successful(encoded):
                log.error("PLAY not sent, schedule breaks a C1 shape rule: %s", encoded.failure())
                return Failure(InputError.REJECTED)
            entry = _Pending(_PLAY, seq, self._loop.create_future())
            self._pending[seq] = entry
            await self._before_write(seq)
            if entry.final.done():  # aborted by release_all() or failed by close()/LINK_LOST: never written
                self._resolve(entry, entry.final.result())
                return entry.final.result()
            entry.sent_ns = time.perf_counter_ns()
            entry.write_started = True
            if (await self._write(entry, session, encoded.unwrap())
                    and await self._await_stage(entry, entry.accepted_evt, ACCEPT_TIMEOUT_S)
                    and await self._await_stage(entry, entry.done_evt, sum(deltas) / 10_000 + DONE_MARGIN_S)):
                self._resolve(entry, Success(SendReport(seq, entry.sent_ns, entry.written_ns, entry.accepted_ns,
                                                        entry.done_ns, entry.accepted, entry.done)))
            return entry.final.result()

    async def _release_all(self) -> Result[SendReport, InputError]:
        """Pre-empts (PA-adj2-D1): never takes the play lock."""
        session = self._session
        if session is None:
            return Failure(InputError.NOT_CONNECTED)
        entry = self._register(_RELEASE)
        for other in list(self._pending.values()):
            if other.kind != _PLAY:
                continue
            if other.write_started:
                entry.covers.append(other)
            else:  # registered, parked before its write: it is never written
                self._resolve(other, Failure(InputError.ABORTED))
        entry.sent_ns = time.perf_counter_ns()
        entry.write_started = True
        if (await self._write(entry, session, encode_release_all(entry.seq))
                and await self._await_stage(entry, entry.done_evt, DONE_MARGIN_S)):
            self._resolve(entry, Success(SendReport(entry.seq, entry.sent_ns, entry.written_ns, None, entry.done_ns,
                                                    None, entry.done)))
        return entry.final.result()
