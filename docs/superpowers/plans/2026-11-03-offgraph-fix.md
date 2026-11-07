# Off-graph fix (option C) and the real-ground simulator: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** the route simulator fails today's walker on Fossil Foundry 2's top slope, then the walker stops freezing there (and on any uneven ground) and the simulator proves it.

**Architecture:** group A teaches the simulator what the live runs showed: the real ground under the map's nodes, the dot's centre 3 px above the node line, sideways teleports as long as the live ones, and mob knockback, all behind defaults that keep today's runs bit-identical. Group B is Hong's option C: (1) every position source reports the point 3 px below the dot's centre, the reference the map's nodes use; (2) a character read at one position for 0.5 s while it cannot be placed on the map is standing, so the walker attaches it to the platform line within 6 px under or over it, or ends the run with a clear message when there is none. Group C (the last task) runs the proofs and writes the spec, decisions and CLAUDE.md text.

**Tech Stack:** Python 3.14, `returns` (`Success`/`Failure`), numpy, pytest; the sim (`src/route/sim/`) drives the real `RouteWalker` and `InputLayer`.

**Spec:** `docs/superpowers/specs/2026-10-21-p1b-route-walking-design.md` (spec B). Design and evidence: `.superpowers/orchestrator/2026-10-21-phase1/notes/OFFD140.md` (option C, the replay table, the test plan) and `notes/OFFG140.md` (the bug). Hong's decisions: QB212, QB213 (OFFD140-D1 = C), QB214 (Task 8 quotes them).

## Global Constraints

- Hong's numbers (QB213): read the position **3 px** lower; **still for 0.5 s** = standing; attach within **6 px** of the platform under it; none → stop with a clear message. They live in code as `position.DOT_TO_FOOT_PX = 3`, `MovementParams.standing_s = 0.5`, `MovementParams.standing_band_px = 6`. Never change these values.
- Frozen files opened by Hong's pick (Ruling [spec-text], orch 140): `walker.py` (Task 6), `locate.py` (Task 5), `params.py` (Task 5) and their tests. **`planner.py` is not changed** by this plan: the walker plans from the snapped point with the unchanged `next_move`, so `route_preview.py` (which repeats `next_move`) needs no change either. Nothing else in the frozen list is touched.
- `C:/Users/Qing_programming/Desktop/helix-rt` is read-only. Never open, copy or read any `keys.jsonl`. Commit only derived data (a ground profile, positions), never a run folder.
- No step runs a walk against the board, starts the app (`python -m src.main`), or runs `probe`, `soak`, `session_starts`, the walk CLI or `measure_moves`. Tests use `SimLink` or fakes. Every command runs with `HELIX_NO_ELEVATE=1`.
- Inputs stay human-like: the fix adds no new key pattern (it changes where the walker plans from, never which moves exist).
- Route code returns `Success`/`Failure` and checks them with `is_successful`; snake_case; new test basenames unique across `tests/` (no `__init__.py`, flat imports).
- Defaults keep today's runs: until Task 4 every new sim knob defaults to "off", so the seed-2 smoke stays `366 moves, 25 no-ops, 21 drops, 23 teleport downs, sim_seconds 241.7168, 6/6 PASS`, and the 1000-lap target run stays `18093 moves, 1054 no-ops, 1003 drops, 1121 teleport downs, sim_seconds 11945.1853, 6/6 PASS` (both measured at 1dbb2b4 on 2026-11-03).

