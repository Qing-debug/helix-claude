"""Tests of the route view's live follower and its start-up handshake (route view plan task RV-8, tests 1-9; contract
V6; spec 5.4, 5.7, 5.9 step 3).

Every Tk root is real and withdrawn (`view_tk.open_root`), has no mainloop and records `after`; `withdraw` and
`deiconify` are replaced by recorders that never call Tk's own, so nothing is ever shown and nothing takes focus. Each
root is destroyed at teardown. The three Win32 calls are a `FakeFocus` that records them, the clock a `FakeClock`.
"""

import ctypes
import os
import statistics
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image
from returns.result import Failure, Success
from test_view_window import _noise_png
from view_runs import FF2, G, GIT_HEAD, REPO_ROOT, default_header, event_lines, make_folder, make_legs_folder, ns
from view_tk import destroy_all, open_root, record_after

from src.route.step_log import MAP_COPY, STEPS_FILE, VIEW_CLAIM, VIEW_READY, header_line, parse_line
from src.route_view import __main__ as cli
from src.route_view import win_focus
from src.route_view.follow import POLL_MS, SILENT_S, Follower, LiveController, Polled
from src.route_view.model import FRAME_INDEX, FRAMES_DIR
from src.route_view.window import RouteViewWindow

SILENT = "no new steps for 10 s: the walk is still running or has crashed"
HINT = "if the walk has ended, open this folder with --replay to step through it"
CLICK_WARNING = "clicking here takes focus from the game"
HEADER = header_line(default_header()).unwrap()
LINES = event_lines(G)  # 26 lines; the last is `end`


class FakeFocus:
    """`win_focus`'s three functions: records `(name, args)` (the root left out) in `calls` and in a shared `log`."""

    def __init__(self, log=None, *, style=None, real=False, claim: Path | None = None):
        self.calls = []
        self.log = [] if log is None else log
        self.style = Success(None) if style is None else style
        self.real = real  # also ask the real `win_focus.wrapper_hwnd` (test 9a)
        self.real_wrapper = None
        self.claim = claim  # if given, whether it existed at each call is recorded in `claim_seen`
        self.claim_seen = []

    def _record(self, name, args):
        self.calls.append((name, args))
        self.log.append(name)
        if self.claim is not None:
            self.claim_seen.append(self.claim.exists())

    def wrapper_hwnd(self, root):
        self._record("wrapper_hwnd", ())
        if self.real:
            self.real_wrapper = win_focus.wrapper_hwnd(root)
        return Success(1234)

    def set_live_style(self, hwnd, live):
        self._record("set_live_style", (hwnd, live))
        return self.style

    def show_first(self, root, hwnd):
        self._record("show_first", (hwnd,))
        return Success(None)


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


@pytest.fixture
def roots():
    made = []
    yield made
    destroy_all(made)


def _recorded_root(roots, monkeypatch, log):
    root = open_root()
    roots.append(root)
    after = record_after(root)
    monkeypatch.setattr(root, "withdraw", lambda: log.append("withdraw"))
    monkeypatch.setattr(root, "deiconify", lambda: log.append("deiconify"))
    return root, after


def _live(roots, monkeypatch, folder, *, hidden=False, styled_hwnd=None, focus=None, clock=None):
    log = []
    root, after = _recorded_root(roots, monkeypatch, log)
    window = RouteViewWindow(root, folder, None, live=True)
    focus = focus or FakeFocus()
    live = LiveController(root, window, folder, repo_root=REPO_ROOT, current_git_head=GIT_HEAD, focus=focus,
                          clock=clock or FakeClock(), hidden=hidden, styled_hwnd=styled_hwnd)
    return SimpleNamespace(root=root, after=after, log=log, window=window, live=live, focus=focus)


def _live_folder(folder: Path, lines) -> Path:
    (folder / MAP_COPY).write_bytes(FF2.read_bytes())
    (folder / STEPS_FILE).write_bytes(HEADER + b"".join(lines))
    return folder


def _append(path: Path, data: bytes) -> None:
    with path.open("ab") as f:
        f.write(data)


