"""Characterization tests (CLEAN217 W0-3): how the two map-CSV readers treat line endings, a quoted cell that spans lines,
and a file that cannot be decoded.

The runtime parser (`csv_parser.CSVParser`) opens the map as `open(path)` (line ends translated to "\\n", lines read as
`read_rows` asks for them) and the editor reader (`map_csv.read_editor_nodes`) as `open(path, "r", newline="")` followed
by `readlines()` (line ends kept, the whole file read first). Each keeps its own way of opening the file, so a refactor
that shares the loading code must not change what either shows. The cases below pin what each does today; the two may
differ from each other, and where they do the test says so.
"""
from pathlib import Path

import pytest
from returns.result import Failure, Success

from src.controller.csv_parser import CSVParser
from src.controller.map_csv import EditorNode, read_editor_nodes
from src.controller.node import Node

HEADER = "nodetype,pos_in_minimap,can_fall,fall_dist_exceeds_vertical_tp,is_left_platform_edge,is_right_platform_edge"
BLUE = 'blue_node,"(1, 2)",FALSE,FALSE,FALSE,FALSE'
ORANGE = 'orange_node,"(3, 4)",TRUE,FALSE,TRUE,FALSE'
RED = 'red_node,"(5, 6)",FALSE,FALSE,FALSE,FALSE'
META_FIX = 'it must be two whole numbers above 0, like "# minimap_size=162,82". Fix it, or delete the line.'
WRONG_COUNT = ("cells, expected 6 (nodetype, pos_in_minimap, can_fall, fall_dist_exceeds_vertical_tp, "
               "is_left_platform_edge, is_right_platform_edge). Look for a missing or extra comma, or a quote that is "
               'not closed; pos_in_minimap needs its quotes, like "(12, 34)".')

EOLS = pytest.mark.parametrize("eol", ["\r\n", "\r", "\n"], ids=["crlf", "lone-cr", "lf"])


def _file(tmp_path: Path, eol: str, *lines: str) -> Path:
    """The lines, each ended by `eol`, written byte for byte (no translation)."""
    path = tmp_path / "map.csv"
    path.write_text("".join(line + eol for line in lines), newline="")
    return path


def _runtime(path: Path):
    rotation, node_map = [], {}
    return CSVParser(CSV=path).parseCSV(rotation, node_map), rotation, node_map


def _refused(path: Path, *problems: str) -> Failure:
    count = "the problem" if len(problems) == 1 else f"the {len(problems)} problems"
    return Failure(f"The map file {path} is not in the expected format, so it was not loaded. Fix {count} below in a "
                   "text editor, then load it again.\n\n" + "\n".join(problems))


def _both_refuse(path: Path, *problems: str) -> None:
    before = Node.node_id_counter
    result, rotation, node_map = _runtime(path)
    assert result == _refused(path, *problems)
    assert rotation == [] and node_map == {}
    assert Node.node_id_counter == before
    assert read_editor_nodes(str(path)) == _refused(path, *problems)


# --- line endings ---------------------------------------------------------------------------------------------------

@EOLS
def test_a_good_file_loads_the_same_whatever_its_line_ends(tmp_path, eol):
    path = _file(tmp_path, eol, "# minimap_size=162,82", HEADER, BLUE, "", ORANGE)
    result, rotation, node_map = _runtime(path)
    assert result == Success(None)
    assert [(n.nodetype, n.minimap_position) for n in node_map.values()] == [("blue_node", (1, 2)),
                                                                              ("orange_node", (3, 4))]
    assert [n.minimap_position for n in rotation] == [(1, 2)]
    nodes, rotation_ids, metadata = read_editor_nodes(str(path)).unwrap()
    assert nodes == [EditorNode(1, "blue_node", (1, 2)), EditorNode(2, "orange_node", (3, 4), True, False, True, False)]
    assert rotation_ids == [1]
    assert metadata == {"minimap_size": (162, 82)}


def test_a_file_that_mixes_line_ends_loads_and_counts_lines_like_any_other(tmp_path):
    path = tmp_path / "map.csv"
    path.write_text("# note\r\n" + HEADER + "\r" + BLUE + "\n" + RED + "\r\n" + ORANGE + "\r", newline="")
    _both_refuse(path, "line 4: nodetype is 'red_node'; it must be blue_node or orange_node.")
    path.write_text("# note\r\n" + HEADER + "\r" + BLUE + "\n" + ORANGE + "\r", newline="")
    assert _runtime(path)[0] == Success(None)
    assert [n.pos for n in read_editor_nodes(str(path)).unwrap()[0]] == [(1, 2), (3, 4)]


@EOLS
def test_a_problem_gets_the_same_line_number_whatever_the_line_ends(tmp_path, eol):
    _both_refuse(_file(tmp_path, eol, "# note", HEADER, BLUE, RED, ORANGE),
                 "line 4: nodetype is 'red_node'; it must be blue_node or orange_node.")


