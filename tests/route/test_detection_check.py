"""Tests for src.route.harness.detection_check (plan task B18): the detection check tool.

Everything is synthetic: no real frame, no recording from `data/`. Recording folders are written by B17's `record`
(with a fake clock and sleep, so nothing waits), full-screen frames are built from the corner templates in
`assets/brds/`, and minimap frames are the B15 test frames (a (30, 30, 30) background with noise and grey lines, the
`assets/chr/` icon pasted where its mask is on). `src.vision` is never imported by the tests; the module imports
`vision_helper_lib` inside `crop_minimap` only.

Tests 1-6 are the plan's: `score` on hand-made dicts (1-5) and `load_frames` on a recording folder (6). The rest pin
what the plan leaves open: `score` edge cases, `crop_minimap`, `load_frames` on full-screen folders, `draft_labels`
and its contact sheets, the labels file, and `main`. Tests 26-41 are the B18 fix round: `score` on JSON-list labels, frames
that cannot be cropped (ruling B18-D1: skipped, reported, scored as nothing found), the draft's colour range and
rounding, and the import-isolation proof.
"""

import json
import re
import subprocess
import sys
import threading
from pathlib import Path

import cv2
import numpy as np
import pytest
from returns.pipeline import is_successful
from returns.result import Failure, Success

from src.route.harness import detection_check
from src.route.harness.detection_check import crop_minimap, draft_labels, load_frames, main, passed, score
from src.route.harness.record_minimap import record

REPO = Path(__file__).resolve().parents[2]
ASSETS = REPO / "assets"
FRAME_W, FRAME_H = 162, 82
LINE_ROWS = (28, 45, 60)


def _load_icon():
    icon_bgr = cv2.imread(str(ASSETS / "chr" / "chr.png"), cv2.IMREAD_COLOR)
    mask = cv2.imread(str(ASSETS / "chr" / "chr_mask.png"), cv2.IMREAD_GRAYSCALE)
    assert icon_bgr is not None and mask is not None
    return cv2.cvtColor(icon_bgr, cv2.COLOR_BGR2RGB), mask > 0


