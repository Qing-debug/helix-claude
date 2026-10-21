# Phase 1 · Feature A — Input over Bluetooth

*Design, 2026-10-21. Part of Phase 1 in `docs/vision.md`; read that first. Features B–D depend on this one.*

## Why

Nothing in the bot can act until the PC can press keys through the ESP32. Today it can't: a BLE write reaches the board, but a keypress triggered from inside the write handler never reaches the PC (Helix `ISSUES.md` #5). The traced cause is that the HID library's send is deferred onto the same NimBLE host task that runs `onWrite`, so by the time it transmits, the key-up report has already overwritten the key-down. The recorded fix direction is: `onWrite` only enqueues; `loop()` dequeues and sends the keys.

This feature also owns **how human-like the timing is**, because every keypress the bot ever makes passes through it.

## Goals

1. The PC can **press, hold, and release** any key the bot needs — at least the four arrows, `E` (teleport), `Alt` (jump), `Space` (interact) — including **chords** (arrow + `E` teleports; `Down` + `Alt` drops through a platform) and **holds** (walking is holding an arrow).
2. Every command the PC sends either produces the intended key events or fails visibly to the PC. No silent drops.
3. **Human-like timing** is built into this layer: key-hold durations, gaps between keys, chord offsets (the two keys of a chord never land in the exact same instant), and reaction delays all vary the way a person's do. Callers ask for an action ("teleport left"); the input layer decides the exact timings.
4. The PC↔ESP32 connection is established and held for a session.

## Non-goals

- Mouse input (Phase 4).
- Reconnecting after a drop. In Phase 1, if the connection drops the bot does nothing special; the session log records it (Feature D).
- Deciding *which* keys to press — that's Features B and C.

## Decided by the user

- **Transport is Bluetooth (BLE) only at runtime**, for both commands and the keyboard. No USB data link between PC and ESP32 while the bot runs. A USB cable is used during development only, to flash and to read the serial log.
- **No software input injection** (`SendInput` etc.). All input is the ESP32 acting as a real HID keyboard.
- **No recordings of the user playing** as a source of timing data. The timing model must come from elsewhere (published human-performance / keystroke-dynamics data, or a defensible parametric model), and be researched and justified, not invented.
- Key bindings: teleport `E`, jump `Alt`, interact `Space`, arrows for direction. Teleport = arrow + `E`. Drop = `Down` + `Alt`.
- **The firmware sketch is copied into this repo** (e.g. `firmware/Helix_BLE/`) and that copy is the one Claude edits and flashes. The user's sketchbook copy (`~/Documents/Arduino/Helix_BLE/Helix_BLE.ino`) is left untouched.
- Claude may **compile and flash the board** whenever it needs to (board is plugged in over USB and paired over Bluetooth).
- Claude may send **live keystrokes on this PC into a test window**, but **only while MapleStory is not running**.
- Claude may **unpair and re-pair "Guiness" in Windows and reset the board over USB serial** itself (user, 2026-10-21).
- The timing model is **researched inline**, not through the `deep-research-interactive` gate, with its sources cited in this spec (user, 2026-10-21).

## Constraints and facts

- Board: ESP32-S3, BLE name "Guiness". Custom service `a723b49a-2b53-46a6-a2db-02bb9e9a577c`; command characteristic `61adcd67-20e9-4c38-b1d0-879445c5b732` (READ | WRITE); notify characteristic `89f7c2ca-89b5-4234-905c-d4a931feaa3b` (currently commented out on the firmware side). Stack: NimBLE-Arduino 2.x + webmonkey ESP32_NIMBLE_Keyboard. Full reference: Helix `docs/reference/firmware.md`, `docs/superpowers/ble-communication/spec.md`, `ISSUES.md` #2–#5.
- **Bonding must be re-added to the firmware.** The HID input report needs an encrypted link (`READ_ENC`, NimBLE-Arduino `NimBLEHIDDevice.cpp:181`), and without a bond on the board side every paired reconnect is killed by Windows ~270 ms in (Helix `ISSUES.md` #3). Helix fixed this with `setSecurityAuth(true, false, true)` + `setSecurityIOCap(BLE_HS_IO_NO_INPUT_OUTPUT)` (user-verified 2026-06-11, Helix commit `3544179`), but that firmware copy was later removed and the current external sketch sets no security. Separately, Python only reaches the custom characteristics after pair → reset → reconnect, not straight after a fresh pair (Helix `ISSUES.md` #4, root cause not investigated).
- Python side: `bleak`, with the existing "event loop on a daemon thread + blocking bridge" pattern in `src/ble/`. `src/ble/ble.py` doesn't parse; `bleV2.py` connects at import time. Both are experiments to replace, not APIs to preserve.
- Helix's earlier design chose strict lock-step (one command, confirm on screen, next). That is a movement-layer concern for Feature B; this layer just has to deliver key events reliably and report failures.

## Verification (first guess — tried early, gaps filled by Claude first)

- **Serial log** from the board over USB: what it received and when.
- **A keystroke listener on this PC** that records which key events arrive from the HID keyboard and their timing, into a focused test window — game not running.
- From that: every command type (single, hold, chord) arrives correctly, ordered, with no drops, over a long run (thousands of commands).
- **Timing realism** checked statistically against the chosen human-timing model (distributions, no two identical sequences, no fixed rhythm).

## Reliability targets

*Set by Claude; the user can override any of them.* All are measured offline, game not running.

| What | Target |
|---|---|
| Delivery | 0 dropped, extra, or out-of-order key events over a soak of ≥10,000 commands mixing single presses, holds and chords (0 in 10,000 bounds the true error rate below 0.03% at 95% confidence). |
| Failures are visible | Every injected fault (board reset mid-soak, PC Bluetooth off) is reported to the caller within 1 s. 0 silent losses. |
| Latency | p99 from command sent to key-down seen by the PC listener ≤ 100 ms. Provisional: re-set once first measured. |
| Holds | Measured hold within ±20 ms of the duration the timing layer chose, for ≥95% of holds. |
| Timing realism | Each timing type (hold, gap, chord offset, reaction) fits its model (KS test, α = 0.01, ≥1,000 samples each). No run of ≥5 consecutive gaps repeats anywhere in the soak. |
| Connection | Session start connects 20/20 times without re-pairing, including after a board reset and after the PC's Bluetooth is toggled. A connected session holds 2 h with no drop. |

## Shipping

Only code that really works is merged (user, 2026-10-21). Branch `p1a-input` off `master`. A's checks run on the real board, over the real Bluetooth link, into a real window, so passing them shows it works: merge to `master` once the targets above pass, then push to `origin`. That the game accepts the keys is confirmed in B's first supervised session.

## Left to the builder

- The command encoding between PC and board, and whether to use the notify channel for acks.
- The firmware fix for `ISSUES.md` #5 (the queue approach is the recorded direction, not a requirement).
- The timing model and its parameters, with sources.
- Groundwork done as the plan's opening tasks: a Python environment for this repo, a dependency file, a test runner.
- Bringing Helix's `rune_solver`-branch refactor of the camera/vision code over is Feature C's job, but if the groundwork tasks touch the same files, coordinate there.
