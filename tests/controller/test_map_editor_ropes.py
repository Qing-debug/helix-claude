"""Ropes in the map editor, part 3a (H91, H94; plan Task 3a): the rope state and its dirty baseline, the rope file's load
and save, the keep-out (the click refusal, the red rings, the Save block) and the drawing.

These build a real `MapEditor` on a real Tk root (withdrawn, never shown, no mainloop), so they are skipped where Tk cannot
open a display. `tk.Misc.after` only records, so nothing is re-run on its own (part 3b steps its frame collection by calling
the recorded callbacks by hand); `messagebox` and `filedialog` are patched. Part 3a changes `state.ropes` directly and then
follows the contract's one rule: `_recomputeDirty()`, then `_renderNodes()` (`set_ropes`)."""
import threading
import tkinter as tk
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from returns.result import Failure

import src.gui.map_editor as map_editor
from src.controller.controller import Controller
from src.controller.map_csv import EditorNode, read_editor_nodes, write_editor_nodes
from src.controller.rope_file import RopeFile, RopeMark, read_ropes, rope_file_for, write_ropes

FRAME_H, FRAME_W = 82, 162  # Fossil Foundry 2's minimap, native px
BACKGROUND = (38, 66, 95)
NOT_LOADED_TEXT = "The map's nodes were loaded without its ropes. Mark the ropes again and save to replace the file."
DAMAGED_QUESTION = "This map's rope file is damaged and no ropes are marked. Save with no ropes and remove the rope file?"
ROPE_107 = RopeMark(107, 43, 61)
ROPE_46 = RopeMark(46, 28, 46)


def _open_root():
    """A withdrawn Tk root. On the dev PC, Tk now and then fails to read its own init files for a moment, so try a few
    times before skipping the test (tests/controller/test_gui_startup.py does the same)."""
    for attempt in range(4):
        try:
            root = tk.Tk()
        except tk.TclError as err:
            if attempt == 3:
                pytest.skip(f"Tk cannot open a display here: {err}")
            threading.Event().wait(0.3)
        else:
            root.withdraw()
            return root
    raise AssertionError("unreachable")


@pytest.fixture
def make_editor(monkeypatch):
    """`make()` -> a rig (a second `make()` in one test is a second editor window on the same root): `.editor`, `.dialogs` (what the patched boxes were shown and what they answer), `.scheduled`
    (the recorded `after` calls). Every box is patched: errors and infos are recorded, `askyesno` (the damaged-rope-file
    question) answers `dialogs.yes_answer`, `askyesnocancel` (the unsaved-changes box) answers Discard, `askokcancel`
    (the save warnings) answers OK; the file dialogs return `dialogs.open_path` and `dialogs.save_path`."""
    roots = []  # one root per test: a second Tk root would break the first one's PhotoImages (the default root)
    dialogs = SimpleNamespace(errors=[], infos=[], questions=[], yes_answer=True, open_path="", save_path="")
    scheduled = []

    monkeypatch.setattr(tk.Misc, "after", lambda self, ms, func=None, *args: scheduled.append((ms, func)))
    monkeypatch.setattr(map_editor.messagebox, "showerror", lambda title, message, **kw: dialogs.errors.append((title, message)))
    monkeypatch.setattr(map_editor.messagebox, "showinfo", lambda title, message, **kw: dialogs.infos.append((title, message)))
    monkeypatch.setattr(map_editor.messagebox, "askokcancel", lambda title, message, **kw: True)
    monkeypatch.setattr(map_editor.messagebox, "askyesnocancel", lambda title, message, **kw: False)

    def ask_yes_no(title, message, **kw):
        dialogs.questions.append((title, message))
        return dialogs.yes_answer

    monkeypatch.setattr(map_editor.messagebox, "askyesno", ask_yes_no)
    monkeypatch.setattr(map_editor.filedialog, "askopenfilename", lambda **kw: dialogs.open_path)
    monkeypatch.setattr(map_editor.filedialog, "asksaveasfilename", lambda **kw: dialogs.save_path)

    def make():
        shared = SimpleNamespace(
            minimap_frame_lock=threading.Lock(),
            minimap_frame=np.full((FRAME_H, FRAME_W, 3), BACKGROUND, dtype=np.uint8),
        )
        if not roots:
            roots.append(_open_root())
        root = roots[0]
        editor = map_editor.MapEditor(root, Controller(sharedInfo=shared), shared)
        return SimpleNamespace(editor=editor, dialogs=dialogs, scheduled=scheduled)

    yield make
    for root in roots:
        root.destroy()  # destroys every editor window made on it


