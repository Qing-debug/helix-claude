"""Tests for src.route.walker.RouteWalker.run and stop_now (plan task B13): the rotation in laps and the stop path.

The first nine tests are the plan's; the rest pin the choices the plan leaves open (the empty-rotation return path,
the single stop guard, a sink that fails on laps as well as legs). Tests that stop a run from another thread use
wall time (`RealClock`), with a leg timeout or a bounded wait as a safety net so a regression fails rather than hangs.
"""

import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
import pytest
from returns.pipeline import is_successful
from returns.result import Failure, Success

from src.classes.classes import Kanna
from src.controller.controller import MapleAgent
from src.input import input_layer
from src.input.input_layer import InputLayer
from src.input.link import InputError
from src.input.timing import TimingModel
from src.route.leg_log import LegOutcome, MemorySink
from src.route.params import MovementParams
from src.route.route_graph import RouteGraph
from src.route.sim.link import SimClock, SimLink, SimPositionSource
from src.route.sim.world import NoiseConfig, SimParams, SimWorld, load_terrain
from src.route.walker import RealClock, RouteWalker, RunSummary, WalkerConfig

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
CSV = FIXTURES / "fossil_foundry_2.csv"
TERRAIN = FIXTURES / "ff2_terrain.json"

FLOOR_RIGHT = (113, 60)  # rotation[0]
FLOOR_MID = (82, 60)
TOP_LEFT = (49, 28)
TOP_RIGHT = (101, 24)
GUARD_THREAD = "route-stop-guard"

# Virtual-time guard for the sim runs that should finish: a leg that never arrives ends TIMED_OUT and fails the
# assertions, rather than looping forever on the SimClock.
SIM = WalkerConfig(use_stimulus=False, leg_timeout_s=120.0, max_laps=3)


@dataclass
class FF2:
    rg: RouteGraph
    terrain: object
    rotation: list


@pytest.fixture(scope="module")
def ff2():
    agent = MapleAgent(characterClass=Kanna())
    assert is_successful(agent.loadMap(CSV))
    rg = RouteGraph.build(agent.pathGraph, MovementParams())
    assert is_successful(rg)
    terrain = load_terrain(TERRAIN)
    assert is_successful(terrain)
    return FF2(rg.unwrap(), terrain.unwrap(), list(agent.map_rotation_order))


def _world(ff2, start, noise=None):
    noise = NoiseConfig.off() if noise is None else noise
    params = SimParams()
    return SimWorld(ff2.terrain, ff2.rg.reach_h, ff2.rg.reach_v, params, noise, np.random.default_rng(1), start)


def _walker(ff2, layer, positions, clock, config, sink, rotation=None):
    rotation = ff2.rotation if rotation is None else rotation
    return RouteWalker(ff2.rg, rotation, layer, positions, clock, sink, np.random.default_rng(0), config)


class Releases:
    """Counts the walker's calls to `layer.release_all` (run, stop_now, the guard), by wall time, and delegates."""

    def __init__(self, layer):
        self.times = []  # list.append is atomic, so the guard thread can record into it
        real = layer.release_all

        def counting():
            self.times.append(time.perf_counter())
            return real()

        layer.release_all = counting

    @property
    def count(self):
        return len(self.times)


@dataclass
class SimRig:
    walker: RouteWalker
    layer: InputLayer
    world: SimWorld
    clock: SimClock
    link: SimLink
    sink: object
    releases: Releases


def _sim(ff2, start, config=SIM, noise=None, sink=None, wrap_link=None, wrap_positions=None, rotation=None):
    """The plan's standard sim rig (as B12): SimWorld + SimLink + SimPositionSource (miss_p 0) under one SimClock, a
    real InputLayer, a MemorySink. `wrap_link(sim_link)` / `wrap_positions(source)` build replacements."""
    world = _world(ff2, start, noise)
    clock = SimClock()
    sim_link = SimLink(world, clock, SimParams())
    link = sim_link if wrap_link is None else wrap_link(sim_link)
    positions = SimPositionSource(world, clock, miss_p=0, rng=np.random.default_rng(2))
    if wrap_positions is not None:
        positions = wrap_positions(positions)
    layer = InputLayer(link, TimingModel(np.random.default_rng(3)))
    releases = Releases(layer)
    sink = MemorySink() if sink is None else sink
    walker = _walker(ff2, layer, positions, clock, config, sink, rotation)
    return SimRig(walker, layer, world, clock, sim_link, sink, releases)


