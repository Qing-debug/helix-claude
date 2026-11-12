"""The map editor finds ropes by itself (AUTOFIND162).

Hong's words (verbatim): "the attempt to find ropes should ways be done no matter what and its implications should be
displayed fom the tget go as opposed to me needing to click find ropes". His four page answers (rope161-1 to -4):
the find adds only ropes not already saved (x within 2 px), saved ropes stay as they are, found ropes mark the editor
dirty and are written only on Save, and there is one find when the editor opens (and one after each successful load)
with a hint that a character standing still on a rope can hide it; the Find ropes button stays.
His later answer rope163-1 ("Remember deleted ropes in the map's rope file, so a rope you delete stays deleted")
replaced rope161-2's "a deleted rope may come back": see the last section of this file.

Like the other editor tests these build a real `MapEditor` on a real withdrawn Tk root (skipped where Tk cannot open a
display) with `after` recorded, and share the fixture and helpers of `test_map_editor_ropes.py`. The fixture's editors
have the automatic find off unless a test asks for it with `make(frame, auto_find=True)`.
"""
import inspect
import json
from pathlib import Path

import numpy as np
import pytest

import src.gui.map_editor as map_editor
import test_map_editor_ropes as ropes
from src.controller.map_csv import EditorNode, write_editor_nodes
from src.controller.rope_file import RopeFile, RopeMark, read_ropes, rope_file_for, write_ropes

make_editor = ropes.make_editor  # the fixture, shared
click = ropes.click
write_map = ropes.write_map
load = ropes.load
ring_count = ropes.ring_count
rope_frame = ropes.rope_frame
run_scan = ropes.run_scan
pending_ticks = ropes.pending_ticks
status = ropes.status
rope_lines = ropes.rope_lines
ROPE_46, ROPE_107 = ropes.ROPE_46, ropes.ROPE_107
FRAME_W, FRAME_H = ropes.FRAME_W, ropes.FRAME_H

HINT = ("A character standing still on a rope can hide it from the find. Move the character, then press Find ropes "
        "if a rope seems missing.")
SCAN = map_editor.ROPE_SCAN_FRAMES


def plain_frame() -> np.ndarray:
    return np.full((FRAME_H, FRAME_W, 3), ropes.BACKGROUND, dtype=np.uint8)


def all_widgets(widget):
    for child in widget.winfo_children():
        yield child
        yield from all_widgets(child)


def notice(editor) -> str:
    return editor._rope_notice_label.cget("text")


def notice_shown(editor) -> bool:
    return bool(editor._rope_notice_label.winfo_manager())


def map_with_saved_ropes(tmp_path, ropes_in_file, positions=((20, 20),), name="m.csv") -> Path:
    csv_path = write_map(tmp_path / name, positions)
    if ropes_in_file is not None:
        write_ropes(rope_file_for(csv_path), RopeFile(tuple(ropes_in_file), (FRAME_W, FRAME_H), {}))
    return csv_path


def no_prompt(monkeypatch):
    monkeypatch.setattr(map_editor.messagebox, "askyesnocancel",
                        lambda *a, **kw: pytest.fail("the unsaved-changes box was shown with nothing unsaved"))


# -- when the find runs ------------------------------------------------------------------------------------------------

def test_the_find_at_open_is_on_by_default():
    assert inspect.signature(map_editor.MapEditor.__init__).parameters["auto_find_ropes"].default is True


def test_opening_the_editor_starts_one_find_that_adds_the_ropes_without_a_click(make_editor):
    rig = make_editor(rope_frame(), auto_find=True)
    editor = rig.editor
    assert status(editor) == map_editor.FIND_ROPES_TEXT  # it started at once
    assert len(pending_ticks(rig)) == 1

    editor._findRopes()  # the button pressed during the automatic collection does nothing
    assert len(pending_ticks(rig)) == 1

    assert run_scan(rig) == SCAN
    assert editor.state.ropes == [ROPE_46, ROPE_107] and rope_lines(editor) == 2
    assert status(editor) == "Found 2 ropes"
    assert pending_ticks(rig) == []  # one find per open, not a repeating one


def test_the_editors_of_the_other_tests_start_no_find(make_editor):
    rig = make_editor(rope_frame())  # the fixture's default: the switch is off
    assert pending_ticks(rig) == [] and status(rig.editor) == "" and not notice_shown(rig.editor)


