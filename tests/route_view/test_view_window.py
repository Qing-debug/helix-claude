"""Tests of the route view's Tk window (route view plan task RV-7, tests 1-10 and 14; contract V5 and spec 5.4, 5.5, 5.7).

Every test opens a real Tk root, withdrawn, with no mainloop, and replaces its `after` by a recorder; each root is
destroyed at teardown. Nothing is shown, so nothing takes focus.
"""

import ast
import io
import random
from pathlib import Path

import pytest
from PIL import Image
from returns.pipeline import is_successful
from view_runs import FF2, G, GIT_HEAD, REPO_ROOT, default_header, make_folder, make_legs_folder, ns
from view_tk import destroy_all, open_root, record_after

from src.route.step_log import read_run
from src.route_view.model import FRAME_INDEX, FRAMES_DIR, WARN_MAP, RunModel, load_folder
from src.route_view.window import NO_STEPS, RouteViewWindow

ROUTE_VIEW_SRC = REPO_ROOT / "src" / "route_view"
FRAME_COLOURS = ((200, 30, 30), (30, 200, 30), (30, 30, 200))


@pytest.fixture
def roots():
    made = []
    yield made
    destroy_all(made)


@pytest.fixture
def make_window(roots):
    """`make(folder_or_model, live=False)` -> the window; `window.after_calls` is its root's recorded `after`."""

    def make(source, *, live=False, git_head=GIT_HEAD):
        root = open_root()
        roots.append(root)
        calls = record_after(root)
        if isinstance(source, Path):
            loaded = load_folder(source, repo_root=REPO_ROOT, current_git_head=git_head)
            assert is_successful(loaded), loaded
            model, folder = loaded.unwrap(), source
        else:
            model, folder = source
        window = RouteViewWindow(root, folder, model, live=live)
        window.after_calls = calls  # type: ignore[attr-defined]
        return window

    return make


def _noise_png(colour) -> bytes:
    """A 162 x 82 PNG that does not compress away (so half of it is a truncated file)."""
    rng = random.Random(7)
    image = Image.new("RGB", (162, 82), colour)
    image.putdata([tuple((c + rng.randrange(60)) % 256 for c in colour) for _ in range(162 * 82)])
    buf = io.BytesIO()
    image.save(buf, "PNG")
    return buf.getvalue()


def _write_frames(folder, times=(1.0, 1.5, 3.0)) -> None:
    frames = folder / FRAMES_DIR
    frames.mkdir()
    lines = []
    for i, (colour, seconds) in enumerate(zip(FRAME_COLOURS, times)):
        Image.new("RGB", (162, 82), colour).save(frames / f"frame_{i:06d}.png")
        lines.append(f'{{"i": {i}, "t_ns": {ns(seconds)}, "file": "frame_{i:06d}.png", "w": 162, "h": 82}}\r\n')
    (frames / FRAME_INDEX).write_bytes("".join(lines).encode())


def _sub(tmp_path, name):
    folder = tmp_path / name
    folder.mkdir()
    return folder


def _canvas_size(window) -> tuple[int, int]:
    return int(window.canvas.cget("width")), int(window.canvas.cget("height"))


def _text_lines(window) -> list[str]:
    return window.why_text.get("1.0", "end-1c").split("\n")


def _tagged_line(window) -> int:
    return int(str(window.why_text.tag_ranges("current")[0]).split(".")[0])


def _labels(window) -> list[str]:
    return [window.canvas.itemcget(item, "text") for item in window.canvas.find_withtag("label")]


# 1 ---------------------------------------------------------------------------------------------------------------


@pytest.mark.slow
def test_1_a_sim_folder_shows_every_kind_of_step(tmp_path, make_window):
    from src.route.sim.run import run_sim

    out = tmp_path / "run"
    result = run_sim(FF2, REPO_ROOT / "tests" / "fixtures" / "ff2_terrain.json", 1, 1, out_dir=out)
    assert is_successful(result), result
    window = make_window(out, git_head=None)
    model = window.model
    assert model.ended and window.index == len(model.events) - 1
    first_planned = next(k for k, e in enumerate(model.events) if e.ev == "planned")
    first_settled = next(k for k, e in enumerate(model.events) if e.ev == "settled")
    two_option = [k for k in range(len(model.events)) if any("50/50" in h.text for h in model.step(k).hops)]
    for k in (0, first_planned, first_settled, *two_option[:1], len(model.events) - 1):
        window.show(k)
        view = model.step(k)
        assert window.index == k
        assert len(window.canvas.find_withtag("node")) == 21
        assert _labels(window) == [f"#{n}" for n in range(1, 8)]
        assert len(window.canvas.find_withtag("target")) == 1
        assert bool(window.canvas.find_withtag("route")) == bool(view.route)
        assert window.panel_label.cget("text") == "\n".join(view.panel)
        assert len(_text_lines(window)) == len(model.why)
        assert _tagged_line(window) == view.why_line + 1
    assert window._root.title().startswith("Route view · ended: max_laps")


# 2 ---------------------------------------------------------------------------------------------------------------


