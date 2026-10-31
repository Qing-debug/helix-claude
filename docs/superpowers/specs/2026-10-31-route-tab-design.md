# Route tab: run the bot from the app and watch it (design spec)

*Draft, 2026-10-31 (unit RTS95). Replaces the separate route-view program of
`docs/superpowers/specs/2026-10-27-route-view-design.md` (the "route-view spec") with two tabs in the main app.
Branch `p1-route-tab`, cut from `p1-route-view` at 61455a7. Every `file:line` is at 61455a7 unless marked otherwise.
Inputs: the investigation `notes/RVT92.md` sections 1-5, Hong's answers to question sets QB114-QB117 and RVT93
(`docs/decisions.md` H55-H63), and `notes/RVT94.md` (clicking the app during a run; workspace
`.superpowers/orchestrator/2026-10-21-phase1/`). One choice is open with Hong: section 9.*

## 1. Summary

Today a walk starts from the command line (`python -m src.route.walk --supervised`) and `--view` opens the route view
as a second program that must not take focus. Hong reshaped this: the route view becomes a tab in the main app, and he
starts and stops the bot from there. The app gets two tabs:

- **Setup**: today's main window, unchanged (minimap, character picker, Load Map, Open Map Editor, console).
- **Route**: the run view (minimap at 4x with the rotation, the planned route, the move on each hop and the "why"
  log), the run settings, Start and Stop, the countdown, and a list of saved runs to replay.

Pressing Start checks that everything is ready, connects the board, counts down (default 5 s, adjustable) while Hong
clicks the game, then walks. F12 or the Stop button ends the run and releases every key. Every run is saved with its
minimap pictures and its step log, so it can be replayed later with the reasons for each move.

The walk logic moves out of `walk.main` into a reusable run object (`src/route/session.py`) that the tab drives from
a worker thread; `python -m src.route.walk` stays as a thin command line over the same object. The separate-program
machinery (spawn, claim/ready files, the no-activate window style, the N1 click-check tool) is removed.