def test_the_find_waits_for_the_canvas_and_starts_when_it_is_built(make_editor):
    rig = make_editor(None, auto_find=True)  # no minimap frame yet, so no canvas
    editor = rig.editor
    assert editor.minimap_canvas is None and pending_ticks(rig) == []
    assert status(editor) == "" and not notice_shown(editor)  # nothing is said about a minimap nobody asked about

    editor.sharedInfo.minimap_frame = rope_frame()
    waits = [item for item in rig.scheduled if item[1] == editor._waitForMinimapAndBuildCanvas]
    assert len(waits) == 1
    waits[0][1](*waits[0][2])  # the editor's own poll finds the frame and builds the canvas

    assert editor.minimap_canvas is not None and status(editor) == map_editor.FIND_ROPES_TEXT
    assert len(pending_ticks(rig)) == 1
    run_scan(rig)
    assert editor.state.ropes == [ROPE_46, ROPE_107]


def test_a_load_before_the_canvas_exists_starts_no_find_and_the_canvas_starts_it(make_editor, tmp_path):
    rig = make_editor(None, auto_find=True)
    editor = rig.editor
    load(rig, map_with_saved_ropes(tmp_path, [ROPE_46]))
    assert editor.state.ropes == [ROPE_46] and pending_ticks(rig) == [] and status(editor) == ""

    editor.sharedInfo.minimap_frame = rope_frame()
    waits = [item for item in rig.scheduled if item[1] == editor._waitForMinimapAndBuildCanvas]
    waits[0][1](*waits[0][2])
    run_scan(rig)

    assert editor.state.ropes == [ROPE_46, ROPE_107]  # against the loaded file, not an empty map


# -- add-only, saved ropes untouched, written only on Save -------------------------------------------------------------

def test_a_load_runs_the_find_against_the_loaded_files_ropes_and_only_adds(make_editor, tmp_path):
    saved_107 = RopeMark(108, 40, 62)  # hand-edited: moved a column and taller; the found one is (107, 43, 61)
    csv_path = map_with_saved_ropes(tmp_path, [saved_107])
    rope_path = rope_file_for(csv_path)
    before = rope_path.read_bytes()
    rig = make_editor(rope_frame(), auto_find=True)
    editor = rig.editor
    run_scan(rig)  # the open-time find, on the untitled map

    load(rig, csv_path)
    assert status(editor) == map_editor.FIND_ROPES_TEXT and len(pending_ticks(rig)) == 1
    assert editor.state.ropes == [saved_107]  # the file's marks at once

    assert run_scan(rig) == SCAN
    assert editor.state.ropes == [saved_107, ROPE_46]  # the saved mark untouched, only the new rope added
    assert status(editor) == "Found 2 ropes, 1 new"
    assert editor.state.dirty and editor.window.title().startswith("* ")
    assert rope_path.read_bytes() == before  # nothing is written until Save

    editor._save()
    assert read_ropes(rope_path).unwrap().ropes == (saved_107, ROPE_46)
    assert not editor.state.dirty


def test_a_load_whose_file_holds_every_found_rope_stays_clean(make_editor, tmp_path, monkeypatch):
    csv_path = map_with_saved_ropes(tmp_path, [ROPE_46, ROPE_107])
    rig = make_editor(rope_frame(), auto_find=True)
    run_scan(rig)
    load(rig, csv_path)

    run_scan(rig)

    editor = rig.editor
    assert editor.state.ropes == [ROPE_46, ROPE_107] and status(editor) == "Found 2 ropes, none new"
    assert not editor.state.dirty and not editor.window.title().startswith("* ")
    no_prompt(monkeypatch)
    assert editor._okToClose() is True


def test_a_load_with_no_rope_file_adds_the_found_ropes_and_marks_the_map_dirty(make_editor, tmp_path):
    csv_path = map_with_saved_ropes(tmp_path, None)
    rig = make_editor(rope_frame(), auto_find=True)
    run_scan(rig)
    load(rig, csv_path)
    assert rig.editor.state.ropes == [] and not rig.editor.state.rope_file_read

    run_scan(rig)

    assert rig.editor.state.ropes == [ROPE_46, ROPE_107] and rig.editor.state.dirty
    assert not rope_file_for(csv_path).exists()  # still only in the editor


def test_a_load_cancels_the_open_time_scan_and_the_new_find_runs_one_collection(make_editor, tmp_path):
    rig = make_editor(rope_frame(), auto_find=True)
    editor = rig.editor
    lock = ropes.RecordingLock()
    editor.sharedInfo.minimap_frame_lock = lock
    assert run_scan(rig, limit=5) == 5 and status(editor) == map_editor.FIND_ROPES_TEXT
    load(rig, map_with_saved_ropes(tmp_path, [ROPE_46]))
    taken = lock.taken

    assert len(pending_ticks(rig)) == 2  # the old collection's tick and the new find's first
    assert run_scan(rig, limit=1) == 1 and lock.taken == taken  # the old one: no frame taken, nothing scheduled
    assert run_scan(rig) == SCAN and lock.taken == taken + SCAN

    assert editor.state.ropes == [ROPE_46, ROPE_107]


