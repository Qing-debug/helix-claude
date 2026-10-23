# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Helix is a Windows-only MapleStory automation agent. It screen-captures the in-game minimap, finds the character on it, builds a navigation graph from a hand-authored map of nodes, and (eventually) drives movement through an ESP32-S3 board over BLE. A rune-solver (YOLO models) is scaffolded but not wired in.

This repo is a Claude-only parallel of the main Helix repo. Remotes: `origin` = `Qing-debug/helix-claude` (work here), `upstream` = `Qing-debug/Helix` (fetch-only; push is disabled).

## Running

`requirements.txt` pins the runtime dependencies. `requirements-dev.txt` is `-r requirements.txt` plus `pytest`, `scipy` and `winrt-Windows.Devices.Radios`, and `pytest.ini` sets `testpaths = tests` and `pythonpath = .`. There is no linter config and no app build step. The rune solver's dependencies (`ultralytics` and what it pulls in) are not in `requirements.txt`; the runes feature (Feature C, `docs/superpowers/specs/2026-10-21-p1c-runes-design.md`) adds them.

The working venv is Python 3.14.0 (`networkx==3.6.1` declares `!=3.14.1`). From the repo root:

```
py -3.14 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
python -m pytest
```

(`.venv\Scripts\activate` in cmd, `source .venv/Scripts/activate` in Git Bash.)

Every test should pass except the skipped golden-vector rejects in `tests/input/test_protocol.py`, which only the board can run.

Run the app from the repo root as a module, since all imports are rooted at `src.`:

```
python -m src.main
```

`main.py` starts the minimap capture thread and then blocks in the Tk mainloop. The GUI waits until a minimap frame exists, so the game must be visible on the captured monitor (`output_idx = 1` in `src/vision/vision.py`, i.e. the second monitor).

## Architecture

**Threads and shared state.** `SharedInfo` (`src/main.py`) holds `minimap_frame` plus `minimap_frame_lock`. The vision thread (`capture_and_process_minimap`) is the only writer. The main GUI and the map editor each poll it with their own `_updateCanvas` loop via `tk.after`. Despite the comment in `main.py`, the frame is stored as **RGB** (vision converts BGR→RGB for Tk/PIL display).

**Vision** (`src/vision/`). Template-matches four minimap corner borders (`assets/brds/*_grayscale.png` + masks) across the whole monitor using masked `TM_CCORR_NORMED`. inf/nan/>1 scores are scrubbed before `minMaxLoc`. Corners must pass a confidence threshold, ordering checks, and rectangle-alignment checks. After that it grabs just the minimap region for `MINIMAP_POSITION_RECALC_INTERVAL` frames before re-detecting. It re-detects early if the region goes invalid or black.

**Controller** (`src/controller/controller.py`). `Controller` is a thin facade the GUI talks to, and `MapleAgent` holds the state. A graph needs **both** a character class and a loaded CSV. Either setter (`setCharacterClass`, `loadMap`) returns `Result[BuildOutcome, str]`: `BUILT`, `PENDING_CSV`, or `PENDING_CHARACTER`. `Failure` is reserved for real errors, never for "the other half is missing". `CSVParser.CSV_file` is the single source of truth for whether a map is loaded, because `loadMap` resets it to `None` on parse failure. `buildPathGraphFor(node_map)` is the only place a `PathGraph` is constructed; both the runtime and the editor preview use it. The pathfinding/movement loop (`executePathFinding`) is commented out and references a stale movement API.

**Path graph** (`src/controller/path_graph.py`). A `networkx.MultiDiGraph` whose nodes are `Node` objects themselves, keyed by identity. `node_map` (uuid → Node) must always match `graph.nodes()` exactly, and `_createEdges` fails otherwise. Each node gets at most one edge per direction (left/right/up/down), pointing to the closest node that meets all three conditions:
- it is aligned within the x/y alignment allowance
- it is within the character class's `max_horizontal_traversal_dist` / `max_vertical_traversal_dist`
- for vertical edges, it passes the platform-edge rules

Ties go to the first-inserted node (strict `>`), so **`node_map` insertion order can change edges**. Edges are a distance heuristic only, with no terrain/gap awareness. Coordinates are minimap pixels with a top-left origin. Angle math negates y to work in a bottom-left frame.