Commands (from the worktree root `C:/Users/Qing_programming/Desktop/helix-offg`, Git Bash; `.venv` is a gitignored junction to the main repo's venv):

```
export HELIX_NO_ELEVATE=1
SMOKE='.venv/Scripts/python.exe -m src.route.sim.run --laps 20 --seed 2 --min-single-error-legs 0'
TARGET='.venv/Scripts/python.exe -m src.route.sim.run --laps 1000 --seed 1 --known-case 49,28:101,24 --known-case 101,24:105,42'
```

## Review Focus

1. Saved runs made before this change (their step-log header `params` lack `standing_s` and `standing_band_px`) must still open in the replay and the Route tab (`src/route_view/model.py:274` builds `MovementParams(**header.params)`): test in Task 5.
2. A character the strict rules place on the map (A1-A3, including A1 next to an end node and A3 beyond a platform's end, both outside every segment) with **no path** to the target is not "off the map": the still timer never runs for it, the walker keeps waiting as today and never ends the run: test in Task 6, at an A1 and an A3 position.
3. A mob knockback that carries the character through the air (one that falls, so the read changes every poll) must never attach loosely or send a key while the position still changes: test in Task 6 (sim scenario).
4. F12 (the stop flag) during the 0.5 s wait still wins: the leg ends `stopped`, not `off_map`: test in Task 6.
5. A loose attach that lands on the leg's target ends the leg `reached` with no key: test in Task 6.

## Decided: how the off-map stop shows (OFFP141-D1, Hong, QB215)

Decision (Hong) OFFP141-D1 (QB215, 2026-11-03): **"Console line only (Recommended)"** = option A. A new leg outcome `off_map` (legs.jsonl and the step log's `leg_end`), run reason `off_map`, `stop.json` reason `off_map`, exit 10 (the existing "character lost" code), and one console line from the walker: `the character is standing at (x, y), more than 6 px from any platform of the map; ending the run`. The Route tab's status line stays its exit-10 text "Ended: the character was lost (exit 10)."; the console box carries the reason: the walker's line, then the usual `stopped: off_map` line that every stop prints (Hong, 2026-11-07, "Keep both lines"). Task 8 quotes it in `docs/decisions.md` with QB212-QB214.

## How "within 6 px" is read (Ruling, orch 141)

Hong's words were "attach to the platform under it within 6 px". The plan reads this as **within 6 px on either side of the read, above or below the platform's line**, as OFFD140 and its option text have it: a dip in the ground puts the read below the mapped line, so "under" cannot mean "only lines below the read". The cost of this reading: a character read up to 6 px under a line attaches to the platform above it. H85 (Task 8) says this in plain words so Hong sees it.

---

### Task 1: Real-ground and live-read fixtures from Hong's runs

Class: integration (image analysis with judgment; no walker code).

**Files:**
- Create: `tools/derive_real_ground.py` (one-off, kept so the fixtures can be re-derived)
- Create: `tests/fixtures/ff2_real_ground.json`, `tests/fixtures/ff2_live_reads.json`
- Test: `tests/route/test_real_ground_fixture.py`

**Interfaces:**
- Produces: `ff2_real_ground.json` in `load_terrain`'s schema (`src/route/sim/world.py:90`; extra keys are ignored) plus `"_source"`: the four platforms `floor`, `mid-left`, `top`, `mid-right` with the same `droppable` as `tests/fixtures/ff2_terrain.json`, points in **foot reference** = real ground row − 3 (the row the map editor's nodes sit on), only change points kept.
- Produces: `ff2_live_reads.json` = `{"_source": str, "runs": [{"run": "<folder name>", "events": [[t_ms, ev, x, y], ...]}], "side_teleports": [[x_from, x_to], ...]}`. `ev` is one of `leg` (x, y = the leg's dst), `no_plan`, `planned`, `reached`, `settled`, `read_failed` (x, y null); positions are the logged **dot-centre** pixels; `t_ms` from the run's first event. `side_teleports`: each logged `planned` teleport LEFT/RIGHT with its next `settled` position in the same leg, filtered by x only (no |dy| filter: a |dy| ≤ 3 filter drops the steepest ramp landings, e.g. (50,25)→(71,21) and (51,26)→(72,21), and could pull the median to 19). No `sent` events and no key data of any kind.

- [ ] **Step 1: Write the failing tests** in `tests/route/test_real_ground_fixture.py`:
  - `test_real_ground_loads`: `load_terrain(...)` is a `Success`; platform names and `droppable` equal `ff2_terrain.json`'s.
  - `test_top_ramp_matches_the_recordings`: on `top`, `surface_y(x) == 28` for x in 50..67, `27` at 68, `26` at 69 and 70, `25` at 71, `24` for x in 72..100 (the measured ground minus 3; OFFD140 section 1's prose said ground 31 up to x = 68, one pixel off at its last column: OFFG-T1-D1 option A, H86).
  - `test_flat_levels_under_the_nodes`: the surface under every node of `tests/fixtures/fossil_foundry_2.csv` is floor 61, mid-left 46, mid-right 43, top-left 28 (x ≤ 66), top-right 24 (x ≥ 79) (OFFD140 section 1).
  - `test_live_reads_counts`: 8 runs; 1229 `no_plan`, 159 `planned` and 55 `reached` events in total (OFFD140 section 3); 48 `side_teleports`; every `ev` in the set above; no key named `keys` or `sent` anywhere in the file.
  - `test_standing_reads_sit_on_the_ground`: for at least 95% of `settled` events, `y + 3` is within 1 px of the fixture surface at `x` (the dot's centre is 6 px above the ground: OFFD140 found +6/+5/+7 in nearly all frames).
- [ ] **Step 2: Run** `.venv/Scripts/python.exe -m pytest tests/route/test_real_ground_fixture.py -q`. Expected: FAIL (fixtures missing).
- [ ] **Step 3: Write `tools/derive_real_ground.py`** with `main(argv) -> int`, arguments `--runs DIR --terrain-out PATH --reads-out PATH`. It opens only `frames/*.png`, `steps.jsonl` and `map.csv` of each run folder (an explicit file list; it never lists or opens `keys.jsonl`) and writes only the two out paths. Ground per x: the median frame per run, first row with grey ≥ 180 under the platform; where bright objects cover the ground (top x 48-56 and 79-96, floor near x 82; OFFD140 section 1 and "Considered and set aside") use the standing reads (dot y + 6) instead. OFFD140's throwaway scripts show the method: `C:\Users\QING_P~1\AppData\Local\Temp\claude\f6e17fea73ddab56\fefc95cb-56df-4a7c-b486-1ab879edee7e\scratchpad\offd\analyse.py` (read it; do not import it; it sits under %TEMP% and may have been cleaned by the time you build: if it is gone, follow the method in OFFD140 section 1, which is enough). The runs' `map.csv` equals `tests/fixtures/fossil_foundry_2.csv` apart from line endings (checked 2026-11-03), so the tests use the fixture map.
- [ ] **Step 4: Run** `.venv/Scripts/python.exe tools/derive_real_ground.py --runs C:/Users/Qing_programming/Desktop/helix-rt/data/route --terrain-out tests/fixtures/ff2_real_ground.json --reads-out tests/fixtures/ff2_live_reads.json`, then the Step 2 command. Expected: PASS. Write the median of `|x_to - x_from|` over `side_teleports` into your notes (OFFG140 fact c expects 20-21, i.e. 1-2 px beyond Kanna's reach of 19; the plan review measured 20 on these runs). If the median is below 20, stop and report: Task 3's before-proof needs an extra of at least 1 px. If a test fails because the recordings disagree with OFFD140's figures, stop and report it; do not edit the expected values.
- [ ] **Step 5: Commit** `git add tools/derive_real_ground.py tests/fixtures/ff2_real_ground.json tests/fixtures/ff2_live_reads.json tests/route/test_real_ground_fixture.py` and `git commit -m "test(route): real-ground and live-read fixtures from Hong's FF2 runs (OFFP141 T1)"`.

### Task 2: The simulated dot, teleport length and mob knockback

Class: integration (simulator only).

**Files:**
- Modify: `src/route/sim/world.py` (`SimParams` :117, `NoiseConfig` :126, `InjectedError` :141, `teleport` :221, `walk` :252, `drop` :277, `position_at` :298)
- Modify: `src/route/sim/link.py` (`SimPositionSource.read` :129)
- Test: `tests/route/test_sim_real_world.py`

**Interfaces:**
- Produces: `SimParams.dot_above_foot_px: int = 0`, `SimParams.side_teleport_extra_px: float = 0.0`, `SimParams.knockback_air_s: float = 0.3`; `NoiseConfig.knockback_p: float = 0.0`, `NoiseConfig.knockback_px: tuple[int, int] = (3, 8)`; `InjectedError.KNOCKBACK = "knockback"`.
- Produces: `SimWorld.knockback(dx_px: float, now_ns: int) -> MoveEffect`: from `self.pos`, x + dx, landing on `terrain.surface_below(x_new, y - _SIDE_TELEPORT_RISE_PX)`; no surface: stays put; kind `"knockback"`, `settle_s = knockback_air_s + max(0, y_land - y) / fall_px_per_s`, `injected = (KNOCKBACK,)`, `t_end_ns = now_ns`. The shove starts at `now_ns`: a knockback injected after a move is recorded at that **move's own `t_end_ns`** (not after its settle), so `SimWorld.pos` and every read agree from the moment the walker can next read; the walker's `_settle` then waits through the shove like any other settle: a falling knockback changes the read at every poll, so `_settle` ends only after the landing; a slow flat shove (3 to 6 px over 0.3 s, depending on the fractional x it starts from) can end it one or two polls early, on two equal reads while the character is still 1 to 2 px short of `world.pos`, as live, and the next move is then recorded from `world.pos` (plan review I4, N1).
- Produces: `position_at` for an effect of kind `"knockback"` moves **x** linearly from start to end over the whole `settle_s` as well as y (today's curve jumps x at once and moves only y, so a knockback on level ground would never be airborne). Effects of every other kind keep today's curve exactly, so seeded runs without knockback stay bit-identical.
- Produces: `SimPositionSource.read()` returns `Success((round(x), round(y) - world.params.dot_above_foot_px))`: round first, then subtract, so that Task 4's `+3` gives back exactly today's `round(y)` (`round` sends halves to the even integer, so `round(y - 3) + 3` would not).

- [ ] **Step 1: Write the failing tests** in `tests/route/test_sim_real_world.py` (world on `ff2_real_ground.json`, `NoiseConfig.off()` unless stated, reach 19/23 read from `Kanna()`, never written as literals in `src`):
  - `test_read_reports_the_dot`: world at (70, 26.0) with `dot_above_foot_px=3` reads (70, 23); at y 42.5 it reads (x, 39).
  - `test_side_teleport_extra`: from (49, 28) with `side_teleport_extra_px=2`, `teleport(Key.RIGHT)` ends at (70.0, 26.0).
  - `test_knockback_lands_and_moves_through_the_air`: from (60, 28), `knockback(5, t)` ends at (65, 28) with `injected == (InjectedError.KNOCKBACK,)` and `settle_s == 0.3`; `position_at(t + 0.15 s)` is (62.5, 28) (x moves over the settle, level ground included); and a knockback that falls (off the top platform's left end onto the floor) gives reads 50 ms apart that differ at every step until it lands.
  - `test_knockback_without_ground_stays`: dx that leaves every platform: position unchanged, kind `"knockback"`.
  - `test_knockback_p_one_follows_every_move`: `knockback_p=1.0`: after one teleport, `world.effects[-1].kind == "knockback"`, with `t_end_ns` equal to the teleport's `t_end_ns` and `start` equal to the teleport's `end`; `world.pos` equals the knockback's `end`; `position_at` at any time ≥ the teleport's `t_end_ns` follows the knockback's curve.
  - `test_defaults_change_nothing`: `run_sim` with the defaults, 20 laps, seed 2 gives moves 366, no_ops 25, drops 21, teleport_downs 23, sim_seconds 241.7168 (rounded to 4 places).
- [ ] **Step 2: Run** `.venv/Scripts/python.exe -m pytest tests/route/test_sim_real_world.py -q`. Expected: FAIL (`dot_above_foot_px` unknown, no `knockback`).
- [ ] **Step 3: Implement** the interfaces above. Sideways teleport: `x_new = x + sign * (reach_h + side_teleport_extra_px + overshoot) + landing`. Knockback injection: at the end of `teleport`, `walk` and `drop` (not `nothing`, not a no-op), only `if self.noise.knockback_p > 0`: one `rng.random()`; on a hit, sign by one more `rng.random() < 0.5`, size `rng.integers(lo, hi + 1)`, then `self.knockback(dx, move.t_end_ns)`. With `knockback_p == 0` there is no draw, so seeded runs stay identical. Note in the module docstring's "Where the plan is silent": a knockback replaces the move's settle curve (the character shows at the move's end at once and is shoved from there), which keeps one effect live at a time, so `position_at` (it reads only `effects[-1]`) and `SimWorld.pos` never disagree.
- [ ] **Step 4: Run** the Step 2 command, then `$SMOKE` and `$TARGET`. Expected: PASS; both runs print exactly the figures under Global Constraints.
- [ ] **Step 5: Commit** `git commit -m "feat(sim): dot offset, live teleport length and mob knockback, all off by default (OFFP141 T2)"` with the two source files and the test.

### Task 3: The real-ground run, and proof that today's walker freezes on the slope

Class: integration.

**Files:**
- Modify: `src/route/sim/run.py` (`SimReport` :91, `run_sim` report :397-417, `main` :471-499)
- Test: `tests/route/test_sim_real_ground.py`

**Interfaces:**
- Consumes: Task 1's fixtures, Task 2's knobs.
- Produces: in `run.py`: `REAL_GROUND_TERRAIN: Path` (`tests/fixtures/ff2_real_ground.json` under `_REPO`), `REAL_GROUND_SIM = SimParams(dot_above_foot_px=3, side_teleport_extra_px=<Task 1's median − reach_h>)`, `REAL_GROUND_NOISE = NoiseConfig(knockback_p=0.02)`; `SimReport.knockbacks: int` (effects with `KNOCKBACK`); CLI flag `--real-ground` (uses those three; FF2 only: with `--terrain` also given, or with a `--map` other than the FF2 fixture, `parser.error`, exit 2).

- [ ] **Step 1: Write the failing tests** in `tests/route/test_sim_real_ground.py`:
  - `test_preset_teleport_matches_the_recordings`: `REAL_GROUND_SIM.side_teleport_extra_px == median(|x_to - x_from|) - Kanna().max_horizontal_traversal_dist` over `ff2_live_reads.json`'s `side_teleports`, and `REAL_GROUND_SIM.side_teleport_extra_px >= 1` (assert message: with an extra of 0 the sim lands at x 68 from 49, which attaches on today's code, so the before-proof below cannot fail; OFFD140 saw exactly this).
  - The two frozen runs below take about 7 s of wall time each (the review measured an equivalent freeze), so both are marked `@pytest.mark.slow` (tests/CLAUDE.md: tests of 0.5 s or more are `slow`) and share one run through a module-scoped fixture.
  - `test_real_ground_run_passes`, marked `@pytest.mark.slow` and `@pytest.mark.xfail(strict=True, reason="today's walker freezes on the top slope; Task 4 removes this mark")`: `run_sim(fixture map, REAL_GROUND_TERRAIN, 20, 2, noise=REAL_GROUND_NOISE, sim_params=REAL_GROUND_SIM)` and every `judge(report, 0)` check is ok.
  - `test_today_freezes_on_the_top_slope` (`slow`; deleted in Task 4): the same run with a recording observer (all nine `StepObserver` methods): the first leg whose `leg_end` outcome is not `reached` has src (49, 28) and dst (101, 24), and most of its `no_plan` positions have 66 ≤ x ≤ 79 and y ≤ 25 (dot pixels on the ramp). Pin the exact facts you see (count, first position) in the test once Step 4 shows them.
  - `test_real_ground_flag_conflicts_with_terrain`: `main(["--laps", "1", "--seed", "2", "--real-ground", "--terrain", "x.json"])` raises `SystemExit(2)`.
  - `test_real_ground_flag_is_ff2_only`: `main(["--laps", "1", "--seed", "2", "--real-ground", "--map", "other.csv"])` raises `SystemExit(2)`; `--map` naming the FF2 fixture itself is accepted.
- [ ] **Step 2: Run** `.venv/Scripts/python.exe -m pytest tests/route/test_sim_real_ground.py -q` (plain pytest runs the `slow` tests too; only `tools/check.py` without `--full` skips them). Expected: FAIL (names missing).
- [ ] **Step 3: Implement** the interfaces. `--real-ground` passes `noise=REAL_GROUND_NOISE, sim_params=REAL_GROUND_SIM` and the terrain to `run_sim`; nothing else in `run_sim` changes.
- [ ] **Step 4: Run** the Step 2 command (expected: PASS, the xfail counted as xfailed; check that adding `-m "not slow"` skips the two frozen runs), then the "before" run `$SMOKE --real-ground` and copy its whole output into your notes. Expected: `FAIL laps` with timed-out legs, the first at (49, 28) → (101, 24). If the first failure is anywhere else, or every leg fails (the offset everywhere, not the slope), stop and report: the fixture or the preset is wrong. Then `$SMOKE` and `$TARGET`: unchanged figures plus a new `knockbacks: 0` line.
- [ ] **Step 5: Commit** `git commit -m "test(sim): real-ground run; today's walker freezes on FF2's top slope (OFFP141 T3)"`.

### Task 4: Fix part 1, read the character 3 px lower

Class: integration (`position.py` and the sim; no frozen file).

**Files:**
- Modify: `src/route/position.py` (`MinimapPositionSource.read` :30-39), `src/route/sim/link.py` (`SimPositionSource.read`), `src/route/sim/world.py` (`SimParams.dot_above_foot_px` default)
- Modify: `tests/route/test_sim_real_ground.py` (remove the xfail mark, delete `test_today_freezes_on_the_top_slope`; time the now-passing run and drop its `slow` mark if it takes under 0.5 s)
- Modify: `tests/route/test_sim_real_world.py` (`test_read_reports_the_dot`: after the shift the source returns the dot plus 3, so its expected reads become (70, 26) and (x, 42); keep the dot itself pinned through `round(y) - world.params.dot_above_foot_px` in the same test)
- Test: `tests/route/test_position_foot.py`, `tests/route/test_offgraph_replay.py`

**Interfaces:**
- Produces: `position.DOT_TO_FOOT_PX: int = 3` (comment: the dot's centre is 6 px above the ground, the editor's node circle of radius 3 rests on it: Hong's QB213) and `position.foot_from_dot(dot: tuple[int, int]) -> tuple[int, int]` = `(x, y + DOT_TO_FOOT_PX)`. `MinimapPositionSource.read` returns the locator's `Success` through `foot_from_dot` (failures, `STALE` included, unchanged). `SimPositionSource.read` returns `Success(foot_from_dot((round(x), round(y) - dot_above_foot_px)))`. `SimParams.dot_above_foot_px` default becomes `3`, in the same commit, so the default sim reads exactly what it read before.
- Every walker read (plan and settle), the step log, the leg log and the replay now hold foot-reference positions; the session's first "character found" read and `measure_moves` get them too (only deltas or success matter there). The idle live minimap still draws the dot from the frame.

- [ ] **Step 1: Write the failing tests.**
  - `tests/route/test_position_foot.py`: `test_foot_from_dot` ((70, 22) → (70, 25)); `test_minimap_source_shifts_the_located_dot` (fake shared frame and a fake locator returning `Success((40, 50))`: read gives (40, 53)); `test_failures_pass_through` (`Failure("no frame")`, `Failure(STALE)`, the locator's own `Failure` unchanged); `test_sim_dot_matches_the_live_offset` (`SimParams().dot_above_foot_px == DOT_TO_FOOT_PX`).
  - `tests/route/test_offgraph_replay.py` (rg = `RouteGraph.build` of the fixture map's path graph with Kanna and default `MovementParams`; replay = every `no_plan` event of `ff2_live_reads.json`, tried with `locate.attach`): `test_today_all_1229_fail` (logged positions as they are: 1229 failures); `test_shift_leaves_five_mid_air` (positions through `foot_from_dot`: exactly 5 failures, dot positions (69, 21) ×3 and (67, 21) ×2; the longest run of consecutive failing polls in one leg lasts ≤ 0.21 s; each of the 5 is at least 2 px above `ff2_real_ground.json`'s surface after the shift, i.e. airborne; OFFD140 measured 4 px on its own profile). These are OFFD140's replay figures ("shift 3" row).
  - `test_shift_keeps_the_good_reads` in the same file (the other direction, plan review I3): every `planned` and `reached` event (159 + 55) attaches with the logged position; through `foot_from_dot` all of them still attach **except exactly 3 `planned` reads**, dot positions (107, 24), (105, 44) and (103, 44). Pin each as mid-fall: in the same run and leg, the event before it is a `settled` read at the same x, 2-3 px higher ((107, 22), (105, 41), (103, 41)), about 50 ms earlier (the walker settled early and planned a drop or teleport DOWN from the air). No read that attaches both before and after changes platform. This is a live behaviour change (the walker now waits for the landing instead of planning from the air); Task 8 states it in spec B and H85.
- [ ] **Step 2: Run** `.venv/Scripts/python.exe -m pytest tests/route/test_position_foot.py tests/route/test_offgraph_replay.py -q`. Expected: FAIL (`foot_from_dot` missing). If the 3 reads of `test_shift_keeps_the_good_reads` differ from the review's on your fixture, stop and report; do not edit the expected values.
- [ ] **Step 3: Implement** the interfaces; remove the xfail mark and delete `test_today_freezes_on_the_top_slope` (its output is in Task 3's notes); update `test_read_reports_the_dot` as listed under Files. If `tests/route/test_import_rules.py` refuses `sim/link.py` importing `src.route.position`, stop and report (contract f allows it: `sim` is the top layer).
- [ ] **Step 4: Run** `.venv/Scripts/python.exe -m pytest tests/route -q`, `$SMOKE` and `$TARGET` (expected: the Global Constraints figures, `knockbacks: 0`) and `$SMOKE --real-ground` (expected: 6/6 PASS; copy the output into notes). If the real-ground run still fails, stop and report where.
- [ ] **Step 5: Commit** `git commit -m "fix(route): read the character 3 px below the dot's centre, where map nodes sit (QB213, OFFP141 T4)"`.

### Task 5: Hong's two numbers and the loose attach

Class: subtle (`locate.py`, `params.py`).

**Files:**
- Modify: `src/route/params.py:10-26`, `src/route/locate.py` (new function after `attach`, :84)
- Modify (only if a pin breaks): `tests/route/test_params.py`
- Test: `tests/route/test_locate_standing.py`

**Interfaces:**
- Produces: `MovementParams.standing_s: float = 0.5  # QB213: read still this long = standing` and `MovementParams.standing_band_px: int = 6  # QB213`; `validate` covers them through its existing loop.
- Produces: `locate.attach_standing(rg: RouteGraph, pos: tuple[int, int]) -> Result[tuple[int, int], str]`: over every platform segment with `Lx <= px <= Rx` (a vertical segment is skipped, as in A2), the one whose interpolated y (exact `Fraction`) is nearest to py with `|offset| <= rg.params.standing_band_px`, above or below; ties the lower platform index, then the leftmost segment. `Success((px, y_line))` with `y_line` = the line's y rounded half up (`floor(y + 1/2)` on the `Fraction`). Else `Failure(f"standing at ({px}, {py}), more than {band} px from any platform of the map")`. Beyond-the-end positions get no loose attach (A3 already covers those within reach).

- [ ] **Step 1: Write the failing tests** in `tests/route/test_locate_standing.py` (FF2 fixture map, default params, positions in foot reference):
  - `test_defaults_are_hongs`: `standing_s == 0.5`, `standing_band_px == 6`; `validate` refuses `standing_band_px=0` and `standing_s=0`.
  - `test_above_the_ramp_snaps_to_its_line`: (70, 22) → `Success((70, 27))` (line 26.77); `attach(rg, (70, 27))` is a `Success` on the top platform.
  - `test_band_edges`: floor line y 60 at x 90: (90, 54) and (90, 66) → `Success((90, 60))`; (90, 53) → `Failure("standing at (90, 53), more than 6 px from any platform of the map")`.
  - `test_stacked_platforms_take_the_nearest`: (60, 40) → mid-left's line (`Success((60, 46))`); (60, 52) → `Failure` (mid-left 6.1 px, floor 8 px).
  - `test_outside_every_platform`: (20, 60) → `Failure`.
  - `test_snapped_point_reattaches_to_the_same_platform`: for every integer x of every non-vertical segment and every dy in −6..6, the snapped point passes `attach` and its anchor's platform is the one `attach_standing` chose.
  - `test_old_step_logs_still_build_params` (Review Focus 1): `MovementParams(**params)` with a dict lacking the two new keys builds, with the defaults.
- [ ] **Step 2: Run** `.venv/Scripts/python.exe -m pytest tests/route/test_locate_standing.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** the interfaces.
- [ ] **Step 4: Run** `.venv/Scripts/python.exe -m pytest tests/route tests/route_view -q`. Expected: PASS (a step-log or view test that pins the full `params` dict follows `dataclasses.asdict(MovementParams())` and should not need editing; if one pins a literal list, update it and say so in the commit body).
- [ ] **Step 5: Commit** `git commit -m "feat(route): loose attach for a standing character, 0.5 s and 6 px (QB213, OFFP141 T5)"`.

### Task 6: The walker's still rule and the off-map stop

Class: subtle (`walker.py`: the control loop, the 0.5 s timing).

**Files:**
- Modify: `src/route/walker.py` (`walk_leg` :155-267, `_run_laps` :304-330, `_reason` :373, `RunSummary` :118), `src/route/leg_log.py` (`LegOutcome` :31)
- Modify: `tests/route/test_leg_log.py:60` (the outcome list gains `"off_map"`; it pins Feature D's values)
- Test: `tests/route/test_walker_standing.py`; extend `tests/route/test_offgraph_replay.py`

**Interfaces:**
- Consumes: `locate.attach_standing`, `MovementParams.standing_s` (Task 5); `SimWorld.knockback` (Task 2).
- Produces: `LegOutcome.OFF_MAP = "off_map"`; `RunSummary.reason` may be `"off_map"`; `walker._Stillness` with `see(pos: tuple[int, int], t_ns: int) -> float` (seconds the same position has been read in a row; a new position restarts it at 0) and `reset() -> None`.
- Log line (Hong's OFFP141-D1, QB215): `f"the character is {attach_standing's failure message}; ending the run"`, through `self._log_line`. It is built from `attach_standing`'s own message, so the band comes from `params.standing_band_px`, never a literal "6"; with the defaults it reads `the character is standing at (x, y), more than 6 px from any platform of the map; ending the run`. (x, y) is the foot reference the walker reads (3 px below the dot's centre); spec B (Task 8) says so.

Rules in `walk_leg` (the timer runs only while the character cannot be placed on the map, after the settle; `_settle` is unchanged):
- a read `Failure` other than `STALE` → `still.reset()`; `STALE` leaves it alone;
- a plan `Success` → `still.reset()`;
- a plan `Failure` (count `off_graph` and emit `no_plan` as today), then:
  - if `locate.attach(rg, pos)` is a `Success` (placed by A1-A3 but the planner still failed, e.g. `no path from ...`, planner.py:211-213) → `still.reset()`, sleep `poll_s` and continue as today: the still rule never runs for a placed character (Review Focus 2; plan review I1: A1 near an end node and A3 beyond an end place positions outside every segment, which `attach_standing` does not see);
  - else if `still.see(pos, read_ns) >= params.standing_s`: `attach_standing(rg, pos)`; on `Failure` log the line, outcome `OFF_MAP`, break; on `Success(snapped)` call `next_move(rg, snapped, dst, rng)` once; if that succeeds, carry on with `pos = snapped` and that decision through the normal path (progress, stop check, `planned`, `execute`, settle); otherwise sleep `poll_s` and continue as today;
  - else sleep `poll_s` and continue as today.
- every move sent (after `execute` returns, before the settle) → `still.reset()`, so after a loose move that changed nothing (a no-op) the walker waits another 0.5 s "in a row" before the next loose attach (plan review M3).
- `walker.py` may import `locate` (lower layer, import-linter contract f).
- `_run_laps` ends the run on `OFF_MAP` as it does on `STOPPED`/`LINK_DOWN`; `_reason` maps it to `"off_map"`.

- [ ] **Step 1: Write the failing tests** in `tests/route/test_walker_standing.py`. Walker unit tests use `SimClock`, a scripted position source (a list of `(from_ns, result)`) and a recording layer, with the FF2 fixture graph:
  - `test_a_fall_never_attaches` (OFFG-R5 fall guard): a slow fall on FF2 above the top ramp (x 70-72, y 21-23, steps 1 px apart); first assert that every position fails the strict `attach` and passes `attach_standing`; each position is held 3 polls, one is held 10 polls (0.45 s between its first and last read, just under `standing_s`), and one STALE read sits inside that hold; `leg_timeout_s=2`: no play, no `planned`, outcome `timed_out`. It pins that the still timer measures time and not polls, and restarts on an exact position change of 1 px (the 5 recorded mid-air reads would snap loosely: only this timer keeps them off).
  - `test_still_half_second_attaches_and_moves`: (70, 22) read every poll: the first `MoveEntry.pos == (70, 27)` and the observer's `planned` pos is (70, 27); `MoveEntry.t_ns - first no_plan t_ns` is ≥ 0.5 s and < 0.55 s.
  - `test_a_new_position_restarts_the_timer`: A for 0.4 s then B: no play before 0.5 s after B's first read.
  - `test_a_miss_restarts_and_stale_does_not`.
  - `test_standing_off_the_map_ends_the_run`: (90, 35) still (no line within 6 px, and no strict attach: (90, 40) would attach by A3 to mid-right's end (97, 43)): leg `off_map` 0.5-0.55 s after the first `no_plan`, the exact log line, `run()` gives `Success` with reason `"off_map"`, no play after it, `release_all` called.
  - `test_no_path_is_never_off_map` (Review Focus 2), parametrised over an A1 and an A3 position, both outside every segment: a tmp map CSV of two platforms that no edge joins upward (e.g. a floor at y 60 with nodes (40, 60) and (80, 60), and a platform at y 10, beyond Kanna's vertical reach; the leg's dst on the high platform). The character stands still at (81, 60) (A1: within 2 px of the end node (80, 60)) and at (90, 60) (A3: beyond the end, within reach). Assert first that `attach` is a `Success`, `attach_standing` is a `Failure` and `next_move` fails with `no path` at that position; then the walker waits as today until the leg timeout (outcome `timed_out`), never `off_map`, no log line, no play.
  - `test_each_loose_move_waits_again` (plan review M3): the position stays (70, 22) for 3 s and every move does nothing: consecutive plays are at least `standing_s` apart, measured from the end of each settle.
  - `test_stop_wins_during_the_wait` (Review Focus 4): stop flag set 0.3 s into a still off-graph spell: outcome `stopped`.
  - `test_loose_attach_on_the_target_is_reached` (Review Focus 5): still within 6 px of the dst node's line, at its x: outcome `reached`, no play.
  - Sim scenarios (upgraded sim: `SimWorld`, `SimLink`, `SimPositionSource` with `dot_above_foot_px=3`, `NoiseConfig.off()`, a tmp map CSV of one long platform and its terrain JSON written by the test): `test_sim_bump_is_walked` (the world's ground 5 px below the map's line over part of the platform; a walk ending there reaches the target; Task 5's code times out); `test_sim_knockback_onto_an_unmapped_ledge_stops` (a world ledge 10 px below the platform's end, not in the map; a shove only rises 6 px (`_SIDE_TELEPORT_RISE_PX`), so a ledge above the line cannot be reached; the shove is injected as the first move's follow-up, see the next scenario: `off_map` 0.5-0.55 s after the first `no_plan` of the final still spell, no play after the landing); `test_sim_knockback_through_the_air_sends_nothing` (Review Focus 3: the knockback must **fall**, e.g. off a platform's end onto one more than 10 px below, so the reads change at every poll while it is in the air; a knockback on level ground would pass trivially. Assert the reads did change during the settle, then: no `planned` and no play while the knockback's settle is running). Inject the knockback as a move's follow-up (a wrapper over `world.walk` that calls `world.knockback` at the move's `t_end_ns`, as `SimWorld._shoved` does), so the walker is inside `_settle` for the whole fall (OFFP141-RR N4); a shove injected through a wrapping position source at an arbitrary read can be read at its start, on the graph, and make the walker plan and play at once, which is not what this rule is about.
  - `test_sim_real_ground_rule_alone` (plan review I2b; part 2 on the real ground): `run_sim(fixture map, REAL_GROUND_TERRAIN, 20, 2, noise=REAL_GROUND_NOISE, sim_params=dataclasses.replace(REAL_GROUND_SIM, dot_above_foot_px=6), observer=recorder)`. With the dot 6 px above the foot line, the shift of part 1 leaves the reads 3 px high, exactly what today's walker saw live, so only part 2 can save the slope. Expected: every `judge(report, 0)` check ok (6/6 PASS), at least one loose attach and no `leg_end` with outcome `off_map`. A loose attach is a `planned` event whose pos equals `attach_standing(rg, p)` of the `no_plan` read p before it, with p read in `no_plan` events (a STALE `read_failed` allowed between) for at least `standing_s` (OFFP141-RR N2: an ordinary landing has the same event pattern, but fails both tests). OFFD140's what-if measured "Hong's rule alone, dot 3: PASS, 94 loose attaches" on its own ramp; write the counts you see into the notes, pin only "> 0". Mark it `slow` if it takes 0.5 s or more.
  - In `tests/route/test_offgraph_replay.py`, `test_option_c_replay`: the 1229 polls through `foot_from_dot`, `_Stillness` (reset on `planned`, `reached` and `leg` events and on a missed read, as the walker resets; a STALE read does not reset it, and all 244 `read_failed` events of the fixture are `why: stale`, checked by OFFG-R6 against the 8 source runs' `steps.jsonl`) and `attach` then `attach_standing`: 5 left, 0 loose attaches, 0 off-map stops, longest still spell under 0.5 s (OFFD140's "shift 3 + still" row).
  - In the same file, `test_rule_alone_replay` (plan review I2a; part 2 on Hong's real reads): the same replay **without** `foot_from_dot` (the logged positions as they are). Expected, OFFD140's "still 0.5 s, band 6" row: 1027 polls attached loosely, all to the top platform, offsets 3.2-5.5 px from the line; 202 polls left off-graph, the longest wait 1.3 s; 0 off-map stops; and for every snapped point, `next_move(rg, snapped, dst, rng)` with that leg's dst (its `leg` event) is a `Success`. The replay does not reset after its own loose attaches (no move is replayed): every poll at or after 0.5 s of one position counts as attached (OFFP141-RR N3). If the figures differ from OFFD140's, stop and report; do not edit them.
- [ ] **Step 2: Run** `.venv/Scripts/python.exe -m pytest tests/route/test_walker_standing.py tests/route/test_offgraph_replay.py -q`. Expected: FAIL (`LegOutcome.OFF_MAP`, `_Stillness` missing; the bump scenario times out).
- [ ] **Step 3: Implement** the rules above. Keep the existing observer call sites and their `if self._observer is not None:` shape (the code-shape rule in `src/route/CLAUDE.md`, "The loop").
- [ ] **Step 4: Run** `.venv/Scripts/python.exe -m pytest tests/route -q`. Expected: PASS, the frozen walker tests unchanged. Then `$SMOKE` and `$TARGET` (this is the first task where the still rule could move them; expected: the Global Constraints figures, `knockbacks: 0`) and `$SMOKE --real-ground` (expected: 6/6 PASS). If a baseline moves, stop and report which legs changed.
- [ ] **Step 5: Commit** `git commit -m "feat(route): a still character is standing: attach it within 6 px or end the run (QB213, OFFP141 T6)"`.

### Task 7: The off-map stop through the session, the CLI and the sim report

Class: integration.

**Files:**
- Modify: `src/route/session.py` (stop payload :576-586), `src/route/walk.py` (docstring exit 10, :6-9), `src/route/sim/run.py` (`SimReport`, `judge` laps detail :425-429)
- Test: `tests/route/test_session.py` (add), `tests/route/test_sim_real_ground.py` (add)

**Interfaces:**
- Consumes: `RunSummary.reason == "off_map"` (Task 6).
- Produces: `summary.reason == "off_map"` → `stop.json` `{"reason": "off_map"}`, exit 10, the `ended` event text `"off_map/"`; the walker's own log line is the console line (the session adds no second line; the usual `stopped: off_map` line that every stop prints stays: Hong, 2026-11-07, "Keep both lines"). `SimReport.off_map: int` (legs with that outcome); the laps check detail ends `..., N link down, N off map`. The Route tab is not changed (Hong's OFFP141-D1, QB215, "Console line only": exit 10 keeps "Ended: the character was lost (exit 10).").

- [ ] **Step 1: Write the failing tests:** `test_off_map_run_exits_10` in `test_session.py` (its existing rig with a fake walker whose `run()` returns `Success(RunSummary(0, 3, "off_map"))`: code 10, `stop.json` reason `off_map`, `ended` text `off_map/`); `test_off_map_is_counted` in `test_sim_real_ground.py` (a tiny sim run that ends off the map reports `off_map == 1` and fails `laps`).
- [ ] **Step 2: Run** `.venv/Scripts/python.exe -m pytest tests/route/test_session.py tests/route/test_sim_real_ground.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement**; walk.py's exit-10 text becomes "the character was lost: a leg timed out with no move (B19-D1), or it stood more than 6 px from every platform (QB213)".
- [ ] **Step 4: Run** `.venv/Scripts/python.exe tools/check.py --full` (this task edits `test_session.py`, which has slow tests, and the root CLAUDE.md asks for `--full` before a commit). Expected: all steps pass.
- [ ] **Step 5: Commit** `git commit -m "feat(route): the off-map stop ends a walk with exit 10 and stop.json reason off_map (OFFP141 T7)"`.

### Task 8: Proof runs, spec B, decisions and CLAUDE.md

Class: routine (docs and command runs).

**Files:**
- Modify: `docs/superpowers/specs/2026-10-21-p1b-route-walking-design.md`, `docs/decisions.md` (append), `src/route/CLAUDE.md`, `CLAUDE.md`, `tests/CLAUDE.md`

- [ ] **Step 1: Run the proofs** and copy each output into the notes:
  1. `.venv/Scripts/python.exe -m pytest tests/route/test_offgraph_replay.py -v`: the 1229 logged off-graph polls → 5 left, all mid-air, ≤ 0.21 s (OFFD140).
  2. Before/after on the upgraded sim: Task 3's recorded `$SMOKE --real-ground` FAIL (leg (49, 28) → (101, 24)) against today's `$SMOKE --real-ground`: 6/6 PASS.
  3. `.venv/Scripts/python.exe -m pytest tests/route/test_walker_standing.py -v`: the part-2 scenarios, including part 2 alone on the real ground (`test_sim_real_ground_rule_alone`: 6/6 PASS, its loose-attach count) and, from item 1's file, `test_rule_alone_replay` (1027 loose attaches, 202 left, 0 off-map on Hong's real reads) and `test_shift_keeps_the_good_reads` (the 3 mid-fall reads).
  4. `$SMOKE`: expected 6/6 PASS with 366 moves and 25 no-ops (the dot offset and the shift cancel, and the default sim never holds an off-graph spot 0.5 s). If any count differs, find which legs changed (loose attaches or an off-map stop, from `--out` and its `steps.jsonl`), and record the new figures with the reason in the root `CLAUDE.md` "Running" line and in `src/route/CLAUDE.md`.
  5. `$TARGET`: expected the figures under Global Constraints; if they differ, record them in spec B "As built" as a dated note under the simulator run.
  6. `.venv/Scripts/python.exe tools/check.py --full`: all pass.
- [ ] **Step 2: Spec B.** Insert after "## Constraints and facts" this section, verbatim:

```markdown
## Where the character stands (added 2026-11-03, Hong's QB213, H85)

- The walker reads the character 3 px below the centre of its minimap dot (`DOT_TO_FOOT_PX`, `src/route/position.py`). That is where nodes sit when they are placed in the map editor: the dot's centre is 6 px above the ground, and the editor's node circle (radius 3) rests on it. Step logs, leg logs and replays now hold this point. One live change besides the slope: on Hong's recorded runs, 3 reads taken mid-fall, 50 ms after the walker had settled too early, no longer place the character, so the walker now waits for the landing instead of planning a drop from the air.
- When the walker cannot place the character on the map (attach rules A1-A3 of the Feature B plan's contract C6 all fail) and reads the same position for 0.5 s in a row (`standing_s`), the character is standing. The walker attaches it to the platform under it: the platform segment that spans its x and whose line is nearest, at most 6 px above or below (`standing_band_px`), and plans from that point on the line. "Within 6 px" counts either side of the line, because a dip in the ground puts the character below the mapped line; so a character up to 6 px under a line is attached to the platform above it. A missed read starts the 0.5 s again; a frame with no new picture does not; every move sent starts it again too.
- A character the rules A1-A3 do place, but with no path to the target, is never "off the map": the walker waits as before.
- If no platform line is within 6 px, the walk ends at once with "the character is standing at (x, y), more than 6 px from any platform of the map; ending the run" (exit 10, `stop.json` reason `off_map`) instead of waiting for the leg timeout. (x, y) is the point the walker reads, 3 px below the dot's centre. The Route tab shows its usual exit-10 line, "Ended: the character was lost (exit 10)."; the console box shows the walker's reason line and then the usual `stopped: off_map` line that every stop prints (Hong, QB215; "Keep both lines", 2026-11-07).
- While the position keeps changing (a fall, a knockback through the air) nothing is attached loosely and no key is sent, as before.
```

  In "## Verification", after the "Offline" bullet, add: `- **Offline on the real ground** (2026-11-03, H86): the simulator's \`--real-ground\` run uses the ground of FF2 taken from Hong's recorded runs, the dot 3 px above the node line, sideways teleports as long as the live ones and mob knockback. Before the fix it froze on the top slope as the live runs did; after it, the 20-lap seed-2 run passes 6/6.` In "As built", "Deviations", add one line: `- H84-H86 (2026-11-03): the off-graph fix (the 3 px reading and the still rule above) and the real-ground simulator; plan docs/superpowers/plans/2026-11-03-offgraph-fix.md.`
- [ ] **Step 3: `docs/decisions.md`.** Append H84, H85, H86 (take the next free numbers if H84 is used by then), each in the file's format (heading with date and source, text, "Affects:"), quoting Hong word for word from `C:/Users/Qing_programming/Desktop/helix-rt/.superpowers/sdd/2026-10-31-route-tab/progress.md` (orch 140, lines 802-806):
  - H84 (QB212): the options he was given, "(1) move nodes now + sign-off on today's code, general fix in parallel; (2) general fix first, sign-off on final code", and his answer "2"; plus walk 2 (164604Z): "i had to move it by hand, or a mob knoccked it to the side which eventually fixed it." (walk 2 does not count; the one-lap walk re-runs after the fix, before the 10 laps).
  - H85 (QB213, OFFD140-D1): his answer "Both (Recommended)" = option C, his own idea quoted: "we know the c haracter isnt fallinng if its y position stays stable so isnnt that a simple way of stopping this innfinite fall as opposed to checkingg its close to the ground?"; the three numbers; the frozen files it opens (walker.py, locate.py, params.py; planner.py was named but not needed). In plain words, how "attach to the platform under it within 6 px" is read (Ruling, orch 141): within 6 px on either side of the read, above or below the platform's line, because a dip in the ground puts the read below the mapped line; so a character read up to 6 px under a line is attached to the platform above it. The one live change besides the slope: 3 of the recorded reads, taken mid-fall 50 ms after an early settle, no longer place the character, so the walker waits for the landing instead of planning from the air (pinned in `test_shift_keeps_the_good_reads`).
  - Also under H85 (or as its own entry if the file's format wants one per question), Decision (Hong) OFFP141-D1 (QB215, 2026-11-03, AskUserQuestion), quoted: "Console line only (Recommended)" = option A: new leg outcome and `stop.json` reason `off_map`, exit 10 (the existing "lost" code), one console line "the character is standing at (x, y), more than 6 px from any platform of the map; ending the run"; the Route tab status stays "Ended: the character was lost (exit 10)."
  - H86 (QB214): "Yes, upgrade it (Recommended)": real-ground fixture, dot offset, knockback injector; sim first, then the fix.
- [ ] **Step 4: CLAUDE.md files.** `src/route/CLAUDE.md` "Traps", Frozen bullet: a third exception, the off-graph fix (H85): `walker.py`'s still rule and `off_map` outcome, `locate.attach_standing`, `params.py`'s `standing_s` and `standing_band_px`; `planner.py` untouched. Task 6 moved lines in `walker.py`: re-cite every `walker.py:` line number in `src/route/CLAUDE.md` against the new file (the "The loop" bullet's `walk_leg`/read/`next_move`/`execute` cites, the step-observer bullet's observer sites `leg` :162 through `leg_end` :264, the constructor :136-137 and :152, the Frozen bullet's :136-137, :153, :332-346, and the stop guard's :57-58, :286, :294); `grep -n "walker.py:" src/route/CLAUDE.md` lists them. "Seams": `position.foot_from_dot` is applied by both `MinimapPositionSource` and `SimPositionSource`, so step and leg logs hold foot-reference positions. "Commands and fixtures": the `--real-ground` run and its fixtures. Root `CLAUDE.md` "Running": the real-ground smoke command `python -m src.route.sim.run --laps 20 --seed 2 --min-single-error-legs 0 --real-ground` with its figures from Step 1; the Route paragraph: one sentence on the 3 px reading and the still rule. `tests/CLAUDE.md` Layout: the two new fixtures (`ff2_real_ground.json`, `ff2_live_reads.json`: positions only from Hong's runs, never keys), and the slow count (`python -m pytest --collect-only -m slow -q`; Task 3 adds `slow` tests in `test_sim_real_ground.py`, Task 4 may drop one, Task 6 may add `test_sim_real_ground_rule_alone`), with the per-folder split and file list in "Speed" brought up to date.
- [ ] **Step 5: Commit** `git commit -m "docs: off-graph fix (H84-H86), spec B standing rule, real-ground sim commands (OFFP141 T8)"`, then run `.venv/Scripts/python.exe tools/check.py` once more (expected: pass).

## Self-review notes

- Coverage: sim upgrade (T1-T3), fails before (T3), part 1 (T4), part 2 (T5-T6), the clear stop (T6-T7), spec text, decisions, docs (T8), every proof the brief names (T8 Step 1).
- Names used across tasks: `DOT_TO_FOOT_PX`, `foot_from_dot`, `attach_standing`, `standing_s`, `standing_band_px`, `_Stillness.see/reset`, `LegOutcome.OFF_MAP`, `"off_map"`, `dot_above_foot_px`, `side_teleport_extra_px`, `knockback_air_s`, `knockback_p`, `knockback_px`, `InjectedError.KNOCKBACK`, `SimWorld.knockback`, `REAL_GROUND_*`, `SimReport.knockbacks`, `SimReport.off_map`.
- Plan review OFFP141-R (4 Important, 9 Minor) applied 2026-11-03 (OFFP141-F): I1 Task 6 rules and Review Focus 2; I2 Task 6 `test_rule_alone_replay` and `test_sim_real_ground_rule_alone`; I3 Task 4 `test_shift_keeps_the_good_reads`, Task 1 counts, Task 8 spec/H85; I4 Task 2 knockback timing and curve, Task 6 falling knockback; M1 Task 4 Files; M2 Task 3 slow marks, Task 8 slow count; M3 Task 6 reset on every move; M4 Task 6 log line; M5 "How within 6 px is read" and H85; M6 Task 1 filter, Task 3 assert; M7 Task 6 Step 4, Task 7 Step 4; M8 Task 8 Step 4; M9 Task 3 flag.
- Known limit, not in this plan (review fyi F1): a character hanging still on a rope or ladder reads off-graph and still; within 6 px of a line it is attached and walked every 0.5 s with no effect (repeated walks, no new key kind); beyond 6 px the run ends `off_map`. The bot never climbs, so only a knockback could put it there.
- Local choices made here (reversible, inside the brief): the shift lives in `position.py` so the live and simulated sources share it; the loose attach plans from the snapped point so `planner.py` and `route_preview.py` stay unchanged; the knockback preset values (0.02 per move, 3-8 px, 0.3 s in the air) are simulator knobs, not Hong's.
