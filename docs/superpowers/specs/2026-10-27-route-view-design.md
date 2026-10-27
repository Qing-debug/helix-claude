# Route view: see what the route walker plans and why (design spec)

*Draft, 2026-10-27 (unit RV1). Belongs to route walking (Feature B); branch `p1-route-view`, cut from `p1b-route-walking`
at 5d1176a. Every claim about today's code cites `file:line` at 5d1176a. Choices Hong would see, and choices that
touch a frozen file or a module boundary, are open decisions in section 8; nothing in them is decided here.*

## 1. Summary

Hong asked, during the first in-game walk test: "I need to see the rotation nodes on the map. I need to see the next
node he's going to and the route that he thinks he will take and what skills/movement abilities he'll use to get
there." The route view is a window of its own that shows, on an enlarged copy of the live minimap:

1. the rotation (blue) nodes numbered in rotation order, with the next target highlighted;
2. the route the bot expects to take to that target, as a line through the in-between nodes;
3. on each hop of that line, the move it plans (walk, teleport, drop) and the keys that move presses;
4. a "why it chose" log: where it thinks the character is, the move it just sent, and why it planned again.

It works live while a walk runs and as a replay of a saved run (step or scrub through it), including old runs that
saved no minimap frames and simulator runs.

Recommended shape (each part is an open decision in section 8):

- The walker reports each step (position read, move planned, move sent, settle, leg end) to an optional observer.
  The walk process writes those steps to a new file, `steps.jsonl`, in the run folder. Nothing else in the walk
  changes, and with no observer the walker behaves exactly as today.
- The window is a separate program (its own process) that follows the run folder while the walk runs and replays it
  afterwards. It recomputes the expected route from each logged position with the planner's own functions, so the
  planner is not changed.
- The live minimap image comes from the frames the walk already records with `--record`.

## 2. What Hong decided (binding)

From `ledger-RV.md`, `Decision (Hong): route view` (QB100-QB103):

- Its own "Route view" window: not the main Helix window, nothing drawn on the game. The live minimap enlarged, like
  the map editor.
- Live while a walk runs, and replay of a saved run afterwards (step or scrub through it).
- It shows (1) the rotation nodes numbered in rotation order, the next target highlighted; (2) the planned route as a
  line through the in-between nodes; (3) on each hop, the move (walk / teleport / jump / drop) and its keys; (4) a
  "why it chose" text log: position, move sent, why it re-planned.
- Built now; the B21 walk test and Feature C (runes) are paused meanwhile.

Brief constraints (RV1): the view must not change what the bot does (route tests and the sim smoke stay
byte-identical) or slow the walker's loop or the vision thread; frozen files bind. Hong's standing facts
(`docs/decisions.md:9-13`) apply, in particular "Fix real problems, not ones we don't know will happen. Make checks
only as strict as they need to be."

## 3. What the code does today

### 3.1 Where the walk runs, and where a Tk window can live

The supervised walk CLI (`python -m src.route.walk`) is one process. Its threads:

| Thread | What it does | Evidence |
|---|---|---|
| main | parses args, builds everything, then sits in `walker.run()` for the whole run | `src/route/walk.py:206`, `:317` |
| `minimap-capture` | the vision loop; publishes each minimap frame into `SharedFrame` | `src/route/runtime.py:278-284`, `src/vision/vision.py:80-81` |
| `stop-hook` | the F12 low-level keyboard hook | `src/route/runtime.py:184-213` |
| `stop-control` | waits for a trigger and calls `walker.stop_now` | `src/route/runtime.py:77-78`, `:108-113` |
| `record` (with `--record`) | saves minimap frames at 10 fps into `frames/` | `src/route/walk.py:75`, `:198-201`, `:303-307` |
| `walk-timer` (with `--minutes`) | triggers the stop when time is up | `src/route/walk.py:308-312` |
| `route-stop-guard` (after a stop) | sends `release_all` every 50 ms until `run` ends | `src/route/walker.py:53`, `:223-239` |

Consequences for a Tk window:

- The main thread is busy in `walker.run()` (`walk.py:317`) until the run ends, so it cannot also run a Tk
  `mainloop`. Moving the walker off the main thread is not an option to take lightly: `StopControl.start` installs the
  Ctrl+C handler with `signal.signal`, which works only on the main thread (`runtime.py:71-74`), and Python runs
  signal handlers on the main thread, which a Tk `mainloop` would block between events.
