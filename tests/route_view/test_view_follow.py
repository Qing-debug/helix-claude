"""Tests of the route view's live follower (route view plan task RV-8, tests 1-8; contract V6; spec 5.4; route tab plan
RT-4: the claim, ready and Win32 focus tests went with their code, spec 5.13).

Every Tk root is real and withdrawn (`view_tk.open_root`), has no mainloop and records `after`; `withdraw` and
`deiconify` are replaced by recorders that never call Tk's own, so nothing is ever shown and nothing takes focus, and a
test that expects no window call checks the recorder. The title is the window's `title_label` (RT-4): the view writes
no title on the root. Each root is destroyed at teardown. The clock is a `FakeClock`.
"""

import os
import statistics
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image
from test_view_window import _noise_png
from view_runs import FF2, G, GIT_HEAD, REPO_ROOT, default_header, event_lines, ns
from view_tk import destroy_all, open_root, record_after

from src.route import step_log
from src.route.step_log import MAP_COPY, STEPS_FILE, header_line, parse_line
from src.route_view.follow import POLL_MS, SILENT_S, Follower, LiveController, Polled
from src.route_view.model import FRAME_INDEX, FRAMES_DIR
from src.route_view.window import RouteViewWindow

SILENT = "no new steps for 10 s: the walk is still running or has crashed"
HINT = "if the walk has ended or crashed, see the console; the replay opens when it ends"  # plan choice 11
HEADER = header_line(default_header()).unwrap()
LINES = event_lines(G)  # 26 lines; the last is `end`


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


def _live(roots, monkeypatch, folder, *, clock=None):
    log = []
    root, after = _recorded_root(roots, monkeypatch, log)
    window = RouteViewWindow(root, folder, None, live=True)
    live = LiveController(root, window, folder, repo_root=REPO_ROOT, current_git_head=GIT_HEAD,
                          clock=clock or FakeClock())
    return SimpleNamespace(root=root, after=after, log=log, window=window, live=live)


def _live_with_frame(roots, monkeypatch, folder, live_frame):
    log = []
    root, after = _recorded_root(roots, monkeypatch, log)
    window = RouteViewWindow(root, folder, None, live=True)
    live = LiveController(root, window, folder, repo_root=REPO_ROOT, current_git_head=GIT_HEAD,
                          clock=FakeClock(), live_frame=live_frame)
    return SimpleNamespace(root=root, after=after, log=log, window=window, live=live)


def _frame(value: int) -> "np.ndarray":
    return np.full((82, 162, 3), value, dtype=np.uint8)


def _spy_shows(monkeypatch, window) -> list:
    """The `picture` of every `window.show` call, in order."""
    shows = []
    real = window.show

    def show(k, **kwargs):
        shows.append(kwargs.get("picture"))
        return real(k, **kwargs)

    monkeypatch.setattr(window, "show", show)
    return shows


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


def _title(window) -> str:
    return window.title_label.cget("text")


def _spy_loads(monkeypatch, window) -> list[str]:
    loads = []
    real = window._load_image

    def load(path):
        loads.append(Path(path).name)
        return real(path)

    monkeypatch.setattr(window, "_load_image", load)
    return loads


def test_step_log_has_no_view_names():
    """The viewer's files (`view.log`, `view.claim`, `view.ready`) went with the walk's viewer spawn (spec 5.13)."""
    for name in ("VIEW_LOG", "VIEW_CLAIM", "VIEW_READY"):
        assert not hasattr(step_log, name), name


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
    assert t.window.bar.winfo_manager() != "pack" and t.window.live  # left as last drawn, no replay bar (H69)
    assert _title(t.window).startswith("Route view · ended: max_laps")
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
    assert _title(t.window).startswith("Route view · LIVE · ") and SILENT not in _title(t.window)
    clock.t = SILENT_S - 0.1
    t.live.tick()
    assert SILENT not in _title(t.window) and _status(t.window) == ""
    clock.t = SILENT_S
    t.live.tick()
    assert _title(t.window).startswith(f"Route view · LIVE · {SILENT} · ")
    assert _status(t.window) == HINT and "console" in HINT
    assert t.after[-1] == (POLL_MS, t.live.tick)  # it keeps following
    _append(folder / STEPS_FILE, LINES[5][12:])
    clock.t = SILENT_S + 5
    t.live.tick()
    assert len(t.window.model.events) == 6
    assert _title(t.window).startswith("Route view · LIVE · ") and SILENT not in _title(t.window)
    assert _status(t.window) == ""
    clock.t = 2 * SILENT_S + 5  # ten more silent seconds from the sixth line
    t.live.tick()
    assert SILENT in _title(t.window)


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


