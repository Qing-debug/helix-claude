# Walk fluidity (overshoot limit, platform-end teleports, fresher pictures) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build Hong's three walking fixes of 2026-11-13: item 1 (walk, don't teleport past, a node closer than 17 px), item 4 (sideways teleports stop at a marked platform end or carry onto a same-height platform; no walks across gaps; the simulator lands the same way) and item 2 (fresher, time-stamped pictures), with item 3 left as a later task that needs his next walk.

**Architecture:** Item 1 and item 4 live in the route planner's frozen files (`params`, `terrain`, `route_graph`, `planner`) plus the simulator's world (`sim/world.py`): one new setting each (`walk_under_px`, `side_teleport_land_dy_px`), one new edge kind (`EdgeKind.GAP`, a sideways teleport between two platforms split by the map's platform-end marks) and one landing function (`RouteGraph.side_landing`) that the planner and the graph cost share; the simulated world gets its own copy of the landing rule. Item 2 lives in the vision capture, the shared frame, the position source, the walker's waits and the recorder (Tasks 6 onward, piece 2).

**Tech Stack:** Python 3.14, networkx 3.6.1, numpy, returns, pytest; the route simulator (`src/route/sim/`).

**Spec:** Hong's decision, verbatim, in `C:/Users/Qing_programming/.claude/orchestrator/question-bank.md` QB266 (item 1), QB283 (items 2 and 3), QB282 (item 4), the `Answer (verbatim)` lines; order and done rule in the phase-1 workspace `ledger-ROPE.md`, line starting `Decision (Hong) (chat paste`. Background: workspace `notes/FLUID164.md` (the overshoot, option A measured), `notes/q-20261113-153031-26c9-qa-answer.md` (teleports are 18-19 px; the sim fixture question), `notes/q-20261113-155322-cd0c-qa-answer.md` (FF2's merged middle platforms, the edge-marked nodes). Workspace = `C:/Users/Qing_programming/Desktop/helix-claude-clone/.superpowers/orchestrator/2026-10-21-phase1`.

## Global Constraints

