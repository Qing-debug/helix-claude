# src/route: route walking (Feature B)

Plan: `docs/superpowers/plans/2026-10-23-p1b-route-walking.md` (hard rules at the top, from line 43). The one-paragraph summary is in the root CLAUDE.md.

## The loop

- `RouteGraph.build(path_graph, params: MovementParams)` (route_graph.py:82) wraps the controller's `PathGraph` and infers terrain (`terrain.infer_terrain`, route_graph.py:18).
- `RouteWalker.walk_leg` (walker.py:117): `self._positions.read()` (:141) → `planner.next_move(rg, pos, dst, rng)` (:150; it anchors the position with `locate.attach`, planner.py:17) → `moves.execute(layer, move, stimulus_ns)` (:169) → settle → repeat from the measured position. `run` cycles the rotation in laps and writes the leg and lap records.

## Seams

- controller: `Node` and `PathGraph` (route_graph.py:15-16, terrain.py:14-15). `terrain.py:163` calls `PathGraph._isVerticalEdgeValid`, so that private name is part of the seam.
- input: `InputLayer` / `InputError` (moves.py:5-6, walker.py:42-43), `Key` (planner.py:16 and `sim/`). The simulator's `SimLink` (sim/link.py) stands in for `BleLink`.
- gui: `src/gui/gui.py` imports `CharacterLocator` (`from_files`, `locate`) from `character.py` (decision H20), so a change to those signatures or a move of `character.py` reaches the GUI. Contract d is the reverse direction (route may not import gui).
- vision: `harness/record_minimap.py:163` imports `src.vision.vision` inside `main`; `harness/detection_check.py` imports `src.vision.vision_helper_lib` (cv2/numpy/enum only, not bettercam) inside `crop_minimap` (:128); `pyproject.toml` contract e has an `ignore_imports` entry for each (:57-58). `position.py` reads the shared minimap frame; `character.py` is the character locator.

## Rules enforced by tests

`tests/route/test_import_rules.py` pins plan hard rule 6 (the import allowlist), rule 4 (no teleport-reach literal such as 19 or 23 under `src/route`) and "no `src.vision` or Windows API at module level" outside `harness/`, `runtime.py` and `walk.py`. import-linter contracts d, e and f in `pyproject.toml` say the same at package level.

## Traps

- Frozen (the split-platform planner fix has landed, B14-inv-D1): `params`, `terrain`, `route_graph`, `locate`, `planner`, `moves`, `walker` and their tests (`test_params`, `test_terrain`, `test_route_graph`, `test_locate`, `test_planner`, `test_moves`, `test_walker_leg`, `test_walker_run`). Don't refactor or restyle them.
- `SimParams` (sim/world.py:117, what the simulated world does) and `MovementParams` (params.py:10, what the planner believes) share default values by copy. They are deliberately separate so the world can differ from the planner's belief: don't merge them.
- Stop guard (PB-D4; walker.py:53-54, `stop_now` at :223): after a stop, a guard thread (started at :231) sends `release_all` every 50 ms of wall time until `run` finishes. It is a safety mechanism (no key stays held): don't remove or slow it.
- Leg-log JSON keys are Feature D's contract: `tests/route/test_leg_log.py:26-29` pins `LEG_KEYS`, `MOVE_KEYS` and `LAP_KEYS`.
- `sim/run.py` `_write_offset_csv` (:141-160) is a third map-CSV reader and writer (`csv.DictReader` + `ast.literal_eval`, column name from `CSVParser().CSV_fields`); a map-format change must update it too.
- Runtime wiring (B19): `runtime.py` holds `StopControl` (F12 hook, Ctrl+C, `--minutes` timer) and the minimap capture helpers; `walk.py` is the supervised walk CLI (`python -m src.route.walk`; exit codes in its docstring, decisions B19-D1 and B19-D2). Both may import `src.vision` or Windows APIs only inside functions. Never run the CLI against a board without Hong's go; the live link check (`data/route/linkcheck/check.py`) is gitignored.
- Movement measurement tool (`harness/measure_moves.py`, B20): `python -m src.route.harness.measure_moves --supervised`. Its procedure (`_Session`) prompts on stdin between phases, settles each move by polling every 10 ms until one position holds for 0.5 s (B20-D2), appends to `measure.jsonl` and writes `summary.json`; exit codes and settle rules are in its docstring. A stop sets an event every prompt, move and settle poll checks and starts a `release_all` guard thread (as the walker's PB-D4 guard). `direction` is the `Key` name; no-op moves are left out of the summary (B20-D1). It shares `runtime.check_out`, `make_positions`, `SharedFrame` and `run_cleanup` with walk.py. Never run it without Hong (B21 step 3).
- Detection check (`harness/detection_check.py`, B18): `draft` and `check` read frames only through `load_frames` (:164), which returns `LoadedFrames(crops, uncroppable)` (:159). A full-screen frame that cannot be cropped is skipped, reported, drafted `null` and scored by `check` as nothing found (decision B18-D1). An unreadable PNG, unreadable corner images or no croppable frame at all exit 2. `draft` writes only inside `data/` and refuses to overwrite. `labels.json` is Hong's file: Claude never writes it. Tests: `tests/route/test_detection_check.py` (all synthetic; test 41 is the slow `python -I` import proof).

## Commands and fixtures

`python -m pytest tests/route -q` (about 5 s); the sim smoke command is in the root CLAUDE.md. The sim defaults to `tests/fixtures/fossil_foundry_2.csv` and `tests/fixtures/ff2_terrain.json` (sim/run.py:392-393).

Codes: `C*`, `DC*`, `G*`, `PB-D*` and task ids `B*` are in the plan; `B11-D1` and similar are `docs/decisions.md` headings.
