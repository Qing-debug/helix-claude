# Helix_BLE firmware

`Helix_BLE/Helix_BLE.ino` is the ESP32-S3 BLE keyboard sketch (NimBLE), advertised as "Guiness". The copy here is
byte-identical to `~/Documents/Arduino/Helix_BLE/Helix_BLE.ino` (Plan A Task F1). That sketchbook copy is read-only.

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
| Monitor | `"$CLI" monitor -p $PORT -c baudrate=115200 --timestamp` | see "Observed behaviour" |
| Reset | `"$ESPTOOL" --port "$PORT" --no-stub read-mac` | last line `Hard resetting via RTS pin...`, exit 0 |

The reset command uses esptool's default `--after hard-reset`. It connects (default `--before default-reset`, which
enters the ROM bootloader), reads the MAC, then resets into the normal boot. Per the esptool docs
(https://docs.espressif.com/projects/esptool/en/latest/esp32s3/esptool/advanced-options.html): "With
USB-Serial/JTAG, the peripheral interprets the RTS serial control signal as a core reset", and that reset "does not
re-sample the boot strapping pins". The reset does not re-enumerate USB, so COM4 stays.

Expected boot output of the unmodified sketch (`Helix_BLE.ino:106`, `:116`, `:92`):

```
Starting NimBLE work!
1070199112                                    (address of bleKeyboard, printed once by loop())
OnConnect method executed, advertising again. (when Windows reconnects, about 1 s after boot)
```

## Capture procedure (F3 and F5 reuse it)

The monitor opens the COM port for exclusive access, so esptool cannot reset the board while the monitor runs.

1. Stop the monitor.
2. Run the reset command. Expected: `Hard resetting via RTS pin...` and exit 0.
3. Reopen the monitor at once with the monitor command.

Evidence of the reboot (record which one showed it). See "Observed behaviour": with the default monitor settings the
boot line shows that *a* boot happened, not which reset caused it. A `LastConnectedTime` read after step 2 and before
step 3 is the reading that ties the reboot to the esptool reset.
- the `Starting NimBLE work!` line (and `OnConnect ...`) in the reopened capture;
- the "Guiness" device's `DEVPKEY_Bluetooth_LastConnectedTime` moving past the reset time:
  ```powershell
  (Get-PnpDeviceProperty -InstanceId 'BTHLE\DEV_1020BA4E2771\8&E129C23&0&1020BA4E2771' -KeyName DEVPKEY_Bluetooth_LastConnectedTime).Data.ToString('o')
  ```
  The value displays **1 h ahead** of true local time (for example `04:54:36` for a 03:54:36 reset), the same offset
  recorded on 2026-10-21.

## Observed behaviour (2026-10-22, Task F2)

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