def click(editor, x, y):
    """A left click at native minimap (x, y): the canvas is drawn at RENDER_SCALE."""
    editor._onCanvasClick(SimpleNamespace(x=x * map_editor.RENDER_SCALE, y=y * map_editor.RENDER_SCALE))


def set_ropes(editor, ropes, selected=None):
    """The contract's one rule: after a change to `state.ropes` or `state.selected_rope`, recompute dirty, then render."""
    editor.state.ropes = list(ropes)
    editor.state.selected_rope = selected
    editor._recomputeDirty()
    editor._renderNodes()


def place_blue(rig, x, y):
    rig.editor._placement_var.set("blue_node")
    rig.editor._onPlacementChange()
    click(rig.editor, x, y)


def write_map(path: Path, positions=((20, 20),)) -> Path:
    """A good map CSV of blue nodes (as the editor writes it, with the minimap size), no rope file."""
    nodes = {i: EditorNode(i, "blue_node", pos) for i, pos in enumerate(positions, start=1)}
    assert write_editor_nodes(str(path), nodes, list(nodes), minimap_size=(FRAME_W, FRAME_H)).unwrap() is None
    return path


def load(rig, path: Path):
    rig.dialogs.open_path = str(path)
    rig.editor._load()


def ring_count(editor) -> int:
    return len(editor.minimap_canvas.find_withtag(map_editor.CANVAS_TAG_KEEPOUT))


def not_placed(x, y, rope_x, left, right) -> str:
    return (f"Not placed: ({x}, {y}) is too close to the rope at x {rope_x}, where it meets a platform. "
            f"Place it at x {left} or less, or {right} or more.")


# -- the keep-out: the click refusal ---------------------------------------------------------------------------------

@pytest.mark.parametrize("rope, refused, placed, left, right", [
    (ROPE_107, [(101, 43), (111, 43), (103, 60)], [(100, 43), (112, 43), (113, 60)], 100, 112),
    (ROPE_46, [(40, 28), (50, 28)], [(39, 28), (51, 28)], 39, 51),
])
def test_click_near_a_rope_end_places_nothing(make_editor, rope, refused, placed, left, right):
    rig = make_editor()
    editor = rig.editor
    set_ropes(editor, [rope])

    # every refused column is clicked before its free neighbour is placed: a node 1 px away would catch the click
    # by the node hit-test (SELECT_RADIUS_PX 3)
    for x, y in refused:
        click(editor, x, y)
        assert editor.state.nodes == {}
        assert editor._status_label.cget("text") == not_placed(x, y, rope.x, left, right)

    for x, y in placed:
        click(editor, x, y)
    assert sorted(node.pos for node in editor.state.nodes.values()) == sorted(placed)
    assert editor._status_label.cget("text") == ""  # a placed node clears the old refusal text
    assert ring_count(editor) == 0


# -- the keep-out: red rings and the Save block ------------------------------------------------------------------------

def test_nodes_near_a_rope_get_a_red_ring_and_block_save(make_editor, tmp_path):
    rig = make_editor()
    editor = rig.editor
    load(rig, write_map(tmp_path / "m.csv", [(105, 42), (20, 20)]))
    assert ring_count(editor) == 0 and editor._validateForSave() == ("ok", "")

    set_ropes(editor, [ROPE_107])  # added after the node was there

    assert ring_count(editor) == 1
    verdict, message = editor._validateForSave()
    assert verdict == "block"
    assert message.split("\n") == [
        "Cannot save: these nodes are too close to a rope where it meets a platform:",
        "#1 (105, 42): rope at x 107; place it at x 100 or less, or 112 or more",
    ]
    editor._save()  # the Save button: the box is shown and nothing is written
    assert rig.dialogs.errors[-1][0] == "Cannot save" and "#1 (105, 42)" in rig.dialogs.errors[-1][1]
    assert not rope_file_for(tmp_path / "m.csv").exists()

    set_ropes(editor, [])
    assert editor._validateForSave() == ("ok", "")
    assert ring_count(editor) == 0


