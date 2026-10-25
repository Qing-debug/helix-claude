"""Detection check on real frames (plan task B18): labels drafted by Claude, checked by Hong, scored against the locator.

    python -m src.route.harness.detection_check draft --frames DIR --out data/.../labels_draft.json
    python -m src.route.harness.detection_check check --frames DIR --labels DIR/labels.json

Run from the repo root. `DIR` is either a B17 recording (`record_minimap`; it has an `index.jsonl`, and its PNGs are
minimap crops already) or a folder of full-screen frames (PNGs of the whole monitor, such as
`data/recordings/ff2_2026-10-21/`). `load_frames` is the one reader of both. Neither command sends a key.

`draft` finds the character in each frame by a method that is independent of `CharacterLocator`: the centroid of the
blob of the dot's colour. It writes `labels_draft.json` (`{file: [x, y] | "absent" | null}`, one line per frame, in file
order) and, beside it, a folder `<name>_sheets/` with one contact sheet per 50 frames: every minimap at 8x with a 1-px
cross at the label and the file name and label above it (a frame that could not be cropped is `null` and has no cell, see
below). Human step: Hong looks at the sheets, corrects any label, marks a
frame unusable (`null`) or without a character (`"absent"`), and saves the result as `labels.json`. Nothing here
writes `labels.json`. `check` runs the locator on every frame and scores it against that file (`score`); it exits 0 iff
the hit rate is at least 0.995 and no position is wrong (spec B:L67).

Where the plan is silent:
- The colour range is that of the icon's inside, in HSV so that a brighter or dimmer capture still falls in it: the
  pixels `assets/chr/chr_mask.png` keeps (mask > 0), minus the one-pixel ring at their edge (the dark outline, which the
  map's own dark pixels share). Hue runs from the lowest to the highest of those pixels' hues with `HUE_SLACK` either
  side; saturation and value run from their lowest, less `SAT_SLACK` and `VAL_SLACK`, up to 255. A blob is a connected
  group (8-neighbours) of in-range pixels with at least half the area the icon's own dot has. One blob gives a position;
  none gives `"absent"`; two or more give `null`, because the draft cannot tell which is the character (Hong decides on
  the sheet).
- The position is the blob's centroid, moved by the offset that the icon's own dot has from `(w // 2, h // 2)` and rounded
  half up, so that a perfectly drawn icon is labelled with exactly the position `CharacterLocator` reports for it.
- `draft` refuses to overwrite `--out` (a rerun would otherwise be able to replace Hong's file if he named it so); it
  writes the sheets first and the labels file last, so a failure part-way leaves no labels file. `--out` must resolve to a
  file inside the repo's gitignored `data/` (recordings, labels and sheets are never committed).
- A full-screen frame whose minimap cannot be cropped (another window covers the game, the minimap is black, no corner
  border is found: ruling B18-D1) is skipped and reported, never dropped quietly: `load_frames` returns it in
  `uncroppable` with the reason, `draft` labels it `null` and prints it, and `check` scores it as nothing found, so an
  `[x, y]` label on it is a miss and skipping can only lower the hit rate. A PNG that cannot be read, corner border
  images that cannot be read, and a folder in which no frame can be cropped still fail the whole run. `check` also
  refuses labels whose file names differ from the frames' (a label for a missing frame, or a frame without a label; an
  uncroppable frame needs a label too), for the same reason. `null` is how a frame is marked unusable.
- Exit codes: 0 done / PASS; 1 `check` ran and did not pass; 2 could not run (bad arguments, `--out` outside `data/`,
  unreadable frames or labels, no frame that could be cropped, a file that would be overwritten).

Imports: `vision_helper_lib` (cv2, numpy and enum only; the corner-border helpers of `src/vision/vision.py`) is imported
inside `crop_minimap`, never at module level (plan hard rule 9). `src/vision/vision.py` itself needs bettercam and is
never imported.
"""

import argparse
import contextlib
import io
import json
import math
import sys
from collections import Counter
from collections.abc import Mapping
from functools import cache
from pathlib import Path
from typing import Literal, NamedTuple

import cv2
import numpy as np
from returns.pipeline import is_successful
from returns.result import Failure, Result, Success

from src.route.character import CharacterLocator
from src.route.harness.record_minimap import INDEX_NAME