def test_a_refused_or_cancelled_load_starts_no_find(make_editor, tmp_path):
    rig = make_editor(rope_frame(), auto_find=True)
    editor = rig.editor
    run_scan(rig)
    bad = tmp_path / "bad.csv"
    bad.write_text("this is not a map", encoding="utf-8")

    rig.dialogs.open_path = ""  # the file dialog is cancelled
    editor._load()
    assert pending_ticks(rig) == []

    load(rig, bad)  # refused: "Could not load map"
    assert rig.dialogs.errors[-1][0] == "Could not load map" and pending_ticks(rig) == []
    assert editor.state.ropes == [ROPE_46, ROPE_107]  # the editor is as it was


def test_the_find_ropes_button_stays_and_retries_once_the_character_has_moved(make_editor):
    # The character stands on rope 107 at open, so the find sees only rope 46; Hong moves it and presses the button.
    rig = make_editor(rope_frame(rope_107=False), auto_find=True)
    editor = rig.editor
    run_scan(rig)
    assert editor.state.ropes == [ROPE_46]

    editor.sharedInfo.minimap_frame = rope_frame()  # the character has moved off the rope
    button = next(w for w in all_widgets(editor._panel_frame) if w.winfo_class() == "Button"
                  and w.cget("text") == "Find ropes")
    button.invoke()
    assert status(editor) == map_editor.FIND_ROPES_TEXT and len(pending_ticks(rig)) == 1
    run_scan(rig)

    assert editor.state.ropes == [ROPE_46, ROPE_107] and status(editor) == "Found 2 ropes, 1 new"


# -- an untitled map with no nodes is not marked dirty by the find ----------------------------------------------------

def test_the_open_time_find_does_not_mark_an_untitled_empty_map_dirty(make_editor, monkeypatch):
    rig = make_editor(rope_frame(), auto_find=True)
    editor = rig.editor

    run_scan(rig)

    assert editor.state.ropes == [ROPE_46, ROPE_107] and rope_lines(editor) == 2
    assert not editor.state.dirty and not editor.window.title().startswith("* ")
    no_prompt(monkeypatch)
    assert editor._okToClose() is True


def test_the_untitled_empty_map_rule_covers_only_the_find_not_what_he_does_next(make_editor):
    rig = make_editor(rope_frame(), auto_find=True)
    editor = rig.editor
    run_scan(rig)

    click(editor, 20, 20)  # a node: unsaved work
    assert editor.state.dirty
    editor._deleteSelected()  # gone again: back to clean, the found ropes are the baseline
    assert editor.state.nodes == {} and not editor.state.dirty

    click(editor, 46, 38)  # select rope 46 and delete it: his edit
    editor._deleteSelectedRope()
    assert editor.state.ropes == [ROPE_107] and editor.state.dirty


def test_a_find_that_ends_on_a_map_with_a_node_marks_it_dirty_for_good(make_editor):
    rig = make_editor(rope_frame(), auto_find=True)
    editor = rig.editor
    click(editor, 20, 20)  # placed while the collection runs
    assert pending_ticks(rig)

    run_scan(rig)

    assert editor.state.ropes == [ROPE_46, ROPE_107]
    editor._deleteSelected()  # the node goes, the ropes stay: still unsaved
    assert editor.state.nodes == {} and editor.state.dirty


def test_the_button_on_an_untitled_empty_map_still_marks_it_dirty(make_editor):
    rig = make_editor(rope_frame())  # no automatic find
    rig.editor._findRopes()
    run_scan(rig)
    assert rig.editor.state.dirty  # unchanged: only the automatic find is exempt


# -- the notice -------------------------------------------------------------------------------------------------------

def test_the_notice_is_on_screen_from_the_start_with_the_hint(make_editor):
    rig = make_editor(rope_frame(), auto_find=True)
    editor = rig.editor

    assert notice_shown(editor)
    assert notice(editor) == "Finding ropes on the minimap (3 seconds)...\n" + HINT
    slaves = editor.window.pack_slaves()  # directly under the status line: on screen without scrolling the panel
    assert slaves.index(editor._rope_notice_label) == slaves.index(editor._status_label) + 1