**Character classes** (`src/classes/classes.py`). The `CharacterClass` ABC uses abstract properties, so a subclass missing `movement_type` or either max-traversal attribute fails at instantiation. `Kanna` is the only concrete class. The GUI's character picker builds instances from `_character_class_options` in `src/gui/gui.py`.

**Map CSVs.** The header is `nodetype,pos_in_minimap,can_fall,fall_dist_exceeds_vertical_tp,is_left_platform_edge,is_right_platform_edge`:
- `nodetype` is `blue_node` (a rotation stop, in file order) or `orange_node` (an intermediary).
- `pos_in_minimap` is a quoted `(x, y)` tuple.
- The booleans are `TRUE`/`FALSE`.
- `#` lines are comments or metadata (e.g. `# minimap_size=...`).

Two deliberately separate implementations share this format:
- `csv_parser.py` (runtime) creates `Node`s, which increments the global `Node.node_id_counter`.
- `map_csv.py` (editor) uses `EditorNode` with an editor-only `local_id`, so editor loads don't increment the counter.

Keep them in sync when changing the format. Map CSVs live in `maps/`, which is gitignored.

**Map editor** (`src/gui/map_editor.py`). A `tk.Toplevel` owned by `MapleGUI`, used to place, edit, and order nodes on the live minimap at `RENDER_SCALE=2`. It has dirty tracking, pre-save validation, and an unsaved-changes modal on close. Its edge preview builds a throwaway `PathGraph` with the same insertion order `write_editor_nodes` produces (rotation-order blue nodes first, then orange nodes by `local_id`), so the preview matches what the runtime builds after save→load. The preview is gated on a character class being selected. `MapleGUI` calls `onCharacterClassChanged()` when the selection changes.

**Input layer** (`src/input/`). Presses keys in the game through the ESP32-S3, which Windows sees as a BLE keyboard. The PC draws every timing value and sends a short schedule; the board only replays it. Nothing outside `src/input/` imports it yet, and the controller does not call it. Modules:
- `protocol.py` (pure): wire protocol v1, binary, little-endian: `ver`, then `op` or `kind`, then `seq` u16 (assigned by the PC, echoed in the replies). Commands `PLAY`, `RELEASE_ALL` and `PING` are writes with response to the command characteristic. A `PLAY` is at most 16 steps of (`delta` in 100 µs units, key mask), each step changes exactly one key, the last mask is 0, and the total is at most 10 s; the PC and the board both validate it. Notifications `ACCEPTED`, `DONE`, `REJECTED` and `PONG` come back on the notify characteristic. `PONG` `flags`: bit0 bonded, bit1 encrypted, bit2 authenticated, bit3 HID input report subscribed. `Key` is the mask: four arrows, `E`, `ALT` (left Alt, sent as a HID modifier) and `SPACE`, the only keys the bot sends.
- `timing.py` (pure): `TimingModel` draws every value from one seeded `numpy` `Generator`. `MODEL` holds the parameters and is the single source for the sampling and for the KS reference CDFs.
- `actions.py` (pure): `tap`, `walk`, `teleport`, `drop`, `jump` and `sequence` build a `Plan`, the steps plus the timing `Sample`s drawn for them.
- `link.py`: `BleLink` runs bleak on a daemon event-loop thread behind a blocking API (`connect`, `play`, `release_all`, `ping`, `faults`, `take_pongs`, `close`). `connect()` succeeds only once a `PONG` shows bonded, encrypted, subscribed and `mtu` ≥ 56.
- `input_layer.py`: `InputLayer`, the facade later features call. `tap`, `walk`, `teleport`, `drop`, `jump` and `sequence` each take an optional `stimulus_ns` and return `Result[ActionRecord, InputError]`, the same `Success`/`Failure` pattern as the controller. `release_all()` skips the timing and may be called from another thread to pre-empt a `play()` in flight, which then returns `Failure(ABORTED)`.

Reaction time is waited on the PC, from the stimulus to the send: with a `stimulus_ns` the layer draws R and sleeps `max(0, R - elapsed)` before sending, and R is not part of the schedule the board plays. The distributions, their sources and the measured reaction residual are in the spec's "Timing model (as built)" section (`docs/superpowers/specs/2026-10-21-p1a-input-over-bluetooth-design.md`).

**Input firmware** (`firmware/`). `Helix_BLE/` is the sketch (`Helix_BLE.ino`) with `protocol.h`, the board side of the protocol, and `notify_retry.h`. `test/` holds the host-compiled tests (`test_protocol.cpp`, `test_notify_retry.cpp`) and `vectors.json`, the golden vectors the C++ and Python protocol tests share, so a format change edits that file first. It is built and flashed with `arduino-cli`, not by Python. Build, flash, monitor, reset, pairing and the compile-time flags are in `firmware/README.md`.