- Hong's order: "1, 2 and 4 can be built now (2's no-pause part is final only after the key-timing check). Then my 10-lap walk ... Then 3." Item 1 does not need item 2, so it is Task 1.
- Done, for each item (Hong): "tested in the simulator, every check passing, reviewed by a second agent, and checked by my own walk". So every task that changes behaviour has a simulator test, and `python tools/check.py` is green at the end of every task; the last task runs `python tools/check.py --full`.
- Item 1 is kept only if Hong's next FF2 10-lap walk is "no slower than 221 s"; otherwise the 17 px limit moves "in the direction my walk shows is faster ... or undo it". So the limit is a named setting, `MovementParams.walk_under_px = 17`, never a literal in a rule.
- Item 1: "No allowance for sliding: the character does not slide, the picture is late, so do not aim walks short." No task shortens a walk.
- Item 4's height range is a setting: "call it 4 px up or down for now, as a setting" -> `MovementParams.side_teleport_land_dy_px = 4` (planner) and `SimParams.side_teleport_land_dy_px = 4.0` (world), copies by value like every other `SimParams`/`MovementParams` pair (src/route/CLAUDE.md, "Traps", SimParams bullet: don't merge them).
- Where the teleport lands on the other platform is not yet measured (Hong: "Measure the height range and where exactly the teleport lands on the other platform from a later walk"). The planner and the simulator assume the full reach from the launch x, clamped to the other platform's far end.
- Frozen files (src/route/CLAUDE.md "Traps", first bullet): `params`, `terrain`, `route_graph`, `locate`, `planner`, `moves`, `walker` and their tests. Hong's paste is his yes to change those that these items need; each task that opens one quotes the sentence that opens it. Change only what the task names; new tests go in new test files; frozen test files get re-pinned values only.
- Plan hard rule 4 (`tests/route/test_import_rules.py:39`): no integer literal 19 or 23 anywhere under `src/route/`. The reach is `rg.reach_h` / `self.reach_h`. 17 and 4 are allowed (they are settings' defaults).
- `route_preview._plan` repeats `planner.next_move` statement for statement (src/route/CLAUDE.md "Seams", planner bullet); every change to `next_move` or to an imported helper's signature is mirrored there in the same task, and `tests/route/test_route_preview.py`'s `_BRANCH_LINES` line numbers move with it.
- New `MovementParams` fields get defaults, so an old step log's header still loads (`MovementParams(**header.params)` in the viewer).
- Safety (root CLAUDE.md): no agent sends a key, starts the app, runs the walk CLI or `harness/measure_moves.py`, or runs `probe`/`soak`/`session_starts` for this plan except the HONG-GATED task, and there only after the orchestrator has Hong's go; the agent never answers the tool's "Is MapleStory closed? y/n" on its own judgment (H66). The 10-lap walk is Hong's.
- Never edit files in `C:/Users/Qing_programming/Desktop/helix-claude-clone` itself; work only in the worktree. Reading files there (Hong's run folder) is fine. Never open any `keys.jsonl`.
- Commit messages: conventional commits with a scope, e.g. `feat(route): ...`, `test(sim): ...`, ending with the trailer the implementer's own harness gives.
- After each task the implementer appends to workspace `notes/WALKFIX169-build.md`: task id, commit sha, and the new figures of every sim run the task re-pinned (smoke seed 1, real-ground seed 2, 1000-lap seed 1, marked-map seed 2), so the docs task can update CLAUDE.md from one place.

## Review Focus

1. A map with no platform-end marks at all (the test fixture `tests/fixtures/fossil_foundry_2.csv`, older maps): platforms, edges and the sideways-teleport rule must be exactly today's apart from item 1. Task 2 pins "no GAP edge and the same platforms on the fixture"; Task 3 pins "no mark, full reach".
2. A teleport toward a marked end node that would carry onto the next platform (the character is within reach of the next platform's start): the planner must not count it as "stops on the end node". Task 3 pins `test_carry_over_is_not_a_stop_on_the_end`.
3. A lower platform beyond the end (more than 4 px down): planner and simulator stop at the end, never fall to it. Tasks 3 and 4 each pin it.
4. Node objects built without the mark arguments (`is_left_platform_edge=None`, the `Node` default, `src/controller/node.py:11`): every mark read goes through `bool(...)`. Task 2 pins a `None`-marked map.
5. A character read just past a marked end (the real ground reaches 80 where the end node is 78 on Hong's map): `locate._beyond_end` (locate.py:68) still anchors it to that platform and the planner plans from it. Task 3 pins `test_read_two_px_past_a_marked_end_plans`.

---

## Task 0: Setup (worktree, venv, baseline)

Already done by the planner: worktree `C:/Users/Qing_programming/Desktop/helix-fluid` on branch `p1-walk-fluid` off `p1b-route-walking` at 026d636; this plan is its first commit.

- [ ] **Step 1: Link the main repo's venv into the worktree (a junction; `.venv/` is gitignored, `.gitignore:8`)**

Run (Git Bash): `cmd //c mklink /J "C:\Users\Qing_programming\Desktop\helix-fluid\.venv" "C:\Users\Qing_programming\Desktop\helix-claude-clone\.venv"`
Expected: `Junction created for ...`. Remove it at the very end with `cmd //c rmdir "C:\Users\Qing_programming\Desktop\helix-fluid\.venv"` only: a recursive delete would follow the junction into the main clone's venv (SWEEP165 gotcha).

- [ ] **Step 2: Baseline**

Run (worktree root): `HELIX_NO_ELEVATE=1 .venv/Scripts/python.exe tools/check.py`
Expected: all steps pass (10 skips in `tests/input/test_protocol.py`). Then the three sim runs and record their figures in `notes/WALKFIX169-build.md` as "baseline": smoke `python -m src.route.sim.run --laps 20 --seed 1 --min-single-error-legs 0` (371 moves, 20 no-ops, 237.642 s, 6/6), real ground `... --seed 2 --min-single-error-legs 0 --real-ground` (364 moves, 241.6268 s, 6/6), 1000 laps `python -m src.route.sim.run --laps 1000 --seed 1 --known-case 49,28:101,24 --known-case 101,24:105,42` (18359 moves, 11891.9525 s, 6/6).

---

## Task 1: Item 1, walk to a node closer than 17 px (subtle: yes; size: M)

Frozen-file opening (Hong, QB266): "Build option A: when the next node is closer than 17 px (one 19 px teleport minus the 2 px the walker allows for reaching a node), walk to it instead of teleporting past it; longer gaps still teleport."

**Files:**
- Modify: `src/route/params.py:10-31` (one field)
- Modify: `src/route/planner.py:93-99` (`_run`)
- Modify: `src/route/route_graph.py:36-43` (`horizontal_cost_s`)
- Test (new): `tests/route/test_walk_limit.py`
- Re-pin only: `tests/route/test_route_graph.py:166` (contract values), `tests/route/test_planner.py`, `tests/route/test_route_preview.py` (`_BRANCH_LINES` if lines moved), `tests/route/test_sim_real_ground.py:65-66`, any other test whose pinned sim figures move (`tools/check.py` names them)

**Interfaces:**
- Produces: `MovementParams.walk_under_px: int = 17`; `_run(rg, px, goal_x)` keeps its signature in this task (Task 3 changes it); `horizontal_cost_s(dx_px, reach_h, p)` keeps its signature in this task (Task 2 adds a keyword).

- [ ] **Step 1: Write the failing tests** in `tests/route/test_walk_limit.py`. Build the route graph the way `tests/route/test_planner.py` builds its flat-platform graphs (Kanna, `MovementParams()` defaults): one flat unmarked platform with nodes (30, 50), (45, 50), (60, 50) (15 px apart: terrain and PathGraph link only nodes within the 19 px reach). From (44, 50) and (43, 50) the cheapest start is (45, 50) and the walk run ends at (60, 50); from (47, 50) the start is (60, 50) itself.

```python
def test_under_the_limit_walks():          # d = 16
    assert next_move(rg, (44, 50), node_60, rng).unwrap().move == Move(MoveKind.WALK, Key.RIGHT, 1280.0)

def test_old_overshoot_range_now_walks():  # d = 13: teleported and walked back before
    assert next_move(rg, (47, 50), node_60, rng).unwrap().move == Move(MoveKind.WALK, Key.RIGHT, 1040.0)

def test_at_the_limit_teleports():         # d = 17: lands 2 px past, within arrive_px
    assert next_move(rg, (43, 50), node_60, rng).unwrap().move == Move(MoveKind.TELEPORT, Key.RIGHT, None)

def test_the_limit_is_a_setting():         # walk_under_px=13 brings the old overshoot back
    rg13 = graph_with(MovementParams(walk_under_px=13))
    assert next_move(rg13, (47, 50), node_60_of(rg13), rng).unwrap().move.kind == MoveKind.TELEPORT

@pytest.mark.parametrize("d, expected", [(16, 1.28), (13, 1.04), (17, 0.55 + 2 / 12.5), (30, 0.55 + 11 / 12.5),
                                         (38, 1.10), (40, 1.10 + 2 / 12.5), (0, 0.0)])
def test_horizontal_cost_follows_the_limit(d, expected):
    assert horizontal_cost_s(d, 19, MovementParams()) == pytest.approx(expected, abs=1e-9)
```
(The test file may write 19: rule 4 covers `src/route/` only.) Plus `test_sim_teleports_no_node_under_the_limit`: `run_sim(CSV, ff2_terrain.json, 3, 1, out_dir=tmp_path / "run")` on the default fixture, then read `steps.jsonl` with `step_log.read_run` and assert that for every `planned` event whose move is a LEFT/RIGHT teleport, `abs(toward_x - pos_x) >= 17` (field names from `step_log.FIELDS`, step_log.py:62).

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/Scripts/python.exe -m pytest tests/route/test_walk_limit.py -q`
Expected: FAIL (`walk_under_px` unknown; d = 13 and 16 teleport; cost values differ).

- [ ] **Step 3: Implement**

`params.py`: add `walk_under_px: int = 17  # QB266 (2026-11-13): a node closer than this is walked to, not teleported past` after `walk_px_per_s`. `planner._run`: `if d < p.walk_under_px: return _walk(...)` before the existing cost test; the cost test stays. `horizontal_cost_s` mirrors `_run` applied hop by hop (this drops the "overshoot and walk back" term FLUID164 named):

```python
d, total = abs(dx_px), 0.0
v, t = p.walk_px_per_s, p.teleport_s
while d >= p.walk_under_px and t + abs(d - reach_h) / v < d / v:
    total += t
    d = abs(d - reach_h)
return total + d / v
```
(The cost test ends the loop for every d below 13 with the default numbers, so it cannot cycle; `test_the_limit_is_a_setting` covers a lowered limit.) Update the docstrings of both functions to say this.

- [ ] **Step 4: Run the new tests, then the route tests, and re-pin**

Run: `.venv/Scripts/python.exe -m pytest tests/route -q`
Expected: the new tests pass. Frozen tests that pin the old overshoot (`test_route_graph.py:166` contract values, `test_planner.py` cases with 13 <= d < 17, `_BRANCH_LINES`) and pinned sim figures fail: re-pin each to the value the new rule gives, changing nothing else in those files. FLUID164's measurement of a planner-only version: smoke 348 moves, about 257.9 s, 6/6; 1000 laps 6/6 with walk-backs 1389 -> 220. Expect figures near those (the graph cost also changes here). If any run fails a check (6/6) or the 1000-lap stall check, stop and report: it is not re-pinned away.

- [ ] **Step 5: Sim runs, check, commit**

Run the three Task 0 sim runs; each must print 6/6 PASS; record the figures in `notes/WALKFIX169-build.md`. Run `HELIX_NO_ELEVATE=1 .venv/Scripts/python.exe tools/check.py` -> green.
```bash
git add src/route/params.py src/route/planner.py src/route/route_graph.py tests/route
git commit -m "feat(route): walk to a node closer than 17 px instead of teleporting past it (WALKFIX Task 1, QB266 A)"
```
Done: the tests above pass, the three sim runs 6/6, `tools/check.py` green.

---

## Task 2: Item 4a, split platforms at the marked ends; a sideways teleport over a gap is a GAP edge (subtle: yes; size: M)

Frozen-file opening (Hong, QB282): "Build it: the walker reads the platform-end marks on the map's nodes to know where platforms end (today it treats the two middle platforms of Fossil Foundry 2 as one, across the gap), stops planning walks across gaps, and plans sideways teleports by this rule."

The rule (Hong, QB282): "a sideways teleport goes up to its full 19 px; it stops at the end of the platform I am on; if another platform starts within 19 px in that direction and sits at about the same height (call it 4 px up or down for now, as a setting), it lands on that platform instead; a much lower platform does not count, the teleport stops at the edge."

**Files:**
- Modify: `src/route/params.py` (one field), `src/route/terrain.py:55-79` (`_walk_links`), `src/route/route_graph.py` (`EdgeKind`, `build` at :100-109, `_edge_cost`, two new methods, one new function, `horizontal_cost_s` keyword)
- Test (new): `tests/route/test_platform_ends.py`
- Re-pin only: `tests/route/test_terrain.py`, `tests/route/test_route_graph.py` if they pin anything that moves (they should not: their maps have no marks)

**Interfaces:**
- Consumes: Task 1's `horizontal_cost_s`.
- Produces (Task 3 and Task 5 rely on these exact names):
  - `MovementParams.side_teleport_land_dy_px: int = 4`
  - `EdgeKind.GAP = "gap"`: a sideways teleport from one platform onto another (only between platforms split by marks; there is no other way two nodes within reach and aligned end up on different platforms).
  - `def is_marked_end(node: Node, sign: int) -> bool` in `route_graph.py`: `bool(node.is_right_platform_edge)` for `sign > 0`, `bool(node.is_left_platform_edge)` otherwise.
  - `RouteGraph.side_landing(self, platform: int, px: int, sign: int) -> tuple[int, int]`: `(x, platform index)` where a sideways teleport from `px` on `platform` toward `sign` (+1 right, -1 left) stops.
  - `RouteGraph.gap_launch(self, u: Node, v: Node) -> int | None`: the x on `u`'s platform to teleport from so the teleport lands on `v`'s platform, or None when none does.
  - `horizontal_cost_s(dx_px, reach_h, p, *, to_end: bool = False)`.

- [ ] **Step 1: Write the failing tests** in `tests/route/test_platform_ends.py`. Map M (Kanna, defaults; neighbours 16 px apart, inside the 19 px reach that terrain and PathGraph links need): platform A nodes (24, 50), (40, 50) with (40, 50) marked right end; platform B nodes (50, 52) marked left end, (66, 52). Map M0: the same four nodes with no marks. Map MN: the same with every mark `None`. `M_b_at(y)`: M with B's two nodes at height `y`.

```python
def test_marks_split_the_platforms():
    assert len(terrain_of(M).platforms) == 2 and len(terrain_of(M0).platforms) == 1
    assert len(terrain_of(MN).platforms) == 1

def test_the_aligned_pair_across_the_gap_is_a_gap_edge():   # PathGraph links (40,50)-(50,52): dy 2 <= align 2
    rg = route_graph_of(M)
    assert rg.graph.edges[n40, n50]["kind"] == EdgeKind.GAP and rg.graph.edges[n50, n40]["kind"] == EdgeKind.GAP
    assert rg.graph.edges[n40, n50]["cost_s"] == pytest.approx(9 / 12.5 + 0.55)   # walk 40 -> 31, teleport to 50

def test_side_landing():
    rg = route_graph_of(M); a, b = platform_of(n24), platform_of(n50)
    assert rg.side_landing(a, 40, -1) == (21, a)     # A's left end is not marked: full reach, as today
    assert rg.side_landing(a, 28, +1) == (40, a)     # B starts 22 px away: stops at the marked end
    assert rg.side_landing(a, 33, +1) == (52, b)     # B starts 17 px away, 2 px lower: carries onto B
    assert rg.side_landing(b, 60, -1) == (50, b)     # A's end is 20 px away: stops at B's marked left end
    assert rg.side_landing(b, 55, -1) == (36, a)     # A's end 15 px away, 2 px higher: carries onto A
    assert route_graph_of(M0).side_landing(0, 33, +1) == (52, 0)  # no marks: full reach, as today

def test_a_platform_more_than_4_px_off_does_not_count():   # 4 px off carries, 5 px off stops
    assert route_graph_of(M_b_at(54)).side_landing(a, 33, +1)[1] == b
    assert route_graph_of(M_b_at(55)).side_landing(a, 33, +1) == (40, a)

def test_gap_launch():   # asked only for existing GAP edges
    rg = route_graph_of(M)
    assert rg.gap_launch(n40, n50) == 31 and rg.gap_launch(n50, n40) == 59

def test_fixture_map_is_unchanged():   # tests/fixtures/fossil_foundry_2.csv has no marks
    rg = route_graph_of_fixture()
    assert all(kind != EdgeKind.GAP for _, _, kind in rg.graph.edges(data="kind"))
    assert [len(p) for p in rg.terrain.platforms] == BASELINE_PLATFORM_SIZES   # computed at 026d636 and written in

@pytest.mark.parametrize("d, expected", [(8, 0.55), (25, 1.10), (17, 0.55), (0, 0.0)])
def test_cost_to_a_marked_end(d, expected):   # the last hop onto an end node is a teleport, even under 17 px (Task 3)
    assert horizontal_cost_s(d, 19, MovementParams(), to_end=True) == pytest.approx(expected)
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/Scripts/python.exe -m pytest tests/route/test_platform_ends.py -q`  Expected: FAIL (`EdgeKind.GAP`, `side_landing` and the rest missing; M has one platform).

- [ ] **Step 3: Implement**

- `params.py`: `side_teleport_land_dy_px: int = 4  # QB282 (2026-11-13): a sideways teleport carries onto a platform this close in height`.
- `terrain._walk_links`: after `best` is chosen for a side, emit nothing for that side when `bool(a.is_right_platform_edge if side > 0 else a.is_left_platform_edge)` or `bool(b.is_left_platform_edge if side > 0 else b.is_right_platform_edge)` (b = `nodes[best[1]]`). Choose the nearest as today first, then drop: never link past the nearest to a farther node. Docstring R1 says so.
- `route_graph.build`: a PathGraph edge with `dx >= abs(dy)` whose ends are on different terrain platforms (`terrain.platform_index`) gets `EdgeKind.GAP` instead of `WALK`.
- `side_landing(platform, px, sign)`: let `nodes = self.terrain.platforms[platform]`, `end = nodes[-1] if sign > 0 else nodes[0]`, `full = px + sign * self.reach_h`. If `end` is not `is_marked_end(end, sign)` or `full` does not pass `end.x` -> `(full, platform)`. Else look at every other platform's near end `n` (`first` for `sign > 0`, `last` otherwise) with `sign * (n.x - end.x) > 0`, `0 < sign * (n.x - px) <= self.reach_h` and `abs(n.y - end.y) <= params.side_teleport_land_dy_px`; the nearest to `px` wins (ties: lower platform index) and the result is `(full clamped into that platform's [first.x, last.x], its index)`; with none, `(end.x, platform)`.
- `gap_launch(u, v)`: `sign` from `v.x - u.x`; `target = v.x - sign * self.reach_h` clamped into `u`'s platform's `[first.x, last.x]`; return it if `side_landing(platform(u), target, sign)[1] == platform(v)`, else None.
- `_edge_cost` for `GAP`: `launch = self.gap_launch(u, v)`; None -> no edge; else `abs(u.x - launch) / v + teleport_s + abs(landing_x - v.x) / v`, landing from `side_landing(platform(u), launch, sign)`.
- `_edge_cost` for `WALK`: `horizontal_cost_s(dx, reach_h, p, to_end=is_marked_end(v, sign))`.
- `horizontal_cost_s(..., to_end=False)`: at the top of each pass of Task 1's loop (made `while d > 0`, breaking when neither rule applies), when `to_end` and `d <= reach_h`, add one `t`, set `d = 0` and stop; otherwise Task 1's rule. Docstring: a teleport stops on a marked end; whether it would carry onto the next platform from that spot is not priced (the planner's move choice in Task 3 is exact; the graph cost stays a heuristic, as it always was across a walk run).

- [ ] **Step 4: Run the tests**

Run: `.venv/Scripts/python.exe -m pytest tests/route -q`  Expected: the new tests pass; nothing else moves (no test map or fixture has marks). If a fixture test moves, a PathGraph edge crossed terrain platforms on an unmarked map: stop and report it (Review Focus 1).

- [ ] **Step 5: Check and commit**

`HELIX_NO_ELEVATE=1 .venv/Scripts/python.exe tools/check.py` -> green.
```bash
git add src/route/params.py src/route/terrain.py src/route/route_graph.py tests/route/test_platform_ends.py
git commit -m "feat(route): split platforms at the map's platform-end marks; a sideways teleport over a gap is a GAP edge (WALKFIX Task 2, QB282 A)"
```
Done: tests pass, the default sim runs give Task 1's figures unchanged, `tools/check.py` green.

---

## Task 3: Item 4b, the planner plans sideways teleports by Hong's landing rule (subtle: yes; size: L)

Frozen-file opening (Hong, QB282): "Build it: the walker reads the platform-end marks ..., stops planning walks across gaps, and plans sideways teleports by this rule. Where a teleport would stop exactly on a node at a platform's end, teleport rather than walk, even under the 17 px limit."

**Files:**
- Modify: `src/route/planner.py` (`_run` :93-99, `_starts` :70-81, P4 :240-252, P5 :254-264)
- Modify: `src/route/route_preview.py` (`_plan` :134 onward, the same statements; `_run` call sites)
- Test: `tests/route/test_platform_ends.py` (add), re-pin `tests/route/test_route_preview.py` `_BRANCH_LINES` (and add the new GAP branch line to it), `tests/route/test_planner.py` call sites of `_run` if any

**Interfaces:**
- Consumes: Task 2's `RouteGraph.side_landing`, `RouteGraph.gap_launch`, `EdgeKind.GAP`, `is_marked_end`, `horizontal_cost_s(..., to_end=)`; Task 1's `walk_under_px`.
- Produces: `_run(rg: RouteGraph, px: int, goal_x: int, platform: int | None) -> Move` (route_preview imports it with this signature).

Launch point across a gap (provisional, WALKFIX169-D1 option A, undo = change `RouteGraph.gap_launch` alone): the planner lines up on the x from which a full-reach teleport lands on the GAP edge's far node (`v.x - sign * reach`, clamped to the own platform), so it never teleports past that node; on FF2 no rotation leg crosses the gap, so this acts on detours only.

- [ ] **Step 1: Write the failing tests** (in `test_platform_ends.py`, map M of Task 2, nodes `n24`, `n40`, `n50`, `n66`; `rng = np.random.default_rng(0)`). Hand-checked: from (25, 50) toward `n66` the cheapest start is `n40` (approach 0.55 by the end-node teleport, then GAP 1.27, then 16 px walked 1.28) and the GAP edge's launch x is 31:

```python
def test_teleport_stops_on_the_marked_end_even_under_17():   # d = 12, B is 22 px away
    assert next_move(rg, (28, 50), n40, rng).unwrap().move == Move(MoveKind.TELEPORT, Key.RIGHT, None)

def test_without_the_mark_the_same_move_walks():
    assert next_move(rg_M0, (28, 50), n40_of_M0, rng).unwrap().move == Move(MoveKind.WALK, Key.RIGHT, 960.0)

def test_carry_over_is_not_a_stop_on_the_end():               # from 33 the teleport carries onto B
    assert next_move(rg, (33, 50), n40, rng).unwrap().move == Move(MoveKind.WALK, Key.RIGHT, 560.0)

def test_lower_platform_does_not_count():                     # B at y 56: from 33 the teleport stops at 40
    assert next_move(rg_b56, (33, 50), n40_b56, rng).unwrap().move.kind == MoveKind.TELEPORT

def test_crossing_the_gap_lines_up_then_teleports():
    d1 = next_move(rg, (25, 50), n66, rng).unwrap()
    assert d1.move == Move(MoveKind.WALK, Key.RIGHT, 480.0) and d1.toward is n50   # to the launch x 31
    d2 = next_move(rg, (31, 50), n66, rng).unwrap()
    assert d2.move == Move(MoveKind.TELEPORT, Key.RIGHT, None) and d2.toward is n50

def test_no_walk_crosses_the_gap():                           # every x on A toward every B node
    for x in range(24, 41):
        for target in (n50, n66):
            move = next_move(rg, (x, 50), target, rng).unwrap().move
            assert not (move.kind == MoveKind.WALK and move.direction == Key.RIGHT
                        and x + 12.5 * move.walk_ms / 1000 > 40 + 2)

def test_read_two_px_past_a_marked_end_plans():               # Review Focus 5
    assert is_successful(next_move(rg, (42, 50), n24, rng))
```
and `test_preview_matches_next_move_on_marked_map`: for every x on A and both targets, `route_preview` `_plan(...)`'s first option equals `next_move(...)`'s move (the pattern of `tests/route/test_route_preview.py`'s S3 check; reuse its helper).

- [ ] **Step 2: Run to see them fail** — `.venv/Scripts/python.exe -m pytest tests/route/test_platform_ends.py -q` -> FAIL (d = 12 walks; the gap is walked or unplanned).

- [ ] **Step 3: Implement**

- `_run(rg, px, goal_x, platform)`: `d = abs(goal_x - px)`, `sign`; if `platform is None` the landing is `px + sign * rg.reach_h` on no platform, else `land_x, land_p = rg.side_landing(platform, px, sign)`. Order: (1) `land_p == platform and abs(land_x - goal_x) <= arrive_px` -> TELEPORT (the end-node rule; with full reach this is also every d in [17, 19]); (2) `d < walk_under_px` -> WALK; (3) `land_p == platform` and `teleport_s + abs(land_x - goal_x) / v < d / v` -> TELEPORT; (4) WALK. Docstring cites QB266 and QB282.
- Every `_run` call passes `anchor.platform` (P4 launch :248, P4 goal :251, P5 WALK :261).
- `_starts`: the approach to an end uses `horizontal_cost_s(d, rg.reach_h, rg.params, to_end=is_marked_end(end, sign))`, sign from `end.x - pos.x`.
- P4: the line-up applies to a GAP edge as to UP/DOWN: `if _edge_kind(rg, g, h) in (UP, DOWN): launch = _launch_x(rg, g, h)`; `elif ... == GAP: launch = rg.gap_launch(g, h)` (never None: `_edge_cost` added no GAP edge without a launch); the rest of P4 is unchanged.
- P5: `if kind == EdgeKind.GAP: return Success(Decision(Move(MoveKind.TELEPORT, _direction(px, h.minimap_position[0]), None), total, h))`.
- `route_preview._plan`: the same statements in the same order; a GAP hop's aim is `_Edge(g, h)` (check how `src/route_view` draws an `_Edge` whose two nodes are level; if it would draw something new for the user, stop and raise it instead of changing the viewer).
- Re-pin `_BRANCH_LINES` to the new line numbers and add the P5 GAP line to it with a hand-built-table row that reaches it.

- [ ] **Step 4: Run** `.venv/Scripts/python.exe -m pytest tests/route -q` -> pass (the slow `test_preview_sim.py` included: `-m slow` once).

- [ ] **Step 5: Sim runs, check, commit**

The three default sim runs must still be 6/6 (no marks on the fixture: figures equal Task 1's unless `_starts` moved them; record either way). `tools/check.py` green.
```bash
git add src/route/planner.py src/route/route_preview.py tests/route
git commit -m "feat(route): plan sideways teleports by the platform-end landing rule; teleport onto an end node; cross gaps by teleport only (WALKFIX Task 3, QB282 A)"
```
Done: tests pass, preview checks green, sim runs 6/6, `tools/check.py` green.

---

## Task 4: Item 4c, the simulator lands sideways teleports by the same rule (subtle: yes; size: M)

Hong (QB282): "The simulator gets the same landing rule." `sim/world.py` is not on the frozen list.

**Files:**
- Modify: `src/route/sim/world.py` (`SimParams` :143-155, `teleport` sideways branch :336-340, one new method)
- Test (new): `tests/route/test_sim_side_teleport.py`
- Re-pin: `tests/route/test_sim_real_ground.py:65-66`, `tests/route/test_sim_world.py` / `test_sim_real_world.py` / `test_sim_rope.py` / `test_sim_run.py` cases whose pinned values move

**Interfaces:**
- Produces: `SimParams.side_teleport_land_dy_px: float = 4.0  # copy of MovementParams.side_teleport_land_dy_px`; `SimWorld._side_target(self, x: float, y: float, sign: int, length: float) -> tuple[float, float] | None`.

- [ ] **Step 1: Write the failing tests** with a synthetic `SimTerrain` (pattern: `tests/route/test_sim_real_world.py`'s `_world`), `NoiseConfig.off()`, `SimParams()`, reach 19: A [(0, 50), (40, 50)], B [(48, 52), (90, 52)], C [(45, 70), (90, 70)].

```python
def test_teleport_within_the_platform_goes_full_reach():  # from (10, 50) right -> (29, 50)
def test_teleport_carries_onto_a_same_height_platform():  # from (30, 50): B starts 18 px away -> (49, 52)
def test_teleport_stops_at_the_end_when_the_next_platform_is_far():  # from (25, 50): B 23 px away -> (40, 50)
def test_teleport_stops_at_the_end_above_a_lower_platform():  # B removed, C only, from (35, 50) -> (40, 50), not y 70
def test_platform_6_px_higher_does_not_count():  # B at y 44, from (30, 50) -> (40, 50)
def test_off_platform_teleport_keeps_the_old_rule():  # character on a rope / in the air: lands as before this task
```
each asserting `world.teleport(Key.RIGHT, 0).pos == expected` (or the effect's position field `world.py` uses).

- [ ] **Step 2: Run to see them fail** — `.venv/Scripts/python.exe -m pytest tests/route/test_sim_side_teleport.py -q` -> FAIL.

- [ ] **Step 3: Implement** `_side_target`: `current = self._platform_at((x, y))`; if None (air or rope) return the old `surface_below(x + sign * length, y - _SIDE_TELEPORT_RISE_PX)` landing. Else with `P` and its end in `sign`'s direction (`points[-1]` / `points[0]`): `x_new = x + sign * length`; inside `P`'s span -> `(x_new, P.surface_y(x_new))`; else the nearest other platform whose near end `q` has `sign * (q.x - end.x) > 0`, `sign * (q.x - x) <= length` and `abs(q.y - end.y) <= self.params.side_teleport_land_dy_px` -> `x_new` clamped into its span, at its surface; else `(end.x, end.y)`. In `teleport`, the sideways branch uses `length = self.reach_h + side_teleport_extra_px + draw.overshoot_px`, then adds `draw.landing_px` and clamps the result into the landing platform's span (noise never puts it in the air). `fall` is computed as today from the landing y.

- [ ] **Step 4: Run** `.venv/Scripts/python.exe -m pytest tests/route -q`; re-pin moved sim figures (the default worlds' platform ends now stop teleports that used to go nowhere or fall). Every run must stay 6/6 with 0 off map; if not, stop and report.

- [ ] **Step 5: Sim runs, check, commit** — three runs 6/6, figures in the build notes, `tools/check.py` green.
```bash
git add src/route/sim/world.py tests/route
git commit -m "feat(sim): sideways teleports stop at the platform end or carry onto a platform within 4 px of height (WALKFIX Task 4, QB282 A)"
```

---

## Task 5: Item 4d, the marked-map fixture and the real-ground teleport length (subtle: no; size: M)

**Files:**
- Create: `tests/fixtures/ff2_marked.csv` (byte copy of Hong's 12 November walk map, `C:/Users/Qing_programming/Desktop/helix-claude-clone/data/route/2026-11-12T152109Z/map.csv`: it has the six edge marks and the 10 px gap between (78, 45) and (88, 43); read it, never edit the source), `tests/fixtures/ff2_marked.ropes.json` (byte copy of `tests/fixtures/fossil_foundry_2.ropes.json`: the same two ropes), `tests/fixtures/ff2_marked_terrain.json`
- Modify: `src/route/sim/run.py:109` (`REAL_GROUND_SIM`)
- Test (new): `tests/route/test_sim_marked.py`; modify `tests/route/test_sim_real_ground.py:30-37` (the preset test)

**Interfaces:**
- Consumes: Tasks 2-4.
- Produces: the fixture names above; `REAL_GROUND_SIM = SimParams(dot_above_foot_px=3, side_teleport_extra_px=0.0)`.

- [ ] **Step 1: Build `ff2_marked_terrain.json`** from `tests/fixtures/ff2_real_ground.json` (same keys, `_source` saying how it was made): the real-ground polylines, each extended at an end to the x of the marked map's end node there, at the polyline's end y, where that node lies beyond it: mid-left from x 32 (its left mark (32, 46)) to the recorded 80; mid-right from 88 (left mark (88, 43)) to 129 (right mark (129, 43)); top to 124 (right mark (124, 23), y 23); floor unchanged (30-135). So the world's gap is 80 -> 88 at heights 46 and 43 (3 px apart, within 4).

- [ ] **Step 2: Write the failing tests** in `test_sim_marked.py`:

```python
def test_marked_map_run_passes():   # run_sim(ff2_marked.csv, ff2_marked_terrain.json, 20, 2, noise=REAL_GROUND_NOISE,
                                    #         sim_params=REAL_GROUND_SIM): 6/6 checks, off_map == 0; then pin
                                    # (moves, no_ops, drops, teleport_downs, round(sim_seconds, 4)) to what it prints
def test_marked_map_middle_platforms_are_two():  # route graph of ff2_marked.csv: (78,45) and (88,43) on different
                                                 # platforms, both directions GAP edges
def test_marked_map_crosses_the_gap_by_teleport():  # a known case from (59, 46) to (100, 42) (run_sim's known-case
                                                    # argument): reached; in its steps no planned WALK starts left of
                                                    # x 80 and ends right of 88, or the reverse
```

- [ ] **Step 3: Check the real-ground teleport length** (26c9 finding): `REAL_GROUND_SIM.side_teleport_extra_px` is 1.0 because `test_preset_teleport_matches_the_recordings` takes the median of `ff2_live_reads.json`'s `side_teleports` pairs (20) minus 19; those pairs are the walker's planned-to-planned reads, which the picture delay inflates. The recorded frames show 64 flat sideways jumps of 18-19 px, median 19.0 (workspace `notes/q-20261113-153031-26c9-qa-answer.md`, Q1). Set the extra to 0.0; rewrite the preset test to assert 0.0 with that source in a comment. Its second assertion (`>= 1`, the slope-freeze guard of the off-graph fix's Task 3): keep that guard as its own scenario if no test already holds it with an explicit extra (`tests/route/test_sim_real_world.py:83` builds a world with `side_teleport_extra_px=2`; read it; if it is that scenario, drop the assertion, else add a scenario test with `SimParams(side_teleport_extra_px=1.0)` that shows the freeze path still being exercised).

- [ ] **Step 4: Run, re-pin, sim runs** — `.venv/Scripts/python.exe -m pytest tests/route -q`; re-pin `test_real_ground_run_passes` (`test_sim_real_ground.py:65-66`) to the new figures; real-ground and marked-map runs 6/6, 0 off map; record all figures in the build notes. If the marked-map run fails a check, stop and report (it is the first run of the walker on Hong's current map in the simulator).

- [ ] **Step 5: Check and commit** — `tools/check.py` green.
```bash
git add tests/fixtures/ff2_marked.csv tests/fixtures/ff2_marked.ropes.json tests/fixtures/ff2_marked_terrain.json src/route/sim/run.py tests/route
git commit -m "test(sim): Hong's marked FF2 map in the simulator; real-ground teleports are 19 px, not 20 (WALKFIX Task 5)"
```
Done: the marked-map run and the real-ground run 6/6 with 0 off map, figures recorded, `tools/check.py` green.

---

## Piece 2: item 2 (fresher pictures), docs, the full check, item 3 in outline

Spec for Tasks 6-10 (Hong, QB283, chat paste 2026-11-13, verbatim): "2. Fresher pictures. I lift the "a picture every 20 ms" rule; the capture may use more processor time. a. Wake the walker the moment a new picture arrives, instead of it looking every 0.05 s. b. Take every new screen picture Windows hands over, with no fixed pause between them. Check that this does not make the keys late: with MapleStory closed and nobody using the PC, run the key-timing check (a test window that changes every frame on the second screen, so the capture runs flat out, with the keys timed as in the long input test). If the keys get later, bring back the smallest pause that fixes it and tell me the number. Ask me before this check, so I can confirm MapleStory is closed. c. Re-find the minimap every so many seconds (about every 22 s, as now) instead of every 1000 pictures. d. Stamp every picture with the moment Windows handed it over: the pictures the walker reads and the ones saved with each walk. None of this changes how the character moves."

Contract with piece 1: Tasks 6-13 change nothing Tasks 1-5 produce (`walk_under_px`, `side_teleport_land_dy_px`, `side_landing`, `gap_launch`, `EdgeKind.GAP`, `is_marked_end`, `SimParams.side_teleport_land_dy_px`, the `ff2_marked*` fixtures). No piece-2 task touches the planner, the route graph, the terrain or the simulator's world, so every pinned sim figure stays exactly what Task 5 left (the sim has no picture delay and no capture: `SimPositionSource.read`, `sim/link.py:146-150`, reads the world at the clock's time). Each of Tasks 6-9 runs the four sim runs of the build notes and records "unchanged" or stops and reports.

WALKFIX169-D1 (where to teleport from across a gap; question bank QB284, provisionally A, open with Hong): nothing in Tasks 6-10, 12 or 13 depends on it. If Hong picks B or C, the change is `RouteGraph.gap_launch` and the Task 3 tests (piece 1), plus the wording of one decisions entry in Task 11 (marked there).

### Review Focus (piece 2)

6. "None of this changes how the character moves" (Hong): the settle's stillness test must not become "two pictures a few milliseconds apart". With pictures every 4-22 ms and a walk moving 1 px per 80 ms (12.5 px/s), two consecutive pictures are nearly always equal while the character still moves. Task 7 keeps the two reads the settle compares at least `poll_s` apart (WALKFIX170-D1, provisional A) and pins it with `test_settle_keeps_its_reads_poll_s_apart`.
7. The frame stamp must belong to the picture it is stored with: frame and stamp are written together under `minimap_frame_lock` and read together under it (Task 6 pins a publisher thread that changes both on every publish against a reader that checks they match).
8. A walker with no picture waiter (the simulator, `tools/route_tab_sim.py`, every existing test fake) must behave exactly as today: `wait_frame=None` falls back to `clock.sleep(poll_s)` (Task 7).
9. The key-timing check asks "Is MapleStory closed? y/n" exactly once per run, before the listener, the link or any key, through `soak.maple_check` (Task 10A pins it with a counter on `soak._read_answer`); no agent answers it (H66, this plan's Global Constraints).

---

## Task 6: Item 2d, stamp every picture with the moment Windows handed it over (subtle: yes; size: L)

Hong (QB283): "d. Stamp every picture with the moment Windows handed it over: the pictures the walker reads and the ones saved with each walk." No frozen file is opened.

The stamp is `time.perf_counter_ns()` read right after `AcquireNextFrame` returns a desktop image (frame_wait.py:77). That is the clock of `RealClock.now_ns` (walker.py:139-140), of `InputLayer`'s stimulus and of the recorder's `t_ns` (record_minimap.py docstring), so a stamp compares directly with every key time in `steps.jsonl`. Python docs (https://docs.python.org/3/library/time.html, `perf_counter`): "The clock is the same for all processes" (3.10+, Windows). Not used: `DXGI_OUTDUPL_FRAME_INFO.LastPresentTime` (Microsoft: "The time stamp of the last update of the desktop image. The operating system calls the QueryPerformanceCounter function to obtain the value", https://learn.microsoft.com/en-us/windows/win32/api/dxgi1_2/ns-dxgi1_2-dxgi_outdupl_frame_info), which is when the image was made, not when Windows handed it over; once the capture has no pause (Task 9) the two differ by the wake-up only.

**Files:**
- Modify: `src/vision/frame_wait.py` (`grab_new_frame` :62-98 split into a stamped body and a wrapper; a `_clock` seam), `src/vision/vision.py` (one helper; the minimap loop's grab :75 and publish :87), `src/vision/minimap_follow.py` (`LostTracker.publish` :92-100)
- Modify: `src/main.py` (`SharedInfo` :9-13), `src/route/runtime.py` (`SharedFrame` :368-373), `src/route/harness/record_minimap.py` (`record` :62-112, `shared_frames` :116-125, its holder :141-142, the docstring), `src/route/session.py` (`FrameHolder` :110-117, `_record_guarded` :276-300 docstring, the `StepLog` built at :496), `src/route/position.py` (`MinimapPositionSource` :34-51), `src/route/step_log.py` (the format: `FIELDS` :62-73 stay; `event_line` :168-188, `parse_line`, `StepLog.planned` :546-550 and `.settled` :558-561, the module docstring)
- Test (new): `tests/vision/test_frame_stamps.py`, `tests/route/test_frame_stamps.py`
- Update (not frozen): `tests/vision/test_minimap_follow.py` (the `publish(...)` calls at :153-187 pass a stamp; `run_loop` :225-232 also patches `vision._grab_stamped`), `tests/route/test_record_minimap.py` and the recorder tests in `tests/route/test_session.py` (their `frames` fakes return `(frame, stamp)` pairs), any holder fake that a type check or test now finds short of `minimap_frame_ns`

**Interfaces (Tasks 7, 10 and 13 rely on these names):**
- `frame_wait.grab_new_frame_stamped(camera, region=None) -> tuple[np.ndarray, int] | None`; `grab_new_frame` keeps its signature and returns `got[0]` or None. `frame_wait._clock = time.perf_counter_ns` (a module seam, as `input_layer._clock`).
- `vision._grab_stamped(camera, region) -> tuple[np.ndarray, int]`: `cameraGrab`'s retry loop over `grab_new_frame_stamped`.
- Every frame holder (`SharedInfo`, `runtime.SharedFrame`, `record_minimap`'s holder) has `minimap_frame_ns: int | None = None`, written with `minimap_frame` under `minimap_frame_lock`, by `LostTracker.publish(frame_rgb, frame_ns: int)` only.
- `MinimapPositionSource.frame_ns: int | None`: the stamp of the picture of its last *successful* read (set after the locator succeeds, from the stamp taken under the lock with the frame).
- `record(frames, ...)`: `frames()` returns `tuple[np.ndarray, int | None] | None`; each `index.jsonl` line gains `"frame_ns"` (int, or null when the holder had no stamp) after `"t_ns"`. `shared_frames(shared)` returns that pair, read under the lock.
- Step log: `EXTRA_FIELDS: dict[str, tuple[str, ...]] = {"planned": ("frame_ns",), "settled": ("frame_ns",)}`; `StepLog(..., frame_ns: Callable[[], int | None] | None = None)`. With a `frame_ns` source, every `planned` and `settled` line carries `"frame_ns"` (null on a `settled` whose `pos` is null); without one the lines are byte for byte today's. `parse_line` puts `frame_ns` into `data` only when the line has it; a `frame_ns` that is neither an int nor null makes the line bad. `VERSION` stays 1 (the key is optional, `parse_line` already ignores keys it does not know).

- [ ] **Step 1: Write the failing tests.**

`tests/vision/test_frame_stamps.py` (Windows; reuse `test_frame_wait.py`'s fake camera and its `_query_texture` patch):
```python
def test_stamped_grab_returns_the_time_after_acquire(monkeypatch):   # _clock patched to a counter: 1 before, 2 after
    frame, t_ns = frame_wait.grab_new_frame_stamped(fake_camera_with_one_image())
    assert t_ns == clock_value_right_after_acquire
def test_pointer_only_update_has_no_stamp():                          # LastPresentTime 0 -> None, as grab_new_frame
def test_plain_grab_is_unchanged():                                   # grab_new_frame returns the same array as before
def test_publish_writes_frame_and_stamp_together():                   # LostTracker.publish(f, 123): holder has both
def test_capture_loop_publishes_the_grab_stamp(monkeypatch):          # run_loop with _grab_stamped stamps 1, 2, 3...:
                                                                      # the published (frame, frame_ns) pairs carry them
```
`tests/route/test_frame_stamps.py`:
```python
def test_position_source_keeps_the_stamp_of_its_last_good_read():   # holder frame A stamp 10 -> read ok, frame_ns 10;
                                                                    # frame B stamp 20, locator fails -> frame_ns still 10
def test_frame_and_stamp_never_mix():                               # a thread publishes (frame_k, k) 10_000 times;
                                                                    # shared_frames() pairs always match (frame[0,0,0] == k % 256)
def test_index_lines_carry_frame_ns(tmp_path):                      # record() over pairs: every line has "frame_ns",
                                                                    # equal to the pair's stamp; null for a None stamp
def test_step_log_writes_frame_ns_when_given_a_source(tmp_path):    # StepLog(frame_ns=lambda: 77): planned/settled lines
                                                                    # have "frame_ns": 77; settled with pos None -> null
def test_step_log_without_a_source_is_unchanged(tmp_path):          # lines equal today's encoding exactly
def test_parse_line_reads_frame_ns_only_when_present():             # old line: "frame_ns" not in data; new line: 77;
                                                                    # "frame_ns": "x" -> Failure
def test_session_wires_the_stamp_source():                          # RunSession with make_positions returning a
                                                                    # MinimapPositionSource over runtime.SharedFrame: the
                                                                    # StepLog's frame_ns source reads positions.frame_ns
```

- [ ] **Step 2: Run them to see them fail** — `.venv/Scripts/python.exe -m pytest tests/vision/test_frame_stamps.py tests/route/test_frame_stamps.py -q` -> FAIL (names missing).

- [ ] **Step 3: Implement.**
- `frame_wait.py`: move the body of `grab_new_frame` into `grab_new_frame_stamped`; read `t_ns = _clock()` on the line after `AcquireNextFrame` (inside the `try`, after the call returned without a COM error) and return `(frame, t_ns)`. `grab_new_frame` = `got = grab_new_frame_stamped(camera, region); return None if got is None else got[0]`. Docstrings say which is which.
- `vision.py`: `import src.vision.frame_wait as frame_wait` at the top (vision.py already loads bettercam at module scope, vision.py:1; the in-function import in `cameraGrab` exists for `detection_check`, which imports `vision_helper_lib`, not `vision`). Add `_grab_stamped` (a `while True` loop returning the first non-None `grab_new_frame_stamped(camera, region)`). In the minimap loop: `box_frame, frame_ns = _grab_stamped(camera, lock.box)` (:75) and `tracker.publish(cv2.cvtColor(...), frame_ns)` (:87). The whole-screen search keeps `vision.cameraGrab` (:45): it publishes nothing. Change nothing else in the file (src/vision/CLAUDE.md, Traps, first bullet: Feature C ports from it).
- `minimap_follow.LostTracker.publish(frame_rgb, frame_ns)`: set `minimap_frame_ns = frame_ns` in the same `with` block as `minimap_frame`.
- Holders: `self.minimap_frame_ns = None` with a one-line comment (stamp of `minimap_frame`, `time.perf_counter_ns()` when Windows handed the picture over, QB283 2d) in `SharedInfo`, `runtime.SharedFrame` and `record_minimap`'s holder. `session.FrameHolder` gets the read-only property `minimap_frame_ns -> int | None`.
- `position.py`: in `read`, take `stamp = self._shared.minimap_frame_ns` under the lock with the frame; after `locate` succeeds set `self.frame_ns = stamp`. Docstring: what `frame_ns` is and that it is read on the walker's own thread (the step log reads it there, below), so it needs no lock.
- `record_minimap.py`: `record`'s loop unpacks `frames()`'s pair (None stays "no frame"), keeps the `is` test on the array and writes `"frame_ns"`; `shared_frames` returns `(shared.minimap_frame, shared.minimap_frame_ns)` under the lock, or None when the frame is None. Update the module docstring's index line format and the `t_ns` bullet (t_ns is the poll's time, frame_ns the picture's).
- `session.py`: `_record_guarded` is unchanged in code (its `frames()` passes the pair through); fix its docstring's "`record` is not edited". Build the step log as `StepLog(log=self._error_line, frame_ns=(lambda: positions.frame_ns) if isinstance(positions, MinimapPositionSource) else None)` (`src.route.position` is already below `session` in import-linter contract f).
- `step_log.py`: `EXTRA_FIELDS`; `event_line` accepts `len(FIELDS[ev])` values or, for an event in `EXTRA_FIELDS`, that many plus its extras (checked int-or-None); `parse_line` copies an extra key into `data` when present and checks its shape; `StepLog.planned`/`settled` append `self._frame_ns()` when the source is set (`settled`: None when `pos` is None). Add a "Where the plan is silent" bullet to the docstring.

- [ ] **Step 4: Run** `.venv/Scripts/python.exe -m pytest tests/vision tests/route -q` -> the new tests pass; update the non-frozen tests listed under Files where they fail only because of the new argument or pair (change nothing they assert). The route view's readers (`src/route_view/model.py:249-266` reads `t_ns` and `file` of index lines; `parse_line` ignores unknown keys) need no change: `tests/route_view` must pass untouched.

- [ ] **Step 5: Sim runs, check, commit.** The four build-notes sim runs: figures identical to Task 5's (record "unchanged"). `HELIX_NO_ELEVATE=1 .venv/Scripts/python.exe tools/check.py` -> green.
```bash
git add src/vision src/main.py src/route/runtime.py src/route/harness/record_minimap.py src/route/session.py src/route/position.py src/route/step_log.py tests/vision tests/route
git commit -m "feat(vision): stamp every picture with the time Windows handed it over; the walker's reads and the recorder save the stamp (WALKFIX Task 6, QB283 2d)"
```
Done: tests pass, sim figures unchanged, `tools/check.py` green.

---

## Task 7: Item 2a, wake the walker on a new picture (subtle: yes; size: M)

Frozen-file opening (Hong, QB283): "a. Wake the walker the moment a new picture arrives, instead of it looking every 0.05 s." Opens `walker.py` only (and its docstring fix, SWEEP165-D3 ruling). `params.py` is not changed: `poll_s` stays the longest wait, so stop checks and timeouts keep today's pace.

Where it acts: the walker sleeps `poll_s` at six places (walker.py:234 after a failed or STALE read, :254 placed but no path, :259 rope hold, :280 standing hold, :291 no plan after attaching, :466 in `_settle`). The biggest waste is the first read after every settle: it sees the frame the settle's last read already copied, so today it is STALE and the walker sleeps 50 ms before planning (:226-235). Each of these waits now ends at the next picture (at most `poll_s`). The still and rope timers count clock time (`_Stillness.see(pos, read_ns)`), not reads, so reading more often changes neither `standing_s` nor `rope_still_s`.

Provisional (WALKFIX170-D1, option A; undo: one line in `_settle`): inside `_settle`, after a *successful* read, the walker still sleeps `poll_s` before the next read, so the two reads it compares stay at least 50 ms apart, as today; after a failed or STALE read it waits for the next picture. Reason: Hong's "None of this changes how the character moves"; two pictures a few ms apart are equal while a walk is still moving (Review Focus 6). Item 3 (Task 13) replaces the settle after his walk.

**Files:**
- Modify: `src/route/walker.py` (`RouteWalker.__init__` :159-189, the six waits above, `_settle` :444-466, the module docstring :9-46 including the fix at :37-38)
- Modify: `src/vision/minimap_follow.py` (`publish` notifies), the three holders (a condition), `src/route/position.py` (`wait_new`), `src/route/session.py` (pass the waiter)
- Test (new): `tests/route/test_walker_wake.py`; add to `tests/route/test_frame_stamps.py` or a new `tests/route/test_frame_wait_walker.py` the real-thread tests below
- Frozen walker tests (`test_walker_leg.py`, `test_walker_run.py`) are not touched: their fakes pass no waiter.

**Interfaces:**
- Every holder: `minimap_frame_new = threading.Condition(self.minimap_frame_lock)` (the condition shares the lock, so every existing `with minimap_frame_lock:` reader is unchanged). `LostTracker.publish` calls `self._shared.minimap_frame_new.notify_all()` inside its `with` block. Verified (Python 3, run 2026-11-13): `notify_all()` inside `with lock:` on a `Condition(lock)` works; outside it raises `RuntimeError: cannot notify on un-acquired lock` (docs: https://docs.python.org/3/library/threading.html#condition-objects).
- `MinimapPositionSource.wait_new(self, timeout_s: float) -> None`: `with cond: cond.wait_for(lambda: self._shared.minimap_frame is not self._last_frame, timeout_s)`; returns at once when a newer frame is already there.
- `walker.py`: `class FrameWaiter(Protocol): def __call__(self, timeout_s: float) -> object: ...` and `RouteWalker(..., *, observer=None, log=_print_error, ropes=RopeZones.EMPTY, wait_frame: FrameWaiter | None = None)`; a private `_wait(self, seconds)` = `self._wait_frame(seconds)` if set, else `self._clock.sleep(seconds)`.
- `RunSession` passes `wait_frame=positions.wait_new if isinstance(positions, MinimapPositionSource) else None`. `sim/run.py` and `tools/route_tab_sim.py` pass nothing.

- [ ] **Step 1: Write the failing tests** in `tests/route/test_walker_wake.py` with the walker test pattern (`tests/route/test_walker_standing.py`'s fake clock, layer and sink) and a `_Pictures` fake: picture times every 4 ms on the fake clock, each showing a scripted position; `read()` returns `Failure(STALE)` unless a picture newer than the last one read exists at `clock.now_ns()`; `wait(timeout_s)` advances the fake clock to the next picture time or by `timeout_s`, whichever is first.
```python
def test_first_read_after_a_settle_waits_for_the_next_picture():
    # the planned event after a settled event is at most 4 ms after it, not 50 ms
def test_settle_keeps_its_reads_poll_s_apart():                 # Review Focus 6, WALKFIX170-D1 A
    # pictures every 4 ms, the position changes every 80 ms: the two equal reads that end a settle are >= poll_s apart,
    # and the settle does not end while the scripted position is still changing
def test_without_a_waiter_the_walker_sleeps_poll_s():          # Review Focus 8: the sleep log equals today's
def test_still_timer_counts_time_not_reads():                   # standing_s 0.5 s still holds with reads every 4 ms
```
Real-thread tests (no walker):
```python
def test_wait_new_returns_when_a_picture_is_published():        # publisher thread publishes after 30 ms; wait_new(1.0)
                                                                # returns in < 0.5 s and the next read is not STALE
def test_wait_new_times_out_without_a_picture():                # returns after about timeout_s (0.05), read is STALE
def test_wait_new_returns_at_once_when_a_newer_frame_is_there():
def test_session_passes_the_picture_wait():                     # RunSession with a MinimapPositionSource: the walker's
                                                                # wait_frame is positions.wait_new; with a SimPositionSource it is None
```

- [ ] **Step 2: Run to see them fail** — `.venv/Scripts/python.exe -m pytest tests/route/test_walker_wake.py -q` -> FAIL.

- [ ] **Step 3: Implement** the interfaces above. In `walk_leg` replace `clock.sleep(params.poll_s)` by `self._wait(params.poll_s)` at :234, :254, :259, :280 and :291. `_settle`:
```python
        while clock.now_ns() - start_ns < timeout_ns:
            if self._stop.is_set():
                return last, True
            read = self._positions.read()
            if is_successful(read):
                current = read.unwrap()
                if current == last:
                    return current, False
                last = current
                clock.sleep(params.poll_s)  # WALKFIX170-D1 A: the two reads compared stay poll_s apart (item 3 replaces this)
            else:
                self._wait(params.poll_s)   # no new picture yet, or a miss: wake on the next picture (QB283 2a)
        return last, False
```
Docstrings: the `_settle` docstring and a new "Where the plan is silent" bullet (the waits end at the next picture when a waiter is given; the settle's spacing; no waiter = today). The SWEEP165-D3 fix (workspace `notes/SWEEP165.md`, M4): replace walker.py:37-38's "With the order used up the leg ends `OFF_MAP` with one line." by "With the order used up, a read the map places goes on with the planner's move (the order starts again); a read the map cannot place ends the leg `OFF_MAP` with one line." (the code at walker.py:266-273 already does this).

- [ ] **Step 4: Run** `.venv/Scripts/python.exe -m pytest tests/route tests/vision -q` -> pass; the frozen walker tests pass untouched (if one fails, the fallback is not today's behaviour: fix the code, not the test).

- [ ] **Step 5: Sim runs, check, commit.** Sim figures identical to Task 5's (the sim passes no waiter). `tools/check.py` green.
```bash
git add src/route/walker.py src/route/position.py src/route/session.py src/route/runtime.py src/route/harness/record_minimap.py src/vision/minimap_follow.py src/main.py tests/route tests/vision
git commit -m "feat(route): the walker wakes on each new picture instead of polling every 50 ms; the settle keeps its 50 ms spacing (WALKFIX Task 7, QB283 2a)"
```
Done: tests pass, frozen walker tests untouched and green, sim figures unchanged, `tools/check.py` green.

---

## Task 8: Item 2c, re-find the minimap by time (about every 22 s) (subtle: no; size: S)

Hong (QB283): "c. Re-find the minimap every so many seconds (about every 22 s, as now) instead of every 1000 pictures." Today: `MINIMAP_POSITION_RECALC_INTERVAL = 1000` (vision.py:10) counted in the minimap loop (vision.py:70-72, :88); at 45.7 pictures a second that is about 22 s (H80 Scope). It must land before Task 9, which would otherwise make it every 4-7 s.

**Files:**
- Modify: `src/vision/vision.py` (:10, :70-72, :88)
- Test: `tests/vision/test_minimap_follow.py` (add two loop tests; `run_loop` gains a fake monotonic clock)

**Interfaces:** `MINIMAP_POSITION_RECALC_S = 22.0` replaces `MINIMAP_POSITION_RECALC_INTERVAL` (grep the repo for the old name: CLAUDE.md files and specs are Task 11's; code and tests here). `vision._monotonic = time.monotonic` (a module seam, so the tests do not patch the global `time.monotonic`).

- [ ] **Step 1: Failing tests**
```python
def test_minimap_is_re_found_after_22_seconds(monkeypatch):    # fake _monotonic advances 1 s per grab: the loop goes
                                                               # back to the whole-screen search after 22 publishes
def test_fast_pictures_do_not_re_find_early(monkeypatch):      # clock frozen: 2000 publishes, one whole-screen search
```
- [ ] **Step 2: Run** `.venv/Scripts/python.exe -m pytest tests/vision -q` -> FAIL.
- [ ] **Step 3: Implement.** `locked_at = _monotonic()` where `minimap_frame_counter` was set (:70); the loop condition `minimap_valid and _monotonic() - locked_at < MINIMAP_POSITION_RECALC_S`; drop the counter and its increment (:88).
- [ ] **Step 4: Run** the vision tests and `tests/test_anti_cheat_names.py` -> pass.
- [ ] **Step 5: Sim runs (unchanged), check, commit.**
```bash
git add src/vision/vision.py tests/vision
git commit -m "feat(vision): re-find the minimap every 22 s instead of every 1000 pictures (WALKFIX Task 8, QB283 2c)"
```
Done: tests pass, `tools/check.py` green.

---

## Task 9: Item 2b (code), no fixed pause between pictures, behind its setting (subtle: no; size: S)

Hong (QB283): "I lift the "a picture every 20 ms" rule ... b. Take every new screen picture Windows hands over, with no fixed pause between them." This is the code only; it is final after Task 10B ("2's no-pause part is final only after the key-timing check").

**Files:**
- Modify: `src/vision/vision.py` (:11, the comment at :89)
- Modify: `tests/test_anti_cheat_names.py` (`test_capture_loop_keeps_its_pause` docstring :108-112 only)
- Test: `tests/vision/test_minimap_follow.py` (one test)

**Interfaces:** `THREAD_SLEEP_DURATION_S = 0.0` keeps its name (it is the setting; Task 10B may set it to the number it finds). The line `time.sleep(THREAD_SLEEP_DURATION_S)` stays as it is, so `test_capture_loop_keeps_its_pause` still finds it: Python docs (https://docs.python.org/3/library/time.html, `time.sleep`, "Windows implementation"): "On Windows, if seconds is zero, the thread relinquishes the remainder of its time slice to any other thread that is ready to run. If there are no other threads ready to run, the function returns immediately". So with 0.0 the loop still lets the walker and the key thread run between pictures; the grab's wait in Windows (H77, `NEW_FRAME_TIMEOUT_MS = 100`) paces it at the screen's new images.

- [ ] **Step 1: Failing test** `test_loop_pause_is_the_setting`: `vision.THREAD_SLEEP_DURATION_S == 0.0` and, in `run_loop`, every publish is followed by exactly one `sleep(0.0)` (the recorded sleeps list).
- [ ] **Step 2: Run** -> FAIL.
- [ ] **Step 3: Implement.** `THREAD_SLEEP_DURATION_S = 0.0  # pause between pictures: none (Hong QB283 2b, lifts H80's 20 ms); Task 10B's key-timing check sets the smallest pause that keeps the keys on time, if one is needed`. The comment at :89 says the same in short. The anti-cheat test's docstring: "the loop keeps its pause line (H77; its value is the setting, 0 since QB283 2b)".
- [ ] **Step 4: Run** the vision tests and `tests/test_anti_cheat_names.py` -> pass.
- [ ] **Step 5: Sim runs (unchanged), check, commit.**
```bash
git add src/vision/vision.py tests/test_anti_cheat_names.py tests/vision
git commit -m "feat(vision): no fixed pause between pictures, behind THREAD_SLEEP_DURATION_S (WALKFIX Task 9, QB283 2b, final after the key-timing check)"
```
Done: tests pass, `tools/check.py` green.

---

## Task 10: Item 2b, the key-timing check (Part A: build the tool, subtle: yes, size: L; Part B: run it, HONG-GATED, size: M)

Hong (QB283): "Check that this does not make the keys late: with MapleStory closed and nobody using the PC, run the key-timing check (a test window that changes every frame on the second screen, so the capture runs flat out, with the keys timed as in the long input test). If the keys get later, bring back the smallest pause that fixes it and tell me the number. Ask me before this check, so I can confirm MapleStory is closed."

What is checked: in a real walk the capture thread, the walker and the key timing (`InputLayer` waits the reaction time on the PC, then `BleLink` sends) share one Python process. A capture that runs flat out could delay the key thread's wake-ups. The long input test (the soak, `src/input/harness/soak.py`) measures exactly the figure that would show it: the reaction residual, "first key-down − stimulus − R, the send → key-down latency" (`realism.reaction_residual`, realism.py:224-238; V3 soak: p50 29.6 ms, p99 75.6 ms, spec `docs/superpowers/specs/2026-10-21-p1a-input-over-bluetooth-design.md:141`). A PC-side oversleep adds to it; the board and Bluetooth part is the same in every arm.

### Part A: build `tools/key_timing_check.py` and `tools/capture_load_window.py` (an agent; no key, no window opened on Hong's screen)

Where: `tools/`, because import-linter contract a forbids `src.input` from importing `src.vision`/`src.route` and `src/route/harness/` may not import `src.input.harness` (`tests/route/test_import_rules.py:242`). `tools/` is scanned by `tests/test_anti_cheat_names.py` (`scanned_files`): write none of its banned names (the tool calls soak's functions by their own names).

**Files:**
- Create: `tools/capture_load_window.py` (the test window), `tools/key_timing_check.py` (the check)
- Modify: `src/input/harness/soak.py` (`run_soak` :587 and `_check_processes` :552-556 gain one optional keyword each; nothing renamed or moved, src/input/CLAUDE.md Traps first bullet)
- Test (new): `tests/tools/test_key_timing_check.py` (or under `tests/input/` if `tests/tools/` does not exist; Windows-only, as the soak tests: soak imports winrt), `tests/input/test_soak_answered.py`

**The test window** (`capture_load_window.py`, run as its own process, like the game would be): a borderless Tk window that never takes focus (do as the route view's live window does: `src/route_view/CLAUDE.md`, its focus rules), DPI-aware before Tk starts (`ctypes.windll.shcore.SetProcessDpiAwareness(2)`, so the corners are drawn 1:1), showing a minimap the capture finds: the four real corner templates (`assets/brds/*_grayscale.png` where their masks are set) around an inside of about 200 x 140 px that changes on every redraw (a dot that moves 1 px per redraw over a fixed background; never all black, `isMinimapBlack`), redrawn with `after(1)`. Build the picture in a pure function `load_picture(t: int) -> np.ndarray` (copy how `tests/vision/test_minimap_follow.py` builds its synthetic screens from the templates). `--at X,Y` places it (default: x = the primary screen's width, y = 0, the usual place of a second screen on the right).

**The check** (`key_timing_check.py`), in this order:
1. Parse `--out data/keytiming/<name> --commands 2000 --seed 1` (defaults); refuse an `--out` outside the repo's `data/` (exit 2), as `record_minimap._resolve_out`.
2. Start the test window process; start the capture in this process with `runtime.start_capture(holder)` where `holder` is a `runtime.SharedFrame` subclass that counts the pictures published (a `minimap_frame` property whose setter counts); start a walker stand-in thread that loops `positions.wait_new(0.05); positions.read()` on `runtime.make_positions(holder)` (the walker's real per-picture load: lock, copy, template match). Wait up to 30 s for the first picture (`runtime.wait_first_frame`); none -> print "the capture did not find the test window's minimap: put the window on the second screen (python tools/capture_load_window.py --at X,Y) and start again", close the window, exit 3. No question asked yet, no listener, no link.
3. Ask the one question: `ok, text = soak.maple_check()` (through the module, so the tests' patch of `soak._read_answer` reaches it). Not ok -> print `text`, close the window, exit 2, before any listener, link or key (H60).
4. Run the arms, each a soak of `--commands` commands with the same `--seed` (the same commands and the same timing draws in every arm, so the arms differ in the capture pause only): set `src.vision.vision.THREAD_SLEEP_DURATION_S = pause_ms / 1000` (read by the loop on every pass), reset the picture counter, then `soak.run_soak(cfg, soak._new_link(), soak._start_sink, answered=text)` with `cfg = soak.config_from_args(soak.parse_args(["--commands", N, "--seed", S, "--out", <out>/<k>-pause<P>ms, "--short"]))` and `soak.validate(cfg)` checked first. A non-zero exit from an arm stops the check (exit 1, the arms so far kept).
5. After each arm, write `<arm>/arm.json`: `pause_ms`, capture pictures per second over the arm, and from `analyze.load_run` + `analyze.match_delivery` (importable, analyze.py docstring) the Delivery verdict and the reaction residual's p50/p99 computed with realism's rule (copy the six lines of `realism.reaction_residual`, citing realism.py:224-238; a test pins that both give the same p50 on a run dir fixture). The agent never opens `keys.jsonl` itself; the analysis code reads it.
6. Arms and the decision rule (all numbers in the tool as named constants):
   - Order: B1 = pause 20 ms (H80, what every walk so far ran with), N = pause 0, B2 = pause 20 ms.
   - Valid only if all three pass Delivery, N's capture rate is at least twice B1's (B is about 45 pictures a second, H80; if N is not well above it, the window did not change every frame) and the baselines agree: |p50(B1) − p50(B2)| <= 2 ms and |p99(B1) − p99(B2)| <= 10 ms. Otherwise: result "inconclusive", no search.
   - "Keys later" for an arm X: p50(X) > max(p50(B1), p50(B2)) + 2 ms, or p99(X) > max(p99(B1), p99(B2)) + 10 ms.
   - N not later -> result 0 ms (no pause). N later -> search the smallest pause in {1, 2, 5, 10} ms that is not later: run 5; if 5 is not later run 2, and if 2 is not later run 1; if 5 is later run 10; the smallest arm that was not later is the result; if 10 is later too, the result is 20 ms (today's value). At most six arms, about 27 minutes each at the V3 soak's pace (10 000 commands in 2.23 h).
   - Write `<out>/result.json` and print one line per arm (pause, pictures/s, residual p50/p99, Delivery, later yes/no) and the result.
7. Close the test window; the capture and stand-in threads are daemons.

`soak.py` change: `_check_processes(writer, answered: str | None = None)`: None -> asks as today; a text -> writes it to `writer.session["process_check"]` and asks nothing. `run_soak(cfg, link, start_sink, *, answered: str | None = None)` passes it through. `soak.main` is unchanged, so `python -m src.input.harness.soak` still asks once per run.

- [ ] **Step 1: Failing tests** (fakes for the window process, `runtime.start_capture`, `soak.run_soak` and `analyze`; nothing real is opened or sent):
```python
def test_asks_once_for_all_arms():            # soak._read_answer patched to count: 1 call over 3+ arms
def test_refusal_stops_before_any_soak():     # answer "n": exit 2, run_soak never called, window closed
def test_no_picture_stops_before_the_question():  # wait_first_frame Failure: exit 3, _read_answer never called
def test_search_order():                      # residuals scripted so N, 5 and 2 are later, 10 is not -> arms 20,0,20,5,10; result 10
def test_no_search_when_n_is_on_time():       # result 0 after three arms
def test_inconclusive_when_baselines_disagree():
def test_inconclusive_when_the_capture_did_not_run_fast():
def test_out_must_be_under_data():
def test_load_picture_is_found_by_the_capture():   # load_picture(t) through the capture's corner match and follow lock
                                                   # (as test_minimap_follow does): found, and still_there holds from t to t+1
```
`tests/input/test_soak_answered.py`: `_check_processes(writer, answered="answered y: MapleStory closed")` asks nothing and records the text; with no keyword it asks as before (existing soak tests stay green untouched).
- [ ] **Step 2: Run to see them fail.**
- [ ] **Step 3: Implement** as above.
- [ ] **Step 4: Run** `.venv/Scripts/python.exe -m pytest tests/input tests/test_anti_cheat_names.py <the new test file> -q` -> pass.
- [ ] **Step 5: Check, commit.**
```bash
git add tools/key_timing_check.py tools/capture_load_window.py src/input/harness/soak.py tests
git commit -m "feat(tools): key-timing check: soaks with the capture running flat out on a test window, one MapleStory question (WALKFIX Task 10A, QB283 2b)"
```
Done: tests pass, `tools/check.py` green. Nothing was run against the board.

### Part B: run the check (HONG-GATED)

Before it: the orchestrator asks Hong (Q&A page or chat) to confirm that MapleStory is closed and that nobody will use the PC for about three hours, and that he is at the PC to answer the tool's question. No agent runs Part B before that answer, and no agent ever answers the tool's "Is MapleStory closed? y/n" (H66; the brief's binding rule): the agent starts the tool in a new console window on Hong's screen, and Hong types the answer there.

- [ ] **Step 1:** Board connected and bonded (as for any soak, `src/input/CLAUDE.md`). From the worktree root, the agent opens a console window for Hong: `cmd //c start "Helix key-timing check" cmd /k ".venv\Scripts\python.exe tools/key_timing_check.py --out data/keytiming/walkfix-<date>"` (in Git Bash). The tool first shows the test window and waits for the capture to find it; if it exits 3, Hong moves the window (`--at X,Y`) and the agent starts it again.
- [ ] **Step 2:** Hong answers the question in that window. The agent waits for the tool to end (it can read `result.json` and the `arm.json` files; it never opens `keys.jsonl`).
- [ ] **Step 3:** Result:
  - 0 ms: nothing to change; `THREAD_SLEEP_DURATION_S` stays 0.0.
  - P ms (1, 2, 5, 10 or 20): set `THREAD_SLEEP_DURATION_S = P / 1000` with a comment citing the result folder, re-pin `test_loop_pause_is_the_setting`, `tools/check.py` green, commit `fix(vision): capture pause P ms, the smallest that keeps the keys on time (WALKFIX Task 10B, QB283 2b)`.
  - inconclusive or an arm failed: change nothing; report to the orchestrator with the table.
- [ ] **Step 4:** Report to the orchestrator the number and the per-arm table (Hong asked "tell me the number"), and append both to `notes/WALKFIX169-build.md`. If Task 11 is already done, update its item-2 decisions entry and the CLAUDE.md lines that name the pause (listed in Task 11) in the same commit.

Done: the result is recorded; the setting is the number found (or 0); Hong is told through the orchestrator.

---

## Task 11: Docs: decisions entries and CLAUDE.md files (subtle: no; size: M)

**Files:**
- Modify: `docs/decisions.md` (new entries at the end; H101 is the next free number at 026d636: check `grep '^## H' docs/decisions.md` first, and use the next free numbers if another branch took some)
- Modify: `CLAUDE.md` (root: Vision paragraph, Threads and shared state, Running's sim smoke lines, Safety rules' tool list), `src/vision/CLAUDE.md`, `src/route/CLAUDE.md`, `src/input/CLAUDE.md`, `tests/CLAUDE.md` if a test count or rule it states moved
- Modify: `docs/superpowers/specs/2026-10-31-route-tab-design.md` (the lines that name the 20 ms floor: 5.9 table, 5.12-5, 6.3, 12.3, 12.5, as H80 "Affects" lists), `tools/route_tab_sim.py:78` (the `PUMP_PERIOD_S` comment only; its 20 ms value is the sim's display pump and stays)

Entries, each in the file's format (heading, Question/Context, Ruling with Hong's words verbatim, In plain words, Affects, Cost if wrong, Undo, Pointer):
- **"Speed and fluidity are both the goal"**: Hong, Q&A page q-20261113-134259-6e30, 2026-11-13, verbatim "SPeed + fluidity are both the goal. " (ledger-ROPE.md: about the overshoot fix; not itself an answer to fluid165-1).
- **Item 1, walk to a node closer than 17 px** (QB266 verbatim; the keep rule: kept only if his next FF2 10-lap walk is no slower than 221 s, else move the limit or undo; no sliding allowance, with the 8db2 finding that the "slide" is picture delay, workspace `notes/q-20261113-140306-8db2-qa-answer.md` Q2). Affects: Task 1's files; `walk_under_px`.
- **Item 4, sideways teleports stop at a platform's end** (QB282 verbatim, his word as the game confirmation; the game fact as he stated it, workspace `notes/q-20261113-155322-cd0c-qa-answer.md`; the 4 px setting; the measured teleport length 18-19 px, median 19.0 over 64 recorded jumps, and the real-ground sim's extra from 1.0 to 0.0, workspace `notes/q-20261113-153031-26c9-qa-answer.md` Q1; the GAP launch point: WALKFIX169-D1 / QB284 with Hong's answer if given by then, else "provisionally A, open"; if he picks B or C, this entry says so and points to the `gap_launch` change). Affects: Tasks 2-5's files.
- **Item 2, fresher pictures; amends H80** (QB283 item 2 verbatim; H80's 20 ms floor lifted, with Hong's earlier page message that the floor was arbitrary and more processor time is fine, workspace `notes/q-20261113-154947-0927-qa-answer.md`; re-find every 22 s; stamps; the walker's wake and the settle spacing (WALKFIX170-D1 with the orchestrator's or Hong's ruling); the key-timing check's result, or "pending Task 10B" if it has not run). Add one line under H80: "Amended by H<n> (2026-11-13): the floor is lifted; see there." (the only edit to an old entry).
- **Item 3, the stopped check after his walk** (QB283 item 3 verbatim; decided, not built; Task 13).
- **The rope fix and the off-map fix are signed off** (QB267, page 2026-11-13 16:22, verbatim "Yes, sign off both the rope fix and the off-map fix"; option A; a sign-off merges nothing).
- The order and the done rule (ledger-ROPE.md `Decision (Hong) (chat paste`): "1, 2 and 4 can be built now (2's no-pause part is final only after the key-timing check). Then my 10-lap walk ... Then 3." and "tested in the simulator, every check passing, reviewed by a second agent, and checked by my own walk": in the item 1 entry, pointed to from the others.

CLAUDE.md updates (make each true; a fact lives in one file, the others point to it):
- Root `CLAUDE.md`: Vision: the stamp, re-find every 22 s, "no fixed pause between pictures (H<n>, amends H80)" or the number Task 10B set; Threads: `minimap_frame_ns` and `minimap_frame_new` written with the frame under the lock, the walker waking on it; Running: both sim smoke lines with the figures from `notes/WALKFIX169-build.md` (the last recorded run of each command), and the marked-map run if Task 5 made it a documented command; Safety rules: `tools/key_timing_check.py` sends keys through the soak, asks the MapleStory question once, HONG-GATED.
- `src/vision/CLAUDE.md`: the pause bullet (value, its setting, `time.sleep(0)` yields on Windows), the re-find bullet, the stamp (`grab_new_frame_stamped`, `_grab_stamped`, the tests' patch of `vision._grab_stamped`), the closing bullet's "floor at vision.py:82".
- `src/route/CLAUDE.md`: frozen-file exceptions (walker: the `wait_frame` keyword and the six waits, Task 7; planner/route_graph/terrain/params: Tasks 1-3); the position source's `frame_ns` and `wait_new`; the step log's `EXTRA_FIELDS`; the recorder's `frame_ns`; the new tests and fixtures; the real-ground preset's 0.0 extra.
- `src/input/CLAUDE.md`: `run_soak`'s and `_check_processes`'s `answered` keyword and that `tools/key_timing_check.py` uses soak's private names (`_new_link`, `_start_sink`), in the "soak.py is a library in practice" bullet.

- [ ] **Step 1:** Write the entries and the CLAUDE.md edits. Re-read each changed `file:line` reference against the code at this commit.
- [ ] **Step 2:** `tools/check.py` green (docs only, but `tests/test_anti_cheat_names.py` reads `tools/` and the docstring test reads `vision.py`).
- [ ] **Step 3:** Commit `docs: decisions for Hong's 2026-11-13 walking fixes (H<a>-H<b>), H80 amended, rope and off-map fixes signed off; CLAUDE.md files (WALKFIX Task 11)`.

Done: every entry above exists with Hong's words verbatim; every CLAUDE.md fact touched by Tasks 1-10 is true at this commit.

---

## Task 12: The full check (subtle: no; size: S)

- [ ] **Step 1:** `HELIX_NO_ELEVATE=1 .venv/Scripts/python.exe tools/check.py --full` -> ALL PASS (10 skips in `tests/input/test_protocol.py`). Record the total time and the pytest line in the build notes.
- [ ] **Step 2:** The four sim runs once more; figures equal the last recorded ones and root CLAUDE.md's.
- [ ] **Step 3:** `git status` clean apart from the `.venv` junction, which is gitignored; remove it with `cmd //c rmdir "C:\Users\Qing_programming\Desktop\helix-fluid\.venv"` only (Task 0: a recursive delete would follow it into the main clone's venv).

Done: `--full` green on the branch head; nothing to commit. Merging and Hong's 10-lap walk are the orchestrator's (the walk is Hong's).

---

## Task 13: Item 3, the stopped check (AFTER-WALK; outline only, planned in full after Hong's walk)

Hong (QB283): "3. Knowing when the character has stopped, after my next walk. From the stamps in my next walk, measure the exact delay from a key to the picture that shows it (teleport jumps and the ends of walks). Then replace "two pictures in a row the same" with: after each move, wait for a picture taken at least that delay after the key was let go, plus a small margin for a slow Bluetooth key, and plan from that picture. Not the "dot sits still for 0.1 to 0.15 s" version."

Starts only after Hong's 10-lap walk with Tasks 1-12 built (Hong's order: "Then my 10-lap walk ... Then 3.").

Outline:
1. Measure (an agent, from his run folder, no key): key-down and key-up times from `steps.jsonl` (`planned` t_ns + `sent` waited_ms, then the `keys` deltas in 100 µs units); picture times from `frames/index.jsonl` `frame_ns` (Task 6) with the character found in each PNG by `CharacterLocator`, and the walker's own reads' `frame_ns` on `planned`/`settled` lines. Delays: key-down of a sideways teleport to the first picture showing the jump (|dx| >= 8); key-up of a walk to the first picture after which the position no longer changes. The recorder saves 10 pictures a second (`RECORD_FPS`, session.py:87), so each move's delay is known to within 0.1 s; over the walk's ~200 moves the median is good to about 0.01 s (see WALKFIX170-D2). Report median, p90, p99 per kind.
2. Decide with Hong (raised by the orchestrator): D = which percentile, the margin ("a small margin for a slow Bluetooth key": the V3 soak's residual p99 − p50 is about 46 ms, spec :141).
3. Build (frozen `walker.py` and `params.py`, Hong's QB283 item 3 opens them): new settings `picture_delay_s`, `key_margin_s`; the walker learns each move's key-up time from the `ActionRecord` `execute` returns and the picture's stamp from the position source (a protocol extension: `frame_ns` from Task 6, decided then); `_settle` waits (Task 7's waiter) for a picture with `frame_ns >= key_up_ns + D + margin` and plans from it, replacing the two-equal-reads rule and `min_settle_s`'s role (WALKFIX170-D1 ends here). The simulator gets a picture delay (`SimPositionSource` reads `position_at(now − D)`) so the new rule has a simulator test; sim figures will move and are re-pinned there.
4. Done rule as for every item: simulator test, every check, a second agent's review, Hong's walk.
