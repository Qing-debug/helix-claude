"""The per-map rope file (C8, H94): `<map stem>.ropes.json` beside the map CSV. The reader and writer, the keep-out test
the map editor refuses nodes by (H91, D1) and the nearest free x's it suggests (D2). Pure: no Tk, and no `src.route`
import (import-linter contract c), which is why the walker's node offset is repeated here (H85)."""
import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TypeGuard

from returns.result import Failure, Result, Success

# a character on a rope reads 1 px left of the rope's column (OUTLINE147: 10 of 10 settled reads)
ON_ROPE_DX = -1
# a node sits 3 px above the ground row: the same fact as position.DOT_TO_FOOT_PX (H85)
NODE_ABOVE_GROUND_PX = 3
# the keep-out of H91 (Hong's pick QB231, ROPE147P-D1): 5 px sideways, 6 px up or down
KEEP_OUT_X_PX = 5
KEEP_OUT_Y_PX = 6
FORMAT = "helix-ropes"
VERSION = 1
FIX_TEXT = ("open the map in the map editor, mark its ropes again (Find ropes or Add rope) and save; the save "
            "replaces this file")
READ_FIX_TEXT = "make the file readable (permissions, or a program holding it open), or move it away to start with no ropes"
NEWER_FIX_TEXT = "update Helix, which reads rope files of version 1"
_KNOWN_KEYS = ("format", "version", "minimap_size", "ropes")


@dataclass(frozen=True)
class RopeMark:
    """One rope in map coordinates: `x` is the column as drawn; `top_y` and `bottom_y` are where a node on the platform
    above and on the platform below would sit (C8)."""
    x: int
    top_y: int
    bottom_y: int


@dataclass(frozen=True)
class RopeFile:
    ropes: tuple[RopeMark, ...]
    minimap_size: tuple[int, int] | None
    extra: Mapping[str, object]   # top-level keys this version does not know; the writer keeps them


def rope_file_for(map_csv: Path) -> Path:
    return map_csv.with_suffix(".ropes.json")


def read_ropes(path: Path) -> Result[RopeFile, str]:
    """Nothing at the path reads as no ropes. Otherwise the whole file is checked before anything is returned, and every
    problem is listed in one message naming the file, each bad rope by its number, and how to fix it (C11)."""
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return Success(RopeFile((), None, {}))
    except OSError as error:
        return Failure(f"{path} could not be read: {_reason(error)}.\nHow to fix it: {READ_FIX_TEXT}.")
    try:
        data = json.loads(raw.decode("utf-8"))
    except ValueError:  # UnicodeDecodeError and JSONDecodeError are both ValueError
        return _refused(path, ["the file is not JSON"])
    if not isinstance(data, dict):
        return _refused(path, ["the file is not a JSON object"])

    problems: list[str] = []
    newer = False
    if data.get("format") != FORMAT:
        problems.append(f'"format" must be "{FORMAT}"')
    version = data.get("version")
    if not _is_whole(version) or version < 1:
        problems.append('"version" must be the whole number 1')
    elif version > VERSION:
        newer = True
        problems.append(f"version {version} was made by a newer Helix")

    minimap_size: tuple[int, int] | None = None
    if "minimap_size" in data:
        size = data["minimap_size"]
        if isinstance(size, list) and len(size) == 2 and all(_is_whole(v) and v > 0 for v in size):
            minimap_size = (size[0], size[1])
        else:
            problems.append('"minimap_size" must be a list of two positive whole numbers, like [162, 82]')

    ropes: list[RopeMark] = []
    raw_ropes = data.get("ropes")
    if not isinstance(raw_ropes, list):
        problems.append('"ropes" must be a list')
    else:
        for number, item in enumerate(raw_ropes, start=1):
            mark, rope_problems = _check_rope(item)
            if mark is None:
                problems.extend(f"rope {number}: {problem}" for problem in rope_problems)
            else:
                ropes.append(mark)

    if problems:
        return _refused(path, problems, NEWER_FIX_TEXT if newer else FIX_TEXT)
    extra = {key: value for key, value in data.items() if key not in _KNOWN_KEYS}
    return Success(RopeFile(tuple(ropes), minimap_size, extra))


def write_ropes(path: Path, file: RopeFile) -> Result[None, str]:
    """Writes the file to `path.with_suffix(".tmp")` and replaces `path` with it, so a failed write leaves the old file."""
    data: dict[str, object] = {"format": FORMAT, "version": VERSION}
    if file.minimap_size is not None:
        data["minimap_size"] = list(file.minimap_size)
    data["ropes"] = [{"x": rope.x, "top_y": rope.top_y, "bottom_y": rope.bottom_y} for rope in file.ropes]
    for key, value in file.extra.items():
        data.setdefault(key, value)
    text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_bytes(text.encode("utf-8"))
        os.replace(tmp, path)
    except OSError as error:
        _remove_quietly(tmp)
        return Failure(f"{path} could not be written: {_reason(error)}.")
    return Success(None)


def near_rope_end(node: tuple[int, int], ropes: Sequence[RopeMark]) -> RopeMark | None:
    """The first rope whose keep-out a node at this map position is inside (H91): the node is within 5 px sideways of
    the rope's read x (`x + ON_ROPE_DX`) and within 6 px of either end."""
    nx, ny = node
    for rope in ropes:
        if abs(nx - (rope.x + ON_ROPE_DX)) <= KEEP_OUT_X_PX and (
                abs(ny - rope.top_y) <= KEEP_OUT_Y_PX or abs(ny - rope.bottom_y) <= KEEP_OUT_Y_PX):
            return rope
    return None


def free_x(mark: RopeMark) -> tuple[int, int]:
    """The nearest x on each side of the rope's keep-out (D2): the x just outside it, left and right."""
    centre = mark.x + ON_ROPE_DX
    return (centre - KEEP_OUT_X_PX - 1, centre + KEEP_OUT_X_PX + 1)


def _is_whole(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)


def _check_rope(item: object) -> tuple[RopeMark | None, list[str]]:
    if not isinstance(item, dict):
        return None, ["it is not an object with x, top_y and bottom_y"]
    problems: list[str] = []
    values: dict[str, int] = {}
    for key in ("x", "top_y", "bottom_y"):
        value = item.get(key)
        if not _is_whole(value):
            problems.append(f'"{key}" must be a whole number')
        elif value < 0:
            problems.append(f'"{key}" must not be negative')
        else:
            values[key] = value
    if "top_y" in values and "bottom_y" in values and values["top_y"] >= values["bottom_y"]:
        problems.append('"top_y" must be less than "bottom_y"')
    if problems:
        return None, problems
    return RopeMark(values["x"], values["top_y"], values["bottom_y"]), []


def _refused(path: Path, problems: list[str], fix: str = FIX_TEXT) -> Failure[str]:
    lines = [f"{path} cannot be used:"] + [f"  - {problem}" for problem in problems]
    lines.append(f"How to fix it: {fix}.")
    return Failure("\n".join(lines))


def _reason(error: OSError) -> str:
    return error.strerror or str(error)


def _remove_quietly(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass
