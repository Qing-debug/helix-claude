# Phase 1 · Feature B — Walking the route

*Design, 2026-10-21. Part of Phase 1 in `docs/vision.md`; read that first. Depends on Feature A (input).*

## Why

The core of the bot is moving around a map along a route. The route graph exists (`src/controller/path_graph.py`) but the loop that actually moves the character is commented out (`MapleAgent.executePathFinding`) and refers to an old movement API. Phase 1 needs the character to walk its rotation reliably before runes (Feature C) or, later, attacking (Phase 2) can sit on top.

## Goals

1. On **Fossil Foundry 2** (`maps/fossil_foundry_2.csv`, minimap 162×82) with **Kanna**, the bot cycles through the blue nodes in file order, forever, passing through orange nodes as needed, until stopped.
2. It moves with Kanna's abilities and **chooses which to use**:
   - **Teleport**: arrow + `E`, any of the four directions. Measured reach: 19 px horizontal, 23 px vertical on the minimap (`src/classes/classes.py`, user-measured), **not measured on Fossil Foundry 2**. The user's working assumption is that reach in minimap pixels is the same on every map. That's unverified. Reach is measured on FF2 in the first supervised session, which settles it. Until then, 19/23 is used, and reach stays a single number in one place (`classes.py`), so a different measurement is a one-line change. Per-map reach is built only if the measurement shows it's needed.
   - **Walk**: hold an arrow, for short distances.
   - **Drop through a platform**: `Down` + `Alt`, only where the node has `can_fall = TRUE`.
   - Teleport-down vs drop is the bot's choice when both are possible.
3. **The bot works out the terrain from the node layout.** The user will not label slopes, steps, or walk-vs-teleport links, on this map or any other. Where nodes imply a walkable slope or a near-aligned vertical move, the bot must handle it.
4. It recovers from small errors by itself: overshooting, landing slightly off, a move that didn't happen. It re-plans from where the character actually is.
5. Lap time and per-leg outcomes are logged (feeds Phase 2's "good rotation" work and Feature D's session log).

## Known cases this must handle (from the current map)

The current graph only joins nodes that are level within 2 px or stacked within 2 px. On Fossil Foundry 2 two legs of the rotation have no path:

- `(49, 28) → (101, 24)`: the top level steps from y≈28 to y≈24 between x≈66 and x≈79. **User: it's a slope you can walk.**
- `(101, 24) → (105, 42)`: no node below `(101, 24)` within 2 px horizontally; nearest are `(105, 42)` and `(97, 43)`, 4 px to the side. `(97, 43)` has `can_fall = TRUE`.

These are test cases, not things to fix by editing the map.

## Non-goals

- Attacking (Phase 2). The rotation is walked without attacking.
- Optimising for XP/gold. That's Phase 2. Context for later: a good rotation balances mobs killed and items picked up (the pet loots when the character passes over drops) against reliably hitting target XP and gold rates; mobs respawn 7 s after the first one on the map is killed.
- Recovering from being stuck, dying, or disconnecting. In Phase 1 the bot does nothing special; Feature D's logs show what happened.
- Other characters or maps, beyond not hard-coding anything that only fits this one.

## Decided by the user

- Character: Kanna. Map: Fossil Foundry 2. Keys as above.
- Extra user-set configuration may turn out to be needed for smooth movement; that's decided **after** testing shows what's missing, not designed up front.
- The bot is **not run in the game while the user is away**. In-game runs are short, supervised sessions.
- **Passive screen capture is allowed while the game runs and the user is away**: no input of any kind, with Kanna left logged in on Fossil Foundry 2 on the second monitor. It's for real minimap frames, rune-spawn appearance (Feature C), and gameplay negatives (Feature D) (user, 2026-10-21).

## Constraints and facts

- Character position comes from template-matching `assets/chr/chr.png` (+ mask) on the minimap frame (`MapleAgent.getCharacterCoords`).
- Minimap capture: `src/vision/vision.py`. Helix's local `rune_solver` branch has a newer `MinimapCapture` class + single locked `CameraWrapper` (commit `f0d2be4`) that Feature C brings over; this feature should build on that if it lands first.
- Known weakness: on 2026-10-21 the desktop wallpaper produced ~0.9995 corner-template matches. The rectangle checks rejected them, but minimap detection can be fooled by background imagery.
- The frame in `SharedInfo` is stored RGB despite a comment saying BGR.
- All timing comes from Feature A's human-like input layer.

