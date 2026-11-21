"""WALKFIX Task 7 (QB283 2a), the real-thread side: `MinimapPositionSource.wait_new` over a holder the capture thread's
`LostTracker.publish` writes, and the session handing the walker that wait. The walker's own use of it, on a fake clock,
is `test_walker_wake.py`.

These tests use real threads and real time, with generous bounds: a publisher thread publishes after 30 ms and the wait is
allowed to take up to 0.5 s, so a missing notify (a wait that ran out its 1.0 s) fails them, and a slow machine does not.
"""

import threading
import time

import numpy as np
from returns.result import Failure, Success
from test_session import _no_real_blelink, env  # noqa: F401, F811  (the session tests' fixtures: `env` runs a RunSession)

from src.route import runtime
from src.route.position import MinimapPositionSource, foot_from_dot
from src.route.walker import STALE, RouteWalker, RunSummary
from src.vision.minimap_follow import LostTracker


class _Locator:
    def locate(self, minimap_rgb):
        return Success((40, 50))


def _frame(n: int) -> np.ndarray:
    return np.full((4, 6, 3), n, np.uint8)


def _rig():
    """A holder the way the app's capture thread writes it, and a position source over it that has read frame 1."""
    shared = runtime.SharedFrame()
    tracker = LostTracker(shared, lambda _line: None)   # the real writer: it is what notifies
    source = MinimapPositionSource(shared, _Locator())
    tracker.publish(_frame(1), 10)
    assert source.read() == Success(foot_from_dot((40, 50)))
    assert source.read() == Failure(STALE)
    return shared, tracker, source


def test_wait_new_returns_when_a_picture_is_published():
    _, tracker, source = _rig()
    publisher = threading.Timer(0.03, tracker.publish, (_frame(2), 20))
    publisher.start()
    started = time.perf_counter()
    source.wait_new(1.0)
    waited = time.perf_counter() - started
    publisher.join()
    assert waited < 0.5   # it woke at the publish, not after the whole second (the timer fires a little early or late)
    assert source.read() == Success(foot_from_dot((40, 50)))   # and the next read is not STALE
    assert source.frame_ns == 20


def test_wait_new_times_out_without_a_picture():
    _, _, source = _rig()
    started = time.perf_counter()
    source.wait_new(0.05)
    waited = time.perf_counter() - started
    # `Condition.wait_for(.., 0.05)` took about 60 ms on this PC (review m10, the Windows timer); harmless, since no timer
    # of the walker counts waits
    assert 0.04 <= waited < 0.5
    assert source.read() == Failure(STALE)


def test_wait_new_returns_at_once_when_a_newer_frame_is_there():
    _, tracker, source = _rig()
    tracker.publish(_frame(2), 20)   # a picture the source has not read yet
    started = time.perf_counter()
    source.wait_new(5.0)
    assert time.perf_counter() - started < 0.5
    assert source.read() == Success(foot_from_dot((40, 50)))


def test_wait_new_does_not_return_for_the_frame_it_already_read():
    """A publish of the same object, or none, is not new: only a different frame object ends the wait."""
    shared, _, source = _rig()
    started = time.perf_counter()
    with shared.minimap_frame_new:   # a notify with nothing new (a spurious wake-up)
        shared.minimap_frame_new.notify_all()
    source.wait_new(0.05)
    assert time.perf_counter() - started >= 0.04


def test_session_passes_the_picture_wait(env, monkeypatch):  # noqa: F811
    """With a `MinimapPositionSource` the walker's `wait_frame` is that source's `wait_new`; with the simulator's source
    (the fixture's own) it is None, so the sim run and `tools/route_tab_sim.py` walk as before."""
    seen: list[object] = []

    def run(self):
        seen.append(self._wait_frame)
        return Success(RunSummary(0, 0, "max_laps"))

    monkeypatch.setattr(RouteWalker, "run", run)
    assert env.run() == 0
    assert seen == [None]   # the fixture's source is a SimPositionSource

    env.positions = MinimapPositionSource(env.shared, _Locator())
    seen.clear()
    assert env.run() == 0
    assert len(seen) == 1 and seen[0] == env.positions.wait_new   # the bound method of the very source the walker reads


def test_every_holder_has_the_condition_on_its_own_lock():
    """The condition is on the holder's own lock, so `with minimap_frame_lock:` readers and the notify share one lock.
    `SharedInfo` (src/main.py) pulls in the GUI, so its class is compiled alone from the source; its two sibling holders
    are built as they are."""
    import ast
    from pathlib import Path

    from src.route.harness import record_minimap

    tree = ast.parse((Path(runtime.__file__).parents[1] / "main.py").read_text(encoding="utf-8"))
    shared_info = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "SharedInfo")
    namespace: dict[str, object] = {"threading": threading}
    exec(compile(ast.Module([shared_info], []), "src/main.py:SharedInfo", "exec"), namespace)

    for holder in (namespace["SharedInfo"](), runtime.SharedFrame(), record_minimap._Shared()):  # type: ignore[operator]
        with holder.minimap_frame_lock:
            holder.minimap_frame_new.notify_all()   # raises RuntimeError unless the condition's lock is the one held
        assert isinstance(holder.minimap_frame_new, threading.Condition)
