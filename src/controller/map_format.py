"""The map-CSV format: the one place to change it.

Shared by the runtime parser (`csv_parser.CSVParser`) and the editor's reader and writer (`map_csv`). It holds what
both sides do identically: the header, the node types, the boolean cells, the `(x, y)` position cell, the check of a
whole file against them with the messages the user sees (`read_rows`, `problems_message`; decision H16) and the
order the editor writes rows in (which the editor's edge preview must copy, because edge ties go to the
first-inserted node). What still differs between the readers (file errors, the editor's strict `# minimap_size=`
line) stays in their own modules; tests/controller/test_map_csv_characterization.py pins both.

Pure: it builds no `Node` or `EditorNode`, so importing or calling it never touches `Node.node_id_counter`.
"""

import ast
import csv
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol, TypeGuard

HEADER: tuple[str, ...] = (
    "nodetype",
    "pos_in_minimap",
    "can_fall",
    "fall_dist_exceeds_vertical_tp",
    "is_left_platform_edge",
    "is_right_platform_edge",
)
BOOL_FIELDS: tuple[str, ...] = HEADER[2:]

BLUE_NODE = "blue_node"      # a rotation stop, in file order
ORANGE_NODE = "orange_node"  # an intermediary
NODETYPES = frozenset({BLUE_NODE, ORANGE_NODE})

TRUE = "TRUE"
FALSE = "FALSE"
BOOL_CELLS = (TRUE, FALSE)


def bool_from_cell(cell: str) -> bool:
    """`TRUE` is True; any other cell is False (both readers reject other cells first, each its own way)."""
    return cell == TRUE


def bool_to_cell(value: bool) -> str:
    return TRUE if value else FALSE


def pos_from_cell(cell: str) -> Any:
    """The cell as a Python literal. Raises what `ast.literal_eval` raises (ValueError, SyntaxError, TypeError, ...)."""
    return ast.literal_eval(cell)


def is_pos(value: object) -> TypeGuard[tuple[int, int]]:
    """A valid position: a 2-tuple of ints (bools count, as `isinstance(True, int)`)."""
    return isinstance(value, tuple) and len(value) == 2 and all(isinstance(c, int) for c in value)


def pos_to_cell(pos: tuple[int, int]) -> str:
    return f"({pos[0]}, {pos[1]})"


class _HasNodetype(Protocol):
    @property
    def nodetype(self) -> str: ...


def ordered_local_ids(nodes: Mapping[int, _HasNodetype], rotation_order: Iterable[int]) -> list[int]:
    """The order the editor writes rows in: the rotation (blue nodes) as given, then orange nodes by ascending id.

    The runtime parser inserts nodes in file order, so this is also the runtime's `node_map` insertion order after a
    save and load, which the editor's edge preview must match.
    """
    return list(rotation_order) + [local_id for local_id in sorted(nodes) if nodes[local_id].nodetype == ORANGE_NODE]


# --- checking a whole file (decision H16) ---------------------------------------------------------------------------

MAX_LISTED_PROBLEMS = 20  # problems_message lists at most this many, so a message box stays readable

_HEADER_LINE = ",".join(HEADER)
_POS_EXAMPLE = '"(12, 34)"'


@dataclass(frozen=True)
class MapRow:
    """One node row of a file that passed `read_rows`. `line` is its line number in the file (1-based, `#` lines
    counted)."""
    line: int
    nodetype: str
    pos: tuple[int, int]
    can_fall: bool
    fall_dist_exceeds_vertical_tp: bool
    is_left_platform_edge: bool
    is_right_platform_edge: bool


def is_comment(line: str) -> bool:
    """A `#` line: a comment or metadata such as `# minimap_size=W,H`. Readers skip it."""
    return line.lstrip().startswith("#")