def test_the_block_comes_after_the_empty_rotation_block_and_before_the_warnings(make_editor):
    rig = make_editor()
    editor = rig.editor
    set_ropes(editor, [ROPE_107])
    editor.state.nodes[1] = EditorNode(1, "orange_node", (105, 42))
    editor.state.nodes[2] = EditorNode(2, "orange_node", (105, 42))  # a duplicate position: a warning on its own

    assert editor._validateForSave()[1].startswith("Cannot save: at least one blue_node is required.")

    editor.state.nodes[3] = EditorNode(3, "blue_node", (20, 20))
    editor.state.rotation_order = [3]
    verdict, message = editor._validateForSave()
    assert verdict == "block" and "Duplicate" not in message and "#1 (105, 42)" in message and "#2 (105, 42)" in message


# -- the drawing ---------------------------------------------------------------------------------------------------------

def test_ropes_are_yellow_lines_the_selected_one_thicker(make_editor):
    rig = make_editor()
    editor = rig.editor
    set_ropes(editor, [ROPE_107, ROPE_46], selected=1)

    canvas = editor.minimap_canvas
    lines = {tuple(canvas.coords(item)): item for item in canvas.find_withtag(map_editor.CANVAS_TAG_ROPE)}
    assert set(lines) == {(214.0, 86.0, 214.0, 122.0), (92.0, 56.0, 92.0, 92.0)}
    assert all(canvas.itemcget(item, "fill") == "yellow" for item in lines.values())
    assert float(canvas.itemcget(lines[(214.0, 86.0, 214.0, 122.0)], "width")) == map_editor.ROPE_LINE_WIDTH
    assert float(canvas.itemcget(lines[(92.0, 56.0, 92.0, 92.0)], "width")) == map_editor.ROPE_SELECTED_LINE_WIDTH

    set_ropes(editor, [ROPE_107])  # a redraw replaces the lines, it does not pile them up
    assert len(canvas.find_withtag(map_editor.CANVAS_TAG_ROPE)) == 1


# -- the dirty baseline --------------------------------------------------------------------------------------------------

def test_rope_changes_make_the_editor_dirty(make_editor):
    rig = make_editor()
    editor = rig.editor
    assert not editor.state.dirty

    set_ropes(editor, [ROPE_107])
    assert editor.state.dirty and editor.window.title().startswith("* ")

    set_ropes(editor, [])
    assert not editor.state.dirty and not editor.window.title().startswith("* ")


# -- the rope file: save and load ----------------------------------------------------------------------------------------

def test_save_writes_both_files_and_load_reads_them(make_editor, tmp_path):
    rig = make_editor()
    place_blue(rig, 20, 20)
    set_ropes(rig.editor, [ROPE_107, ROPE_46])
    rig.dialogs.save_path = str(tmp_path / "m.csv")
    rig.editor._save()

    assert not rig.editor.state.dirty and rig.editor.state.rope_file_read
    saved = read_ropes(tmp_path / "m.ropes.json").unwrap()
    assert saved == RopeFile((ROPE_107, ROPE_46), (FRAME_W, FRAME_H), {})

    # the CSV is byte-identical to a save of the same nodes without ropes
    plain = make_editor()
    place_blue(plain, 20, 20)
    plain.dialogs.save_path = str(tmp_path / "plain.csv")
    plain.editor._save()
    assert (tmp_path / "m.csv").read_bytes() == (tmp_path / "plain.csv").read_bytes()

    reopened = make_editor()
    load(reopened, tmp_path / "m.csv")
    assert reopened.editor.state.ropes == [ROPE_107, ROPE_46]
    assert reopened.editor.state.rope_file_read and not reopened.editor.state.dirty
    assert reopened.dialogs.errors == []


