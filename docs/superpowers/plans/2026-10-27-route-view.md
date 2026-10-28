# Plan: Route view (Feature B follow-up): see what the route walker plans and why

**Status: draft, all pieces written (1, 2, 2b, 3 and 4); ready for plan review** · written 2026-10-27 by unit RV3 for branch
`p1-route-view` (cut from `p1b-route-walking` at `5d1176a`; spec reviewed and fixed at `f807b0f`). Piece 1 wrote the skeleton (everything
above "Tasks") and the full text of RV-1, RV-2 and RV-3. Piece 2 (unit RV3b) wrote RV-4 and refined V4 (the landing
rule, field docs), hard rule 6 (`route_preview` may import `src.input.protocol`) and the Decisions section (RV3-D4
decided, RV3-D5 raised); RV-5 moved to its own piece 2b on sizing. Piece 3 (unit RV3c) wrote RV-6, RV-7 and RV-8,
V5 in full, and raised RV3-D6 (why-line times). Piece 2b (unit RV3b2) wrote RV-5 and applied the RV3-D5 ruling (A2,
marked decided): RV-4's D1 text and tests 9-10 (`_rejoins` replaced by `follows`, pinned to 9 FF2 pairs; the S3b
helpers in `tests/route/preview_checks.py`), V4's `path` doc and the stub-generator condition, and in V5 (piece 3's
contract) the route-changed rule (`hop_route`, new) and the panel's Route line, with RV-6 test 10 and the k=20 note;
RV-5 now depends on RV-6. Piece 4 (unit RV3d) wrote RV-9, RV-10, RV-11 and H-1..H-4 in full, V7 in full, the
"Not verified at planning time" section, and applied the RV3-D2 and RV3-D3 rulings across the plan: V3 drops calls
after `close()` (A2; RV-2 test 12), RV-3 refuses a used `--out` before touching it (A3; RV-3 test 9), RV-9 closes
the step log at once and starts no viewer when step 1 fails (A1), and V1 counts a second `run` line as a bad line
(RV-1 test 5; RV-8's `extra_headers` folded into `bad_lines`, test 7). RV3-D1, D2, D3 are marked decided; RV3-D6
stays open for Hong (A built); RV3-D7 raised (which live widgets N1 clicks; provisional A). The split and each
piece's contract are in `.superpowers/orchestrator/2026-10-21-phase1/notes/RV3-plan.md`.
The plan review (RV4, `notes/RV4.md`: approved with fixes) was applied by unit RV5, with the orchestrator's rulings
(`notes/RV80-ADJ.md`): RV4-D1 A (`--replay` for a folder with no `end`), RV3-D7 A + title bar (decided), RV3-D8 B
("route changed" ignores walk length), RV4-D2 A (wall-clock anchor in the header); RV4's I1-I3 and m1-m9. Two items
stay open with Hong and are built as written: RV80-ADJ-D1 (scale buttons hidden while live, RV-11 As-built) and
RV4 I4 (which tasks get a comparison arm, "Comparison units").

> **For agentic workers:** one fresh implementer per task, in the order of the task list and its Depends column. Each
> task is TDD: write the listed tests first, watch them fail, implement, watch them pass, run the full suite, commit.
> Steps use checkbox (`- [ ]`) syntax. A task's text is complete on its own: read the Contracts it names, not other
> tasks. REQUIRED SUB-SKILL: superpowers:subagent-driven-development.

**Spec:** `docs/superpowers/specs/2026-10-27-route-view-design.md` (executors read it with this plan).

**Goal:** a "Route view" window of its own, beside the game, that shows on an enlarged copy of the minimap the
rotation (blue) nodes numbered in rotation order with the next target highlighted, the route the bot expects to take
as a line through the in-between nodes, the move and its keys on each hop, and a "why it chose" log (position, move
sent, why it planned again). It works live while `walk --view` runs and as a replay of any saved run folder,
including runs that saved no frames (`b21-test1`) and simulator runs. What the bot does does not change.

**Architecture** (spec section 5.1):

```
 walk process (threads unchanged)                      route view process (new, python -m src.route_view)
 main: RouteWalker.walk_leg ── observer call ──┐         Tk main thread, every 100 ms:
                                               │ queue     read new complete lines of steps.jsonl and
 step-log thread (new) <───────────────────────┘           frames/index.jsonl, decode the newest frame, redraw
   one JSON line per event -> steps.jsonl                route preview (pure): rebuild the route graph from
 record thread (existing, --record, on with --view)        map.csv + logged params, recompute the route for the
   frames/frame_%06d.png + index.jsonl ──── files ────>    shown step with the planner's own helpers (no rng)
 minimap-capture (existing, unchanged)
```

The two processes share only files in the run folder. `walk --view` starts the viewer as a child process by module
name, waits (at most 15 s, plus 2 s if the claim is taken) for its window, then counts down. Same viewer code replays a
finished folder.

**Tech stack:** Python 3.14.0 venv · numpy · networkx · returns · opencv-python · Pillow (pinned already,
`requirements.txt:6`) · tkinter · ctypes (viewer only, inside functions). No new dependency.

**Binding inputs:** spec `docs/superpowers/specs/2026-10-27-route-view-design.md` at `f807b0f` (cited `spec 5.2`,
`spec S3`, ...); `docs/decisions.md` H47-H52 (Hong's, fixed) and H53 (orchestrator rulings D3, D4, D5, binding);
"Hong's standing facts" (`docs/decisions.md:9-13`); root `CLAUDE.md`, `src/route/CLAUDE.md`, `src/input/CLAUDE.md`,
`tests/CLAUDE.md`; plan B's hard rules (`docs/superpowers/plans/2026-10-23-p1b-route-walking.md:43-`).

---

## Hard rules for every task (read before starting any task)

1. **Branch.** All work is on `p1-route-view`. Never commit to `master`, `p1b-route-walking` or `p1c-runes`; never
   merge, rebase or force-push. The later merge into `p1c-runes` (H47) is not a build task ("Merge into p1c-runes"
   below lists what it must handle).
2. **No game, no keys.** No task sends keys or needs MapleStory. Nothing here runs `python -m src.route.walk` against
   a board. Every walk-side test goes through `walk.py`'s seams (fake capture, positions, link, clock, stop control;
   `walk.py:87-111`) and RV-9's new viewer-start seam. The only tasks that need the game or real mouse input are the
   [Hong] tasks at the end.
3. **Frozen files.** `src/route/{params,terrain,route_graph,locate,planner,moves,walker}.py` and their tests
   (`test_params`, `test_terrain`, `test_route_graph`, `test_locate`, `test_planner`, `test_moves`,
   `test_walker_leg`, `test_walker_run`) are frozen (`src/route/CLAUDE.md`, "Traps"). The one change this plan makes
   to them is RV-2's observer in `walker.py` (RV1-D3 A, H53). No task edits a frozen test file; new tests go in new
   files. `route_preview.py` calls the planner's private helpers (RV1-D4 A, H53) and never edits `planner.py`.
4. **No observer means no change** (spec 5.2, the code-shape rule). Every observer call site in `walker.py` is
   `if self._observer is not None:` with everything the event needs, including any `clock.now_ns()` call, built
   inside that branch. With `observer=None` the walker does one attribute test per call site and nothing else. The
   sim smoke stays byte-identical (S1) after every task that touches `src/route`.
5. **Error handling.** Code in `src/route` (and the view model) returns `returns.result` `Success`/`Failure` and
   checks with `is_successful` (root `CLAUDE.md`, "Error handling"). The writer thread and the viewer's poll loop
   never raise into their caller; they report a failure once and go on.
6. **Imports.** Rooted at `src.`. `src/route/step_log.py`: standard library and `returns` only (RV3-D1, decided
   A; it joins the bottom layer of contract f). `src/route/route_preview.py`: `planner`, `locate`, `route_graph`,
   `terrain`, `params`, `src.controller.node`, `src.input.protocol` (for `Key`, as `planner.py:17`), `returns`,
   `networkx`, `numpy`. `src/route_view/` may import what spec
   5.8 lists and nothing it forbids; import-linter enforces both route_view contracts (RV-6 adds them). `walk.py`
   starts the viewer by module name and imports no `src.route_view` module. ctypes and every Windows call happen only
   inside functions (spec 5.7), never at import.
7. **Size.** A task changes at most about three files of real code; tests, fixtures and docs do not count (spec 6).
8. **Naming.** New code is snake_case. `walker.py` keeps its style.
9. **Tests.** Test file basenames are unique across `tests/`; no `__init__.py` or `conftest.py` (`tests/CLAUDE.md`).
   New route tests go in `tests/route/`, viewer tests in a new `tests/route_view/`. Tk tests follow
   `tests/controller/test_gui_startup.py` (real withdrawn root, no mainloop, skip if Tk cannot open). A test of about
   0.5 s or more, or one that starts a subprocess, is marked `@pytest.mark.slow`.
10. **Commands.** From the repo root with the venv active. One file: `python -m pytest -q <file>`. Before commit:
    `python tools/check.py --full` (ruff, basedpyright with `.basedpyright/baseline.json`, lint-imports, every test):
    all pass except the 10 board-only skips in `tests/input/test_protocol.py`.
11. **The sim smoke hash (S1).** In Git Bash from the repo root:
    `python -m src.route.sim.run --laps 20 --seed 1 --min-single-error-legs 0 | sha256sum` must print
    `d982184e54311493311110657d93992fe7f0af4963dc603617c5a0f4d09d8035` (raw CRLF stdout; spec 7). Never hash a file
    written by PowerShell 5.1's `>` (UTF-16).
12. **Library behaviour.** Before relying on a Tk, Pillow, ctypes/Win32, `subprocess` or `queue` behaviour, ground it
    (installed source, official docs, or a throwaway run) and cite `file:line` or the URL in the commit body
    (`~/.claude/CLAUDE.md`).
13. **Commits.** Conventional with a scope: `feat(route): ...`, `feat(route-view): ...`, `test(route): ...`,
    `docs(route-view): ...`. One commit per task unless the task says otherwise. Never `git add -A`: add the task's
    files by name.

### Review Focus (inputs the spec implies but does not name; each has a test in its owning task)

1. A folder from a walk that crashed or was killed: `steps.jsonl` ends in a torn line and has no `end`. The reader
   skips the torn line and never raises. `--follow` alone follows it live (nothing in the folder tells a crashed walk
   from a running one), and after 10 s without a line it says so in its title and names `--replay` in its status
   line. `--follow <folder> --replay` replays what is complete under the title `REPLAY (did not end)` (RV4-D1 A;
   RV-1 test 10; RV-7 test 14; RV-8 tests 4 and 9g-9i).
2. An old or moved run: the map path in `session.json` no longer exists or its sha256 differs, or `session.json`
   lacks a key an older walk did not write. `read_run` still succeeds where it can (or fails naming the missing key);
   the view draws with the warning line or a dark canvas, never a traceback (RV-1 test 11; RV-6 in piece 3).
3. Hong closes the view, or the viewer crashes, in the middle of a walk: the walk goes on and its exit code is
   unchanged (RV-9 in piece 4, a fake viewer that exits at once; H-2).
4. A frame PNG or `minimap.png` that is half written or corrupt when the viewer reads it: it keeps the last good
   picture and retries on the next poll (RV-7 and RV-8 in piece 3).
5. A long run: an hour of walking gives about 50,000 `steps.jsonl` lines and 36,000 frames. `read_run` and the
   viewer's load stay fast enough to scrub (RV-1 test 12; RV-6 and RV-8 in piece 3 bound the load and the per-poll
   work).

### The TDD steps every task follows

- [ ] **T1** write the tests; **T2** run them, see them fail for the right reason (import error or assertion, not a
  typo); **T3** implement; **T4** run them, all pass; **T5** `python tools/check.py --full` (hard rule 10) and, for a
  task that touches `src/route`, the S1 hash (hard rule 11); **T6** commit with the task's message.

---

## Grounding (read 2026-10-27 at `f807b0f`; the code under `src/` is unchanged from `5d1176a`)

Confirmed by reading the file this session:
- `walker.py:57-64` defines `Clock` and `PositionSource` as `Protocol`s; `:92-102` is `RouteWalker.__init__` with
  `config` last; `:117-204` is `walk_leg`: `start_ns` :123, the stop check :130, `now` :133, the stall :137-139, the
  read :141 and `read_ns` :142, the failed read :143-147, `next_move` :150 and its Failure :151-154, `move is None`
  :157-159, the progress update :161-163, the second stop check :165-167, `execute` :169, `MoveEntry` :171-180, the
  link-down break :181-184, `_settle` :186-187, the record :192-204. `_settle` reads its own clock at :284.
- `planner.py:184-247` `next_move` (P1 :197, P2 :200-201, P3 :204-218, P4 :221-235, P5 :238-247); the helpers
  `_is_at` :46, `_on_slope_segment` :58, `_starts` :68, `_walk` :86, `_run` :91, `_walk_run_end` :100, `_launch_x`
  :135, `_offered` :155, `_go_down` :168 (the only rng draw, :176). `Move` :29-32, `Decision` :36-39, `MoveKind`
  :22-25.
- `input_layer.py:39-44` `ActionRecord(plan, reaction, waited_ms, report)`; `reaction` is a `timing.Sample` with
  `.ms` (`timing.py:75-83`) or None without a stimulus; `plan.steps` is a tuple of `protocol.Step(delta_100us, mask)`.
- `leg_log.py`: strict JSON, binary append, `MoveEntry` field `direction` written as JSON `"dir"` (:87-97),
  `leg_to_json` :100-114; it imports `returns` (:25) and sits in the bottom layer of contract f with `params` and
  `character` (`pyproject.toml`, contract "f route layers").
- `sim/run.py:244-248` `run_sim(map_csv, terrain, laps, seed, noise, sim_params, params, out_dir, known_cases,
  character)`; `:291-297` opens the leg log only with `out_dir`; `:163-176` `_load_shifted_agent` writes the shifted
  CSV into a `TemporaryDirectory` that is gone when it returns; `:188-198` the recovery replay walkers;
  `:298-299` the run's walker; `main` :386 (`--out` default None); `SimLink.fail_next` `sim/link.py:70`.
- `walk.py:81` `_OUT_FILES`; `:87-111` the seams; `:249` `code = 0`; `:278` walker built; `:282` `stop.start()`;
  `:287-299` `session.json` (`"params": asdict(rg.params)`, `"class"`, `"map"`, `"map_sha256"`, `"git_head"`);
  `:300-301` the countdown; `:342-367` the cleanup steps; `:368` `return code`.
- `tests/route/` holds no `test_step_log*`, `test_route_preview*`, `test_sim_steps*` or `test_walk_view*` file; there
  is no `tests/route_view/`.

Not checked at planning time: whether Tk 8.6's wrapper HWND accepts `WS_EX_NOACTIVATE` after creation (N1 checks
it); the spawn flags' real behaviour (N1); Pillow's `ImageTk` with nearest scaling at 4x on the dev PC (RV-7 grounds
it).

---

## Plan choices (local, reversible, inside the spec; each is a review item)

1. **Observer type and name.** `StepObserver`, a `Protocol` in `walker.py` with one method per event (V2), plain
   values only, so `step_log.py` needs no `src` import and `walker.py` imports nothing new (spec 5.2).
2. **One guard helper.** `RouteWalker._emit(call, *args)` runs the observer method in `try/except Exception`; on the
   first exception it prints one line to stderr and sets `self._observer = None`, so every later call site's
   `is not None` test skips it (spec 5.2, last paragraph).
3. **`stall.best_cost_s` before any plan** is `math.inf` in the walker; strict JSON (as the leg log) cannot hold it,
   so it is written as `null` and the "why" line says "no plan yet".
4. **`sent` on a failed send:** `reaction_ms` and `waited_ms` null, `keys` `[]`. In the simulator
   (`use_stimulus=False`) `reaction_ms` is null and `waited_ms` 0.0 (`input_layer.py:84`).
5. **The simulator's run header:** `"source": "sim"`, `"clock": "sim_ns"` (spec 5.2), `"git_head": null` (the sim
   does not run git; the viewer then shows no "code changed" line), `"class"` the `--class` key, `"wall_ns"` and
   `"perf_ns"` null (virtual time has no wall-clock anchor; RV4-D2 A).
6. **`run_sim` gains `observer: StepObserver | None = None`** (keyword, last) for S2 and S3, which need events without
   a folder. `observer` and `out_dir` together is a `Failure` (one source of events per run).
7. **The shifted map copy** (spec 5.2, `map.csv` with node offsets): `_load_shifted_agent` also returns the shifted
   CSV's bytes, read inside its `with` block before the temporary folder goes. Rewriting it from the same offsets
   would draw from `rng_world` again and change the run.
8. **The legs-only reader** synthesises `leg`, then per `MoveEntry` a `planned` (with `toward` null) and a `settled`
   (`t_ns` null), then `leg_end`; a last move of a `LINK_DOWN` leg gets no `settled` (as S4 says of `steps.jsonl`).
9. **Viewer claim and ready only with `--ready`.** The walk always passes `--ready`; a viewer started by hand (replay
   of a finished folder, `b21-test1`) has no walk waiting for it, takes no claim and shows at once (spec 5.7: "A
   viewer opened on a finished folder ... never sets the no-activate style").
10. **Replay is `--follow` on a finished folder** (spec 5.7, N1: "a finished sim folder has its `end` and would open
    in replay"): the viewer CLI has one folder option, `--follow`; a folder whose `steps.jsonl` ends with `end`, or
    that has only `legs.jsonl`, opens in replay. The one exception is `--replay` (RV4-D1 A, V6), typed by hand for a
    folder with no `end` (a walk that crashed or was killed): it opens any folder as a replay.

---

## Contracts (the interfaces every task builds against)

### V0. Existing interfaces used

`RouteWalker`, `WalkerConfig`, `Clock`, `PositionSource` (`walker.py`); `next_move`, `Move`, `MoveKind`, `Decision`
and the private helpers listed in Grounding (`planner.py`); `attach`, `Anchor` (`locate.py:18-23`, `:84`);
`RouteGraph.build/path/cost_to`, `EdgeKind`, `down_options` (`route_graph.py`); `MovementParams`, `validate`
(`params.py`); `LegRecord`, `MoveEntry`, `LegOutcome`, `LEGS_FILE`, `LAPS_FILE` (`leg_log.py`); `MapleAgent.loadMap`,
`map_rotation_order` (`controller.py:90-94`); `run_sim`, `SimClock`, `SimLink`, `SimWorld`, `NoiseConfig`
(`sim/`); `ActionRecord` (`input_layer.py:39-44`).

### V1. Step-log format and reader (`src/route/step_log.py`) — RV-1

```python
STEPS_FILE = "steps.jsonl"; MAP_COPY = "map.csv"; SNAPSHOT = "minimap.png"
VIEW_LOG = "view.log"; VIEW_CLAIM = "view.claim"; VIEW_READY = "view.ready"
VERSION = 1
# Field names after "ev" and "t_ns", in this order, per event (spec 5.2 table). "run" has no t_ns.
FIELDS: dict[str, tuple[str, ...]] = {
    "leg": ("lap", "leg", "from", "to"),
    "read_failed": ("why",),
    "no_plan": ("pos", "why"),
    "reached": ("pos",),
    "stall": ("best_cost_s",),
    "planned": ("pos", "kind", "dir", "walk_ms", "cost_to_go_s", "toward"),
    "sent": ("input", "reaction_ms", "waited_ms", "keys"),
    "settled": ("pos", "stopped"),
    "leg_end": ("outcome", "misses", "off_graph", "stalls"),
    "end": ("reason", "code"),
}
EVENTS = tuple(FIELDS)  # every event name except "run"

@dataclass(frozen=True)
class RunHeader:            # line 1 of steps.jsonl: {"ev": "run", "version": 1, "source", "clock", "map",
    source: str             #   "map_sha256", "class", "params", "rotation", "git_head", "wall_ns", "perf_ns"}
                            #   in this key order
    clock: str              # "perf_counter_ns" | "sim_ns"
    map: str                # "map.csv" for walk and sim; the session's map path for a legs-only folder
    map_sha256: str | None
    character: str          # JSON key "class"
    params: Mapping[str, Any]   # dataclasses.asdict(MovementParams), as session.json (walk.py:289)
    rotation: tuple[tuple[int, int], ...] | None   # None for a legs-only folder (the viewer takes it from the map)
    git_head: str | None
    wall_ns: int | None = None  # RV4-D2 A: time.time_ns() and the run clock's now_ns(), read back to back when the
    perf_ns: int | None = None  #   walk builds the header (a wall-clock anchor for event t_ns); None (JSON null) for
                                #   sim and legs-only headers; a header line without the two keys parses with None
    version: int = VERSION

@dataclass(frozen=True)
class StepEvent:
    ev: str                 # one of EVENTS
    t_ns: int | None        # None only for a legs-only `settled`
    data: Mapping[str, Any] # the FIELDS of `ev`; "pos", "toward", "from", "to" as tuple[int, int] | None,
                            # "keys" as tuple[tuple[int, int], ...]; other values as JSON decoded them

def header_line(header: RunHeader) -> Result[bytes, str]      # one JSON line ending b"\n"; strict JSON
def event_line(event: tuple) -> Result[bytes, str]            # event = (ev, t_ns, *values in FIELDS[ev] order);
                                                              # wrong arity, unknown ev, NaN/inf -> Failure; never raises
def parse_line(line: bytes) -> Result[RunHeader | StepEvent, str]   # never raises

class StepTail:             # incremental reader for a file that is still being written
    def __init__(self, path: Path) -> None
    def poll(self) -> list[RunHeader | StepEvent]   # the complete new lines since the last poll, in file order;
                                                    # a line without its b"\n" is left for the next poll; a missing
                                                    # file gives []; a bad line is skipped and counted in .bad_lines
    bad_lines: int                                  # bad lines so far; a `run` line after line 1 is one (below)

@dataclass(frozen=True)
class RunSteps:
    header: RunHeader
    events: tuple[StepEvent, ...]
    origin: str             # "steps" (steps.jsonl) | "legs" (legs-only fallback)
    ended: bool             # the last event is "end" (always True for "legs")
    bad_lines: int

def read_run(folder: Path) -> Result[RunSteps, str]   # steps.jsonl if present, else session.json + legs.jsonl
                                                       # (plan choice 8); Failure names the folder and what is missing
```

JSON encoding: `json.dumps(obj, allow_nan=False)` (default separators, as the spec's example lines) + `"\n"`, bytes
written in binary mode so `\n` stays `\n` on Windows (as `leg_log.py:10`). Positions are JSON arrays `[x, y]`.

One run per file (RV3-D3 A: `StepLog.start` opens `steps.jsonl` exclusively, so a second header can only come from a
hand edit). A `run` line after line 1 counts as a bad line: `StepTail.poll` and `read_run` skip it, add one to
`bad_lines` and never raise; the events after it stay part of the first run. Imports: standard library and `returns`
(RV3-D1, decided A).

### V2. Observer protocol (`src/route/walker.py`) — RV-2

```python
class StepObserver(Protocol):
    def leg(self, t_ns: int, lap: int, leg: int, src: tuple[int, int] | None, dst: tuple[int, int]) -> None: ...
    def read_failed(self, t_ns: int, why: str) -> None: ...
    def no_plan(self, t_ns: int, pos: tuple[int, int], why: str) -> None: ...
    def reached(self, t_ns: int, pos: tuple[int, int]) -> None: ...
    def stall(self, t_ns: int, best_cost_s: float) -> None: ...      # math.inf before the leg's first plan
    def planned(self, t_ns: int, pos: tuple[int, int], kind: str, direction: str | None, walk_ms: float | None,
                cost_to_go_s: float, toward: tuple[int, int] | None) -> None: ...
    def sent(self, t_ns: int, input_name: str, reaction_ms: float | None, waited_ms: float | None,
             keys: tuple[tuple[int, int], ...]) -> None: ...
    def settled(self, t_ns: int, pos: tuple[int, int] | None, stopped: bool) -> None: ...
    def leg_end(self, t_ns: int, outcome: str, misses: int, off_graph: int, stalls: int) -> None: ...

class RouteWalker:
    def __init__(self, rg, rotation, layer, positions, clock, sink, rng, config=WalkerConfig(), *,
                 observer: StepObserver | None = None) -> None
```

Call points and values (spec 5.2 table): `leg` after :123 (`start_ns`, `src.minimap_position` or None,
`dst.minimap_position`); `stall` inside :137-139 (`now`, `best`); `read_failed` in :143-147 (`read_ns`,
`read.failure()`, which is `"stale"` for a stale frame); `no_plan` in :151-154 (`read_ns`, `pos`,
`planned.failure()`); `reached` in :157-159 (`read_ns`, `pos`); `planned` after :167, before :168 (`read_ns`, `pos`,
`move.kind.value`, `move.direction.name` or None, `move.walk_ms`, `decision.cost_to_go_s`,
`decision.toward.minimap_position` or None); `sent` after :170 (`clock.now_ns()`, `"ok"` or `failure.name`, and on
success `rec.reaction.ms` or None, `rec.waited_ms`, `tuple((s.delta_100us, int(s.mask)) for s in rec.plan.steps)`;
on failure None, None, `()`); `settled` after :186 (`clock.now_ns()`, `settled`, `stopped`); `leg_end` after the
record is built (`record.end_ns`, `record.outcome.value`, `record.misses`, `record.off_graph`, `record.stalls`),
then `return record`. No `settled` after a link-down break (:181-184), as today no settle runs there.

### V3. Step-log writer (`src/route/step_log.py`) — RV-2

```python
class StepLog:                         # satisfies walker.StepObserver structurally; imports nothing from walker
    def __init__(self) -> None         # no I/O; observer calls before start() are queued
    def start(self, folder: Path, header: RunHeader) -> Result[None, str]
        # opens folder/steps.jsonl with mode "xb" (exclusive: RV3-D3 A), writes the header line, starts the daemon
        # thread "step-log"; Failure (nothing started) when the file exists or cannot be opened, or after close()
        # ("step log already closed"); call once
    # the nine V2 methods: each builds (ev, t_ns, *values) and SimpleQueue.put()s it; nothing else. After close()
    # they return at once and queue nothing (RV3-D2 A2: a walk whose step 1 failed closes its StepLog at once and
    # the walker keeps calling it for the whole walk, so a queue there would only grow)
    def end(self, t_ns: int, reason: str | None, code: int | None) -> None   # queues the "end" event (dropped
                                                                           # after close(), as the nine)
    def close(self, join_s: float = 2.0) -> Result[None, str]
        # marks the log closed first (every later call is dropped), then queues the sentinel, joins the thread for
        # at most join_s, then flush + os.fsync + close; idempotent; never raises; Failure carries the first write
        # error of the run (or "writer still busy after 2.0 s"). Before start(): discards the queue, Success
    @property
    def failure(self) -> str | None    # the first write or encode failure so far, else None
```

Writer thread: `get()` → sentinel ends it; else `event_line` → one `write` + `flush` (no fsync per line, spec 5.2).
An encode or write failure is kept as `failure`, printed once to stderr (`step log write failed (not reported again
this run): ...`), and the thread keeps draining the queue without writing. It catches `Exception` around each line so
it never dies with events left in the queue.

### V4. Route preview (`src/route/route_preview.py`) — RV-4 (piece 2 owns the text; names and types fixed here)

```python
@dataclass(frozen=True)
class StartOption:
    node: Node
    cost_s: float                      # approach + rg.cost_to(node, target) on the offered graph (planner.py:206-210);
                                       # a start whose cost_to fails is left out, as the planner leaves it out (:209)

@dataclass(frozen=True)
class Hop:
    options: tuple[Move, ...]          # one Move; two for a coin-flip down: (Move(DROP), Move(TELEPORT, DOWN)),
                                       # in that order
    toward: Node                       # Decision.toward for this move
    from_pos: tuple[int, int]          # the position this hop is planned from (pos, or the previous hop's landing)
    landing: tuple[int, int]           # the position the preview assumes after it (the landing rule below)
    cost_to_go_s: float                # the planner's total from from_pos (bit-equal to next_move's)

@dataclass(frozen=True)
class Preview:
    pos: tuple[int, int]
    target: Node
    arrived: bool                      # P1 held at pos: no hops, no starts, start None, cost 0.0, path ()
    starts: tuple[StartOption, ...]    # every candidate start with a path, left before right (_starts order)
    start: Node | None                 # the chosen start (min cost, ties left before right: `min` keeps the first)
    cost_to_go_s: float                # hops[0].cost_to_go_s, or 0.0 when arrived
    path: tuple[Node, ...]             # rg.path(start, target) on the offered graph, from pos only (the "why"
                                       # log's route); the expected track is pos -> hops[0].landing -> ... ->
                                       # hops[-1].landing, which follows the planner's re-plans; `path` need not
                                       # hold every hop's `toward` (RV3-D5 A2: RV-4's `follows` measures it), so
                                       # the view draws the route line and the "Route" text from the hops (V5)
    hops: tuple[Hop, ...]              # hops[0] is the move next_move makes from pos; hops[k] is the move
                                       # next_move makes from hops[k-1].landing
    complete: bool                     # True when the last landing is "arrived" (P1); False when the expansion
                                       # stopped early (max_hops reached, or the plan at a landing failed);
                                       # S3b requires True on all its inputs

def preview(rg: RouteGraph, pos: tuple[int, int], target: Node, max_hops: int = 64) -> Result[Preview, str]
    # Failure exactly when next_move fails, with next_move's message (target not in graph, off-graph, no path);
    # takes no generator and draws nothing
```

**How hops are built (RV3-D4 A, ruling).** One private function, `_plan(rg, pos, target)`, is the preview's own copy
of `next_move`'s P1-P5 (`planner.py:190-247`), line for line, calling the planner's helpers (`_is_at`, `_offered`,
`_on_slope_segment`, `_starts`, `_walk_run_end`, `_edge_kind`, `_launch_x`, `_walk`, `_run`, `_go_down`,
`_surface_ys`) and `rg.cost_to` / `rg.path`, and returning, besides the decision, the candidate starts, the path and
each move's **aim** (the goal x the planner used and the node whose platform holds it). `preview` calls `_plan` at
`pos`, then again at each landing, until a plan is "arrived" (`complete=True`), a plan fails or `max_hops` hops exist
(`complete=False`; the hops so far are kept).

**The coin flip without a draw.** At P5's DOWN edge `_plan` calls `planner._go_down(rg, g, h, rng)` twice, with two
private stub generators whose `random()` returns `0.0` and `0.99` (`typing.cast` to `np.random.Generator`). The two
results are equal when `_go_down` takes no draw (one option, or the clearly cheaper one, `planner.py:171-172`,
`:177-178`) and are `(DROP, TELEPORT DOWN)` when it would toss the coin (`:175-176`). So the band rule is the
planner's own code, never copied. **Condition (accepted with RV3-D5's ruling):** the stub class defines `random()`
and nothing else (no `__getattr__`, no other method, no base class beyond `object`), so if a future `_go_down` drew
in any other way it would fail loudly with `AttributeError` instead of silently giving a wrong option; RV-4 test 1's
seeds 0..49 check pins the outcome empirically. The landing rule below is accepted as a stated assumption (module
docstring, "Where the plan is silent"): on the simulator's FF2 terrain 5 of the 7 distinct past-end landings of the
FF2 sweep have ground at the assumed y and 2 would fall (RV3-D5 notes); only display hops depend on it.

**The landing rule (one per move kind).** `(px, py)` is the hop's `from_pos`; `v = walk_px_per_s`; "the surface of
platform P at x" is `planner._surface_ys(rg.terrain.platforms[P], x)`, the piece nearest `py` when there are several,
rounded with Python's `round` to an int.
- **WALK or TELEPORT left/right**, aim x `ax`, aim node `an` (P4 to g: `ax = g.x`, `an = g`; P4 to a launch x:
  `ax = launch`, `an = g`; P5 SLOPE: `h`; P5 WALK: `g_next`). Landing x `lx`: a TELEPORT moves `reach_h`,
  `lx = px ± reach_h` toward `ax` (it may pass `ax`: `_run` teleports when that still beats walking, `planner.py:95`);
  a WALK lands on `ax` when `1000 * |ax - px| / v <= max_walk_ms` (not capped, `planner.py:88`), else
  `lx = px ± round(max_walk_ms * v / 1000)`. If `lx == an.x` the landing is `an.minimap_position`. Otherwise its y is
  the surface at `lx` of the first platform that has one, in this order: `an`'s platform, the platforms of the path
  nodes from the one before `an` back to `path[0]`, the from-anchor's platform; if none has a surface at `lx` (past a
  platform end), the y of the end node of `an`'s platform nearest `lx` (`attach`'s A3 then anchors it,
  `locate.py:64-81`).
