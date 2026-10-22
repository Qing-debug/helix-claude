"""Measurement probes for the input link (plan A, Task V1): fixed scripts through `InputLayer` and the listener.

No copies (A-PF-D3): the C5 run writer, the focus policy and the start wait come from `soak.py` (H2), Delivery
matching and the M7 split from `analyze.py` (H3), the sample pairing from `realism.py` (H4).

A run is ``data/probe/<run>/``; each probe writes a complete C5 run dir ``data/probe/<run>/<probe>/`` with its own
`BleLink`, listener (``keys.jsonl``) and `RunWriter`, so analyze.py / realism.py read it unchanged. Per run:

1. Step 1, MapleStory check (hard rule 2, `soak.maple_check`) and step 2, the board port (hard rule 3: exactly one
   port with VID 0x303A / PID 0x1001 in ``arduino-cli board list --format json``), before any listener, link or
   run dir exists. Either one failing: BLOCKED (exit 3).
2. Per probe, in order: open the writer, MapleStory check, start the listener in the probe dir, the start wait
   (`soak.wait_for_click`: one bring-to-foreground try, then the operator clicks the sink; a timeout is BLOCKED),
   connect, then the probe's commands. Each command goes through `soak.run_command` (focus policy, `stimulus_ns`,
   the C5 row, a drain); the MapleStory check repeats every 60 s.
3. A command that ends `FOCUS_LOST` or with a link loss (NOT_CONNECTED / LINK_LOST), a MapleStory process, a
   failed connect or listener, or Ctrl-C stops probe.py like the soak (PA-rev m15, A-H2-R-D2): `release_all()`,
   `session.json` written, link closed, listener stopped, non-zero exit; the probes after it are not run and are
   named on stderr.
4. After a probe's files are closed its report runs on them; its lines go to stdout and ``report.txt``. A report
   may carry `[review]` items and a `stop` (e.g. a silent drop): a stop ends the run after that probe (exit 4).

Run: ``python -m src.input.harness.probe [--probes delivery,...] [--run NAME | --out DIR] [--seed N] [--cli EXE]``
"""
from __future__ import annotations

import argparse
import datetime
import json
import subprocess
import sys
import time
import zlib
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np
from returns.pipeline import is_successful
from returns.result import Failure, Result, Success

from src.input.harness import analyze, listener, soak
from src.input.harness.soak import (EXIT_ABORT, EXIT_INTERRUPTED, EXIT_OK, EXIT_REFUSED, FOCUS_RECHECKS,
                                    START_WAIT_S, Command, CommandOutcome, RunWriter)
from src.input.input_layer import ActionRecord, InputLayer
from src.input.link import BleLink, InputError, SessionInfo
from src.input.timing import TimingModel

# Seam the tests monkeypatch (as soak.py); _clock is perf_counter_ns, the clock of every C5 file.
_clock = time.perf_counter_ns

EXIT_BLOCKED = 3  # a precondition failed (MapleStory, board port, sink never in front); nothing was sent
EXIT_STOP = 4  # a probe's report asked to stop (plan V1: "stop and report")

PROBE_ROOT = Path("data/probe")
CLI_DEFAULT = ("C:/Users/Qing_programming/AppData/Local/Programs/Arduino IDE/resources/app/lib/backend/resources/"
               "arduino-cli.exe")  # plan A's $CLI
BOARD_VID, BOARD_PID = "0x303a", "0x1001"  # the board's native USB-Serial/JTAG (hard rule 3)


# ---- step 2: the board port (hard rule 3) ----------------------------------------------------------

def _board_list_json(cli: str) -> Result[str, str]:
    """stdout of ``<cli> board list --format json``."""
    try:
        out = subprocess.run([cli, "board", "list", "--format", "json"], capture_output=True, text=True,
                             errors="replace", timeout=60)
    except (OSError, subprocess.SubprocessError) as e:
        return Failure(f"arduino-cli board list failed: {e}")
    if out.returncode != 0:
        return Failure(f"arduino-cli board list exited {out.returncode}: {out.stderr.strip()}")
    return Success(out.stdout)


def board_ports(text: str) -> list[str]:
    """Addresses of the detected ports with VID 0x303A and PID 0x1001 (any case), hard rule 3's filter."""
    out = []
    for p in json.loads(text).get("detected_ports", []):
        port = p.get("port") or {}
        props = port.get("properties") or {}
        if props.get("vid", "").lower() == BOARD_VID and props.get("pid", "").lower() == BOARD_PID:
            out.append(port["address"])
    return out


