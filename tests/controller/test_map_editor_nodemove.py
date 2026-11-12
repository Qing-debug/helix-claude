"""Moving a selected node with the arrow keys in the map editor (NODEMOVE159; Hong's request, 2026-11-12, QB240, QB241).

With a node selected, Left/Right/Up/Down move it 1 minimap px and Shift+arrow 5 px; only `pos` changes. A press that lands in
a rope keep-out still moves (red ring, Save blocked); a press that would leave the minimap does nothing; the keys do nothing
with no node selected or while the keyboard is in a number box.

Like `test_map_editor_ropes.py` (whose fixture and helpers these tests share; there is no conftest.py under tests/), these build
a real `MapEditor` on a real withdrawn Tk root and are skipped where Tk cannot open a display. A key goes through
`press` below, which sends the real key event when Tk's focus allows it and otherwise runs the window's handler by hand, as
`press_delete` does for the Delete key.
"""
import copy
import tkinter as tk
from dataclasses import replace
from types import SimpleNamespace

import pytest

import src.gui.map_editor as map_editor
import test_map_editor_ropes as ropes
from src.classes.classes import Kanna
from src.controller.map_csv import EditorNode, read_editor_nodes
from src.controller.rope_file import rope_file_for

make_editor = ropes.make_editor  # the fixture, shared
click = ropes.click
place_blue = ropes.place_blue
write_map = ropes.write_map
load = ropes.load
ring_count = ropes.ring_count
FRAME_W, FRAME_H = ropes.FRAME_W, ropes.FRAME_H  # 162 x 82: x runs 0..161, y runs 0..81

STEPS = {"Left": (-1, 0), "Right": (1, 0), "Up": (0, -1), "Down": (0, 1)}
KEYS = tuple(STEPS)


def press(editor, key, shift=False, widget=None):
    """An arrow key (Shift+arrow when `shift`) typed with the keyboard in `widget` (by default the map canvas, which a click on
    the map gives the keyboard), through the widget's own bindings and then the window's.

    Never `focus_force` (it asks Windows for the foreground; see `press_delete` in test_map_editor_ropes.py). When Tk's focus is
    the widget after `focus_set`, send the real key event. Otherwise (inside one pytest process only the first Tk window gets
    the keyboard from Windows) check that the window has the binding and runs after the widget's own and its class's, and call
    the window's handler by hand with the widget as `event.widget`. Returns "key" or "call"."""
    window = editor.window
    window.update()
    widget = widget if widget is not None else editor.minimap_canvas
    widget.focus_set()
    window.update()
    sequence = f"<Shift-{key}>" if shift else f"<{key}>"
    if str(window.tk.call("focus")) == str(widget):
        widget.event_generate(sequence)
        return "key"
    assert window.bind(sequence)
    tags = list(widget.bindtags())
    assert tags.index(widget.winfo_class()) < tags.index(str(window))
    step = 5 if shift else 1
    dx, dy = STEPS[key]
    editor._onArrowKey(SimpleNamespace(widget=widget), dx * step, dy * step)
    return "call"


def node_ovals(editor):
    canvas = editor.minimap_canvas
    return [tuple(canvas.coords(item)) for item in canvas.find_withtag(map_editor.CANVAS_TAG_NODE) if canvas.type(item) == "oval"]


def selected_ring(editor):
    canvas = editor.minimap_canvas
    return [tuple(canvas.coords(item)) for item in canvas.find_withtag(map_editor.CANVAS_TAG_SELECTED)]


def square(x, y, radius_px):
    """The bounding box of a circle of `radius_px` canvas px around native (x, y), as Tk reports an oval's coords."""
    cx, cy = x * map_editor.RENDER_SCALE, y * map_editor.RENDER_SCALE
    return (cx - radius_px, cy - radius_px, cx + radius_px, cy + radius_px)


# -- the move ------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("shift, step", [(False, 1), (True, 5)])
@pytest.mark.parametrize("key", KEYS)
def test_an_arrow_moves_the_selected_node_1_px_and_shift_arrow_5_px(make_editor, key, shift, step):
    editor = make_editor().editor
    click(editor, 80, 40)  # places an orange node, which is selected
    dx, dy = STEPS[key]

    press(editor, key, shift)
    assert editor.state.nodes[1].pos == (80 + dx * step, 40 + dy * step)
    press(editor, key, shift)  # a second press moves it on from there
    assert editor.state.nodes[1].pos == (80 + 2 * dx * step, 40 + 2 * dy * step)

    assert editor.state.selected_id == 1 and len(editor.state.nodes) == 1  # still the one selected node, nothing placed


