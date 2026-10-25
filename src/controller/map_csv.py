"""
Pure CSV I/O for the in-GUI map editor.

Deliberately independent of src.controller.csv_parser (CSVParser) and
src.controller.node (Node) so that:
  1. Editor loads don't bump Node.node_id_counter.
  2. Editor I/O lifecycle stays isolated from the agent's runtime state.

The format itself (header, node types, cell encodings, row order) and the
check of a file against it, with its messages, live in
src.controller.map_format, which CSVParser uses too: change the format there.
This module keeps only the editor's own strict `# minimap_size=` check
(tests/controller/test_map_csv_characterization.py pins both readers).
"""

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Tuple, List, Dict, Optional, Any

from returns.result import Result, Success, Failure
from returns.pipeline import is_successful

from src.controller.map_format import (
    BLUE_NODE,
    HEADER,
    bool_to_cell,
    is_comment,
    not_found_message,
    ordered_local_ids,
    pos_to_cell,
    problems_message,
    read_rows,
    unreadable_message,
)

# The header row write_editor_nodes writes, as a list for csv.writer.
CSV_HEADER = list(HEADER)


@dataclass
class EditorNode:
    local_id: int                                  # editor-only id; NOT Node.uuid
    nodetype: str                                  # "blue_node" | "orange_node"
    pos: Tuple[int, int]                           # minimap pixel coords (top-left origin)
    can_fall: bool = False
    fall_dist_exceeds_vertical_tp: bool = False
    is_left_platform_edge: bool = False
    is_right_platform_edge: bool = False


def read_editor_nodes(
    path: str,
) -> Result[Tuple[List[EditorNode], List[int], Dict[str, Any]], str]:
    """
    Read a map CSV from `path`.

    Returns Success((nodes, rotation_order, metadata)) where:
      - nodes is the list of EditorNode in file order.
      - rotation_order is a list of local_ids of blue_nodes in file order.
      - metadata is a dict of recognized `# key=value` comment lines.
        Currently recognized: `minimap_size` -> Tuple[int, int]. Legacy CSVs
        (no `#` lines) yield an empty dict.

    On any error returns Failure(<reason str>). Caller can decide UX.
    Malformed metadata (recognized key, unparseable value) returns Failure
    per the strict-metadata design decision (Task 19, Q3 locked 2026-05-22).
    local_id starts at 1 and matches file row order (header excluded).
    """
    p = Path(path)
    if not p.exists():
        return Failure(not_found_message(path))
    try:
        with open(p, "r", newline="") as f:
            all_lines = f.readlines()
    except (OSError, UnicodeDecodeError) as e:
        return Failure(unreadable_message(path, e))

    # Runs before the row check, so a bad metadata line is reported alone (src/controller/CLAUDE.md).
    metadata_lines = [(number, line) for number, line in enumerate(all_lines, start=1) if is_comment(line)]
    meta_result = _parse_metadata_lines(metadata_lines)
    if not is_successful(meta_result):
        return Failure(problems_message(path, [meta_result.failure()]))
    metadata = meta_result.unwrap()

    # The same check and messages as the runtime parser (decision H16).
    rows, problems = read_rows(all_lines)
    if problems:
        return Failure(problems_message(path, problems))
    nodes = [
        EditorNode(i, row.nodetype, row.pos, row.can_fall, row.fall_dist_exceeds_vertical_tp,
                   row.is_left_platform_edge, row.is_right_platform_edge)
        for i, row in enumerate(rows, start=1)
    ]
    rotation_order = [node.local_id for node in nodes if node.nodetype == BLUE_NODE]
    return Success((nodes, rotation_order, metadata))


def write_editor_nodes(
    path: str,
    nodes: Dict[int, EditorNode],
    rotation_order: List[int],
    minimap_size: Optional[Tuple[int, int]] = None,
) -> Result[None, str]:
    """
    Write editor nodes to a CSV at `path`.

    Row order: map_format.ordered_local_ids, i.e. blue_nodes in rotation_order
    first, then orange_nodes by ascending local_id.
    Creates parent directory if it doesn't exist.

    If `minimap_size` is provided, a `# minimap_size=W,H` comment line is written
    as the first line. The runtime parser (csv_parser.CSVParser) and the editor
    reader (read_editor_nodes) both strip `#`-prefixed lines, so this is forward-
    compatible with legacy CSVs (no metadata) and with the runtime.
    """
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", newline="") as f:
            if minimap_size is not None:
                # Manual write with \r\n to match csv.writer's excel-dialect line ending.
                # Cannot use writer.writerow(["# minimap_size=W,H"]) - the comma in W,H
                # would trigger CSV quoting and break the `#`-prefix filter on read.
                f.write(f"# minimap_size={minimap_size[0]},{minimap_size[1]}\r\n")
            writer = csv.writer(f, delimiter=",")
            writer.writerow(CSV_HEADER)
            for local_id in ordered_local_ids(nodes, rotation_order):
                writer.writerow(_row_from_node(nodes[local_id]))
        return Success(None)
    except OSError as e:
        return Failure(f"OS error writing {path}: {e}")


def _row_from_node(node: EditorNode) -> List[str]:
    return [
        node.nodetype,
        pos_to_cell(node.pos),
        bool_to_cell(node.can_fall),
        bool_to_cell(node.fall_dist_exceeds_vertical_tp),
        bool_to_cell(node.is_left_platform_edge),
        bool_to_cell(node.is_right_platform_edge),
    ]


def _parse_metadata_lines(lines: List[Tuple[int, str]]) -> Result[Dict[str, Any], str]:
    """
    Parse `#`-prefixed metadata lines, given as (file line number, line) pairs, into a dict.

    Recognized keys:
      - `minimap_size`: value is "W,H" -> parsed as `Tuple[int, int]`.

    Unrecognized keys (e.g. `# editor_version=1`, or plain `# any comment`) are
    silently ignored - those are valid forward-compat / human comments, not
    malformed metadata.

    A `#` line whose key IS recognized but whose value is unparseable returns
    Failure (strict-metadata design decision, Task 19 Q3): one problem line, "line N: ...",
    that says what the line must be. Caller (read_editor_nodes) puts it in the same
    message as the row problems (problems_message) so the editor's _load shows a showerror.

    If `minimap_size` appears on multiple lines, the FIRST one wins (subsequent
    duplicates are ignored - same forgiving stance as unknown keys).
    """
    metadata: Dict[str, Any] = {}
    for number, raw in lines:
        body = raw.lstrip().lstrip('#').strip()
        if '=' not in body:
            continue  # plain comment, not key=value
        key, _, value = body.partition('=')
        key = key.strip()
        value = value.strip()
        if key == 'minimap_size':
            if 'minimap_size' in metadata:
                continue  # first one wins
            bad = Failure(
                f"line {number}: the \"# minimap_size=\" line is '{raw.rstrip()}'; it must be two whole numbers "
                "above 0, like \"# minimap_size=162,82\". Fix it, or delete the line."
            )
            parts = value.split(',')
            if len(parts) != 2:
                return bad
            try:
                w, h = int(parts[0].strip()), int(parts[1].strip())
            except ValueError:
                return bad
            if w < 1 or h < 1:
                return bad
            metadata['minimap_size'] = (w, h)
        # Unknown keys: silently ignore (treated as plain comments).
    return Success(metadata)