def read_rows(lines: Iterable[str]) -> tuple[list[MapRow], list[str]]:
    """Check a map file's lines (text mode, line endings optional) against the format and decode its node rows.

    Returns `(rows, problems)`. `problems` is empty when the file is valid; otherwise each entry starts with the line
    number ("line 4: ...") or describes the file, says what is wrong and how to fix it, and `rows` must not be used.
    `#` lines and blank lines are skipped. The first other line must be the header; if it is wrong only that is
    reported, as the rows cannot be read against an unknown column order. Otherwise every row is checked and every
    problem is reported, several per row if a row has several bad cells.
    """
    rows: list[MapRow] = []
    problems: list[str] = []
    header_seen = False
    for number, raw in enumerate(lines, start=1):
        line = raw.rstrip("\r\n")
        if is_comment(line) or not line.strip():
            continue
        cells: list[str] | None = None
        csv_error = ""
        try:
            cells = next(csv.reader([line]))
        except csv.Error as e:
            csv_error = str(e)
        if not header_seen:
            if cells != list(HEADER):
                return [], [
                    f"line {number}: the header row is not the expected one. Replace the line with the expected one.\n"
                    f"    expected: {_HEADER_LINE}\n"
                    f"    found:    {line}"
                ]
            header_seen = True
        elif cells is None:
            problems.append(f"line {number}: cannot be read as CSV ({csv_error}). Check the quotes and commas.")
        else:
            row = _read_row(number, cells, problems)
            if row is not None:
                rows.append(row)
    if not header_seen:
        return [], [
            "The file has no header row (it is empty or has only # lines). Its first line that is not a # line must "
            f"be:\n    {_HEADER_LINE}"
        ]
    return rows, problems


def _read_row(number: int, cells: list[str], problems: list[str]) -> MapRow | None:
    """Decode one row, or append its problems and return None."""
    if not any(cell.strip() for cell in cells):
        problems.append(f"line {number}: the row is empty (only commas). Delete the line.")
        return None
    if len(cells) != len(HEADER):
        if len(cells) > len(HEADER) and not any(cell.strip() for cell in cells[len(HEADER):]):
            problems.append(f"line {number}: {len(cells)} cells, expected {len(HEADER)}. Remove the extra comma(s) at "
                            "the end of the line.")
        else:
            problems.append(f"line {number}: {len(cells)} cells, expected {len(HEADER)} ({', '.join(HEADER)}). Look "
                            f"for a missing or extra comma; pos_in_minimap needs its quotes, like {_POS_EXAMPLE}.")
        return None
    nodetype, pos_cell, *bool_cells = cells
    found: list[str] = []
    if nodetype not in NODETYPES:
        found.append(f"line {number}: nodetype is {nodetype!r}; it must be {BLUE_NODE} or {ORANGE_NODE}.")
    try:
        pos = pos_from_cell(pos_cell)
    except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError):
        pos = None
    if not is_pos(pos):
        found.append(f"line {number}: pos_in_minimap is {pos_cell!r}; it must be two whole numbers in quotes, like "
                     f"{_POS_EXAMPLE}.")
    for field, cell in zip(BOOL_FIELDS, bool_cells):
        if cell not in BOOL_CELLS:
            found.append(f"line {number}: {field} is {cell!r}; it must be {TRUE} or {FALSE} (capitals, no spaces).")
    if found or not is_pos(pos):
        problems.extend(found)
        return None
    can_fall, fall_dist, left_edge, right_edge = (bool_from_cell(cell) for cell in bool_cells)
    return MapRow(number, nodetype, pos, can_fall, fall_dist, left_edge, right_edge)


def problems_message(path: object, problems: list[str]) -> str:
    """The message the user sees for a file `read_rows` refused: the file, what to do, then the problems (at most
    MAX_LISTED_PROBLEMS of them)."""
    count = "the problem" if len(problems) == 1 else f"the {len(problems)} problems"
    shown = problems[:MAX_LISTED_PROBLEMS]
    if len(problems) > len(shown):
        shown = shown + [f"... and {len(problems) - len(shown)} more."]
    return (f"The map file {path} is not in the expected format, so it was not loaded. Fix {count} below in a text "
            "editor, then load it again.\n\n" + "\n".join(shown))


def not_found_message(path: object) -> str:
    return f"The map file {path} does not exist, so it was not loaded. Pick another file."


def unreadable_message(path: object, error: Exception) -> str:
    return f"The map file {path} could not be read, so it was not loaded: {error}"