**Input harness** (`src/input/harness/`). Each tool runs from the repo root as `python -m src.input.harness.<tool>`. The ones that send keys (`probe`, `soak`, `session_starts`) need the board, a real Bluetooth link and the "Helix input test sink" window (opened by the listener, where the board's keys land), and every run directory goes under `data/` (gitignored: `soak/`, `probe/`, `starts/`). Never run them with MapleStory open: each checks for a MapleStory process before sending a key and aborts if one is running, and `soak` and `probe` re-check every 60 s.
- `listener --out DIR [--seconds 20] [--no-block]`: a low-level keyboard hook that logs to `DIR/keys.jsonl`, plus the sink window. It records every key event on the machine, for all virtual-key codes, not only the bot keys. By default it also blocks the seven bot keys system-wide after recording them; `--no-block` passes them through.
- `probe [--probes LIST] [--run NAME] [--out DIR] [--seed N] [--cli PATH] [--link-debug] [--run-sheet]`: fixed measurement scripts through `InputLayer` and the listener, one run directory per probe. `--run-sheet` prints the live run sheet and runs nothing, `--link-debug` logs each probe's link records at DEBUG, and `--cli` is the `arduino-cli` used for the board-port check.
- `soak --commands N [--min-hours H] [--seed S] [--out DIR] [--short] [--faults reset_board=5,bluetooth_off_on=5]`: a seeded mix of commands through `InputLayer`, optionally with injected board resets and Bluetooth off/on. It waits up to 120 s for you to click the sink, aborts on `FOCUS_LOST` (the sink lost the foreground), and refuses a `--commands` that gives fewer than 1000 samples per KS type unless `--short` (a dry run or fault soak, which `realism` does not judge).
- `session_starts [--out DIR] [--seed N]`: 20 session starts (8 plain, 6 after a board reset, 6 after a Bluetooth off/on), each followed by one tap of `E` that the listener must see.
- `analyze RUN_DIR [--starts session_starts.jsonl]` and `realism RUN_DIR`: offline checks on a soak run directory (delivery, latency, faults, stuck keys and connection; then timing realism by a KS test per type against `MODEL`, and delivered timing). Both exit non-zero on a FAIL.
- `faults` (the injectors `soak` uses) and `g4_check` (a one-off check that resets the board) are helpers.

Because the listener logs every key pressed on the machine, `keys.jsonl` is effectively a keylog: it stays in the gitignored `data/` folder and is never committed or shared. Nobody uses the keyboard on the soak PC during a soak; any key pressed there is an extra key event that fails `analyze`'s Delivery check.

**Error handling.** Controller, parser, and graph code return `returns.result` `Success`/`Failure` and check them with `is_successful`, rather than raising. Follow that pattern in those layers.

## Incomplete / experimental code

- `src/input/` (see Input layer) replaces `src/ble/`: the input spec calls both files there experiments to replace, not APIs to preserve. `src/ble/` is still in the tree, untouched, and the notes below describe it.
- `src/ble/ble.py` does not parse (empty `async def establish_connection`).
- `src/ble/bleV2.py` connects to an ESP32 named `"Guiness"` **at import time** and imports `classes.classes` (not `src.`), so it only runs as a script from `src/`.
- `src/ble/test.py` is asyncio practice.
- `src/rune_solver/pipeline.py` loads YOLO models from placeholder `.../modeltraining/...` paths. The weights are in `assets/models/`. The stated plan is to move to ONNX runtime for CPU, platform-agnostic inference.

## Repo conventions

The upstream `.gitignore` keeps only what's needed to run the project. `scripts/`, `debugging_scripts/`, `data/`, `modeltraining/`, `runs/`, `maps/`, and root status docs (`RESUME.md`, `ISSUES.md`, etc.) are untracked. Unlike upstream, this repo **tracks `CLAUDE.md` and `docs/`**, so the vision doc, specs, and plans live in git history. Code comments sometimes cite upstream-only files (e.g. `docs/superpowers/map-editor/spec.md`, `ISSUES.md`). They aren't present in this clone.

Commit messages use conventional-commit style with a scope, e.g. `feat(editor): ...`, `fix(vision): ...`.