- **TELEPORT UP, TELEPORT DOWN or DROP** on the edge g -> h (both options of a coin flip share one landing): if
  `px == h.x`, `h.minimap_position`; else `(px, y)` with y the surface of `h`'s platform at `px`; if it has none
  there, the same with `_launch_x(rg, g, h)` in place of `px`.

Measured at planning time (a scratch prototype of exactly this rule at `f807b0f`, RV3b notes): on FF2's 420 ordered
node pairs under five parameter sets every preview is complete (at most 9 hops) and every hop equals `next_move` at
its `from_pos`.

### V5. View model (`src/route_view/model.py`) — RV-6 (piece 3 owns the text)

Pure, no Tk, no Windows call: built from a `RunHeader` and `StepEvent`s (V1), the route graph rebuilt from the
folder's map and the header's params, and the frame index; it answers "what does step k look like" for the window
(RV-7) and the follower (RV-8), and renders the same answer as text for S5's golden tests.

```python
SCALES = (2, 3, 4); DEFAULT_SCALE = 4                      # H51 (RV1-D8 B)
WARN_MAP = "map changed since this run: the route shown may not be the one the bot used"   # spec 5.3, word for word
WARN_CODE = "code changed since this run"                                                  # spec 5.3
FRAMES_DIR = "frames"; FRAME_INDEX = "index.jsonl"        # record_minimap.py:8-10 (walk records into <out>/frames)

@dataclass(frozen=True)
class NodeMark:
    pos: tuple[int, int]
    blue: bool
    label: str              # "#1".."#n" for blue nodes in rotation order (= their file order, controller.py:94 via
                            # csv_parser.py:25); "o1".."om" for orange nodes in file order (node_map order, csv_parser.py:23)

@dataclass(frozen=True)
class HopView:
    n: int                  # 1-based
    from_pos: tuple[int, int]
    landing: tuple[int, int]
    text: str               # "teleport right" | "walk right 1.4 s" | "drop" | "teleport down" |
                            # "drop or teleport down, 50/50" (a two-option hop; planner.py:176 is rng.random() < 0.5)
    keys: str               # spec 3.3: "Right+E", "Up+E", "hold Right", "Down+Alt", "Down+Alt or Down+E"
    sent: bool              # True for hop 1 of a logged plan (spec 5.3 step 2: the logged move, marked "SENT")

@dataclass(frozen=True)
class StepView:
    index: int                              # into RunModel.events
    ev: str
    lap: int | None; leg: int | None        # of the leg the step is in; None before the first `leg`
    target: NodeMark | None                 # that leg's `to` (yellow ring); a NodeMark with label "(x, y)" if no node is there
    character: tuple[int, int] | None       # the newest logged position in this leg up to the step (planned, settled,
                                            # reached, no_plan pos), else None
    aim: tuple[int, int] | None             # hops[0].landing of the plan in force (spec 5.3 step 3: "landed where it aimed")
    route: tuple[tuple[int, int], ...]      # the plan in force: its pos, then each hop's landing (V4's expected track)
    hops: tuple[HopView, ...]
    recent: tuple[tuple[tuple[int, int], tuple[int, int]], ...]   # up to 5 (planned pos, settled pos), oldest first,
                                            # moves whose `settled` came at or before the step, the plan in force excluded
    panel: tuple[str, ...]                  # side panel lines (spec 5.4): "Target  #6 (101, 24)",
                                            # "Position (68, 27), on o7-o8", "Route   you -> o7 -> o8 -> #6   5.2 s",
                                            # "Hops", then one "  1 teleport right   Right+E   SENT" line per hop
    state: str                              # title word: the STATE_WORDS entry of `ev`
    why_line: int                           # index into RunModel.why of the step's line (replay selects it)
    frame: str | None                       # file name in frames/ of the newest frame with t_ns <= the step's time

class RunModel:
    folder: Path; header: RunHeader; origin: str     # "steps" | "legs" (V1)
    ended: bool                                      # an `end` event has been added
    end_reason: str | None
    nodes: tuple[NodeMark, ...]                      # () when there is no graph
    rg: RouteGraph | None                            # None when the map or graph cannot be built (see warnings)
    minimap_size: tuple[int, int] | None             # "# minimap_size=" of the map (map_csv.read_editor_nodes metadata)
    warnings: tuple[str, ...]                        # red lines at the top, in this order: WARN_MAP, WARN_CODE,
                                                     # "route not shown: <reason>" (map missing, class unknown, graph failed)
    events: list[StepEvent]                          # grows in live mode
    why: list[str]                                   # grows; a repeat of the last read_failed line is collapsed in place
    frames: list[tuple[int, str]]                    # (t_ns, file), in index order

    @classmethod
    def open(cls, folder: Path, header: RunHeader, origin: str, *, repo_root: Path,
             current_git_head: str | None) -> "RunModel"      # never raises, never fails: problems become warnings
    def extend(self, events: Sequence[StepEvent]) -> None     # appends; builds why lines and per-event indexes; O(new)
    def add_frames(self, frames: Sequence[tuple[int, str]]) -> None
    def step(self, k: int) -> StepView                        # 0 <= k < len(events); IndexError otherwise
    def seek(self, k: int, unit: str, delta: int) -> int      # unit "event" | "move" (a `planned`) | "leg" (a `leg`);
                                                              # delta +1/-1; stays at k when there is none that way
    def find_leg(self, lap: int, leg: int) -> int | None      # index of that leg's `leg` event
    def play_delay_ms(self, k: int, speed: float) -> int      # (t[k+1] - t[k]) / 1e6 / speed, rounded; 100 when either
                                                              # t_ns is None or k is the last step

def load_folder(folder: Path, *, repo_root: Path, current_git_head: str | None) -> Result[RunModel, str]
    # read_run (V1) + read_frame_index + RunModel.open + extend + add_frames; Failure only when read_run fails
def read_frame_index(data: bytes) -> tuple[list[tuple[int, str]], int]
    # complete lines only (up to the last b"\n"); "\r\n" accepted (record_minimap.py:78 opens the index in text mode,
    # so on Windows its lines end "\r\n"); returns (frames, bad_lines)
def hop_route(hops: Sequence[Hop]) -> tuple[tuple[int, int], ...]   # the hops' toward positions, repeats dropped
def route_changed(prev: Preview, move: Move, toward: tuple[int, int] | None, cur: Preview | None) -> bool
def render_text(model: RunModel, view: StepView) -> str     # the golden-text form, below
```

**Rules the model follows (each is a review item).**
- *Map and graph* (spec 5.3, 5.5): the map is `folder / header.map` when `header.map == "map.csv"`, else
  `header.map` itself, relative paths taken from `repo_root` (a legs-only folder's `session.json` map path,
  `walk.py:287-299`). The graph is built as `walk.py:164-178` does: `MapleAgent(characterClass=_CLASSES[class]())`,
  `loadMap` under `contextlib.redirect_stdout(io.StringIO())`, `RouteGraph.build(agent.pathGraph,
  MovementParams(**header.params))`; `_CLASSES = {"kanna": Kanna}` is the model's own copy (it may not import
  `walk.py` or `sim/run.py`, spec 5.8). `WARN_MAP` when the file's sha256 differs from `header.map_sha256` (none when
  that is None); `WARN_CODE` when both `header.git_head` and `current_git_head` are set and differ (a sim header has
  `git_head None`, plan choice 5). Any failure on the way (file missing, `loadMap` Failure, unknown class, a
  `TypeError` from `MovementParams(**params)`, a `RouteGraph.build` Failure) gives one warning
  `"route not shown: <reason>"`, `rg None`, no nodes; events, positions and why lines still work (Review Focus 2).
- *Previews*: `route_preview.preview(rg, pos, target_node)` once per distinct `(pos, target position)` (a dict
  cache), computed in `extend` for each `planned` event (its why line needs it) and reused by `step`. A target
  position with no node in the graph (map changed) → no preview, why line "at (x, y): target (x, y) is not on this
  map". A preview Failure → its message in the why line; no hops.
- *The plan in force at step k*: the newest `planned` event of the step's leg at or before k, unless a `no_plan`,
  `reached` or `leg_end` came after it (then none: no route, no hops, no aim). Hop 1 is the logged move (kind, dir,
  `walk_ms` from the event; `sent True`); hops 2..n are `preview.hops[1:]`; route and aim come from the preview
  (`pos`, then the landings). Legs-only `planned` events (`toward None`) are drawn the same way.
- *Labels*: a node is named by its NodeMark label; a position on no node by `(x, y)`. "on <...>" in the Position
  line and the planned why line comes from `locate.attach(rg, pos)`: a node → its label; a segment → `<left>-<right>`;
  past a platform end → `left of <right>` / `right of <left>`; a Failure → `off the graph`.
- *Move words* (`kind`, `dir` as logged): walk → `walk <dir> <walk_ms/1000:.1f> s` / keys `hold <Dir>`; teleport →
  `teleport <dir>` / `<Dir>+E`; drop → `drop` / `Down+Alt`; a two-option preview hop → the coin-flip text above.
  `<dir>` is lower case, `<Dir>` capitalised (`Key` names, `protocol.py:54-61`: `Left`, `Right`, `Up`, `Down`, `E`,
  `Alt`, `Space`).
- *Distance to the aim* (the settled line and the route-changed line): the move's axis is x for LEFT/RIGHT, y for
  UP, DOWN and DROP; `d = (settled - aim)` on that axis, times +1 when the move goes toward +x/+y (RIGHT, DOWN, DROP)
  and -1 otherwise. `d > 0` is "`d` px past the aim", `d < 0` "`-d` px short of the aim", `d == 0` "on the aim".
- *Route changed* (spec 5.4 last row, the same comparison as S3b's report part): the pair is a `planned` event Q
  and the `planned` event P before it in the same leg (between one `leg` and its `leg_end`), with no other
  `planned`, no `no_plan` and no `reached` between them, and at least one `settled` event between them; a `settled`
  with pos None counts (the line then has no bracket, below). RV-5 test 5 counts exactly these pairs. For such a
  pair, `route_changed(P's preview, Q's logged move, Q's logged toward, Q's preview)` is True when `P.hops` has fewer
  than 2 hops, or `(move.kind, move.direction)` is not among `(o.kind, o.direction)` for `o` in `P.hops[1].options`
  (walk length is not compared: a re-plan that changes only how long the walk is, because the landing moved its
  start, is not a route change; the settled line shows the landing error; RV3-D8 B), or (with a logged `toward`)
  `toward != P.hops[1].toward.minimap_position`, or `cur` is not
  None and `hop_route(cur.hops) != hop_route(P.hops[1:])`, where `hop_route(hops)` is the tuple of the hops'
  `toward` positions with consecutive repeats dropped (RV3-D5 A2: "the remaining path" is the route the hops take,
  not `path`; `hop_route` is a public function of `model.py` beside `route_changed`, and RV-5 counts S3b's
  real-landing rate with these two functions, so the printed rate and the view's lines are one comparison). It
  adds the line "route changed: expected next <P hop 2 text> toward <label>, now <logged move text> toward <label>
  (landed <d> px short/past)" before the event's own lines (d from the last `settled` between P and Q that has a
  position, against P's aim; no bracket when none has one; `expected next arrival` when P had one hop; "on the aim"
  in place of the bracket when d is 0). The move words are added to the spec's "<node>" so a change of move at the
  same node (spec 5.3: the walk-or-teleport break-even) is visible.
- *Why lines* (spec 5.4 table, word for word where it gives words; each line starts with the time, RV3-D6):
  `leg` "lap L leg K: target <label> (x, y)"; `planned` two lines, "at (x, y) on <...>; start <label> (<c:.2f> s;
  via <label> <c:.2f> s ...); route <labels of preview.path joined by spaces>" (one "via" per other start, in
  `starts` order; "at (x, y) on <...>; <preview Failure>" when the preview fails; "at (x, y): route not shown" when
  there is no graph) and "plan: <move text> toward <label> (aim x <aim x>)" (the label of the logged `toward`, or of
  the preview's `hops[0].toward` when it is None, legs-only; no bracket without a preview); `sent` "sent: <Key>
  down, <Key> down +31 ms, ... (waited <waited_ms:.0f> ms)" from `protocol.transitions` of `Step(d, Key(m))` per logged pair (times `t_100us / 10` rounded to whole ms; the first key
  change has no "+"; no bracket when `waited_ms` is None) or "send failed: <input>"; `settled` "settled (x, y): <d
  words>; plan again from there", or "did not settle within <settle_timeout_s:g> s" when pos is None, with
  " (stop requested)" added when `stopped`; `read_failed` "no character on the minimap (<why>)", or "no new frame
  yet" for `why == "stale"`, a repeat of the same line (same `why`, nothing else between) replacing it in place by
  "<newest repeat's time> <text> x<n>"; `no_plan` "cannot plan from (x, y): <why>"; `stall` "no progress for <stall_s:g> s (best cost to go <c:.2f> s): stall counted", "(no plan yet)" for a
  null cost (plan choice 3); `reached` "reached <target label>"; `leg_end` "leg ended: <outcome> (<misses> misses,
  <off_graph> off-graph, <stalls> stalls)"; `end` "run ended: <reason or 'no reason'>" plus " (exit code <code>)"
  when code is not None.
- *Times* (RV3-D6, provisional A): elapsed since the run's first event with a `t_ns`, as `mm:ss.mmm` (`h:mm:ss.mmm`
  from one hour); an event with `t_ns None` (legs-only `settled`) repeats the previous event's time.
- *State words* (title): leg "starting leg", planned "planned", sent "moving", settled "settled", read_failed "no
  character", no_plan "cannot plan", stall "stalled", reached "reached", leg_end "leg ended", end "ended".
- *Frames*: the newest frame with `t_ns <=` the step's time (`bisect` on the index, which is in time order); an event
  with `t_ns None` uses the previous event's time; none → `frame None` (the window then uses `minimap.png`, else a
  dark canvas, spec 5.5).

**`render_text` (S5's golden form).** Lines, in order: `title: <state> lap <lap> leg <leg> -> <target label>`;
`warnings: <each warning, " | "-joined, or "none">`; `nodes: <label@x,y ...>` (blue then orange, file order);
`target: <label>`; `character: x,y | none`; `aim: x,y | none`; `route: x,y x,y ...`; `recent: x,y>x,y ...`; the
panel lines; `why: <model.why[view.why_line]>`; `frame: <file | none>`. Positions with no spaces inside, one space
between, so a golden diff points at one value; an empty list or a None prints `none`. Panel hop lines are
`f"  {n} {text}   {keys}"`, plus `"   SENT"` when `sent`; the Route line is `"Route   you -> " + " -> ".join(labels of
the hops' route) + f"   {cost:.1f} s"`, the hops' route being `hop_route` of the plan in force's hops (hop 1's logged
`toward`, or the preview's `hops[0].toward` when it is None, then hops 2..n's; RV3-D5 A2: drawn from the hops, not
from `path`, which the planned why line keeps), with the plan in force's logged `cost_to_go_s` (legs-only events
carry it too).

### V6. Viewer CLI and run-folder files — RV-8 (viewer side), RV-9 (walk side)

- `python -m src.route_view --follow <run folder> [--ready <run folder>/view.ready | --replay]`
  (`src/route_view/__main__.py`). A folder whose `steps.jsonl` ends with `end`, or with no `steps.jsonl` and a
  `legs.jsonl`, opens in replay; else it follows live (plan choice 10). It prints `window built in <s> s` to stdout
  when the window is built (L4).
