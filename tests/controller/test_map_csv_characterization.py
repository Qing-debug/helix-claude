"""Characterization tests: what the two map-CSV readers accept and reject today, the bytes the editor writes, and the
node insertion order of the runtime parser, the editor reader and the editor's edge preview.

The runtime parser (`csv_parser.CSVParser`) and the editor reader (`map_csv.read_editor_nodes`) diverge on purpose or
by drift; these tests pin each one's current behaviour, divergences included, so a refactor of the shared format code
cannot change either. Unifying them is a behaviour change, not a refactor.
"""
from pathlib import Path
from types import SimpleNamespace

import pytest
from returns.pipeline import is_successful
from returns.result import Failure, Success

from src.classes.classes import Kanna
from src.controller.controller import MapleAgent
from src.controller.csv_parser import CSVParser
from src.controller.map_csv import EditorNode, read_editor_nodes, write_editor_nodes
from src.controller.node import Node

HEADER = "nodetype,pos_in_minimap,can_fall,fall_dist_exceeds_vertical_tp,is_left_platform_edge,is_right_platform_edge\n"
HEADER_LIST = ["nodetype", "pos_in_minimap", "can_fall", "fall_dist_exceeds_vertical_tp", "is_left_platform_edge",
               "is_right_platform_edge"]
OK_ROW = 'blue_node,"(1, 2)",FALSE,FALSE,FALSE,FALSE\n'


def _csv(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "map.csv"
    path.write_text(text, newline="")
    return path


def _runtime(path: Path):
    rotation, node_map = [], {}
    return CSVParser(CSV=path).parseCSV(rotation, node_map), rotation, node_map


def _editor(path: Path):
    return read_editor_nodes(str(path))


# --- metadata lines -------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("line, reason", [
    ("# minimap_size=abc", "(expected `# minimap_size=W,H`)"),
    ("# minimap_size=a,b", "(W and H must be integers)"),
    ("# minimap_size=0,5", "(W and H must be positive integers)"),
])
def test_bad_minimap_size_runtime_accepts_editor_refuses(tmp_path, line, reason):
    path = _csv(tmp_path, f"{line}\n{HEADER}{OK_ROW}")
    assert _runtime(path)[0] == Success(None)
    assert _editor(path) == Failure(f"Malformed metadata line: '{line}' {reason}")


def test_metadata_first_minimap_size_wins_and_other_comments_ignored(tmp_path):
    path = _csv(tmp_path, "# minimap_size=3,4\n# minimap_size=x\n# note\n#editor_version=1\n" + HEADER + OK_ROW)
    assert _runtime(path)[0] == Success(None)
    nodes, rotation, metadata = _editor(path).unwrap()
    assert metadata == {"minimap_size": (3, 4)}
    assert [n.local_id for n in nodes] == [1] and rotation == [1]


def test_no_metadata_gives_empty_dict(tmp_path):
    assert _editor(_csv(tmp_path, HEADER + OK_ROW)).unwrap()[2] == {}


# --- several bad rows: runtime reports all, editor stops at the first -----------------------------------------------

def test_several_bad_rows(tmp_path):
    path = _csv(tmp_path, HEADER
                + 'green_node,"(1, 2)",FALSE,FALSE,FALSE,FALSE\n'
                + OK_ROW
                + 'blue_node,"(1.5, 2)",true,FALSE,FALSE,FALSE\n')
    result, rotation, node_map = _runtime(path)
    assert result == Failure(
        "\nRow 2: Invalid node type 'green_node' in column 'nodetype'. Expected 'blue_node' or 'orange_node'."
        "\n\nRow 4: the tuple '(1.5, 2)' should only contain ints."
        "\nRow 4: Invalid value 'true' in column 'can_fall'. Expected TRUE or FALSE.")
    assert rotation == [] and node_map == {}
    assert _editor(path) == Failure("Row 2: invalid nodetype 'green_node', expected one of ['blue_node', 'orange_node']")


# --- header variants ------------------------------------------------------------------------------------------------

