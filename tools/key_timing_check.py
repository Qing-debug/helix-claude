"""The key-timing check (WALKFIX Task 10A, QB283 2b; Hong, QB283: "check that this does not make the keys late").

    python tools/key_timing_check.py [--out data/keytiming/<name>] [--commands 2000] [--seed 1] [--at X,Y]

In a real walk the capture thread, the walker and the key timing share one Python process, and a capture that runs flat
out (`vision.THREAD_SLEEP_DURATION_S` = 0) could delay the thread that sends the keys. The long input test (the soak,
`src/input/harness/soak.py`) measures the figure that would show it: the reaction residual, "first key-down - stimulus - R,
the send -> key-down latency" (realism.py:224-238). This tool runs short soaks, one per capture pause, with the capture
running flat out on a test window (`tools/capture_load_window.py`, started here as a process of its own) and a stand-in
for the walker reading every picture as the walker does, and compares that figure between the pauses.

It needs the board, a real Bluetooth link and the "Helix input test sink" window, as the soak does, and MapleStory must
be closed and stay closed until it ends (root CLAUDE.md, Safety rules). It asks "Is MapleStory closed? y/n" once, in
`run_check`, before any listener, link or key, and no arm asks again (just before it, it asks for one click on the console,
since the test window can take the keyboard as it starts: Tk activates a process's first window when it makes its wrapper,
review m1): each soak gets the text of that one answer
(`soak.run_soak(answered=...)`). Each arm starts a new sink, which must be in front before the soak connects: the console
beeps and prints `>>> Arm k of up to 6 ...`; click the sink if it is not in front by itself (mouse only, no keys).

Order and decision rule (the numbers are the constants below). Arms: B1 = pause 20 ms (H80, what every walk so far ran
with), N = pause 0, B2 = pause 20 ms; every arm is a soak of the same commands with the same seed, so the arms differ in
the capture pause only. Valid only if all three pass Delivery (with the listener alive and a residual to measure), N's
capture rate is at least FAST_FACTOR times B1's, and the baselines agree (p50 within BASELINE_P50_TOLERANCE_MS, p99
within BASELINE_P99_TOLERANCE_MS); otherwise the result is "inconclusive" and nothing is searched. An arm is "later" if its
p50 is above max(p50 of B1, B2) + LATER_P50_MS or its p99 is above max(p99 of B1, B2) + LATER_P99_MS. N not later: the
result is 0 ms. N later: run 5 ms; if 5 is not later run 2, and if 2 is not later run 1; if 5 is later run 10; the
smallest arm that was not later is the result, and if 10 is later too the result is 20 ms. At most MAX_ARMS arms. A search
arm that fails Delivery, loses the listener or has no residual makes the result "inconclusive" (its figures cannot say
"later" or "not later"); the search arms' capture rates are written but not judged. Every arm, the first three and the
search arms, must also have had its load all through: an arm at whose end the test window's process had ended, or during
which the capture lost the test window's minimap at any point (`minimap_lost` raised, `minimap_follow.py`), measured some
other load, and makes the result "inconclusive" too (review I1).

Files, under `--out` (a new or empty folder inside the repo's `data/`, never a file of a run before): one soak run folder
per arm, `<k>-pause<P>ms/`, with `arm.json` (the pause, pictures per second, walker reads, whether the test window was
still running at the arm's end and whether the minimap was lost during it, the Delivery verdict, the listener's state, and
the residual's n, p50 and p99), and `result.json` (the rules, every finished arm with `later`
(null for a baseline), and `result`: the pause in ms or "inconclusive", with `reason`). A soak that does not end normally
(a missed click is `EXIT_ABORT`) stops the check cleanly: exit 1, the finished arms kept, and `result.json` has
`"stopped": "arm <k>: <the soak's exit code>"` and no `result`; a Ctrl+C between arms or in an arm's analysis does the
same, as `"arm <k>: 130"` (the soak's own Ctrl+C code). A Ctrl+C at the question writes nothing, so the same `--out` can
be used again. Exit 0 means a result was reached, "inconclusive"
included; 2 a refusal (usage, `--out`, the MapleStory answer); 3 the capture did not find the test window.

`keys.jsonl` of each arm is a keylog and stays under `data/` (gitignored); this tool never opens it, the analysis code
does (`analyze.load_run`).
"""
import argparse
import datetime
import json
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:  # run as a script, sys.path[0] is tools/; imports are rooted at `src.` and `tools.`
    sys.path.insert(0, str(REPO))

