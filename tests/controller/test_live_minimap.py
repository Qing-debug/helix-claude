"""The Route tab's idle live minimap (spec 5.2 "What the view area shows", H70; plan C7): `LiveMinimap` draws the
shared minimap frame at 4x, the loaded map's nodes and the character, and nothing else.

Same real-Tk-root setup as test_gui_startup.py (skipped where Tk cannot open a display; `after` only records). The
frames are the synthetic ones of test_gui_character_buttons.py (the real icon pasted on a noisy background)."""
import tkinter as tk
from pathlib import Path

import numpy as np
import pytest

import src.gui.live_minimap as live_minimap_module
import test_gui_startup
from src.controller.node import Node
from src.gui.live_minimap import NOT_FOUND_TEXT, LiveMinimap
from src.route.character import CharacterLocator
from src.route_view.model import DEFAULT_SCALE
from src.route_view.window import DARK, NODE_R, ORANGE_R
from test_gui_character_buttons import FIXTURE_MAP, FRAME_H, FRAME_W, _blank, _centre, _with_icons
from test_gui_startup import _console, _lock_is_free

make_gui = test_gui_startup.make_gui  # the fixture, shared (no conftest.py under tests/)
S = DEFAULT_SCALE
ICON_AT = (60, 30)  # the planted icon's top-left corner


@pytest.fixture
def make_minimap(make_gui):
    """`make(frame, csv=FIXTURE_MAP)`: a window with no Route tab whose shared frame is `frame` and whose loaded map is
    `csv`, and a `LiveMinimap` packed in a frame of its root."""
    def make(frame, csv=FIXTURE_MAP):
        gui, shared, _ = make_gui(frame=frame, route_tab_factory=None)
        gui.controller.agent.CSVParser.CSV_file = csv
        host = tk.Frame(gui.gui)
        host.pack()
        minimap = LiveMinimap(host, gui)
        minimap.canvas.pack()
        return gui, shared, minimap
    return make


def _items(canvas, tag):
    return canvas.find_withtag(tag)


def _centre_of(canvas, item):
    x0, y0, x1, y1 = canvas.coords(item)
    return (x0 + x1) / 2, (y0 + y1) / 2


def test_minimap_draws_frame_nodes_and_character(make_minimap, monkeypatch):
    frame = _with_icons(ICON_AT)
    gui, shared, minimap = make_minimap(frame)
    built, real = [], CharacterLocator.from_files
    monkeypatch.setattr(CharacterLocator, "from_files", lambda *paths: (built.append(paths), real(*paths))[1])
    counter = Node.node_id_counter
    minimap.draw()
    minimap.draw()
    canvas = minimap.canvas
    assert (int(canvas.cget("width")), int(canvas.cget("height"))) == (FRAME_W * S, FRAME_H * S)
    assert _lock_is_free(shared) and Node.node_id_counter == counter  # the editor's reader makes no Node
    assert len(built) == 1  # the locator is built once
    (bg,) = _items(canvas, "bg")
    assert canvas.coords(bg) == [0.0, 0.0] and canvas.find_all()[0] == bg  # the picture is the lowest item
    image = canvas.itemcget(bg, "image")
    assert (canvas.tk.call("image", "width", image), canvas.tk.call("image", "height", image)) == (FRAME_W * S, FRAME_H * S)
    for x, y in ((0, 0), (45, 28), (ICON_AT[0] + 3, ICON_AT[1] + 2)):  # nearest neighbour: each pixel a 4x4 block
        for dx, dy in ((0, 0), (S - 1, S - 1)):
            got = tuple(int(v) for v in canvas.tk.splitlist(canvas.tk.call(image, "get", x * S + dx, y * S + dy)))
            assert got == tuple(int(v) for v in frame[y, x])

    rows = [line.split(",") for line in FIXTURE_MAP.read_text().splitlines()[2:]]
    blue = [(int(r[1].strip('"( ')), int(r[2].strip('") '))) for r in rows if r[0] == "blue_node"]
    orange = [(int(r[1].strip('"( ')), int(r[2].strip('") '))) for r in rows if r[0] == "orange_node"]
    nodes = _items(canvas, "node")
    assert [_centre_of(canvas, n) for n in nodes] == [(x * S, y * S) for x, y in blue + orange]
    assert [canvas.coords(n)[2] - canvas.coords(n)[0] for n in nodes] == (
        [2 * NODE_R * S] * len(blue) + [2 * ORANGE_R * S] * len(orange))
    labels = _items(canvas, "label")
    assert [canvas.itemcget(t, "text") for t in labels] == [f"#{i}" for i in range(1, len(blue) + 1)]
    assert [canvas.coords(t) for t in labels] == [[x * S + NODE_R * S + 2, y * S] for x, y in blue]
    (character,) = _items(canvas, "character")
    cx, cy = _centre(ICON_AT)
    assert _centre_of(canvas, character) == (cx * S, cy * S)
    assert canvas.find_all()[-1] == character  # drawn last, over the nodes, as the view does


