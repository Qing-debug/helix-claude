"""The sim app tool `tools/route_tab_sim.py` (plan RT-12, spec 5.7 and 9; H75). No window, no board, no key."""
import ast
import json
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))  # the tool is a script, not a package

import route_tab_sim  # noqa: E402
from route_tab_sim import ModeSummary  # noqa: E402

from src.route import runtime, session  # noqa: E402
from src.route.session import RunSettings  # noqa: E402
from src.route.sim.link import SimClock  # noqa: E402
from src.route.walker import RealClock  # noqa: E402


def _settings(out: Path) -> RunSettings:
    return RunSettings(map_csv=route_tab_sim.FIXTURE, character="kanna", laps=1, minutes=None, countdown_s=1,
                       seed=None, out=out)


def _record_sessions(monkeypatch) -> list[SimpleNamespace]:
    calls: list[SimpleNamespace] = []

    def fake(settings, shared, **kwargs):
        calls.append(SimpleNamespace(settings=settings, shared=shared, kwargs=kwargs))
        return calls[-1]

    monkeypatch.setattr(route_tab_sim, "RunSession", fake)
    return calls


@pytest.fixture(scope="module")
def setup():
    return route_tab_sim.load_sim().unwrap()


def _write_steps(folder: Path, sends: list[tuple[float | None, float | None]], end: tuple[str, int] | None
                 = ("max_laps", 0)) -> None:
    """A hand-written steps.jsonl: a header line, one `sent` per (reaction_ms, waited_ms), then the `end` event."""
    folder.mkdir(parents=True)
    lines = [{"ev": "run", "version": 1, "source": "walk"}]
    for i, (reaction, waited) in enumerate(sends):
        lines.append({"ev": "leg", "t_ns": i, "lap": 1, "leg": 1, "from": [1, 2], "to": [3, 4]})
        lines.append({"ev": "sent", "t_ns": i, "input": "ok", "reaction_ms": reaction, "waited_ms": waited,
                      "keys": []})
    if end is not None:
        lines.append({"ev": "end", "t_ns": len(sends), "reason": end[0], "code": end[1]})
    (folder / "steps.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines), encoding="utf-8")


def _even_sends(n: int, processing_ms: float = 5.0) -> list[tuple[float, float]]:
    return [(200.0, 200.0 - processing_ms)] * n


# ---- the session factory ------------------------------------------------------------------------------------------

def test_sim_factory_builds_a_session_with_no_hook(monkeypatch, setup, tmp_path):
    calls = _record_sessions(monkeypatch)
    route_tab_sim.sim_factory(setup, app_focus=False)(_settings(tmp_path / "run"), runtime.SharedFrame(),
                                                      lambda event: None, lambda: True, lambda text, error: None)
    kw = calls[0].kwargs
    stop = kw["make_stop"](lambda: None, log=lambda text: None)
    assert isinstance(stop, runtime.StopControl)
    remove = stop._install_hook()
    assert remove() is None  # the no-op remover: no Windows hook was installed
    link = kw["make_link"]()
    assert link.connect().unwrap() is None
    link.close()
    assert kw["sigint"] is False


def test_sim_factory_shares_one_real_time_clock(monkeypatch, setup, tmp_path):
    calls = _record_sessions(monkeypatch)
    log = []
    factory = route_tab_sim.sim_factory(setup, app_focus=False)
    shared = runtime.SharedFrame()
    for _ in range(2):
        factory(_settings(tmp_path / "run"), shared, lambda event: None, lambda: True, lambda t, e: log.append(t))
    first, second = (c.kwargs for c in calls)
    clock = first["make_link"]().sim.clock
    assert clock is first["make_positions"](shared).clock
    assert isinstance(clock, route_tab_sim.RealTimeSimClock)
    assert isinstance(first["make_clock"](), RealClock)
    assert "wait_first_frame" not in first and "start_capture" not in first
    assert second["make_link"]().sim.clock is not clock
    assert second["make_link"]().sim.world is not first["make_link"]().sim.world
    assert calls[0].settings.seed == 1 and calls[0].settings.out == tmp_path / "run"  # the tab passes seed=None
    assert calls[0].shared is shared
    assert first["may_walk"] is None  # measure: the window the tool opened has the keyboard
    first["log"]("a line", False)
    assert log == ["a line"]  # the tab's console-box log is passed on (H71)


def test_sim_factory_passes_the_tabs_may_walk_in_manual(monkeypatch, setup, tmp_path):
    calls = _record_sessions(monkeypatch)

    def may_walk():
        return False

    route_tab_sim.sim_factory(setup, app_focus=True)(_settings(tmp_path / "run"), runtime.SharedFrame(),
                                                     lambda event: None, may_walk, lambda t, e: None)
    assert calls[0].kwargs["may_walk"] is may_walk


def test_route_loads_once_before_any_session(monkeypatch, setup, tmp_path):
    """DBG115: the tool redirects stdout (`session._load_route`) only in `load_sim`, on the main thread before the
    window or any worker; a factory call (on the Tk thread at Start) never does."""
    _record_sessions(monkeypatch)

    def no_load(*args):
        raise AssertionError("_load_route called by the factory")

    monkeypatch.setattr(session, "_load_route", no_load)
    route_tab_sim.sim_factory(setup, app_focus=False)(_settings(tmp_path / "run"), runtime.SharedFrame(),
                                                      lambda event: None, lambda: True, lambda t, e: None)


def test_real_time_sim_clock():
    clock = route_tab_sim.RealTimeSimClock()
    assert isinstance(clock, SimClock)
    assert abs(clock.now_ns() - time.perf_counter_ns()) < 5_000_000
    before_clock, before_perf = clock.now_ns(), time.perf_counter_ns()
    clock.advance(20_000_000)
    assert clock.now_ns() - before_clock >= 20_000_000
    assert time.perf_counter_ns() - before_perf >= 20_000_000
    with pytest.raises(ValueError):
        clock.advance(-1)


def test_frame_pump():
    holder = runtime.SharedFrame()
    pump = route_tab_sim.FramePump(holder)
    pump.start()
    try:
        with holder.minimap_frame_lock:
            first = holder.minimap_frame
        assert isinstance(first, np.ndarray) and first.shape == (82, 162, 3) and first.dtype == np.uint8
        time.sleep(0.1)
        with holder.minimap_frame_lock:
            later = holder.minimap_frame
        assert later is not first
        assert isinstance(later, np.ndarray) and later.tobytes() == first.tobytes()
    finally:
        pump.stop()
    assert not any(t.name == "sim-capture" for t in threading.enumerate())


# ---- the summary, the verdict and the refusals --------------------------------------------------------------------

def test_summary_from_steps(tmp_path):
    # processing = reaction - waited: 0, 10, ..., 180 for 19 waits, then a no-wait send counted at its reaction (300)
    sends = [(200.0 + 10 * i, 200.0) for i in range(19)] + [(300.0, 0.0), (None, None)]
    _write_steps(tmp_path / "cli", sends)
    _write_steps(tmp_path / "app", [(150.0, 140.0), (170.0, 150.0), (130.0, 0.0), (None, 5.0)])
    cli = route_tab_sim.read_sends(tmp_path / "cli", "cli", laps=1).unwrap()
    app = route_tab_sim.read_sends(tmp_path / "app", "app", laps=1).unwrap()
    assert len(cli) == 20 and len(app) == 3  # a sent with no reaction_ms is left out
    s = route_tab_sim.summarize(cli)
    assert s.sends == 20
    assert s.processing_median_ms == pytest.approx(95.0)
    assert s.processing_p95_ms == pytest.approx(180.0)  # nearest rank: ceil(0.95 * 20) = 19th of 20
    assert s.no_wait_share == pytest.approx(0.05)
    assert s.reaction_median_ms == pytest.approx(295.0)
    a = route_tab_sim.summarize(app)
    assert (a.sends, a.processing_median_ms, a.processing_p95_ms) == (3, 20.0, 130.0)  # rank ceil(2.85) = 3
    assert a.no_wait_share == pytest.approx(1 / 3)
    assert a.reaction_median_ms == 150.0


BASE = ModeSummary(sends=300, processing_median_ms=10.0, processing_p95_ms=50.0, no_wait_share=0.0,
                   reaction_median_ms=250.0)


@pytest.mark.parametrize("rule, at, past", [
    ("median_gap", {"processing_median_ms": 30.0}, {"processing_median_ms": 30.5}),
    ("p95", {"processing_p95_ms": 100.0}, {"processing_p95_ms": 100.5}),
    ("no_wait_share", {"no_wait_share": 0.05}, {"no_wait_share": 0.0501}),
])
def test_verdict_rules(rule, at, past):
    import dataclasses

    assert route_tab_sim.verdict(BASE, dataclasses.replace(BASE, **at)) == (False, [])
    assert route_tab_sim.verdict(BASE, dataclasses.replace(BASE, **past)) == (True, [rule])
    assert route_tab_sim.verdict(BASE, BASE) == (False, [])


def test_report_writes_the_table_and_timing_json(tmp_path, capsys):
    _write_steps(tmp_path / "cli", _even_sends(240, 4.0))
    _write_steps(tmp_path / "app", _even_sends(250, 30.0))
    assert route_tab_sim.report(tmp_path, laps=3) == 0
    out = capsys.readouterr().out
    assert "cli" in out and "app" in out and "F12" in out
    data = json.loads((tmp_path / "timing.json").read_text(encoding="utf-8"))
    assert data["laps"] == 3
    assert data["modes"]["cli"]["sends"] == 240 and data["modes"]["app"]["sends"] == 250
    assert data["rules"] == {"MIN_SENDS": 200, "MEDIAN_GAP_MS": 20, "P95_MAX_MS": 100, "NO_WAIT_GAP": 0.05}
    assert data["over_threshold"] is True and data["fired"] == ["median_gap"]
    assert len(data["not_covered"]) == 2


def _refusal_case(name: str, folder: Path) -> None:
    if name == "199 sends":
        _write_steps(folder, _even_sends(199))
    elif name == "stopped":
        _write_steps(folder, _even_sends(300), end=("stopped", 0))
    elif name == "code 10":
        _write_steps(folder, _even_sends(300), end=("off_graph", 10))
    elif name == "no end":
        _write_steps(folder, _even_sends(300), end=None)
    else:  # "no steps.jsonl"
        folder.mkdir(parents=True)


@pytest.mark.parametrize("case", ["199 sends", "stopped", "code 10", "no end", "no steps.jsonl"])
@pytest.mark.parametrize("mode", ["cli", "app"])
def test_refusals(tmp_path, capsys, case, mode):
    other = "app" if mode == "cli" else "cli"
    _write_steps(tmp_path / other, _even_sends(300))
    _refusal_case(case, tmp_path / mode)
    assert route_tab_sim.report(tmp_path, laps=2) == 2
    out = capsys.readouterr().out
    assert "processing_ms" not in out  # no table
    data = json.loads((tmp_path / "timing.json").read_text(encoding="utf-8"))
    assert set(data) == {"over_threshold", "refused"} and data["over_threshold"] is None
    assert mode in data["refused"] and data["refused"] in out
    if case == "199 sends":
        assert "--laps 3" in data["refused"]  # 199 sends in 2 laps: 200 needs ceil(200 * 2 / 199) = 3
    elif case in ("stopped", "code 10"):
        assert ("stopped" in data["refused"]) if case == "stopped" else ("10" in data["refused"])


def test_cli_refusal_opens_no_window(monkeypatch, tmp_path):
    def run_cli(holder, out, setup, **kwargs):
        _write_steps(out, _even_sends(300), end=("stopped", 0))
        return 0

    opened = []
    monkeypatch.setattr(route_tab_sim, "run_cli", run_cli)
    monkeypatch.setattr(route_tab_sim, "MapleGUI", lambda *a, **k: opened.append(a))
    assert route_tab_sim.measure(runtime.SharedFrame(), None, tmp_path, laps=2) == 2
    assert opened == []
    assert json.loads((tmp_path / "timing.json").read_text(encoding="utf-8"))["over_threshold"] is None


def test_tool_never_names_the_board():
    tree = ast.parse((REPO / "tools" / "route_tab_sim.py").read_text(encoding="utf-8"))
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    names |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    modules = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    modules |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names}
    assert "BleLink" not in names | imported
    assert "src.input.link" not in modules


# ---- a headless simulator run on real time (no window, no Start button; not Step 5's measurement) ----------------

@pytest.mark.slow
def test_cli_mode_end_to_end(setup, tmp_path):
    holder = runtime.SharedFrame()
    pump = route_tab_sim.FramePump(holder)
    pump.start()
    try:
        code = route_tab_sim.run_cli(holder, tmp_path / "cli", setup, laps=None, minutes=0.1, countdown_s=0)
    finally:
        pump.stop()
    assert code == 0  # the timer's stop
    lines = (tmp_path / "cli" / "steps.jsonl").read_text(encoding="utf-8").splitlines()
    events = [json.loads(line) for line in lines]
    assert any(e["ev"] == "sent" and (e["waited_ms"] or 0) > 0 for e in events)
    assert len({tuple(e["pos"]) for e in events if e["ev"] == "settled" and e["pos"] is not None}) > 1
    assert (tmp_path / "cli" / "frames" / "index.jsonl").read_text(encoding="utf-8").strip()
    assert events[-1]["ev"] == "end" and events[-1]["code"] == 0