class BlockingLink:
    """`play` blocks until a `release_all` that comes after it started, then returns Failure(ABORTED), as a pre-empted
    BleLink play does. Records the wall time of every play and release_all."""

    def __init__(self):
        self._released = threading.Event()
        self.play_times = []
        self.release_times = []

    def play(self, steps):
        self._released.clear()
        self.play_times.append(time.perf_counter())
        self._released.wait(timeout=5.0)  # bounded, so a stop that never lands cannot hang the suite
        return Failure(InputError.ABORTED)

    def release_all(self):
        self.release_times.append(time.perf_counter())
        self._released.set()
        return Success(None)


class LinkLostOnPlay5:
    """Wraps a SimLink: when 4 plays have happened it pushes LINK_LOST onto `fail_next`, so play 5 fails (B10's
    `fail_next` fails only the next play); `release_all` is delegated."""

    def __init__(self, sim_link):
        self._sim = sim_link

    def play(self, steps):
        if self._sim.plays == 4:
            self._sim.fail_next.append(InputError.LINK_LOST)
        return self._sim.play(steps)

    def release_all(self):
        return self._sim.release_all()


def _real_time_rig(ff2, config, link):
    """A walker on RealClock over a link that never moves the world; positions from a SimWorld at (113, 60) read at
    a SimClock that nothing advances (as B13 tests 2 and 3)."""
    world = _world(ff2, FLOOR_RIGHT)
    positions = SimPositionSource(world, SimClock(), miss_p=0, rng=np.random.default_rng(2))
    layer = InputLayer(link, TimingModel(np.random.default_rng(3)))
    releases = Releases(layer)
    sink = MemorySink()
    return _walker(ff2, layer, positions, RealClock(), config, sink), sink, releases


def _guard_threads():
    return [t for t in threading.enumerate() if t.name == GUARD_THREAD and t.is_alive()]


# --- the plan's tests -------------------------------------------------------------------------------------


def test_1_three_laps_of_the_rotation(ff2):
    rig = _sim(ff2, FLOOR_RIGHT)

    result = rig.walker.run()

    assert result == Success(RunSummary(3, 22, "max_laps"))
    legs = rig.sink.legs
    assert len(legs) == 22
    assert [(r.lap, r.leg) for r in legs] == [(0, 0)] + [(lap, i) for lap in (1, 2, 3) for i in range(7)]
    assert all(r.outcome == LegOutcome.REACHED for r in legs)
    laps = rig.sink.laps
    assert [r.lap for r in laps] == [1, 2, 3]
    assert all(r.legs == 7 and r.all_reached and r.end_ns > r.start_ns for r in laps)
    approach = legs[0]
    assert approach.src is None and approach.dst == FLOOR_RIGHT
    (leg,) = [r for r in legs if (r.lap, r.leg) == (2, 4)]
    assert leg.src == TOP_LEFT and leg.dst == TOP_RIGHT


def test_2_stop_from_another_thread_cuts_a_play_in_flight(ff2):
    link = BlockingLink()
    config = WalkerConfig(use_stimulus=False, leg_timeout_s=10.0, max_laps=1)
    walker, sink, releases = _real_time_rig(ff2, config, link)
    timer = threading.Timer(0.2, walker.stop_now)

    timer.start()
    t0 = time.perf_counter()
    result = walker.run()
    elapsed = time.perf_counter() - t0
    timer.join()

    assert elapsed < 1.0
    assert result == Success(RunSummary(0, 2, "stopped"))
    assert sink.legs[-1].outcome == LegOutcome.STOPPED
    assert link.play_times, "the run never started a play, so the stop did not land in one"
    assert releases.count >= 2