from returns.pipeline import is_successful  # noqa: E402
from returns.result import Failure, Result, Success  # noqa: E402

from src.input.harness import analyze, listener, soak  # noqa: E402
from src.route import runtime  # noqa: E402
from tools import capture_load_window as window_tool  # noqa: E402

DATA = REPO / "data"
WINDOW_SCRIPT = REPO / "tools" / "capture_load_window.py"
ARM_FILE = "arm.json"
RESULT_FILE = "result.json"
INCONCLUSIVE = "inconclusive"

FIRST_PICTURE_WAIT_S = 30.0
WALKER_WAIT_S = 0.05            # the stand-in's wait for a new picture, the walker's `wait_new` timeout
WINDOW_CLOSE_WAIT_S = 5.0
EXIT_NO_PICTURE = 3

BASELINE_PAUSE_MS = 20          # H80: the pause every walk so far ran with
NO_PAUSE_MS = 0
SEARCH_PAUSES_MS = (1, 2, 5, 10)
MAX_ARMS = 6                    # B1, N, B2, and at most three search arms
FAST_FACTOR = 2.0               # N's capture rate over B1's, at least (B is about 45 pictures a second, H80)
BASELINE_P50_TOLERANCE_MS = 2.0
BASELINE_P99_TOLERANCE_MS = 10.0
LATER_P50_MS = 2.0
LATER_P99_MS = 10.0
RULES = {"BASELINE_PAUSE_MS": BASELINE_PAUSE_MS, "NO_PAUSE_MS": NO_PAUSE_MS, "SEARCH_PAUSES_MS": list(SEARCH_PAUSES_MS),
         "MAX_ARMS": MAX_ARMS, "FAST_FACTOR": FAST_FACTOR, "BASELINE_P50_TOLERANCE_MS": BASELINE_P50_TOLERANCE_MS,
         "BASELINE_P99_TOLERANCE_MS": BASELINE_P99_TOLERANCE_MS, "LATER_P50_MS": LATER_P50_MS,
         "LATER_P99_MS": LATER_P99_MS}

CLICK_CONSOLE_TEXT = ("Before you answer: click this console window once with the mouse (the test window may have taken "
                      "the keyboard when it started), then type the answer here.")
NO_PICTURE_TEXT = ("the capture did not find the test window's minimap: put the window on the second screen "
                   "(python tools/capture_load_window.py --at X,Y) and start again")

_clock = time.monotonic  # a seam: the tests give each arm a fake length


# ---- the capture and the walker stand-in ---------------------------------------------------------------------------

class CountingFrame(runtime.SharedFrame):
    """The shared frame the capture writes into, counting the pictures published (each one is a new array) and noting
    whether the capture lost the minimap since the last `reset` (`was_lost`). The capture raises `minimap_lost` once per
    failed stretch (`minimap_follow.LostTracker.search_failed`) and clears it with the next picture, both under the lock."""

    def __init__(self) -> None:
        self.pictures = 0
        self._frame = None
        self._lost = False
        self.was_lost = False
        super().__init__()  # sets minimap_frame = None, through the property below

    @property
    def minimap_lost(self) -> bool:
        return self._lost

    @minimap_lost.setter
    def minimap_lost(self, value: bool) -> None:
        self._lost = value
        if value:
            self.was_lost = True

    @property
    def minimap_frame(self):
        return self._frame

    @minimap_frame.setter
    def minimap_frame(self, value) -> None:
        self._frame = value
        if value is not None:
            self.pictures += 1

    def reset(self) -> None:
        """Restart the count and the lost note; the last picture stays. A minimap still lost now counts as lost: the arm
        that starts has no load from its start."""
        with self.minimap_frame_lock:
            self.pictures = 0
            self.was_lost = self._lost