Label = tuple[int, int] | Literal["absent"] | None
JsonLabel = Label | list[int]  # a label as `json.loads` gives it: `score` takes `[x, y]` as well as `(x, y)`

_REPO = Path(__file__).resolve().parents[3]
_DATA = _REPO / "data"
_BORDERS = _REPO / "assets" / "brds"
_CHR = _REPO / "assets" / "chr"

# The corner order and the match threshold of src/vision/vision.py:25 and :15.
_CORNERS = ["left_top", "right_top", "right_bot", "left_bot"]
_BORDER_CONFIDENCE = 0.95

HIT_PX = 1             # found within this many px on both axes: a hit
WRONG_PX = 3           # found more than this many px away in a straight line: wrong (spec "never reported > 3 px away")
MIN_HIT_RATE = 0.995   # spec B:L67

HUE_SLACK = 4          # hue steps (OpenCV 0-179) added to each side of the dot's hue range
SAT_SLACK = 30         # saturation levels below the dot's lowest: takes in its anti-aliased edge (S 228 beside a core of 253)
VAL_SLACK = 90         # value levels below the dot's lowest: takes in that edge (V 149 beside a core of 238) and a dimmer capture
MAX_LISTED = 20        # lines of one list printed before "... and N more"
SHEET_SIZE = 50        # frames per contact sheet
SHEET_COLUMNS = 5
SCALE = 8              # contact sheet zoom
CAPTION_H = 28         # px above each cell for the file name and label
CROSS_ARM = 12         # px of the cross on each side of the label, on the sheet
CROSS_RGB = (0, 255, 255)  # cyan: the dot is yellow and the map is dark


class _Match(NamedTuple):
    """What `vision_helper_lib` reads of a template match (`MatchedTemplateInfo` in src/vision/vision.py)."""
    x: int
    y: int
    conf_score: float


@cache
def _corner_templates() -> Result[dict[str, tuple[np.ndarray, np.ndarray]], str]:
    """The grayscale border and its mask of each corner, as vision.py:38-40 reads them."""
    templates = {}
    for corner in _CORNERS:
        border = cv2.imread(str(_BORDERS / f"{corner}_grayscale.png"), cv2.IMREAD_GRAYSCALE)
        mask = cv2.imread(str(_BORDERS / f"{corner}_mask.png"), cv2.IMREAD_GRAYSCALE)
        if border is None or mask is None:
            return Failure(f"cannot read the {corner} border images in {_BORDERS}")
        templates[corner] = (border, mask)
    return Success(templates)


def crop_minimap(full_bgr: np.ndarray) -> Result[np.ndarray, str]:
    """The minimap region of a full-screen BGR frame, as BGR: the steps of vision.py:46-74, via `vision_helper_lib`.

    Masked `TM_CCORR_NORMED` of each corner border over the whole frame (inf, nan and scores above 1 scrubbed), the
    validity check of `isMatchedTemplateLocValid`, the region from the left-top and right-bottom corner centres, and the
    region and black checks. The returned array is a copy, so a kept crop does not keep its full-screen frame alive.
    """
    if not (isinstance(full_bgr, np.ndarray) and full_bgr.dtype == np.uint8 and full_bgr.ndim == 3
            and full_bgr.shape[2] == 3):
        return Failure("bad frame: need a uint8 H x W x 3 array")
    templates = _corner_templates()
    if not is_successful(templates):
        return Failure(templates.failure())
    import src.vision.vision_helper_lib as helper  # not at module level: plan hard rule 9

    height, width = full_bgr.shape[:2]
    gray = cv2.cvtColor(full_bgr, cv2.COLOR_BGR2GRAY)
    matches = {}
    for corner, (border, mask) in templates.unwrap().items():
        if height < border.shape[0] or width < border.shape[1]:
            return Failure(f"bad frame: {width} x {height} is smaller than the {corner} border")
        scores = cv2.matchTemplate(gray, border, cv2.TM_CCORR_NORMED, mask=mask)
        scores[~np.isfinite(scores) | (scores > 1.0)] = -1.0  # as vision.py:55
        _, best, _, (x, y) = cv2.minMaxLoc(scores)
        matches[corner] = _Match(x, y, best)
    seen = ", ".join(f"{corner} {match.conf_score:.4f}" for corner, match in matches.items())
    borders = {corner: border for corner, (border, _) in templates.unwrap().items()}
    with contextlib.redirect_stdout(io.StringIO()) as printed:  # the helpers print; 300 frames would drown the report
        valid = helper.isMatchedTemplateLocValid(matches, _CORNERS, _BORDER_CONFIDENCE)
        centres = helper.findInGameMinimapBorder(matches, borders, _CORNERS) if valid else {}
    if not valid:
        reason = printed.getvalue().strip().splitlines()
        return Failure(f"no minimap found (corner scores: {seen}" + (f"; {reason[-1]}" if reason else "") + ")")
    (x1, y1), (x2, y2) = centres["left_top"], centres["right_bot"]
    if not helper.isProposedMinimapRegionValid((x1, y1, x2, y2), width, height):
        return Failure(f"no minimap found: the region {(x1, y1, x2, y2)} does not fit the {width} x {height} frame")
    crop = full_bgr[y1:y2, x1:x2].copy()
    with contextlib.redirect_stdout(io.StringIO()):
        black = helper.isMinimapBlack(crop)
    if black:
        return Failure("the minimap region is black")
    return Success(crop)