def board_port(cli: str) -> Result[str, str]:
    """The one board port, or why there is not exactly one (with the board list quoted)."""
    listed = _board_list_json(cli)
    if not is_successful(listed):
        return listed
    text = listed.unwrap()
    try:
        ports = board_ports(text)
    except (ValueError, KeyError, AttributeError) as e:
        return Failure(f"cannot read arduino-cli board list output ({e}): {text}")
    if len(ports) != 1:
        return Failure(f"expected exactly one board port (VID 0x303A / PID 0x1001), found {len(ports)} {ports}; "
                       f"arduino-cli board list --format json: {text}")
    return Success(ports[0])


# ---- probes ------------------------------------------------------------------------------------

@dataclass(frozen=True)
class ProbeReport:
    lines: list[str]  # printed and written to report.txt
    review: list[str] = field(default_factory=list)  # [review] items for the task report
    stop: str | None = None  # why probe.py must stop after this probe (None: go on)


class ProbeAbort(Exception):
    """Stops probe.py like the soak's abort path (release_all, session.json, non-zero exit)."""

    def __init__(self, message: str, code: int = EXIT_ABORT) -> None:
        super().__init__(message)
        self.code = code


class ProbeContext:
    """What a probe's `commands(ctx)` drives: the link, the InputLayer on the probe's model, the writer, the sink
    and the probe's own generator. `command()` / `issue()` number the rows from 0."""

    def __init__(self, name: str, run_dir: Path, writer: RunWriter, sink: soak.Sink, link: BleLink,
                 layer: InputLayer, rng: np.random.Generator, session: SessionInfo,
                 periodic_check: Callable[[], None]) -> None:
        self.name = name
        self.run_dir = run_dir
        self.writer = writer
        self.sink = sink
        self.link = link
        self.layer = layer
        self.rng = rng
        self.session = session
        self._periodic_check = periodic_check
        self.seq = 0

    def command(self, action: str, call: Callable[[int], Result[ActionRecord, InputError]],
                intended_ms: float | None = None) -> CommandOutcome:
        """`soak.run_command` with the next seq; FOCUS_LOST and a link loss raise `ProbeAbort` (H2's rules)."""
        self._periodic_check()
        seq = self.seq
        outcome = soak.run_command(self.writer, self.sink, seq, action, call, intended_ms)
        self.seq += 1
        if outcome.focus_lost:
            raise ProbeAbort(f"FOCUS_LOST in probe {self.name} at seq {seq}: the sink was not back after "
                             f"{FOCUS_RECHECKS} s")
        if not is_successful(outcome.result) and outcome.result.failure() in soak.LINK_LOSS:
            raise ProbeAbort(f"{outcome.result.failure()} in probe {self.name} at seq {seq}: the link was lost")
        return outcome

    def issue(self, cmd: Command) -> CommandOutcome:
        """One soak `Command` through `soak.dispatch`."""
        return self.command(cmd.action, lambda s: soak.dispatch(self.layer, cmd, s), cmd.intended_ms)


@dataclass(frozen=True)
class Probe:
    name: str
    commands: Callable[[ProbeContext], None]
    report: Callable[[Path], ProbeReport]  # runs on the closed probe dir
    model: Callable[[np.random.Generator], TimingModel] = TimingModel


def probe_streams(seed: int, name: str) -> tuple[np.random.Generator, np.random.Generator]:
    """(timing model, probe's own draws) for one probe: a function of the seed and the probe's name only, so a
    probe run alone draws what it draws in a full run."""
    timing, own = np.random.SeedSequence([seed, zlib.crc32(name.encode())]).spawn(2)
    return np.random.default_rng(timing), np.random.default_rng(own)


# ---- probe: every command type (plan V1 step 3, first bullet) ---------------------------------------

DELIVERY_N = 20
DELIVERY_WALK_MS = 1000.0
DELIVERY_SEQUENCE_LEN = 4


