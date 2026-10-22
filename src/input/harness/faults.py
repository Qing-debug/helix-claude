"""Fault injectors for the soak driver (plan A, Task H2; C5 ``faults.jsonl``).

``reset_board()`` resets the ESP32-S3 with the esptool command firmware/README.md records ("Reset" row:
``esptool --port PORT --no-stub read-mac``, last line ``Hard resetting via RTS pin...``). The port is looked up on
every call by USB identity (VID 0x303A / PID 1001, plan hard rule 3), never cached.

``bluetooth_off_on(seconds)`` switches the PC's Bluetooth radio off, waits, and switches it on again through
``winrt.windows.devices.radios`` (3.2.1, read from the installed ``_winrt_windows_devices_radios.pyi``:
``Radio.get_radios_async() -> IAsyncOperation[Sequence[Radio]]``, ``Radio.kind``,
``Radio.set_state_async(RadioState) -> IAsyncOperation[RadioAccessStatus]``; ``IAsyncOperation.wait(timeout) ->
AsyncStatus`` and ``get_results()`` from ``_winrt_windows_foundation.pyi``). A refusal is a returned
``RadioAccessStatus`` other than ``ALLOWED``, not an exception (PA-rev4a m3); an ``OSError`` from the call (how
pywinrt raises a failed HRESULT) counts as a refusal too (A-H2-R M2). On a refusal it falls back to
``Disable-PnpDevice`` / ``Enable-PnpDevice`` on the Bluetooth adapter, which needs an elevated shell.

Stamps (PA-rev I5, PA-rev3b G2-G4; all ``perf_counter_ns``). The caller passes a ``FaultStamps``; the injector
fills it on every path. ``injected_ns`` is the moment the fault takes effect, or None when that was not observed:
esptool's reset line as the driver reads it from the pipe (not the launch, not the exit); for the radio, just
before ``set_state_async(OFF)``, kept only if it returned ``ALLOWED``; for the fallback, the marker line the
PowerShell command prints right before ``Disable-PnpDevice``, kept only if the cmdlet's success line follows.
``launch_ns`` is esptool's start (None for ``bluetooth_off_on``). An injector can return ``Failure`` with
``injected_ns`` set: the fault took effect and a later part (exit code, switching back on) failed.
"""
from __future__ import annotations

import json
import subprocess
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from returns.pipeline import is_successful
from returns.result import Failure, Result, Success
from winrt.windows.devices.radios import Radio, RadioAccessStatus, RadioKind, RadioState
from winrt.windows.foundation import AsyncStatus

# Plan "Shell variables": CLI and ESPTOOL (the newest esptool_py/<version>/ directory, `ls -d ... | tail -1`).
ARDUINO_CLI = Path("C:/Users/Qing_programming/AppData/Local/Programs/Arduino IDE/resources/app/lib/backend/"
                   "resources/arduino-cli.exe")
ESPTOOL_DIR = Path.home() / "AppData/Local/Arduino15/packages/esp32/tools/esptool_py"
BOARD_VID, BOARD_PID = "0x303a", "0x1001"
RESET_LINE = "Hard resetting via RTS pin..."  # firmware/README.md, "Reset" row

PNP_MARKER = "HELIX_BT_DISABLE_NOW"     # printed right before Disable-PnpDevice: the injection stamp
PNP_OFF_DONE = "HELIX_BT_DISABLED"      # printed after Disable-PnpDevice returned without error
PNP_ON_DONE = "HELIX_BT_ENABLED"        # printed after Enable-PnpDevice returned without error
_WINRT_TIMEOUT_S = 10.0
_TAIL_LINES = 5

# The Bluetooth adapter is the class-Bluetooth device on a bus (USB\ or PCI\); the rest are enumerators and
# paired devices (Get-PnpDevice -Class Bluetooth on this PC, 2026-10-22: one USB\VID_8087&PID_0032 adapter).
_PNP_FIND_ADAPTER = (
    "$ErrorActionPreference = 'Stop'; "
    "$a = @(Get-PnpDevice -Class Bluetooth | Where-Object { $_.InstanceId -match '^(USB|PCI)\\\\' }); "
    "if ($a.Count -ne 1) { Write-Output ('Bluetooth adapters found: ' + $a.Count); exit 2 }; "
)


@dataclass
class FaultStamps:
    launch_ns: int | None = None
    injected_ns: int | None = None


