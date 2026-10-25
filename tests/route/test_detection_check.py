"""Tests for src.route.harness.detection_check (plan task B18): the detection check tool.

Everything is synthetic: no real frame, no recording from `data/`. Recording folders are written by B17's `record`
(with a fake clock and sleep, so nothing waits), full-screen frames are built from the corner templates in
`assets/brds/`, and minimap frames are the B15 test frames (a (30, 30, 30) background with noise and grey lines, the
`assets/chr/` icon pasted where its mask is on). `src.vision` is never imported by the tests; the module imports
`vision_helper_lib` inside `crop_minimap` only.

Tests 1-6 are the plan's: `score` on hand-made dicts (1-5) and `load_frames` on a recording folder (6). The rest pin
what the plan leaves open: `score` edge cases, `crop_minimap`, `load_frames` on full-screen folders, `draft_labels`
and its contact sheets, the labels file, and `main`.
"""

import json
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

ASSETS = Path(__file__).resolve().parent.parent.parent / "assets"
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
    frames = result.unwrap()
    assert list(frames) == ["frame_000000.png"]
    assert frames["frame_000000.png"].shape == (6, 8, 3)
    assert (frames["frame_000000.png"] == (255, 0, 0)).all()


def test_10_a_recording_is_returned_sorted_by_name_and_ignores_files_that_are_not_pngs(tmp_path):
    frames = [_background(i, 20, 12) for i in range(12)]  # frame_000010.png sorts after frame_000009.png, not after _000001
    folder = _write_recording(tmp_path / "rec", frames)
    (folder / "notes.txt").write_text("not a frame", encoding="utf-8")
    result = load_frames(folder)
    assert is_successful(result)
    assert list(result.unwrap()) == _names(12)
    for i, frame in enumerate(frames):
        assert np.array_equal(result.unwrap()[f"frame_{i:06d}.png"], frame)


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


def test_15_load_frames_crops_a_full_screen_folder_converts_to_rgb_and_names_the_frame_it_cannot_crop(tmp_path):
    folder = tmp_path / "screens"
    folder.mkdir()
    frames, regions = zip(*(_full_screen(seed) for seed in (4, 5)))
    for i, frame in enumerate(frames):
        assert cv2.imwrite(str(folder / f"screen_{i}.png"), frame)
    result = load_frames(folder)
    assert is_successful(result)
    loaded = result.unwrap()
    assert list(loaded) == ["screen_0.png", "screen_1.png"]
    for i, (x1, y1, x2, y2) in enumerate(regions):
        assert np.array_equal(loaded[f"screen_{i}.png"], cv2.cvtColor(frames[i][y1:y2, x1:x2], cv2.COLOR_BGR2RGB))
    nothing = np.random.default_rng(9).integers(0, 256, size=(300, 400, 3), dtype=np.uint8)
    assert cv2.imwrite(str(folder / "screen_2.png"), nothing)
    result = load_frames(folder)
    assert isinstance(result, Failure) and "screen_2.png" in result.failure()


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
    assert draft_labels(folder, out) == Success(7)
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
    assert draft_labels(folder, out) == Success(51)
    sheets = sorted(p.name for p in (folder / "labels_draft_sheets").iterdir())
    assert sheets == ["sheet_00.png", "sheet_01.png"]
    columns, scale, caption = detection_check.SHEET_COLUMNS, detection_check.SCALE, detection_check.CAPTION_H
    first = cv2.imread(str(folder / "labels_draft_sheets" / "sheet_00.png"), cv2.IMREAD_COLOR)
    last = cv2.imread(str(folder / "labels_draft_sheets" / "sheet_01.png"), cv2.IMREAD_COLOR)
    assert first.shape == (10 * (caption + 20 * scale), columns * 30 * scale, 3)  # 50 cells in 5 columns x 10 rows
    assert last.shape == (caption + 20 * scale, 30 * scale, 3)                    # the 51st frame alone
    assert len(load_frames(folder).unwrap()) == 51                              # the sheets are not read as frames


def test_19_a_sheet_cell_is_the_frame_at_8x_with_a_cross_on_the_label_and_a_caption(tmp_path):
    frame = _paste(_background(1, 30, 20), 12, 4)
    folder = _write_recording(tmp_path / "rec", [frame])
    out = tmp_path / "labels_draft.json"
    assert draft_labels(folder, out) == Success(1)
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
