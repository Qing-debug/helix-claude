# Helix_BLE firmware

`Helix_BLE/Helix_BLE.ino` is the ESP32-S3 BLE keyboard sketch (NimBLE), advertised as "Guiness". It began as a copy of
`~/Documents/Arduino/Helix_BLE/Helix_BLE.ino` (Plan A Task F1) and has since diverged (Tasks F3 to F5, the A-M6fix
reply-notify retry and the A-FC all-up report retry: bonding, connection logging, the command queue and executor,
wire protocol v1). That sketchbook copy is read-only; edit and flash only this one.

| Path | What |
|---|---|
| `Helix_BLE/Helix_BLE.ino` | the sketch |
| `Helix_BLE/protocol.h` | wire protocol v1: command parser and notification encoders (header-only, no Arduino includes) |
| `Helix_BLE/notify_retry.h` | the bounded retry of a refused reply notify (A-M6fix) and of the loop task's all-up HID report after a refused key report or a RELEASE_ALL (A-FC) |
| `test/` | host-compiled tests of the two headers, and `vectors.json` (below) |
| `build/` | compile output (gitignored) |

## Board identity (checked 2026-10-22)

| What | Value |
|---|---|
| Chip | ESP32-S3 (QFN56) rev v0.2, BT 5 (LE), dual core 240 MHz, 8 MB embedded PSRAM (esptool `read-mac`) |
| USB interface | Built-in USB-Serial/JTAG (esptool `USB mode: USB-Serial/JTAG`). No CP210x/CH34x bridge. |
| USB VID:PID | `0x303A:0x1001` |
| USB serial / MAC | `10:20:BA:4E:27:70` (the BLE address is `...:71`) |
| Port | `COM4` on 2026-10-22. **Never hard-code it.** Look it up by VID:PID each time (below), because Windows can assign a new number if the cable moves. |
| FQBN | `esp32:esp32:esp32s3:USBMode=hwcdc,CDCOnBoot=cdc` |
| Bluetooth device | "Guiness", PnP instance `BTHLE\DEV_1020BA4E2771\8&E129C23&0&1020BA4E2771` |

Always pass the FQBN explicitly. `arduino-cli board list` matches this VID:PID to `esp32:esp32:esp32_family` and to an
unrelated `esp32:esp32:ozobot_drvkit`. `CDCOnBoot=cdc` is required for `Serial` to reach the USB port.

## Shell variables (Git Bash)

```bash
REPO=/c/Users/Qing_programming/Desktop/helix-claude-clone
PY=$REPO/.venv/Scripts/python.exe
CLI="/c/Users/Qing_programming/AppData/Local/Programs/Arduino IDE/resources/app/lib/backend/resources/arduino-cli.exe"
ESPTOOL=$(ls -d ~/AppData/Local/Arduino15/packages/esp32/tools/esptool_py/*/ | tail -1)esptool.exe   # v5.3.0
FQBN=esp32:esp32:esp32s3:USBMode=hwcdc,CDCOnBoot=cdc
```

Before anything that flashes, resets, monitors or connects, check that MapleStory is not running. If this prints
anything, stop:

```powershell
Get-Process | Where-Object { $_.ProcessName -match 'maple' } | Select-Object ProcessName, Id
```

## Port lookup (re-run before each flash, monitor or reset)

```bash
PORT=$("$CLI" board list --format json | "$PY" -c "import json,sys; print(' '.join(p['port']['address'] for p in json.load(sys.stdin)['detected_ports'] if (p['port'].get('properties') or {}).get('vid','').lower()=='0x303a' and p['port']['properties'].get('pid','').lower()=='0x1001'))")
echo "PORT=[$PORT]"   # expect exactly one port
```

## Compile, flash, monitor, reset

| Action | Command | Expected |
|---|---|---|
| Compile | `"$CLI" compile -b "$FQBN" --output-dir $REPO/firmware/build $REPO/firmware/Helix_BLE` | `Sketch uses ... bytes`, exit 0 |
| Compile + flash | `"$CLI" compile -b "$FQBN" -u -p $PORT --output-dir $REPO/firmware/build $REPO/firmware/Helix_BLE` | esptool output ending `Hard resetting via RTS pin...`, exit 0 |
| Monitor | `"$CLI" monitor -p $PORT -c baudrate=115200,dtr=off,rts=off --timestamp` | see "Observed behaviour" |
| Reset | `"$ESPTOOL" --port "$PORT" --no-stub read-mac` | last line `Hard resetting via RTS pin...`, exit 0 |

The reset command uses esptool's default `--after hard-reset`. It connects (default `--before default-reset`, which
enters the ROM bootloader), reads the MAC, then resets into the normal boot. Per the esptool docs
(https://docs.espressif.com/projects/esptool/en/latest/esp32s3/esptool/advanced-options.html): "With
USB-Serial/JTAG, the peripheral interprets the RTS serial control signal as a core reset", and that reset "does not
re-sample the boot strapping pins". The reset does not re-enumerate USB, so COM4 stays.

Serial lines the current sketch prints (names and lines from `Helix_BLE.ino` at 812411e; the order inside one boot is
the code order, not a capture):

| Line | Printed by |
|---|---|
| `Starting NimBLE work!` | `setup()`, right after the 3 s host wait (`:416`) |
| `HID input report found` or `ERROR HID input report not found` | `onStarted`, during `begin()` (`:366`, `:364`) |
| `BONDS=<n>` | `setup()`, after `begin()` (`:422`) |
| `OnConnect method executed, advertising again.`, `CONN ev=connect ...`, `PEERS count=<n>` (`WARN PEERS` when more than one) | `onConnect` (`:372-376`) |
| `CONN ev=auth ...`, `CONN ev=params ...`, `MTU handle=<h> mtu=<n>`, `SUB handle=<h> notify=<0\|1>`, `CIREQ handle=<h>`, `DISC handle=<h> reason=<r>` | the matching callbacks (`CIREQ`: the connection-interval request, sent from `loop()` 1.5 s after connect) |
| `ACCEPTED`, `DONE`, `DROP`, `REJECTED` | per command, from the executor and `onWrite` |
| `NOTIFY ...` | only in a `-DNOTIFY_DIAG=1` build (see "Compile-time flags") |

## Capture procedure (F3 and F5 reuse it)

The monitor opens the COM port for exclusive access, so esptool cannot reset the board while the monitor runs. Every
capture monitor keeps DTR and RTS off (the Monitor command above, A-F2-D1). With the defaults (`dtr=on`, `rts=on`),
closing the monitor reboots the board (see "Observed behaviour").

1. Stop the monitor, then read the "Guiness" last-connected time (command below). This is reading (1).
2. Run the reset command. Expected: `Hard resetting via RTS pin...` and exit 0.
3. Reopen the monitor at once with the Monitor command, with nothing in between. From a script, hold its stdin
   open for the whole capture, for example
   `timeout 60 "$CLI" monitor -p "$PORT" -c baudrate=115200,dtr=off,rts=off --timestamp < <(sleep 65) > "$REPO/data/<name>.log"`.
   arduino-cli ends the monitor when stdin reaches EOF (arduino-cli 1.5.1 `internal/cli/monitor/monitor.go:292-303`),
   so with stdin at EOF it exits at once, exit 0, with an empty log.
4. After the capture, stop the monitor and read the last-connected time again. This is reading (4).

Evidence of the reboot (record which one showed it):
- the `Starting NimBLE work!` boot line in the reopened capture. Output printed before the monitor opens is queued in
  HWCDC's 256-byte TX ring (esp32 core 3.3.10 `HWCDC.cpp:422`). Once the ring is full, each later line is dropped
  whole (connected path, `:586-604`; at once with `Serial.setTxTimeoutMs(0)`), so a capture keeps the earliest
  ≈ 256 bytes (with the F5 firmware, through the `SUB` line) and loses later lines until the monitor drains the
  ring. The drop-oldest
  policy (`:293-341`) applies only while `isCDC_Connected()` is false. The boot line is the earliest output, so it
  is in the capture. Boot and connect lines past ≈ 256 bytes (`CONN ev=auth`, `MTU`, `CIREQ`, `CONN ev=params`)
  are lost; read link state from a PING's PONG instead. The monitor's timestamps are arrival times, not boot times.
- reading (4) later than reading (1) of the "Guiness" device's `DEVPKEY_Bluetooth_LastConnectedTime`. Compare the two
  readings with each other, not with the reset time: the value can be earlier than esptool's exit (03:54:36.35 for a
  run that exited at 03:54:36.46, see "Observed behaviour"). A second `OnConnect` line in the capture means a later
  reconnect also moved the value.
  ```powershell
  (Get-PnpDeviceProperty -InstanceId 'BTHLE\DEV_1020BA4E2771\8&E129C23&0&1020BA4E2771' -KeyName DEVPKEY_Bluetooth_LastConnectedTime).Data.ToString('o')
  ```
  The value displays ahead of true local time. On 2026-10-21/22 it was 1 h ahead (for example `04:54:36` for a
  03:54:36 reset), which equals the UK BST offset from UTC. That is the offset as observed, not a fixed value:
  *inferred*, it is a local-vs-UTC display offset and becomes 0 h when BST ends (2026-10-25). Compare two readings
  rather than correcting the value; both carry the same offset.