def test_header_columns_swapped(tmp_path):
    swapped = "pos_in_minimap,nodetype," + HEADER.split(",", 2)[2]
    path = _csv(tmp_path, swapped + OK_ROW)
    assert _runtime(path)[0] == Failure(
        "Invalid CSV header column name detected. Here is what we received as opposed to what we expected:\n"
        "Expected: nodetype, Received: pos_in_minimap\n"
        "Expected: pos_in_minimap, Received: nodetype")
    got = ["pos_in_minimap", "nodetype"] + HEADER_LIST[2:]
    assert _editor(path) == Failure(f"CSV header mismatch. Expected {HEADER_LIST}, got {got}")


def test_header_missing_column(tmp_path):
    path = _csv(tmp_path, ",".join(HEADER_LIST[:5]) + "\n" + 'blue_node,"(1, 2)",FALSE,FALSE,FALSE\n')
    assert _runtime(path)[0] == Failure(
        "number of expected CSV field names do not match actual number of CSV field names")
    assert _editor(path) == Failure(f"CSV header mismatch. Expected {HEADER_LIST}, got {HEADER_LIST[:5]}")


@pytest.mark.parametrize("text", ["", "# minimap_size=10,10\n"])
def test_no_header_runtime_raises_editor_refuses(tmp_path, text):
    path = _csv(tmp_path, text)
    with pytest.raises(TypeError):
        _runtime(path)
    assert _editor(path) == Failure(f"CSV header mismatch. Expected {HEADER_LIST}, got None")


def test_missing_file(tmp_path):
    path = tmp_path / "absent.csv"
    assert _runtime(path)[0] == Failure("CSV file points to an invalid path, failed to load CSV file.")
    assert _editor(path) == Failure(f"File not found: {path}")
    assert CSVParser().parseCSV([], {}) == Failure("CSV file is a None object, failed to load CSV file.")


# --- row shape ------------------------------------------------------------------------------------------------------

def test_extra_cell_runtime_raises_editor_accepts(tmp_path):
    path = _csv(tmp_path, HEADER + 'blue_node,"(1, 2)",FALSE,FALSE,FALSE,FALSE,EXTRA\n')
    with pytest.raises(TypeError):
        _runtime(path)
    assert _editor(path) == Success(([EditorNode(1, "blue_node", (1, 2))], [1], {}))


def test_short_row(tmp_path):
    path = _csv(tmp_path, HEADER + 'blue_node,"(1, 2)",FALSE\n')
    assert _runtime(path)[0] == Failure(
        "\nRow 2: Invalid value 'None' in column 'fall_dist_exceeds_vertical_tp'. Expected TRUE or FALSE."
        "\nRow 2: Invalid value 'None' in column 'is_left_platform_edge'. Expected TRUE or FALSE."
        "\nRow 2: Invalid value 'None' in column 'is_right_platform_edge'. Expected TRUE or FALSE.")
    assert _editor(path) == Failure("Row 2: fall_dist_exceeds_vertical_tp must be TRUE or FALSE, got None")


# --- position cell --------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("cell, runtime_msg, editor_msg", [
    ('"[1, 2]"', "Row 2: Value '[1, 2]' is not a tuple.",
     "Row 2: pos_in_minimap '[1, 2]' must be a 2-tuple of ints"),
    ('"(1, 2, 3)"', "Row 2: Value '(1, 2, 3)' should contain exactly 2 elements.",
     "Row 2: pos_in_minimap '(1, 2, 3)' must be a 2-tuple of ints"),
    ('"(1.5, 2)"', "Row 2: the tuple '(1.5, 2)' should only contain ints.",
     "Row 2: pos_in_minimap '(1.5, 2)' must be a 2-tuple of ints"),
    ("abc", "Row 2: Value 'abc' failed to be evaluated as a tuple. Ensure it is a valid 2-element tuple. Here is the "
            "exception error: malformed node or string on line 1: Name(id='abc', ctx=Load())",
     "Row 2: pos_in_minimap 'abc' not a valid literal: malformed node or string on line 1: Name(id='abc', ctx=Load())"),
])
def test_bad_position(tmp_path, cell, runtime_msg, editor_msg):
    path = _csv(tmp_path, HEADER + f"blue_node,{cell},FALSE,FALSE,FALSE,FALSE\n")
    assert _runtime(path)[0] == Failure("\n" + runtime_msg)
    assert _editor(path) == Failure(editor_msg)