def test_3_stop_during_the_reaction_wait(ff2, monkeypatch):
    link = BlockingLink()
    config = WalkerConfig(use_stimulus=True, leg_timeout_s=10.0, max_laps=1)
    walker, sink, releases = _real_time_rig(ff2, config, link)
    real_sleep = time.sleep
    waits = []  # wall time each reaction wait began
    stop_times = []

    def stop():
        stop_times.append(time.perf_counter())
        walker.stop_now()

    def blocking_sleep(seconds):
        waits.append(time.perf_counter())
        if len(waits) == 1:
            threading.Timer(0.1, stop).start()
        real_sleep(0.5)

    monkeypatch.setattr(input_layer, "_sleep", blocking_sleep)

    t0 = time.perf_counter()
    result = walker.run()
    elapsed = time.perf_counter() - t0

    assert waits, "no reaction wait happened"
    assert elapsed < 1.0
    assert result == Success(RunSummary(0, 2, "stopped"))
    assert sink.legs[-1].outcome == LegOutcome.STOPPED
    assert stop_times, "stop_now was never called"
    # The stop landed inside the wait, so the play started after it: a release_all must follow within 0.1 s.
    started_after_stop = [t for t in link.play_times if t > stop_times[0]]
    assert started_after_stop
    for started in started_after_stop:
        assert any(started <= r <= started + 0.1 for r in releases.times), (started, releases.times)
    count = releases.count
    real_sleep(0.2)
    assert releases.count == count, "release_all was called after run returned"
    assert _guard_threads() == []


def test_4_link_lost_ends_the_run_as_link_down(ff2):
    rig = _sim(ff2, FLOOR_RIGHT, wrap_link=LinkLostOnPlay5)

    result = rig.walker.run()

    assert is_successful(result)
    assert result.unwrap().reason == "link_down"
    assert rig.link.plays == 5
    assert rig.sink.legs[-1].outcome == LegOutcome.LINK_DOWN
    assert rig.sink.laps == []


def test_5_an_empty_rotation_is_a_failure(ff2):
    rig = _sim(ff2, FLOOR_RIGHT, rotation=[])

    result = rig.walker.run()

    assert not is_successful(result)
    assert isinstance(result.failure(), str)
    assert rig.sink.legs == [] and rig.sink.laps == []


def test_6_a_failing_sink_is_printed_once_and_does_not_stop_the_walk(ff2, capsys):
    class DiskFullSink:
        def __init__(self):
            self.leg_calls = 0
            self.laps = []

        def write_leg(self, rec):
            self.leg_calls += 1
            return Failure("disk full")

        def write_lap(self, rec):
            self.laps.append(rec)
            return Success(None)

    sink = DiskFullSink()
    rig = _sim(ff2, FLOOR_RIGHT, config=replace(SIM, max_laps=1), sink=sink)

    result = rig.walker.run()

    assert result == Success(RunSummary(1, 8, "max_laps"))
    assert sink.leg_calls == 8
    assert len(sink.laps) == 1
    out, err = capsys.readouterr()
    assert (out + err).count("disk full") == 1


def test_7_a_timed_out_leg_does_not_end_the_run(ff2):
    config = WalkerConfig(use_stimulus=False, leg_timeout_s=3.0, max_laps=1)
    rig = _sim(ff2, FLOOR_RIGHT, config=config, noise=replace(NoiseConfig.off(), no_op_p=1))

    result = rig.walker.run()

    assert result == Success(RunSummary(1, 8, "max_laps"))
    legs = rig.sink.legs
    assert (legs[0].lap, legs[0].leg, legs[0].outcome) == (0, 0, LegOutcome.REACHED)
    assert [r.outcome for r in legs[1:7]] == [LegOutcome.TIMED_OUT] * 6
    assert (legs[7].lap, legs[7].leg, legs[7].outcome) == (1, 6, LegOutcome.REACHED)
    assert (legs[7].src, legs[7].dst) == ((105, 42), FLOOR_RIGHT)
    (lap,) = rig.sink.laps
    assert lap.legs == 7 and lap.all_reached is False