class WalkerStandIn:
    """The walker's per-picture load, with no walker: wait for a new picture, then `read` it (the lock, the copy and the
    template match of `MinimapPositionSource`). `reads` counts the `read` calls."""

    def __init__(self, positions) -> None:
        self._positions = positions
        self.reads = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="walker-stand-in", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread.is_alive():  # never started when the capture could not start
            self._thread.join(WALKER_WAIT_S * 20)

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._positions.wait_new(WALKER_WAIT_S)
            self._positions.read()
            self.reads += 1


def _set_capture_pause(pause_ms: float) -> float:
    """Set the capture loop's pause between pictures (read by the loop on every pass); returns the previous one, in ms."""
    from src.vision import vision  # Windows and bettercam; not at module level

    previous = vision.THREAD_SLEEP_DURATION_S * 1000
    vision.THREAD_SLEEP_DURATION_S = pause_ms / 1000
    return previous


def _start_window(at: str | None) -> subprocess.Popen:
    """The test window, as a process of its own (`--at X,Y` given through)."""
    return subprocess.Popen([sys.executable, str(WINDOW_SCRIPT), *(["--at", at] if at else [])], cwd=REPO)


def _close_window(window) -> None:
    window.terminate()
    try:
        window.wait(timeout=WINDOW_CLOSE_WAIT_S)
    except subprocess.TimeoutExpired:
        window.kill()


# ---- the analysis of one arm ---------------------------------------------------------------------------------------

@dataclass(frozen=True)
class ArmFigures:
    delivery: str          # analyze's Delivery verdict, "PASS" or "FAIL"
    listener_alive: bool   # analyze's Listener liveness check passed (a dead listener leaves commands unmeasured)
    p50_ms: float | None   # the reaction residual, None when there was nothing to measure
    p99_ms: float | None
    n: int

    @property
    def usable(self) -> bool:
        """The arm's residual may be compared: Delivery passed, the listener stayed alive, and there is a residual."""
        return self.delivery == "PASS" and self.listener_alive and self.p50_ms is not None and self.p99_ms is not None


def residuals(dm: analyze.DeliveryMatch) -> list[float]:
    """First key-down - stimulus - R per measured command whose wait was not clamped, in ms: a copy of the loop in
    `realism.reaction_residual` (realism.py:224-238), which gives the figure as text. A test pins that the p50 of both agree."""
    vals = []
    for m in dm.commands:
        r = m.row
        if m.alignment is None or r["reaction_ms"] is None:
            continue
        if not r["waited_ms"] > 0:
            continue
        if m.first_down_ns is not None:
            vals.append((m.first_down_ns - r["stimulus_ns"]) / analyze.NS_PER_MS - r["reaction_ms"])
    return vals


def measure_arm(run_dir: Path) -> ArmFigures:
    """Delivery, the listener's state and the reaction residual's p50 and p99 of a finished arm's run folder; the
    analysis code reads the folder (this tool never opens `keys.jsonl`)."""
    run = analyze.load_run(run_dir)
    dm = analyze.match_delivery(run)
    vals = residuals(dm)
    return ArmFigures(analyze.check_delivery(run, dm).verdict, not analyze.check_liveness(dm).failed,
                      analyze.percentile(vals, 50) if vals else None, analyze.percentile(vals, 99) if vals else None,
                      len(vals))


@dataclass
class Arm:
    k: int
    pause_ms: int
    seconds: float
    pictures: int
    walker_reads: int
    figures: ArmFigures
    window_alive: bool = True   # the test window's process was still running when the arm's soak ended
    minimap_lost: bool = False  # the capture lost the test window's minimap at some point during the arm
    later: bool | None = None   # judged against the baselines; None for a baseline

    @property
    def pictures_per_s(self) -> float:
        return self.pictures / self.seconds if self.seconds > 0 else 0.0


def arm_record(arm: Arm) -> dict:
    f = arm.figures
    return {"k": arm.k, "pause_ms": arm.pause_ms, "seconds": round(arm.seconds, 3), "pictures": arm.pictures,
            "pictures_per_s": round(arm.pictures_per_s, 3), "walker_reads": arm.walker_reads,
            "window_alive": arm.window_alive, "minimap_lost": arm.minimap_lost, "delivery": f.delivery,
            "listener_alive": f.listener_alive, "residual_n": f.n, "residual_p50_ms": f.p50_ms,
            "residual_p99_ms": f.p99_ms}