- Tk itself does not need the main thread. Python's docs, "Threading model"
  (https://docs.python.org/3/library/tkinter.html): "Each `Tk` object created by `tkinter` contains a Tcl
  interpreter. It also keeps track of which thread created that interpreter. Calls to `tkinter` can be made from any
  Python thread. Internally, if a call comes from a thread other than the one that created the `Tk` object, an event
  is posted to the interpreter's event queue". So a window can live either on a new thread of the walk process (it
  then shares the walk's GIL) or in a separate process (it shares nothing). The venv's Tk is 8.6 (`tkinter.TclVersion`
  printed `8.6` on the dev PC); whether that build is thread-aware was not checked, and matters only for the
  in-process option.
- The vision thread already opens an OpenCV window named "Minimap" (`vision.py:83-84`: `cv2.imshow`,
  `cv2.waitKey`), so the walk already has one window of its own besides the console.

### 3.2 Does the planner compute a whole route, or only the next move?

Only the next move is returned, but the whole route is computed inside it and thrown away.

- `next_move` (`src/route/planner.py:184-247`) anchors the position (`attach`, `:193`), keeps only the up/down edges
  that have a launch point (`_offered`, `:204`), costs each candidate start (the node the character stands on, or both
  ends of its segment, `:206-214`), and takes the cheapest start's minimum-cost path to the target with
  `rg.path(start, target)` (`:215-218`), which is `networkx.shortest_path(..., weight="cost_s")`
  (`src/route/route_graph.py:142-150`). From that path it builds one move (rules P4 and P5, `:220-247`) and returns
  `Decision(move, cost_to_go_s, toward)` (`:35-39`). The path itself is not returned; the module docstring says "It
  plans one move at a time; the walker re-plans from the measured position after each settle" (`planner.py:4-5`).
- `next_move` draws from its random generator only to choose between teleporting down and dropping when the two cost
  about the same (`planner.py:168-181`, `:188`). Calling it a second time on the walker's generator would change the
  walk; a recomputation must use its own generator or none.
- Moves are `walk`, `teleport` and `drop` only (`MoveKind`, `planner.py:22-25`). The planner never plans a jump, so
  the view will never show one, although the input layer can send one (`input_layer.py:67-68`, `actions.py:105-106`).

How the view gets the expected route without changing the planner: it recomputes it, outside the walker, from the
logged position and target with the planner's own functions and the same route graph (section 5.3, decision
RV1-D4). The graph is rebuilt from the same map file and the same movement parameters, which the run already logs
(`walk.py:287-299`: `params`, `map`, `map_sha256`, `git_head`, `class`). Two checks make the recomputation honest: the
recomputed cost to go must equal the logged `cost_to_go_s` and the recomputed first move must equal the logged move
(section 7, checks S3).

### 3.3 Keys each move presses

`moves.execute` makes exactly one input-layer call per move (`src/route/moves.py:16-20`); the action builders choose
the keys (`src/input/actions.py`):

| Move | Layer call | Keys | Evidence |
|---|---|---|---|
| walk left/right | `walk(direction, walk_ms)` | hold Left or Right for a drawn time around `walk_ms` | `actions.py:117-129` |
| teleport left/right/up/down | `teleport(direction)` | hold the arrow, press E while it is held (a chord) | `actions.py:83-98`, `:109-110` |
| drop | `drop()` | hold Down, press Alt while it is held | `actions.py:113-114` |
| (jump, never planned) | `jump()` | hold Alt | `actions.py:105-106` |

A planned walk is `min(1000 * distance / walk_px_per_s, max_walk_ms)` ms (`planner.py:86-88`); a longer distance
becomes several walks, because the walker plans again after each one. The schedule the board actually plays is in the
`ActionRecord` the layer returns (`input_layer.py:41-46`: `plan.steps`, `reaction`, `waited_ms`); each wire `Step` is
a delay and the full key mask after it, one key changing per step (`src/input/protocol.py:64-67`, `:108-122`).

### 3.4 What a run folder saves today, and what a replay needs

The walk CLI writes into `--out` (default `data/route/<UTC stamp>`, `walk.py:229-230`):

| File | Written | Holds | Evidence |
|---|---|---|---|
| `session.json` | once, before the countdown | seed, movement params, leg timeout, map path and sha256, git head, start time, class, laps, minutes, record flag | `walk.py:287-299` |
| `legs.jsonl` | one line per leg, **when the leg ends** | lap, leg, from, to, start/end, outcome, misses, off-graph count, stalls, and per move: `t_ns`, `pos` (planned from), `kind`, `dir`, `walk_ms`, `cost_to_go_s`, `input`, `settled` | `leg_log.py:96-123`, `walker.py:171-187`, `:192-204` |
| `laps.jsonl` | one line per completed lap | lap, start/end, duration, legs, all reached | `leg_log.py:126-135`, `walker.py:263-264` |
| `stop.json` | at the end | reason, trigger time and source | `walk.py:355-360` |
| `frames/` (only with `--record`) | 10 frames a second | `frame_%06d.png` and `index.jsonl` lines `{"i", "t_ns", "file", "w", "h"}`; each PNG is written before its index line | `record_minimap.py:8-10`, `:95-99` |

The first walk test's folder (`data/route/b21-test1`, gitignored) has `session.json`, `legs.jsonl` (8 legs),
`laps.jsonl` and `stop.json`, and no frames (`"record": false`).

Frame times and move times are on the same clock: `MoveEntry.t_ns` is `RealClock.now_ns`, which is
`time.perf_counter_ns` (`walker.py:67-71`, `:142`, `:172`), and a frame's `t_ns` is `time.perf_counter_ns` too
(`record_minimap.py:16-17`). A frame can therefore be matched to a step by time.

What a replay additionally needs, and why `legs.jsonl` is not enough:

- **Live**: `legs.jsonl` is written only when a leg ends (`walker.py:259`, `JsonlLegLog` writes per record), up to the
  60 s leg timeout later (`walk.py:73`). A live view needs each step as it happens.
