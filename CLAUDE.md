# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository. Each package also has a CLAUDE.md with its seams and traps (listed under "Per-directory notes"); read the one for the package you edit.

## What this is

Helix is a Windows-only MapleStory automation agent. It screen-captures the in-game minimap, finds the character on it, builds a navigation graph from a hand-authored map of nodes, and (eventually) drives movement through an ESP32-S3 board over BLE. A rune-solver (YOLO models) is scaffolded but not wired in.

This repo is a Claude-only parallel of the main Helix repo. Remotes: `origin` = `Qing-debug/helix-claude` (work here), `upstream` = `Qing-debug/Helix` (fetch-only; push is disabled).

## Safety rules

- Never push to `upstream` (its push URL is `DISABLED`).
- The harness tools that send keys (`probe`, `soak`, `session_starts` in `src/input/harness/`) need the board, a real Bluetooth link and the "Helix input test sink" window. Never run them with MapleStory open: each checks for a MapleStory process before sending a key and aborts if one is running, and `soak` and `probe` re-check every 60 s. Details are in `src/input/CLAUDE.md`.
- That MapleStory check (`soak.maple_check`, `soak._process_names`, and `_check_processes` in `probe` and `session_starts`) is patched by name in the tests: never move or rename it (`src/input/CLAUDE.md`). The route view's N1 tool has the same kind of check, `_maple_check` in `src/route/harness/view_check.py` (:155), patched by name in `tests/route/test_view_check.py` (:240): never move or rename it either (`src/route/CLAUDE.md`).
- Because the listener logs every key pressed on the machine, `keys.jsonl` is effectively a keylog: it stays in the gitignored `data/` folder and is never committed or shared.
- Nobody uses the keyboard on the soak PC during a soak; any key pressed there is an extra key event that fails `analyze`'s Delivery check.
- Never open a route-view replay (`python -m src.route_view --follow <folder>` on a finished run, or with `--replay`) while a walk runs: a replay window takes focus when clicked, and the board's keys go to whichever window has focus (`src/route_view/CLAUDE.md`). The live view that `walk --view` starts never takes focus.
- Hong's standing permission (H15 in `docs/decisions.md`, 2026-10-26): an agent may run `probe`, `soak` or `session_starts`, and may flash or reset the board, without asking him, when all three hold: the work needs it; MapleStory is not running (the tools' own check still applies and is never bypassed); and nobody is using the PC. Check the time since the last keyboard or mouse input, or ask Hong, because the board's keys go to whichever window has focus. Every rule above still holds.

## Running

`requirements.txt` pins the runtime dependencies. `requirements-dev.txt` is `-r requirements.txt` plus `pytest`, `scipy`, `winrt-Windows.Devices.Radios` and the check tools `ruff`, `basedpyright` and `import-linter`, which are configured in `pyproject.toml`. `pytest.ini` is still pytest's config (`testpaths = tests`, `pythonpath = .`, `--strict-markers`, the `slow` marker). There is no app build step. The rune solver's dependencies (`ultralytics` and what it pulls in) are not in `requirements.txt`; the runes feature (Feature C, `docs/superpowers/specs/2026-10-21-p1c-runes-design.md`) adds them.

The working venv is Python 3.14.0 (`networkx==3.6.1` declares `!=3.14.1`). From the repo root:

```
py -3.14 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
```

(`.venv\Scripts\activate` in cmd, `source .venv/Scripts/activate` in Git Bash.)

Checks, from the repo root (wall times measured on the dev PC when this text was written):

```
python tools/check.py            # ruff, basedpyright, lint-imports, pytest -m "not slow": about 31 s
python tools/check.py --full     # the same with the slow tests: about 74 s; run it before you commit
python -m pytest tests/route -q  # about 32 s (about 22 s without the slow tests; tests/route_view about 15 s, tests/controller about 3 s)
python -m src.route.sim.run --laps 20 --seed 1 --min-single-error-legs 0   # sim smoke: 6/6 PASS, same output every run
```

`tools/check.py` uses the tools next to the running interpreter, so `.venv\Scripts\python.exe tools/check.py` works without activating the venv. basedpyright runs against `.basedpyright/baseline.json`; how to keep it right is in `tests/CLAUDE.md`. Every test passes except the 10 skipped wire-only rejects in `tests/input/test_protocol.py` (`tests/CLAUDE.md` says where they really run).

A command that imports `src` under `python -I` must add the repo root to `sys.path` itself, because `-I` drops the current directory: `python -I -c "import sys; sys.path.insert(0, '.'); import src.gui.map_editor"`.

Run the app from the repo root as a module, since all imports are rooted at `src.`:

```
python -m src.main
```

`main.py` starts the minimap capture thread and then blocks in the Tk mainloop. The main window opens at once; its minimap area appears when the first frame arrives (the buttons that need it, including Open Map Editor, say "Minimap not found yet" until then), so the game must be visible on the captured monitor (`output_idx = 1` in `src/vision/vision.py`, i.e. the second monitor).