## Host tests

`protocol.h` and `notify_retry.h` have no Arduino dependency, so g++ builds and runs their tests on the PC (no board,
no Arduino core). `firmware/build/` is gitignored; create it first.

```bash
mkdir -p $REPO/firmware/build
g++ -std=c++17 -Wall -Werror -I$REPO/firmware/Helix_BLE $REPO/firmware/test/test_protocol.cpp -o $REPO/firmware/build/test_protocol.exe && $REPO/firmware/build/test_protocol.exe
g++ -std=c++17 -Wall -Werror -I$REPO/firmware/Helix_BLE $REPO/firmware/test/test_notify_retry.cpp -o $REPO/firmware/build/test_notify_retry.exe && $REPO/firmware/build/test_notify_retry.exe
```

Each prints `ALL PASS` and exits 0 (at 08646be: `test_protocol` 25 command vectors, 13 notification vectors, 211
checks; `test_notify_retry` 92 checks). `test_protocol.cpp` is driven by `firmware/test/vectors.json`, the golden
vectors the Python protocol tests share.

## Compile-time flags

Both are `#ifndef` guards near the top of `Helix_BLE.ino` (`:61-70`); the plain compile and flash commands above build
the defaults.

| Flag | Default | Meaning |
|---|---|---|
| `START_JITTER` | `1` | a command's schedule starts at `t0 = now + U(0, CI)`, CI being the connection interval (PA-D1, PA-rev I8). A probe build may pass `-DSTART_JITTER=0`. |
| `NOTIFY_DIAG` | `0` | `1` makes `notifyPeer` keep each reply's NimBLE return code and print serial `NOTIFY ...` lines (A-M6diag). The default compiles none of that code. |

Pass a flag through `compiler.cpp.extra_flags`, the property `arduino-cli compile --help` shows for extra defines. A
build with the diagnostic on, compiled to its own output directory (add `-u -p "$PORT"` to flash it, as in the
compile and flash row above):

```bash
"$CLI" compile -b "$FQBN" --output-dir $REPO/firmware/build/m6diag --build-property "compiler.cpp.extra_flags=-DNOTIFY_DIAG=1" $REPO/firmware/Helix_BLE
```

Do not override `build.extra_flags`: the esp32 core builds its board defines into it (core 3.3.10 `platform.txt:105`
includes `{build.extra_flags.{build.mcu}}`, the esp32s3 USB defines at `:79`). A flashed diagnostic image stays on the
board until the default build is flashed over it. This `--build-property` form is the one A-M6diag compiled and flashed
(exit 0); it was not re-run for this README.

## Pairing

