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

## Piece 2 (not yet written)

Tasks 6 onward are written by a second planning pass (contract in workspace `notes/WALKFIX169-plan.md`): 6 item 2d picture time stamps; 7 item 2a wake the walker on a new picture (with the SWEEP165-D3 docstring fix at `walker.py:38`); 8 item 2c re-find the minimap by time; 9 item 2b no fixed capture pause (code); 10 HONG-GATED key-timing check; 11 docs (decisions entries, CLAUDE.md files, root CLAUDE.md sim figures from `notes/WALKFIX169-build.md`); 12 `python tools/check.py --full`; 13 AFTER-WALK item 3 (outline).