ICON_RGB, ICON_ON = _load_icon()
ICON_H, ICON_W = ICON_ON.shape
CENTRE = (ICON_W // 2, ICON_H // 2)  # top-left to the position CharacterLocator reports


def _background(seed: int, width: int = FRAME_W, height: int = FRAME_H) -> np.ndarray:
    noise = np.random.default_rng(seed).integers(-10, 11, size=(height, width, 1))
    frame = np.repeat(30 + noise, 3, axis=2).astype(np.uint8)
    if height == FRAME_H:
        frame[list(LINE_ROWS), :, :] = 120
    return frame


def _paste(frame: np.ndarray, x: int, y: int) -> np.ndarray:
    frame[y:y + ICON_H, x:x + ICON_W][ICON_ON] = ICON_RGB[ICON_ON]
    return frame


def _write_recording(folder: Path, frames: list[np.ndarray]) -> Path:
    """A B17 recording (frame_000000.png ... and index.jsonl) of `frames`, written by `record` itself."""
    stop = threading.Event()
    calls = []

    def feed() -> np.ndarray:
        calls.append(1)
        if len(calls) == len(frames):
            stop.set()
        return frames[len(calls) - 1]

    result = record(feed, folder, seconds=None, fps=10, clock=lambda: 0, sleep=lambda seconds: None, stop=stop)
    assert result == Success(len(frames))
    return folder


# ---- the plan's tests 1-5: score on hand-made dicts -------------------------------------------------------------------

def _names(count: int) -> list[str]:
    return [f"frame_{i:06d}.png" for i in range(count)]


def test_1_199_hits_and_1_miss_pass_at_exactly_the_limit():
    names = _names(200)
    labels = {name: (10 + i % 5, 20) for i, name in enumerate(names)}
    found = dict(labels)
    found[names[7]] = None
    result = score(found, labels)
    assert result == {"frames": 200, "hits": 199, "misses": 1, "wrong": 0, "hit_rate": 0.995, "max_err_px": 0.0}
    assert passed(result)


def test_1b_198_hits_and_2_misses_fail():
    names = _names(200)
    labels = {name: (10, 20) for name in names}
    found = dict(labels) | {names[0]: None, names[1]: None}
    result = score(found, labels)
    assert result["hit_rate"] == 0.99 and result["misses"] == 2 and result["wrong"] == 0
    assert not passed(result)


def test_2_a_position_4_px_away_is_wrong():
    result = score({"a": (14, 10)}, {"a": (10, 10)})
    assert result == {"frames": 1, "hits": 0, "misses": 0, "wrong": 1, "hit_rate": 0.0, "max_err_px": 4.0}
    assert not passed(result)


def test_3_a_2_px_error_is_neither_a_hit_nor_wrong():
    result = score({"a": (12, 10)}, {"a": (10, 10)})
    assert result == {"frames": 1, "hits": 0, "misses": 0, "wrong": 0, "hit_rate": 0.0, "max_err_px": 2.0}
    assert not passed(result)  # it counts against the hit rate only
    ok = score({"a": (12, 10)} | {f"b{i}": (5, 5) for i in range(199)}, {"a": (10, 10)} | {f"b{i}": (5, 5) for i in range(199)})
    assert ok["wrong"] == 0 and ok["hits"] == 199 and ok["hit_rate"] == 0.995 and passed(ok)


def test_4_a_position_found_on_an_absent_frame_is_wrong_and_the_frame_is_not_counted():
    result = score({"a": (14, 10)}, {"a": "absent"})
    assert result == {"frames": 0, "hits": 0, "misses": 0, "wrong": 1, "hit_rate": 0.0, "max_err_px": 0.0}
    assert not passed(result)


def test_5_absent_frames_with_nothing_found_do_not_change_the_rate():
    names = _names(200)
    labels = {name: (10, 20) for name in names}
    found = dict(labels) | {names[3]: None}
    labels |= {"gone_1": "absent", "gone_2": "absent", "gone_3": "absent"}
    found |= {"gone_1": None, "gone_2": None, "gone_3": None}
    result = score(found, labels)
    assert result == {"frames": 200, "hits": 199, "misses": 1, "wrong": 0, "hit_rate": 0.995, "max_err_px": 0.0}
    assert passed(result)


# ---- score, the edges --------------------------------------------------------------------------------------------------

def test_7_the_hit_box_is_1_px_per_axis_and_wrong_is_more_than_3_px_in_a_straight_line():
    labels = {name: (50, 50) for name in "abcdefg"}
    found = {"a": (51, 51), "b": (49, 49),   # +-1 on both axes: hits (the corner is 1.41 px away)
             "c": (52, 50),                  # 2 px on one axis: neither
             "d": (53, 50),                  # exactly 3 px: neither (wrong is more than 3)
             "e": (53, 51),                  # 3.16 px: wrong
             "f": (52, 52),                  # 2.83 px: neither
             "g": (54, 53)}                  # 5 px: wrong
    result = score(found, labels)
    assert (result["hits"], result["wrong"], result["misses"], result["frames"]) == (2, 2, 0, 7)
    assert result["max_err_px"] == 5.0


def test_8_unusable_frames_are_skipped_and_max_err_is_over_positioned_labels_with_a_position_found():
    labels = {"a": (10, 10), "b": None, "c": "absent", "d": (30, 30), "e": (40, 40)}
    found = {"a": (10, 11), "b": (999, 999), "c": None, "d": None, "e": (41, 40)}
    result = score(found, labels)
    # b (null) is skipped even with a position found; c is absent and found nothing; d is a miss with no error to report
    assert result == {"frames": 3, "hits": 2, "misses": 1, "wrong": 0, "hit_rate": 2 / 3, "max_err_px": 1.0}


def test_9_a_labelled_frame_the_locator_never_saw_is_a_miss_and_nothing_labelled_cannot_pass():
    assert score({}, {"a": (1, 1)})["misses"] == 1
    empty = score({"a": (1, 1)}, {})
    assert empty == {"frames": 0, "hits": 0, "misses": 0, "wrong": 0, "hit_rate": 0.0, "max_err_px": 0.0}
    assert not passed(empty)
    assert not passed(score({"a": None}, {"a": None}))


# ---- the plan's test 6 and the frame folders ---------------------------------------------------------------------------

def test_6_a_recording_is_loaded_as_rgb_and_never_cropped(tmp_path, monkeypatch):
    def must_not_crop(full_bgr):
        raise AssertionError("a B17 recording holds minimap crops already")

    monkeypatch.setattr(detection_check, "crop_minimap", must_not_crop)
    red = np.zeros((6, 8, 3), dtype=np.uint8)
    red[..., 0] = 255  # RGB red
    folder = _write_recording(tmp_path / "rec", [red])
    result = load_frames(folder)
    assert is_successful(result)
    frames = result.unwrap().crops
    assert result.unwrap().uncroppable == {}  # a recording is never cropped, so no frame can fail to crop
    assert list(frames) == ["frame_000000.png"]
    assert frames["frame_000000.png"].shape == (6, 8, 3)
    assert (frames["frame_000000.png"] == (255, 0, 0)).all()


def test_10_a_recording_is_returned_sorted_by_name_and_ignores_files_that_are_not_pngs(tmp_path):
    frames = [_background(i, 20, 12) for i in range(12)]  # frame_000010.png sorts after frame_000009.png, not after _000001
    folder = _write_recording(tmp_path / "rec", frames)
    (folder / "notes.txt").write_text("not a frame", encoding="utf-8")
    result = load_frames(folder)
    assert is_successful(result)
    crops = result.unwrap().crops
    assert list(crops) == _names(12)
    for i, frame in enumerate(frames):
        assert np.array_equal(crops[f"frame_{i:06d}.png"], frame)


def test_11_load_frames_fails_on_a_missing_folder_an_empty_folder_and_an_unreadable_png(tmp_path):
    assert isinstance(load_frames(tmp_path / "nowhere"), Failure)
    (tmp_path / "empty").mkdir()
    assert isinstance(load_frames(tmp_path / "empty"), Failure)
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "index.jsonl").write_text("", encoding="utf-8")
    (bad / "frame_000000.png").write_bytes(b"this is not a png")
    result = load_frames(bad)
    assert isinstance(result, Failure) and "frame_000000.png" in result.failure()