# ---- the decision rule ---------------------------------------------------------------------------------------------

def keys_later(p50: float, p99: float, base_p50: float, base_p99: float) -> bool:
    """An arm's keys are later than the baselines' (`base_*` is the larger of B1's and B2's)."""
    return p50 > base_p50 + LATER_P50_MS or p99 > base_p99 + LATER_P99_MS


def _unusable_text(arm: Arm) -> str:
    f = arm.figures
    if f.delivery != "PASS":
        return f"did not pass Delivery ({f.delivery})"
    if not f.listener_alive:
        return "lost the listener (commands unmeasured)"
    return "has no reaction residual to measure"


def why_unusable(arm: Arm) -> str | None:
    """Why the arm's residual may not be compared, or None: its figures are not usable, or its load was not there all
    through the arm (review I1)."""
    if not arm.figures.usable:
        return _unusable_text(arm)
    if not arm.window_alive:
        return "lost its load: the test window's process had ended by the arm's end"
    if arm.minimap_lost:
        return "lost its load: the capture lost the test window's minimap during the arm"
    return None


def invalid_reason(b1: Arm, n: Arm, b2: Arm) -> str | None:
    """Why the three first arms cannot be compared (the result is then "inconclusive"), or None."""
    for arm in (b1, n, b2):
        why = why_unusable(arm)
        if why is not None:
            return f"arm {arm.k} (pause {arm.pause_ms} ms) {why}"
    if n.pictures_per_s < FAST_FACTOR * b1.pictures_per_s:
        return (f"with no pause the capture made {n.pictures_per_s:.1f} pictures a second, under {FAST_FACTOR:g} times "
                f"the {b1.pictures_per_s:.1f} of the first {BASELINE_PAUSE_MS} ms arm: the test window did not change "
                "every frame (is it covered, or off the captured screen?)")
    p50s, p99s = (b1.figures.p50_ms, b2.figures.p50_ms), (b1.figures.p99_ms, b2.figures.p99_ms)
    if abs(p50s[0] - p50s[1]) > BASELINE_P50_TOLERANCE_MS:
        return (f"the two {BASELINE_PAUSE_MS} ms baselines disagree: p50 {p50s[0]:.3f} and {p50s[1]:.3f} ms, more than "
                f"{BASELINE_P50_TOLERANCE_MS:g} ms apart")
    if abs(p99s[0] - p99s[1]) > BASELINE_P99_TOLERANCE_MS:
        return (f"the two {BASELINE_PAUSE_MS} ms baselines disagree: p99 {p99s[0]:.3f} and {p99s[1]:.3f} ms, more than "
                f"{BASELINE_P99_TOLERANCE_MS:g} ms apart")
    return None


class _Inconclusive(Exception):
    pass


def judge(play) -> tuple[int | str, str]:
    """Run the arms through `play(pause_ms) -> Arm` and decide: (the pause in ms or "inconclusive", why)."""
    b1, n, b2 = play(BASELINE_PAUSE_MS), play(NO_PAUSE_MS), play(BASELINE_PAUSE_MS)
    why = invalid_reason(b1, n, b2)
    if why is not None:
        return INCONCLUSIVE, why
    base_p50 = max(b1.figures.p50_ms, b2.figures.p50_ms)
    base_p99 = max(b1.figures.p99_ms, b2.figures.p99_ms)

    def is_later(arm: Arm) -> bool:
        arm.later = keys_later(arm.figures.p50_ms, arm.figures.p99_ms, base_p50, base_p99)
        return arm.later

    def search_later(pause_ms: int) -> bool:
        arm = play(pause_ms)
        why = why_unusable(arm)
        if why is not None:
            raise _Inconclusive(f"search arm {arm.k} (pause {pause_ms} ms) {why}")
        return is_later(arm)

    if not is_later(n):
        return NO_PAUSE_MS, f"with no pause the keys are not later than with {BASELINE_PAUSE_MS} ms"
    try:
        if search_later(5):
            if search_later(10):
                return BASELINE_PAUSE_MS, f"5 ms and 10 ms are both later: {BASELINE_PAUSE_MS} ms stays"
            return 10, "5 ms is later and 10 ms is not"
        if search_later(2):
            return 5, "5 ms is not later and 2 ms is"
        if search_later(1):
            return 2, "2 ms is not later and 1 ms is"
        return 1, "1 ms is not later"
    except _Inconclusive as error:
        return INCONCLUSIVE, str(error)


