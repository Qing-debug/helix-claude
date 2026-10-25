"""Characterization tests: what the two map-CSV readers accept and reject today, the bytes the editor writes, and the
node insertion order of the runtime parser, the editor reader and the editor's edge preview.

The runtime parser (`csv_parser.CSVParser`) and the editor reader (`map_csv.read_editor_nodes`) share one file check
and its messages since decision H16 (`map_format.read_rows`). The differences left (the editor's strict
`# minimap_size=` line, the runtime's message for a `None` path) are pinned here too, so a refactor cannot change
either reader. Changing what they accept or the words they show is a behaviour change, not a refactor.
"""
from pathlib import Path
from types import SimpleNamespace

import pytest
from returns.result import Failure, Success

from src.classes.classes import Kanna
from src.controller.controller import MapleAgent
from src.controller.csv_parser import CSVParser
from src.controller.map_csv import EditorNode, read_editor_nodes, write_editor_nodes
from src.controller.node import Node

HEADER = "nodetype,pos_in_minimap,can_fall,fall_dist_exceeds_vertical_tp,is_left_platform_edge,is_right_platform_edge\n"
HEADER_LIST = ["nodetype", "pos_in_minimap", "can_fall", "fall_dist_exceeds_vertical_tp", "is_left_platform_edge",
               "is_right_platform_edge"]
EXPECTED_HEADER_LINE = HEADER.rstrip("\n")
OK_ROW = 'blue_node,"(1, 2)",FALSE,FALSE,FALSE,FALSE\n'
POS_FIX = 'it must be two whole numbers in quotes, like "(12, 34)".'