def test_the_notice_names_what_was_added_and_what_it_means(make_editor):
    rig = make_editor(rope_frame(), auto_find=True)
    editor = rig.editor

    run_scan(rig)

    text = notice(editor)
    assert text == (
        "Added 2 ropes found on the minimap (x 46, x 107). Nodes within 5 px sideways and 6 px of where a rope meets a "
        "platform are refused and ringed red, and Save is blocked until they are moved. A wrong rope can be deleted: "
        "select it and press Delete, and it stays deleted once you press Save. Ropes are written only when you press Save.\n" + HINT)
    assert notice_shown(editor)


def test_the_notice_says_one_rope_for_one(make_editor):
    rig = make_editor(rope_frame(rope_46=False), auto_find=True)
    run_scan(rig)
    assert notice(rig.editor).startswith("Added 1 rope found on the minimap (x 107). ")


def test_the_notice_stays_when_a_click_clears_the_status_line(make_editor):
    rig = make_editor(rope_frame(), auto_find=True)
    editor = rig.editor
    run_scan(rig)
    kept = notice(editor)

    click(editor, 20, 20)  # a placed node writes "" to the status line
    click(editor, 101, 43)  # a refused click writes its own text (6 px from the rope's line, inside its keep-out)
    assert status(editor).startswith("Not placed:")

    assert notice(editor) == kept and notice_shown(editor)


def test_the_notice_for_a_find_that_found_nothing(make_editor):
    rig = make_editor(plain_frame(), auto_find=True)
    run_scan(rig)
    assert notice(rig.editor) == "No rope found on the minimap.\n" + HINT
    assert status(rig.editor) == "Found no rope" and not rig.editor.state.dirty


def test_the_notice_when_every_found_rope_was_already_marked(make_editor, tmp_path):
    rig = make_editor(rope_frame(), auto_find=True)
    run_scan(rig)
    load(rig, map_with_saved_ropes(tmp_path, [ROPE_46, ROPE_107]))
    run_scan(rig)
    assert notice(rig.editor) == "Found 2 ropes on the minimap, all already marked in this map.\n" + HINT


def test_the_notice_when_the_find_had_no_frames(make_editor):
    rig = make_editor(rope_frame(), auto_find=True)
    rig.editor.sharedInfo.minimap_frame = None  # the vision thread lost the minimap for the whole collection
    run_scan(rig)
    text = notice(rig.editor)
    assert text.startswith("Rope find failed: ") and "no minimap frames were collected" in text and text.endswith(HINT)


def test_a_load_replaces_the_notice_of_the_old_map(make_editor, tmp_path):
    rig = make_editor(rope_frame())  # no automatic find: the load alone must drop the old map's notice
    editor = rig.editor
    editor._findRopes()
    run_scan(rig)
    assert notice_shown(editor)

    load(rig, map_with_saved_ropes(tmp_path, [ROPE_46]))

    assert notice(editor) == "" and not notice_shown(editor)


def test_a_load_that_cancels_a_scan_clears_its_notice(make_editor, tmp_path):
    rig = make_editor(rope_frame())
    editor = rig.editor
    editor._findRopes()
    run_scan(rig, limit=3)
    assert notice(editor).startswith("Finding ropes")

    load(rig, map_with_saved_ropes(tmp_path, [ROPE_46]))

    assert notice(editor) == "" and status(editor) == ""


def test_the_status_line_says_how_many_found_ropes_are_new(make_editor):
    rig = make_editor(rope_frame())
    editor = rig.editor
    ropes.set_ropes(editor, [ROPE_46])
    editor._findRopes()
    run_scan(rig)
    assert status(editor) == "Found 2 ropes, 1 new"

    editor._findRopes()
    run_scan(rig)
    assert status(editor) == "Found 2 ropes, none new"


# -- the keep-out applies to ropes found by itself --------------------------------------------------------------------

def test_a_found_rope_rings_a_node_near_its_end_and_blocks_save(make_editor, tmp_path):
    csv_path = map_with_saved_ropes(tmp_path, None, positions=((105, 42), (20, 20)))
    rig = make_editor(rope_frame(), auto_find=True)
    run_scan(rig)
    load(rig, csv_path)
    editor = rig.editor
    assert ring_count(editor) == 0

    run_scan(rig)

    assert editor.state.ropes == [ROPE_46, ROPE_107] and ring_count(editor) == 1
    verdict, message = editor._validateForSave()
    assert verdict == "block" and "#1 (105, 42): rope at x 107" in message
    editor._save()
    assert rig.dialogs.errors[-1][0] == "Cannot save" and not rope_file_for(csv_path).exists()


def test_a_found_rope_refuses_a_click_near_its_end_on_an_untitled_map(make_editor):
    rig = make_editor(rope_frame(), auto_find=True)
    editor = rig.editor
    run_scan(rig)

    click(editor, 101, 43)

    assert editor.state.nodes == {}
    assert status(editor) == ropes.not_placed(101, 43, 107, 100, 112)


