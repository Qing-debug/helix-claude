"""A bad map file (decision H16): `MapleAgent.loadMap` returns the checker's message as a Failure, keeps nothing of
the file (the CSV_file rule in the root CLAUDE.md, "Controller"), and the main window shows the message in a message
box. No test drives Tk: `MapleGUI._loadMapBrowse` runs on a stand-in `self` with the dialogs patched."""
from pathlib import Path
from types import SimpleNamespace

from returns.pipeline import is_successful
from returns.result import Success

import src.gui.gui as gui_module
from src.classes.classes import Kanna
from src.controller.controller import BuildOutcome, MapleAgent

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "fossil_foundry_2.csv"
HEADER = "nodetype,pos_in_minimap,can_fall,fall_dist_exceeds_vertical_tp,is_left_platform_edge,is_right_platform_edge\n"
BAD_ROW = 'blue_node,"(1, 2)",FALSE,FALSE,FALSE,FALSE,\n'  # stray trailing comma


def _bad_map(tmp_path: Path) -> Path:
    path = tmp_path / "bad.csv"
    path.write_text(HEADER + BAD_ROW, newline="")
    return path


def test_bad_file_after_good_one_leaves_no_map_loaded(tmp_path):
    agent = MapleAgent(characterClass=Kanna())
    assert agent.loadMap(FIXTURE) == Success(BuildOutcome.BUILT)

    result = agent.loadMap(_bad_map(tmp_path))

    assert not is_successful(result)
    assert "bad.csv is not in the expected format" in result.failure()
    assert "line 2: 7 cells, expected 6. Remove the extra comma(s) at the end of the line." in result.failure()
    assert agent.CSVParser.CSV_file is None
    assert agent.node_map == {} and agent.map_rotation_order == [] and agent.pathGraph is None


def test_after_bad_file_picking_a_character_waits_for_a_map(tmp_path):
    agent = MapleAgent()
    assert not is_successful(agent.loadMap(_bad_map(tmp_path)))
    assert agent.setCharacterClass(Kanna()) == Success(BuildOutcome.PENDING_CSV)


def _gui(monkeypatch, path: Path, agent: MapleAgent):
    shown, console = [], []
    monkeypatch.setattr(gui_module.filedialog, "askopenfilename", lambda **kwargs: str(path))
    monkeypatch.setattr(gui_module.messagebox, "showerror",
                        lambda title, message, **kwargs: shown.append((title, message)))
    fake = SimpleNamespace(gui=None, controller=SimpleNamespace(loadMap=agent.loadMap),
                           _printToGUIConsole=console.append)
    gui_module.MapleGUI._loadMapBrowse(fake)
    return shown, console


def test_gui_shows_bad_file_message_in_a_message_box(monkeypatch, tmp_path):
    path = _bad_map(tmp_path)
    agent = MapleAgent()
    shown, console = _gui(monkeypatch, path, agent)

    message = (f"The map file {path} is not in the expected format, so it was not loaded. Fix the problem below in a "
               "text editor, then load it again.\n\n"
               "line 2: 7 cells, expected 6. Remove the extra comma(s) at the end of the line.")
    assert shown == [("Could not load map", message)]
    assert console == [f"Failed to load map {path}: {message}"]
    assert agent.CSVParser.CSV_file is None


def test_gui_good_file_shows_no_message_box(monkeypatch):
    shown, console = _gui(monkeypatch, FIXTURE, MapleAgent(characterClass=Kanna()))
    assert shown == []
    assert console == [f"Loaded map: {FIXTURE}; graph built."]