def test_2_a_legs_only_folder_opens_on_a_dark_canvas(tmp_path, make_window):
    window = make_window(make_legs_folder(tmp_path))
    assert "REPLAY (from legs.jsonl)" in window._root.title()
    assert _canvas_size(window) == (648, 328)  # FF2 "# minimap_size=162,82" at 4x
    assert window.shown_background == "dark" and window.shown_image is None
    assert window.canvas.cget("bg") == "#202020"
    for k in range(len(window.model.events)):
        window.show(k)
        assert window.index == k
        assert window.panel_label.cget("text") == "\n".join(window.model.step(k).panel)


# 3 ---------------------------------------------------------------------------------------------------------------


def test_3_scale_buttons_resize_the_canvas_and_the_nodes(tmp_path, make_window):
    window = make_window(make_legs_folder(tmp_path))
    first = window.canvas.find_withtag("node")[0]
    at4 = window.canvas.coords(first)
    assert window.scale == 4 and _canvas_size(window) == (648, 328)
    window.set_scale(2)
    assert _canvas_size(window) == (324, 164)
    assert window.canvas.coords(window.canvas.find_withtag("node")[0]) == [c / 2 for c in at4]
    window.scale_buttons[3].invoke()
    assert window.scale == 3 and _canvas_size(window) == (486, 246)
    window.scale_buttons[2].invoke()
    assert window.scale == 2 and _canvas_size(window) == (324, 164)
    window.scale_buttons[4].invoke()
    assert window.scale == 4 and window.canvas.coords(window.canvas.find_withtag("node")[0]) == at4
    with pytest.raises(ValueError):
        window.set_scale(5)


# 4 ---------------------------------------------------------------------------------------------------------------


def test_4_the_background_comes_from_the_frame_then_the_snapshot_then_dark(tmp_path, make_window):
    both = make_folder(_sub(tmp_path, "both"))
    _write_frames(both)
    Image.new("RGB", (162, 82), (9, 9, 9)).save(both / "minimap.png")
    window = make_window(both)
    window.show(1)
    assert window.shown_background == "frames/frame_000000.png"
    assert window.shown_image.getpixel((5, 5)) == FRAME_COLOURS[0] and window.background_ok
    window.show(6)
    assert window.shown_background == "frames/frame_000002.png"
    assert window.shown_image.getpixel((5, 5)) == FRAME_COLOURS[2]
    window.show(1, background="frame_000002.png")
    assert window.shown_background == "frames/frame_000002.png"
    assert window.shown_image.getpixel((5, 5)) == FRAME_COLOURS[2]
    window.show(1, background="frame_000001.png")
    assert window.shown_image.getpixel((5, 5)) == FRAME_COLOURS[1]
    assert _canvas_size(window) == (648, 328)

    snapshot = make_folder(_sub(tmp_path, "snapshot"))
    Image.new("RGB", (162, 82), (9, 9, 9)).save(snapshot / "minimap.png")
    window = make_window(snapshot)
    window.show(1)
    assert window.shown_background == "minimap.png" and window.shown_image.getpixel((0, 0)) == (9, 9, 9)

    neither = make_window(make_folder(_sub(tmp_path, "neither")))
    neither.show(1)
    assert neither.shown_background == "dark" and neither.shown_image is None and neither.background_ok


def test_4_a_step_before_the_first_frame_uses_the_snapshot(tmp_path, make_window):
    folder = make_folder(tmp_path)
    _write_frames(folder, times=(1.05, 1.5, 3.0))
    Image.new("RGB", (162, 82), (9, 9, 9)).save(folder / "minimap.png")
    window = make_window(folder)
    window.show(0)  # 1.000 s: no frame yet
    assert window.shown_background == "minimap.png"
    window.show(1)  # 1.100 s
    assert window.shown_background == "frames/frame_000000.png"
    window.show(0)  # back before the first frame: the snapshot again, not the last picture
    assert window.shown_background == "minimap.png"


def test_4_a_picture_sets_the_canvas_size_and_a_dark_canvas_with_no_map_size_is_the_bounding_box(tmp_path, make_window):
    folder = make_folder(tmp_path)
    window = make_window(folder)
    assert _canvas_size(window) == (648, 328)  # the map's minimap_size
    Image.new("RGB", (100, 50), (1, 2, 3)).save(folder / "minimap.png")
    window.show(1)
    assert _canvas_size(window) == (400, 200)
    (folder / "minimap.png").unlink()
    window.model.minimap_size = None  # a map with no "# minimap_size=" line
    window.show(1)
    xs = [m.pos[0] for m in window.model.nodes] + [p[0] for e in window.model.events for p in _positions(e)]
    ys = [m.pos[1] for m in window.model.nodes] + [p[1] for e in window.model.events for p in _positions(e)]
    assert _canvas_size(window) == (max(xs) * 4 + 40, max(ys) * 4 + 40)


def _positions(event):
    return [v for v in event.data.values() if isinstance(v, tuple) and len(v) == 2 and isinstance(v[0], int)]


def test_4_show_reads_one_picture_through_the_named_loader(tmp_path, make_window, monkeypatch):
    folder = make_folder(tmp_path)
    _write_frames(folder)
    window = make_window(folder)
    seen = []
    real = window._load_image
    monkeypatch.setattr(window, "_load_image", lambda path: (seen.append(path.name), real(path))[1])
    window.show(4)
    assert seen == ["frame_000001.png"]  # one decode for one show (PF81 m5: RV-8 spies on this method)
    window.show(4, background="frame_000002.png")
    assert seen == ["frame_000001.png", "frame_000002.png"]


# 5 ---------------------------------------------------------------------------------------------------------------


