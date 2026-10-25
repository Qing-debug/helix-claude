"""The main window's "draw node on character" buttons and the dev "get path" button find the character with the route
walker's locator (`src.route.character`), and say "Character not found" instead of drawing a guess (issue 2, decision H20).

Same real-Tk-root setup as test_gui_startup.py (skipped where Tk cannot open a display). The frames are synthetic, built
as in tests/route/test_character.py: a noisy dark background, grey platform lines, and the real icon from assets/chr/
pasted where its mask is on. Nothing here needs the game."""
from pathlib import Path

import cv2
import numpy as np
import pytest

import src.gui.gui as gui_module
from src.classes.classes import Kanna
from src.controller.node import CharacterNode
from src.route.character import CharacterLocator
import test_gui_startup
from test_gui_startup import _console, _lock_is_free

make_gui = test_gui_startup.make_gui  # the fixture, shared (no conftest.py under tests/)

ASSETS = Path(__file__).resolve().parent.parent.parent / "assets" / "chr"
FIXTURE_MAP = Path(__file__).resolve().parent.parent / "fixtures" / "fossil_foundry_2.csv"
FRAME_W, FRAME_H = 162, 82

_icon_bgr = cv2.imread(str(ASSETS / "chr.png"), cv2.IMREAD_COLOR)
_mask = cv2.imread(str(ASSETS / "chr_mask.png"), cv2.IMREAD_GRAYSCALE)
assert _icon_bgr is not None and _mask is not None
ICON_RGB = cv2.cvtColor(_icon_bgr, cv2.COLOR_BGR2RGB)
ICON_ON = _mask > 0
ICON_H, ICON_W = ICON_ON.shape
MISS_TEXT = "Character not found on the minimap (no place on the minimap looks like the character icon)."
AMBIGUOUS_TEXT = "Character not found on the minimap (two separate places look almost equally like the character icon)."


def _blank(seed=0):
    noise = np.random.default_rng(seed).integers(-10, 11, size=(FRAME_H, FRAME_W, 1))
    frame = np.repeat(30 + noise, 3, axis=2).astype(np.uint8)
    frame[[28, 45, 60], :, :] = 120
    return frame


def _with_icons(*top_lefts, seed=0):
    frame = _blank(seed)
    for x, y in top_lefts:
        frame[y:y + ICON_H, x:x + ICON_W][ICON_ON] = ICON_RGB[ICON_ON]
    return frame


def _centre(top_left):
    return top_left[0] + ICON_W // 2, top_left[1] + ICON_H // 2


def _window(make_gui, frame):
    gui, shared, scheduled = make_gui(frame=frame)
    gui._updateCanvas()  # the first frame builds the minimap area the buttons draw on
    return gui, shared


def _nothing_drawn(gui):
    assert gui.node_count == 0
    assert gui.minimap_canvas.find_withtag("node") == () and gui.minimap_canvas.find_withtag("node_tag") == ()
    assert len(gui.minimap_canvas.find_all()) == 1  # the minimap image stays


@pytest.mark.parametrize("button, node_type", [("drawBlueNodeOnCharacter", "Blue Node"),
                                               ("drawOrangeNodeOnCharacter", "Orange Node")])
def test_a_found_character_gets_a_dot_at_the_centre_of_its_icon(make_gui, button, node_type):
    top_left = (40, 20)
    gui, _ = _window(make_gui, _with_icons(top_left))
    x, y = _centre(top_left)

    getattr(gui, button)()

    assert gui.node_count == 1
    assert f"Identifier: 1, Type of Node: {node_type} | Character Position: ({x},{y})" in _console(gui)
    assert "Character not found" not in _console(gui)
    (oval,) = gui.minimap_canvas.find_withtag("node")
    assert gui.minimap_canvas.coords(oval) == [x - 2, y - 2, x + 2, y + 2]
    assert len(gui.minimap_canvas.find_withtag("node_tag")) == 1


@pytest.mark.parametrize("button", ["drawBlueNodeOnCharacter", "drawOrangeNodeOnCharacter"])
def test_a_missing_character_draws_nothing_and_says_so(make_gui, button):
    gui, _ = _window(make_gui, _blank())

    getattr(gui, button)()

    assert MISS_TEXT in _console(gui)
    assert "Character Position" not in _console(gui)
    _nothing_drawn(gui)


@pytest.mark.parametrize("button", ["drawBlueNodeOnCharacter", "drawOrangeNodeOnCharacter"])
def test_two_look_alikes_are_not_sure_so_nothing_is_drawn(make_gui, button):
    gui, _ = _window(make_gui, _with_icons((10, 10), (110, 50)))

    getattr(gui, button)()

    assert AMBIGUOUS_TEXT in _console(gui)
    _nothing_drawn(gui)