def test_end_freezes_the_window_without_window_calls(tmp_path, roots, monkeypatch):
    """Route tab H68, H69: at `end` the window is not switched to replay; it stays live (no replay bar), as last drawn,
    and only its title's mode says the run ended."""
    folder = _live_folder(tmp_path, LINES[:25])
    t = _live(roots, monkeypatch, folder)
    t.live.tick()
    assert t.after == [(POLL_MS, t.live.tick)] and t.log == [] and t.window.live and not t.live.ended
    _append(folder / STEPS_FILE, LINES[25])
    t.live.tick()
    assert t.window.live is True and t.live.ended is True
    assert t.log == []  # neither `withdraw` nor `deiconify` was called on the root
    assert t.window.bar.winfo_manager() != "pack" and t.window.index == len(t.window.model.events) - 1
    assert _title(t.window).startswith("Route view · ended: max_laps · ")
    assert len(t.after) == 1  # no further tick
    t.live.tick()
    assert len(t.after) == 1 and t.log == []


def test_stop_ends_the_ticks(tmp_path, roots, monkeypatch):
    folder = _live_folder(tmp_path, LINES[:5])
    t = _live(roots, monkeypatch, folder)
    t.live.tick()
    assert len(t.after) == 1
    t.live.stop()
    t.live.stop()  # idempotent
    t.live.tick()
    assert len(t.after) == 1  # no further tick was scheduled
    events, index, shown = len(t.window.model.events), t.window.index, t.window.shown_background
    _append(folder / STEPS_FILE, LINES[5])  # what a tick queued before `stop()` would find
    t.live.update()
    assert len(t.window.model.events) == events and t.window.index == index
    assert t.window.shown_background == shown and len(t.after) == 1


def test_a_folder_that_ended_before_the_first_poll_touches_no_window_call(tmp_path, roots, monkeypatch):
    folder = _live_folder(tmp_path, LINES)
    t = _live(roots, monkeypatch, folder)
    t.live.tick()
    assert t.log == [] and t.window.live and t.after == []
    assert _title(t.window).startswith("Route view · ended: max_laps · ")


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


def test_m2_an_error_on_the_tick_that_reads_end_still_ends_the_run(tmp_path, roots, monkeypatch, capsys):
    """RV-8 review m2: the run still ends when the tick that reads `end` fails after its poll (the walk is over, and the
    same error may come every tick); the title gives the `end` line's reason though the model did not take it."""
    folder = _live_folder(tmp_path, LINES[:25])
    t = _live(roots, monkeypatch, folder)
    t.live.tick()

    def boom(events):
        raise RuntimeError("x")

    monkeypatch.setattr(t.window.model, "extend", boom)
    _append(folder / STEPS_FILE, LINES[25])
    t.live.tick()
    assert t.live.ended and len(t.after) == 1  # no further tick
    assert t.window.live and t.log == [] and "follow error: RuntimeError: x" in capsys.readouterr().out
    assert not t.window.model.ended and _title(t.window).startswith("Route view · ended: max_laps · ")