- **Why**: the leg record keeps only counts of misses, off-graph positions and stalls (`leg_log.py:60-62`), not when
  they happened or the planner's message ("off-graph: (x, y)" or "no path ...", `locate.py:94`, `planner.py:213`);
  it keeps no `toward` node (`planner.py:39` is dropped at `walker.py:171-180`) and not the keys or reaction wait that
  were actually sent.
- **Image**: without `--record` there is no minimap image at all.

Hence a new per-step log, `steps.jsonl` (section 5.2), plus one minimap snapshot per run (section 5.5).

### 3.5 Traps found on the way

- The map editor's redraw loop resizes the frame and builds the `PhotoImage` while it holds `minimap_frame_lock`
  (`map_editor.py:1069-1087`), every 10 ms (`:1088`). `src/gui/CLAUDE.md:9` says to take the frame under the lock and
  release it before any Tk call. The route view must not copy the editor's loop.
- The vision thread publishes a new array for every frame and never changes one it published (`vision.py:80-81`;
  `position.py:12-13`; `record_minimap.py:116-126` relies on it), so a reader may take the reference under the lock and
  use it after releasing the lock without copying.
- The simulator's position source draws from its random generator on every `read` (`sim/link.py:129-130`): an
  observer must never call `read` itself. The simulator's clock has no side effects on `now_ns` (`sim/link.py:33-34`),
  so reading it is harmless.
- `walk.py`'s re-run guard lists the files a run writes (`_OUT_FILES`, `walk.py:81`, decision B19-D2): new run-folder
  files must be added to it.
- Keys go to whichever window has focus (root `CLAUDE.md`, safety rules). A view window that takes focus during a walk
  takes the bot's keys with it (section 5.7).

## 4. Goals and non-goals

Goals: the four things in section 1, live and in replay; no change to what the bot does; no measurable slow-down of
the walker or the vision thread; usable without the game (simulator runs) for development and tests.

Non-goals: drawing on the game; changing the planner, the walker's decisions or the input; controlling the walk from
the view (F12 stays the stop); showing the raw route graph or terrain inference (possible later); the rune feature;
the implementation plan (unit RV3).

## 5. Design

### 5.1 Overview

```
 walk process (unchanged threads)                        route view process (new)
 ------------------------------------                    -------------------------------------------
 main: walker.run()                                      Tk main thread
   walk_leg loop ── observer.event(...) ──┐                every 100 ms: read new lines of steps.jsonl
                                          │ SimpleQueue     and frames/index.jsonl, redraw
 step-log writer thread  <────────────────┘                route preview: rebuild the route graph from
   json line -> steps.jsonl (flush)                         map.csv + params, recompute the route for
 record thread (existing, --record)                         the newest step (planner functions, no rng)
   frame_%06d.png + index.jsonl  ──────── files ────────>
 minimap-capture (existing, unchanged)
```

The two processes share only files in the run folder. The same viewer code replays a finished folder.

### 5.2 Walk side: step events and `steps.jsonl`

