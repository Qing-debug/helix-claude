"""Tests for the H2 fault injectors (src/input/harness/faults.py). No board, no radio, no elevation needed:
the board list, the esptool command, the radio and the PnP fallback commands are all faked. The esptool and
fallback fakes are small Python scripts run through the same Popen path as the real commands."""
import json
import sys
import time

from returns.pipeline import is_successful
from returns.result import Failure, Success
from winrt.windows.devices.radios import RadioAccessStatus

from src.input.harness import faults
from src.input.harness.faults import FaultStamps, bluetooth_off_on, reset_board


def _board_list(*ports):
    """`arduino-cli board list --format json` output: one detected port per (address, vid, pid)."""
    detected = [{"port": {"address": a, "protocol": "serial",
                          "properties": {"vid": vid, "pid": pid, "serialNumber": "x"}}}
                for a, vid, pid in ports]
    detected.append({"port": {"address": "COM1", "protocol": "serial"}})  # no properties at all
    return json.dumps({"detected_ports": detected})


def _script(body: str) -> list[str]:
    return [sys.executable, "-c", "import time\n" + body]


def _p(text: str) -> str:
    return f"print({text!r}, flush=True)\n"


RESET_SCRIPT = _p("esptool v5.3.0") + _p("Connected to ESP32-S3") + "time.sleep(0.5)\n" + \
    _p(faults.RESET_LINE) + "time.sleep(0.3)\n"


def _fake_reset(monkeypatch, board_json, script=RESET_SCRIPT):
    calls = []
    monkeypatch.setattr(faults, "_board_list_json", lambda: Success(board_json))

    def cmd(port):
        calls.append(port)
        return _script(script)
    monkeypatch.setattr(faults, "_reset_cmd", cmd)
    return calls


# ---- reset_board: port lookup (hard rule 3) -----------------------------------------

def test_reset_board_runs_reset_on_the_one_matching_port(monkeypatch):
    calls = _fake_reset(monkeypatch, _board_list(("COM7", "0x303A", "0x1001"), ("COM3", "0x10C4", "0xEA60")))
    stamps = FaultStamps()
    assert reset_board(stamps) == Success(None)
    assert calls == ["COM7"]
    assert stamps.launch_ns is not None and stamps.injected_ns is not None


def test_reset_board_matches_vid_pid_case_insensitively(monkeypatch):
    calls = _fake_reset(monkeypatch, _board_list(("COM9", "0x303a", "0X1001")))
    assert is_successful(reset_board())
    assert calls == ["COM9"]


def test_reset_board_with_no_matching_port_fails_and_runs_nothing(monkeypatch):
    calls = _fake_reset(monkeypatch, _board_list(("COM3", "0x10C4", "0xEA60")))
    stamps = FaultStamps()
    result = reset_board(stamps)
    assert not is_successful(result)
    assert "0 ports" in result.failure() and "COM3" in result.failure()
    assert calls == []
    assert stamps == FaultStamps(launch_ns=None, injected_ns=None)


def test_reset_board_with_two_matching_ports_fails_and_runs_nothing(monkeypatch):
    calls = _fake_reset(monkeypatch, _board_list(("COM4", "0x303A", "0x1001"), ("COM5", "0x303A", "0x1001")))
    stamps = FaultStamps()
    result = reset_board(stamps)
    assert not is_successful(result)
    assert "COM4" in result.failure() and "COM5" in result.failure()
    assert calls == []
    assert stamps.injected_ns is None


def test_reset_board_looks_the_port_up_on_every_call(monkeypatch):
    calls = _fake_reset(monkeypatch, _board_list(("COM4", "0x303A", "0x1001")))
    assert is_successful(reset_board())
    monkeypatch.setattr(faults, "_board_list_json", lambda: Success(_board_list(("COM6", "0x303A", "0x1001"))))
    assert is_successful(reset_board())
    assert calls == ["COM4", "COM6"]


def test_reset_board_passes_a_board_list_failure_through(monkeypatch):
    calls = _fake_reset(monkeypatch, "")
    monkeypatch.setattr(faults, "_board_list_json", lambda: Failure("arduino-cli not found"))
    result = reset_board()
    assert result == Failure("arduino-cli not found")
    assert calls == []


def test_reset_cmd_is_the_recorded_esptool_reset(monkeypatch):
    monkeypatch.setattr(faults, "_esptool_path", lambda: "C:/x/esptool.exe")
    assert faults._reset_cmd("COM4") == ["C:/x/esptool.exe", "--port", "COM4", "--no-stub", "read-mac"]


# ---- reset_board: I5 stamps -----------------------------------------------------------

