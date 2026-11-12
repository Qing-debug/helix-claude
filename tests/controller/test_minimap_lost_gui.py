"""VIS162-FIX: while the capture has lost the minimap, the windows show "Minimap lost: searching for it" in place of the
frozen picture, and the picture comes back with the next frame. The flag is `SharedInfo.minimap_lost`; a holder without
it (test fakes, the walk CLI's) means nothing is lost. Same real-Tk-root setup as test_gui_startup.py."""
import threading
import tkinter as tk
from types import SimpleNamespace

import test_gui_startup
from test_gui_character_buttons import _with_icons
from test_gui_startup import FRAME_H, FRAME_W, _frame, _lock_is_free
from test_live_minimap import ICON_AT, make_minimap  # noqa: F401 (a fixture, used by name)

from src.gui.live_minimap import NOT_FOUND_TEXT
from src.gui.map_editor import MapEditor
from src.gui.minimap_lost import LOST_TAG, LOST_TEXT, read_minimap

make_gui = test_gui_startup.make_gui  # the fixture, shared (no conftest.py under tests/)


def _texts(canvas, tag):
    return [canvas.itemcget(item, "text") for item in canvas.find_withtag(tag) if canvas.type(item) == "text"]


def test_read_minimap_reads_both_under_the_lock_and_defaults_to_not_lost():
    shared = SimpleNamespace(minimap_frame_lock=threading.Lock(), minimap_frame=_frame())
    frame, lost = read_minimap(shared)
    assert frame is shared.minimap_frame and lost is False and _lock_is_free(shared)  # no flag on the holder: not lost
    shared.minimap_lost = True
    assert read_minimap(shared)[1] is True and _lock_is_free(shared)


def test_main_window_shows_lost_in_place_of_the_frozen_picture_and_gets_the_picture_back(make_gui):
    gui, shared, scheduled = make_gui(frame=_frame(), route_tab_factory=None)
    gui._updateCanvas()
    canvas, image_id, first_image = gui.minimap_canvas, gui.minimap_canvas_bg_img_id, gui.minimap_canvas.image
    assert len(canvas.find_all()) == 1 and not canvas.find_withtag(LOST_TAG)

    shared.minimap_lost = True
    gui._updateCanvas()
    gui._updateCanvas()  # drawn once, not again every 10 ms
    assert _texts(canvas, LOST_TAG) == [LOST_TEXT] and len(canvas.find_withtag(LOST_TAG)) == 2
    assert canvas.find_all()[-2:] == canvas.find_withtag(LOST_TAG)  # over the picture
    assert canvas.image is first_image  # the frozen picture is not redrawn
    assert scheduled[-1] == (10, gui._updateCanvas) and _lock_is_free(shared)

    shared.minimap_lost = False
    shared.minimap_frame = _frame(200)
    gui._updateCanvas()
    assert not canvas.find_withtag(LOST_TAG) and len(canvas.find_all()) == 1
    assert gui.minimap_canvas_bg_img_id == image_id and canvas.image is not first_image


def test_main_window_builds_its_canvas_from_a_lost_frame_too(make_gui):
    """The window opened after the minimap was lost: the frame in `shared` is the last one, the flag is up."""
    gui, shared, _ = make_gui(frame=_frame(), route_tab_factory=None)
    shared.minimap_lost = True
    gui._updateCanvas()
    assert gui.minimap_canvas is not None and _texts(gui.minimap_canvas, LOST_TAG) == [LOST_TEXT]


def test_live_minimap_shows_lost_in_place_of_the_frozen_picture(make_minimap):
    gui, shared, minimap = make_minimap(_with_icons(ICON_AT))
    canvas = minimap.canvas
    minimap.draw()
    size = (int(canvas.cget("width")), int(canvas.cget("height")))
    assert canvas.find_withtag("bg") and canvas.find_withtag("character")

    shared.minimap_lost = True
    minimap.draw()
    (text,) = canvas.find_all()  # no picture, no node, no character
    assert canvas.gettags(text) == ("lost",) and canvas.itemcget(text, "text") == LOST_TEXT
    assert (int(canvas.cget("width")), int(canvas.cget("height"))) == size and _lock_is_free(shared)

    shared.minimap_lost = False
    minimap.draw()
    assert not canvas.find_withtag("lost") and canvas.find_withtag("bg") and canvas.find_withtag("character")


def test_live_minimap_before_the_first_frame_says_not_found_even_with_the_flag_up(make_minimap):
    gui, shared, minimap = make_minimap(None)
    shared.minimap_lost = True
    minimap.draw()
    (text,) = minimap.canvas.find_all()
    assert minimap.canvas.itemcget(text, "text") == NOT_FOUND_TEXT


def test_map_editor_shows_lost_over_the_frozen_picture(make_gui):
    gui, shared, _ = make_gui(frame=_frame(), route_tab_factory=None)
    w, h = FRAME_W * 2, FRAME_H * 2  # RENDER_SCALE 2
    canvas = tk.Canvas(gui.gui, width=w, height=h)
    scheduled = []
    editor = SimpleNamespace(window=SimpleNamespace(winfo_exists=lambda: True, after=lambda ms, fn: scheduled.append((ms, fn))),
                             minimap_canvas=canvas, minimap_canvas_width=w, minimap_canvas_height=h,
                             minimap_canvas_bg_img_id=None, sharedInfo=shared, _updateCanvas=lambda: None)
    MapEditor._updateCanvas(editor)  # type: ignore[arg-type]
    first_image = canvas.image  # type: ignore[attr-defined]
    assert len(canvas.find_all()) == 1 and not canvas.find_withtag(LOST_TAG)

    shared.minimap_lost = True
    MapEditor._updateCanvas(editor)  # type: ignore[arg-type]
    assert _texts(canvas, LOST_TAG) == [LOST_TEXT] and canvas.image is first_image  # type: ignore[attr-defined]

    shared.minimap_lost = False
    shared.minimap_frame = _frame(200)
    MapEditor._updateCanvas(editor)  # type: ignore[arg-type]
    assert not canvas.find_withtag(LOST_TAG) and canvas.image is not first_image  # type: ignore[attr-defined]
    assert [ms for ms, _ in scheduled] == [10, 10, 10]
