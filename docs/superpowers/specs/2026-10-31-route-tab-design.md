# Route tab: run the bot from the app and watch it (design spec)

*Draft, 2026-10-31 (unit RTS95). Replaces the separate route-view program of
`docs/superpowers/specs/2026-10-27-route-view-design.md` (the "route-view spec") with two tabs in the main app.
Branch `p1-route-tab`, cut from `p1-route-view` at 61455a7. Every `file:line` is at 61455a7 unless marked otherwise.
Inputs: the investigation `notes/RVT92.md` sections 1-5, Hong's answers to question sets QB114-QB117 and RVT93
(`docs/decisions.md` H55-H63), and `notes/RVT94.md` (clicking the app during a run; workspace
`.superpowers/orchestrator/2026-10-21-phase1/`), and Hong's answers to RVT94-RVT96 (H64-H67): the app stops the run
when it gets the keyboard (section 9), the no-new-window rule stays (5.12), an agent answers the key-test tools'
question only as H66 says (6.3), and the capture thread's "Minimap" window is removed (5.12). Every choice in this
spec is settled.*

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
| 3 | No automatic check of which window is in front; F12, Stop, or (row 13) the app getting the keyboard | "C: no automatic check" (QB116) | H55, amended by H64 |
| 4 | Why-log lines show the time of day | "show time of day, not since run starteed" (QB117) | H55 |
| 5 | Anti-cheat: nothing may interact with MapleStory's memory or ask Windows about its window or process | "Remember, we can't interact with memory/windows API else the anticheat flags it" | standing fact, H56 |
| 6 | The three old route-view decisions are replaced by the tab; the save/replay + reasoning-log principle of the first is kept | RVT93-1, option A, with his note (quoted in H57) | H57 |
| 7 | Second-monitor screen capture stays as it is | RVT93-3 A | H58 |
| 8 | The F12 keyboard hook stays as it is | RVT93-4 A | H59 |
| 9 | Key-test tools ask "Is MapleStory closed? y/n" instead of reading the program list | RVT93-5 B | H60 |
| 10 | After Start, a countdown in the tab, default 5 s, adjustable | RVT93-6 A | H61 |
| 11 | Every run records its minimap pictures | RVT93-7 B, "Always record" | H62 |
| 12 | Two tabs, "Setup" (today's screen) and "Route" (run view, settings, Start and Stop) | RVT93-8 A | H63 |
| 13 | If our app gets the keyboard during a run (a click, Alt-Tab), the run stops at once; it does not begin if the app still has the keyboard when the countdown ends | "Stop when our app gets the keyboard (Recommended)" (RVT94-1, option B; RVT93-2 re-asked) | H64 |
| 14 | Tk's own foreground query is accepted outside a run (the app triggers none today); the rule "no new window, style or icon change during a run" stays | "Fine as it is (Recommended)" (RVT94-2, option A) | H65 |
| 15 | An agent answers the key-test tools' "Is MapleStory closed?" with `y` only if Hong said in that same session that MapleStory is closed; otherwise it asks him first | "The agent answers 'y' only if you said in that same session that MapleStory is closed; otherwise it asks you first (Recommended)" (RVT95-1, option A) | H66 |
| 16 | The capture thread's separate "Minimap" window is removed; the capture itself is unchanged | "Remove it (Recommended)" (RVT96-1, option A) | H67 |

Still in force from the route-view spec: H51 (4x default, 2x/3x/4x buttons), H52 (every run saves `minimap.png` at its
start), H53 (observer argument, route preview, `src/route_view` package), H54 (window-only tools may run with
MapleStory open). Retired: H48's separate program and `--view`, H49 (no-activate window), H50 (`walk --view`).

Rule kept by Hong's RVT94-2 pick (H65): **during a run the app opens no new window and changes no window style or
icon.** Reason, corrected after RVT94 (Tk 8.6.15, `win/tkWinWm.c`; `notes/q-20261031-162350-51bf.md`,
`notes/q-20261031-162958-7708.md`): Tk's only "which window is in front" call is `GetForegroundWindow` in
`UpdateWrapper` (:2017-2018, `(oldWrapper == NULL) || (oldWrapper != GetForegroundWindow())`). The `||` skips it while
a window has no wrapper yet, so a window's first show (:1909, :2388) and an icon set (:1059) make no query. It runs when
an already-shown window is restyled (`wm resizable` :5075, `overrideredirect` :4848, `attributes -toolwindow` :3089 and
:3246, full screen :2525) or made transient (:5617). `attributes -topmost` is not a trigger: it calls `SetWindowPos`
directly (:3219-3229). A new window during a run can also take the keyboard from the game. The rule bans the whole
family (new windows, style and icon changes) so the code need not tell the cases apart. Section 5.12 turns it into
rules.

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

Today's `MapleGUI` content, moved as is into the first tab of a `ttk.Notebook` that fills the window. Its widgets and
their behaviour do not change outside a run. The one visible difference: the window is 120 px wider (1120x1000
instead of 1000x1000, `gui.py:144`) so the Route tab's 4x view fits (5.2), and the Setup tab shows that extra width as
empty space beside its column; its scrollbar and mousewheel scrolling stay (5.7 scopes the wheel to this tab). During a run (from Start until the run's cleanup has finished) these are
disabled: every button of the Setup tab and its character picker, that is Load Map, the picker, Open Map Editor, the
dev buttons (`gui.py:198-215`), the draw blue node and draw orange node buttons, clear console and clear drawn nodes. Load
Map, the picker, Open Map Editor and the dev buttons change the graph or open a window (section 5.12); the other four
are greyed out too because Hong's pick (H64, RVT94-1 option B, "Same tab as A", whose tab has "everything except Stop
greyed out") leaves only Stop usable during a run. All of them come back after the cleanup.

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
| Saved runs (a list, 4 rows visible, scrolls):                              [ Open ] [ Refresh ]  |
|   2026-10-31 14:02  fossil_foundry_2  5 laps  312 MB                                               |
|   2026-10-31 11:40  fossil_foundry_2  2 laps  130 MB                                               |
| replay bar (when a saved run is open): |< < > >|  slider  2x 3x 4x                                |
+--------------------------------------------------------------------------------------------------+
```

The view area and its panel are `RouteViewWindow`'s widgets (section 5.9), unchanged in look except the time column
(section 5.10). The window grows to 1120x1000 so the 4x view (648x328 canvas plus the 450 px panel, `window.py:51`)
fits beside nothing else; the Setup tab keeps its own scrollbar (5.1).

Hosting: `RouteViewWindow(parent, folder, model, *, live)` is built for one folder (`window.py:69`). The tab keeps one
container Frame for the view and, for each new live run and each replay it opens, destroys the old `RouteViewWindow`
(and stops its `LiveController`) and builds a new one in the container. It never re-targets a built view to another
folder. Rebuilding packs plain widgets into an existing Frame, so it opens no window (5.12).

States of the top area:

| State | Status line text | Enabled |
|---|---|---|
| Not ready | one line naming what is missing, e.g. "Load a map on the Setup tab first." (see 5.4) | settings, saved runs |
| Ready | "Ready. Press Start, then click the game." | settings, Start, saved runs |
| Connecting | "Connecting to the board..." | Stop |
| Countdown | "Click the game now. Starting in 5 ... 4 ... 3 ... 2 ... 1. F12 stops." | Stop |
| Running | "Running. F12 or Stop ends the run. Clicking this app also stops it." (section 9) | Stop |
| Stopping | "Stopping: releasing keys..."; if Stop was pressed while connecting, "Stopping: waiting for the board connection attempt to end (up to 30 s)..." (5.6) | none |
| Ended | "Ended: <reason in words> (<run folder name>). Replay is open." (texts in 5.5, including a worker that ended without saying so) | settings, Start, saved runs |

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
already reaches past the facade). The class goes to the run as its key in `session.CLASSES` (e.g. `"kanna"`), not as
the class or its name (5.6).

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
6. A new run folder `data/route/<UTC stamp>` that does not exist yet (`runtime.check_out`, as `walk._check_out`). The
   stamp has 1 s resolution, so a second Start in the same second as a run that wrote its folder is refused: "A run
   folder for this second already exists. Press Start again."

The route is loaded on the worker by `walk._load_route`'s code (`walk.py:220-234`), which silences the CSV parser with
`contextlib.redirect_stdout` (:222). That swaps `sys.stdout` for the whole process for the load's duration, so in the
app it also swallows other threads' prints for that moment (e.g. the capture thread's "Minimap frame is detected to be
black"). Accepted: the load is one CSV parse and graph build, and those lines are informational; the plan does not
change it.

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
| App got the keyboard while walking (section 9) | `app_focus` (new) | "Stopped: you clicked the app. Press Start to run again." |
| Stop before the walk (route load, connect or countdown) | `button`, `window_close` or `app_focus`, reason `cancelled` (5.6) | "Ended: cancelled before the walk (you pressed Stop)." (`app_focus`: "Stopped: you clicked the app. Press Start to run again.") |
| App still had the keyboard when the countdown ended (section 9) | `countdown_focus` (new), reason `cancelled` (5.6) | "Not started: click the game first. Press Start, then click the game before the countdown ends." |

Other exit codes (same numbers as `walk.py:6-9`): 1 "Ended: the board link was lost.", 2 "Could not start: the run
folder could not be written." (on the worker this is the leg log failing to open, `walk.py:381-384`), 3 "Could not
start: no minimap frame or character found.", 4 "Could not start: the board did not connect.", 5 "Could not start:
the map, route or character icon did not load.", 6 and 7 "Ended: the walker failed; see <run folder>.", 9 "Could not
start: the F12 stop key could not be installed." An `ended` event with `code=None` (an exception left `run()`, 5.6)
shows "Ended: the run stopped unexpectedly: <error>. See the console and <run folder>." A run that got as far as the
leg log (after connect) leaves its folder, as `walk` does; a cancel always leaves one with its `stop.json` (5.6). In
both "stopped unexpectedly" texts (this one and the next paragraph's) the words "and <run folder>" are shown only when
that folder exists (an exception before the leg log, e.g. in the route load, leaves none); otherwise the text ends at
"the console." ("See the console." in this one, "; see the console." in the next paragraph's).

The tab never stays in Running or Stopping without a live worker: its 100 ms queue drain also checks
`worker.is_alive()`; a worker that has ended with no `ended` event seen (after one more drain of the queue) is treated
as ended with the text "Ended: the run stopped unexpectedly; see the console and <run folder>.", and the controls come
back as for any end.

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
    character: str              # the class key of CLASSES below, e.g. "kanna"; written to session.json and RunHeader
    laps: int | None
    minutes: float | None
    countdown_s: float          # H61; the tab passes 1-60, the CLI passes walk._countdown_s (5.0; tests set 0)
    seed: int | None            # None: drawn from the OS, written to session.json
    out: Path

CLASSES: dict[str, type[CharacterClass]] = {"kanna": Kanna}   # moved from walk._CLASSES (walk.py:84)

@dataclass(frozen=True)
class RunEvent:                 # posted by the worker; the tab drains a queue.SimpleQueue with `after`
    kind: Literal["status", "countdown", "running", "warning", "ended"]
    text: str
    seconds_left: int | None = None
    code: int | None = None     # set on "ended": the walk exit code; None when an exception ended run()

class RunSession:
    def __init__(self, settings: RunSettings, shared: SharedFrame, *,
                 on_event: Callable[[RunEvent], None],
                 make_positions: Callable[[SharedFrame], PositionSource | Failure[str]] = runtime.make_positions,
                 wait_first_frame: Callable[[SharedFrame, float], Result[None, str]] = runtime.wait_first_frame,
                 make_link: Callable[[], Link] = ..., make_clock: Callable[[], Clock] = ...,
                 make_stop: Callable[[Callable[[], None]], StopControl] = ...,
                 start_capture: Callable[[SharedFrame], object] | None = None,   # the CLI passes walk._start_capture
                 may_walk: Callable[[], bool] | None = None,   # asked once when the countdown ends (section 9)
                 sleep: Callable[[float], None] = time.sleep, sigint: bool = False) -> None: ...
    def run(self) -> int        # blocking: everything from loading the route to the cleanup; returns the exit code
    def stop(self, source: str) -> None   # any thread; a cancel flag before StopControl.start(), trigger(source) after
```

Seams and the walk tests (the oracle). `walk.main` keeps its module-level seams `_make_positions`, `_wait_first_frame`,
`_make_link`, `_make_clock`, `_make_stop_control` and `_countdown_s` (`walk.py:103-131`) and passes them to
`RunSession` at call time (`make_positions=_make_positions`, and so on, read from walk's globals when `main` runs),
including `start_capture=_start_capture`. So every `monkeypatch.setattr(walk, ...)` in
`tests/route/test_walk_cli.py` (:464-470, :555, :575) still reaches the run unchanged, and those tests keep their
assertions and their patches. Three other lines of that file reach a name through `walk` that moves to `session.py`
with the body: `walk._load_route` (:438), `walk._LostLegSink` (:754) and `walk.JsonlLegLog.open` (:881, patched on
the class through walk's import, `walk.py:77`). Those three lines re-point to `session._load_route`,
`session._LostLegSink` and `session.JsonlLegLog.open`; their assertions do not change, and `walk.py` does not keep
the names for the tests' sake. Two other test files hold the same kind of line (a name that moves, reached through
`walk`), and they re-point the same way with their assertions unchanged: `tests/route/test_walk_view_minors.py:39`
and `:55` patch `walk.RouteWalker.run` and re-point to `session.RouteWalker.run` (the same class object, so the patch
still reaches the run); `tests/route/test_walk_view.py:247` and `:253` read `walk._OUT_FILES` and `walk._FRAMES` and
re-point to `session.OUT_FILES` and `session._FRAMES`. `test_walk_view.py:256-266` runs the walk with `--view`,
which is now rejected: it drops `--view` and runs over the three new names left (`steps.jsonl`, `map.csv`,
`minimap.png`; 5.13 removes the `view.*` names), with its assertions unchanged.
`tests/route/test_session.py` passes the same fakes (the simulator's
`SimPositionSource` and `SimLink`, as `test_walk_cli.py:25`, :314, :446) as constructor arguments. The patches of
`walk._start_viewer`,
`walk._wait_for_viewer` and `walk.open` live in tests that 5.13 removes. The tab passes only `settings`, `shared`,
`on_event`, `may_walk` (section 9) and `sigint=False`, so it gets `runtime.make_positions` (the real `MinimapPositionSource`); the in-app timing
measurement (5.7) builds the session with `make_positions` and `make_link` from the simulator, as `test_session.py`
does.

Order inside `run()`, with the exit code of each early end (as `walk.main` today, `walk.py:343-397`): load the route
from the CSV (5); `make_positions(shared)` (5); `start_capture(shared)` if one was given (the CLI's, so a bad map or
icon still exits 5 before any capture thread starts, as `walk.py:343-363` today; the tab passes none);
`wait_first_frame(shared, 30 s)` (3, "no minimap frame"); the character read on that frame (3); connect (4); open the
leg log, which creates the run folder (2); `make_clock()` (where `walk.py:388` calls it today); build the walker;
`StopControl.start(sigint)` (9); write the run files; countdown; `may_walk()` if one was given (section 9); recorder;
timer; walk; cleanup. The first
frame wait and the character check are inside `run()`, not in the tab or the CLI: the tab's own check 4 (5.4) only
refuses Start early, and `run()` checks again on the frame it will start from.

Class key: the tab holds a class instance (`controller.agent.characterClass`, `gui.py:349`); it looks up
`type(instance)` among `CLASSES`' values and passes the key, so `session.json`'s `class` and `RunHeader.character` stay
`"kanna"` as the CLI writes them (`walk.py:399`, `:421`), never `"Kanna"`. A class with no key refuses Start at check 1
("This character cannot walk a route yet."). `run()` builds `CLASSES[settings.character]()`.

- `shared` is the app's `SharedInfo` (it has the two attributes `runtime.SharedFrame` names, `runtime.py:245-250`).
  `RunSession` has no capture code of its own: the tab passes no `start_capture` (the app's capture thread already
  runs on `SharedInfo`); the CLI builds a `runtime.SharedFrame`, passes it, and passes `walk._start_capture`
  (`runtime.start_capture`), which `run()` calls at the point in the order above. There is never a second capture of
  the same monitor (H58 unchanged).
- `on_event` is called on the worker thread; the tab passes `queue.put`. The worker never touches Tk.
- `stop(source)` has two paths, split at `StopControl.start()`. One lock in `RunSession` guards the cancel flag, its
  source and stamp, and a "stop control started" bit, so a `stop()` can never fall between the two paths:
  - Before it (route load, first frame, character check, connect, leg log, walker build) `stop()` sets, under the lock,
    the cancel flag (first call only), its `source` and `time.perf_counter_ns()` (the clock `StopControl.trigger`
    itself stamps with, `runtime.py:87`; no session clock is needed, so a Stop right after Start is covered); `run()`
    checks the flag after each of those steps. Right after `StopControl.start()` succeeds, `run()` sets the "started"
    bit under the lock and, if the flag was set meanwhile (a `stop()` after the last check), calls
    `stop_control.trigger(source)` at once, so the countdown sees it at its first check. If a step itself fails while
    the flag is set (route load or icon 5, first frame or character 3, connect 4, leg log 2, `StopControl.start` 9),
    the step's own failure wins: that exit code, its "Could not start: ..." text, and no cancel payload, as today's
    early exits. A connect in progress is not interrupted: `BleLink.connect` waits up to its 30 s timeout (`link.py:143`), so a Stop
    pressed while connecting takes effect when the connect returns. The status line says so ("Stopping: waiting for
    the board connection attempt to end (up to 30 s)...").
  - From the "started" bit on, the countdown included (the walker and the started `StopControl` exist before the
    countdown, `walk.py:390-397` vs `:429-430`), `stop()` is `stop_control.trigger(source)`. The countdown checks
    `trigger_source` after each one-second step; if set, `run()` skips the recorder, the timer and `walker.run()` and
    goes to the cleanup. When the countdown ends with no stop, `run()` calls `may_walk()` if one was given; `False`
    is a stop with source `countdown_focus` (`stop_control.trigger("countdown_focus")`, then the cleanup, nothing
    walked; section 9).
- A run stopped before the walk (either path, `countdown_focus` included) ends with exit 0 and this cleanup payload,
  written by the same `stop.json` step of the cleanup (5.5): `{"reason": "cancelled", "trigger_ns": <the cancel
  flag's stamp if the flag was set, else StopControl.trigger_ns>, "trigger_source": <source>}` (both stamps are
  `perf_counter_ns`). The `stop.json` step creates the run folder if a cancel came before the leg log created it, so
  the source is always on disk. The step log's `end` line (only if the step log was started, i.e. from the countdown
  on) gets reason `"cancelled"` and exit code 0. The tab's Ended line is the 5.5 table's text for the source (nothing
  for `window_close`).
- The countdown is `countdown_s` one-second steps, each posting a `countdown` event and then checking for a stop.
- Recording is always on (H62): `run()` starts the frame recorder after the countdown, as `--record` does today. The
  recorder thread's target is a wrapper around `record` (today `_record_frames`, `walk.py:303-307`, which catches
  nothing) that catches every exception (`except Exception`, e.g. `OSError` from `index.write`,
  `record_minimap.py:99-100`, on a full disk) as well as a `Failure` return, posts a `warning` event ("Recording
  stopped: <reason>. The walk goes on.") and ends; the walk goes on. The wrapper also stops recording when free space
  on the run folder's drive falls below a floor, checked with `shutil.disk_usage` every 600 frames (1 minute at 10 fps):
  "Recording stopped: less than 1 GB free on C:. The walk goes on." `record` itself is unchanged
  (`record(frames, out_dir, seconds, fps, clock, sleep, stop)` loops until `stop` is set,
  `src/route/harness/record_minimap.py:63-111`): the wrapper passes it a `frames` callable that wraps
  `shared_frames(shared)`, counts its polls and calls `shutil.disk_usage` on every 600th; below the floor it sets the
  `stop` event it gave `record` and remembers why, so `record` returns `Success` and the wrapper then posts the
  low-space warning. The floor (1 GB) and the Start refusal (2 GB, 5.4)
  are provisional until the plan's measurement step (section 11) has the real MB per hour.
- `run()` never ends without telling the tab: it posts the `ended` event from a `finally` around its whole body, with
  the exit code, or with `code=None` and the exception text ("the run stopped unexpectedly: <type>: <message>") when an
  exception is leaving `run()`. `run()` still re-raises (the CLI keeps today's behaviour, including a held
  `KeyboardInterrupt` re-raised by `runtime.run_cleanup`, `walk.py:469-471`). The tab's worker thread target wraps
  `run()` in `try/except BaseException` and prints the traceback to the console, so no exception leaves the worker.
- `StopControl.start(sigint: bool = True)` gains the flag; `RunSession` passes `sigint` through. The CLI runs on the
  main thread and passes `True` (Ctrl+C as today); the tab passes `False`, since there is no console and
  `signal.signal` fails off the main thread.
- Layers: contract f ("f route layers", `pyproject.toml:62-75`) gets `src.route.session` in its top layer, beside
  `src.route.sim`: the first entry becomes `"src.route.session | src.route.sim"`. It must sit above
  `src.route.position`, not just above `walker`, because it imports `runtime`, which imports `src.route.position`
  (`runtime.py:253-258`); the `|` makes the two independent, so neither imports the other (import-linter
  `contracts/layers.py:20`). It imports `src.input` (link, layer, timing) as `walk` does; it imports neither
  `src.gui` (contract d) nor `src.vision` (contract e).

The walk CLI after the change: `python -m src.route.walk --map ... --supervised [--class kanna] [--laps N]
[--minutes M] [--seed S] [--out DIR]`. `--view` is removed (H57 retires H50; argparse rejects it with exit 2). Every
CLI walk records (H62); `--record` stays accepted and does nothing, so older commands in the plans still run
(orchestrator ruling RTS95-D3).

### 5.7 Threads

| Thread | Does | Touches Tk |
|---|---|---|
| Tk main | mainloop, both tabs, drains the run event queue every 100 ms, `LiveController` poll | yes |
| capture (existing) | `capture_and_process_minimap` on `SharedInfo` | no |
| run worker (new, daemon) | `RunSession.run()` | no |
| `BleLink-loop`, `stop-hook`, `stop-control`, `step-log`, `record`, `walk-timer`, `route-stop-guard` | as in the walk today (`record` runs the catching wrapper of 5.6) | no |
| `saved-run-sizes` (new, daemon) | sums the saved runs' folder sizes for the list (5.11) | no |

While a run is live the Setup tab's `_updateCanvas` keeps its 10 ms loop but skips building the picture when the Setup
tab is not shown, so the walker shares the interpreter with one redraw (the Route tab's) instead of two. The cost of
running the walker inside the app is not measured (RVT92 [review], route-view spec 5.6): the plan adds a measurement
step that compares the `sent` events' `reaction_ms` and `waited_ms` of an in-app simulator run with a CLI simulator run.
Who runs it (waits for Hong: RTP96-RB-D1): an agent under answer A, Hong under answer B (6.3 gives both wordings).

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
| Notebook | `src/gui/gui.py` | `ttk.Notebook` with Setup and Route; close handler (5.5); the focus binding of section 9 |
| Capture loop | `src/vision/vision.py` | the `cv2.imshow("Minimap", ...)` / `cv2.waitKey(FRAME_DELAY_MS)` pair (:83-84) becomes one `time.sleep(FRAME_DELAY_MS / 1000)`; nothing else (H67, 5.12) |
| Standalone replay | `src/route_view/__main__.py` | `python -m src.route_view --follow <folder>` always opens a replay; `--ready` and the live path removed |

Contracts (`pyproject.toml`):
- "route_view is a leaf" (:87-93): `src.gui` **and `src.main`** are taken off its `source_modules`, so
  `src.gui.route_tab` may import `src.route_view`. `src.main` must go too: the contract has no
  `allow_indirect_imports`, so it forbids indirect chains as well (`forbidden.py:135-143` uses
  `graph.find_shortest_chains` unless `allow_indirect_imports` is true), and after the change
  `src.main -> src.gui.gui -> src.gui.route_tab -> src.route_view` is such a chain (`src/main.py:3` imports
  `src.gui.gui`). The other sources (`src.route`, `src.controller`, `src.vision`, `src.input`, `src.classes`,
  `src.rune_solver`) stay; `lint-imports` confirms that none of them reaches `src.gui` (for `src.route`, contract d
  already forbids it).
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
  (5.4 step 5); at that rate 2 GB lasts about 4 h, and laps and minutes may both be empty ("until stopped"), so during
  the run the recorder checks free space every 600 frames and stops recording below 1 GB free, with a warning on the
  status line, while the walk goes on (5.6). A recorder error of any kind (e.g. a full disk) does the same. `data/route`
  is under the repo, on C:, so this guards the system drive. Both numbers are provisional: the plan measures the real
  MB per hour (section 11) and sets them from it.
- Old runs: kept until Hong deletes them by hand, as the RVT93-7 option he picked said ("you delete old runs by
  hand"). Nothing deletes runs automatically. The saved-run list shows each run's size so the big ones are easy to find.
- Picking a run: the "Saved runs" list in the Route tab is a `tk.Listbox` with a scrollbar (no file dialog and no
  Combobox drop-down, both of which are new windows, 5.12). It shows the folders under `data/route` that hold
  `steps.jsonl` or `legs.jsonl`, newest first, as "<local start time> <map file name> <laps done> <size>".
  "Refresh" re-reads the folder; "Open" loads the selected run with `load_folder` (`model.py:690`) into the view as a
  replay. The list, Open and Refresh are disabled while a run is live.
- Sizes: a long run holds tens of thousands of frame files (10 per second is 36 000 per hour), so summing them on the
  Tk thread would freeze the app. The list is filled at once with "size ..." and a daemon thread (`saved-run-sizes`)
  sums each folder's file sizes (`os.scandir`, no Tk) and posts the results to a queue that the tab drains with
  `after`, as the run events are (5.6). A Refresh while a sum is running starts a new one and drops the old results.
- Replay draws on the recorded frames when the run has them, else on `minimap.png` (H52), else on a dark canvas (as
  built, route-view spec 5.5).

### 5.12 Anti-cheat rules in the app (H56, H64, H65, H67)

1. Nothing in the app, the run driver, the walk or the view asks Windows which window is in front, enumerates windows
   or processes, or opens, reads or signals MapleStory's process or window. The only Windows facilities used while the
   game runs are monitor capture (H58) and the F12 low-level keyboard hook (H59), plus Tk's own handling of its own
   windows. That includes the one reaction section 9 adds (H64): the tab binds Tk's `<FocusIn>`/`<FocusOut>` events,
   which Tk makes from the `WM_SETFOCUS`/`WM_KILLFOCUS` messages Windows sends to our own window anyway
   (`win/tkWinX.c:1053-1100`), and reads Tk's own `focus_get()` (Tk memory, `tkFocus.c:696-708`); neither asks
   Windows anything.
2. During a run (from Start until the cleanup has finished) the app opens no new window (no message box, file dialog,
   Toplevel or OpenCV window), and calls no `wm` command that changes a window's style or icon (`overrideredirect`,
   `resizable`, `transient`, `attributes` with `-toolwindow`, `-topmost` or `-fullscreen`, `iconbitmap`, `iconphoto`).
   Hong kept this rule (RVT94-2 A, H65); its reason is in section 2 (some of these reach Tk's own
   `GetForegroundWindow` call on an already-shown window; `-topmost` and the icon calls do not, and are banned
   anyway so the rule stays one plain line; a new window can take the keyboard). Errors and warnings go to the Route
   tab's status line. This is why the map editor and Load Map are disabled during a run, and why the Route tab never
   uses a dialog even outside a run (the game is usually open then too).
3. During a run the app never calls `focus_force`, `focus_set`, `lift`, `tkraise` or `grab_set` (they move or take
   focus; the existing `test_10` ban list in `tests/route_view/test_view_window.py:555` carries over to the tab).
4. A test enforces rule 1 by name (section 8). Scope: every `src/**/*.py` and `tools/**/*.py` file, except those under
   `src/input/harness/`; `tests/` is not scanned (the key-test tests and the guard tests name these calls on purpose,
   e.g. `tests/input/test_guard_targets.py`, `test_listener.py`, `test_probe.py`, `test_soak.py`, and the scan test
   itself plants a sample). No scanned file may contain `GetForegroundWindow`, `EnumWindows`, `EnumProcesses`,
   `CreateToolhelp32Snapshot`, `OpenProcess`, `FindWindow`, `GetWindowText`, `GetClassName`,
   `GetWindowThreadProcessId`, `tasklist`, `Get-Process` or `psutil`. Today these appear only in `src/input/harness/`
   and `src/route/harness/view_check.py` (grep at 61455a7), and `view_check` is removed (5.13). `src/input/harness/`
   is exempt because its tools run only with MapleStory closed; after section 6, `tasklist` and `Get-Process` leave it
   too. Known limit: a scan by name cannot see a name built at run time (`getattr(ctypes.windll.user32, "Get" +
   ...)`); review covers that.

5. No OpenCV window (H67, Hong's RVT96-1 pick "Remove it (Recommended)"). Today the capture loop shows every frame
   in a native OpenCV window of its own, `cv2.imshow("Minimap", minimap_frame)` and `cv2.waitKey(FRAME_DELAY_MS)`
   (`src/vision/vision.py:83-84`), in the app (`src/main.py:17`) and in the CLI walk (`runtime.start_capture`,
   `runtime.py:278-282`), so it exists during every run: clicking it sends the bot's keys there, and closing it makes
   the next `imshow` create and show a new window mid-run (OpenCV 4.13.0 `modules/highgui/src/window_w32.cpp`:
   `ShowWindow(..., SW_SHOW)` :1071, `SetFocus` on `WM_ACTIVATE` :1618-1621, `WM_DESTROY` :1543-1545). The window is
   removed: the pair becomes `time.sleep(FRAME_DELAY_MS / 1000)`, which keeps the same pause in the loop (the comment
   at :84 says the longer delay lowers how often `camera.grab()` returns None). The capture itself (H58) is unchanged
   and the minimap stays visible on the Setup tab. The name scan of rule 4 also bans `imshow`, `namedWindow` and
   `waitKey`, with the same scope; today they appear only at `vision.py:83-84` (grep of `src` and `tools`).

Tk at first show, for Hong's information (corrected after RVT94; section 2): Tk makes no `GetForegroundWindow` call
when the app's main window or the map editor is first shown, nor when an icon is set or a window is made always on
top; it makes one only when an already-shown window is restyled or made transient, which the app never does
(`notes/q-20261031-162350-51bf.md`: no `transient`, `resizable`, `overrideredirect`, `attributes`, `iconphoto` or
`iconbitmap` call in `src/gui` or `src/main.py`). [inferred, source reading, not run: an icon set before a window's
first show makes that first show take the query path.] So opening the app or the map editor while the game runs
asks Windows nothing.

Also accepted, outside a run only: the Setup tab's own pop-up windows, each a new window that could take the keyboard
from the game: Load Map's file dialog (`filedialog.askopenfilename`, `gui.py:316`), its error box
(`messagebox.showerror`, `gui.py:337`), and the character picker's drop-down list (a `ttk.Combobox`, `gui.py:174`).
[inferred: the dialog and the box are native Windows dialogs, not Tk top-levels; a Combobox drop-down is a Toplevel
shown on its first post; not verified in Tk's source.] All three are disabled during a run (5.1). The Route tab itself never opens one: its saved-run picker is a
`tk.Listbox` (5.11), not a Combobox.

### 5.13 What is removed

`src/route_view/win_focus.py` and `tests/route_view/test_win_focus.py`; the claim/ready/first-show code in
`src/route_view/__main__.py` and its tests in `test_view_cli.py`; the spawn and ready wait in `walk.py` and
`tests/route/test_walk_view*.py`; the N1 tool `src/route/harness/view_check.py` and `tests/route/test_view_check.py`
(and with it the root CLAUDE.md rule about `_maple_check`); the constants `VIEW_LOG`, `VIEW_CLAIM`, `VIEW_READY`
(`step_log.py:59-61`); the route-view spec's checks N1 and H-1. The ban on `wm resizable`/`overrideredirect`/
`transient`/`attributes` in the view's test stays as part of rule 5.12-2 (its reason changes from "keeps the
no-activate style" to "no style change during a run").

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
- It asks once per tool run, not again (where the one answer is read and how probe reuses it for each probe: 6.2).
  The 60 s re-check in `soak` and `probe`, probe's per-probe check and the per-start check in `session_starts` are
  removed: a question cannot be asked mid-run, because the console would take focus from the
  sink (a `FOCUS_LOST` abort) and the typed answer would be extra key events in `keys.jsonl` (a Delivery failure). Hong
  was told when he chose: "the 60-second re-check that catches the game being opened during a long soak run is lost;
  you would be the safety net" (RVT93-5 option B cost). The orchestrator's ruling RTS95-D1 (one ask at the start,
  no mid-run re-check) records that this loosens nothing beyond what he chose.
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
4. H15's conditions for agents (work needs it, MapleStory closed, PC idle), and Hong's rule for the answer (H66,
   RVT95-1 A): an agent types `y` only if Hong said in that same session that MapleStory is closed; otherwise it asks
   him first, and never answers `y` on its own judgment (not from the captured screen, not from an earlier session).

### 6.2 Code shape (keeps the patched names)

- `soak._process_names` is replaced by `soak._read_answer() -> Result[str, str]` (calls `input(PROMPT)`; `EOFError` or
  `OSError` gives `Failure`). `soak.PROMPT = "Is MapleStory closed? y/n "`.
- `soak.maple_check() -> tuple[bool, str]` keeps its name and signature and reads `_read_answer` from soak's own
  globals: ok only for `y`.
- `_check_processes` keeps its name in all three modules (the name is now historical; renaming it would edit three
  modules and `test_guard_targets.py` for no behaviour gain). `PROCESS_CHECK_PERIOD_NS` and every `periodic_check`
  closure go; `wait_for_click` no longer gets a MapleStory tick.

Where the one answer is read, and the call sites that remain (today each tool asks more than once, so this is a
change, not a rename):

| Tool | The one call that asks | Call sites removed | How the answer reaches `session.json` |
|---|---|---|---|
| `soak` | `run_soak` -> `_check_processes(writer)` (`soak.py:599`) -> `maple_check()` | the 60 s re-check (`:600-605`, `periodic_check`) | `_check_processes` writes `writer.session["process_check"]` as today |
| `session_starts` | `run_starts` -> `_check_processes()` (`session_starts.py:171`) -> `soak.maple_check()` | the start-wait re-check (`:172-177`) and the per-start call before each start (`:189`) | no `session.json` (unchanged) |
| `probe` | `run_probes` step 1 -> `soak.maple_check()` (`probe.py:1065`), once per tool run, before the board port check | the per-probe ask in `_run_probe` (`:1017`) and its 60 s re-check (`:1018-1023`) | `run_probes` passes the step-1 answer text to every `_run_probe(..., answer=text)`; `_run_probe` calls `_check_processes(writer, answer)`, which now only writes `writer.session["process_check"] = answer` and never asks |

So `probe._check_processes` changes signature to `(writer: RunWriter, answer: str) -> None` and no longer reads
`soak.maple_check`; `soak._check_processes` and `session_starts._check_processes` keep their signatures. With this,
`echo y | python -m src.input.harness.probe ...` reads one line and runs every probe; a second `input()` would raise
`EOFError` and refuse. The test changes this forces in `tests/input/test_guard_targets.py` are listed in section 8.

### 6.3 Safety text

Root `CLAUDE.md`, "Safety rules": replace the second and third bullets (the harness tools, and the patched check names
including `view_check`) with:

> - The harness tools that send keys (`probe`, `soak`, `session_starts` in `src/input/harness/`) need the board, a real Bluetooth link and the "Helix input test sink" window. Never run them with MapleStory open. Before the first key each asks once on its console "Is MapleStory closed? y/n" and goes on only on `y`; any other answer, or no answer (input closed), stops it with exit 2 before the listener, the link or any key (decision H60). Nothing checks again during the run, so MapleStory must not be started until the tool has ended. The tools never list the running programs (Hong's anti-cheat standing fact, H56). Details are in `src/input/CLAUDE.md`.
> - That question (`soak.maple_check`, which reads the answer through `soak._read_answer`, and `_check_processes` in `soak`, `probe` and `session_starts`) is patched by name in the tests: never move or rename it (`src/input/CLAUDE.md`).

Replace the bullet "Never open a route-view replay ... H-1, has not run yet" with:

> - A walk starts only from the Route tab's Start button or `python -m src.route.walk --supervised`, and only with Hong's go: an agent never presses Start and never runs the walk against the board (tests drive `RunSession` with fake links). During a run the app opens no window and changes no window style (spec `docs/superpowers/specs/2026-10-31-route-tab-design.md` 5.12). Never open a standalone replay (`python -m src.route_view`) while a walk runs: a new window takes focus when clicked, and the board's keys go to whichever window has focus.
> - Hong's anti-cheat standing fact (H56): while MapleStory runs, nothing of ours may read or write its memory, open, query or enumerate its process or window, or ask Windows which window is in front. `tests/test_anti_cheat_names.py` fails if a banned call name appears outside `src/input/harness/`, whose tools run only with MapleStory closed.

(waits for Hong: RTP96-RB-D1) The "A walk starts only ..." bullet is written for answer B: the rule stays as it is
and Hong runs the in-app timing measurement (5.7). Answer A changes one sentence: its clause "an agent never presses Start and never runs
the walk against the board" becomes "an agent never presses Start in the app (`python -m src.main`) and never runs the
walk against the board; it may run the simulator tool `tools/route_tab_sim.py measure`, which has no board and sends no
key, on an idle PC (H15)". The rest of the bullet is the same under both answers.

In the H15 bullet, replace "MapleStory is not running (the tools' own check still applies and is never bypassed)" with
this text (Hong's RVT95-1 pick, H66):

> MapleStory is not running (each tool asks "Is MapleStory closed? y/n" once at its start; an agent answers `y` only if Hong said in that same session that MapleStory is closed, and otherwise asks him first; it never answers `y` on its own judgment, decision H66)

`src/input/CLAUDE.md`, "Traps", replace the "MapleStory guard" bullet with:

> - MapleStory question (H60). `soak.maple_check` (soak.py) reads the answer through `soak._read_answer`, which calls `input(soak.PROMPT)`; only `y` passes, and a closed input is a `Failure`. Each tool asks exactly once per run, before the listener, the link or any key, and never again: `soak` through `soak._check_processes` in `run_soak`, `session_starts` through `session_starts._check_processes` in `run_starts`, `probe` at step 1 in `run_probes` (`soak.maple_check()` through the module); `probe._check_processes(writer, answer)` only writes that one answer into each probe's `session.json` and never asks. The tests patch `soak._read_answer` by name (test_probe, test_session_starts, test_soak, test_soak_v2_faults; `test_guard_targets.py` adds its own): if the function moves, those patches stop reaching the question and a test would block on real input. `tests/input/test_guard_targets.py` fails if a patched name stops being defined in its module. Never move or rename `maple_check`, `_read_answer` or `_check_processes` (the last name is historical: it no longer lists processes).

In its "Harness tools" paragraph replace "(the tools' own check still applies)" with "(the tools ask; see the root
CLAUDE.md H15 bullet for how an agent answers)". `firmware/CLAUDE.md:5`: replace "never bypass the tools' own
MapleStory check" with "answer the tools' 'Is MapleStory closed?' question `y` only if Hong said in that same session
that MapleStory is closed, otherwise ask him first (root CLAUDE.md H15 bullet, decision H66)". `tests/CLAUDE.md:15`:
"patches `soak._process_names`" becomes "patches `soak._read_answer`". `src/vision/CLAUDE.md:9-10`: the
`cv2.imshow("Minimap", ...)` debug-window line goes and the pacing line names `time.sleep(FRAME_DELAY_MS / 1000)` in
place of `cv2.waitKey` (H67, 5.12-5).

`src/route/CLAUDE.md`: drop the N1 tool lines (:36, :49-55) with the tool; add `session.py` to the seams and the
`RunSession` traps (Tk never touched from the worker; `sigint=False` off the main thread). `src/gui/CLAUDE.md`: add the
Notebook, the Route tab, the close handler, the focus binding (section 9) and rule 5.12. `src/route_view/CLAUDE.md`: rewrite for a hosted view (no
separate process, no claim/ready, no `win_focus`).

## 7. What must not change

The planner, walker, moves and input layer behaviour; `steps.jsonl` format V1; the run folder's file names; the walk's
exit codes and cleanup order; the simulator's output for a seed (sim smoke 6/6 PASS, same output); the F12 hook
(H59); monitor capture (H58: the grab and the corner match are unchanged; only the separate "Minimap" display window
goes, H67, 5.12-5); the Setup tab's widgets and behaviour outside a run (the window is 120 px wider, 5.1).

## 8. Tests

New:
- `tests/route/test_session.py`: `RunSession` built with the simulator's `SimPositionSource` (`make_positions`) and
  `SimLink` (`make_link`), a fake `wait_first_frame`, a fake stop control and a fake clock and sleep: the events in
  order (status, countdown 5..1, running, ended); `stop("button")` during connect (cancel flag; `stop.json` is
  `{"reason": "cancelled", "trigger_ns": <the perf_counter_ns stamp taken at stop()>, "trigger_source": "button"}`,
  exit 0, `walker.run` never called), right after Start (a `stop()` before `make_clock()` runs: same payload, no
  error), between the last flag check and `StopControl.start()` (a fake stop control whose `start` calls
  `session.stop("button")`: the countdown sees the trigger at its first check, exit 0, the flag's stamp in
  `stop.json`, `walker.run` never called), with the flag set while the step itself fails (a connect `Failure`: exit 4,
  no cancel payload), during the countdown (`trigger`; same payload with `StopControl.trigger_ns`; the step log's `end`
  has reason `"cancelled"`, exit 0) and while walking, each ending with `release_all` first when connected; the
  window-close and `app_focus` sources; `may_walk` returning `False` at the end of the countdown (source
  `countdown_focus`, cancel payload, exit 0, `walker.run` never called) and `True` (the walk runs); `start_capture`
  called once, after `make_positions` and before `wait_first_frame`, and not at all on a route-load or icon failure;
  exit codes 0/1/2/3/4/5/9/10 as `walk`'s tests have them (3 from the fake `wait_first_frame`
  and from a character not found, both inside `run()`); recording always on (frames written); no Tk import.
- `tests/route/test_session.py`, failure paths: an exception raised inside `walker.run` (a fake walker) still posts
  exactly one `ended` event, with `code` 7 as today; an exception raised outside the walk body (e.g. by a fake
  `make_clock` or the leg log) posts `ended` with `code=None` and the error text, and `run()` re-raises it; the
  recorder wrapper turns an `OSError` from `index.write` (patched) into one `warning` event and the walk goes on; with
  `shutil.disk_usage` patched below the 1 GB floor, recording stops after the next 600-frame check with the low-space
  warning and the walk goes on.
- `tests/route/test_walk_cli.py`: its existing `setattr(walk, ...)` patches (:464-470, :555, :575) reach the run
  through `RunSession`'s arguments unchanged (5.6), and every existing assertion holds; the three lines that reach a
  moved name (:438 `_load_route`, :754 `_LostLegSink`, :881 `JsonlLegLog.open`) reach it through `session` instead,
  with their assertions unchanged (5.6); so do `test_walk_view_minors.py:39`, `:55` and `test_walk_view.py:247`,
  `:253`, and `test_walk_view.py:256-266` drops `--view` (5.6).
  `test_e_missing_map_exits_5` and `test_e_character_icon_failure_exits_5`
  (:568, :574) also assert `capture_calls == 0`, so the capture still starts after the route load.
- `tests/route/test_walk_cli.py`: unchanged expectations for exit codes and files; `--view` rejected with exit 2;
  `--record` accepted.
- `tests/route/test_walk_cli.py` (where `StopControl` is tested today): `start(sigint=False)` off the main thread succeeds
  and installs no handler.
- `tests/controller/test_route_tab.py` (beside `test_gui_startup.py`, whose real withdrawn Tk root it reuses): Start's six refusals and their
  texts; controls disabled during a run and back after; the countdown text; events drained into the status line;
  the saved-run list (a `tk.Listbox`; order; sizes filled in from the size thread through the queue, never summed on
  the Tk thread; disabled while live); the class key passed to the session is `"kanna"` for a `Kanna` instance, and a
  class with no key refuses Start; a worker that ends without an `ended` event (a fake worker thread that just
  returns) brings the tab to Ended with the "stopped unexpectedly" text and the controls back; the worker wrapper
  keeps an exception from `run()` off the thread (no traceback escapes, controls back); a view rebuilt per
  run/replay (the old `RouteViewWindow` destroyed); the close handler calls `stop("window_close")` and destroys
  only after the worker ends or 10 s; no message box, Toplevel, `wm` style or icon call and no focus call during a
  run (patch `tkinter.messagebox`, `tk.Toplevel` and the `wm`/focus methods and assert none was called); the focus
  binding of section 9 (a generated `<FocusIn>` on the withdrawn root after the app had lost the keyboard calls
  `session.stop("app_focus")` once during the countdown and while walking, and not before Start or after the end;
  the `may_walk` the tab passes reads the tab's "app has the keyboard" flag and touches no Tk).
- `tests/test_anti_cheat_names.py`: the name scan of 5.12-4 (with 5.12-5's `imshow`, `namedWindow` and `waitKey`)
  over `src/**/*.py` and `tools/**/*.py`, exempting
  `src/input/harness/` and not scanning `tests/`, with a planted sample (in `tmp_path`, scanned by the same function)
  to show it can fail.
- `tests/route_view/test_view_model.py`: time-of-day why lines from `wall_ns`/`perf_ns`; elapsed when absent.
- Import contracts: the "gui reaches input only through the run driver" contract, the changed leaf contract (`src.gui`
  and `src.main` both off its sources, 5.9) and `src.route.session` in contract f's top layer are checked by
  `lint-imports` in `tools/check.py`, which must pass with `src.main -> src.gui.gui -> src.gui.route_tab ->
  src.route_view` in the graph; `tests/route_view/test_route_view_imports.py` updated to match.

Changed for the y/n question (section 6):
- Every `monkeypatch.setattr(soak, "_process_names", ...)` (test_probe 4, test_session_starts 3, test_soak 5,
  test_soak_v2_faults 1) becomes `monkeypatch.setattr(soak, "_read_answer", lambda: Success("y"))` or the case's answer.
- "MapleStory running" tests (test_soak:610, test_probe:302, test_session_starts:303) become "answer n refuses" tests
  and keep their "before connect / before the listener / before any dir" assertions; "process list failure" (test_soak:620)
  becomes "no answer (EOF) refuses".
- The re-check tests are deleted: test_soak:626 (60 s check), test_soak:756 (during the start wait), test_probe:442
  (mid-probe), test_session_starts:312 (before a start). New tests in their place: each tool calls `_read_answer`
  exactly once per run, and a full fake run sends its keys with no further call; for probe, a run of two or more
  probes with `_read_answer` patched to answer once (a second call raises) runs every probe, and each probe's
  `session.json` holds the same `process_check` text.
- test_soak:1033 (`_process_names` lists this process) is deleted; new: `_read_answer` returns `Success("y")` for input
  "y", `Failure` for a closed input (patch `builtins.input`).
- `process_check` value assertions (test_soak:258, :264, :585, :617, :769; test_probe:346, :464) take the new texts.
- `tests/input/test_guard_targets.py` (line numbers at 61455a7):
  - `PATCHED[SOAK]` (:35) lists `_read_answer` instead of `_process_names`.
  - `test_maple_check_lives_in_soak_and_reads_process_names_from_its_own_globals` (:70-73) checks `_read_answer`.
  - `test_the_other_tools_call_the_guard_through_soak` (:76-83): the loop at :78-81 runs over `session_starts` only;
    the `run_probes` assertion (:82) and the soak one (:83) stay; new assertion: `probe._check_processes`'s
    `co_names` contain neither `maple_check` nor `_read_answer` (it only records).
  - `test_every_key_sending_entry_point_calls_its_check_processes` (:86-89) is unchanged (`_run_probe` still calls
    `_check_processes`).
  - The three `maple_check` tests (:94-113) use answers (`n`, `y`, a `Failure` for no answer).
  - The parametrised reach test (:116-126) keeps only the `session_starts` case; the probe case becomes: with
    `soak._read_answer` patched to `Success("n")`, `run_probes` returns `soak.EXIT_REFUSED` (2, as 6.1; ruling
    RTP96-D1 A) and never calls `_new_link` or
    `_board_list_json`; with `Success("y")`, `probe._check_processes(writer, text)` writes `text` into
    `writer.session["process_check"]`.
  - New: `soak.py`, `probe.py` and `session_starts.py` contain no `tasklist` and no `subprocess` call that lists
    processes.
- `probe --run-sheet` text tests, if any pin `MAPLE_CHECK`, take the new line.

Removed: `tests/route_view/test_win_focus.py`, `tests/route/test_view_check.py`, the claim/ready parts of
`test_view_cli.py`, the spawn parts of `test_walk_view*.py`.

Every test drives fakes; no test connects a board, sends a key or presses Start.

## 9. Clicking the app during a run (Hong's pick, RVT94-1 B; H64)

Hong picked "Stop when our app gets the keyboard (Recommended)" (RVT94-1, option B, after asking back on RVT93-2 how
the earlier option would detect the click). What he was told it does: when Windows tells our window it now has the
keyboard, the bot stops at once, releases all keys and the tab says "Stopped: you clicked the app"; the run also does
not begin if the countdown ends while the app still has the keyboard; nothing asks Windows which window is in front.
What he was told it misses: a third program (a chat pop-up, say) taking the keyboard; the bot is not told and keeps
going, as today. The walk still does not end by itself when keys go astray in that case (a leg that sent moves and
timed out does not end the run, `walk.py:16-21`).

Where the signal comes from. Windows sends `WM_SETFOCUS`/`WM_KILLFOCUS` to our own window when it gains or loses the
keyboard; Tk turns them into `<FocusIn>`/`<FocusOut>` events (`win/tkWinX.c:1053-1100`; the `wParam` handle is only
looked up in Tk's own table, `win/tkWinWindow.c:107-119`, no Windows call). `focus_get()` returns Tk's own record of
which of our widgets has the keyboard (`tkFocus.c:127-133`, `:696-708`, Tk memory, no Windows call). Rule 5.12-1
names this as part of "Tk's own handling of its own windows".

How the tab uses it:
- The tab keeps one flag, "the app has the keyboard", a `threading.Event` that only the Tk thread sets and clears. A
  binding on the main window for all its widgets (`<FocusIn>` and `<FocusOut>`) schedules one `after_idle` re-read:
  the flag is set when `focus_get()` returns one of our widgets and cleared when it returns `None`. Focus moving
  between two of our own widgets gives a `<FocusOut>` and a `<FocusIn>` in a row; the re-read after both still sees
  the app has the keyboard, so it is not taken for a click. [inferred from Tk's source, not run: when the app loses
  the keyboard Tk clears its focus record, so `focus_get()` returns `None`; the plan's first tab step confirms this
  before building on it, and Hong's manual check below covers it on this PC.] At Start the flag is read once from
  `focus_get()`.
- When the flag rises (it was clear, the app now has the keyboard) while the run is in its countdown or walking, the
  tab calls `session.stop("app_focus")`. While walking this is a `trigger`, like the Stop button: the walker stops,
  the cleanup releases every key (its second step, 5.5), and the status line reads "Stopped: you clicked the app.
  Press Start to run again." In the countdown it is a stop before the walk (5.6, cancel payload, same text). Before
  the countdown (connecting) no key is sent yet, so the binding does nothing then; the check at the countdown's end
  covers it.
- At the end of the countdown `run()` calls the `may_walk` the tab passed (5.6), which only reads the flag
  (`not flag.is_set()`; the worker never touches Tk). If the app still has the keyboard, the run does not start:
  source `countdown_focus`, exit 0, nothing walked, and the status line reads "Not started: click the game first.
  Press Start, then click the game before the countdown ends." (5.5).
- A key or two may land in the app between the click and the stop; everything but Stop is greyed out (5.1), so they
  change nothing. [inferred]
- The CLI walk has no Tk window and gets no `may_walk`: its behaviour is unchanged.
- Tests (section 8): tab tests for the binding and the countdown refusal, and `RunSession` tests for `may_walk` and
  the `app_focus` source. Hong checks it by hand once, with no game and no board: an in-app simulator run (built as
  the 5.7 timing measurement builds it, simulator positions and link, so no key goes anywhere), where he clicks
  another ordinary window and then the app (the run stops with the "you clicked the app" text), and lets one
  countdown end with the app in front (the run does not start).

Hong's RVT94-2 pick ("Fine as it is (Recommended)", H65) keeps 5.12 rule 2 as written. 5.1's greying out of Load Map,
the class picker, the map editor and the dev buttons stays (they change the graph or open a window).

## 10. Related items outside this spec (they do not block it)

- RV80-ADJ-D1: the scale buttons sit in the replay bar, so they are hidden while live (as built,
  `src/route_view/CLAUDE.md`). Kept as built.
- His question on RVT93-4 ("Is this a risky? What's the likelihood this gets flagged by the anticheat?") goes to the
  Q&A page; the hook stays as H59 records.
- The B21 walk test and Feature C stay paused (H47).

## 11. Notes for the plan

Rough order (RVT92 section 5, adjusted): (1) `session.py` from `walk.main`, walk tests as the oracle; (2)
`StopControl.start(sigint=...)`; (3) y/n question in the key-test tools and its tests (independent; can go first);
(4) `RouteViewWindow`/`LiveController` on a parent Frame, focus/claim code removed; (5) Notebook and Setup tab;
(6) Route tab: settings, Start checks, worker, event queue, close handler, the focus flag and `may_walk` of section 9
(first confirm the inferred `focus_get()` behaviour there); (7) live background from `SharedInfo`, time of day; (8)
saved-run list; (9) contracts, anti-cheat name test, removals, and the capture loop's `imshow`/`waitKey` pair becoming
one `time.sleep` (5.12-5); (10) docs and safety text (section 6.3);
(11) the in-app timing measurement (5.7; who runs it waits for Hong: RTP96-RB-D1, see 6.3); (12) the recording size measurement: record frames for a timed stretch
with no keys sent (the recorder over `SharedInfo` with the game's minimap visible, or a simulator run with recording
on), compute MB per hour from the `frames/` folder size, and set Start's free-space refusal (2 GB) and the recorder's
floor (1 GB) from it (5.11), with the measured rate replacing "about 0.5 GB per hour" in the tab's Recording line.
Hong then does section 9's manual check (simulator run, no game, no board), then checks an in-app 1-lap walk from
Start (F12, Stop and a click on the app) and a replay; agents never press Start.