# ---- subprocess helpers ----------------------------------------------------------------

@dataclass
class _Streamed:
    launch_ns: int
    matched_ns: dict[str, int]  # wanted line -> perf_counter_ns when the driver read it
    returncode: int
    tail: list[str]


def _stream(cmd: list[str], wanted: list[str],
            on_line: Callable[[int, str], None] | None = None) -> Result[_Streamed, str]:
    """Run ``cmd``, read its merged output line by line, stamp the first line containing each wanted string.
    ``on_line(read_ns, line)`` sees every line as it is read (the G4 check logs them)."""
    launch_ns = time.perf_counter_ns()
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                encoding="utf-8", errors="replace")
    except OSError as e:
        return Failure(f"could not start {cmd[0]}: {e}")
    matched: dict[str, int] = {}
    tail: deque[str] = deque(maxlen=_TAIL_LINES)
    assert proc.stdout is not None
    with proc.stdout:
        for line in proc.stdout:
            now = time.perf_counter_ns()
            if on_line is not None:
                on_line(now, line.rstrip("\r\n"))
            for w in wanted:
                if w not in matched and w in line:
                    matched[w] = now
            if line.strip():
                tail.append(line.strip())
    return Success(_Streamed(launch_ns, matched, proc.wait(), list(tail)))


def _board_list_json() -> Result[str, str]:
    try:
        out = subprocess.run([str(ARDUINO_CLI), "board", "list", "--format", "json"], capture_output=True,
                             text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        return Failure(f"arduino-cli board list failed: {e}")
    if out.returncode != 0:
        return Failure(f"arduino-cli board list exited {out.returncode}: {out.stderr.strip()}")
    return Success(out.stdout)


def find_board_port() -> Result[str, str]:
    """The one serial port with the board's USB identity (hard rule 3); zero or several is a Failure."""
    listed = _board_list_json()
    if not is_successful(listed):
        return listed
    try:
        ports = [p.get("port", {}) for p in json.loads(listed.unwrap()).get("detected_ports", [])]
    except (json.JSONDecodeError, AttributeError) as e:
        return Failure(f"arduino-cli board list gave unreadable JSON: {e}")
    matches = [p.get("address") for p in ports
               if (p.get("properties") or {}).get("vid", "").lower() == BOARD_VID
               and (p.get("properties") or {}).get("pid", "").lower() == BOARD_PID]
    if len(matches) != 1:
        return Failure(f"expected exactly one {BOARD_VID}/{BOARD_PID} port, found {len(matches)} ports "
                       f"{matches}; all ports listed: {[p.get('address') for p in ports]}")
    return Success(matches[0])


def _esptool_path() -> str:
    versions = sorted(ESPTOOL_DIR.glob("*/"))
    return str((versions[-1] if versions else ESPTOOL_DIR / "<none>") / "esptool.exe")


def _reset_cmd(port: str) -> list[str]:
    return [_esptool_path(), "--port", port, "--no-stub", "read-mac"]


# ---- reset_board ------------------------------------------------------------------------

def reset_board(stamps: FaultStamps | None = None, *,
                on_line: Callable[[int, str], None] | None = None) -> Result[None, str]:
    """Reset the board with esptool; ``injected_ns`` is stamped when its reset line is read. Returns after exit.
    ``on_line(read_ns, line)``, if given, sees every esptool output line as it is read (G4 check)."""
    stamps = stamps if stamps is not None else FaultStamps()
    port = find_board_port()
    if not is_successful(port):
        return port
    streamed = _stream(_reset_cmd(port.unwrap()), [RESET_LINE], on_line)
    if not is_successful(streamed):
        return streamed
    run = streamed.unwrap()
    stamps.launch_ns = run.launch_ns
    stamps.injected_ns = run.matched_ns.get(RESET_LINE)
    if stamps.injected_ns is None:
        return Failure(f"esptool exited {run.returncode} without the reset line {RESET_LINE!r}; "
                       f"last output: {run.tail}")
    if run.returncode != 0:
        return Failure(f"esptool exited {run.returncode} after the reset line; last output: {run.tail}")
    return Success(None)


# ---- bluetooth_off_on ---------------------------------------------------------------------

class _Radio(Protocol):
    def set_state(self, on: bool) -> RadioAccessStatus: ...


class _WinrtRadio:
    def __init__(self, radio) -> None:
        self._radio = radio

    def set_state(self, on: bool) -> RadioAccessStatus:
        op = self._radio.set_state_async(RadioState.ON if on else RadioState.OFF)
        if op.wait(_WINRT_TIMEOUT_S) != AsyncStatus.COMPLETED:  # timed out, cancelled or errored: not ALLOWED
            return RadioAccessStatus.UNSPECIFIED
        return RadioAccessStatus(op.get_results())


def _find_bluetooth_radio() -> Result[_Radio, str]:
    try:
        op = Radio.get_radios_async()
        if op.wait(_WINRT_TIMEOUT_S) != AsyncStatus.COMPLETED:
            return Failure("Radio.get_radios_async did not complete")
        radios = [r for r in op.get_results() if r.kind == RadioKind.BLUETOOTH]
    except OSError as e:
        return Failure(f"Radio.get_radios_async failed: {e}")
    if len(radios) != 1:
        return Failure(f"expected one Bluetooth radio, found {len(radios)}")
    return Success(_WinrtRadio(radios[0]))


def _powershell(script: str) -> list[str]:
    return ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script]