def test_map_without_ropes_saves_one_file(make_editor, tmp_path):
    # a fresh editor with no ropes
    fresh = make_editor()
    place_blue(fresh, 20, 20)
    fresh.dialogs.save_path = str(tmp_path / "m.csv")
    fresh.editor._save()
    assert (tmp_path / "m.csv").exists() and not (tmp_path / "m.ropes.json").exists()
    assert not fresh.editor.state.rope_file_read

    # a good CSV with no rope file: a missing file reads as Success with no ropes and must not set the flag
    loaded = make_editor()
    load(loaded, write_map(tmp_path / "n.csv"))
    assert not loaded.editor.state.rope_file_read
    loaded.editor._save()  # to the same path
    assert not (tmp_path / "n.ropes.json").exists()
    loaded.dialogs.save_path = str(tmp_path / "n2.csv")
    loaded.editor._saveAs()  # to a new one
    assert (tmp_path / "n2.csv").exists() and not (tmp_path / "n2.ropes.json").exists()

    # map A has a rope file, map B has none: B must not inherit A's flag (or A's unknown keys)
    write_ropes(rope_file_for(write_map(tmp_path / "a.csv")), RopeFile((ROPE_107,), None, {"platforms": [1]}))
    sequence = make_editor()
    load(sequence, tmp_path / "a.csv")
    assert sequence.editor.state.rope_file_read and sequence.editor.state.ropes == [ROPE_107]
    load(sequence, write_map(tmp_path / "b.csv"))
    assert not sequence.editor.state.rope_file_read and sequence.editor.state.ropes == []
    assert sequence.editor.state.rope_extra == {}
    sequence.editor._save()
    assert (tmp_path / "b.csv").exists() and not (tmp_path / "b.ropes.json").exists()


def test_load_clears_the_selected_rope(make_editor, tmp_path):
    write_ropes(rope_file_for(write_map(tmp_path / "one.csv")), RopeFile((ROPE_46,), None, {}))
    write_map(tmp_path / "none.csv")
    for name, expected in (("one.csv", [ROPE_46]), ("none.csv", [])):
        rig = make_editor()
        editor = rig.editor
        set_ropes(editor, [ROPE_107, ROPE_46], selected=1)  # rope 2 of two; the editor is dirty, so the box asks (Discard)
        load(rig, tmp_path / name)
        assert editor.state.selected_rope is None
        assert editor.state.ropes == expected
        assert len(editor.minimap_canvas.find_withtag(map_editor.CANVAS_TAG_ROPE)) == len(expected)


def test_deleting_every_rope_of_a_good_file_saves_an_empty_one(make_editor, tmp_path):
    csv_path = write_map(tmp_path / "m.csv")
    write_ropes(rope_file_for(csv_path), RopeFile((ROPE_107, ROPE_46), (FRAME_W, FRAME_H), {"platforms": [1]}))
    rig = make_editor()
    load(rig, csv_path)
    set_ropes(rig.editor, [])
    assert rig.editor.state.dirty

    rig.editor._save()

    saved = read_ropes(rope_file_for(csv_path)).unwrap()
    assert saved.ropes == () and saved.extra == {"platforms": [1]}
    assert not rig.editor.state.dirty and rig.dialogs.questions == []


def test_unknown_rope_keys_survive_a_save(make_editor, tmp_path):
    csv_path = write_map(tmp_path / "m.csv")
    write_ropes(rope_file_for(csv_path), RopeFile((ROPE_107,), (FRAME_W, FRAME_H), {"platforms": [1]}))
    rig = make_editor()
    load(rig, csv_path)
    assert rig.editor.state.rope_extra == {"platforms": [1]}

    set_ropes(rig.editor, [ROPE_107, ROPE_46])
    rig.editor._save()

    saved = read_ropes(rope_file_for(csv_path)).unwrap()
    assert saved.ropes == (ROPE_107, ROPE_46) and saved.extra == {"platforms": [1]}


def test_a_failed_rope_file_write_says_so_and_keeps_the_title_dirty(make_editor, tmp_path, monkeypatch):
    rig = make_editor()
    place_blue(rig, 20, 20)
    set_ropes(rig.editor, [ROPE_107])
    monkeypatch.setattr(map_editor, "write_ropes", lambda path, file: Failure(f"{path} could not be written: disk full."))
    rig.dialogs.save_path = str(tmp_path / "m.csv")

    rig.editor._save()

    assert (tmp_path / "m.csv").exists()  # the CSV is already written
    title, message = rig.dialogs.errors[-1]
    assert title == "Save failed" and str(tmp_path / "m.ropes.json") in message and "disk full" in message
    assert rig.editor.state.dirty and rig.editor.window.title().startswith("* ")
    assert rig.dialogs.infos == [] and not rig.editor.state.rope_file_read


# -- a damaged rope file (ruling ROPE148R-D1 = A, and Hong's QB236) -----------------------------------------------------

def damaged_map(tmp_path: Path):
    csv_path = write_map(tmp_path / "m.csv")
    rope_path = rope_file_for(csv_path)
    rope_path.write_bytes(b"this is not json")
    return csv_path, rope_path


def test_bad_rope_file_opens_the_nodes_only(make_editor, tmp_path):
    csv_path, rope_path = damaged_map(tmp_path)
    rig = make_editor()

    load(rig, csv_path)

    state = rig.editor.state
    assert len(state.nodes) == 1 and state.ropes == [] and state.rope_extra == {} and not state.rope_file_read
    assert not state.dirty
    (title, message), = rig.dialogs.errors
    assert title == "Rope file not loaded" and title != "Could not load map"
    assert read_ropes(rope_path).failure() in message and NOT_LOADED_TEXT in message
    assert "Your current editor state has not been changed." not in message
    assert rope_path.read_bytes() == b"this is not json"


def test_saving_with_no_ropes_asks_and_no_leaves_the_damaged_file(make_editor, tmp_path):
    csv_path, rope_path = damaged_map(tmp_path)
    rig = make_editor()
    load(rig, csv_path)
    rig.dialogs.yes_answer = False
    click(rig.editor, 90, 20)  # a change the CSV must carry
    assert rig.editor.state.dirty

    rig.editor._save()

    assert rig.dialogs.questions == [("Rope file damaged", DAMAGED_QUESTION)]
    assert rope_path.read_bytes() == b"this is not json"  # No: the file stays (the walk still refuses it)
    assert len(read_editor_nodes(str(csv_path)).unwrap()[0]) == 2  # but the map is saved
    assert not rig.editor.state.dirty and not rig.editor.state.rope_file_read
    assert len(rig.dialogs.errors) == 1  # only the load's box


def test_saving_with_no_ropes_asks_and_yes_removes_only_that_rope_file(make_editor, tmp_path):
    csv_path, rope_path = damaged_map(tmp_path)
    neighbour = write_ropes(rope_file_for(write_map(tmp_path / "other.csv")), RopeFile((ROPE_46,), None, {}))
    assert neighbour.unwrap() is None
    rig = make_editor()
    load(rig, csv_path)
    rig.dialogs.yes_answer = True
    click(rig.editor, 90, 20)

    rig.editor._save()

    assert rig.dialogs.questions == [("Rope file damaged", DAMAGED_QUESTION)]
    assert not rope_path.exists()  # Yes: the map now works with no ropes
    assert (tmp_path / "other.ropes.json").exists() and (tmp_path / "other.csv").exists()
    assert len(read_editor_nodes(str(csv_path)).unwrap()[0]) == 2
    assert not rig.editor.state.dirty and not rig.editor.state.rope_file_read
    assert read_ropes(rope_path).unwrap().ropes == ()

    errors_before = len(rig.dialogs.errors)
    again = make_editor()  # the map now opens with no box at all
    load(again, csv_path)
    assert len(again.dialogs.errors) == errors_before and again.editor.state.ropes == []
    rig.editor._save()  # and saving it again asks nothing and writes no rope file
    assert len(rig.dialogs.questions) == 1 and not rope_path.exists()


def test_saving_with_a_rope_replaces_the_damaged_file_without_asking(make_editor, tmp_path):
    csv_path, rope_path = damaged_map(tmp_path)
    rig = make_editor()
    load(rig, csv_path)
    set_ropes(rig.editor, [ROPE_107])

    rig.editor._save()

    assert rig.dialogs.questions == []
    assert read_ropes(rope_path).unwrap() == RopeFile((ROPE_107,), (FRAME_W, FRAME_H), {})
    assert rig.editor.state.rope_file_read and not rig.editor.state.dirty


def test_save_as_elsewhere_asks_nothing_and_leaves_the_damaged_file(make_editor, tmp_path):
    csv_path, rope_path = damaged_map(tmp_path)
    rig = make_editor()
    load(rig, csv_path)
    rig.dialogs.save_path = str(tmp_path / "copy.csv")

    rig.editor._saveAs()  # the rope file that counts is the one beside the file being written

    assert rig.dialogs.questions == []
    assert (tmp_path / "copy.csv").exists() and not (tmp_path / "copy.ropes.json").exists()
    assert rope_path.read_bytes() == b"this is not json"
