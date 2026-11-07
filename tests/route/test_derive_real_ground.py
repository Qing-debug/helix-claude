"""Tests for `tools/derive_real_ground.py` (OFFG-R1 M2, M4): the tool that made the real-ground fixtures from saved runs.

The keylog promise of its docstring is checked here on a small run folder built in `tmp_path`, with a `keys.jsonl`
beside the files the tool may read: the tool must never open it, and must never list a run folder (a listing would show
the keylog's name). Nothing here reads a real run folder.
"""

import builtins
import io
import json
import os
from pathlib import Path

import cv2
import numpy as np

from tools import derive_real_ground as derive

MAP_HEADER = "nodetype,pos_in_minimap,can_fall,fall_dist_exceeds_vertical_tp,is_left_platform_edge,is_right_platform_edge"
LAYOUT = {"minimap_size": [40, 30], "platforms": [{"name": "floor", "droppable": False, "points": [[5, 10], [15, 10]]}]}
GROUND_ROW = 14


def _run_folder(root: Path, *, ground: bool = True, steps: list[dict] | None = None) -> Path:
    run = root / "runs" / "2026-11-03T000000Z"
    (run / "frames").mkdir(parents=True)
    for i in range(3):
        frame = np.zeros((30, 40), np.uint8)
        if ground:
            frame[GROUND_ROW, 5:16] = 255
        assert cv2.imwrite(str(run / "frames" / f"{i}.png"), frame)
    (run / "map.csv").write_text(f'# minimap_size=40,30\n{MAP_HEADER}\nblue_node,"(10, 10)",FALSE,FALSE,FALSE,FALSE\n',
                                 encoding="utf-8")
    events = steps if steps is not None else [
        {"ev": "leg", "t_ns": 1_000_000, "to": [10, 10]},
        {"ev": "settled", "t_ns": 2_000_000, "pos": [10, 8]},
    ]
    (run / "steps.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    (run / "keys.jsonl").write_text('{"key": "trap"}\n', encoding="utf-8")
    return run


def _args(root: Path) -> list[str]:
    layout = root / "layout.json"
    layout.write_text(json.dumps(LAYOUT), encoding="utf-8")
    return ["--runs", str(root / "runs"), "--layout", str(layout),
            "--terrain-out", str(root / "terrain.json"), "--reads-out", str(root / "reads.json")]


def test_the_tool_never_opens_or_lists_the_keylog(tmp_path, monkeypatch, capsys):
    run = _run_folder(tmp_path)
    args = _args(tmp_path)
    opened, listed = [], []

    real_open, real_io_open, real_scandir, real_listdir, real_imread = (
        builtins.open, io.open, os.scandir, os.listdir, cv2.imread)

    def watch(log, real):
        def wrapper(path=".", *a, **k):
            log.append(os.fspath(path) if isinstance(path, (str, os.PathLike)) else str(path))
            return real(path, *a, **k)
        return wrapper

    monkeypatch.setattr(builtins, "open", watch(opened, real_open))
    monkeypatch.setattr(io, "open", watch(opened, real_io_open))
    monkeypatch.setattr(cv2, "imread", watch(opened, real_imread))
    monkeypatch.setattr(os, "scandir", watch(listed, real_scandir))
    monkeypatch.setattr(os, "listdir", watch(listed, real_listdir))
    code = derive.main(args)
    monkeypatch.undo()

    assert code == 0, capsys.readouterr().err
    assert (tmp_path / "terrain.json").is_file() and (tmp_path / "reads.json").is_file()
    assert (run / "keys.jsonl").exists()
    touched = opened + listed
    assert any("steps.jsonl" in p for p in opened) and any(p.endswith(".png") for p in opened)  # the watch sees reads
    assert not [p for p in touched if "keys" in p], touched
    assert {Path(p) for p in listed} <= {tmp_path / "runs", run / "frames"}, listed  # never a run folder itself


def test_a_platform_with_no_bright_ground_row_is_a_usage_error_not_a_crash(tmp_path, capsys):
    _run_folder(tmp_path, ground=False)

    assert derive.main(_args(tmp_path)) == 2

    err = capsys.readouterr().err
    assert err.startswith("error: ") and "no bright ground row" in err and len(err.splitlines()) == 1


def test_a_leg_event_without_its_target_is_a_usage_error_not_a_crash(tmp_path, capsys):
    _run_folder(tmp_path, steps=[{"ev": "leg", "t_ns": 1_000_000}])

    assert derive.main(_args(tmp_path)) == 2

    err = capsys.readouterr().err
    assert err.startswith("error: ") and "`leg` event without its `to`" in err and len(err.splitlines()) == 1
