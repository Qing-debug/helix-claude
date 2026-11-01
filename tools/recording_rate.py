"""Recording rate from a frames folder (plan RT-13 part A; spec 5.11, section 11 step 12).

    python tools/recording_rate.py FRAMES_DIR

Prints two lines: `MB per hour` (MB = 2**20 bytes) and `GB per hour` (GB = 2**30 bytes). The rate is the folder's total
bytes, index included, over the span of its `index.jsonl` (last `t_ns` minus first). Exit 0 with the numbers; exit 2
(message on stderr) when the index is missing, has fewer than two lines, or spans 0 or less.
"""
import argparse
import json
import sys
from pathlib import Path

INDEX_NAME = "index.jsonl"
NS_PER_HOUR = 3600 * 10**9
MB = 2**20
GB = 2**30


def rate(folder: Path) -> tuple[float, float]:
    """(MB per hour, GB per hour) for `folder`; raises ValueError when the index cannot give a span."""
    index = folder / INDEX_NAME
    if not index.is_file():
        raise ValueError(f"no {INDEX_NAME} in {folder}")
    lines = [line for line in index.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(lines) < 2:
        raise ValueError(f"{INDEX_NAME} has fewer than two lines; cannot take a span")
    span_ns = json.loads(lines[-1])["t_ns"] - json.loads(lines[0])["t_ns"]
    if span_ns <= 0:
        raise ValueError(f"{INDEX_NAME} spans {span_ns} ns; need a span above 0")
    total = sum(p.stat().st_size for p in folder.iterdir() if p.is_file())
    per_hour = total * NS_PER_HOUR / span_ns
    return per_hour / MB, per_hour / GB


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Recording rate (MB and GB per hour) from a frames folder.")
    parser.add_argument("frames_dir", type=Path)
    args = parser.parse_args(argv)
    try:
        mb, gb = rate(args.frames_dir)
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"recording_rate: {error}", file=sys.stderr)
        return 2
    print(f"MB per hour: {mb:.1f}")
    print(f"GB per hour: {gb:.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