- `--replay` (RV4-D1 A) opens the folder as a replay whatever it holds; a folder with `steps.jsonl` and no `end` gets
  the title mode `REPLAY (did not end)`. With `--ready` it is refused: stderr `error: --replay cannot be used with
  --ready`, exit 2, before any window is built. The walk never passes it; Hong types it for a walk that crashed or
  was killed (no `end`: Task Manager, power loss, the walk's console window closed).
- With `--ready` (plan choice 9): build the window withdrawn → `open(folder / VIEW_CLAIM, "x")`:
  - claimed: first show without activating (spec 5.7), then write `VIEW_READY` as `view.ready.tmp` +
    `os.replace`;
  - `FileExistsError`: stay withdrawn, follow the folder, show only after reading `end`, already in replay.
- The walk (RV-9) starts it as spec 5.9 step 2 says, exactly:
  `subprocess.Popen([sys.executable, "-u", "-m", "src.route_view", "--follow", str(out), "--ready",
  str(out / VIEW_READY)], cwd=<repo root>, creationflags=subprocess.CREATE_NO_WINDOW |
  subprocess.CREATE_NEW_PROCESS_GROUP, stdin=subprocess.DEVNULL, stdout=<out/view.log opened "ab">,
  stderr=subprocess.STDOUT)`.
- `_OUT_FILES` (`walk.py:81`) gains `steps.jsonl`, `map.csv`, `minimap.png`, `view.log`, `view.claim`, `view.ready`.

### V7. Walk-side seams (`src/route/walk.py`) — RV-9 (written by piece 4; RV-10 uses its two seams)

```python
_VIEW_READY_S = 15.0        # spec 5.9 step 3: wait for view.ready at most this long
_VIEW_CLAIM_EXTRA_S = 2.0   # ... and this much more when the viewer holds the claim
_VIEW_POLL_S = 0.05         # poll period of the wait

# seams (tests monkeypatch them; RV-10's N1 tool calls the real ones, so N1 starts the viewer exactly as a walk does)
def _start_viewer(out: Path) -> subprocess.Popen | Failure[str]
    # V6's Popen call, word for word: [sys.executable, "-u", "-m", "src.route_view", "--follow", str(out),
    # "--ready", str(out / VIEW_READY)], cwd=_REPO, creationflags=subprocess.CREATE_NO_WINDOW |
    # subprocess.CREATE_NEW_PROCESS_GROUP, stdin=subprocess.DEVNULL, stdout=<out / VIEW_LOG opened "ab">,
    # stderr=subprocess.STDOUT; the parent's copy of the view.log handle is closed once Popen returns. An OSError
    # (from the open or from Popen) -> Failure(str); never raises
def _wait_for_viewer(out: Path, proc) -> bool
    # spec 5.9 step 3, walk side. `proc` needs only .poll(). True when view.ready appeared (in the first
    # _VIEW_READY_S, or in the _VIEW_CLAIM_EXTRA_S after finding the claim taken); else False after it printed the
    # one line VIEW_LATE_LINE. Never raises, never kills or waits on the process
VIEW_LATE_LINE = ("route view did not start within {s:g} s; walking without it (it opens when the walk ends), "
                  "see view.log")   # spec 5.9 step 3 word for word, with s = _VIEW_READY_S; printed to stderr

# helper (private, not a seam)
def _write_run_files(out: Path, map_path: Path, frame, step_log: StepLog, header: Callable[[str], RunHeader]
                     ) -> Result[None, str]
    # spec 5.9 step 1 minus session.json: map.csv, then minimap.png, then step_log.start; `header(sha)` builds the
    # run header from the sha256 of the map bytes it copied; see the order below
```

Order inside `main` (spec 5.9; `walk.py` line numbers at `5d1176a`):
1. `--view` sets `args.record = True` right after parsing (H48; `session.json`'s `"record"` then says True).
2. After `_wait_first_frame` succeeds (:255-258): `first_frame = shared_frames(shared)()` (the frame it waited for,
   held in a local; spec 5.2 "from the first frame it already waits for").
3. Before the walker (:278): `clock = _make_clock()` and `step_log = StepLog()`; the walker is built with `clock` and
   `observer=step_log` (V2's keyword). Every walk passes it, `--view` or not.
4. After `stop.start()` succeeds (after :285), step 1: `head = _git_head()` (once; `session.json` uses the same
   value), then `_write_run_files(out, args.map, first_frame, step_log, lambda sha: RunHeader(source="walk",
   clock="perf_counter_ns", map=MAP_COPY, map_sha256=sha, character=args.character, params=asdict(rg.params),
   rotation=tuple(n.minimap_position for n in rotation), git_head=head, wall_ns=time.time_ns(),
   perf_ns=clock.now_ns()))` (RV4-D2 A: the two read back to back inside the lambda, wall clock first; `clock` is the
   walker's, `RealClock.now_ns` is `time.perf_counter_ns`, `walker.py:68-71`), which:
   - reads `map_path`'s bytes and writes them to `out / MAP_COPY` (`write_bytes`; so `map.csv` is byte-equal to
     `--map` and the header's `map_sha256` equals `session.json`'s), then `out / SNAPSHOT`: `cv2.imencode(".png", cv2.cvtColor(frame,
     cv2.COLOR_RGB2BGR))` (the shared frame is RGB, `record_minimap.py:9`, `:96`), the bytes to `minimap.png.tmp`,
     then `os.replace` onto `minimap.png`; a `None` frame (only possible through the test seams) is the failure "no
     first frame to save". Each of the two is tried even when the other failed, so one that writes stays (H52's
     snapshot where possible; RV3-D2 A1);
   - only when both wrote, `step_log.start(out, header)`. When either failed, `start` is not called: under RV3-D2 A
     the walk then has no step log at all, and a `steps.jsonl` with a header and no `end` would read as a crashed run;
   - every exception inside is caught (`except Exception`) and becomes part of the `Failure`, whose text joins the
     errors with `"; "`. It never raises.
   On `Failure` (RV3-D2 A with A1): print `step log unavailable: <failure>; walking without the route view` to
   stderr (one line), call `step_log.close()` at once (before session.json; from here every observer call is dropped,
   V3 A2), add no `step_log` cleanup step, and start no viewer and write no `view.claim`, even with `--view`. The
   exit code does not change for this.
   Then `session.json` as today (:287-299; content unchanged; `git_head` is `head`).
5. Only with `--view` and a started step log, steps 2-3: `proc = _start_viewer(out)`; a `Failure` → print `route
   view could not start: <failure>; walking without it` to stderr and go on (no wait, no claim: no process exists
   that could claim); else `_wait_for_viewer(out, proc)`:
   - poll every `_VIEW_POLL_S` until `out / VIEW_READY` exists (→ True), `proc.poll() is not None` (exited) or
     `_VIEW_READY_S` has passed (by `time.monotonic`);
   - not ready: `open(out / VIEW_CLAIM, "x").close()`. Created → print `VIEW_LATE_LINE`, return False.
     `FileExistsError` (the viewer is between its claim and its show) → poll for `view.ready` up to
     `_VIEW_CLAIM_EXTRA_S` more → True, or print `VIEW_LATE_LINE` and return False. Any other `OSError` → print
     `VIEW_LATE_LINE` and return False. The viewer's own claim then likely fails too (RV-8 treats any `OSError`
     there as "stay hidden", the safe side), but a transient error need not repeat: the viewer may then claim and
     show mid-walk. That show is the non-activating first show N1 tests, so it costs no focus (RV4 m9; fyi).
   The walk never touches `proc` again: it does not wait for it, kill it or read its exit code later.
6. The countdown (:300-301), unchanged.
7. Cleanup (:341-367): `leaving = sys.exc_info()[0]` is the first statement of the `finally` (spec 5.9, last
   paragraph: set while an exception is passing through; it must be read there, not inside `runtime.run_cleanup`,
   whose own `except` would change it). When the step log started, a `("step_log", end_step_log)` step goes right
   after `stop.json` (and so after `release_all`) and before `leg_log`; it calls `step_log.end(clock.now_ns(),
   stop_payload["reason"] if stop_payload is not None else None, None if leaving is not None else code)` then
   `step_log.close()`, and prints `step log: <failure>` to stderr when `close` returns a `Failure`. The values are
   bound as default arguments when the step is built, as the other steps do (:344-360). It never changes the exit
   code.

`_OUT_FILES` (:81) becomes `(_SESSION, _STOP, LEGS_FILE, LAPS_FILE, f"{_FRAMES}/{INDEX_NAME}", STEPS_FILE, MAP_COPY,
SNAPSHOT, VIEW_LOG, VIEW_CLAIM, VIEW_READY)`: the new names after the old ones, so the B19-D2 message for an old run
folder is unchanged. `walk.py` imports `StepLog`, `RunHeader` and the six names from `src.route.step_log`, and `cv2`
and `os`; nothing from `src.route_view` (hard rule 6).

---

## Coverage: spec → tasks

| Spec item | Met by | Criterion that can fail |
|---|---|---|
| S1 behaviour unchanged | RV-2, RV-3 (and every later task that touches `src/route`) | the sim smoke hash of hard rule 11; frozen tests byte-identical to `5d1176a`; 1000-lap run's stdout identical before/after RV-2 |
| S2 the observer changes nothing | RV-2 (walker level), RV-3 (`run_sim` level) | equal `LegRecord`s, `LapRecord`s, report and generator states with and without a recording observer |
| S3 preview agrees with the planner | RV-4 (hand-built table, six branches, two Failures), RV-5 (both 20-lap sim runs) | exact `cost_to_go_s`, `toward`, kind, dir, `walk_ms` per `planned` event |
| S3b every later hop agrees | RV-4 (table, FF2 sweep), RV-5 (sim runs + the reported rate) | `next_move` at each assumed landing equals hop k; the preview from that landing gives exactly the remaining hops (RV3-D5 A2); departures from `path` pinned on the FF2 sweep, reported on the sim runs |
| S4 step log matches leg log | RV-3 | one-to-one `planned`/`sent`/`settled` ↔ `MoveEntry`; `leg`/`leg_end` ↔ leg records; header first, `end` last |
| S5 the view model | RV-6 | golden text at chosen steps (coin-flip down, teleport run, off-graph read) |
| S6 the window opens | RV-7 | withdrawn root draws step k of a sim folder and a legs-only folder |
| S7 following a live folder | RV-8 | half lines never shown; newest complete step and frame shown |
| S8 cost on the walker thread | RV-3 | median time inside the observer < 20 µs; queue empty within 1 s of the run's end |
| S9 rules | every task; contract f (RV-1, RV-4), route_view contracts (RV-6) | `tools/check.py --full` passes |
| S10 walk wiring, no game | RV-9 | the S10 list, item by item |
| N1 no-key check | RV-10 (the driver tool), H-1 (the run) | foreground unchanged by show and clicks; the view updates while live; no console; Ctrl+C isolation; outlives parent; replay switch |
| L1-L4 | H-2, H-3, H-4 | Hong's checks (spec 7) |
| Spec 6 docs (`src/route/CLAUDE.md`, `src/route_view/CLAUDE.md`, root `CLAUDE.md`) | RV-11 | facts with `file:line`; commands run |

(The S1 row's criterion is the hash in hard rule 11: `d982184e54311493311110657d93992fe7f0af4963dc603617c5a0f4d09d8035`.)

---

## Task list

Class: **M** routine (complete spec, one area, little judgment) · **O** multi-file integration (several files, some
design choices) · **S** subtle (concurrency, timing, control loops, data loss, a frozen control loop) · **D** docs ·
**[Hong]** needs Hong's hands or eyes. M/O/S/D follow the SKILL.md "Model policy" rows. **Size** is the estimated
total context of one implementer at finish (reading included). **Cloud:** yes for pure-Python tasks with no Windows
API and no Tk window (memory CL-1).

| # | Task | Class | Size | Cloud | Depends on | Files (real code) | Contract | Acceptance (one line; full text in the task) | Text in |
|---|---|---|---|---|---|---|---|---|---|
| RV-1 | Step-log format and reader (incl. legs-only fallback) | M | ~70k | yes | RV3-D1 | `src/route/step_log.py`, `pyproject.toml` (contract f) | V1 | round-trip of every event; half line left for the next poll; `b21-test1`-shaped folder read; contract f kept | piece 1 |
| RV-2 | Walker observer and the step-log writer thread | S | ~110k | yes | RV-1, RV3-D3 | `src/route/walker.py` (observer only), `src/route/step_log.py` (`StepLog`) | V2, V3 | S1 (frozen tests unchanged, 1000-lap stdout identical, smoke hash); S2 at walker level; guard disables a raising observer after one stderr line; writer: header first, `end` last, close ≤ 2 s | piece 1 |
| RV-3 | Simulator writes `steps.jsonl` and `map.csv`; S2, S4, S8 | O | ~100k | yes | RV-2 | `src/route/sim/run.py` | V1, V3 | S1 hash; S2 at `run_sim` level; S4 on a `--out` folder (plain and node-offset); S8 median < 20 µs; `map.csv` rebuilds the same graph | piece 1 |
| RV-4 | Route preview and the hand-built agreement table | S | ~130k | yes | RV3-D4 (A), RV3-D5 (A2) | `src/route/route_preview.py`, `pyproject.toml` (contract f) | V4 | S3 table: each of the six branches forced (and traced), the Failures with the planner's message; S3b on every table case | piece 2 |
| RV-5 | Preview agreement over the two 20-lap sim runs; S3b's rate | O | ~90k | yes | RV-3, RV-4, RV-6 | tests only (fixes in `route_preview.py` if a case fails) | V1, V4, V5 (`route_changed`, `hop_route`) | S3 and S3b exact on every `planned` event of both runs; departures and the real-landing agreement rate printed | piece 2b |
| RV-6 | Pure view model, "why" lines, route_view contracts | O | ~120k | yes | RV-1, RV-4 | `src/route_view/__init__.py`, `src/route_view/model.py`, `pyproject.toml` (two contracts) | V5 | S5 golden text; both forbidden contracts kept | piece 3 |
| RV-7 | Tk window and replay controls | O | ~110k | no (Tk) | RV-6 | `src/route_view/window.py`, `src/route_view/__main__.py` | V5, V6 | S6 on a sim folder and a legs-only folder; 4x default, 2x/3x/4x; editor colours | piece 3 |
| RV-8 | Live follower, no-activate style, viewer-side claim and ready | S | ~130k | no (Win32) | RV-7 | `src/route_view/follow.py`, `src/route_view/win_focus.py`, `__main__.py` | V1, V6 | S7; claim/ready per V6; ctypes only inside functions; `end` switches to replay and clears the style | piece 3 |
| RV-9 | `walk --view`, snapshot, map copy, step log, viewer start and ready wait | S | ~120k | yes (seams; no spawn) | RV-2, RV3-D2 (A, A1) | `src/route/walk.py` | V1, V3, V6, V7 | S10, item by item; Review Focus 3; RV3-D2 A1 (step-1 failure walks on, no viewer) | piece 4 |
| RV-10 | N1 driver tool (feeds a folder, starts the viewer through RV-9's seams, reads the foreground) | O | ~100k | no (Win32) | RV-8, RV-9, RV3-D7 | `src/route/harness/view_check.py` | V1, V6, V7 | `--dry-run` (no viewer, no Win32 call) feeds a sim run into a fresh folder and `read_run` gives back the source run; the pure parts tested with fakes; never sends keys or input; refuses to run with MapleStory open | piece 4 |
| RV-11 | Docs: `src/route/CLAUDE.md`, new `src/route_view/CLAUDE.md`, root `CLAUDE.md`, spec "As built" | D | ~90k | yes | RV-1..RV-10 | docs only | — | facts with `file:line`; every command named runs (`--help` for the viewer) | piece 4 |
| H-1 | N1 run [Hong] (or an agent on an idle PC with MapleStory closed, H15's idle rule) | [Hong] | — | no | RV-10 | none | — | spec 5.7 no-key check passes, or its named fallback is applied | piece 4 |
| H-2 | L1 + L2: supervised 1-lap `walk --view`, then a short walk stopped with Ctrl+C | [Hong] | — | no | H-1 | none | — | spec 7 L1, L2 | piece 4 |
| H-3 | L3: replay of the L1 folder and `b21-test1` | [Hong] | — | no | H-2 | none | — | spec 7 L3 | piece 4 |
| H-4 | L4: focus for a whole run, once from a cold start | [Hong] | — | no | H-1 | none | — | spec 7 L4 | piece 4 |

**Counts (11 build tasks + 4 [Hong]):** M 1 (RV-1) · O 5 (RV-3, RV-5, RV-6, RV-7, RV-10) · S 4 (RV-2, RV-4, RV-8,
RV-9) · D 1 (RV-11).

**Frozen `walker.py` (subtle):** only RV-2 touches it. RV-3 and RV-9 construct `RouteWalker` with `observer=` and
change no line of it. The plan reviewer reads RV-2's `walker.py` diff against the code-shape rule (hard rule 4; spec
section 9).

**Build order and parallelism.** RV-1 and RV-4 have no dependency on each other and can run at once. Then RV-2 →
RV-3 → RV-5 (RV-5 also needs RV-4 and RV-6: it counts with V5's `route_changed`); RV-6 after RV-1 and RV-4; RV-7 → RV-8; RV-9 after RV-2 (it needs no viewer code:
its tests use a fake viewer), so it can run beside RV-6..RV-8; RV-10 after RV-8 and RV-9; RV-11 last; then H-1 and
the checks with Hong, in spec section 9's order (N1 before L1).

**Critical path:** RV-1 → RV-2 → RV-3 → RV-5 (after RV-6 too); RV-4 → RV-6 → RV-7 → RV-8 → RV-10 → H-1 → H-2 →
H-3/H-4. Nothing depends on RV-5, so it can run beside RV-7..RV-9.

**Comparison units (model-ab.md Part 5).** Part 5 compares every tagged unit (Hong, 2026-10-27), each on its row pick
against its comparison arm; these are the ones that suit it best, being independent and testable without Windows:
- **M:** RV-1 (`orch-haiku-high` pick vs `orch-sonnet-high`). Independent of every other task, pure, fully specified
  by V1; hidden tests can add malformed lines, NaN, torn lines and a legs-only folder with a `LINK_DOWN` leg.
- **O:** RV-3 (`orch-sonnet-xhigh` vs `orch-sonnet-high`): one file, measurable by S1/S2/S4 and hidden tests on the
  node-offset map copy. RV-6 is the second O candidate (pure, golden text), once RV-1 and RV-4 have merged.
- **S:** RV-4 (`orch-opus-xhigh` vs `orch-sonnet-xhigh`): pure and independent, and S3/S3b are exact, so the judge
  has a hard oracle. RV-2 is also S and touches the frozen `walker.py`: compare it only with both arms held to the
  code-shape rule and the same baseline (its step 1).
- Not good comparison units: RV-7 (Tk: its tests need a real Tk root and no cloud run, and its layout is judged by
  eye in its review item), RV-8 and RV-10 (Win32 behaviour no hidden test can see on Linux), RV-9 (long seam
  setup), RV-11 (docs).
- Open with Hong (RV4 I4: model-ab.md Part 5 compares every tagged unit, and whether Part 5 covers this Feature B
  follow-up is his call): the marking of RV-5, RV-7, RV-8, RV-9 and RV-10 above and in RV-5's "Comparison unit: no" may
  change on his answer. Until then it stands as written.

---

## Tasks needing Hong ([Hong]), placed last (one batched list)

| Id | What | Why it needs him | Blocks |
|---|---|---|---|
| H-1 | N1, the no-key check (spec 5.7, 7): with MapleStory closed, run RV-10's tool; it starts the real viewer through the walk's seam on a folder it keeps appending sim events to, keeps its own console as the foreground window, checks that the view updates while live, and asks for four clicks on the viewer (canvas, the why log's text, its scrollbar, the title bar: RV3-D7 A + title bar, decided); it reads the foreground after each, then checks the spawn (no console window, Ctrl+C in the parent's console, the viewer outlives the parent), writes `end`, and asks for one click on the new taskbar button | real mouse input that moves the cursor: Hong, or an agent only while nobody uses the PC (H15's idle rule) and MapleStory is closed | L1; a failure picks the spec's fallback (warning line; `deiconify`; or back to the spec for the spawn) |
| H-2 | L1 + L2: a supervised 1-lap `python -m src.route.walk --map maps/fossil_foundry_2.csv --supervised --laps 1 --view`; watch the view; F12 stops; close the view mid-walk once. Then a second short walk stopped with Ctrl+C in the walk's console: the view stays and switches to replay. L2: results as without the view; frame-gap report next to an earlier `--record` run if one exists | game running, keys sent, his eyes | H-3 |
| H-3 | L3: replay the L1 folder and `data/route/b21-test1` (`python -m src.route_view --follow <folder>`), step and scrub to any move | his eyes | — |
| H-4 | L4: during H-2's walks the game keeps focus all run; once from a cold start (first `--view` walk after a reboot): the countdown starts only after the window is up and its appearance does not take the foreground; a miss of the 15 s wait is reported with `view.log`'s build time | his presence after a reboot | — |

---

## Merge into `p1c-runes` (later; not a build task; spec section 10)

- `walker.py`: expect conflicts in the docstring's "Where the plan is silent" list and around `WalkerConfig` /
  `__init__`. The observer stays a keyword-only constructor argument, not a `WalkerConfig` field; at a leg boundary
  the order is `leg_end` (in `walk_leg`) → `write_leg` → `between_legs` → the next leg's `leg`.
- `src/route/CLAUDE.md:23` (freeze line): after the merge it names two exceptions, `between_legs` (C19) and the
  observer (RV-2), each with its `walker.py` line; RV-11's other route notes go in the same "Traps" section.
- `pyproject.toml`: contracts `g` and `h` (runes) and this plan's two route_view contracts have different names; both
  route_view contracts already list `src.rune_solver`. Contract f is the same on both branches before this plan; after
  RV-1 and RV-4 it lists `step_log` and `route_preview`, which the merge keeps.
- Root `CLAUDE.md`: both branches edit it (C4, C20; RV-11): keep both.
- `.basedpyright/baseline.json`: take either side, then rewrite it on the merged code (`tests/CLAUDE.md`) and check
  the entry count did not rise.
- Known gaps, Feature C's follow-ups, not built here: the view is blind during a rune trip (no `trip` event); the rune
  CLI (C20) passes no observer and writes no `minimap.png`, `map.csv` or `steps.jsonl` (H52's reach over rune runs is
  flagged to Feature C, not re-decided).

---

## Decisions

### Binding (not reopened)

H47-H52 (Hong): own window, live + replay, build now; separate process following the run folder, `--view` turns on
`--record`; no-activate live window with the warning fallback; `walk --view` starts the viewer; 4x with 2x/3x/4x;
every walk saves `minimap.png`. H53 (orchestrator): D3 A observer argument, D4 A preview via the planner's private
helpers pinned by S3/S3b, D5 A `src/route` pure parts + `src/route_view` package.

### Raised by RV3 (open unless marked decided below its block; answers go here)

```
DECISION NEEDED: RV3-D1   (made provisionally in hard rule 6 and V1 · undo: drop `returns` from step_log)
Question: May src/route/step_log.py import `returns` (as leg_log.py does), where spec 5.8 says "standard library only"?
Why it matters: the brief and root CLAUDE.md want Success/Failure in route layers; read_run, start and close need a
  Result; the spec's reason for "standard library only" is plan B's hard rule 6, which allows `returns`
  (tests/route/test_import_rules.py THIRD_PARTY).
Options:
  A. Standard library + `returns` — cost: none; same as leg_log.py; rule 6 and contract f still hold.
  B. Standard library only — cost: read_run and start return a value-or-error-string pair, unlike every other route
     module; the brief's Success/Failure constraint is broken for this module.
Recommendation: A — it keeps the spec's purpose (rule 6) and the brief's error pattern.
Paused meanwhile: nothing (RV-1 builds A) · Continued: the whole plan assumes A.
```

Decided (orchestrator ruling, given to RV3b2 and RV3d): **A**, `step_log.py` imports the standard library and
`returns`. Built in hard rule 6, V1 and RV-1.

```
DECISION NEEDED: RV3-D2
Question: What does a walk do when it cannot write map.csv, minimap.png or steps.jsonl (spec 5.9 step 1 is silent)?
Why it matters: a new failure exit would change walk.py's exit codes (a CLI interface); walking on loses the view.
Options:
  A. Print one line ("step log unavailable: <error>; walking without the route view"), walk on with no observer,
     start no viewer — cost: none to build; Hong gets a walk without the view, as before this feature.
  B. Exit 2 before the countdown, like a leg log that cannot open (walk.py:270-274) — cost: a walk Hong started for
     the view does not run; a new reason for an existing code.
Recommendation: A — spec 5.2 calls the step log "a debugging aid, unlike the leg log", and spec 5.9 says "a viewer
  failure never stops a walk".
Paused meanwhile: RV-9's text for this path (piece 4) · Continued: everything else.
```

Decided (orchestrator ruling, morning-summary level; the adjudicator's case tables are in
`.superpowers/orchestrator/2026-10-21-phase1/notes/RV3-D23.md`): **A**, with two additions that make it buildable.
A walk that cannot write `map.csv`, `minimap.png` or `steps.jsonl` prints one line and walks on with no step log and
no viewer; `walk.py`'s exit codes do not change. **A1** (RV-9, V7 step 4): on that failure the walk calls
`close()` on the step log at once (the walker already holds it as its observer, built before `stop.start()`), adds
no `step_log` cleanup step, and starts no viewer and writes no `view.claim`, even with `--view` (a viewer on a
folder with `legs.jsonl` and no `steps.jsonl` would open in replay while the walk sends keys); the files that did
write stay. **A2** (V3, RV-2 test 12): calls after `close()` are dropped, not queued. Undo: swap V7 step 4's A1
branch for `return 2`, add the cause to `walk.py`'s exit-code docstring, flip RV-9 test 9.

```
DECISION NEEDED: RV3-D3   (made provisionally in V3 · undo: open steps.jsonl with "ab" and let the reader keep the last run)
Question: What happens when a simulator --out folder (which has no re-run guard) already holds a steps.jsonl?
Why it matters: today a second sim run into the same --out appends to legs.jsonl; appending a second run to
  steps.jsonl would put two "run" headers in one file, which the reader and the viewer do not model.
Options:
  A. StepLog.start opens steps.jsonl exclusively ("xb"): run_sim returns Failure ("<folder>/steps.jsonl already
     exists: use a new --out"), the sim CLI exits 2 as for any run_sim Failure — cost: a sim re-run into an old
     folder now fails where it used to append; the smoke (no --out) and the walk (re-run guard) are unaffected.
  B. Append; the reader keeps only the last run of the file — cost: the reader models several runs; legs.jsonl
     still mixes both runs, so S4 cannot hold in such a folder.
Recommendation: A — one run per folder, as the walk's re-run guard (B19-D2) already enforces for walks.
Paused meanwhile: nothing (RV-2 builds A) · Continued: RV-3 assumes A.
```

Decided (orchestrator ruling, morning-summary level; `notes/RV3-D23.md`): **A**, `steps.jsonl` is opened with
`"xb"`; a sim `--out` folder that already has `steps.jsonl` is a `run_sim` `Failure`, exit 2. **A3** (RV-3): that
refusal comes before anything in the folder is touched (before `JsonlLegLog.open` and before `map.csv`), with `"xb"`
kept as the backstop; RV-3 test 9 checks the old folder is byte-identical afterwards. V1: a `run` line after line 1
counts as a bad line and never raises (RV-1 test 5, RV-8 test 7). Undo: open with `"ab"` plus a torn-line repair,
make `read_run` and `StepTail` keep the last header, drop RV-2 test 11's first half and RV-3 test 9.

```
DECISION NEEDED: RV3-D4
Question: How does the preview build hops 2..n (spec 5.3 step 3)?
Why it matters: S3b pins every later hop exactly against next_move at the assumed landing; the build method decides
  how much new rule code there is to get wrong.
Options:
  A. Apply the preview's own P1-P5 steps (the mirror of next_move that steps 1 and 2 need anyway, with the coin flip
     returned as two options) again from each hop's assumed landing, until P1 holds or max_hops; the only new rule is
     the landing point per move kind — cost: least new code; S3b then checks the mirror at every landing and that the
     landings keep the path; it follows spec 5.3 step 3's "the way the planner walks it" literally.
  B. Spec 5.3 step 3's rules as separate code: split the path into runs, repeat `_run` on the distance left, add
     launch-x walks, then the up/down move — cost: a second copy of P4/P5's logic in a new shape, which S3b must catch
     wherever it drifts.
Recommendation: A — same result as B wherever B is right, with one rule set instead of two; the spec's examples
  ("teleport right x2, then walk right about 1.4 s") come out of it unchanged.
Paused meanwhile: RV-4's text (piece 2) · Continued: V4's types, which fit both.
```

Decided (orchestrator ruling, given to RV3b): **A**, the preview re-applies its own copy of the planner steps from
each assumed landing; S3b pins it. Built in V4 and RV-4.

```
DECISION NEEDED: RV3-D5   (made provisionally in RV-4 test 9 · undo: the one edit named there)
Question: What does S3b's "the preview computed from that landing point must give the same remaining path as the
  original preview from hop k on" check, now that hops are built by re-planning at each landing (RV3-D4 A)?
Why it matters: it is half of S3b's pass/fail. Read as "its graph path is a node suffix of the original path", it
  fails on correct planner behaviour: a planning-time prototype of V4's rule fails it on 74 of FF2's 420 node pairs,
  because the re-plan at a landing often starts at a node beside the path (FF2 (113,60)->(101,24): from the landing
  (101,42) the plan starts at (97,43), not (105,42), for the same teleport up at x 101) or at the far end after an
  overshoot. The spec says such a case is raised, not loosened (spec S3b).
Options:
  A. Two parts, both pass/fail: (1) the preview from hop k-1's landing gives exactly hops k..n of the original
     (options, toward, from_pos, landing, cost_to_go_s) and the same `complete`; (2) its path "rejoins": with the
     leading nodes that are not on the original path dropped, it is a non-empty suffix of the original path. Part
     (2) holds on all 24 table cases and on 419 of FF2's 420 pairs at default parameters; the one miss is a real
     near-tie re-route (FF2 (129,60)->(97,43): from the landing (110,60) the cheapest route goes up at x 105, not at
     x 97), so the table pins that pair as a known departure (hops still equal next_move at every landing, part (2)
     false). RV-5 counts part-(2) misses over the sim runs as "route changes at an assumed landing" next to the
     real-landing rate; a miss there is reported, not a failure — cost: part (2) is no longer pass/fail on the sim
     runs; the moves stay exact everywhere (the half of S3b that pins the planner).
  B. Part (2) pass/fail everywhere, the FF2 departure pair left out of the table — cost: RV-5 fails if a sim run
     plans from such a position; a test that fails on correct behaviour.
  C. `Preview.path` becomes the route the hops follow (the original path, replaced from the hop where a re-plan
     departs), so part (2) holds by construction — cost: `path` no longer is "rg.path(start, target)" (V4's doc),
     and the check then pins nothing.
Recommendation: A — the planner-agreement half stays exact everywhere; the path half is exact on the table and
  measured on the sim runs, and the view draws the expected track from the hops' landings, which always matches.
Paused meanwhile: nothing (RV-4 builds A; under B or C only RV-4 test 9's departure case and RV-5's counting change) ·
  Continued: RV-4 in full.
```

Decided (orchestrator ruling as Hong, given to RV3b2; the adjudicator's case tables are in
`.superpowers/orchestrator/2026-10-21-phase1/notes/RV3-D5.md`): **A2**, a corrected A. (1) Pass/fail everywhere (the
table, the FF2 sweep, both 20-lap sim runs): each hop equals `next_move` at the landing it starts from, and the
preview from hop k-1's landing gives exactly `hops[k:]` and the same `complete`; "the same remaining path" is the
route the hops take. (2) `_rejoins` is dropped. In its place a measure, `follows(path, hops)`: every hop's `toward`
lies on `path`, in path order. It is pinned as an exact set of 9 node pairs on the FF2 sweep (RV-4 test 10), D1 stays
in the table as a known departure (RV-4 test 9), and on the sim runs it is reported only (RV-5). (3) `Preview.path`
is unchanged (`rg.path(start, target)` from pos, the why log's route); the view draws the route line and the panel's
"Route" text from the hops (V5). Also fixed with it: D1 is an exact cost tie (3.475 s both ways from (129,60), 2.175 s
both ways from (110,60)) that `rg.path` breaks differently from different sources, not a near-tie re-route (RV-4's
table and test 9); V5's route-changed rule compares the hops' routes (`hop_route`), and RV-5 counts S3b's
real-landing rate with V5's own functions (spec S3b: "the same comparison"). Undo: plan text only (RV-4 tests 9 and
10, V5's route-changed rule and Route line, RV-5 tests 4 and 5).

```
DECISION NEEDED: RV3-D6   (made provisionally in V5 "Times" · undo: one function, `_clock_text`, in model.py)
Question: What time does each "why" line start with, when spec 5.4's sketch shows a wall-clock time (10:15:03.512)
  but no file of the run holds a wall-clock time for each event?
Why it matters: it is what Hong reads on every line; the event times are `perf_counter_ns` (walk) or virtual
  `sim_ns` (simulator), and `steps.jsonl`'s header has no wall-clock anchor (V1, spec 5.2).
Options:
  A. Elapsed time since the run's first event, `mm:ss.mmm` (`h:mm:ss.mmm` from one hour) — cost: none; the same for
     walk, sim and legs-only runs; not the time of day of the sketch.
  B. Time of day: `session.json`'s start time plus each event's offset from the first event — cost: off by the time
     between writing `session.json` and the first event (the countdown, about 5 s), so the times look exact and are
     not; no anchor for sim folders (they would fall back to A).
  C. Add a wall-clock field to the `run` header (`time.time_ns()` beside `perf_counter_ns()` at the same moment) and
     show time of day from it — cost: a V1 format change (RV-1, RV-2, RV-9 and their tests), a spec change.
Recommendation: A — exact for every source, and the sketch's times are illustrative (spec 5.2 calls its example
  times illustrative too); C is the one to choose if Hong wants time of day.
Paused meanwhile: nothing (RV-6 builds A) · Continued: RV-6..RV-8 in full.
```

Still open: waits for Hong (it is what he reads on every line). Option A is what RV-6 builds; B or C changes only
`_clock_text` (C's header anchor is now recorded by every walk, RV4-D2 A below, so C needs no format change, and a
walk logged before Hong answers can still show time of day; RV-6's golden why-lines carry the time prefix and would
be rewritten).

```
DECISION NEEDED: RV3-D7   (made provisionally in RV-10's CLICKS and H-1 · undo: RV-10's CLICKS tuple, RV-7's set_live)
Question: Which parts of the live viewer does the no-key check click, when spec 5.7 says "click the viewer's canvas,
  a button and the slider" but the live window has no button or slider (RV-7 hides the whole replay bar while live,
  from spec 5.4's sketch "Replay only: |< < step > >| [slider] 1x 4x leg [3] lap [2]")?
Why it matters: N1 is the pass/fail evidence that a click on the live window does not take the game's focus (RV1-D6
  B, Hong's). What it clicks is what it proves; and what the live window shows is what Hong sees.
Options:
  A. Click what the live window has: the canvas, the "why" log's text and its scrollbar (three clicks, as the spec's
     count). The text widget is the one whose Tk class bindings are likeliest to call `focus` on a click — cost: no
     code beyond RV-10's prompt list; N1 no longer clicks a button or the slider, which do not exist while live, so
     the spec's words are not met literally.
  B. Show the replay bar while live with its controls disabled ("watch-only controls while live", spec 5.7's
     fallback wording), and click the canvas, a disabled button and the disabled slider — cost: RV-7's `set_live`
     and its test 8 change (disable, not hide); Hong sees greyed-out controls during every live walk; a disabled Tk
     widget runs no class binding, so the click proves less about bindings than A's text click.
  C. Both: A's three clicks plus B's two (five) — cost: B's UI change and two more clicks for Hong.
Recommendation: A — it keeps the window piece 3 specified (spec 5.4's "Replay only") and clicks the widget most
  likely to take focus; the spec's "button and slider" are read as "the controls the live window has".
Paused meanwhile: nothing (RV-10 builds A; under B or C only RV-10's CLICKS tuple and RV-7's `set_live` change) ·
  Continued: RV-9..RV-11 and H-1..H-4 in full.
```

Decided (orchestrator ruling as Hong, while he is away; the adjudicator's case tables are in
`.superpowers/orchestrator/2026-10-21-phase1/notes/RV80-ADJ.md` section 2): **A plus a fourth click on the title bar**
(press and release, no drag). Spec 5.7's "click the viewer's canvas, a button and the slider" (spec:496) is read as
"the controls the live window has": the button and slider exist only in the replay bar (spec 5.4 "Replay only",
spec:379), hidden while live. That holds for the slider always and for the buttons as RV-7 builds them: spec 5.4
does not mark the 2x/3x/4x scale buttons "Replay only" (spec:383-384), so this reading rests on RV80-ADJ-D1
staying A (its note below lists what changes under B; RV6 m1). Checked against Tk 8.6.15's library as installed with Python 3.14
(`.../Python314/tcl/tk8.6/`): `bind Text <1>` (`text.tcl:45-48`) runs `proc ::tk::TextButton1`
(`text.tcl:562-583`), which calls `focus $w` unconditionally (`text.tcl:579`); the Text's other focus calls are on
`<Tab>` (`:196`), `<Control-Tab>` and `<Control-Shift-Tab>` (`:206`, `:209`, `focus [tk_focusNext/Prev %W]`), all
keys, and the middle-button paste (`:730`, only when the widget's state is `normal`; the why log
is read-only). `scrlbar.tcl`, `scale.tcl` and `button.tcl` contain no `focus`; the canvas, labels and frames have no
class bindings in the library; the two `bind all` lines in `tk.tcl:526-527` are keyboard traversal, and the library's
other `bind all` lines are keys (`menu.tcl:180,184`: `<Alt-Key>`, `<F10>`) or exist only inside
`tk_focusFollowsMouse` (`focus.tcl:164-176`, which the viewer never calls). So the why log's
Text is the one live widget whose click binding calls `focus`, and the title bar is the click Hong is likeliest to
make (to move the window). Undo: RV-10's `CLICKS` tuple and H-1's text.

### Raised by the plan review (RV4) and its adjudication (RV80-ADJ)

```
DECISION NEEDED: RV3-D8   (raised by RV3b2 as a [review] concern; cases in notes/RV4.md "RV3-D8 cases")
Question: Does "route changed" fire when a re-plan keeps the move's kind, direction and node and changes only how
  long the walk is (the landing moved its start)?
Why it matters: it decides how often Hong reads "route changed" (6-9% of moves under exact comparison, some naming
  the same node and move twice), RV-5's printed agreement rate, and RV-6 test 10.
Options:
  A. Exact (`move in P.hops[1].options`, `walk_ms` compared with `==`) — cost: none to build; lines whose two sides
     read the same after rounding.
  B. Ignore walk length: compare `(kind, direction)` only — cost: a re-plan that changed only the walk's length gets
     no line (the settled line and hop 1 still show it); RV-6 test 10 gains cases; RV-5's expected numbers change.
  C. A tolerance on walk length — cost: a parameter to pick and test.
  D. A separate "walk adjusted" line — cost: new wording to settle with Hong.
Recommendation: B — "route changed" then means what spec 5.4 says (the route) and the causes spec 5.3 names.
```

Decided (orchestrator ruling as Hong, while he is away; `notes/RV80-ADJ.md` section 3): **B**. V5's rule compares
`(kind, direction)`; the line keeps RV3c's move words. Both are recorded in RV-11's As-built (a departure from spec
5.4's wording, spec:402). Expected rates (RV3b2's prototype): plain 204 of 239, offset 196 of 216. Undo: the V5
rule, RV-6 test 10's walk-length-only False case, RV-5's expected numbers.

```
DECISION NEEDED: RV4-D1
Question: How does Hong replay a walk folder that has `steps.jsonl` and no `end` (a walk that crashed or was killed:
  Task Manager, power loss, its console window closed, so no `finally` ran)?
Why it matters: V6 sends every such folder to live follow, which hides the replay bar and shows only the newest
  step, so the run Hong most needs to step through can never be scrubbed (against H49/H50 and Review Focus 1).
Options:
  A. A `--replay` flag typed by hand (refused with `--ready`) opens any folder as a replay; a live view silent for
     10 s names the flag in its status line — cost: about 20 lines in code RV-7 already builds; Hong reopens by hand.
  B. The walk passes its pid; the viewer switches to replay when that process is gone — cost: changes spec 5.9 step
     2's Popen arguments, a liveness poll in RV-8, a false "gone" would activate the window mid-walk; a folder opened
     later still needs the pid in the header and a guard against pid reuse.
  C. Accept it; fix the Review Focus text — cost: a crashed walk stays unscrubbable.
Recommendation: A — fixes every no-`end` case with no new Win32 call and no new activating transition.
```

Decided (orchestrator ruling, while Hong is away; `notes/RV80-ADJ.md` section 1): **A**, the `--replay` flag
(refused with `--ready`) plus the status hint. B (pid) not built: the spec accepts the hidden orphan (5.9 step 3),
and B alone does not cover a folder opened later. A header-only folder (killed in the countdown) replays with no
steps (RV-7's 0-event rule, test 14). Built in V6, RV-7, RV-8 (tests 4, 9g-9i), RV-11. Undo: drop the flag, its
RV-7 parse line, RV-8's branch, tests 9g-9i, the hint and RV-7 test 14.

```
DECISION NEEDED: RV4-D2
Question: Does the step-log header record a wall-clock anchor now, though RV3-D6 (the why-line time) is open?
Why it matters: RV3-D6's option C (time of day) needs an anchor; a step log written without one can never show time
  of day, and adding it later changes V1, RV-1, RV-2, RV-9 and their tests.
Options:
  A. Optional `wall_ns` and `perf_ns` header fields (`time.time_ns()` and the run clock, read together when the walk
     builds the header), null in the sim and legs-only headers; nothing reads them yet — cost: two fields and a few
     test lines; a later C is one function (`_clock_text`).
  B. No anchor until Hong answers RV3-D6 — cost: walks logged meanwhile never show time of day.
Recommendation: A.
```

Decided (orchestrator ruling): **A**. Built in V1 (`RunHeader`), plan choice 5, V7 step 4, RV-1 tests 2 and 7,
RV-3 (sim header), RV-9 test 2. The viewer does not read them (RV3-D6 A is built). Undo: drop the two fields and
their test lines.

RV80-ADJ-D1 (the 2x/3x/4x scale buttons sit in the replay bar, so they are hidden while live; spec 5.4 does not mark
them "Replay only", spec:383-384) is open with Hong. The plan keeps them hidden while live, as built in RV-7; RV-11's
As-built says so and that it may change on his answer. If he answers B (scale buttons shown while live), these
follow (RV6 m1): RV-10's `CLICKS` gains a fifth entry (a scale button) and H-1's "four parts" (step 2) become
five; RV-7's layout moves the scale buttons out of the replay bar and its test 8 checks they stay packed under
`set_live(True)`; the buttons must stay `tk.Button` (a test pins the class), because `ttk/button.tcl:27` runs
`ttk::clickToFocus` on a press and RV3-D7's "`button.tcl` has no `focus`" covers only `tk.Button`; and RV3-D7's
reading of spec 5.7's "a button" is revisited.

---

## Tasks

### Task RV-1: Step-log format and reader

**Files:** create `src/route/step_log.py` (the V1 part only; RV-2 adds `StepLog`); `pyproject.toml` (contract f's
bottom layer gains `src.route.step_log`); tests `tests/route/test_step_log.py`.
**Class:** M — one pure module fully given by V1. **Cloud:** yes. **Depends on:** RV3-D1 (built as A).
**Interface created:** V1 (used by RV-2, RV-3, RV-6, RV-8, RV-9).

- [ ] Read V1, plan choices 3, 4 and 8, spec 5.2 (event table, the example lines) and 5.5 (legs-only row),
  `src/route/leg_log.py:1-15` (strict JSON, binary append) and `:87-126` (the leg and lap JSON), and a real
  `session.json` shape (`walk.py:287-299`).
- [ ] `pyproject.toml`: contract "f route layers", last layer becomes
  `"src.route.params | src.route.leg_log | src.route.character | src.route.step_log"`. Ground the `|` syntax
  (independent siblings) in the installed import-linter's docs or source before relying on it (hard rule 12).
- [ ] Module docstring: what the file holds, the "Where the plan is silent" items you decide (each one line).
- [ ] **Tests** (`tests/route/test_step_log.py`; write them first):
  1. `FIELDS` equals the spec 5.2 table, key by key and in order; `EVENTS` is its keys; `"run"` is not in it.
  2. `header_line` of a `RunHeader` gives one line ending `b"\n"` whose JSON keys are, in order, `ev, version,
     source, clock, map, map_sha256, class, params, rotation, git_head, wall_ns, perf_ns`; `parse_line` gives the
     equal `RunHeader` back (rotation as a tuple of tuples), with `wall_ns`/`perf_ns` as ints and, in a second
     header, as null → None; a header line without the two keys parses with both None (RV4-D2 A).
  3. For each event name, `event_line((ev, t_ns, *values))` then `parse_line` gives a `StepEvent` with the same
     `ev`, `t_ns` and values; `pos`, `toward`, `from`, `to` come back as tuples or None; `keys` as a tuple of pairs.
     The spec's example `planned` line (spec 5.2: `pos [62, 58]`, `teleport`, `RIGHT`, `walk_ms` null, `3.725`,
     `toward [81, 58]`) parses to those values.
  4. `event_line` → `Failure` (never raises) for: an unknown `ev`; one value too few or too many; a NaN or inf
     value; a value JSON cannot encode (an `object()`). `parse_line` → `Failure` for: not JSON, a JSON array, a
     missing `ev`, an unknown `ev`, a missing field, bytes that are not UTF-8.
  5. `StepTail` on a missing file → `[]`; after a header and two events are appended → those three; then half a
     line is appended → `[]`, the rest of it plus one more line → those two; a bad line between two good ones →
     the two good ones and `bad_lines == 1`; lines written with `\r\n` are not produced by the writer, but a reader
     given one still parses it (strip `\r`).
     A second `run` line (V1, one run per file): header, event, a second valid header line, event → `poll` gives the
     first header and both events, not the second header, and `bad_lines == 1`; `read_run` on the same file gives the
     first header, both events and `bad_lines == 1`; neither raises.
  6. `read_run` on a folder with `steps.jsonl` ending in `end` → `origin "steps"`, `ended True`; without `end` →
     `ended False`; a `steps.jsonl` whose first line is not a `run` header → `Failure` naming the file.
  7. Legs-only: a folder with `session.json` (the `walk.py:287-299` keys) and `legs.jsonl` of two legs (written
     with `leg_log.leg_to_json` in the test, so the shape is the real one), one of them `LINK_DOWN` with a last move
     whose `settled` is null → header `source "legs"`, `map` the session's `map`, `map_sha256`, `class`, `params`,
     `rotation None`, `wall_ns None`, `perf_ns None`; events `leg, planned, settled, ..., leg_end` per leg in file order, `planned` carrying the
     move's `t_ns, pos, kind, dir, walk_ms, cost_to_go_s` and `toward None`, `settled` with `t_ns None` and the
     move's `settled`; no `settled` after the `LINK_DOWN` leg's last move; `leg_end` with the record's `end_ns`,
     outcome and counts; `ended True`.
  8. `read_run` on an empty folder → `Failure` naming the folder and saying neither `steps.jsonl` nor `legs.jsonl`
     is there; a legs-only folder without `session.json` → `Failure` naming it.
  9. Importing `src.route.step_log` imports no `src` module except `src` (a namespace package: there is no
     `src/__init__.py`), `src.route` (`src/route/__init__.py`, empty) and `src.route.step_log` itself (RV4 m4)
     (check `sys.modules` before and after in a
     `python -I -c` subprocess with the repo root put on `sys.path`; mark `slow`).
  10. Review Focus 1: a `steps.jsonl` of a header and 5 events whose last line has no `b"\n"` → `read_run` gives the
      header and the 4 complete events, `ended False`, and does not raise.
  11. Review Focus 2: a legs-only folder whose `session.json` `map` path does not exist → `Success` (the map is the
      viewer's to check, V5); a `session.json` without `params` or without `class` → `Failure` naming the key and the
      file.
  12. Review Focus 5 (mark `slow`): `read_run` of a synthetic `steps.jsonl` of 50,000 events finishes within 2 s on
      the dev PC; `StepTail.poll` after one more line reads only that line (check `tell()`-style offset, not a re-read).
- **Run:** `python -m pytest -q tests/route/test_step_log.py tests/route/test_import_rules.py` then `lint-imports`.
- **Commit:** `feat(route): step-log format and reader for the route view` (body: the import-linter grounding).

### Task RV-2: Walker observer and the step-log writer thread

**Files:** `src/route/walker.py` (the observer only: hard rule 3, RV1-D3 A); `src/route/step_log.py` (add
`StepLog`, V3); tests `tests/route/test_walker_observer.py`, `tests/route/test_step_log_writer.py`. No file under
`tests/route/` that exists today changes.
**Class:** S — a change inside the frozen control loop with a no-change guarantee, plus a writer thread whose
shutdown must not lose or reorder lines. **Cloud:** yes (simulator only), except that step 1's baseline and T5's
re-run must be on the same machine. **Depends on:** RV-1, RV3-D3 (built as A).
**Interfaces created:** V2, V3 (used by RV-3, RV-9).

- [ ] Read V2, V3, hard rules 3 and 4, plan choices 1-4, spec 5.2 (whole), `walker.py:1-27` (the docstring),
  `:57-64`, `:92-115`, `:117-204`, `:275-298`.
- [ ] **Step 1, baseline before any edit** (same machine as T5): in Git Bash, `mkdir -p data/route_view/rv2 &&
  python -m src.route.sim.run --laps 1000 --seed 1 --known-case 49,28:101,24 --known-case 101,24:105,42 >
  data/route_view/rv2/before.txt` and the smoke hash (hard rule 11). Record the commit in the task's notes.
- [ ] `walker.py`, the observer only:
  - `StepObserver` (V2) after `PositionSource`, with a one-line docstring;
  - `observer: StepObserver | None = None` keyword-only after `config` (a bare `*` before it), stored as
    `self._observer`;
  - `_emit(self, call, *args)` (plan choice 2): `try: call(*args)` / `except Exception as exc:` set
    `self._observer = None` and print `route view observer failed, not called again this run:
    {type(exc).__name__}: {exc}` to stderr;
  - the nine call sites of V2, each `if self._observer is not None: self._emit(self._observer.<event>, ...)` with
    every argument (and any `clock.now_ns()`) inside the branch; `leg_end`: build the `LegRecord` into a local, emit,
    return it;
  - one bullet in the docstring's "Where the plan is silent" list: the observer is optional, called at the V2
    points on the walker's thread, disabled after its first exception; with None nothing else happens.
  Nothing else changes: `git diff` shows only these lines.
- [ ] `step_log.py`: `StepLog` as V3. The queue is `queue.SimpleQueue`; the thread is named `step-log`, daemon; the
  sentinel is a private module object; `close` is idempotent and safe before `start` (it then discards the queue and
  returns `Success`). A private `_closed` flag, set first thing in `close`, is tested first in every V2 method and in
  `end`: once set they return without building or queuing anything (V3, RV3-D2 A2); `start` after `close` is a
  `Failure`. Ground `SimpleQueue.put`'s "never blocks" and `Thread.join(timeout)` in the venv's docs or
  source (hard rule 12).
- [ ] **Tests**, `test_walker_observer.py` (a sim rig built as `sim/run.py:_replay_moves` does: `SimWorld`,
  `SimClock`, `SimLink`, `SimPositionSource`, `InputLayer`, `WalkerConfig(use_stimulus=False, ...)`, FF2 fixtures;
  a `RecordingObserver` in the test keeps `(name, args)` per call):
  1. Code shape (S1's rule, as text): parse `walker.py` with `ast`; every call of `self._emit` is the only statement
     inside an `if self._observer is not None:` whose body holds it. Values that differ by case (`sent`'s on success
     and on failure, V2) come from a private module-level helper in `walker.py` (e.g. `_sent_values(sent)`) called
     inside the `_emit` arguments, not from an inner `if` in the branch (RV4 m1). No `now_ns()` call in `walk_leg` lies
     outside such a branch except the four the walker already makes (:123, :133, :142, :198 at `5d1176a`: the line
     numbers may move, so count them: four).
  2. S2 at walker level: two walkers from the same seeds walk 3 laps of FF2, one with a `RecordingObserver`, one
     without → equal `MemorySink.legs` and `.laps`, and the four generators' `bit_generator.state` equal at the end.
     The same with `NoiseConfig(node_offset_px=0, miss_p=0.2)` so `read_failed` events occur.
  3. Event order per leg: `leg` first, `leg_end` last; between them only `read_failed`, `no_plan`, `stall`,
     `reached`, `planned`, `sent`, `settled`; every `planned` is followed by one `sent`, then one `settled` unless the
     leg ended `LINK_DOWN` (a `SimLink.fail_next` `LINK_LOST` case); `reached` comes right before `leg_end` of a
     `REACHED` leg.
  4. Values: each `planned` equals the matching `MoveEntry` of the leg record (`t_ns`, `pos`, `kind`, `direction`,
     `walk_ms`, `cost_to_go_s`), its `toward` is a node position in the route graph; each `sent.input_name` equals
     the entry's `input`; each `settled.pos` equals the entry's `settled`; `leg_end` carries the record's `end_ns`,
     outcome value and counts; `sent.keys` is the `plan.steps` of the `ActionRecord` the `SimLink` played (a spy
     link records the plans), `reaction_ms None`, `waited_ms 0.0`.
  5. A stall: a world with `no_op_p=1.0` and a short `stall_s` (via `MovementParams(stall_s=...)` in the test's
     route graph) → a `stall` event whose `best_cost_s` is the walker's best (or `math.inf` before the first plan).
  6. `no_plan`: a position source that returns a position off the graph → `no_plan` with `next_move`'s message.
  7. Guard: an observer whose `planned` raises `RuntimeError("boom")` → stderr has exactly one line containing
     `route view observer failed` and `boom`; no observer method is called after it (the recording part sees no call
     after the raise); the leg records equal those of a run with no observer.
  8. Positional construction still works: `RouteWalker(rg, rotation, layer, positions, clock, sink, rng, config)`;
     passing an observer positionally (a ninth positional argument) raises `TypeError`.
- [ ] **Tests**, `test_step_log_writer.py` (no sim; a `tmp_path` folder; a fixed `RunHeader`):
  9. `start` writes the header line at once (readable before any event); events queued before `start` are written
     after it, in order; `end` then `close` → the file is header, events in call order, `end` last; `read_run` reads
     it with `ended True`.
  10. Every V2 method writes the line `event_line` makes of the same values (compare bytes); `stall` with
      `math.inf` writes `"best_cost_s": null` (plan choice 3).
  11. `start` into a folder that already has `steps.jsonl` → `Failure` naming it, the file unchanged, no thread
      started (RV3-D3 A); `start` on a path that cannot be opened (a folder named `steps.jsonl`) → `Failure`.
  12. `close` twice → the second is a no-op `Success`; `close` before `start` → `Success`, nothing written, and a
      later `start` → `Failure("step log already closed")` with no file created. Calls after `close` are dropped,
      not queued (RV3-D2 A2): after `close` (both before and after a `start`), 1,000 calls of each V2 method and
      `end` do not raise, the private queue's `qsize()` stays 0 (the test may read it), and the file is
      byte-identical to what it was at `close`.
  13. A write failure (patch the file object's `write` to raise `OSError` after the header) → `failure` is set,
      stderr has one line, later events do not raise, `close` returns `Failure` with that message.
  14. Slow disk: patch `write` to sleep 3 s once → `close(join_s=0.2)` returns within about 0.5 s with
      `Failure("writer still busy ...")` and does not raise; mark `slow`.
  15. 10,000 events from the calling thread, then `close` → all 10,000 lines present, in order (no loss at shutdown).
- [ ] **T5 extra (S1):** rerun step 1's 1000-lap command into `data/route_view/rv2/after.txt`; `diff` it against
  `before.txt` (no difference is the pass); the smoke hash (hard rule 11); `git diff --stat 5d1176a --
  tests/route/test_params.py tests/route/test_terrain.py tests/route/test_route_graph.py tests/route/test_locate.py
  tests/route/test_planner.py tests/route/test_moves.py tests/route/test_walker_leg.py
  tests/route/test_walker_run.py` is empty. The commit body states all three results.
- **Run:** `python -m pytest -q tests/route/test_walker_observer.py tests/route/test_step_log_writer.py tests/route`
- **Commit:** `feat(route): optional step observer in the walker and the step-log writer` (body: the S1 results, the
  grounding of `SimpleQueue` and `join`, RV1-D3 A / H53).
- **Review item:** the reviewer reads the `walker.py` diff and confirms the code-shape rule by eye (spec section 9),
  not only test 1.

### Task RV-3: Simulator writes `steps.jsonl` and `map.csv`; checks S2, S4 and S8

**Files:** `src/route/sim/run.py`; tests `tests/route/test_sim_steps.py`. `tests/route/test_sim_run.py` is not
changed.
**Class:** O — one file, several design points (the shifted map copy, the run header, `observer` vs `out_dir`).
**Cloud:** yes. **Depends on:** RV-2.
**Interface used:** V1, V2, V3; plan choices 5-7.

- [ ] Read plan choices 5-7, V1 (`RunHeader`), V3, spec 5.2 (the simulator bullet), S2, S4, S8, and
  `sim/run.py:1-30`, `:163-176`, `:244-310`, `:386-412`.
- [ ] `run_sim` gains `observer: StepObserver | None = None` (keyword, last; import `StepObserver` from
  `src.route.walker`, which `sim/run.py` already imports from). `observer` and `out_dir` both given → `Failure("give
  observer or out_dir, not both")` before anything runs.
- [ ] `_load_shifted_agent` returns `(agent, csv_bytes)`: read `shifted.read_bytes()` inside the `with` block, after
  `_load_agent`. Its caller in `run_sim` unpacks it. Nothing else draws from `rng_world` (plan choice 7).
- [ ] With `out_dir`, in this order (RV3-D3 A with A3: refuse before any file in the folder is touched):
  1. `out_dir / STEPS_FILE` exists → return `Failure(f"{out_dir / STEPS_FILE} already exists: use a new --out")`
     before `JsonlLegLog.open` and before `map.csv` (the old folder's `map.csv`, `legs.jsonl` and `laps.jsonl` stay
     byte-identical, and no leg-log handle is opened);
  2. `JsonlLegLog.open` (unchanged);
  3. write `out_dir/map.csv`, then `StepLog().start` (below). `start`'s exclusive `"xb"` stays as the backstop: if it
     still returns a `Failure` (a race, or an open error), close the leg log that step 2 opened, then return it.
  Step 3 in detail: write `out_dir/map.csv` (the `--map` file's bytes, or the shifted
  bytes with node offsets), build the `RunHeader` (`source "sim"`, `clock "sim_ns"`, `map "map.csv"`, `map_sha256` of
  those bytes, `class` the `_CLASSES` key of `character`'s type, `params` `dataclasses.asdict(params)`, `rotation` the
  positions of `rotation`, `git_head None`, `wall_ns None`, `perf_ns None`: plan choice 5), `StepLog().start(out_dir, header)` (a `Failure` → close the leg log, return it), and pass
  the `StepLog` as the run's walker's `observer` (`sim/run.py:299` only; the replay walkers of `_replay_moves` get
  none, spec 5.2). With `observer` instead, pass it the same way.
- [ ] When `walker.run()` returns (in the existing `finally`, before `log.close()`): `step_log.end(clock.now_ns(),
  summary.reason or None, None)` (`reason` None when `run` raised or returned a Failure), then `step_log.close()`; a
  close `Failure` is printed to stderr, never returned (the report is still good).
- [ ] The module docstring's "Where the plan is silent" list gains: with `--out` the run also writes `steps.jsonl` and
  `map.csv`; `--out` into a folder that already has `steps.jsonl` fails (RV3-D3 A).
- [ ] **Tests** (`tests/route/test_sim_steps.py`; FF2 fixtures; `run_sim` directly):
  1. S1: `run_sim(..., laps=20, seed=1)` with `out_dir=None, observer=None` gives the same `SimReport` as before the
     change (pin the report's fields from a run on the parent commit, written into the test as literals; at least
     `laps_completed 20`, `legs_reached 141`, `moves 379`, spec 7); and the CLI smoke hash (hard rule 11) at T5.
  2. S2 at `run_sim` level: seed 1, 20 laps, once with a recording observer and once without → equal `SimReport`s;
     the same with `NoiseConfig(node_offset_px=2)`.
  3. S4, plain run with `out_dir`: `read_run(out)` → header first (`source "sim"`, `clock "sim_ns"`, `wall_ns` and
     `perf_ns` None, rotation equal to
     the FF2 blue nodes in file order), `end` last with `reason "max_laps"` and `code None`; for each leg record of
     `legs.jsonl`, in order, one `leg` … `leg_end` group whose `planned`/`sent`/`settled` map one to one onto its
     `moves` (`planned`: `t_ns`, `pos`, `kind`, `dir`, `walk_ms`, `cost_to_go_s`; `sent.input`; `settled.pos` as
     `settled`; a move with `settled` null at the end of a `LINK_DOWN` leg has no `settled` event), and `leg_end` has
     the record's `end_ns`, outcome and counts. `sent` and `settled` times are not compared (spec S4).
  4. S4 with `NoiseConfig(node_offset_px=2)` and `out_dir`: as test 3, and `map.csv` is the shifted copy: loading it
     with `MapleAgent.loadMap` gives node positions equal to the header's rotation and to the run's leg `to`
     positions; the `SimReport` equals the one from the same run with `out_dir=None` (the copy drew nothing).
  5. `map.csv` of a plain run is byte-equal to the `--map` file; its sha256 is the header's `map_sha256`.
  6. Rebuild: `RouteGraph.build` on the `pathGraph` of `loadMap(out/map.csv)` with `MovementParams(**header.params)`
     gives the same edges (as `(u.minimap_position, v.minimap_position, kind, cost_s)` sets) as a build from the
     original FF2 CSV with default params.
  7. A link-down case: monkeypatch `src.route.sim.run.SimLink` with a subclass that, in its first instance only (the
     run's link, built at `sim/run.py:289` before any recovery replay), pushes `InputError.LINK_LOST` into
     `fail_next` (`sim/link.py:70`) on its 30th `play` → `end.reason "link_down"`, and the last leg has no `settled`
     after its last `planned`/`sent`.
  8. `observer` and `out_dir` together → `Failure`, and no file is created in `out_dir`.
  9. `--out` into a folder that already has `steps.jsonl` → the CLI prints `error: ...steps.jsonl already exists...`
     and exits 2 (RV3-D3 A). Make the folder with a finished 1-lap `run_sim(..., out_dir=out)` first; record the
     bytes of every file in it; after the refused run every file is byte-identical (`map.csv`, `legs.jsonl`,
     `laps.jsonl`, `steps.jsonl`), no file was added, and `shutil.rmtree(out)` succeeds at once (no leg-log handle
     left open: on Windows an open handle makes the delete fail) (A3). The same through `run_sim` directly: a
     `Failure` with that message, and the files unchanged.
  10. S8 (mark `slow`): a `TimingStepLog(StepLog)` in the test wraps each V2 method with `time.perf_counter_ns()`
      before and after the `super()` call; over a 20-lap run the median per-event time is under 20 µs; after `close`,
      the writer thread has exited and every queued event is in the file; `close` returns within 1 s of `run`'s end.
      The test prints the median (the commit body quotes it). It measures the observer's own work, not the argument
      building in `walker.py` (a tuple of at most 16 pairs, V2); say so in the test's docstring.
- **Run:** `python -m pytest -q tests/route/test_sim_steps.py tests/route` and the smoke hash.
- **Commit:** `feat(route): the simulator writes steps.jsonl and map.csv with --out` (body: S1 hash, the S8 median).

### Task RV-4: Route preview and the hand-built agreement table (S3 part 3, S3b on the table)

**Files:** create `src/route/route_preview.py`; `pyproject.toml` (contract f gains `src.route.route_preview`); tests
`tests/route/test_route_preview.py` and the flat test helper `tests/route/preview_checks.py` (no `test_` prefix, so
pytest does not collect it; RV-5 imports it as `from preview_checks import s3b_problems, follows`, as
`tests/CLAUDE.md` describes flat helper imports). No frozen file changes (hard rule 3): `planner.py` is read and
called, never edited.
**Class:** S — a line-for-line copy of the frozen planner's rules plus new landing rules, pinned by exact checks.
**Cloud:** yes. **Depends on:** RV3-D4 (decided A), RV3-D5 (decided A2). Independent of RV-1..RV-3.
**Interface created:** V4 (used by RV-5, RV-6).
**Comparison unit (S):** the oracle is exact (S3, S3b), so hidden tests may add any FF2 node pair or synthetic graph.

- [ ] Read V4 (whole, with the landing rule), hard rules 3 and 6, spec 5.3, S3 and S3b (spec section 7),
  `planner.py:1-247` (all of it: `_plan` copies `:190-247`), `locate.py:84-94`, `route_graph.py:36-54`, `:142-160`,
  `params.py:10-26`, and `tests/route/test_planner.py:18-61` (the helpers this task copies) and `:192-440` (the
  branch-forcing cases the table reuses).
- [ ] `pyproject.toml`, contract "f route layers": the line `"src.route.moves",` becomes
  `"src.route.moves | src.route.route_preview",` (a layer above `planner`, spec 5.8; the two may not import each
  other). Ground `|` in the installed import-linter (hard rule 12; RV-1 grounds it too: cite its commit if it has
  landed). RV-1 edits the last line of the same list; the two edits do not touch the same line.
- [ ] `src/route/route_preview.py`: the V4 types, `preview`, and private `_plan`, `_landing_horizontal`,
  `_landing_vertical`, `_surface_y` and the two stub generators. Module docstring: what the preview is (spec 5.3),
  that it copies `next_move`'s P1-P5 and calls the planner's private helpers (RV1-D4 A, H53; the seam
  `src/route/CLAUDE.md` gets in RV-11), and its "Where the plan is silent" items (each one line). `_plan` follows
  `planner.py:190-247` statement for statement, in the same order, with the same variable names where they exist
  (`anchor`, `on_slope`, `candidates`, `total`, `start`, `path`, `gi`, `g`, `h`, `launch`, `g_next`): the reviewer
  reads the two side by side. It differs only in what it returns (the decision's options, `toward`, `total`, the
  starts, the path and the move's aim) and in the coin flip (V4).
- [ ] **Tests** (`tests/route/test_route_preview.py`; write them first). Copy `_node` and `_synthetic` from
  `test_planner.py:33-61` (`_synthetic(points, can_fall=False, **params)` passes `MovementParams(**params)`), add
  `_ff2(csv=FIXTURE, **params) -> (agent, rg)` and `_edited_ff2(tmp_path, *replacements)` (the FF2 text with
  `str.replace` pairs applied, written with `newline="\n"`, as `test_planner.py:327-330`). Names: `TP_R = Move(TELEPORT,
  RIGHT, None)` and so on, `DROP`, `TP_DOWN`, `TP_UP`; a hop written `kind dir walk_ms -> toward @ landing`.

  **The table.** One list of cases, each `(id, graph, pos, target, expected)`; `#i` is node `i` of `_synthetic`;
  positions are node positions on the graph named. Expected values were computed at planning time by a prototype of
  V4's rule (RV3b notes); a literal that disagrees with V4's rule text is a plan error to report, not a reason to
  change the rule, and `cost` is compared with `pytest.approx` here (exactness is test 1's job).

  | id | graph | pos -> target | expected (cost; hops) | forces |
  |---|---|---|---|---|
  | T1 | FF2 | (49,28) -> (101,24) | 5.175; teleport RIGHT -> (66,28) @ (68,27); walk RIGHT 1375 -> (79,24) @ (79,24); teleport RIGHT -> (101,24) @ (98,24); walk RIGHT 375 -> (101,24) @ (101,24) | known case 1, overshoot onto a slope |
  | T2 | FF2, `ability_mix_band=0` | (101,24) -> (105,42) | 1.05; walk RIGHT 500 -> (105,42) @ (105,24); teleport DOWN -> (105,42) @ (105,42) | known case 2, launch x |
  | T3 | FF2 | (101,24) -> (105,42) | 1.05; walk RIGHT 500 -> (105,42) @ (105,24); (DROP, TP_DOWN) -> (105,42) @ (105,42) | coin flip `:175-176` |
  | T4 | FF2 | (104,43) -> (105,42) | arrived | P1 |
  | T5 | FF2 | (105,45) -> (105,42) | arrived | P1 on the node's platform |
  | T6 | `_synthetic([(10,10),(10,28)])` | (10,10) -> #1 | 0.55; teleport DOWN -> #1 @ (10,28) | single option `:172` |
  | T7 | same, `can_fall=True, ability_mix_band=0` | (10,10) -> #1 | 0.55; teleport DOWN -> #1 @ (10,28) | cheaper `:178` (teleport) |
  | T8 | FF2, `ability_mix_band=0, teleport_s=0.9` | (105,23) -> (105,42) | 1.65; drop -> (105,42) @ (105,42) | cheaper `:178` (drop) |
  | T9 | FF2 with `"(79, 24)"` -> `"(79, 22)"` | (77,25) -> (101,24) | 3.0; teleport RIGHT -> (101,24) @ (96,24); walk RIGHT 625 -> (101,24) @ (101,24) | walk after a slope start `:243-244` |
  | T10 | FF2 with `"(97, 43)"` -> `"(97, 45)"`, `"(105, 42)"` -> `"(105, 40)"`, `ability_mix_band=0` | (99,24) -> (105,40) | 1.05; walk RIGHT 750 -> (105,40) @ (105,24); teleport DOWN -> (105,40) @ (105,40) | split platform |
  | T11 | `_synthetic([(10,10),(25,10),(29,28),(45,28),(5,45),(20,45),(35,45),(50,45)], can_fall=True)` | (25,10) -> #2 | 7.8583333; teleport LEFT -> #4 @ (6,10); walk RIGHT 500 -> #4 @ (10,10); drop -> #4 @ (10,45); teleport RIGHT -> #3 @ (29,45); teleport RIGHT -> #3 @ (48,45); walk LEFT 375 -> #3 @ (45,45); teleport UP -> #3 @ (45,28); teleport LEFT -> #2 @ (26,28); walk RIGHT 375 -> #2 @ (29,28) | DOWN not offered `:152` |
  | T12 | `_synthetic([(10,60),(25,60),(29,50),(45,50),(6,38),(18,38),(30,38)])` | (25,60) -> #2 | 5.625; teleport LEFT -> #4 @ (6,60); walk RIGHT 500 -> #4 @ (10,60); teleport UP -> #4 @ (10,38); teleport RIGHT -> #2 @ (29,38); teleport DOWN -> #2 @ (29,50) | UP not offered `:152` |
  | T13 | FF2 | (86,37) -> (99,60) | Failure `off-graph: (86, 37)` | `:201` |
  | T14 | `_synthetic([(10,10),(100,10)])` | (10,10) -> #1 | Failure `no path from (10, 10) to (100, 10)` | `:213` |
  | T15 | `_synthetic([(10,10),(25,10),(29,28),(45,28)])` | (25,10) -> #2 | Failure `no path from (25, 10) to (29, 28)` | `:213` after `:152` |
  | T16 | FF2, target `Node("blue_node", (113, 60), False, False, False, False)` | (113,60) -> it | Failure `target (113, 60) is not in the route graph` | `:191` |
  | T17 | `_synthetic([(10,10),(25,10),(40,10),(29,28),(45,28)])` | (25,10) -> #3 | 1.05; walk RIGHT 500 -> #3 @ (29,10); teleport DOWN -> #3 @ (29,28) | launch case 1 (`:145-146`) |
  | T18 | `_synthetic([(10,28),(25,28),(20,10),(29,10)])` | (21,28) -> #3 | 1.55; walk RIGHT 500 -> #3 @ (25,28); teleport UP -> #3 @ (25,10); walk RIGHT 500 -> #3 @ (29,10) | launch case 2 (`:147-151`), UP landing off the node |
  | T19 | FF2 | (48,61) -> (58,46) | 1.8; teleport UP -> (48,45) @ (48,45); walk RIGHT 1250 -> (58,46) @ (58,46) | UP edge |
  | T20 | FF2, `max_walk_ms=1000` | (37,60) -> (48,61) | 1.375; walk RIGHT 1000 -> (48,61) @ (45,61); walk RIGHT 375 -> (48,61) @ (48,61) | capped walk |
  | T21 | FF2, `max_walk_ms=1000` | (66,28) -> (101,24) | 4.375; walk RIGHT 1000 -> (79,24) @ (74,26); walk RIGHT 625 -> (79,24) @ (79,24); teleport RIGHT -> (101,24) @ (98,24); walk RIGHT 375 -> (101,24) @ (101,24) | capped slope walk, rounded y |
  | T22 | FF2 | (72,26) -> (49,28) | 1.55; walk LEFT 750 -> (66,28) @ (66,28); teleport LEFT -> (49,28) @ (47,28) | on a slope segment, leftward |
  | T23 | FF2 | (82,60) -> (99,60) | 0.8; teleport RIGHT -> (99,60) @ (101,60) | overshoot within `arrive_px` ends it |
  | T24 | FF2 | (48,64) -> (58,46) | 1.8; teleport UP -> (48,45) @ (48,45); walk RIGHT 1250 -> (58,46) @ (58,46) | below a node, on its platform |
  | D1 | FF2 | (129,60) -> (97,43) | 3.475; teleport LEFT -> (97,43) @ (110,60); walk LEFT 625 -> (105,42) @ (105,60); teleport UP -> (105,42) @ (105,42); walk LEFT 1000 -> (97,43) @ (97,43) | hops that leave `path` (RV3-D5 A2): an exact cost tie, not a re-route (test 9) |

  Each hop's `from_pos` is `pos` for the first and the previous hop's landing after that; every non-Failure,
  non-arrived case is `complete=True`.

  1. **S3, first hop, exact.** For every case, `preview(rg, pos, target)` against `next_move(rg, pos, target,
     np.random.default_rng(0))`: both Failure with equal messages; or both arrived (`Decision(None, 0.0, None)`,
     `arrived True`); or `d.move in hops[0].options`, `hops[0].toward is d.toward`, `hops[0].cost_to_go_s ==
     d.cost_to_go_s` and `preview.cost_to_go_s == d.cost_to_go_s` (`==`, not approx). For T3's second hop and any
     two-option hop, `next_move` from that hop's `from_pos` over seeds 0..49 gives only the two options and both.
  2. **The table's literals:** each case's hops (options, `toward` by position, landing), `cost_to_go_s`
     (`pytest.approx`) and `complete`, as listed; arrived cases have `starts ()`, `start None`, `path ()`, `hops ()`.
  3. **The six branches are really taken** (spec S3: "at least one case forcing each of those six"): run
     `next_move` on every case's `pos` and on every hop's `from_pos` under `sys.settrace` (a tracer that records
     `(co_filename, f_lineno)` for "line" events in `planner.py`; ground `sys.settrace` and its "line" events in the
     Python 3.14 docs, hard rule 12); lines 152, 172, 178, 201, 213 and 243 of `planner.py` are all recorded. First
     assert the text of each of those lines (read `planner.py`): 152 `return None`, 172 `choice = next(iter(options))`,
     178 `choice = cheap`, 201 `return Failure(anchor_result.failure())`, 213 `return Failure(f"no path from ...`,
     243 `g_next = path[_walk_run_end(rg, path, gi + 1)]`, so a moved line fails loudly instead of tracing the wrong
     statement.
  4. **Starts.** T22's position (72,26): `starts` is `((66,28), 6/8 + c1), ((79,24), 7/8 + c2)` in that order, with
     `c1 = rg.cost_to(node(66,28), target)` and `c2` likewise (the slope approach is `|dx| / walk_px_per_s`,
     `planner.py:77`); `start` is (66,28). A node position (T1) has one start, cost `rg.cost_to(start, target)`.
     `path[0] is start` and `path[-1] is target` on every successful non-arrived case.
  5. **`max_hops`.** T1 with `max_hops=1`: one hop equal to T1's first, `complete False`; `max_hops=4`: equal to the
     full T1, `complete True`; `max_hops=0`: no hops, `complete False`, the other fields as the full preview's.
  6. **Pure and draw-free.** `preview` has no generator parameter; three calls on T3 give equal `Preview`s; the
     route graph's edge list with data (`list(rg.graph.edges(data=True))`) and its node list are equal before and
     after previewing every case (`_offered` makes a view, `planner.py:164`).
  7. **The coin flip's options:** T3's second hop is `(DROP, TP_DOWN)` in that order; T2's is `(TP_DOWN,)`; T7's only
     hop `(TP_DOWN,)`; T8's `(DROP,)`.
  8. **The landing rule, case by case** (each assertion names the V4 bullet it checks): a teleport that passes its aim
     (T1 hop 1: `68 = 49 + 19`, y from the slope segment (66,28)-(79,24) at x 68, `round(27.38) = 27`); a teleport past
     a platform end (T11 hop 1: `6 = 25 - 19`, y the end node (10,10)'s y); a capped walk (T20 hop 1: `45 = 37 +
     round(1000 * 8 / 1000)`); an UP landing off the node (T18 hop 2: `(25, 10)`, the surface of #3's platform at
     x 25); a landing on the aim node's x is the node's own position (T19 hop 1).
  **The S3b helpers** (`tests/route/preview_checks.py`, written with the tests; RV-5 reuses both, so they take only
  what any caller has):
  - `s3b_problems(rg, pv, target) -> list[str]`: one message per failed check (naming `pv.pos`, the target's
    position, k and what differs), `[]` when S3b holds for this preview. Checks: `pv.complete` is True; for every k
    from 1 to `len(pv.hops) - 1` (0-based), `d = next_move(rg, pv.hops[k-1].landing, target,
    np.random.default_rng(k)).unwrap()` has `d.move in pv.hops[k].options`, `d.toward is pv.hops[k].toward` and
    `d.cost_to_go_s == pv.hops[k].cost_to_go_s` (`==`); `sub = preview(rg, pv.hops[k-1].landing, target).unwrap()`
    has `sub.hops == pv.hops[k:]` and `sub.complete == pv.complete` (spec S3b's "the same remaining path" is the
    route the hops take: RV3-D5 A2); and at `pv.hops[-1].landing`, `next_move` returns `Decision(None, 0.0, None)`
    and `preview` is `arrived`. A Failure where a Success is expected is a message, never a raise.
  - `follows(path, hops) -> bool`: every hop's `toward` is in `path` (by identity), at a path index no lower than the
    hop before's. A measure, not S3b's pass/fail (RV3-D5 A2).
  9. **S3b on the table, exact (RV3-D5 A2).** For every non-Failure, non-arrived case, `s3b_problems(rg, pv, target)
     == []`. And `follows(pv.path, pv.hops)` is True for every such case except D1, where it is False and the test
     says why in a comment: D1 is an exact cost tie, not a re-route. From (129,60) both routes to (97,43) cost 3.475 s
     (via (99,60) and up at x 97, or via (105,42) and up at x 105), and from hop 1's landing (110,60) both cost
     2.175 s; `rg.path` breaks the tie one way from (129,60) and the other way from (105,60), so `path` goes via
     (99,60) while the hops go via (105,42) (RV3-D5 notes). The moves still agree at every landing, which
     `s3b_problems` pins.
  10. **FF2 sweep** (mark `slow`): for all 420 ordered pairs of distinct FF2 nodes at default `MovementParams`, from
      the first node's position to the second: test 1's first-hop agreement and `s3b_problems(...) == []` on every
      pair; and the set of `(pos, target position)` pairs where `follows` is False equals exactly these 9 (measured
      at planning time by two prototypes, RV3-D5 notes and RV3b2 notes):
      - `((129,60), (97,43))`: D1, the tie above;
      - `((105,42), (66,28))`, `((118,43), (66,28))`: at hop 2 the re-plan from the UP's launch x goes via (97,43),
        down to (99,60) and along the bottom (5.725 s against 5.925 s by the upper route);
      - `((109,23), (49,28))`, `((109,23), (42,28))`: the re-plan from (105,24) goes via (101,24) and the upper route
        (5.675 s against 6.475 s; 6.55 s against 7.35 s);
      - `((65,60), (90,24))`, `((65,60), (97,43))`, `((65,60), (101,24))`, `((65,60), (109,23))`: two teleports right
        overshoot the launch x 97; the landing (103,60) anchors on node (105,60) (`|dx| = 2 <= align_px`), so the
        re-plan goes up at x 105.
      A pair joining or leaving the set is a change in the preview's or the planner's behaviour: report it with the
      pair's hops, never update the set to make the test pass. Print the pair count and the run time.
- **Run:** `python -m pytest -q tests/route/test_route_preview.py tests/route/test_import_rules.py
  tests/route/test_planner.py`, then `lint-imports`; T5 with the S1 hash (this task adds a file under `src/route`).
- **Commit:** `feat(route): route preview of the planner's route and hops, with no random draw` (body: the `|` and
  `sys.settrace` grounding, the sweep's time, RV1-D4 A / H53, RV3-D4 A, RV3-D5 A2).
- **Review item:** the reviewer diffs `_plan` against `planner.py:190-247` by eye and confirms the only differences
  are what it returns and the coin flip (V4); checks the landing code against V4's rule text, bullet by bullet; and
  checks that the stub generators define `random()` only (V4's condition).

### Task RV-5: Preview agreement over the two 20-lap sim runs (S3, S3b), departures and the real-landing rate

**Files:** tests `tests/route/test_preview_sim.py` only. No source file changes unless a check fails (see "If a
check fails" below); then only `src/route/route_preview.py`, and RV-4's table gains the failing case as a row.
**Class:** O — tests only, but they join four earlier interfaces (RV-3's `--out` folder, V1's reader, V4's preview,
V5's comparison), and a failure needs judgment (fix the preview, or raise it). **Cloud:** yes (simulator only; no
Tk: `src.route_view.model` imports none, RV-6 test 13). **Depends on:** RV-3 (the `--out` folder with `steps.jsonl`
and `map.csv`), RV-4 (V4 and `tests/route/preview_checks.py`), RV-6 (V5's `route_changed` and `hop_route`), RV3-D5
(decided A2).
**Interfaces used:** V1 (`read_run`, `RunHeader.params`, the event fields), V4, V5 (`route_changed` and `hop_route`
only). Creates none.
**Comparison unit:** no. The O candidates are RV-3 and RV-6 ("Comparison units"); RV-5 needs three merged tasks and
its oracle is RV-4's, so a comparison would mostly measure RV-4 again.

- [ ] Read V1 (`RunHeader`, `StepEvent`, `read_run`), V4 (whole), V5's "Route changed" rule and the
  `route_changed` / `hop_route` lines, RV3-D5's ruling (Decisions), spec 5.3 (its last three paragraphs), S3 and S3b
  (spec section 7), RV-3's tests 3, 4 and 6 (how a `--out` folder and its `map.csv` rebuild are checked),
  `tests/route/preview_checks.py` (RV-4), `run_sim`'s signature (`sim/run.py`) and `walk.py:164-178` (how a graph is
  built from a map and params).
- [ ] Ground before relying on it (hard rule 12): that `json.dumps` writes a float with `repr` and `json.loads` reads
  it back to the same float (the Python 3.14 `json` docs or `Lib/json/encoder.py`), since tests 2 and 3 compare the
  logged `cost_to_go_s` and `walk_ms` with `==`. Cite it in the commit body.
- [ ] Module docstring of the test file: what it pins (S3 and S3b over both sim runs), what it only prints (the
  departures and the real-landing rate) and why (RV3-D5 A2; spec S3b: "reported, not required"). Mark the whole file
  `pytestmark = pytest.mark.slow` (two 20-lap runs with `out_dir` plus every sub-preview; at planning time a prototype
  took about 0.15 s per run and 0.25 s for its previews and sub-previews).
- [ ] **Fixture** `_runs` (module scope, `tmp_path_factory`), two runs from the FF2 fixtures
  (`tests/fixtures/fossil_foundry_2.csv`, `tests/fixtures/ff2_terrain.json`), laps 20, seed 1: `"plain"` with
  `out_dir=<tmp>/plain`, and `"offset"` with `noise=NoiseConfig(node_offset_px=2)` (as `tests/route/test_sim_run.py:81`)
  and `out_dir=<tmp>/offset`. Per run it keeps: the `SimReport`; `read_run(folder).unwrap()`; the route graph rebuilt
  from the folder alone, as the viewer will (V5 "Map and graph"; RV-3 test 6): `MapleAgent(characterClass=Kanna())`,
  `loadMap(str(folder / "map.csv"))` under `contextlib.redirect_stdout(io.StringIO())`,
  `RouteGraph.build(agent.pathGraph, MovementParams(**header.params)).unwrap()`; and `by_pos`, node position → node
  of that graph (assert the positions are distinct: the offset map moves nodes by up to 2 px). Rebuilding from the
  folder rather than reusing `run_sim`'s graph (the shifted run keeps its map only inside `run_sim`, plan choice 7)
  also covers the map copy.
  Helpers: `_steps(events, by_pos)` walks the events in order, keeps the current leg's target (`by_pos[leg "to"]`),
  and yields `(index, event, target)` for every `planned`, `no_plan` and `reached` event; `_logged_move(ev) -> Move`
  is `Move(MoveKind(ev.data["kind"]), Key[ev.data["dir"]] if ev.data["dir"] is not None else None,
  ev.data["walk_ms"])` (V2 logs `move.direction.name`; `Key` from `src.input.protocol`, an `IntFlag`, so `Key["RIGHT"]`
  is `Key.RIGHT`).
- [ ] **Tests** (`tests/route/test_preview_sim.py`; write them first). Tests 2 and 3 collect every mismatch into a
  list and assert it is empty with a message listing the first 10 (run, event index, pos, target, what differs), so
  one run shows every failing event:
  1. **Inputs.** For both runs: `run_sim` is a Success; `origin "steps"`, `ended True`, the last event `end` with
     reason `"max_laps"`; the number of `planned` events equals `report.moves`, which is 379 for the plain run (RV-3
     test 1, spec 7) and 356 for the offset run (measured at planning time; both are fixed by S1); every leg's `to`
     and every logged `toward` is a key of `by_pos`. Print per run the counts of `planned`, `no_plan` and `reached`
     events and of previews whose hop 1 has two options (coin-flip first moves). Measured at planning time: no
     `next_move` Failure in either run, so there is no `no_plan` event and S3's Failure branches rest on RV-4's
     T13-T16.
  2. **S3, exact, every event.** For each `planned` event with its leg's target: `pv = preview(rg, pos, target)` is
     a Success with `arrived False`; `pv.cost_to_go_s == ev.data["cost_to_go_s"]` and `pv.hops[0].cost_to_go_s ==`
     the same (`==`, no approx); `pv.hops[0].toward.minimap_position == ev.data["toward"]`; `_logged_move(ev) in
     pv.hops[0].options` (kind, direction and `walk_ms` exactly; for a coin-flip down the logged choice is one of the
     two options). For each `reached` event: `preview(rg, pos, target)` is `arrived`. For each `no_plan` event (none
     today; kept so a run with off-graph reads is covered): `preview` is a Failure whose message equals the logged
     `why`.
  3. **S3b, exact, every planned event (RV3-D5 A2, the pass/fail part).** For each `planned` event,
     `s3b_problems(rg, pv, target) == []` (`from preview_checks import s3b_problems, follows`): every later hop equals
     `next_move` at the landing it starts from, the preview from hop k-1's landing gives exactly `hops[k:]` and the
     same `complete`, every preview is complete and its last landing is arrived. Print per run the previews and the
     hops checked.
  4. **Departures from `path`, reported only (A2 part 2).** Per run, the number of previews where `follows(pv.path,
     pv.hops)` is False; print `hops leave the planned route at an assumed landing: <n> of <N> previews (<run>)`.
     Asserted only `0 <= n <= N`. Measured at planning time: 0 of 379 (plain) and 0 of 356 (offset). A non-zero
     count fails nothing; name its pairs in the commit body (the view shows such a departure as the hops' route,
     V5).
  5. **The real-landing agreement rate (S3b's report part, the comparison of spec 5.4's "route changed" line).**
     Pairs: V5's "Route changed" pairs, exactly (two `planned` events P then Q in the same leg, between one `leg` and
     its `leg_end`, with no other `planned`, no `no_plan` and no `reached` between them, and at least one `settled`
     event between them, a `settled` with pos None included). A pair agrees when `route_changed(pv_P, _logged_move(Q), Q.data["toward"], pv_Q)` is False, with
     `route_changed` imported from `src.route_view.model` (never a copy of the rule in the test, so the printed rate
     and the view's lines are one comparison). Print `real-landing agreement: <a> of <p> pairs (<run>)`. Asserted:
     `p > 0`, and every pair whose Q position equals `pv_P.hops[0].landing` (the character landed exactly where the
     preview assumed) agrees: by S3b this must hold, so a failure here is a bug in `route_changed`, `hop_route` or the
     pairing, not planner behaviour. Print how many such pairs there are (planning time: 120 plain, 18 offset, all
     agreeing). Also print `agreeing pairs whose walk length differs: <n> (<run>)`: agreeing pairs whose logged move
     `_logged_move(Q)` is not in `P.hops[1].options` (exact comparison, `walk_ms` included) though its `(kind,
     direction)` is (RV6 m6), i.e. re-plans that moved only the walk's end, which the view does not call a route
     change (RV3-D8 B). Expected output, not asserted (RV3b2's
     prototype measure with walk length ignored, notes/RV3b2.md): plain 204 of 239 pairs, offset 196 of 216. Of
     plain's 35 changes, 18 are a different kind or direction and 17 a one-hop P (an expected arrival that landed off
     it); offset's 20 are 9 + 10 + 1 whose cause the prototype did not separate (inferred: the hops' route). Agreeing
     pairs whose walk length differs: about 15 plain, 19 offset.
- [ ] **If a check fails** (tests 1-3, or test 5's landed-exactly part):
  - first confirm the rebuilt graph is the run's (RV-3 test 6 passes; for the offset run the rebuilt node positions
    equal the logged leg `to`s);
  - reproduce the failing `(pos, target)` on that graph as a new row of RV-4's table (same form, the expected
    literals taken from `next_move` at each landing, not from the preview) and fix `route_preview.py` until the new
    row and every earlier row and RV-4's sweep pass;
  - if the fix would change V4's landing rule, or the failure is the planner's correct behaviour that the hop rules
    do not model, stop and raise it (spec S3b: raised, not loosened). Never loosen a check, approximate a cost or
    skip an event.
- **Run:** `python -m pytest -q -s tests/route/test_preview_sim.py` (`-s` shows the printed counts), then
  `python -m pytest -q tests/route tests/route_view`; T5's S1 hash only if `route_preview.py` changed.
- **Commit:** `test(route): route preview agrees with the planner over both 20-lap sim runs (S3, S3b)` (body: the
  printed counts of tests 1, 3, 4 and 5 for both runs, the `json` float grounding, RV3-D5 A2, and any table row added
  and why).
- **Review item:** test 5 calls V5's `route_changed` (no copy of the rule), and tests 2 and 3 compare with `==` and
  `is` where the text says so, with no tolerance anywhere.

### Task RV-6: Pure view model, "why" lines, route_view contracts

**Files:** create `src/route_view/__init__.py` (a docstring only: what the package is, spec 5.8), create
`src/route_view/model.py` (V5); `pyproject.toml` (two new import-linter contracts); tests
`tests/route_view/test_view_model.py`, `tests/route_view/test_route_view_imports.py`.
**Class:** O — one pure module, but many small rules (V5) and the boundary contracts. **Cloud:** yes (no Tk, no
Windows call). **Depends on:** RV-1 (V1 reader), RV-4 (V4 preview); RV-3 for test 9's sim folder (if RV-3 has not
landed, build that folder with `StepLog` and a sim walker as RV-2's tests do, and say so in the commit body).
**Interface created:** V5 (used by RV-7, RV-8; RV-5 uses `route_changed` and `hop_route`). **Comparison unit (O, second candidate):** pure, golden text, hidden
tests can add any hand-written `steps.jsonl`.

- [ ] Read V5 (whole: the types and every rule), V1, V4, hard rules 5, 6, 9, plan choices 3, 5 and 8, spec 5.3 (the
  map-changed warning), 5.4 (the why-line table, the side panel), 5.5, 5.8 (the two contracts), `walk.py:164-178`
  (how the graph is built), `controller.py:90-111`, `csv_parser.py:14-30` (node order), `map_csv.py:53-99`
  (`read_editor_nodes`, its metadata), `locate.py:17-23` and `:84-94` (`Anchor`, `attach`), `protocol.py:54-67` and
  `:179-190` (`Key`, `Step`, `transitions`), `planner.py:22-39`, `params.py:10-26`, `pyproject.toml:46-75`.
- [ ] `pyproject.toml`: two forbidden contracts after "f route layers" (spec 5.8; names say what they do, not letters,
  spec 10):
  - `name = "route_view boundaries"`, `source_modules = ["src.route_view"]`, `forbidden_modules = ["src.gui",
    "src.main", "src.vision", "src.rune_solver", "src.input.input_layer", "src.input.link", "src.input.actions",
    "src.input.timing", "src.input.harness", "src.route.walker", "src.route.walk", "src.route.moves",
    "src.route.runtime", "src.route.sim"]` (every `src.input` module except `protocol`: `ls src/input` at
    `f807b0f` is `actions, harness, input_layer, link, protocol, timing`; re-list it and add any new one);
  - `name = "route_view is a leaf"`, `source_modules = ["src.route", "src.controller", "src.gui", "src.main",
    "src.vision", "src.input", "src.classes", "src.rune_solver"]`, `forbidden_modules = ["src.route_view"]`.
  Forbidden contracts check indirect imports too: ground that default (`allow_indirect_imports`) in the installed
  import-linter's docs or source and cite it (hard rule 12). Run `lint-imports` before writing any `src/route_view`
  code: both contracts must already be kept (an empty package).
- [ ] `src/route_view/model.py`: V5 exactly (names, fields, rules). Imports only what spec 5.8 allows; no Tk, no
  ctypes, no `subprocess` (the git head comes in as `current_git_head`). Module docstring: what the model is, and a
  "Where the plan is silent" list (each item one line). One item is given (RV4 m7/q14): "the planned why line's
  route is `preview.path` and the panel's Route line is the hops' route (`hop_route`); where the hops leave `path`
  (RV3-D5 A2's 9 FF2 pairs; none in the sim runs) the two differ, by design".
- [ ] **Tests** (`tests/route_view/test_view_model.py`; write them first). Helper `_folder(tmp_path, events,
  header=None)`: copies `tests/fixtures/fossil_foundry_2.csv` to `<tmp>/map.csv`, writes `steps.jsonl` with
  `step_log.header_line` and `event_line` (so the format is V1's), returns the folder. The default header: `source
  "walk"`, `clock "perf_counter_ns"`, `map "map.csv"`, `map_sha256` of the fixture's bytes, `class "kanna"`,
  `params dataclasses.asdict(MovementParams())`, `rotation` FF2's seven blue nodes in file order, `git_head "a" * 40`;
  `load_folder(folder, repo_root=<repo root>, current_git_head="a" * 40)`.

  **The hand-written run `G`** (FF2 labels: blue `#1` (113,60) `#2` (82,60) `#3` (48,61) `#4` (58,46) `#5` (49,28)
  `#6` (101,24) `#7` (105,42); orange `o1` (65,60) `o2` (99,60) `o3` (129,60) `o4` (48,45) `o5` (74,45) `o6` (42,28)
  `o7` (66,28) `o8` (79,24) `o9` (90,24) `o10` (109,23) `o11` (118,43) `o12` (97,43) `o13` (37,60) `o14` (105,60)).
  Times in seconds (write `t_ns = round(s * 1e9)`); keys as `(delta_100us, mask)`, each delta from the previous step
  (`protocol.transitions` sums them, `protocol.py:179-190`):

  | k | ev | t (s) | values |
  |---|---|---|---|
  | 0 | leg | 1.000 | lap 0, leg 5, from (49,28), to (101,24) |
  | 1 | planned | 1.100 | pos (49,28), teleport, RIGHT, walk_ms None, 5.175, toward (66,28) |
  | 2 | sent | 1.331 | "ok", 231.0, 228.4, ((0,2),(310,18),(810,2),(280,0)) |
  | 3 | settled | 1.900 | (68,27), False |
  | 4 | planned | 1.950 | (68,27), walk, RIGHT, 1375.0, 4.625, toward (79,24) |
  | 5 | sent | 2.160 | "ok", 210.0, 205.0, ((0,2),(13750,0)) |
  | 6 | settled | 3.600 | (80,24), False |
  | 7 | planned | 3.650 | (80,24), walk, RIGHT, 2625.0, 3.4, toward (101,24) |
  | 8 | sent | 3.850 | "ok", 195.0, 190.0, ((0,2),(26250,0)) |
  | 9 | settled | 5.900 | None, False |
  | 10, 11, 12 | read_failed | 6.000, 6.050, 6.100 | "miss" |
  | 13 | read_failed | 6.150 | "stale" |
  | 14 | no_plan | 6.200 | (86,37), "off-graph: (86, 37)" |
  | 15 | leg_end | 7.000 | "timed_out", 3, 1, 0 |
  | 16 | leg | 7.100 | lap 0, leg 6, from (101,24), to (105,42) |
  | 17 | planned | 7.200 | (101,24), walk, RIGHT, 500.0, 1.05, toward (105,42) |
  | 18 | sent | 7.400 | "ok", 200.0, 196.0, ((0,2),(5000,0)) |
  | 19 | settled | 7.950 | (105,24), False |
  | 20 | planned | 8.000 | (105,24), drop, None, None, 0.5, toward (105,42) |
  | 21 | sent | 8.100 | "ok", 150.0, 148.0, ((0,8),(300,40),(800,8),(250,0)) |
  | 22 | settled | 8.600 | (105,42), False |
  | 23 | reached | 8.650 | (105,42) |
  | 24 | leg_end | 8.650 | "reached", 0, 0, 0 |
  | 25 | end | 9.000 | "max_laps", 0 |

  Steps 1-6 are RV-4's T1 (a teleport run: the preview at (49,28) is T1's four hops, and by S3b the preview at its
  landing (68,27) is T1's hops 2-4); steps 16-22 are T3 (the coin-flip down as hop 2, then the logged drop); step
  14 is T13's off-graph position.

  **Golden text** (`render_text(model, model.step(k))`), one literal per k in {1, 3, 6, 7, 12, 14, 17, 20, 25},
  written in the test by hand from V5's rules. These lines are fixed by V5 and RV-4's table and must appear exactly:
  - k=1: `title: planned lap 0 leg 5 -> #6`; `warnings: none`; `target: #6`; `character: 49,28`; `aim: 68,27`;
    `route: 49,28 68,27 79,24 98,24 101,24`; `recent: none`; panel `Target  #6 (101, 24)`, `Position (49, 28), on
    #5`, `Route   you -> o7 -> o8 -> #6   5.2 s` (the hops' route, T1's towards (66,28) (79,24) (101,24), and the
    logged 5.175 s: V5, RV4 m2), `Hops`, `  1 teleport right   Right+E   SENT`, `  2 walk right 1.4 s   hold Right`, `  3 teleport right
    Right+E`, `  4 walk right 0.4 s   hold Right`; `why: 00:00.100 plan: teleport right toward o7 (aim x 68)`;
    `frame: none`.
  - k=3: `character: 68,27`, the same route and aim as k=1, `why: 00:00.900 settled (68, 27): on the aim; plan again
    from there`.
  - k=6: `aim: 79,24`, `recent: 49,28>68,27`, `why: 00:02.600 settled (80, 24): 1 px past the aim; plan again from
    there`.
  - k=7: the why lines of step 7 begin with `00:02.650 route changed: expected next teleport right toward #6, now
    walk right 2.6 s toward #6 (landed 1 px past)`; hop 1 is `  1 walk right 2.6 s   hold Right   SENT`.
  - k=12: `why: 00:05.100 no character on the minimap (miss) x3`; the run's why list holds that line once (steps 10,
    11 and 12 share it: `step(10).why_line == step(12).why_line`).
  - k=14: `character: 86,37`; `aim: none`; `route: none`; no hop lines; `why: 00:05.200 cannot plan
    from (86, 37): off-graph: (86, 37)`.
  - k=17: `title: planned lap 0 leg 6 -> #7`; `aim: 105,24`; `route: 101,24 105,24 105,42`; `  1 walk right 0.5 s
    hold Right   SENT`, `  2 drop or teleport down, 50/50   Down+Alt or Down+E`; `why: 00:06.200 plan: walk right 0.5
    s toward #7 (aim x 105)`.
  - k=20: `  1 drop   Down+Alt   SENT` and no hop 2; `route: 105,24 105,42`; no "route changed" line before step 20's
    lines (the drop is one of hop 2's options, toward the same node, and the hops' routes agree: `hop_route`, V5).
  - k=25: `title: ended lap 0 leg 6 -> #7`; `why: 00:08.000 run ended: max_laps (exit code 0)`.
  The parts that come from `rg.path` and `rg.cost_to` (the planned line's `start ... route ...`, the k=7 hops after
  hop 1) are written in after one run (the `Route` panel line does not: it comes from the hops and the logged cost,
  V5, so k=1's is the literal above), checked by hand against `preview()` (the test also
  asserts each against labels computed from `preview(...)` directly, so a wrong literal cannot hide), and never
  regenerated to make a later failure pass.
  1. **S5, golden:** the literals above, `==` per line.
  2. **Why lines of `G`, in full:** `model.why` equals a hand-written list for the whole run, including `00:00.331
     sent: Right down, E down +31 ms, E up +112 ms, Right up +140 ms (waited 228 ms)` (step 2: spec 5.4's example
     line), `00:04.900 did not settle within 2 s` (step 9), `00:05.150 no new frame yet` (13), `00:06.000 leg ended:
     timed_out (3 misses, 1 off-graph, 0 stalls)` (15), `00:07.100 sent: Down down, Alt down +30 ms, Alt up +110 ms,
     Down up +135 ms (waited 148 ms)` (21), `00:07.650 reached #7` (23). Each step's `why_line` points at its last
     line.
  3. **Nodes and targets** (S5 "blue nodes numbered 1..n in file order, the right target highlighted per leg"):
     `model.nodes` labels and positions are the FF2 list above, blue first; `step(k).target.label` is `#6` for k 0-15
     and `#7` for 16-25.
  4. **Incremental equals whole** (the live path): a model `open`ed on `G`'s header and `extend`ed in three slices
     (0-9, 10-11, 12-25) has the same `why` and the same `render_text` for every k as `load_folder`; in particular
     extending with step 12 after 10-11 replaces the collapsed line in place (`len(why)` unchanged).
  5. **Warnings** (Review Focus 2): `map.csv` with one byte changed → `warnings` starts with `WARN_MAP`, nodes still
     drawn; `current_git_head="b" * 40` → `WARN_CODE`; `git_head None` in the header or `current_git_head None` → no
     `WARN_CODE`; `map.csv` deleted → one warning starting `route not shown:` that names the file, `nodes ()`, every
     `step(k)` and `render_text` work, and the planned why lines read `at (49, 28): route not shown`; header `class
     "nobody"` → `route not shown: unknown class nobody`; header `params` with an extra key → `route not shown:` and
     the `TypeError` text. None of these raises.
  6. **Legs-only** (spec 5.5 row 3): a folder of `session.json` + `legs.jsonl` written as RV-1 test 7 does (two
     legs, one `LINK_DOWN`; the session's `map` the fixture's absolute path) → `load_folder` succeeds, `origin
     "legs"`, `rotation None` taken from the map, planned why lines built with `toward None` (the plan line names the
     preview's hop-1 `toward`), `settled` lines take the previous time, no sent lines; the `minimap_size` is
     `(162, 82)`.
  7. **Frames:** `read_frame_index` on `b'{"i": 0, "t_ns": 5, "file": "frame_000000.png", "w": 162, "h": 82}\r\n{"i":
     1, ...}\r\n{"i": 2, "t_'` → two frames, 0 bad, the torn tail ignored; a non-JSON line → counted in bad lines.
     With frames at t = 1.0, 1.5, 3.0 s, `step(1).frame` is the 1.0 s file, `step(6).frame` the 3.0 s one, and a
     step before the first frame has `frame None`.
  8. **Navigation:** on `G`: `seek(0, "move", 1) == 1`, `seek(1, "move", 1) == 4`, `seek(4, "leg", 1) == 16`,
     `seek(16, "leg", -1) == 0`, `seek(25, "event", 1) == 25`, `find_leg(0, 6) == 16`, `find_leg(3, 0) is None`,
     `play_delay_ms(1, 1.0) == 231`, `play_delay_ms(1, 4.0) == 58`, `play_delay_ms(25, 1.0) == 100`.
  9. **A sim folder** (S5 "built from a sim folder"; mark `slow`): a 1-lap `run_sim(..., out_dir=tmp)` (RV-3) →
     `load_folder` succeeds with no warnings; every `step(k)` renders; for every `planned` event hop 1 equals the
     logged move and `preview(...)`'s `hops[0].options` contains it (S3 already pins this; here it guards the model's
     wiring); every leg's target label is `#<i+1>` for its `to` = rotation[i].
  10. **`route_changed` unit cases:** with T1's preview as `P`: the logged move `walk RIGHT` toward (79,24) with `cur`
      = the preview at (68,27) → False; `teleport RIGHT` toward (79,24) → True (kind not among hop 2's options); `walk
      RIGHT` toward (66,28) → True (toward differs). With D1's preview at (129,60) as `P` and the logged move its
      hop 2 (`walk LEFT 625` toward (105,42)): `cur` the preview at its assumed landing (110,60) → False (its hops are
      `P`'s hops 2..4, although `P.path` goes the other way round the tie: RV3-D5 A2 compares the hops' routes, not
      `path`); `cur` the preview at (129,60) itself (hops' route (97,43) (105,42) (97,43), not `P`'s remaining
      (105,42) (97,43)) → True (the hops' route differs). `hop_route` of D1's hops is `((97,43), (105,42), (97,43))`
      and of T1's `((66,28), (79,24), (101,24))`. Walk length is not compared (RV3-D8 B), with T1's preview as `P`
      and `cur=None`: `Move(WALK, RIGHT, P.hops[1].options[0].walk_ms - 100)` toward (79,24) → False (walk length
      only); `Move(WALK, LEFT, P.hops[1].options[0].walk_ms)` toward (79,24) → True (direction).
  11. **Review Focus 5** (mark `slow`): `G`'s events 0-24 repeated 2,000 times (50,000 events, times shifted by 10 s a
      copy, then `end`) → `load_folder` within 3 s on the dev PC and the preview cache holds at most 10 entries (the
      positions repeat); 100 `step(k)` calls at spread k within 0.5 s. Print both times.
- [ ] **Tests** (`tests/route_view/test_route_view_imports.py`):
  12. `pyproject.toml` has both contracts with exactly the module lists above (parse with `tomllib`).
  13. Importing `src.route_view.model` in a `python -I -c` subprocess (repo root on `sys.path`) does not import
      `tkinter`, `src.input.input_layer`, `src.route.walker` or `src.gui` (check `sys.modules`; mark `slow`).
- **Run:** `python -m pytest -q tests/route_view tests/route/test_import_rules.py`, then `lint-imports`.
- **Commit:** `feat(route-view): view model, why lines and the route_view import contracts` (body: the
  `allow_indirect_imports` grounding, the timing test's numbers).

### Task RV-7: Tk window and replay controls

**Files:** create `src/route_view/window.py`, create `src/route_view/__main__.py`; tests
`tests/route_view/test_view_window.py`, `tests/route_view/test_view_cli.py`.
**Class:** O — a Tk window over V5 with several design points (background precedence, incremental why log,
replay controls). **Cloud:** no (Tk needs a display; the tests skip without one). **Depends on:** RV-6; RV-3 for the
sim folder of test 1.
**Interfaces created:** `RouteViewWindow` (below; used by RV-8); the V6 CLI's replay half.

- [ ] Read V5, V6, plan choices 9 and 10, hard rules 6, 9 and 12, spec 5.4 (whole), 5.5, 5.7 (the "never calls"
  rule), `map_editor.py:25-29`, `:40`, `:1024-1056` (radius, label colour, node colours, the yellow ring) and
  `:1069-1087` (nearest scaling; its lock misuse is not copied, spec 3.5), `tests/controller/test_gui_startup.py`
  (the withdrawn-root pattern, `_open_window`'s retry, `after` recorded by monkeypatch), `walk.py:181-187`
  (`_git_head`).
- [ ] `window.py`:
  ```python
  # Copied, not imported (src.gui is forbidden, spec 5.8): map_editor.py:28, :40, :1029-1030, :1050-1056
  NODE_R = 3; ORANGE_R = 2; RING_R = 5; LABEL_FG = "white"
  BLUE_FILL, BLUE_LINE = "lightblue", "blue"; ORANGE_FILL, ORANGE_LINE = "orange", "darkorange"; RING = "yellow"
  ROUTE, CHARACTER, AIM, RECENT, DARK = "cyan", "red", "magenta", "gray60", "#202020"
  class RouteViewWindow:
      def __init__(self, root: tk.Tk, folder: Path, model: RunModel | None, *, live: bool) -> None
      def set_model(self, model: RunModel) -> None       # RV-8: the live header arrived
      def show(self, k: int, *, background: str | None = None) -> None
          # draws step k; `background` (live, RV-8) is a frames/ file to use instead of the step's own frame
      def sync_why(self) -> None        # inserts only model.why lines it has not shown; rewrites its last line if the
                                        # model collapsed a repeat into it (compares the text); never rebuilds the log
      def set_scale(self, s: int) -> None                # 2, 3 or 4 (SCALES)
      def set_live(self, live: bool) -> None             # live: replay bar hidden (spec 5.4 "Replay only"), newest step
      def set_mode(self, text: str) -> None              # the title's mode part
      def set_status(self, text: str) -> None
      def add_warning(self, text: str) -> None           # RV-8's fallback line
      shown_background: str                              # "frames/<file>" | "minimap.png" | "dark" (tests read it)
      index: int | None
  ```
  Layout (spec 5.4): the red warning label (hidden when there are none) above; the canvas left; the side panel
  (`tk.Label`, `justify=left`, `TkFixedFont`) right, its text `"\n".join(view.panel)`; the why log (`tk.Text`,
  read-only, scrollbar, a `current` tag on `why_line + 1`, `see()` it) below; the replay bar (buttons `|<` `<` `>`
  `>|`, a unit menu "event"/"move"/"leg", a horizontal `tk.Scale` 0..n-1, play buttons `1x` and `4x` (press again
  to stop), `lap` and `leg` entries that jump with Enter through `find_leg`, and the `2x` `3x` `4x` scale buttons)
  under it; a status line at the bottom. Title: `Route view · <mode> · <folder> · lap L · leg K -> <label> ·
  <state>`; mode `REPLAY` for an ended run's replay ("ended: <reason>" when `model.ended`), `REPLAY (from
  legs.jsonl)` for `origin "legs"`, `REPLAY (did not end)` for a folder without `end` (RV-7 opens every folder this
  way; from RV-8 on, only with `--replay`, RV4-D1 A), `LIVE` while live.
  **0 events** (a header-only `steps.jsonl`: a walk killed in the countdown or before its first `leg`): the window
  shows the background and the nodes, no character, aim, route or hops; the title state is `no steps`; the slider
  and the step buttons do nothing; the status reads `no steps logged`; `index` is None.
  Drawing per `show`: the background (below), scaled with `Image.resize((w * s, h * s), Image.Resampling.NEAREST)`
  then `ImageTk.PhotoImage` (keep a reference); nodes with tag `node` (blue radius `NODE_R * s`, orange `ORANGE_R *
  s`) and blue labels with tag `label` (text the NodeMark label, `LABEL_FG`, left of nothing: at `x + r + 2` as the
  editor); the target ring (`target`); the route polyline (`route`) and each hop's number at its landing
  (`hopnum`); recent moves dashed (`recent`); the aim as an x (`aim`); the character as a filled dot (`character`).
  Canvas coordinates are `pos * s` (as `map_editor.py:1027`).
  **Background**, best first (spec 5.5): `background` if given, else `view.frame` → `folder/frames/<file>`; else
  `folder/minimap.png`; else a `DARK` canvas of `model.minimap_size`; else of the bounding box of the nodes and
  logged positions plus 10 px (at least 40 x 40). Each image is read with `PIL.Image.open` + `.load()` +
  `.convert("RGB")`, catching `OSError` and `ValueError` (ground which errors Pillow 12.2.0 raises for a truncated
  PNG, hard rule 12): on failure keep the last good picture (or the dark canvas if there is none), set the status
  `cannot read <file>: <error>; showing the last good picture` and try that file again at the next `show`
  (Review Focus 4). Decode only the one image `show` needs.
  The window never calls `focus_force`, `focus_set`, `lift`, `tkraise`, `grab_set`, `attributes("-topmost", ...)`,
  and opens no `messagebox` or dialog (spec 5.7); errors go to the status line. Nor does any `src/route_view` code
  call `resizable`, `overrideredirect`, `transient` or `attributes` (any option), in either form (`wm_resizable`
  ...) or raw (`tk.call("wm", ...)`): once the first idle pass has made Tk's wrapper window, each of these makes Tk
  build a new wrapper and destroy the old one (Tk 8.6.15 `win/tkWinWm.c`: `WmResizableCmd` :5073-5076,
  `WmOverrideredirectCmd` :4847-4848, `WmTransientCmd` :5611-5617, `WmAttributesCmd` :3246-3254, all reaching
  `UpdateWrapper`, which creates the new one at :2123 and destroys the old at :2225), which silently drops RV-8's
  no-activate style (RV8 n1, measured: `resizable(True, True)` then `update()` gave a new `wm frame` without
  `WS_EX_NOACTIVATE`). `title`, `geometry`, `minsize`, `maxsize`, resizing the canvas and packing widgets keep the
  wrapper (measured, RV8). None of the four is needed here (the window keeps Tk's default resizing).
- [ ] `__main__.py` (V6's CLI; RV-8 adds following and `--ready`):
  ```python
  # first two statements after the docstring:  import time;  _T0 = time.perf_counter()
  def main(argv: list[str] | None = None, *, make_root=tk.Tk, run_loop: bool = True) -> int
  ```
  `--follow FOLDER` (required), `--ready PATH` (parsed; help "write PATH once the window is up (walk --view
  passes it)"; RV-8 implements it) and `--replay` (`store_true`; help "open as a replay even if the run has no end
  line (a walk that crashed or was killed); not with --ready"; a no-op in RV-7, which replays every folder; RV-8
  implements it, RV4-D1 A). The parser's description ends "Never open a replay while a walk runs: clicking it takes
  focus from the game." (RV4 m5). Steps: `root = make_root()` (a `tk.TclError` → stderr `error: cannot open a
  window: <err>`, return 2), `root.withdraw()` at once; `current_git_head` from a copy of `walk.py:181-187`
  (`git rev-parse HEAD` in the repo root, timeout 10 s, None on any error); `load_folder` (a Failure → stderr
  `error: <message>`, `root.destroy()`, return 2); build the window, show the last step; print `window built in
  <time.perf_counter() - _T0:.2f> s` (flush; L4 reads it from `view.log`); `root.deiconify()`; `mainloop()` when
  `run_loop`; return 0. Ends with `if __name__ == "__main__": raise SystemExit(main())`. Ground `subprocess.run`'s
  `timeout` and `tk.TclError` on a failed root in the venv's docs or source (hard rule 12).
- [ ] **Tests** (`tests/route_view/test_view_window.py`; a real Tk root, withdrawn, no mainloop; `_open_root()`
  retries `tk.Tk()` four times 0.3 s apart and skips on `tk.TclError`, as `test_gui_startup.py:35-44`; `after`
  replaced by a recorder; every root destroyed at teardown):
  1. **S6, sim folder** (mark `slow`): a 1-lap `run_sim(..., out_dir=tmp)` folder → `load_folder` → a window;
     `show(k)` for k = 0, the first `planned`, the first `settled`, a step whose plan has a two-option hop if the lap
     has one, and the last step: no exception; `find_withtag("node")` has 21 items, `"label"` 7 with texts `#1` ..
     `#7` in rotation order; one `target` item; `route` items exactly when `view.route` is non-empty; the side panel
     text is `"\n".join(view.panel)`; the why log has `len(model.why)` lines and the `current` tag is on line
     `why_line + 1`; title starts `Route view · ended: max_laps`.
  2. **S6, legs-only folder** (RV-6 test 6's folder): opens; title has `REPLAY (from legs.jsonl)`; canvas `width` and
     `height` are 648 and 328 (FF2 `162,82` at 4x, a dark canvas: `shown_background == "dark"`); every k shows.
  3. **Scale** (H51): default 4; `set_scale(2)` (and the `2x` button's `invoke()`) → canvas 324 x 164 and the first
     node's `coords` halve; `3x` → 486 x 246.
  4. **Background precedence:** `G` (RV-6) plus `frames/` with three PNGs (written with Pillow, three colours) and an
     `index.jsonl` with `\r\n` lines at t = 1.0, 1.5, 3.0 s → `show(1)` gives `shown_background ==
     "frames/frame_000000.png"`, `show(6)` the third; without `frames/` but with `minimap.png` → `"minimap.png"`;
     with neither → `"dark"`. `show(1, background="frame_000002.png")` uses that file.
  5. **Review Focus 4:** `minimap.png` holding the first half of a valid PNG → `show` does not raise, background
     `"dark"`, the status names the file; the file then completed → the next `show` draws it. A good frame, then a
     corrupt newer one → the canvas keeps the same `PhotoImage` object as before and the status names the file.
  6. **Why log is incremental:** after `sync_why`, extending the model by one event and calling `sync_why` again
     calls the Text's `insert` once (spy) and does not `delete("1.0", "end")`; extending with a repeat `read_failed`
     rewrites only the last line.
  7. **Replay controls:** on `G`: `>` → index 1; unit "move" then `>` → 4; unit "leg" then `>` → 16; `>|` → 25; `|<`
     → 0; the slider's command with "5" → 5; lap 0 / leg 6 + Enter → 16; `4x` → `after` recorded with
     `model.play_delay_ms(k, 4.0)`; calling the recorded callback advances one step and schedules the next; at the
     last step nothing more is scheduled; `4x` pressed again while playing stops (the next recorded callback does
     nothing).
  8. **`set_live(True)`** hides the replay bar (`winfo_manager() == ""` for its frame) and `set_live(False)` shows it.
  9. **Warnings:** a model with `WARN_MAP` → the warning label's text is the warnings joined by `\n` and it is packed;
     with none it is not.
  10. **Never takes focus** (spec 5.7): an AST walk over every `src/route_view/*.py` finds no attribute access
      `focus_force`, `focus_set`, `lift`, `tkraise`, `grab_set`, no string constant `-topmost`, and no import of
      `tkinter.messagebox`, `tkinter.filedialog` or `tkinter.simpledialog`. It also finds no call whose function is
      an attribute named `resizable`, `wm_resizable`, `overrideredirect`, `wm_overrideredirect`, `transient`,
      `wm_transient`, `attributes` or `wm_attributes`, and no call with the string constant `"wm"` as its first
      argument (the raw `tk.call("wm", ...)` form) (RV8 n1: after the idle pass these rebuild the wrapper and drop the
      no-activate style). A planted sample (a string of source with `root.wm_resizable(True, True)` parsed by the
      same walker) is found, so the walk is shown to work.
- [ ] **Tests** (`tests/route_view/test_view_cli.py`):
  11. `main(["--follow", str(empty_folder)], make_root=<recorder root>, run_loop=False)` → 2, stderr starts
      `error:` and names the folder; no window shown.
  12. `main(["--follow", str(G)], run_loop=False)` on a real withdrawn root (patched `deiconify` recorder) → 0,
      stdout has one line matching `window built in \d+\.\d\d s`, `deiconify` called once, title starts `Route view ·
      ended: max_laps`.
  13. `python -m src.route_view --help` in a subprocess from the repo root → exit 0; the text names `--follow`,
      `--ready` and `--replay`, and `" ".join(text.split())` has `Never open a replay while a walk runs: clicking it
      takes focus from the game.` (whitespace joined first: argparse fills the description with `textwrap.fill` at
      the subprocess's 80 columns, so the phrase may wrap, RV6 m3) (mark `slow`).
  14. **0 events** (RV4-D1 A; in `test_view_window.py`): RV-6's `_folder(tmp_path, [])` (FF2's `map.csv`, hence the 21 nodes
      below; `steps.jsonl` a header only: no events, no `end`; RV6 m7) → the window opens without an exception, the title has `REPLAY (did not end)` and `no steps`, the status
      is `no steps logged`, `index is None`, 21 `node` items are drawn, and `>`, `>|` and the slider's command with
      "0" change nothing (`index` stays None, no exception).
- **Run:** `python -m pytest -q tests/route_view`, then `lint-imports`.
- **Commit:** `feat(route-view): the route view window and replay` (body: the Pillow and Tk grounding).
- **Review item:** the reviewer opens `python -m src.route_view --follow <a sim --out folder>` once by hand on the dev
  PC (not a test: it shows a window) and compares it with spec 5.4's sketch; screenshots are not committed.

### Task RV-8: Live follower, no-activate style, viewer-side claim and ready

**Files:** create `src/route_view/follow.py`, create `src/route_view/win_focus.py`, edit `src/route_view/__main__.py`;
tests `tests/route_view/test_view_follow.py`, `tests/route_view/test_win_focus.py`. `window.py` does not change (RV-7
gave it `set_model`, `show(background=)`, `sync_why`, `set_live`, `set_mode`, `add_warning`); if it must, that is a
fourth code file: say why in the commit body.
**Class:** S — a poll loop over files another process is writing (torn lines, half-written PNGs), the focus-safety
rules of spec 5.7 and the claim race of spec 5.9 step 3. **Cloud:** no (Win32, Tk). **Depends on:** RV-7.
**Interfaces created:** the V6 CLI in full (`--ready`, live following); used by RV-9 (the walk starts it), RV-10.

- [ ] Read V1 (`StepTail`), V5, V6, plan choices 9 and 10, hard rules 5, 6 and 12, spec 5.4 ("Live mode follows the
  folder ..." to the end of that paragraph), 5.7 (whole), 5.9 steps 2-3, the RV-7 code (`window.py`, `__main__.py`),
  and V1's one-run-per-file rule (a second `run` line, reachable only by hand, is a bad line: `StepTail` skips and
  counts it, never raises).
- [ ] `follow.py`:
  ```python
  POLL_MS = 100; SILENT_S = 10.0                              # spec 5.4
  @dataclass(frozen=True)
  class Polled:
      header: RunHeader | None          # the file's first header, on the poll that read it
      events: list[StepEvent]
      frames: list[tuple[int, str]]     # new complete index lines (read_frame_index on the new bytes, V5)
      bad_lines: int                    # StepTail.bad_lines (a `run` line after line 1 is one, V1) + frame-index
                                        # bad lines, totals so far
  class Follower:
      def __init__(self, folder: Path) -> None   # StepTail(folder/STEPS_FILE); frames/index.jsonl read from a byte
                                                 # offset, complete lines only, like StepTail
      def poll(self) -> Polled                   # never raises; a missing file gives nothing
  class LiveController:
      def __init__(self, root, window: RouteViewWindow, folder: Path, *, repo_root: Path,
                   current_git_head: str | None, focus=win_focus, clock=time.monotonic,
                   hidden: bool, styled_hwnd: int | None) -> None
      def tick(self) -> None
  ```
  `tick` (one poll; scheduled with `root.after(POLL_MS, self.tick)`): poll; on the first header `RunModel.open` +
  `window.set_model`; `model.extend(events)`, `model.add_frames(frames)`; redraw only when something changed (new
  events, new frames, a background retry pending, a mode change): `window.sync_why()` and `window.show(last,
  background=<newest frame file>)`, so per poll only the newest frame is decoded however many index lines arrived
  (spec 5.4); a decode failure keeps the last good picture and marks a retry for the next tick. No new `steps.jsonl`
  line for `SILENT_S` (by `clock`) and no `end` → mode `LIVE · no new steps for 10 s: the walk is still running or
  has crashed` (spec 5.4) and the status `if the walk has ended, open this folder with --replay to step through it`
  (RV4-D1 A; the title's words are fixed by spec 5.4, so the hint goes in the status line); back to `LIVE`, and the
  hint cleared, on the next line; it keeps following. On `end` (spec 5.4, 5.7): stop
  scheduling; if `styled_hwnd`: `focus.set_live_style(styled_hwnd, False)`, then `root.withdraw()` and
  `root.deiconify()` so the taskbar sees the change (spec 5.7: part of N1); if `hidden`: only `root.deiconify()`
  (never styled); then `window.set_live(False)`, mode `ended: <reason>`. `bad_lines` growing →
  one status line (`<n> bad lines skipped`). Any exception inside `tick` → status `follow error: <type>: <message>`, printed once to
  stdout (it goes to `view.log`), and the next tick is still scheduled (hard rule 5).
- [ ] `win_focus.py` (spec 5.7; every `ctypes` use inside a function, hard rule 6):
  ```python
  GWL_EXSTYLE = -20; WS_EX_NOACTIVATE = 0x08000000; WS_EX_APPWINDOW = 0x00040000; SW_SHOWNOACTIVATE = 4
  SWP_NOSIZE, SWP_NOMOVE, SWP_NOZORDER, SWP_NOACTIVATE, SWP_FRAMECHANGED = 0x1, 0x2, 0x4, 0x10, 0x20
  FIRST_SHOW = "noactivate"      # spec 5.7's fallback is "deiconify": the one-line change if N1's first-show part fails
  def wrapper_hwnd(root) -> Result[int, str]          # int(root.wm_frame(), 0): the `wm frame` HWND (spec 5.7);
                                                      # Failure("no wrapper yet") when it equals root.winfo_id()
  def set_live_style(hwnd: int, live: bool) -> Result[None, str]
      # live: ex-style | NOACTIVATE, & ~APPWINDOW; else & ~NOACTIVATE | APPWINDOW; then SetWindowPos(hwnd, 0, 0, 0, 0,
      # 0, NOSIZE | NOMOVE | NOZORDER | NOACTIVATE | FRAMECHANGED)
  def show_first(root, hwnd: int) -> Result[None, str]   # FIRST_SHOW "noactivate": ShowWindow(hwnd, SW_SHOWNOACTIVATE);
                                                         # "deiconify": root.deiconify()
  ```
  **The wrapper exists only after an idle pass (RV6 I1).** On a withdrawn root, before Tk's first idle pass, `wm
  frame` returns the root's own window, not a wrapper. Tk's `wm` manual (https://www.tcl-lang.org/man/tcl8.6/TkCmd/wm.htm,
  `wm frame`): "If window has not been reparented by the window manager then the command returns the platform
  specific window identifier for window."
  Measured in the venv (Python 3.14.0, Tk 8.6.15; RV6 and again in RV7, notes/RV7.md) on `tk.Tk()` + `withdraw()`
  + a packed Label and Canvas: before `update_idletasks()`, `wm_frame` == `winfo_id` and `GetParent(winfo_id)` == 0
  (ex-style 0x4); after it, `wm_frame` == `GetParent(winfo_id)` (a different HWND, ex-style 0x100) and `state()` is
  still `withdrawn`. Styling and `ShowWindow`-ing the inner window would leave the wrapper, the window Windows
  activates, untouched. Hence two rules, both in RV-8: `wrapper_hwnd` refuses (`Failure("no wrapper yet")`) instead
  of returning the inner window, and `__main__` runs `root.update_idletasks()` once before the claim. A failing
  test 10 is fixed in `__main__`/`wrapper_hwnd`, never by adding an idle pass to the test alone; and in N1 a
  `no wrapper yet` is an RV-8 bug, not a reason for `FIRST_SHOW = "deiconify"` (H-1's mapping).
  Each returns `Failure("not Windows")` when `sys.platform != "win32"` before touching `ctypes`, and a `Failure`
  with `ctypes.get_last_error()` when a call fails. What "fails" means, per call (RV4 I1, from Microsoft's
  "Return value" sections):
  - `ShowWindow`: its return is the window's previous visibility, never an error ("If the window was previously
    visible, the return value is nonzero. If the window was previously hidden, the return value is zero.",
    https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-showwindow). The viewer's root is
    withdrawn, so the first show returns 0: `show_first` is `Success` once the call returns, whatever it returns.
    (Reading 0 as failure would send every first show to `root.deiconify()`, an activating show, and push H-1 onto
    the wrong fallback.)
  - `GetWindowLongPtrW` and `SetWindowLongPtrW`: `ctypes.set_last_error(0)`, call, then `Failure` only when the
    result is 0 and `ctypes.get_last_error() != 0` ("If the previous value is zero and the function succeeds, the
    return value is zero, but the function does not clear the last error information. To determine success or
    failure, clear the last error information by calling SetLastError with 0, then call SetWindowLongPtr. Function
    failure will be indicated by a return value of zero and a GetLastError result that is nonzero.",
    https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setwindowlongptrw; GetWindowLongPtrW's
    page: "If the function fails, the return value is zero", and an ex-style can legitimately be 0). With
    `use_last_error=True`, ctypes swaps its private copy into the system LastError before the call and back after
    ("use_last_error does the same for the Windows error code", Python 3.14 `ctypes` docs, `CFUNCTYPE`), so
    `set_last_error(0)` is `SetLastError(0)` for that call; checked in the venv (RV5 notes: after
    `set_last_error(5)` a succeeding `GetDesktopWindow` leaves 5; after `set_last_error(0)` it leaves 0;
    `GetWindowLongPtrW(NULL, -20)` gives 0 and error 1400).
  - `SetWindowPos`: 0 is failure ("If the function fails, the return value is zero.",
    https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setwindowpos).
  Use `ctypes.WinDLL("user32", use_last_error=True)` with `argtypes`/`restype` set, and
  `GetWindowLongPtrW`/`SetWindowLongPtrW`. Ground each Win32 call and constant in
  Microsoft's docs (the `SetWindowLongPtrW` page's note on `SetWindowPos` with `SWP_FRAMECHANGED`, the
  extended-styles page, `ShowWindow`'s `SW_SHOWNOACTIVATE`) and `wm frame` in Tk's `wm` manual, with URLs in the
  commit body (hard rule 12).
- [ ] `__main__.py`, `main(argv=None, *, make_root=tk.Tk, run_loop=True, focus=win_focus, clock=time.monotonic)`
  (spec 5.9 step 3; V6; plan choices 9 and 10). First, before any window is built: `--replay` with `--ready` →
  stderr `error: --replay cannot be used with --ready`, return 2 (no root made, no claim). After `root.withdraw()`
  and the git head:
  - `--replay` (RV4-D1 A) → RV-7's replay path whatever the folder holds: plain `root.deiconify()`, no claim, no
    style, no `Follower`; a folder with no `end` gets `REPLAY (did not end)` (0 events: RV-7's 0-event rule);
  - no `steps.jsonl` → RV-7's replay path (legs-only or a Failure), whatever `--ready` says;
  - else build the window with no model, a `Follower`, and poll once synchronously (the walk wrote the header
    before starting the viewer, spec 5.9 step 1); `end` already read → replay: plain `root.deiconify()`, no claim,
    no style, `--ready` ignored (plan choice 9);
  - else live. `root.update_idletasks()` once (the idle pass that makes Tk create the wrapper `wrapper_hwnd` needs,
    RV6 I1 above; the root stays withdrawn), then print `window built in <s> s`. With `--ready`:
    `open(folder / VIEW_CLAIM, "x").close()`;
    `FileExistsError` (or any `OSError`: the safe side) → `hidden`: stay withdrawn, follow, show only at `end`. If
    claimed, or without `--ready`: `wrapper_hwnd` → `set_live_style(hwnd, True)` → `show_first(root, hwnd)` (style
    before the first show, spec 5.7); if any of the three fails, print `focus call failed: <error>` (flush; RV-10
    copies it into `n1.json`), `root.deiconify()`, and
    `window.add_warning("clicking here takes focus from the game")` (RV1-D6's fallback line). Right after the first
    show (either way) print `first show: root.state() = <root.state()>` (flush; it goes to `view.log`, and RV-10
    copies it into `n1.json`: a raw `ShowWindow` behind Tk's back may leave Tk believing the root is withdrawn,
    RV4 I3). Then, with `--ready`
    and the claim: write `str(os.getpid())` to `<ready>.tmp` and `os.replace` it onto the `--ready` path (an
    `OSError` is printed; the walk then waits its bounded time, spec 5.9). Then the `LiveController`'s first `tick`
    and `mainloop()` when `run_loop`.
  Nothing between the claim and `show_first` but the two focus calls (spec 5.9 step 3: "adjacent calls"); the idle
  pass therefore goes before the claim, never between the claim and the show.
- [ ] **Tests** (`tests/route_view/test_view_follow.py`; real withdrawn roots as RV-7; `after` recorded; a
  `FakeFocus` with the three functions that records `(name, args)` and returns `Success`, `wrapper_hwnd` giving
  `1234`; a `FakeClock`):
  1. **S7:** a writer thread appends `G`'s lines (RV-6) to `steps.jsonl`, each line in two writes 20 ms apart, and
     between steps adds a PNG (written to a temporary name, then `os.replace`) and its index line (also in two
     halves); the main thread calls `tick()` every 10 ms until the writer is done. The writer publishes, under a lock,
     how many lines it has completed; around each tick, `completed_before <= len(model.events) <=
     completed_after`, every event equals the line it came from (never a half line), `window.index ==
     len(model.events) - 1`, and `window.shown_background` is the newest index line's file. At the end: all 26
     events, `ended`, replay bar shown, mode `ended: max_laps`.
  2. **Newest frame only:** three PNGs and index lines added between two ticks → the window's image loader (spy) is
     called once in that tick, with the third file.
  3. **Review Focus 4:** the newest PNG is half written when a tick runs (the index line points at it) → the last
     good picture stays; the next tick, with no new line in either file, retries the same file; once the file is
     whole it is shown.
  4. **Review Focus 1:** `steps.jsonl` = header + 5 events + a torn sixth line, no `end` → after the first tick 5
     events, `LIVE`; `FakeClock` + 10.0 s with no new line → the title contains `no new steps for 10 s: the walk is
     still running or has crashed` and the status names `--replay` (`if the walk has ended, open this folder with
     --replay to step through it`); the sixth line completed → `LIVE` again, 6 events, and the status no longer names
     `--replay`.
  5. **Review Focus 5:** a model already holding RV-6 test 11's 50,000 events; one more line → `tick` takes under
     50 ms (median of 5) and the why Text's `insert` is called once (mark `slow`).
  6. **End:** a live, styled controller (`styled_hwnd=1234`) reads `end` → `FakeFocus` has exactly one
     `set_live_style(1234, False)`, `withdraw` then `deiconify` recorded on the root, `set_live(False)`, the title has
     `ended: max_laps`, and no further tick is scheduled. A `hidden` controller → `deiconify` only, no
     `set_live_style` call.
  7. A second `run` line → skipped as a bad line (V1): no model rebuild, `bad_lines == 1`, one status line; another
     bad line (not JSON) → `bad_lines == 2`, the status line updated; neither raises.
  8. `model.extend` patched to raise `RuntimeError("x")` → `tick` does not raise, the status has `follow error:
     RuntimeError: x`, the next tick is scheduled.
  9. **Claim and ready** (`main(..., run_loop=False, focus=FakeFocus, make_root=<withdrawn root with recorded
     deiconify and update_idletasks>)`):
     a. live folder, `--ready <f>/view.ready`, no claim → `update_idletasks` recorded once, before `view.claim`
        exists and before any `FakeFocus` call (RV6 I1); calls in order `wrapper_hwnd`, `set_live_style(1234, True)`,
        `show_first`; then `view.claim` and `view.ready` exist, `view.ready.tmp` does not, `deiconify` not called.
        On Windows the recorded `update_idletasks` also calls the real one, and `FakeFocus.wrapper_hwnd` also
        records the real `win_focus.wrapper_hwnd(root)`, which must be `Success(GetParent(root.winfo_id()))` (ctypes
        in the test): the real guard passes at the point `main` asks (RV8 nit; no window is shown, as in test 10);
     b. live folder, `view.claim` already there → no `FakeFocus` call, root still withdrawn (`root.state() ==
        "withdrawn"`), no `view.ready`; feeding `end` and ticking → plain `deiconify`, still no `set_live_style`;
     c. finished folder (`G` ends with `end`), `--ready` given → no `view.claim`, no `view.ready`, `deiconify` once, no
        `FakeFocus` call;
     d. live folder, no `--ready` → style and `show_first`, no `view.claim`;
     e. `FakeFocus.set_live_style` returns `Failure("denied")` → `deiconify` once, the warning label shows `clicking
        here takes focus from the game`, stdout has `focus call failed: denied`; with `--ready` the claim is still taken and
        `view.ready` written;
     f. a legs-only folder with `--ready` → replay, no claim.
     g. `--replay` on `G` without its `end` line (RV4-D1 A) → no `FakeFocus` call, no `view.claim`, `deiconify`
        once, title `REPLAY (did not end)`, steps 0..24 reachable (`>|` → 24).
     h. `--replay --ready <f>/view.ready` → returns 2, stderr has `--replay cannot be used with --ready`, `make_root`
        not called, no `view.claim`, no `view.ready`.
     i. `--replay` on `G` with `end` → the same as without the flag (title `ended: max_laps`, `deiconify` once, no
        claim).
     In a and d, stdout has `window built in` before any `FakeFocus` call, and a `first show: root.state() = ` line
     after the `show_first` call.
- [ ] **Tests** (`tests/route_view/test_win_focus.py`):
  10. On Windows (skip elsewhere), a real root built the way `__main__` builds it (`tk.Tk()`, `withdraw()`, a
      packed widget; RV6 I1): before any idle pass `wrapper_hwnd(root)` is `Failure("no wrapper yet")` (and
      `int(root.wm_frame(), 0) == root.winfo_id()`, the fact it guards); after `root.update_idletasks()` it is
      `Success(h)` with `h == GetParent(root.winfo_id())` (ctypes in the test), `h != root.winfo_id()` and
      `root.state() == "withdrawn"`; `set_live_style(h, True)` → `GetWindowLongPtrW(h, GWL_EXSTYLE)` has
      `WS_EX_NOACTIVATE` and not `WS_EX_APPWINDOW`; then three `root.title(...)` calls with different texts (what
      the live ticks do) and `root.update()` → `wrapper_hwnd(root)` is still `Success(h)` and `h` still has
      `WS_EX_NOACTIVATE` (RV8 n1: the style lives on this wrapper, so a call that rebuilt it would drop the style;
      RV-7 test 10 bans the calls that do); `False` → the reverse. Nothing is shown (no `show_first` here: a
      shown window is N1's job, H-1). If this test fails, fix `wrapper_hwnd` or `__main__` (see "The wrapper exists
      only after an idle pass"), not the test's order.
  11. `sys.platform` patched to `"linux"` and `ctypes.WinDLL` patched to raise → all three functions return
      `Failure("not Windows")`.
  12. **ctypes only inside functions:** an AST walk over every `src/route_view/*.py` finds no module-level `import
      ctypes` / `from ctypes ...`, and every `ctypes` name use inside a function body. (Not a `sys.modules` check:
      numpy, which the model imports, may import `ctypes` itself.)
  13. `FIRST_SHOW == "noactivate"`; with it patched to `"deiconify"`, `show_first` calls `root.deiconify()` and no
      `ShowWindow` (patched `WinDLL`).
  14. **Return values are read as Microsoft documents them** (RV4 I1; a patched `WinDLL` whose functions return
      scripted values and a patched `ctypes.get_last_error`/`set_last_error` pair; `sys.platform` "win32"):
      `ShowWindow` returning 0 (the window was hidden) → `show_first` is `Success` and `root.deiconify` is not
      called; returning 24 → `Success`. `SetWindowLongPtrW` returning 0 with last error 0 (the previous ex-style was
      0) → `set_live_style` is `Success`; returning 0 with last error 5 → `Failure` naming 5; `set_last_error(0)` is
      called before each `Get`/`SetWindowLongPtrW` call (the fake records the order). `SetWindowPos` returning 0 →
      `Failure`.
- **Run:** `python -m pytest -q tests/route_view`, then `lint-imports`.
- **Commit:** `feat(route-view): live following, the no-activate first show and the claim/ready handshake` (body: the
  Win32 and Tk grounding URLs, the `wm frame` quote for the idle pass and why the idle pass makes the wrapper of a
  withdrawn window: its first map reaches `UpdateWrapper` (Tk 8.6.15 `win/tkWinWm.c:2388`) without the withdrawn
  check, which is only in the `else` branch (:2370-2373); that RV-7 bans the `wm` calls that rebuild the wrapper
  (RV8 n1); that N1 (H-1) is what tests the shown window).
- **Review items:** the reviewer checks against spec 5.7 that nothing calls a Win32 function at import, that the
  style is set before the first show and cleared only at `end`, that `__main__` runs `update_idletasks()` before the
  claim and `wrapper_hwnd` refuses the inner window (RV6 I1), and against spec 5.9 step 3 that the claim and the
  show are adjacent and a lost claim never shows the window before `end`.

### Task RV-9: `walk --view`, snapshot, map copy, step log, viewer start and ready wait

**Files:** `src/route/walk.py`; tests `tests/route/test_walk_view.py`. `tests/route/test_walk_cli.py` is not changed
and must still pass as it is.
**Class:** S — the walk's start-up and cleanup order (exit codes, the `end` event's `code`), a wait with a claim race
against another process, and focus safety (no viewer may show during a walk after "Focus the game window...").
**Cloud:** yes (seams only; test 7's Popen check is Windows-only and skips elsewhere). **Depends on:** RV-2 (V2's
`observer=` and V3's `StepLog`, with A2), RV3-D2 (decided A, with A1). No viewer code: the tests use a fake viewer.
**Interfaces created:** V7 (used by RV-10, which calls `_start_viewer` and `_wait_for_viewer` for real).

- [ ] Read V1 (`RunHeader`, the six file names), V3, V6, V7 (whole: it is this task's order), RV3-D2's "Decided"
  paragraph, spec 5.2 (the walk bullets), 5.9 (whole), 6 ("must not change"), S10; `walk.py` (whole);
  `runtime.py:231-242` (`check_out`) and `:261-275` (`run_cleanup`); `record_minimap.py:8-10`, `:96`, `:116-125`
  (RGB frames, `shared_frames`); `tests/route/test_walk_cli.py:320-500` (the rig this task reuses).
- [ ] Ground (hard rule 12), with the source or docs line in the commit body: `cv2.imencode` returns `(ok, buf)`;
  `subprocess.Popen` with a file object as `stdout` gives the child its own handle, so the parent may close its
  copy once `Popen` returns; `subprocess.CREATE_NO_WINDOW` and `CREATE_NEW_PROCESS_GROUP` exist only on Windows
  (on another platform `_start_viewer` returns a `Failure`, it never raises: catch `AttributeError` with `OSError`);
  `sys.exc_info()` inside a `finally` that an exception passes through (spec 5.9 measured it; test 4 pins it).
- [ ] `walk.py`, as V7 says, step by step: `--view` (`action="store_true"`, help "open the route view beside the
  game; turns on --record"); the frame held after `_wait_first_frame`; `clock` and `step_log` before the walker and
  `observer=step_log`; step 1 through `_write_run_files` with the A1 branch; `session.json` with the one `head`;
  `_start_viewer`, `_wait_for_viewer` and `VIEW_LATE_LINE` with `--view`; `leaving` and the `step_log` cleanup
  step; `_OUT_FILES`. Nothing else in `main` moves: the early returns (:214-285) and the cleanup steps keep their
  order, with `step_log` inserted between `stop.json` and `leg_log`.
- [ ] Docstring: the usage line gains `[--view]`; the exit-code paragraph does not change (`git diff` shows none
  there); the cleanup list reads "timer, release_all, recorder, stop.json, step log, leg log, link.close, stop
  control close"; "Where the plan is silent" gains one line each: every walk writes `map.csv`, `minimap.png` and
  `steps.jsonl` once the stop control has started (spec 5.9 step 1); a step-1 failure prints one line and walks on
  with no step log and no viewer (RV3-D2 A, A1); `--view` turns on `--record` and starts the viewer by module name
  (`python -m src.route_view`), never imports it; a viewer that is late, exits or cannot start costs one stderr line,
  never the walk; the `end` event's `code` is null when an exception leaves `main`.
- [ ] **Tests** (`tests/route/test_walk_view.py`; write them first). The rig: import the B19 rig flat, as
  `tests/CLAUDE.md` describes (`from test_walk_cli import sim, FIXTURE, _lines, _json, _alive_names`; `sim` is a
  pytest fixture and works when imported). Every test sets `sim.frame = np.arange(72, dtype=np.uint8).reshape(4, 6,
  3)` unless it says otherwise (an asymmetric frame, so a channel swap shows). A `FakeViewer` installed with
  `monkeypatch.setattr(walk, "_start_viewer", fake)` records each call's `out`, appends `"viewer"` to `sim.log`, and
  returns a `FakeProc` (`poll()` counts its calls and returns `None` or a code; `kill`, `terminate`, `wait` raise
  `AssertionError`) per mode: `ready` (a thread that after 0.1 s creates `view.claim` with `open(..., "x")`, writes
  `"viewer"` into it, prints `FAKE READY`, then writes `view.ready.tmp` and `os.replace`s it), `never` (does
  nothing), `claim_only` (creates `view.claim` at once, never `view.ready`), `exits` (`poll()` returns 0 from the
  first call), `ready_then_exits` (as `ready`, then `poll()` returns 0), and `fails` (returns
  `Failure("no python")`). `_VIEW_READY_S`, `_VIEW_CLAIM_EXTRA_S` and `_VIEW_POLL_S` are patched short where a test
  says so.
  1. **`--view` turns `--record` on** (S10): `sim.main("--laps", "1", "--view")` with the `ready` fake → 0;
     `session.json["record"] is True`; `frames/index.jsonl` has at least one line. Without `--view` or `--record`,
     `"record"` is False and there is no `frames/`.
  2. **Every walk writes the three files; the walker gets the observer** (S10): `sim.main("--laps", "1")` (no
     `--view`) → 0; `read_run(out)` → `origin "steps"`, `ended True`, `bad_lines 0`; the header has `source
     "walk"`, `clock "perf_counter_ns"`, `map "map.csv"`, `map_sha256` equal to `session.json`'s, `class "kanna"`,
     `params` equal to `session.json`'s, `rotation` equal to `tuple(n.minimap_position for n in sim.rotation)`,
     `git_head` equal to `session.json`'s, `wall_ns` an int between `time.time_ns()` read before and after `main`,
     `perf_ns` an int no greater than the first event's `t_ns` (both from the run's clock, RV4-D2 A); the first
     event is `leg` and the last is `end` with `reason "max_laps"`,
     `code 0`; the `leg` events are as many as the lines of `legs.jsonl` (8), and the `planned` events map one to one
     onto the moves of `legs.jsonl` in order (`t_ns`, `pos`, `kind`, `dir`, `walk_ms`, `cost_to_go_s`). `map.csv` is
     byte-equal to `FIXTURE`. `cv2.cvtColor(cv2.imread(str(out / "minimap.png")), cv2.COLOR_BGR2RGB)` equals
     `sim.frame`; no `minimap.png.tmp` is left. The fake viewer was never called; there is no `view.claim`,
     `view.ready` or `view.log`; `"step-log"` is not in `_alive_names()` after `main` returns.
  3. **`end.code` and `end.reason` per way out** (S10, spec 5.9): `RouteWalker.run` patched to return
     `Failure("boom")` → exit 6, `end` `reason "error"`, `code 6`; patched to raise `RuntimeError` → 7, `"error"`,
     `7`; the link-down summary as `test_walk_cli.py`'s test (c) builds it → 1, `"link_down"`, `1`. In each,
     `steps.jsonl` is header then `end` (the patched `run` sends nothing) and `end` is the last line.
  4. **An exception leaving `main`**: `RouteWalker.run` patched to raise `KeyboardInterrupt` → `walk.main(...)`
     raises `KeyboardInterrupt` (`pytest.raises`); `steps.jsonl`'s last event is `end` with `reason None` and `code
     None`; `release_all`, `stop.close` and `link.close` still ran (`sim.log`).
  5. **No step log before `stop.start()` succeeds** (S10): connect failure (`sim.link.connect_result =
     Failure("x")`) → 4; stop-control start failure (`sim.stop_start = Failure("x")`) → 9. In both, none of
     `steps.jsonl`, `map.csv`, `minimap.png` exists, and `StepLog.start` (a spy on the class) was never called.
  6. **`_OUT_FILES`** (S10): equals the five old names followed by `steps.jsonl`, `map.csv`, `minimap.png`,
     `view.log`, `view.claim`, `view.ready`, in that order. Parametrized over the six new names: an `--out` holding
     that file → exit 2, stderr has `<path> already exists; choose a new --out`, `sim.capture_calls == 0`, and the
     folder's listing and the file's bytes are unchanged (as `test_walk_cli.py`'s B19-D2 test).
  7. **The spawn arguments** (S10, spec 5.9 step 2; Windows only, skip elsewhere): `walk.subprocess.Popen` patched
     with a recorder class (no process starts) → `walk._start_viewer(out)` calls it once with args `[sys.executable,
     "-u", "-m", "src.route_view", "--follow", str(out), "--ready", str(out / "view.ready")]`, `cwd=walk._REPO`,
     `creationflags == subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP` (and `== 134217728 | 512`),
     `stdin=subprocess.DEVNULL`, `stderr=subprocess.STDOUT`, and `stdout` a file object named `out / "view.log"`
     with mode `"ab"`, which is closed once `_start_viewer` has returned; it returns the recorder. A recorder that
     raises `OSError("x")` → `Failure` containing `x`, and the `view.log` handle is closed.
  8. **The ready wait, viewer on time** (S10): `--view` with the `ready` fake → 0; in the captured stdout `FAKE
     READY` comes before `Focus the game window`; stderr has no `route view did not start`; `view.claim` holds
     `"viewer"` (the walk did not create it).
  9. **RV3-D2 A1, a step-1 failure** (the one this ruling flips; see the Decisions section's undo):
     a. `StepLog.start` patched to return `Failure("disk full")`, `--view` with the `ready` fake → exit 0; stderr has
        exactly one line starting `step log unavailable:`, and it contains `disk full` and `walking without the
        route view`; the fake viewer was never called; no `view.claim`, `view.ready`, `view.log` or `steps.jsonl`;
        `map.csv` and `minimap.png` exist (the files that wrote stay); `legs.jsonl` has 8 lines; `StepLog.close`
        (a spy that prints `CLOSED`) ran exactly once, and `CLOSED` comes before `Focus the game window` in stdout
        (closed at once, no cleanup step).
     b. `sim.frame = None` (the seams allow it) → exit 0; `StepLog.start` never called; `map.csv` exists,
        `minimap.png` and `minimap.png.tmp` do not; the one line names `no first frame to save`.
     c. `Path.write_bytes` patched to raise `OSError("locked")` for a path named `map.csv` only → exit 0;
        `minimap.png` exists, `steps.jsonl` does not; the one line names `locked`.
     d. With `StepLog.start` failing as in (a): the walker's observer calls during the walk are dropped (A2): spy on
        `StepLog.planned` (wrapping the real method) → it was called (the walker kept calling it), and the private
        queue's `qsize()` is 0 when `main` returns.
  10. **Viewer never ready** (S10): the `never` fake, `_VIEW_READY_S = 0.3`, `_VIEW_POLL_S = 0.01` → 0; the wait
      took at least 0.3 s and under 1.5 s (time `main` with `time.monotonic` around a patched `_wait_for_viewer`
      that calls the real one); `view.claim` exists and is empty (the walk created it); stderr has
      `route view did not start within 0.3 s; walking without it (it opens when the walk ends), see view.log` once.
  11. **Claim taken, no `view.ready`** (S10): the `claim_only` fake, `_VIEW_READY_S = 0.2`, `_VIEW_CLAIM_EXTRA_S =
      0.3` → 0; the wait took at least 0.5 s and under 2 s; `view.claim` still holds the fake's content; the late
      line once.
  12. **Review Focus 3, the viewer closes or crashes during the walk**: (a) the `exits` fake with `_VIEW_READY_S =
      5.0` → `main` returns 0 within 2 s (it did not wait the 5 s), `view.claim` was created by the walk, the late
      line once, `legs.jsonl` has 8 lines and every outcome is `reached`, as a run without `--view`; (b) the
      `ready_then_exits` fake → 0, the same legs, and `FakeProc.poll`'s call count when `main` returns equals its
      count when `_wait_for_viewer` returned (the walk never looks at the process again); `kill`, `terminate` and
      `wait` were never called (they raise).
  13. **The viewer cannot start**: the `fails` fake → 0; stderr has `route view could not start: no python;
      walking without it`; no `view.claim`; `_wait_for_viewer` (spy) never called.
  14. **Cleanup order** (S10): `StepLog.close` wrapped to append `("step_log", (out / "stop.json").exists())` to
      `sim.log` (as a tuple), `--view` with the `ready` fake → `sim.log` has `"release_all"` before the `step_log`
      entry, that entry's flag is True (stop.json was final), and `"link.close"` after it. The same when
      `RouteWalker.run` raises `RuntimeError`.
- [ ] **T5 extra:** `python -m pytest -q tests/route/test_walk_cli.py` (unchanged file) passes; the S1 hash (hard rule
  11).
- **Run:** `python -m pytest -q tests/route/test_walk_view.py tests/route/test_walk_cli.py tests/route`
- **Commit:** `feat(route): walk --view starts the route view; every walk writes steps.jsonl, map.csv and
  minimap.png` (body: the grounding of `cv2.imencode`, the Popen handle and `sys.exc_info`; RV3-D2 A with A1; the S1
  hash).
- **Review items:** against spec 5.9, the reviewer checks that `steps.jsonl` opens only after `stop.start()`
  succeeds and no `return` follows it except `return code`; that `leaving` is read first in the `finally`; that
  `git diff` changes no exit code in the docstring or the code; and against spec 5.7 that no path shows a viewer
  after "Focus the game window..." (a step-1 failure starts none; a late viewer finds the claim taken).

### Task RV-10: N1 driver tool (`view_check`)

**Files:** create `src/route/harness/view_check.py`; tests `tests/route/test_view_check.py`.
**Class:** O — one tool file over Win32 reads, a feeder thread and a scripted procedure; the hard parts (spawn, wait)
are RV-9's real functions. **Cloud:** no (Win32); every test except 12 uses fakes and runs anywhere. **Depends on:**
RV-8 (the real viewer it starts), RV-9 (`_start_viewer`, `_wait_for_viewer`), RV3-D7 (decided: A + title bar).
**Interfaces created:** the CLI below (used by H-1 and RV-11).

The tool runs spec 5.7's no-key check. It never sends a key, a mouse event or any other input, never sets the
foreground, and needs no board: it starts the real viewer exactly as a walk does (`walk._start_viewer`,
`walk._wait_for_viewer`), feeds it a sim run line by line, and reads the foreground window while Hong clicks.

```
python -m src.route.harness.view_check run [--out data/route_view/n1-<UTC stamp>] [--laps 3] [--dry-run]
python -m src.route.harness.view_check after --out <the run's folder>
```
Exit codes: 0 every check passed · 1 a check failed or was not done · 2 usage, an `--out` outside `data/` or already
used, (without `--dry-run`) not Windows, or standard input closed so that its two questions cannot be asked (`n1.json`
is still written, with `stopped_by_no_input` and the checks not yet made as "not done") · 3 MapleStory is running, or
the process list cannot be read. (As built, RV-10 fix round, ruled final: a Ctrl+C that cannot be sent is a `FAIL`
whose detail starts `GenerateConsoleCtrlEvent failed:`, exit 1.)

- [ ] Read V1 (`read_run`, the file names), V6, V7, spec 5.7 (whole, the no-key check above all), 5.9 steps 2-3, RV3-D7,
  RV-8's `__main__` text (the viewer writes its pid into `view.ready`; its title starts `Route view`),
  `src/input/harness/soak.py:507-527` (the MapleStory check to copy), `record_minimap.py:128-137` (`_resolve_out`,
  the `data/` rule to copy) and `tests/route/test_import_rules.py:1-12` (harness may use Windows names only inside
  functions; `src.input.harness` is not on the route allowlist, which is why the check is copied, not imported).
- [ ] Ground every Win32 call and constant in Microsoft's docs, URLs in the commit body (hard rule 12):
  `GetForegroundWindow`; `EnumWindows`, `IsWindowVisible`, `GetWindowTextW`, `GetClassNameW`,
  `GetWindowThreadProcessId`; `GetCursorPos`, `WindowFromPoint`, `GetAncestor` (`GA_ROOT` = 2) (`WindowFromPoint`
  "Retrieves a handle to the window that contains the specified point" and skips hidden or disabled windows; that a
  point on the title bar gives the top-level wrapper itself, so `left_down_on` sees a title-bar click, is inferred
  from that, not documented; H-1's fourth click tests it, test 4 only scripts it); `GetAsyncKeyState`
  (`VK_LBUTTON` = 1, the high bit); `GetWindowLongPtrW` (`GWL_EXSTYLE`); `OpenProcess`
  (`PROCESS_QUERY_LIMITED_INFORMATION` = 0x1000), `GetExitCodeProcess` (`STILL_ACTIVE` = 259), `CloseHandle`;
  `GenerateConsoleCtrlEvent` (`CTRL_C_EVENT` = 0, process group 0 = every process attached to the calling process's
  console). `ctypes.WinDLL(..., use_last_error=True)` with `argtypes`/`restype`, inside functions only.
- [ ] `view_check.py`:
  ```python
  CLICKS = ("the map (the big picture)", "the text of the 'why' log", "the scrollbar of the 'why' log",
            "the title bar of the route view (press and let go without moving the mouse)")  # RV3-D7 A + title bar
  UPDATE_WAIT_S = 3.0                             # RV4 I3: the view's title must change within this while fed
  FIRST_EVENTS = 10; FEED_PERIOD_S = 0.25; CLICK_WAIT_S = 20.0; TASKBAR_WAIT_S = 30.0; STYLE_WAIT_S = 5.0
  WATCH_PERIOD_S = 0.02; AFTER_CLICK_S = 0.5; N1_FILE = "n1.json"; SOURCE = "source"
  @dataclass(frozen=True)
  class Check:
      name: str; passed: bool | None; detail: str     # None: not done (no click in time, or skipped)
  def _maple_check() -> tuple[bool, str]          # a copy of soak.maple_check (tasklist; "maple" in any name, any
                                                  # case); off Windows (dry run only) -> (True, "not Windows")
  def build_feed(out: Path, laps: int) -> Result[list[bytes], str]
      # run_sim(FF2 fixtures, laps, seed=1, out_dir=out / SOURCE); copies out/source/map.csv to out/map.csv; writes
      # out/steps.jsonl = the source's header line + its first FIRST_EVENTS event lines; returns the event lines
      # still to feed, the source's `end` line last (raw bytes, b"\n" kept)
  class Feeder:                                   # daemon thread "n1-feed": appends one line every period_s,
      def __init__(self, path: Path, lines: list[bytes], period_s: float) -> None  # binary append + flush;
      def start(self) -> None; def stop(self) -> None   # stop() joins it; it never writes the last (`end`) line
      def remaining(self) -> int                  # lines not fed yet, `end` excluded (0: the feed has run out)
      def write_end(self) -> None                 # appends the lines not fed yet, `end` last
  class Win32:                                    # the real reads; tests pass a fake with the same methods
      def foreground(self) -> int
      def visible_windows(self) -> dict[int, tuple[int, str, str]]   # hwnd -> (pid, title, class), top-level only
      def title(self, hwnd: int) -> str            # GetWindowTextW; "" if the window is gone
      def window_info(self, hwnd: int) -> tuple[int, str, str]   # (pid, title, class) of any window, visible or
                                                   # not; (0, "", "") if it is gone (RV8 n2)
      def left_down_on(self, hwnd: int) -> bool    # left button down and the cursor's root window is hwnd
      def left_down(self) -> bool
      def ex_style(self, hwnd: int) -> int
      def process_alive(self, pid: int) -> bool
      def ctrl_c_own_console(self) -> None
  def run_checks(out: Path, lines: list[bytes], *, win, start_viewer, wait, ask=input, say=print,
                 clock=time.monotonic, sleep=time.sleep, maple=_maple_check
                 ) -> tuple[list[Check], dict]    # checks + facts for n1.json; lines: build_feed's result, the
                                                  # lines the Feeder appends (RV8 n4)
  def main(argv: list[str] | None = None) -> int
  ```
- [ ] `run_checks`, in this order (spec 5.7's procedure; each step appends one `Check`; a failed precondition marks
  the checks it makes impossible `None` with the reason and goes to "end"):
  1. `fg0 = win.foreground()`, `before = win.visible_windows()`; `say` "Keep this console in front. Do not click
     anything until you are asked." A watch thread (`n1-watch`) reads `win.foreground()` every `WATCH_PERIOD_S` and
     keeps every change as `(clock(), hwnd, pid, title, class)`, the last three from `win.window_info(hwnd)` read at
     the change (so a window that closes later is still named; RV8 n2); once step 3 has found the view window it
     also reads `win.title(view)` each period and keeps every change as `(clock(), title, <lines the Feeder had left>)` (RV6 m2: the title is
     watched from the first show on, not only after the questions).
  2. `proc = start_viewer(out)`; a `Failure` → `Check("viewer started", False, <failure>)`, then "end". Else
     `ready = wait(out, proc)` → `Check("ready within the walk's wait", ready, ...)`; `Feeder(out / "steps.jsonl",
     lines, FEED_PERIOD_S)` starts. Not ready
     → the viewer stays hidden until `end` (spec 5.9 step 3), so the click checks are `None`; go to "end".
  3. `viewer_pid = int((out / VIEW_READY).read_text())` (RV-8 writes its pid there); the view window is the visible
     top-level window of that pid whose title starts `Route view`. After 1 s: `Check("first show kept the
     foreground", <the foreground rule over the watch's changes since step 1>, ...)`. **The foreground rule**
     (RV8 n2; step 4 uses it too): take the watch's changes in the span, plus `foreground()` now if it is not `fg0`,
     and drop those to `fg0` itself (a return to the console). If any went to a window of `viewer_pid` (the view's
     wrapper or any other window of that process) → `False`, the detail naming it: the view took the foreground,
     the verdict H-1 maps to a fallback. Else if any is left → `None`, the detail naming each such window's title,
     class and pid ("another window took the foreground; run again"): a dialog of another program or a console that
     closed again says nothing about the view, so it must not send H-1 to a fallback. Else → `True`.
     `Check("no console window", <the windows
     new since before are exactly the view window>, <each other new window's title, class and pid>)`, and `ask`
     "Did any other window (a console) appear when the route view opened? [y/N]": `y` fails it.
     Then `Check("the view updates while live", ...)` (RV4 I3: spec 5.7 makes "Tk's own state stays right" part of
     N1; the title carries lap, leg and state, so it changes as steps arrive), judged on the watch's title changes
     since the view was found, waiting up to `UPDATE_WAIT_S` more if there is none yet, and the answer to `ask` "Does
     the route view show the map and change as lines arrive? [Y/n]":
     - at least one title change while the Feeder still had lines, and the answer is not `n` → `True`;
     - the answer is `n`, or no change while the Feeder still had lines for the whole wait → `False`, the detail
       starting with which one decided it: `answered n` (titles did change while fed) or `no title change while
       fed` (whenever none was seen, whatever the answer) (RV8 n3: H-1 treats the two differently);
     - no change, and the Feeder had run out of lines (all but `end` fed) before the wait ended → `None` ("the feed
       finished before a title change was seen; run again with more --laps"): never `False`, so a slow answer or a
       short feed cannot send H-1 to `FIRST_SHOW = "deiconify"` (RV6 m2).
     The detail has the first and last titles seen and the view.log line that starts `first show: root.state() =`.
     That line is copied from `out / VIEW_LOG` into the facts (`first_show_state`), and so is the line that starts
     `focus call failed:` (`focus_error`, None if there is none; RV-8 prints it when a focus call failed and it fell
     back to `root.deiconify()`, RV6 I1).
  4. For each of `CLICKS`: `maple()` again (MapleStory found → stop, write `end`, exit 3); `say` "Click <target> in
     the route view once, then let go of the mouse. (<CLICK_WAIT_S> s)" (the target text of the title-bar entry
     already ends "without moving the mouse": a drag would start the window's move loop); wait until `win.left_down_on(view)` (clicks
     elsewhere are ignored, and counted in the detail), then until the button is up, then `AFTER_CLICK_S`;
     `Check("click on <target> kept the foreground", <the foreground rule over the watch's changes since the
     prompt>, ...)`; no click in time → `None`. After one that left the foreground off `fg0` (a `False`, or a `None`
     from another window), `say` "Click this console to bring it back to the front", and wait (up to `CLICK_WAIT_S`) until `foreground() == fg0` before the next prompt.
  5. Ctrl+C (spec 5.7: "a Ctrl+C in the parent's console does not end the viewer"): install a SIGINT handler that
     records the signal, `win.ctrl_c_own_console()`, sleep 1 s, restore the previous handler;
     `Check("Ctrl+C in the parent's console did not reach the viewer", <the handler fired, proc.poll() is None and
     win.process_alive(viewer_pid)>, ...)`. The handler not firing is a failure of the check itself ("the Ctrl+C was
     not delivered"), not a pass.
  6. End: `feeder.stop()`, `feeder.write_end()`; poll `win.ex_style(view)` up to `STYLE_WAIT_S` →
     `Check("end clears the no-activate style", <WS_EX_APPWINDOW set and WS_EX_NOACTIVATE clear>, <the style in
     hex>)`; `say` "The route view now has a taskbar button. Click it once."; wait up to `TASKBAR_WAIT_S` for
     `foreground() == view` → `Check("a taskbar click activates it after end", ...)`, `None` on timeout.
  The facts dict: `fg0`, `view_hwnd`, `viewer_pid`, `redirector_pid` (`proc.pid`), the watch's changes (with each
  window's pid, title and class), the number of
  lines fed, the watch's title changes, `first_show_state` and `focus_error` (step 3).
- [ ] `main`: `run` resolves `--out` (default `data/route_view/n1-<UTC stamp>`) under the repo's `data/` as
  `record_minimap._resolve_out` does (outside `data/` → 2) and refuses a folder that holds `steps.jsonl` or `source`
  (`runtime.check_out`, → 2); without `--dry-run`, not Windows → 2; `_maple_check()` not ok → print it, 3, before any
  file is written. Then `build_feed` (a `Failure` → 2). `--dry-run`: no viewer, no `Win32`: feed every line with
  period 0 and `end`, then `read_run(out)` must give the source's header and events, equal value for value, and
  `ended True` → print `dry run: <n> events fed and read back equal`, 0; else print the first difference, 1.
  Without `--dry-run`: `run_checks(out, lines, win=Win32(), start_viewer=walk._start_viewer,
  wait=walk._wait_for_viewer)` with `lines` = `build_feed`'s `Success` value (RV8 n4);
  write `out / N1_FILE` (`{"checks": [...], "facts": {...}}`, indent 2); print one line per check (`PASS`, `FAIL`
  or `NOT DONE`, name, detail); print "Now let this tool exit, then run: python -m src.route.harness.view_check
  after --out <out>"; return 0 iff every check passed. `after`: read `n1.json`; `Check("the viewer outlived its
  parent", <Win32().process_alive(viewer_pid) and the view window still visible>)`; add it under `"after"` in
  `n1.json`; print it, "You can close the route view now."; 0 iff passed.
- [ ] Module docstring: what it checks (spec 5.7), that it never sends keys or input and needs no board, that it
  must run with MapleStory closed and with Hong (or an agent on an idle PC, H15's rule) doing the clicks, that it
  must be started with the venv's `python.exe` so the parent is what a walk is (spec 5.9's job-object note), and
  the exit codes.
- [ ] **Tests** (`tests/route/test_view_check.py`; a `FakeWin` scripted by time on a `FakeClock`, whose `sleep`
  advances it; `say` and `ask` recorded; `start_viewer` and `wait` fakes that write `view.ready` with a pid):
  1. `build_feed(tmp, laps=1)` → `map.csv` byte-equal to `source/map.csv`; `steps.jsonl` is the source's header line
     and its first 10 event lines; the returned lines are the rest, the source's `end` line last.
  2. `Feeder`: started with period 0.01 over 5 lines, stopped after the third has been read back → `stop()` returns;
     `write_end()` → the file equals the source file byte for byte.
  3. Dry run: `main(["run", "--dry-run", "--laps", "1", "--out", <a folder under a patched data root>])` → 0 and the
     line `read back equal`; no `Win32` method is called (a `Win32` patched to raise on construction).
  4. All pass: a `FakeWin` with a constant foreground, the view window appearing after `wait` with a title that
     changes every `FEED_PERIOD_S` (RV6 m2: not once, which could fall before the check starts), a `view.log`
     holding `first show: root.state() = normal`, each of the four prompted clicks
     scripted on the view window, the style switched at `end`, the taskbar click making the view the foreground →
     every `Check` passed; `n1.json` holds them and the facts; exit 0 through `main` with `Win32` patched to the
     fake.
  5. The second click changes the foreground to the view (the fake's `window_info` gives it `viewer_pid`) → that
     check `False`, the "bring it back" prompt is said,
     the third click is still prompted once the foreground is back; exit 1.
  6. No click within `CLICK_WAIT_S` → `None` (`NOT DONE` printed) and exit 1; a click outside the view first is
     ignored and counted in the detail.
  7. A second new visible window (title `C:\...\python.exe`, class `ConsoleWindowClass`) → "no console window"
     `False` with that title in the detail; `ask` answered `y` → `False` too.
  8. `wait` returns False → the click and style checks are `None` with the reason, `end` is still written (the
     folder's `read_run` has `ended True`), exit 1.
  9. MapleStory: `_maple_check` patched to `(False, "MapleStory running: ['MapleStory.exe']")` → `main` returns 3 and
     the `--out` folder does not exist; the same check failing before the second click → 3, `end` written.
  10. Ctrl+C: the fake's `ctrl_c_own_console` calls `signal.raise_signal(signal.SIGINT)` → the check passes when
      `proc.poll()` is None and `process_alive` True; `process_alive` False → `False`; a fake that delivers nothing →
      `False` with "the Ctrl+C was not delivered". The previous SIGINT handler is restored in each case.
  11. Never sends input: an AST walk over `view_check.py` finds none of `SendInput`, `keybd_event`, `mouse_event`,
      `SetCursorPos`, `SetForegroundWindow`, `SetActiveWindow`, `SetFocus`, `BringWindowToTop`, `PostMessageW`,
      `PostMessageA`, `SendMessageW`, `SendMessageA`, `BlockInput`, and no call of `BleLink`, `InputLayer` or
      `moves.execute`.
  12. Windows only (skip elsewhere), shows no window: `Win32().process_alive(os.getpid())` is True; for a child
      `python -c "pass"` that has exited, False; `foreground()` returns an int; `visible_windows()` returns a dict
      whose values are `(int, str, str)`; `title(<a hwnd from it>)` returns a str; `window_info(<that hwnd>)` has
      the same pid and class as its `visible_windows()` value (not the title, which another program may change
      between reads), and `window_info(0)` is `(0, "", "")`.
  13. `after`: an `n1.json` with a `viewer_pid` and a fake `process_alive` True and the window visible → 0 and
      `n1.json` gains `"after"`; False → 1.
  14. **The view updates while live** (RV4 I3): the view's title never changes within `UPDATE_WAIT_S` → that check
      `False` with both titles in the detail, which starts `no title change while fed`, and exit 1; the title
      changes but `ask` is answered `n` → `False`, the detail starting `answered n`; no change and `n` → `no title
      change while fed`; the
      `view.log` line is copied into the facts as `first_show_state` in both cases (`"withdrawn"` in the second, to
      show the copy is verbatim); no `view.log` line → `first_show_state` None and the detail says so. RV6 m2: the
      title changes only once, during the slow "[y/N]" answer (the fake `ask` advances the clock 30 s), and never
      after → `True` (the watch saw it); no change and the Feeder out of lines before the wait ends (`Feeder.remaining`
      patched to return 0) →
      `None` (`NOT DONE`, "the feed finished"), exit 1. A `view.log` holding `focus call failed: no wrapper yet` →
      `focus_error` is that line's text; without one, None.
  15. **The foreground rule** (RV8 n2): during step 3's 1 s the foreground moves to a window of another pid (title
      `Dialog`, class `#32770`) and back to `fg0` → "first show kept the foreground" is `None` (`NOT DONE`) with
      that title, class and pid in the detail, exit 1; the same with the window closed before step 3 lists the
      windows (`window_info` read at the change still names it; "no console window" passes) → `None` too; a move to a
      second window of `viewer_pid` (not the view's) → `False`; a move to the view and back to `fg0` → `False`. A
      click check whose span holds only a move to another pid's window → `None` and the "bring it back" prompt is
      said if the foreground is not `fg0`.
- **Run:** `python -m pytest -q tests/route/test_view_check.py tests/route/test_import_rules.py`, then the dry run:
  `python -m src.route.harness.view_check run --dry-run --out data/route_view/n1-dry` (exit 0; it opens no window
  and sends nothing, so an agent may run it). Never run `run` without `--dry-run`: that is H-1.
- **Commit:** `feat(route): N1 driver tool for the route view's no-key check` (body: the Win32 grounding URLs; RV3-D7
  A + title bar; the dry run's output line).
- **Review items:** the reviewer checks test 11's list against the file by eye, that every Win32 call is a read
  except `GenerateConsoleCtrlEvent` (which sends a console signal, no key), and that steps 1-6 follow spec 5.7's
  procedure in its order.

### Task RV-11: Docs (`src/route/CLAUDE.md`, `src/route_view/CLAUDE.md`, root `CLAUDE.md`, `tests/CLAUDE.md`, spec "As built")

**Files:** `src/route/CLAUDE.md`; create `src/route_view/CLAUDE.md`; root `CLAUDE.md`; `tests/CLAUDE.md`; the spec
`docs/superpowers/specs/2026-10-27-route-view-design.md` (a new last section "As built"). No code.
**Class:** D. **Cloud:** yes (the `--help` of the viewer needs `tkinter` importable). **Depends on:** RV-1..RV-10
merged on `p1-route-view` (the facts are about the built code); H-1's result if it has run (else the spec section
says "N1 pending").

Every fact in these files cites `file:line` from the code as built; a fact lives in one file and the others point to
it (root `CLAUDE.md`, "Per-directory notes"); safety rules are repeated where an agent acts on them.

- [ ] Read root `CLAUDE.md` (whole), `src/route/CLAUDE.md`, `tests/CLAUDE.md`, `src/input/CLAUDE.md` (the form of a
  package note), spec 5.7, 5.9 and 6 (what the docs must say), this plan's Decisions section (the rulings to record),
  and the built files: `step_log.py`, `route_preview.py`, `walker.py`'s observer lines, `walk.py`'s `--view` lines,
  `sim/run.py`'s `--out` lines, `src/route_view/*.py`, `harness/view_check.py`, `pyproject.toml`'s contracts.
- [ ] `src/route/CLAUDE.md`:
  - "The loop": one line on the observer: called at the V2 points (`walker.py:<lines>`), keyword-only, `None` by
    default; with `None` nothing else happens (the code-shape rule, spec 5.2).
  - "Seams": `route_preview.py` calls the planner's private helpers (list each name with its `planner.py` line;
    RV1-D4 A, H53): a change to one of them must keep S3/S3b green (`tests/route/test_route_preview*.py`,
    `test_preview_sim.py`); the step log (`step_log.py`) is the route view's input, its format V1 (header keys, one
    run per file, a second `run` line is a bad line).
  - "Rules enforced by tests": contract f now lists `step_log` and `route_preview` (`pyproject.toml:<lines>`).
  - "Traps": the freeze line names the observer as the one exception (RV-2, `walker.py:<lines>`); every walk writes
    `steps.jsonl`, `map.csv` and `minimap.png` after the stop control starts, and a step-1 failure walks on with no
    step log and no viewer (RV3-D2 A, `walk.py:<lines>`); `_OUT_FILES` has the six new names; a sim `--out` that
    already holds `steps.jsonl` exits 2 before anything is written (RV3-D3 A, `sim/run.py:<lines>`); the sim's
    shifted `map.csv` is read inside `_load_shifted_agent`'s `with` block (plan choice 7).
  - "Commands": the walk line gains `[--view]`; the N1 tool's three commands (`run --dry-run` sends nothing and opens
    no window; `run` and `after` need Hong's clicks and MapleStory closed); `python -m src.route_view --follow
    <folder>` for a replay.
- [ ] `src/route_view/CLAUDE.md` (new; the repo keeps one per package, spec 6): what the package is (a separate
  process; imports per spec 5.8, both contracts by name at `pyproject.toml:<lines>`); the modules (`model.py`,
  `window.py`, `follow.py`, `win_focus.py`, `__main__.py`), one line each with the main entry and its line; the CLI
  (V6); the run-folder files it reads and the three it writes or reads with the walk (`view.log`, `view.claim`,
  `view.ready`) and the claim rule (spec 5.9 step 3); the spawn flags and why (spec 5.9 step 2, one line, pointing to
  the spec); the no-activate style's lifecycle (spec 5.7) and `win_focus.FIRST_SHOW` (the one-line fallback switch); the idle
  pass before the claim and why (`wrapper_hwnd`'s `no wrapper yet`, RV6 I1);
  "ctypes only inside functions" with the test that enforces it; traps: never call `focus_force`, `lift`, `-topmost`
  or a dialog while live; never call `wm resizable`, `overrideredirect`, `transient` or `attributes` (any form):
  after the idle pass Tk rebuilds the wrapper on these (Tk 8.6.15 `win/tkWinWm.c:5073-5076` for `resizable`),
  silently dropping the no-activate style, and a later click on the view takes focus from the game (RV8 n1; RV-7
  test 10 enforces it); a viewer on a folder with `legs.jsonl` and no `steps.jsonl` opens in replay (so a walk
  never starts one there, RV3-D2 A1); `--replay` opens a folder with no `end` (a crashed or killed walk) as a
  replay, never with `--ready`; never open a replay while a walk runs (a click activates it; RV4-D1 A, RV4 m5); the
  planned why line's route is `preview.path` while the panel's Route line is the hops' route, so they can differ
  (RV4 m7, `model.py`'s docstring line); the why-line time format (RV3-D6, built A, open with Hong; the header's
  `wall_ns`/`perf_ns` anchor is recorded and not yet read, RV4-D2 A); tests and the commands (`python -m pytest -q
  tests/route_view`); the N1 result (H-1) or "N1 pending".
- [ ] Root `CLAUDE.md`: in "Architecture", one paragraph "**Route view** (`src/route_view/`)": what it shows, `walk
  --view` starts it as a child process by module name and waits for its window before the countdown, the walk and the
  viewer share only files in the run folder, replay with `python -m src.route_view --follow <folder>`; point to
  `src/route_view/CLAUDE.md`. In "Per-directory notes", add `src/route_view/CLAUDE.md` to the list. In "Running",
  the viewer command, `python -m src.route_view --follow <folder> [--replay]`. In "Safety rules", one line (RV4 m5):
  never open a route-view replay while a walk runs, because a replay window takes focus when clicked and the board's
  keys go to whichever window has focus. Nothing else there: the live view sends no keys, and the N1 tool's rule
  lives in `src/route/CLAUDE.md`, where an agent acts on it.
- [ ] `tests/CLAUDE.md`: "Layout" gains `tests/route_view`; "Speed": the `slow` count from `python -m pytest
  --collect-only -m slow -q` (it was 43), and the `tests/route_view` time.
- [ ] The spec's "As built" section (new, last): the commits of RV-1..RV-10 (short sha, subject); the rulings built
  (RV3-D1 A, RV3-D2 A with A1/A2, RV3-D3 A with A3, RV3-D4 A, RV3-D5 A2, RV3-D6 A provisional, RV3-D7 A + title
  bar, RV3-D8 B, RV4-D1 A, RV4-D2 A); these lines, each one sentence:
  - spec 5.7's "click the viewer's canvas, a button and the slider" was read as "the controls the live window has"
    (the map, the why log's text and scrollbar, the title bar), because the button and slider exist only in the
    replay bar, hidden while live (spec 5.4 "Replay only"; RV3-D7); for the scale buttons this rests on
    RV80-ADJ-D1 (next line);
  - "route changed" compares the move's kind and direction, not the walk's length, and the line names the move as
    well as the node ("expected next <move> toward <node>, now <move> toward <node>"): both depart from spec 5.4's
    wording "expected next <node>, now <node>" (RV3c, RV3-D8 B; judged in RV80-ADJ);
  - a folder with no `end` follows live unless opened with `--replay`, and a silent live view names `--replay` in its
    status line (RV4-D1 A);
  - the 2x/3x/4x scale buttons are in the replay bar, so they are hidden while live; this is open with Hong
    (RV80-ADJ-D1) and may change on his answer;
  - the step-log header has two fields spec 5.2's example (spec:275-276) does not, `wall_ns` and `perf_ns` (the
    wall clock and the run clock read together; null in sim and legs-only headers; nothing reads them yet): an
    extension of spec 5.2 (RV4-D2 A; RV3-D6's option C calls exactly this a spec change; RV6 m4);
  - spec 5.7's measurement that `wm frame` is `GetParent(winfo_id)` holds only after Tk's first idle pass (before
    it, `wm frame` is the root's own window), so the viewer runs `update_idletasks()` before the claim and
    `wrapper_hwnd` refuses the inner window (RV6 I1, RV-8);
  then the measured values (S8 median from RV-3's commit body, the S3b real-landing rates RV-5 printed);
  the spec's section 9 verdict questions with the plan reviewer's answers if given; N1's result or "pending (H-1)";
  L1-L4 "pending (H-2..H-4)".
- [ ] **Verify** (the D-class rule: every command named runs): `python -m src.route_view --help`, `python -m
  src.route.walk --help`, `python -m src.route.harness.view_check --help` and `run --dry-run --out
  data/route_view/n1-docs` (exit 0), `python -m pytest -q tests/route tests/route_view`, the S1 hash (hard rule 11),
  `python tools/check.py --full`; re-open each cited `file:line` and check it says what the doc says.
- **Commit:** `docs(route-view): package notes, root CLAUDE.md and the spec's As built section`.

---

## Tasks needing Hong ([Hong]), in full

Run in this order: H-1, then H-2 (which includes H-4's warm part), H-3, H-4's cold start. None is run by an agent
without Hong's go, except H-1 on an idle PC as its "Who" says. The root `CLAUDE.md` safety rules apply to every walk
(the board's keys go to whichever window has focus).

### H-1: N1, the no-key check (spec 5.7, section 7)

**Who:** Hong; or an agent, only while nobody uses the PC (H15's idle rule: check the time since the last keyboard or
mouse input) and with MapleStory closed, using real mouse input. **Needs:** RV-1..RV-10 merged on `p1-route-view`; no
board, no game. **Blocks:** H-2.

1. Close MapleStory. From the repo root, in a console window of its own, opened by hand and used for nothing else,
   with the venv's interpreter (so the parent is what a walk is):
   `.venv\Scripts\python.exe -m src.route.harness.view_check run`. It prints its folder (`data/route_view/n1-<stamp>`).
   Never run it through a pipe or `Tee-Object`, a `.bat` wrapper, an agent's shell tool, or the console of a walk: its
   Ctrl+C check sends CTRL_C_EVENT to every process on that console, and a process without a handler (the `tee`, the
   wrapper, the shell, the walk) exits (`src/route/harness/view_check.py`, module docstring; the tool does not test
   for this). An agent that runs H-1 opens a fresh console window for it.
2. Do what it asks, in order: keep its console in front; answer the "did another window appear" question; answer
   whether the route view shows the map and changes as lines arrive; click the four parts of the route view it names
   (the map, the why log's text, the why log's scrollbar, the title bar without moving the mouse: RV3-D7 A + title
   bar), one at a time, each when asked, nothing else; when the route view gets a taskbar button, click that button once.
   The tool itself sends the Ctrl+C to its own console (a console signal, not a key).
3. When it exits: `.venv\Scripts\python.exe -m src.route.harness.view_check after --out <that folder>`. Then close
   the route view window.
4. Report to the orchestrator: the exit codes of both commands and `<folder>/n1.json`.

**Pass:** both exit 0 (every check `PASS`). **On a failure**, by the check that failed (spec 5.7's fallbacks; the
orchestrator applies them, then H-1 runs again). First read `n1.json`'s `focus_error`: if it is set, the first show
was RV-8's activating `root.deiconify()` fallback, not the no-activate show, so every failure below is that error,
not a verdict on `FIRST_SHOW`. Fix the error in RV-8 and run H-1 again (`no wrapper yet` means `__main__`'s idle
pass before the claim is missing or too late, RV6 I1). Only with `focus_error` None:
- "first show kept the foreground" `FAIL` (it fails only when the foreground went to a window of the viewer's
  process; a move to any other window is `NOT DONE`, naming that window, and is run again as below; RV8 n2) →
  RV-8's `win_focus.FIRST_SHOW = "deiconify"` (the one-line switch);
- "the view updates while live" `FAIL` (a `NOT DONE` here, the feed running out, is run again as below), by its
  detail and `n1.json`'s `first_show_state` (what Tk believed after the first show; RV8 n3):
  - the detail starts `no title change while fed`, or `first_show_state` is not `normal` (Tk's state left wrong by
    the raw first show) → the same switch, `FIRST_SHOW = "deiconify"` (spec 5.7's fallback for "leaves Tk's state
    wrong"), then H-1 again;
  - the detail starts `answered n` and `first_show_state` is `normal` (Tk's state was right and the title kept
    changing, so the picture is what Hong found wrong) → not the switch: raised to the orchestrator as a drawing or
    following problem in RV-7/RV-8;
- a check that still fails after its fallback has been applied and H-1 run again → raised to the orchestrator (no
  further fallback; RV8 n3 fyi);
- a "click on ... kept the foreground" check → RV1-D6's fallback A (Hong's): RV-8's `__main__` calls
  `window.add_warning("clicking here takes focus from the game")` on every live show (the live window already has
  no controls, so it is already watch-only);
- "ready within the walk's wait", "no console window", "the viewer outlived its parent", or "Ctrl+C ... did not reach
  the viewer" as a `FAIL` whose detail is "the viewer ended" (the spawn part) → stop: back to spec 5.9 step 2 before
  L1. The other two outcomes of the Ctrl+C check mean the check could not run, not that the spawn is wrong: the detail
  "the Ctrl+C was not delivered", or one that starts `GenerateConsoleCtrlEvent failed:` → fix the launch (a fresh
  console window of its own, step 1) and run H-1 again. So is an exit 2 that says standard input is closed
  (`n1.json` has `stopped_by_no_input`);
- "end clears the no-activate style" or "a taskbar click activates it after end" → raised to the orchestrator
  (replay after a live run; it does not touch focus during a walk).
A `NOT DONE` check is run again, not passed.

### H-2: L1 and L2, supervised walks with the view (spec 7)

**Who:** Hong, at the PC, with the board connected and MapleStory running on Fossil Foundry 2. **Needs:** H-1
passed (or its fallback applied and H-1 passed again). **Blocks:** H-3.

1. Walk 1 (L1): `python -m src.route.walk --map maps/fossil_foundry_2.csv --supervised --laps 1 --view --out
   data/route/rv-l1`. The route view opens on the other monitor before "Focus the game window..."; click the game
   within the 5 s. Watch: the blue nodes numbered #1..#7, the target ring moving leg by leg, each move and its route
   line appearing within about 0.3 s of the move, the why log reading sensibly. During the last leg, close the route
   view window with its close button: the walk goes on and ends by itself (exit 0), and after the close the game
   still has focus (the last leg is `reached`). N1 cannot click the close button (it would end the viewer before
   its later checks), so this is its only test (RV4 m6).
2. Walk 2 (L1, F12): the same command with `--out data/route/rv-l1-f12`; press F12 after about 30 s: the walk stops
   (its `stopped: ...` line names the hotkey) and the view switches to replay.
3. Walk 3 (L1, Ctrl+C): `... --minutes 2 --view --out data/route/rv-l1-ctrlc` (no `--laps`); after about 30 s, click
   the walk's console and press Ctrl+C at once (keys sent in between go to the console: accepted for this one
   check): the walk stops and the view stays open and switches to replay of that run.
4. Report, per walk: the exit code, the `stopped:` line, and anything the view showed that looked wrong.
5. L2 (the orchestrator dispatches an agent for it afterwards; read-only, no game): walk 1's `legs.jsonl` has 8
   legs, all `reached`, as a walk without the view; the gaps between `t_ns` of `frames/index.jsonl` (median, 95th
   percentile, max) for `rv-l1`, next to the same for an earlier `--record` run without the view if one exists
   under `data/route/`. Reported, not pass/fail beyond the legs.

**Pass:** L1's items as seen by Hong; walk 1 exit 0; F12 and Ctrl+C each stop their walk and leave the view in replay.

### H-3: L3, replay (spec 7)

**Who:** Hong (his eyes). **Needs:** H-2. No walk may be running (a replay window takes focus when clicked, as it
should).

1. `python -m src.route_view --follow data/route/rv-l1`: step by event, by move and by leg; scrub with the slider to
   any move; play at 1x and 4x; jump with the lap and leg boxes; change the scale (2x, 3x, 4x).
2. `python -m src.route_view --follow data/route/b21-test1`: the title says `REPLAY (from legs.jsonl)`; the same
   stepping and scrubbing.

**Pass:** both replay, step and scrub to any move without an error line.

### H-4: L4, focus for a whole run, and once from a cold start (spec 7)

**Who:** Hong. **Needs:** H-1; the warm part is judged during H-2's walks.

1. Warm: during H-2's walks, after the countdown the game kept focus for the whole run (no move went missing; every
   leg of walk 1 `reached`).
2. Cold start: after a reboot, as the first `--view` walk: `python -m src.route.walk --map maps/fossil_foundry_2.csv
   --supervised --laps 1 --view --out data/route/rv-l4-cold`. The countdown starts only after the route view's
   window is up, and its appearance does not take the foreground from the console.
3. If the walk printed `route view did not start within 15 s ...`: the window stays hidden and opens when the walk
   ends; the game must still keep focus all run. Report the time on `view.log`'s `window built in <s> s` line.

**Pass:** the game keeps focus in every walk; the cold-start window appears before the countdown without taking the
foreground, or the miss is reported with the build time.

---

## Not verified at planning time

Collected from all four pieces; each is grounded or measured by the task named.
- (Measured by RV6 and RV7: the wrapper HWND exists only after Tk's first idle pass; RV-8's `wrapper_hwnd` note.)
  Tk 8.6's wrapper HWND taking `WS_EX_NOACTIVATE` after creation, `SW_SHOWNOACTIVATE` leaving Tk's state right, the
  taskbar noticing the style change at `end`, and the spawn flags' real behaviour (no console window, Ctrl+C
  isolation, outliving the parent): N1 (RV-10, H-1).
- `GenerateConsoleCtrlEvent(CTRL_C_EVENT, 0)` reaching the same processes as a typed Ctrl+C (Microsoft's docs say
  group 0 is every process attached to the console); `GetWindowThreadProcessId` on a console window naming the
  process that would own a stray console (RV-10 ground; H-1's y/N question covers a console the window list misses).
- (Closed by RV5, RV4 m8.) A click on Tk's `Text` calls `focus`: read in Tk 8.6.15's `text.tcl` as installed with
  Python 3.14, `bind Text <1>` (`:45-48`) → `proc ::tk::TextButton1` (`:562-583`), `focus $w` at `:579`,
  unconditional; `scale.tcl`, `scrlbar.tcl` and `button.tcl` contain no `focus` (RV3-D7's ruling).
- That a press and release on the title bar of a `WS_EX_NOACTIVATE` window does not activate it, and that
  `WindowFromPoint` on a title-bar point gives that top-level window (so `left_down_on` sees the click): inferred,
  not documented; N1's fourth click (RV-10, H-1) tests both.
- What Tk's `root.state()` reports after the raw `ShowWindow(SW_SHOWNOACTIVATE)` (likely still `withdrawn`, Tk not
  having been told), and whether Tk still redraws then: RV-8 logs it, N1's "the view updates while live" check
  judges it (RV4 I3).
- Pillow's `ImageTk` with nearest scaling at 4x (RV-7); the import-linter `|` layer syntax (RV-1); `cv2.imencode`'s
  return value, the Popen `stdout` handle, `subprocess`'s Windows-only flags under basedpyright on Linux, and
  `sys.exc_info()` in a `finally` (RV-9; the last was measured by the spec).
- Values to take from runs, not from the plan: RV-3 test 1's `SimReport` fields beyond 20/141/379 (run on the parent
  commit); RV-6's golden values that come from `rg.path` / `cost_to`; RV-5's printed rates (measured by RV3b2 with a
  prototype, not the built code).
- The task sizes are estimates; RV-8 (~130k) and RV-9 (~120k) are the largest (RV-8's `win_focus.py` and its tests
  split off cleanly if its implementer nears the cap).
- No test or command was run for this plan (planning only); the code under `src/` is unchanged from `5d1176a`.