def test_8_release_all_counts(ff2):
    done = _sim(ff2, FLOOR_RIGHT, config=replace(SIM, max_laps=1))
    assert is_successful(done.walker.run())
    assert done.releases.count == 1

    down = _sim(ff2, FLOOR_RIGHT, wrap_link=LinkLostOnPlay5)
    assert down.walker.run().unwrap().reason == "link_down"
    assert down.releases.count == 1

    link = BlockingLink()
    config = WalkerConfig(use_stimulus=False, leg_timeout_s=10.0, max_laps=1)
    walker, _, releases = _real_time_rig(ff2, config, link)
    timer = threading.Timer(0.2, walker.stop_now)
    timer.start()
    assert walker.run().unwrap().reason == "stopped"
    timer.join()
    count = releases.count
    assert count >= 2
    time.sleep(0.2)
    assert releases.count == count


def test_9_the_stop_flag_is_re_checked_before_execute(ff2):
    holder = []

    class StopOnFirstRead:
        def __init__(self, inner):
            self._inner = inner
            self._first = True

        def read(self):
            if self._first:
                self._first = False
                holder[0].stop_now()
            return self._inner.read()

    rig = _sim(ff2, FLOOR_MID, wrap_positions=StopOnFirstRead)
    holder.append(rig.walker)

    result = rig.walker.run()

    assert result == Success(RunSummary(0, 1, "stopped"))
    (leg,) = rig.sink.legs
    assert leg.outcome == LegOutcome.STOPPED
    assert leg.moves == ()
    assert rig.link.plays == 0
    assert rig.sink.laps == []


# --- choices the plan leaves open -------------------------------------------------------------------------


def test_the_empty_rotation_return_path_also_releases_once(ff2):
    rig = _sim(ff2, FLOOR_RIGHT, rotation=[])

    assert not is_successful(rig.walker.run())

    assert rig.releases.count == 1


def test_a_stop_before_run_ends_the_run_at_the_approach_leg(ff2):
    rig = _sim(ff2, FLOOR_MID)

    rig.walker.stop_now()
    result = rig.walker.run()

    assert result == Success(RunSummary(0, 1, "stopped"))
    (leg,) = rig.sink.legs
    assert leg.outcome == LegOutcome.STOPPED and leg.moves == ()
    assert rig.link.plays == 0
    assert rig.releases.count >= 2  # stop_now, then the end of run
    assert _guard_threads() == []


def test_stop_now_twice_starts_one_guard_and_run_joins_it(ff2):
    rig = _sim(ff2, FLOOR_MID)

    rig.walker.stop_now()
    rig.walker.stop_now()
    assert len(_guard_threads()) == 1
    rig.walker.run()

    assert _guard_threads() == []


def test_stop_now_after_run_finished_starts_no_guard(ff2):
    rig = _sim(ff2, FLOOR_RIGHT, config=replace(SIM, max_laps=1))
    assert is_successful(rig.walker.run())

    rig.walker.stop_now()

    assert _guard_threads() == []


def test_a_sink_that_fails_on_laps_too_is_still_printed_once_per_run(ff2, capsys):
    class FailingSink:
        def write_leg(self, rec):
            return Failure("leg write failed")

        def write_lap(self, rec):
            return Failure("lap write failed")

    rig = _sim(ff2, FLOOR_RIGHT, config=replace(SIM, max_laps=2), sink=FailingSink())

    assert rig.walker.run() == Success(RunSummary(2, 15, "max_laps"))

    out, err = capsys.readouterr()
    assert len((out + err).strip().splitlines()) == 1


def test_a_timed_out_approach_leg_moves_on_to_the_laps(ff2):
    config = WalkerConfig(use_stimulus=False, leg_timeout_s=3.0, max_laps=1)
    rig = _sim(ff2, FLOOR_MID, config=config, noise=replace(NoiseConfig.off(), no_op_p=1))

    result = rig.walker.run()

    assert result == Success(RunSummary(1, 8, "max_laps"))
    assert rig.sink.legs[0].outcome == LegOutcome.TIMED_OUT
    assert [r.lap for r in rig.sink.legs] == [0] + [1] * 7