# -- a rope file that cannot be read: no find, so the question on Save still comes first (AUTOFIND163 I1) ---------------

def damaged_rope_file_with_a_hand_made_rope(tmp_path):
    """A rope file from a newer Helix (version 99) that holds a rope Hong marked by hand: x 80, which the finder never sees."""
    csv_path = write_map(tmp_path / "m.csv")
    payload = json.dumps({"format": "helix-ropes", "version": 99, "ropes": [{"x": 80, "top_y": 30, "bottom_y": 50}]}).encode()
    rope_file_for(csv_path).write_bytes(payload)
    return csv_path, payload


def test_a_load_whose_rope_file_cannot_be_read_runs_no_find_and_save_still_asks(make_editor, tmp_path):
    csv_path, payload = damaged_rope_file_with_a_hand_made_rope(tmp_path)
    rig = make_editor(rope_frame(), auto_find=True)
    editor = rig.editor
    run_scan(rig)  # the open-time find, on the untitled map

    load(rig, csv_path)
    assert rig.dialogs.errors[-1][0] == "Rope file not loaded"
    run_scan(rig)  # nothing may be pending: a find here would fill state.ropes and Save would not ask
    assert editor.state.ropes == [] and pending_ticks(rig) == []

    rig.dialogs.yes_answer = False  # No: leave the file
    editor._save()
    assert [title for title, _ in rig.dialogs.questions] == ["Rope file damaged"]
    assert rope_file_for(csv_path).read_bytes() == payload  # the hand-made rope x 80 survives


def test_the_notice_says_why_no_find_ran_after_a_load_with_a_damaged_rope_file(make_editor, tmp_path):
    csv_path, _ = damaged_rope_file_with_a_hand_made_rope(tmp_path)
    rig = make_editor(rope_frame(), auto_find=True)
    run_scan(rig)

    load(rig, csv_path)

    assert notice_shown(rig.editor)
    assert notice(rig.editor) == map_editor.ROPE_SKIP_DAMAGED_TEXT
    assert "rope file could not be read" in notice(rig.editor) and "Find ropes" in notice(rig.editor)


def test_the_find_ropes_button_still_works_after_a_damaged_rope_file(make_editor, tmp_path):
    csv_path, _ = damaged_rope_file_with_a_hand_made_rope(tmp_path)
    rig = make_editor(rope_frame(), auto_find=True)
    run_scan(rig)
    load(rig, csv_path)

    rig.editor._findRopes()  # his own click: he has seen the box
    run_scan(rig)

    assert rig.editor.state.ropes == [ROPE_46, ROPE_107]


def test_a_damaged_rope_file_loaded_before_the_canvas_exists_gets_no_find_when_the_canvas_is_built(make_editor, tmp_path):
    csv_path, _ = damaged_rope_file_with_a_hand_made_rope(tmp_path)
    rig = make_editor(None, auto_find=True)
    load(rig, csv_path)

    rig.editor.sharedInfo.minimap_frame = rope_frame()
    waits = [item for item in rig.scheduled if item[1] == rig.editor._waitForMinimapAndBuildCanvas]
    waits[0][1](*waits[0][2])  # the canvas is built: the one find per open is due, but not for this map

    assert pending_ticks(rig) == [] and notice(rig.editor) == map_editor.ROPE_SKIP_DAMAGED_TEXT


# -- a deleted rope stays deleted (AUTOFIND163 I2 and M2; Hong, rope163-1) -----------------------------------------------

def deleted_in_file(csv_path) -> tuple:
    return read_ropes(rope_file_for(Path(csv_path))).unwrap().deleted


def select_and_delete(editor, x, y):
    click(editor, x, y)
    assert editor.state.selected_rope is not None
    editor._deleteSelectedRope()


def test_a_deleted_rope_stays_deleted_on_the_next_open_and_load(make_editor, tmp_path):
    csv_path = map_with_saved_ropes(tmp_path, [ROPE_46, ROPE_107])
    first = make_editor(rope_frame(), auto_find=True)
    run_scan(first)
    load(first, csv_path)
    run_scan(first)
    select_and_delete(first.editor, 107, 52)
    assert first.editor.state.dirty  # the deletion is a change to the file, so the title says so until Save
    first.editor._save()
    assert ropes.saved_ropes(csv_path) == (ROPE_46,) and deleted_in_file(csv_path) == (ROPE_107,)
    assert not first.editor.state.dirty

    second = make_editor(rope_frame(), auto_find=True)
    run_scan(second)
    load(second, csv_path)
    run_scan(second)

    editor = second.editor
    assert editor.state.ropes == [ROPE_46] and rope_lines(editor) == 1
    assert status(editor) == "Found 2 ropes, none new" and not editor.state.dirty
    assert "Skipped 1 rope found where you deleted one before" in notice(editor)


