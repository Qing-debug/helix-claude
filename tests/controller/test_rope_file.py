"""The per-map rope file (C8, H94): the reader's checks and messages (C11), the writer's replace that keeps the old file
on a failure, the unknown keys that survive a round trip, the keep-out test of H91 (D1) and the free x's the editor
suggests (D2). The fixture is the two ropes measured on Fossil Foundry 2 (Task 2, Step 4)."""
import json
from pathlib import Path

import pytest
from returns.pipeline import is_successful

from src.controller.rope_file import (FORMAT, RopeFile, RopeMark, free_x, near_rope_end, read_ropes,
                                      rope_file_for, write_ropes)

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "fossil_foundry_2.ropes.json"
FIX = ("open the map in the map editor, mark its ropes again (Find ropes or Add rope) and save; the save replaces "
       "this file")
ROPE_107 = RopeMark(107, 43, 61)
ROPE_46 = RopeMark(46, 28, 46)
GOOD = {"format": FORMAT, "version": 1, "minimap_size": [162, 82], "ropes": [{"x": 107, "top_y": 43, "bottom_y": 61}]}


def _document(**changes: object) -> dict[str, object]:
    document = dict(GOOD)
    document.update(changes)
    return document


def test_round_trip_gives_the_same_rope_file(tmp_path: Path):
    path = tmp_path / "ff2.ropes.json"
    written = RopeFile((ROPE_107, ROPE_46), (162, 82), {"platforms": [[1, 2]]})
    assert is_successful(write_ropes(path, written))
    assert is_successful(read_ropes(path))
    assert read_ropes(path).unwrap() == written


def test_written_file_has_format_version_and_a_final_newline(tmp_path: Path):
    path = tmp_path / "ff2.ropes.json"
    write_ropes(path, RopeFile((ROPE_107,), None, {}))
    text = path.read_text(encoding="utf-8")
    assert text.endswith("\n")
    assert json.loads(text) == {"format": FORMAT, "version": 1, "ropes": [{"x": 107, "top_y": 43, "bottom_y": 61}]}


def test_missing_file_reads_as_no_ropes(tmp_path: Path):
    assert read_ropes(tmp_path / "none.ropes.json").unwrap() == RopeFile((), None, {})