def test_5_a_half_written_snapshot_shows_the_dark_canvas_and_is_read_again(tmp_path, make_window):
    folder = make_folder(tmp_path)
    data = _noise_png((10, 120, 200))
    (folder / "minimap.png").write_bytes(data[: len(data) // 2])
    window = make_window(folder)
    window.show(1)
    assert window.shown_background == "dark" and not window.background_ok
    assert "minimap.png" in window._status.get() and window._status.get().startswith("cannot read minimap.png: ")
    assert window._status.get().endswith("; showing the last good picture")
    (folder / "minimap.png").write_bytes(data)
    window.show(1)
    assert window.shown_background == "minimap.png" and window.background_ok
    assert window._status.get() == ""


def test_5_a_corrupt_newer_frame_keeps_the_last_good_picture(tmp_path, make_window):
    folder = make_folder(tmp_path)
    _write_frames(folder)
    good = (folder / FRAMES_DIR / "frame_000001.png").read_bytes()
    noisy = _noise_png(FRAME_COLOURS[1])
    (folder / FRAMES_DIR / "frame_000001.png").write_bytes(noisy[: len(noisy) // 2])
    window = make_window(folder)
    window.show(1)  # t = 1.100 s: frame 0, good
    photo = window.shown_photo
    assert photo is not None and window.shown_background == "frames/frame_000000.png" and window.background_ok
    window.show(4)  # t = 1.950 s: frame 1, corrupt
    assert window.shown_photo is photo and not window.background_ok
    assert window.shown_background == "frames/frame_000000.png"
    assert "frame_000001.png" in window._status.get()
    assert len(window.canvas.find_withtag("bg")) == 1 and len(window.canvas.find_withtag("node")) == 21
    (folder / FRAMES_DIR / "frame_000001.png").write_bytes(good)
    window.show(4)  # tried again: now it is whole
    assert window.shown_background == "frames/frame_000001.png" and window.background_ok
    assert window.shown_photo is not photo and window._status.get() == ""


def test_5_a_new_scale_while_the_read_keeps_failing_rebuilds_the_last_good_picture(tmp_path, make_window):
    """RV-7 re-review m-a: with the read failing, the old picture object stays, so only the scale says to rebuild it
    (`window.py` `_photo`); else the nodes move to the new scale and the picture does not."""
    folder = make_folder(tmp_path)
    _write_frames(folder)
    noisy = _noise_png(FRAME_COLOURS[1])
    (folder / FRAMES_DIR / "frame_000001.png").write_bytes(noisy[: len(noisy) // 2])
    window = make_window(folder)
    window.show(1)  # frame 0, good
    window.show(4)  # frame 1, corrupt: frame 0 stays
    assert not window.background_ok and window.shown_background == "frames/frame_000000.png"
    assert (window.shown_photo.width(), window.shown_photo.height()) == (648, 328)
    window.set_scale(2)  # the read fails again
    assert not window.background_ok and window.shown_background == "frames/frame_000000.png"
    assert (window.shown_photo.width(), window.shown_photo.height()) == (324, 164)
    assert window.canvas.bbox("bg") == (0, 0, 324, 164) and _canvas_size(window) == (324, 164)


def test_5_a_missing_frame_file_is_a_read_failure_too(tmp_path, make_window):
    folder = make_folder(tmp_path)
    _write_frames(folder)
    (folder / FRAMES_DIR / "frame_000002.png").unlink()
    window = make_window(folder)
    window.show(6)
    assert window.shown_background == "dark" and not window.background_ok
    assert window._status.get().startswith("cannot read frame_000002.png: ")


# 6 ---------------------------------------------------------------------------------------------------------------


def _live_model(folder):
    run = read_run(folder).unwrap()
    return RunModel.open(folder, run.header, run.origin, repo_root=REPO_ROOT, current_git_head=GIT_HEAD), run.events


def test_6_the_why_log_only_adds_the_new_lines(tmp_path, make_window, monkeypatch):
    folder = make_folder(tmp_path)
    model, events = _live_model(folder)
    model.extend(events[:10])
    window = make_window((model, folder), live=True)
    window.sync_why()
    assert _text_lines(window) == model.why
    inserts, deletes = [], []
    real_insert, real_delete = window.why_text.insert, window.why_text.delete
    monkeypatch.setattr(window.why_text, "insert", lambda *a, **k: (inserts.append(a), real_insert(*a, **k))[1])
    monkeypatch.setattr(window.why_text, "delete", lambda *a, **k: (deletes.append(a), real_delete(*a, **k))[1])

    model.extend(events[10:11])  # the first read_failed: one new line
    window.sync_why()
    assert len(inserts) == 1 and deletes == []
    assert _text_lines(window) == model.why
    window.sync_why()  # nothing new: nothing touched
    assert len(inserts) == 1 and deletes == []

    count = len(model.why)
    model.extend(events[11:12])  # a repeat of the same read_failed: collapsed into the last line, "x2"
    assert len(model.why) == count and model.why[-1].endswith(" x2")
    window.sync_why()
    assert len(_text_lines(window)) == count and _text_lines(window)[-1] == model.why[-1]
    assert len(inserts) == 2 and len(deletes) == 1
    assert ("1.0", "end") not in deletes and deletes[0][0] == f"{count}.0"
    assert _text_lines(window) == model.why

    model.extend(events[12:])  # more: a third repeat rewrites the line, then new lines are appended
    window.sync_why()
    assert _text_lines(window) == model.why and ("1.0", "end") not in deletes


def test_6_the_why_log_is_read_only_and_marks_the_current_line(tmp_path, make_window):
    window = make_window(make_folder(tmp_path))
    assert str(window.why_text.cget("state")) == "disabled"
    window.why_text.insert("1.0", "typed")  # a disabled Text ignores it
    assert "typed" not in window.why_text.get("1.0", "end")
    for k in (0, 1, 13, 25):
        window.show(k)
        assert _tagged_line(window) == window.model.step(k).why_line + 1


# 7 ---------------------------------------------------------------------------------------------------------------


def test_7_the_buttons_move_by_event_move_and_leg(tmp_path, make_window):
    window = make_window(make_folder(tmp_path))
    assert window.index == 25  # opens on the newest step
    window.btn_first.invoke()
    assert window.index == 0
    window.btn_next.invoke()
    assert window.index == 1
    window.unit_var.set("move")
    window.btn_next.invoke()
    assert window.index == 4
    window.unit_var.set("leg")
    window.btn_next.invoke()
    assert window.index == 16
    window.btn_prev.invoke()
    assert window.index == 0
    window.btn_last.invoke()
    assert window.index == 25
    window.btn_next.invoke()  # none that way: stays
    assert window.index == 25
    window.btn_first.invoke()
    assert window.index == 0
    window.on_slider("5")
    assert window.index == 5
    window.lap_var.set("0")
    window.leg_var.set("6")
    window.go_to_leg()
    assert window.index == 16
    assert window.lap_entry.bind("<Return>") and window.leg_entry.bind("<Return>")
    window.lap_var.set("7")
    window.go_to_leg()
    assert window.index == 16 and window._status.get() == "no lap 7 leg 6 in this run"
    window.lap_var.set("x")
    window.go_to_leg()
    assert window._status.get() == "lap and leg must be whole numbers"


def test_7_the_slider_follows_the_step_and_its_own_moves_do_not_come_back(tmp_path, make_window):
    window = make_window(make_folder(tmp_path))
    assert int(float(window.slider.cget("to"))) == 25
    window.show(7)
    assert window.slider.get() == 7
    window.slider.set(12)  # what Tk does when the slider is dragged: the command runs
    window.on_slider("12")
    assert window.index == 12 and window.step_label.cget("text") == "13 / 26"
    window.on_slider("not a number")
    window.on_slider("99")
    assert window.index == 12
    window.play_buttons[4].invoke()
    window.on_slider("12")  # Tk's call for the window's own slider.set(): the shown step, so playback goes on
    assert window._speed == 4.0
    window.on_slider("13")  # a drag to another step: stops the playback and goes there
    assert window._speed is None and window.index == 13


def test_7_play_follows_the_logged_times_and_stops_at_the_end(tmp_path, make_window):
    window = make_window(make_folder(tmp_path))
    model, calls = window.model, window.after_calls
    window.btn_first.invoke()
    window.play_buttons[4].invoke()
    assert len(calls) == 1 and calls[0][0] == model.play_delay_ms(0, 4.0) == 25
    calls[-1][1]()
    assert window.index == 1 and len(calls) == 2 and calls[1][0] == model.play_delay_ms(1, 4.0)
    while len(calls) and window.index < 25:
        before = len(calls)
        calls[-1][1]()
        if window.index < 25:
            assert len(calls) == before + 1 and calls[-1][0] == model.play_delay_ms(window.index, 4.0)
    assert window.index == 25 and len(calls) == 25  # the callback that reached the last step scheduled nothing
    assert window._speed is None


def test_7_pressing_play_again_stops_it(tmp_path, make_window):
    window = make_window(make_folder(tmp_path))
    calls = window.after_calls
    window.btn_first.invoke()
    window.play_buttons[4].invoke()
    window.play_buttons[4].invoke()  # stop
    calls[-1][1]()
    assert window.index == 0 and len(calls) == 1
    window.play_buttons[1].invoke()  # 1x from step 0
    assert calls[-1][0] == window.model.play_delay_ms(0, 1.0) == 100
    window.play_buttons[4].invoke()  # the other speed takes over
    assert calls[-1][0] == 25
    calls[-2][1]()  # the 1x callback that was already scheduled does nothing
    assert window.index == 0
    calls[-1][1]()
    assert window.index == 1
    window.btn_next.invoke()  # moving by hand stops playing
    calls[-1][1]()
    assert window.index == 2 and len(calls) == 4
    window.btn_last.invoke()
    window.play_buttons[4].invoke()  # at the last step there is nothing to play
    assert len(calls) == 4


# 8 ---------------------------------------------------------------------------------------------------------------


def test_8_live_hides_the_replay_bar(tmp_path, make_window):
    window = make_window(make_folder(tmp_path))
    assert window.bar.winfo_manager() == "pack" and window._mode() == "ended: max_laps"
    window.set_live(True)
    assert window.bar.winfo_manager() == "" and window._root.title().split(" · ")[1] == "LIVE"
    window.set_live(False)
    assert window.bar.winfo_manager() == "pack" and window._root.title().split(" · ")[1] == "ended: max_laps"
    window.set_mode("LIVE: something")
    assert window._root.title().split(" · ")[1] == "LIVE: something"
    window.set_live(True)  # the default comes back
    assert window._root.title().split(" · ")[1] == "LIVE"


def test_8_a_live_window_never_packs_the_bar_and_a_model_can_arrive_later(tmp_path, make_window, roots):
    folder = make_folder(tmp_path)
    root = open_root()
    roots.append(root)
    window = RouteViewWindow(root, folder, None, live=True)
    assert window.bar.winfo_manager() == "" and window.index is None and window.model is None
    assert root.title().split(" · ")[1] == "LIVE"
    model, events = _live_model(folder)
    window.set_model(model)
    assert window.index is None and len(window.canvas.find_withtag("node")) == 21
    model.extend(events[:4])
    window.sync_why()
    window.show(3)
    assert window.index == 3 and _text_lines(window) == model.why


# 9 ---------------------------------------------------------------------------------------------------------------


def test_9_warnings_are_a_red_label_above_the_canvas(tmp_path, make_window):
    warned = make_window(make_folder(_sub(tmp_path, "a"), None, default_header(map_sha256="f" * 64)))
    assert warned.model.warnings == (WARN_MAP,)
    assert warned.warn_label.cget("text") == "\n".join(warned.model.warnings)
    assert warned.warn_label.winfo_manager() == "pack" and warned.warn_label.cget("fg") == "red"
    assert warned.warn_label.pack_info()["side"] == "top"
    clean = make_window(make_folder(_sub(tmp_path, "b")))
    assert clean.model.warnings == () and clean.warn_label.winfo_manager() == ""
    clean.add_warning("clicking here takes focus from the game")
    assert clean.warn_label.winfo_manager() == "pack"
    assert clean.warn_label.cget("text") == "clicking here takes focus from the game"
    clean.add_warning("second")
    assert clean.warn_label.cget("text").split("\n") == ["clicking here takes focus from the game", "second"]


# 10 --------------------------------------------------------------------------------------------------------------

_FOCUS_ATTRS = {"focus_force", "focus_set", "lift", "tkraise", "grab_set"}
_WRAPPER_CALLS = {"resizable", "wm_resizable", "overrideredirect", "wm_overrideredirect", "transient", "wm_transient",
                  "attributes", "wm_attributes"}
_DIALOG_MODULES = {"messagebox", "filedialog", "simpledialog"}


def focus_problems(source: str) -> list[str]:
    """What in `source` takes focus, opens a dialog or rebuilds Tk's wrapper window (spec 5.7; plan RV-7 test 10)."""
    problems = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Attribute) and node.attr in _FOCUS_ATTRS:
            problems.append(f"line {node.lineno}: .{node.attr}")
        if isinstance(node, ast.Constant) and node.value == "-topmost":
            problems.append(f"line {node.lineno}: '-topmost'")
        if isinstance(node, ast.Import):
            problems += [f"line {node.lineno}: import {a.name}" for a in node.names
                         if a.name.split(".")[0] == "tkinter" and a.name.split(".")[-1] in _DIALOG_MODULES]
        if isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[0] == "tkinter":
            if node.module.split(".")[-1] in _DIALOG_MODULES:
                problems.append(f"line {node.lineno}: from {node.module}")
            problems += [f"line {node.lineno}: from tkinter import {a.name}" for a in node.names
                         if a.name in _DIALOG_MODULES]
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr in _WRAPPER_CALLS:
                problems.append(f"line {node.lineno}: .{func.attr}(...)")
            if node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value == "wm":
                problems.append(f"line {node.lineno}: call(\"wm\", ...)")
    return problems


def test_10_the_viewer_never_takes_focus_or_rebuilds_the_wrapper_window():
    sources = sorted(ROUTE_VIEW_SRC.glob("*.py"))
    assert {p.name for p in sources} >= {"__init__.py", "__main__.py", "model.py", "window.py"}
    for path in sources:
        assert focus_problems(path.read_text(encoding="utf-8")) == [], path.name


def test_10_the_walk_finds_a_planted_sample():
    assert focus_problems("root.wm_resizable(True, True)") == ["line 1: .wm_resizable(...)"]
    planted = "\n".join(["w.focus_force()", "w.attributes('-topmost', True)", "import tkinter.messagebox",
                         "from tkinter import filedialog", "from tkinter.simpledialog import askstring",
                         "root.tk.call('wm', 'resizable', root, 1, 1)", "w.overrideredirect(True)", "w.lift()",
                         "w.transient(root)", "w.grab_set()", "w.tkraise()", "w.focus_set()", "w.resizable(0, 0)"])
    found = focus_problems(planted)
    assert len(found) == 14, found  # line 2 counts twice: the .attributes call and its '-topmost' constant
    assert {int(p.split(":")[0].split()[1]) for p in found} == set(range(1, 14))
    assert focus_problems("root.title('x')\nroot.geometry('1x1')\nc.configure(width=1)") == []


# 14 --------------------------------------------------------------------------------------------------------------


def test_14_a_steps_file_with_a_header_only_opens_with_no_steps(tmp_path, make_window):
    window = make_window(make_folder(tmp_path, []))
    title = window._root.title()
    assert "REPLAY (did not end)" in title and "no steps" in title, title
    assert window._status.get() == NO_STEPS == "no steps logged"
    assert window.index is None and window.model.events == [] and window._view is None
    assert len(window.canvas.find_withtag("node")) == 21 and window.canvas.find_withtag("character") == ()
    assert window.canvas.find_withtag("route") == () and window.canvas.find_withtag("aim") == ()
    assert window.canvas.find_withtag("target") == () and window.canvas.find_withtag("hopnum") == ()
    assert window.panel_label.cget("text") == "" and _canvas_size(window) == (648, 328)
    for press in (window.btn_next, window.btn_prev, window.btn_first, window.btn_last):
        press.invoke()
    window.on_slider("0")
    window.toggle_play(4.0)
    window.go_to_leg()
    assert window.index is None and window.after_calls == []
    window.show(0)  # the window cannot be asked for a step that is not there either
    assert window.index is None


# drawing ---------------------------------------------------------------------------------------------------------


def test_the_canvas_draws_the_step_the_model_describes(tmp_path, make_window):
    window = make_window(make_folder(tmp_path))
    canvas, s = window.canvas, 4
    window.show(6)  # settled at (80, 24), after two moves
    view = window.model.step(6)
    (character,) = canvas.find_withtag("character")
    x0, y0, x1, y1 = canvas.coords(character)
    assert ((x0 + x1) / 2, (y0 + y1) / 2) == (view.character[0] * s, view.character[1] * s) == (320, 96)
    assert view.aim is not None and len(canvas.find_withtag("aim")) == 2
    assert len(canvas.find_withtag("hopnum")) == len(view.hops) > 0
    assert [canvas.itemcget(i, "text") for i in canvas.find_withtag("hopnum")] == [str(h.n) for h in view.hops]
    assert len(canvas.find_withtag("recent")) == len(view.recent)
    (target,) = canvas.find_withtag("target")
    x0, y0, x1, y1 = canvas.coords(target)
    assert ((x0 + x1) / 2, (y0 + y1) / 2) == (101 * s, 24 * s) and canvas.itemcget(target, "outline") == "yellow"
    (route,) = canvas.find_withtag("route")
    assert canvas.coords(route) == [float(c * s) for p in view.route for c in p]
    assert len(canvas.find_withtag("node")) == 21 and len(canvas.find_withtag("label")) == 7
    first = canvas.find_withtag("node")[0]
    assert canvas.itemcget(first, "fill") == "lightblue" and canvas.itemcget(first, "outline") == "blue"
    orange = [i for i in canvas.find_withtag("node") if canvas.itemcget(i, "fill") == "orange"]
    assert len(orange) == 14 and all(canvas.itemcget(i, "outline") == "darkorange" for i in orange)
    window.show(0)  # before any plan: no route, aim or hops, a character is not known yet
    assert canvas.find_withtag("route") == () and canvas.find_withtag("aim") == ()
    assert canvas.find_withtag("hopnum") == () and canvas.find_withtag("character") == ()
    assert len(canvas.find_withtag("target")) == 1


def test_the_title_names_the_mode_folder_lap_leg_target_and_state(tmp_path, make_window):
    folder = make_folder(tmp_path)
    window = make_window(folder)
    window.show(1)
    assert window._root.title() == f"Route view · ended: max_laps · {folder} · lap 0 · leg 5 -> #6 · planned"
    window.show(25)
    assert window._root.title().endswith("· lap 0 · leg 6 -> #7 · ended")
    legs = make_window(make_legs_folder(_sub(tmp_path, "legs")))
    assert legs._root.title().startswith("Route view · REPLAY (from legs.jsonl) · ")
    cut = make_window(make_folder(_sub(tmp_path, "cut"), G[:12]))
    assert cut._root.title().split(" · ")[1] == "REPLAY (did not end)"


# RV-7 review fixes: what the window draws and where (I1) and the stale read-failure status (m1) ------------------------

CHECKER = ((0, 0, 255), (255, 0, 0))


def _checkerboard_png(path, size=(162, 82)) -> None:
    """A one-pixel checkerboard of two colours: an enlargement that is not nearest-neighbour mixes them at block edges."""
    width, height = size
    image = Image.new("RGB", size)
    image.putdata([CHECKER[(i % width + i // width) % 2] for i in range(width * height)])
    image.save(path)


def _photo_pixel(window, x, y) -> tuple[int, ...]:
    """The colour of pixel (x, y) of the picture the canvas holds (Tk's `<image> get x y`; ImageTk has no `get`)."""
    return tuple(int(c) for c in window._root.tk.call(str(window.shown_photo), "get", x, y))


def _top_to_bottom(window) -> list:
    """The root's packed widgets as they stack in the window: the top-side ones in pack order, then the bottom-side ones
    with the last packed first (the packer fills the top edge downwards and the bottom edge upwards)."""
    slaves = window._root.pack_slaves()
    return ([w for w in slaves if w.pack_info()["side"] == "top"]
            + [w for w in slaves if w.pack_info()["side"] == "bottom"][::-1])


def test_the_picture_is_enlarged_with_nearest_neighbour_and_anchored_at_the_top_left(tmp_path, make_window):
    folder = make_folder(tmp_path)
    _checkerboard_png(folder / "minimap.png")
    window = make_window(folder)
    canvas = window.canvas
    for s in (4, 2, 3, 4):
        window.scale_buttons[s].invoke()
        photo = window.shown_photo
        assert (photo.width(), photo.height()) == (162 * s, 82 * s) == _canvas_size(window)  # enlarged by the scale
        (bg,) = canvas.find_withtag("bg")
        assert canvas.itemcget(bg, "image") == str(photo)
        assert canvas.itemcget(bg, "anchor") == "nw" and canvas.coords(bg) == [0.0, 0.0]
        assert canvas.bbox(bg) == (0, 0, 162 * s, 82 * s)  # minimap pixel (x, y) is canvas (x * s, y * s), as the nodes
        assert canvas.find_all()[0] == bg  # the lowest item: everything else is drawn over it
        for px, py in ((0, 0), (1, 0), (0, 1), (5, 3), (161, 81)):  # the first blocks and the last
            for dx in (0, s - 1):
                for dy in (0, s - 1):  # a block's edge pixels keep their own colour: nearest-neighbour, pixels sharp
                    assert _photo_pixel(window, px * s + dx, py * s + dy) == CHECKER[(px + py) % 2], (s, px, py, dx, dy)


def test_nodes_and_their_numbers_are_drawn_at_the_node_positions_in_the_editors_sizes(tmp_path, make_window):
    window = make_window(make_folder(tmp_path))
    canvas, marks = window.canvas, window.model.nodes
    blue = [m for m in marks if m.blue]
    assert len(marks) == 21 and len(blue) == 7
    for s in (4, 2):
        window.set_scale(s)
        nodes = canvas.find_withtag("node")
        assert len(nodes) == 21
        for item, mark in zip(nodes, marks, strict=True):
            x, y, r = mark.pos[0] * s, mark.pos[1] * s, (3 if mark.blue else 2) * s  # NODE_R 3, ORANGE_R 2 (plan)
            assert canvas.coords(item) == [x - r, y - r, x + r, y + r], (s, mark)
            colours = ("lightblue", "blue") if mark.blue else ("orange", "darkorange")
            assert (canvas.itemcget(item, "fill"), canvas.itemcget(item, "outline")) == colours
        labels = canvas.find_withtag("label")
        assert len(labels) == 7
        for item, mark in zip(labels, blue, strict=True):  # beside the node, at x + r + 2 as the editor
            assert canvas.coords(item) == [mark.pos[0] * s + 3 * s + 2, mark.pos[1] * s]
            assert canvas.itemcget(item, "anchor") == "w" and canvas.itemcget(item, "fill") == "white"
            assert canvas.itemcget(item, "text") == mark.label


def test_the_ring_hop_numbers_aim_and_recent_moves_are_drawn_where_the_step_puts_them(tmp_path, make_window):
    window = make_window(make_folder(tmp_path))
    canvas = window.canvas
    window.show(6)  # settled at (80, 24); the plan in force aims at (79, 24); target #6 is at (101, 24)
    view = window.model.step(6)
    assert [h.from_pos for h in view.hops] == [(68, 27), (79, 24), (98, 24)]
    assert [h.landing for h in view.hops] == [(79, 24), (98, 24), (101, 24)] and view.aim == (79, 24)
    assert view.target is not None and view.target.pos == (101, 24)
    for s in (4, 2, 3):
        window.set_scale(s)
        (ring,) = canvas.find_withtag("target")  # RING_R 5 (plan), around the target
        x, y, r = 101 * s, 24 * s, 5 * s
        assert canvas.coords(ring) == [x - r, y - r, x + r, y + r], s
        numbers = canvas.find_withtag("hopnum")  # each hop's number sits at the point its hop lands on
        assert [canvas.coords(i) for i in numbers] == [[79 * s, 24 * s], [98 * s, 24 * s], [101 * s, 24 * s]], s
        assert [canvas.itemcget(i, "text") for i in numbers] == ["1", "2", "3"]
        assert {canvas.itemcget(i, "anchor") for i in numbers} == {"s"}  # the digits stand above that point
        x, y, r = 79 * s, 24 * s, 2 * s  # the aim: an x about the aim, two diagonals
        assert sorted(canvas.coords(i) for i in canvas.find_withtag("aim")) == sorted(
            [[x - r, y - r, x + r, y + r], [x - r, y + r, x + r, y - r]]), s
    window.show(20)  # three finished moves are faint behind it, oldest first
    view = window.model.step(20)
    assert view.recent == (((49, 28), (68, 27)), ((68, 27), (80, 24)), ((101, 24), (105, 24)))
    for s in (4, 2):
        window.set_scale(s)
        recent = canvas.find_withtag("recent")
        assert [canvas.coords(i) for i in recent] == [
            [a[0] * s, a[1] * s, b[0] * s, b[1] * s] for a, b in view.recent], s
        assert {canvas.itemcget(i, "dash") for i in recent} == {"4 3"}  # dashed, as built (the plan: "dashed")
        assert {canvas.itemcget(i, "fill") for i in recent} == {"gray60"}


def test_9_the_warning_label_is_above_the_canvas_and_the_bar_stays_under_the_log(tmp_path, make_window):
    warned = make_window(make_folder(_sub(tmp_path, "a"), None, default_header(map_sha256="f" * 64)))
    order = [warned.warn_label, warned.main, warned.why_frame, warned.bar, warned.status_label]
    assert _top_to_bottom(warned) == order  # warning, canvas and panel, why log, replay bar, status line
    packed = warned._root.pack_slaves()  # the packing order too: it decides who is squeezed when the window is small
    assert packed == [warned.status_label, warned.bar, warned.warn_label, warned.main, warned.why_frame]
    warned.set_live(True)
    assert _top_to_bottom(warned) == [w for w in order if w is not warned.bar]
    warned.set_live(False)
    assert _top_to_bottom(warned) == order  # the bar comes back where it was
    assert warned._root.pack_slaves() == packed  # ... right after the status line in the packing order
    clean = make_window(make_folder(_sub(tmp_path, "b")))
    assert _top_to_bottom(clean) == [clean.main, clean.why_frame, clean.bar, clean.status_label]
    clean.add_warning("clicking here takes focus from the game")
    assert _top_to_bottom(clean) == [clean.warn_label, clean.main, clean.why_frame, clean.bar, clean.status_label]
    clean.add_warning("second")
    assert _top_to_bottom(clean) == [clean.warn_label, clean.main, clean.why_frame, clean.bar, clean.status_label]


def test_6_the_log_scrolls_to_the_current_line(tmp_path, make_window, monkeypatch):
    window = make_window(make_folder(tmp_path))
    seen = []
    real = window.why_text.see
    monkeypatch.setattr(window.why_text, "see", lambda index: (seen.append(index), real(index))[1])
    for k in (3, 20, 0):
        window.show(k)
        assert seen[-1] == f"{window.model.step(k).why_line + 1}.0"
    assert len(seen) == 3


def test_7_the_lap_and_leg_entries_follow_the_step(tmp_path, make_window):
    window = make_window(make_folder(tmp_path))
    for k, leg in ((1, 5), (13, 5), (17, 6), (25, 6)):
        window.show(k)
        view = window.model.step(k)
        assert (view.lap, view.leg) == (0, leg)
        assert (window.lap_var.get(), window.leg_var.get()) == ("0", str(leg))


def test_7_the_pressed_play_button_is_drawn_sunken_until_playback_ends(tmp_path, make_window):
    window = make_window(make_folder(tmp_path))
    calls = window.after_calls

    def reliefs() -> dict[int, str]:
        return {speed: str(button.cget("relief")) for speed, button in window.play_buttons.items()}

    window.btn_first.invoke()
    assert reliefs() == {1: "raised", 4: "raised"}
    window.play_buttons[4].invoke()
    assert reliefs() == {1: "raised", 4: "sunken"}
    window.play_buttons[1].invoke()  # the other speed takes over
    assert reliefs() == {1: "sunken", 4: "raised"}
    window.play_buttons[1].invoke()  # pressed again: stop
    assert reliefs() == {1: "raised", 4: "raised"}
    window.play_buttons[4].invoke()
    window.btn_next.invoke()  # moving by hand
    assert reliefs() == {1: "raised", 4: "raised"}
    window.btn_first.invoke()
    window.play_buttons[4].invoke()
    window.lap_var.set("0")
    window.leg_var.set("6")
    window.go_to_leg()  # a jump by hand stops it too
    assert window.index == 16 and window._speed is None and reliefs() == {1: "raised", 4: "raised"}
    window.btn_first.invoke()
    window.play_buttons[4].invoke()
    for _ in range(40):
        if window._speed is None:
            break
        calls[-1][1]()
    assert window.index == 25 and reliefs() == {1: "raised", 4: "raised"}


def test_8_going_live_stops_a_playback(tmp_path, make_window):
    window = make_window(make_folder(tmp_path))
    calls = window.after_calls
    window.btn_first.invoke()
    window.play_buttons[4].invoke()
    assert window._speed == 4.0 and str(window.play_buttons[4].cget("relief")) == "sunken"
    window.set_live(True)
    assert window._speed is None and str(window.play_buttons[4].cget("relief")) == "raised"
    calls[-1][1]()  # the callback that was already scheduled finds a new generation and does nothing
    assert window.index == 0


def test_14_the_no_steps_status_goes_when_the_first_step_arrives(tmp_path, make_window):
    folder = make_folder(tmp_path)
    model, events = _live_model(folder)
    assert model.events == []
    window = make_window((model, folder), live=True)
    assert window._status.get() == NO_STEPS and window.index is None
    model.extend(events[:2])
    window.show(1)
    assert window.index == 1 and window._status.get() == ""


def test_5_a_status_from_a_failed_read_goes_when_a_show_needs_no_file(tmp_path, make_window):
    folder = make_folder(_sub(tmp_path, "snapshot"))
    (folder / "minimap.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"junk")
    window = make_window(folder)
    assert not window.background_ok and window._status.get().startswith("cannot read minimap.png: ")
    (folder / "minimap.png").unlink()
    window.show(1)  # nothing is asked for any more, so nothing failed: the dark canvas and no stale status
    assert window.shown_background == "dark" and window.background_ok and window._status.get() == ""

    framed = make_folder(_sub(tmp_path, "frames"))
    _write_frames(framed, times=(1.05, 1.5, 3.0))
    (framed / FRAMES_DIR / "frame_000000.png").write_bytes(b"junk")
    window = make_window(framed)
    window.show(1)  # 1.100 s: frame 0, which cannot be read
    assert not window.background_ok and window._status.get().startswith("cannot read frame_000000.png: ")
    window.show(0)  # 1.000 s: no frame yet and no snapshot to ask for
    assert window.shown_background == "dark" and window.background_ok and window._status.get() == ""
    window.show(1)
    window.set_status("something else")  # a status somebody else set is not ours to clear
    window.show(0)
    assert window._status.get() == "something else"
