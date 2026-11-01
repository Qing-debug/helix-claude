"""The Route tab (spec 5.2-5.5, contract C5): the pure helpers first (settings validation, class key, run texts,
countdown text), then the tab itself on `make_gui`'s withdrawn root (the tab's tests are added by the next piece)."""
import threading

import pytest

from src.classes.classes import Kanna
from src.gui.route_tab import classKey, countdownText, endedText, validateSettings

LAPS_ERROR = "Laps must be a whole number of 1 or more"
MINUTES_ERROR = f"Minutes must be a number above 0 and at most {threading.TIMEOUT_MAX / 60:g}"
COUNTDOWN_ERROR = "Countdown must be a whole number of seconds from 1 to 60"
RUN_NAME = "2026-10-31T140231Z"


@pytest.mark.parametrize("laps, minutes, countdown, expected", [
    ("", "", "5", (None, None, 5)),
    (" 2 ", "1.5", "60", (2, 1.5, 60)),
    ("1", " 0.5 ", " 1 ", (1, 0.5, 1)),
    ("", repr(threading.TIMEOUT_MAX / 60), "5", (None, threading.TIMEOUT_MAX / 60, 5)),  # "at most": the top is valid
])
def test_validate_settings_accepts(laps, minutes, countdown, expected):
    assert validateSettings(laps, minutes, countdown) == (expected, None)


@pytest.mark.parametrize("laps", ["0", "-1", "1.5", "abc", "1_0", "+2", "²"])
def test_validate_settings_table_laps(laps):
    assert validateSettings(laps, "", "5") == (None, LAPS_ERROR)


@pytest.mark.parametrize("minutes", ["0", "-1", "abc", "nan", "inf", "1e400", "1_0", str(threading.TIMEOUT_MAX / 60 * 1.01)])
def test_validate_settings_table_minutes(minutes):
    assert validateSettings("", minutes, "5") == (None, MINUTES_ERROR)


@pytest.mark.parametrize("countdown", ["0", "61", "", "2.5", "-3", "abc"])
def test_validate_settings_table_countdown(countdown):
    assert validateSettings("", "", countdown) == (None, COUNTDOWN_ERROR)


def test_validate_settings_names_the_first_bad_field():
    assert validateSettings("0", "abc", "0") == (None, LAPS_ERROR)
    assert validateSettings("1", "abc", "0") == (None, MINUTES_ERROR)


def test_class_key_is_kanna():
    assert classKey(Kanna()) == "kanna"
    assert classKey(object()) is None


@pytest.fixture
def folder(tmp_path):
    made = tmp_path / RUN_NAME
    made.mkdir()
    return made


@pytest.mark.parametrize("code, text, expected", [
    (0, "max_laps/", "Ended: all laps done."),  # the natural end: no trigger
    (0, "max_laps/hotkey", "Ended: all laps done."),  # a stop that lands as the last lap ends still did the laps
    (0, "max_laps/button", "Ended: all laps done."),
    (0, "stopped/timer", "Ended: the time limit was reached."),
    (0, "stopped/hotkey", "Ended: you pressed F12."),
    (0, "stopped/button", "Ended: you pressed Stop."),
    (0, "stopped/app_focus", "Stopped: you clicked the app. Press Start to run again."),
    (0, "cancelled/app_focus", "Stopped: you clicked the app. Press Start to run again."),
    (0, "stopped/window_close", ""),
    (0, "cancelled/window_close", ""),
    (0, "cancelled/hotkey", "Ended: cancelled before the walk (you pressed F12)."),
    (0, "cancelled/button", "Ended: cancelled before the walk (you pressed Stop)."),
    (0, "cancelled/countdown_focus",
     "Not started: click the game first. Press Start, then click the game before the countdown ends."),
    (0, "none/", "Ended (none/)."),  # choice 3's "none" comes only with codes 2-5 and 9, which map by code
    (0, "stopped/", "Ended (stopped/)."),
    (0, "xyz/abc", "Ended (xyz/abc)."),
])
def test_ended_text_table(code, text, expected):
    assert endedText(code, text, None, False) == expected