def _index_line(i: int, seconds: float) -> bytes:
    return f'{{"i": {i}, "t_ns": {ns(seconds)}, "file": "frame_{i:06d}.png", "w": 162, "h": 82}}\r\n'.encode()


def _add_frame(folder: Path, i: int, seconds: float, data: bytes | None = None) -> None:
    """A PNG written to a temporary name and moved into place, then its index line (record_minimap's order)."""
    frames = folder / FRAMES_DIR
    frames.mkdir(exist_ok=True)
    tmp = frames / f"frame_{i:06d}.png.tmp"
    if data is None:
        Image.new("RGB", (162, 82), ((40 * i) % 256, 120, 200)).save(tmp, "PNG")
    else:
        tmp.write_bytes(data)
    os.replace(tmp, frames / f"frame_{i:06d}.png")
    _append(frames / FRAME_INDEX, _index_line(i, seconds))


def _status(window) -> str:
    return window._status.get()


def _spy_loads(monkeypatch, window) -> list[str]:
    loads = []
    real = window._load_image

    def load(path):
        loads.append(Path(path).name)
        return real(path)

    monkeypatch.setattr(window, "_load_image", load)
    return loads


# Follower ---------------------------------------------------------------------------------------------------------


def test_the_follower_reads_complete_lines_of_both_files_and_counts_bad_ones(tmp_path):
    follower = Follower(tmp_path)
    assert follower.poll() == Polled(None, [], [], 0)  # no files yet: nothing, no error
    _live_folder(tmp_path, LINES[:2])
    _append(tmp_path / STEPS_FILE, LINES[2][:7])
    (tmp_path / FRAMES_DIR).mkdir()
    _append(tmp_path / FRAMES_DIR / FRAME_INDEX, b"garbage\r\n" + _index_line(0, 1.05) + _index_line(1, 1.5)[:9])
    first = follower.poll()
    assert first.header == default_header()
    assert first.events == [parse_line(line).unwrap() for line in LINES[:2]]
    assert first.frames == [(ns(1.05), "frame_000000.png")] and first.bad_lines == 1
    _append(tmp_path / STEPS_FILE, LINES[2][7:])
    _append(tmp_path / FRAMES_DIR / FRAME_INDEX, _index_line(1, 1.5)[9:])
    second = follower.poll()
    assert second.header is None and second.events == [parse_line(LINES[2]).unwrap()]
    assert second.frames == [(ns(1.5), "frame_000001.png")] and second.bad_lines == 1
    assert follower.poll() == Polled(None, [], [], 1)


# 1 ----------------------------------------------------------------------------------------------------------------


