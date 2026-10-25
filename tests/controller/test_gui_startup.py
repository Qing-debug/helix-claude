"""The main window's startup (issue 1, decision H18): it appears at once instead of sleeping and looping until the
game's minimap exists, and the minimap area is built by `_updateCanvas` when the first frame arrives.

Unlike the other GUI tests, these build a real `MapleGUI` on a real Tk root (withdrawn, never shown, no mainloop), so
they are skipped where Tk cannot open a display. `after` callbacks never run; `_updateCanvas` is called by hand."""
import threading
import time
import tkinter as tk
from types import SimpleNamespace

import numpy as np
import pytest

import src.gui.gui as gui_module
from src.controller.controller import Controller

FRAME_H, FRAME_W = 20, 30


def _shared(frame=None):
    return SimpleNamespace(minimap_frame_lock=threading.Lock(), minimap_frame=frame)


def _frame(value=0):
    return np.full((FRAME_H, FRAME_W, 3), value, dtype=np.uint8)


def _lock_is_free(shared) -> bool:
    free = shared.minimap_frame_lock.acquire(blocking=False)
    if free:
        shared.minimap_frame_lock.release()
    return free


def _open_window(shared):
    """A MapleGUI on a new Tk root. On the dev PC, Tk now and then fails to read its own init files ("couldn't read
    file ... init.tcl: No error") for a moment, so try a few times before skipping the test."""
    for attempt in range(4):
        try:
            return gui_module.MapleGUI(controller=Controller(sharedInfo=shared), sharedInfo=shared)
        except tk.TclError as err:
            if attempt == 3:
                pytest.skip(f"Tk cannot open a display here: {err}")
            threading.Event().wait(0.3)  # not time.sleep: one test patches it to prove the window never sleeps


@pytest.fixture
def make_gui(monkeypatch):
    """A set-up window (its `_setupGUI` run) with no minimap frame yet; `after` only records, so nothing is re-run.
    `time.sleep` raises for the whole test: the old startup wait (sleep 5 s, then retry every 10 s) must make a test
    fail at once, not hang it."""
    made = []

    def no_sleep(seconds):
        raise AssertionError(f"the main window slept {seconds} s")

    monkeypatch.setattr(time, "sleep", no_sleep)

    def make(frame=None):
        shared = _shared(frame)
        gui = _open_window(shared)
        made.append(gui)
        gui.gui.withdraw()
        scheduled = []
        monkeypatch.setattr(gui.gui, "after", lambda ms, func: scheduled.append((ms, func)))
        gui._setupGUI()
        return gui, shared, scheduled

    yield make
    for gui in made:
        gui.gui.destroy()


def _console(gui) -> str:
    return gui.gui_console.get("1.0", "end")


def test_setup_returns_at_once_without_a_minimap(make_gui):
    started = time.perf_counter()
    gui, shared, _ = make_gui()
    assert time.perf_counter() - started < 5

    assert gui.minimap_canvas is None
    assert gui.minimap_status_label is not None
    assert gui.minimap_status_label.cget("text") == gui_module.MINIMAP_NOT_FOUND_LABEL
    assert "Minimap not found yet" in gui.minimap_status_label.cget("text")
    assert _lock_is_free(shared)


def test_update_without_a_frame_keeps_waiting_and_holds_nothing(make_gui):
    gui, shared, scheduled = make_gui()

    gui._updateCanvas()

    assert gui.minimap_canvas is None
    assert gui.minimap_status_label.winfo_exists()
    assert scheduled == [(10, gui._updateCanvas)]
    assert _lock_is_free(shared)


def test_first_frame_builds_the_minimap_area_in_place_of_the_label(make_gui):
    gui, shared, scheduled = make_gui()
    gui._updateCanvas()
    label = gui.minimap_status_label

    shared.minimap_frame = _frame()
    gui._updateCanvas()

    assert gui.minimap_canvas is not None
    assert int(gui.minimap_canvas.cget("width")) == FRAME_W and int(gui.minimap_canvas.cget("height")) == FRAME_H
    assert (gui.minimap_canvas_width, gui.minimap_canvas_height) == (FRAME_W, FRAME_H)
    assert gui.minimap_status_label is None and not label.winfo_exists()
    assert gui.minimap_canvas.winfo_parent() == str(gui.scrollable_frame)
    assert gui.minimap_canvas_bg_img_id is not None
    assert len(gui.minimap_canvas.find_all()) == 1
    assert scheduled[-1] == (10, gui._updateCanvas)
    assert _lock_is_free(shared)


