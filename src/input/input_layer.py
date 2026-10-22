"""InputLayer: the facade Features B-D call (plan A task P5; contract C3).

Each action method builds its Plan with `src.input.actions`, optionally waits out a human reaction time measured
from the caller's stimulus, and plays the Plan on the link. `release_all()` goes straight to the link: no timing,
no draw, no wait, and there is no lock here, so a `release_all()` from another thread never waits for an action in
flight (PA-adj2-D1). One thread draws from the model at a time (C3 `TimingModel.draws`).
"""
import time
from collections.abc import Sequence
from dataclasses import dataclass

from returns.pipeline import is_successful
from returns.result import Failure, Result, Success

from src.input import actions
from src.input.actions import Plan
from src.input.link import BleLink, InputError, SendReport
from src.input.protocol import Key
from src.input.timing import Sample, TimingModel, TimingType

# Seams the tests monkeypatch (A-PF m6); _clock is the stimulus clock callers use (perf_counter_ns).
_clock = time.perf_counter_ns
_sleep = time.sleep

# play() failures after which the PC cannot know whether a key is down (PA-adj1b-D1). The others need no release:
# REJECTED / TOO_LONG pressed nothing, ABORTED means a release already ran, LINK_LOST / NOT_CONNECTED have no link.
_UNCERTAIN = frozenset({InputError.NOTIFY_FAILED, InputError.ACK_TIMEOUT, InputError.WRITE_FAILED})


@dataclass(frozen=True)
class ActionRecord:
    plan: Plan
    reaction: Sample | None  # REACTION draw; None without a stimulus
    waited_ms: float  # max(0, R - elapsed since the stimulus); 0.0 without a stimulus
    report: SendReport


class InputLayer:
    def __init__(self, link: BleLink, model: TimingModel) -> None:
        self._link = link
        self._model = model

    def tap(self, key: Key, stimulus_ns: int | None = None) -> Result[ActionRecord, InputError]:
        return self._act(actions.tap(self._model, key), stimulus_ns)

    def walk(self, direction: Key, intended_ms: float, stimulus_ns: int | None = None) -> Result[ActionRecord, InputError]:
        # Any builder Failure (per-step cap, or an unusable intended_ms) is TOO_LONG, before any wait (C3).
        return self._act_checked(actions.walk(self._model, direction, intended_ms), stimulus_ns)

    def teleport(self, direction: Key, stimulus_ns: int | None = None) -> Result[ActionRecord, InputError]:
        return self._act(actions.teleport(self._model, direction), stimulus_ns)

    def drop(self, stimulus_ns: int | None = None) -> Result[ActionRecord, InputError]:
        return self._act(actions.drop(self._model), stimulus_ns)

    def jump(self, stimulus_ns: int | None = None) -> Result[ActionRecord, InputError]:
        return self._act(actions.jump(self._model), stimulus_ns)

    def sequence(self, keys: Sequence[Key], stimulus_ns: int | None = None) -> Result[ActionRecord, InputError]:
        # More than 8 keys (or no key) is a builder Failure -> TOO_LONG, before any wait (C3, PA-rev m2).
        return self._act_checked(actions.sequence(self._model, keys), stimulus_ns)

    def release_all(self) -> Result[SendReport, InputError]:
        return self._link.release_all()

    def _act_checked(self, built: Result[Plan, str], stimulus_ns: int | None) -> Result[ActionRecord, InputError]:
        if not is_successful(built):
            return Failure(InputError.TOO_LONG)
        return self._act(built.unwrap(), stimulus_ns)

    def _act(self, plan: Plan, stimulus_ns: int | None) -> Result[ActionRecord, InputError]:
        reaction, waited_ms = None, 0.0
        if stimulus_ns is not None:
            # Reaction is PC-only: wait = max(0, R - elapsed) before sending; the Sample logs R (C4).
            r_ms = float(self._model.reaction())
            reaction = Sample(TimingType.REACTION, r_ms, self._model.draws - 1, None, None, None)
            waited_ms = max(0.0, r_ms - (_clock() - stimulus_ns) / 1e6)
            if waited_ms > 0:
                _sleep(waited_ms / 1000)
        return self._play(plan, reaction, waited_ms)

    def _play(self, plan: Plan, reaction: Sample | None, waited_ms: float) -> Result[ActionRecord, InputError]:
        """The one place `link.play()` is called."""
        played = self._link.play(plan.steps)
        if not is_successful(played):
            if played.failure() in _UNCERTAIN:
                # A key may be down: release once, discard that result, keep the play's error. A failed release is
                # never retried (PA-adj1b-D1, PA-rev I2(vii)).
                self._link.release_all()
            return played
        return Success(ActionRecord(plan, reaction, waited_ms, played.unwrap()))
