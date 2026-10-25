"""The map-CSV format: the one place to change it.

Shared by the runtime parser (`csv_parser.CSVParser`) and the editor's reader and writer (`map_csv`). It holds only
what both sides do identically: the header, the node types, the boolean cells, the `(x, y)` position cell and the
order the editor writes rows in (which the editor's edge preview must copy, because edge ties go to the
first-inserted node). The readers' accept/reject rules and their error texts differ today and stay in their own
modules; tests/controller/test_map_csv_characterization.py pins both.

Pure: it builds no `Node` or `EditorNode`, so importing or calling it never touches `Node.node_id_counter`.
"""

import ast
from collections.abc import Iterable, Mapping
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
