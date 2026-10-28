# Route view: see what the route walker plans and why (design spec)

*Draft, 2026-10-27 (unit RV1; decisions recorded by unit RV1b, same day). Belongs to route walking (Feature B); branch
`p1-route-view`, cut from `p1b-route-walking` at 5d1176a. Every claim about today's code cites `file:line` at 5d1176a.
The nine choices of section 8 are decided: Hong decided D1, D2 and D6-D9; the orchestrator ruled on D3-D5 (the spec
reviewer gets those as verdict questions). Section 8 keeps each question, its options and the evidence, and says what
was chosen; the rest of the spec describes only the chosen design. Revised by unit RV2f after the spec review (RV2:
7 Important, 12 Minor findings), and by unit RV2g after the re-review (RV2r: 3 Important, 6 Minor); section 11 maps
each finding to its change.*

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

Shape (each part was an open decision in section 8, now decided):

- The walker reports each step (position read, move planned, move sent, settle, leg end) to an optional observer.
  The walk process writes those steps to a new file, `steps.jsonl`, in the run folder. Nothing else in the walk
  changes, and with no observer the walker does what it does today (section 5.2 says how the code guarantees it).
- The window is a separate program (its own process) that follows the run folder while the walk runs and replays it
  afterwards. `walk --view` starts it apart from the walk's console and with no console window of its own, waits (at
  most 15 s) until its window is up, then counts down (a viewer that misses that wait stays hidden until the walk
  ends); it turns `--record` on. It recomputes the expected route from each logged position with the
  planner's own functions, so the planner is not changed.
- The live minimap image comes from the frames the walk already records with `--record`, which `--view` turns on.

## 2. What Hong decided (binding)

From `ledger-RV.md`, `Decision (Hong): route view` (QB100-QB103):

- Its own "Route view" window: not the main Helix window, nothing drawn on the game. The live minimap enlarged, like
  the map editor.
- Live while a walk runs, and replay of a saved run afterwards (step or scrub through it).
- It shows (1) the rotation nodes numbered in rotation order, the next target highlighted; (2) the planned route as a
  line through the in-between nodes; (3) on each hop, the move (walk / teleport / jump / drop, as he listed them; the planner never plans a jump, so the view shows walk,
  teleport and drop only, and Hong was told so on 2026-10-27) and its keys; (4) a
  "why it chose" text log: position, move sent, why it re-planned.
- Built now; the B21 walk test and Feature C (runes) are paused meanwhile.

Hong's answers to the open choices, 2026-10-27 (section 8 has each question and its options; `ledger-RV.md`; the
decisions are `docs/decisions.md` H47-H52):

- RV1-D1 and D2 (QB104), his pick "Separate, from saved (Recommended)": the view is its own program that follows the
  run folder (D1 A), and `--view` turns on `--record`, about 0.5 GB an hour (D2 A).
- RV1-D6 (QB105), "Can't take focus (Recommended)": the live window does not take focus when clicked, using
  `WS_EX_NOACTIVATE` (B), with a warning line as the fallback if the no-key check shows it fails on a Tk window.
- RV1-D7 (QB106), "walk --view (Recommended)": `walk --view` starts the viewer (A).
- RV1-D8 (QB107), "4x, with 2x/3x/4x buttons (Recommended)": 4x by default (B).
- RV1-D9 (QB108), "Yes, save one picture (Recommended)": every walk saves `minimap.png` at its start (A); old runs
  draw on a dark canvas. He was asked twice because the first wording was unclear to him.
- Told to him, not asked: the planner never plans a jump, so the view shows walk, teleport and drop only.

The orchestrator ruled on three code-structure choices (not Hong's decisions; H53): RV1-D3 A (optional observer in the
walker), RV1-D4 A (a pure preview module calling the planner's private helpers), RV1-D5 A (pure parts in `src/route`,
the Tk app in a new `src/route_view`). The spec reviewer (RV2) agreed with all three, D4 on condition that check S3 is
widened (section 7, S3 and S3b).

A consequence of Hong's D1 pick, not a new decision: because the view is a separate program that reads files, its
live picture lags the walk by about 0.1-0.3 s (H48), while the walk waits only about 0.2 s between planning a move and
sending its keys (the reaction wait: exgaussian mu 195 ms, sigma 20 ms, tau 35 ms, at least 110 ms,
`src/input/timing.py:67-69`, `src/input/input_layer.py:88-90`). So a planned move usually shows on the view as, or just
after, its keys are sent, not before. Check L1 is worded to match.
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
  Hong was told this on 2026-10-27, so the view's hop list is walk, teleport and drop only; showing jumps would be a
  planner change, not a view change.

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

The two processes share only files in the run folder. The same viewer code replays a finished folder. How the walk
starts the viewer, waits for its window and tells it the run ended is in section 5.9.

### 5.2 Walk side: step events and `steps.jsonl`

The walker gets an optional observer (decision RV1-D3 A, orchestrator ruling). It is called at the points of `walk_leg`
below. It never calls the position source, the clock's `sleep` or the random generator.

The observer's type is a `Protocol` defined in `walker.py` itself, like `Clock` and `PositionSource`
(`walker.py:57-64`), so `walker.py` imports nothing new. The constructor argument is `observer=None`, keyword-only and
last, after `config` (`walker.py:92-102`), so every existing positional construction stays valid: `walk.py:278`,
`sim/run.py:198` and `:299`, and the rune CLI that `p1c-runes` plans (section 10). It is a constructor argument, not a
`WalkerConfig` field.

Why no observer means no change (the code-shape rule; a plan-review check, not only a test): every call site is
`if self._observer is not None:` with everything the event needs, including any `clock.now_ns()` call, built inside
that branch. With `observer=None` the walker therefore does at most one attribute test per call site per loop and
nothing else; it reads no clock, draws no number and builds nothing. Checks S1 and S2 (section 7) are regression
checks on top of that rule, in the simulator only; they do not prove it.

Timestamps: `leg`, `stall`, `read_failed`, `no_plan`, `reached` and `planned` use a time the walker already took
(`start_ns`, `now` and `read_ns`, `walker.py:123`, `:133`, `:142`). `sent` and `settled` have none (nothing reads the
clock after `execute` or `_settle`), so they call `clock.now_ns()` inside the observer branch; `RealClock.now_ns` is
`time.perf_counter_ns` (`walker.py:67-71`) and `SimClock.now_ns` only returns a field (`sim/link.py:33-34`), so the
call has no side effect. `leg_end` takes the leg record's `end_ns`: the return at `walker.py:192-204` becomes "build
the record, call the observer, return the record".

