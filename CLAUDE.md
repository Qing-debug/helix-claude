# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Helix is a Windows-only MapleStory automation agent. It screen-captures the in-game minimap, finds the character on it, builds a navigation graph from a hand-authored map of nodes, and (eventually) drives movement through an ESP32-S3 board over BLE. A rune-solver (YOLO models) is scaffolded but not wired in.

This repo is a Claude-only parallel of the main Helix repo. Remotes: `origin` = `Qing-debug/helix-claude` (work here), `upstream` = `Qing-debug/Helix` (fetch-only; push is disabled).

## Running

There is no requirements file, test suite, linter config, or build step. Dependencies, as imported: `opencv-python`, `numpy`, `bettercam`, `networkx`, `returns`, `Pillow`, `matplotlib`, `bleak`, `ultralytics`.

Run from the repo root as a module, since all imports are rooted at `src.`:

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

**Error handling.** Controller, parser, and graph code return `returns.result` `Success`/`Failure` and check them with `is_successful`, rather than raising. Follow that pattern in those layers.

## Incomplete / experimental code

- `src/ble/ble.py` does not parse (empty `async def establish_connection`).
- `src/ble/bleV2.py` connects to an ESP32 named `"Guiness"` **at import time** and imports `classes.classes` (not `src.`), so it only runs as a script from `src/`.
- `src/ble/test.py` is asyncio practice.
- `src/rune_solver/pipeline.py` loads YOLO models from placeholder `.../modeltraining/...` paths. The weights are in `assets/models/`. The stated plan is to move to ONNX runtime for CPU, platform-agnostic inference.

## Repo conventions

The upstream `.gitignore` keeps only what's needed to run the project. `scripts/`, `debugging_scripts/`, `data/`, `modeltraining/`, `runs/`, `maps/`, and root status docs (`RESUME.md`, `ISSUES.md`, etc.) are untracked. Unlike upstream, this repo **tracks `CLAUDE.md` and `docs/`**, so the vision doc, specs, and plans live in git history. Code comments sometimes cite upstream-only files (e.g. `docs/superpowers/map-editor/spec.md`, `ISSUES.md`). They aren't present in this clone.

Commit messages use conventional-commit style with a scope, e.g. `feat(editor): ...`, `fix(vision): ...`.
