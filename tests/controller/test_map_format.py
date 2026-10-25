"""The shared map-CSV format module: `ordered_local_ids` is the row order the editor writes, and both readers use the
module's header and cell encodings."""
import csv

import pytest

from src.controller import map_csv
from src.controller.csv_parser import CSVParser
from src.controller.map_csv import EditorNode, write_editor_nodes
from src.controller.map_format import (
    BOOL_FIELDS,
    HEADER,
    bool_from_cell,
    bool_to_cell,
    is_pos,
    ordered_local_ids,
    pos_from_cell,
    pos_to_cell,
)


def _state(spec: dict[int, str]) -> dict[int, EditorNode]:
    """local_id -> EditorNode with a position unique to the id, so a written row identifies its node."""
    return {lid: EditorNode(lid, nodetype, (lid, 100 + lid)) for lid, nodetype in spec.items()}


def _written_ids(tmp_path, nodes, rotation) -> list[int]:
    path = tmp_path / "out.csv"
    write_editor_nodes(str(path), nodes, rotation, minimap_size=(10, 10))
    with open(path, newline="") as f:
        rows = list(csv.reader(line for line in f if not line.startswith("#")))
    assert tuple(rows[0]) == HEADER
    return [pos_from_cell(row[1])[0] for row in rows[1:]]


CASES = {
    "mixed, keys and rotation unsorted": ({3: "orange_node", 1: "orange_node", 4: "blue_node", 2: "blue_node"}, [4, 2]),
    "empty": ({}, []),
    "blue only": ({1: "blue_node", 2: "blue_node", 3: "blue_node"}, [3, 1, 2]),
    "orange only": ({5: "orange_node", 2: "orange_node", 9: "orange_node"}, []),
    "interleaved": ({i: ("blue_node" if i % 3 == 0 else "orange_node") for i in range(1, 13)}, [12, 3, 9, 6]),
}


@pytest.mark.parametrize("spec, rotation", CASES.values(), ids=CASES.keys())
def test_ordered_local_ids_is_the_written_row_order(tmp_path, spec, rotation):
    nodes = _state(spec)
    assert ordered_local_ids(nodes, rotation) == _written_ids(tmp_path, nodes, rotation)


def test_ordered_local_ids_rotation_first_then_orange_by_id():
    nodes = _state({3: "orange_node", 1: "orange_node", 4: "blue_node", 2: "blue_node"})
    assert ordered_local_ids(nodes, [4, 2]) == [4, 2, 1, 3]
    assert ordered_local_ids(nodes, (2, 4)) == [2, 4, 1, 3]


def test_both_readers_use_the_shared_header():
    assert tuple(CSVParser().CSV_fields) == HEADER
    assert all(key == value for key, value in CSVParser().CSV_fields.items())
    assert map_csv.CSV_HEADER == list(HEADER)
    assert BOOL_FIELDS == HEADER[2:]


def test_cell_encodings():
    assert [bool_to_cell(True), bool_to_cell(False)] == ["TRUE", "FALSE"]
    assert [bool_from_cell(c) for c in ("TRUE", "FALSE", "true", "")] == [True, False, False, False]
    assert pos_to_cell((12, 3)) == "(12, 3)"
    assert pos_from_cell(pos_to_cell((12, 3))) == (12, 3)
    assert is_pos((1, 2)) and is_pos((True, 2))
    assert not any(is_pos(v) for v in ([1, 2], (1, 2, 3), (1.5, 2), (1,), "(1, 2)", None))