def test_m2b_an_event_the_model_rejects_every_time_is_retried_once_then_skipped_and_said(tmp_path, roots, monkeypatch,
                                                                                         capsys):
    """RV-9 review m2 (orchestrator ruling 88): one event the model always rejects must not freeze the live view. It is
    retried on the next tick, then skipped, and the status line says so; the lines behind it go in."""
    folder = _live_folder(tmp_path, LINES[:3])
    t = _live(roots, monkeypatch, folder)
    t.live.tick()
    model = t.window.model
    assert model is not None and len(model.events) == 3
    real = model.extend
    bad_t = parse_line(LINES[4]).unwrap().t_ns
    tries = []

    def reject(events):  # LINES[4] fails every time
        if any(e.t_ns == bad_t for e in events):
            tries.append(True)
            raise RuntimeError("x")
        real(events)

    monkeypatch.setattr(model, "extend", reject)
    _append(folder / STEPS_FILE, b"".join(LINES[3:6]))
    t.live.tick()  # LINES[3] goes in; LINES[4] raises the first time: it and LINES[5] stay pending
    assert len(model.events) == 4 and len(tries) == 1 and _status(t.window) == "follow error: RuntimeError: x"
    t.live.tick()  # LINES[4] is tried once more, rejected again and skipped; LINES[5] goes in behind it
    assert len(tries) == 2 and len(model.events) == 5 and len(t.after) == 3 and not t.live.ended
    assert [e.t_ns for e in model.events] == [parse_line(LINES[i]).unwrap().t_ns for i in (0, 1, 2, 3, 5)]
    assert _status(t.window) == "skipped 1 event the view could not read (last: RuntimeError: x)"
    _append(folder / STEPS_FILE, LINES[6])
    t.live.tick()  # the view goes on: the skipped event is not tried a third time
    assert len(tries) == 2 and len(model.events) == 6 and len(t.after) == 4
    assert capsys.readouterr().out == "follow error: RuntimeError: x\n"


def test_m3_end_runs_at_most_once_even_if_a_call_inside_it_raises(tmp_path, roots, monkeypatch, capsys):
    """RV-9 review m3: `set_mode` raising inside `_end` must not make `update`'s error path run `_end` again."""
    folder = _live_folder(tmp_path, LINES[:25])
    t = _live(roots, monkeypatch, folder)
    t.live.tick()
    capsys.readouterr()
    calls = []

    def boom(text):
        calls.append(text)
        raise RuntimeError("x")

    monkeypatch.setattr(t.window, "set_mode", boom)
    _append(folder / STEPS_FILE, LINES[25])
    t.live.tick()
    assert t.live.ended and len(t.after) == 1
    assert calls == ["ended: max_laps"] and t.log == []
    assert capsys.readouterr().out == "follow error: RuntimeError: x\n"
    t.live.update()  # an ended controller does nothing more
    assert calls == ["ended: max_laps"] and t.log == []


# the live picture (route tab plan C3, choice 12; spec 5.8) ---------------------------------------------------------


def test_live_frame_is_drawn_and_new_frames_redraw(tmp_path, roots, monkeypatch):
    folder = _live_folder(tmp_path, LINES[:5])
    _add_frame(folder, 0, 1.05)  # a recorded frame is there, and is not used while live_frame gives a picture
    a, b = _frame(30), _frame(200)
    given = [a, a, b]
    t = _live_with_frame(roots, monkeypatch, folder, lambda: given.pop(0) if len(given) > 1 else given[0])
    loads = _spy_loads(monkeypatch, t.window)
    draws = []  # (shown_background, the picture's pixels) at every draw
    real_draw = t.window._draw

    def draw(view):
        image = t.window.shown_image
        draws.append((t.window.shown_background, None if image is None else np.asarray(image)[0, 0, 0]))
        return real_draw(view)

    monkeypatch.setattr(t.window, "_draw", draw)
    t.live.tick()  # A: the first draw
    assert draws and all(draw == ("live", 30) for draw in draws)
    before = len(draws)
    t.live.tick()  # A again, the same object, nothing else new: no redraw
    assert len(draws) == before
    t.live.tick()  # B, a new object: exactly one redraw, with B
    assert len(draws) == before + 1 and draws[before] == ("live", 200)
    assert t.window.shown_background == "live"
    assert np.array_equal(np.asarray(t.window.shown_image), b)
    assert loads == []  # no file was read, at any draw


def test_live_picture_kept_when_only_steps_change(tmp_path, roots, monkeypatch):
    folder = _live_folder(tmp_path, LINES[:5])
    a = _frame(30)
    t = _live_with_frame(roots, monkeypatch, folder, lambda: a)
    t.live.tick()
    picture = t.window.shown_image
    assert picture is not None and t.window.shown_background == "live"
    loads = _spy_loads(monkeypatch, t.window)
    shows = _spy_shows(monkeypatch, t.window)
    _append(folder / STEPS_FILE, b"".join(LINES[5:8]))  # steps change, live_frame returns the same A
    t.live.tick()
    assert len(shows) == 1 and shows[0] is picture  # redrawn, and with the picture made from A
    assert loads == [] and t.window.shown_image is picture and t.window.shown_background == "live"
    _add_frame(folder, 0, 1.05)  # a recorded frame arrives: still not used
    t.live.tick()
    assert loads == [] and t.window.shown_image is picture and t.window.shown_background == "live"