def test_the_count_only_moves_when_a_dot_is_drawn(make_gui):
    gui, shared = _window(make_gui, _with_icons((40, 20)))
    gui.drawBlueNodeOnCharacter()

    shared.minimap_frame = _blank()  # the icon is gone (a map change, say)
    gui.drawBlueNodeOnCharacter()
    shared.minimap_frame = _with_icons((100, 40))
    gui.drawOrangeNodeOnCharacter()

    assert gui.node_count == 2
    assert "Identifier: 2, Type of Node: Orange Node" in _console(gui)
    assert _console(gui).count("Character not found") == 1
    assert len(gui.minimap_canvas.find_withtag("node")) == 2


def _path_window(make_gui, frame, monkeypatch):
    """A window with the fossil-foundry map loaded and Kanna picked, so showPathToGoalNode has a real graph. `_drawGraph`
    opens a matplotlib window, so it is replaced; `connectCharacterNode` is wrapped to record the character node."""
    gui, shared = _window(make_gui, frame)
    gui.controller.setCharacterClass(Kanna())
    gui.controller.loadMap(FIXTURE_MAP)
    assert gui.controller.agent.pathGraph is not None
    monkeypatch.setattr(gui, "_drawGraph", lambda: None)
    connected = []
    real = gui.controller.connectCharacterNode

    def spy(char_node=None):
        connected.append(char_node)
        real(char_node)

    monkeypatch.setattr(gui.controller, "connectCharacterNode", spy)
    return gui, connected


def test_get_path_starts_from_the_found_character(make_gui, monkeypatch, capsys):
    top_left = (40, 20)
    gui, connected = _path_window(make_gui, _with_icons(top_left), monkeypatch)
    capsys.readouterr()  # drop what loading the map printed

    gui.showPathToGoalNode()

    assert len(connected) == 1 and isinstance(connected[0], CharacterNode)
    assert connected[0].minimap_position == _centre(top_left)
    assert "Character not found" not in _console(gui)
    assert "goal node" in capsys.readouterr().out  # "computed path ..." or "no path found ...", the walk went on


def test_get_path_with_a_missing_character_says_so_and_does_nothing(make_gui, monkeypatch, capsys):
    gui, connected = _path_window(make_gui, _blank(), monkeypatch)
    nodes_before = len(gui.controller.getNodeMap())
    capsys.readouterr()  # drop what loading the map printed

    gui.showPathToGoalNode()

    assert MISS_TEXT in _console(gui)
    assert connected == []
    assert len(gui.controller.getNodeMap()) == nodes_before
    assert capsys.readouterr().out == ""


def test_the_locator_is_built_once_and_searches_outside_the_frame_lock(make_gui, monkeypatch):
    gui, shared = _window(make_gui, _with_icons((40, 20)))
    builds, lock_free = [], []
    real_from_files = CharacterLocator.from_files
    real_locate = CharacterLocator.locate

    def counting_from_files(icon_path, mask_path):
        builds.append((icon_path, mask_path))
        return real_from_files(icon_path, mask_path)

    def checking_locate(self, frame):
        lock_free.append(_lock_is_free(shared))
        return real_locate(self, frame)

    monkeypatch.setattr(CharacterLocator, "from_files", staticmethod(counting_from_files))
    monkeypatch.setattr(CharacterLocator, "locate", checking_locate)

    gui.drawBlueNodeOnCharacter()
    gui.drawOrangeNodeOnCharacter()

    assert builds == [(gui_module.CHARACTER_ICON_PATH, gui_module.CHARACTER_MASK_PATH)]
    assert lock_free == [True, True]
    assert gui.node_count == 2


def test_an_unreadable_icon_file_is_reported_and_retried(make_gui, monkeypatch, tmp_path):
    gui, _ = _window(make_gui, _with_icons((40, 20)))
    monkeypatch.setattr(gui_module, "CHARACTER_ICON_PATH", tmp_path / "no_such_icon.png")

    gui.drawBlueNodeOnCharacter()

    assert "Cannot look for the character: cannot read the icon image" in _console(gui)
    _nothing_drawn(gui)
    assert gui._character_locator is None

    monkeypatch.setattr(gui_module, "CHARACTER_ICON_PATH", ASSETS / "chr.png")  # the file is back
    gui.drawBlueNodeOnCharacter()

    assert gui.node_count == 1


def test_a_frame_the_finder_rejects_shows_its_reason(make_gui):
    gui, shared = _window(make_gui, _blank())
    shared.minimap_frame = np.zeros((5, 5, 3), np.uint8)  # smaller than the icon

    gui.drawBlueNodeOnCharacter()

    assert "Character not found on the minimap (bad frame: 5 x 5 is smaller than the" in _console(gui)
    _nothing_drawn(gui)
