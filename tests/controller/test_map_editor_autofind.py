"""The map editor finds ropes by itself (AUTOFIND162).

Hong's words (verbatim): "the attempt to find ropes should ways be done no matter what and its implications should be
displayed fom the tget go as opposed to me needing to click find ropes". His four page answers (rope161-1 to -4):
the find adds only ropes not already saved (x within 2 px), saved ropes stay as they are, found ropes mark the editor
dirty and are written only on Save, and there is one find when the editor opens (and one after each successful load)
with a hint that a character standing still on a rope can hide it; the Find ropes button stays.

Like the other editor tests these build a real `MapEditor` on a real withdrawn Tk root (skipped where Tk cannot open a
display) with `after` recorded, and share the fixture and helpers of `test_map_editor_ropes.py`. The fixture's editors
have the automatic find off unless a test asks for it with `make(frame, auto_find=True)`.
"""
import inspect
from pathlib import Path

import numpy as np
import pytest

import src.gui.map_editor as map_editor
import test_map_editor_ropes as ropes
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


def test_a_deleted_rope_comes_back_on_the_next_open_and_marks_the_map_dirty(make_editor, tmp_path):
    # Hong accepted this (rope161-2): the rope file keeps no memory of what was deleted.
    csv_path = map_with_saved_ropes(tmp_path, [ROPE_46, ROPE_107])
    first = make_editor(rope_frame(), auto_find=True)
    run_scan(first)
    load(first, csv_path)
    run_scan(first)
    click(first.editor, 107, 52)
    assert first.editor.state.selected_rope == 1
    first.editor._deleteSelectedRope()
    first.editor._save()
    assert ropes.saved_ropes(csv_path) == (ROPE_46,) and not first.editor.state.dirty

    second = make_editor(rope_frame(), auto_find=True)
    run_scan(second)
    load(second, csv_path)
    assert second.editor.state.ropes == [ROPE_46]
    run_scan(second)

    assert second.editor.state.ropes == [ROPE_46, ROPE_107] and second.editor.state.dirty


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
        "select it and press Delete. Ropes are written only when you press Save.\n" + HINT)
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