class LoadedFrames(NamedTuple):
    crops: dict[str, np.ndarray]   # file name -> RGB minimap, sorted by name
    uncroppable: dict[str, str]    # file name -> why `crop_minimap` failed, sorted by name; always {} for a B17 recording


def load_frames(frames_dir: Path) -> Result[LoadedFrames, str]:
    """The RGB minimap of every PNG in `frames_dir` that has one, sorted by name, and the PNGs that have none.

    A folder with an `index.jsonl` is a B17 recording: its PNGs are minimap crops already (`cv2.imread` gives BGR, converted
    to RGB), so none is uncroppable. Any other folder holds full-screen frames: `cv2.imread`, `crop_minimap`, then BGR to
    RGB. A frame `crop_minimap` fails on goes to `uncroppable` with its reason (B18-D1). A PNG `cv2.imread` cannot read,
    corner border images that cannot be read, and a folder in which no frame can be cropped are a `Failure`.
    """
    if not frames_dir.is_dir():
        return Failure(f"{frames_dir} is not a folder")
    paths = sorted(frames_dir.glob("*.png"), key=lambda path: path.name)
    if not paths:
        return Failure(f"no PNG files in {frames_dir}")
    recording = (frames_dir / INDEX_NAME).is_file()
    if not recording:
        templates = _corner_templates()  # without them no frame would crop, which is not a problem of the frames
        if not is_successful(templates):
            return Failure(templates.failure())
    crops: dict[str, np.ndarray] = {}
    uncroppable: dict[str, str] = {}
    for path in paths:
        bgr = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if bgr is None:
            return Failure(f"cannot read {path}")
        if not recording:
            cropped = crop_minimap(bgr)
            if not is_successful(cropped):
                uncroppable[path.name] = cropped.failure()
                continue
            bgr = cropped.unwrap()
        crops[path.name] = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    if not crops:  # every PNG failed to crop
        name, reason = next(iter(uncroppable.items()))
        return Failure(f"no frame in {frames_dir} could be cropped ({len(uncroppable)} of {len(uncroppable)}); "
                       f"first: {name}: {reason}")
    return Success(LoadedFrames(crops, uncroppable))


# ---- the draft: the character found by the colour of its dot, independently of CharacterLocator ------------------------

class _Dot(NamedTuple):
    low: np.ndarray        # HSV (OpenCV hue 0-179), per channel
    high: np.ndarray
    min_area: int          # px of a blob worth a candidate
    offset: tuple[float, float]  # (w // 2, h // 2) minus the centroid of the icon's own in-range pixels