The route view (a window that shows what the route walker planned and why) starts with `python -m src.route.walk ... --view`, or is opened on a run folder by hand with `python -m src.route_view --follow <folder> [--replay]` (see the Safety rules before opening one).

## Architecture

**Threads and shared state.** `SharedInfo` (`src/main.py`) holds `minimap_frame` plus `minimap_frame_lock`. The vision thread (`capture_and_process_minimap`) is the only writer. The main GUI and the map editor each poll it with their own `_updateCanvas` loop via `tk.after`; the controller and `src/route/position.py` also read it under the lock. The frame is stored as **RGB** (vision converts BGR→RGB for Tk/PIL display).

**Vision** (`src/vision/`). Template-matches four minimap corner borders (`assets/brds/*_grayscale.png` + masks) across the whole monitor using masked `TM_CCORR_NORMED`. inf/nan/>1 scores are scrubbed before `minMaxLoc`. Corners must pass a confidence threshold, ordering checks, and rectangle-alignment checks. After that it grabs just the minimap region for `MINIMAP_POSITION_RECALC_INTERVAL` frames before re-detecting. It re-detects early if the region goes invalid or black.

**Controller** (`src/controller/controller.py`). `Controller` is a thin facade over `MapleAgent`, which holds the state (the GUI also reaches past the facade; see `src/gui/CLAUDE.md`). A graph needs **both** a character class and a loaded CSV. Either setter (`setCharacterClass`, `loadMap`) returns `Result[BuildOutcome, str]`: `BUILT`, `PENDING_CSV`, or `PENDING_CHARACTER`. `Failure` is reserved for real errors, never for "the other half is missing". `CSVParser.CSV_file` is the single source of truth for whether a map is loaded, because `loadMap` resets it to `None` on parse failure. `buildPathGraphFor(node_map)` is the only place a `PathGraph` is constructed; the runtime, the editor preview and the route simulator all go through it.

**Path graph** (`src/controller/path_graph.py`). A `networkx.MultiDiGraph` whose nodes are `Node` objects themselves, keyed by identity. `node_map` (uuid → Node) must always match `graph.nodes()` exactly, and `_createEdges` fails otherwise. Each node gets at most one edge per direction (left/right/up/down), to a near node that is aligned and within the character class's reach; how that node is chosen (vertical edges included) and why **`node_map` insertion order can change edges** is in `src/controller/CLAUDE.md`. Edges are a distance heuristic only, with no terrain/gap awareness. Coordinates are minimap pixels with a top-left origin. Angle math negates y to work in a bottom-left frame.

**Character classes** (`src/classes/classes.py`). The `CharacterClass` ABC uses abstract properties, so a subclass missing `movement_type` or either max-traversal attribute fails at instantiation. `Kanna` is the only concrete class. The GUI's character picker builds `_character_class_options` in `src/gui/gui.py` from the direct subclasses (`src/gui/CLAUDE.md`).

**Map CSVs.** The header is `nodetype,pos_in_minimap,can_fall,fall_dist_exceeds_vertical_tp,is_left_platform_edge,is_right_platform_edge`:
- `nodetype` is `blue_node` (a rotation stop, in file order) or `orange_node` (an intermediary).
- `pos_in_minimap` is a quoted `(x, y)` tuple.
- The booleans are `TRUE`/`FALSE`.
- `#` lines are comments or metadata (e.g. `# minimap_size=...`).

