"""A simulator copy of the app: the Route tab's timing measurement and Hong's section-9 check (plan RT-12; spec 5.7,
section 9; H75).

    python tools/route_tab_sim.py measure [--laps N]
    python tools/route_tab_sim.py manual

No board, no key, no game, no vision capture thread and no screen grab (H58): the link and the positions are the
simulator's, and every part runs on real time, so the walker's waits, the link's plays and the world's settles take as
long as in a board run. A frame pump stores a fixed minimap-sized picture every 20 ms, as the capture loop would, so the
run writes `minimap.png` and its step log, the recorder saves frames and the live view redraws.

`measure` walks the same settings twice: "cli" (`RunSession.run()` on the main thread, no Tk), then "app" (a real
`MapleGUI` window whose Route tab presses Start itself, 500 ms after the window starts, and closes once the tab's
worker is gone; the finish is read from steps.jsonl). It compares the PC's time from the stimulus to the send
(`reaction_ms - waited_ms`) of the two runs, prints a table and writes `timing.json` into the run root. Exit 0 with a
verdict, whatever it says; exit 2 on a refusal (a run that did not finish all its laps, too few sends) or a setup
failure. An agent may run it on an idle PC (H75): the window it opens takes the keyboard.

`manual` opens the same sim app with the tab's own app-focus check and waits for Hong (plan H-2); exit 0 when the window
closes. It stays Hong's (H75). F12 does nothing in the sim app: its stop control installs no keyboard hook.

Run folders: `data/route_tab_sim/<stamp>/` (`cli/`, `app/` and `timing.json` in `measure`; one folder per Start in
`manual`), never `data/route`, so sim runs stay out of the Saved runs list.
"""
import argparse
import dataclasses
import json
import math
import statistics
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:  # run as a script, sys.path[0] is tools/; imports are rooted at `src.`
    sys.path.insert(0, str(REPO))

from returns.pipeline import is_successful  # noqa: E402
from returns.result import Failure, Result, Success  # noqa: E402

from src.classes.classes import Kanna  # noqa: E402
from src.controller.controller import BuildOutcome, Controller  # noqa: E402
from src.gui.gui import MapleGUI  # noqa: E402
from src.gui.route_tab import RouteTab, SessionFactory  # noqa: E402
from src.route import runtime, session  # noqa: E402
from src.route.session import FrameHolder, RunEvent, RunSession, RunSettings  # noqa: E402
from src.route.sim.link import SimClock, SimLink, SimPositionSource  # noqa: E402
from src.route.sim.world import NoiseConfig, SimParams, SimTerrain, SimWorld, load_terrain  # noqa: E402
from src.route.step_log import STEPS_FILE  # noqa: E402
from src.route.walker import RealClock  # noqa: E402

FIXTURE = REPO / "tests" / "fixtures" / "fossil_foundry_2.csv"
TERRAIN = REPO / "tests" / "fixtures" / "ff2_terrain.json"
DATA_ROOT = REPO / "data" / "route_tab_sim"
START = (113.0, 60.0)  # the walk tests' start on FF2 (tests/route/test_walk_cli.py, `sim` fixture)
SEED = 1               # both modes draw the same timing and planner numbers (the tab's own runs carry seed=None)
CHARACTER = "kanna"

MEASURE_LAPS = 14      # ceil(240 / 18): one headless lap sent 18 times in 18.1 s (plan RT-12 Step 3, 2026-11-01)
MIN_SENDS = 200        # the verdict's thresholds are provisional (plan review RA4 I1; the spec sets none)
MEDIAN_GAP_MS = 20
P95_MAX_MS = 100
NO_WAIT_GAP = 0.05
RULES = {"MIN_SENDS": MIN_SENDS, "MEDIAN_GAP_MS": MEDIAN_GAP_MS, "P95_MAX_MS": P95_MAX_MS, "NO_WAIT_GAP": NO_WAIT_GAP}
NOT_COVERED = (
    "the F12 hook's own latency (the sim app's stop control installs no keyboard hook)",
    "the real capture thread's load (the frame pump only stores a copy of one picture)",
    "finding the character on a real frame (the sim's position source never looks at a real frame, so the cost of "
    "that and its contention with the Tk thread are not in the figures)",
)
MODES = ("cli", "app")