def test_a_false_rope_the_editor_found_is_dismissed_for_good_by_deleting_it_and_saving(make_editor, tmp_path):
    csv_path = map_with_saved_ropes(tmp_path, None)  # a map with no rope file yet
    rig = make_editor(rope_frame(), auto_find=True)
    run_scan(rig)
    load(rig, csv_path)
    run_scan(rig)
    editor = rig.editor
    assert editor.state.ropes == [ROPE_46, ROPE_107]

    select_and_delete(editor, 107, 52)
    assert editor.state.dirty and not rope_file_for(csv_path).exists()  # written only on Save
    editor._save()
    assert ropes.saved_ropes(csv_path) == (ROPE_46,) and deleted_in_file(csv_path) == (ROPE_107,)

    again = make_editor(rope_frame(), auto_find=True)
    run_scan(again)
    load(again, csv_path)
    run_scan(again)
    assert again.editor.state.ropes == [ROPE_46] and not again.editor.state.dirty


def test_the_sessions_deletions_count_before_any_save(make_editor, tmp_path):
    rig = make_editor(rope_frame(), auto_find=True)
    load(rig, map_with_saved_ropes(tmp_path, None))
    run_scan(rig)
    editor = rig.editor
    select_and_delete(editor, 107, 52)

    editor._findRopes()  # the button, pressed again after the deletion and before any Save
    run_scan(rig)

    assert editor.state.ropes == [ROPE_46]
    assert "Skipped 1 rope found where you deleted one before" in notice(editor)


def test_a_map_with_only_remembered_deletions_still_gets_a_rope_file(make_editor, tmp_path):
    csv_path = map_with_saved_ropes(tmp_path, None)
    rig = make_editor(rope_frame(), auto_find=True)
    load(rig, csv_path)
    run_scan(rig)
    select_and_delete(rig.editor, 107, 52)
    select_and_delete(rig.editor, 46, 38)
    assert rig.editor.state.ropes == []

    rig.editor._save()

    assert ropes.saved_ropes(csv_path) == () and set(deleted_in_file(csv_path)) == {ROPE_46, ROPE_107}


def test_deleting_a_rope_that_was_never_saved_or_found_remembers_nothing(make_editor, tmp_path):
    csv_path = map_with_saved_ropes(tmp_path, [ROPE_46])
    rig = make_editor()
    load(rig, csv_path)
    rig.editor._startAddRope()
    click(rig.editor, 60, 30)
    click(rig.editor, 60, 50)
    assert rig.editor.state.dirty
    rig.editor._deleteSelectedRope()  # a rope he marked by hand and removed again before any Save
    assert not rig.editor.state.dirty

    rig.editor._save()

    assert deleted_in_file(csv_path) == ()


def test_a_rope_marked_by_hand_where_a_deletion_is_remembered_clears_that_deletion(make_editor, tmp_path):
    csv_path = map_with_saved_ropes(tmp_path, [])
    write_ropes(rope_file_for(csv_path), RopeFile((), (FRAME_W, FRAME_H), {}, deleted=(ROPE_107,)))
    rig = make_editor(rope_frame(rope_46=False))
    editor = rig.editor
    load(rig, csv_path)
    assert not editor.state.dirty

    editor._startAddRope()
    click(editor, 108, 43)  # a click or two off: the remembered place is matched within the find's own tolerance
    click(editor, 108, 61)
    assert editor.state.dirty  # the file still remembers the deletion, so there is something to save
    editor._save()
    assert ropes.saved_ropes(csv_path) == (RopeMark(108, 43, 61),) and deleted_in_file(csv_path) == ()

    editor._findRopes()
    run_scan(rig)
    assert editor.state.ropes == [RopeMark(108, 43, 61)] and not editor.state.dirty
    assert notice(editor).startswith("Found 1 rope on the minimap, all already marked in this map.")

    select_and_delete(editor, 108, 52)  # now a saved rope, so deleting it is remembered again
    editor._save()
    assert deleted_in_file(csv_path) == (RopeMark(108, 43, 61),)