# ---- the run -------------------------------------------------------------------------------------------------------

class _Stopped(Exception):
    """An arm's soak did not end normally, or Ctrl+C came outside a soak; the check stops with the arms so far kept."""

    def __init__(self, k: int, code: int, what: str | None = None) -> None:
        super().__init__(f"arm {k}: {code}")
        self.k, self.code = k, code
        self.what = what if what is not None else f"the soak ended with exit code {code}"


def _arm_config(args: argparse.Namespace, k: int, pause_ms: int, out: Path) -> soak.SoakConfig:
    """The soak of arm `k`: the same commands and seed in every arm, `--short` (a few thousand commands give fewer than
    the judge's samples per type, and nothing here is judged by it)."""
    argv = ["--commands", str(args.commands), "--seed", str(args.seed), "--out", str(out / f"{k}-pause{pause_ms}ms"),
            "--short"]
    return soak.config_from_args(soak.parse_args(argv))


def _arm_line(arm: Arm) -> str:
    f = arm.figures
    p50 = "n/a" if f.p50_ms is None else f"{f.p50_ms:.3f}"
    p99 = "n/a" if f.p99_ms is None else f"{f.p99_ms:.3f}"
    later = "baseline" if arm.later is None else ("yes" if arm.later else "no")
    listener_note = "" if f.listener_alive else ", listener lost"
    load_note = ("" if arm.window_alive else ", test window ended") + (", minimap lost" if arm.minimap_lost else "")
    return (f"arm {arm.k}: pause {arm.pause_ms} ms, {arm.pictures_per_s:.1f} pictures/s{load_note}, residual p50 {p50} ms "
            f"p99 {p99} ms (n {f.n}), Delivery {f.delivery}{listener_note}, later {later}")


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def run_check(args: argparse.Namespace, out: Path, holder: CountingFrame, stand_in: WalkerStandIn,
              window: subprocess.Popen) -> int:
    """Steps 3 to 6: the one MapleStory question, then the arms and the decision, then `result.json` and the lines.
    The capture is running and has a picture; no listener, link or key has been touched yet. `window` is the test
    window's process, which this tool started: only its own state is read (`poll`), no other process is looked at."""
    print(CLICK_CONSOLE_TEXT, flush=True)
    ok, text = soak.maple_check()  # through the module, so a patch of soak._read_answer reaches it; asked once, here
    if not ok:
        print(text, file=sys.stderr, flush=True)
        return soak.EXIT_REFUSED
    arms: list[Arm] = []
    pause_before: list[float] = []

    def play(pause_ms: int) -> Arm:
        k = len(arms) + 1
        cfg = _arm_config(args, k, pause_ms, out)
        print(f"\a>>> Arm {k} of up to {MAX_ARMS} (pause {pause_ms} ms) is starting. If the window titled "
              f"'{listener.SINK_TITLE}' does not come to the front by itself, click it now (mouse only, no keys). "
              f"The soak waits up to {soak.START_WAIT_S:g} s for it.", flush=True)
        was = _set_capture_pause(pause_ms)
        if not pause_before:
            pause_before.append(was)
        holder.reset()
        reads = stand_in.reads
        started = _clock()
        code = soak.run_soak(cfg, soak._new_link(), soak._start_sink, answered=text)
        seconds = _clock() - started
        pictures, lost = holder.pictures, holder.was_lost
        window_alive = window.poll() is None  # None: still running; it cannot come back, so dead now is dead at the end
        if code != soak.EXIT_OK:
            raise _Stopped(k, code)
        arm = Arm(k, pause_ms, seconds, pictures, stand_in.reads - reads, measure_arm(cfg.out), window_alive, lost)
        _write_json(cfg.out / ARM_FILE, arm_record(arm))
        arms.append(arm)
        return arm

    result: int | str | None = None
    reason = ""
    stopped: _Stopped | None = None
    try:
        result, reason = judge(play)
    except _Stopped as error:
        stopped = error
    except KeyboardInterrupt:  # outside a soak, which catches its own: between arms or in an arm's analysis (review m5)
        stopped = _Stopped(len(arms) + 1, soak.EXIT_INTERRUPTED, "Ctrl+C")
    finally:
        if pause_before:
            _set_capture_pause(pause_before[0])
    summary = {"commands": args.commands, "seed": args.seed, "rules": RULES,
               "arms": [{**arm_record(arm), "later": arm.later} for arm in arms]}
    if stopped is not None:
        summary["stopped"] = str(stopped)
    else:
        summary.update(result=result, reason=reason)
    _write_json(out / RESULT_FILE, summary)
    for arm in arms:
        print(_arm_line(arm), flush=True)
    if stopped is not None:
        print(f"arm {stopped.k} stopped: {stopped.what}; the arms so far are kept in {out}", flush=True)
        return soak.EXIT_ABORT
    if result == INCONCLUSIVE:
        print(f"result: {INCONCLUSIVE}: {reason}", flush=True)
    else:
        print(f"result: {result} ms: {reason}", flush=True)
    return soak.EXIT_OK


