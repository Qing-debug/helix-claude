"""G4 buffering check (plan A, Task H2 step 3; PA-rev3b G4): is esptool's reset line read when it is printed, or
only when esptool exits (block-buffered piped stdout)?

One `faults.reset_board()` call, the soak's own reset path: board-list port lookup, then
``esptool --port P --no-stub read-mac`` (resets the board; no flash, no erase). It logs when each output line was
read (ms after launch), esptool's exit time (taken when `reset_board()` returns, right after the process ended, so
at or just after the exit) and prints exit - `injected_ns`.

Verdict. A block-buffered pipe delivers every line at once at exit; read as printed, the lines spread over esptool's
run (the reset line comes after connecting to and reading the chip, about 2 s after esptool's first line; notes/BRD
and plan Task H2 I5 give ~3 s launch -> reset line). So the check judges the spread from the first line read to the
reset line: >= SPREAD_MS -> READ_BEFORE_EXIT; less -> BLOCK_BUFFERED, a [review] (every reset_board stamp is late,
the optimistic direction for the Faults gate). exit - injected_ns is printed as the plan asks, not judged alone: the
reset is esptool's last action, so it is small in both cases.

Run (board attached, no keys are sent): ``python -m src.input.harness.g4_check``. Exit 0 READ_BEFORE_EXIT,
2 BLOCK_BUFFERED, 1 reset failed (no port / no reset line / esptool error).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field

from returns.pipeline import is_successful
from returns.result import Result

from src.input.harness import faults

READ_BEFORE_EXIT = "READ_BEFORE_EXIT"
BLOCK_BUFFERED = "BLOCK_BUFFERED"
NO_RESET = "NO_RESET_LINE"
SPREAD_MS = 500.0  # buffered: all lines within a few ms; as printed: ~2 s (inferred from ~3 s launch -> reset line)


@dataclass
class G4Report:
    result: Result[None, str]
    launch_ns: int | None
    injected_ns: int | None
    exit_ns: int
    lines: list[tuple[int, str]] = field(default_factory=list)  # (read ns, text), in read order

    def exit_minus_injected_ms(self) -> float | None:
        return None if self.injected_ns is None else (self.exit_ns - self.injected_ns) / 1e6

    def spread_ms(self) -> float | None:
        """First line read -> reset line read."""
        if self.injected_ns is None or not self.lines:
            return None
        return (self.injected_ns - self.lines[0][0]) / 1e6

    def verdict(self) -> str:
        spread = self.spread_ms()
        if spread is None:
            return NO_RESET
        return READ_BEFORE_EXIT if spread >= SPREAD_MS else BLOCK_BUFFERED


def run_check() -> G4Report:
    stamps = faults.FaultStamps()
    lines: list[tuple[int, str]] = []
    result = faults.reset_board(stamps, on_line=lambda ns, text: lines.append((ns, text)))
    exit_ns = time.perf_counter_ns()  # reset_board returns right after esptool's wait(): at or just after exit
    return G4Report(result, stamps.launch_ns, stamps.injected_ns, exit_ns, lines)


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(prog="python -m src.input.harness.g4_check",
                            description="One reset_board(); is esptool's reset line read before esptool exits?"
                            ).parse_args(argv)
    rep = run_check()
    base = rep.launch_ns if rep.launch_ns is not None else rep.exit_ns
    for ns, text in rep.lines:
        print(f"{(ns - base) / 1e6:9.1f} ms  {text}")
    if not is_successful(rep.result):
        print(f"reset_board failed: {rep.result.failure()}")
    exit_minus = rep.exit_minus_injected_ms()
    verdict = rep.verdict()
    if exit_minus is not None:
        print(f"injected_ns - launch_ns = {(rep.injected_ns - rep.launch_ns) / 1e6:.1f} ms; "
              f"exit - injected_ns = {exit_minus:.1f} ms; first line -> reset line = {rep.spread_ms():.1f} ms")
    if verdict == READ_BEFORE_EXIT:
        print(f"{verdict}: the reset line was read when printed, before esptool exited")
    elif verdict == BLOCK_BUFFERED:
        print(f"{verdict} [review]: every line arrived together at exit; reset_board stamps are late (PA-rev3b G4)")
    print(json.dumps({"verdict": verdict, "ok": is_successful(rep.result), "launch_ns": rep.launch_ns,
                      "injected_ns": rep.injected_ns, "exit_ns": rep.exit_ns, "exit_minus_injected_ms": exit_minus,
                      "spread_ms": rep.spread_ms(),
                      "lines_ms": [[round((ns - base) / 1e6, 1), text] for ns, text in rep.lines]}))
    if verdict == NO_RESET or not is_successful(rep.result):
        return 1
    return 0 if verdict == READ_BEFORE_EXIT else 2


if __name__ == "__main__":
    sys.exit(main())
