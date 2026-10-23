# Plan: Phase 1 · Feature B — Walking the route

**Status: approved** · written 2026-10-23 on branch `p1b-route-walking` (cut from `master` 2413c64)

Amended after approval: B19 (PB-amend, 2026-10-23)

> **For agentic workers:** one fresh implementer per task, in the order of the task list. Each task is TDD: write the
> listed tests first, watch them fail, implement, watch them pass, run the full suite, commit. Steps use checkbox
> (`- [ ]`) syntax. A task's text is complete on its own: read the Contracts it names, not other tasks.

**Goal:** on Fossil Foundry 2 (FF2) with Kanna, the bot walks the blue-node rotation in file order, forever, through
orange nodes as needed, until stopped. It chooses between walk, teleport and drop, works out slopes and near-aligned
vertical moves from the node layout alone, re-plans from where the character actually is after every move, and logs
every leg and lap. Spec B's offline targets are met in a simulator that drives the real planner, control loop and
`InputLayer`; the character-detection target is met on real frames; the in-game sign-off is a supervised session.

**Architecture:** a new pure package `src/route/` sits between the map graph and Feature A's `InputLayer`:

```
CSV ──MapleAgent.loadMap──► PathGraph (unchanged) ──► terrain.infer_terrain ──► RouteGraph (typed, costed edges)
                                                                                     │
PositionSource.read() ──► locate.attach ──► planner.next_move ──► moves.execute ──► InputLayer ──► link
        ▲                                         (one move per decision; re-plan after each settle)   │
        └──────── RouteWalker (sense → plan → act → settle loop, stall/timeout, legs and laps) ◄──────┘
```

In tests and in the cloud the link is `SimLink` and the positions come from `SimWorld` (a hand-authored FF2 terrain
with injected errors), so the real `InputLayer`, action builders, planner and walker all run without the game. At
runtime (Windows only) the link is `BleLink`, positions come from `MinimapPositionSource` over the existing capture
thread, and a CLI wires them up. Nothing under `src/route/` except `harness/`, `runtime.py` and `walk.py` imports
`src.vision`, `src.gui` or Windows-only code.

**Tech stack:** Python 3.14.0 venv (as on master) · networkx 3.6.1 · numpy · returns · opencv-python (character
locator) · pytest. **No new dependency.**