def test_the_find_ropes_button_leaves_out_remembered_deletions_and_says_how_many(make_editor, tmp_path):
    csv_path = map_with_saved_ropes(tmp_path, [ROPE_46])
    write_ropes(rope_file_for(csv_path), RopeFile((ROPE_46,), (FRAME_W, FRAME_H), {}, deleted=(ROPE_107,)))
    rig = make_editor(rope_frame())  # no automatic find: the button alone
    editor = rig.editor
    load(rig, csv_path)

    editor._findRopes()
    run_scan(rig)

    assert editor.state.ropes == [ROPE_46] and not editor.state.dirty
    assert notice(editor) == ("Found 2 ropes on the minimap, none added. Skipped 1 rope found where you deleted one before: "
                              "a deleted rope stays deleted. To bring one back, mark it with Add rope.\n" + HINT)


def test_a_found_rope_in_the_same_column_but_on_other_rows_is_not_the_deleted_one(make_editor, tmp_path):
    # the finder keeps ropes stacked in one column apart, so a deletion is matched by column and overlapping rows
    csv_path = map_with_saved_ropes(tmp_path, [])
    stacked_above = RopeMark(107, 5, 20)
    write_ropes(rope_file_for(csv_path), RopeFile((), (FRAME_W, FRAME_H), {}, deleted=(stacked_above,)))
    rig = make_editor(rope_frame(rope_46=False))
    load(rig, csv_path)

    rig.editor._findRopes()
    run_scan(rig)

    assert rig.editor.state.ropes == [ROPE_107]


def test_a_file_that_holds_a_rope_and_its_deletion_loads_clean(make_editor, tmp_path):
    csv_path = map_with_saved_ropes(tmp_path, [])
    write_ropes(rope_file_for(csv_path), RopeFile((ROPE_107,), (FRAME_W, FRAME_H), {}, deleted=(ROPE_107,)))
    rig = make_editor()
    load(rig, csv_path)
    assert rig.editor.state.ropes == [ROPE_107] and not rig.editor.state.dirty


def test_a_rope_moved_more_than_2_px_does_not_come_back_as_a_second_rope(make_editor, tmp_path):
    csv_path = map_with_saved_ropes(tmp_path, [ROPE_107])
    first = make_editor()
    load(first, csv_path)
    click(first.editor, 107, 52)
    ropes.type_in_box(first.editor, "x", "111")  # 4 px off the finder's column
    first.editor._save()
    moved = RopeMark(111, 43, 61)
    assert ropes.saved_ropes(csv_path) == (moved,) and deleted_in_file(csv_path) == (ROPE_107,)

    second = make_editor(rope_frame(rope_46=False), auto_find=True)
    run_scan(second)
    load(second, csv_path)
    run_scan(second)

    assert second.editor.state.ropes == [moved] and not second.editor.state.dirty  # no second rope beside his


def test_a_rope_moved_a_pixel_at_a_time_remembers_where_it_came_from(make_editor, tmp_path):
    # the Spinbox's arrows commit each step: 107 -> 111 is four moves, and the place it started from is what counts
    csv_path = map_with_saved_ropes(tmp_path, [ROPE_107])
    rig = make_editor()
    load(rig, csv_path)
    click(rig.editor, 107, 52)
    for x in (108, 109, 110, 111):
        ropes.type_in_box(rig.editor, "x", str(x))

    rig.editor._save()

    assert ropes.saved_ropes(csv_path) == (RopeMark(111, 43, 61),) and deleted_in_file(csv_path) == (ROPE_107,)


def test_a_move_of_2_px_or_less_remembers_nothing(make_editor, tmp_path):
    csv_path = map_with_saved_ropes(tmp_path, [ROPE_107])
    first = make_editor()
    load(first, csv_path)
    click(first.editor, 107, 52)
    ropes.type_in_box(first.editor, "x", "109")
    first.editor._save()
    assert ropes.saved_ropes(csv_path) == (RopeMark(109, 43, 61),) and deleted_in_file(csv_path) == ()

    second = make_editor(rope_frame(rope_46=False), auto_find=True)
    run_scan(second)
    load(second, csv_path)
    run_scan(second)
    assert second.editor.state.ropes == [RopeMark(109, 43, 61)] and not second.editor.state.dirty  # the add-only 2 px


def test_moving_a_rope_away_and_back_leaves_the_map_clean(make_editor, tmp_path):
    csv_path = map_with_saved_ropes(tmp_path, [ROPE_107])
    rig = make_editor()
    load(rig, csv_path)
    click(rig.editor, 107, 52)

    ropes.type_in_box(rig.editor, "x", "111")
    assert rig.editor.state.dirty
    ropes.type_in_box(rig.editor, "x", "107")

    assert rig.editor.state.ropes == [ROPE_107] and not rig.editor.state.dirty


# -- the find after a load only when the map says which minimap it was made on, and the live one is that size (AUTOFIND163 M3) --