def test_reset_board_stamps_the_reset_line_not_launch_or_exit(monkeypatch):
    _fake_reset(monkeypatch, _board_list(("COM4", "0x303A", "0x1001")))
    stamps = FaultStamps()
    assert is_successful(reset_board(stamps))
    exit_ns = time.perf_counter_ns()  # after the process exited (reset_board waits for it)
    assert stamps.injected_ns - stamps.launch_ns >= 500_000_000
    assert exit_ns - stamps.injected_ns >= 250_000_000


def test_reset_board_without_the_reset_line_fails_with_null_injected(monkeypatch):
    _fake_reset(monkeypatch, _board_list(("COM4", "0x303A", "0x1001")),
                script=_p("A fatal error occurred: Could not open COM4") + "raise SystemExit(2)\n")
    stamps = FaultStamps()
    result = reset_board(stamps)
    assert not is_successful(result)
    assert "reset line" in result.failure() and "Could not open COM4" in result.failure()
    assert stamps.injected_ns is None
    assert stamps.launch_ns is not None  # esptool did start (C5 launch_ns = esptool start)


def test_reset_board_nonzero_exit_after_the_reset_line_keeps_the_stamp(monkeypatch):
    _fake_reset(monkeypatch, _board_list(("COM4", "0x303A", "0x1001")),
                script=_p(faults.RESET_LINE) + "raise SystemExit(1)\n")
    stamps = FaultStamps()
    result = reset_board(stamps)
    assert not is_successful(result)
    assert stamps.injected_ns is not None  # the reset happened; only the exit code is off


# ---- bluetooth_off_on: radio path and fallback (G2, G3, PA-rev4a m2/m3/m5) ------------

class FakeRadio:
    def __init__(self, off_status, on_status=RadioAccessStatus.ALLOWED):
        self.off_status, self.on_status = off_status, on_status
        self.calls = []  # (on, perf_counter_ns at the call)

    def set_state(self, on):
        self.calls.append((on, time.perf_counter_ns()))
        return self.on_status if on else self.off_status


def _fake_radio(monkeypatch, radio):
    monkeypatch.setattr(faults, "_find_bluetooth_radio", lambda: Success(radio))


def _fake_fallback(monkeypatch, off_body, on_body=None):
    ran = []

    def off_cmd():
        ran.append("off")
        return _script(off_body)

    def on_cmd():
        ran.append("on")
        return _script(on_body if on_body is not None else _p(faults.PNP_ON_DONE))
    monkeypatch.setattr(faults, "_pnp_off_cmd", off_cmd)
    monkeypatch.setattr(faults, "_pnp_on_cmd", on_cmd)
    return ran


GOOD_FALLBACK = "time.sleep(0.3)\n" + _p(faults.PNP_MARKER) + "time.sleep(0.5)\n" + _p(faults.PNP_OFF_DONE)


def test_radio_allowed_keeps_the_stamp_taken_before_the_call(monkeypatch):
    radio = FakeRadio(RadioAccessStatus.ALLOWED)
    _fake_radio(monkeypatch, radio)
    ran = _fake_fallback(monkeypatch, GOOD_FALLBACK)
    stamps = FaultStamps()
    assert bluetooth_off_on(0, stamps) == Success(None)
    assert [on for on, _ in radio.calls] == [False, True]
    assert stamps.injected_ns is not None and stamps.injected_ns <= radio.calls[0][1]
    assert stamps.launch_ns is None
    assert ran == []


def test_radio_off_then_on_waits_the_given_seconds(monkeypatch):
    radio = FakeRadio(RadioAccessStatus.ALLOWED)
    _fake_radio(monkeypatch, radio)
    assert is_successful(bluetooth_off_on(0.2))
    assert radio.calls[1][1] - radio.calls[0][1] >= 200_000_000


def test_radio_unspecified_drops_the_stamp_and_the_fallback_marker_gives_injected(monkeypatch):
    radio = FakeRadio(RadioAccessStatus.UNSPECIFIED)
    _fake_radio(monkeypatch, radio)
    ran = _fake_fallback(monkeypatch, GOOD_FALLBACK)
    stamps = FaultStamps()
    start = time.perf_counter_ns()
    assert bluetooth_off_on(0, stamps) == Success(None)
    assert ran == ["off", "on"]
    assert [on for on, _ in radio.calls] == [False]  # the refused radio is not switched back on
    assert stamps.injected_ns - start >= 300_000_000  # not the radio call's stamp, not the launch


def test_fallback_stamps_the_marker_not_launch_or_exit(monkeypatch):
    _fake_radio(monkeypatch, FakeRadio(RadioAccessStatus.DENIED_BY_USER))
    _fake_fallback(monkeypatch, GOOD_FALLBACK)
    stamps = FaultStamps()
    start = time.perf_counter_ns()
    assert is_successful(bluetooth_off_on(0, stamps))
    exit_ns = time.perf_counter_ns()  # after both fallback processes exited
    assert stamps.injected_ns - start >= 300_000_000
    assert exit_ns - stamps.injected_ns >= 400_000_000


