# firmware: ESP32-S3 sketch and host tests

- `Helix_BLE/` is the sketch (`Helix_BLE.ino`) with `protocol.h`, the board side of the protocol, and `notify_retry.h`. `test/` holds the host-compiled tests (`test_protocol.cpp`, `test_notify_retry.cpp`) and `vectors.json`, the golden vectors the C++ and Python protocol tests share, so a format change edits that file first.
- It is built and flashed with `arduino-cli`, not by Python. Build, flash, monitor, reset, pairing and the compile-time flags are in `firmware/README.md`.
- The host tests need only g++, no board: `python -m pytest tests/input/test_firmware_host.py` builds and runs both. By hand, from the repo root (`firmware/build/` is gitignored): `mkdir -p firmware/build`, then `g++ -std=c++17 -Wall -Werror -Ifirmware/Helix_BLE firmware/test/test_protocol.cpp -o firmware/build/test_protocol.exe && firmware/build/test_protocol.exe firmware/test/vectors.json` (the same for `test_notify_retry.cpp`, with no argument). Each prints `ALL PASS`.
- `tests/input/test_firmware_constants.py` pins the constants the PC shares with `protocol.h` and `Helix_BLE.ino`.
