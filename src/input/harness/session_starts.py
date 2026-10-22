"""Session-start runner (plan A, Task V2 step 3; C5 ``session_starts.jsonl``, A-PF-D4).

Runs V2 step 3's 20 session starts in this order (PLAN): 8 ``plain``, 6 ``reset_board``, 6 ``bluetooth_off_on``,
into its own run dir (``data/starts/<run>/``, never a soak's), with its own listener (H1), so ``keys.jsonl`` lands
next to ``session_starts.jsonl``. Before the first start: the MapleStory check (hard rule 2), the listener, and
soak's start wait for the operator's click on the sink. Then, per start (attempt k, 1-based):

1. MapleStory check (hard rule 2) before this start's reset or key; a match aborts the run.
2. The condition's injector, if any, while the previous start's session is still open: ``faults.reset_board()``
   (which repeats the VID 303A / PID 1001 port lookup itself on every call, H2) or ``faults.bluetooth_off_on(10)``.
   A Failure ends the start as a Failure row carrying the injector's message; ``connect()`` is not called, and the
   start is neither skipped nor retried.
3. ``BleLink.close()`` (a no-op if the session already ended) + ``BleLink.connect()``; ``seconds`` is the duration
   of the ``connect()`` call (null when it was not called).
4. One ``ping()``; soak's focus check (a sink not back after its 10 re-checks aborts the run: the start is then
   invalid, not a link failure); one tap-E through ``InputLayer.tap`` (no stimulus, so no reaction wait).
5. The listener must log E's down and then its up at or after the tap call began, within TAP_SEEN_TIMEOUT_S of the
   tap's return (``keys.jsonl`` is polled); otherwise the error is ``TAP_NOT_SEEN``.

The first thing that fails is the row's ``error`` (C5); the run goes on with the next start. A successful start
leaves its session open for the next one. No re-pairing: the runner only closes and connects (spec:L61). Any abort
or Ctrl-C calls ``release_all()``; every exit path closes the link and stops the listener.

    python -m src.input.harness.session_starts [--out data/starts/<run>] [--seed N]
"""
from __future__ import annotations

import argparse
import datetime
import json
import sys
import time
from pathlib import Path
from typing import Callable

import numpy as np
from returns.pipeline import is_successful
from returns.result import Result

from src.input.harness import faults, listener, soak
from src.input.input_layer import InputLayer
from src.input.link import BleLink
from src.input.protocol import Key
from src.input.timing import TimingModel

# Seams the tests monkeypatch; _clock is perf_counter_ns, the listener's clock.
_clock = time.perf_counter_ns
_sleep = time.sleep

STARTS_FILE = "session_starts.jsonl"
STARTS_FIELDS = ("attempt", "condition", "result", "error", "seconds", "start_ns", "end_ns")  # C5 order
PLAN = (("plain", 8), ("reset_board", 6), ("bluetooth_off_on", 6))  # V2 step 3; H3 expects these counts
BT_OFF_SECONDS = 10.0  # V2 step 3 runs bluetooth_off_on(10)
TAP_KEY = Key.E
TAP_NOT_SEEN = "TAP_NOT_SEEN"
TAP_SEEN_TIMEOUT_S = 1.0  # after the tap's DONE the keys are already at the PC; this covers the writer's flush
TAP_SEEN_POLL_S = 0.05

# Seam: the tests replace these with fakes. Each takes a fresh FaultStamps (the row does not use the stamps).
INJECTORS: dict[str, Callable[[faults.FaultStamps], Result[None, str]]] = {
    "reset_board": lambda stamps: faults.reset_board(stamps),
    "bluetooth_off_on": lambda stamps: faults.bluetooth_off_on(BT_OFF_SECONDS, stamps),
}


def conditions(plan: tuple[tuple[str, int], ...] = PLAN) -> list[str]:
    """One condition per start, in run order."""
    return [c for c, n in plan for _ in range(n)]


def start_row(attempt: int, condition: str, error: str | None, seconds: float | None, start_ns: int,
              end_ns: int) -> dict:
    """A C5 `session_starts.jsonl` row; `result` is Success exactly when nothing failed."""
    return {"attempt": attempt, "condition": condition, "result": "Success" if error is None else "Failure",
            "error": error, "seconds": seconds, "start_ns": start_ns, "end_ns": end_ns}


# ---- tap seen by the listener --------------------------------------------------------------------