def _csv(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "map.csv"
    path.write_text(text, newline="")
    return path


def _runtime(path: Path):
    rotation, node_map = [], {}
    return CSVParser(CSV=path).parseCSV(rotation, node_map), rotation, node_map


def _editor(path: Path):
    return read_editor_nodes(str(path))


def _refused(path: Path, *problems: str) -> Failure:
    """The message both readers return for a file with these problems, pinned word for word (deliberately not built
    with map_format.problems_message)."""
    count = "the problem" if len(problems) == 1 else f"the {len(problems)} problems"
    return Failure(f"The map file {path} is not in the expected format, so it was not loaded. Fix {count} below in a "
                   "text editor, then load it again.\n\n" + "\n".join(problems))


def _both_refuse(path: Path, *problems: str) -> None:
    before = Node.node_id_counter
    result, rotation, node_map = _runtime(path)
    assert result == _refused(path, *problems)
    assert rotation == [] and node_map == {}
    assert Node.node_id_counter == before  # no Node was built, not even for the good rows
    assert _editor(path) == _refused(path, *problems)


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


# --- several bad rows: both readers report every problem ------------------------------------------------------------

def test_several_bad_rows(tmp_path):
    path = _csv(tmp_path, HEADER
                + 'green_node,"(1, 2)",FALSE,FALSE,FALSE,FALSE\n'
                + OK_ROW
                + 'blue_node,"(1.5, 2)",true,FALSE,FALSE,FALSE\n')
    _both_refuse(path,
                 "line 2: nodetype is 'green_node'; it must be blue_node or orange_node.",
                 f"line 4: pos_in_minimap is '(1.5, 2)'; {POS_FIX}",
                 "line 4: can_fall is 'true'; it must be TRUE or FALSE (capitals, no spaces).")


def test_line_numbers_count_comment_and_blank_lines(tmp_path):
    path = _csv(tmp_path, "# minimap_size=10,10\n\n" + HEADER + "\n   \n# note\n"
                + 'red_node,"(1, 2)",FALSE,FALSE,FALSE,FALSE\n')
    _both_refuse(path, "line 7: nodetype is 'red_node'; it must be blue_node or orange_node.")


def test_more_than_twenty_problems_are_cut_short(tmp_path):
    path = _csv(tmp_path, HEADER + 'x,"(1, 2)",FALSE,FALSE,FALSE,FALSE\n' * 25)
    shown = [f"line {n}: nodetype is 'x'; it must be blue_node or orange_node." for n in range(2, 22)]
    expected = _refused(path, *shown, "... and 5 more.").failure().replace("Fix the 21 problems", "Fix the 25 problems")
    assert _runtime(path)[0] == Failure(expected)
    assert _editor(path) == Failure(expected)


# --- header variants: a bad header is the only problem reported -----------------------------------------------------

def _header_problem(number: int, found: str) -> str:
    return (f"line {number}: the header row is not the expected one. Replace the line with the expected one.\n"
            f"    expected: {EXPECTED_HEADER_LINE}\n"
            f"    found:    {found}")


def test_header_columns_swapped(tmp_path):
    swapped = "pos_in_minimap,nodetype," + HEADER.split(",", 2)[2]
    path = _csv(tmp_path, swapped + OK_ROW)
    _both_refuse(path, _header_problem(1, swapped.rstrip("\n")))


def test_header_missing_column(tmp_path):
    header = ",".join(HEADER_LIST[:5])
    path = _csv(tmp_path, "# minimap_size=10,10\n" + header + "\n" + 'blue_node,"(1, 2)",FALSE,FALSE,FALSE\n')
    _both_refuse(path, _header_problem(2, header))


def test_header_trailing_comma(tmp_path):
    path = _csv(tmp_path, HEADER.rstrip("\n") + ",\n" + OK_ROW)
    _both_refuse(path, _header_problem(1, EXPECTED_HEADER_LINE + ","))


@pytest.mark.parametrize("text", ["", "# minimap_size=10,10\n", "\n\n# note\n"])
def test_no_header(tmp_path, text):
    path = _csv(tmp_path, text)
    _both_refuse(path, "The file has no header row (it is empty or has only # lines). Its first line that is not a # "
                       f"line must be:\n    {EXPECTED_HEADER_LINE}")


def test_header_only_file_is_accepted(tmp_path):
    path = _csv(tmp_path, HEADER)
    assert _runtime(path) == (Success(None), [], {})
    assert _editor(path) == Success(([], [], {}))


def test_missing_file(tmp_path):
    path = tmp_path / "absent.csv"
    message = Failure(f"The map file {path} does not exist, so it was not loaded. Pick another file.")
    assert _runtime(path)[0] == message
    assert _editor(path) == message
    assert CSVParser().parseCSV([], {}) == Failure("CSV file is a None object, failed to load CSV file.")


# --- row shape ------------------------------------------------------------------------------------------------------

WRONG_COUNT = ("cells, expected 6 (nodetype, pos_in_minimap, can_fall, fall_dist_exceeds_vertical_tp, "
               "is_left_platform_edge, is_right_platform_edge). Look for a missing or extra comma; pos_in_minimap "
               'needs its quotes, like "(12, 34)".')
TRAILING = "cells, expected 6. Remove the extra comma(s) at the end of the line."


@pytest.mark.parametrize("row, problem", [
    ('blue_node,"(1, 2)",FALSE,FALSE,FALSE,FALSE,EXTRA', f"line 2: 7 {WRONG_COUNT}"),
    ('blue_node,"(1, 2)",FALSE', f"line 2: 3 {WRONG_COUNT}"),
    ("blue_node,(1, 2),FALSE,FALSE,FALSE,FALSE", f"line 2: 7 {WRONG_COUNT}"),
    ('blue_node,"(1, 2)",FALSE,FALSE,FALSE,FALSE,', f"line 2: 7 {TRAILING}"),
    ('blue_node,"(1, 2)",FALSE,FALSE,FALSE,FALSE,, ', f"line 2: 8 {TRAILING}"),
    (",,,,,", "line 2: the row is empty (only commas). Delete the line."),
    (",,,,,,,", "line 2: the row is empty (only commas). Delete the line."),
])
def test_wrong_cell_count(tmp_path, row, problem):
    _both_refuse(_csv(tmp_path, HEADER + row + "\n"), problem)


# --- position cell --------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("cell, shown", [
    ('"[1, 2]"', "[1, 2]"),
    ('"(1, 2, 3)"', "(1, 2, 3)"),
    ('"(1.5, 2)"', "(1.5, 2)"),
    ("abc", "abc"),
    ('"(1,"', "(1,"),            # SyntaxError
    ('"{[1]: 2}"', "{[1]: 2}"),  # TypeError: the editor raised it before H16
    ('""', ""),
])
def test_bad_position(tmp_path, cell, shown):
    path = _csv(tmp_path, HEADER + f"blue_node,{cell},FALSE,FALSE,FALSE,FALSE\n")
    _both_refuse(path, f"line 2: pos_in_minimap is {shown!r}; {POS_FIX}")


def test_bool_tuple_position_accepted_by_both(tmp_path):
    path = _csv(tmp_path, HEADER + 'blue_node,"(True, 2)",FALSE,FALSE,FALSE,FALSE\n')
    result, _, node_map = _runtime(path)
    assert result == Success(None)
    assert [n.minimap_position for n in node_map.values()] == [(True, 2)]
    assert _editor(path).unwrap()[0][0].pos == (True, 2)


# --- boolean and nodetype cells -------------------------------------------------------------------------------------

@pytest.mark.parametrize("cell", ["true", " TRUE", "TRUE ", "1", ""])
def test_bad_boolean(tmp_path, cell):
    path = _csv(tmp_path, HEADER + f'blue_node,"(1, 2)",{cell},FALSE,FALSE,FALSE\n')
    _both_refuse(path, f"line 2: can_fall is {cell!r}; it must be TRUE or FALSE (capitals, no spaces).")


@pytest.mark.parametrize("cell", ["Blue_node", "blue", " blue_node", ""])
def test_bad_nodetype(tmp_path, cell):
    path = _csv(tmp_path, HEADER + f'{cell},"(1, 2)",FALSE,FALSE,FALSE,FALSE\n')
    _both_refuse(path, f"line 2: nodetype is {cell!r}; it must be blue_node or orange_node.")


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