def test_minimap_without_a_frame_says_not_found(make_minimap):
    gui, shared, minimap = make_minimap(None)
    minimap.draw()
    canvas = minimap.canvas
    assert str(canvas.cget("bg")) == DARK
    (text,) = canvas.find_all()
    assert canvas.gettags(text) == ("notfound",) and canvas.itemcget(text, "text") == NOT_FOUND_TEXT
    shared.minimap_frame = _with_icons(ICON_AT)  # the first frame arrives
    minimap.draw()
    assert _items(canvas, "notfound") == () and len(_items(canvas, "bg")) == 1 and _items(canvas, "character")


@pytest.mark.parametrize("csv", [None, "missing"])
def test_minimap_without_a_map_draws_no_nodes(make_minimap, tmp_path, csv):
    """No map loaded, or a map file that cannot be read: the minimap and the character only."""
    gui, _, minimap = make_minimap(_with_icons(ICON_AT), csv=None if csv is None else tmp_path / "gone.csv")
    minimap.draw()
    canvas = minimap.canvas
    assert _items(canvas, "node") == () and _items(canvas, "label") == ()
    assert len(_items(canvas, "bg")) == 1 and len(_items(canvas, "character")) == 1


def test_minimap_reads_nodes_again_only_when_the_csv_changes(make_minimap, monkeypatch):
    gui, _, minimap = make_minimap(_blank())
    reads, real = [], live_minimap_module.read_editor_nodes
    monkeypatch.setattr(live_minimap_module, "read_editor_nodes", lambda path: (reads.append(path), real(path))[1])
    for _ in range(3):
        minimap.draw()
    assert reads == [str(FIXTURE_MAP)]
    parser = gui.controller.agent.CSVParser
    parser.CSV_file = Path(str(FIXTURE_MAP))  # Load Map again, the same file: a new object (controller.py:121)
    minimap.draw()
    minimap.draw()
    assert reads == [str(FIXTURE_MAP)] * 2 and len(_items(minimap.canvas, "node")) == 21
    parser.CSV_file = None  # a load that failed
    minimap.draw()
    assert reads == [str(FIXTURE_MAP)] * 2 and _items(minimap.canvas, "node") == ()


@pytest.mark.parametrize("frame", [_blank(), _with_icons((20, 20), (100, 40))], ids=["miss", "ambiguous"])
def test_minimap_miss_writes_no_console_line(make_minimap, capsys, frame):
    gui, _, minimap = make_minimap(frame)
    console = _console(gui)
    minimap.draw()
    assert _items(minimap.canvas, "character") == () and len(_items(minimap.canvas, "bg")) == 1
    assert _console(gui) == console and capsys.readouterr() == ("", "")


def test_minimap_draw_never_raises(make_minimap, capsys):
    gui, shared, minimap = make_minimap(_with_icons(ICON_AT))
    minimap.draw()
    drawn = minimap.canvas.find_all()
    shared.minimap_frame = np.zeros((2, 2, 2, 2), dtype=np.uint8)  # Image.fromarray cannot read it
    minimap.draw()
    assert minimap.canvas.find_all() == drawn and _lock_is_free(shared)  # the last drawing stays
    gui.sharedInfo = None  # no holder: drawn as "not found yet"
    minimap.draw()
    assert [minimap.canvas.itemcget(i, "text") for i in minimap.canvas.find_all()] == [NOT_FOUND_TEXT]
    minimap.canvas.destroy()  # the window going away under a queued drain
    minimap.draw()
    assert capsys.readouterr() == ("", "")
