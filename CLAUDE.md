# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository. Each package also has a CLAUDE.md with its seams and traps (listed under "Per-directory notes"); read the one for the package you edit.

## What this is

Helix is a Windows-only MapleStory automation agent. It screen-captures the in-game minimap, finds the character on it, builds a navigation graph from a hand-authored map of nodes, and (eventually) drives movement through an ESP32-S3 board over BLE. A rune-solver (YOLO models) is scaffolded but not wired in.

This repo is a Claude-only parallel of the main Helix repo. Remotes: `origin` = `Qing-debug/helix-claude` (work here), `upstream` = `Qing-debug/Helix` (fetch-only; push is disabled).

## Safety rules

- Never push to `upstream` (its push URL is `DISABLED`).
- The harness tools that send keys (`probe`, `soak`, `session_starts` in `src/input/harness/`) need the board, a real Bluetooth link and the "Helix input test sink" window. Never run them with MapleStory open. Before the first key each asks once on its console "Is MapleStory closed? y/n" and goes on only on `y`; any other answer, or no answer (input closed), stops it with exit 2 before the listener, the link or any key (decision H60). Nothing checks again during the run, so MapleStory must not be started until the tool has ended. The tools never list the running programs (Hong's anti-cheat standing fact, H56). Details are in `src/input/CLAUDE.md`.
- That question (`soak.maple_check`, which reads the answer through `soak._read_answer`, and `_check_processes` in `soak`, `probe` and `session_starts`) is patched by name in the tests: never move or rename it (`src/input/CLAUDE.md`).
- Because the listener logs every key pressed on the machine, `keys.jsonl` is effectively a keylog: it stays in the gitignored `data/` folder and is never committed or shared.
- Nobody uses the keyboard on the soak PC during a soak; any key pressed there is an extra key event that fails `analyze`'s Delivery check.
- A walk starts only from the Route tab's Start button or `python -m src.route.walk --supervised`, and only with Hong's go: an agent never presses Start in the app (`python -m src.main`) and never runs the walk against the board (tests drive `RunSession` with fake links); it may run the simulator tool `tools/route_tab_sim.py measure`, which has no board and sends no key, on an idle PC (H15). The manual-mode check (`tools/route_tab_sim.py manual`) stays Hong's (H75). During a run the app opens no window and changes no window style (spec `docs/superpowers/specs/2026-10-31-route-tab-design.md` 5.12). Never open a standalone replay (`python -m src.route_view`) while a walk runs: a new window takes focus when clicked, and the board's keys go to whichever window has focus (`src/route_view/CLAUDE.md`).
- Hong's anti-cheat standing fact (H56): while MapleStory runs, nothing of ours may read or write its memory, open, query or enumerate its process or window, or ask Windows which window is in front. `tests/test_anti_cheat_names.py` fails if a banned call name appears in a `.py` file under `src/` or `tools/` (comments and strings count), except under `src/input/harness/`, whose tools run only with MapleStory closed; it does not scan `tests/`, `docs/` or `firmware/`.
- Hong's standing permission (H15 in `docs/decisions.md`, 2026-10-26): an agent may run `probe`, `soak` or `session_starts`, and may flash or reset the board, without asking him, when all three hold: the work needs it; MapleStory is not running (each tool asks "Is MapleStory closed? y/n" once at its start; an agent answers `y` only if Hong said in that same session that MapleStory is closed, and otherwise asks him first; it never answers `y` on its own judgment, decision H66); and nobody is using the PC. Check the time since the last keyboard or mouse input, or ask Hong, because the board's keys go to whichever window has focus. Every rule above still holds.

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
python tools/check.py            # ruff, basedpyright, lint-imports, pytest -m "not slow": about 90 s
python tools/check.py --full     # the same with the slow tests: about 179 s (178.5 s measured at 8208b2b); run it before you commit
python -m pytest tests/route -q  # about 41 s (about 25 s without the slow tests; tests/route_view about 18 s, tests/controller about 24 s)
python -m src.route.sim.run --laps 20 --seed 1 --min-single-error-legs 0   # sim smoke: 6/6 PASS, same output every run (371 moves, 20 no-ops, 0 drops, 44 teleports down, 2 jump-offs since C10 option B; the 5% no-op bar depends on the seed since the 12.5 px/s walk speed, H81: seeds 0, 2 and 8 miss it, seed 2 with 4.58%)
python -m src.route.sim.run --laps 20 --seed 2 --min-single-error-legs 0 --real-ground   # the same on Fossil Foundry 2's real ground (H86): 6/6 PASS, 364 moves, 23 no-ops, 0 drops, 46 teleports down, 2 knockbacks, 4 rope grabs, 2 rope moves, both teleports up (ropes from the fixture rope file, H88, H93; the same figures with C10 option B)
```

`tools/check.py` uses the tools next to the running interpreter, so `.venv\Scripts\python.exe tools/check.py` works without activating the venv. basedpyright runs against `.basedpyright/baseline.json`; how to keep it right is in `tests/CLAUDE.md`. Every test passes except the 10 skipped wire-only rejects in `tests/input/test_protocol.py` (`tests/CLAUDE.md` says where they really run).

A command that imports `src` under `python -I` must add the repo root to `sys.path` itself, because `-I` drops the current directory: `python -I -c "import sys; sys.path.insert(0, '.'); import src.gui.map_editor"`.

Run the app from the repo root as a module, since all imports are rooted at `src.`:

```
python -m src.main
```

`main.py` starts the minimap capture thread and then blocks in the Tk mainloop. The main window opens at once; its minimap area appears when the first frame arrives (the buttons that need it, including Open Map Editor, say "Minimap not found yet" until then), so the game must be visible on the captured monitor (`output_idx = 1` in `src/vision/vision.py`, i.e. the second monitor).

Helix runs as administrator (H82, RT-FIX-1). MapleStory runs elevated, and Windows (UIPI) does not show a program that is not elevated the keys typed into an elevated window, so a normal Helix never hears F12 with the game in front and the F12 stop does nothing (proved with a listener run as a normal user and as administrator). So `python -m src.main` checks at start whether the process is elevated (`runtime.ensure_elevated`, `src/route/runtime.py`); if not, it starts the same command again (same interpreter and arguments, run from the repo root through a small `-c` bootstrap, so it does not depend on the folder Windows gives the elevated copy) with ShellExecute's "runas" verb, which shows one Windows prompt per start, and the first copy exits. If the prompt is declined, the app keeps running and says so in the terminal and in the Setup tab's console box; the Route tab then refuses Start with "F12 cannot stop a walk while MapleStory is in front, because Helix is not running as administrator". The walk CLI and the measurement tool (`harness/measure_moves.py`) do not relaunch: unelevated they print "Helix must run as administrator so F12 can stop the walk with MapleStory in front; start it from an administrator terminal" and exit 11, after their usage checks and whatever `HELIX_NO_ELEVATE` says. The simulator, replays, the map editor and everything else that sends no key work unelevated. Tests and agents never take the prompt: set `HELIX_NO_ELEVATE=1` (`tools/check.py` does) for any run that sends no key, and never start the app (the prompt would appear on Hong's screen), the walk CLI or the measurement tool (they send keys) for real.

The window has two tabs. **Setup** is the old main screen (character class, map, the minimap, the console box, the map editor). **Route** has the run settings, Start and Stop, the route view (the live minimap while idle, the live view during a run, and after a run its last picture and why lines) and the Saved runs list (`src/gui/CLAUDE.md`). A real walk starts from the Route tab's Start button or from `python -m src.route.walk --map maps/<map>.csv --supervised` (see the Safety rules before either).

The route view (a window that shows what the route walker planned and why) is also a standalone program that only replays: `python -m src.route_view --follow <folder> [--replay]` on a finished run's folder (see the Safety rules before opening one). `python tools/route_tab_sim.py measure` runs the Route tab against the simulator (no board, no key) and times the key path; its `manual` mode is Hong's check (`src/route/CLAUDE.md`).

## Architecture

**Threads and shared state.** `SharedInfo` (`src/main.py`) holds `minimap_frame` plus `minimap_frame_lock`. The vision thread (`capture_and_process_minimap`) is the only writer. The main GUI and the map editor each poll it with their own `_updateCanvas` loop via `tk.after`; the controller and `src/route/position.py` also read it under the lock. The frame is stored as **RGB** (vision converts BGR→RGB for Tk/PIL display). The Route tab's Start runs the walk (`RunSession`, `src/route/session.py`) on a daemon worker thread (`route-run`): it reads the frame through `src/route/position.py`, never touches Tk, and posts `RunEvent`s to a `queue.Queue` that the tab drains with `tk.after` every 100 ms. While a run is live and the Setup tab is hidden, the main window's `_updateCanvas` builds no minimap picture (spec 5.7, `src/gui/CLAUDE.md`). Every run adds a recorder thread (H62), and the saved-run list has a `saved-run-sizes` thread that sums folder sizes (`src/route/CLAUDE.md`, `src/gui/CLAUDE.md`).

**Vision** (`src/vision/`). Template-matches four minimap corner borders (`assets/brds/*_grayscale.png` + masks) across the whole monitor using masked `TM_CCORR_NORMED`. inf/nan/>1 scores are scrubbed before `minMaxLoc`. Corners must pass a confidence threshold, ordering checks, and rectangle-alignment checks. After that it grabs just the minimap region for `MINIMAP_POSITION_RECALC_INTERVAL` frames before re-detecting. It re-detects early if the region goes invalid or black. Each grab waits inside Windows for the next new desktop image (`src/vision/frame_wait.py`, up to 100 ms), and the loop sleeps a 20 ms floor between frames (H77, H80).

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

Both check the whole file with `map_format.read_rows` before building anything and give the user the same message, naming the file, each bad line and how to fix it (decision H16); the few differences left are listed in `src/controller/CLAUDE.md`. The route simulator has a third reader (`src/route/CLAUDE.md`). Map CSVs live in `maps/`, which is gitignored. A map may also have a rope file beside it, `<map stem>.ropes.json` (H94): the marks of its ropes, which routes never use and the walker and the editor treat as hazards (H91, H93). It has its own reader, `src/controller/rope_file.py`, the CSV readers never see it, and a map with no rope file works as before (`src/controller/CLAUDE.md`).

**Map editor** (`src/gui/map_editor.py`). A `tk.Toplevel` owned by `MapleGUI`, used to place, edit, and order nodes on the live minimap at `RENDER_SCALE=2`. It has dirty tracking, pre-save validation, and an unsaved-changes modal on close. Its edge preview builds a throwaway `PathGraph` in `map_format.ordered_local_ids` order, the order `write_editor_nodes` writes (rotation-order blue nodes first, then orange nodes by `local_id`), so the preview matches what the runtime builds after save→load. The preview is gated on a character class being selected. `MapleGUI` calls `onCharacterClassChanged()` when the selection changes. It also marks the map's ropes (H91 to H93): a Ropes panel finds them from the live minimap or takes two clicks for one, and moves or deletes each; the editor refuses a node near where a rope meets a platform, draws such nodes with a red ring and blocks Save with a list of them, and Save writes the rope file beside the CSV (`src/gui/CLAUDE.md`).

**Input layer** (`src/input/`). Presses keys in the game through the ESP32-S3, which Windows sees as a BLE keyboard. The PC draws every timing value and sends a short schedule; the board only replays it. `src/route/` imports it (`moves.py`, `walker.py`, `planner.py`, `sim/`, `session.py`, which builds a real `BleLink` by default, and the two live tools `walk.py` and `harness/measure_moves.py`); the controller does not, and the GUI reaches it only through `session.py` (an import-linter contract in `pyproject.toml`). Callers use `InputLayer` (`input_layer.py`): `tap`, `walk`, `teleport`, `drop`, `jump_off` (Alt plus Left or Right, to leave a rope; H88), `jump` and `sequence` each take an optional `stimulus_ns` and return `Result[ActionRecord, InputError]`, the same `Success`/`Failure` pattern as the controller. `release_all()` skips the timing and may be called from another thread to pre-empt a `play()` in flight, which then returns `Failure(ABORTED)`. `InputLayer` depends only on the `Link` protocol (`play`, `release_all`), which `BleLink` and the simulator's `SimLink` satisfy. Protocol, link, harness tools and test fakes: `src/input/CLAUDE.md`; board and firmware tests: `firmware/CLAUDE.md`.

Reaction time is waited on the PC, from the stimulus to the send: with a `stimulus_ns` the layer draws R and sleeps `max(0, R - elapsed)` before sending, and R is not part of the schedule the board plays. The distributions, their sources and the measured reaction residual are in the spec's "Timing model (as built)" section (`docs/superpowers/specs/2026-10-21-p1a-input-over-bluetooth-design.md`).

**Route** (`src/route/`). Walks the map's rotation one leg at a time. `RouteGraph.build(path_graph, params)` adds inferred terrain and move costs to the controller's `PathGraph`; `RouteWalker.walk_leg` reads the character's position, asks `planner.next_move` for one move, plays it with `moves.execute` through `InputLayer`, waits for the character to settle and re-plans from the measured position. The position is the point 3 px below the minimap dot's centre, where map nodes sit; a character that stays still for 0.5 s where the map cannot place it is attached to the platform line within 6 px, and with none in range the run ends with exit 10 and `stop.json` reason `off_map` (H85). Inside a rope marked in the map's rope file the walker gets off the rope first: a still read there the map cannot place, or a read the map places that the move sent from it left exactly the same (a move that did nothing), is the character on the rope after 0.1 s, and it teleports to the nearer platform in reach (no teleport try when none is in reach), else jumps off with Alt plus Left or Right, and only then ends `off_map` (a read the map places starts the order again instead) (H88, H93). Going down it always teleports when a teleport reaches (H87). The simulator (`src/route/sim/`) and `RunSession` (`src/route/session.py`, the body of a real walk) both drive it: `RunSession` runs for the supervised walk CLI (`python -m src.route.walk`, with `runtime.py`'s stop control) and for the Route tab's Start button (`src/gui/route_tab.py`, on a worker thread). `tools/route_tab_sim.py` runs the tab itself against the simulator. Seams, frozen files and traps: `src/route/CLAUDE.md`.

**Route view** (`src/route_view/`). A Tk window that shows, step by step, what the route walker planned and why: the minimap with the route, the hops, the aim and the character, and a log of the planner's reasons. The walk writes a step log (`steps.jsonl`, with `map.csv` and `minimap.png`) into its run folder, and the viewer reads it. The walk and the viewer share only files in the run folder; only the Route tab (`src/gui/route_tab.py`, `src/gui/live_minimap.py`) imports the viewer, and it imports no game-input, walker, simulator, GUI or vision module (two import-linter contracts). The live window is built not to take focus from the game. In the Route tab it follows a run live and, when the run ends, stays on its last picture and why lines (a "Back to live minimap" button returns to the live minimap); a finished run replays, with a slider, from the tab's Saved runs list or by hand with `python -m src.route_view --follow <folder>`. The viewer's seams, focus rules and traps: `src/route_view/CLAUDE.md`; the walk's side (the step log, the preview of the planner's route): `src/route/CLAUDE.md`.

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
