"""Characterization tests (CLEAN217 W0-3): the rope file's exact bytes on write and its exact message on a refused read.

`test_rope_file.py` pins the reader's checks by substring. These pin the whole text, so that moving the reader's two
rope loops and the writer's two rope dicts into shared helpers cannot change a byte of the file the editor saves or a
word of the message the walk and the editor show.
"""
from pathlib import Path

from returns.pipeline import is_successful

from src.controller.rope_file import RopeFile, RopeMark, read_ropes, write_ropes

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "fossil_foundry_2.ropes.json"
ROPE_46 = RopeMark(46, 28, 46)
ROPE_107 = RopeMark(107, 43, 61)
FIX = ("open the map in the map editor, mark its ropes again (Find ropes or Add rope) and save; the save replaces "
       "this file")

# Everything the file can hold: the size, two ropes, a remembered deletion and two keys this version does not know
# (kept after the known ones, in their order).
FULL_FILE = RopeFile((ROPE_46, ROPE_107), (162, 82), {"note": "café", "platforms": [[1, 2]]}, deleted=(ROPE_107,))
FULL_BYTES = (
    b'{\n'
    b'  "format": "helix-ropes",\n'
    b'  "version": 1,\n'
    b'  "minimap_size": [\n'
    b'    162,\n'
    b'    82\n'
    b'  ],\n'
    b'  "ropes": [\n'
    b'    {\n'
    b'      "x": 46,\n'
    b'      "top_y": 28,\n'
    b'      "bottom_y": 46\n'
    b'    },\n'
    b'    {\n'
    b'      "x": 107,\n'
    b'      "top_y": 43,\n'
    b'      "bottom_y": 61\n'
    b'    }\n'
    b'  ],\n'
    b'  "deleted_ropes": [\n'
    b'    {\n'
    b'      "x": 107,\n'
    b'      "top_y": 43,\n'
    b'      "bottom_y": 61\n'
    b'    }\n'
    b'  ],\n'
    b'  "note": "caf\xc3\xa9",\n'
    b'  "platforms": [\n'
    b'    [\n'
    b'      1,\n'
    b'      2\n'
    b'    ]\n'
    b'  ]\n'
    b'}\n'
)


def test_the_fixture_rope_file_round_trips_byte_for_byte(tmp_path: Path):
    """Git stores the fixture with LF and a Windows checkout may turn that into CRLF (core.autocrlf); the writer always
    writes LF, so the comparison is against the fixture's LF form."""
    copy = tmp_path / "copy.ropes.json"
    assert is_successful(write_ropes(copy, read_ropes(FIXTURE).unwrap()))
    assert copy.read_bytes() == FIXTURE.read_bytes().replace(b"\r\n", b"\n")


def test_every_field_is_written_in_this_exact_form(tmp_path: Path):
    path = tmp_path / "full.ropes.json"
    assert is_successful(write_ropes(path, FULL_FILE))
    assert path.read_bytes() == FULL_BYTES


def test_a_file_with_every_field_reads_back_and_writes_the_same_bytes(tmp_path: Path):
    source = tmp_path / "a.ropes.json"
    source.write_bytes(FULL_BYTES)
    assert read_ropes(source).unwrap() == FULL_FILE
    copy = tmp_path / "b.ropes.json"
    assert is_successful(write_ropes(copy, read_ropes(source).unwrap()))
    assert copy.read_bytes() == FULL_BYTES


def test_an_empty_rope_file_is_written_with_no_size_and_no_deleted_key(tmp_path: Path):
    path = tmp_path / "empty.ropes.json"
    assert is_successful(write_ropes(path, RopeFile((), None, {})))
    assert path.read_bytes() == b'{\n  "format": "helix-ropes",\n  "version": 1,\n  "ropes": []\n}\n'


def _refusal(path: Path, *problems: str, fix: str = FIX) -> str:
    return "\n".join([f"{path} cannot be used:", *(f"  - {problem}" for problem in problems), f"How to fix it: {fix}."])


def test_every_problem_of_a_refused_file_is_listed_in_the_order_the_file_is_checked(tmp_path: Path):
    path = tmp_path / "bad.ropes.json"
    path.write_bytes(
        b'{"format": "other", "version": 0, "minimap_size": [162], '
        b'"ropes": [{"x": 1, "top_y": 5, "bottom_y": 2}, {"top_y": 1, "bottom_y": 9}, 4, {"x": 2, "top_y": 1, "bottom_y": 9}], '
        b'"deleted_ropes": [{"x": 4, "top_y": 61, "bottom_y": 43}, 7, {"x": -1, "top_y": "a", "bottom_y": 3}]}')
    result = read_ropes(path)
    assert not is_successful(result)
    assert result.failure() == _refusal(
        path,
        '"format" must be "helix-ropes"',
        '"version" must be the whole number 1',
        '"minimap_size" must be a list of two positive whole numbers, like [162, 82]',
        'rope 1: "top_y" must be less than "bottom_y"',
        'rope 2: "x" must be a whole number',
        "rope 3: it is not an object with x, top_y and bottom_y",
        'deleted rope 1: "top_y" must be less than "bottom_y"',
        "deleted rope 2: it is not an object with x, top_y and bottom_y",
        'deleted rope 3: "x" must not be negative',
        'deleted rope 3: "top_y" must be a whole number',
    )


def test_a_missing_or_wrong_ropes_list_and_a_wrong_deleted_list_are_named(tmp_path: Path):
    path = tmp_path / "bad.ropes.json"
    path.write_bytes(b'{"format": "helix-ropes", "version": 1, "deleted_ropes": "none"}')
    assert read_ropes(path).failure() == _refusal(path, '"ropes" must be a list', '"deleted_ropes" must be a list')
    path.write_bytes(b'{"format": "helix-ropes", "version": 1, "ropes": {}, "deleted_ropes": null}')
    assert read_ropes(path).failure() == _refusal(path, '"ropes" must be a list', '"deleted_ropes" must be a list')


def test_a_file_from_a_newer_helix_gets_the_update_fix_with_its_rope_problems(tmp_path: Path):
    path = tmp_path / "new.ropes.json"
    path.write_bytes(b'{"format": "helix-ropes", "version": 2, "ropes": [], "deleted_ropes": [{"x": 1}]}')
    assert read_ropes(path).failure() == _refusal(
        path, "version 2 was made by a newer Helix", 'deleted rope 1: "top_y" must be a whole number',
        'deleted rope 1: "bottom_y" must be a whole number', fix="update Helix, which reads rope files of version 1")