def tap_seen(keys_path: Path, vk: int, after_ns: int) -> bool:
    """`keys.jsonl` holds a down of `vk` at or after `after_ns`, then an up of it (OS repeats ignored)."""
    try:
        with open(keys_path, encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError:
        return False
    down = False
    for line in lines:
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:  # the writer thread is mid-line
            continue
        if ev.get("vk") != vk or ev.get("qpc_ns", -1) < after_ns or ev.get("repeat"):
            continue
        if ev.get("down"):
            down = True
        elif down:
            return True
    return False


def wait_tap_seen(keys_path: Path, vk: int, after_ns: int, timeout_s: float = TAP_SEEN_TIMEOUT_S) -> bool:
    deadline_ns = _clock() + int(timeout_s * 1e9)
    while not tap_seen(keys_path, vk, after_ns):
        if _clock() >= deadline_ns:
            return False
        _sleep(TAP_SEEN_POLL_S)
    return True


# ---- one start -----------------------------------------------------------------------------------

class _Abort(Exception):
    pass


def _attempt(condition: str, link: BleLink, layer: InputLayer, sink: soak.Sink,
             keys_path: Path) -> tuple[str | None, float | None]:
    """(error or None, connect seconds or None) of one start."""
    injector = INJECTORS.get(condition)
    if injector is not None:
        injected = injector(faults.FaultStamps())
        if not is_successful(injected):
            return str(injected.failure()), None
    link.close()
    t0 = _clock()
    connected = link.connect()
    seconds = (_clock() - t0) / 1e9
    if not is_successful(connected):
        return str(connected.failure()), seconds
    pinged = link.ping()
    if not is_successful(pinged):
        return str(pinged.failure()), seconds
    if not soak.check_focus(sink).ok:
        raise _Abort(f"FOCUS_LOST: the sink was not back after {soak.FOCUS_RECHECKS} s")
    tap_ns = _clock()
    tapped = layer.tap(TAP_KEY)
    if not is_successful(tapped):
        return str(tapped.failure()), seconds
    if not wait_tap_seen(keys_path, listener.VK_BY_KEY[TAP_KEY], tap_ns):
        return TAP_NOT_SEEN, seconds
    return None, seconds


def run_start(attempt: int, condition: str, link: BleLink, layer: InputLayer, sink: soak.Sink,
              keys_path: Path) -> dict:
    start_ns = _clock()
    error, seconds = _attempt(condition, link, layer, sink, keys_path)
    return start_row(attempt, condition, error, seconds, start_ns, _clock())


# ---- the runner ----------------------------------------------------------------------------------

def _check_processes() -> None:
    ok, text = soak.maple_check()
    if not ok:
        raise _Abort(text)


def run_starts(out: Path, link: BleLink, start_sink: Callable[[Path], soak._SoakSink], seed: int,
               plan: tuple[tuple[str, int], ...] = PLAN) -> int:
    """All starts of `plan` into `out`; returns the exit code (soak's: 0 done, 1 abort, 130 Ctrl-C)."""
    out = Path(out)
    layer = InputLayer(link, TimingModel(np.random.default_rng(seed)))
    sink = None
    ended = False
    out.mkdir(parents=True, exist_ok=True)
    (out / listener.KEYS_FILE).write_bytes(b"")  # the listener appends: start it on a fresh file (A-V2prep-R m2)
    with open(out / STARTS_FILE, "w", encoding="utf-8", newline="\n") as fh:
        try:
            _check_processes()  # before the listener blocks any key, before any reset or connect (hard rule 2)
            last_check_ns = _clock()

            def periodic_check() -> None:  # during the start wait, as the soak does
                nonlocal last_check_ns
                if _clock() - last_check_ns >= soak.PROCESS_CHECK_PERIOD_NS:
                    _check_processes()
                    last_check_ns = _clock()

            try:
                sink = start_sink(out)
            except OSError as e:
                raise _Abort(f"listener did not start: {e}") from e
            if not soak.wait_for_click(sink, periodic_check):
                raise _Abort(f"the sink did not take the foreground within {soak.START_WAIT_S:g} s (no click)")
            names = conditions(plan)
            ok = 0
            for attempt, condition in enumerate(names, 1):
                _check_processes()  # before this start's reset or key (hard rule 2)
                row = run_start(attempt, condition, link, layer, sink, out / listener.KEYS_FILE)
                fh.write(json.dumps(row) + "\n")
                fh.flush()
                ok += row["result"] == "Success"
                secs = "-" if row["seconds"] is None else f"{row['seconds']:.2f} s"
                print(f"start {attempt}/{len(names)} {condition}: {row['result']}"
                      f"{'' if row['error'] is None else ' ' + row['error']} (connect {secs})", flush=True)
            ended = True
            print(f"session starts done: {ok}/{len(names)} Success -> {out / STARTS_FILE}", flush=True)
            return soak.EXIT_OK
        except _Abort as e:
            print(f"session starts aborted: {e}", file=sys.stderr, flush=True)
            return soak.EXIT_ABORT
        except KeyboardInterrupt:
            print("session starts interrupted (Ctrl-C)", file=sys.stderr, flush=True)
            return soak.EXIT_INTERRUPTED
        finally:
            if not ended:  # any abort, Ctrl-C or unexpected error: lift every key first
                released = layer.release_all()
                if not is_successful(released):
                    print(f"release_all: {released.failure()}", file=sys.stderr, flush=True)
            link.close()
            if sink is not None:
                sink.stop()


# ---- CLI -----------------------------------------------------------------------------------------

# Seams: the tests replace these with fakes.
_new_link: Callable[[], BleLink] = BleLink


def _start_sink(run_dir: Path) -> soak._SoakSink:
    return listener.start(run_dir)


def default_out() -> Path:
    return Path("data/starts") / datetime.datetime.now().strftime("%Y%m%d-%H%M%S")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="python -m src.input.harness.session_starts",
                                description="V2 step 3: 8 plain, 6 reset_board, 6 bluetooth_off_on(10) session "
                                            "starts; C5 session_starts.jsonl and keys.jsonl in the run dir.")
    p.add_argument("--out", default=None, help="run directory (default data/starts/<YYYYmmdd-HHMMSS>)")
    p.add_argument("--seed", type=int, default=None, help="timing seed for the taps (default: a fresh one, printed)")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    seed = args.seed if args.seed is not None else int(np.random.SeedSequence().entropy)
    out = Path(args.out) if args.out else default_out()
    print(f"session starts: {', '.join(f'{n} {c}' for c, n in PLAN)}, seed {seed} -> {out}", flush=True)
    return run_starts(out, _new_link(), _start_sink, seed)


if __name__ == "__main__":
    sys.exit(main())