Run with MapleStory closed (see the check above) and the board on USB. The board's BLE address is
`10:20:BA:4E:27:71` (`0x1020BA4E2771`). Python reaches the custom characteristics only after pair, reset, reconnect,
not straight after a fresh pair (the spec's "Constraints and facts", Helix `ISSUES.md` #4; root cause not investigated).

1. Unpair, then re-pair, with bleak (3.0.2, the venv's `$PY`). `BleakClient.unpair()`
   (`bleak/__init__.py:684-696`): "Unpairing will also disconnect the device." (Windows and Linux only). Re-pair by
   connecting with `pair=True` (`:479-485`). The calls Task F3 ran (shape only; the script itself is not in git and was
   not re-run for this README):

   ```python
   await BleakClient(ADDR).unpair()
   await asyncio.sleep(3)
   async with BleakClient(ADDR, pair=True, timeout=30) as c: ...   # F3: connected and paired, is_connected True
   ```
2. Reset the board with the Capture procedure above (pair, reset, reconnect).
3. Check that Windows installed the keyboard:
   ```powershell
   Get-PnpDevice -PresentOnly -Class Keyboard | Where-Object InstanceId -match '1020BA4E2771' | Select-Object Status, Class, FriendlyName
   ```
   Expected (printed 2026-10-22 after F3's re-pair): one row `OK Keyboard HID Keyboard Device`. The HID entries are not
   named "Guiness" (`Bluetooth Low Energy GATT compliant HID device`, `HID Keyboard Device`), so a `FriendlyName -match
   'Guiness'` filter finds only the Bluetooth entry. They carry the board's address in their instance id.
4. If Windows does not set the board up as a keyboard after a bleak pairing, the only other route is pairing in the
   Windows Settings UI, which a person has to do.

### Bond evidence

- `BONDS=1` in the boot capture. It is printed by `setup()` inside the first ≈ 256 bytes of boot output, so a capture
  reopened right after a reset keeps it (Capture procedure above).
- A PING's PONG `flags`: bit0 = bonded, bit1 = encrypted (bit2 authenticated, bit3 subscribed; `proto::PongFlag` in
  `protocol.h`). Bonded and encrypted both 1 is the evidence. In Python, `BleLink.ping()` (`src/input/link.py`)
  returns a `Pong` with `.bonded` and `.encrypted` (`src/input/protocol.py`); a soak writes the bits as the `flags`
  field of `pongs.jsonl`.
- Not `onConnect`'s `CONN ev=connect` line: it is printed before encryption and always shows `bonded=0 encrypted=0`
  (A-F5-R I-1). The later `CONN ev=auth` line does show the state, but it falls past the first ≈ 256 bytes and is
  usually dropped (Capture procedure above).
- Link up, for the 60 s hold check: this prints `1` when Windows is connected to the board (WinRT
  `BluetoothLEDevice.ConnectionStatus`, `Connected = 1`). It opens no COM port.
  ```bash
  "$PY" -c "import asyncio; from winrt.windows.devices.bluetooth import BluetoothLEDevice as D; print(int(asyncio.run(asyncio.wait_for(D.from_bluetooth_address_async(0x1020BA4E2771), 10)).connection_status))"
  ```

## Protocol v1

The PC and the board speak a binary protocol, version byte `1`, little-endian: ops `PLAY` (1, a schedule of up to 16
single-key steps), `RELEASE_ALL` (2) and `PING` (3); notifications `ACCEPTED`, `DONE`, `REJECTED` and `PONG`. Where it
is defined:

- `docs/superpowers/plans/2026-10-21-p1a-input.md`, contract C1, and `docs/decisions.md` PA-D1: the contract.
- `Helix_BLE/protocol.h`: the board side. `src/input/protocol.py`: the PC side.
- `test/vectors.json`: golden vectors both sides are tested against (`test_protocol.cpp` and the Python tests).
- The three UUIDs (spec `docs/superpowers/specs/2026-10-21-p1a-input-over-bluetooth-design.md`, "Constraints and
  facts"): service `a723b49a-2b53-46a6-a2db-02bb9e9a577c`; command characteristic
  `61adcd67-20e9-4c38-b1d0-879445c5b732` (READ | WRITE); notify characteristic
  `89f7c2ca-89b5-4234-905c-d4a931feaa3b` (NOTIFY).

## Observed behaviour (2026-10-22, Task F2, on the unmodified sketch)

Recorded before F3 to F5 changed the sketch, so the boot lines named here are that sketch's. The monitor and reset
behaviour is the host's and the board's.

- **Boot output is buffered, not lost.** Output printed before the monitor opens is delivered when it opens (host
  timestamps about 2 s after open). In a run with `dtr=off,rts=off` the boot lines arrived while
  `LastConnectedTime` did not move, so they came from the earlier esptool reset, not a new boot. The boot line is
  therefore usually present after a reset. It does not prove *when* the boot happened, though: the timestamps show
  arrival time, not boot time.
- **Closing the monitor with its defaults (`dtr=on`, `rts=on`) reboots the board.** Three times, `LastConnectedTime`
  moved to 0.2-0.7 s after the monitor closed (03:51:58 → 03:51:59.07, 03:53:25.96 → 03:53:26.63,
  03:53:54.64 → 03:53:54.82). With `-c baudrate=115200,dtr=off,rts=off` it did not move at close. *Inferred:* the
  DTR/RTS drop at close is read as a reset. So a default monitor session ends with an extra reboot, and the next
  capture shows that reboot's buffered boot lines.
- Whether opening the monitor with its defaults also resets the board was not isolated.
- An esptool reset with no monitor open moved `LastConnectedTime` to the reset time (03:54:36.35, esptool exited at
  03:54:36.46).
