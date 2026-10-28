# src/route_view: the route view

Spec: `docs/superpowers/specs/2026-10-27-route-view-design.md` (5.4 the window, 5.7 focus, 5.8 where the code lives, 5.9 the walk's side). Plan: `docs/superpowers/plans/2026-10-27-route-view.md`. A window that shows what the route walker planned and why, from a run folder. It is a separate process: `python -m src.route.walk --view` starts it by module name, and the two share only files in the run folder. It sends no key and needs no board. The walk's side (`steps.jsonl` and the other files it writes) is in `src/route/CLAUDE.md`; the spawn and the wait are below.

## Modules

- `model.py`: the pure view model, no Tk and no Windows call. `RunModel` (:285) is built from a `RunHeader` and `StepEvent`s from `src.route.step_log`; `load_folder` (:690) reads a whole run folder; `render_text` (:707) is the plain-text form the golden tests compare. Also `hop_route` (:213), `route_changed` (:218) and `read_frame_index` (:238). `SCALES` and `DEFAULT_SCALE = 4` (:63-64), the two warning lines `WARN_MAP` and `WARN_CODE` (:65-66), `FRAMES_DIR` and `FRAME_INDEX` (:67-68).
- `window.py`: `RouteViewWindow` (:62), the Tk window. `show(k)` (:188) draws step k; `set_live` (:234) hides the replay bar while live (`set_live(False)` puts it back at the end); `set_status` (:249) and `add_warning` (:252) write the status line. `_draw` (:518) paints the picture; `NODE_R`, `ORANGE_R` and `RING_R` (:41-43), `PANEL_WIDTH_PX` (:51), `NO_STEPS` (:54). Draw order, bottom to top (`window.py:518-549`): the minimap picture, the faint recent moves, the route line, the nodes and their labels (`_draw_nodes`, :551), the target ring, the hop numbers, the aim cross, the character. So the route line and the recent moves lie under the node dots, which keep the dots and numbers readable. Only "the picture is the lowest item" is pinned by a test.
- `follow.py`: following a folder a walk is still writing. `Follower` (:58) reads the complete new lines of `steps.jsonl` and `frames/index.jsonl`; `LiveController` (:93) polls every `POLL_MS = 100` (:35) with `tick` (:122) and `update` (:128), says so after `SILENT_S = 10.0` (:36) with no new step (`SILENT_MODE`, :38, and `REPLAY_HINT`, :39), and at `end` switches the window to replay (`_end`, :207). `Focus` (:42) is the protocol for the three `win_focus` functions, so tests pass a fake.
- `win_focus.py`: the only Win32 calls (see "Focus" below). `wrapper_hwnd` (:60), `set_live_style` (:75), `show_first` (:103), `FIRST_SHOW` (:38). Nothing here raises; each function returns `Failure("not Windows")` off Windows before it touches `ctypes`.
- `__main__.py`: the command line. `_parser` (:46), `main` (:59), `_built` (:106), `_claim` (:110), `_first_show` (:120), `_write_ready` (:134).

## Command line

`python -m src.route_view --follow <folder> [--ready PATH] [--replay]` (`__main__.py:46-56`).

- A folder whose `steps.jsonl` ends with `end`, or that has no `steps.jsonl` (a `legs.jsonl` from an older walk), opens as a replay (`__main__.py:73`, `:87`). `--replay` opens any folder as a replay, including one with no `end` (a walk that crashed or was killed). Otherwise the view follows the folder live.
- `--replay` with `--ready` exits 2 (`__main__.py:62-64`); so do a window that cannot be opened (:69) and an unreadable folder (:78). A walk never passes `--replay`.
- A live view that has not seen a step for 10 s says so in the status line and names `--replay` (`follow.py:38-39`, decision RV4-D1 A). Opening a replay on a folder still being written by a running walk is the thing to avoid (see "Traps").
- On stdout (which the walk sends to `view.log`): `window built in <s> s` (`__main__.py:106-107`) and `first show: root.state() = ...` (:97), which the N1 check reads. A failed focus call prints `focus call failed: <error>` (`__main__.py:120-131`; at `end`, `follow.py:214-216`).

## Run-folder files

- Reads: `steps.jsonl` (or the legs-only fallback, `legs.jsonl` and `session.json`), `map.csv`, `minimap.png`, `frames/index.jsonl` and the frames. The names are constants in `src/route/step_log.py:56-61`; the format is `step_log.py`'s (format V1).
- Shared with the walk: `view.log` (the viewer's output; the walk opens it), `view.claim` and `view.ready`.
- The claim rule (spec 5.9 step 3, `docs/superpowers/specs/2026-10-27-route-view-design.md:575-592`). The viewer, once its window is built and still withdrawn, creates `view.claim` with `open(path, "x")` (`__main__.py:110-117`; `False` on any `OSError`, the safe side). If it gets the claim, it shows its window without activating it and then writes `view.ready` (a temporary name, then `os.replace`; the contents are its pid; `__main__.py:134-143`). If the walk holds the claim, the viewer follows hidden and shows its window only when it reads `end` (`__main__.py:93-94`, `follow.py:219-220`). The walk's side is `walk.py:148-165` (`_wait_for_viewer`: 15 s, `_VIEW_READY_S` at `walk.py:97`, then 2 s more when the viewer holds the claim, `walk.py:98`, and one line, `VIEW_LATE_LINE`, `walk.py:100-101`).
- The spawn (`walk.py:132-146`): `CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP`, `python -u -m src.route_view`, stdin to `DEVNULL`, output to `view.log`, no `shell=True` and no `startupinfo`. The reasons (the venv redirector, the console, the job object, why `-u`) are in spec 5.9 step 2 (`docs/superpowers/specs/2026-10-27-route-view-design.md:544-574`); do not repeat them here.

## Focus: the no-activate style (spec 5.7)

- The live window must never take the foreground from the game. Before its first show it gets `WS_EX_NOACTIVATE` and loses `WS_EX_APPWINDOW` on Tk's wrapper window (`win_focus.set_live_style`, :75), and it is first shown with `ShowWindow(SW_SHOWNOACTIVATE)` (`show_first`, :103). At `end` the styles come back, the window is hidden and shown again so the taskbar sees the change, and `window.set_live(False)` runs (`LiveController._end`, `follow.py:207-221`; at most once).
- `win_focus.FIRST_SHOW = "noactivate"` (`win_focus.py:38`) is the one-line switch to spec 5.7's fallback, `"deiconify"` (`show_first` then calls Tk's `root.deiconify()`, `win_focus.py:108-110`). Change it only if the N1 check's first-show part fails.
- The idle pass. `main` calls `root.update_idletasks()` before the claim (`__main__.py:91`) because Tk makes its wrapper window then. Before it, `wm frame` is the root's own window, so `wrapper_hwnd` returns `Failure("no wrapper yet")` (`win_focus.py:60-72`) rather than hand back the inner window (RV6 I1). That `wm frame` is `GetParent(winfo_id)` after the first idle pass is a measurement in spec 5.7.
- If a focus call fails, `_first_show` prints `focus call failed:`, shows the window with Tk's plain `deiconify` and adds the warning line `CLICK_WARNING`, "clicking here takes focus from the game" (`__main__.py:29`, :120-131).
- Every `ctypes` use is inside a function (Linux and cloud runs import this package): `tests/route_view/test_win_focus.py:133` (`test_12_ctypes_is_used_only_inside_functions`, with a planted sample at :141 to show the check can fail).

## Rules enforced by tests

- Two import-linter contracts in `pyproject.toml`: "route_view boundaries" (:77-85) forbids `src.gui`, `src.main`, `src.vision`, `src.rune_solver`, every `src.input` module but `protocol`, and the route modules that pull those in (`walker`, `walk`, `moves`, `runtime`, `sim`); "route_view is a leaf" (:87-93) means nothing in `route`, `controller`, `gui`, `main`, `vision`, `input`, `classes` or `rune_solver` imports `src.route_view`. What the package may import is spec 5.8. `tests/route_view/test_route_view_imports.py` pins the module lists in both contracts (:30), that every `src.input` module but `protocol` is forbidden (:41) and that importing the model pulls in no Tk, `input_layer`, walker or GUI (:50).
- The viewer is built not to take focus (the design intent; the real-window check, H-1, has not run yet) and never rebuilds Tk's wrapper window (see the first trap); the test pins that it calls none of the banned names: `tests/route_view/test_view_window.py:538` (`test_10_the_viewer_never_takes_focus_or_rebuilds_the_wrapper_window`; the banned names are `_FOCUS_ATTRS`, :507, and `_WRAPPER_CALLS`, :508; a planted sample at :545).

## Traps

- While the view is live, never call `focus_force`, `focus_set`, `lift`, `tkraise` or `grab_set`, and never open a dialog.
- Never call `wm resizable`, `overrideredirect`, `transient` or `attributes`, in any form. After the idle pass, Tk rebuilds the wrapper window on these, which silently drops the no-activate style, and a later click on the view then takes focus from the game. The Tk source line cited by the review is `win/tkWinWm.c:5073-5076` (for `resizable`, Tk 8.6.15); it was not re-read for this note. The test above enforces the names.
- A folder with `legs.jsonl` and no `steps.jsonl` opens as a replay (`__main__.py:73`), so a walk never starts a viewer on it: `walk.py` starts it only when it has a step log (`walk.py:423`, `run_log is not None`).
- `--replay` on a folder with no `end` shows it as a replay; never with `--ready`.
- Never open a replay while a walk runs. A replay window takes focus when clicked, and the board's keys go to whichever window has focus (root `CLAUDE.md`, Safety rules).
- The planned why line's route is `preview.path`; the panel's Route line is the hops' route (`model.py:14-15`). They can differ where the hops leave the path at an assumed landing (nine pairs on the FF2 map, none in the simulator runs).
- The why-line time is the elapsed time since the run's first event (`model.py:420-421`) as `mm:ss.mmm` (`h:mm:ss.mmm` past an hour; `RunModel._stamp`, `model.py:404-409`). It is open with Hong (RV3-D6, built as option A). The header's `wall_ns` and `perf_ns` (`step_log.py:83-84`) are recorded and not read by anything yet (RV4-D2 A).
- The scale buttons are in the replay bar, so they are hidden while the view is live; open with Hong (RV80-ADJ-D1).

## Tests and commands

`python -m pytest -q tests/route_view`. Test files: `test_route_view_imports`, `test_view_cli`, `test_view_follow`, `test_view_model`, `test_view_window` and `test_win_focus`; flat helpers `view_runs.py` (builds run folders) and `view_tk.py` (a real, withdrawn Tk root that never runs a mainloop). A replay of a finished run: `python -m src.route_view --follow <folder> [--replay]`.

The no-key focus check on a real window is the N1 tool (`src/route/harness/view_check.py`; its commands, its own-console rule and its exit codes are in `src/route/CLAUDE.md`). N1: pending (H-1 has not run). The live-walk checks L1-L4 (H-2 to H-4) are pending too.