def _pnp_off_cmd() -> list[str]:
    return _powershell(_PNP_FIND_ADAPTER + f"Write-Output '{PNP_MARKER}'; "
                       "Disable-PnpDevice -InstanceId $a[0].InstanceId -Confirm:$false -ErrorAction Stop; "
                       f"Write-Output '{PNP_OFF_DONE}'")


def _pnp_on_cmd() -> list[str]:
    return _powershell(_PNP_FIND_ADAPTER +
                       "Enable-PnpDevice -InstanceId $a[0].InstanceId -Confirm:$false -ErrorAction Stop; "
                       f"Write-Output '{PNP_ON_DONE}'")


def _pnp_off_on(seconds: float, stamps: FaultStamps, sleep: Callable[[float], None]) -> Result[None, str]:
    streamed = _stream(_pnp_off_cmd(), [PNP_MARKER, PNP_OFF_DONE])
    if not is_successful(streamed):
        return streamed
    off = streamed.unwrap()
    if PNP_OFF_DONE not in off.matched_ns or PNP_MARKER not in off.matched_ns:
        return Failure(f"Disable-PnpDevice fallback did not succeed (exit {off.returncode}); last output: {off.tail}")
    stamps.injected_ns = off.matched_ns[PNP_MARKER]
    sleep(seconds)
    streamed = _stream(_pnp_on_cmd(), [PNP_ON_DONE])
    if not is_successful(streamed):
        return Failure(f"Enable-PnpDevice did not run, Bluetooth adapter left disabled: {streamed.failure()}")
    on = streamed.unwrap()
    if PNP_ON_DONE not in on.matched_ns:
        return Failure(f"Enable-PnpDevice failed (exit {on.returncode}), Bluetooth adapter may be left disabled; "
                       f"last output: {on.tail}")
    return Success(None)


def bluetooth_off_on(seconds: float, stamps: FaultStamps | None = None, *, fallback: bool = True,
                     sleep: Callable[[float], None] = time.sleep) -> Result[None, str]:
    """Bluetooth radio off, wait ``seconds``, on. Returns once it is on again (or on the failure)."""
    stamps = stamps if stamps is not None else FaultStamps()
    found = _find_bluetooth_radio()
    if is_successful(found):
        radio = found.unwrap()
        before_ns = time.perf_counter_ns()
        try:
            status = radio.set_state(False)
        except OSError as e:  # a failed WinRT call: a refusal like any other (A-H2-R M2)
            status, refusal = None, f"radio OFF call failed: {e}"
        if status == RadioAccessStatus.ALLOWED:
            stamps.injected_ns = before_ns
            sleep(seconds)
            try:
                status_on = radio.set_state(True)
            except OSError as e:
                return Failure(f"radio ON call failed: {e}; Bluetooth left off")
            if status_on != RadioAccessStatus.ALLOWED:
                return Failure(f"radio ON refused ({status_on.name}); Bluetooth left off")
            return Success(None)
        if status is not None:
            refusal = f"radio OFF refused ({status.name})"
    else:
        refusal = found.failure()
    if not fallback:
        return Failure(f"{refusal}; no fallback")
    result = _pnp_off_on(seconds, stamps, sleep)
    return result.alt(lambda e: f"{refusal}; fallback: {e}")