def test_the_arrows_work_with_the_keyboard_on_a_button_or_a_checkbox(make_editor):
    editor = make_editor().editor
    click(editor, 80, 40)

    press(editor, "Right", widget=editor._show_edges_cb)  # a widget with no arrow binding of its own
    press(editor, "Down", True, widget=editor._delete_rope_button)

    assert editor.state.nodes[1].pos == (81, 45)


def test_the_eight_arrow_sequences_are_bound_on_the_editor_window_only(make_editor):
    editor = make_editor().editor
    for key in KEYS:
        for sequence in (f"<{key}>", f"<Shift-{key}>"):
            assert editor.window.bind(sequence)
            assert editor.window.bind_all(sequence) == ""  # not on every window of the application
            assert editor.window.master.bind(sequence) == ""  # not on the main window


def test_a_move_changes_only_the_position(make_editor):
    rig = make_editor()
    editor = rig.editor
    editor._placement_flag_vars["can_fall"].set(True)
    place_blue(rig, 30, 20)  # #1 blue, can_fall
    editor._placement_flag_vars["can_fall"].set(False)
    editor._placement_flag_vars["is_left_platform_edge"].set(True)
    place_blue(rig, 60, 20)  # #2 blue, left edge
    editor._placement_var.set("orange_node")
    editor._onPlacementChange()
    editor._placement_flag_vars["is_right_platform_edge"].set(True)
    click(editor, 90, 20)  # #3 orange, left edge and right edge
    before = copy.deepcopy(editor.state.nodes)
    assert before[1].can_fall and before[2].is_left_platform_edge and before[3].is_right_platform_edge
    order = list(editor.state.rotation_order)
    assert order == [1, 2]

    click(editor, 30, 20)  # select #1, the first in the rotation order
    press(editor, "Down")
    press(editor, "Right", True)
    click(editor, 90, 20)  # and #3, an orange one
    press(editor, "Up", True)

    after = editor.state.nodes
    assert after[1] == replace(before[1], pos=(35, 21))
    assert after[2] == before[2]
    assert after[3] == replace(before[3], pos=(90, 15))
    assert editor.state.rotation_order == order  # #1 is still first in the rotation: the place does not follow the position


def test_a_move_refreshes_the_inspector_the_rotation_list_and_the_drawing(make_editor):
    rig = make_editor()
    editor = rig.editor
    place_blue(rig, 30, 20)
    assert node_ovals(editor) == [square(30, 20, map_editor.NODE_DRAW_RADIUS_PX * map_editor.RENDER_SCALE)]

    press(editor, "Right", True)
    press(editor, "Down")

    assert editor._inspector_pos_label.cget("text") == "pos: (35, 21)"
    rows = [row.winfo_children()[0].cget("text") for row in editor._rotation_frame.winfo_children()]
    assert rows == ["1. #1 (35, 21)"]
    assert node_ovals(editor) == [square(35, 21, map_editor.NODE_DRAW_RADIUS_PX * map_editor.RENDER_SCALE)]
    assert selected_ring(editor) == [square(35, 21, map_editor.SELECTED_RING_RADIUS_PX * map_editor.RENDER_SCALE)]

    click(editor, 35, 21)  # the node is hit where it is now, not where it was: it is selected, nothing is placed
    assert editor.state.selected_id == 1 and len(editor.state.nodes) == 1


def test_a_move_redraws_the_edge_preview(make_editor):
    rig = make_editor()
    editor = rig.editor
    editor.controller.agent.setCharacterClass(Kanna())  # the preview needs a character class
    editor.onCharacterClassChanged()
    editor._show_edges_var.set(True)
    click(editor, 20, 20)
    click(editor, 42, 20)  # #2, selected; 22 px from #1, Kanna's horizontal reach is 19
    edges = lambda: len(editor.minimap_canvas.find_withtag(map_editor.CANVAS_TAG_EDGE))  # noqa: E731
    assert edges() == 0

    press(editor, "Left", True)  # (37, 20): 17 px away, in reach
    assert edges() == 1

    press(editor, "Up", True)  # (37, 15): 5 px off the line, past the edge tolerance of 2
    assert edges() == 0


# -- dirty, save, close --------------------------------------------------------------------------------------------------