def test_position_syntax_error_both_refuse(tmp_path):
    path = _csv(tmp_path, HEADER + 'blue_node,"(1,",FALSE,FALSE,FALSE,FALSE\n')
    runtime = _runtime(path)[0]
    assert not is_successful(runtime)
    assert runtime.failure().startswith("\nRow 2: Value '(1,' failed to be evaluated as a tuple.")
    editor = _editor(path)
    assert not is_successful(editor)
    assert editor.failure().startswith("Row 2: pos_in_minimap '(1,' not a valid literal: ")


def test_position_type_error_runtime_refuses_editor_raises(tmp_path):
    path = _csv(tmp_path, HEADER + 'blue_node,"{[1]: 2}",FALSE,FALSE,FALSE,FALSE\n')
    runtime = _runtime(path)[0]
    assert not is_successful(runtime)
    assert runtime.failure().startswith("\nRow 2: Value '{[1]: 2}' failed to be evaluated as a tuple. Ensure it is a "
                                        "valid 2-element tuple. Here is the exception error: ")
    with pytest.raises(TypeError):
        _editor(path)


def test_bool_tuple_position_accepted_by_both(tmp_path):
    path = _csv(tmp_path, HEADER + 'blue_node,"(True, 2)",FALSE,FALSE,FALSE,FALSE\n')
    result, _, node_map = _runtime(path)
    assert result == Success(None)
    assert [n.minimap_position for n in node_map.values()] == [(True, 2)]
    assert _editor(path).unwrap()[0][0].pos == (True, 2)


# --- boolean and nodetype cells -------------------------------------------------------------------------------------

@pytest.mark.parametrize("cell", ["true", " TRUE", "1", ""])
def test_bad_boolean(tmp_path, cell):
    path = _csv(tmp_path, HEADER + f'blue_node,"(1, 2)",{cell},FALSE,FALSE,FALSE\n')
    assert _runtime(path)[0] == Failure(
        f"\nRow 2: Invalid value '{cell}' in column 'can_fall'. Expected TRUE or FALSE.")
    assert _editor(path) == Failure(f"Row 2: can_fall must be TRUE or FALSE, got {cell!r}")


def test_flags_decode(tmp_path):
    path = _csv(tmp_path, HEADER + 'orange_node,"(3, 4)",TRUE,FALSE,TRUE,FALSE\n'
                + 'blue_node,"(5, 6)",FALSE,TRUE,FALSE,TRUE\n')
    _, _, node_map = _runtime(path)
    got = [(n.nodetype, n.minimap_position, n.can_fall, n.fall_dist_exceeds_vertical_tp, n.is_left_platform_edge,
            n.is_right_platform_edge) for n in node_map.values()]
    assert got == [("orange_node", (3, 4), True, False, True, False), ("blue_node", (5, 6), False, True, False, True)]
    nodes = _editor(path).unwrap()[0]
    assert nodes == [EditorNode(1, "orange_node", (3, 4), True, False, True, False),
                     EditorNode(2, "blue_node", (5, 6), False, True, False, True)]


# --- node insertion order and the id counter ------------------------------------------------------------------------

MIXED = (HEADER
         + 'orange_node,"(1, 1)",FALSE,FALSE,FALSE,FALSE\n'
         + 'blue_node,"(2, 2)",FALSE,FALSE,FALSE,FALSE\n'
         + 'orange_node,"(3, 3)",FALSE,FALSE,FALSE,FALSE\n'
         + 'blue_node,"(4, 4)",FALSE,FALSE,FALSE,FALSE\n')