def _read_dot() -> Result[_Dot, str]:
    icon_bgr = cv2.imread(str(_CHR / "chr.png"), cv2.IMREAD_COLOR)
    mask = cv2.imread(str(_CHR / "chr_mask.png"), cv2.IMREAD_GRAYSCALE)
    if icon_bgr is None or mask is None:
        return Failure(f"cannot read the icon images in {_CHR}")
    kept = mask > 0
    inside = cv2.erode(kept.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0  # without the outline ring
    if not inside.any():
        return Failure("the icon mask has no inside to take a colour from")
    icon = cv2.cvtColor(icon_bgr, cv2.COLOR_BGR2HSV)
    hue, sat, val = (icon[inside][:, channel].astype(int) for channel in range(3))
    low = np.array([max(0, hue.min() - HUE_SLACK), max(0, sat.min() - SAT_SLACK), max(0, val.min() - VAL_SLACK)], dtype=np.uint8)
    high = np.array([min(179, hue.max() + HUE_SLACK), 255, 255], dtype=np.uint8)
    ys, xs = np.nonzero((cv2.inRange(icon, low, high) > 0) & kept)
    height, width = kept.shape
    return Success(_Dot(low, high, max(1, len(xs) // 2), (width // 2 - float(xs.mean()), height // 2 - float(ys.mean()))))


def _draft_label(frame_rgb: np.ndarray, dot: _Dot) -> Label:
    in_range = cv2.inRange(cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2HSV), dot.low, dot.high)
    count, _, stats, centroids = cv2.connectedComponentsWithStats(in_range, connectivity=8)
    blobs = [i for i in range(1, count) if stats[i, cv2.CC_STAT_AREA] >= dot.min_area]  # component 0 is the background
    if not blobs:
        return "absent"
    if len(blobs) > 1:
        return None
    cx, cy = centroids[blobs[0]]
    return math.floor(cx + dot.offset[0] + 0.5), math.floor(cy + dot.offset[1] + 0.5)  # half up, as the locator's w // 2


def _label_json(label: Label) -> str:
    return json.dumps(list(label) if isinstance(label, tuple) else label)


def _write_sheets(frames: dict[str, np.ndarray], labels: dict[str, Label], folder: Path) -> Result[int, str]:
    """One PNG per `SHEET_SIZE` frames in `folder`: the cells in `SHEET_COLUMNS` columns, each a caption over the frame at 8x."""
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        return Failure(f"cannot create {folder}: {error}")
    names = list(frames)
    for number, start in enumerate(range(0, len(names), SHEET_SIZE)):
        chunk = names[start:start + SHEET_SIZE]
        cell_w = max(frames[name].shape[1] for name in chunk) * SCALE
        cell_h = max(frames[name].shape[0] for name in chunk) * SCALE + CAPTION_H
        columns = min(SHEET_COLUMNS, len(chunk))
        rows = -(-len(chunk) // columns)
        sheet = np.zeros((rows * cell_h, columns * cell_w, 3), dtype=np.uint8)
        for k, name in enumerate(chunk):
            frame, label = frames[name], labels[name]
            big = cv2.resize(frame, (frame.shape[1] * SCALE, frame.shape[0] * SCALE), interpolation=cv2.INTER_NEAREST)
            if isinstance(label, tuple):  # the cross is centred on the label's pixel
                cx, cy = label[0] * SCALE + SCALE // 2, label[1] * SCALE + SCALE // 2
                cv2.line(big, (cx - CROSS_ARM, cy), (cx + CROSS_ARM, cy), CROSS_RGB, 1)
                cv2.line(big, (cx, cy - CROSS_ARM), (cx, cy + CROSS_ARM), CROSS_RGB, 1)
            x0, y0 = (k % columns) * cell_w, (k // columns) * cell_h
            sheet[y0 + CAPTION_H:y0 + CAPTION_H + big.shape[0], x0:x0 + big.shape[1]] = big
            cv2.putText(sheet, f"{name}  {_label_json(label)}", (x0 + 4, y0 + CAPTION_H - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                        (255, 255, 255), 1, cv2.LINE_AA)
        path = folder / f"sheet_{number:02d}.png"
        if not cv2.imwrite(str(path), cv2.cvtColor(sheet, cv2.COLOR_RGB2BGR)):
            return Failure(f"cannot write {path}")
    return Success(-(-len(names) // SHEET_SIZE))


def draft_labels(frames_dir: Path, out: Path) -> Result[tuple[int, dict[str, str]], str]:
    """Write the draft labels for every PNG in `frames_dir` to `out` and the contact sheets beside it.

    Returns the label count and the frames that could not be cropped (file name -> reason). Those are labelled `null` and
    have no cell on a sheet.
    """
    if out.exists():
        return Failure(f"{out} already exists; refusing to overwrite it")
    loaded = load_frames(frames_dir)
    if not is_successful(loaded):
        return Failure(loaded.failure())
    dot = _read_dot()
    if not is_successful(dot):
        return Failure(dot.failure())
    crops, uncroppable = loaded.unwrap()
    drafted: dict[str, Label] = {name: _draft_label(frame, dot.unwrap()) for name, frame in crops.items()}
    sheets = _write_sheets(crops, drafted, out.parent / f"{out.stem}_sheets")
    if not is_successful(sheets):
        return Failure(sheets.failure())
    labels: dict[str, Label] = {name: drafted.get(name) for name in sorted([*crops, *uncroppable])}  # an uncroppable frame has no draft: null
    text = "{\n" + ",\n".join(f"{json.dumps(name)}: {_label_json(label)}" for name, label in labels.items()) + "\n}\n"
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
    except OSError as error:
        return Failure(f"cannot write {out}: {error}")
    return Success((len(labels), uncroppable))


# ---- the score ---------------------------------------------------------------------------------------------------------

def _target(label: JsonLabel) -> tuple[int, int] | None:
    """The `[x, y]` of a label, written as a tuple or as the list JSON gives; None for `"absent"` and `null`."""
    if isinstance(label, (tuple, list)):
        return label[0], label[1]
    return None


def _verdict(label: JsonLabel, position: tuple[int, int] | None) -> str:
    """skipped | hit | miss | wrong | near (neither a hit nor wrong: 2 or 3 px off)."""
    if label is None:
        return "skipped"
    target = _target(label)
    if target is None:  # "absent": any position found there is wrong
        return "skipped" if position is None else "wrong"
    if position is None:
        return "miss"
    dx, dy = position[0] - target[0], position[1] - target[1]
    if abs(dx) <= HIT_PX and abs(dy) <= HIT_PX:
        return "hit"
    return "wrong" if math.hypot(dx, dy) > WRONG_PX else "near"


def score(found: Mapping[str, tuple[int, int] | None], labels: Mapping[str, JsonLabel]) -> dict:
    """Score the positions found against the labels.

    A label is `(x, y)` or `[x, y]` (as JSON gives it), `"absent"` or `null`. `null` labels are skipped. An `"absent"`
    label counts only as wrong when a position was found, and stays out of `frames`, `hits`, `misses`, `hit_rate` and
    `max_err_px`. A file with an `[x, y]` label that is not in `found` counts as not found. `max_err_px` is the largest
    straight-line error over `[x, y]` labels with a position found (0.0 if none); `hit_rate` is 0.0 when no frame has an
    `[x, y]` label.
    """
    frames = 0
    max_err = 0.0
    tally: Counter[str] = Counter()
    for name, label in labels.items():
        position = found.get(name)
        verdict = _verdict(label, position)
        if verdict == "skipped":
            continue
        tally[verdict] += 1
        target = _target(label)
        if target is not None:
            frames += 1
            if position is not None:
                max_err = max(max_err, math.hypot(position[0] - target[0], position[1] - target[1]))
    return {"frames": frames, "hits": tally["hit"], "misses": tally["miss"], "wrong": tally["wrong"],
            "hit_rate": tally["hit"] / frames if frames else 0.0, "max_err_px": max_err}


def passed(result: dict) -> bool:
    """`check`'s verdict on a `score` result (spec B:L67)."""
    return result["hit_rate"] >= MIN_HIT_RATE and result["wrong"] == 0


# ---- the labels file ---------------------------------------------------------------------------------------------------

def _read_labels(path: Path) -> Result[dict[str, Label], str]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:  # json.JSONDecodeError is a ValueError
        return Failure(f"cannot read the labels {path}: {error}")
    if not isinstance(raw, dict):
        return Failure(f'{path}: expected an object {{file: [x, y] | "absent" | null}}')
    labels: dict[str, Label] = {}
    for name, value in raw.items():
        if value is None or value == "absent":
            labels[name] = value
        elif isinstance(value, list) and len(value) == 2 and all(type(v) is int for v in value):
            labels[name] = (value[0], value[1])
        else:
            return Failure(f'{path}: {name}: expected [x, y] with whole numbers, "absent" or null, got {json.dumps(value)}')
    return Success(labels)


# ---- the command line --------------------------------------------------------------------------------------------------

def _under_data(path: Path) -> Result[Path, str]:
    """`path` resolved, if it is a file or folder inside the repo's `data/` (not `data/` itself)."""
    resolved = path.resolve()
    data = _DATA.resolve()
    if resolved == data or not resolved.is_relative_to(data):
        return Failure(f"--out must be inside {data}, got {resolved}")
    return Success(resolved)


def _error(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 2


def _print_listed(lines: list[str]) -> None:
    """The first `MAX_LISTED` of `lines`, then how many more there are."""
    for line in lines[:MAX_LISTED]:
        print(line)
    if len(lines) > MAX_LISTED:
        print(f"  ... and {len(lines) - MAX_LISTED} more")


def _run_draft(frames_dir: Path, out: Path) -> int:
    resolved = _under_data(out)
    if not is_successful(resolved):
        return _error(resolved.failure())
    result = draft_labels(frames_dir, resolved.unwrap())
    if not is_successful(result):
        return _error(result.failure())
    out_path = resolved.unwrap()
    count, uncroppable = result.unwrap()
    print(f"{count} draft labels in {out_path}; contact sheets in {out_path.parent / f'{out_path.stem}_sheets'}")
    print(f"uncroppable: {len(uncroppable)} of {count} frames, labelled null, not on the sheets")
    _print_listed([f"  {name}: {reason}" for name, reason in uncroppable.items()])
    print("Next: check the sheets, correct the labels, save the result as labels.json, then run `check`.")
    return 0


def _run_check(frames_dir: Path, labels_path: Path) -> int:
    labels = _read_labels(labels_path)
    if not is_successful(labels):
        return _error(labels.failure())
    loaded = load_frames(frames_dir)
    if not is_successful(loaded):
        return _error(loaded.failure())
    locator = CharacterLocator.from_files(_CHR / "chr.png", _CHR / "chr_mask.png")
    if not is_successful(locator):
        return _error(locator.failure())
    crops, uncroppable = loaded.unwrap()
    names, wanted = sorted([*crops, *uncroppable]), labels.unwrap()  # every PNG needs a label, cropped or not
    unlabelled, unmatched = sorted(set(names) - set(wanted)), sorted(set(wanted) - set(names))
    if unlabelled or unmatched:
        return _error(f"the labels and the frames differ: {len(unlabelled)} frames without a label {unlabelled[:3]}, "
                      f"{len(unmatched)} labels without a frame {unmatched[:3]}")

    found: dict[str, tuple[int, int] | None] = {name: None for name in uncroppable}  # nothing found on a frame with no minimap
    why: dict[str, str] = {name: f"uncroppable: {reason}" for name, reason in uncroppable.items()}
    for name, frame in crops.items():
        located = locator.unwrap().locate(frame)
        found[name] = located.unwrap() if is_successful(located) else None
        if not is_successful(located):
            why[name] = located.failure()
    result = score(found, wanted)
    _print_listed([f"  {name}: label {_label_json(wanted[name])}, locator {found[name] or why[name]} "
                   f"({_verdict(wanted[name], found[name])})"
                   for name in names if _verdict(wanted[name], found[name]) in ("miss", "wrong", "near")])
    print(f"uncroppable: {len(uncroppable)} of {len(names)} frames (scored as nothing found)")
    positions = {pos for pos in found.values() if pos is not None}
    labelled = {label for label in wanted.values() if isinstance(label, tuple)}
    print(f"distinct positions: {len(positions)} found, {len(labelled)} labelled")
    print(f"frames {result['frames']}, hits {result['hits']}, misses {result['misses']}, wrong {result['wrong']}, "
          f"hit rate {result['hit_rate']:.4f} (need {MIN_HIT_RATE}), max error {result['max_err_px']:.2f} px")
    ok = passed(result)
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m src.route.harness.detection_check",
                                     description="Draft labels for a folder of frames, and score the character locator against "
                                                 "hand-checked labels. Sends no keys.")
    commands = parser.add_subparsers(dest="command", required=True)
    draft = commands.add_parser("draft", help="write labels_draft.json and contact sheets for Hong to check")
    draft.add_argument("--frames", type=Path, required=True, help="a B17 recording or a folder of full-screen PNG frames")
    draft.add_argument("--out", type=Path, required=True, help="the draft labels file, inside the repo's data/ (not overwritten)")
    check = commands.add_parser("check", help="score the locator against labels.json; exit 0 iff it passes")
    check.add_argument("--frames", type=Path, required=True, help="the same folder the labels were drafted from")
    check.add_argument("--labels", type=Path, required=True, help="the labels file Hong checked")
    args = parser.parse_args(argv)
    if args.command == "draft":
        return _run_draft(args.frames, args.out)
    return _run_check(args.frames, args.labels)


if __name__ == "__main__":
    sys.exit(main())
