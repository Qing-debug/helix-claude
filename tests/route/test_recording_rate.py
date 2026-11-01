"""The recording-rate tool `tools/recording_rate.py` (plan RT-13 part A; spec 5.11, section 11 step 12). No game, no screen."""
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))  # the tool is a script, not a package

import recording_rate  # noqa: E402


def _index_line(i: int, t_ns: int) -> str:
    return json.dumps({"i": i, "t_ns": t_ns, "file": f"frame_{i:06d}.png", "w": 4, "h": 4}) + "\n"


def test_rate_from_a_made_folder(tmp_path, capsys):
    folder = tmp_path / "rate"
    folder.mkdir()
    for i in range(10):
        (folder / f"frame_{i:06d}.png").write_bytes(b"x" * 1000)
    index = folder / "index.jsonl"
    # Three lines, so last minus first is told apart from second minus first (RT-13-R M2).
    index.write_text(_index_line(0, 0) + _index_line(5, 400_000_000) + _index_line(9, 1_000_000_000), encoding="utf-8")
    total = 10 * 1000 + index.stat().st_size  # index included, span 1 s

    assert recording_rate.main([str(folder)]) == 0

    out = capsys.readouterr().out.splitlines()
    assert out == [
        f"MB per hour: {total * 3600 / 2**20:.1f}",
        f"GB per hour: {total * 3600 / 2**30:.2f}",
    ]


def test_missing_index_exits_2(tmp_path, capsys):
    folder = tmp_path / "empty"
    folder.mkdir()
    (folder / "frame_000000.png").write_bytes(b"x" * 1000)

    assert recording_rate.main([str(folder)]) == 2

    assert "index.jsonl" in capsys.readouterr().err


def test_zero_span_exits_2(tmp_path, capsys):
    folder = tmp_path / "zero"
    folder.mkdir()
    (folder / "index.jsonl").write_text(_index_line(0, 5) + _index_line(1, 5), encoding="utf-8")

    assert recording_rate.main([str(folder)]) == 2

    assert "span" in capsys.readouterr().err


def test_negative_span_exits_2(tmp_path, capsys):
    folder = tmp_path / "negative"
    folder.mkdir()
    (folder / "index.jsonl").write_text(_index_line(0, 5) + _index_line(1, 4), encoding="utf-8")

    assert recording_rate.main([str(folder)]) == 2

    assert "span" in capsys.readouterr().err


def test_one_index_line_exits_2(tmp_path, capsys):
    folder = tmp_path / "one"
    folder.mkdir()
    (folder / "index.jsonl").write_text(_index_line(0, 0), encoding="utf-8")

    assert recording_rate.main([str(folder)]) == 2

    assert "two" in capsys.readouterr().err


BAD_LAST_LINES = [
    '{"i": 9}',  # no t_ns
    "[1, 2]",  # not an object
    "not json",
    '{"t_ns": "x"}',  # not a number
    '{"t_ns": true}',  # a bool is an int in Python (route_view/model.py:255 refuses it too)
    '{"t_ns": NaN}',
    '{"t_ns": Infinity}',
    '{"t_ns": 1.5}',
]


@pytest.mark.parametrize("last", BAD_LAST_LINES)
def test_a_malformed_index_line_exits_2_with_one_line(tmp_path, capsys, last):
    folder = tmp_path / "bad"
    folder.mkdir()
    (folder / "index.jsonl").write_text(_index_line(0, 0) + last + "\n", encoding="utf-8")

    assert recording_rate.main([str(folder)]) == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Traceback" not in captured.err
    assert len(captured.err.splitlines()) == 1
    assert "bad line in index.jsonl" in captured.err


def test_a_malformed_first_line_exits_2(tmp_path, capsys):
    folder = tmp_path / "badfirst"
    folder.mkdir()
    (folder / "index.jsonl").write_text('{"i": 0}\n' +_index_line(1, 5), encoding="utf-8")

    assert recording_rate.main([str(folder)]) == 2

    assert "bad line in index.jsonl" in capsys.readouterr().err


def test_errors_use_the_repo_cli_prefix(tmp_path, capsys):
    folder = tmp_path / "empty"
    folder.mkdir()

    assert recording_rate.main([str(folder)]) == 2

    assert capsys.readouterr().err.startswith("error: ")  # as record_minimap, walk, sim.run, measure_moves