The format lives in one place, `src/controller/map_format.py` (header, node types, boolean and position cells, and `ordered_local_ids`, the editor's write order); change it there. Two readers use it and stay deliberately separate:
- `csv_parser.py` (runtime) creates `Node`s, which increments the global `Node.node_id_counter`.
- `map_csv.py` (editor) uses `EditorNode` with an editor-only `local_id`, so editor loads don't increment the counter.

Both check the whole file with `map_format.read_rows` before building anything and give the user the same message, naming the file, each bad line and how to fix it (decision H16); the few differences left are listed in `src/controller/CLAUDE.md`. The route simulator has a third reader (`src/route/CLAUDE.md`). Map CSVs live in `maps/`, which is gitignored.

**Map editor** (`src/gui/map_editor.py`). A `tk.Toplevel` owned by `MapleGUI`, used to place, edit, and order nodes on the live minimap at `RENDER_SCALE=2`. It has dirty tracking, pre-save validation, and an unsaved-changes modal on close. Its edge preview builds a throwaway `PathGraph` in `map_format.ordered_local_ids` order, the order `write_editor_nodes` writes (rotation-order blue nodes first, then orange nodes by `local_id`), so the preview matches what the runtime builds after save→load. The preview is gated on a character class being selected. `MapleGUI` calls `onCharacterClassChanged()` when the selection changes.

**Input layer** (`src/input/`). Presses keys in the game through the ESP32-S3, which Windows sees as a BLE keyboard. The PC draws every timing value and sends a short schedule; the board only replays it. `src/route/` imports it (`moves.py`, `walker.py`, `planner.py`, `sim/`, and the two live tools that build a real `BleLink`: `walk.py` and `harness/measure_moves.py`); the controller and the GUI do not. Callers use `InputLayer` (`input_layer.py`): `tap`, `walk`, `teleport`, `drop`, `jump` and `sequence` each take an optional `stimulus_ns` and return `Result[ActionRecord, InputError]`, the same `Success`/`Failure` pattern as the controller. `release_all()` skips the timing and may be called from another thread to pre-empt a `play()` in flight, which then returns `Failure(ABORTED)`. `InputLayer` depends only on the `Link` protocol (`play`, `release_all`), which `BleLink` and the simulator's `SimLink` satisfy. Protocol, link, harness tools and test fakes: `src/input/CLAUDE.md`; board and firmware tests: `firmware/CLAUDE.md`.

Reaction time is waited on the PC, from the stimulus to the send: with a `stimulus_ns` the layer draws R and sleeps `max(0, R - elapsed)` before sending, and R is not part of the schedule the board plays. The distributions, their sources and the measured reaction residual are in the spec's "Timing model (as built)" section (`docs/superpowers/specs/2026-10-21-p1a-input-over-bluetooth-design.md`).

**Route** (`src/route/`). Walks the map's rotation one leg at a time. `RouteGraph.build(path_graph, params)` adds inferred terrain and move costs to the controller's `PathGraph`; `RouteWalker.walk_leg` reads the character's position, asks `planner.next_move` for one move, plays it with `moves.execute` through `InputLayer`, waits for the character to settle and re-plans from the measured position. The simulator (`src/route/sim/`) and the supervised walk CLI (`python -m src.route.walk`, with `runtime.py`'s stop control) drive it. Seams, frozen files and traps: `src/route/CLAUDE.md`.

**Route view** (`src/route_view/`). A Tk window that shows, step by step, what the route walker planned and why: the minimap with the route, the hops, the aim and the character, and a log of the planner's reasons. The walk writes a step log (`steps.jsonl`, with `map.csv` and `minimap.png`) into its run folder, and `walk --view` starts the viewer as a child process by module name (`python -m src.route_view`) and waits for its window before the countdown. The walk and the viewer share only files in the run folder; nothing else in `src` imports the viewer, and it imports no game-input, walker, simulator, GUI or vision module (two import-linter contracts). The live window never takes focus from the game; at the end of a run it becomes a replay with a slider. Replay a finished run with `python -m src.route_view --follow <folder>`. The viewer's seams, focus rules and traps: `src/route_view/CLAUDE.md`; the walk's side (the step log, the preview of the planner's route, the N1 check): `src/route/CLAUDE.md`.

**Error handling.** Controller, parser, graph and route code return `returns.result` `Success`/`Failure` and check them with `is_successful`, rather than raising. Follow that pattern in those layers.

## Per-directory notes

`src/input/CLAUDE.md`, `src/route/CLAUDE.md`, `src/route_view/CLAUDE.md`, `src/controller/CLAUDE.md`, `src/gui/CLAUDE.md`, `src/vision/CLAUDE.md`, `tests/CLAUDE.md` and `firmware/CLAUDE.md` hold each package's seams, traps (with `file:line`) and commands. A fact lives in one file; the others point to it. The exception is the safety rules, which are deliberately repeated where an agent will act on them.

## Incomplete / experimental code

- `src/rune_solver/pipeline.py` loads YOLO models from placeholder `.../modeltraining/...` paths. The weights are in `assets/models/`. The stated plan is to move to ONNX runtime for CPU, platform-agnostic inference.

## Repo conventions

The upstream `.gitignore` keeps only what's needed to run the project. `scripts/`, `debugging_scripts/`, `data/`, `modeltraining/`, `runs/`, `maps/`, and root status docs (`RESUME.md`, `ISSUES.md`, etc.) are untracked. Unlike upstream, this repo **tracks `CLAUDE.md` and `docs/`**, so the vision doc, specs, and plans live in git history. Code comments sometimes cite upstream-only files (e.g. `docs/superpowers/map-editor/spec.md`, `ISSUES.md`). They aren't present in this clone.

Commit messages use conventional-commit style with a scope, e.g. `feat(editor): ...`, `fix(vision): ...`.

Naming: new code is snake_case (as in `src/input` and `src/route`); `src/controller`, `src/gui` and `src/vision` stay camelCase until the rename, so don't mix styles inside a file.

Codes in comments and docs: `PA-*`, `PB-*`, `A-*`, `B11-D1`, `H1`… are headings in `docs/decisions.md` (search for the heading); `C1`, `G16`, `DC4`, task ids such as `B12` and hard-rule numbers are in the feature's plan under `docs/superpowers/plans/` (`2026-10-21-p1a-input.md` for input, `2026-10-23-p1b-route-walking.md` for route).