## Verification (first guess — tried early, gaps filled by Claude first)

- **Offline:** a simulated character on this map (teleport reach known; walk/drop/teleport timings and variance estimated) driving the real planner and control loop, so planning, terrain inference, and recovery are tested without the game. Target: many consecutive simulated laps with every leg completed and no stalls.
- **Real minimap frames of Fossil Foundry 2** to test character detection and the sim's realism. `data/recordings/ff2_2026-10-21/` has 307 full-screen frames (character standing still; every 5th frame checked, all show a valid 162×82 minimap). More come from passive capture. Frames with the character moving come from the first supervised session.
- **In-game:** supervised short runs with the user for sign-off.

## Reliability targets

*Set by Claude; the user can override any of them.*

| What | Target |
|---|---|
| Simulated laps | 1,000 consecutive laps on FF2 with every leg completed and 0 stalls (no progress toward the target for >10 s), with noise on: landing error, overshoot, and ≥5% of moves silently not happening. |
| Known cases | Both no-path legs above completed in every simulated lap. |
| Recovery | After an injected error, the bot reaches the leg's target within 3 extra moves in ≥99% of cases. |
| Character detection | On real FF2 frames, the character is found within ±1 px of a hand-checked position in ≥99.5% of frames, and never reported >3 px away. A miss is acceptable; a wrong position isn't. |
| In-game sign-off (supervised, later) | 10 consecutive laps with no manual intervention. |

## Shipping

Only code that really works is merged (user, 2026-10-21). Branch `p1b-route-walking` off `master` after Feature A is merged, and push the branch to `origin`. It **merges to `master` only after the in-game sign-off passes**, which needs the user. Passing the offline targets makes it ready for sign-off, not ready to merge. The FF2 reach measurement happens in the same supervised session.

## Left to the builder

- Path-graph changes (slopes, near-aligned moves, drop edges) and how terrain is inferred from nodes.
- The control loop's form (Helix's earlier design chose strict lock-step with on-screen confirmation; keep, change, or replace it), tolerances, and timeouts.
- The movement simulator.
- Ability choice (walk vs teleport vs drop).

## As built

*Written 2026-10-26 (plan task B22a) at commit `1257222`, before the in-game session. It records what was built, what the offline runs showed and what is not done yet. No requirement above changes.*

### Status

The offline targets are met. The in-game session (plan task B21) has not been run: Hong parked it on 2026-10-26 (decision B22-inv-D1), and that decision is not a measurement of anything. Feature B is **ready for sign-off, not signed off, and not merged**. It stays on `p1b-route-walking` until the in-game sign-off passes (H4; "Shipping" above; decision B22-inv-D2).

### Results against the reliability targets

| Target (table above) | Result | Run |
|---|---|---|
| Simulated laps | met: 1000 of 1000 laps, 7001 of 7001 legs reached, 0 stalls, 0 timeouts, 0 link-down; 1078 of 18660 moves were silent no-ops (5.78%, need 5% or more) | simulator run below |
| Known cases | met: `(49, 28) -> (101, 24)` in 1000 of 1000 laps and `(101, 24) -> (105, 42)` in 1000 of 1000 laps | simulator run |
| Recovery | met: 1805 of 1805 single-error legs within 3 extra moves (need 100 or more legs and 99%); 428 of 428 multi-error legs within 3 extra moves per error | simulator run |
| Character detection | met on a character standing still only: 304 of 304 hits, 0 misses, 0 wrong, hit rate 1.0000 (need 0.995), max error 0.00 px | detection run below |
| In-game sign-off | not run | none |

**Simulator run** (plan task B14's target run, at `1257222`, 2026-10-26; exit 0, about 6 s of wall time on the dev PC, which answers the plan's "1000-lap wall time" question). It replaces the numbers recorded earlier in the build, which came before the planner fix B14-inv-D1.