Separately (Hong's answer RVT93-5, H60), the three key-test tools (`probe`, `soak`, `session_starts`) stop reading the
list of running programs and ask "Is MapleStory closed? y/n" instead. Section 6 gives the new behaviour, the safety
text and the test changes.

## 2. What Hong decided (binding)

| # | Decision | His words (verbatim) | Entry |
|---|---|---|---|
| 1 | Start and Stop live in the main app; the tab shows the run live | "Yes, start from the app (Recommended)" (QB114) | H55 |
| 2 | The tab shows live runs and replays saved runs | "Live and replay (Recommended)" (QB115) | H55 |
| 3 | No automatic check of which window is in front; F12 or Stop only | "C: no automatic check" (QB116) | H55 |
| 4 | Why-log lines show the time of day | "show time of day, not since run starteed" (QB117) | H55 |
| 5 | Anti-cheat: nothing may interact with MapleStory's memory or ask Windows about its window or process | "Remember, we can't interact with memory/windows API else the anticheat flags it" | standing fact, H56 |
| 6 | The three old route-view decisions are replaced by the tab; the save/replay + reasoning-log principle of the first is kept | RVT93-1, option A, with his note (quoted in H57) | H57 |
| 7 | Second-monitor screen capture stays as it is | RVT93-3 A | H58 |
| 8 | The F12 keyboard hook stays as it is | RVT93-4 A | H59 |
| 9 | Key-test tools ask "Is MapleStory closed? y/n" instead of reading the program list | RVT93-5 B | H60 |
| 10 | After Start, a countdown in the tab, default 5 s, adjustable | RVT93-6 A | H61 |
| 11 | Every run records its minimap pictures | RVT93-7 B, "Always record" | H62 |
| 12 | Two tabs, "Setup" (today's screen) and "Route" (run view, settings, Start and Stop) | RVT93-8 A | H63 |
| - | Clicking the app during a run | open (RVT93-2 asked back; re-asked as RVT94-2) | section 9 |

Still in force from the route-view spec: H51 (4x default, 2x/3x/4x buttons), H52 (every run saves `minimap.png` at its
start), H53 (observer argument, route preview, `src/route_view` package), H54 (window-only tools may run with
MapleStory open). Retired: H48's separate program and `--view`, H49 (no-activate window), H50 (`walk --view`).

Orchestrator ruling (RVT94, 2026-10-31; may change after Hong answers RVT94-2): **during a run the app opens no new
window and changes no window style or icon.** Reason: Tk itself calls `GetForegroundWindow` when a top-level window is
first shown or its style or icon changes (`win/tkWinWm.c:2017-2018` in `UpdateWrapper`, Tk 8.6.15; `notes/RVT94.md`
section 3). Section 5.12 turns this into rules.

## 3. What the code does today (at 61455a7)

- The walk is one CLI function, `walk.main` (`src/route/walk.py:311-508`): it validates arguments, builds its own
  `MapleAgent` and `RouteGraph` from the CSV (`_load_route`, :220-234), starts its **own** capture thread (:363), waits
  for the first frame and the character, connects `BleLink` (up to 30 s), builds the walker with the step log as
  observer (:390), starts `StopControl` (F12 hook, SIGINT, :391-397), writes the run files (`_write_run_files`,
  :268-300; `session.json` :410-422), optionally spawns the viewer (:423-428), prints "Focus the game window.
  Starting in 5 s. F12 stops." and sleeps (:429-430), optionally records frames at 10 fps (`_RECORD_FPS`, :87;
  :432-436), runs `walker.run()` on the main thread (:446), and cleans up in one `finally` (:473-507).
- `StopControl.start` installs SIGINT with `signal.signal`, which fails off the main thread
  (`src/route/runtime.py:64-79`; `ValueError` confirmed in RVT92). `trigger(source)` only sets an event and is safe
  from any thread (:81-89).
- The viewer (`src/route_view/window.py`) is plain Tk packed into a `tk.Tk` root; only `title()` (:405) needs a
  top-level (RVT92 section 1). `LiveController` (`follow.py:93-227`) polls the run folder every 100 ms.
- The main app (`src/main.py`) runs the capture thread on `SharedInfo` and one `tk.Tk` window, `MapleGUI`
  (`src/gui/gui.py`), 1000x1000 (:144), one scrollable column whose `bind_all('<MouseWheel>')` (:85) catches every
  wheel event.
- Import contracts (`pyproject.toml`): "route_view is a leaf" (:87-93) forbids `src.gui` and `src.main` from importing
  `src.route_view`; "route_view boundaries" (:77-85) keeps `src.route_view` off input, walker, walk, runtime and sim.
  No contract stops `src.gui` importing `src.route` or `src.input`; only docs say so.
- The key-test tools' MapleStory check: `soak._process_names` runs `tasklist` (`src/input/harness/soak.py:507-516`),
  `soak.maple_check` looks for "maple" in the names (:519-527), `_check_processes` in soak (:554-558), probe (:1002) and
  session_starts (:154) abort on it; soak re-checks every 60 s (`PROCESS_CHECK_PERIOD_NS`, :504; :599-606), probe too
  (:1017-1023), and session_starts before each of its 20 starts (:171-189).

## 4. Goals and non-goals

Goals: Start/Stop and the route view inside the app (H55, H63); live and replay with the reasons for each move (H55,
H57); countdown (H61); every run recorded (H62); the walk's behaviour, exit codes and cleanup order unchanged; no new
call that asks Windows about another window or process (H56).

Non-goals: any change to the planner, walker or moves; detecting which window is in front (H55, QB116 C); pausing and
resuming a run; a settings file; deleting old runs automatically; the runes feature.

## 5. Design

### 5.1 The Setup tab

Today's `MapleGUI` content, moved as is into the first tab of a `ttk.Notebook` that fills the window. Nothing on it
changes look or behaviour outside a run. During a run (from Start until the run's cleanup has finished) these are
disabled: Load Map, the character picker, Open Map Editor, and the dev buttons (`gui.py:198-215`), because they change
the graph or open a window (section 5.12). The draw-node, clear-console and clear-drawn-nodes buttons stay usable.

### 5.2 The Route tab, as Hong sees it

```
+--------------------------------------------------------------------------------------------------+
| [ Setup ] [ Route ]                                                                              |
+--------------------------------------------------------------------------------------------------+
| Map: fossil_foundry_2.csv   Class: Kanna                                                         |
| Laps [    ] (empty = until stopped)   Minutes [    ] (empty = no limit)   Countdown [ 5 ] s      |
| [ Start ]  [ Stop ]                                    Recording: on (about 0.5 GB per hour)      |
| Status: Ready. Press Start, then click the game.                                                  |
+------------------------------------------------------+-------------------------------------------+
|                                                      | Lap 2/5  leg 4/9  target 7                |
|   minimap at 4x: rotation nodes numbered,            | Move: teleport up (Alt+Up)                |
|   next target highlighted, planned route,            | Route: 3 > 7 > 8                          |
|   character, move on each hop                        |-------------------------------------------|
|                                                      | why log (time of day)                     |
|                                                      | 14:02:31.204 at (52,40): teleport up ...  |
+------------------------------------------------------+-------------------------------------------+
| Saved runs: [ 2026-10-31 14:02 fossil_foundry_2  5 laps  312 MB  v ]  [ Open ] [ Refresh ]        |
| replay bar (when a saved run is open): |< < > >|  slider  2x 3x 4x                                |
+--------------------------------------------------------------------------------------------------+
```

The view area and its panel are `RouteViewWindow`'s widgets (section 5.9), unchanged in look except the time column
(section 5.10). The window grows to 1120x1000 so the 4x view (648x328 canvas plus the 450 px panel, `window.py:51`)
fits beside nothing else; the Setup tab keeps its own scrollbar.

States of the top area:

| State | Status line text | Enabled |
|---|---|---|
| Not ready | one line naming what is missing, e.g. "Load a map on the Setup tab first." (see 5.4) | settings, saved runs |
| Ready | "Ready. Press Start, then click the game." | settings, Start, saved runs |
| Connecting | "Connecting to the board..." | Stop |
| Countdown | "Click the game now. Starting in 5 ... 4 ... 3 ... 2 ... 1. F12 stops." | Stop |
| Running | "Running. F12 or Stop ends the run." plus the line section 9 settles | Stop |
| Stopping | "Stopping: releasing keys..." | none |
| Ended | "Ended: <reason in words> (<run folder name>). Replay is open." | settings, Start, saved runs |

At the end of a run the view switches to replay of that run (as the old viewer did at `end`, `follow.py:207-221`
minus the focus calls). Error texts per exit code are in 5.5.

### 5.3 Run settings

| Setting | Field | Default | Valid | Maps to |
|---|---|---|---|---|
| Laps | entry | empty (until stopped) | whole number >= 1 | `--laps` |
| Minutes | entry | empty (no limit) | number > 0, at most `threading.TIMEOUT_MAX / 60` (as `walk.py:325-329`) | `--minutes` |
| Countdown | spinbox | 5 | whole seconds 1-60 | H61 |
| Seed | not shown | drawn from the OS per run, written to `session.json` | - | `--seed` |
| Recording | not a setting | always on (H62); the line "Recording: on" is information only | - | frames at 10 fps |

An invalid field turns Start off and the status line names the field ("Laps must be a whole number of 1 or more").
Settings live for the app session only; there is no settings file. Map and class come from the Setup tab (the loaded
CSV path `controller.agent.CSVParser.CSV_file` and `controller.agent.characterClass`; `src/gui/CLAUDE.md` notes the GUI
already reaches past the facade).

### 5.4 Start: checks, then the worker

On the Tk thread, Start runs these checks in order and stops at the first that fails, writing its text to the status
line (never a message box, 5.12):

1. A map is loaded and a class is chosen ("Load a map on the Setup tab first." / "Choose a character on the Setup
   tab first.").
2. The map editor is closed ("Close the map editor before Start."). This also covers unsaved editor changes and the
   editor's loop that holds the minimap lock while it scales (`src/gui/map_editor.py:1069-1087` (tree), RVT92 2e).
3. The minimap has been found (`MapleGUI._minimapReady`, `gui.py:232`).
4. The character is found on the current frame (`MapleGUI._findCharacter`, `gui.py:240`).
5. At least 2 GB free on the drive that holds `data/route` (`shutil.disk_usage`), because every run records about
   0.5 GB per hour (H62): "Less than 2 GB free on C:. Delete old runs in data/route, then press Start."
6. A new run folder `data/route/<UTC stamp>` that does not exist yet (`runtime.check_out`, as `walk._check_out`).

Then the tab disables the controls (5.1, 5.2), shows "Connecting to the board..." and starts the run worker thread
(5.6), which does the rest: build the route graph from the CSV file on disk (as `walk._load_route` does, so the run
folder's `map.csv` and its sha256 match what was walked), connect, start the stop control, write the run files, count
down, record, walk, clean up. The graph is rebuilt from the file, not taken from `controller.agent.pathGraph`, so the
run and its saved `map.csv` cannot differ.

### 5.5 Stop, the end of a run, and its words

Ways a run ends, each a `StopControl.trigger(source)` except the natural end:

| How | Source in `stop.json` | Status line |
|---|---|---|
| F12 (game in front) | `hotkey` (as today, `runtime.py:81`) | "Ended: you pressed F12." |
| Stop button | `button` (new) | "Ended: you pressed Stop." |
| Minutes reached | as `walk` today (the timer) | "Ended: the time limit was reached." |
| Laps done | none (natural end, exit 0) | "Ended: all laps done." |
| Character lost (B19-D1) | `off_graph` / `no_position` | "Ended: the character was lost (exit 10)." |
| App window closed | `window_close` (new) | none (the app closes after the cleanup) |

Other exit codes (same numbers as `walk.py:6-9`): 1 "Ended: the board link was lost.", 3 "Could not start: no minimap
frame or character found.", 4 "Could not start: the board did not connect.", 5 "Could not start: the map, route or
character icon did not load.", 6 and 7 "Ended: the walker failed; see <run folder>.", 9 "Could not start: the F12
stop key could not be installed." A run that never reached the walk still leaves its folder (as `walk` does).

The cleanup is `walk`'s, in its order (timer, release_all, recorder, `stop.json`, step log `end`, leg log,
`link.close`, stop control close; `walk.py:473-507`), now inside `RunSession` (5.6) so the CLI and the tab share it.

Closing the app mid-run: the close handler (`WM_DELETE_WINDOW`) calls `session.stop("window_close")`, shows
"Stopping: releasing keys..." and polls the worker with `after` until it has finished its cleanup, then destroys the
window. If the cleanup has not finished after 10 s it destroys anyway and prints one line to the console; every run
thread is a daemon and dies with the process. `release_all` is the second cleanup step, so keys are released first.
Whether the board releases held keys when the BLE link drops was not checked (RVT92 [fyi]); the plan adds no test for
it.

### 5.6 The run driver: `src/route/session.py`

`walk.main`'s body moves into one object; `walk.py` keeps its argument parsing and becomes a thin CLI over it, with the
same exit codes, files and cleanup order (the existing walk tests are the oracle).

```python
@dataclass(frozen=True)
class RunSettings:
    map_csv: Path
    character: type[CharacterClass]
    laps: int | None
    minutes: float | None
    countdown_s: float          # H61; the CLI passes 5.0
    seed: int | None            # None: drawn from the OS, written to session.json
    out: Path

@dataclass(frozen=True)
class RunEvent:                 # posted by the worker; the tab drains a queue.SimpleQueue with `after`
    kind: Literal["status", "countdown", "running", "warning", "ended"]
    text: str
    seconds_left: int | None = None
    code: int | None = None     # set on "ended": the walk exit code

class RunSession:
    def __init__(self, settings: RunSettings, shared: SharedFrame, *,
                 on_event: Callable[[RunEvent], None],
                 make_link: Callable[[], Link] = ..., make_stop: Callable[..., StopControl] = ...,
                 clock: Clock = ..., sleep: Callable[[float], None] = ..., sigint: bool = False) -> None: ...
    def run(self) -> int        # blocking: everything from loading the route to the cleanup; returns the exit code
    def stop(self, source: str) -> None   # any thread; = stop_control.trigger(source), or a pre-walk cancel flag
```

- `shared` is the app's `SharedInfo` (it has the two attributes `runtime.SharedFrame` names, `runtime.py:245-250`).
  `RunSession` never starts a capture thread; the CLI starts one (`runtime.start_capture`) and passes its frame
  holder in. There is never a second capture of the same monitor (H58 unchanged).
- `on_event` is called on the worker thread; the tab passes `queue.put`. The worker never touches Tk.
- `stop()` before the walker exists (during connect or the countdown) sets a cancel flag that `run()` checks between
  its steps and in the countdown; the run then cleans up and ends with exit 0 and the source in `stop.json`.
- The countdown is `countdown_s` one-second steps, each posting a `countdown` event and checking the cancel flag.
- Recording is always on (H62): `run()` starts the frame recorder (`_record_frames`, `walk.py:303-307`) after the
  countdown, as `--record` does today. A recorder failure posts a `warning` event and the walk goes on.
- `StopControl.start(sigint: bool = True)` gains the flag; `RunSession` passes `sigint` through. The CLI runs on the
  main thread and passes `True` (Ctrl+C as today); the tab passes `False`, since there is no console and
  `signal.signal` fails off the main thread.
- Layers: `session` sits above `walker` in contract f (it builds the walker); it imports `src.input` (link, layer,
  timing) as `walk` does; it imports neither `src.gui` (contract d) nor `src.vision` (contract e).

The walk CLI after the change: `python -m src.route.walk --map ... --supervised [--class kanna] [--laps N]
[--minutes M] [--seed S] [--out DIR]`. `--view` is removed (H57 retires H50; argparse rejects it with exit 2). Every
CLI walk records (H62); `--record` stays accepted and does nothing, so older commands in the plans still run (see
`DECISION NEEDED` RTS95-D3 in the notes: made provisionally).

### 5.7 Threads

| Thread | Does | Touches Tk |
|---|---|---|
| Tk main | mainloop, both tabs, drains the run event queue every 100 ms, `LiveController` poll | yes |
| capture (existing) | `capture_and_process_minimap` on `SharedInfo` | no |
| run worker (new, daemon) | `RunSession.run()` | no |
| `BleLink-loop`, `stop-hook`, `stop-control`, `step-log`, `record`, `walk-timer`, `route-stop-guard` | as in the walk today | no |

While a run is live the Setup tab's `_updateCanvas` keeps its 10 ms loop but skips building the picture when the Setup
tab is not shown, so the walker shares the interpreter with one redraw (the Route tab's) instead of two. The cost of
running the walker inside the app is not measured (RVT92 [review], route-view spec 5.6): the plan adds a measurement
step that compares the `sent` events' `reaction_ms` and `waited_ms` of an in-app simulator run with a CLI simulator run.

The mousewheel handler (`gui.py:81-85`) acts only while the Setup tab is shown, so wheel events over the Route tab's
canvas and slider are not taken by the Setup tab's scroll canvas.

### 5.8 Live feed

Steps: the tab follows the run folder as built (`Follower` and `LiveController`, `follow.py:58-227`), so live and replay
read the same files with one tested code path. The picture behind the live view comes from `SharedInfo.minimap_frame`
(copied under the lock, released, then scaled, as `gui.py:442-445` does), not from the recorded frames, so it has no
file lag. The step lines lag the walk by up to about 0.1-0.3 s (the poll), as before.

### 5.9 Where the code lives, and the import contracts

| Piece | Where | Change |
|---|---|---|
| Run driver | `src/route/session.py` (new) | from `walk.main` (5.6) |
| Walk CLI | `src/route/walk.py` | thin over `RunSession`; `--view`, `_start_viewer`, `_wait_for_viewer`, `_ready_within`, `_VIEW_*` removed |
| Stop control | `src/route/runtime.py` | `start(sigint=True)`; nothing else |
| View model, follower | `src/route_view/model.py`, `follow.py` | as is, except `LiveController` loses the hide/show/style code in `_end` (`follow.py:207-221`) and the `Focus` protocol (:42-47); its texts lose `--replay` (:38-39) |
| View widgets | `src/route_view/window.py` | `RouteViewWindow(parent: tk.Misc, ...)` packs into a given Frame; `title()` (:405) becomes a label |
| Route tab | `src/gui/route_tab.py` (new; camelCase methods, as `src/gui`) | settings, Start/Stop, countdown text, saved-run list, event queue, hosts `RouteViewWindow` |
| Notebook | `src/gui/gui.py` | `ttk.Notebook` with Setup and Route; close handler (5.5) |
| Standalone replay | `src/route_view/__main__.py` | `python -m src.route_view --follow <folder>` always opens a replay; `--ready` and the live path removed |

Contracts (`pyproject.toml`):
- "route_view is a leaf" (:87-93): `src.gui` is taken off its list of importers, so `src.gui.route_tab` may import
  `src.route_view`. `src.main` stays forbidden.
- "route_view boundaries" (:77-85): unchanged, and `src.route.session` is added to its forbidden list, so the view stays
  free of the run driver.
- New forbidden contract "gui reaches input only through the run driver": `src.gui` may not import `src.input`, with
  `allow_indirect_imports = true` so `src.gui -> src.route.session -> src.input` passes (import-linter's
  `ForbiddenContract` has `allow_indirect_imports`, default False:
  `.venv/Lib/site-packages/importlinter/contracts/forbidden.py:56`, `:75`, `:135`).

### 5.10 Why-log time of day (QB117)

Each why line starts with the local time of day, `HH:MM:SS.mmm`, computed as the header's `wall_ns` plus
`t_ns - perf_ns` (`step_log.py:99-100`). Runs whose header has no `wall_ns` (simulator runs, legs-only folders) keep
today's elapsed form (`model.py:404-409`). No format change to `steps.jsonl`.

### 5.11 Saved runs, recordings and replay

- Where: one folder per run, `data/route/<UTC stamp>/` under the repo root (gitignored), as the walk writes today:
  `session.json`, `stop.json`, `legs.jsonl`, `laps.jsonl`, `steps.jsonl`, `map.csv`, `minimap.png`, and the recorded
  frames in `frames/` (`frame_%06d.png` plus `index.jsonl`, 10 per second). This keeps H57's kept principle: every run
  can be replayed later with the log of why the bot chose each move.
- Size: about 0.5 GB per hour (the route-view spec's estimate, not measured). Start refuses with less than 2 GB free
  (5.4 step 5).
- Old runs: kept until Hong deletes them by hand, as the RVT93-7 option he picked said ("you delete old runs by
  hand"). Nothing deletes runs automatically. The saved-run list shows each run's size so the big ones are easy to find.
- Picking a run: the "Saved runs" list in the Route tab (no file dialog, 5.12) shows the folders under `data/route`
  that hold `steps.jsonl` or `legs.jsonl`, newest first, as "<local start time> <map file name> <laps done> <size>".
  "Refresh" re-reads the folder; "Open" loads the run with `load_folder` (`model.py:690`) into the view as a replay.
  The list, Open and Refresh are disabled while a run is live.
- Replay draws on the recorded frames when the run has them, else on `minimap.png` (H52), else on a dark canvas (as
  built, route-view spec 5.5).

### 5.12 Anti-cheat rules in the app (H56 and the RVT94 ruling)

1. Nothing in the app, the run driver, the walk or the view asks Windows which window is in front, enumerates windows
   or processes, or opens, reads or signals MapleStory's process or window. The only Windows facilities used while the
   game runs are monitor capture (H58) and the F12 low-level keyboard hook (H59), plus Tk's own handling of its own
   windows.
2. During a run (from Start until the cleanup has finished) the app opens no new window (no message box, file dialog,
   Toplevel or OpenCV window), and calls no `wm` command that changes a window's style or icon (`overrideredirect`,
   `resizable`, `attributes` with `-toolwindow`, `-topmost` or `-fullscreen`, `iconbitmap`, `iconphoto`). Errors and
   warnings go to the Route tab's status line. This is why the map editor and Load Map are disabled during a run, and
   why the Route tab never uses a dialog even outside a run (the game is usually open then too).
3. During a run the app never calls `focus_force`, `focus_set`, `lift`, `tkraise` or `grab_set` (they move or take
   focus; the existing `test_10` ban list in `tests/route_view/test_view_window.py:507` carries over to the tab).
4. A test enforces rule 1 by name (section 8): no module outside `src/input/harness/` may contain
   `GetForegroundWindow`, `EnumWindows`, `EnumProcesses`, `CreateToolhelp32Snapshot`, `OpenProcess`, `FindWindow`,
   `GetWindowText`, `GetClassName`, `GetWindowThreadProcessId`, `tasklist`, `Get-Process` or `psutil`. Today these
   appear only in `src/input/harness/` and `src/route/harness/view_check.py` (grep at 61455a7), and `view_check` is
   removed (5.13). `src/input/harness/` is exempt because its tools run only with MapleStory closed; after section 6,
   `tasklist` and `Get-Process` leave it too.

Known and accepted, for Hong's information (RVT94 concern 1): Tk makes one `GetForegroundWindow` call when the app's
main window is first shown, and again when the map editor opens. It compares the answer only with its own window. If
the app is opened after the game, that call happens while the game runs. Starting the app before the game avoids it.

### 5.13 What is removed

`src/route_view/win_focus.py` and `tests/route_view/test_win_focus.py`; the claim/ready/first-show code in
`src/route_view/__main__.py` and its tests in `test_view_cli.py`; the spawn and ready wait in `walk.py` and
`tests/route/test_walk_view*.py`; the N1 tool `src/route/harness/view_check.py` and `tests/route/test_view_check.py`
(and with it the root CLAUDE.md rule about `_maple_check`); the constants `VIEW_LOG`, `VIEW_CLAIM`, `VIEW_READY`
(`step_log.py:59-61`); the route-view spec's checks N1 and H-1. The ban on `wm resizable`/`overrideredirect`/
`transient`/`attributes` in the view's test stays as part of rule 5.12-2 (its reason changes from "keeps the
no-activate style" to "no style change during a run"), unless section 9's option C needs `attributes -disabled`.

## 6. Key-test tools: "Is MapleStory closed? y/n" (H60)

### 6.1 Behaviour

- Each of `soak`, `probe` and `session_starts` asks once, on its console, at the point where it makes its first
  MapleStory check today (before the listener starts and blocks any key, before the board port check or any reset,
  before connect, before any run directory is written where that is the case today):
  `Is MapleStory closed? y/n `
- It goes on only on the answer `y` (after stripping spaces; `Y` counts). Any other answer, an empty line, or no answer
  (standard input closed: `input()` raises `EOFError`, confirmed in the venv) stops the tool with its refusal exit code
  (`EXIT_REFUSED`, 2) and the line `refused: MapleStory not confirmed closed (answer: <answer or "none">)`, before the
  listener, the link or any key.
- It asks once per tool run, not again. The 60 s re-check in `soak` and `probe` and the per-start check in
  `session_starts` are removed: a question cannot be asked mid-run, because the console would take focus from the
  sink (a `FOCUS_LOST` abort) and the typed answer would be extra key events in `keys.jsonl` (a Delivery failure). Hong
  was told when he chose: "the 60-second re-check that catches the game being opened during a long soak run is lost;
  you would be the safety net" (RVT93-5 option B cost). See `DECISION NEEDED` RTS95-D1 in the notes for the check
  that this loosens nothing beyond what he chose.
- The tools never run `tasklist` or any other process listing again. `probe --run-sheet`'s `MAPLE_CHECK` line
  (`probe.py:896`, printed at :948 and :952), a `Get-Process` command for the operator, becomes
  "Check that MapleStory is closed (no game window, no game on the taskbar)."
- `session.json` keeps its `process_check` key (`soak.py:197`); its value is now the answer text, e.g.
  `"answered y: MapleStory closed"`.

What still guards a key-test run after the change:
1. The y/n question, once, before anything that could send a key; only `y` goes on.
2. The listener blocks the seven bot keys system-wide after logging them unless started with `--no-block`
   (`src/input/CLAUDE.md`, "Harness tools"), so the board's keys do not reach other windows. [inferred from the docs;
   the plan confirms each of the three tools starts it in blocking mode]
3. Before each command the tools check that the sink window is in front and pause, then abort with `FOCUS_LOST`, if
   it is not (`soak.check_focus`, `soak.py:298-324`; used by probe and session_starts). This reads the foreground
   window; it runs only when the answer said the game is closed.
4. H15's conditions for agents (work needs it, MapleStory closed, PC idle), with how an agent may answer the question
   still open (`DECISION NEEDED` RTS95-D2 in the notes).

### 6.2 Code shape (keeps the patched names)

- `soak._process_names` is replaced by `soak._read_answer() -> Result[str, str]` (calls `input(PROMPT)`; `EOFError` or
  `OSError` gives `Failure`). `soak.PROMPT = "Is MapleStory closed? y/n "`.
- `soak.maple_check() -> tuple[bool, str]` keeps its name and signature and reads `_read_answer` from soak's own
  globals: ok only for `y`.
- `_check_processes` keeps its name in all three modules (the name is now historical; renaming it would edit three
  modules and `test_guard_targets.py` for no behaviour gain). `PROCESS_CHECK_PERIOD_NS` and soak's `periodic_check`
  go; `wait_for_click` no longer gets a MapleStory tick.

### 6.3 Safety text

Root `CLAUDE.md`, "Safety rules": replace the second and third bullets (the harness tools, and the patched check names
including `view_check`) with:

> - The harness tools that send keys (`probe`, `soak`, `session_starts` in `src/input/harness/`) need the board, a real Bluetooth link and the "Helix input test sink" window. Never run them with MapleStory open. Before the first key each asks once on its console "Is MapleStory closed? y/n" and goes on only on `y`; any other answer, or no answer (input closed), stops it with exit 2 before the listener, the link or any key (decision H60). Nothing checks again during the run, so MapleStory must not be started until the tool has ended. The tools never list the running programs (Hong's anti-cheat standing fact, H56). Details are in `src/input/CLAUDE.md`.
> - That question (`soak.maple_check`, which reads the answer through `soak._read_answer`, and `_check_processes` in `soak`, `probe` and `session_starts`) is patched by name in the tests: never move or rename it (`src/input/CLAUDE.md`).

Replace the bullet "Never open a route-view replay ... H-1, has not run yet" with:

> - A walk starts only from the Route tab's Start button or `python -m src.route.walk --supervised`, and only with Hong's go: an agent never presses Start and never runs the walk against the board (tests drive `RunSession` with fake links). During a run the app opens no window and changes no window style (spec `docs/superpowers/specs/2026-10-31-route-tab-design.md` 5.12). Never open a standalone replay (`python -m src.route_view`) while a walk runs: a new window takes focus when clicked, and the board's keys go to whichever window has focus.
> - Hong's anti-cheat standing fact (H56): while MapleStory runs, nothing of ours may read or write its memory, open, query or enumerate its process or window, or ask Windows which window is in front. `tests/test_anti_cheat_names.py` fails if a banned call name appears outside `src/input/harness/`, whose tools run only with MapleStory closed.

In the H15 bullet, replace "MapleStory is not running (the tools' own check still applies and is never bypassed)" with
the text RTS95-D2 settles; the recommended text is "MapleStory is not running (the tools ask; an agent answers `y`
only when Hong has said in the current session that MapleStory is closed, and never otherwise)".

`src/input/CLAUDE.md`, "Traps", replace the "MapleStory guard" bullet with:

> - MapleStory question (H60). `soak.maple_check` (soak.py) reads the answer through `soak._read_answer`, which calls `input(soak.PROMPT)`; only `y` passes, and a closed input is a `Failure`. `probe._check_processes` and `session_starts._check_processes` call `soak.maple_check()` through the module; each tool asks once, before the listener, the link or any key, and never again. The tests patch `soak._read_answer` by name (test_probe, test_session_starts, test_soak, test_soak_v2_faults; `test_guard_targets.py` adds its own): if the function moves, those patches stop reaching the question and a test would block on real input. `tests/input/test_guard_targets.py` fails if a patched name stops being defined in its module. Never move or rename `maple_check`, `_read_answer` or `_check_processes` (the last name is historical: it no longer lists processes).

In its "Harness tools" paragraph replace "(the tools' own check still applies)" with "(the tools ask; see the root
CLAUDE.md H15 bullet for how an agent answers)". `firmware/CLAUDE.md:5`: replace "never bypass the tools' own
MapleStory check" with "never answer the tools' MapleStory question for Hong". `tests/CLAUDE.md:15`: "patches
`soak._process_names`" becomes "patches `soak._read_answer`".

`src/route/CLAUDE.md`: drop the N1 tool lines (:36, :49-55) with the tool; add `session.py` to the seams and the
`RunSession` traps (Tk never touched from the worker; `sigint=False` off the main thread). `src/gui/CLAUDE.md`: add the
Notebook, the Route tab, the close handler and rule 5.12. `src/route_view/CLAUDE.md`: rewrite for a hosted view (no
separate process, no claim/ready, no `win_focus`).

## 7. What must not change

The planner, walker, moves and input layer behaviour; `steps.jsonl` format V1; the run folder's file names; the walk's
exit codes and cleanup order; the simulator's output for a seed (sim smoke 6/6 PASS, same output); the F12 hook
(H59); monitor capture (H58); the Setup tab's look and behaviour outside a run.

## 8. Tests

New:
- `tests/route/test_session.py`: `RunSession` with the simulator's `SimLink`, a fake stop control and a fake clock
  and sleep: the events in order (status, countdown 5..1, running, ended); `stop("button")` during connect, during the
  countdown and while walking, each ending with `release_all` first and `stop.json` naming the source; window-close
  source; exit codes 0/1/4/5/9/10 as `walk`'s tests have them; recording always on (frames written); no Tk import.
- `tests/route/test_walk_cli.py`: unchanged expectations for exit codes and files; `--view` rejected with exit 2;
  `--record` accepted.
- `tests/route/test_walk_cli.py` (where `StopControl` is tested today): `start(sigint=False)` off the main thread succeeds
  and installs no handler.
- `tests/controller/test_route_tab.py` (beside `test_gui_startup.py`, whose real withdrawn Tk root it reuses): Start's six refusals and their
  texts; controls disabled during a run and back after; the countdown text; events drained into the status line;
  the saved-run list (order, size, disabled while live); the close handler calls `stop("window_close")` and destroys
  only after the worker ends or 10 s; no message box, Toplevel, `wm` style or icon call and no focus call during a
  run (patch `tkinter.messagebox`, `tk.Toplevel` and the `wm`/focus methods and assert none was called).
- `tests/test_anti_cheat_names.py`: the name scan of 5.12-4, with a planted sample to show it can fail.
- `tests/route_view/test_view_model.py`: time-of-day why lines from `wall_ns`/`perf_ns`; elapsed when absent.
- Import contracts: the "gui reaches input only through the run driver" contract and the changed leaf contract are
  checked by `lint-imports` in `tools/check.py`; `tests/route_view/test_route_view_imports.py` updated to match.

Changed for the y/n question (section 6):
- Every `monkeypatch.setattr(soak, "_process_names", ...)` (test_probe 4, test_session_starts 3, test_soak 5,
  test_soak_v2_faults 1) becomes `monkeypatch.setattr(soak, "_read_answer", lambda: Success("y"))` or the case's answer.
- "MapleStory running" tests (test_soak:610, test_probe:302, test_session_starts:303) become "answer n refuses" tests
  and keep their "before connect / before the listener / before any dir" assertions; "process list failure" (test_soak:620)
  becomes "no answer (EOF) refuses".
- The re-check tests are deleted: test_soak:626 (60 s check), test_soak:756 (during the start wait), test_probe:442
  (mid-probe), test_session_starts:312 (before a start). New tests in their place: each tool calls `_read_answer`
  exactly once per run, and a full fake run sends its keys with no further call.
- test_soak:1033 (`_process_names` lists this process) is deleted; new: `_read_answer` returns `Success("y")` for input
  "y", `Failure` for a closed input (patch `builtins.input`).
- `process_check` value assertions (test_soak:258, :264, :585, :617, :769; test_probe:346, :464) take the new texts.
- `tests/input/test_guard_targets.py`: `PATCHED[SOAK]` lists `_read_answer` instead of `_process_names`;
  `test_maple_check_lives_in_soak_and_reads_process_names_from_its_own_globals` checks `_read_answer`; the three
  `maple_check` tests and the parametrised reach test use answers; new: `soak.py`, `probe.py` and `session_starts.py`
  contain no `tasklist` and no `subprocess` call that lists processes.
- `probe --run-sheet` text tests, if any pin `MAPLE_CHECK`, take the new line.

Removed: `tests/route_view/test_win_focus.py`, `tests/route/test_view_check.py`, the claim/ready parts of
`test_view_cli.py`, the spawn parts of `test_walk_view*.py`.

Every test drives fakes; no test connects a board, sends a key or presses Start.

## 9. Open: clicking the app during a run (RVT93-2, re-asked as RVT94-2)

Not decided here. Hong asked back how the recommended option would detect the click; the answer (`notes/RVT94.md`
section 1) is that it detected nothing: the warning line is shown for the whole run. RVT94 offers three options, none
of which asks Windows which window is in front. For each, what changes in this spec:

**A. Warning only.** The Running status line adds, for the whole run: "Clicking this window sends the bot's keys here,
not to the game. Click the game again or press F12." Changes: none beyond that text (5.2). The walk does not end by
itself when keys go astray (a leg that sent moves and timed out does not end the run, `walk.py:16-21`).

**B. Stop the run when our own window gets the keyboard (RVT94's recommendation).** Tk turns the `WM_SETFOCUS`
message Windows sends to our own window into a `<FocusIn>` event (`win/tkWinX.c:1053-1100`); the tab binds it. Changes:
- 5.5 gains a source `app_focus`: "Ended: you clicked the app. Press Start to run again."
- 5.6: `RunSession` gains nothing; the tab calls `session.stop("app_focus")` from the `<FocusIn>` handler while a run
  is in its countdown or walking.
- 5.4/5.6 countdown: if the app still has the keyboard when the countdown ends (Tk's own `focus_get()`, which reads Tk
  state, `tkFocus.c:696-708`, no Windows call), the run does not start: "Click the game first." (`stop("countdown_focus")`,
  exit 0, nothing walked).
- 5.12 rule 1 notes this one exception: reacting to Tk's focus event on our own window.
- Section 8: tab tests for the binding (generate `<FocusIn>` on the withdrawn root; stop called once) and the countdown
  refusal; a manual check by Hong with no game and no key (click between two ordinary windows).

**C. Lock the app window for the run; F12 only.** At the end of the countdown the tab calls
`wm attributes -disabled 1` on the main window (one `EnableWindow` call on our own window, `tkWinWm.c:3232-3242`,
which does not reach `UpdateWrapper`) and `-disabled 0` in the cleanup. Changes:
- 5.2: Stop is not clickable while walking (still during connect and the countdown); the Running line says
  "The app is locked during the run. F12 stops."
- 5.5: the Stop button source applies only before the walk; the close handler cannot run while locked (the window
  takes no clicks); F12 is the only stop while walking.
- 5.12 rule 2 allows exactly `attributes -disabled` on the main window, and 5.13 keeps the `attributes` ban otherwise.
- Section 8: tab tests that the lock is set after the countdown and always cleared in the cleanup (also after a
  worker exception); a manual check by Hong at the PC (click the locked window, Alt-Tab, the taskbar button) before the
  first real run. Untested on this PC (RVT94 concern 2).

Whatever he picks, 5.1's greying out of Load Map, the class picker, the map editor and the dev buttons stays (they
change the graph or open a window).

## 10. Other points still open with Hong (not reopened here)

- RV80-ADJ-D1: the scale buttons sit in the replay bar, so they are hidden while live (as built,
  `src/route_view/CLAUDE.md`). Kept as built until he answers.
- His question on RVT93-4 ("Is this a risky? What's the likelihood this gets flagged by the anticheat?") goes to the
  Q&A page; the hook stays as H59 records.
- The B21 walk test and Feature C stay paused (H47).

## 11. Notes for the plan

Rough order (RVT92 section 5, adjusted): (1) `session.py` from `walk.main`, walk tests as the oracle; (2)
`StopControl.start(sigint=...)`; (3) y/n question in the key-test tools and its tests (independent; can go first);
(4) `RouteViewWindow`/`LiveController` on a parent Frame, focus/claim code removed; (5) Notebook and Setup tab;
(6) Route tab: settings, Start checks, worker, event queue, close handler; (7) live background from `SharedInfo`, time
of day; (8) saved-run list; (9) contracts, anti-cheat name test, removals; (10) docs and safety text (section 6.3);
(11) the in-app timing measurement (5.7); then section 9's choice once Hong answers. Hong then checks an in-app 1-lap
walk from Start (F12 and Stop) and a replay; agents never press Start.