@pytest.mark.parametrize(("payload", "expected"), [
    pytest.param(b"{ nope", "is not JSON", id="not-json"),
    pytest.param(b'{"format": "helix-ropes", "version": 1, "ropes": [], "x": "\xe9"}', "is not JSON",
                 id="not-utf8"),
    pytest.param(_document(format="other"), '"format" must be', id="wrong-format"),
    pytest.param(_document(version=2), "made by a newer Helix", id="newer-version"),
    pytest.param(_document(version="1"), '"version" must be', id="version-text"),
    pytest.param(_document(minimap_size="162x82"), '"minimap_size" must be', id="size-text"),
    pytest.param(_document(minimap_size=[162]), '"minimap_size" must be', id="size-one-number"),
    pytest.param(_document(minimap_size=[162, True]), '"minimap_size" must be', id="size-bool"),
    pytest.param(_document(ropes={"x": 1}), '"ropes" must be a list', id="ropes-not-list"),
    pytest.param(_document(ropes=[{"top_y": 43, "bottom_y": 61}]), "rope 1:", id="rope-no-x"),
    pytest.param(_document(ropes=[{"x": True, "top_y": 43, "bottom_y": 61}]), '"x" must be a whole number',
                 id="rope-bool-x"),
    pytest.param(_document(ropes=[{"x": 107, "top_y": 61, "bottom_y": 43}]), '"top_y" must be less than',
                 id="rope-top-below-bottom"),
    pytest.param(_document(ropes=[{"x": -107, "top_y": 43, "bottom_y": 61}]), '"x" must not be negative',
                 id="rope-negative"),
])
def test_bad_file_is_refused_naming_the_file_the_rope_and_the_fix(tmp_path: Path, payload: object, expected: str):
    path = tmp_path / "ff2.ropes.json"
    path.write_bytes(payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8"))
    result = read_ropes(path)
    assert not is_successful(result)
    message = result.failure()
    assert str(path) in message
    assert expected in message
    assert FIX in message or "update Helix" in message


def test_two_bad_ropes_are_both_listed_by_number(tmp_path: Path):
    path = tmp_path / "ff2.ropes.json"
    ropes = [{"x": 1, "top_y": 5, "bottom_y": 2}, {"x": 2, "top_y": 1, "bottom_y": 9},
             {"top_y": 1, "bottom_y": 9}]
    path.write_text(json.dumps(_document(ropes=ropes)), encoding="utf-8")
    message = read_ropes(path).failure()
    assert "rope 1:" in message
    assert "rope 3:" in message
    assert "rope 2" not in message


def test_minimap_size_problem_names_the_key(tmp_path: Path):
    path = tmp_path / "ff2.ropes.json"
    path.write_text(json.dumps(_document(minimap_size="162x82")), encoding="utf-8")
    assert "minimap_size" in read_ropes(path).failure()


def test_folder_at_the_rope_file_path_is_a_failure_not_no_ropes(tmp_path: Path):
    folder = tmp_path / "ff2.ropes.json"
    folder.mkdir()
    result = read_ropes(folder)
    assert not is_successful(result)
    assert str(folder) in result.failure()


def test_a_path_that_cannot_be_read_is_a_failure_with_a_fix_not_no_ropes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # a stat error makes Path.exists() answer False; only "nothing at the path" may read as no ropes
    path = tmp_path / "ff2.ropes.json"
    path.write_text(json.dumps(GOOD), encoding="utf-8")
    real_read = Path.read_bytes

    def read_bytes(self):
        if self == path:
            raise PermissionError(13, "Access is denied")
        return real_read(self)

    monkeypatch.setattr(Path, "exists", lambda self: False)
    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    result = read_ropes(path)
    assert not is_successful(result)
    assert str(path) in result.failure()
    assert "Access is denied" in result.failure()
    assert "How to fix it:" in result.failure()


def test_unknown_top_level_key_survives_read_write_read(tmp_path: Path):
    source = tmp_path / "a.ropes.json"
    source.write_text(json.dumps(_document(platforms=[[1, 2, 3]])), encoding="utf-8")
    first = read_ropes(source).unwrap()
    copy = tmp_path / "b.ropes.json"
    assert is_successful(write_ropes(copy, first))
    assert json.loads(copy.read_text(encoding="utf-8"))["platforms"] == [[1, 2, 3]]
    assert read_ropes(copy).unwrap() == first


def test_failed_write_where_the_tmp_file_is_a_folder_keeps_the_old_file(tmp_path: Path):
    path = tmp_path / "ff2.ropes.json"
    path.write_text("old", encoding="utf-8")
    path.with_suffix(".tmp").mkdir()
    result = write_ropes(path, RopeFile((ROPE_46,), None, {}))
    assert not is_successful(result)
    assert path.read_text(encoding="utf-8") == "old"


def test_failed_write_where_a_folder_sits_at_the_path_is_a_failure(tmp_path: Path):
    path = tmp_path / "ff2.ropes.json"
    path.mkdir()
    result = write_ropes(path, RopeFile((ROPE_46,), None, {}))
    assert not is_successful(result)
    assert path.is_dir()


def test_rope_file_for_is_the_map_csv_with_ropes_json_suffix():
    assert rope_file_for(Path("maps/ff2.csv")) == Path("maps/ff2.ropes.json")


def test_keep_out_refuses_the_five_fossil_foundry_2_cases_of_d1():
    ropes = [ROPE_107]
    assert near_rope_end((105, 42), ropes) == ROPE_107
    assert near_rope_end((105, 60), ropes) == ROPE_107
    assert near_rope_end((113, 60), ropes) is None
    assert near_rope_end((97, 43), ropes) is None
    assert near_rope_end((109, 23), ropes) is None


def test_keep_out_box_edges():
    assert near_rope_end((101, 43), [ROPE_107]) == ROPE_107
    assert near_rope_end((111, 43), [ROPE_107]) == ROPE_107
    assert near_rope_end((100, 43), [ROPE_107]) is None
    assert near_rope_end((112, 43), [ROPE_107]) is None
    assert near_rope_end((106, 37), [ROPE_107]) == ROPE_107
    assert near_rope_end((106, 49), [ROPE_107]) == ROPE_107
    assert near_rope_end((106, 36), [ROPE_107]) is None
    assert near_rope_end((106, 50), [ROPE_107]) is None


def test_free_x_is_the_nearest_allowed_x_each_side():
    assert free_x(ROPE_107) == (100, 112)
    assert free_x(ROPE_46) == (39, 51)


def test_free_x_is_allowed_and_one_step_inside_is_refused_at_the_top_end():
    left, right = free_x(ROPE_107)
    assert near_rope_end((left, ROPE_107.top_y), [ROPE_107]) is None
    assert near_rope_end((right, ROPE_107.top_y), [ROPE_107]) is None
    assert near_rope_end((left + 1, ROPE_107.top_y), [ROPE_107]) == ROPE_107
    assert near_rope_end((right - 1, ROPE_107.top_y), [ROPE_107]) == ROPE_107
    left, right = free_x(ROPE_46)
    assert near_rope_end((left, ROPE_46.top_y), [ROPE_46]) is None
    assert near_rope_end((right, ROPE_46.top_y), [ROPE_46]) is None
    assert near_rope_end((left + 1, ROPE_46.top_y), [ROPE_46]) == ROPE_46
    assert near_rope_end((right - 1, ROPE_46.top_y), [ROPE_46]) == ROPE_46


def test_fossil_foundry_2_fixture_reads_back_as_the_two_measured_ropes():
    result = read_ropes(FIXTURE)
    assert is_successful(result)
    assert result.unwrap() == RopeFile((ROPE_46, ROPE_107), (162, 82), {})


# -- the remembered deletions (AUTOFIND163 I2; Hong, rope163-1: "Remember deleted ropes in the map's rope file, so a rope
# you delete stays deleted") ---------------------------------------------------------------------------------------------

def test_deleted_ropes_round_trip_beside_the_ropes(tmp_path: Path):
    path = tmp_path / "ff2.ropes.json"
    written = RopeFile((ROPE_107,), (162, 82), {"platforms": [1]}, deleted=(ROPE_46,))
    assert is_successful(write_ropes(path, written))
    assert json.loads(path.read_text(encoding="utf-8"))["deleted_ropes"] == [{"x": 46, "top_y": 28, "bottom_y": 46}]
    assert read_ropes(path).unwrap() == written


def test_a_file_without_deleted_ropes_reads_as_none_and_is_written_without_the_key(tmp_path: Path):
    path = tmp_path / "old.ropes.json"
    path.write_text(json.dumps(GOOD), encoding="utf-8")  # a file as the editor wrote it before this key existed
    assert read_ropes(path).unwrap().deleted == ()
    write_ropes(path, read_ropes(path).unwrap())
    assert "deleted_ropes" not in json.loads(path.read_text(encoding="utf-8"))  # no empty key added to every file


def test_the_deleted_key_is_known_so_it_is_not_kept_as_an_unknown_one(tmp_path: Path):
    path = tmp_path / "a.ropes.json"
    path.write_text(json.dumps(_document(deleted_ropes=[{"x": 46, "top_y": 28, "bottom_y": 46}], platforms=[1])),
                    encoding="utf-8")
    loaded = read_ropes(path).unwrap()
    assert loaded.deleted == (ROPE_46,) and loaded.extra == {"platforms": [1]}


@pytest.mark.parametrize(("payload", "expected"), [
    pytest.param(_document(deleted_ropes={"x": 1}), '"deleted_ropes" must be a list', id="not-a-list"),
    pytest.param(_document(deleted_ropes=[{"top_y": 43, "bottom_y": 61}]), "deleted rope 1:", id="no-x"),
    pytest.param(_document(deleted_ropes=[{"x": 4, "top_y": 61, "bottom_y": 43}]), '"top_y" must be less than',
                 id="top-below-bottom"),
])
def test_a_bad_deleted_rope_is_refused_by_number_like_a_bad_rope(tmp_path: Path, payload: object, expected: str):
    path = tmp_path / "bad.ropes.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    result = read_ropes(path)
    assert not is_successful(result) and expected in result.failure() and str(path) in result.failure()
