# src/route_view: the route view

Spec: `docs/superpowers/specs/2026-10-27-route-view-design.md` (5.4 the window, 5.8 where the code lives) and `docs/superpowers/specs/2026-10-31-route-tab-design.md` (5.9, 5.13: the view is hosted, not a process of its own). Plan: `docs/superpowers/plans/2026-10-27-route-view.md`. A window that shows what the route walker planned and why, from a run folder. It sends no key, needs no board and calls no Windows function. The walk no longer starts it (`--view` and the viewer spawn went in RT-2a); the two share only the files in the run folder, and the walk's side (`steps.jsonl` and the other files it writes) is in `src/route/CLAUDE.md`.

## Modules

- `model.py`: the pure view model, no Tk and no Windows call. `RunModel` (:285) is built from a `RunHeader` and `StepEvent`s from `src.route.step_log`; `load_folder` (:690) reads a whole run folder; `render_text` (:707) is the plain-text form the golden tests compare. Also `hop_route` (:213), `route_changed` (:218) and `read_frame_index` (:238). `SCALES` and `DEFAULT_SCALE = 4` (:63-64), the two warning lines `WARN_MAP` and `WARN_CODE` (:65-66), `FRAMES_DIR` and `FRAME_INDEX` (:67-68).
- `window.py`: `RouteViewWindow` (:62), the Tk window. `show(k)` (:188) draws step k; `set_live` (:234) hides the replay bar while live (`set_live(False)` puts it back at the end); `set_status` (:249) and `add_warning` (:252) write the status line. `_draw` (:518) paints the picture; `NODE_R`, `ORANGE_R` and `RING_R` (:41-43), `PANEL_WIDTH_PX` (:51), `NO_STEPS` (:54). Draw order, bottom to top (`window.py:518-549`): the minimap picture, the faint recent moves, the route line, the nodes and their labels (`_draw_nodes`, :551), the target ring, the hop numbers, the aim cross, the character. So the route line and the recent moves lie under the node dots, which keep the dots and numbers readable. Only "the picture is the lowest item" is pinned by a test.
- `follow.py`: following a folder a walk is still writing. `Follower` (:46) reads the complete new lines of `steps.jsonl` and `frames/index.jsonl`; `LiveController` (:81) polls every `POLL_MS = 100` (:31) with `tick` (:112) and `update` (:118), says so after `SILENT_S = 10.0` (:32) with no new step (`SILENT_MODE`, :34, and `REPLAY_HINT`, :35), and at `end` switches the window to replay with `set_live(False)` and no call on the root (`_end`, :197). The owner learns of the end from `ended`: there is no callback. `stop()` (:107) ends the ticks: after it `tick` and `update` return at once, since a tick already queued with `after` can still fire. Today only tests build a `LiveController`; the Route tab will own one (RT-8b).
- `__main__.py`: the replay command line. `_parser` (:39), `main` (:50), `_git_head` (:27).

## Command line

`python -m src.route_view --follow <folder> [--replay]` (`_parser`, `__main__.py:39`). It only replays and runs only on its own, outside any run (route-tab plan choice 10).

- Any run folder opens as a replay on its last step: a `steps.jsonl` with or without an `end` line, or only `legs.jsonl` (an older walk). `--replay` is accepted and ignored. `--ready` is gone, so argparse refuses it with exit 2.
- Exit 2 also for a window that cannot be opened and for an unreadable folder (`error: ...` on stderr).
- On stdout: `window built in <s> s`.
- A `LiveController` that has not seen a step for 10 s says so in the status line and points to the console (`follow.py:34-35`, plan choice 11). Opening a replay on a folder still being written by a running walk is the thing to avoid (see "Traps").

## Run-folder files

- Reads: `steps.jsonl` (or the legs-only fallback, `legs.jsonl` and `session.json`), `map.csv`, `minimap.png`, `frames/index.jsonl` and the frames. The names are constants in `src/route/step_log.py:56-58`; the format is `step_log.py`'s (format V1).
- Shared with the walk: nothing but those files. The `view.log`, `view.claim` and `view.ready` files went with the viewer spawn (spec 5.13).

## Rules enforced by tests

- Two import-linter contracts in `pyproject.toml`: "route_view boundaries" (:77-85) forbids `src.gui`, `src.main`, `src.vision`, `src.rune_solver`, every `src.input` module but `protocol`, and the route modules that pull those in (`walker`, `walk`, `session`, `moves`, `runtime`, `sim`); "route_view is a leaf" (:87-93) means nothing in `route`, `controller`, `gui`, `main`, `vision`, `input`, `classes` or `rune_solver` imports `src.route_view`. What the package may import is spec 5.8. `tests/route_view/test_route_view_imports.py` pins the module lists in both contracts (:30), that every `src.input` module but `protocol` is forbidden (:41) and that importing the model pulls in no Tk, `input_layer`, walker or GUI (:50).
- The viewer is built not to take focus (the design intent; the real-window check, H-1, has not run yet) and never rebuilds Tk's wrapper window (see the first trap); the test pins that it calls none of the banned names: `tests/route_view/test_view_window.py:538` (`test_10_the_viewer_never_takes_focus_or_rebuilds_the_wrapper_window`; the banned names are `_FOCUS_ATTRS`, :507, and `_WRAPPER_CALLS`, :508; a planted sample at :545).

## Traps

- While the view is live, never call `focus_force`, `focus_set`, `lift`, `tkraise` or `grab_set`, and never open a dialog.
- Never call `wm resizable`, `overrideredirect`, `transient` or `attributes`, in any form. After the idle pass Tk rebuilds the wrapper window on these, and the app makes no style change to that window during a run (spec 5.13). The Tk source line cited by the review is `win/tkWinWm.c:5073-5076` (for `resizable`, Tk 8.6.15); it was not re-read for this note. The test above enforces the names.
- Never open a replay while a walk runs. A replay window takes focus when clicked, and the board's keys go to whichever window has focus (root `CLAUDE.md`, Safety rules).
- The planned why line's route is `preview.path`; the panel's Route line is the hops' route (`model.py:14-15`). They can differ where the hops leave the path at an assumed landing (nine pairs on the FF2 map, none in the simulator runs).
- The why-line time is the elapsed time since the run's first event (`model.py:420-421`) as `mm:ss.mmm` (`h:mm:ss.mmm` past an hour; `RunModel._stamp`, `model.py:404-409`). It is open with Hong (RV3-D6, built as option A). The header's `wall_ns` and `perf_ns` (`step_log.py:83-84`) are recorded and not read by anything yet (RV4-D2 A).
- The scale buttons are in the replay bar, so they are hidden while the view is live; open with Hong (RV80-ADJ-D1).

## Tests and commands

`python -m pytest -q tests/route_view`. Test files: `test_route_view_imports`, `test_view_cli`, `test_view_follow`, `test_view_model` and `test_view_window`; flat helpers `view_runs.py` (builds run folders) and `view_tk.py` (a real, withdrawn Tk root that never runs a mainloop). A replay of a finished run: `python -m src.route_view --follow <folder>`.