def test_a_move_makes_the_editor_dirty_and_moving_back_makes_it_clean(make_editor, tmp_path):
    rig = make_editor()
    editor = rig.editor
    load(rig, write_map(tmp_path / "m.csv", [(20, 20), (40, 20)]))
    click(editor, 20, 20)  # selects #1 and changes nothing
    assert not editor.state.dirty and not editor.window.title().startswith("* ")

    press(editor, "Right")
    assert editor.state.dirty and editor.window.title().startswith("* ")

    press(editor, "Left")  # the editor compares with the saved baseline, so the way back is clean again
    assert not editor.state.dirty and not editor.window.title().startswith("* ")


def test_a_moved_node_counts_as_unsaved_and_is_saved_where_it_is(make_editor, tmp_path, monkeypatch):
    csv_path = write_map(tmp_path / "m.csv", [(20, 20), (40, 20)])
    rig = make_editor()
    editor = rig.editor
    load(rig, csv_path)
    click(editor, 20, 20)
    press(editor, "Down", True)
    asked = []
    monkeypatch.setattr(map_editor.messagebox, "askyesnocancel", lambda title, message, **kw: asked.append((title, message)))

    assert editor._okToClose() is False  # the box answers Cancel (None)
    assert asked == [("Unsaved changes", "You have unsaved changes to m.csv.\nSave before closing?")]

    editor._save()
    assert not editor.state.dirty
    nodes, order, _ = read_editor_nodes(str(csv_path)).unwrap()
    assert [(n.nodetype, n.pos) for n in nodes] == [("blue_node", (20, 25)), ("blue_node", (40, 20))]
    assert order == [1, 2]


# -- the rope keep-out ---------------------------------------------------------------------------------------------------

def test_a_press_into_a_rope_keep_out_still_moves_with_a_red_ring_and_save_is_blocked(make_editor, tmp_path):
    rig = make_editor()
    editor = rig.editor
    ropes.set_ropes(editor, [ropes.ROPE_107])  # keep-out: x 101..111, y 37..49 and 55..67 (the click refusal's numbers)
    place_blue(rig, 112, 43)  # just outside it
    assert ring_count(editor) == 0 and editor._validateForSave() == ("ok", "")

    press(editor, "Left")  # (111, 43): inside
    assert editor.state.nodes[1].pos == (111, 43)
    assert ring_count(editor) == 1
    verdict, message = editor._validateForSave()
    assert verdict == "block"
    assert message.split("\n") == [
        "Cannot save: these nodes are too close to a rope where it meets a platform:",
        "#1 (111, 43): rope at x 107; place it at x 100 or less, or 112 or more",
    ]
    rig.dialogs.save_path = str(tmp_path / "m.csv")
    editor._save()
    assert rig.dialogs.errors[-1][0] == "Cannot save" and "#1 (111, 43)" in rig.dialogs.errors[-1][1]
    assert not (tmp_path / "m.csv").exists() and not rope_file_for(tmp_path / "m.csv").exists()

    press(editor, "Right")  # out again: the ring goes and Save is free
    assert editor.state.nodes[1].pos == (112, 43) and ring_count(editor) == 0
    assert editor._validateForSave() == ("ok", "")


def test_shift_arrows_walk_a_node_through_a_keep_out_and_out_the_other_side(make_editor):
    rig = make_editor()
    editor = rig.editor
    ropes.set_ropes(editor, [ropes.ROPE_107])
    place_blue(rig, 112, 43)

    seen = []
    for _ in range(3):
        press(editor, "Left", True)
        seen.append((editor.state.nodes[1].pos, ring_count(editor)))

    assert seen == [((107, 43), 1), ((102, 43), 1), ((97, 43), 0)]  # x 101..111 is the keep-out