def test_later_frames_update_the_same_canvas_and_image(make_gui):
    gui, shared, _ = make_gui(frame=_frame())
    gui._updateCanvas()
    canvas, image_id, first_image = gui.minimap_canvas, gui.minimap_canvas_bg_img_id, gui.minimap_canvas.image

    shared.minimap_frame = _frame(200)
    gui._updateCanvas()

    assert gui.minimap_canvas is canvas
    assert gui.minimap_canvas_bg_img_id == image_id
    assert len(canvas.find_all()) == 1
    assert canvas.image is not first_image


def test_drawing_happens_outside_the_frame_lock(make_gui, monkeypatch):
    """The lock is held only to read the frame reference, never across PIL or Tk work (it starves the vision thread)."""
    gui, shared, _ = make_gui(frame=_frame())
    checked = []

    def lock_free_then(real):
        def wrapper(*args, **kwargs):
            checked.append(_lock_is_free(shared))
            return real(*args, **kwargs)
        return wrapper

    monkeypatch.setattr(gui, "_createMinimapCanvas", lock_free_then(gui._createMinimapCanvas))
    monkeypatch.setattr(gui_module, "Image", SimpleNamespace(fromarray=lock_free_then(gui_module.Image.fromarray)))
    monkeypatch.setattr(gui_module, "ImageTk", SimpleNamespace(PhotoImage=lock_free_then(gui_module.ImageTk.PhotoImage)))

    gui._updateCanvas()  # first frame: canvas, PIL image, PhotoImage, create_image
    shared.minimap_frame = _frame(200)
    gui._updateCanvas()  # later frame: PIL image, PhotoImage, itemconfig

    assert len(checked) == 5  # create canvas once; PIL image and PhotoImage twice each
    assert all(checked)


@pytest.mark.parametrize("button", ["drawBlueNodeOnCharacter", "drawOrangeNodeOnCharacter", "showPathToGoalNode",
                                    "_clearDrawnNodesOnGUIMinimap", "_openMapEditor"])
def test_buttons_that_need_the_minimap_say_so_before_it_exists(make_gui, monkeypatch, button):
    gui, _, _ = make_gui()

    def must_not_run(*args):
        raise AssertionError("used the minimap before the first frame")

    monkeypatch.setattr(gui, "_findCharacter", must_not_run)
    monkeypatch.setattr(gui_module, "MapEditor", must_not_run)

    getattr(gui, button)()

    assert gui_module.MINIMAP_NOT_FOUND_MESSAGE in _console(gui)
    assert gui.node_count == 0
    assert gui.map_editor is None


def test_draw_and_clear_buttons_work_once_the_first_frame_has_arrived(make_gui, monkeypatch):
    gui, _, _ = make_gui(frame=_frame())
    gui._updateCanvas()
    monkeypatch.setattr(gui, "_findCharacter", lambda: (5, 6))  # where the finder works is test_gui_character_buttons.py

    gui.drawBlueNodeOnCharacter()
    gui.drawOrangeNodeOnCharacter()

    assert gui.node_count == 2
    assert gui_module.MINIMAP_NOT_FOUND_MESSAGE not in _console(gui)
    assert "Identifier: 2, Type of Node: Orange Node | Character Position: (5,6)" in _console(gui)
    assert len(gui.minimap_canvas.find_withtag("node")) == 2
    assert len(gui.minimap_canvas.find_withtag("node_tag")) == 2

    gui._clearDrawnNodesOnGUIMinimap()

    assert gui.minimap_canvas.find_withtag("node") == () and gui.minimap_canvas.find_withtag("node_tag") == ()
    assert len(gui.minimap_canvas.find_all()) == 1  # the minimap image stays


def test_map_editor_opens_once_the_first_frame_has_arrived(make_gui, monkeypatch):
    gui, _, _ = make_gui(frame=_frame())
    gui._updateCanvas()
    opened = []

    def fake_editor(root, controller, shared):
        opened.append((root, controller, shared))
        return SimpleNamespace(window=SimpleNamespace(protocol=lambda *args: None))

    monkeypatch.setattr(gui_module, "MapEditor", fake_editor)

    gui._openMapEditor()

    assert len(opened) == 1 and gui.map_editor is not None
    assert gui_module.MINIMAP_NOT_FOUND_MESSAGE not in _console(gui)