```
python -m src.route.sim.run --laps 1000 --seed 1 --known-case 49,28:101,24 --known-case 101,24:105,42
laps_requested: 1000
laps_completed: 1000
legs: 7001
legs_reached: 7001
stalls: 0
timeouts: 0
link_down: 0
known_cases: {'(49, 28)->(101, 24)': (1000, 1000), '(101, 24)->(105, 42)': (1000, 1000)}
single_error_legs: 1805
single_error_within_3: 1805
multi_error_legs: 428
multi_error_within_bound: 428
moves: 18660
no_ops: 1078
drops: 994
teleport_downs: 1110
sim_seconds: 14729.5829
mean_lap_s: 14.729582899999999
PASS laps: 1000/1000 laps, 7001/7001 legs reached, 0 timed out, 0 link down
PASS stalls: 0 stalls
PASS known_cases: (49, 28)->(101, 24) 1000/1000, (101, 24)->(105, 42) 1000/1000 reached/attempted (need 1000 each)
PASS recovery: 1805/1805 single-error legs within 3 extra moves (need >= 100 legs and 99%); 428/428 multi-error legs within 3 extra moves per error
PASS noise: 1078/18660 moves were no-ops (5.78%, need >= 5%)
PASS abilities: 994 drops, 1110 teleports down (need both > 0)
```

**Detection run** (plan task B18, 2026-10-26; exit 0):

```
python -m src.route.harness.detection_check check --frames data/recordings/ff2_2026-10-21 --labels data/detection_check/ff2_2026-10-21/labels.json
uncroppable: 3 of 307 frames (scored as nothing found)
distinct positions: 1 found, 1 labelled
frames 304, hits 304, misses 0, wrong 0, hit rate 1.0000 (need 0.995), max error 0.00 px
PASS
```

- The recording has 307 full-screen frames. 3 of them could not be cropped to a minimap; they are skipped and reported (B18-D1), so 304 are scored.
- All 304 show the character at one position (`distinct positions: 1 found, 1 labelled`). The run checks one spot, with the character standing still. It says nothing yet about moving frames (the target above; B21 step 6).
- The labels are the `draft` tool's marks (the character's dot found by its colour, independently of the locator). Hong checked every mark on the contact sheets on 2026-10-26 and found them all right, and they were saved unchanged as `labels.json` (an exact copy of `labels_draft.json`; both are in the gitignored `data/detection_check/ff2_2026-10-21/`). Hong's own remark is that one standing spot makes this test thin.

### Reach and movement values (not measured on FF2)

- **Reach** is 19 px horizontal and 23 px vertical (`src/classes/classes.py:47-48`), Hong's own measurement, not made on Fossil Foundry 2 (H3). It is used unchanged and stays a single value in that one place; no module under `src/route` writes 19 or 23 (`tests/route/test_import_rules.py`). Nothing in the build measured reach on FF2.
- **Movement values are estimates, not measurements.** The planner's `MovementParams` (`src/route/params.py:16-19`): walk 8.0 px/s, teleport 0.55 s, drop base 0.35 s, fall 60 px/s. The simulator's `SimParams` (`src/route/sim/world.py:117-122`): walk 8.0 px/s, teleport settle 0.35 s, drop settle 0.25 s, fall 60 px/s, link latency 0.03 s. The tool that measures them (`measure_moves`, plan task B20) is built and tested offline but has not been run in the game.

### What was built

The code is `src/route/`. Its layout, seams, frozen files, commands and traps are in `src/route/CLAUDE.md`, and the one-paragraph summary is in the root `CLAUDE.md`. Two choices a reader of the spec might not expect:

- Terrain inference sits in its own layer (`RouteGraph.build`) on top of the unchanged `PathGraph` (PB-D1), so the map editor's edge preview does not show the slope and near-vertical links the bot uses.
- When teleport-down and drop are both possible and cost within 30% of each other, the planner picks one at random (PB-D3, `ability_mix_band` in `src/route/params.py`). Both abilities are used on FF2: the simulator run above has 994 drops and 1110 teleports down.

### Deviations from the spec and the plan

The plan's own decisions PB-D1 to PB-D4, made provisionally in the plan and built as stated (they have no entry in `docs/decisions.md`): PB-D1 and PB-D3 above; PB-D2, F12 is the stop key, through a keyboard hook that swallows it; PB-D4, after a stop a guard thread sends `release_all` every 50 ms until the run returns.

