"""Plan task B19: the runtime stop control and capture helpers (`src/route/runtime.py`) and the walk CLI.

No test here installs a real hook except the Windows-only one marked below, and none sends a key."""

import signal
import sys
import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest
from returns.pipeline import is_successful
from returns.result import Failure, Success

from src.route import runtime
from src.route.runtime import StopControl


class Remove:
    """The `remove()` callable a fake `_install_hook` returns; counts its calls."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self) -> None:
        self.calls += 1


@pytest.fixture
def hook(monkeypatch):
    """Replace the real hook with a recording fake; yields its `Remove`."""
    remove = Remove()
    monkeypatch.setattr(StopControl, "_install_hook", lambda self: remove)
    return remove


@pytest.fixture
def saved_sigint():
    """Safety net on top of what close() does: restore the SIGINT handler the test started with."""
    before = signal.getsignal(signal.SIGINT)
    try:
        yield before
    finally:
        signal.signal(signal.SIGINT, before)  # pyright: ignore[reportArgumentType]


def _worker_alive() -> bool:
    return any(t.name == "stop-control" and t.is_alive() for t in threading.enumerate())


def test_trigger_returns_at_once_while_on_stop_blocks(hook, saved_sigint):
    entered = threading.Event()

    def on_stop() -> None:
        entered.set()
        time.sleep(1.0)

    stop = StopControl(on_stop)
    assert is_successful(stop.start())
    try:
        t0 = time.perf_counter()
        stop.trigger()
        elapsed = time.perf_counter() - t0
        assert elapsed < 0.010
        assert entered.wait(1.0)
    finally:
        stop.close()


def test_trigger_twice_calls_on_stop_once(hook, saved_sigint):
    calls: list[int] = []
    done = threading.Event()

    def on_stop() -> None:
        calls.append(1)
        done.set()

    stop = StopControl(on_stop)
    assert is_successful(stop.start())
    try:
        stop.trigger()
        stop.trigger()
        assert done.wait(1.0)
    finally:
        stop.close()
    assert calls == [1]


def test_first_trigger_keeps_its_source_and_time(hook, saved_sigint):
    stop = StopControl(lambda: None)
    stop.trigger("sigint")
    first_ns = stop.trigger_ns
    stop.trigger()
    assert stop.trigger_source == "sigint"
    assert first_ns is not None and stop.trigger_ns == first_ns


def test_sigint_handler_triggers_on_stop_once(hook, saved_sigint):
    ran = threading.Event()
    calls: list[int] = []

    def on_stop() -> None:
        calls.append(1)
        ran.set()

    stop = StopControl(on_stop)
    assert is_successful(stop.start())
    try:
        handler = signal.getsignal(signal.SIGINT)
        assert callable(handler) and handler is not saved_sigint
        handler(signal.SIGINT, None)
        assert stop.trigger_source == "sigint"  # set synchronously
        assert ran.wait(1.0)
    finally:
        stop.close()
    assert calls == [1]


def test_close_without_trigger_undoes_start(hook, saved_sigint):
    calls: list[int] = []
    stop = StopControl(lambda: calls.append(1))
    assert is_successful(stop.start())
    assert _worker_alive()
    stop.close()
    assert calls == []
    assert not _worker_alive()
    assert hook.calls == 1
    assert signal.getsignal(signal.SIGINT) is saved_sigint
    stop.close()
    assert hook.calls == 1


def test_close_before_start_does_nothing(hook, saved_sigint):
    stop = StopControl(lambda: None)
    stop.close()
    assert signal.getsignal(signal.SIGINT) is saved_sigint
    assert hook.calls == 0


def test_close_keeps_a_handler_installed_after_start(hook, saved_sigint):
    def x(signum, frame) -> None:
        pass

    stop = StopControl(lambda: None)
    assert is_successful(stop.start())
    signal.signal(signal.SIGINT, x)
    stop.close()
    assert signal.getsignal(signal.SIGINT) is x


def test_second_close_keeps_a_handler_installed_after_the_first(hook, saved_sigint):
    def x(signum, frame) -> None:
        pass

    stop = StopControl(lambda: None)
    assert is_successful(stop.start())
    stop.close()
    signal.signal(signal.SIGINT, x)
    stop.close()
    assert signal.getsignal(signal.SIGINT) is x


def test_close_off_the_main_thread_ends_worker_and_hook_but_leaves_sigint(hook, saved_sigint):
    stop = StopControl(lambda: None)
    assert is_successful(stop.start())
    installed = signal.getsignal(signal.SIGINT)
    errors: list[BaseException] = []

    def close() -> None:
        try:
            stop.close()
        except BaseException as exc:
            errors.append(exc)

    th = threading.Thread(target=close)
    th.start()
    th.join(10)
    assert errors == []
    assert signal.getsignal(signal.SIGINT) is installed
    assert not _worker_alive()
    assert hook.calls == 1


def test_previous_handler_none_is_restored_as_default_int_handler(hook, saved_sigint, monkeypatch):
    real_getsignal = signal.getsignal
    real_signal = signal.signal

    def s(signum, frame) -> None:  # the sentinel that stands for "a handler installed from C"
        pass

    real_signal(signal.SIGINT, s)
    try:
        def getsignal(signum):
            value = real_getsignal(signum)
            return None if value is s else value

        def set_signal(signum, handler):
            previous = real_signal(signum, handler)
            return None if previous is s else previous

        monkeypatch.setattr(signal, "getsignal", getsignal)
        monkeypatch.setattr(signal, "signal", set_signal)
        stop = StopControl(lambda: None)
        assert is_successful(stop.start())
        stop.close()
        monkeypatch.undo()
        assert real_getsignal(signal.SIGINT) is signal.default_int_handler
    finally:
        real_signal(signal.SIGINT, saved_sigint)


def test_start_fails_when_the_hook_cannot_be_installed(monkeypatch, saved_sigint):
    def failing_install(self):
        raise OSError("SetWindowsHookExW failed")

    monkeypatch.setattr(StopControl, "_install_hook", failing_install)
    before = {t.ident for t in threading.enumerate()}
    stop = StopControl(lambda: None)
    result = stop.start()
    assert not is_successful(result)
    assert "SetWindowsHookExW failed" in result.failure()
    assert result.failure().startswith("hook: ")
    assert signal.getsignal(signal.SIGINT) is saved_sigint
    assert {t.ident for t in threading.enumerate()} <= before
    stop.close()  # nothing was installed, so there is no remove() to call; it must not raise


def test_start_off_the_main_thread_fails_and_close_removes_the_hook(hook, saved_sigint):
    stop = StopControl(lambda: None)
    results: list[object] = []
    th = threading.Thread(target=lambda: results.append(stop.start()))
    th.start()
    th.join(10)
    assert len(results) == 1
    result = results[0]
    assert isinstance(result, Failure) and str(result.failure()).startswith("sigint: ")
    assert signal.getsignal(signal.SIGINT) is saved_sigint
    assert not _worker_alive()
    stop.close()
    assert hook.calls == 1


def test_trigger_before_start_calls_on_stop_once_started(hook, saved_sigint):
    ran = threading.Event()
    stop = StopControl(ran.set)
    stop.trigger("maple_check")
    assert is_successful(stop.start())
    try:
        assert ran.wait(1.0)
    finally:
        stop.close()


def _shared(frame=None) -> SimpleNamespace:
    return SimpleNamespace(minimap_frame_lock=threading.Lock(), minimap_frame=frame)


def test_wait_first_frame_succeeds_once_a_frame_arrives():
    shared = _shared()

    def later() -> None:
        time.sleep(0.1)
        with shared.minimap_frame_lock:
            shared.minimap_frame = np.zeros((4, 4, 3), np.uint8)

    threading.Thread(target=later, daemon=True).start()
    assert runtime.wait_first_frame(shared, 5.0) == Success(None)


def test_wait_first_frame_times_out():
    t0 = time.monotonic()
    result = runtime.wait_first_frame(_shared(), 0.1)
    assert not is_successful(result)
    assert time.monotonic() - t0 < 2.0


@pytest.mark.skipif(sys.platform != "win32", reason="installs a real WH_KEYBOARD_LL hook; sends no key")
def test_real_hook_installs_and_removes():
    remove = runtime._install_keyboard_hook(0x7B, lambda: None)
    assert any(t.name == "stop-hook" and t.is_alive() for t in threading.enumerate())
    remove()
    assert not any(t.name == "stop-hook" and t.is_alive() for t in threading.enumerate())
