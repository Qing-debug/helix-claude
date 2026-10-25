"""Movement measurement tool (plan task B20), piece 1: `MoveSample`, `summarize`, the jsonl line and `main`'s checks.

All synthetic: no board, no game, no screen capture.
"""

import json

import pytest

from src.route.harness import measure_moves
from src.route.harness.measure_moves import MEASURE_FILE, MoveSample, append_sample, main, summarize

SUMMARY_KEYS = {"reach_h_mean", "reach_h_sd", "reach_h_n", "reach_v_peak_mean", "walk_px_per_s",
                "teleport_settle_s", "drop_settle_s"}


def teleport(direction: str, dx: int, dy: int = 0, settle_s: float = 0.4, peak_y: int | None = None,
             start: tuple[int, int] = (80, 100)) -> MoveSample:
    return MoveSample("teleport", direction, start, (start[0] + dx, start[1] + dy), peak_y, settle_s, None)


def walk(direction: str, dx: int, held_ms: float = 2000.0, start: tuple[int, int] = (80, 100)) -> MoveSample:
    return MoveSample("walk", direction, start, (start[0] + dx, start[1]), None, 0.2, held_ms)


def drop(dy: int, settle_s: float) -> MoveSample:
    return MoveSample("drop", None, (80, 60), (80, 60 + dy), None, settle_s, None)


# ---- the plan's four cases ------------------------------------------------------------------------------------------

def test_alternating_19_and_20_teleports_give_mean_19_5_over_10():
    samples = [teleport("right", 19) if i % 2 == 0 else teleport("left", -20) for i in range(10)]
    summary = summarize(samples)
    assert summary["reach_h_n"] == 10
    assert summary["reach_h_mean"] == pytest.approx(19.5)


def test_teleport_with_dy_5_is_excluded_from_reach():
    samples = [teleport("right", 19), teleport("left", -20), teleport("right", 30, dy=5)]
    summary = summarize(samples)
    assert summary["reach_h_n"] == 2
    assert summary["reach_h_mean"] == pytest.approx(19.5)


def test_walks_of_16_px_over_2000_ms_give_8_px_per_s():
    samples = [walk("right", 16), walk("left", -16), walk("right", 16)]
    assert summarize(samples)["walk_px_per_s"] == pytest.approx(8.0)


def test_main_without_supervised_returns_2(tmp_path, capsys):
    assert main(["--out", str(tmp_path / "m")]) == 2
    assert "--supervised" in capsys.readouterr().err
    assert not (tmp_path / "m").exists()


# ---- summarize, beyond the plan's cases -----------------------------------------------------------------------------

def test_summary_has_exactly_the_plan_keys():
    assert set(summarize([])) == SUMMARY_KEYS


def test_empty_samples_give_none_and_zero_n():
    assert summarize([]) == {key: (0 if key == "reach_h_n" else None) for key in SUMMARY_KEYS}


def test_dy_2_is_kept_and_dy_3_is_not():
    summary = summarize([teleport("right", 19, dy=2), teleport("left", -19, dy=-2), teleport("right", 19, dy=3)])
    assert summary["reach_h_n"] == 2


def test_vertical_and_downward_teleports_do_not_count_as_horizontal_reach():
    summary = summarize([teleport("right", 20), teleport("up", 0, dy=-1), teleport("down", 0, dy=1)])
    assert summary["reach_h_n"] == 1


def test_reach_sd_is_the_sample_sd_and_none_below_two():
    assert summarize([teleport("right", 19)])["reach_h_sd"] is None
    summary = summarize([teleport("right", 18), teleport("left", -20)])
    assert summary["reach_h_sd"] == pytest.approx(2 ** 0.5)


def test_vertical_peak_reach_is_rise_to_smallest_y_of_up_teleports_with_peak():
    samples = [teleport("up", 0, dy=-20, peak_y=100 - 22), teleport("up", 0, dy=-20, peak_y=100 - 24),
               teleport("up", 0, dy=0, peak_y=None), teleport("right", 20, peak_y=10)]
    assert summarize(samples)["reach_v_peak_mean"] == pytest.approx(23.0)