@pytest.mark.parametrize("code, expected", [
    (1, "Ended: the board link was lost."),
    (2, "Could not start: the run folder could not be written."),
    (3, "Could not start: no minimap frame or character found."),
    (4, "Could not start: the board did not connect."),
    (5, "Could not start: the map, route or character icon did not load."),
    (9, "Could not start: the F12 stop key could not be installed."),
    (10, "Ended: the character was lost (exit 10)."),
])
@pytest.mark.parametrize("text", ["none/", "max_laps/", "stopped/hotkey", "link_down/", "anything"])
def test_ended_text_maps_a_nonzero_code_first(code, expected, text, folder):
    assert endedText(code, text, folder, False) == expected


@pytest.mark.parametrize("code", [6, 7])
@pytest.mark.parametrize("text", ["none/", "stopped/hotkey", "x"])
def test_ended_text_walker_failed_names_the_folder_once(code, text, folder):
    assert endedText(code, text, folder, False) == f"Ended: the walker failed; see {folder.name}."
    assert endedText(code, text, folder, True) == f"Ended: the walker failed; see {folder.name}. Replay is open."


def test_ended_text_suffix_follows_replay_open_only(folder):
    (folder / "steps.jsonl").write_text("{}\n", encoding="utf-8")
    assert endedText(0, "stopped/hotkey", folder, True) == f"Ended: you pressed F12 ({folder.name}). Replay is open."
    assert (endedText(0, "cancelled/button", folder, True)
            == f"Ended: cancelled before the walk (you pressed Stop) ({folder.name}). Replay is open.")
    assert (endedText(0, "max_laps/", folder, True)
            == f"Ended: all laps done ({folder.name}). Replay is open.")
    assert (endedText(1, "link_down/", folder, True)
            == f"Ended: the board link was lost ({folder.name}). Replay is open.")
    # the folder holds a step log, but no replay opened: no suffix and no name (choice 7)
    assert endedText(0, "stopped/hotkey", folder, False) == "Ended: you pressed F12."
    assert endedText(0, "cancelled/button", folder, False) == "Ended: cancelled before the walk (you pressed Stop)."


@pytest.mark.parametrize("code, text", [
    (0, "stopped/app_focus"), (0, "cancelled/app_focus"), (0, "cancelled/countdown_focus"), (0, "stopped/window_close"),
    (0, "xyz/abc"), (0, "none/"), (2, "none/"), (3, "none/"), (4, "none/"), (5, "none/"), (9, "none/"),
])
def test_ended_text_other_prefixes_take_no_suffix(code, text, folder):
    assert endedText(code, text, folder, True) == endedText(code, text, folder, False)
    assert endedText(code, text, folder, True) == endedText(code, text, None, False)


def test_ended_text_unexpected_stop(folder, tmp_path):
    error = "the run stopped unexpectedly: RuntimeError: x"
    assert (endedText(None, error, folder, False)
            == f"Ended: the run stopped unexpectedly: RuntimeError: x. See the console and {folder.name}.")
    assert (endedText(None, error, folder, True)
            == f"Ended: the run stopped unexpectedly: RuntimeError: x. See the console and {folder.name}."
               " Replay is open.")
    gone = tmp_path / "never_made"  # an exception before the leg log leaves no folder
    assert endedText(None, error, gone, False) == "Ended: the run stopped unexpectedly: RuntimeError: x. See the console."
    assert endedText(None, error, None, False) == "Ended: the run stopped unexpectedly: RuntimeError: x. See the console."


def test_countdown_text():
    assert countdownText(5, 5) == "Click the game now. Starting in 5 ... F12 stops."
    assert countdownText(5, 1) == "Click the game now. Starting in 5 ... 4 ... 3 ... 2 ... 1. F12 stops."
    assert countdownText(5, 3) == "Click the game now. Starting in 5 ... 4 ... 3 ... F12 stops."
    # a long countdown shows only the last five counts so far (choice 4)
    assert countdownText(60, 50) == "Click the game now. Starting in 54 ... 53 ... 52 ... 51 ... 50 ... F12 stops."
    assert countdownText(60, 1) == countdownText(5, 1)