def test_runtime_order_is_file_order_and_bumps_counter(tmp_path):
    path = _csv(tmp_path, MIXED)
    before = Node.node_id_counter
    result, rotation, node_map = _runtime(path)
    assert result == Success(None)
    assert Node.node_id_counter == before + 4
    assert [n.minimap_position for n in node_map.values()] == [(1, 1), (2, 2), (3, 3), (4, 4)]
    assert [n.minimap_position for n in rotation] == [(2, 2), (4, 4)]


def test_editor_order_is_file_order_and_does_not_bump_counter(tmp_path):
    path = _csv(tmp_path, MIXED)
    before = Node.node_id_counter
    nodes, rotation, _ = _editor(path).unwrap()
    assert Node.node_id_counter == before
    assert [(n.local_id, n.pos) for n in nodes] == [(1, (1, 1)), (2, (2, 2)), (3, (3, 3)), (4, (4, 4))]
    assert rotation == [2, 4]


# --- the bytes the editor writes ------------------------------------------------------------------------------------

def _editor_state():
    """Keys deliberately not in sorted order, rotation not in local_id order."""
    nodes = {
        3: EditorNode(3, "orange_node", (30, 3), is_left_platform_edge=True),
        1: EditorNode(1, "orange_node", (10, 1), can_fall=True),
        4: EditorNode(4, "blue_node", (40, 4), fall_dist_exceeds_vertical_tp=True, is_right_platform_edge=True),
        2: EditorNode(2, "blue_node", (20, 2)),
    }
    return nodes, [4, 2]


WRITTEN_ROWS = (
    b"nodetype,pos_in_minimap,can_fall,fall_dist_exceeds_vertical_tp,is_left_platform_edge,is_right_platform_edge\r\n"
    b'blue_node,"(40, 4)",FALSE,TRUE,FALSE,TRUE\r\n'
    b'blue_node,"(20, 2)",FALSE,FALSE,FALSE,FALSE\r\n'
    b'orange_node,"(10, 1)",TRUE,FALSE,FALSE,FALSE\r\n'
    b'orange_node,"(30, 3)",FALSE,FALSE,TRUE,FALSE\r\n'
)


def test_written_bytes_with_minimap_size(tmp_path):
    nodes, rotation = _editor_state()
    path = tmp_path / "sub" / "out.csv"
    assert write_editor_nodes(str(path), nodes, rotation, minimap_size=(162, 82)) == Success(None)
    assert path.read_bytes() == b"# minimap_size=162,82\r\n" + WRITTEN_ROWS


def test_written_bytes_without_minimap_size(tmp_path):
    nodes, rotation = _editor_state()
    path = tmp_path / "out.csv"
    assert write_editor_nodes(str(path), nodes, rotation) == Success(None)
    assert path.read_bytes() == WRITTEN_ROWS


def test_written_file_round_trips_through_both_readers(tmp_path):
    nodes, rotation = _editor_state()
    path = tmp_path / "out.csv"
    write_editor_nodes(str(path), nodes, rotation, minimap_size=(162, 82))
    result, runtime_rotation, node_map = _runtime(path)
    assert result == Success(None)
    assert [n.minimap_position for n in node_map.values()] == [(40, 4), (20, 2), (10, 1), (30, 3)]
    assert [n.minimap_position for n in runtime_rotation] == [(40, 4), (20, 2)]
    read_nodes, read_rotation, metadata = _editor(path).unwrap()
    assert [n.pos for n in read_nodes] == [(40, 4), (20, 2), (10, 1), (30, 3)]
    assert read_rotation == [1, 2] and metadata == {"minimap_size": (162, 82)}


def test_editor_preview_insertion_order_matches_written_row_order():
    from src.gui.map_editor import MapEditor

    nodes, rotation = _editor_state()
    fake_editor = SimpleNamespace(
        state=SimpleNamespace(nodes=nodes, rotation_order=rotation),
        controller=SimpleNamespace(agent=MapleAgent(characterClass=Kanna())),
    )
    path_graph, id_to_node = MapEditor._buildPreviewGraph(fake_editor)
    assert list(id_to_node) == [4, 2, 1, 3]
    assert [n.minimap_position for n in path_graph.node_map.values()] == [(40, 4), (20, 2), (10, 1), (30, 3)]