def test_the_end_keeps_the_live_picture(tmp_path, roots, monkeypatch):
    """Route tab H69 (replaces RT5-2a-D2's switch to the recorded frame): the last live picture stays after `end`, and
    the window keeps it for its own redraws."""
    folder = _live_folder(tmp_path, LINES[:25])
    _add_frame(folder, 0, 1.05)
    t = _live_with_frame(roots, monkeypatch, folder, lambda: _frame(30))
    t.live.tick()
    assert t.window.shown_background == "live"
    picture = t.window.shown_image
    _append(folder / STEPS_FILE, LINES[25])
    t.live.tick()
    assert t.live.ended and t.window.live is True
    assert t.window.shown_background == "live" and t.window.shown_image.tobytes() == picture.tobytes()
    t.window._redraw_here()
    assert t.window.shown_background == "live"


def test_a_live_frame_that_raises_on_the_header_tick_loses_no_line(tmp_path, roots, monkeypatch, capsys):
    """RT-5 review I1: the polled lines are stored whatever `live_frame` does. The error goes to the status line and the
    console once, the picture stays what it was (none yet), and following goes on."""
    folder = _live_folder(tmp_path, LINES[:5])
    calls = []

    def live_frame():
        calls.append(True)
        if len(calls) == 1:
            raise RuntimeError("x")
        return _frame(30)

    t = _live_with_frame(roots, monkeypatch, folder, live_frame)
    t.live.tick()  # the tick that reads the header: the callable raises
    model = t.window.model
    assert model is not None and len(model.events) == 5  # the header and the lines were not lost with it
    assert _status(t.window) == "follow error: live frame: RuntimeError: x"
    assert capsys.readouterr().out == "follow error: live frame: RuntimeError: x\n"
    assert t.window.shown_background != "live" and len(t.after) == 1 and not t.live.ended
    _append(folder / STEPS_FILE, b"".join(LINES[5:7]))
    t.live.tick()  # the callable is fine again: the picture appears and the steps go on
    assert len(model.events) == 7 and t.window.shown_background == "live" and len(t.after) == 2
    assert capsys.readouterr().out == ""


def test_a_live_frame_that_raises_on_the_end_tick_still_ends_the_run(tmp_path, roots, monkeypatch, capsys):
    """RT-5 review I1: the `end` line is stored (so the run ends and the window is frozen) even if `live_frame` raises
    on the tick that reads it."""
    folder = _live_folder(tmp_path, LINES[:25])
    _add_frame(folder, 0, 1.05)
    broken = []

    def live_frame():
        if broken:
            raise RuntimeError("x")
        return _frame(30)

    t = _live_with_frame(roots, monkeypatch, folder, live_frame)
    t.live.tick()
    assert t.window.shown_background == "live"
    capsys.readouterr()
    broken.append(True)
    _append(folder / STEPS_FILE, LINES[25])
    t.live.tick()  # reads `end`; the callable raises
    assert t.live.ended and t.window.model is not None and t.window.model.ended
    assert t.window.live is True and len(t.after) == 1  # no further tick
    assert t.window.shown_background == "live"  # the live picture made so far stays (H69)
    assert _status(t.window) == "follow error: live frame: RuntimeError: x"
    assert capsys.readouterr().out == "follow error: live frame: RuntimeError: x\n"


def test_a_live_frame_that_cannot_be_made_into_a_picture_is_reported_the_same_way(tmp_path, roots, monkeypatch, capsys):
    """`Image.fromarray` is in the same guard as the callable (RT-5 review I1): a dtype it cannot take is an error on
    the status line, and the steps of the tick still go in."""
    folder = _live_folder(tmp_path, LINES[:5])
    bad = np.zeros((82, 162, 3), dtype=np.float64)  # Pillow has no mode for this
    t = _live_with_frame(roots, monkeypatch, folder, lambda: bad)
    t.live.tick()
    model = t.window.model
    assert model is not None and len(model.events) == 5
    assert _status(t.window).startswith("follow error: live frame: TypeError: ")
    assert t.window.shown_background != "live" and not t.live.ended
    capsys.readouterr()