The plan was amended after approval for B19, B15, B20 and B21 (the plan's "Amended after approval" line), and for the B22 split (B22-inv-D1). Rulings in `docs/decisions.md`, one line each:

- PB-B19: the walk has exit codes for a failed or crashed run, and the live link-check script refuses to run while MapleStory is running.
- B6-D1: `attach` skips a platform segment whose two ends share an x.
- B11-D1, B11-D2: the leg log ends a torn last line before it appends, and writes strict JSON (NaN and infinity refused).
- B12-D1, B12-fix-D1: `MoveEntry.t_ns` is the clock reading right after the position read (the layer's `stimulus_ns`); a leg's `misses` counts planning reads only.
- B14-inv-D1: the planner is fixed so an up/down move is made only from where the character can really make it (a livelock on a platform split in two, found by the simulator with node offsets).
- B15-D1: the character locator scrubs a match score only above 1 + 1e-3.
- B17-D1: the minimap recorder refuses a folder that already holds a recording, exits 1 on a failed or empty recording, and takes `--out` only inside `data/`.
- B18-D1: a full-screen frame that cannot be cropped is skipped, reported, drafted as `null` and scored as nothing found.
- B19-D1, B19-D2, B19-D3: the walk ends with exit 10 when a leg times out with no move (the character is lost); it refuses an `--out` that already holds a run, or names a file, and `--laps` below 1, each with exit 2.
- B20-D1 to B20-D4: the measurement tool writes directions as `Key` names and leaves no-op moves out of reach and walk speed; settles a move on one position held for 0.5 s; keeps its built CLI and file surface; and its vertical reach is a lower bound that B21 checks on hand-teleport frames before changing Kanna's 23.
- Shared code changed during the build, which the walk uses: H16 and H19 (the map loader refuses a file that does not match the format, or has a header and no nodes); H17 (`PathGraph` up/down links skip a node Kanna can't teleport to; `RouteGraph.build` copies every `PathGraph` edge; the entry states that FF2 builds the same 40 edges as before); H20 (the main window's draw-node buttons use route's `CharacterLocator`). H18 and H21 were made in the same round and do not touch Feature B.

### Not done yet

- **The in-game session, plan task B21, steps 2 to 8** (parked by Hong, 2026-10-26): a moving recording (step 2), `measure_moves` for reach and movement (step 3), the reach decision (step 4), movement values against the simulator's (step 5), detection on the moving frames (step 6), short supervised walks including one F12 press mid-leg (step 7), and the sign-off (step 8). Nothing from these steps is recorded here as a result.
- **Reach on FF2** (spec "Goals" 2, H3): 19/23 is still Hong's own measurement, not made on FF2.
- **Movement values**: still the estimates above.
- **Detection on moving frames**: only one standing position is checked. A moving recording is queued for Hong's next game session.
- **The in-game sign-off** (10 consecutive laps with no manual intervention, read from `laps.jsonl` with `all_reached` true, and Hong's word): not run, so Feature B is not mergeable (H4).
- **Plan "Not verified at planning time" items still open:** 1 (movement values); 2 (teleport semantics: that a horizontal teleport always travels the full reach, that teleport UP lands on the first platform at or below the end point, that teleport DOWN passes through the current platform; `SimWorld` assumes them); 3 (the platform extents in `tests/fixtures/ff2_terrain.json`, for example mid-left drawn to x 80); 4 (vertical reach on FF2 may not be measurable by landing; then 23 stays and B21 says so); 6 (how often the capture thread delivers no new frame between two 50 ms polls; no run counted it); 8 (whether F12 is bound in MapleStory); 10 (detection on moving frames). Item 5 (detection method) is answered for standing frames by the detection run above, and item 7 (1000-lap wall time) by the simulator run above (about 6 s). Item 9 is a note for Feature D.
- **The F12 stop was checked against the "Helix input test sink" window, not MapleStory.** The B19 link check on 2026-10-26 (real board link and `StopControl`, run folder `data/route/linkcheck/20261026T001306Z`): `stop.json` has reason `stopped` and trigger source `hotkey`; the last leg's outcome is `stopped`, 44.9 ms after the trigger.
- **Ctrl+C at a prompt of `measure_moves` on a real console, and its 0.3 s typed-ahead Enter drop,** have only run against pipes and fakes; Hong tries both once in B21 (B20-D3).
- **Owed to Hong before B21 runs:** B21 step 2 now includes at least 5 hand teleports UP into open space (B20-D4).
- **B22b** (plan task B22, after B21) replaces this list with B21's outputs.