The walker gets an optional observer (decision RV1-D3). It is called at the points of `walk_leg` below, with values the
walker already has; it never reads the position source, the clock's `sleep` or the random generator. With no observer
(the default, and the simulator's smoke run) nothing is called.

| Event | Where in `walk_leg` | Fields |
|---|---|---|
| `leg` | after `start_ns` (`walker.py:123`) | `t_ns`, `lap`, `leg`, `from`, `to` |
| `read_failed` | a failed read (`walker.py:143-147`) | `t_ns`, `why` (`"stale"` or the source's message) |
| `no_plan` | `next_move` failed (`walker.py:151-154`) | `t_ns`, `pos`, `why` (the planner's message) |
| `reached` | `move is None` (`walker.py:157-159`) | `t_ns`, `pos` |
| `stall` | a stall counted (`walker.py:137-139`) | `t_ns`, `best_cost_s` |
| `planned` | after the stop check, before `execute` (`walker.py:165-168`) | `t_ns` (= `read_ns`), `pos`, `kind`, `dir`, `walk_ms`, `cost_to_go_s`, `toward` |
| `sent` | after `execute` returns (`walker.py:169-170`) | `t_ns`, `input` (`"ok"` or the error name), `reaction_ms`, `waited_ms`, `keys` (`[[delta_100us, mask], ...]` from `plan.steps`) |
| `settled` | after `_settle` (`walker.py:186-187`) | `t_ns`, `pos` (or null), `stopped` |
| `leg_end` | before the return (`walker.py:192-204`) | `t_ns`, `outcome`, `misses`, `off_graph`, `stalls` |

The `planned` fields use the leg log's names (`kind`, `dir`, `walk_ms`, `cost_to_go_s`, `leg_log.py:96-106`) so the
two files read alike. `legs.jsonl` and its keys (Feature D's contract, `tests/route/test_leg_log.py:26-29`) do not
change.

`steps.jsonl` is one JSON object per line, written in the run folder. Its first line describes the run, so a viewer
needs no other file except the map:

```json
{"ev": "run", "version": 1, "source": "walk", "clock": "perf_counter_ns", "map": "map.csv", "map_sha256": "...",
 "class": "kanna", "params": {...MovementParams...}, "rotation": [[113, 60], ...], "git_head": "..."}
{"ev": "leg", "t_ns": 44462799439300, "lap": 0, "leg": 0, "from": null, "to": [113, 60]}
{"ev": "planned", "t_ns": 44469522743700, "pos": [62, 58], "kind": "teleport", "dir": "RIGHT", "walk_ms": null,
 "cost_to_go_s": 3.725, "toward": [81, 58]}
{"ev": "sent", "t_ns": 44469771000000, "input": "ok", "reaction_ms": 231.0, "waited_ms": 228.4, "keys": [[0, 2], ...]}
{"ev": "settled", "t_ns": 44470260000000, "pos": [82, 58], "stopped": false}
```

(The `planned` values above are the first move of `b21-test1`; the `toward`, `sent` and `settled` times are
illustrative.) For the simulator, `source` is `"sim"` and `clock` is `"sim_ns"` (virtual time).

Writing (walk process):

- The observer turns each event into a tuple of plain values and puts it on a `queue.SimpleQueue`; `put` "never
  blocks" (its docstring, printed from the venv's Python 3.14.0). That is all the walker thread does per event.
- A daemon thread, `step-log`, takes events off the queue, encodes them and appends them to `steps.jsonl` with one
  `write` and a `flush` per line, no `fsync` (a crash loses at most the last lines; this is a debugging aid, unlike
  the leg log, which syncs, `leg_log.py:197-198`). At the end of the run the cleanup puts a sentinel, joins the thread
  for at most 2 s, then syncs and closes the file. Its cleanup step goes after `release_all` in `walk.py`'s cleanup
  order (`walk.py:342-367`), so a slow disk never delays releasing the keys.
- The walk also copies the map file it loaded into the run folder as `map.csv` (so a later edit to `maps/` cannot
  change a replay) and saves one minimap snapshot, `minimap.png`, from the first frame it already waits for
  (`walk.py:255-262`). `steps.jsonl`, `map.csv` and `minimap.png` are added to `_OUT_FILES` (`walk.py:81`).
- The simulator writes `steps.jsonl` and `map.csv` too, but only when `--out` is given (`sim/run.py:291-297`, `:394`);
  the smoke command has no `--out`, so its output cannot change. Only the run's own walker gets the observer, not the
  replay walkers of the recovery metric (`sim/run.py:188-198`).

An observer bug must not end a walk: the walker calls it inside a guard that catches `Exception`, prints one line to
stderr the first time and stops calling it for the rest of the run (decision RV1-D3 covers this).

### 5.3 The expected route and the move on each hop (route preview)

A new pure function computes, for a logged position and target, what the planner would do (decision RV1-D4):

1. **Start and path**: the same steps as `next_move` P1-P3 (`planner.py:190-218`): `attach`, `_offered`,
   `_on_slope_segment`, `_starts`, `cost_to` per start, cheapest start (ties left before right), `rg.path`. It also
   keeps every candidate start with its cost, for the "why" log ("start at #3: 3.73 s; via #2 it would be 4.10 s").
2. **First move**: the same P4/P5 rules (`planner.py:220-247`), with no random draw: where `next_move` would toss a
   coin between teleporting down and dropping (`planner.py:171-178`), the preview returns both options. In a live or
   recorded step the first move is not predicted at all: the logged `planned` event is drawn, marked "sent".
3. **Later hops**: the path is split into hops the way the planner walks it, each assuming the move before landed
   where it aimed:
   - a run of level (WALK) edges or a slope (SLOPE) edge: the planner heads for the end of the run
     (`_walk_run_end`, `planner.py:100-104`) and, off a slope, teleports while one teleport plus the remaining walk
     beats walking (`_run`, `planner.py:91-97`); the preview repeats that rule on the distance left, giving for
     example "teleport right (Right+E) x2, then walk right about 1.4 s (hold Right)". A walk longer than `max_walk_ms`
     (`params.py:20`) shows as several walks;
   - an up or down edge: first the walk or teleports to its launch x (`_launch_x`, `planner.py:135-152`), then
     "teleport up (Up+E)", or for down the options of `down_options` with the coin-flip band (`planner.py:168-181`):
     "drop (Down+Alt)", "teleport down (Down+E)", or "drop or teleport down, 50/50".
4. **Keys** for each hop come from the table in section 3.3; for the sent move they come from the logged schedule.

The preview runs in the viewer process only, once per displayed step (a few Dijkstra searches on a graph of about 20
nodes for FF2: `tests/fixtures/fossil_foundry_2.csv` has 21 node rows), never on the walker's thread.

The viewer rebuilds the route graph from `map.csv` and the logged params the way the walk does (`walk.py:164-178`:
`MapleAgent.loadMap`, `RouteGraph.build(pathGraph, MovementParams(...))`). If the map's sha256 or the git head differs
from the run's, the view still draws and shows a red line at the top: "map changed since this run: the route shown may
not be the one the bot used" (or "code changed since this run"). Per Hong's standing fact, this is a warning, not a
refusal.

### 5.4 The window

```
+----------------------------------------------------------------------------------------+
| Route view · LIVE · data/route/2026-10-28T101500Z · lap 2 · leg 3 -> #4 · walking       |
+---------------------------------------------+------------------------------------------+
|                                             | Target  #4 (101, 24)                     |
|   enlarged minimap                          | Position (62, 58), on #2-#3              |
|    1..n  blue nodes numbered, rotation order| Route   you -> #3 -> o7 -> #4   3.7 s    |
|    #4    next target: yellow ring           | Hops                                     |
|    ----  route line, hops numbered 1, 2, 3  |  1 teleport right   Right+E      SENT    |
|    *     character (planned-from position)  |  2 walk right 1.4 s hold Right           |
|    x     where the move aims                |  3 teleport up      Up+E                 |
|    ...   last 5 moves, faint                |                                          |
+---------------------------------------------+------------------------------------------+
| Why log (newest at the bottom; replay: the line of the current step is selected)       |
| 10:15:03.512 lap 2 leg 3: target #4 (101, 24)                                          |
| 10:15:03.530 at (62, 58) on #2-#3; start #3 (3.73 s; via #2 4.10 s); route #3 o7 #4     |
| 10:15:03.531 plan: teleport right toward o7 (aim x 81)                                 |
| 10:15:03.760 sent: Right down, E down +31 ms, E up +112 ms, Right up +140 ms (waited 228 ms)|
| 10:15:04.250 settled (82, 58): 1 px past the aim; plan again from there                 |
| 10:15:04.300 no character on the minimap (miss) x3; waiting                             |
+----------------------------------------------------------------------------------------+
| Replay only:  |< <  step  > >|  [==========o==============]  1x 4x  leg [3]  lap [2]     |
+----------------------------------------------------------------------------------------+
```

Minimap panel: the newest frame (live) or the frame nearest before the step (replay), enlarged with nearest-neighbour
scaling so pixels stay sharp, as the editor does (`map_editor.py:1073-1077`); the scale is decision RV1-D8. Blue nodes
use the editor's colours (`map_editor.py:1029-1030`) and are numbered 1..n in rotation order, which is the blue nodes'
file order (root `CLAUDE.md`, "Map CSVs"; `controller.py:94`). Orange nodes are small dots without numbers, labelled
`o<k>` in the side panel only.

"Why" lines, one per event, in plain words:

| Event | Line |
|---|---|
| `leg` | "lap L leg K: target #n (x, y)" |
| `planned` | "at (x, y) on <node or segment>; start <node> (<cost>; other start <cost>); route <nodes>" and "plan: <move> toward <node> (aim x <x>)" |
| `sent` | the keys in order with their times, the reaction wait; or "send failed: <error>" |
| `settled` | "settled (x, y): <d> px from the aim; plan again from there"; "did not settle within 2 s" when null |
| `read_failed` | "no character on the minimap (<why>)" or "no new frame yet", repeats collapsed ("x3") |
| `no_plan` | "cannot plan from (x, y): <planner message>" |
| `stall` | "no progress for 10 s (best cost to go <c> s): stall counted" |
| `reached` | "reached #n" |
| `leg_end` | "leg ended: <outcome> (<misses> misses, <off_graph> off-graph, <stalls> stalls)" |
| any re-plan whose route differs from the last prediction | "route changed: expected next <node>, now <node> (landed <d> px short/past)" |

"Why it re-planned" has one structural answer: the walker always plans again after every settle and after every
failed read or failed plan (`walker.py:1-7`, `:129-190`). The log says which of those it was and whether the new
plan differs from what the previous one predicted.

Live mode follows the folder: every 100 ms it reads new complete lines of `steps.jsonl` and `frames/index.jsonl` (a
line without its newline is left for the next poll) and redraws only when something changed. Replay mode loads the
folder, then steps by event, by move or by leg, scrubs with a slider, and plays at 1x or 4x on the logged times.

### 5.5 Replay sources, best first

| Folder has | Background | Steps |
|---|---|---|
| `steps.jsonl` + `frames/` | the recorded frame nearest before each step (same clock, section 3.4) | every event |
| `steps.jsonl`, no frames | `minimap.png`, the snapshot from the start of the run; the character is drawn from the logged positions | every event |
| `legs.jsonl` only (runs before this feature, e.g. `b21-test1`) | a dark canvas of the map's `# minimap_size=` (FF2: `162,82`, `tests/fixtures/fossil_foundry_2.csv:1`), or a snapshot if one exists | one `planned` + `settled` pair per `MoveEntry`, leg starts and ends from the leg record; no keys timing, no miss times; the expected route recomputed as in 5.3 |
| simulator `--out` folder | dark canvas of `# minimap_size=` | every event, virtual times |

The legs-only reader is what lets Hong look at `b21-test1` today. Its map comes from `session.json`'s `map` path,
checked against `map_sha256`; a mismatch gives the warning of section 5.3.

### 5.6 Threads, locks, and why the walk is not slowed

With the recommended design (separate process, RV1-D1 A):

- **Walker thread**: per event, one tuple and one `SimpleQueue.put` (never blocks). Events come at most about every
  50 ms (the poll interval, `params.py:24`) while the character is lost, and a handful per move otherwise. It takes
  no lock that the vision thread takes.
- **Vision thread**: unchanged. The walk adds no reader of `minimap_frame` except the one-off snapshot at the start,
  which takes the reference under `minimap_frame_lock`, releases the lock, then encodes the PNG (section 3.5).
- **New `step-log` thread**: JSON-encodes and writes a few short lines a second. It shares the walk's GIL; its work
  is small next to the existing `record` thread's PNG encoding at 10 fps (`walk.py:75`), which is already accepted.
- **Viewer**: its own process, its own GIL and Tk. It reads files only; no lock or memory is shared.
- **Simulator**: the observer reads values the walker already holds and the simulator clock without side effects
  (`sim/link.py:33-34`), so virtual time, random draws and every result are unchanged.

With the in-process alternative (RV1-D1 B): a Tk thread in the walk process takes the frame reference under
`minimap_frame_lock`, releases it, then scales and draws (never under the lock, unlike `map_editor.py:1069-1087`),
and takes events from the same queue. Its redraws (scaling, `PhotoImage`, canvas items) share the GIL with the walker
and the vision thread, so the walk can only be shown to be slowed little, by measurement, not by construction.

### 5.7 Focus safety (the view must not take the bot's keys)

The board's keys go to whichever window has focus. Rules for the view during a live run:

- The viewer starts before the walk's 5 s countdown (`walk.py:300-301`), so Hong focuses the game after it opened.
- It never calls `focus_force`, `lift`, `-topmost`, or opens a message box or dialog while following a live run;
  errors go to its status line.
- Clicking it during a live run would move focus to it and the bot's next moves would go nowhere. Decision RV1-D6 is
  between a warning only and making the live window one that clicks do not activate. Microsoft's docs for
  `WS_EX_NOACTIVATE` (https://learn.microsoft.com/en-us/windows/win32/winmsg/extended-window-styles): "A top-level
  window created with this style does not become the foreground window when the user clicks it." The docs speak of a
  window *created* with the style; whether setting it on an existing Tk window has the same effect is untested and
  needs a no-key check (open the window, click it, read the foreground window).

### 5.8 Where the code lives

Recommended (decision RV1-D5):

- `src/route/step_log.py` (new): the observer, the event tuples, the writer thread and a reader of `steps.jsonl` and
  of the legs-only fallback. Standard library only, so it fits plan hard rule 6 (the route import allowlist,
  `docs/superpowers/plans/2026-10-23-p1b-route-walking.md:62-65`, `tests/route/test_import_rules.py:64-69`).
- `src/route/route_preview.py` (new): the pure preview of section 5.3. Imports `planner`, `locate`, `route_graph`.
- `src/route_view/` (new top-level package, snake_case): the Tk window, live follower and replay. It may import
  `src.route`, `src.controller`, `src.classes`, `tkinter`, Pillow (already pinned, `requirements.txt:6`) and `cv2`; a
  new import-linter contract forbids it `src.gui`, `src.main`, `src.vision` and `src.input` beyond `protocol`, and
  nothing in `src` imports it. `walk.py` starts it as a child process by module name (`python -m src.route_view`),
  so the walk imports none of it.

## 6. What must not change, and what does

Must not change: the planner, moves, route graph, terrain, locate and params (frozen, `src/route/CLAUDE.md:23`) and
their tests; the walker's behaviour with no observer; `legs.jsonl` / `laps.jsonl` and their pinned keys; `walk.py`'s
exit codes and cleanup order apart from the added step; the stop guard (`walker.py:53`, `:223-239`).

Changes (recommended options): `src/route/walker.py` gets the optional observer (frozen file: RV1-D3);
`src/route/walk.py` gets `--view` (RV1-D7), the observer and writer wiring, the snapshot, the `map.csv` copy, the new
`_OUT_FILES` and one cleanup step; `src/route/sim/run.py` writes `steps.jsonl` and `map.csv` with `--out`; new
`src/route/step_log.py`, `src/route/route_preview.py`, `src/route_view/`; `pyproject.toml` (one contract);
`src/route/CLAUDE.md` (the new seam on the planner's private helpers, the observer). The plan (RV3) splits this into
tasks of at most about three code files each (plan hard rule 7).

## 7. Acceptance checks: what "working" means

Baseline measured for this spec at 5d1176a: `python -m src.route.sim.run --laps 20 --seed 1
--min-single-error-legs 0` prints six PASS lines (20/20 laps, 141/141 legs reached, 379 moves) and its stdout has
sha256 `d982184e54311493311110657d93992fe7f0af4963dc603617c5a0f4d09d8035`.

Simulator-only checks (no game, no board; an agent runs them):

- **S1 behaviour unchanged.** The sim smoke's stdout has the sha256 above; `python -m pytest tests/route -q` passes
  with the frozen test files byte-identical to 5d1176a; the 1000-lap target run (`src/route/CLAUDE.md:38`) prints the
  same lines as before.
- **S2 the observer changes nothing.** Two simulator runs with the same seed, one with a recording observer and one
  without, give identical `LegRecord`s, `LapRecord`s and report, and the generators end in the same state.
- **S3 the preview agrees with the planner.** Over every `planned` event of a 20-lap sim run: the preview's cost to go
  equals the logged `cost_to_go_s` exactly, its `toward` equals the logged one, and its first move equals the logged
  kind, direction and `walk_ms` (for a coin-flip down move, the logged choice is one of the two options).
- **S4 the step log matches the leg log.** In a sim `--out` folder, the `planned`/`sent`/`settled` events map one to
  one onto the `MoveEntry`s of `legs.jsonl` (same `t_ns`, `pos`, `kind`, `dir`, `walk_ms`, `cost_to_go_s`, `input`,
  `settled`); every `leg` has a `leg_end` with the leg record's outcome and counts; the first line is a valid `run`
  header.
- **S5 the view model.** A pure (no Tk) model built from a sim folder at chosen steps gives: blue nodes numbered 1..n
  in file order, the right target highlighted per leg, the route polyline's points, the hop labels and keys, and the
  "why" lines; pinned by golden text for a handful of steps, including a coin-flip down, a teleport run and an
  off-graph read (from a hand-written `steps.jsonl`).
- **S6 the window opens.** As `tests/controller/test_gui_startup.py` does (real, withdrawn Tk root, no mainloop,
  skips if Tk cannot start), the view loads a sim folder and a legs-only folder and draws step k without error.
- **S7 following a live folder.** A test thread appends `steps.jsonl` lines (including half a line) and frame index
  lines while the follower polls: the view shows the newest complete step and frame, never a half line.
- **S8 cost on the walker thread.** In a sim run, the walker thread's time per event with the observer is under
  20 µs on the dev PC (median of a 20-lap run); the writer's queue is empty within 1 s of the run's end.
- **S9 rules.** `python tools/check.py` passes (ruff, basedpyright with its baseline, import-linter with the new
  contract, tests); the route import allowlist test still passes.

Checks with Hong (need the game; never run by an agent without his go, root `CLAUDE.md` safety rules):

- **L1** A supervised 1-lap walk with `--view` (and `--record` if RV1-D2 A): the window shows the nodes numbered, the
  target ring moves leg by leg, the route line and hop moves appear before each move is sent, the "why" log reads
  sensibly; F12 still stops the walk; closing the view does not stop the walk.
- **L2** The walk's results are as without the view (same exit code path, legs reached), judged on that run only.
- **L3** Replay of the L1 folder and of `b21-test1` steps and scrubs to any move.
- **L4** Focus: after the countdown, with the view open on the other monitor, the game keeps focus for the whole run.

## 8. Open decisions

"Who" says who should decide: Hong for what he sees or does, the orchestrator for code structure (it may still send
those to Hong).

```
DECISION NEEDED: RV1-D1 (Hong)
Question: Should the route view run as its own program next to the walk, or inside the walk program?
Why it matters: decides whether the view can slow the walk at all, how live and replay share code, and how fresh the
  live picture is.
Options:
  A. Own program (process) that follows the run folder — cost: live picture lags about 0.1-0.3 s (file polling);
     the walk cannot be slowed by drawing; one code path for live and replay; easy to reverse.
  B. A window thread inside the walk process reading the shared frame — cost: freshest picture; the view's drawing
     shares the walk's Python lock (GIL), so a slow-down can only be measured, not ruled out; a Tk failure happens
     inside the walk; replay still needs the file reader of A; moderate to reverse.
Recommendation: A — the brief's "must not slow the walker or the vision thread" holds by construction.
Paused meanwhile: the plan (RV3) · Continued: everything else in this spec works with either option.
```

```
DECISION NEEDED: RV1-D2 (Hong)
Question: In the separate-program design, where does the live minimap picture come from?
Why it matters: the view needs the live minimap, and the separate program cannot read the walk's memory.
Options:
  A. Reuse the existing frame recorder: --view also turns on --record (10 frames a second saved as PNGs) — cost: no
     new capture code; disk use about 150 KB/s by estimate (a 162x82 minimap PNG of roughly 15 KB, not measured),
     about 0.5 GB per hour; replays get frames for free.
  B. The walk writes only the newest frame into a small shared-memory block the view reads — cost: no disk growth;
     new cross-process code with its own tests; replay has no frames unless --record is also on.
  C. The view captures the screen itself — cost: a second full capture loop with its own minimap search, on the same
     GPU output as the walk's; whether two captures interfere is unknown (untested).
Recommendation: A — tested code, and supervised runs are minutes long today.
Paused meanwhile: frame handling in the plan · Continued: the snapshot and legs-only replay.
```

```
DECISION NEEDED: RV1-D3 (orchestrator; touches a frozen file)
Question: How should the walker's steps reach the step log?
Why it matters: the walker (src/route/walker.py) is frozen (src/route/CLAUDE.md:23); the step log's accuracy depends
  on where the events come from.
Options:
  A. An optional observer argument to RouteWalker, called at the nine points of section 5.2, inside a guard that
     disables it after its first exception; default None — cost: about 30 lines in a frozen file plus tests; exact
     events (the planner's messages, stalls, toward, the sent schedule); behaviour with None provably unchanged
     (checks S1, S2); reversible (remove the argument).
  B. No walker change: wrap the position source, the input layer (a subclass of InputLayer) and the leg sink in
     walk.py and sim/run.py, and infer the steps from the order of their calls (the planning read is the last
     successful read before a layer call, walker.py:141-169; the target from the leg count) — cost: no frozen edit;
     stalls and off-graph messages must be recomputed by the viewer; silently wrong if the walker's call order ever
     changes; more code.
Recommendation: A — exact data for the "why" log, small and behaviour-neutral when unused.
Paused meanwhile: the walk-side tasks of the plan · Continued: the step-log format and the viewer design.
```

```
DECISION NEEDED: RV1-D4 (orchestrator; touches a seam of a frozen file)
Question: How is the expected route computed without changing the planner?
Why it matters: the planner returns only the next move (planner.py:184-247); the route line and hop moves must match
  what it would do.
Options:
  A. A new pure module, src/route/route_preview.py, that calls the planner's private helpers (_offered, _starts,
     _on_slope_segment, _walk_run_end, _launch_x, _run, _walk, _is_at) the way next_move does, pinned by the
     agreement check S3 — cost: the planner's private names become a seam (like terrain.py's use of
     PathGraph._isVerticalEdgeValid), noted in src/route/CLAUDE.md; no frozen edit.
  B. Add a public function to planner.py that returns the start, path and candidates (next_move unchanged) — cost: a
     frozen-file edit; cleaner seam.
  C. Copy the planner's rules into the preview — cost: no seam, but the copy drifts silently if the planner changes;
     S3 would catch drift only in the simulator.
Recommendation: A — no frozen edit, and S3 fails loudly if the two ever disagree.
Paused meanwhile: the preview task · Continued: everything else.
```

```
DECISION NEEDED: RV1-D5 (orchestrator; module boundaries)
Question: Where does the viewer's code live?
Why it matters: import rules (plan hard rule 6, import-linter contracts in pyproject.toml) limit what src/route and
  src/gui may import.
Options:
  A. Pure parts in src/route (step_log.py, route_preview.py, both within rule 6); the Tk app in a new top-level package
     src/route_view with its own import-linter contract — cost: one new package and contract; free to use Pillow.
  B. Everything under src/route/view/ — cost: rule 6 forbids Pillow there, so images go through Tk's own PPM/PNG
     loading; tkinter (standard library) is allowed; no new top-level package.
  C. In src/gui, opened from the main Helix window — cost: Hong asked for its own window, not the main Helix window;
     src/gui importing more of src.route is a module-boundary change (src/gui/CLAUDE.md:13).
Recommendation: A — keeps the walk's package free of GUI code and the GUI package out of route.
Paused meanwhile: file layout in the plan · Continued: everything else.
```

```
DECISION NEEDED: RV1-D6 (Hong)
Question: What should stop a click on the live view from taking the game's focus (and so the bot's keys)?
Why it matters: keys go to the focused window; a click on the view mid-walk would send the bot's next moves to it.
Options:
  A. A warning only: a line on the view "clicking here takes focus from the game", watch-only controls while live —
     cost: nothing to build; relies on Hong not clicking.
  B. Make the live window not activate on click (Windows' WS_EX_NOACTIVATE), with A as the fallback if a no-key check
     shows it does not work on a Tk window — cost: a small Windows-only piece in the viewer and a manual check; the
     window then cannot take typing (replay controls work after the run, or by mouse only).
Recommendation: B — it removes the hazard instead of documenting it.
Paused meanwhile: nothing (both fit the design) · Continued: everything.
```

```
DECISION NEEDED: RV1-D7 (Hong)
Question: How is the live view started?
Why it matters: what Hong types, and whether the window is open before the countdown (focus, section 5.7).
Options:
  A. walk --view starts the viewer itself, before the 5 s countdown; the viewer keeps running after the walk ends,
     switched to replay of that run — cost: one flag; a child process the walk does not wait for.
  B. Hong starts it himself in a second console (python -m src.route_view --follow <run folder>) — cost: two commands,
     and the run folder name must be known before the walk starts (or the viewer picks the newest folder).
Recommendation: A — one command, and the order relative to the countdown is guaranteed.
Paused meanwhile: CLI wording in the plan · Continued: everything else.
```

```
DECISION NEEDED: RV1-D8 (Hong)
Question: How big should the minimap be drawn?
Why it matters: FF2's minimap is 162x82 pixels (fossil_foundry_2.csv:1); at the editor's 2x (map_editor.py:25) that
  is 324x164, small for numbered nodes and hop labels.
Options:
  A. 2x, as the map editor — cost: same look as the editor; labels crowd.
  B. 4x by default (648x328), with 2x/3x/4x buttons — cost: a little more drawing; labels readable.
Recommendation: B — the view exists to be read at a glance.
Paused meanwhile: nothing · Continued: everything.
```

```
DECISION NEEDED: RV1-D9 (Hong)
Question: For runs without recorded frames, what is the replay drawn on?
Why it matters: the brief asks for replay without frames; the first walk test (b21-test1) has none.
Options:
  A. From now on every walk saves one minimap snapshot at the start (minimap.png), and replay draws on it; older runs
     draw on a dark canvas the size of the map's minimap — cost: one small PNG per run.
  B. Always a dark canvas when there are no frames — cost: nothing saved; less context (no platforms visible).
Recommendation: A — the snapshot shows the platforms the route runs over, for almost no cost.
Paused meanwhile: nothing · Continued: everything.
```

## 9. Notes for the plan (RV3)

- Order: step log and observer with checks S1, S2, S4; route preview with S3; the pure view model with S5; the Tk
  window and replay with S6; live following with S7; walk and sim wiring; then the checks with Hong.
- The view will never show a jump: the planner has no jump move (section 3.2). If Hong expects jumps, that is a
  planner question, not a view one.
- Feature D wants "what the bot was doing — current leg, state" for its incidents
  (`docs/superpowers/specs/2026-10-21-p1d-watching-and-logging-design.md:13`); `steps.jsonl` can serve it later.