# -- the edge of the minimap ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("start, key, shift, expected", [
    # a press that would leave the minimap does nothing; it does not stop at the edge
    ((0, 10), "Left", False, (0, 10)),
    ((FRAME_W - 1, 10), "Right", False, (FRAME_W - 1, 10)),
    ((50, 0), "Up", False, (50, 0)),
    ((50, FRAME_H - 1), "Down", False, (50, FRAME_H - 1)),
    ((3, 10), "Left", True, (3, 10)),
    ((FRAME_W - 4, 10), "Right", True, (FRAME_W - 4, 10)),
    ((50, 4), "Up", True, (50, 4)),
    ((50, FRAME_H - 5), "Down", True, (50, FRAME_H - 5)),
    # a press that stays inside moves, up to and including the edge pixel
    ((1, 10), "Left", False, (0, 10)),
    ((FRAME_W - 2, 10), "Right", False, (FRAME_W - 1, 10)),
    ((50, 1), "Up", False, (50, 0)),
    ((50, FRAME_H - 2), "Down", False, (50, FRAME_H - 1)),
    ((5, 10), "Left", True, (0, 10)),
    ((FRAME_W - 6, 10), "Right", True, (FRAME_W - 1, 10)),
    ((50, 5), "Up", True, (50, 0)),
    ((50, FRAME_H - 6), "Down", True, (50, FRAME_H - 1)),
])
def test_a_press_that_would_leave_the_minimap_does_nothing(make_editor, tmp_path, start, key, shift, expected):
    rig = make_editor()
    editor = rig.editor
    load(rig, write_map(tmp_path / "m.csv", [start]))
    click(editor, *start)
    assert editor.state.selected_id == 1 and not editor.state.dirty

    press(editor, key, shift)

    assert editor.state.nodes[1].pos == expected
    assert editor.state.dirty == (expected != start)  # a refused press is not an edit


def test_a_node_already_outside_the_minimap_can_be_moved_back_toward_it_and_not_further_out(make_editor):
    # A map file can hold a node off the minimap (Save warns about it). The rule is about a press that LEAVES the minimap.
    rig = make_editor()
    editor = rig.editor
    place_blue(rig, 50, 20)
    editor.state.nodes[1].pos = (FRAME_W + 2, FRAME_H + 3)  # (164, 85)
    editor._renderNodes()

    press(editor, "Right")
    press(editor, "Down", True)
    assert editor.state.nodes[1].pos == (FRAME_W + 2, FRAME_H + 3)  # further out: nothing

    press(editor, "Left")
    press(editor, "Up")
    assert editor.state.nodes[1].pos == (FRAME_W + 1, FRAME_H + 2)  # closer, still outside: moves

    press(editor, "Left", True)
    press(editor, "Up", True)
    assert editor.state.nodes[1].pos == (FRAME_W - 4, FRAME_H - 3)  # back inside


# -- when the keys do nothing --------------------------------------------------------------------------------------------

def test_arrows_do_nothing_with_no_node_selected(make_editor):
    rig = make_editor()
    editor = rig.editor
    click(editor, 20, 20)
    click(editor, 60, 20)  # #2 selected
    editor._deleteSelected()  # nothing selected now; #1 is still on the map
    assert editor.state.selected_id is None and list(editor.state.nodes) == [1]
    before = (copy.deepcopy(editor.state.nodes), editor.state.dirty)

    for key in KEYS:
        for shift in (False, True):
            press(editor, key, shift)

    assert (editor.state.nodes, editor.state.dirty) == before
    assert editor.state.selected_id is None


def test_arrows_do_nothing_with_a_rope_selected(make_editor):
    rig = make_editor()
    editor = rig.editor
    click(editor, 20, 20)
    ropes.set_ropes(editor, [ropes.ROPE_107, ropes.ROPE_46])
    click(editor, 107, 52)  # selects the rope and clears the node selection
    assert editor.state.selected_rope == 0 and editor.state.selected_id is None
    before = (copy.deepcopy(editor.state.nodes), list(editor.state.ropes))

    for key in KEYS:
        for shift in (False, True):
            press(editor, key, shift)

    assert (editor.state.nodes, editor.state.ropes) == before and editor.state.selected_rope == 0


def test_arrows_in_a_number_box_do_not_move_the_selected_node(make_editor):
    rig = make_editor()
    editor = rig.editor
    place_blue(rig, 40, 30)  # selected
    box = editor._rope_boxes["x"]
    box.config(state=tk.NORMAL)  # the boxes are disabled while a node is selected; the guard is about whoever has the keyboard
    editor._rope_vars["x"].set("12")
    before = (copy.deepcopy(editor.state.nodes), editor.state.dirty)

    for key in KEYS:
        for shift in (False, True):
            press(editor, key, shift, widget=box)

    assert (editor.state.nodes, editor.state.dirty) == before and editor.state.selected_id == 1


def test_arrows_before_the_minimap_exists_do_nothing(make_editor):
    editor = make_editor(None).editor  # no minimap frame yet: no canvas, no node was ever drawn
    assert editor.minimap_canvas is None
    editor.state.nodes[1] = EditorNode(1, "orange_node", (20, 20))
    editor.state.selected_id = 1

    editor._onArrowKey(SimpleNamespace(widget=editor.window), 1, 0)

    assert editor.state.nodes[1].pos == (20, 20)