def delivery_commands(ctx: ProbeContext) -> None:
    """Tap each of the 7 keys x20, walk 1 s x20 (Left / Right alternating), teleport each direction x20, drop x20,
    sequence of 4 x20 (keys drawn with repeats from soak's SEQUENCE_KEYS, no Alt)."""
    for key in soak.TAP_KEYS:
        for _ in range(DELIVERY_N):
            ctx.issue(Command("tap", (key,)))
    for i in range(DELIVERY_N):
        ctx.issue(Command("walk", (soak.WALK_KEYS[i % len(soak.WALK_KEYS)],), DELIVERY_WALK_MS))
    for direction in soak.ARROWS:
        for _ in range(DELIVERY_N):
            ctx.issue(Command("teleport", (direction,)))
    for _ in range(DELIVERY_N):
        ctx.issue(Command("drop"))
    for _ in range(DELIVERY_N):
        idx = ctx.rng.integers(len(soak.SEQUENCE_KEYS), size=DELIVERY_SEQUENCE_LEN)
        ctx.issue(Command("sequence", tuple(soak.SEQUENCE_KEYS[i] for i in idx)))


def delivery_report(run_dir: Path) -> ProbeReport:
    """H3's Listener liveness and Delivery checks on this probe's dir. Expected: PASS, no failed command."""
    run = analyze.load_run(run_dir)
    dm = analyze.match_delivery(run)
    checks = [analyze.check_liveness(dm), analyze.check_delivery(run, dm)]
    lines = [line for c in checks for line in c.render()]
    counts = Counter(r["action"] for r in analyze.commands_of(run))
    lines.append("commands: " + ", ".join(f"{a} {n}" for a, n in counts.items()))
    passed = not any(c.failed for c in checks)
    lines.append(f"delivery probe: {'PASS' if passed else 'FAIL'} (expected: listener transitions equal the "
                 "commanded transitions exactly, no failed command)")
    review = [] if passed else ["every-command-type probe: " + "; ".join(f"{c.name} {c.verdict} {c.summary}"
                                                                         for c in checks if c.failed)]
    return ProbeReport(lines, review)


PROBES: dict[str, Probe] = {
    "delivery": Probe("delivery", delivery_commands, delivery_report),
}


# ---- the runner --------------------------------------------------------------------------------

@dataclass(frozen=True)
class ProbeConfig:
    probes: tuple[str, ...]
    out: Path
    seed: int
    cli: str = CLI_DEFAULT


def _check_processes(writer: RunWriter) -> None:
    ok, text = soak.maple_check()
    writer.session["process_check"] = text
    if not ok:
        raise ProbeAbort(text)


def _run_probe(p: Probe, cfg: ProbeConfig, link: BleLink, start_sink: Callable[[Path], soak._SoakSink]) -> int:
    """One probe into ``cfg.out / p.name``; returns EXIT_OK when its commands ran to the end."""
    timing_rng, own_rng = probe_streams(cfg.seed, p.name)
    layer = InputLayer(link, p.model(timing_rng))
    sink = None
    ended = False
    with RunWriter(cfg.out / p.name, link, run_id=f"{cfg.out.name}/{p.name}", seed=cfg.seed) as writer:
        try:
            _check_processes(writer)
            last_check_ns = _clock()

            def periodic_check() -> None:
                nonlocal last_check_ns
                if _clock() - last_check_ns >= soak.PROCESS_CHECK_PERIOD_NS:
                    _check_processes(writer)
                    last_check_ns = _clock()

            try:
                sink = start_sink(writer.run_dir)
            except OSError as e:
                raise ProbeAbort(f"listener did not start: {e}") from e
            if not soak.wait_for_click(sink, periodic_check):  # PA-rev m15 foreground check, before connect
                raise ProbeAbort(f"BLOCKED: the sink did not take the foreground within {START_WAIT_S:g} s; every "
                                 "command would end FOCUS_LOST", EXIT_BLOCKED)
            connected = link.connect()
            if not is_successful(connected):
                raise ProbeAbort(f"connect failed: {connected.failure()}")
            writer.record_connect(connected.unwrap())
            ctx = ProbeContext(p.name, writer.run_dir, writer, sink, link, layer, own_rng, connected.unwrap(),
                               periodic_check)
            p.commands(ctx)
            ended = True
            return EXIT_OK
        finally:
            if not ended:  # any abort, Ctrl-C or unexpected error: lift every key first
                released = layer.release_all()
                if not is_successful(released):
                    print(f"release_all: {released.failure()}", file=sys.stderr, flush=True)
            link.close()
            if sink is not None:
                sink.stop()


