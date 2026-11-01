"""The recording-rate tool `tools/recording_rate.py` (plan RT-13 part A; spec 5.11, section 11 step 12). No game, no screen."""
import json
import sys
from pathlib import Path

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