@pytest.mark.slow
def test_1_following_a_folder_while_a_writer_thread_writes_it(tmp_path, roots, monkeypatch):
    """S7: every line arrives in two writes 20 ms apart; a tick never shows a half line or misses a whole one."""
    folder = tmp_path
    (folder / MAP_COPY).write_bytes(FF2.read_bytes())
    (folder / STEPS_FILE).write_bytes(b"")
    t = _live(roots, monkeypatch, folder)
    lock = threading.Lock()
    done = {"lines": 0, "frames": 0}

    def halves(path: Path, data: bytes) -> None:
        with path.open("ab") as f:
            f.write(data[: len(data) // 2])
            f.flush()
            time.sleep(0.02)
            f.write(data[len(data) // 2:])

    def writer():
        halves(folder / STEPS_FILE, HEADER)
        for i, line in enumerate(LINES):
            halves(folder / STEPS_FILE, line)
            with lock:
                done["lines"] = i + 1
            if i + 1 < len(LINES):  # a frame between steps
                frames = folder / FRAMES_DIR
                frames.mkdir(exist_ok=True)
                tmp = frames / f"frame_{i:06d}.png.tmp"
                Image.new("RGB", (162, 82), ((9 * i) % 256, 100, 200)).save(tmp, "PNG")
                os.replace(tmp, frames / f"frame_{i:06d}.png")
                halves(frames / FRAME_INDEX, _index_line(i, G[i][1] + 0.01))
                with lock:
                    done["frames"] = i + 1

    expected = [parse_line(line).unwrap() for line in LINES]
    thread = threading.Thread(target=writer)
    thread.start()
    ticks = 0
    try:
        while True:
            finished = not thread.is_alive()
            with lock:
                before = dict(done)
            t.live.tick()
            with lock:
                after = dict(done)
            ticks += 1
            model = t.window.model
            n = len(model.events) if model is not None else 0
            assert before["lines"] <= n <= after["lines"], (before, n, after)
            if model is not None:
                assert model.events == expected[:n]  # never a half line
                if n:
                    assert t.window.index == n - 1
            if before["frames"]:
                newest = {f"frames/frame_{j:06d}.png" for j in range(before["frames"] - 1, after["frames"])}
                assert t.window.shown_background in newest and t.window.background_ok
            if finished:
                break
            time.sleep(0.01)
    finally:
        thread.join()
    model = t.window.model
    assert model is not None and len(model.events) == 26 and model.ended and t.live.ended
    assert t.window.bar.winfo_manager() == "pack" and not t.window.live
    assert t.root.title().startswith("Route view · ended: max_laps")
    assert t.window.shown_background == "frames/frame_000024.png"
    scheduled = len(t.after)
    assert scheduled < ticks  # the tick that read `end` scheduled nothing
    t.live.tick()
    assert len(t.after) == scheduled


# 2 ----------------------------------------------------------------------------------------------------------------


def test_2_a_tick_decodes_only_the_newest_frame(tmp_path, roots, monkeypatch):
    folder = _live_folder(tmp_path, LINES[:5])
    t = _live(roots, monkeypatch, folder)
    t.live.tick()
    loads = _spy_loads(monkeypatch, t.window)
    for i, seconds in enumerate((1.05, 1.5, 1.9)):
        _add_frame(folder, i, seconds)
    t.live.tick()
    assert loads == ["frame_000002.png"]
    assert t.window.shown_background == "frames/frame_000002.png" and t.window.background_ok
    t.live.tick()  # nothing new: no redraw, no decode
    assert loads == ["frame_000002.png"]


# 3 ----------------------------------------------------------------------------------------------------------------


def test_3_a_half_written_newest_frame_keeps_the_last_good_picture_and_is_read_again(tmp_path, roots, monkeypatch):
    folder = _live_folder(tmp_path, LINES[:5])
    _add_frame(folder, 0, 1.05)
    t = _live(roots, monkeypatch, folder)
    t.live.tick()
    assert t.window.shown_background == "frames/frame_000000.png"
    photo = t.window.shown_photo
    loads = _spy_loads(monkeypatch, t.window)
    whole = _noise_png((200, 40, 40))
    _add_frame(folder, 1, 1.5, whole[: len(whole) // 2])
    t.live.tick()
    assert loads == ["frame_000001.png"] and not t.window.background_ok
    assert t.window.shown_background == "frames/frame_000000.png" and t.window.shown_photo is photo
    t.live.tick()  # no new line in either file: the same file is tried again
    assert loads == ["frame_000001.png"] * 2 and t.window.shown_background == "frames/frame_000000.png"
    (folder / FRAMES_DIR / "frame_000001.png").write_bytes(whole)
    t.live.tick()
    assert loads == ["frame_000001.png"] * 3
    assert t.window.shown_background == "frames/frame_000001.png" and t.window.background_ok
    t.live.tick()  # whole now: no more retries
    assert loads == ["frame_000001.png"] * 3


# 4 ----------------------------------------------------------------------------------------------------------------


def test_4_a_torn_last_line_and_ten_silent_seconds(tmp_path, roots, monkeypatch):
    """Review Focus 1: a walk that crashed (or is still running) mid-line."""
    folder = _live_folder(tmp_path, [*LINES[:5], LINES[5][:12]])
    clock = FakeClock()
    t = _live(roots, monkeypatch, folder, clock=clock)
    t.live.tick()
    assert len(t.window.model.events) == 5
    assert t.root.title().startswith("Route view · LIVE · ") and SILENT not in t.root.title()
    clock.t = SILENT_S - 0.1
    t.live.tick()
    assert SILENT not in t.root.title() and "--replay" not in _status(t.window)
    clock.t = SILENT_S
    t.live.tick()
    assert t.root.title().startswith(f"Route view · LIVE · {SILENT} · ")
    assert _status(t.window) == HINT and "--replay" in HINT
    assert t.after[-1] == (POLL_MS, t.live.tick)  # it keeps following
    _append(folder / STEPS_FILE, LINES[5][12:])
    clock.t = SILENT_S + 5
    t.live.tick()
    assert len(t.window.model.events) == 6
    assert t.root.title().startswith("Route view · LIVE · ") and SILENT not in t.root.title()
    assert "--replay" not in _status(t.window)
    clock.t = 2 * SILENT_S + 5  # ten more silent seconds from the sixth line
    t.live.tick()
    assert SILENT in t.root.title()


# 5 ----------------------------------------------------------------------------------------------------------------


@pytest.mark.slow
def test_5_one_more_line_on_fifty_thousand_is_cheap(tmp_path, roots, monkeypatch):
    """Review Focus 5: RV-6 test 11's 50,000 events without the final `end` (PF81 m9)."""
    copies = 2000
    events = []
    for c in range(copies):
        events.extend((ev, seconds + 10 * c, *values) for ev, seconds, *values in G[:25])
    assert len(events) == 50_000
    folder = _live_folder(tmp_path, event_lines(events))
    t = _live(roots, monkeypatch, folder)
    t.live.tick()
    assert len(t.window.model.events) == 50_000 and not t.live.ended
    inserts = []
    real_insert = t.window.why_text.insert

    def insert(*args, **kwargs):
        inserts.append(args)
        return real_insert(*args, **kwargs)

    monkeypatch.setattr(t.window.why_text, "insert", insert)
    times = []
    more = event_lines([(ev, seconds + 10 * copies, *values) for ev, seconds, *values in G[:5]])
    for j, line in enumerate(more):
        inserts.clear()
        _append(folder / STEPS_FILE, line)
        started = time.perf_counter()
        t.live.tick()
        times.append(time.perf_counter() - started)
        assert len(t.window.model.events) == 50_001 + j and len(inserts) == 1
    print(f"tick with one new line on 50,000 events: median {statistics.median(times) * 1000:.1f} ms")
    assert statistics.median(times) < 0.05


# 6 ----------------------------------------------------------------------------------------------------------------


def test_6_at_end_a_styled_window_drops_the_style_and_is_shown_again(tmp_path, roots, monkeypatch):
    folder = _live_folder(tmp_path, LINES[:25])
    t = _live(roots, monkeypatch, folder, styled_hwnd=1234)
    t.live.tick()
    assert t.after == [(POLL_MS, t.live.tick)] and t.log == [] and t.focus.calls == []
    _append(folder / STEPS_FILE, LINES[25])
    t.live.tick()
    assert t.focus.calls == [("set_live_style", (1234, False))]
    assert t.log == ["withdraw", "deiconify"]
    assert not t.window.live and t.window.bar.winfo_manager() == "pack"
    assert t.root.title().startswith("Route view · ended: max_laps · ")
    assert len(t.after) == 1 and t.live.ended  # no further tick
    t.live.tick()
    assert len(t.after) == 1 and t.log == ["withdraw", "deiconify"] and len(t.focus.calls) == 1


def test_6_at_end_a_hidden_window_is_only_shown(tmp_path, roots, monkeypatch):
    folder = _live_folder(tmp_path, LINES[:25])
    t = _live(roots, monkeypatch, folder, hidden=True)
    t.live.tick()
    assert t.log == []
    _append(folder / STEPS_FILE, LINES[25])
    t.live.tick()
    assert t.log == ["deiconify"] and t.focus.calls == []
    assert not t.window.live and t.root.title().startswith("Route view · ended: max_laps · ")


def test_6_a_folder_that_ended_before_the_first_poll_touches_neither_style_nor_visibility(tmp_path, roots, monkeypatch):
    folder = _live_folder(tmp_path, LINES)
    t = _live(roots, monkeypatch, folder)
    t.live.tick()
    assert t.log == [] and t.focus.calls == [] and not t.window.live and t.after == []


# 7 ----------------------------------------------------------------------------------------------------------------


def test_7_a_second_run_line_and_a_line_that_is_not_json_are_counted_and_skipped(tmp_path, roots, monkeypatch):
    folder = _live_folder(tmp_path, LINES[:3])
    t = _live(roots, monkeypatch, folder)
    t.live.tick()
    model = t.window.model
    assert model is not None and t.live.bad_lines == 0 and _status(t.window) == ""
    _append(folder / STEPS_FILE, HEADER)
    t.live.tick()
    assert t.window.model is model and len(model.events) == 3
    assert t.live.bad_lines == 1 and _status(t.window) == "1 bad lines skipped"
    _append(folder / STEPS_FILE, b"not json\n" + LINES[3])
    t.live.tick()
    assert t.window.model is model and len(model.events) == 4  # the events after a bad line still count
    assert t.live.bad_lines == 2 and _status(t.window) == "2 bad lines skipped"


# 8 ----------------------------------------------------------------------------------------------------------------


def test_8_an_error_inside_a_tick_goes_to_the_status_once_and_following_goes_on(tmp_path, roots, monkeypatch, capsys):
    folder = _live_folder(tmp_path, LINES[:3])
    t = _live(roots, monkeypatch, folder)
    t.live.tick()
    capsys.readouterr()

    def boom(events):
        raise RuntimeError("x")

    monkeypatch.setattr(t.window.model, "extend", boom)
    _append(folder / STEPS_FILE, LINES[3])
    t.live.tick()
    assert _status(t.window) == "follow error: RuntimeError: x"
    assert t.after[-1] == (POLL_MS, t.live.tick) and len(t.after) == 2
    assert capsys.readouterr().out == "follow error: RuntimeError: x\n"
    _append(folder / STEPS_FILE, LINES[4])
    t.live.tick()  # the same error again: not printed again
    assert len(t.after) == 3 and capsys.readouterr().out == ""


def test_m2_lines_read_in_a_failed_tick_go_into_the_model_on_the_next_one(tmp_path, roots, monkeypatch, capsys):
    """RV-8 review m2: an error after the poll keeps the lines (and frames) it read for the next tick, without
    adding twice the ones that went in before the error."""
    folder = _live_folder(tmp_path, LINES[:3])
    t = _live(roots, monkeypatch, folder)
    t.live.tick()
    model = t.window.model
    assert model is not None and len(model.events) == 3
    real = model.extend
    bad_t = parse_line(LINES[4]).unwrap().t_ns
    raised = []

    def flaky(events):  # fails once, on the batch that holds LINES[4]
        if not raised and any(e.t_ns == bad_t for e in events):
            raised.append(True)
            raise RuntimeError("x")
        real(events)

    monkeypatch.setattr(model, "extend", flaky)
    _append(folder / STEPS_FILE, b"".join(LINES[3:6]))
    _add_frame(folder, 0, 0.5)
    t.live.tick()  # LINES[3] goes in, LINES[4] raises: LINES[4:6] and the frame stay pending
    assert len(model.events) == 4 and _status(t.window) == "follow error: RuntimeError: x"
    t.live.tick()  # nothing new on disk: the pending lines and the frame go in
    assert [e.ev for e in model.events] == [parse_line(line).unwrap().ev for line in LINES[:6]]
    assert [e.t_ns for e in model.events] == [parse_line(line).unwrap().t_ns for line in LINES[:6]]
    assert len(model.frames) == 1 and len(t.after) == 3 and not t.live.ended
    assert capsys.readouterr().out == "follow error: RuntimeError: x\n"


@pytest.mark.parametrize("styled", [False, True])
def test_m2_an_error_on_the_tick_that_reads_end_still_ends_the_run(tmp_path, roots, monkeypatch, capsys, styled):
    """RV-8 review m2: a hidden viewer still shows, and a styled one still loses the no-activate style, when the tick
    that reads `end` fails after its poll (the walk is over, and the same error may come every tick)."""
    folder = _live_folder(tmp_path, LINES[:25])
    t = _live(roots, monkeypatch, folder, hidden=not styled, styled_hwnd=1234 if styled else None)
    t.live.tick()
    assert t.log == [] and t.focus.calls == []

    def boom(events):
        raise RuntimeError("x")

    monkeypatch.setattr(t.window.model, "extend", boom)
    _append(folder / STEPS_FILE, LINES[25])
    t.live.tick()
    assert t.live.ended and len(t.after) == 1  # no further tick
    if styled:
        assert t.focus.calls == [("set_live_style", (1234, False))] and t.log == ["withdraw", "deiconify"]
    else:
        assert t.focus.calls == [] and t.log == ["deiconify"]
    assert not t.window.live and "follow error: RuntimeError: x" in capsys.readouterr().out


def test_m4_clearing_the_style_failing_at_end_is_reported_and_the_window_still_shown(tmp_path, roots, monkeypatch,
                                                                                     capsys):
    folder = _live_folder(tmp_path, LINES[:25])
    focus = FakeFocus(style=Failure("denied"))
    t = _live(roots, monkeypatch, folder, styled_hwnd=1234, focus=focus)
    t.live.tick()
    capsys.readouterr()
    _append(folder / STEPS_FILE, LINES[25])
    t.live.tick()
    assert focus.calls == [("set_live_style", (1234, False))] and t.log == ["withdraw", "deiconify"]
    assert capsys.readouterr().out == "focus call failed: denied\n" and _status(t.window) == "focus call failed: denied"
    assert t.live.ended and not t.window.live



# 9 ----------------------------------------------------------------------------------------------------------------


class _Out:
    """Stands in for stdout: each printed line goes into the shared log as "out:<line>"."""

    def __init__(self, log):
        self.log = log

    def write(self, text):
        for line in text.splitlines():
            if line.strip():
                self.log.append("out:" + line)
        return len(text)

    def flush(self):
        pass


def _main(roots, monkeypatch, args, focus, log, folder):
    """`cli.main(args)` with a withdrawn root whose `deiconify` and `update_idletasks` are recorded in `log` (on
    Windows the latter also runs the real one: it keeps the root withdrawn, RV6 I1)."""
    made = SimpleNamespace(root=None, after=None, idle_saw_claim=[])

    def make_root():
        root, after = _recorded_root(roots, monkeypatch, log)
        real_idle = root.update_idletasks

        def idle():
            log.append("update_idletasks")
            made.idle_saw_claim.append((folder / VIEW_CLAIM).exists())
            if sys.platform == "win32":
                real_idle()

        monkeypatch.setattr(root, "update_idletasks", idle)
        made.root, made.after = root, after
        return root

    monkeypatch.setattr(cli, "_git_head", lambda: GIT_HEAD)
    monkeypatch.setattr(sys, "stdout", _Out(log))
    made.code = cli.main(args, make_root=make_root, run_loop=False, focus=focus, clock=FakeClock())
    return made


def _texts(widget) -> list[str]:
    out = []
    for child in widget.winfo_children():
        if isinstance(child, (tk.Label, tk.Button)):
            out.append(str(child.cget("text")))
        out += _texts(child)
    return out


def _button(widget, text) -> tk.Button:
    for child in widget.winfo_children():
        if isinstance(child, tk.Button) and child.cget("text") == text:
            return child
        found = _button(child, text) if child.winfo_children() else None
        if found is not None:
            return found
    return None  # type: ignore[return-value]


def _order_checks(log):
    """In a and d: `window built in` before any focus call, `first show: root.state() = ` after `show_first`."""
    built = next(i for i, e in enumerate(log) if e.startswith("out:window built in "))
    first_show = [i for i, e in enumerate(log) if e.startswith("out:first show: root.state() = ")]
    assert built < log.index("wrapper_hwnd")
    assert len(first_show) == 1 and first_show[0] > log.index("show_first")
    assert log[first_show[0]] == "out:first show: root.state() = withdrawn"  # the fake showed nothing


def test_9a_a_live_folder_with_ready_claims_styles_shows_without_activating_and_writes_ready(tmp_path, roots,
                                                                                               monkeypatch):
    folder = _live_folder(tmp_path, LINES[:5])
    log = []
    focus = FakeFocus(log, real=True, claim=folder / VIEW_CLAIM)
    made = _main(roots, monkeypatch, ["--follow", str(folder), "--ready", str(folder / VIEW_READY)], focus, log,
                 folder)
    assert made.code == 0
    assert log.count("update_idletasks") == 1 and made.idle_saw_claim == [False]
    assert log.index("update_idletasks") < log.index("wrapper_hwnd")
    assert focus.calls == [("wrapper_hwnd", ()), ("set_live_style", (1234, True)), ("show_first", (1234,))]
    assert focus.claim_seen == [True, True, True]  # the claim comes right before the focus calls
    assert (folder / VIEW_CLAIM).exists()
    assert (folder / VIEW_READY).read_text(encoding="ascii") == str(os.getpid())
    assert not (folder / "view.ready.tmp").exists()
    assert "deiconify" not in log and made.root.state() == "withdrawn"
    _order_checks(log)
    assert made.after == [(POLL_MS, made.after[0][1])]  # the first tick ran and scheduled the next
    if sys.platform == "win32":
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.GetParent.argtypes = [ctypes.c_void_p]
        user32.GetParent.restype = ctypes.c_void_p
        assert focus.real_wrapper == Success(user32.GetParent(made.root.winfo_id()))


def test_9b_a_lost_claim_follows_hidden_and_shows_only_at_end(tmp_path, roots, monkeypatch):
    folder = _live_folder(tmp_path, LINES[:5])
    (folder / VIEW_CLAIM).touch()
    log = []
    focus = FakeFocus(log)
    made = _main(roots, monkeypatch, ["--follow", str(folder), "--ready", str(folder / VIEW_READY)], focus, log,
                 folder)
    assert made.code == 0 and focus.calls == [] and "deiconify" not in log
    assert made.root.state() == "withdrawn" and not (folder / VIEW_READY).exists()
    assert not any(e.startswith("out:first show") for e in log)
    _append(folder / STEPS_FILE, b"".join(LINES[5:]))
    made.after[-1][1]()  # the scheduled tick
    assert log.count("deiconify") == 1 and focus.calls == []
    assert made.root.title().startswith("Route view · ended: max_laps · ")


def test_9c_a_finished_folder_with_ready_opens_as_a_replay_without_a_claim(tmp_path, roots, monkeypatch):
    folder = make_folder(tmp_path)
    log = []
    focus = FakeFocus(log)
    made = _main(roots, monkeypatch, ["--follow", str(folder), "--ready", str(folder / VIEW_READY)], focus, log,
                 folder)
    assert made.code == 0 and focus.calls == [] and log.count("deiconify") == 1
    assert not (folder / VIEW_CLAIM).exists() and not (folder / VIEW_READY).exists()
    assert made.root.title().startswith("Route view · ended: max_laps · ")
    assert any(e.startswith("out:window built in ") for e in log)


def test_9d_a_live_folder_without_ready_styles_and_shows_but_takes_no_claim(tmp_path, roots, monkeypatch):
    folder = _live_folder(tmp_path, LINES[:5])
    log = []
    focus = FakeFocus(log)
    made = _main(roots, monkeypatch, ["--follow", str(folder)], focus, log, folder)
    assert made.code == 0
    assert focus.calls == [("wrapper_hwnd", ()), ("set_live_style", (1234, True)), ("show_first", (1234,))]
    assert not (folder / VIEW_CLAIM).exists() and not (folder / VIEW_READY).exists() and "deiconify" not in log
    _order_checks(log)


def test_9e_a_failed_focus_call_falls_back_to_deiconify_and_the_warning(tmp_path, roots, monkeypatch):
    folder = _live_folder(tmp_path, LINES[:5])
    log = []
    focus = FakeFocus(log, style=Failure("denied"))
    made = _main(roots, monkeypatch, ["--follow", str(folder), "--ready", str(folder / VIEW_READY)], focus, log,
                 folder)
    assert made.code == 0 and log.count("deiconify") == 1
    assert [c[0] for c in focus.calls] == ["wrapper_hwnd", "set_live_style"]  # no show after a failed style
    assert "out:focus call failed: denied" in log
    assert log.index("out:focus call failed: denied") < log.index("deiconify")
    assert CLICK_WARNING in _texts(made.root)
    assert (folder / VIEW_CLAIM).exists() and (folder / VIEW_READY).exists()
    assert any(e.startswith("out:first show: root.state() = ") for e in log)


def test_9f_a_legs_only_folder_with_ready_is_a_replay_without_a_claim(tmp_path, roots, monkeypatch):
    folder = make_legs_folder(tmp_path)
    log = []
    focus = FakeFocus(log)
    made = _main(roots, monkeypatch, ["--follow", str(folder), "--ready", str(folder / VIEW_READY)], focus, log,
                 folder)
    assert made.code == 0 and focus.calls == [] and log.count("deiconify") == 1
    assert not (folder / VIEW_CLAIM).exists() and not (folder / VIEW_READY).exists()
    assert made.root.title().startswith("Route view · REPLAY (from legs.jsonl) · ")


def test_9g_replay_opens_a_folder_without_end_as_a_replay(tmp_path, roots, monkeypatch):
    folder = make_folder(tmp_path, G[:25])
    log = []
    focus = FakeFocus(log)
    made = _main(roots, monkeypatch, ["--follow", str(folder), "--replay"], focus, log, folder)
    assert made.code == 0 and focus.calls == [] and log.count("deiconify") == 1
    assert not (folder / VIEW_CLAIM).exists()
    assert made.root.title().startswith("Route view · REPLAY (did not end) · ")
    _button(made.root, "|<").invoke()
    assert "1 / 25" in _texts(made.root)
    _button(made.root, ">|").invoke()
    assert "25 / 25" in _texts(made.root)


def test_9h_replay_with_ready_is_refused_before_any_window(tmp_path, monkeypatch, capsys):
    folder = _live_folder(tmp_path, LINES[:5])
    made = []
    focus = FakeFocus()
    code = cli.main(["--follow", str(folder), "--replay", "--ready", str(folder / VIEW_READY)],
                    make_root=lambda: made.append(1), run_loop=False, focus=focus, clock=FakeClock())
    out, err = capsys.readouterr()
    assert code == 2 and made == [] and focus.calls == [] and out == ""
    assert err == "error: --replay cannot be used with --ready\n"
    assert not (folder / VIEW_CLAIM).exists() and not (folder / VIEW_READY).exists()


def test_9i_replay_on_a_finished_folder_is_the_same_as_without_it(tmp_path, roots, monkeypatch):
    folder = make_folder(tmp_path)
    log = []
    focus = FakeFocus(log)
    made = _main(roots, monkeypatch, ["--follow", str(folder), "--replay"], focus, log, folder)
    assert made.code == 0 and focus.calls == [] and log.count("deiconify") == 1
    assert not (folder / VIEW_CLAIM).exists()
    assert made.root.title().startswith("Route view · ended: max_laps · ")


def test_m4_a_claim_failing_with_another_oserror_follows_hidden_as_a_lost_claim(tmp_path, roots, monkeypatch):
    """RV-8 review m4: any OSError on the claim (here a PermissionError, not FileExistsError) is the safe side."""
    folder = _live_folder(tmp_path, LINES[:5])
    real_open = open

    def denied(path, *args, **kwargs):
        if Path(path).name == VIEW_CLAIM:
            raise PermissionError(13, "denied", str(path))
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(cli, "open", denied, raising=False)
    log = []
    focus = FakeFocus(log)
    made = _main(roots, monkeypatch, ["--follow", str(folder), "--ready", str(folder / VIEW_READY)], focus, log,
                 folder)
    assert made.code == 0 and focus.calls == [] and "deiconify" not in log
    assert made.root.state() == "withdrawn" and not (folder / VIEW_READY).exists()
    assert not (folder / VIEW_CLAIM).exists()
    _append(folder / STEPS_FILE, b"".join(LINES[5:]))
    made.after[-1][1]()  # the scheduled tick
    assert log.count("deiconify") == 1 and focus.calls == []
