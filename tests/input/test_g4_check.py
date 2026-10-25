"""Tests for the G4 buffering check (src/input/harness/g4_check.py; plan A Task H2 step 3, PA-rev3b G4).

No board: the board list and the esptool command are faked as in test_faults.py; the esptool fakes are Python
scripts run through faults.py's real Popen path, so pipe buffering behaves as it would for a real child.
"""
import json
import sys

import pytest
from returns.pipeline import is_successful
from returns.result import Success

from src.input.harness import faults, g4_check

ONE_PORT = json.dumps({"detected_ports": [
    {"port": {"address": "COM7", "properties": {"vid": "0x303A", "pid": "0x1001"}}}]})
NO_PORT = json.dumps({"detected_ports": []})


def _fake(monkeypatch, body, board_json=ONE_PORT):
    calls = []
    monkeypatch.setattr(faults, "_board_list_json", lambda: Success(board_json))

    def cmd(port):
        calls.append(port)
        return [sys.executable, "-c", "import time\n" + body]
    monkeypatch.setattr(faults, "_reset_cmd", cmd)
    return calls


def _p(text, flush=True):
    return f"print({text!r}, flush={flush})\n"


# Lines reach the pipe as printed: 0.6 s between the first line and the reset line, 0.3 s from it to exit.
LINE_BUFFERED = _p("esptool v5.3.0") + "time.sleep(0.6)\n" + _p("MAC: 10:20:ba:4e:27:70") + \
    _p(faults.RESET_LINE) + "time.sleep(0.3)\n"
# Same output, never flushed: a piped Python stdout is block-buffered, so every line arrives at exit.
BLOCK_BUFFERED = _p("esptool v5.3.0", False) + "time.sleep(0.6)\n" + _p("MAC: 10:20:ba:4e:27:70", False) + \
    _p(faults.RESET_LINE, False) + "time.sleep(0.3)\n"


@pytest.mark.slow
def test_reset_on_board_list_port_logs_every_line_and_exit_minus_injected(monkeypatch):
    calls = _fake(monkeypatch, LINE_BUFFERED)
    rep = g4_check.run_check()
    assert calls == ["COM7"] and is_successful(rep.result)
    assert [text for _, text in rep.lines] == ["esptool v5.3.0", "MAC: 10:20:ba:4e:27:70", faults.RESET_LINE]
    assert rep.launch_ns < rep.lines[0][0] <= rep.injected_ns < rep.exit_ns
    assert rep.injected_ns == rep.lines[2][0]  # the stamp reset_board took is the reset line's read time
    assert rep.exit_minus_injected_ms() >= 250  # read when printed, 0.3 s before exit
    assert rep.spread_ms() >= 500
    assert rep.verdict() == g4_check.READ_BEFORE_EXIT


@pytest.mark.slow
def test_block_buffered_output_is_flagged(monkeypatch):
    _fake(monkeypatch, BLOCK_BUFFERED)
    rep = g4_check.run_check()
    assert is_successful(rep.result) and rep.injected_ns is not None
    assert rep.spread_ms() < 100  # all three lines arrived together, at exit
    assert rep.exit_minus_injected_ms() < 250
    assert rep.verdict() == g4_check.BLOCK_BUFFERED


def test_no_reset_line_is_its_own_verdict(monkeypatch):
    _fake(monkeypatch, _p("A fatal error occurred: could not connect"))
    rep = g4_check.run_check()
    assert not is_successful(rep.result) and rep.injected_ns is None
    assert rep.exit_minus_injected_ms() is None and rep.verdict() == g4_check.NO_RESET


@pytest.mark.slow
def test_main_exit_codes_and_printed_lines(monkeypatch, capsys):
    _fake(monkeypatch, LINE_BUFFERED)
    assert g4_check.main([]) == 0
    out = capsys.readouterr().out
    assert "exit - injected_ns" in out and g4_check.READ_BEFORE_EXIT in out
    summary = json.loads(out.strip().splitlines()[-1])
    assert summary["verdict"] == g4_check.READ_BEFORE_EXIT and summary["exit_minus_injected_ms"] >= 250
    assert len(summary["lines_ms"]) == 3

    _fake(monkeypatch, BLOCK_BUFFERED)
    assert g4_check.main([]) == 2
    assert "[review]" in capsys.readouterr().out


def test_main_with_no_board_port_runs_no_esptool_and_exits_1(monkeypatch, capsys):
    calls = _fake(monkeypatch, LINE_BUFFERED, board_json=NO_PORT)
    assert g4_check.main([]) == 1
    assert calls == []
    assert "0 ports" in capsys.readouterr().out