| Event | Where in `walk_leg` | Fields |
|---|---|---|
| `leg` | after `start_ns` (`walker.py:123`) | `t_ns` (= `start_ns`), `lap`, `leg`, `from`, `to` |
| `read_failed` | a failed read (`walker.py:143-147`) | `t_ns` (= `read_ns`), `why` (`"stale"` or the source's message) |
| `no_plan` | `next_move` failed (`walker.py:151-154`) | `t_ns` (= `read_ns`), `pos`, `why` (the planner's message) |
| `reached` | `move is None` (`walker.py:157-159`) | `t_ns` (= `read_ns`), `pos` |
| `stall` | a stall counted (`walker.py:137-139`) | `t_ns` (= `now`), `best_cost_s` |
| `planned` | after the stop check, before `execute` (`walker.py:165-168`) | `t_ns` (= `read_ns`), `pos`, `kind`, `dir`, `walk_ms`, `cost_to_go_s`, `toward` |
| `sent` | after `execute` returns (`walker.py:169-170`) | `t_ns` (`now_ns()`), `input` (`"ok"` or the error name), `reaction_ms`, `waited_ms`, `keys` (`[[delta_100us, mask], ...]` from `plan.steps`) |
| `settled` | after `_settle` (`walker.py:186-187`) | `t_ns` (`now_ns()`), `pos` (or null), `stopped` |
| `leg_end` | before the return (`walker.py:192-204`) | `t_ns` (= the record's `end_ns`), `outcome`, `misses`, `off_graph`, `stalls` |
| `end` | not the walker: `walk.py`'s cleanup (section 5.9), last event of a run | `t_ns`, `reason` (the `stop.json` reason, or null), `code` (the value `main` returns, or null when an exception is leaving `main`; section 5.9) |

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
  the leg log, which syncs, `leg_log.py:197-198`). The walk opens `steps.jsonl` only after `stop.start()` succeeds
  (section 5.9), so the file and its cleanup step exist only from there. At the end of the run the cleanup puts the
  `end` event, then a sentinel, joins the thread for at most 2 s, then syncs and closes the file. Its cleanup step goes
  after
  `release_all` and after `stop.json` in `walk.py`'s cleanup order (`walk.py:342-367`), so a slow disk never delays
  releasing the keys and the `end` event comes after every other file of the run is final except the leg log, which
  the viewer does not read for a run that has `steps.jsonl`.
- The walk also copies the map file it loaded into the run folder as `map.csv` (so a later edit to `maps/` cannot
  change a replay) and, in every walk (decision RV1-D9 A, Hong's), saves one minimap snapshot, `minimap.png`, from the
  first frame it already waits for (`walk.py:255-262`). `minimap.png` is written to a temporary name in the run folder
  and moved into place with `os.replace`, so a viewer never reads half a PNG; the viewer also retries a failed decode
  on its next poll. `steps.jsonl`, `map.csv`, `minimap.png` and the viewer's three files (`view.log`, `view.claim`,
  `view.ready`, section 5.9) are added to `_OUT_FILES` (`walk.py:81`), so the re-run guard (B19-D2) sees them.
- The simulator writes `steps.jsonl` and `map.csv` too, but only when `--out` is given (`sim/run.py:291-297`, `:394`);
  the smoke command has no `--out`, so its output cannot change. `map.csv` is the CSV the route graph was built from:
  with node offsets that is the shifted copy (`sim/run.py:269-270`, written by `_load_shifted_agent` into a temporary
  folder that is deleted on return, `sim/run.py:163-176`), not the `--map` file, so the copy must be taken while it
  exists or written again from the same offsets. Only the run's own walker gets the observer, not the replay walkers of
  the recovery metric (`sim/run.py:188-198`). It writes the `end` event too, when `run` returns (`reason` = the run
  summary's reason, `code` null).

An observer bug must not end a walk: the walker calls it inside a guard that catches `Exception`, prints one line to
stderr the first time and stops calling it for the rest of the run (part of decision RV1-D3 A).

### 5.3 The expected route and the move on each hop (route preview)

A new pure function computes, for a logged position and target, what the planner would do (decision RV1-D4 A, orchestrator ruling):

1. **Start and path**: the same steps as `next_move` P1-P3 (`planner.py:190-218`): `attach`, `_offered`,
   `_on_slope_segment`, `_starts`, `cost_to` per start, cheapest start (ties left before right), `rg.path`. It also
   keeps every candidate start with its cost, for the "why" log ("start at #3: 3.73 s; via #2 it would be 4.10 s").
2. **First move**: the same P4/P5 rules (`planner.py:220-247`), with no random draw: where `next_move` would toss a
   coin between teleporting down and dropping (`planner.py:171-178`), the preview returns both options. In a live or
   recorded step the first move is not predicted at all: the logged `planned` event is drawn, marked "sent".
3. **Later hops**: the path is split into hops the way the planner walks it, each assuming the move before landed
   where it aimed. Each hop's result carries that assumed landing point (x, y) with its move and `toward`, because
   check S3b plans again from it:
   - a run of level (WALK) edges or a slope (SLOPE) edge: the planner heads for the end of the run
     (`_walk_run_end`, `planner.py:100-104`) and, off a slope, teleports while one teleport plus the remaining walk
     beats walking (`_run`, `planner.py:91-97`); the preview repeats that rule on the distance left, giving for
     example "teleport right (Right+E) x2, then walk right about 1.4 s (hold Right)". A walk longer than `max_walk_ms`
     (`params.py:20`) shows as several walks;
   - an up or down edge: first the walk or teleports to its launch x (`_launch_x`, `planner.py:135-152`), then
     "teleport up (Up+E)", or for down the options of `down_options` with the coin-flip band (`planner.py:168-181`):
     "drop (Down+Alt)", "teleport down (Down+E)", or "drop or teleport down, 50/50".
4. **Keys** for each hop come from the table in section 3.3; for the sent move they come from the logged schedule.

Because the first move of a live or recorded step is the logged one, what the view adds is the route line and hops
2..n, built by step 3's new rules. Check S3b (section 7) pins every one of those hops exactly against `next_move`
called at the landing point the preview assumed, and S3's widened inputs cover the planner branches one simulator run
never takes. Where the real character lands a little off the aim, the planner may correctly choose differently (a
landing within `arrive_px` can cross the walk-or-teleport break-even of `_run`, `planner.py:91-97`, or swap two
near-tied starts); the view shows that as "route changed" (section 5.4), and S3b reports how often it happens.

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
scaling so pixels stay sharp, as the editor does (`map_editor.py:1073-1077`); the scale is 4x by default with 2x, 3x and 4x buttons (decision RV1-D8 B, Hong's). Blue nodes
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
line without its newline is left for the next poll) and redraws only when something changed. Per poll it decodes only
the newest frame, however many index lines arrived. When it reads the `end` event it switches to replay of the same
folder (H50), drops the live window's no-activate style (section 5.7) and its title says "ended: <reason>". A viewer
that missed the walk's ready wait follows the folder with its window hidden and shows it only then, already in replay
(section 5.9 step 3). With no `end` and no new line for 10 s its title says "no new
steps for 10 s: the walk is still running or has crashed"; it keeps following. Replay mode loads the folder, then
steps by event, by move or by leg, scrubs with a slider, and plays at 1x or 4x on the logged times.

### 5.5 Replay sources, best first

| Folder has | Background | Steps |
|---|---|---|
| `steps.jsonl` + `frames/` | the recorded frame nearest before each step (same clock, section 3.4) | every event |
| `steps.jsonl`, no frames | `minimap.png`, the snapshot from the start of the run; the character is drawn from the logged positions | every event |
| `legs.jsonl` only (runs before this feature, e.g. `b21-test1`) | a dark canvas of the map's `# minimap_size=` (FF2: `162,82`, `tests/fixtures/fossil_foundry_2.csv:1`), or a snapshot if one exists | one `planned` + `settled` pair per `MoveEntry`, leg starts and ends from the leg record; no keys timing, no miss times; the expected route recomputed as in 5.3 |
| simulator `--out` folder | dark canvas of `# minimap_size=` | every event, virtual times |

Every walk from this feature on saves the snapshot (RV1-D9 A); the dark canvas is for older runs and the simulator.
The legs-only reader is what lets Hong look at `b21-test1` today. Its map comes from `session.json`'s `map` path,
checked against `map_sha256`; a mismatch gives the warning of section 5.3.

### 5.6 Threads, locks, and why the walk is not slowed

The design (separate process, decision RV1-D1 A, Hong's):

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

The in-process alternative, not chosen (RV1-D1 B; kept as evidence): a Tk thread in the walk process takes the frame reference under
`minimap_frame_lock`, releases it, then scales and draws (never under the lock, unlike `map_editor.py:1069-1087`),
and takes events from the same queue. Its redraws (scaling, `PhotoImage`, canvas items) share the GIL with the walker
and the vision thread, so the walk can only be shown to be slowed little, by measurement, not by construction.

### 5.7 Focus safety (the view must not take the bot's keys)

The board's keys go to whichever window has focus. Rules for the view during a live run:

- The viewer's window is up before the walk's 5 s countdown starts (`walk.py:300-301`), so Hong focuses the game
  after it opened. Starting the process first is not enough: the window appears only after Python, Tk, Pillow, cv2,
  networkx and the controller are imported and the route graph is built. So the walk waits for the viewer's ready
  signal before it prints "Focus the game window..." (section 5.9).
- Its first show does not activate it. Microsoft's docs for `SetForegroundWindow`
  (https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setforegroundwindow) let a process take the
  foreground if, among others, "The calling process was started by the foreground process", which the viewer is when
  Hong started the walk from a focused console. The viewer creates its root withdrawn, sets the extended style on the
  Tk wrapper window (below) before the first show, and shows it without activating it (`ShowWindow` with
  `SW_SHOWNOACTIVATE`, not Tk's `deiconify`). Whether Tk's own state stays right after that is part of the no-key
  check. Fallback if N1 shows that this first show still activates the window or leaves Tk's state wrong: Tk's plain
  `deiconify` at the same point. That is safe because the first show always comes before the walk prints "Focus the
  game window..." (Hong focuses the game after it), and a viewer that is too late for that never shows its window
  while the walk runs (section 5.9 step 3).
- It never calls `focus_force`, `lift`, `-topmost`, or opens a message box or dialog while following a live run;
  errors go to its status line.
- Clicking it during a live run would move focus to it and the bot's next moves would go nowhere. Decision RV1-D6 B
  (Hong's): the live window is one that clicks do not activate; if the no-key check below shows that does not work on a
  Tk window, the fallback is option A, a warning line ("clicking here takes focus from the game") with watch-only
  controls while live. Microsoft's docs for
  `WS_EX_NOACTIVATE` (https://learn.microsoft.com/en-us/windows/win32/winmsg/extended-window-styles): "A top-level
  window created with this style does not become the foreground window when the user clicks it." The docs speak of a
  window *created* with the style; whether setting it on an existing Tk window has the same effect is untested and
  needs a no-key check (below).
- Which window gets the style: Tk's top-level is a wrapper around the window `winfo_id` returns. Measured by the
  reviewer on a withdrawn `tk.Tk()`: `winfo_id` 0x51b86, `wm frame` 0x480b10 = `GetParent(winfo_id)`. The style goes
  on the `wm frame` HWND, the window Windows activates; on the `winfo_id` child it does nothing useful.
- The style's lifecycle. A window with `WS_EX_NOACTIVATE` "does not appear on the taskbar by default. To force the
  window to appear on the taskbar, use the WS_EX_APPWINDOW style" (the same Microsoft page). While live the viewer
  does not add `WS_EX_APPWINDOW`: it has no taskbar button, because clicking one is another way to activate it, and
  the window sits in plain view on the other monitor. When it reads `end` and switches to replay it clears
  `WS_EX_NOACTIVATE` and sets `WS_EX_APPWINDOW`, so it gets its taskbar button, takes focus on click and takes typing
  (H49: "replay controls work after the run"). A viewer opened on a finished folder, or one that missed the ready wait
  and opens at `end`, never sets the no-activate style. How a style change takes effect on a window already shown
  (Windows may need the window hidden and shown again for the taskbar to notice) is part of N1.
- Windows calls (ctypes) happen only inside the viewer's functions, never at import, so check S6 and cloud (Linux)
  runs can import the package.

The no-key check (sends no keys and needs no MapleStory; any other window can hold the foreground): start the real
viewer, with its slider and buttons, through the walk's viewer-start seam (the same function, arguments and flags as
section 5.9, from a parent Python process in a console of its own), following a folder that a script keeps appending
sim events to, with no `end` yet (a finished sim folder has its `end` and would open in replay); give another window
the foreground; read `GetForegroundWindow`; click the viewer's canvas, a button and the slider; read
`GetForegroundWindow` again. Pass: it is the other window every time, and the viewer's first show did not change it
either. The same run checks the spawn: no console window appears for the viewer (no visible window of the viewer's
processes other than the route view itself), a Ctrl+C in the parent's console does not end the viewer, and the viewer
is still running after the parent exits. Then the script writes `end`: the viewer switches to replay, shows a taskbar
button, and a click on it now does activate it. The clicks are real mouse input that moves the cursor, so Hong does
them, or an agent only while nobody is using the PC (the same idle rule as H15), and with MapleStory closed. If the
focus part fails, the fallback of RV1-D6 applies (warning line, watch-only while live); if the first show is the part
that fails, the `deiconify` fallback above; if the spawn part fails, the plan comes back to this spec before L1.

### 5.8 Where the code lives

Decided (decision RV1-D5 A, orchestrator ruling):

- `src/route/step_log.py` (new): the observer, the event tuples, the writer thread and a reader of `steps.jsonl` and
  of the legs-only fallback. Standard library only, so it fits plan hard rule 6 (the route import allowlist,
  `docs/superpowers/plans/2026-10-23-p1b-route-walking.md:62-65`, `tests/route/test_import_rules.py:64-69`).
- `src/route/route_preview.py` (new): the pure preview of section 5.3. Imports `planner`, `locate`, `route_graph`.
- Both new route modules join import-linter contract `f route layers` (`pyproject.toml:62-75`): `route_preview` in
  a layer above `planner` (it imports it; nothing in the walk imports it), `step_log` in the bottom layer with
  `params`, `leg_log` and `character` (standard library only; `walk.py` and `sim/run.py` import it).
- `src/route_view/` (new top-level package, snake_case): the Tk window, live follower and replay. It may import
  `src.route.step_log`, `src.route.route_preview`, `src.route.planner`, `src.route.locate`, `src.route.route_graph`,
  `src.route.params`, `src.route.terrain`, `src.controller`, `src.classes`, `src.input.protocol`, `tkinter`, Pillow
  (already pinned, `requirements.txt:6`) and `cv2`. A new import-linter contract (a forbidden contract named by what
  it does, e.g. `"route_view boundaries"`, not by a letter: `p1c-runes` already uses `g` and `h`, section 10) forbids
  it `src.gui`, `src.main`, `src.vision`, `src.rune_solver`, every `src.input` module except `protocol`
  (`src.input.input_layer`, `link`, `actions`, `timing`, `harness` and the rest), and the route modules that pull
  those in: `src.route.walker`, `src.route.walk`, `src.route.moves`, `src.route.runtime`, `src.route.sim`. A second
  forbidden contract makes `src.route_view` a module nothing imports (source modules `src.route`, `src.controller`,
  `src.gui`, `src.main`, `src.vision`, `src.input`, `src.classes`, `src.rune_solver`). `src.rune_solver` is in the
  import graph today (a namespace package with no `__init__.py`; RV2r: `grimp.build_graph('src')` has it), so both
  contracts list it now. That matters: import-linter refuses a listed module that is missing from the graph
  (`importlinter/contracts/forbidden.py:225`, `Module '...' does not exist.`). `walk.py`
  starts the viewer as a child process by module name (`python -m src.route_view`), so the walk imports none of it
  (decision RV1-D7 A, Hong's: `walk --view` starts it).

### 5.9 Starting the viewer, and the end of a run (walk side)

`walk --view` (it also sets `--record`, H48) does, in this order, after `stop.start()` succeeds and before
`session.json` and the countdown (`walk.py:282-301`):

1. Copy the map to `map.csv`, write `minimap.png` (atomically, section 5.2), open `steps.jsonl` and put its `run`
   header, write `session.json`. Every walk does this step at this point, with or without `--view`. Opening
   `steps.jsonl` only after `stop.start()` succeeds is what makes the `end` event's `code` exact: every early `return`
   of `main` (`walk.py:258`, `:262`, `:268`, `:274`, `:285`) comes before it and none of them sets `code`
   (`walk.py:249` sets 0), so from here the only ways out of `main` are `return code` (`walk.py:368`) after
   `walker.run`, or an exception.
2. Start the viewer apart from the walk's console and with no console window of its own:
   `subprocess.Popen([sys.executable, "-u", "-m", "src.route_view", "--follow", <run folder>, "--ready", <run
   folder>/view.ready], cwd=<repo root>, creationflags=CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP, stdin=DEVNULL,
   stdout=<run folder>/view.log, stderr=STDOUT)`. Both flags exist in the venv's `subprocess` (printed values
   134217728 and 512). The walk never waits for the process to exit and never kills it. Why these flags (RV2r I8):
   - Under the venv, `sys.executable` is the redirector `.venv\Scripts\python.exe`, which starts the base
     `python.exe` with creation flags 0 (CPython 3.14 `PC/venvlauncher.c:444-446`). Started with `DETACHED_PROCESS`,
     as the previous version of this spec had it, the redirector has no console to pass on, and "The system creates a
     new console when it starts a console process" (Microsoft,
     https://learn.microsoft.com/en-us/windows/console/creation-of-a-console): a stray console window on every
     `walk --view`, which could take the foreground and which Hong could close as junk, ending the viewer.
   - With `CREATE_NO_WINDOW` "The process is a console application that is being run without a console window"
     (https://learn.microsoft.com/en-us/windows/win32/procthread/process-creation-flags): the redirector gets a
     console with no window, and the base interpreter, started with flags 0, inherits that console. It is not the
     walk's console, so Hong's Ctrl+C there (the console sends CTRL+C to the processes attached to it,
     https://learn.microsoft.com/en-us/windows/console/ctrl-c-and-ctrl-break-signals) and closing it do not reach the
     viewer. `CREATE_NO_WINDOW` "is ignored if ... used with either CREATE_NEW_CONSOLE or DETACHED_PROCESS" (same
     page), which is why `DETACHED_PROCESS` is dropped. `CREATE_NEW_PROCESS_GROUP` stays: "If this flag is specified,
     CTRL+C signals will be disabled for all processes within the new process group" (same page).
   - Chosen over starting `.venv\Scripts\pythonw.exe` (the windowless redirector): the flags work on whatever
     `sys.executable` is, the venv redirector or a base `python.exe`, while the `pythonw.exe` path would have to be
     derived from `sys.executable` by file name. The viewer stays a console program, so its output streams behave as
     in any other console run.
   - The viewer outlives the walk: the redirector's job object has `JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK`
     (`venvlauncher.c:415-416`), so the walk's children are not in the job that `KILL_ON_JOB_CLOSE` ends.
   - `-u` (unbuffered output; measured in the venv with output to a file: `sys.stdout.write_through` is True with
     `-u`, and stdout is neither line-buffered nor write-through without it) so the viewer's status lines reach
     `view.log` as they are printed, not when a block fills (RV2r m17). Tracebacks go to stderr, which is
     line-buffered anyway.
   This all rests on Microsoft's docs and CPython's source, untested (RV2r could not run a spawn while MapleStory was
   running); N1 (section 5.7) checks it before L1.
3. Wait for the ready signal, with one claim file so that a late viewer never shows its window during the walk
   (RV2r I9). The viewer, once its window is built and still withdrawn, claims the live show by creating
   `<run folder>/view.claim` with `open(path, "x")`, which fails with `FileExistsError` if the file already exists
   (measured in the venv). If it gets the claim, it shows the window (section 5.7) and then writes `view.ready`
   (temporary name, then `os.replace`). The walk polls for `view.ready` every 50 ms for at most 15 s; when it appears
   the walk goes on. If it has not appeared by then, or the process has already exited, the walk tries to create
   `view.claim` itself:
   - it gets the claim: a viewer still starting will find the claim taken, keeps its window withdrawn, follows the
     folder hidden, and shows its window only when it reads `end`, already switched to replay (section 5.4). The walk
     prints one line ("route view did not start within 15 s; walking without it (it opens when the walk ends), see
     view.log") and goes on;
   - the claim is taken: the viewer is between its claim and its show, which are adjacent calls; the walk waits for
     `view.ready` at most 2 s more, then goes on (with the same line if it still has not appeared).
   Exactly one of the two creates `view.claim`, so the viewer's first show either comes before "Focus the game
   window..." or not until the run has ended. What is left: a viewer that takes the claim and then stalls more than
   2 s before its show call; and a walk killed hard (no `end` written) leaves a hidden viewer running with no window
   (ended from Task Manager). Both are accepted (section 9 asks the plan reviewer). A viewer failure never stops a
   walk.
4. Print "Focus the game window..." and count down, as today.

At the end, the cleanup step of section 5.2 puts the `end` event with the `stop.json` reason (or null) and `code`:
the value `main` returns, which from step 1 on is the `code` variable, or null when an exception is leaving `main`
(for example Ctrl+C during the countdown), found with `sys.exc_info()` in the `finally` (measured: inside a `finally`
that an exception is passing through, `sys.exc_info()[0]` is that exception's type; it is None after a normal exit).
The viewer then switches to replay (section 5.4). The walk's cleanup does not touch the viewer process; Hong closes
its window.

Without `--view` none of this happens except `map.csv`, `minimap.png` and `steps.jsonl`, which every walk writes.

## 6. What must not change, and what does

Must not change: the planner, moves, route graph, terrain, locate and params (frozen, `src/route/CLAUDE.md:23`) and
their tests; the walker's behaviour with no observer; `legs.jsonl` / `laps.jsonl` and their pinned keys; `walk.py`'s
exit codes and cleanup order apart from the added step; the stop guard (`walker.py:53`, `:223-239`).

Changes: `src/route/walker.py` gets the optional observer (frozen file: RV1-D3);
`src/route/walk.py` gets `--view` (RV1-D7), the observer and writer wiring, the snapshot, the `map.csv` copy, the new
`_OUT_FILES`, the viewer start and ready wait (section 5.9) and one cleanup step; `src/route/sim/run.py` writes
`steps.jsonl` and `map.csv` with `--out`; new `src/route/step_log.py`, `src/route/route_preview.py`,
`src/route_view/`; `pyproject.toml` (contract f gains the two new route modules; two new route_view contracts);
`src/route/CLAUDE.md` (the new seam on the planner's private helpers, the observer); a new `src/route_view/CLAUDE.md`
(its seams and traps: the claim and ready files, the spawn flags, the no-activate style, ctypes only inside
functions), since the repo keeps one per package; root `CLAUDE.md` (an "Architecture" paragraph for the route view
and `walk --view`, and `src/route_view/CLAUDE.md` in the "Per-directory notes" list). The plan (RV3) splits this into
tasks of at most about three code files each (plan hard rule 7).

## 7. Acceptance checks: what "working" means

Baseline measured for this spec at 5d1176a: `python -m src.route.sim.run --laps 20 --seed 1
--min-single-error-legs 0` prints six PASS lines (20/20 laps, 141/141 legs reached, 379 moves) and its stdout has
sha256 `d982184e54311493311110657d93992fe7f0af4963dc603617c5a0f4d09d8035`. How the hash is taken: of the raw stdout
bytes, which end lines with CRLF on Windows, through a bash pipe (Git Bash, from the repo root):
`python -m src.route.sim.run --laps 20 --seed 1 --min-single-error-legs 0 | sha256sum`. Re-measured by RV2 and again by
RV2f with the venv's Python at f9dcf06 (same code as 5d1176a): the same hash. With the `\r` stripped the hash is
`6574cac5...8d25` (RV2); PowerShell 5.1's `>` writes UTF-16 and gives neither, so do not hash a file written that way.

Simulator-only checks (no game, no board; an agent runs them):

- **S1 behaviour unchanged.** The sim smoke's stdout has the sha256 above; `python -m pytest tests/route -q` passes
  with the frozen test files byte-identical to 5d1176a; the 1000-lap target run (`src/route/CLAUDE.md:38`) prints the
  same lines as before.
- **S2 the observer changes nothing.** Two simulator runs with the same seed, one with a recording observer and one
  without, give identical `LegRecord`s, `LapRecord`s and report, and the generators end in the same state.
- **S3 the preview agrees with the planner.** Over every `planned` event of a 20-lap sim run: the preview's cost to go
  equals the logged `cost_to_go_s` exactly, its `toward` equals the logged one, and its first move equals the logged
  kind, direction and `walk_ms` (for a coin-flip down move, the logged choice is one of the two options). The same
  over every `planned` event of a 20-lap run with node offsets (`NoiseConfig(node_offset_px=2)`, as
  `tests/route/test_sim_run.py:81` runs it; `sim/world.py:133`), whose off-graph reads the plain run lacks. One plain
  run is not enough: measured by RV2 with `sys.settrace` on the seed-1 run's 379 moves, it never takes the planner's
  single-option down (`planner.py:172`), clearly-cheaper down (`:178`), walk after a slope start (`:243-244`), an up or
  down edge dropped by `_offered` because `_launch_x` returns None (`:152`), off-graph Failure (`:201`) or no path
  (`:213`). So S3 also has an agreement table on small hand-built graphs, in a new test file (the frozen tests stay
  untouched), with at least one case forcing each of those six; for the two Failures the preview gives a Failure with
  the planner's message. Optional, by hand, not a test (the folder is gitignored): the 37 moves of `b21-test1`, which
  RV2 found agree 37/37 on cost to go.
- **S3b every later hop agrees with the planner, exactly.** The pass/fail part: for every preview of S3's inputs (every
  `planned` event of both 20-lap sim runs, and every case of the hand-built table), and for each hop k from 2 to the
  last, call `next_move` (with a generator of the test's own) at the landing point the preview assumed for hop k-1,
  with the same target and route graph. Its move must equal the preview's hop k exactly: kind, direction, `walk_ms`
  and `toward` (for a coin-flip down hop, `next_move`'s move is one of the hop's two options), and the preview
  computed from that landing point must give the same remaining path as the original preview from hop k on. Because
  the landing point is the one the preview assumed, both sides plan from the same position and no tolerance is
  needed; this covers hops 3..n and the down and slope branches as later hops, which the hand-built cases can force
  (S3). A disagreement fails the check; if one turns out to be the planner's correct behaviour that the hop rules do
  not model, the implementer raises it instead of loosening the check. The report part, not pass/fail: for two
  `planned` events in a row in the same leg of the sim runs, compare the first event's hop 2 and remaining path with
  the second event's logged move and its preview, and print how many pairs agree out of how many. A real landing a
  pixel or two off the aim can correctly change the plan (section 5.3), so this agreement rate is the "route changed"
  frequency, the same comparison as the line of section 5.4, and is reported, not required.
- **S4 the step log matches the leg log.** In a sim `--out` folder, the `planned`/`sent`/`settled` events map one to
  one onto the `MoveEntry`s of `legs.jsonl` (the `planned` event's `t_ns`, `pos`, `kind`, `dir`, `walk_ms` and
  `cost_to_go_s`, the `sent` event's `input`, the `settled` event's `pos` as `settled`; a move that ended the leg with
  the link down has no `settled` event and `settled` null); every `leg` has a `leg_end` with the leg record's
  `end_ns`, outcome and counts; the first line is a valid `run` header and the last an `end` event. `sent` and
  `settled` times are not compared: the leg log has none (section 5.2).
- **S5 the view model.** A pure (no Tk) model built from a sim folder at chosen steps gives: blue nodes numbered 1..n
  in file order, the right target highlighted per leg, the route polyline's points, the hop labels and keys, and the
  "why" lines; pinned by golden text for a handful of steps, including a coin-flip down, a teleport run and an
  off-graph read (from a hand-written `steps.jsonl`).
- **S6 the window opens.** As `tests/controller/test_gui_startup.py` does (real, withdrawn Tk root, no mainloop,
  skips if Tk cannot start), the view loads a sim folder and a legs-only folder and draws step k without error.
- **S7 following a live folder.** A test thread appends `steps.jsonl` lines (including half a line) and frame index
  lines while the follower polls: the view shows the newest complete step and frame, never a half line.
- **S8 cost on the walker thread.** In a sim run, the walker thread's time per event with the observer is under
  20 µs on the dev PC (median of a 20-lap run); the writer's queue is empty within 1 s of the run's end. This bounds
  the cost in the simulator only; live, a `planned` event's cost comes out of the reaction wait (section 2) and the
  others delay `_settle`'s start by that much, whose window is measured from its own start (`walker.py:284`).
- **S9 rules.** `python tools/check.py` passes (ruff, basedpyright with its baseline, import-linter with contract f
  widened and the two new contracts, tests); the route import allowlist test still passes.
- **S10 walk wiring, no game.** Through `walk.py`'s seams (`walk.py:87-111`: fake capture, positions, link, clock and
  stop control) and a new seam for starting the viewer: `--view` turns `--record` on; the walker gets the observer;
  the run folder gets `steps.jsonl` (a `run` header first, an `end` event last with the stop reason and `code`: the
  value `main` returned after a normal or failed walk, null when an exception left `main`, for example a
  KeyboardInterrupt during the countdown), `map.csv` byte-equal to `--map`, and `minimap.png` that decodes to the first
  frame; a walk that returns before `stop.start()` succeeds (for example a failed connect, `walk.py:268`) writes no
  `steps.jsonl`; `_OUT_FILES` lists every new file, so a second walk into the same folder is refused; the cleanup runs
  `release_all`, then `stop.json`, then the step log; the viewer is started with the arguments of section 5.9
  (`-u`, module, `--follow`, `--ready`, `creationflags` exactly `CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP`, stdin
  closed, output to `view.log`); with a fake viewer that takes `view.claim` and writes `view.ready` the countdown
  starts after it; with one that never does, the walk creates `view.claim` itself, prints its one line and goes on
  after the bounded wait (patched short in the test); with one that took the claim but writes no `view.ready`, the walk
  waits the extra bounded time, then goes on. Without `--view` no viewer is started and no `view.claim` is written.

Checks with Hong (need the game; never run by an agent without his go, root `CLAUDE.md` safety rules):

- **L1** A supervised 1-lap walk with `--view` (which turns `--record` on, RV1-D2 A): the window shows the nodes numbered, the
  target ring moves leg by leg, each planned move and its route line appear within about 0.3 s of being planned (they
  may show just after the keys are sent: the reaction wait is about 0.2 s, section 2), the "why" log reads
  sensibly; F12 still stops the walk; closing the view does not stop the walk. A second short walk stopped with Ctrl+C
  in the walk's console: the view stays open and switches to replay of that run.
- **L2** The walk's results are as without the view (same exit code path, legs reached), judged on that run only. As a
  cheap health measure, the gaps between frame times in `frames/index.jsonl` are reported next to those of an earlier
  `--record` run without the view, if one exists.
- **L3** Replay of the L1 folder and of `b21-test1` steps and scrubs to any move.
- **L4** Focus: after the countdown, with the view open on the other monitor, the game keeps focus for the whole run.
  Once from a cold start (the first `--view` walk after a reboot, when the viewer's imports are slowest): the
  countdown starts only after the window is up, and the window's appearance does not take the foreground. If the cold
  start misses the 15 s wait, the walk says so in its one line, the window stays hidden and opens when the walk ends
  (section 5.9 step 3); the game must still keep focus, and the miss is reported with how long the viewer took to
  build its window (it prints that time to `view.log`).

Check with Hong or an idle PC (no game, no keys sent, MapleStory closed): **N1** the no-key check of section 5.7,
including its spawn part (no console window, Ctrl+C in the parent's console does not reach the viewer, the viewer
outlives its parent), before L1.

## 8. Open decisions

"Who" says who should decide: Hong for what he sees or does, the orchestrator for code structure (it may still send
those to Hong).

Status, 2026-10-27: all nine are decided (unit RV1b). The blocks below keep the question, the options and the cost
evidence as written; each header now says who decided and what was chosen, and its last two lines say what the choice
unblocks. The other options are not part of the design. Hong's words are quoted from `ledger-RV.md`; `docs/decisions.md`
H47-H53 record them.

```
DECIDED: RV1-D1 (Hong, 2026-10-27, QB104) -> A
Question: Should the route view run as its own program next to the walk, or inside the walk program?
Why it matters: decides whether the view can slow the walk at all, how live and replay share code, and how fresh the
  live picture is.
Options:
  A. Own program (process) that follows the run folder — cost: live picture lags about 0.1-0.3 s (file polling);
     the walk cannot be slowed by drawing; one code path for live and replay; easy to reverse.
  B. A window thread inside the walk process reading the shared frame — cost: freshest picture; the view's drawing
     shares the walk's Python lock (GIL), so a slow-down can only be measured, not ruled out; a Tk failure happens
     inside the walk; replay still needs the file reader of A; moderate to reverse.
Decided: A, own program (process) that follows the run folder. Hong's pick: "Separate, from saved (Recommended)". Recommendation was A: the brief's "must not slow the walker or the vision thread" holds by construction.
Unblocks: the plan (RV3) · Option B is not part of the design.
```

```
DECIDED: RV1-D2 (Hong, 2026-10-27, QB104) -> A
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
Decided: A, `--view` also turns on `--record`. Hong's pick, one answer for D1 and D2: "Separate, from saved (Recommended)". Recommendation was A: tested code, and supervised runs are minutes long today.
Unblocks: frame handling in the plan · Options B and C are not part of the design.
```

```
DECIDED: RV1-D3 (orchestrator ruling, 2026-10-27; touches a frozen file) -> A
Question: How should the walker's steps reach the step log?
Why it matters: the walker (src/route/walker.py) is frozen (src/route/CLAUDE.md:23); the step log's accuracy depends
  on where the events come from.
Options:
  A. An optional observer argument to RouteWalker, called at the nine points of section 5.2, inside a guard that
     disables it after its first exception; default None — cost: about 30 lines in a frozen file plus tests; exact
     events (the planner's messages, stalls, toward, the sent schedule); behaviour with None unchanged by the
     code-shape rule of section 5.2 (checks S1, S2 are regression checks on top); reversible (remove the argument).
  B. No walker change: wrap the position source, the input layer (a subclass of InputLayer) and the leg sink in
     walk.py and sim/run.py, and infer the steps from the order of their calls (the planning read is the last
     successful read before a layer call, walker.py:141-169; the target from the leg count) — cost: no frozen edit;
     stalls and off-graph messages must be recomputed by the viewer; silently wrong if the walker's call order ever
     changes; more code.
Decided: A, an optional observer argument to `RouteWalker`, default None, guarded (disabled after its first exception), about 30 lines in a frozen file. Orchestrator ruling, not Hong's. The walker's freeze is not Hong's either: B14-inv-D1 (a Hong ruling, on the planner fix) froze these files only "until the fix lands" (`docs/decisions.md:1307`); the standing freeze wording came from Claude's docs commits fe51e9b and b5c7a48 (`src/route/CLAUDE.md:23`), and the orchestrator ruled it is not a Hong decision (`.superpowers/orchestrator/2026-10-21-phase1/ledger-C.md:85`). Reason: exact events for the "why" log, and with None the walker is unchanged by the code-shape rule (section 5.2; checks S1, S2 on top). The spec reviewer (RV2) agreed. Cost if wrong: remove one argument. The p1c-runes branch plans a `WalkerConfig.between_legs` hook in the same file; section 10 lists what the later merge must handle.
Unblocks: the walk-side tasks of the plan · Option B is not part of the design.
```

```
DECIDED: RV1-D4 (orchestrator ruling, 2026-10-27; touches a seam of a frozen file) -> A
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
Decided: A, a pure `src/route/route_preview.py` calling the planner's private helpers, pinned by S3; the seam is noted in `src/route/CLAUDE.md`. Orchestrator ruling, not Hong's. Reason: no frozen edit, and S3 fails loudly on drift. The spec reviewer (RV2) agreed on condition that S3 is widened; it now covers a node-offset run and hand-built graphs for the planner branches one sim run misses, and S3b checks the later hops (section 7). Cost if wrong: a later public planner function.
Unblocks: the preview task · Options B and C are not part of the design.
```

```
DECIDED: RV1-D5 (orchestrator ruling, 2026-10-27; module boundaries) -> A
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
Decided: A, pure parts in `src/route`, the Tk app in a new top-level `src/route_view` with its own import-linter contract. Orchestrator ruling, not Hong's. Reason: it keeps GUI code out of the walk package and route out of `src/gui`. The spec reviewer (RV2) agreed; section 5.8 spells out the contracts. Cost if wrong: moving one package.
Unblocks: file layout in the plan · Options B and C are not part of the design.
```

```
DECIDED: RV1-D6 (Hong, 2026-10-27, QB105) -> B
Question: What should stop a click on the live view from taking the game's focus (and so the bot's keys)?
Why it matters: keys go to the focused window; a click on the view mid-walk would send the bot's next moves to it.
Options:
  A. A warning only: a line on the view "clicking here takes focus from the game", watch-only controls while live —
     cost: nothing to build; relies on Hong not clicking.
  B. Make the live window not activate on click (Windows' WS_EX_NOACTIVATE), with A as the fallback if a no-key check
     shows it does not work on a Tk window — cost: a small Windows-only piece in the viewer and a manual check; the
     window then cannot take typing (replay controls work after the run, or by mouse only).
Decided: B, the live window does not activate on click (`WS_EX_NOACTIVATE`), with option A (warning) as the fallback if the no-key check shows it fails on a Tk window. Hong's pick: "Can't take focus (Recommended)". Recommendation was B: it removes the hazard instead of documenting it.
Unblocks: nothing was waiting · Option A stays only as the fallback.
```

```
DECIDED: RV1-D7 (Hong, 2026-10-27, QB106) -> A
Question: How is the live view started?
Why it matters: what Hong types, and whether the window is open before the countdown (focus, section 5.7).
Options:
  A. walk --view starts the viewer itself, before the 5 s countdown; the viewer keeps running after the walk ends,
     switched to replay of that run — cost: one flag; a child process the walk does not wait for.
  B. Hong starts it himself in a second console (python -m src.route_view --follow <run folder>) — cost: two commands,
     and the run folder name must be known before the walk starts (or the viewer picks the newest folder).
Decided: A, `walk --view` starts the viewer before the 5 s countdown, and it keeps running after the walk ends, switched to replay of that run. Hong's pick: "walk --view (Recommended)". Recommendation was A: one command, and the order relative to the countdown is guaranteed.
Unblocks: CLI wording in the plan · Option B is not part of the design.
```

```
DECIDED: RV1-D8 (Hong, 2026-10-27, QB107) -> B
Question: How big should the minimap be drawn?
Why it matters: FF2's minimap is 162x82 pixels (fossil_foundry_2.csv:1); at the editor's 2x (map_editor.py:25) that
  is 324x164, small for numbered nodes and hop labels.
Options:
  A. 2x, as the map editor — cost: same look as the editor; labels crowd.
  B. 4x by default (648x328), with 2x/3x/4x buttons — cost: a little more drawing; labels readable.
Decided: B, 4x by default (648x328) with 2x/3x/4x buttons. Hong's pick: "4x, with 2x/3x/4x buttons (Recommended)". Recommendation was B: the view exists to be read at a glance.
Unblocks: nothing was waiting · Option A is not part of the design.
```

```
DECIDED: RV1-D9 (Hong, 2026-10-27, QB108) -> A
Question: For runs without recorded frames, what is the replay drawn on?
Why it matters: the brief asks for replay without frames; the first walk test (b21-test1) has none.
Options:
  A. From now on every walk saves one minimap snapshot at the start (minimap.png), and replay draws on it; older runs
     draw on a dark canvas the size of the map's minimap — cost: one small PNG per run.
  B. Always a dark canvas when there are no frames — cost: nothing saved; less context (no platforms visible).
Decided: A, every walk saves `minimap.png` at its start; replay draws on it; older runs draw on a dark canvas. Hong's pick: "Yes, save one picture (Recommended)". He was asked twice because the first wording was unclear to him. Recommendation was A: the snapshot shows the platforms the route runs over, for almost no cost.
Unblocks: nothing was waiting · Option B is not part of the design.
```

## 9. Notes for the plan (RV3)

- Order: step log and observer with checks S1, S2, S4; route preview with S3 and S3b; the pure view model with S5;
  the Tk window and replay with S6; live following with S7; walk and sim wiring with S10; the no-key check N1; then
  the checks with Hong.
- The code-shape rule of section 5.2 is a plan-review item for the observer task: the reviewer reads the `walker.py`
  diff and confirms every call site is guarded and builds its event inside the guard.
- The view will never show a jump: the planner has no jump move (section 3.2). Hong was told this on 2026-10-27; if he
  later wants jumps, that is a planner question, not a view one.
- Feature D wants "what the bot was doing — current leg, state" for its incidents
  (`docs/superpowers/specs/2026-10-21-p1d-watching-and-logging-design.md:13`); `steps.jsonl` can serve it later.
- Verdict questions for the plan reviewer: mechanisms chosen by RV2g (the second fix round), not by a reviewer.
  1. Spawn flags `CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP` on `sys.executable`, chosen over the venv's
     `pythonw.exe` (section 5.9 step 2). Right choice, given that N1 tests it before L1?
  2. The claim file `view.claim`, created with `open(path, "x")` by whichever of the viewer and the walk comes first
     (section 5.9 step 3), and the 2 s extra wait when the walk finds the claim taken. Is the exclusive create sound on
     the run folder's disk (local NTFS)? Are the two accepted leftovers acceptable: a viewer that stalls over 2 s
     between its claim and its show, and a hidden viewer left running with no window after a hard-killed walk? (A
     possible fix for the second: the walk passes its process id and a hidden viewer also shows once that process
     has exited.)
  3. The style lifecycle (section 5.7): no taskbar button while live, then `WS_EX_NOACTIVATE` cleared and
     `WS_EX_APPWINDOW` set at `end`.
  4. `-u` for the viewer's output (section 5.9 step 2) rather than sending its status lines to stderr.
  5. S3b's exact every-hop check from the preview's assumed landing points, with the real-landing comparison as a
     reported rate only (section 7): does it leave any planner behaviour on later hops unpinned?
  6. `steps.jsonl` opened only after `stop.start()` succeeds, so `end.code` is `main`'s return value or null
     (section 5.9 step 1). A walk that fails before that point then writes no `map.csv`, `minimap.png` or
     `steps.jsonl`, as it writes no `session.json` today; acceptable?

## 10. Merge into `p1c-runes` (for the plan)

This branch is to be merged into `p1c-runes` later (H47). Compared at `95641ca` against `5d1176a`, the two branches
differ under `src/route`, `src/input` and `pyproject.toml` only in `pyproject.toml` (RV2), and the rune feature's
walker hook (task C19) is not built yet. What the merge must handle:

- **`src/route/walker.py`.** Both branches edit it. C19 adds a frozen `BetweenLegs(lap, leg, record)` dataclass, a
  `WalkerConfig.between_legs` field (last, default None) and a call right after each `self._sink.write_leg(record)` in
  `_run_laps` (`walker.py:248`, `:259`), plus one bullet in the module docstring's "Where the plan is silent" list
  (`docs/superpowers/plans/2026-10-27-p1c-runes.md:2273-2280` on `p1c-runes`). This spec adds the observer
  `Protocol`, the keyword-only `observer` constructor argument and the call sites in `walk_leg`. Expected text
  conflicts: the docstring list, and the neighbourhood of `WalkerConfig` and `__init__`. Rules for resolving them: the
  observer stays a constructor argument, not a `WalkerConfig` field; the event order at a leg boundary is `leg_end`
  (in `walk_leg`) -> `write_leg` -> `between_legs` -> the next leg's `leg`.
- **The view is blind during a rune trip.** The hook runs the trip on the walker thread and sends keys the observer
  never sees, so the view shows a gap between `leg_end` and the next `leg`. A `trip` event is Feature C's follow-up,
  not part of this spec.
- **`src/route/CLAUDE.md:23`, the freeze line.** C19 rewrites it to state its status as ruled and its one exception,
  `between_legs` (`p1c-runes.md:2329-2332`). After the merge it names two exceptions: `between_legs` and this spec's
  observer, each with its `walker.py` line. This spec's other `src/route/CLAUDE.md` additions (the observer, the seam
  on the planner's private helpers) go in the same "Traps" section.
- **`pyproject.toml`.** `p1c-runes` adds contracts `"g route not rune_solver"` and `"h rune_solver not gui/main"`.
  This spec's two contracts are named by what they do, not by a letter (section 5.8), so the names do not collide;
  `src.rune_solver` is already in the route_view contracts' lists (section 5.8). Contract f is the same on both
  branches.
- **The rune CLI (C20)** builds its own `RouteWalker` and its own run folder. Because `observer` is keyword-only and
  last, its construction stays valid; it passes no observer, so a rune run has no view unless C20 is wired for one
  later (Feature C's follow-up). C20 copies `walk.py`'s shape (`p1c-runes.md:2380`, `:2399` on `p1c-runes`), not its
  code, so after the merge a rune run also writes no `minimap.png`, `map.csv` or `steps.jsonl`. H52 says every walk
  saves `minimap.png`; whether that covers rune runs is flagged to Feature C, not re-decided here.
- **Root `CLAUDE.md`.** `p1c-runes` edits it (tasks C4 and C20, `p1c-runes.md:786`, `:803`, `:945`) and this spec
  adds the route view's paragraph and its `src/route_view/CLAUDE.md` pointer (section 6): expect a text conflict;
  keep both.
- **`.basedpyright/baseline.json`.** Both branches regenerate it, so it will conflict. Do not merge it by hand: take
  either side, then rewrite it on the merged code as `tests/CLAUDE.md` ("basedpyright baseline") says, and check that
  the entry count did not rise.

## 11. Review record (RV2, applied by RV2f; RV2r, applied by RV2g)

The spec review is `notes/RV2.md` (orchestrator workspace, `.superpowers/orchestrator/2026-10-21-phase1/`). Its
verdict: approved with fixes, 0 Critical, 7 Important, 12 Minor; D3 and D5 agreed, D4 agreed on condition that S3 is
widened. Every finding is applied; none is rejected.

| Finding | Change |
|---|---|
| I1 focus race at start | Section 5.7 (window up before the countdown; non-activating first show with `SW_SHOWNOACTIVATE`), 5.9 step 3 (ready file, 15 s bounded wait, the walk goes on with one console line), L4 cold start |
| I2 viewer shares the console | Section 5.9 step 2 (started with `DETACHED_PROCESS`, new process group, stdin closed, output to `view.log`); S10 checks the arguments; L1 adds the Ctrl+C case |
| I3 no run-end signal, unguarded reads | `end` event (sections 5.2, 5.9; the simulator writes it too); live mode switches to replay on `end` and shows "still running or crashed" after 10 s without a line (5.4); `minimap.png` written with `os.replace`, failed decodes retried (5.2) |
| I4 later hops unverified | New check S3b; section 5.3 says what the view adds and how it is pinned; the "route changed" line uses S3b's comparison |
| I5 S3 misses planner branches | S3 widened: a node-offset run, a hand-built agreement table forcing each of the six branches, optional `b21-test1` by hand; D4 block updated |
| I6 no no-game check of the walk wiring | New check S10 |
| I7 L1 cannot pass | L1 reworded ("within about 0.3 s of being planned; it may show just after the keys"); section 2 states it is a consequence of H48, not a new decision |
| m1 freeze citation | D3 block and `docs/decisions.md` H53 cite B14-inv-D1's "until the fix lands", commits fe51e9b and b5c7a48, and `ledger-C.md:85` |
| m2 timestamps the walker lacks | Section 5.2: `sent` and `settled` call `now_ns()` inside the observer branch, `leg_end` uses the record's `end_ns`; each event's `t_ns` source is in the table. S4's "same `t_ns`" holds for `planned`, which is the only event it compares by time |
| m3 "provably unchanged" | Replaced by the code-shape rule (section 5.2), a plan-review item (section 9); D3 block and H53 reworded |
| m4 observer type and argument | Section 5.2: `Protocol` in `walker.py`; `observer=None` keyword-only and last; not a `WalkerConfig` field |
| m5 contract f | Section 5.8: `step_log` and `route_preview` join contract f |
| m6 "src.input beyond protocol" | Section 5.8 lists the allowed and forbidden modules, forbids `walker`, `walk`, `moves`, `runtime`, `sim`; section 5.7: ctypes only inside functions |
| m7 how the hash is taken | Section 7 baseline paragraph (raw CRLF stdout through a bash pipe; re-measured by RV2f) |
| m8 sim `map.csv` | Section 5.2: the CSV the graph was built from, the shifted copy with node offsets |
| m9 which HWND, real window | Section 5.7: the style on the `wm frame` HWND; the no-key check uses the real viewer with slider and buttons |
| m10 who clicks | Section 5.7: Hong, or an agent only with the PC idle; listed as N1 in section 7 |
| m11 frame decoding, frame gaps | Section 5.4: only the newest frame decoded per poll; L2 reports frame gaps |
| m12 merge into `p1c-runes` | New section 10 |
| [fyi] for Hong: the view shows a move as or just after its keys are sent | Section 2; the orchestrator decides how Hong is told |

The re-review is `notes/RV2r.md` (same workspace). Its verdict: approved with fixes, 0 Critical, 3 new Important
(I8-I10), 6 new Minor (m13-m18); all 19 RV2 findings applied, I2 and I4 only partly (hence I8 and I10). Applied by
RV2g; none is rejected. The new mechanisms are verdict questions for the plan reviewer (section 9).

| Finding | Change |
|---|---|
| I8 the viewer gets a console window of its own under the venv | Section 5.9 step 2: `CREATE_NO_WINDOW \| CREATE_NEW_PROCESS_GROUP` instead of `DETACHED_PROCESS` (with the reasons, and why not `pythonw.exe`); section 1; S10 asserts the exact flags; N1 (section 5.7 and section 7) gains the spawn part: no console window, Ctrl+C in the parent's console does not reach the viewer, the viewer outlives its parent |
| I9 a late viewer can show its window mid-walk | Section 5.9 step 3: the `view.claim` file, created exclusively by whichever side comes first; a viewer that loses it stays hidden and opens at `end`; leftovers stated; section 5.4; section 5.7 names plain `deiconify` as the first-show fallback if N1 fails; S10 and L4 cover the late case |
| I10 S3b checks hop 2 only and is flaky by construction | S3b rewritten: an exact check of every hop k >= 2 against `next_move` at the landing point the preview assumed (no tolerance); the real-landing comparison is a reported agreement rate, not pass/fail; section 5.3 (each hop carries its assumed landing point; why a real landing may correctly change the route) |
| m13 `end.code` not readable as defined | Section 5.9 step 1: `steps.jsonl` opened only after `stop.start()` succeeds; `code` is `main`'s return value, null when an exception is leaving `main` (`sys.exc_info()` in the `finally`, measured); event table; section 5.2; S10 |
| m14 style lifecycle under `WS_EX_NOACTIVATE` | Section 5.7: no taskbar button while live; at `end` the viewer clears `WS_EX_NOACTIVATE` and sets `WS_EX_APPWINDOW`; N1 checks the switch; section 5.4 |
| m15 `src.rune_solver` "once it exists" | Section 5.8: listed now, with grimp's evidence and why a missing module would break import-linter |
| m16 section 10 omissions | Section 10: rune runs write no `minimap.png`/`map.csv`/`steps.jsonl` (flagged to Feature C, H52 not re-decided), root `CLAUDE.md` conflict, the basedpyright baseline; section 6 adds `src/route_view/CLAUDE.md` and the root `CLAUDE.md` lines |
| m17 `view.log` buffering | Section 5.9 step 2: the viewer is started with `-u` (measured: stdout write-through with `-u`, block-buffered without); S10 asserts it |
| m18 H53 "Affects" stale | `docs/decisions.md` H53: contract f widened plus two new contracts |

## 12. As built (RV-11, written 2026-10-28)

This section says what the code at `21040ce` (the tip of `p1-route-view` before this section) does, where that is not
what sections 1-11 say. Where this section and an earlier one disagree, this section is right. Every `file:line` was
read from the code at that commit. N1 and L1-L4 are not run yet (section 12.5).

### 12.1 Commits (RV-1 to RV-10)

| Task | Commit | Subject |
|---|---|---|
| RV-1 | `8bd6a74` | feat(route): step-log format and reader for the route view |
| RV-1 | `48197b3` | fix(route): step-log reader review minors (Mapping import, leg `to`, test gaps) |
| RV-4 | `0109062` | feat(route): route preview of the planner's route and hops, with no random draw |
| RV-4 | `980cef9` | fix(route): route preview review minors and the two survived mutants |
| RV-1 | `b1ca031` | fix(route): step-log reader and encoder never raise and refuse non-int positions (RV-1 judge 2 m1-m3) |
| RV-2 | `17ad495` | feat(route): optional step observer in the walker and the step-log writer |
| RV-2 | `7f2735d` | fix(route): step-log writer closes steps.jsonl when its last flush fails; a failed start leaves no file |
| RV-3 | `195e5ad` | feat(route): the simulator writes steps.jsonl and map.csv with --out |
| RV-3 | `360f1ae` | fix(route): RV-3 review minor: close the leg log even if ending the step log raises |
| RV-6 | `495c24f` | feat(route-view): view model, why lines and the route_view import contracts |
| RV-6 | `d45fa25` | fix(route-view): RV-6 review minors: tests for the unpinned rules, the real cost 4.375, wrap long lines |
| RV-5 | `87080de` | test(route): route preview agrees with the planner over both 20-lap sim runs (S3, S3b) |
| RV-7 | `57e29e2` | feat(route-view): the route view window and replay |
| RV-7 | `5378cc1` | fix(route-view): RV-7 review fixes: pin the window's picture and geometry, clear a stale read status |
| RV-8 | `c9b93b1` | feat(route-view): live following, the no-activate first show and the claim/ready handshake |
| RV-8 | `7a9dd3b` | fix(route-view): RV-8 review minors m1-m4 (no lost lines on a failed tick, dead keyword, test name, two tests) |
| RV-9 | `798f3e3` | feat(route): walk --view starts the route view; every walk writes steps.jsonl, map.csv and minimap.png |
| RV-9 | `909efbc` | fix(route): walk records its exit state without sys.exc_info; tests for the walk's other claim OSError and the non-Windows spawn flags |
| RV-8 (found by the RV-9 review) | `5f4e029` | fix(route-view): a rejected event is retried once then skipped; the follower's end runs at most once |
| RV-10 | `1657526` | feat(route): N1 driver tool for the route view's no-key check |
| RV-10 | `4b03b20` | fix(route): view_check records lines_fed as the thread fed it, and judges a ran-out feed at the end of the wait |
| RV-10 | `ce9a6e3` | test(route): view_check tests for the 11 mutants the RV-10 review found alive; test 12 is slow |
| RV-10 | `7ad61d3` | fix(route): view_check stops cleanly on a closed stdin and reports a Ctrl+C that could not be sent |
| RV-10 | `75744de` | docs(route): view_check's docstring states the own-console rule for its Ctrl+C check |

The plan is `73b7559`. RV-11 (the docs) is `1a2be63`, `a3cb445`, `21040ce` and the commit that adds this section.

### 12.2 Where the build departs from the text above

1. **`close` of the step log: the writer thread closes the file, not `close` (the plan's V3 text; RV-2-D1, ruled A;
   the reviewer found it sound once the failing-last-flush finding I1 was fixed).** The plan's V3 text says `close` marks the log closed, joins
   the thread for at most `join_s`, and only then flushes, fsyncs and closes the file. As built the writer thread does
   the flush, the fsync and the close itself when it reaches its stop marker (`_drain`, `step_log.py:588`; the file is
   closed in a `finally`, `step_log.py:607-613`; the class note is `step_log.py:32-38`). So `close` (`step_log.py:570-586`)
   keeps its promise of waiting at most `join_s`, and it never touches a file that a slow writer still holds. After a
   join timeout the thread still finishes the queue and closes the file; `close` returns the first write failure, else
   "writer still busy after N s". The writer closes the file even when its last flush fails (a full disk, commit
   `7f2735d`). Calls after `close` are dropped (RV3-D2 A2). The plan text is not edited; this is the correction.
2. **What the view draws, bottom to top (RV-7 m4).** The plan lists: background, nodes with labels, target ring, route
   line, hop numbers, recent moves, aim, character. `_draw` (`window.py:518`) draws: the minimap picture (`:524-526`),
   the faint recent moves (`:528-530`), the route line (`:531-533`), the nodes and their labels (`_draw_nodes`, called at
   `:534`, defined at `:551`), the target ring (`:537`), the hop numbers (`:540`), the aim (`:543`), the character
   (`:547`). So the route line and the recent moves lie under the node dots, and the dots and their numbers stay
   readable. The plan's list was not meant as a draw order. Only "the picture is the lowest item" is pinned by a test
   (`tests/route_view/test_view_window.py:663`); how the whole looks is judged by eye (L2).
3. **A stale docstring in a test (RV-3 minor; fixed in RV-final-fix).** `tests/route/test_sim_steps.py:6-7` said "The
   last three tests pin what the plan leaves open: an unregistered character class, a walker that raises, a failing
   close". Five tests follow test 10 (`:361`, `:371`, `:387`, `:406`, `:424`); the docstring named three of them. It now
   says "The last five tests" and names all five (`:6-8`).
4. **What N1 clicks (RV3-D7 A, plus the title bar).** Section 5.7 says to click "the viewer's canvas, a button and the
   slider" (spec line 496). The button and slider exist only in the replay bar, which is hidden while live
   (`set_live`, `window.py:234-240`; section 5.4 "Replay only"), so the no-key check clicks the controls the live
   window has: the map, the why log's text, its scrollbar, and the title bar (`CLICKS`, `view_check.py:72`). For the
   scale buttons this rests on item 6.
5. **"Route changed" (RV3c, RV3-D8 B; judged in RV80-ADJ).** The test compares the move's kind and direction, not the
   walk's length (`route_changed`, `model.py:218`), and the line names the move as well as the node: "route changed:
   expected next <move> toward <node>, now <move> toward <node>" with the bracket from section 5.4 (`model.py:528`). Both
   differ from section 5.4's table row ("expected next <node>, now <node>", spec line 402).
6. **The scale buttons are in the replay bar, so they are hidden while live (RV80-ADJ-D1).** This is open with Hong
   and may change on his answer.
7. **A folder with no `end` follows live unless opened with `--replay` (RV4-D1 A).** A silent live view says so in its
   status line and names `--replay` (`REPLAY_HINT`, `follow.py:39`, set at `follow.py:195`); `--replay` with `--ready`
   is refused with exit 2 (`__main__.py:62-64`). Option B (the walk passes its process id) is not built.
8. **The step-log header has two fields that section 5.2's example (spec lines 275-276) does not: `wall_ns` and
   `perf_ns` (RV4-D2 A).** They are the wall clock and the run clock read back to back (`step_log.py:99-100`, written
   at `:163-164`, optional on read at `:253`). They are null in simulator and legs-only headers. Nothing reads them yet.
   This extends section 5.2 (RV3-D6's option C calls exactly this a spec change; RV6 m4).
9. **Section 5.7's measurement that `wm frame` is `GetParent(winfo_id)` holds only after Tk's first idle pass (RV6 I1).**
   Before it, `wm frame` is the root's own window. So the viewer runs `update_idletasks()` before the claim
   (`__main__.py:91`) and `wrapper_hwnd` refuses with `Failure("no wrapper yet")` rather than return the inner window
   (`win_focus.py:60-71`).
10. **The N1 tool's exit codes and Ctrl+C outcomes (`view_check.py`, as built; ruled final).** Exit codes (module
    docstring, `view_check.py:25-27`):
    - 0: every check passed.
    - 1: a check failed or was not done.
    - 2: usage, an `--out` outside `data/` or already used, not Windows (without `--dry-run`), or standard input
      closed at one of the tool's two questions (`view_check.py:752-755`). In that last case `n1.json` is still written
      with `stopped_by_no_input` and the checks not yet made are "not done" (`view_check.py:663-667`).
    - 3: MapleStory is running, or the process list cannot be read (`view_check.py:733-736`). This check runs before
      `--dry-run` is looked at, so even `run --dry-run` exits 3 with MapleStory running (H54 in `docs/decisions.md`
      does not change the tool).

    The Ctrl+C check has three failing outcomes (`view_check.py:634-640`): a detail that starts
    `GenerateConsoleCtrlEvent failed:` (the signal could not be sent; exit 1), "the Ctrl+C was not delivered", and
    "the viewer ended". Only the last means the spawn is wrong, and only it goes back to section 5.9 step 2; the first
    two mean the check could not run: fix the launch (a fresh console window of its own, `view_check.py:19-23`) and run
    again, as for an exit 2 with standard input closed. A taskbar click that never comes is "not done", not a failure
    (`view_check.py:659-661`).

### 12.3 Rulings built, in plain words

- **The step log imports only the standard library and `returns` (RV3-D1 A).** `step_log.py:7`, imports `:41-54`.
- **A walk that cannot write its run files walks on without them (RV3-D2 A).** If `map.csv`, `minimap.png` or
  `steps.jsonl` cannot be written, the walk prints one line and walks on with no step log and no viewer; exit codes do
  not change (`walk.py:394-409`; the viewer starts only if the log is open, `walk.py:423`). A1: the step log is closed at once, no viewer is started and no `view.claim` is
  made. A2: calls to the step log after `close` are dropped.
- **`steps.jsonl` is opened with `"xb"` (RV3-D3 A, with A3).** A simulator `--out` folder that already has one is a
  `run_sim` Failure and the run exits 2 (`step_log.py:494`, `sim/run.py:288-290`). A3: it is refused before anything in
  the folder is touched. A `run` line anywhere but line 1 is a bad line (`step_log.py:353`).
- **The preview applies its own copy of the planner's steps from each assumed landing (RV3-D4 A).** S3b pins it
  (`route_preview.py:38-52`).
- **S3b: each hop equals `next_move` at its assumed landing, and the preview from hop k-1's landing gives `hops[k:]`
  (RV3-D5 A2).** `follows(path, hops)` (`tests/route/preview_checks.py:75`) is pinned to the nine FF2 pairs and only
  reported on the simulator runs.
- **The why line's time is elapsed `mm:ss.mmm` (RV3-D6 A, provisional).** `h:mm:ss.mmm` from one hour (`model.py:404-409`).
  Open with Hong. The header's clock anchor is recorded and not read (item 8 above).
- **The no-key check clicks what the live window has, plus the title bar (RV3-D7 A).** Item 4 above.
- **"Route changed" compares kind and direction (RV3-D8 B).** Item 5 above.
- **`--replay` is refused with `--ready`, and a silent live view names `--replay` (RV4-D1 A).** Item 7 above.
- **The header carries `wall_ns` and `perf_ns` (RV4-D2 A).** Item 8 above.
- **The scale buttons hidden while live (RV80-ADJ-D1) is open with Hong.** Item 6 above.

### 12.4 Measured values

- **S1:** the simulator's 20-lap report is unchanged: `python -m src.route.sim.run --laps 20 --seed 1
  --min-single-error-legs 0` through a bash pipe has the SHA-256 `d982184e54311493311110657d93992fe7f0af4963dc603617c5a0f4d09d8035`
  (section 7, baseline paragraph).
- **S8:** the observer costs the walker thread a median of 0.90 us per event over 1566 events (limit 20 us), and `close`
  returned in well under 1 s (commit `195e5ad`).
- **S3 and S3b** (`tests/route/test_preview_sim.py`, commit `87080de`; FF2 map, seed 1, 20 laps). Plain run: 379 planned
  steps, no `no_plan`, 705 hops, 0 mismatches. Node-offset run: 356 planned, 645 hops, 0 mismatches. Hops that leave
  the planned route at an assumed landing: 0 of 379 and 0 of 356. At the real landing the preview agrees with the
  earlier one in 204 of 239 pairs (plain) and 196 of 216 (offset); the view model's "route changed:" lines over the
  same two folders number 35 and 20, which is 239 - 204 and 216 - 196.
- **Test sizes** (2026-10-28, at `21040ce`): `python tools/check.py --full` 1921 passed, 10 skipped (pytest 112.8 s,
  120.1 s with the other three checks); `python tools/check.py` 1857 passed, 10 skipped, 64 deselected (pytest 52.4 s,
  59.7 s in all). `tests/route_view` is 136 tests and `tests/route` 890 (`tests/CLAUDE.md`, "Speed", has the times).

### 12.5 The plan reviewer's answers, and what is not run yet

- **Section 9's six mechanisms (plan review, `notes/RV4.md`, "Verdict questions" 1-4): yes to all six as asked.** The
  reviewer's answers: the claim file is sound (`open(path, "x")` is an atomic create on local NTFS); the flags
  `CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP` over `pythonw` are right; the every-hop S3b is right; `steps.jsonl`
  after `stop.start()` is right (a walk that fails earlier has nothing to replay); `-u` is right; `FIRST_SHOW` as a
  one-line switch to `"deiconify"` is right, with the caution that a misread `ShowWindow` return would push N1 onto
  that fallback for the wrong reason. The style lifecycle (question 3) has no separate answer in the review; N1 checks
  it. The two open cases (a viewer that stalls over 2 s between its claim and its show; a hidden viewer left running
  after a hard-killed walk) are acceptable as open, to be reopened only if N1 ends on `FIRST_SHOW = "deiconify"`. The
  15 s ready wait that ignores F12 and Ctrl+C is acceptable (it costs up to 15 + 2 + 5 s before the walk ends).
- **N1: pending (H-1).** The tool is built and its dry run is covered by a test with no Win32 call
  (`tests/route/test_view_check.py:294`); the check with real clicks has not been run.
- **L1-L4: pending (H-2 to H-4).**