def _at_text(text: str) -> str:
    window_tool.parse_at(text)  # raises argparse.ArgumentTypeError on a bad value
    return text.strip()


def _resolve_out(out: Path) -> Result[Path, str]:
    """`out` resolved, if it is a folder inside the repo's `data/` (as `record_minimap._resolve_out`)."""
    resolved = out.resolve()
    data = DATA.resolve()
    if resolved == data or not resolved.is_relative_to(data):
        return Failure(f"--out must be a folder inside {data}, got {resolved}")
    return Success(resolved)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python tools/key_timing_check.py", description=(
        "Soaks with the capture running flat out on a test window, to see whether the keys get later without a pause "
        "between pictures. Needs the board and the sink; MapleStory must be closed."))
    parser.add_argument("--out", type=Path, default=None, help="result folder, new or empty, inside the repo's data/ "
                        "(default data/keytiming/<YYYYmmdd-HHMMSS>)")
    parser.add_argument("--commands", type=int, default=2000, help="commands per arm (default 2000, about 27 minutes)")
    parser.add_argument("--seed", type=int, default=1, help="the seed of every arm (default 1)")
    parser.add_argument("--at", type=_at_text, default=None, metavar="X,Y",
                        help="where the test window goes (default: the right edge of the primary screen, y 0)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    out = args.out if args.out is not None else DATA / "keytiming" / datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    resolved = _resolve_out(out)
    if not is_successful(resolved):
        print(f"error: {resolved.failure()}", file=sys.stderr, flush=True)
        return soak.EXIT_REFUSED
    out = resolved.unwrap()
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        print(f"error: {out} already exists and is not empty; choose a new --out", file=sys.stderr, flush=True)
        return soak.EXIT_REFUSED
    refusal = soak.validate(_arm_config(args, 1, BASELINE_PAUSE_MS, out))
    if refusal is not None:
        print(refusal, file=sys.stderr, flush=True)
        return soak.EXIT_REFUSED
    holder = CountingFrame()
    positions = runtime.make_positions(holder)  # a bare source, or a Failure (as src/route/session.py checks it)
    if isinstance(positions, Failure):
        print(f"error: {positions.failure()}", file=sys.stderr, flush=True)
        return soak.EXIT_ABORT
    print(f"key-timing check: {args.commands} commands per arm, seed {args.seed}, up to {MAX_ARMS} arms -> {out}\n"
          "starting the test window; it must be on the second screen, uncovered", flush=True)
    stand_in = WalkerStandIn(positions)
    window = _start_window(args.at)
    try:
        runtime.start_capture(holder)
        stand_in.start()
        if not is_successful(runtime.wait_first_frame(holder, FIRST_PICTURE_WAIT_S)):
            print(NO_PICTURE_TEXT, file=sys.stderr, flush=True)
            return EXIT_NO_PICTURE
        return run_check(args, out, holder, stand_in, window)
    finally:
        stand_in.stop()
        _close_window(window)


if __name__ == "__main__":
    sys.exit(main())