@EOLS
def test_a_bad_header_is_shown_without_its_line_end(tmp_path, eol):
    path = _file(tmp_path, eol, "# note", "nodetype,pos_in_minimap", BLUE)
    _both_refuse(path, "line 2: the header row is not the expected one. Replace the line with the expected one.\n"
                       f"    expected: {HEADER}\n"
                       "    found:    nodetype,pos_in_minimap")


@EOLS
def test_the_editor_shows_a_bad_metadata_line_without_its_line_end(tmp_path, eol):
    path = _file(tmp_path, eol, "# note", "# minimap_size=abc", HEADER, BLUE)
    assert read_editor_nodes(str(path)) == _refused(
        path, f'line 2: the "# minimap_size=" line is \'# minimap_size=abc\'; {META_FIX}')
    assert _runtime(path)[0] == Success(None)  # the runtime parser does not check the metadata


# --- a quoted cell cannot span lines --------------------------------------------------------------------------------

@EOLS
def test_a_quoted_cell_that_spans_lines_is_two_bad_lines_in_both_readers(tmp_path, eol):
    """Both readers split the file into lines first, so a quoted position that continues on the next line is read as
    two rows, each with the wrong number of cells."""
    path = _file(tmp_path, eol, HEADER, 'blue_node,"(1,', '2)",FALSE,FALSE,FALSE,FALSE', ORANGE)
    _both_refuse(path, f"line 2: 2 {WRONG_COUNT}", f"line 3: 5 {WRONG_COUNT}")


def test_a_line_end_inside_a_quoted_cell_of_a_header_row_is_a_bad_header(tmp_path):
    path = _file(tmp_path, "\r\n", 'nodetype,"pos_in_minimap', 'x",can_fall', BLUE)
    _both_refuse(path, "line 1: the header row is not the expected one. Replace the line with the expected one.\n"
                       f"    expected: {HEADER}\n"
                       '    found:    nodetype,"pos_in_minimap')


# --- how each reader opens the file ---------------------------------------------------------------------------------

def _undecodable_byte(tmp_path: Path) -> bytes:
    """A byte the default text encoding of `open()` cannot decode (this machine's, whatever it is)."""
    probe = tmp_path / "probe.txt"
    for value in range(0x80, 0x100):
        probe.write_bytes(bytes([value]))
        try:
            with open(probe) as f:
                f.read()
        except UnicodeDecodeError:
            return bytes([value])
    pytest.skip("this machine's default text encoding decodes every byte")


def test_the_runtime_parser_reads_lazily_and_the_editor_reads_the_whole_file_first(tmp_path):
    """A bad header ends the runtime parser's read there, so bytes it cannot decode far below are never reached; the
    editor reads every line before it checks any, so it reports the file as unreadable."""
    bad = _undecodable_byte(tmp_path)
    path = tmp_path / "map.csv"
    path.write_bytes(b"nodetype,pos_in_minimap\n" + b"# padding that pushes the bad byte past any read-ahead\n" * 8000
                     + b"# " + bad + b"\n")

    assert _runtime(path)[0] == _refused(
        path, "line 1: the header row is not the expected one. Replace the line with the expected one.\n"
              f"    expected: {HEADER}\n"
              "    found:    nodetype,pos_in_minimap")
    result = read_editor_nodes(str(path))
    assert isinstance(result, Failure)
    assert result.failure().startswith(f"The map file {path} could not be read, so it was not loaded: ")


def test_both_readers_report_a_file_they_cannot_decode_as_unreadable(tmp_path):
    bad = _undecodable_byte(tmp_path)
    path = tmp_path / "map.csv"
    path.write_bytes(HEADER.encode() + b"\n" + BLUE.encode() + b"\n# " + bad + b"\n")
    prefix = f"The map file {path} could not be read, so it was not loaded: "
    runtime = _runtime(path)[0]
    assert isinstance(runtime, Failure) and runtime.failure().startswith(prefix)
    editor = read_editor_nodes(str(path))
    assert isinstance(editor, Failure) and editor.failure().startswith(prefix)
    assert editor.failure() == runtime.failure()  # the same decode error text, so the same message


def test_the_editor_names_its_path_as_given_and_the_runtime_parser_as_a_path_object(tmp_path):
    """The messages print the path the reader was given: the editor's `str` as typed, the runtime's `Path` as Windows
    spells it (backslashes on Windows)."""
    given = (tmp_path / "sub" / ".." / "absent.csv").as_posix()
    editor = read_editor_nodes(given)
    assert editor == Failure(f"The map file {given} does not exist, so it was not loaded. Pick another file.")
    runtime = _runtime(Path(given))[0]
    assert runtime == Failure(f"The map file {Path(given)} does not exist, so it was not loaded. Pick another file.")