**Binding inputs:** spec B `docs/superpowers/specs/2026-10-21-p1b-route-walking-design.md` (cited `specB:L<n>`);
`docs/vision.md`; `CLAUDE.md`; spec A "Timing model (as built)"; spec D goals 3-4 and its "Trail" row (the session log
B's leg records feed); `docs/decisions.md` H3 (reach unmeasured on FF2), H4 (merge only after in-game sign-off),
H8 (git actions, pushes), H9 (autonomy), H10 (Hong is asked only about spec deviations and human-only tasks); Feature A code as on `master` 2413c64.

---

## Hard rules for every task (read before starting any task)

1. **Branch.** All build work is on `p1b-route-walking`. Never commit to `master`; never merge, rebase or force-push.
   The orchestrator running the build pushes `p1b-route-walking` to `origin` after each task (specB:L72).
   B merges to `master` only after the in-game sign-off (task B21) passes (H4, specB:L72); no build task merges.
2. **No game input without Hong.** Only task B21 sends keys while MapleStory runs, and only with Hong present
   (specB:L41, "not run in the game while the user is away"). Tasks B17-B20 build and unit-test tools offline; any
   live smoke test of them sends keys only into Feature A's "Helix input test sink" window with MapleStory closed,
   and checks for a MapleStory process first and aborts if one runs (B19's link check does; PB-B19).
3. **Error handling.** Graph, terrain, planner and log code return `returns.result` `Success`/`Failure` and check
   with `is_successful` (`CLAUDE.md`, "Error handling"). Simulator internals may raise `ValueError` on programmer
   error (bad constructor arguments) but return `Result` from every function a task lists as returning one.
4. **Reach stays in one place.** Teleport reach is read from the `CharacterClass` (`src/classes/classes.py:47-48`)
   through `PathGraph.max_horizontal_traversal_dist` / `max_vertical_traversal_dist` (`path_graph.py:28-29`). No
   module under `src/route/` writes 19 or 23 as a literal. Tests may.
5. **Nothing FF2-only in runtime code** (specB:L35). FF2 appears only in test fixtures, the sim terrain fixture and
   CLI arguments.
6. **Imports.** Rooted at `src.`. Every module under `src/route/` (including `src/route/sim/`) imports only the
   standard library, numpy, networkx, returns, cv2, `src.input.{protocol,timing,actions,input_layer,link}`,
   `src.controller.*`, `src.classes.*` and other `src/route/` modules, **except** `src/route/harness/*`,
   `src/route/runtime.py` and `src/route/walk.py`, which may import `src.vision` and Windows APIs.
7. **Size.** A task changes at most about three files of real code; tests and fixtures do not count.
8. **Tests.** New test directories `tests/controller/` and `tests/route/`, no `__init__.py` (as `tests/input/`).
   Test file basenames must be unique across `tests/` (pytest's default rootdir import mode fails on duplicates).
9. **Commands.** From the repo root with the venv active. Single file: `python -m pytest -q <file>`. Full suite on
   Windows: `python -m pytest -q` → every test passes except the 10 board-only skips in
   `tests/input/test_protocol.py`. Full suite on Linux (cloud): `python -m pytest -q --continue-on-collection-errors`
   → the same, plus exactly the 9 `tests/input/` harness files that cannot be collected on Linux (`test_analyze`,
   `test_faults`, `test_g4_check`, `test_listener`, `test_probe`, `test_realism`, `test_session_starts`, `test_soak`,
   `test_soak_v2_faults`), plus skips of tests marked `pytest.mark.skipif(sys.platform != "win32", ...)`; any other
   error is a failure. So that this holds, the Windows-only tools of B17-B20 (`src/route/harness/*`,
   `src/route/runtime.py`, `src/route/walk.py`) import `src.vision`, `ctypes.WinDLL`/`ctypes.windll` and
   `ctypes.WINFUNCTYPE` **inside functions only**, never at module level, so their test files collect on Linux; a
   test that needs Windows is marked with that `skipif`.
10. **Commits.** Conventional with a scope, e.g. `test(controller): ...`, `feat(route): ...`. One commit per task
    unless the task says otherwise.

### The TDD steps every task follows

Each task lists its tests under **Tests** and its command under **Run**. The steps are:
- [ ] **T1** write the tests; **T2** run them, see them fail for the right reason (import error or assertion, not a
  typo); **T3** implement; **T4** run them, all pass; **T5** run the full suite (hard rule 9); **T6** commit with the
  task's message. Characterization tasks (B1, B3) pin current behaviour, so their T2 is "they pass on the unchanged
  code"; a failure there means the test is wrong, not the code.

---

## Grounding (current code, read 2026-10-23 at 2413c64)

| # | Claim | Where |
|---|---|---|
| G1 | `InputLayer(link: BleLink, model: TimingModel)` | `src/input/input_layer.py:38-41` |
| G2 | `tap(key, stimulus_ns=None)`, `walk(direction, intended_ms, stimulus_ns=None)`, `teleport(direction, stimulus_ns=None)`, `drop(stimulus_ns=None)`, `jump(stimulus_ns=None)`, `sequence(keys, stimulus_ns=None)`, each `-> Result[ActionRecord, InputError]`; `release_all() -> Result[SendReport, InputError]` | `input_layer.py:43-66` |
| G3 | A builder Failure in `walk`/`sequence` becomes `Failure(InputError.TOO_LONG)` before any wait | `input_layer.py:46-49, 68-71` |
| G4 | `ActionRecord(plan, reaction, waited_ms, report)` | `input_layer.py:30-35` |
| G5 | The stimulus clock is `time.perf_counter_ns`; reaction wait = `max(0, R - elapsed)` | `input_layer.py:22, 73-82` |
| G6 | `release_all()` takes no lock and may pre-empt a `play()` in flight from another thread | `input_layer.py:1-6, 65-66` |
| G7 | After `NOTIFY_FAILED`/`ACK_TIMEOUT`/`WRITE_FAILED` the layer sends one `release_all()` | `input_layer.py:27, 86-92` |
| G8 | `InputError` members: `NOT_CONNECTED, WRITE_FAILED, REJECTED, NOTIFY_FAILED, ABORTED, ACK_TIMEOUT, LINK_LOST, TOO_LONG` | `src/input/link.py:44-54` |
| G9 | `SendReport(seq, sent_ns, written_ns, accepted_ns, done_ns, accepted, done)` | `link.py:57-65` |
| G10 | `BleLink(name="Guiness", ...)`; `connect(timeout_s=30.0) -> Result[SessionInfo, str]`; `play(steps)`; `release_all()`; `close()` | `link.py:116-117, 140-158` |
| G11 | `Key` IntFlag: `LEFT=1 RIGHT=2 UP=4 DOWN=8 E=16 ALT=32 SPACE=64`; `Step(delta_100us, mask)`; `Done(seq, status, fail_step, rc, t0_us, late_us)` | `src/input/protocol.py:30-37, 40-43, 52-59` |
| G12 | A PLAY is ≤16 steps and ≤10 s total (`MAX_TOTAL_DELTA = 100_000`) | `protocol.py:17, 19, 82-104` |
| G13 | Teleport = arrow down, then `E`; drop = `Down`, then `Alt`; walk = one arrow held for a `WALK` draw | `src/input/actions.py:109-114, 117-134` |
| G14 | `walk` fails for `intended_ms > 13_107.1`; a `WALK` draw is `T + Normal(0, k·T² + σ_i²)`, √k 0.04, σ_i 65, ≥ T/2 | `actions.py:19, 117-124`; `src/input/timing.py:70` (`MODEL[WALK]`) |
| G15 | `TimingModel(rng: np.random.Generator)` | `timing.py:86-87` |
| G16 | Test pattern: `FakeLink` with `play(steps)`/`release_all()` returning `Result`s; `_report(seq)` builds a `SendReport` | `tests/input/test_input_layer.py:56-80` |
| G17 | `PathGraph(node_map, edge_y_alignment_allowance, edge_x_alignment_allowance, max_horizontal_traversal_dist, max_vertical_traversal_dist)`; `graph` is a `networkx.MultiDiGraph` of `Node` objects | `src/controller/path_graph.py:23-30` |
| G18 | `buildGraph() -> Result[None, str]` adds nodes then edges left, right, up, down | `path_graph.py:158-167, 210-216` |
| G19 | Closest-node search uses strict `>`, so ties go to the earlier node in `node_map` | `path_graph.py:260, 304, 348, 401` |
| G20 | `_isVerticalEdgeValid(curr, target)` is a `@staticmethod` (platform-edge rules) | `path_graph.py:405-433` |
| G21 | `getPathToGoalNode(char, goal) -> Path` returns an empty `Path` when `nx.NetworkXNoPath` is raised (it prints only when a path is found, and lets `NodeNotFound` through); `_convertToPolylinePath` is a `@staticmethod` taking `self` explicitly | `path_graph.py:33-71, 73-74` |
| G22 | `MapleAgent(sharedInfo=None, CSV=None, characterClass=None)`; visit radius 2, edge tolerance 2 | `src/controller/controller.py:76, 83-86` |
| G23 | `loadMap(csv_path) -> Result[BuildOutcome, str]`; fills `node_map` and `map_rotation_order` (blue nodes in file order) | `controller.py:116-136`; `src/controller/csv_parser.py:38-41` |
| G24 | `buildPathGraphFor(node_map) -> Result[PathGraph, str]`, the only `PathGraph` construction site | `controller.py:237-256` |
| G25 | `executePathFinding` is commented out and uses a stale API | `controller.py:172-211` |
| G26 | `getCharacterCoords` converts with `COLOR_BGR2GRAY` although the shared frame is RGB | `controller.py:218`; `src/vision/vision.py:81` |
| G27 | `CharacterClass` ABC; `Kanna`: `movement_type="teleport"`, reach 19 / 23 | `src/classes/classes.py:4, 41-48` |
| G28 | `Node(nodetype, minimap_pos, can_fall, fall_dist_exceeds_vertical_tp, is_left_platform_edge, is_right_platform_edge)`; `uuid` from a global counter | `src/controller/node.py:10-29, 51-63` |
| G29 | `CSVParser` skips `#` lines; `read_editor_nodes(path) -> Result[(nodes, rotation_order, metadata), str]`, metadata `minimap_size` as `(W, H)` | `csv_parser.py:24-27`; `src/controller/map_csv.py:52, 188-237` |
| G30 | `SharedInfo` has `minimap_frame_lock` and `minimap_frame`; vision assigns a new RGB array each frame | `src/main.py:8-11`; `vision.py:80-81` |
| G31 | `vision.py` imports `bettercam`; `vision_helper_lib.py` imports only cv2, numpy, enum | `vision.py:1`; `vision_helper_lib.py:1-3` |
| G32 | `assets/chr/chr.png` and `chr_mask.png` are 10×10 | PNG headers, read 2026-10-23 |
| G33 | The listener's `WH_KEYBOARD_LL` hook pattern (hook thread with message pump, `return 1` blocks a key) | `src/input/harness/listener.py:40, 265-286` |
| G34 | `src/input/` has an `__init__.py`; `src/`, `src/controller/`, `src/classes/` and `src/vision/` do not | `ls`, 2026-10-23 |

### Measured at planning (Linux, Python 3.14.0, FF2 from Appendix A loaded through `MapleAgent(characterClass=Kanna()).loadMap`)

```
loadMap: <Success: BuildOutcome.BUILT>
nodes 21 edges 40
(113, 60) -> (82, 60): has_path=True [(113, 60), (105, 60), (99, 60), (82, 60)]
(82, 60) -> (48, 61): has_path=True [(82, 60), (65, 60), (48, 61)]
(48, 61) -> (58, 46): has_path=True [(48, 61), (48, 45), (58, 46)]
(58, 46) -> (49, 28): has_path=True [(58, 46), (48, 45), (49, 28)]
(49, 28) -> (101, 24): has_path=False
(101, 24) -> (105, 42): has_path=False
(105, 42) -> (113, 60): has_path=True [(105, 42), (105, 60), (113, 60)]
```

Both of spec B's no-path legs (specB:L25-L26) have no path today. The 40 PathGraph edges are listed in task B3. A
prototype of contract C4's rules on the same nodes gives the 11 extra links, 4 platforms and 51-edge route graph
quoted in tasks B4 and B5, and every rotation leg then has a path. The suite on this branch's base gives
`355 passed, 10 skipped, 9 errors` on Linux.

---

## Design choices (spec B "Left to the builder", specB:L74-L79)

| # | Choice | Reason | Cost to reverse |
|---|---|---|---|
| DC1 | Terrain is inferred in a new layer (`src/route/terrain.py` + `RouteGraph`) composed on top of an unchanged `PathGraph` | `PathGraph` keeps its characterized behaviour and its editor preview; inference is testable alone | Moving the rules into `PathGraph` later: one task, plus editor preview tests (see PB-D1) |
| DC2 | Slope = a horizontal neighbour with 2 < \|dy\| ≤ 6 and \|dy\| ≤ 0.5·\|dx\|, within horizontal reach | FF2's slope is dy 4 over dx 13 (grade 0.31); 6/0.5 leave margin without linking steps | Thresholds are `MovementParams` fields |
| DC3 | Near-aligned vertical = nearest node above/below within \|dx\| ≤ 5, not blocked by another platform in between | FF2 needs \|dx\| = 4; the "blocked" rule removes a drop that would skip a platform (`(66,28)→(65,60)` in the prototype) | `near_x_px` field; the blocked rule is one function |
| DC4 | Control loop: closed-loop lock-step, one move per decision, re-plan from the measured position after every settle | Recovery (goal 4) follows for free; matches Helix's earlier "strict lock-step with on-screen confirmation" | `RouteWalker` only |
| DC5 | Tolerances: arrive ±2 px (as `MapleAgent` visit radius, G22); attach ±3 px in y; stall = no new best cost-to-go for > 10 s (specB:L64) | Reuse existing numbers where they exist | `MovementParams` fields |
| DC6 | Ability choice by estimated time: walk vs teleport by the formula in C7; teleport-down vs drop by cost with a random mix band (PB-D3) | Spec leaves the choice to the bot (specB:L16) | `planner.py` only |
| DC7 | Simulator drives the real `InputLayer` through a duck-typed `SimLink` that decodes key schedules into moves | Exercises Feature A's builders and timing draws exactly as at runtime | `src/route/sim/` only |
| DC8 | Simulator's FF2 terrain is hand-authored from the node layout (fixture `tests/fixtures/ff2_terrain.json`, given in B9) | No platform data exists; nodes and spec B's statements are the only source | Fixture edit; B21 checks it against recorded frames |
| DC9 | On `LINK_LOST`/`NOT_CONNECTED` the walker ends the run (outcome `link_down`); any other input failure counts as a failed move and the walker re-plans | No reconnect in Phase 1 (spec B non-goals, specB:L34) | `RouteWalker` only |
| DC10 | The run starts with an approach leg from wherever the character is to the first blue node, then cycles laps | Simple and predictable; specB:L11 fixes the order, not the start | `RouteWalker.run` only |
| DC11 | Runtime walk and measurement are CLIs, not GUI buttons | No GUI change (nothing the user sees changes outside the CLI) | One later GUI task |

Spec B says Feature C's `MinimapCapture` should be used "if it lands first" (specB:L47). C comes after B, so B uses
the existing capture thread (`vision.py:20`).

---

## Contracts (the interfaces every task builds against)

### C1. Existing interfaces B uses (not changed by this plan)

Everything in the Grounding table G1-G24, G27-G30. B does **not** change any `src/input/` signature, the wire
protocol, the map CSV format, `PathGraph`, `MapleAgent` or `CharacterClass`. `InputLayer` is constructed with a
duck-typed link in the simulator, as Feature A's own tests do with `FakeLink` (G16); that is not an interface change.

### C2. Module layout

```
src/route/__init__.py            empty
src/route/params.py              MovementParams, validate            (B2)
src/route/terrain.py             LinkKind, Link, Terrain, infer_terrain (B4)
src/route/route_graph.py         EdgeKind, RouteGraph, horizontal_cost_s, down_options (B5)
src/route/locate.py              Anchor, attach                       (B6)
src/route/planner.py             MoveKind, Move, Decision, next_move  (B7)
src/route/moves.py               execute                              (B8)
src/route/sim/__init__.py        empty
src/route/sim/world.py           SimTerrain, load_terrain, SimParams, NoiseConfig, InjectedError, MoveEffect, SimWorld (B9)
src/route/sim/link.py            SimClock, SimLink, SimPositionSource (B10)
src/route/leg_log.py             LegOutcome, MoveEntry, LegRecord, LapRecord, LegSink, MemorySink, JsonlLegLog (B11)
src/route/walker.py              Clock, PositionSource, RealClock, WalkerConfig, RunSummary, RouteWalker (B12, B13)
src/route/sim/run.py             SimReport, Check, run_sim, judge, main (B14)
src/route/character.py           CharacterLocator                     (B15)
src/route/position.py            MinimapPositionSource                (B16)
src/route/harness/__init__.py    empty
src/route/harness/record_minimap.py  (B17)   src/route/harness/detection_check.py (B18)
src/route/runtime.py             wiring + StopControl (B19)           src/route/walk.py  CLI (B19)
src/route/harness/measure_moves.py   (B20)
tests/fixtures/fossil_foundry_2.csv  (B1)    tests/fixtures/ff2_terrain.json (B9)
```

### C3. `MovementParams` (`src/route/params.py`)

```python
@dataclass(frozen=True)
class MovementParams:
    arrive_px: int = 2             # leg target reached when |dx| <= 2 and |dy| <= 2 (MapleAgent visit radius, G22)
    slope_max_dy_px: int = 6       # DC2
    slope_max_grade: float = 0.5   # DC2: |dy| <= grade * |dx|
    near_x_px: int = 5             # DC3
    attach_y_px: int = 3           # DC5
    walk_px_per_s: float = 8.0     # ESTIMATE, unmeasured; B20/B21 measure it
    teleport_s: float = 0.55       # ESTIMATE: keys + animation of one teleport
    drop_base_s: float = 0.35      # ESTIMATE: keys + start of a drop
    fall_px_per_s: float = 60.0    # ESTIMATE
    max_walk_ms: float = 4000.0    # one walk is at most this; longer distances are re-planned
    ability_mix_band: float = 0.3  # PB-D3; 0 makes the down choice deterministic
    progress_eps_s: float = 0.05   # a new best cost-to-go must beat the old by more than this
    stall_s: float = 10.0          # specB:L64
    poll_s: float = 0.05           # position poll interval while settling or waiting
    min_settle_s: float = 0.15     # wait at least this after a move before settling
    settle_timeout_s: float = 2.0

def validate(p: MovementParams) -> Result[MovementParams, str]
```

`validate` returns `Success(p)` when: the four `int` fields are ≥ 1; `0 < slope_max_grade ≤ 1`; every `float` field
is finite and > 0 except `ability_mix_band`, which is finite and ≥ 0; `max_walk_ms ≤ 5000` (a `WALK` draw at
T = 5000 has SD √(0.0016·T² + 65²) ≈ 210 ms, far below the 6 553.5 ms per-step cap, G12/G14);
`min_settle_s < settle_timeout_s`. Otherwise `Failure` whose message contains the offending field's name.

### C4. Terrain inference (`src/route/terrain.py`)

```python
class LinkKind(StrEnum):
    LEVEL = "level"; SLOPE = "slope"; UP = "up"; DOWN = "down"

@dataclass(frozen=True)
class Link:
    src: Node
    dst: Node
    kind: LinkKind

@dataclass(frozen=True)
class Terrain:
    links: tuple[Link, ...]
    platforms: tuple[tuple[Node, ...], ...]
    def platform_index(self, node: Node) -> int        # raises KeyError for a node not in any platform

def infer_terrain(nodes: Sequence[Node], align_px: int, reach_h: int, reach_v: int,
                  params: MovementParams) -> Result[Terrain, str]
```

`nodes` is in `node_map` order; "order" below means index in `nodes`. `Failure` if `nodes` is empty or any
`minimap_position` is not a 2-tuple of ints. Positions are minimap pixels, top-left origin (y grows downward).

- **R1 walk links.** For each node A (in order) and each side s ∈ (−1, +1) (left first): candidates are B ≠ A with
  s·(Bx − Ax) > 0, |Bx − Ax| ≤ `reach_h`, and walkable: |dy| ≤ `align_px`, or (|dy| ≤ `slope_max_dy_px` and
  |dy| ≤ `slope_max_grade`·|dx|). Pick the smallest |dx| (ties: earliest in order). Emit `Link(A, B, LEVEL)` if
  |dy| ≤ `align_px`, else `Link(A, B, SLOPE)`.
- **R2 platforms.** Connected components of the undirected graph of R1 links (a node with no R1 link is a platform of
  one). Each platform is a tuple sorted by (x, order); platforms are ordered by the order of their earliest member.
- **R3 vertical links.** For each node A (in order), up then down: candidates B ≠ A with |Bx − Ax| ≤ `near_x_px` and,
  for up, By < Ay and |dy| ≤ `reach_v`; for down, By > Ay and (|dy| ≤ `reach_v` or `A.can_fall`). Pick the smallest
  |dy|, then the smallest |dx|, then earliest in order. Reject it if B is on A's platform; or if it is *blocked*: some
  consecutive pair (P, Q) of a third platform has Px ≤ Ax ≤ Qx and the linear interpolation of y at Ax lies strictly
  between Ay and By; or if `PathGraph._isVerticalEdgeValid(A, B)` (G20) is false. Otherwise emit `Link(A, B, UP)` or
  `Link(A, B, DOWN)`. A DOWN link with |dy| > `reach_v` is drop-only.
- `links` = all R1 links in emission order, then all R3 links in emission order.

### C5. Route graph (`src/route/route_graph.py`)

```python
class EdgeKind(StrEnum):
    WALK = "walk"; SLOPE = "slope"; UP = "up"; DOWN = "down"

def horizontal_cost_s(dx_px: float, reach_h: int, p: MovementParams) -> float
def down_options(dy_px: float, src_can_fall: bool, reach_v: int, p: MovementParams) -> dict[str, float]

class RouteGraph:
    graph: nx.DiGraph            # nodes: the Node objects of path_graph.node_map; edge attrs kind: EdgeKind, cost_s: float
    terrain: Terrain
    align_px: int; reach_h: int; reach_v: int
    params: MovementParams
    @classmethod
    def build(cls, path_graph: PathGraph, params: MovementParams) -> Result["RouteGraph", str]
    def path(self, src: Node, dst: Node) -> Result[tuple[Node, ...], str]   # min total cost_s; includes src and dst
    def cost_to(self, src: Node, dst: Node) -> Result[float, str]
```

- `horizontal_cost_s(d)`: with d = |dx|, n = ⌊d / reach_h⌋, r = d − n·reach_h, V = `walk_px_per_s`,
  T = `teleport_s`: `min(d/V, n·T + r/V, (n+1)·T + ((n+1)·reach_h − d)/V)`. With defaults and reach 19:
  d=4 → 0.5, d=13 → 1.3, d=17 → 0.8, d=19 → 0.55, d=23 → 1.05, d=30 → 1.925, d=40 → 1.35.
- `down_options(dy, can_fall)`: `{"teleport": teleport_s}` if dy ≤ reach_v; plus `{"drop": drop_base_s + dy/fall_px_per_s}`
  if `can_fall`; may be empty.
- `build`: `Failure` if `path_graph.graph` has no nodes (graph not built) or its node set differs from
  `path_graph.node_map.values()`. Reads `align_px = path_graph.edge_y_alignment_allowance`, `reach_h`, `reach_v`
  from the PathGraph (G17). Runs `infer_terrain(list(path_graph.node_map.values()), ...)`. Adds every PathGraph edge
  (u, v) first, kind `WALK` if |dx| ≥ |dy| else `UP` (dy < 0) / `DOWN` (dy > 0); then every terrain link whose (u, v)
  is not already present, `LEVEL→WALK`, `SLOPE→SLOPE`, `UP→UP`, `DOWN→DOWN`. `cost_s`: WALK `horizontal_cost_s(|dx|)`;
  SLOPE |dx|/V; UP T + |dx|/V; DOWN `min(down_options(|dy|, u.can_fall))` + |dx|/V, and a DOWN edge with no option is
  not added. `path`/`cost_to`: `Failure("no path ...")` when none; nodes not in the graph are a `Failure` too.

### C6. Localisation (`src/route/locate.py`)

```python
@dataclass(frozen=True)
class Anchor:
    node: Node | None        # the character is at this node
    platform: int | None     # index into terrain.platforms
    left: Node | None        # segment ends (None beyond a platform end)
    right: Node | None

def attach(rg: RouteGraph, pos: tuple[int, int]) -> Result[Anchor, str]
```

- **A1** If some node has |px − nx| ≤ `rg.align_px` and |py − ny| ≤ `rg.align_px`: `Anchor(node=nearest by Euclidean
  distance (ties: earliest in node_map order), platform=its platform, left=None, right=None)`.
- **A2** Else, for each platform and each consecutive pair (L, R) of it with Lx ≤ px ≤ Rx and |py − y_interp(px)| ≤
  `attach_y_px`: candidate. Pick the smallest |py − y_interp|, ties the lower platform index, then the leftmost segment:
  `Anchor(None, platform, L, R)`.
- **A3** Else, beyond a platform end: the first node F of a platform with 0 < Fx − px ≤ `rg.reach_h` and |py − Fy| ≤
  `attach_y_px` gives `Anchor(None, platform, None, F)`; the last node Z with 0 < px − Zx ≤ `rg.reach_h` and |py − Zy| ≤ `attach_y_px` gives
  `Anchor(None, platform, Z, None)`. Smallest |dx| wins; ties the lower platform index.
- **A4** Else `Failure("off-graph: (x, y)")`.

### C7. Planner (`src/route/planner.py`)

```python
class MoveKind(StrEnum):
    WALK = "walk"; TELEPORT = "teleport"; DROP = "drop"

@dataclass(frozen=True)
class Move:
    kind: MoveKind
    direction: Key | None      # Key.LEFT/RIGHT for WALK; LEFT/RIGHT/UP/DOWN for TELEPORT; None for DROP
    walk_ms: float | None      # only for WALK

@dataclass(frozen=True)
class Decision:
    move: Move | None          # None: arrived
    cost_to_go_s: float        # 0.0 when arrived
    toward: Node | None        # the node this move heads for; None when arrived

def next_move(rg: RouteGraph, pos: tuple[int, int], target: Node,
              rng: np.random.Generator) -> Result[Decision, str]
```

- **"At" a node.** The character is *at* node n when |px − nx| ≤ `arrive_px` and either |py − ny| ≤ `arrive_px` or
  `attach(rg, pos)` succeeds with `platform == rg.terrain.platform_index(n)`. (Hand-placed nodes may sit a few px
  above or below the surface the character stands on; the platform test accepts that.)
- **P1** Arrived when the character is at `target`: `Success(Decision(None, 0.0, None))`.
- **P2** `attach(rg, pos)`; its `Failure` is returned unchanged.
- **P3 start.** With `anchor.node`: starts = [(node, 0.0)]. Otherwise starts = each of `left`, `right` that is not
  None with its approach cost: |px − ex|/V if left and right are joined by a SLOPE edge (either direction), else
  `horizontal_cost_s(|px − ex|)`. For each start, total = approach + `rg.cost_to(start, target)` (skip Failures).
  None left → `Failure("no path from (x, y) to (tx, ty)")`. Pick the smallest total, ties left before right.
  `cost_to_go_s` = that total.
- **P4 horizontal run.** Let `path = rg.path(start, target)`. If the anchor is on a slope segment (`left` and
  `right` joined by a SLOPE edge in either direction), the goal node g is the start itself. Otherwise g = start, then
  g advances along the path while the next edge is `WALK`. If the character is not at g, move toward g with d = |gx − px|, direction `Key.RIGHT` if gx > px else `Key.LEFT`:
  - on a slope segment: `WALK`, `walk_ms = min(1000·d/V, max_walk_ms)`;
  - else `TELEPORT` if T + |d − reach_h|/V < d/V (strict), else `WALK` with `walk_ms = min(1000·d/V, max_walk_ms)`.
    With defaults: d = 13 → TELEPORT, d = 11 → WALK, d = 4 → WALK, d = 30 → TELEPORT. Known behaviour: the teleport
    rule ignores platform ends, so a teleport toward an end node can overshoot it by up to `reach_h − d` px (≤ 7 with
    defaults) and fall off; the walker then re-plans from where the character lands (goal 4). B14's `laps`, `stalls` and
    `recovery` checks cover it; it is not special-cased or counted separately.
- **P5 at g** (and g ≠ target, else P1 applied). The next path edge (g, h): `SLOPE` → `WALK` toward h, `walk_ms = min(1000·|hx − px|/V, max_walk_ms)`;
  `WALK` (reached only when P4 kept g at the start of a slope segment and the character counts as at g) → let g′ = h,
  then g′ advances along the path while the next edge is `WALK`; move toward g′ by P4's off-slope rule (`TELEPORT` if
  T + |d − reach_h|/V < d/V, else `WALK` with `walk_ms = min(1000·d/V, max_walk_ms)`, d = |g′x − px|);
  `UP` → `TELEPORT` `Key.UP`; `DOWN` → options = `down_options(|hy − gy|, g.can_fall, rg.reach_v, rg.params)`: one option → that
  one; two → let c_lo ≤ c_hi; if c_hi ≤ c_lo·(1 + `ability_mix_band`), `DROP` iff `rng.random() < 0.5`, else
  `TELEPORT` `Key.DOWN`; otherwise the cheaper (ties `TELEPORT`). `TELEPORT DOWN` is `Move(TELEPORT, Key.DOWN, None)`;
  `DROP` is `Move(DROP, None, None)`. `rng` is used only in this two-option, in-band case.
- `toward` = g for a P4 move, h for a P5 move, g′ for a P5 `WALK` move.

### C8. Executing a move (`src/route/moves.py`)

```python
def execute(layer: InputLayer, move: Move, stimulus_ns: int | None) -> Result[ActionRecord, InputError]
```

WALK → `layer.walk(move.direction, move.walk_ms, stimulus_ns)`; TELEPORT → `layer.teleport(move.direction,
stimulus_ns)`; DROP → `layer.drop(stimulus_ns)`. Exactly one layer call; its `Result` is returned unchanged.

### C9. Leg and lap log (`src/route/leg_log.py`) — **interface** for Feature D's session log (spec D goal 4)

```python
class LegOutcome(StrEnum):
    REACHED = "reached"; STOPPED = "stopped"; TIMED_OUT = "timed_out"; LINK_DOWN = "link_down"

@dataclass(frozen=True)
class MoveEntry:
    t_ns: int; pos: tuple[int, int]; kind: str; direction: str | None; walk_ms: float | None   # direction = Key.name, e.g. "RIGHT"
    cost_to_go_s: float; input: str            # "ok" or the InputError name
    settled: tuple[int, int] | None

@dataclass(frozen=True)
class LegRecord:
    lap: int; leg: int; src: tuple[int, int] | None; dst: tuple[int, int]
    start_ns: int; end_ns: int; outcome: LegOutcome
    moves: tuple[MoveEntry, ...]; misses: int; off_graph: int; stalls: int

@dataclass(frozen=True)
class LapRecord:
    lap: int; start_ns: int; end_ns: int; legs: int; all_reached: bool

class LegSink(Protocol):
    def write_leg(self, rec: LegRecord) -> Result[None, str]: ...
    def write_lap(self, rec: LapRecord) -> Result[None, str]: ...

class MemorySink:                   # keeps lists .legs and .laps; never fails
class JsonlLegLog:
    def __init__(self, directory: Path, fsync: bool = True) -> None
    @classmethod
    def open(cls, directory: Path, fsync: bool = True) -> Result["JsonlLegLog", str]
    def write_leg(self, rec: LegRecord) -> Result[None, str]
    def write_lap(self, rec: LapRecord) -> Result[None, str]
    def close(self) -> None

def leg_to_json(rec: LegRecord) -> dict
def lap_to_json(rec: LapRecord) -> dict
```

JSONL, UTF-8, `\n` line ends, one object per line, appended (never truncated):
- `legs.jsonl`: `{"type": "leg", "lap", "leg", "from": [x, y] | null, "to": [x, y], "start_ns", "end_ns",
  "outcome", "moves": [{"t_ns", "pos": [x, y], "kind", "dir", "walk_ms", "cost_to_go_s", "input", "settled": [x, y] |
  null}], "misses", "off_graph", "stalls"}`
- `laps.jsonl`: `{"type": "lap", "lap", "start_ns", "end_ns", "duration_s", "legs", "all_reached"}` with
  `duration_s = (end_ns − start_ns) / 1e9`.

Numbering: lap 0 holds only the approach leg (leg 0, `from` null). Laps 1, 2, … each have `n` legs (n = number of blue
nodes), leg i going rotation[i] → rotation[(i + 1) mod n]. Each record is written with one `write` call, then
`flush()`, then `os.fsync()` when `fsync` is true, so a crash loses at most the record being written.

### C10. Walker (`src/route/walker.py`)

```python
class Clock(Protocol):
    def now_ns(self) -> int: ...
    def sleep(self, seconds: float) -> None: ...

class PositionSource(Protocol):
    def read(self) -> Result[tuple[int, int], str]: ...   # Failure("stale") = no new frame; any other Failure = miss

class RealClock:                    # now_ns = time.perf_counter_ns (same clock as InputLayer, G5); sleep = time.sleep

STALE = "stale"

@dataclass(frozen=True)
class WalkerConfig:
    use_stimulus: bool = True       # pass stimulus_ns to InputLayer (runtime); the simulator uses False
    leg_timeout_s: float | None = None
    max_laps: int | None = None

@dataclass(frozen=True)
class RunSummary:
    laps_completed: int; legs: int; reason: str     # "max_laps" | "stopped" | "link_down"

class RouteWalker:
    def __init__(self, rg: RouteGraph, rotation: Sequence[Node], layer: InputLayer, positions: PositionSource,
                 clock: Clock, sink: LegSink, rng: np.random.Generator, config: WalkerConfig = WalkerConfig()) -> None
    def walk_leg(self, lap: int, leg: int, src: Node | None, dst: Node) -> LegRecord
    def run(self) -> Result[RunSummary, str]
    def stop_now(self) -> None      # thread-safe; see B13 (stop flag, release_all, stop guard)
```

### C11. Simulator (`src/route/sim/`)

Terrain fixture format (JSON): `{"minimap_size": [W, H], "platforms": [{"name": str, "droppable": bool,
"points": [[x, y], ...]}]}`, points sorted by strictly increasing x. Full signatures are in tasks B9, B10 and B14.

---

## Coverage: spec B → tasks

| Spec B item | Met by | Criterion that can fail |
|---|---|---|
| Goal 1: cycle blue nodes in file order forever through orange nodes, until stopped (L11) | B5, B13, B14, B21 | B13: leg i targets rotation[(i+1) mod n]; B14: 1000 laps; B21: 10 laps in game |
| Goal 2: teleport/walk/drop and choose (L12-L16) | B7, B8, B9, B10, B14 | B7: formula cases; B14: report counts drops > 0 and teleport-downs > 0 |
| Goal 2: reach single value, measured on FF2 (L13) | hard rule 4, B20, B21 | B21 step 3: mean measured reach vs 19/23 |
| Goal 3: terrain from the node layout (L17) | B4, B5, B6 | B4: exact FF2 links; B5: all 7 legs have a path |
| Goal 4: recover, re-plan from the actual position (L18) | B7, B12, B14 | B12: re-plans after each move; B14: recovery ≥ 99% (single- and multi-error legs) and node-offset runs |
| Goal 5: lap time and per-leg outcomes logged (L19) | B11, B13, B19 | B11 format tests; B13 lap records; B19 writes them at runtime |
| Known case `(49,28)→(101,24)` slope (L25) | B3 (no path today), B4, B5, B14 | B5: path uses the SLOPE edge; B14: reached every lap |
| Known case `(101,24)→(105,42)` 4 px offset (L26) | B3, B4, B5, B14 | B5: DOWN edge exists; B14: reached every lap |
| Target: 1000 simulated laps, 0 stalls, noise on (L64) | B14 | `judge` checks laps, stalls, no-op rate ≥ 5% |
| Target: known cases every lap (L65) | B14 | `judge` "known cases" check |
| Target: recovery within 3 extra moves ≥ 99% (L66) | B14 | `judge` "recovery" check |
| Target: detection ±1 px ≥ 99.5%, never > 3 px (L67) | B15 (synthetic), B18 (real frames), B21 (moving frames) | B18/B21: `detection_check` PASS |
| Target: in-game 10 consecutive laps (L68) | B21 | Hong's sign-off |
| Verification: offline simulator (L54) | B9, B10, B14 | as above |
| Verification: real FF2 frames, detection and sim realism (L55) | B17, B18, B20, B21 | B21 step 5 compares measured movement to `SimParams` |
| Verification: supervised in-game (L56) | B21 | — |
| Shipping: merge only after sign-off (L72) | hard rule 1, B21 | — |

No target is left without a task. The only target that cannot be met in the cloud is detection on real frames and
everything in game (B17-B21).

---

## Task list

| # | Task | Class | Cloud | Depends on |
|---|---|---|---|---|
| B1 | FF2 fixture + parser characterization tests | M | yes | — |
| B2 | `MovementParams` | M | yes | — |
| B3 | `PathGraph` characterization tests, both known cases as "no path today" | M | yes | B1 |
| B4 | Terrain inference | O | yes | B1, B2 |
| B5 | `RouteGraph` | O | yes | B1, B2, B4 |
| B6 | Localisation (`attach`) | O | yes | B5 |
| B7 | Planner and ability choice (`next_move`) | O | yes | B6 |
| B8 | Move executor | M | yes | B7 |
| B9 | Simulator world: terrain, movement physics, injected errors | S | yes | B2 |
| B10 | Simulator link, clock and position source | S | yes | B9 |
| B11 | Leg and lap log | S | yes | B2 |
| B12 | Walker: one leg (sense, plan, act, settle, stall, errors) | S | yes | B8, B10, B11 |
| B13 | Walker: rotation, laps, stop | S | yes | B12 |
| B14 | Simulator evaluation and the 1000-lap run | S | yes | B13 |
| B15 | Character locator | O | yes | B2 |
| B16 | Minimap position source | S | yes | B12, B15 |
| B17 | Minimap recorder | S | no | B16 |
| B18 | Detection check on real frames (labels drafted by Claude, checked by Hong) | O | no | B15, B17 |
| B19 | Runtime wiring and the walk CLI | S | no | B13, B16, B17 |
| B20 | Movement measurement tool | S | no | B19 |
| B21 | Supervised in-game session: reach measurement, sim realism, sign-off (human-only) | S | no | B14, B18, B19, B20 |
| B22 | Docs: CLAUDE.md, spec B "as built" | M | no | B21 |

**Counts:** M 5 (B1, B2, B3, B8, B22) · O 6 (B4, B5, B6, B7, B15, B18) · S 11 (B9-B14, B16, B17, B19, B20,
B21). Cloud yes 16, Cloud no 6. **Shortfall: M has 5 tasks, one short of 6;** the work does not split into another
natural mechanical unit, so none was padded in. In plan order the ladder's stage 1 takes M: B1, B2; O: B4, B5; S: B9,
B10 (all Cloud yes; every dependency of a stage-1 unit is itself a stage-1 unit or B1). Stage 2 takes M: B3, B8, B22
(**only 3 left**; B22 is Cloud no); O: B6, B7, B15, B18 (B18 is Cloud no); S: B11, B12, B13, B14. B5 lists no
dependency on B3: B3 only adds tests that pin `PathGraph`, which B5 reads but does not change; plan order still puts
B3 first.

---

### Task B1: FF2 fixture + parser characterization tests

**Files:** create `tests/fixtures/fossil_foundry_2.csv`; tests `tests/controller/test_csv_parser_ff2.py`.
**Class:** M — a fixture and tests of existing behaviour, one area, no design. **Cloud:** yes — tests import only
`src.controller.{csv_parser,map_csv,node,controller}` and `src.classes`.
**Interface created:** the fixture file (used by B3, B4, B5, B12-B14). No code.

- [ ] **Fixture.** Copy Appendix A below exactly (23 lines: the `# minimap_size=162,82` line, the header,
  21 rows; LF line ends; final newline) to `tests/fixtures/fossil_foundry_2.csv`.
- [ ] **Tests** (all pass on unchanged code):
  1. `CSVParser(CSV=Path(fixture)).parseCSV(rot, nm)` returns `Success(None)`; `len(nm) == 21`; `len(rot) == 7`;
     the rotation positions in order are `(113,60), (82,60), (48,61), (58,46), (49,28), (101,24), (105,42)`.
  2. `can_fall` is `True` for exactly these 13 positions: `(58,46) (49,28) (101,24) (105,42) (48,45) (74,45) (42,28)
     (66,28) (79,24) (90,24) (109,23) (118,43) (97,43)`, and `False` for the other 8: `(113,60) (82,60) (48,61)
     (65,60) (99,60) (129,60) (37,60) (105,60)`. The three other flags are `False` on all 21 nodes.
  3. `read_editor_nodes(str(fixture))` returns `Success`; 21 nodes; rotation order `[1, 2, 3, 4, 5, 6, 7]`; metadata
     `{"minimap_size": (162, 82)}`.
  4. `MapleAgent(characterClass=Kanna()).loadMap(fixture)` returns `Success(BuildOutcome.BUILT)` and
     `agent.pathGraph.graph.number_of_nodes() == 21`.
- **Run:** `python -m pytest -q tests/controller/test_csv_parser_ff2.py`
- **Commit:** `test(controller): add FF2 map fixture and parser characterization tests`

### Task B2: `MovementParams`

**Files:** create `src/route/__init__.py` (empty), `src/route/params.py`; tests `tests/route/test_params.py`.
**Class:** M — a frozen dataclass and a validator fully given by C3. **Cloud:** yes — stdlib + returns.
**Interface created:** C3, exactly.

- [ ] **Tests:** defaults equal C3's values; `validate(MovementParams())` is `Success` holding the same object; each
  rule in C3 has one failing case whose `Failure` message contains the field name (`arrive_px=0`,
  `slope_max_grade=0`, `slope_max_grade=1.5`, `walk_px_per_s=float("nan")`, `teleport_s=-1`,
  `ability_mix_band=-0.1`, `max_walk_ms=5001`, `min_settle_s=2.0` with `settle_timeout_s=2.0`); `ability_mix_band=0`
  is valid; the dataclass is frozen (assigning raises `FrozenInstanceError`); `params.py` source contains neither
  `19` nor `23` as a number token.
- **Run:** `python -m pytest -q tests/route/test_params.py`
- **Commit:** `feat(route): add movement parameters with validation`

### Task B3: `PathGraph` characterization tests; both known cases as "no path today"

**Files:** tests `tests/controller/test_path_graph_characterization.py`. No source change.
**Class:** M — pins existing behaviour, one module. **Cloud:** yes — imports `src.controller.*`, `src.classes`,
networkx.
**Interface created:** none.

- [ ] **Tests** (all pass on unchanged code; build FF2 through `MapleAgent(characterClass=Kanna()).loadMap(fixture)`
  and use `agent.pathGraph`):
  1. Exactly 40 edges, and the set of `(u.minimap_position, v.minimap_position)` equals:
     `(101,24)→(109,23), (101,24)→(90,24), (105,42)→(105,60), (105,42)→(118,43), (105,42)→(97,43), (105,60)→(105,42),
     (105,60)→(113,60), (105,60)→(99,60), (109,23)→(101,24), (113,60)→(105,60), (113,60)→(129,60), (118,43)→(105,42),
     (129,60)→(113,60), (37,60)→(48,61), (42,28)→(49,28), (48,45)→(48,61), (48,45)→(49,28), (48,45)→(58,46),
     (48,61)→(37,60), (48,61)→(48,45), (48,61)→(65,60), (49,28)→(42,28), (49,28)→(48,45), (49,28)→(66,28),
     (58,46)→(48,45), (58,46)→(74,45), (65,60)→(48,61), (65,60)→(82,60), (66,28)→(49,28), (74,45)→(58,46),
     (79,24)→(90,24), (82,60)→(65,60), (82,60)→(99,60), (90,24)→(101,24), (90,24)→(79,24), (97,43)→(105,42),
     (97,43)→(99,60), (99,60)→(105,60), (99,60)→(82,60), (99,60)→(97,43)`.
  2. Edge weights are |dx| for horizontal edges and |dy| for vertical ones (e.g. `(82,60)→(99,60)` weight 17,
     `(48,61)→(48,45)` weight 16).
  3. **Known cases, today:** `nx.has_path(graph, (49,28)-node, (101,24)-node)` is False and
     `nx.has_path(graph, (101,24)-node, (105,42)-node)` is False; the other 5 rotation legs have a path.
  4. Synthetic `PathGraph` (3-6 hand-made `Node`s, allowances 2/2, reach 19/23, `buildGraph()`): a node 20 px away
     horizontally gets no edge, 19 px does; a node 24 px above gets no edge, 23 does; a node 3 px off-axis gets no
     edge; two candidates at the same distance → the edge goes to the one inserted first in `node_map` (G19); a target
     with `is_left_platform_edge=True` gets a vertical edge only from a source with x ≥ its x (G20).
  5. `getPathToGoalNode` returns an empty `Path` for an unreachable goal (G21).
- **Run:** `python -m pytest -q tests/controller/test_path_graph_characterization.py`
- **Commit:** `test(controller): characterize PathGraph edges and the two FF2 no-path legs`

### Task B4: Terrain inference

**Files:** create `src/route/terrain.py`; tests `tests/route/test_terrain.py`.
**Class:** O — encodes the terrain-inference design (DC2, DC3) and reuses `PathGraph`'s edge rule; judgment in the
edge cases. **Cloud:** yes — stdlib, networkx, returns, `src.controller`, `src.route.params`.
**Interface created:** C4, exactly.

- [ ] **Tests** (FF2 nodes from `CSVParser` in `node_map` order, `align_px=2`, `reach_h=19`, `reach_v=23`, default
  params):
  1. SLOPE links are exactly `(66,28)→(79,24)` and `(79,24)→(66,28)`.
  2. UP/DOWN links (R3) are exactly these 17: UP `(113,60)→(118,43)`, `(48,61)→(48,45)`, `(105,42)→(101,24)`,
     `(99,60)→(97,43)`, `(48,45)→(49,28)`, `(74,45)→(79,24)`, `(97,43)→(101,24)`, `(105,60)→(105,42)`; DOWN
     `(49,28)→(48,45)`, `(101,24)→(105,42)`, `(105,42)→(105,60)`, `(48,45)→(48,61)`, `(42,28)→(37,60)`,
     `(79,24)→(74,45)`, `(109,23)→(105,42)`, `(118,43)→(113,60)`, `(97,43)→(99,60)`.
  3. The candidate `(66,28)→(65,60)` is **not** emitted (blocked by the platform through `(58,46)`-`(74,45)`).
  4. Platforms, in order: `[(37,60), (48,61), (65,60), (82,60), (99,60), (105,60), (113,60), (129,60)]`,
     `[(48,45), (58,46), (74,45)]`, `[(42,28), (49,28), (66,28), (79,24), (90,24), (101,24), (109,23)]`,
     `[(97,43), (105,42), (118,43)]`; `platform_index` of the `(79,24)` node is 2.
  5. No LEVEL/SLOPE link spans `(74,45)`↔`(97,43)` (dx 23 > reach 19).
  6. Synthetic (every pair within `reach_h` 19 and with a grade the other rule would allow, so each case is decided by
     the rule it names): a step of dy 7 over dx 18 is not a slope (dy 7 > `slope_max_dy_px` 6, although the grade
     7/18 ≈ 0.39 ≤ 0.5); dy 5 over dx 8 is not (grade 0.625 > 0.5, although dy 5 ≤ 6); dy 5 over dx 10 is; a drop of dy 30 from a `can_fall=False` node is not linked; from a `can_fall=True` node it is
     (DOWN); a target flagged `is_right_platform_edge=True` to the left of the source is rejected (G20).
  7. Empty `nodes` → `Failure`; a node with `minimap_position=(1.5, 2)` → `Failure`.
  8. Calling `infer_terrain` twice on the same input gives equal `links` (deterministic order).
- **Run:** `python -m pytest -q tests/route/test_terrain.py`
- **Commit:** `feat(route): infer slopes, near-aligned vertical moves and platforms from the node layout`

### Task B5: `RouteGraph`

**Files:** create `src/route/route_graph.py`; tests `tests/route/test_route_graph.py`.
**Class:** O — integrates `PathGraph`, terrain and the cost model; design choices in edge typing and costs.
**Cloud:** yes — networkx, returns, `src.controller`, `src.route`.
**Interface created:** C5, exactly.

- [ ] **Tests** (FF2: `agent = MapleAgent(characterClass=Kanna()); agent.loadMap(fixture)`;
  `RouteGraph.build(agent.pathGraph, MovementParams())`):
  1. `Success`; 21 nodes, **51** edges; `nx.is_strongly_connected(rg.graph)` is True.
  2. All 40 PathGraph edges are present with their B5 kinds; the 11 added edges are the 2 SLOPE edges `(66,28)→(79,24)` and `(79,24)→(66,28)` and
     the 9 vertical links of B4.2 that PathGraph lacks: `(113,60)→(118,43)`, `(101,24)→(105,42)`,
     `(105,42)→(101,24)`, `(74,45)→(79,24)`, `(42,28)→(37,60)`, `(79,24)→(74,45)`, `(109,23)→(105,42)`,
     `(118,43)→(113,60)`, `(97,43)→(101,24)`.
  3. **Known cases now have a path:** `rg.path(<(49,28) node>, <(101,24) node>)` is `Success` and its edges include the SLOPE edge
     `(66,28)→(79,24)`; `rg.path(<(101,24) node>, <(105,42) node>)` is `Success` of the 2-tuple `((101,24) node, (105,42) node)` and that edge is `DOWN` with
     `cost_s == pytest.approx(1.05)` (teleport 0.55 + 4/8).
  4. Every one of the 7 rotation legs has a path.
  5. `horizontal_cost_s` matches C5's seven listed values (`pytest.approx`, abs 1e-9); `down_options(18, True, 23, p)`
     has keys `{"teleport", "drop"}` with values `pytest.approx(0.55)` and `pytest.approx(0.65)`; `down_options(30, False, 23, p)` is `{}`.
  6. `build` on a `PathGraph` whose `buildGraph()` was not called → `Failure`; `path` to a node not in the graph →
     `Failure`.
  7. `route_graph.py` source contains neither `19` nor `23` as a number token.
- **Run:** `python -m pytest -q tests/route/test_route_graph.py`
- **Commit:** `feat(route): typed, costed route graph over PathGraph plus inferred terrain`

### Task B6: Localisation (`attach`)

**Files:** create `src/route/locate.py`; tests `tests/route/test_locate.py`.
**Class:** O — design choices in how an off-node position joins the graph. **Cloud:** yes — pure.
**Interface created:** C6, exactly.

- [ ] **Tests** (FF2 `RouteGraph`: `agent = MapleAgent(characterClass=Kanna()); agent.loadMap(fixture)`;
  `rg = RouteGraph.build(agent.pathGraph, MovementParams()).unwrap()`):
  1. `(101,24)` → `Anchor(node=(101,24)-node, platform=2, left=None, right=None)`; `(102,25)` → same node (A1).
  2. `(72,26)` (on the slope, y_interp 26.15) → `Anchor(None, 2, (66,28), (79,24))`.
  3. `(102,42)` (y_interp 42.375) → `Anchor(None, 3, (97,43), (105,42))`; `(103,42)` → `Anchor(node=(105,42)-node,
     platform=3, ...)` by A1 (|dx| = 2).
  4. `(132,60)` → `Anchor(None, 0, (129,60), None)` (A3); `(20,60)` → `Anchor(None, 0, None, (37,60))` (17 ≤ 19);
     `(15,60)` → `Failure` (22 > 19).
  5. `(86,37)` (mid-air between platforms) → `Failure` starting `"off-graph"`.
  6. `(72,30)` (4 px below the slope) → not on platform 2 (|30 − 26.15| > 3) → `Failure`.
- **Run:** `python -m pytest -q tests/route/test_locate.py`
- **Commit:** `feat(route): attach a measured position to the route graph`

### Task B7: Planner and ability choice (`next_move`)

**Files:** create `src/route/planner.py`; tests `tests/route/test_planner.py`.
**Class:** O — ability choice and the move rules (DC6, PB-D3), multi-module integration. **Cloud:** yes — pure plus
numpy and `src.input.protocol.Key`.
**Interface created:** C7, exactly.

- [ ] **Tests** (FF2 `RouteGraph`: `agent = MapleAgent(characterClass=Kanna()); agent.loadMap(fixture)`;
  `rg = RouteGraph.build(agent.pathGraph, MovementParams()).unwrap()`; `rng = np.random.default_rng(0)` unless stated; a test that names a changed parameter, such as
  `ability_mix_band=0` or `max_walk_ms=1000`, rebuilds `rg` with `RouteGraph.build(agent.pathGraph,
  MovementParams(<that parameter>)).unwrap()`, because `next_move` takes no params argument and reads `rg.params`):
  1. Arrived: pos `(104,43)`, target `(105,42)` → `Decision(None, 0.0, None)`.
  2. Known case 2: pos `(101,24)`, target `(105,42)`, `ability_mix_band=0` → `Move(TELEPORT, Key.DOWN, None)`,
     `toward` the `(105,42)` node, `cost_to_go_s == approx(1.05)`. Then pos `(101,42)` (landed) → `WALK`, `Key.RIGHT`,
     `walk_ms == approx(500.0)`.
  3. Known case 1: pos `(49,28)`, target `(101,24)` → `TELEPORT` `Key.RIGHT` (run 49→66, d = 17). Pos `(66,28)` →
     `WALK` `Key.RIGHT`, `walk_ms == approx(1625.0)` (SLOPE 13 px at 8 px/s). Pos `(72,26)` → `WALK` `Key.RIGHT`
     toward `(79,24)`, `walk_ms == approx(875.0)`. Pos `(79,24)` → `TELEPORT` `Key.RIGHT` (run 79→101, d = 22).
  4. P4 formula: on platform 0 from `(82,60)`, target `(99,60)` (d = 17) → TELEPORT; from `(88,60)` (d = 11) → WALK
     `walk_ms == approx(1375.0)`; from `(86,60)` (d = 13) → TELEPORT; from `(95,60)` (d = 4) → WALK 500.
  5. UP: pos `(48,61)`, target `(58,46)` → `Move(TELEPORT, Key.UP, None)`.
  6. Mix band: pos `(101,24)`, target `(105,42)` with default band 0.3 (teleport 0.55, drop 0.65, 0.65 ≤ 0.715): over
     2000 calls with one `default_rng(1)`, the DROP share is within [0.45, 0.55] and every other move is
     `TELEPORT DOWN`. With band 0 → never DROP. In a synthetic two-node graph whose upper node has `can_fall=False`
     and a node 18 px straight below, only `TELEPORT DOWN` is chosen.
  7. Off-graph pos `(86,37)` → `Failure` starting `"off-graph"`; `walk_ms` never exceeds `max_walk_ms` (pos `(37,60)`,
     target `(48,61)`, `max_walk_ms=1000`: an 11 px walk → `walk_ms == 1000.0`).
  8. Node off the surface: target `(105,42)` and pos `(105,45)` (3 px below the node, on platform 3 by A2) →
     arrived; pos `(105,46)` → `Failure` starting `"off-graph"` (not at the node: |dy| 4 > `arrive_px` 2, and `attach`
     finds nothing: A1 |dy| 4 > `align_px` 2, A2 |46 − 42| = 4 > `attach_y_px` 3, A3 no platform end within `reach_h`, so
     P2 returns A4's `Failure`; never a `Success`). In P4, pos `(48,64)` with target `(58,46)` counts as
     at `(48,61)` (|dy| 3, floor platform by A2) and gives `TELEPORT` `Key.UP`.
  9. `rng` is untouched (same next draw as a fresh `default_rng(0)`) in every case except 6.
  10. P5 WALK edge at a slope-segment start (review round 5, I1): the FF2 rows with the `(79, 24)` node moved to
     `(79, 22)`, written to a temporary CSV and loaded the same way; pos `(77,25)` (on the slope segment, at the
     `(79,22)` node by the platform test), target `(101,24)` → `TELEPORT` `Key.RIGHT` `toward` the `(101,24)` node
     (g′ advanced over the WALK edges via `(90,24)`, d = 24), never `Failure` or `None`.
- **Run:** `python -m pytest -q tests/route/test_planner.py`
- **Commit:** `feat(route): plan the next move and choose walk, teleport or drop`

### Task B8: Move executor

**Files:** create `src/route/moves.py`; tests `tests/route/test_moves.py`.
**Class:** M — a three-way dispatch fully given by C8. **Cloud:** yes — `src.input.input_layer` imports on Linux.
**Interface created:** C8, exactly.

- [ ] **Tests** (a recording fake layer with `walk`, `teleport`, `drop` methods returning a sentinel `Success`, and one
  real `InputLayer(FakeLink(), StubModel())` built from copies of those two helpers placed in `test_moves.py` (G16;
  copy, do not import from `tests/input/test_input_layer.py`):
  1. WALK → exactly one call `walk(Key.LEFT, 750.0, 123)`; TELEPORT UP → `teleport(Key.UP, 123)`; DROP → `drop(123)`;
     `stimulus_ns=None` is passed through as None.
  2. The returned `Result` is the layer's object (identity).
  3. With the real `InputLayer` and a `FakeLink` whose `play_result` is `Failure(InputError.LINK_LOST)`, `execute`
     returns `Failure(InputError.LINK_LOST)`.
- **Run:** `python -m pytest -q tests/route/test_moves.py`
- **Commit:** `feat(route): execute a planned move through the input layer`

### Task B9: Simulator world: terrain, movement physics, injected errors

**Files:** create `src/route/sim/__init__.py` (empty), `src/route/sim/world.py`, `tests/fixtures/ff2_terrain.json`;
tests `tests/route/test_sim_world.py`.
**Class:** S — models movement timing, falls and injected errors that the timing and recovery targets are judged
on. **Cloud:** yes — stdlib, numpy, returns, `src.input.protocol`.
**Interface created:**

```python
@dataclass(frozen=True)
class SimPlatform:
    name: str; droppable: bool; points: tuple[tuple[float, float], ...]
    def surface_y(self, x: float) -> float | None      # linear interpolation; None outside [x0, xn]

@dataclass(frozen=True)
class SimTerrain:
    minimap_size: tuple[int, int]; platforms: tuple[SimPlatform, ...]
    def surface_below(self, x: float, y_from: float) -> tuple[int, float] | None
        # the platform (index, y) with the smallest surface y >= y_from at x; None if none

def load_terrain(path: Path) -> Result[SimTerrain, str]

@dataclass(frozen=True)
class SimParams:
    walk_px_per_s: float = 8.0; teleport_settle_s: float = 0.35; drop_settle_s: float = 0.25
    fall_px_per_s: float = 60.0; link_latency_s: float = 0.03

@dataclass(frozen=True)
class NoiseConfig:
    no_op_p: float = 0.06; overshoot_p: float = 0.05; overshoot_px: tuple[int, int] = (2, 4)
    landing_p: float = 0.10; landing_px: tuple[int, int] = (1, 2); miss_p: float = 0.01
    node_offset_px: int = 0      # B14 shifts each map node's y by U{-k..k} before planning; the terrain stays
    @staticmethod
    def off() -> "NoiseConfig"                         # every probability 0

class InjectedError(StrEnum):
    NO_OP = "no_op"; OVERSHOOT = "overshoot"; LANDING = "landing"

@dataclass(frozen=True)
class MoveEffect:
    kind: str                                  # "teleport" | "walk" | "drop" | "none"
    start: tuple[float, float]; end: tuple[float, float]
    t_end_ns: int; settle_s: float; injected: tuple[InjectedError, ...]

class SimWorld:
    def __init__(self, terrain: SimTerrain, reach_h: int, reach_v: int, params: SimParams, noise: NoiseConfig,
                 rng: np.random.Generator, start: tuple[float, float]) -> None   # ValueError unless some surface_y(start x) is within 0.5 px of start y
    pos: tuple[float, float]
    effects: list[MoveEffect]
    def teleport(self, direction: Key, now_ns: int) -> MoveEffect
    def walk(self, direction: Key, held_ms: float, now_ns: int) -> MoveEffect
    def drop(self, now_ns: int) -> MoveEffect
    def nothing(self, now_ns: int) -> MoveEffect          # a schedule that moves nothing
    def position_at(self, t_ns: int) -> tuple[float, float]
```

- [ ] **Fixture** `tests/fixtures/ff2_terrain.json` (hand-authored, DC8; unverified, checked in B21):
  ```json
  {"minimap_size": [162, 82], "platforms": [
    {"name": "floor", "droppable": false, "points": [[30, 60], [37, 60], [48, 61], [65, 60], [135, 60]]},
    {"name": "mid-left", "droppable": true, "points": [[44, 45], [48, 45], [58, 46], [74, 45], [80, 45]]},
    {"name": "top", "droppable": true, "points": [[38, 28], [49, 28], [66, 28], [79, 24], [101, 24], [109, 23], [113, 23]]},
    {"name": "mid-right", "droppable": true, "points": [[94, 43], [97, 43], [105, 42], [118, 43], [122, 43]]}]}
  ```
- [ ] **Physics** (each move first draws NO_OP with `no_op_p`: then `pos` is unchanged, `kind="none"`, `settle_s=0`,
  `injected=(NO_OP,)`; one `rng.random()` per probability, drawn in the order no-op, overshoot, landing):
  - **teleport LEFT/RIGHT:** x′ = x ± `reach_h`, plus (with `overshoot_p`) an extra `rng.integers(lo, hi+1)` px in
    the same direction (OVERSHOOT), plus (with `landing_p`) ± `rng.integers(lo, hi+1)` px, random sign (LANDING).
    Land on `surface_below(x′, y − 6)`; none → stay (`kind="teleport"`, end = start).
    `settle_s = teleport_settle_s + max(0, y_land − y)/fall_px_per_s`.
  - **teleport UP:** land on `surface_below(x + landing offset, y − reach_v)` (may be the current platform);
    `settle_s` as above. **teleport DOWN:** land on `surface_below(x + landing offset, y + 1)`; none → stay;
    `settle_s = teleport_settle_s + max(0, (y_land − y) − reach_v)/fall_px_per_s`. No overshoot vertically.
  - **drop:** the current platform is the one whose `surface_y(x)` is within 0.5 px of y. If it is not droppable → `kind="none"`, no move, nothing injected. Else land on
    `surface_below(x + landing offset, y + 1)`; `settle_s = drop_settle_s + (y_land − y)/fall_px_per_s`.
  - **walk LEFT/RIGHT:** d = held_ms/1000 · `walk_px_per_s` (+ OVERSHOOT extra px with `overshoot_p`); x′ = x ± d;
    if x′ is beyond the current platform's end, the character walks off at that end: land on
    `surface_below(x_end ± 0.5, y + 1)` at the end's x (no crossing a gap); else land on the current platform at x′.
    `settle_s = max(0, y_land − y)/fall_px_per_s`. UP/DOWN walk → `nothing`.
  - `position_at(t)`: before the last effect's `t_end_ns` → its `start`; during `settle_s` after it → x = end x,
    y linearly from start y to end y; after → `end`.
- [ ] **Tests:**
  1. `load_terrain(fixture)` is `Success`; 4 platforms; `surface_y` of "top" at 72 == approx(26.1538, abs 1e-3);
     points not strictly increasing in x → `Failure`; missing key → `Failure`.
  2. With `NoiseConfig.off()`, reach 19/23, default `SimParams`: from `(101,24)` teleport DOWN → end
     `(101, 42.5)`, `settle_s == approx(0.35)`; from `(48,61)` teleport UP → `(48,45)`; from `(86,60)` teleport UP →
     stays `(86,60)`; from `(58,46)` teleport LEFT → x 39, falls to the floor at y 60 + 2/11 ≈ 60.18, settle 0.35 + 14.18/60;
     from `(66,28)` walk RIGHT 1625 ms → `(79,24)`; from `(48,61)` (floor) drop → `kind="none"`; from `(49,28)` drop →
     `(49, 45.1)`, settle 0.25 + 17.1/60.
  3. Starting from `NoiseConfig.off()` and changing one field with `dataclasses.replace`: `no_op_p=1` → every move
     is NO_OP and `pos` never changes; `overshoot_p=1, overshoot_px=(3,3)` → a teleport RIGHT from `(82,60)` ends at
     x 104; `landing_p=1, landing_px=(1,1)` → the end x differs from the noise-off end by exactly 1.
  4. Over 10 000 teleports with defaults and `default_rng(7)`, the NO_OP share is within [0.05, 0.07].
  5. Same seed → identical `effects` sequence.
- **Run:** `python -m pytest -q tests/route/test_sim_world.py`
- **Commit:** `feat(route): simulated FF2 world with movement physics and injected errors`

### Task B10: Simulator link, clock and position source

**Files:** create `src/route/sim/link.py`; tests `tests/route/test_sim_link.py`.
**Class:** S — virtual time and decoding timed key schedules into moves. **Cloud:** yes — pure.
**Interface created:**

```python
class SimClock:                                   # implements walker.Clock
    def __init__(self, start_ns: int = 1_000_000_000) -> None
    def now_ns(self) -> int
    def sleep(self, seconds: float) -> None       # advances virtual time; never blocks
    def advance(self, ns: int) -> None

class SimLink:                                    # duck-types BleLink for InputLayer (G16)
    def __init__(self, world: SimWorld, clock: SimClock, params: SimParams) -> None
    fail_next: list[InputError]                   # tests push errors; play pops one and fails without moving
    plays: int                                    # every play call, failed ones included, incremented on entry
    def play(self, steps: Sequence[Step]) -> Result[SendReport, InputError]
    def release_all(self) -> Result[SendReport, InputError]

class SimPositionSource:                          # implements walker.PositionSource
    def __init__(self, world: SimWorld, clock: SimClock, miss_p: float, rng: np.random.Generator) -> None
    def read(self) -> Result[tuple[int, int], str]   # Failure("miss") with miss_p; else rounded position_at(now)
```

- [ ] **Decoding** a `play(steps)`: walk the steps accumulating time in 100 µs units; record each key's first down
  and last up. Then: exactly one arrow and `E`, arrow down first → `world.teleport(arrow, t_end)`; `DOWN` and `ALT`,
  `DOWN` first → `world.drop(t_end)`; exactly one arrow and no other key → `world.walk(arrow, held_ms, t_end)` with
  held_ms = (up − down)/10; anything else → `world.nothing(t_end)`. Time: `clock.advance(link_latency_s)`, then
  advance by the schedule total; `t_end` = clock after that. Returns `Success(SendReport(seq, sent_ns, written_ns,
  accepted_ns, done_ns, Accepted(seq, 0), Done(seq, 0, None, 0, 0, ())))` (`Accepted`, G11's module) with `seq` counting from 1 and the four stamps
  non-decreasing. `release_all()` advances nothing and returns a `SendReport` with `accepted_ns=None` and `accepted=None`.
- [ ] **Tests:**
  1. Through a real `InputLayer(SimLink(...), TimingModel(default_rng(3)))`, noise off: `teleport(Key.DOWN)` from
     `(101,24)` moves the world to `(101, 42.5)`; `drop()` from `(49,28)` → y 45.1; `walk(Key.RIGHT, 1000.0)` from
     `(82,60)` moves x by (`wire_100us` of the `ActionRecord`'s `WALK` sample)/10/1000·8 within 1e-6 (the link sees
     the quantised schedule, G13); `tap(Key.E)` and `jump()` move nothing.
  2. The clock advances by 30 ms + the schedule total per `play`; `SimClock.sleep(0.25)` advances 250 000 000 ns.
  3. `fail_next=[InputError.ACK_TIMEOUT]` → that `play` returns the Failure, the world does not move, the next
     `play` works; `InputLayer` then also calls `release_all` once (G7) and it succeeds; afterwards `plays == 2` (the
     failed play counts).
  4. `SimPositionSource` with `miss_p=0`: reads during a fall return x = end x and a y between start and end;
     after settle the rounded end. With `miss_p=1` every read is `Failure("miss")`.
- **Run:** `python -m pytest -q tests/route/test_sim_link.py`
- **Commit:** `feat(route): simulated link, clock and position source`

### Task B11: Leg and lap log

**Files:** create `src/route/leg_log.py`; tests `tests/route/test_leg_log.py`.
**Class:** S — the record format Feature D consumes and its crash/data-loss behaviour. **Cloud:** yes — stdlib +
returns.
**Interface created:** C9, exactly.

- [ ] **Tests:**
  1. `leg_to_json` / `lap_to_json` produce exactly C9's keys; tuples become lists; `outcome` is the string value;
     `duration_s == (end_ns − start_ns)/1e9`; `json.dumps` of the dict succeeds.
  2. `JsonlLegLog.open(tmp)` creates the directory; after `write_leg` (not closed) a second, independent
     `open(tmp/"legs.jsonl").read()` shows the full line ending in `\n` (flushed per record).
  3. Opening an existing directory appends: two sessions of 2 legs each → 4 lines, the first 2 unchanged.
  4. `write_leg` after `close()` → `Failure`; `open` on a path that is an existing file → `Failure`.
  5. With `fsync=True`, `os.fsync` is called once per record (monkeypatch `os.fsync` to count); with False, never.
  6. `MemorySink` keeps records in order and always returns `Success(None)`.
- **Run:** `python -m pytest -q tests/route/test_leg_log.py`
- **Commit:** `feat(route): JSONL leg and lap log`

### Task B12: Walker — one leg

**Files:** create `src/route/walker.py` (`Clock`, `PositionSource`, `RealClock`, `STALE`, `WalkerConfig`,
`RunSummary`, `RouteWalker.__init__`, `walk_leg`); tests `tests/route/test_walker_leg.py`.
**Class:** S — the control loop: settle timing, stall detection, error handling. **Cloud:** yes — pure; tests use
B9/B10.
**Interface created:** C10 except `run` and `stop_now` (B13).

- [ ] **Stop flag.** `RouteWalker.__init__` creates the stop flag, a private `threading.Event` (the same constructor
  also holds `rg`, `rotation`, `layer`, `positions`, `clock`, `sink`, `rng`, `config`); `walk_leg` only reads it (steps 1, 7
  and 8). B13's `stop_now` sets it, and B13's tests exercise it; B12 has no way to set it. B13 adds the "run finished"
  `Event` and `Lock` to the same constructor.
- [ ] **Loop** `walk_leg(lap, leg, src, dst)`, `start_ns = clock.now_ns()`, `best = inf`, `last_progress = start_ns`:
  1. If the stop flag is set → outcome `STOPPED`.
  2. If `leg_timeout_s` is set and now − start > it → `TIMED_OUT`.
  3. Stall check: if now − last_progress > `stall_s` → `stalls += 1`, `last_progress = now`.
  4. `read()`: `Failure(STALE)` → sleep `poll_s`, continue (not a miss); other `Failure` → `misses += 1`, sleep
     `poll_s`, continue.
  5. `next_move(rg, pos, dst, rng)`: `Failure` → `off_graph += 1`, sleep `poll_s`, continue. `move is None` →
     `REACHED`.
  6. If `cost_to_go_s < best − progress_eps_s` → `best = cost_to_go_s`, `last_progress = now`.
  7. Re-check the stop flag (set → `STOPPED`, nothing sent). `stimulus` = the `now_ns()` taken right after the
     read if `use_stimulus` else None; `execute(layer, move, stimulus)`. `Failure(LINK_LOST)` or `Failure(NOT_CONNECTED)` → append the move entry, outcome `LINK_DOWN`. Any
     other `Failure` → entry with `input` = the error name, continue to settle.
  8. Settle: sleep `min_settle_s`; then poll every `poll_s`: settled when two consecutive successful reads are equal;
     give up after `settle_timeout_s` (settled = last successful read, or None). The stop flag is checked before
     every poll; set → record the `MoveEntry` and end `STOPPED`. Record `MoveEntry` and loop.
  Every outcome returns a `LegRecord` with `end_ns = clock.now_ns()` and `src`/`dst` positions (`src` None for None).
- [ ] **Tests** (FF2 `RouteGraph`: `agent = MapleAgent(characterClass=Kanna()); agent.loadMap(fixture)`;
  `rg = RouteGraph.build(agent.pathGraph, MovementParams()).unwrap()`; `SimWorld` over `load_terrain(ff2_terrain.json)` with reach
  from `Kanna` + `SimLink` + `SimPositionSource`, `SimClock`, `use_stimulus=False`,
  `rng=default_rng(0)`. Unless a test says otherwise: `SimParams()`, `NoiseConfig.off()`, `SimWorld(terrain, 19, 23,
  params, noise, default_rng(1), start)`, `SimLink(world, clock, params)`, `SimPositionSource(world, clock, miss_p=0,
  rng=default_rng(2))`, `InputLayer(link, TimingModel(default_rng(3)))`, `WalkerConfig(use_stimulus=False)`, a
  `MemorySink`, and `rotation` = the nodes at the 7 blue rows of the fixture in file order):
  1. Noise off, start `(49,28)`, leg to `(101,24)` → `REACHED` in ≤ 8 moves; the moves include a WALK whose start
     `pos` is on the slope (x in 66..79) and whose `direction == "RIGHT"` (`Key.name`, never `str(Key)`).
  2. Noise off, start `(101,24)`, leg to `(105,42)` → `REACHED` in ≤ 3 moves; final settled position within 2 px.
  3. Noise off, every rotation leg from its source node → `REACHED`, 0 stalls, 0 off_graph.
  4. A link that never moves (`replace(NoiseConfig.off(), no_op_p=1)`): leg `(82,60)`→`(48,61)` with `leg_timeout_s=25` → `TIMED_OUT`, `stalls
     == 2` (10 s and 20 s windows), every move entry has `input == "ok"`.
  5. `fail_next=[InputError.ACK_TIMEOUT]` → the first entry has `input == "ACK_TIMEOUT"` and the leg is still
     `REACHED`. `fail_next=[InputError.LINK_LOST]` → `LINK_DOWN` after exactly 1 entry and no further `play`.
  6. A position source scripted as `[Failure("miss"), Failure(STALE), Success(target)]` → `REACHED`, `misses == 1`.
  7. `use_stimulus=True`, `leg_timeout_s=2.0`, start `(82,60)`, leg to `(48,61)`, `SimClock`, a fake layer whose `walk`,
     `teleport` and `drop` record their `stimulus_ns` argument, return `Success(None)` and never move the world (`Success(None)` stands in for an `ActionRecord`; `walk_leg` uses only
     `is_successful` and the error of `execute`'s result, never the record's fields). The
     position source is wrapped so each `read()` first advances the clock by 1 ms (`clock.advance(1_000_000)`) and
     records the `clock.now_ns()` it then returns as `read_ns`. The leg ends `TIMED_OUT` (virtual time, no real wait,
     so the test cannot hang); at least 2 calls were made; each call's `stimulus_ns` equals the `read_ns` of the read
     just before it (so it is the `now_ns()` taken right after that read, not the start of the loop pass). With
     `use_stimulus=False` every call gets `stimulus_ns is None`.
- **Run:** `python -m pytest -q tests/route/test_walker_leg.py`
- **Commit:** `feat(route): walk one leg with re-planning, settle and stall detection`

### Task B13: Walker — rotation, laps, stop

**Files:** modify `src/route/walker.py` (`run`, `stop_now`); tests `tests/route/test_walker_run.py`.
**Class:** S — cross-thread stop with `release_all` pre-empting a `play` in flight (G6); lap bookkeeping.
**Cloud:** yes — pure.
**Interface created:** `RouteWalker.run`, `RouteWalker.stop_now` (C10).

- [ ] **Behaviour:** `run()` returns `Failure` if `rotation` is empty. It walks the approach leg (lap 0, leg 0,
  `src=None`, `dst=rotation[0]`), then laps 1, 2, …; leg i of a lap goes rotation[i] → rotation[(i+1) mod n]. Every
  leg record and, after each full lap, a `LapRecord(all_reached = every leg REACHED)` goes to the sink. A sink
  `Failure` is printed once per run and does not stop the walk. A leg ending `STOPPED` → summary reason `"stopped"`;
  `LINK_DOWN` → `"link_down"`; after `max_laps` laps → `"max_laps"`. A `TIMED_OUT` leg moves on to the next leg. On
  every return path `layer.release_all()` is called exactly once more; then a `threading.Event` "run finished" is
  set and, if the stop guard was started, `run` joins it (timeout 1 s) before returning. Starting the guard and
  setting "run finished" happen under one `threading.Lock`: `stop_now` starts no guard once "run finished" is set,
  and the guard checks "run finished" before each `release_all`.
  `stop_now()` (safe from any thread, more than once): sets the stop `threading.Event` that `RouteWalker.__init__` created (B12); calls `layer.release_all()`;
  and, on its first call only, starts a daemon *stop guard* thread that calls `layer.release_all()` every 50 ms
  (wall time, `time.sleep`) until "run finished" is set (PB-D4). `walk_leg` checks the stop flag again immediately
  before `execute`, so a stop seen before the call sends nothing; a stop that lands inside `InputLayer`'s reaction
  wait (G5) is cut by the guard within 50 ms of the `play` starting.
- [ ] **Tests:**
  1. Sim as in B12 (FF2, noise off, world start `(113,60)`), `max_laps=3`, `MemorySink` → `Success(RunSummary(3, 22, "max_laps"))`; 22 leg records (1 + 3·7);
     3 lap records with `legs == 7`, `all_reached` True, `end_ns > start_ns`; leg (lap 2, leg 4) has `src (49,28)`
     and `dst (101,24)`.
  Release counts in this task are counted on the `InputLayer` instance: replace `layer.release_all` with a function
  that counts the call and delegates. It sees only the walker's calls (`run`, `stop_now`, the guard), not the layer's
  own `link.release_all()` after a failed play (`input_layer.py:91`).
  2. Stop from another thread (positions from B10's `SimPositionSource` over a `SimWorld` as in test 1, start
     `(113,60)`, with its own `SimClock` that nothing advances, because the fake link below never moves the world; test
     3 the same): a fake link whose `play` blocks on an `Event` until `release_all` is called and then
     returns `Failure(InputError.ABORTED)`; the walker's clock is `RealClock`; a timer thread calls `stop_now()` after 0.2 s → `run`
     returns within 1.0 s wall with reason `"stopped"`, the last leg record is `STOPPED`, and `release_all` was called
     ≥ 2 times.
  3. Stop during the reaction wait: `use_stimulus=True`, `RealClock` (for the walker; positions as in test 2), `input_layer._sleep` monkeypatched to a
     function that blocks 0.5 s wall time; a fake link recording wall-clock times of `play` and `release_all`;
     `stop_now()` is called 0.1 s into the first reaction wait → `run` returns within 1.0 s with reason `"stopped"`,
     and if a `play` started after `stop_now()`, a `release_all` follows it within 0.1 s wall time. After `run`
     returns, no further `release_all` calls happen (the guard has stopped).
  4. Sim as in test 1 (world start `(113,60)`), with the `SimLink` wrapped by a link whose `play` pushes `InputError.LINK_LOST` onto
     `sim_link.fail_next` when `sim_link.plays == 4` and then delegates (B10's `fail_next` fails only the next
     play), so play 5 fails; the wrapper delegates `release_all` and everything else to the `SimLink` → reason `"link_down"`, the last leg record `LINK_DOWN`, no lap record for the
     unfinished lap.
  5. `rotation=[]` → `Failure`.
  6. A sink whose `write_leg` returns `Failure("disk full")` → the run still completes `max_laps=1` (all 8 legs
     attempted), and `"disk full"` appears exactly once in the captured stdout plus stderr (`capsys`).
  7. A leg that times out does not end the run, and `all_reached` is False for its lap: sim as in test 1 but
     `replace(NoiseConfig.off(), no_op_p=1)` (no move ever changes the position; start `(113,60)`),
     `WalkerConfig(use_stimulus=False, leg_timeout_s=3.0, max_laps=1)` → `Success(RunSummary(1, 8, "max_laps"))`; the
     approach leg is `REACHED` (already at `(113,60)`); lap 1's legs 0 to 5 are `TIMED_OUT` and leg 6
     (`(105,42)` → `(113,60)`) is `REACHED`; the 1 lap record has `legs == 7` and `all_reached` False.
  8. `release_all` counts (counted on the layer as above): a run to `max_laps=1` as in test 1 calls `layer.release_all`
     exactly 1 time; the `"link_down"` run of test 4 exactly 1 time; the stop run of test 2 at least 2 times (one from
     `stop_now`, one at the end of `run`), and none after `run` returns.
  9. The stop flag is re-checked before `execute`: sim as in test 1 but world start `(82,60)` (so the approach leg to
     `(113,60)` needs a move) and a wrapper around `SimPositionSource` whose first `read()` calls `walker.stop_now()` and
     then returns the position read; `run` returns `Success(RunSummary(0, 1, "stopped"))`, the one leg record is
     `STOPPED` with 0 moves, and `sim_link.plays == 0` (nothing was sent after the stop).
- **Run:** `python -m pytest -q tests/route/test_walker_run.py`
- **Commit:** `feat(route): cycle the rotation in laps, stoppable from another thread`

### Task B14: Simulator evaluation and the 1000-lap run

**Files:** create `src/route/sim/run.py`; tests `tests/route/test_sim_run.py`.
**Class:** S — judges the timing/control-loop targets, including the recovery metric. **Cloud:** yes — pure.
**Interface created:**

```python
@dataclass(frozen=True)
class SimReport:
    laps_requested: int; laps_completed: int; legs: int; legs_reached: int
    stalls: int; timeouts: int; link_down: int
    known_cases: dict[str, tuple[int, int]]    # "(x1, y1)->(x2, y2)" -> (reached, attempted), laps >= 1 only
    single_error_legs: int; single_error_within_3: int
    multi_error_legs: int; multi_error_within_bound: int     # extra <= 3 x (number of errors)
    moves: int; no_ops: int; drops: int; teleport_downs: int
    sim_seconds: float; mean_lap_s: float

@dataclass(frozen=True)
class Check:
    name: str; ok: bool; detail: str

def run_sim(map_csv: Path, terrain: Path, laps: int, seed: int, noise: NoiseConfig = NoiseConfig(),
            sim_params: SimParams = SimParams(), params: MovementParams = MovementParams(),
            out_dir: Path | None = None,
            known_cases: Sequence[tuple[tuple[int, int], tuple[int, int]]] = (),
            character: CharacterClass | None = None) -> Result[SimReport, str]   # None -> Kanna()
def judge(report: SimReport, min_single_error_legs: int = 100) -> list[Check]
def main(argv: list[str] | None = None) -> int
```

- [ ] **`run_sim`:** `MapleAgent(characterClass=character or Kanna()).loadMap(map_csv)`, `RouteGraph.build`, `load_terrain`;
  seeds from `np.random.SeedSequence(seed).spawn(4)` → timing, planner, world, positions; the world starts at the
  first rotation node's **unshifted** CSV position (snapped to the surface below it, `surface_below(x, y − 3)`); `InputLayer(SimLink(...), TimingModel(rng_timing))`; `RouteWalker` with
  `use_stimulus=False`, `leg_timeout_s=60`, `max_laps=laps`, sink `JsonlLegLog(out_dir, fsync=False)` if `out_dir`
  else `MemorySink`, wrapped in a sink that records `len(world.effects)` at each `write_leg`, so the effects between
  two consecutive leg writes belong to that leg. `known_cases` names legs by their (src, dst) positions; each listed
  leg gets a `known_cases` entry keyed `f"{src}->{dst}"` (Python tuple repr) counting the laps ≥ 1 in which it was
  attempted and reached. No map position is written in `run.py` (hard rule 5). **Recovery metric:** for each leg whose moves had exactly one injected error (from
  `world.effects`), replay the same leg in a fresh noise-off world from the leg's actual start position (`world.pos` when the leg began) (same terrain
  and the same `RouteGraph` the run used, including any node offsets; a fresh `TimingModel` and planner rng seeded
  from the leg's number) and count its moves (nominal);
  `extra = moves − nominal`; `single_error_within_3` counts `extra ≤ 3`. A leg with k ≥ 2 errors is replayed the
  same way and counts in `multi_error_legs`, and in `multi_error_within_bound` when `extra ≤ 3·k` (each injected
  error is allowed its own 3 extra moves).
- [ ] **`judge`** returns these checks, in order: `laps` (`laps_completed == laps_requested` and
  `legs_reached == legs` and `timeouts == 0` and `link_down == 0`); `stalls` (`== 0`); `known_cases` (every entry has
  reached == attempted == laps_completed; with no entries it passes with detail "none listed"); `recovery` (`single_error_legs ≥ min_single_error_legs` and
  within_3/single ≥ 0.99, where 0/0 passes only when `min_single_error_legs == 0`, and, when `multi_error_legs > 0`, within_bound/multi ≥ 0.99); `noise` (`no_ops/moves ≥ 0.05`); `abilities` (`drops > 0` and `teleport_downs > 0`).
- [ ] **CLI:** `python -m src.route.sim.run --laps N --seed S [--map tests/fixtures/fossil_foundry_2.csv]
  [--terrain tests/fixtures/ff2_terrain.json] [--out DIR] [--known-case X1,Y1:X2,Y2]... [--min-single-error-legs K]
  [--class kanna]` (`--known-case` repeatable; `--min-single-error-legs` default 100; `--class` chooses from
  `{"kanna": Kanna}`, default `kanna`) prints the report and exactly one line per check starting `PASS ` or `FAIL `;
  exit 0 iff all pass.
- [ ] **Node offsets.** With `noise.node_offset_px = k > 0`, `run_sim` adds `rng_world.integers(-k, k + 1)` to each
  node's y in the loaded map (a copy of the CSV rows, written to a temporary CSV that `loadMap` then reads, since
  `loadMap` takes a path), so the planner sees hand-placed nodes off
  the surface while `SimWorld` uses the unchanged terrain (review round 1, I8). Offsets can change which links are
  inferred (with ±2 the FF2 slope link survives in about 2 of 3 draws, measured by review round 2), so test (3b)
  checks only that legs complete, not which route they take. Node offsets never move the world's start (above), and
  `known_cases` entries are matched against each leg's **unshifted** CSV positions (run_sim matches by rotation index: leg i of a lap
  is rotation[i] → rotation[(i+1) mod n], and rotation[i] has the unshifted position of the i-th blue CSV row), so a known case is found whatever the offset.
- [ ] **Tests** (all pass `known_cases=[((49, 28), (101, 24)), ((101, 24), (105, 42))]`): (1) 30 laps, seed 1,
  defaults except `no_op_p=0.08`: every `judge(report, min_single_error_legs=10)` check passes; both known cases
  (30, 30). (2) Same seed twice → equal reports. (3) 20 laps with `SimParams(walk_px_per_s=6.5)` and with
  `9.5` (planner still assumes 8.0) → `laps`, `stalls`, `known_cases` pass. (3b) 20 laps with
`node_offset_px=2` → `laps`, `stalls`, `known_cases` pass. (4) `NoiseConfig(no_op_p=0.01)` →
  `noise` check fails. (5) `main(["--laps", "2", "--seed", "1", "--min-single-error-legs", "0", "--known-case", "49,28:101,24"])` prints
  exactly 6 lines starting `PASS ` or `FAIL ` and returns 0 iff all 6 are `PASS`. (6) A `known_cases` entry for a
  pair that is not a rotation leg → (0, 0) and the check fails. The 30-lap test takes < 60 s.
- [ ] **The target run** (task step, not a pytest): `python -m src.route.sim.run --laps 1000 --seed 1
  --known-case 49,28:101,24 --known-case 101,24:105,42` → every check
  PASS, exit 0. Paste its output into the task report and the commit body. If it takes more than 15 min, report the
  time; that is not a failure.
- **Run:** `python -m pytest -q tests/route/test_sim_run.py`
- **Commit:** `feat(route): simulator evaluation against spec B's targets; 1000-lap run passes`

### Task B15: Character locator

**Files:** create `src/route/character.py`; tests `tests/route/test_character.py`.
**Class:** O — method and threshold choices for detection. **Cloud:** yes — cv2 and numpy only, synthetic frames;
it touches no file under `src/vision/`. This reads Appendix B's "never vision" by directory and imports, not by
function (an orchestrator reading, recorded here; the cloud-built branch still runs the full Windows suite).
**Interface created:**

```python
class CharacterLocator:
    def __init__(self, icon_gray: np.ndarray, mask: np.ndarray, min_score: float = 0.95,
                 min_margin: float = 0.02, exclusion_px: int = 5) -> None
    @classmethod
    def from_files(cls, icon_path: Path, mask_path: Path) -> Result["CharacterLocator", str]
    def locate(self, minimap_rgb: np.ndarray) -> Result[tuple[int, int], str]
```

- [ ] **Behaviour:** converts with `COLOR_RGB2GRAY` (the shared frame is RGB, G26/G30); masked template match;
  scrubs non-finite and > 1 scores as `vision.py` does; the reported position is the best match's top-left plus
  (w//2, h//2), the same centre convention as `getCharacterCoords` (`controller.py:227`). `Failure("miss")` if the
  best score < `min_score`; `Failure("ambiguous")` if the best score outside `exclusion_px` of the best location is
  ≥ best − `min_margin`; `Failure` for a frame that is not `uint8` H×W×3 or is smaller than the icon. The method
  (`TM_CCORR_NORMED` as today, or another masked method if it separates better) is the implementer's choice, stated
  in the commit body with the measured synthetic scores. `getCharacterCoords` is not changed.
- [ ] **Tests** (synthetic 162×82 RGB frames: background (30, 30, 30) + `default_rng(s).integers(−10, 11)` noise per
  pixel, and three grey (120, 120, 120) horizontal 1-px lines at y = 28, 45, 60; the icon from `assets/chr/` pasted
  where `mask > 0`):
  1. `from_files` on the two asset PNGs → `Success`; on a missing path → `Failure`.
  2. 50 seeded positions with the whole icon inside the frame → each `locate` returns exactly the pasted top-left +
     (5, 5).
  3. 20 seeded backgrounds with no icon → every `locate` is a `Failure` (never a position).
  4. Two icons ≥ 20 px apart → `Failure("ambiguous")`.
  5. Icon half outside the frame → a `Failure` or a position within 3 px (Euclidean) of the true centre; never > 3 px.
  6. A float frame or a 5×5 frame → `Failure`.
- **Run:** `python -m pytest -q tests/route/test_character.py`
- **Commit:** `feat(route): character locator that reports a miss rather than a wrong position`

### Task B16: Minimap position source

**Files:** create `src/route/position.py`; tests `tests/route/test_position.py`.
**Class:** S — a thin adapter, but it reads a frame shared with the capture thread under its lock and detects
stale frames (concurrency). **Cloud:** yes — imports `src.route.character`, numpy; does **not**
import `src.main` or `src.vision` (the shared object is duck-typed).
**Interface created:**

```python
class MinimapPositionSource:                     # implements walker.PositionSource
    def __init__(self, shared, locator: CharacterLocator) -> None   # shared has .minimap_frame_lock, .minimap_frame
    def read(self) -> Result[tuple[int, int], str]
```

- [ ] **Behaviour:** under `shared.minimap_frame_lock`: frame None → `Failure("no frame")`; the same object as the
  previous read (`is`) → `Failure("stale")` (`walker.STALE`); else keep a reference and take `frame.copy()`. Outside
  the lock: `locator.locate(copy)`, returned unchanged.
- [ ] **Tests** (a fake shared object whose `minimap_frame_lock` is a wrapper around a `threading.Lock` implementing
  `acquire`/`release`/`__enter__`/`__exit__`, counting acquires and exposing whether it is held — `threading.Lock`
  cannot be subclassed on 3.14 — and a fake locator recording whether the lock was held when `locate` ran): None → `"no frame"`; same array twice → second is `"stale"`; a new array with the icon
  pasted → its position; `locate` never runs while the lock is held; one lock acquire per `read`.
- **Run:** `python -m pytest -q tests/route/test_position.py`
- **Commit:** `feat(route): position source over the shared minimap frame`

### Task B17: Minimap recorder

**Files:** create `src/route/harness/__init__.py` (empty), `src/route/harness/record_minimap.py`; tests
`tests/route/test_record_minimap.py`.
**Class:** S — runs the capture thread and polls it on a timer under the shared lock (concurrency, timing).
**Cloud:** no — `main()` starts the `src.vision` capture thread (bettercam,
Windows-only, G31).
**Interface created:**

```python
def record(frames: Callable[[], np.ndarray | None], out_dir: Path, seconds: float | None, fps: float,
           clock: Callable[[], int] = time.perf_counter_ns, sleep: Callable[[float], None] = time.sleep,
           stop: threading.Event | None = None) -> Result[int, str]   # B19-record-D1
def shared_frames(shared) -> Callable[[], np.ndarray | None]   # reads shared.minimap_frame under shared.minimap_frame_lock
def main(argv: list[str] | None = None) -> int
# python -m src.route.harness.record_minimap --out data/recordings/<name> [--seconds 120] [--fps 10]
```

- [ ] **Behaviour:** `record` polls `frames()` every 1/fps s until `seconds` have passed (when `seconds` is not
  `None`) or `stop` is set (when `stop` is given), whichever comes first; the end test runs after each poll, so a
  `stop` that is already set when `record` starts still saves the first frame. One of `seconds` and `stop` must be
  given: `seconds is None and stop is None` → `Failure` and nothing is written. Each new frame object (not `is` the
  last saved) is written RGB→BGR with `cv2.imwrite` as `frame_%06d.png` and a line `{"i", "t_ns", "file", "w", "h"}`
  is appended to `index.jsonl` (flushed per line); returns the number saved. `record` creates `out_dir` (with parents) after validating its arguments, never earlier, and
  never in `main`. `shared_frames(shared)` returns a
  callable that, under `shared.minimap_frame_lock`, returns `shared.minimap_frame` itself (the reference, never a
  copy: vision rebinds the attribute to a new array under the lock, G30, so the `is` de-dup above works and a copy
  would make every poll count as new); the lock rule lives here once, and `main` and B19 both use it. `--out` must
  resolve under the repo's `data/` (gitignored) or `main` exits 2. `main` imports `src.vision.vision` inside the
  function, runs `capture_and_process_minimap` on a daemon thread with its own `SharedInfo`-like object, and reads
  its frame through `shared_frames`; its `--seconds` and `--fps` options are unchanged. It sends no keys.
- [ ] **Tests** (`src.vision` is never imported by the tests; the existing ones use fake `clock` and `sleep`): fake
  `frames` returning a new array 5 times then the same one → 5 files and 5 index lines; colours round-trip (a
  pure-red RGB frame reads back from the PNG as red in RGB); `main(["--out", "/tmp/x"])` → 2. Stop (B19-record-D1):
  a fake `frames` that returns a new array on every call and sets a `threading.Event` on its 3rd call, with
  `seconds=None, stop=<that event>` → `Success(3)`, 3 files and 3 index lines; `seconds=None, stop=None` →
  `Failure`, and `out_dir` is not created (or holds no files). `shared_frames` is covered by B19's `--record` end-to-end test.
- **Run:** `python -m pytest -q tests/route/test_record_minimap.py`
- **Commit:** `feat(route): minimap frame recorder for supervised sessions`

### Task B18: Detection check on real frames (labels drafted by Claude, checked by Hong)

**Files:** create `src/route/harness/detection_check.py`; tests `tests/route/test_detection_check.py`; data under
`data/` only (gitignored).
**Class:** O — two tools, a human labelling loop and threshold calibration. **Cloud:** no — needs the local frames in
`data/recordings/ff2_2026-10-21/` and Hong.
**Interface created:**

```python
def crop_minimap(full_bgr: np.ndarray) -> Result[np.ndarray, str]   # same corner-template steps as vision.py:20-67, via vision_helper_lib
def load_frames(frames_dir: Path) -> Result[dict[str, np.ndarray], str]   # file name -> RGB minimap, sorted by name
def draft_labels(frames_dir: Path, out: Path) -> Result[int, str]
def score(found: dict[str, tuple[int, int] | None], labels: dict[str, tuple[int, int] | Literal["absent"] | None]) -> dict
def main(argv: list[str] | None = None) -> int
# python -m src.route.harness.detection_check draft --frames DIR --out DIR/labels_draft.json
# python -m src.route.harness.detection_check check --frames DIR --labels DIR/labels.json
```

- [ ] **Who labels:** Claude drafts, Hong checks. `draft` finds the character by a method independent of the locator
  (colour-blob centroid of the character dot in the minimap crop, with the colour range read from
  `assets/chr/chr.png`'s non-mask pixels), writes `labels_draft.json` (`{file: [x, y] | "absent" | null}`) and a contact sheet
  per 50 frames (crops at 8× with a 1-px cross at the label). **Human-only step:** Hong opens the sheets, corrects any
  wrong label or marks a frame unusable, and saves `labels.json`. Claude never edits `labels.json` after Hong.
- [ ] **Frame folders.** A folder with an `index.jsonl` is a B17 recording: its PNGs are already minimap crops,
  read with `cv2.imread` (BGR) and converted `COLOR_BGR2RGB`, no crop. Any other folder holds full-screen frames
  (as `data/recordings/ff2_2026-10-21/`): `cv2.imread`, `crop_minimap`, then `COLOR_BGR2RGB`. Both `draft` and
  `check` read frames only through `load_frames`; the locator always receives RGB.
- [ ] **`score`:** a label may also be the string `"absent"` (Hong marks a frame with no character on the
  minimap); any position found on such a frame counts as wrong, and absent frames are left out of `frames`, `hits`,
  `misses` and `hit_rate` (spec L67's rate is against a hand-checked position, which they lack). `null` (unusable)
  frames are skipped. Per other frame with a label: found within ±1 px in both axes → hit; found > 3 px away in Euclidean distance →
  wrong (spec "never reported > 3 px away", read as straight-line distance); `None` → miss. Returns `{"frames", "hits", "misses", "wrong", "hit_rate", "max_err_px"}`; `max_err_px` is over frames with an
  `[x, y]` label and a position found (absent frames stay out of it). `check` exits 0
  iff `hit_rate ≥ 0.995` and `wrong == 0` (specB:L67).
- [ ] **Tests** (synthetic, no real frames): `score` on hand-made dicts: 199 hits + 1 miss → hit_rate 0.995, PASS
  path; 1 found at 4 px → wrong 1; a 2 px error is neither hit nor wrong (counts against hit_rate only); an `"absent"` frame with a position found → wrong 1; 199 hits + 1 miss + 3 `"absent"` frames with `None` found
  → `frames` 200, `hit_rate` 0.995, `wrong` 0. A
  temporary folder with `index.jsonl` and one pure-red PNG written by B17's `record` is loaded by `load_frames` as an RGB array whose
  pixels are (255, 0, 0) and is not passed to `crop_minimap` (monkeypatch it to fail).
- [ ] **Real run:** `check` on `data/recordings/ff2_2026-10-21/` with Hong's labels. If it fails, tune
  `CharacterLocator`'s thresholds or method in `src/route/character.py` (re-run B15's tests) until it passes. Report
  how many distinct positions the 307 standing-still frames contain; moving frames are checked in B21.
- **Run:** `python -m pytest -q tests/route/test_detection_check.py`
- **Commit:** `feat(route): detection check against hand-checked labels`

### Task B19: Runtime wiring and the walk CLI

**Files:** create `src/route/runtime.py`, `src/route/walk.py`; tests `tests/route/test_walk_cli.py`.
**Class:** S — threads (capture, hotkey, walker), cross-thread stop, release on every exit. **Cloud:** no — imports
`src.vision` (bettercam) and installs a Windows keyboard hook.
**Interface created:**

```python
# src/route/runtime.py
class StopControl:
    def __init__(self, on_stop: Callable[[], None], hotkey_vk: int = 0x7B) -> None   # 0x7B = F12 (PB-D2)
    def start(self) -> Result[None, str]       # calls self._install_hook() (the WH_KEYBOARD_LL hook thread, G33;
                                               # a seam tests replace; it returns a remove() callable, see close()),
                                               # installs a SIGINT handler, then starts the worker thread that waits
                                               # for trigger() and calls on_stop once
    def trigger(self, source: str = "hotkey") -> None   # hook: "hotkey", SIGINT: "sigint", --minutes timer: "timer";
                                               # idempotent; returns at once; called from three threads, so the
                                               # first call, under a threading.Lock, sets trigger_ns and
                                               # trigger_source
    trigger_ns: int | None                     # time.perf_counter_ns() at the first trigger(), else None
    trigger_source: str | None
    def close(self) -> None                    # undoes start() (PB-amend2-D1, see below)
    def _install_hook(self) -> Callable[[], None]   # the seam; returns remove(): stops the hook thread and unhooks
def start_capture(shared) -> threading.Thread
def wait_first_frame(shared, timeout_s: float) -> Result[None, str]
# src/route/walk.py
def main(argv: list[str] | None = None) -> int
# python -m src.route.walk --map maps/fossil_foundry_2.csv --supervised [--class kanna] [--laps N] [--minutes M]
#                          [--seed S] [--out data/route/<UTC stamp>] [--record]
# exit codes: 0 done · 1 run ended link_down · 2 usage / no --supervised · 3 no first frame or no character found
#             · 4 connect failed · 5 map, route graph or character icon failed to load
#             · 6 run() returned a Failure · 7 run() raised an exception (PB-B19)
```

- [ ] **Seams** (module-level names in `walk.py` that `main` calls and tests monkeypatch): `_start_capture(shared)`
  (default `runtime.start_capture`), `_wait_first_frame(shared, timeout_s)` (default `runtime.wait_first_frame`),
  `_make_positions(shared) -> PositionSource` (default: `CharacterLocator.from_files(assets/chr/chr.png,
  assets/chr/chr_mask.png)`, exit 5 on `Failure`, else `MinimapPositionSource(shared, locator)`), `_make_link()` (default `BleLink()`; the object needs `connect`, `play`,
  `release_all`, `close`), `_make_clock() -> Clock` (default `RealClock()`), `_make_stop_control(on_stop)` (default
  `StopControl`; the object needs `start`, `close`, `trigger(source)`, `trigger_ns`, `trigger_source`), `_countdown_s = 5.0`.
- [ ] **Behaviour of `main`:** refuses (exit 2) without `--supervised`. Loads the map with `MapleAgent` and the
  `--class` character (choices `{"kanna": Kanna}`, default `kanna`), builds the `RouteGraph`; `_start_capture`;
  `_wait_first_frame(30 s)`; one `positions.read()` must succeed (exit 3 otherwise); `link.connect()` (exit 4 on
  `Failure`); `InputLayer(link, TimingModel(rng))`; `RouteWalker` with
  `_make_clock()`, `use_stimulus=True`, `JsonlLegLog(out)` and `max_laps=--laps`; writes `session.json` (seed, params,
  map path and SHA-256, git HEAD, start time). Prints "Focus the game window. Starting in 5 s. F12 stops." and waits
  `_countdown_s` s. `trigger()` only sets an `Event` (first call) that a StopControl worker thread waits on; the worker then
  calls `on_stop` (`walker.stop_now()`), so the hook procedure returns immediately and never blocks on Bluetooth
  (Windows drops a low-level hook that is slow to return; Feature A's listener only queues, G33). F12 (`trigger("hotkey")`) and Ctrl+C (`trigger("sigint")`) stop the run. `--minutes M` (a number of minutes,
  `type=float`, so `0.5` is allowed; `--minutes 2` as in the live check is unchanged) must satisfy: finite and
  `0 < M <= threading.TIMEOUT_MAX / 60`, else `main` returns 2 before anything starts (argparse takes `0`, negatives,
  `nan` and `inf`, and an `M` above the limit kills the timer thread with `OverflowError`, so the run would have no time
  bound). It arms a timer (`threading.Timer(60 * M, ...)`, a daemon, thread name `walk-timer`) that does the same
  (`trigger("timer")`). The timer is started immediately before `walker.run()`, so `M` counts the walk, not the first-frame
  wait, connect and countdown. On every exit path after the walker ran, `main` writes `out/stop.json`: `{"reason":
  RunSummary.reason, "trigger_ns", "trigger_source"}` (the last two from the StopControl, null if it never triggered),
  and prints `stopped: <reason>`, followed by ` (<source>)` when a source is set.
  **`--record` (B19-record-D1).** With `--record`, after the countdown and immediately before `walker.run()`, `main`
  starts a daemon thread named `record` that runs `record(shared_frames(shared), out/"frames", None, 10.0,
  stop=rec_stop)` (B17; `shared_frames` passes the shared frame object, not a copy; `rec_stop` is a
  `threading.Event` that `main` owns). A `Failure` from `record` prints `record failed: <reason>` to stderr and does not
  change the exit code (the walk decides it); a `record` that raises dies through `threading.excepthook` and also
  leaves the exit code alone. The recorder is started before the timer, both just before `run()`; an
  exit before the walker (2, 3, 4, 5) never starts either, so no `out/frames/` exists then.
  **Cleanup (PB-amend2-D1, B19-record-D1).** `main` does all cleanup in one `finally` that covers every exit path (including an
  exception from `run()`; `KeyboardInterrupt` is not caught, see below). The steps run in this order, each only if the
  thing exists or happened, and each step runs even if an earlier one raised:
  1. cancel the `--minutes` timer and join it (`cancel()` only sets an Event, the thread is still alive right after;
     done first so a timer firing during cleanup cannot stamp `"timer"` on a `max_laps` run before step 4 reads the
     StopControl);
  2. `layer.release_all()` (after a successful `connect`);
  3. `rec_stop.set()` and `join(2.0)` the `record` thread (with `--record`);
  4. write `out/stop.json` and print `stopped: ...` (after the walker ran);
  5. `link.close()` (if the link was made);
  6. `StopControl.close()` (if it was started), last.
  Through steps 1-5 a Ctrl+C or F12 still lands on StopControl (idempotent), so the cleanup cannot be cut short; after
  step 6 Ctrl+C is the normal `KeyboardInterrupt`. On the exits before the walker (2, 3, 4, 5) only the steps whose
  thing exists apply (steps 5 and 6, and step 2 once connect succeeded).
  **`StopControl.close()` (PB-amend2-D1).** `close()` undoes `start()` in this order (worker first, SIGINT last, so Ctrl+C during `close()` still lands on
  StopControl), never raises (it runs in cleanup), and is idempotent (a second call does nothing): (1) end the worker thread (no `on_stop` call unless
  `trigger()` already ran) and join it; (2) call the `remove()` that `_install_hook()` returned, once (the pattern of
  Feature A's listener: `PostThreadMessageW(hook_tid, WM_QUIT, 0, 0)` and the hook thread's `UnhookWindowsHookEx`,
  `src/input/harness/listener.py:377-388` and `:285`; without it F12 stays swallowed system-wide until the process
  ends); (3) last, restore the SIGINT handler that was installed before `start()`, with these guards: only when
  `close()` runs on the main thread (`signal.signal` raises `ValueError` elsewhere; off the main thread the worker and
  hook are still ended and SIGINT is left as it is); only when `signal.getsignal(signal.SIGINT) is` the handler object
  `start()` stored (the stored bound method, not a fresh `self._on_sigint`, which is a different object), so a handler
  someone installed later stays; and only once. A previous handler that was `None` (installed from C; `signal.signal`
  rejects `None` with `TypeError`) is restored as `signal.default_int_handler`. "Not installed" (`close()` before
  `start()`, or a `start()` that failed before reaching a step) is kept apart from a `None` previous handler, and
  `close()` undoes only what `start()` had installed.
  `StopControl.start()` called off the main thread (it raises `ValueError` from `signal.signal`) is unsettled: which
  `Failure` it returns and which exit code `main` gives are not decided here.
  **Failure exit (PB-B19).** `main` calls `walker.run()` inside a `try`. If `run()` returns a `Failure`, `main` prints
  `walk failed: <reason>` (the `Failure` value) to stderr and returns 6. If `run()` raises an `Exception`, `main`
  prints `walk crashed: <ExceptionType>: <message>` to stderr and returns 7; the exception does not escape `main`
  (`KeyboardInterrupt` is not caught: Ctrl+C is handled by `StopControl`'s SIGINT handler). On both paths the cleanup
  above still runs, in its order (timer, `release_all` if connected, recorder, `stop.json`, `link.close()`,
  `StopControl.close()`). `stop.json` is
  `{"reason": "error", "error": <text>, "trigger_ns", "trigger_source"}`, where `<text>` is the printed text after
  the prefix (the `Failure` value, or `<ExceptionType>: <message>`) and the last two are as on the other paths. `main` then prints `stopped: error` (and ` (<source>)` when a source is set), as for
  any other reason. A `RunSummary` with reason `"link_down"` is not a `Failure` and still returns 1; `"max_laps"` and
  `"stopped"` return 0.
- [ ] **Tests** (fakes for capture, link and hook; the module imports collect on Linux, hard rule 9): no `--supervised` → 2;
  `trigger()` returns in < 10 ms while `on_stop` blocks for 1 s; connect `Failure` → 4 and
  `release_all` not attempted on a link that never connected; `StopControl.trigger()` twice → `on_stop` once; end to end:
  seams patched so `_make_link` returns a wrapper around B10's `SimLink` (adds `connect() -> Success`, `close()`),
  `_make_positions` returns B10's `SimPositionSource` over the same `SimWorld` (FF2 terrain, `NoiseConfig.off()`,
  start at `(113,60)`), `_make_clock` returns the same `SimClock`, `_start_capture` a no-op, `_wait_first_frame`
  `Success(None)`, `_make_stop_control` a fake, `_countdown_s = 0`, and `input_layer._clock` / `input_layer._sleep` monkeypatched to the `SimClock`'s
  `now_ns` / `sleep` (the layer's reaction wait otherwise uses `perf_counter_ns`, G5); `main(["--map", fixture, "--supervised",
  "--laps", "1", "--out", tmp])` → exit 0, `legs.jsonl` has 8 lines, `laps.jsonl` 1, `session.json` exists, and
  the wrapper's `release_all` and `close` were each called at least once on exit, and `stop.json` is
  `{"reason": "max_laps", "trigger_ns": null, "trigger_source": null}`. `StopControl.trigger("sigint")` then
  `trigger()` → `trigger_source == "sigint"` and `trigger_ns` unchanged by the second call.
  Failure exit (PB-B19), with the same seams as the end-to-end test and the wrapper's `release_all` and `close`
  calls counted: (a) `monkeypatch.setattr(RouteWalker, "run", lambda self: Failure("boom"))` → `main` returns 6,
  stderr (`capsys`) contains `walk failed: boom`, `stop.json` is `{"reason": "error", "error": "boom",
  "trigger_ns": null, "trigger_source": null}`, and `release_all` and `close` were each called at least once;
  (b) `run` replaced by a function that raises `RuntimeError("kaboom")` → `main` returns 7 without raising, stderr
  contains `walk crashed: RuntimeError: kaboom`, `stop.json` has `"reason": "error"` and `"error": "RuntimeError:
  kaboom"`, and `release_all` and `close` were each called at least once; (c) `run` returns
  `Success(RunSummary(0, 0, "link_down"))` → `main` returns 1 and stderr contains neither `walk failed` nor
  `walk crashed`.
  Exits 3 and 5, the `--minutes` timer and `StopControl` (same seams as the end-to-end test unless stated; each test
  also asserts the exit code is an `int` returned, not raised):
  (d) exit 3: `_wait_first_frame` returns `Failure("timeout")` → 3; separately, `_make_positions` returns a source
  whose `read()` is `Failure("miss")` → 3. In both, `_make_link` was never called (a seam that records calls), and no
  `stop.json` exists (the walker never ran).
  (e) exit 5: `--map` naming a file that does not exist → 5; and, with the real default `_make_positions`,
  `CharacterLocator.from_files` monkeypatched to return `Failure("no icon")` → 5. In both, `_make_link` was never
  called.
  (f) timer: a fake stop control whose `trigger(source)` records the first source and `trigger_ns` and calls the
  `on_stop` it was built with (as the real worker would), `--minutes 0.02` and no `--laps` (the simulated walker
  would otherwise run for ever) → `main` returns 0 within 10 s wall, `stop.json` has `"reason": "stopped"`,
  `"trigger_source": "timer"` and a non-null `trigger_ns`. With `--laps 1 --minutes 60` the run ends by `"max_laps"`,
  the fake's `trigger` was never called, `stop.json` has `"trigger_source": null`, and no thread that `main` started
  is still alive afterwards (compare `threading.enumerate()` before and after), so the timer was cancelled and joined.
  (g) `StopControl` with the real class and `_install_hook` monkeypatched to a recording fake that returns a `remove`
  callable counting its calls: `start()` returns `Success`; the SIGINT handler now installed
  (`signal.getsignal(signal.SIGINT)`), called directly as `handler(signal.SIGINT, None)`, makes `on_stop` run once (within 1 s: `on_stop` runs on the worker
  thread, so the fake `on_stop` sets an `Event` that the test waits on) and sets `trigger_source == "sigint"` (set
  synchronously, no wait); each test below saves the previous handler first and restores it in a `finally`
  (a safety net on top of what `close()` does). `close()` after `start()` with no trigger: `on_stop` is never called,
  the worker thread has ended (it is not in `threading.enumerate()` after `close()` returns), `remove` was called
  exactly once, `signal.getsignal(signal.SIGINT)` is the handler saved before `start()`, and a second `close()` does
  not raise and does not call `remove` again. `close()` on a `StopControl` that was never started does not raise and
  leaves SIGINT as it was. A handler X that the test installs after `start()` is still installed after `close()`; X
  installed after the first `close()` is still installed after a second `close()`. `close()` called from a
  `threading.Thread` (after `start()` on the main thread) does not raise, leaves SIGINT as it was, ends the worker and
  calls `remove` once. A previous handler of `None` (not settable from Python, so the test
  fakes it): before `start()` the test installs its own sentinel Python handler S with the real `signal.signal`, saves
  the real `signal.getsignal` and `signal.signal`, and then `monkeypatch`es wrappers that make `signal.getsignal` and
  the return value of `signal.signal` report `None` whenever the real value is S, and otherwise delegate. After
  `start()` and `close()`, the final check uses the saved real `getsignal` (not the patched one):
  `real_getsignal(signal.SIGINT) is signal.default_int_handler`. (Keying on S, not on whatever handler pytest had,
  keeps the patched `getsignal` from reporting `None` again after a correct restore. A `close()` that restores `None`,
  which raises `TypeError`, or restores nothing, leaves StopControl's handler installed and fails the check.) The
  `finally` restores S's predecessor with the real `signal.signal`.
  (h) `--minutes` range: `--minutes 0`, `-1`, `nan`, `inf` and `1e6` each → `main` returns 2 and `_start_capture`
  was never called; `--minutes 0.5` is accepted (the test uses `--laps 1` so the run ends at once, and checks the exit
  is 0).
  (i) `--record` (B19-record-D1), same seams as the end-to-end test, with `_start_capture` replaced by a function
  that sets `shared.minimap_frame` to one RGB array under `shared.minimap_frame_lock` (so `shared_frames` is covered
  here): `main([... "--laps", "1", "--record", "--out", tmp])` → exit 0, `tmp/frames/index.jsonl` has exactly 1 line
  (the same object is not saved twice), `frame_000000.png` exists, and no thread named `record` is alive when `main`
  returns. The same with `RouteWalker.run` replaced by `lambda self: Failure("boom")` → exit 6 and no `record` thread
  alive. The same with `_make_link` returning a wrapper whose `connect` is `Failure("x")` → exit 4 and `tmp/frames`
  does not exist.
  (j) cleanup order (PB-amend2-D1, B19-record-D1): the end-to-end seams plus `--laps 1 --minutes 60 --record`; the
  link wrapper and the fake stop control append `"release_all"`, `"link.close"` and `"stop.close"` to one list; at
  the last `release_all` call (cleanup step 2; `run()` makes one of its own first, while the timer is still alive) the
  thread named `walk-timer` is no longer alive, at the `link.close` call the thread named
  `record` is no longer alive and `out/stop.json` exists, and the list ends `["release_all", "link.close",
  "stop.close"]`. The same list (without the recorder check) when `run` is replaced by one that raises
  `RuntimeError("kaboom")`.
- [ ] **Live link check (MapleStory closed; Windows, board paired).** With the game closed there is no minimap, so
  the default `_wait_first_frame` fails (exit 3) before `connect`; this check patches the seams instead.
  This check sends real keys, so it refuses to run while MapleStory runs (PB-B19), the way Feature A's `probe`,
  `soak` and `session_starts` do. They all call `soak.maple_check()` (`src/input/harness/soak.py:518`; it returns
  `(ok, text)` and is not ok if any process image name contains "maple", any case, or the `tasklist` list cannot be
  read), at `probe.py:1003` (and `:1065`), `soak.py:554` and `session_starts.py:155`. Reuse it; do not write another
  check.
  Each attempt uses a fresh folder `A = data/route/linkcheck/<UTC stamp>` (the listener appends to `keys.jsonl`,
  so a retry in the same folder would mix runs).
  0. Before the listener starts (it blocks the bot keys system-wide, so nothing may be running yet), run the guard
     alone: `python -m data.route.linkcheck.check --guard-only`. It calls `guard()` (below), which prints its text,
     and exits 0 if no MapleStory process is running, else exits 8. On 8, stop: close the game and start a new
     attempt.
  1. Start `python -m src.input.harness.listener --out A --seconds 180` (the sink window).
  2. Run a throwaway script `data/route/linkcheck/check.py` (gitignored, never committed) from the repo root as
     `python -m data.route.linkcheck.check A` (so `import src...` resolves; a plain `python data/...py` puts the
     script's folder on `sys.path` instead). It defines `guard() -> None`: `ok, text = soak.maple_check()`, print
     `text`, and if not `ok`, `raise SystemExit(8)`. Importing the script runs nothing (the work is under
     `if __name__ == "__main__"`). With `--guard-only` as its only argument it calls `guard()` and exits 0. Run as
     `... check A`, it calls `guard()` as its first statement, before it patches
     anything or touches the board, and starts a daemon thread that re-checks every `soak.PROCESS_CHECK_PERIOD_NS`
     (60 s) while `main` runs; if that re-check is not ok, the thread prints the text and calls
     `trigger("maple_check")` on the real `StopControl` (kept by the `_make_stop_control` wrapper below), which stops
     the walker and releases every key, and the script then exits 8 after `main` returns (whatever `main` returned).
     If a re-check fails before the `StopControl` exists, the thread sets an abort flag and the wrapper triggers
     `"maple_check"` as soon as it builds the `StopControl`.
     It patches `walk.py`'s seams as in the end-to-end test except: `_make_link` the real `BleLink`,
     `_make_stop_control` a wrapper that builds the real `StopControl` and keeps it for the re-check thread,
     `_make_clock` `RealClock()`, `_countdown_s` left at 5.0, and
     `input_layer._clock` / `input_layer._sleep` left at their defaults; then calls `main(["--map",
     "tests/fixtures/fossil_foundry_2.csv", "--supervised", "--laps", "1", "--minutes", "2", "--out", A + "/run"])`.
     Because the script is never committed, the implementer copies its full text into its notes file, so B19's per-unit
     review can check the guard.
  3. During the 5 s countdown click the sink window; about 30 s later press F12 once.
  Guard test (no game or board needed; run once before the first attempt; nothing is sent). Each line runs as
  `python -c "<line>"` from the repo root with the venv active, where the line is
  `from returns.result import Success, Failure; from src.input.harness import soak; soak._process_names = <X>;
  from data.route.linkcheck import check; check.guard()` with `<X>` as below:
  `lambda: Success(['MapleStory.exe'])` must exit 8 and print `MapleStory running: ['MapleStory.exe']`;
  `lambda: Failure('x')` must exit 8 and print `process check failed: x` (an unreadable list counts as running);
  `lambda: Success(['python.exe'])` must return normally and print `no MapleStory process`.
  Pass: `stop.json` has `reason "stopped"` and `trigger_source "hotkey"` (not `"timer"`); the last line of
  `legs.jsonl` has outcome `stopped` and `end_ns − trigger_ns ≤ 1e9` (both `perf_counter_ns`); in the listener's
  `keys.jsonl`, every event between the first and the last bot-key event is one of the seven bot keys or F12 (vk 0x7B;
  F12 may also be absent, since StopControl's hook may swallow it before the listener sees it, PB-D2), and every bot key that went down also went up. The simulated world
  does not follow the real keys, so legs may stall; only delivery and the stop are checked here. F12 in game is
  checked again in B21 step 7.
- **Run:** `python -m pytest -q tests/route/test_walk_cli.py`
- **Commit:** `feat(route): runtime wiring and supervised walk CLI with F12 stop`

### Task B20: Movement measurement tool

**Files:** create `src/route/harness/measure_moves.py`; tests `tests/route/test_measure_moves.py`.
**Class:** S — drives the board in game and measures settle times (timing) (a tool that drives the board in game; first run in B21 step 3). **Cloud:** no — runs against the game through B19's
wiring.
**Interface created:**

```python
@dataclass(frozen=True)
class MoveSample:
    kind: str; direction: str | None; before: tuple[int, int]; after: tuple[int, int]
    peak_y: int | None; settle_s: float; held_ms: float | None
def summarize(samples: Sequence[MoveSample]) -> dict
def main(argv: list[str] | None = None) -> int
# python -m src.route.harness.measure_moves --supervised --out data/route/measure/<stamp> [--reps 10]
```

- [ ] **Procedure** (Hong places Kanna on the floor near x 80 first): `reps` teleports alternating RIGHT/LEFT;
  `reps` walks of 2000 ms alternating RIGHT/LEFT; `reps` teleports UP from where Hong places Kanna under open space
  (the tool prompts), polling every 10 ms during settle for the smallest y (`peak_y`); `reps/2` teleport-downs and
  `reps/2` drops from a droppable platform (prompted). Each sample is appended to `measure.jsonl`.
- [ ] **`summarize`** returns `reach_h_mean`, `reach_h_sd`, `reach_h_n` (|Δx| of horizontal teleports with
  |Δy| ≤ 2), `reach_v_peak_mean` (from `peak_y` when present), `walk_px_per_s` (Σ|Δx| / Σheld s of walks),
  `teleport_settle_s`, `drop_settle_s` (medians).
- [ ] **Tests:** `summarize` on synthetic samples: 10 teleports of Δx ±19 and ±20 alternating → mean 19.5, n 10;
  a teleport with |Δy| 5 is excluded; walks of 16 px over 2000 ms → 8.0 px/s; `main` without `--supervised` → 2.
- **Run:** `python -m pytest -q tests/route/test_measure_moves.py`
- **Commit:** `feat(route): movement measurement tool for the supervised session`

### Task B21: Supervised in-game session — reach measurement, sim realism, sign-off (human-only)

**Files:** none in `src/` unless step 4 or 5 applies (then `src/classes/classes.py` and/or `src/route/params.py`,
`src/route/sim/world.py` defaults, `tests/fixtures/ff2_terrain.json`); results recorded in the B22 docs.
**Class:** S — live control loop in the game with Hong. **Cloud:** no — MapleStory, the board, Bluetooth, real
frames.
**Interface created:** none.

- [ ] **Step 1: preconditions.** B14's 1000-lap run passed; B18 passed; Hong is at the PC; Kanna on FF2 on monitor 2;
  "Guiness" paired; the Windows full suite passes.
- [ ] **Step 2: record.** `python -m src.route.harness.record_minimap --out data/recordings/ff2_<date>_moving
  --seconds 120` while Hong moves Kanna by hand around all four platforms.
- [ ] **Step 3: FF2 reach measurement** (specB:L13, H3). `python -m src.route.harness.measure_moves --supervised --out
  data/route/measure/<UTC stamp> --reps 10`. Report the summary.
- [ ] **Step 4: reach decision.** If `reach_h_mean` differs from 19 by more than 1 px, or `reach_v_peak_mean` (when
  measurable) from 23 by more than 1 px: change `Kanna`'s value(s) in `classes.py` (the one place), re-run the full suite and update the reach- and
  default-dependent FF2 numbers in the tests of B2-B7 and B12-B14 in the same commit (the measurement in the body),
  then re-run B14's 1000-lap run.
  Per-map reach is built only if a second map's measurement shows it is needed (specB:L13).
- [ ] **Step 5: sim realism.** Compare `walk_px_per_s`, `teleport_settle_s`, `drop_settle_s` and the step-2 platform
  positions with `SimParams`, `MovementParams` and `ff2_terrain.json`. Any value off by > 20%, or a platform end off
  by > 3 px: update the default or fixture, re-run the full suite (updating default-dependent numbers in B2-B7 and
  B12-B14 tests), re-run B14 (1000 laps) and paste the result.
- [ ] **Step 6: detection on moving frames.** B18 `draft` + Hong's check + `check` on step 2's frames → PASS.
- [ ] **Step 7: short runs.** `python -m src.route.walk --map maps/fossil_foundry_2.csv --supervised --laps 1`
  twice, then the same with `--laps 10 --record`. Hong watches; F12 aborts
  at any sign of trouble. In the first run Hong presses F12 once mid-leg on purpose: `stop.json` reads `reason "stopped"`,
  `trigger_source "hotkey"`, the last `legs.jsonl` line has outcome `stopped` with `end_ns − trigger_ns ≤ 1e9`, and
  Hong sees no key stay held in game (then restart it).
- [ ] **Step 8: sign-off** (specB:L68): 10 consecutive laps with no manual intervention, read from `laps.jsonl`
  (`all_reached` true for 10 consecutive laps) and Hong's word. Only then may Hong merge (H4).
- **Commit (only if step 4 or 5 changed files):** `fix(route): use movement values measured on FF2` (body: the
  measurements and the re-run outputs).

### Task B22: Docs

**Files:** modify `CLAUDE.md` (a "Route walking" paragraph under Architecture and the new commands), spec B (append
"As built": the results of B14, B18 and B21, measured reach and movement values, any deviation).
**Class:** M — documentation of a finished state. **Cloud:** no — not a Python module (Appendix B's rule), and it
records B21's in-game results.
**Interface created:** none.

- [ ] Every number written comes from a B14/B18/B21 output quoted in the commit body.
- **Run:** `python -m pytest -q` (Windows) still passes.
- **Commit:** `docs(route): document route walking as built and the FF2 measurements`

---

## Appendix A: `tests/fixtures/fossil_foundry_2.csv` (LF transcription of the user's `maps/fossil_foundry_2.csv`; the editor writes CRLF, `map_csv.py:163`, and both parsers read either)

```
# minimap_size=162,82
nodetype,pos_in_minimap,can_fall,fall_dist_exceeds_vertical_tp,is_left_platform_edge,is_right_platform_edge
blue_node,"(113, 60)",FALSE,FALSE,FALSE,FALSE
blue_node,"(82, 60)",FALSE,FALSE,FALSE,FALSE
blue_node,"(48, 61)",FALSE,FALSE,FALSE,FALSE
blue_node,"(58, 46)",TRUE,FALSE,FALSE,FALSE
blue_node,"(49, 28)",TRUE,FALSE,FALSE,FALSE
blue_node,"(101, 24)",TRUE,FALSE,FALSE,FALSE
blue_node,"(105, 42)",TRUE,FALSE,FALSE,FALSE
orange_node,"(65, 60)",FALSE,FALSE,FALSE,FALSE
orange_node,"(99, 60)",FALSE,FALSE,FALSE,FALSE
orange_node,"(129, 60)",FALSE,FALSE,FALSE,FALSE
orange_node,"(48, 45)",TRUE,FALSE,FALSE,FALSE
orange_node,"(74, 45)",TRUE,FALSE,FALSE,FALSE
orange_node,"(42, 28)",TRUE,FALSE,FALSE,FALSE
orange_node,"(66, 28)",TRUE,FALSE,FALSE,FALSE
orange_node,"(79, 24)",TRUE,FALSE,FALSE,FALSE
orange_node,"(90, 24)",TRUE,FALSE,FALSE,FALSE
orange_node,"(109, 23)",TRUE,FALSE,FALSE,FALSE
orange_node,"(118, 43)",TRUE,FALSE,FALSE,FALSE
orange_node,"(97, 43)",TRUE,FALSE,FALSE,FALSE
orange_node,"(37, 60)",FALSE,FALSE,FALSE,FALSE
orange_node,"(105, 60)",FALSE,FALSE,FALSE,FALSE
```

---

## Sources

- Spec B, spec A "Timing model (as built)", spec D goals 3-4 and "Trail" row, `docs/vision.md`, `docs/decisions.md`
  H3/H4/H9, `CLAUDE.md` — read 2026-10-23 at 2413c64.
- Code cites in the Grounding table — read 2026-10-23 at 2413c64.
- The FF2 output above and the B4/B5 numbers — produced 2026-10-23 on Linux (Python 3.14.0, the pinned
  `requirements-dev.txt` minus winrt) from Appendix A, with a throwaway prototype of C4/C5 outside the repo.

## Not verified at planning time

1. **Movement values.** `walk_px_per_s` 8.0, `teleport_s` 0.55, `drop_base_s` 0.35, `fall_px_per_s` 60 and all
   `SimParams` are estimates, not measurements. B14 test 3 checks robustness to ±20% walk speed; B20/B21 measure them.
2. **Teleport semantics.** That a horizontal teleport always travels the full reach, that teleport UP lands on the
   first platform at or below the end point, and that teleport DOWN passes through the current platform (including
   a non-droppable one) are assumptions built into `SimWorld`. B21 step 2/7 shows the truth.
3. **FF2 platform extents** in `ff2_terrain.json` (DC8) and that the floor is solid across 30-135. Mid-left is
   drawn to x 80 so that the inferred DOWN link `(79,24)→(74,45)` lands on it in the simulator; whether it reaches
   that far in the game is unknown.
4. **Vertical reach on FF2** may not be measurable by landing, because every platform above another is less than
   23 px away; B20 measures the peak during the teleport, which may also fail. Then 23 stays and B21 says so.
5. **Detection method.** Whether masked `TM_CCORR_NORMED` (today's method) separates the 10×10 icon from minimap
   backgrounds well enough, and whether OpenCV 4.13's `matchTemplate` supports a mask for the other methods. B15
   decides on synthetic frames and B18 on real ones.
6. **Stale frames.** How often the capture thread delivers no new frame between two 50 ms polls (B16 handles it as
   `stale`; the rate is unknown).
7. **1000-lap wall time** in the simulator (estimated well under 15 min, not run).
8. **F12** in MapleStory: whether it is bound in Hong's key config (the hook swallows it, PB-D2).
9. **Feature D** (spec D goal 3) will want the walker's current leg and state at incident time; B exposes it
   only through the leg log. D adds a read-only accessor when it is planned.
10. **The 307 recorded frames** show the character standing still, so they contain few distinct positions; the
   detection target on moving frames depends on B21.

## Open questions

```
DECISION NEEDED: PB-D1
Question: Should terrain inference live in a separate RouteGraph layer (PathGraph unchanged), leaving the map
editor's edge preview without the inferred slope and near-vertical edges?
Why it matters: CLAUDE.md says the editor preview "matches what the runtime builds"; with option A the runtime
walks on 51 FF2 edges while the editor still previews PathGraph's 40. B4-B7 and everything after build on the choice.
Options:
  A. Separate layer (src/route/terrain.py + RouteGraph), PathGraph and editor unchanged; a later task can draw
     route edges in the editor — cost: low effort; editor preview no longer shows every edge the bot uses; reversible
     by one editor task.
  B. Put the rules into PathGraph so the editor preview shows them too — cost: changes PathGraph's characterized
     behaviour (B3 tests rewritten), touches the GUI (Cloud no), more risk; hard to undo once the editor relies on it.
Recommendation: A — keeps PathGraph and the editor stable while the rules are proven in the simulator.
Paused meanwhile: nothing · Continued: plan uses A.
made provisionally · undo: B4, B5 (move the rules into path_graph.py) plus a new editor task
```

```
DECISION NEEDED: PB-D2
Question: How does Hong stop a supervised run while the game window has focus?
Why it matters: Ctrl+C reaches only the console, and the game must be focused for the keys to land; without a
global stop key the only stop is closing the window or pulling the board. Changes what Hong does in B19/B21.
Options:
  A. F12 through a low-level keyboard hook that swallows F12 (Feature A's listener pattern, G33), plus Ctrl+C and
     --laps/--minutes bounds — cost: one hook thread (Windows-only code); F12 never reaches the game during a run.
  B. Ctrl+C and --laps/--minutes only — cost: none to build; Hong must alt-tab to stop, slow in an emergency.
Recommendation: A — an instant stop matters more than the key; any other key is a one-line change (hotkey_vk).
Paused meanwhile: nothing · Continued: plan uses A with F12.
made provisionally · undo: B19 (StopControl)
```

```
DECISION NEEDED: PB-D3
Question: When both teleport-down and drop are possible and their estimated times are close, should the bot pick
at random (seeded) instead of always the cheaper one?
Why it matters: with the estimates, teleport-down (0.55 s) always beats drop (~0.65 s) on FF2, so a deterministic
bot would never drop on this map: spec B's drop ability would go untested in sim and game, and every lap would use
identical moves (vision.md asks for no identical repeated loops). It changes what the character does in game.
Options:
  A. Always the cheaper option (ties: teleport) — cost: none; drop unused on FF2; laps more repetitive.
  B. Random 50/50 when the costs are within ability_mix_band (30%), else the cheaper — cost: small (one rng);
     drop exercised every lap on FF2; reversible by setting ability_mix_band = 0.
Recommendation: B — exercises both abilities spec B lists and varies the route, at no cost to reliability.
Paused meanwhile: nothing · Continued: plan uses B (C3 ability_mix_band 0.3, C7 P5, B14 "abilities" check).
made provisionally · undo: B2 default ability_mix_band = 0 (and drop B14's "abilities" check)
```

```
DECISION NEEDED: PB-D4
Question: How is a stop that lands between the walker's stop check and play(), including InputLayer's reaction
sleep, kept from sending a full move after Hong presses F12?
Why it matters: release_all() pre-empts only a play already in flight (G6), and InputLayer sleeps the reaction time
before it calls play (G5), so without a guard up to one whole move (≤ 4 s of held keys) can follow a stop. This
changes the concurrency of the stop path (raised by review round 1, PB-rev1-D2).
Options:
  A. The walker re-checks the stop flag right before execute, and stop_now() starts a guard thread that calls
     release_all() every 50 ms until run() has returned — cost: ~15 lines and one race test (B13 test 3); no change
     to Feature A; extra RELEASE_ALL commands on the link for at most the remaining run time.
  B. Accept at most one move after a stop and document it — cost: none; F12 is not immediate.
Recommendation: A — a stop key that may be followed by a 4 s move is not a stop.
Paused meanwhile: nothing · Continued: plan uses A (C10, B12 step 7, B13).
made provisionally · undo: B13 (remove the guard thread and its test)
```

## Review record

| Round | Reviewer (model, effort) | C / I / m | What changed |
|---|---|---|---|
| 1 | PB-rev1, Opus 5.5 (`claude-opus-5-5`), effort high requested (reported: not shown) | 0 / 8 / 12 | I1: B2↔B3 swapped so params are stage 1; deps fixed. I2: hard rule 9 + function-level Windows imports. I3: stop re-check + guard thread, new decision PB-D4, race test. I4: `StopControl.trigger()` non-blocking + test. I5: B18 reads B17 folders, BGR→RGB. I6: recovery also judges multi-error legs (extra ≤ 3·k). I7: B16→O, B17→S, B20→S; M shortfall (5) stated. I8: "at a node" rule, P6 removed, `node_offset_px` sim option + tests. Minors 9-20 fixed (spec cites re-mapped, counts, SD 210, G14/G21 cites, A3 height check, approx, noise test bands, tolerances, LF transcription wording, deps, merge wording). |
| 2 | PB-rev2, Opus 5.5 (`claude-opus-5-5`), effort high requested (reported: not shown) | 0 / 5 / 12 | I1: `--min-single-error-legs` CLI arg; test (5) checks 6 PASS/FAIL lines. I2: `known_cases` and `--known-case` parameters, no FF2 positions in `run.py`. I3: `run` joins the stop guard before returning; stop checked during settle. I4: B19 seams named, end-to-end test fully specified. I5: B18 `load_frames`. Minors: test (1) `no_op_p=0.08`; (3b) caveat and cite; B21 re-run scope; `down_options` args; slope edges listed in B5; drop's current platform; replay graph and leg split; `NoiseConfig.off()` base in B12.4; `Accepted(seq, 0)`; `--class` arg; B15 Cloud rationale; Feature D accessor noted. |
| 3 | PB-rev3, Opus 5.5 (`claude-opus-5-5`), effort high requested (reported: not shown) | 0 / 1 / 10 | I1: world starts at the unshifted first node; known cases matched on unshifted positions. Minors: B19 usage line, `--class`, exit codes 0-5, locator `Result` unwrapped, `release_all` only after connect, layer clock seams in the end-to-end test; 0/0 recovery rule; Euclidean > 3 px in `score` and B15.5; stop-guard lock; teleport overshoot at platform ends stated as known behaviour; walk cannot cross a gap in the sim; B6 recipe inlined; `accepted=None` for RELEASE_ALL; who pushes the branch. |
| 4 | PB-rev4, Opus 5.5 (`claude-opus-5-5`), effort high requested (reported: not shown) | 0 / 2 / 9 | I1: B16 → S (counts O 6, S 11). I2: B16 test uses a lock wrapper (`threading.Lock` cannot be subclassed on 3.14). Minors: `direction = Key.name` + test; overshoot covered by existing checks; B7/B12/B13 build recipes and start; B21 commands in full; `StopControl._install_hook` seam and worker start; known cases by rotation index; B15 Cloud reading recorded; H8/H10 cited; mid-left drawn to x 80. |
| 5 | PB-rev5b, Opus 5.5 (`claude-opus-5-5`), effort high requested (reported: not shown) | 0 / 2 / 5 | I1: C7 P5 gains a `WALK` case (g′ advanced along WALK edges, P4 off-slope rule) for a character at a slope-segment start; B7 test 10. I2: B19 live smoke could never pass (no minimap with the game closed → exit 3); now a link check through the seams with the real `BleLink` and `StopControl`, and an F12 check added to B21 step 7. Minors: B13 tests name the position source and world start, test 4 arms `fail_next` from `plays`; B8 copies `FakeLink`/`StubModel`; B14 writes shifted rows to a temporary CSV; B18 `"absent"` label counts found positions as wrong; B5 `rg.path` takes nodes. **Not approved after 5 rounds**; the round-5 fixes are unreviewed. |
| 6 | PB-rev6, Opus 5.5 (`claude-opus-5-5`), effort high requested (reported: not shown), scoped to the round-5 fixes | 0 / 1 / 5 | Round-5 fixes confirmed (B7 test 10 re-derived: TELEPORT RIGHT toward (101,24), d = 24). I1: B19 link check made checkable: listener `--seconds 180`, 5 s countdown kept, layer clocks left at defaults, script location named; `StopControl.trigger(source)` records `trigger_ns`/`trigger_source`; `main` writes `stop.json` and prints the reason; pass criteria from `stop.json`, `legs.jsonl` and `keys.jsonl`. Minors: `SimLink.plays` defined, B13 wrapper delegates `release_all`; B18 `score` signature widened, absent frames out of `hit_rate`; B21 step 7 reads `stop.json`/`legs.jsonl`; typo. Round-6 fixes unreviewed. |
| 7 | PB-rev7, Opus 5.5 (`claude-opus-5-5`), effort high requested (reported: not shown), scoped to the round-6 fixes | 0 / 0 / 5 | **APPROVED.** Minors applied: B10 test 3 checks `plays == 2`; B18 test with absent frames and `max_err_px` scope; B19 link check allows F12 in `keys.jsonl`, uses a fresh `<UTC stamp>` folder per attempt and runs its script with `python -m`; `_make_stop_control` seam lists its members, `trigger`'s first-call record is under a lock; the stop line prints the source only when set. |