# ---- crop_minimap: a synthetic full-screen frame built from the corner templates ---------------------------------------

CORNER_TOP_LEFTS = {"left_top": (50, 40), "right_top": (220, 40), "right_bot": (220, 130), "left_bot": (50, 130)}


def _full_screen(seed: int = 0, fill: int | None = None) -> tuple[np.ndarray, tuple[int, int, int, int]]:
    """A 400 x 300 BGR frame with the four minimap corner borders pasted in, and the region crop_minimap should return.

    The templates are pasted with +-2 of noise: an exact copy can score 1.0000001 in float32, which vision.py scrubs.
    The region runs from the left-top corner's centre to the right-bottom corner's centre (vision.py:66-67).
    """
    rng = np.random.default_rng(seed)
    if fill is None:
        frame = rng.integers(0, 256, size=(300, 400, 3), dtype=np.uint8)
    else:
        frame = np.full((300, 400, 3), fill, dtype=np.uint8)
    sizes = {}
    for corner, (x, y) in CORNER_TOP_LEFTS.items():
        border = cv2.imread(str(ASSETS / "brds" / f"{corner}_grayscale.png"), cv2.IMREAD_GRAYSCALE)
        mask = cv2.imread(str(ASSETS / "brds" / f"{corner}_mask.png"), cv2.IMREAD_GRAYSCALE)
        assert border is not None and mask is not None
        height, width = border.shape
        value = np.clip(border.astype(int) + rng.integers(-2, 3, size=border.shape), 0, 255).astype(np.uint8)
        on = mask > 0
        frame[y:y + height, x:x + width][on] = value[on][:, None]  # equal channels: the grey is the template value
        sizes[corner] = (width, height)
    (x1, y1), (x2, y2) = CORNER_TOP_LEFTS["left_top"], CORNER_TOP_LEFTS["right_bot"]
    region = (x1 + sizes["left_top"][0] // 2, y1 + sizes["left_top"][1] // 2,
              x2 + sizes["right_bot"][0] // 2, y2 + sizes["right_bot"][1] // 2)
    return frame, region


def test_12_crop_minimap_returns_the_region_between_the_corner_centres_as_bgr():
    frame, (x1, y1, x2, y2) = _full_screen()
    result = crop_minimap(frame)
    assert is_successful(result)
    crop = result.unwrap()
    assert crop.shape == (y2 - y1, x2 - x1, 3) and crop.dtype == np.uint8
    assert np.array_equal(crop, frame[y1:y2, x1:x2])  # BGR, as it came in
    assert crop.base is None  # a copy: 300 kept crops must not keep 300 full-screen frames alive


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_13_crop_minimap_finds_the_same_region_on_other_backgrounds(seed):
    frame, (x1, y1, x2, y2) = _full_screen(seed)
    result = crop_minimap(frame)
    assert is_successful(result) and np.array_equal(result.unwrap(), frame[y1:y2, x1:x2])


def test_14_crop_minimap_fails_when_there_is_no_minimap_a_black_minimap_or_a_bad_frame():
    nothing = np.random.default_rng(0).integers(0, 256, size=(300, 400, 3), dtype=np.uint8)
    result = crop_minimap(nothing)
    assert isinstance(result, Failure) and "minimap" in result.failure()
    black, _ = _full_screen(fill=0)
    result = crop_minimap(black)
    assert isinstance(result, Failure) and "black" in result.failure()
    for bad in (np.zeros((300, 400), dtype=np.uint8), np.zeros((300, 400, 3), dtype=np.float32),
                np.zeros((5, 5, 3), dtype=np.uint8), None):
        assert isinstance(crop_minimap(bad), Failure)  # type: ignore[arg-type]


def test_15_load_frames_crops_a_full_screen_folder_converts_to_rgb_and_reports_the_frame_it_cannot_crop(tmp_path):
    folder = tmp_path / "screens"
    folder.mkdir()
    frames, regions = zip(*(_full_screen(seed) for seed in (4, 5)))
    for i, frame in enumerate(frames):
        assert cv2.imwrite(str(folder / f"screen_{i}.png"), frame)
    result = load_frames(folder)
    assert is_successful(result)
    loaded = result.unwrap()
    assert list(loaded.crops) == ["screen_0.png", "screen_1.png"] and loaded.uncroppable == {}
    for i, (x1, y1, x2, y2) in enumerate(regions):
        assert np.array_equal(loaded.crops[f"screen_{i}.png"], cv2.cvtColor(frames[i][y1:y2, x1:x2], cv2.COLOR_BGR2RGB))
    nothing = np.random.default_rng(9).integers(0, 256, size=(300, 400, 3), dtype=np.uint8)
    assert cv2.imwrite(str(folder / "screen_2.png"), nothing)
    result = load_frames(folder)  # B18-D1: a frame with no minimap on screen is skipped and reported, not a failure
    assert is_successful(result)
    assert list(result.unwrap().crops) == ["screen_0.png", "screen_1.png"]
    assert list(result.unwrap().uncroppable) == ["screen_2.png"] and "minimap" in result.unwrap().uncroppable["screen_2.png"]


# ---- draft_labels and the contact sheets -------------------------------------------------------------------------------

POSITIONS = [(0, 0), (152, 72), (10, 40), (77, 31), (120, 5)]  # icon top-lefts; the whole icon is inside the frame


def _draft_frames() -> list[np.ndarray]:
    frames = [_paste(_background(i), x, y) for i, (x, y) in enumerate(POSITIONS)]
    frames.append(_background(8))                                                # no character
    frames.append(_paste(_paste(_background(9), 10, 10), 100, 50))               # two of them
    return frames


def test_16_draft_labels_puts_each_label_where_the_locator_would_report_the_icon(tmp_path):
    folder = _write_recording(tmp_path / "rec", _draft_frames())
    out = tmp_path / "out" / "labels_draft.json"
    assert draft_labels(folder, out) == Success((7, {}))
    expected = {f"frame_{i:06d}.png": [x + CENTRE[0], y + CENTRE[1]] for i, (x, y) in enumerate(POSITIONS)}
    expected["frame_000005.png"] = "absent"  # no blob at all
    expected["frame_000006.png"] = None      # two blobs: the draft cannot tell which is the character
    assert json.loads(out.read_text(encoding="utf-8")) == expected
    assert list(json.loads(out.read_text(encoding="utf-8"))) == _names(7)


def test_17_draft_labels_refuses_to_overwrite_a_file_and_writes_nothing_on_a_failure(tmp_path):
    folder = _write_recording(tmp_path / "rec", _draft_frames()[:2])
    out = tmp_path / "labels.json"
    out.write_text("hong's labels", encoding="utf-8")
    assert isinstance(draft_labels(folder, out), Failure)
    assert out.read_text(encoding="utf-8") == "hong's labels"
    assert not (tmp_path / "labels_sheets").exists()
    fresh = tmp_path / "fresh" / "labels_draft.json"
    assert isinstance(draft_labels(tmp_path / "nowhere", fresh), Failure)
    assert not fresh.exists() and not fresh.parent.exists()


def test_18_the_contact_sheets_hold_50_frames_each_in_a_folder_the_frame_reader_does_not_read(tmp_path):
    frames = [_paste(_background(i, 30, 20), 10 + i % 10, 5) for i in range(51)]
    folder = _write_recording(tmp_path / "rec", frames)
    out = folder / "labels_draft.json"  # as in the plan: the draft is written into the frames folder
    assert draft_labels(folder, out) == Success((51, {}))
    sheets = sorted(p.name for p in (folder / "labels_draft_sheets").iterdir())
    assert sheets == ["sheet_00.png", "sheet_01.png"]
    columns, scale, caption = detection_check.SHEET_COLUMNS, detection_check.SCALE, detection_check.CAPTION_H
    first = cv2.imread(str(folder / "labels_draft_sheets" / "sheet_00.png"), cv2.IMREAD_COLOR)
    last = cv2.imread(str(folder / "labels_draft_sheets" / "sheet_01.png"), cv2.IMREAD_COLOR)
    assert first.shape == (10 * (caption + 20 * scale), columns * 30 * scale, 3)  # 50 cells in 5 columns x 10 rows
    assert last.shape == (caption + 20 * scale, 30 * scale, 3)                    # the 51st frame alone
    assert len(load_frames(folder).unwrap().crops) == 51                            # the sheets are not read as frames


def test_19_a_sheet_cell_is_the_frame_at_8x_with_a_cross_on_the_label_and_a_caption(tmp_path):
    frame = _paste(_background(1, 30, 20), 12, 4)
    folder = _write_recording(tmp_path / "rec", [frame])
    out = tmp_path / "labels_draft.json"
    assert draft_labels(folder, out) == Success((1, {}))
    sheet = cv2.cvtColor(cv2.imread(str(tmp_path / "labels_draft_sheets" / "sheet_00.png"), cv2.IMREAD_COLOR),
                         cv2.COLOR_BGR2RGB)
    scale, caption = detection_check.SCALE, detection_check.CAPTION_H
    label_x, label_y = 12 + CENTRE[0], 4 + CENTRE[1]
    cross = tuple(detection_check.CROSS_RGB)
    assert tuple(sheet[caption + label_y * scale + scale // 2, label_x * scale + scale // 2]) == cross
    assert tuple(sheet[caption + label_y * scale + scale // 2, (label_x + 1) * scale + scale // 2]) == cross  # an arm
    # elsewhere the cell is the frame, each pixel scaled to scale x scale: pixel (2, 15) is background
    assert tuple(sheet[caption + 15 * scale + 3, 2 * scale + 5]) == tuple(frame[15, 2])
    assert sheet[:caption].any()  # the caption (file name and label) is drawn above the cell


# ---- the labels file and check -----------------------------------------------------------------------------------------

def _labels_file(tmp_path: Path, labels: dict) -> Path:
    path = tmp_path / "labels.json"
    path.write_text(json.dumps(labels), encoding="utf-8")
    return path


def _check(folder: Path, labels_path: Path) -> int:
    return main(["check", "--frames", str(folder), "--labels", str(labels_path)])


def _positions(count: int = 8) -> list[tuple[int, int]]:
    rng = np.random.default_rng(3)
    return [(int(rng.integers(0, FRAME_W - ICON_W + 1)), int(rng.integers(0, FRAME_H - ICON_H + 1))) for _ in range(count)]


def _located_frames(positions) -> list[np.ndarray]:
    return [_paste(_background(i), x, y) for i, (x, y) in enumerate(positions)]


def _exact_labels(positions) -> dict:
    return {name: [x + CENTRE[0], y + CENTRE[1]] for name, (x, y) in zip(_names(len(positions)), positions)}


def test_20_check_passes_when_the_locator_finds_every_labelled_position(tmp_path, capsys):
    positions = _positions()
    folder = _write_recording(tmp_path / "rec", _located_frames(positions))
    labels = _exact_labels(positions) | {"frame_000003.png": None}  # an unusable frame is skipped
    assert _check(folder, _labels_file(tmp_path, labels)) == 0
    shown = capsys.readouterr().out
    assert "PASS" in shown and "hits 7" in shown and "frames 7" in shown


def test_21_check_fails_on_a_miss_a_wrong_position_or_a_position_on_an_absent_frame(tmp_path, capsys):
    positions = _positions()
    folder = _write_recording(tmp_path / "rec", _located_frames(positions) + [_background(40)])
    exact = _exact_labels(positions) | {"frame_000008.png": "absent"}      # the last frame has no character
    on_empty_frame = exact | {"frame_000008.png": [60, 40]}           # the locator reports a miss there: 8 hits of 9
    four_px_off = exact | {"frame_000002.png": [exact["frame_000002.png"][0] + 4, exact["frame_000002.png"][1]]}
    absent_but_there = exact | {"frame_000001.png": "absent"}
    assert _check(folder, _labels_file(tmp_path, exact)) == 0
    assert _check(folder, _labels_file(tmp_path, on_empty_frame)) == 1
    assert "frame_000008.png" in capsys.readouterr().out  # the failing frame is named
    assert _check(folder, _labels_file(tmp_path, four_px_off)) == 1
    assert "frame_000002.png" in capsys.readouterr().out
    assert _check(folder, _labels_file(tmp_path, absent_but_there)) == 1
    assert "frame_000001.png" in capsys.readouterr().out


def test_22_check_refuses_labels_that_do_not_match_the_frames_or_the_format(tmp_path):
    positions = _positions(3)
    folder = _write_recording(tmp_path / "rec", _located_frames(positions))
    exact = _exact_labels(positions)
    missing_a_frame = {name: value for name, value in exact.items() if name != "frame_000001.png"}
    for bad in (missing_a_frame, exact | {"frame_000009.png": [1, 1]},                 # a frame without a label, a label without a frame
                exact | {"frame_000000.png": [1.5, 2]}, exact | {"frame_000000.png": [1]},
                exact | {"frame_000000.png": "present"}, exact | {"frame_000000.png": True}, ["frame_000000.png"]):
        assert _check(folder, _labels_file(tmp_path, bad)) == 2, bad
    notjson = tmp_path / "notjson.json"
    notjson.write_text("{not json", encoding="utf-8")
    assert _check(folder, notjson) == 2
    assert _check(folder, tmp_path / "no_such_labels.json") == 2
    assert _check(tmp_path / "no_such_folder", _labels_file(tmp_path, exact)) == 2


def test_23_main_needs_a_sub_command_and_its_arguments():
    for argv in ([], ["draft"], ["draft", "--frames", "x"], ["check", "--frames", "x"], ["check", "--labels", "y"], ["other"]):
        with pytest.raises(SystemExit) as raised:
            main(argv)
        assert raised.value.code == 2, argv


def test_24_draft_writes_only_inside_data(tmp_path, monkeypatch):
    folder = _write_recording(tmp_path / "rec", _located_frames(_positions(2)))
    data = tmp_path / "data"
    monkeypatch.setattr(detection_check, "_DATA", data)
    outside = tmp_path / "labels_draft.json"
    assert main(["draft", "--frames", str(folder), "--out", str(outside)]) == 2
    assert not outside.exists() and not data.exists()
    assert main(["draft", "--frames", str(folder), "--out", str(data / "x" / "labels_draft.json")]) == 0
    assert (data / "x" / "labels_draft.json").is_file() and (data / "x" / "labels_draft_sheets" / "sheet_00.png").is_file()
    assert main(["draft", "--frames", str(folder), "--out", str(data / "x" / "labels_draft.json")]) == 2  # already there


def test_25_the_drafted_labels_pass_check_on_the_same_frames(tmp_path, monkeypatch):
    positions = _positions()
    folder = _write_recording(tmp_path / "rec", _located_frames(positions) + [_background(40)])
    monkeypatch.setattr(detection_check, "_DATA", tmp_path / "data")
    draft = tmp_path / "data" / "labels_draft.json"
    assert main(["draft", "--frames", str(folder), "--out", str(draft)]) == 0
    labels = tmp_path / "labels.json"  # what Hong saves after checking the sheets
    labels.write_text(draft.read_text(encoding="utf-8"), encoding="utf-8")
    assert json.loads(labels.read_text(encoding="utf-8"))["frame_000008.png"] == "absent"
    assert _check(folder, labels) == 0


# ---- score takes a label as JSON gives it (a list), not only a tuple ---------------------------------------------------

def test_26_score_accepts_json_style_list_labels():
    labels = {"a.png": [20, 20], "b.png": [40, 40], "x.png": "absent", "y.png": None}
    found = {"a.png": (20, 21), "b.png": (44, 40), "x.png": None, "y.png": (1, 1)}
    result = score(found, labels)
    assert result == {"frames": 2, "hits": 1, "misses": 0, "wrong": 1, "hit_rate": 0.5, "max_err_px": 4.0}
    assert score(found, {name: tuple(label) if isinstance(label, list) else label for name, label in labels.items()}) == result


def test_27_a_list_label_the_locator_missed_is_a_counted_miss():
    result = score({"a.png": None}, {"a.png": [20, 20]})
    assert result["frames"] == 1 and result["misses"] == 1 and result["hits"] == 0 and result["hit_rate"] == 0.0


# ---- the 4-decimal scores in a crop failure ----------------------------------------------------------------------------

def test_28_a_crop_failure_prints_the_corner_scores_with_4_decimals():
    nothing = np.random.default_rng(0).integers(0, 256, size=(300, 400, 3), dtype=np.uint8)
    result = crop_minimap(nothing)
    assert isinstance(result, Failure)
    assert re.search(r"left_top -?\d+\.\d{4}[,;)]", result.failure()), result.failure()  # 0.9997 is not shown as 1.000


# ---- frames that cannot be cropped (B18-D1): skipped, reported, scored as nothing found --------------------------------

SCREEN_MARGIN = 14  # px of the crop left as it is around the panel: the corner borders reach about this far into the region
SCREEN_ICONS = {"screen_0.png": (30, 20), "screen_1.png": (80, 30), "screen_2.png": (120, 50), "screen_3.png": (50, 60)}
COVERED = "screen_1_covered.png"  # sorts between screen_1.png and screen_2.png


def _screen(seed: int, icon_at: tuple[int, int]) -> np.ndarray:
    """A full-screen BGR frame whose minimap crop holds a quiet B15-style panel with the icon's top-left at `icon_at`."""
    frame, (x1, y1, x2, y2) = _full_screen(seed)
    panel = _paste(_background(seed, x2 - x1, y2 - y1), *icon_at)
    m = SCREEN_MARGIN
    frame[y1 + m:y2 - m, x1 + m:x2 - m] = panel[m:-m, m:-m, ::-1]  # RGB to BGR
    return frame


def _covered(seed: int = 9) -> np.ndarray:
    """A full-screen frame with no minimap on it: another window covers the game."""
    return np.random.default_rng(seed).integers(0, 256, size=(300, 400, 3), dtype=np.uint8)


def _screens(tmp_path: Path, *, covered: bool = True) -> Path:
    folder = tmp_path / "screens"
    folder.mkdir(parents=True)
    for i, (name, at) in enumerate(SCREEN_ICONS.items()):
        assert cv2.imwrite(str(folder / name), _screen(20 + i, at))
    if covered:
        assert cv2.imwrite(str(folder / COVERED), _covered())
    return folder


def _screen_labels() -> dict:
    return {name: [x + CENTRE[0], y + CENTRE[1]] for name, (x, y) in SCREEN_ICONS.items()}


def _screen_crop_size() -> tuple[int, int]:
    _, (x1, y1, x2, y2) = _full_screen()
    return x2 - x1, y2 - y1


def test_29_load_frames_fails_when_no_frame_in_a_full_screen_folder_can_be_cropped(tmp_path):
    folder = tmp_path / "covered"
    folder.mkdir()
    for i in range(3):
        assert cv2.imwrite(str(folder / f"shot_{i}.png"), _covered(i))
    result = load_frames(folder)
    assert isinstance(result, Failure)
    assert "3 of 3" in result.failure() and "shot_0.png" in result.failure() and "minimap" in result.failure()


def test_30_load_frames_fails_on_missing_corner_images_and_on_an_unreadable_png_in_a_full_screen_folder(tmp_path, monkeypatch):
    folder = _screens(tmp_path, covered=False)
    with monkeypatch.context() as patched:
        patched.setattr(detection_check, "_corner_templates", lambda: Failure("cannot read the left_top border images"))
        result = load_frames(folder)  # not a frame problem: nothing would crop, so it must not look like 4 skipped frames
        assert isinstance(result, Failure) and "border images" in result.failure()
    (folder / "screen_9_broken.png").write_bytes(b"this is not a png")
    result = load_frames(folder)  # a file defect, not what the screen showed: also a failure of the whole run
    assert isinstance(result, Failure) and "screen_9_broken.png" in result.failure()


def test_31_draft_labels_marks_an_uncroppable_frame_null_and_draws_no_cell_for_it(tmp_path):
    folder = _screens(tmp_path)
    out = tmp_path / "labels_draft.json"
    result = draft_labels(folder, out)
    assert is_successful(result)
    count, uncroppable = result.unwrap()
    assert count == 5 and list(uncroppable) == [COVERED] and "minimap" in uncroppable[COVERED]
    labels = json.loads(out.read_text(encoding="utf-8"))
    assert list(labels) == sorted([*SCREEN_ICONS, COVERED])  # every PNG, in name order
    assert labels == _screen_labels() | {COVERED: None}
    width, height = _screen_crop_size()
    scale, caption, columns = detection_check.SCALE, detection_check.CAPTION_H, 4
    sheet = cv2.imread(str(tmp_path / "labels_draft_sheets" / "sheet_00.png"), cv2.IMREAD_COLOR)
    assert sheet.shape == (caption + height * scale, columns * width * scale, 3)  # 4 cells in a row, none for the covered frame


def test_32_draft_prints_the_uncroppable_count_and_each_frame_with_its_reason(tmp_path, monkeypatch, capsys):
    folder = _screens(tmp_path)
    monkeypatch.setattr(detection_check, "_DATA", tmp_path / "data")
    out = tmp_path / "data" / "labels_draft.json"
    assert main(["draft", "--frames", str(folder), "--out", str(out)]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("5 draft labels in ")
    assert lines[1] == "uncroppable: 1 of 5 frames, labelled null, not on the sheets"
    assert lines[2].startswith(f"  {COVERED}: ") and "minimap" in lines[2]
    assert lines[3].startswith("Next:")
    other = _screens(tmp_path / "again", covered=False)
    assert main(["draft", "--frames", str(other), "--out", str(tmp_path / "data" / "b.json")]) == 0
    assert "uncroppable: 0 of 4 frames" in capsys.readouterr().out


def test_33_draft_lists_at_most_20_uncroppable_frames(tmp_path, monkeypatch, capsys):
    folder = _screens(tmp_path, covered=False)
    for i in range(23):
        assert cv2.imwrite(str(folder / f"covered_{i:02d}.png"), _covered(i))
    monkeypatch.setattr(detection_check, "_DATA", tmp_path / "data")
    assert main(["draft", "--frames", str(folder), "--out", str(tmp_path / "data" / "labels_draft.json")]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[1] == "uncroppable: 23 of 27 frames, labelled null, not on the sheets"
    assert sum(line.startswith("  covered_") for line in lines) == 20 and "  ... and 3 more" in lines


def test_34_draft_fails_and_writes_nothing_when_no_frame_can_be_cropped(tmp_path, monkeypatch, capsys):
    folder = tmp_path / "covered"
    folder.mkdir()
    for i in range(2):
        assert cv2.imwrite(str(folder / f"shot_{i}.png"), _covered(i))
    monkeypatch.setattr(detection_check, "_DATA", tmp_path / "data")
    assert main(["draft", "--frames", str(folder), "--out", str(tmp_path / "data" / "labels_draft.json")]) == 2
    assert "2 of 2" in capsys.readouterr().err
    assert not (tmp_path / "data").exists()


@pytest.mark.parametrize("label, exit_code", [(None, 0), ("absent", 0), ([40, 40], 1)])
def test_35_check_scores_an_uncroppable_frame_as_nothing_found(tmp_path, capsys, label, exit_code):
    folder = _screens(tmp_path)
    assert _check(folder, _labels_file(tmp_path, _screen_labels() | {COVERED: label})) == exit_code
    shown = capsys.readouterr().out
    assert "uncroppable: 1 of 5 frames (scored as nothing found)" in shown
    if label == [40, 40]:  # a labelled frame the check could not even look at is a miss, with the reason
        assert f"{COVERED}: label [40, 40], locator uncroppable: " in shown and "(miss)" in shown and "FAIL" in shown
    else:
        assert "PASS" in shown and "frames 4, hits 4" in shown


def test_36_check_refuses_labels_that_leave_out_an_uncroppable_frame(tmp_path):
    folder = _screens(tmp_path)
    assert _check(folder, _labels_file(tmp_path, _screen_labels())) == 2  # a frame without a label, even if it cannot be cropped
    assert _check(folder, _labels_file(tmp_path, _screen_labels() | {COVERED: None, "other.png": None})) == 2


def test_37_the_drafted_labels_pass_check_on_a_folder_with_an_uncroppable_frame(tmp_path, monkeypatch, capsys):
    folder = _screens(tmp_path)
    monkeypatch.setattr(detection_check, "_DATA", tmp_path / "data")
    draft = tmp_path / "data" / "labels_draft.json"
    assert main(["draft", "--frames", str(folder), "--out", str(draft)]) == 0
    labels = tmp_path / "labels.json"
    labels.write_text(draft.read_text(encoding="utf-8"), encoding="utf-8")
    capsys.readouterr()
    assert _check(folder, labels) == 0
    assert "PASS" in capsys.readouterr().out


# ---- the draft's dot colour and rounding -------------------------------------------------------------------------------

def test_38_a_dimmer_capture_of_the_icon_is_still_drafted_at_its_position(tmp_path):
    frames = []
    for i, factor in enumerate((1.0, 0.8, 0.7)):  # at 80 % brightness the icon's RGB is far outside the original's per-channel box
        icon = np.clip(ICON_RGB.astype(float) * factor, 0, 255).astype(np.uint8)
        frame = _background(i)
        frame[10:10 + ICON_H, 20:20 + ICON_W][ICON_ON] = icon[ICON_ON]
        frames.append(frame)
    folder = _write_recording(tmp_path / "rec", frames)
    out = tmp_path / "labels_draft.json"
    assert draft_labels(folder, out) == Success((3, {}))
    assert json.loads(out.read_text(encoding="utf-8")) == {name: [20 + CENTRE[0], 10 + CENTRE[1]] for name in _names(3)}


def test_39_the_dark_outline_alone_and_the_maps_greys_are_not_the_dot(tmp_path):
    ring = ICON_ON & (ICON_RGB.max(axis=2) < 100)  # the icon's dark outline, none of its yellow
    assert ring.any()
    outline_only = _background(0)
    outline_only[20:20 + ICON_H, 50:50 + ICON_W][ring] = ICON_RGB[ring]
    folder = _write_recording(tmp_path / "rec", [outline_only, _background(1)])
    out = tmp_path / "labels_draft.json"
    assert draft_labels(folder, out) == Success((2, {}))
    assert json.loads(out.read_text(encoding="utf-8")) == {name: "absent" for name in _names(2)}


def test_40_a_label_halfway_between_two_pixels_rounds_up_like_the_locators_integer_centre(tmp_path):
    dot = ICON_RGB[ICON_H // 2 - 1, ICON_W // 2 - 1]  # a pixel of the dot itself
    assert dot[0] > 200 and dot[1] > 200 and dot[2] < 60
    frames = []
    for centre in ((10, 10), (11, 11)):  # a 5 x 5 patch has its centroid on a pixel; the dot's offset adds 0.5
        frame = _background(0)
        frame[centre[1] - 2:centre[1] + 3, centre[0] - 2:centre[0] + 3] = dot
        frames.append(frame)
    folder = _write_recording(tmp_path / "rec", frames)
    out = tmp_path / "labels_draft.json"
    assert draft_labels(folder, out) == Success((2, {}))
    assert json.loads(out.read_text(encoding="utf-8")) == {"frame_000000.png": [11, 11], "frame_000001.png": [12, 12]}  # round(): 10


# ---- imports -----------------------------------------------------------------------------------------------------------

@pytest.mark.slow
def test_41_importing_the_module_and_cropping_a_frame_never_import_src_vision_vision_or_bettercam():
    code = (
        "import sys; sys.path.insert(0, '.')\n"
        "import numpy as np\n"
        "import src.route.harness.detection_check as dc\n"
        "def leaked(): return sorted(m for m in sys.modules if m == 'bettercam' or m.startswith('src.vision'))\n"
        "assert leaked() == [], leaked()  # at import: nothing\n"
        "dc.crop_minimap(np.zeros((300, 400, 3), np.uint8))\n"
        "assert 'src.vision.vision_helper_lib' in sys.modules\n"
        "assert 'src.vision.vision' not in sys.modules and 'bettercam' not in sys.modules, leaked()\n")
    done = subprocess.run([sys.executable, "-I", "-c", code], cwd=REPO, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr
