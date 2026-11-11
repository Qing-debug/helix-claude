"""Ropes in the map editor, parts 3a and 3b (H91, H92, H94; plan Task 3). Part 3a: the rope state and its dirty baseline,
the rope file's load and save, the keep-out (the click refusal, the red rings, the Save block) and the drawing. Part 3b
(at the end): the Ropes panel (Find ropes, Add rope, select, edit, delete) and the Delete key.

These build a real `MapEditor` on a real Tk root (withdrawn, never shown, no mainloop), so they are skipped where Tk cannot
open a display. `tk.Misc.after` only records, so nothing is re-run on its own (part 3b steps its frame collection by calling
the recorded callbacks by hand); `messagebox` and `filedialog` are patched. Part 3a changes `state.ropes` directly and then
follows the contract's one rule: `_recomputeDirty()`, then `_renderNodes()` (`set_ropes`)."""
import json
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
from src.controller.rope_finder import find_ropes

FRAME_H, FRAME_W = 82, 162  # Fossil Foundry 2's minimap, native px
BACKGROUND = (38, 66, 95)
NOT_LOADED_TEXT = "The map's nodes were loaded without its ropes. Mark the ropes again and save to replace the file."
DAMAGED_QUESTION = "This map's rope file is damaged and no ropes are marked. Save with no ropes and remove the rope file?"
ROPE_107 = RopeMark(107, 43, 61)
ROPE_46 = RopeMark(46, 28, 46)
ROPE_COLOUR = (38, 58, 73)
PLATFORM_COLOUR = (238, 255, 255)
PLAIN = object()  # make(frame=PLAIN): the plain background frame; make(None): no minimap frame yet


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

    monkeypatch.setattr(tk.Misc, "after", lambda self, ms, func=None, *args: scheduled.append((ms, func, args)))
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

    def make(frame=PLAIN):
        shared = SimpleNamespace(
            minimap_frame_lock=threading.Lock(),
            minimap_frame=np.full((FRAME_H, FRAME_W, 3), BACKGROUND, dtype=np.uint8) if frame is PLAIN else frame,
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
    reader_message = read_ropes(rope_path).failure()

    rig.editor._save()

    assert rig.dialogs.questions == [("Rope file damaged", f"{DAMAGED_QUESTION}\n\n{reader_message}")]
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
    reader_message = read_ropes(rope_path).failure()

    rig.editor._save()

    assert rig.dialogs.questions == [("Rope file damaged", f"{DAMAGED_QUESTION}\n\n{reader_message}")]
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


def test_the_damaged_question_says_why_the_reader_failed(make_editor, tmp_path):
    # ROPE152-R3a M1: a rope file from a newer Helix is not "damaged", so the question shows the reader's own message
    csv_path = write_map(tmp_path / "m.csv")
    rope_path = rope_file_for(csv_path)
    rope_path.write_text(json.dumps({"format": "helix-ropes", "version": 2, "ropes": []}), encoding="utf-8")
    rig = make_editor()
    load(rig, csv_path)
    rig.dialogs.yes_answer = False
    click(rig.editor, 90, 20)

    rig.editor._save()

    (title, message), = rig.dialogs.questions
    assert title == "Rope file damaged"
    assert message.startswith(DAMAGED_QUESTION + "\n\n") and message.endswith(read_ropes(rope_path).failure())
    assert "made by a newer Helix" in message and "update Helix" in message
    assert rope_path.exists()


def test_a_failed_csv_write_after_yes_leaves_the_rope_file(make_editor, tmp_path, monkeypatch):
    csv_path, rope_path = damaged_map(tmp_path)
    rig = make_editor()
    load(rig, csv_path)
    rig.dialogs.yes_answer = True
    click(rig.editor, 90, 20)
    monkeypatch.setattr(map_editor, "write_editor_nodes", lambda *args, **kwargs: Failure("disk full"))

    rig.editor._save()

    assert len(rig.dialogs.questions) == 1  # asked before anything was written ...
    assert rope_path.read_bytes() == b"this is not json"  # ... and Yes removes nothing when the CSV was not written
    title, message = rig.dialogs.errors[-1]
    assert title == "Save failed" and "disk full" in message
    assert rig.editor.state.dirty and rig.dialogs.infos == []


def test_a_failed_rope_file_delete_says_so_and_keeps_the_title_dirty(make_editor, tmp_path, monkeypatch):
    csv_path, rope_path = damaged_map(tmp_path)
    rig = make_editor()
    load(rig, csv_path)
    rig.dialogs.yes_answer = True
    click(rig.editor, 90, 20)

    def refuse(self, missing_ok=False):
        raise PermissionError(13, "Permission denied", str(self))

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", refuse)
        rig.editor._save()

    assert len(read_editor_nodes(str(csv_path)).unwrap()[0]) == 2  # the CSV is already written
    assert rope_path.read_bytes() == b"this is not json"
    title, message = rig.dialogs.errors[-1]
    assert title == "Save failed" and str(rope_path) in message and "Permission denied" in message
    assert rig.editor.state.dirty and rig.editor.window.title().startswith("* ")
    assert rig.dialogs.infos == [] and not rig.editor.state.rope_file_read


# -- part 3b: the Ropes panel (H92, D3) -------------------------------------------------------------------------------------

def rope_frame(*, rope_46=True, rope_107=True) -> np.ndarray:
    """A synthetic Fossil Foundry 2 minimap: platform rows 31 and 49 (left), 46 (right), 64 (floor); rope 46 runs between
    rows 31 and 49 and rope 107 between rows 46 and 64, so the finder gives RopeMark(46, 28, 46) and RopeMark(107, 43, 61)."""
    frame = np.full((FRAME_H, FRAME_W, 3), BACKGROUND, dtype=np.uint8)
    for row, x0, x1 in ((31, 20, 80), (49, 20, 80), (46, 85, 140), (64, 0, FRAME_W)):
        frame[row, x0:x1] = PLATFORM_COLOUR
    if rope_46:
        frame[32:49, 46] = ROPE_COLOUR
    if rope_107:
        frame[47:64, 107] = ROPE_COLOUR
    return frame


def run_scan(rig, limit=None) -> int:
    """Run the recorded Find-ropes ticks by hand, oldest first, until none is pending (or `limit` have run), each with the
    arguments it was scheduled with. Returns how many ran."""
    ran = 0
    while limit is None or ran < limit:
        pending = [item for item in rig.scheduled if item[1] == rig.editor._onRopeScanTick]
        if not pending:
            break
        rig.scheduled.remove(pending[0])
        assert pending[0][0] == map_editor.ROPE_SCAN_EVERY_MS
        pending[0][1](*pending[0][2])
        ran += 1
    return ran


def status(editor) -> str:
    return editor._status_label.cget("text")


def rope_lines(editor) -> int:
    return len(editor.minimap_canvas.find_withtag(map_editor.CANVAS_TAG_ROPE))


def box_values(editor) -> list:
    return [editor._rope_vars[name].get() for name in ("x", "top_y", "bottom_y")]


def box_states(editor) -> set:
    return {str(box.cget("state")) for box in editor._rope_boxes.values()}


def type_in_box(editor, name, text):
    """What the user does in a number box: type a value and press Return."""
    editor._rope_vars[name].set(text)
    editor._applyRopeBox(name)


def assert_delete_chain(editor, widget):
    """What Tk runs for a <Delete> typed in `widget`, checked without sending a key: the window's binding is `_onDeleteKey`,
    the window's bindtag comes after the widget's own and its class's (so the widget edits its text first), and a text
    box's class binding does not `break` (a `break` would stop the window's binding, and the Delete key with it)."""
    window = editor.window
    assert "_onDeleteKey" in window.bind("<Delete>")
    tags = list(widget.bindtags())
    assert tags[0] == str(widget) and tags.index(widget.winfo_class()) < tags.index(str(window))
    if isinstance(widget, tk.Spinbox):
        assert widget.bind_class("Spinbox", "<Delete>") and "break" not in widget.bind_class("Spinbox", "<Delete>").split()


def press_delete(editor, widget=None):
    """A <Delete> key for `widget` (by default the map canvas, which a click on the map gives the keyboard), through the
    widget's own bindings and then the window's (`_onDeleteKey`).

    Never `focus_force`: that asks Windows for the foreground (Tk's `focus -force` ends in `SetForegroundWindow`), and a
    test run must not take the keyboard from the game or a walk (the board's keys go to the window in front; ROPE152-R3b
    I1). `focus_set` asks Windows for nothing. Tk hands a generated key event to the display's focus window and drops it
    when the application has none, and inside one pytest process only the first Tk window the process opens gets the
    keyboard from Windows (measured: later windows have an empty `focus`). So: when Tk's focus is the widget after
    `focus_set`, send the real key event. Otherwise check the chain Tk would run (`assert_delete_chain`) and run both its
    ends by hand: a text box's own Delete (it removes the character after the cursor) and `_onDeleteKey` with the box as
    `event.widget`. Returns "key" or "call", for the test that wants to know which."""
    window = editor.window
    window.update()  # maps the window first: measured, a canvas click's `focus_set` before the first update left `focus -lastfor` at the window
    widget = widget if widget is not None else editor.minimap_canvas
    widget.focus_set()
    window.update()
    if str(window.tk.call("focus")) == str(widget):
        widget.event_generate("<Delete>")
        return "key"
    assert_delete_chain(editor, widget)
    if isinstance(widget, tk.Spinbox):
        widget.delete(widget.index("insert"))  # the Spinbox class binding; the tests leave no selection in a box
    editor._onDeleteKey(SimpleNamespace(widget=widget))
    return "call"


def test_the_delete_binding_runs_after_a_boxs_own_and_is_not_stopped(make_editor):
    editor = make_editor().editor
    for widget in (*editor._rope_boxes.values(), editor.minimap_canvas):
        assert_delete_chain(editor, widget)


def test_find_ropes_adds_the_ropes_in_the_frames(make_editor):
    rig = make_editor(rope_frame())
    editor = rig.editor
    assert map_editor.ROPE_SCAN_FRAMES == 30 and map_editor.ROPE_SCAN_EVERY_MS == 100

    editor._findRopes()
    assert status(editor) == "Finding ropes: keep the character moving for 3 seconds..."
    editor._findRopes()  # a press while collecting does nothing
    assert len([1 for item in rig.scheduled if item[1] == editor._onRopeScanTick]) == 1

    assert run_scan(rig) == 30
    assert editor.state.ropes == [ROPE_46, ROPE_107]
    assert status(editor) == "Found 2 ropes"
    assert editor.state.dirty and rope_lines(editor) == 2

    editor._findRopes()  # a second search finds the same ropes and adds nothing
    run_scan(rig)
    assert editor.state.ropes == [ROPE_46, ROPE_107] and rope_lines(editor) == 2

    # a found rope within 2 px of a mark is skipped; one farther away is added
    set_ropes(editor, [RopeMark(108, 43, 61)])
    editor._findRopes()
    run_scan(rig)
    assert editor.state.ropes == [RopeMark(108, 43, 61), ROPE_46]

    one = make_editor(rope_frame(rope_46=False))
    one.editor._findRopes()
    run_scan(one)
    assert status(one.editor) == "Found 1 rope" and one.editor.state.ropes == [ROPE_107]
    none = make_editor()
    none.editor._findRopes()
    run_scan(none)
    assert status(none.editor) == "Found no rope" and none.editor.state.ropes == [] and not none.editor.state.dirty


def test_find_ropes_with_no_frames_says_why(make_editor):
    rig = make_editor(rope_frame())
    rig.editor._findRopes()
    rig.editor.sharedInfo.minimap_frame = None  # the vision thread lost the minimap for the whole collection

    run_scan(rig)

    assert "no minimap frames were collected" in status(rig.editor) and rig.editor.state.ropes == []


class RecordingLock:
    """Stands in for minimap_frame_lock: counts the times it was taken and says whether it is held now."""

    def __init__(self):
        self.held = False
        self.taken = 0

    def __enter__(self):
        assert not self.held
        self.held = True
        self.taken += 1

    def __exit__(self, *exc):
        self.held = False


class WhileUnlocked:
    """Wraps a Tk object: using any of it while the lock is held fails the test."""

    def __init__(self, inner, lock):
        self._inner, self._lock = inner, lock

    def __getattr__(self, name):
        assert not self._lock.held, f"{name} was used while minimap_frame_lock was held"
        return getattr(self._inner, name)


def test_find_ropes_holds_the_lock_only_to_copy(make_editor):
    rig = make_editor(rope_frame())
    editor = rig.editor
    lock = RecordingLock()
    editor.sharedInfo.minimap_frame_lock = lock
    editor.window = WhileUnlocked(editor.window, lock)  # after, winfo_exists, title: no Tk call under the lock
    editor.minimap_canvas = WhileUnlocked(editor.minimap_canvas, lock)
    editor._status_label = WhileUnlocked(editor._status_label, lock)

    editor._findRopes()
    assert run_scan(rig) == 30

    assert lock.taken == 30 and not lock.held
    assert editor.state.ropes == [ROPE_46, ROPE_107]


def test_find_ropes_before_the_minimap(make_editor):
    rig = make_editor(None)  # no minimap frame yet, so no canvas
    editor = rig.editor
    assert editor.minimap_canvas is None
    scheduled = list(rig.scheduled)

    editor._findRopes()

    assert status(editor) == "Minimap not found yet"
    assert rig.scheduled == scheduled and editor.state.ropes == []
    editor._startAddRope()  # nothing to click on either
    assert status(editor) == "Minimap not found yet" and not editor.state.add_rope_armed


def test_add_rope_clicks_are_where_nodes_sit(make_editor):
    rig = make_editor(rope_frame(rope_46=False))
    editor = rig.editor

    editor._startAddRope()
    assert status(editor) == "Add rope: click the rope's top end, where a node on the platform above would sit."
    editor._onCanvasClick(SimpleNamespace(x=215, y=87))  # screen px: native (215 // 2, 87 // 2) = (107, 43), as a node click
    assert status(editor) == "Now click its bottom end, where a node on the platform below would sit."
    assert editor.state.ropes == [] and editor.state.nodes == {}
    click(editor, 107, 61)

    assert editor.state.ropes == [RopeMark(107, 43, 61)]
    assert editor.state.nodes == {}  # an armed Add rope places no node
    assert editor.state.ropes == find_ropes(rope_frame(rope_46=False))  # the finder gives the same mark for the same rope


def test_add_move_delete_rope(make_editor, tmp_path):
    rig = make_editor()
    editor = rig.editor
    editor._startAddRope()
    click(editor, 60, 50)
    click(editor, 64, 50)  # the same height: refused, the first end is kept
    assert editor.state.ropes == [] and editor.state.adding_rope == (60, 50) and status(editor).startswith("Not added")
    click(editor, 64, 30)  # the second end is higher: x comes from the first click, the ends are ordered
    assert editor.state.ropes == [RopeMark(60, 30, 50)]
    assert editor.state.selected_rope == 0 and editor.state.adding_rope is None and not editor.state.add_rope_armed
    assert editor.state.dirty and rope_lines(editor) == 1
    assert box_values(editor) == ["60", "30", "50"] and box_states(editor) == {"normal"}

    type_in_box(editor, "x", "70")
    type_in_box(editor, "top_y", "20")
    type_in_box(editor, "bottom_y", "45")
    assert editor.state.ropes == [RopeMark(70, 20, 45)]
    for name, text, reason in (
            ("top_y", "45", "Not changed: top y (45) must be less than bottom y (45)."),
            ("bottom_y", "20", "Not changed: top y (20) must be less than bottom y (20)."),
            ("x", "abc", "Not changed: x must be a whole number, 0 or more."),
            ("x", "-3", "Not changed: x must be a whole number, 0 or more."),
            ("x", "", "Not changed: x must be a whole number, 0 or more.")):
        type_in_box(editor, name, text)
        assert status(editor) == reason
        assert editor.state.ropes == [RopeMark(70, 20, 45)]
        assert editor._rope_vars[name].get() == str(getattr(editor.state.ropes[0], name))  # the box went back

    editor._deleteSelectedRope()  # the panel's Delete rope
    assert editor.state.ropes == [] and editor.state.selected_rope is None
    assert not editor.state.dirty and rope_lines(editor) == 0  # added, moved and deleted: back to the saved state
    assert box_values(editor) == ["", "", ""] and box_states(editor) == {"disabled"}

    # moving a mark of a saved map and moving it back is clean again
    csv_path = write_map(tmp_path / "m.csv")
    write_ropes(rope_file_for(csv_path), RopeFile((ROPE_107,), (FRAME_W, FRAME_H), {}))
    load(rig, csv_path)
    click(editor, 107, 52)
    type_in_box(editor, "x", "108")
    assert editor.state.ropes == [RopeMark(108, 43, 61)] and editor.state.dirty
    type_in_box(editor, "x", "107")
    assert editor.state.ropes == [ROPE_107] and not editor.state.dirty


def test_click_on_a_node_next_to_a_rope_selects_the_node(make_editor, tmp_path):
    rig = make_editor()
    editor = rig.editor
    load(rig, write_map(tmp_path / "m.csv", [(105, 60), (48, 45), (20, 20)]))
    set_ropes(editor, [ROPE_107, ROPE_46])  # (105, 60) and (48, 45) are 2 px from a rope's line

    for node_id, pos in ((1, (105, 60)), (2, (48, 45))):
        click(editor, *pos)
        assert editor.state.selected_id == node_id and editor.state.selected_rope is None
        editor._deleteSelected()
        assert node_id not in editor.state.nodes
    assert editor.state.ropes == [ROPE_107, ROPE_46]  # the ropes were not touched

    click(editor, 107, 52)  # on the line, no node within 3 px
    assert editor.state.selected_rope == 0 and editor.state.selected_id is None
    assert box_values(editor) == ["107", "43", "61"]
    click(editor, 20, 20)  # selecting a node clears the rope selection
    assert editor.state.selected_id == 3 and editor.state.selected_rope is None
    assert box_states(editor) == {"disabled"}
    click(editor, 44, 40)  # 2 px from rope 46's line
    assert editor.state.selected_rope == 1 and editor.state.selected_id is None
    click(editor, 90, 10)  # far from both: a new node, and the rope is no longer selected
    assert editor.state.selected_rope is None and len(editor.state.nodes) == 2


def test_delete_key_deletes_the_selected_rope(make_editor, tmp_path):
    csv_path = write_map(tmp_path / "m.csv", [(20, 20)])
    write_ropes(rope_file_for(csv_path), RopeFile((ROPE_107, ROPE_46), (FRAME_W, FRAME_H), {}))
    rig = make_editor()
    editor = rig.editor
    load(rig, csv_path)
    assert not editor.state.dirty

    click(editor, 107, 52)
    press_delete(editor)

    assert editor.state.ropes == [ROPE_46] and editor.state.selected_rope is None
    assert editor.state.dirty and len(editor.state.nodes) == 1 and rope_lines(editor) == 1

    by_key = (list(editor.state.ropes), editor.state.selected_rope, editor.state.dirty)
    load(rig, csv_path)  # the same again with the panel's Delete rope instead of the key (the box asks, the answer is Discard)
    click(editor, 107, 52)
    editor._deleteSelectedRope()
    assert (editor.state.ropes, editor.state.selected_rope, editor.state.dirty) == by_key

    click(editor, 20, 20)  # a node: Delete removes the node, as today, and leaves the ropes
    assert editor.state.selected_id == 1
    press_delete(editor)
    assert editor.state.nodes == {} and editor.state.ropes == [ROPE_46]

    press_delete(editor)  # nothing selected: nothing changes
    assert editor.state.nodes == {} and editor.state.ropes == [ROPE_46]


def test_delete_in_a_number_box_edits_the_text_and_leaves_the_ropes(make_editor):
    # ROPE150RR R-I2: the <Delete> binding is on the window, and Tk runs it after a box's own binding
    rig = make_editor()
    editor = rig.editor
    set_ropes(editor, [ROPE_107, ROPE_46])
    click(editor, 107, 52)
    assert editor.state.selected_rope == 0

    # A real Delete in the x box (cursor before the "1"): the Spinbox's own binding removes the character, then the window's
    # binding runs with event.widget = the box and `_onDeleteKey` leaves the ropes alone.
    box = editor._rope_boxes["x"]
    box.icursor(0)
    press_delete(editor, box)
    assert editor._rope_vars["x"].get() == "07"
    assert editor.state.ropes == [ROPE_107, ROPE_46] and editor.state.selected_rope == 0
    assert rope_lines(editor) == 2

    editor._rope_vars["x"].set("107")  # undo the typing, so leaving the box has no edit to commit
    click(editor, 107, 52)  # a click on the map gives the canvas the keyboard back (Tk's record: `lastfor` holds when the
    assert editor.window.focus_lastfor() is editor.minimap_canvas  # application itself has no keyboard focus here)
    press_delete(editor)
    assert editor.state.ropes == [ROPE_46] and editor.state.selected_rope is None


def test_load_clears_an_armed_add_rope(make_editor, tmp_path):
    rig = make_editor()
    editor = rig.editor
    editor._startAddRope()
    click(editor, 60, 50)
    assert editor.state.adding_rope == (60, 50) and editor.state.add_rope_armed

    load(rig, write_map(tmp_path / "m.csv", [(20, 20)]))
    assert editor.state.adding_rope is None and not editor.state.add_rope_armed

    click(editor, 90, 20)  # a node as usual, not the second end of a rope
    assert len(editor.state.nodes) == 2 and editor.state.ropes == []
    set_ropes(editor, [ROPE_107])
    click(editor, 101, 43)  # and the keep-out still refuses
    assert status(editor).startswith("Not placed:") and len(editor.state.nodes) == 2

    editor._startAddRope()  # armed with no click yet is cleared too
    load(rig, tmp_path / "m.csv")
    assert not editor.state.add_rope_armed
    click(editor, 90, 40)
    assert editor.state.ropes == [] and len(editor.state.nodes) == 2


# -- fix round 1 (ROPE152-R3b I2 and m1) ------------------------------------------------------------------------------------

def pending_ticks(rig) -> list:
    return [item for item in rig.scheduled if item[1] == rig.editor._onRopeScanTick]


def saved_ropes(csv_path) -> tuple:
    return read_ropes(rope_file_for(Path(csv_path))).unwrap().ropes


def typed_but_not_committed(rig, tmp_path, name="x", text="108") -> Path:
    """A saved map with rope 107 selected and `text` typed in a number box without Return and without leaving the box.
    That is the state when the user then clicks a button: a button click does not take the keyboard from the box, so the
    box's leave binding never fires."""
    csv_path = write_map(tmp_path / "m.csv", [(20, 20)])
    write_ropes(rope_file_for(csv_path), RopeFile((ROPE_107,), (FRAME_W, FRAME_H), {}))
    load(rig, csv_path)
    click(rig.editor, 107, 52)
    rig.editor._rope_vars[name].set(text)
    assert rig.editor.state.ropes == [ROPE_107] and not rig.editor.state.dirty  # nothing committed yet
    return csv_path


@pytest.mark.parametrize("name, text, expected", [
    ("x", "108", RopeMark(108, 43, 61)),
    ("top_y", "40", RopeMark(107, 40, 61)),
    ("bottom_y", "65", RopeMark(107, 43, 65)),
])
def test_save_writes_a_value_typed_in_a_number_box_and_not_committed(make_editor, tmp_path, name, text, expected):
    rig = make_editor()
    csv_path = typed_but_not_committed(rig, tmp_path, name, text)

    rig.editor._save()

    assert saved_ropes(csv_path) == (expected,)
    assert rig.editor.state.ropes == [expected] and not rig.editor.state.dirty
    assert box_values(rig.editor) == [str(expected.x), str(expected.top_y), str(expected.bottom_y)]


def test_save_as_writes_a_value_typed_in_a_number_box_and_not_committed(make_editor, tmp_path):
    rig = make_editor()
    typed_but_not_committed(rig, tmp_path)
    other = tmp_path / "other.csv"
    rig.dialogs.save_path = str(other)

    rig.editor._saveAs()

    assert saved_ropes(other) == (RopeMark(108, 43, 61),)
    assert not rig.editor.state.dirty


def test_save_checks_the_keep_out_against_a_typed_value(make_editor, tmp_path):
    rig = make_editor()
    csv_path = write_map(tmp_path / "m.csv", [(20, 20), (60, 43)])
    write_ropes(rope_file_for(csv_path), RopeFile((ROPE_107,), (FRAME_W, FRAME_H), {}))
    load(rig, csv_path)
    click(rig.editor, 107, 52)
    rig.editor._rope_vars["x"].set("61")  # the rope moves to x 61, next to the node at (60, 43), where it meets the platform

    rig.editor._save()

    assert rig.dialogs.errors and rig.dialogs.errors[-1][0] == "Cannot save"
    assert saved_ropes(csv_path) == (ROPE_107,) and rig.editor.state.ropes == [RopeMark(61, 43, 61)]


def test_save_refuses_a_bad_typed_value_as_leaving_the_box_does(make_editor, tmp_path):
    rig = make_editor()
    csv_path = typed_but_not_committed(rig, tmp_path, "x", "abc")

    rig.editor._save()

    assert saved_ropes(csv_path) == (ROPE_107,) and not rig.editor.state.dirty
    assert status(rig.editor) == "Not changed: x must be a whole number, 0 or more."
    assert box_values(rig.editor) == ["107", "43", "61"]


@pytest.mark.parametrize("answer, closes, saves", [(None, False, False), (False, True, False), (True, True, True)])
def test_close_counts_a_value_typed_in_a_number_box_as_unsaved(make_editor, tmp_path, monkeypatch, answer, closes, saves):
    rig = make_editor()
    csv_path = typed_but_not_committed(rig, tmp_path)
    asked = []

    def ask(title, message, **kw):
        asked.append((title, message))
        return answer

    monkeypatch.setattr(map_editor.messagebox, "askyesnocancel", ask)

    assert rig.editor._okToClose() is closes

    assert asked == [("Unsaved changes", "You have unsaved changes to m.csv.\nSave before closing?")]
    assert saved_ropes(csv_path) == ((RopeMark(108, 43, 61),) if saves else (ROPE_107,))


def test_close_with_nothing_typed_still_closes_without_asking(make_editor, tmp_path, monkeypatch):
    rig = make_editor()
    typed_but_not_committed(rig, tmp_path, "x", "107")  # the text is the rope's own value
    monkeypatch.setattr(map_editor.messagebox, "askyesnocancel", lambda *a, **kw: pytest.fail("asked with nothing unsaved"))

    assert rig.editor._okToClose() is True


def test_load_counts_a_value_typed_in_a_number_box_as_unsaved(make_editor, tmp_path, monkeypatch):
    rig = make_editor()
    csv_path = typed_but_not_committed(rig, tmp_path)
    asked = []
    monkeypatch.setattr(map_editor.messagebox, "askyesnocancel", lambda title, message, **kw: asked.append(message))

    load(rig, write_map(tmp_path / "other.csv", [(30, 30)]))  # the box answers Cancel (None)

    assert asked == ["You have unsaved changes to m.csv.\nSave before loading?"]
    assert rig.editor.state.current_file == str(csv_path) and rig.editor.state.ropes == [RopeMark(108, 43, 61)]


def test_a_load_cancels_a_find_scan_in_flight(make_editor, tmp_path):
    rig = make_editor(rope_frame())
    editor = rig.editor
    editor._findRopes()
    assert run_scan(rig, limit=5) == 5 and status(editor) == map_editor.FIND_ROPES_TEXT

    load(rig, write_map(tmp_path / "m.csv", [(20, 20)]))  # the live minimap's ropes must not land on this map
    assert status(editor) == ""
    assert run_scan(rig) == 1  # the pending tick fires, finds its collection gone and schedules nothing

    assert editor.state.ropes == [] and not editor.state.dirty and pending_ticks(rig) == []


def test_a_refused_load_leaves_a_find_scan_running(make_editor, tmp_path):
    rig = make_editor(rope_frame())
    editor = rig.editor
    editor._findRopes()
    run_scan(rig, limit=5)

    rig.dialogs.open_path = ""  # the file dialog is cancelled: the editor is unchanged, so is the collection
    editor._load()

    assert status(editor) == map_editor.FIND_ROPES_TEXT
    run_scan(rig)
    assert editor.state.ropes == [ROPE_46, ROPE_107]


def test_a_find_after_a_load_runs_one_collection(make_editor, tmp_path):
    rig = make_editor(rope_frame())
    editor = rig.editor
    lock = RecordingLock()
    editor.sharedInfo.minimap_frame_lock = lock
    editor._findRopes()
    run_scan(rig, limit=5)
    load(rig, write_map(tmp_path / "m.csv", [(20, 20)]))
    taken = lock.taken

    editor._findRopes()  # pressed again before the cancelled collection's tick has fired
    assert len(pending_ticks(rig)) == 2
    assert run_scan(rig, limit=1) == 1  # the old collection's tick: no frame taken, none scheduled
    assert lock.taken == taken and len(pending_ticks(rig)) == 1

    assert run_scan(rig) == map_editor.ROPE_SCAN_FRAMES and lock.taken == taken + map_editor.ROPE_SCAN_FRAMES
    assert editor.state.ropes == [ROPE_46, ROPE_107] and status(editor) == "Found 2 ropes"


# -- end minors sweep B (ROPE153-R3bRR n-m1) ----------------------------------------------------------------------------

@pytest.mark.parametrize("click_at, selected_rope, selected_id", [
    pytest.param((46, 38), 1, None, id="another rope"),
    pytest.param((20, 20), None, 1, id="a node"),
    pytest.param((90, 20), None, 2, id="empty ground places a node"),
])
def test_a_click_on_the_map_that_changes_the_selection_keeps_a_value_typed_in_a_number_box(
        make_editor, tmp_path, click_at, selected_rope, selected_id):
    # A click on the map takes the keyboard from the box, but Tk only queues the box's FocusOut and the click refreshes
    # the boxes first, so the typed value used to be overwritten before anything committed it (n-m1).
    rig = make_editor()
    editor = rig.editor
    csv_path = write_map(tmp_path / "m.csv", [(20, 20)])
    write_ropes(rope_file_for(csv_path), RopeFile((ROPE_107, ROPE_46), (FRAME_W, FRAME_H), {}))
    load(rig, csv_path)
    click(editor, 107, 52)
    assert editor.state.selected_rope == 0
    editor._rope_vars["x"].set("108")  # typed, no Return, the box not left
    assert editor.state.ropes == [ROPE_107, ROPE_46] and not editor.state.dirty

    click(editor, *click_at)

    assert editor.state.ropes == [RopeMark(108, 43, 61), ROPE_46]
    assert editor.state.dirty and editor.window.title().startswith("* ")
    assert editor.state.selected_rope == selected_rope and editor.state.selected_id == selected_id