FRAME_SHAPE = (82, 162, 3)   # FF2's minimap, `# minimap_size=162,82` (tests/fixtures/fossil_foundry_2.csv:1)
PUMP_PERIOD_S = 0.020        # the capture loop's pace: a 10 ms key wait plus its 10 ms sleep before RT-11; since H77
                             # the wait in Windows for the game's next frame plus a 10 ms floor (src/vision/vision.py)
START_AFTER_MS = 500         # measure: Start is pressed this long after the window starts
APP_COUNTDOWN_S = 1
WATCH_MS = 200               # measure: how often the tool looks for the tab's end


class RealTimeSimClock(SimClock):
    """A `SimClock` on `time.perf_counter_ns`, the time base of `RealClock` and of `InputLayer`'s stimulus clock:
    `advance` sleeps until at least `ns` have passed, so a play and a settle take real time."""

    def __init__(self) -> None:
        super().__init__(time.perf_counter_ns())

    def now_ns(self) -> int:
        return time.perf_counter_ns()

    def advance(self, ns: int) -> None:
        if ns < 0:
            raise ValueError(f"time cannot go backwards, got {ns} ns")
        end = time.perf_counter_ns() + int(ns)
        while (left := end - time.perf_counter_ns()) > 0:
            time.sleep(left / 1e9)


class SimRunLink:
    """The `SimLink` with `connect` and `close` (what `RunSession` needs of a link); the simulator link is `.sim`."""

    def __init__(self, sim: SimLink) -> None:
        self.sim = sim

    def connect(self) -> Result[object, str]:
        return Success(None)

    def play(self, steps):
        return self.sim.play(steps)

    def release_all(self):
        return self.sim.release_all()

    def close(self) -> None:
        return None


def _no_hook() -> None:
    return None


class NoHookStopControl(runtime.StopControl):
    """The real stop control (Stop, the app-focus stop, the timer) with no Windows keyboard hook: F12 does nothing."""

    def _install_hook(self) -> Callable[[], None]:
        return _no_hook


@dataclasses.dataclass(frozen=True)
class SimSetup:
    reach_h: int
    reach_v: int
    terrain: SimTerrain


def load_sim() -> Result[SimSetup, str]:
    """The route's reach and the FF2 terrain, read once on the main thread before the window or any run starts. Its
    `redirect_stdout` (in `session._load_route`) runs before any thread exists. Each run's own route load ends before
    that run's first event and the live view's model load starts only after `running`, so no two swaps overlap
    (DBG115)."""
    route = session._load_route(FIXTURE, Kanna())
    if not is_successful(route):
        return Failure(route.failure())
    terrain = load_terrain(TERRAIN)
    if not is_successful(terrain):
        return Failure(f"terrain {TERRAIN}: {terrain.failure()}")
    rg, _rotation = route.unwrap()
    return Success(SimSetup(rg.reach_h, rg.reach_v, terrain.unwrap()))


def sim_factory(setup: SimSetup, *, app_focus: bool) -> SessionFactory:
    """The Route tab's `make_session` (and the "cli" run's): every call builds one `RealTimeSimClock`, a fresh world on
    it (noise off, the walk tests' start), its link and its positions. `app_focus` passes the tab's `may_walk` on
    (manual); without it the session gets none (measure: the window the tool opened has the keyboard)."""

    def make(settings: RunSettings, shared: FrameHolder, on_event: Callable[[RunEvent], None],
             may_walk: Callable[[], bool], log: Callable[[str, bool], None]) -> RunSession:
        clock = RealTimeSimClock()
        params = SimParams()
        world = SimWorld(setup.terrain, setup.reach_h, setup.reach_v, params, NoiseConfig.off(),
                         np.random.default_rng(0), START)
        link = SimRunLink(SimLink(world, clock, params))
        positions = SimPositionSource(world, clock, 0.0, np.random.default_rng(1))
        return RunSession(dataclasses.replace(settings, seed=SEED), shared, on_event=on_event,
                          make_positions=lambda _shared: positions, make_link=lambda: link, make_clock=RealClock,
                          make_stop=NoHookStopControl, may_walk=may_walk if app_focus else None, sigint=False,
                          log=log)

    return make