def _emit_report(p: Probe, run_dir: Path, report: ProbeReport) -> None:
    lines = report.lines + [f"[review] {p.name}: {r}" for r in report.review]
    with open(run_dir / "report.txt", "w", encoding="utf-8", newline="\n") as fh:
        fh.writelines(line + "\n" for line in lines)
    print(f"== probe {p.name} -> {run_dir}", flush=True)
    for line in lines:
        print(line, flush=True)


def run_probes(cfg: ProbeConfig, new_link: Callable[[], BleLink],
               start_sink: Callable[[Path], soak._SoakSink]) -> int:
    """Steps 1-2, then each probe in `cfg.probes` order; returns the exit code."""
    ok, text = soak.maple_check()
    if not ok:
        print(f"BLOCKED (step 1, hard rule 2): {text}", file=sys.stderr, flush=True)
        return EXIT_BLOCKED
    port = board_port(cfg.cli)
    if not is_successful(port):
        print(f"BLOCKED (step 2, hard rule 3): {port.failure()}", file=sys.stderr, flush=True)
        return EXIT_BLOCKED
    print(f"{text}; board port {port.unwrap()} (VID 0x303A / PID 0x1001)", flush=True)
    for i, name in enumerate(cfg.probes):
        p = PROBES[name]
        rest = ", ".join(cfg.probes[i + 1:]) or "none"
        try:
            _run_probe(p, cfg, new_link(), start_sink)
        except ProbeAbort as e:
            tag = "[review] " if "FOCUS_LOST" in str(e) else ""
            print(f"{tag}{e} (probe.py stopped in probe {name}; not run: {rest})", file=sys.stderr, flush=True)
            return e.code
        except KeyboardInterrupt:
            print(f"probe.py interrupted (Ctrl-C) in probe {name}; not run: {rest}", file=sys.stderr, flush=True)
            return EXIT_INTERRUPTED
        report = p.report(cfg.out / name)
        _emit_report(p, cfg.out / name, report)
        if report.stop is not None:
            print(f"probe.py stopped after probe {name}: {report.stop}; not run: {rest}", file=sys.stderr,
                  flush=True)
            return EXIT_STOP
    print(f"probes done: {', '.join(cfg.probes)} -> {cfg.out}", flush=True)
    return EXIT_OK


# ---- CLI ---------------------------------------------------------------------------------------------

# Seams: the tests replace these with fakes.
_new_link: Callable[[], BleLink] = BleLink


def _start_sink(run_dir: Path) -> soak._SoakSink:
    return listener.start(run_dir)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="python -m src.input.harness.probe",
                                description="V1 measurement probes on the board; one C5 run dir per probe.")
    p.add_argument("--probes", default=",".join(PROBES),
                   help=f"comma-separated, run in this order (default: {','.join(PROBES)})")
    p.add_argument("--run", default=None, help="run name: data/probe/<run>/ (default: YYYYmmdd-HHMMSS)")
    p.add_argument("--out", default=None, help="run directory (overrides --run)")
    p.add_argument("--seed", type=int, default=None, help="timing and probe seed (default: a fresh one, logged)")
    p.add_argument("--cli", default=CLI_DEFAULT, help="arduino-cli.exe for the board-port check (hard rule 3)")
    return p.parse_args(argv)


def config_from_args(args: argparse.Namespace) -> ProbeConfig:
    seed = args.seed if args.seed is not None else int(np.random.SeedSequence().entropy)
    if args.out:
        out = Path(args.out)
    else:
        out = PROBE_ROOT / (args.run or datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
    probes = tuple(s.strip() for s in args.probes.split(",") if s.strip())
    return ProbeConfig(probes=probes, out=out, seed=seed, cli=args.cli)


def main(argv: list[str] | None = None) -> int:
    cfg = config_from_args(parse_args(argv))
    unknown = [n for n in cfg.probes if n not in PROBES]
    if unknown or not cfg.probes:
        print(f"refused: unknown probe(s) {unknown}; known: {', '.join(PROBES)}", file=sys.stderr, flush=True)
        return EXIT_REFUSED
    print(f"probes: {', '.join(cfg.probes)}, seed {cfg.seed} -> {cfg.out}", flush=True)
    return run_probes(cfg, _new_link, _start_sink)


if __name__ == "__main__":
    sys.exit(main())