def map_made_on(tmp_path, size, ropes_in_file=None, name="made_on.csv") -> Path:
    """A good map CSV whose `# minimap_size=` line says `size` (None: a legacy CSV with no such line), with a rope file of
    `ropes_in_file` beside it (None: no rope file)."""
    path = tmp_path / name
    nodes = {1: EditorNode(1, "blue_node", (20, 20))}
    assert write_editor_nodes(str(path), nodes, list(nodes), minimap_size=size).unwrap() is None
    if ropes_in_file is not None:
        write_ropes(rope_file_for(path), RopeFile(tuple(ropes_in_file), size, {}))
    return path


def build_the_canvas(rig):
    """The minimap frame arrives and the editor's own poll builds its canvas (the editor was made with no frame)."""
    rig.editor.sharedInfo.minimap_frame = rope_frame()
    waits = [item for item in rig.scheduled if item[1] == rig.editor._waitForMinimapAndBuildCanvas]
    waits[0][1](*waits[0][2])


def test_a_legacy_csv_with_no_minimap_size_gets_no_automatic_find_and_the_notice_says_why(make_editor, tmp_path):
    csv_path = map_made_on(tmp_path, None, ropes_in_file=[ROPE_46])
    rig = make_editor(rope_frame(), auto_find=True)
    editor = rig.editor
    run_scan(rig)  # the open-time find, on the untitled map

    load(rig, csv_path)

    assert pending_ticks(rig) == []  # a find would take the live minimap's ropes for a map that cannot say it is that one
    assert editor.state.ropes == [ROPE_46]  # the file's ropes, untouched
    assert notice_shown(editor) and notice(editor) == map_editor.ROPE_SKIP_NO_SIZE_TEXT
    assert "does not say which minimap size" in notice(editor) and "Find ropes" in notice(editor)
    assert not editor.state.dirty


def test_the_find_ropes_button_still_works_after_a_legacy_csv(make_editor, tmp_path):
    csv_path = map_made_on(tmp_path, None, ropes_in_file=[ROPE_46])
    rig = make_editor(rope_frame(), auto_find=True)
    run_scan(rig)
    load(rig, csv_path)

    rig.editor._findRopes()  # his own click, after he has read why the editor did not
    run_scan(rig)

    assert rig.editor.state.ropes == [ROPE_46, ROPE_107]


def test_a_map_made_on_the_live_minimap_size_still_gets_the_automatic_find(make_editor, tmp_path):
    csv_path = map_made_on(tmp_path, (FRAME_W, FRAME_H), ropes_in_file=[ROPE_46])
    rig = make_editor(rope_frame(), auto_find=True)
    run_scan(rig)

    load(rig, csv_path)
    assert len(pending_ticks(rig)) == 1 and status(rig.editor) == map_editor.FIND_ROPES_TEXT
    run_scan(rig)

    assert rig.editor.state.ropes == [ROPE_46, ROPE_107]


def test_a_legacy_csv_loaded_before_the_canvas_exists_gets_no_find_when_the_canvas_is_built(make_editor, tmp_path):
    csv_path = map_made_on(tmp_path, None)
    rig = make_editor(None, auto_find=True)
    load(rig, csv_path)

    build_the_canvas(rig)  # the one find per open is due, but not for this map

    assert pending_ticks(rig) == [] and notice(rig.editor) == map_editor.ROPE_SKIP_NO_SIZE_TEXT


def test_a_csv_made_on_another_minimap_size_loaded_before_the_canvas_exists_gets_no_find(make_editor, tmp_path):
    # with no canvas the load's size gate is skipped, so the file opens; the live minimap that appears is another size
    csv_path = map_made_on(tmp_path, (100, 50))
    rig = make_editor(None, auto_find=True)
    load(rig, csv_path)
    assert rig.editor.state.current_file == str(csv_path)

    build_the_canvas(rig)

    assert pending_ticks(rig) == [] and notice(rig.editor) == map_editor.ROPE_SKIP_OTHER_SIZE_TEXT
    assert "different minimap size" in notice(rig.editor) and "Find ropes" in notice(rig.editor)


def test_a_csv_made_on_the_live_size_loaded_before_the_canvas_exists_gets_the_find_when_it_is_built(make_editor, tmp_path):
    csv_path = map_made_on(tmp_path, (FRAME_W, FRAME_H), ropes_in_file=[ROPE_46])
    rig = make_editor(None, auto_find=True)
    load(rig, csv_path)

    build_the_canvas(rig)
    run_scan(rig)

    assert rig.editor.state.ropes == [ROPE_46, ROPE_107]