class FramePump:
    """Stores a copy of one fixed RGB picture in `holder` every 20 ms on the daemon thread `sim-capture`; the first is
    stored by `start` itself."""

    def __init__(self, holder: FrameHolder) -> None:
        self._holder = holder
        self._frame = np.broadcast_to(np.arange(FRAME_SHAPE[1], dtype=np.uint8)[None, :, None], FRAME_SHAPE).copy()
        self._stop = threading.Event()
        self.thread: threading.Thread | None = None

    def _store(self) -> None:
        frame = self._frame.copy()
        with self._holder.minimap_frame_lock:
            self._holder.minimap_frame = frame  # pyright: ignore[reportAttributeAccessIssue]

    def _pump(self) -> None:
        while not self._stop.wait(PUMP_PERIOD_S):
            self._store()

    def start(self) -> None:
        self._store()
        self.thread = threading.Thread(target=self._pump, name="sim-capture", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self.thread is not None:
            self.thread.join()


def _ignore(event: RunEvent) -> None:
    return None


def run_cli(holder: FrameHolder, out: Path, setup: SimSetup, *, laps: int | None, minutes: float | None,
            countdown_s: float) -> int:
    """The "cli" mode: one sim session's `run()` on this thread, no Tk; its lines print as the walk's do."""
    settings = RunSettings(map_csv=FIXTURE, character=CHARACTER, laps=laps, minutes=minutes, countdown_s=countdown_s,
                           seed=None, out=out)
    return sim_factory(setup, app_focus=False)(settings, holder, _ignore, lambda: True, session._print_line).run()


def build_app(holder: FrameHolder, setup: SimSetup, *, run_root: Path, new_out: Callable[[], Path],
              app_focus: bool) -> Result[MapleGUI, str]:
    """The app as `src/main.py` builds it, with the class and the FF2 map set, a Route tab on the sim factory and no
    frame checks (spec 5.4 checks 3-4: the pumped picture has no character), its runs under `run_root`."""
    controller = Controller(sharedInfo=holder, CSV=None, characterClass=None)
    controller.setCharacterClass(Kanna())
    loaded = controller.loadMap(FIXTURE)
    if not is_successful(loaded) or loaded.unwrap() is not BuildOutcome.BUILT:
        return Failure(f"the sim app could not load {FIXTURE}: {loaded}")

    def make_tab(parent, gui) -> RouteTab:
        return RouteTab(parent, gui, make_session=sim_factory(setup, app_focus=app_focus), run_root=run_root,
                        new_out=new_out, frame_checks=False)

    return Success(MapleGUI(controller, holder, route_tab_factory=make_tab))


def run_app(holder: FrameHolder, setup: SimSetup, run_root: Path, laps: int) -> str | None:
    """The "app" mode: the window, Start pressed by the tool, the window closed once the tab's worker is gone. None
    when the run went through the tab, else why it did not start.

    The watch waits for the worker, not for the Ended state: the drain step that clears a dead worker has shown the
    run's end (`ended`, or the watchdog's) and then refreshes readiness in the same step, which turns Ended into Ready
    when the fields changed since its last look, as they do here (set in the same call as Start). Whether the run
    finished is read from its `steps.jsonl` afterwards (`read_sends`)."""
    built = build_app(holder, setup, run_root=run_root, new_out=lambda: run_root / "app", app_focus=False)
    if not is_successful(built):
        return built.failure()
    gui = built.unwrap()
    problem: list[str] = []

    def watch() -> None:
        tab = gui.route_tab
        if tab is not None and tab.worker is None:  # armed only once Start started a worker
            gui.gui.destroy()
        else:
            gui.gui.after(WATCH_MS, watch)

    def press_start() -> None:
        try:
            tab = gui.route_tab  # looked up now: `start()` builds the tab (RTB-PF f3)
            assert tab is not None
            gui.notebook.select(gui.route_tab_frame)
            tab.laps_var.set(str(laps))
            tab.minutes_var.set("")
            tab.countdown_var.set(str(APP_COUNTDOWN_S))
            tab.startRun()
            if tab.worker is None:
                problem.append(f"app run not started: {tab.status_var.get()}")
                gui.gui.destroy()
                return
        except Exception as exc:
            problem.append(f"app run not started: {type(exc).__name__}: {exc}")
            gui.gui.destroy()
            return
        gui.gui.after(WATCH_MS, watch)

    gui.gui.after(START_AFTER_MS, press_start)
    gui.start()
    return problem[0] if problem else None


def read_sends(folder: Path, mode: str, laps: int) -> Result[list[tuple[float, float]], str]:
    """(reaction_ms, waited_ms) of every `sent` event that carries both, from a run that finished all its laps (its last
    event `end` with reason `max_laps` and code 0; `stop.json` is written on every end, so it proves nothing), or the
    refusal's text."""
    path = folder / STEPS_FILE
    if not path.is_file():
        return Failure(f"{mode} run: no {STEPS_FILE} in {folder}")
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and "t_ns" in event:
            events.append(event)
    if not events or events[-1].get("ev") != "end":
        return Failure(f"{mode} run: its step log has no end event; the run did not finish")
    end = events[-1]
    if end.get("reason") != "max_laps" or end.get("code") != 0:
        return Failure(f"{mode} run ended with reason {end.get('reason')}, code {end.get('code')}: not all {laps} "
                       "laps done")
    return Success([(float(e["reaction_ms"]), float(e["waited_ms"])) for e in events
                    if e.get("ev") == "sent" and isinstance(e.get("reaction_ms"), (int, float))
                    and isinstance(e.get("waited_ms"), (int, float))])


def checked_sends(folder: Path, mode: str, laps: int) -> Result[list[tuple[float, float]], str]:
    """`read_sends`, refused also below `MIN_SENDS`, with the `--laps` that would reach it at this run's sends per lap."""
    def enough(sends: list[tuple[float, float]]) -> Result[list[tuple[float, float]], str]:
        if len(sends) >= MIN_SENDS:
            return Success(sends)
        hint = (f"run again with --laps {math.ceil(MIN_SENDS * laps / len(sends))}" if sends
                else "no send to scale from")
        return Failure(f"{mode} run: {len(sends)} sends, fewer than {MIN_SENDS}; {hint}")

    return read_sends(folder, mode, laps).bind(enough)


@dataclasses.dataclass(frozen=True)
class ModeSummary:
    sends: int
    processing_median_ms: float
    processing_p95_ms: float
    no_wait_share: float
    reaction_median_ms: float


def summarize(sends: list[tuple[float, float]]) -> ModeSummary:
    """`processing_ms = reaction_ms - waited_ms` (a no-wait send counts at its `reaction_ms`, a lower bound): its median
    and its p95 by nearest rank (the value at rank ceil(0.95 n) of the sorted list); the share of sends with no wait;
    the median `reaction_ms`."""
    processing = sorted(r - w for r, w in sends)
    rank = (95 * len(processing) + 99) // 100
    return ModeSummary(sends=len(sends), processing_median_ms=statistics.median(processing),
                       processing_p95_ms=processing[rank - 1],
                       no_wait_share=sum(1 for _r, w in sends if w == 0) / len(sends),
                       reaction_median_ms=statistics.median(r for r, _w in sends))


def verdict(cli: ModeSummary, app: ModeSummary) -> tuple[bool, list[str]]:
    """Over the threshold when any rule fires; the names of those that do."""
    fired = []
    if app.processing_median_ms - cli.processing_median_ms > MEDIAN_GAP_MS:
        fired.append("median_gap")
    if app.processing_p95_ms > P95_MAX_MS:
        fired.append("p95")
    if app.no_wait_share - cli.no_wait_share > NO_WAIT_GAP:
        fired.append("no_wait_share")
    return bool(fired), fired


def _write_timing(run_root: Path, payload: dict) -> None:
    run_root.mkdir(parents=True, exist_ok=True)
    (run_root / "timing.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def refuse(run_root: Path, reason: str) -> int:
    print(f"No verdict: {reason}")
    _write_timing(run_root, {"over_threshold": None, "refused": reason})
    return 2


def report(run_root: Path, laps: int) -> int:
    """Read `run_root/cli` and `run_root/app`, print the table and write `timing.json`: 0 with a verdict, 2 refused."""
    summaries: dict[str, ModeSummary] = {}
    for mode in MODES:
        sends = checked_sends(run_root / mode, mode, laps)
        if not is_successful(sends):
            return refuse(run_root, sends.failure())
        summaries[mode] = summarize(sends.unwrap())
    over, fired = verdict(summaries["cli"], summaries["app"])
    print(f"{'mode':<5}{'sends':>7}{'processing_ms median':>22}{'processing_ms p95':>19}{'no-wait share':>15}"
          f"{'reaction_ms median':>20}")
    for mode, s in summaries.items():
        print(f"{mode:<5}{s.sends:>7}{s.processing_median_ms:>22.1f}{s.processing_p95_ms:>19.1f}"
              f"{s.no_wait_share:>15.1%}{s.reaction_median_ms:>20.1f}")
    print(f"over_threshold: {'true' if over else 'false'}" + (f" (fired: {', '.join(fired)})" if fired else ""))
    print("Not covered: " + "; ".join(NOT_COVERED) + ".")
    _write_timing(run_root, {"laps": laps, "modes": {m: dataclasses.asdict(s) for m, s in summaries.items()},
                             "rules": RULES, "over_threshold": over, "fired": fired, "not_covered": list(NOT_COVERED)})
    print(f"Wrote {run_root / 'timing.json'}")
    return 0


def measure(holder: FrameHolder, setup: SimSetup, run_root: Path, laps: int) -> int:
    """"cli" first; its finish and send count are checked before the window opens, so a refusal there costs no app
    run. Then "app", then the report."""
    print(f"cli run: {laps} laps in {run_root / 'cli'}")
    run_cli(holder, run_root / "cli", setup, laps=laps, minutes=None, countdown_s=APP_COUNTDOWN_S)
    cli = checked_sends(run_root / "cli", "cli", laps)
    if not is_successful(cli):
        return refuse(run_root, cli.failure())
    print(f"app run: {laps} laps in {run_root / 'app'} (a window opens and takes the keyboard)")
    problem = run_app(holder, setup, run_root, laps)
    if problem is not None:
        return refuse(run_root, problem)
    return report(run_root, laps)


def run_manual(holder: FrameHolder, setup: SimSetup, run_root: Path) -> int:
    """The sim app for Hong's section-9 check (plan H-2): the tab's own `may_walk`, a new folder per Start."""
    built = build_app(holder, setup, run_root=run_root, new_out=lambda: run_root / session.new_out().name,
                      app_focus=True)
    if not is_successful(built):
        print(built.failure())
        return 2
    gui = built.unwrap()
    print(f"Sim app open (no board, no game, no key sent); runs go to {run_root}. Close the window to end.")
    gui.start()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="route_tab_sim", description="A simulator copy of the app (no board, no key, "
                                     "no game): the Route tab's timing measurement and the section-9 check.")
    modes = parser.add_subparsers(dest="mode", required=True)
    measure_parser = modes.add_parser("measure", help="the in-app timing measurement (an agent may run it, H75)")
    measure_parser.add_argument("--laps", type=int, default=MEASURE_LAPS)
    modes.add_parser("manual", help="the sim app for Hong's section-9 check")
    args = parser.parse_args(argv)
    if args.mode == "measure" and args.laps < 1:
        parser.error("--laps must be at least 1")
    setup = load_sim()
    if not is_successful(setup):
        print(f"error: {setup.failure()}")
        return 2
    run_root = DATA_ROOT / session.new_out().name
    holder = runtime.SharedFrame()
    pump = FramePump(holder)
    pump.start()
    try:
        if args.mode == "manual":
            return run_manual(holder, setup.unwrap(), run_root)
        return measure(holder, setup.unwrap(), run_root, args.laps)
    finally:
        pump.stop()


if __name__ == "__main__":
    sys.exit(main())