def test_walks_without_held_time_are_left_out_of_speed():
    samples = [walk("right", 16), MoveSample("walk", "left", (80, 100), (40, 100), None, 0.2, None)]
    assert summarize(samples)["walk_px_per_s"] == pytest.approx(8.0)


def test_settle_times_are_medians_per_kind():
    samples = [teleport("right", 19, settle_s=0.3), teleport("left", -19, settle_s=0.9),
               teleport("down", 0, dy=10, settle_s=0.5), drop(15, 0.6), drop(15, 0.8), walk("right", 16)]
    summary = summarize(samples)
    assert summary["teleport_settle_s"] == pytest.approx(0.5)
    assert summary["drop_settle_s"] == pytest.approx(0.7)


# ---- measure.jsonl ---------------------------------------------------------------------------------------------------

def test_append_sample_writes_one_json_object_per_line(tmp_path):
    path = tmp_path / MEASURE_FILE
    first, second = teleport("up", 0, dy=-20, peak_y=77), walk("left", -16)
    append_sample(path, first)
    append_sample(path, second)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert [json.loads(line) for line in lines] == [
        {"kind": "teleport", "direction": "up", "before": [80, 100], "after": [80, 80], "peak_y": 77,
         "settle_s": 0.4, "held_ms": None},
        {"kind": "walk", "direction": "left", "before": [80, 100], "after": [64, 100], "peak_y": None,
         "settle_s": 0.2, "held_ms": 2000.0},
    ]


# ---- main's checks ---------------------------------------------------------------------------------------------------

@pytest.fixture
def ran(monkeypatch):
    """Replace the procedure, so a test can see whether main got as far as starting it."""
    calls: list[tuple[int, object]] = []

    def fake_run(args, out):
        calls.append((args.reps, out))
        return 0

    monkeypatch.setattr(measure_moves, "_run_supervised", fake_run)
    return calls


def test_supervised_with_a_fresh_out_starts_the_procedure(tmp_path, ran):
    out = tmp_path / "m"
    assert main(["--supervised", "--out", str(out), "--reps", "4"]) == 0
    assert ran == [(4, out)]


def test_reps_defaults_to_10(tmp_path, ran):
    assert main(["--supervised", "--out", str(tmp_path / "m")]) == 0
    assert ran[0][0] == 10


@pytest.mark.parametrize("reps", ["1", "0", "-3"])
def test_reps_below_2_returns_2(tmp_path, ran, capsys, reps):
    assert main(["--supervised", "--out", str(tmp_path / "m"), "--reps", reps]) == 2
    assert "--reps" in capsys.readouterr().err
    assert ran == []


def test_non_integer_reps_returns_2(tmp_path, ran):
    assert main(["--supervised", "--out", str(tmp_path / "m"), "--reps", "ten"]) == 2
    assert ran == []


def test_out_holding_measure_jsonl_is_refused(tmp_path, ran, capsys):
    (tmp_path / MEASURE_FILE).write_text("", encoding="utf-8")
    assert main(["--supervised", "--out", str(tmp_path)]) == 2
    assert "already exists" in capsys.readouterr().err
    assert ran == []


def test_out_that_is_a_file_is_refused(tmp_path, ran, capsys):
    path = tmp_path / "file.txt"
    path.write_text("x", encoding="utf-8")
    assert main(["--supervised", "--out", str(path)]) == 2
    assert "not a folder" in capsys.readouterr().err
    assert ran == []


def test_default_out_is_a_stamped_folder_under_data_route_measure(ran):
    assert main(["--supervised"]) == 0
    out = ran[0][1]
    assert out.parent == measure_moves._REPO / "data" / "route" / "measure"  # noqa: SLF001
    assert not out.exists()


def test_piece_1_procedure_refuses_without_sending(tmp_path, capsys):
    assert main(["--supervised", "--out", str(tmp_path / "m")]) == 2
    assert "not built yet" in capsys.readouterr().err