def test_fallback_marker_without_success_line_is_a_null_entry(monkeypatch):
    _fake_radio(monkeypatch, FakeRadio(RadioAccessStatus.DENIED_BY_SYSTEM))
    ran = _fake_fallback(monkeypatch, _p(faults.PNP_MARKER) +
                         "import sys\nprint('Disable-PnpDevice : Access denied', file=sys.stderr, flush=True)\n"
                         "raise SystemExit(1)\n")
    stamps = FaultStamps()
    result = bluetooth_off_on(0, stamps)
    assert not is_successful(result)
    assert "Access denied" in result.failure()
    assert stamps.injected_ns is None
    assert ran == ["off"]  # nothing was disabled, so nothing is re-enabled


def test_radio_denied_with_no_fallback_is_a_null_entry(monkeypatch):
    radio = FakeRadio(RadioAccessStatus.DENIED_BY_SYSTEM)
    _fake_radio(monkeypatch, radio)
    ran = _fake_fallback(monkeypatch, GOOD_FALLBACK)
    stamps = FaultStamps()
    result = bluetooth_off_on(0, stamps, fallback=False)
    assert not is_successful(result)
    assert "DENIED_BY_SYSTEM" in result.failure()
    assert stamps == FaultStamps(launch_ns=None, injected_ns=None)
    assert ran == []


def test_no_bluetooth_radio_goes_to_the_fallback(monkeypatch):
    monkeypatch.setattr(faults, "_find_bluetooth_radio", lambda: Failure("no Bluetooth radio"))
    ran = _fake_fallback(monkeypatch, GOOD_FALLBACK)
    stamps = FaultStamps()
    assert is_successful(bluetooth_off_on(0, stamps))
    assert ran == ["off", "on"] and stamps.injected_ns is not None


def test_radio_on_refused_after_off_keeps_the_stamp_and_fails(monkeypatch):
    _fake_radio(monkeypatch, FakeRadio(RadioAccessStatus.ALLOWED, on_status=RadioAccessStatus.DENIED_BY_SYSTEM))
    stamps = FaultStamps()
    result = bluetooth_off_on(0, stamps)
    assert not is_successful(result) and "ON" in result.failure()
    assert stamps.injected_ns is not None  # the radio did go off


class RaisingRadio(FakeRadio):
    """WinRT failures surface as OSError (pywinrt 3.2.1: an invalid `Uri(...)` raises OSError, winerror E_INVALIDARG)."""

    def __init__(self, raise_on, off_status=RadioAccessStatus.ALLOWED):
        super().__init__(off_status)
        self.raise_on = raise_on

    def set_state(self, on):
        super().set_state(on)
        if on == self.raise_on:
            raise OSError(-2147024891, "Access is denied")
        return self.on_status if on else self.off_status


def test_radio_off_call_raising_takes_the_fallback_like_a_refusal(monkeypatch):
    # A-H2-R M2: an exception from the WinRT call is a refusal, not an error leaving run_soak
    _fake_radio(monkeypatch, RaisingRadio(raise_on=False))
    ran = _fake_fallback(monkeypatch, GOOD_FALLBACK)
    stamps = FaultStamps()
    start = time.perf_counter_ns()
    assert bluetooth_off_on(0, stamps) == Success(None)
    assert ran == ["off", "on"]
    assert stamps.injected_ns - start >= 300_000_000  # the fallback marker, not the radio call's stamp


def test_radio_off_call_raising_with_no_fallback_is_a_null_entry(monkeypatch):
    _fake_radio(monkeypatch, RaisingRadio(raise_on=False))
    ran = _fake_fallback(monkeypatch, GOOD_FALLBACK)
    stamps = FaultStamps()
    result = bluetooth_off_on(0, stamps, fallback=False)
    assert not is_successful(result) and "Access is denied" in result.failure()
    assert stamps == FaultStamps() and ran == []


def test_radio_on_call_raising_after_off_keeps_the_stamp_and_fails(monkeypatch):
    _fake_radio(monkeypatch, RaisingRadio(raise_on=True))
    ran = _fake_fallback(monkeypatch, GOOD_FALLBACK)
    stamps = FaultStamps()
    result = bluetooth_off_on(0, stamps)
    assert not is_successful(result) and "ON" in result.failure() and "Access is denied" in result.failure()
    assert stamps.injected_ns is not None and ran == []  # the radio did go off; no fallback after it


def test_fallback_enable_failure_keeps_the_stamp_and_fails(monkeypatch):
    _fake_radio(monkeypatch, FakeRadio(RadioAccessStatus.UNSPECIFIED))
    _fake_fallback(monkeypatch, GOOD_FALLBACK, on_body="raise SystemExit(1)\n")
    stamps = FaultStamps()
    result = bluetooth_off_on(0, stamps)
    assert not is_successful(result) and "Enable" in result.failure()
    assert stamps.injected_ns is not None
