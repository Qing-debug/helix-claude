# PB plan-review ladder results

Plan-review ladder (handoff Appendix C) run on the approved plan PB
(`docs/superpowers/plans/2026-10-23-p1b-route-walking.md`, approved in `2b12db4`). It measures which reviewer
settings catch known defects planted in a plan. It judges quality only; token figures are FYI.

## Setup

- **Clean tree:** `git worktree add --detach /tmp/pb-ladder/tree 2413c64` (the repo without the plan). Reviewers and
  scorers read the spec and code from it. The worktree was removed afterwards.
- **Seeding:** each piece got 1 Critical, 2 Important and 2 minor defects (5 per piece, 15 in all), planted inside
  its line range only. Each seeded file was checked with `diff` against the approved plan. No seeding shifted any
  lines, so seeded and original line numbers are the same. The seeded files stayed in `/tmp/` and are not in the
  repo.
- **Reviewers:** for each piece, all four settings got the identical brief (the rules block plus the plan-review
  template), pointed at the seeded file and the piece's range. They could read only the seeded file, spec B and the
  clean tree.
- **Scorers:** one per piece, `opus`, effort `medium`. Each scored all four reviews against the piece's key.

| Piece | Kind | Tasks | Lines (plan and seeded) |
|---|---|---|---|
| 1 | control loop / recovery | B12 Walker — one leg; B13 Walker — rotation, laps, stop | 892-977 |
| 2 | path graph / terrain / ability choice | B5 `RouteGraph`; B6 Localisation (`attach`); B7 Planner and ability choice (`next_move`) | 632-712 |
| 3 | real content (runtime wiring) | B19 Runtime wiring and the walk CLI | 1188-1272 |

### Models

Every subagent was the built-in `general-purpose` type, spawned with an explicit `model` and `effort`.

- **Model:** the model each one reported (the first line of its notes file) matched the model requested:
  `opus` → Opus 5.5 (`claude-opus-5-5`), `sonnet` → Sonnet 5.5 (`claude-sonnet-5-5`).
- **Effort:** no subagent's system prompt shows the effort level, so every effort is "requested, not shown". The
  piece 1 and piece 3 Sonnet reviewers also saw a numeric `reasoning_effort` of 30 in their prompt; it is recorded
  here as reported, not interpreted.

| Id | Purpose | Requested | Reported |
|---|---|---|---|
| PB-seed-1/2/3 | seed pieces 1/2/3 | opus, high | Opus 5.5 `claude-opus-5-5`, effort not shown |
| PB-lad-p{1,2,3}-sonnet-xhigh | review | sonnet, xhigh | Sonnet 5.5 `claude-sonnet-5-5`, effort not shown (p1 and p3 saw a numeric `reasoning_effort` of 30) |
| PB-lad-p{1,2,3}-opus-medium | review | opus, medium | Opus 5.5 `claude-opus-5-5`, effort not shown |
| PB-lad-p{1,2,3}-opus-high | review | opus, high | Opus 5.5 `claude-opus-5-5`, effort not shown |
| PB-lad-p{1,2,3}-opus-xhigh | review | opus, xhigh | Opus 5.5 `claude-opus-5-5`, effort not shown |
| PB-score-1/2/3 | score pieces 1/2/3 | opus, medium | Opus 5.5 `claude-opus-5-5`, effort not shown |

No agent handed off and no setting was skipped.

### How to read the tables

- **Planted caught:** a review counts as catching a planted defect when one of its findings is at the same place and
  names the same problem. Caught counts are given as C · I · m, each out of the number planted.
- **Severity matched:** how many of the caught defects the reviewer rated at the same severity as the key.
- **Real, not planted:** findings that are not in the key but that the scorer checked and judged to be genuine. The
  scorer chose their severities.
- **False alarm:** a finding the scorer checked and found to be no defect.

## Totals over the 3 pieces (15 planted: 3 C, 6 I, 6 m)

| Reviewer | Planted caught C · I · m | Caught total | Severity matched | Real, not planted (C/I/m) | False alarms |
|---|---|---|---|---|---|
| sonnet xhigh | 3/3 · 6/6 · 4/6 | 13/15 | 11 of 13 | 24 (0/4/20) | 4 |
| opus medium | 2/3 · 6/6 · 4/6 | 12/15 | 11 of 12 | 17 (0/3/14) | 0 |
| opus high | 3/3 · 6/6 · 6/6 | 15/15 | 13 of 15 | 12 (0/2/10) | 2 |
| opus xhigh | 3/3 · 6/6 · 6/6 | 15/15 | 11 of 15 | 14 (0/1/13) | 0 |

What the numbers show:

- **Coverage:** all four settings caught every planted Important defect. Only `opus high` and `opus xhigh` caught
  all 15 planted defects. `opus medium` missed one Critical: piece 3's swapped `MinimapPositionSource` arguments.
  `sonnet xhigh` and `opus medium` each missed two planted minors.
- **Signal versus noise:** `opus xhigh` had the cleanest record: every planted defect and no false alarms. Its
  severity calls were the least aligned with the key, with 4 mismatches. `sonnet xhigh` raised the most real
  findings that were not planted, but also the most false alarms.
- **Small sample:** there are 15 planted defects over 3 pieces, so one defect moves a rate by about 7 points.

## Per piece

### Piece 1 (B12-B13, lines 892-977)

Scorer PB-score-1's output, verbatim:

#### Piece 1 scores (B12-B13, seeded lines 892-977) — PB-score-1

Planted: Critical 1 (P1-D1), Important 2 (P1-D2, P1-D3), minor 2 (P1-D4, P1-D5).
Caught columns are C · I · m.

#### 1. Summary

| reviewer | planted caught C · I · m | severity matched (of caught) | real-not-planted (C/I/m) | false alarms |
|---|---|---|---|---|
| sonnet-xhigh | 1/1 · 2/2 · 1/2 | 3 of 4 | 8 (0/2/6) | 0 |
| opus-medium | 1/1 · 2/2 · 2/2 | 5 of 5 | 7 (0/1/6) | 0 |
| opus-high | 1/1 · 2/2 · 2/2 | 4 of 5 | 4 (0/0/4) | 1 |
| opus-xhigh | 1/1 · 2/2 · 2/2 | 4 of 5 | 4 (0/0/4) | 0 |

#### 2. Planted defects per reviewer

sonnet-xhigh
- P1-D1 (C, :908 progress never resets last_progress): caught, finding 2, reviewer severity Important (mismatch)
- P1-D2 (I, :924 leg_timeout_s=35 gives 3 stalls): caught, finding 1, Important (match)
- P1-D3 (I, :967-968 test 3 cannot fail without the guard): caught, finding 3, Important (match)
- P1-D4 (m, :952 PB-D3 cite): caught, finding 6, minor (match)
- P1-D5 (m, :976 feat(walker) scope): missed

opus-medium
- P1-D1: caught, finding 1, Critical (match)
- P1-D2: caught, finding 2, Important (match)
- P1-D3: caught, finding 3, Important (match)
- P1-D4: caught, finding 5, minor (match)
- P1-D5: caught, finding 12, minor (match)

opus-high
- P1-D1: caught, finding 1, Critical (match)
- P1-D2: caught, finding 2, Critical (mismatch, planted Important)
- P1-D3: caught, finding 3, Important (match)
- P1-D4: caught, finding 4, minor (match)
- P1-D5: caught, finding 5, minor (match)

opus-xhigh
- P1-D1: caught, finding 1, Critical (match)
- P1-D2: caught, finding 2, Critical (mismatch, planted Important)
- P1-D3: caught, finding 3, Important (match)
- P1-D4: caught, finding 4, minor (match)
- P1-D5: caught, finding 5, minor (match)

#### 3. Real-not-planted and false-alarm findings

Real, not planted
- sonnet-xhigh #4 (I) all_reached False / TIMED_OUT-moves-on / forever untested: confirmed; only B13 test 1 asserts lap records (all True, :956-958), no test yields TIMED_OUT inside run, and B21 sign-off reads all_reached (:1331-1332).
- sonnet-xhigh #5 (I) stop flag not created in B12; pre-execute re-check untested: confirmed; B12 interface (:898) never names the flag, C10 defers it to B13 (:466), and B13 tests 2/3 stop during play / reaction wait, after the :909 re-check, so PB-D4 option A's first half (:1469) has no failing test.
- sonnet-xhigh #7 (m) B12 test 7 cannot tell when the stimulus was taken: confirmed; SimClock advances only on sleep/advance (:835-838), so iteration-start and post-read now_ns() are equal.
- sonnet-xhigh #8 (m) step 5/8 under-specified, off_graph and settle give-up untested: confirmed for t_ns, pair rule, settle-timeout start and untested branches; part (e) is weak since RouteGraph carries params (:283).
- sonnet-xhigh #9 (m) B12 setup recipe incomplete (InputLayer/TimingModel seed, rotation, miss_p, sink): confirmed against :916-919 and the header's "complete on its own" (:7).
- sonnet-xhigh #10 (m) RealClock epoch untested and masked by test 3's patched _sleep: confirmed; input_layer.py:79-81 uses _clock() - stimulus_ns, and no test checks RealClock against perf_counter_ns.
- sonnet-xhigh #11 (m) small B13 statements with no criterion (print once, stop_now twice, summary counts on other paths, 1.0 s reference): confirmed, none asserted in :955-974.
- sonnet-xhigh #12 (m) one-node rotation busy loop; wall-clock flake: confirmed by reading; a rotation[0]->rotation[0] leg returns REACHED with no sleep, so run() spins with max_laps None; not FF2.
- opus-medium #4 (I) TIMED_OUT/all_reached False/release_all count/print-once untested: confirmed, same as sonnet #4.
- opus-medium #6 (m) MoveEntry.t_ns and kind never defined: confirmed, C9 :393-396 gives only types.
- opus-medium #7 (m) B12 never says __init__ creates the stop Event: confirmed (:898, :466).
- opus-medium #8 (m) test 7's fake layer never moves and no leg_timeout_s, so the leg never ends; test 5 names no leg: confirmed for test 7 (no terminating condition in :929-930).
- opus-medium #9 (m) rotation argument unnamed; SimPositionSource needs a SimClock while tests 2-3 use RealClock: confirmed (:848, :959-961).
- opus-medium #10 (m) "every return path" ambiguous for the empty-rotation Failure: confirmed, :941 vs :946.
- opus-medium #11 (m) LapRecord start_ns/end_ns undefined: confirmed, C9 :404 and B13 :943 never define them.
- opus-high #6 (m) B12 test 7 never terminates: confirmed, as opus-medium #8.
- opus-high #8 (m) release_all "exactly once more" unasserted on max_laps/link_down; empty-rotation ambiguity: confirmed.
- opus-high #9 (m) SimPositionSource needs SimClock under RealClock; miss_p unstated: confirmed (:848, :916-919).
- opus-high #10 (m) stop flag created only in B13: confirmed (:898, :466).
- opus-xhigh #6 (m) B12 setup gaps (layer seed, sink, rotation, miss_p) and test 5 leg unnamed: confirmed for the setup gaps; the claim that test 5's result depends on the leg is overstated (any rotation leg reaches with noise off).
- opus-xhigh #7 (m) MoveEntry t_ns/pos/kind/cost_to_go_s sources undefined: confirmed, as opus-medium #6.
- opus-xhigh #8 (m) a next_move "no path" Failure (C7 P3, :356) is also counted as off_graph; off_graph and settle give-up untested: confirmed, step 5 (:906) counts every Failure.
- opus-xhigh #9 (m) empty-rotation release_all ambiguity; print-once unasserted: confirmed (:941, :944, :974).
- Not counted separately: opus-xhigh #3 also flags the untested pre-execute stop re-check (as sonnet #5 does); it sits inside the finding that catches P1-D3, so it counts once.

False alarms
- opus-high #7 (m) B12 tests 5 and 6 name no leg: no defect; test 6's scripted source returns the target for any leg, and test 5's ACK_TIMEOUT-then-REACHED and LINK_LOST-after-1-entry hold for any rotation leg from its source with noise off (B12 test 3 shows all of them reach).

### Piece 2 (B5-B7, lines 632-712)

Scorer PB-score-2's output, verbatim:

#### Piece 2 scores (Tasks B5-B7, seeded lines 632-712)

Planted: Critical 1 (P2-D1), Important 2 (P2-D2, P2-D3), minor 2 (P2-D4, P2-D5). Scorer: PB-score-2.

#### 1. Table

| reviewer | planted caught C · I · m | severity matched (n of caught) | real-not-planted (C/I/m) | false alarms |
|---|---|---|---|---|
| sonnet-xhigh | 1/1 · 2/2 · 1/2 | 3 of 4 | 5 (0/0/5) | 2 |
| opus-medium  | 1/1 · 2/2 · 1/2 | 3 of 4 | 3 (0/0/3) | 0 |
| opus-high    | 1/1 · 2/2 · 2/2 | 4 of 5 | 2 (0/0/2) | 1 |
| opus-xhigh   | 1/1 · 2/2 · 2/2 | 2 of 5 | 3 (0/0/3) | 0 |

#### 2. Planted defects per reviewer

sonnet-xhigh
- P2-D1 (C, :640 build(…, Kanna(), …)): caught yes (#1), reviewer Critical (match)
- P2-D2 (I, :647-648 weakened known case 2): caught yes (#6, fix asserts cost_to((101,24),(105,42)) == 1.05), reviewer Important (match)
- P2-D3 (I, :694 d=13 → WALK 1625): caught yes (#2), reviewer Critical (mismatch)
- P2-D4 (m, :680 DC5 for DC6): caught no
- P2-D5 (m, :669 A2 for A1): caught yes (#7), reviewer minor (match)

opus-medium
- P2-D1: caught yes (#1), Critical (match)
- P2-D2: caught yes (#3), Important (match)
- P2-D3: caught yes (#2), Critical (mismatch)
- P2-D4: caught no
- P2-D5: caught yes (#4), minor (match)

opus-high
- P2-D1: caught yes (#1), Critical (match)
- P2-D2: caught yes (#3), Important (match)
- P2-D3: caught yes (#2), Critical (mismatch)
- P2-D4: caught yes (#4), minor (match)
- P2-D5: caught yes (#6), minor (match)

opus-xhigh
- P2-D1: caught yes (#1), Critical (match)
- P2-D2: caught yes (#5; fix pins path, DOWN kind, cost 1.05), minor (mismatch; reviewer says target not uncovered because B7.2 checks 1.05)
- P2-D3: caught yes (#2), Critical (mismatch)
- P2-D4: caught yes (#6), minor (match)
- P2-D5: caught yes (#3), Important (mismatch; argues a writer following "A2" asserts wrong left/right, but the expected value states node=, so minor)

#### 3. Real-not-planted and false-alarm findings

Real, not planted (all minor by the rubric):
- sonnet-xhigh #3 "(105,46) → not arrived" is really Failure (rated I; scored m): confirmed by C6 — A1 |dy| 4 > 2, A2 |46−42| = 4 > attach_y 3, A3 no first node right of 105 and last node (74,45) is 31 > 19 px away, so A4 Failure and P2 returns it (plan:316-324, 352); the expectation is vague wording, not a wrong value.
- sonnet-xhigh #4 no 19/23-token test in B6/B7 (rated I; scored m): hard rule 4 (plan:52-54) covers all of src/route/ but only B2/B5 test it; locate.py/planner.py use reach_h (plan:321, 362); rule still binds, so a test-hardening gap.
- sonnet-xhigh #5 Key.LEFT never exercised in B7 (rated I; scored m): confirmed, B7 tests 1-10 give only RIGHT/UP/DOWN; P4's LEFT branch (plan:360) untested until B14.
- sonnet-xhigh #8 C5/C7 Failure branches untested (m): confirmed, FF2 is strongly connected, so no B5/B7 case reaches C5 "no path" (plan:301) or C7 P3 "no path from" (plan:356); hard rule 3 relies on these.
- sonnet-xhigh #10 wording bundle (m): `p` undefined at :651; params changes (:687, :698, :701) need a rebuilt rg since next_move has no params arg (plan:344-345, 283).
- opus-medium #5 max_walk_ms=1000 needs its own rg (m): same as above, confirmed (plan:685, 344-345).
- opus-medium #6 (105,46) expected result unstated (m): confirmed as above.
- opus-medium #7 no LEFT case in B7 (m): confirmed as above.
- opus-high #5 "B5 kinds" should be "C5 kinds" (m): confirmed, kind rule is C5 (plan:298), B5 cites itself.
- opus-high #8 (105,46) → Failure, not "not arrived" (m): confirmed as above.
- opus-xhigh #4 "B5 kinds" → C5 kinds (m): confirmed (plan:298).
- opus-xhigh #7 band/max_walk_ms params location unstated (m): confirmed (plan:344-345, 685).
- opus-xhigh #8 (105,46) → Failure "off-graph" (m): confirmed as above.

False alarms:
- sonnet-xhigh #9 C6 tie-breaks / A3 height gate untested: extra-test suggestion; no stated target or claimed coverage is left uncovered and no text is wrong.
- sonnet-xhigh #11 B6 class rationale stale: the reviewer concedes O is allowed (borderline goes higher); "design choices in how an off-node position joins the graph" still describes C6's tie-break choices; no defect.
- opus-high #7 B7 test 7 (86,37) has no target: (86,37) is at no node (P1 fails for any target) and P2 returns attach's Failure before the target matters (plan:351-352), so any target gives the asserted result.

Notes: sonnet-xhigh #6 also names "B5 kinds" and missing SLOPE/UP costs; merged into its P2-D2 catch (one finding, counted once).

### Piece 3 (B19, lines 1188-1272)

Scorer PB-score-3's output, verbatim:

#### Piece 3 scores (Task B19, PB-seeded.md:1188-1272)

Planted: Critical 1 (P3-D1) · Important 2 (P3-D2, P3-D3) · minor 2 (P3-D4, P3-D5).
Scorer: PB-score-3 (Opus 5.5, claude-opus-5-5). Severity of real-not-planted findings is my judgment (rubric in the brief).

#### 1. Table

| reviewer | planted caught C · I · m | severity matched | real-not-planted (C/I/m) | false alarms |
|---|---|---|---|---|
| sonnet-xhigh | 1/1 · 2/2 · 2/2 | 5 of 5 | 11 (0/2/9) | 2 |
| opus-medium  | 0/1 · 2/2 · 1/2 | 3 of 3 | 7 (0/2/5) | 0 |
| opus-high    | 1/1 · 2/2 · 2/2 | 5 of 5 | 6 (0/2/4) | 0 |
| opus-xhigh   | 1/1 · 2/2 · 2/2 | 5 of 5 | 7 (0/1/6) | 0 |

#### 2. Planted defects per reviewer

sonnet-xhigh
- P3-D1 (C, L1222 swapped MinimapPositionSource args): caught, reviewer Critical (#1)
- P3-D2 (I, connect before capture): caught, reviewer Important (#3)
- P3-D3 (I, Cloud yes): caught, reviewer Important (#2)
- P3-D4 (m, PB-D4 cite): caught, reviewer minor (#11)
- P3-D5 (m, "3 s" text): caught, reviewer minor (#10)

opus-medium
- P3-D1: missed (its "Confirmed correct" paragraph does not check the B16 signature)
- P3-D2: caught, reviewer Important (#2; #3 "layer.release_all with no layer" is a consequence of the same order, merged)
- P3-D3: caught, reviewer Important (#1)
- P3-D4: missed
- P3-D5: caught, reviewer minor (#8)

opus-high
- P3-D1: caught, reviewer Critical (#1)
- P3-D2: caught, reviewer Important (#3)
- P3-D3: caught, reviewer Important (#2)
- P3-D4: caught, reviewer minor (#6)
- P3-D5: caught, reviewer minor (#5)

opus-xhigh
- P3-D1: caught, reviewer Critical (#1)
- P3-D2: caught, reviewer Important (#2)
- P3-D3: caught, reviewer Important (#3)
- P3-D4: caught, reviewer minor (#4)
- P3-D5: caught, reviewer minor (#5)

#### 3. Real-not-planted and false-alarm findings

Real, not planted
- sonnet-xhigh #4 `_make_positions` returns a bare PositionSource yet "exit 5 on Failure" (m): L1221-1222; the seam's type has no way to carry the Failure, and the test patch returns a bare source.
- sonnet-xhigh #5 `JsonlLegLog(out)` not `.open` (m): C9 L413-416 has both; only `open` is tested to create the dir/return Failure (B11 test 2/4, L883-886); B14 L1011 also uses the constructor, so it is a gap, not a break.
- sonnet-xhigh #6 source of `shared` and capture-thread daemon unstated (m): B19 is silent; B17 L1135 states both; `src/main.py:1-4` imports vision/gui, so it cannot be imported under rule 9.
- sonnet-xhigh #7 exit/failure paths undefined (stopped exit code, run() Failure, start() Failure, seed default) (m): L1215-1216 has no code for reason "stopped" or run() Failure (C10 L464 returns Result).
- sonnet-xhigh #8 stated behaviours untested (exit 1/3/5, timer, sigint, close, stop.json after a stop) (I): Tests L1239-1250 cover only exits 0/2/4 and trigger; those behaviours have nothing that can fail.
- sonnet-xhigh #9 `--record` underspecified (I): B17 `record` (L1125-1126) needs `seconds`/`fps`/`frames` and has no stop; B19 L1236 gives none, B21 L1327 uses `--laps 10 --record`.
- sonnet-xhigh #12 G33 cite one line off (m, outside the piece at L123): hook proc is `listener.py:264`, unhook at 285, line 286 blank; confirmed.
- sonnet-xhigh #13 non-reentrant Lock in trigger() under a nested SIGINT (m): L1204 says threading.Lock; a second Python signal handler can run on the main thread inside the first; edge case, inferred.
- sonnet-xhigh #14 close()/start()/timer lifecycle unspecified (SIGINT handler not restored, timer not cancelled) (m): L1208 `close()` has no stated effect.
- sonnet-xhigh #15 seed default, git HEAD failure and stamp format loose (m): L1213-1214, L1229-1230 leave them open.
- sonnet-xhigh #16 live-check details (SimPositionSource needs a SimClock while `_make_clock` is RealClock) (m): B10 L848 takes a SimClock; L1259-1260 swaps the clock but keeps "as in the end-to-end test"; the "LEFT and E only" claim I did not verify.
- opus-medium #4 exit code / stop.json undefined for "stopped" and run() Failure (m): as sonnet #7.
- opus-medium #5 `--record` cannot be wired as specified (I): as sonnet #9.
- opus-medium #6 untested behaviours plus close() undefined (I): as sonnet #8 (close() part merged here).
- opus-medium #7 live link check has no MapleStory-process guard (m): Feature A enforces it in code (`soak.py:518-526`); B19 relies on the heading only; hard rule 2 (L45-47) is procedural, so a safety gap, not a break.
- opus-medium #9 sentence fragment at L1233 (m): "On F12 or Ctrl+C; `--minutes` arms..." has no verb; confirmed.
- opus-medium #10 unstated wiring (rotation, RouteGraph.build, rng, params) and `JsonlLegLog(out)` (m): as sonnet #5 plus open wiring details.
- opus-medium #11 SimPositionSource args (miss_p, rng) missing in the e2e test (m): B10 L848 has no defaults for them; confirmed.
- opus-high #4 untested exits/timer, plus `_make_positions` cannot return a Failure, plus "stopped" exit code (I): as sonnet #8/#4/#7, one finding.
- opus-high #7 sentence fragment at L1233 (m): confirmed.
- opus-high #8 `JsonlLegLog(out)` and unstated seeding/params (m): as sonnet #5/#15.
- opus-high #9 `--record` underspecified (I; reviewer rated minor): as sonnet #9.
- opus-high #10 SimPositionSource args missing (m): B10 L848; confirmed.
- opus-high fyi `cv2.imshow` in the capture thread (m): `vision.py:83-84` confirmed; walk would open a debug window in game; outside B19's lines.
- opus-xhigh #6 sentence fragment at L1233 and timer start unstated (m): confirmed.
- opus-xhigh #7 `JsonlLegLog(out)` not `.open` (m): as sonnet #5.
- opus-xhigh #8 `--record` underspecified (I; reviewer rated minor): as sonnet #9.
- opus-xhigh #9 start_capture/wait_first_frame have no behaviour or test; `shared` source (m): L1209-1210 signatures only; as sonnet #6.
- opus-xhigh #10 SIGINT handler not restored; non-reentrant lock (m): as sonnet #13/#14.
- opus-xhigh #11 exit codes for "stopped" and run() Failure (m): as sonnet #7.
- opus-xhigh #12 live check lacks MapleStory guard (m): as opus-medium #7.

False alarms
- sonnet-xhigh #17 Ctrl+C may wait for the move in flight on Windows: inferred, not run; the plan sets no Ctrl+C latency target (the 1 s bound is on F12, L1265, and B21 tests F12), so no plan defect.
- sonnet-xhigh #18 relative asset and map paths: `python -m src.route.walk` only runs from the repo root (imports rooted at `src.`, hard rule 9 "from the repo root"), and `--map maps/...` is relative by design; no defect.

## Skipped settings

None. All four reviewer settings ran on all three pieces.

## Token figures (FYI only, total subagent tokens as the harness reported them)

| Agent | Piece 1 | Piece 2 | Piece 3 |
|---|---|---|---|
| seeder (opus high) | 89,867 | 87,130 | 96,979 |
| sonnet xhigh | 245,684 | 202,468 | 218,808 |
| opus medium | 128,633 | 105,329 | 103,065 |
| opus high | 153,107 | 122,869 | 124,157 |
| opus xhigh | 166,444 | 149,623 | 142,499 |
| scorer (opus medium) | 105,464 | 93,851 | 102,186 |

## Answer keys (appended verbatim after scoring)

<!-- key, piece 1: /tmp/pb-ladder/keys/piece-1-answers.md, verbatim -->

# Piece 1 answer key (PB-seed-1)

Seeded file: /tmp/pb-ladder/piece-1/PB-seeded.md
Original: /home/user/helix-claude/docs/superpowers/plans/2026-10-23-p1b-route-walking.md (unchanged)
Piece range after seeding: lines 892-977 (line count unchanged; total file 1488 lines in both).
`diff` original vs seeded: changes only at 908, 924, 952, 967-968, 976.

Counts: Critical 1, Important 2, minor 2.

## P1-D1 — Critical — line 908 (B12 loop step 6)

- Original: `  6. If \`cost_to_go_s < best − progress_eps_s\` → \`best = cost_to_go_s\`, \`last_progress = now\`.`
- Seeded:   `  6. If \`cost_to_go_s < best − progress_eps_s\` → \`best = cost_to_go_s\`.`
- Why: `last_progress` is now only set at loop start (line 900) and at each stall (line 903), so step 3 counts a
  stall every `stall_s` seconds of leg time regardless of progress. The stall metric becomes a 10 s timer, not "no
  progress toward the target for >10 s" as spec B defines it
  (/tmp/pb-ladder/tree/docs/superpowers/specs/2026-10-21-p1b-route-walking-design.md:64) and as the plan's DC5
  states ("stall = no new best cost-to-go for > 10 s", plan:155; `progress_eps_s` comment, plan:217). The code
  fails the spec's Simulated-laps target definition (0 stalls), which B14 judges with `leg_timeout_s=60` (plan:1011):
  any slow but progressing leg counts as stalled. B12's tests do not catch it (test 3's legs are under 10 s; test 4's
  link never moves, so it passes either way).

## P1-D2 — Important — line 924 (B12 test 4)

- Original: `... leg \`(82,60)\`→\`(48,61)\` with \`leg_timeout_s=25\` → \`TIMED_OUT\`, \`stalls`
- Seeded:   `... leg \`(82,60)\`→\`(48,61)\` with \`leg_timeout_s=35\` → \`TIMED_OUT\`, \`stalls`
- Why: with `stall_s = 10.0` (plan:218) and a link that never moves, a 35 s leg crosses 10 s, 20 s and 30 s, so
  `stalls == 3`, but the test still asserts `stalls == 2 (10 s and 20 s windows)` (plan:924-925). The test as
  written fails against a correct implementation, forcing rework of either the test or the code.

## P1-D3 — Important — lines 967-968 (B13 test 3)

- Original: `     and if a \`play\` started after \`stop_now()\`, a \`release_all\` follows it within 0.1 s wall time. After \`run\``
            `     returns, no further \`release_all\` calls happen (the guard has stopped).`
- Seeded:   `     and the last leg record is \`STOPPED\`. After \`run\` returns, no further \`release_all\` calls happen (the guard`
            `     has stopped).`
- Why: the dropped clause was the only failing-able check that the stop guard cuts a `play` that starts after the
  stop. PB-D4 option A (plan:1469-1471) costs "one race test (B13 test 3)" and B13 claims the guard cuts it "within
  50 ms of the `play` starting" (plan:953-954). With the clause gone, test 3 passes with no guard at all (the fake
  link's `play` does not block, so `run` returns `"stopped"` on the next stop check), leaving PB-D4's guarantee
  untested.

## P1-D4 — minor — line 952 (B13 Behaviour)

- Original: `  (wall time, \`time.sleep\`) until "run finished" is set (PB-D4). ...`
- Seeded:   `  (wall time, \`time.sleep\`) until "run finished" is set (PB-D3). ...`
- Why: stale/wrong cite. The stop guard is decision PB-D4 (plan:1462-1475, "Continued: plan uses A (C10, B12 step 7,
  B13)"); PB-D3 is the teleport-down vs drop mix band (plan:1446-1458).

## P1-D5 — minor — line 976 (B13 Commit)

- Original: `- **Commit:** \`feat(route): cycle the rotation in laps, stoppable from another thread\``
- Seeded:   `- **Commit:** \`feat(walker): cycle the rotation in laps, stoppable from another thread\``
- Why: naming/convention. Every other task in feature B commits with scope `route` (e.g. B12 plan:932, B14
  plan:1052), and the walker lives in `src/route/walker.py` (plan:190); CLAUDE.md asks for conventional commits with
  a scope, and the plan's scope is `route`.

<!-- key, piece 2: /tmp/pb-ladder/keys/piece-2-answers.md, verbatim -->

# Piece 2 answer key (PB-seed-2)

Seeded file: /tmp/pb-ladder/piece-2/PB-seeded.md
Original: /home/user/helix-claude/docs/superpowers/plans/2026-10-23-p1b-route-walking.md
Piece range after seeding: lines 632-712 (unchanged; seeded file is 1488 lines, same as the original). Every
changed line (640, 647-648, 669, 680, 694) lies inside 632-712 (verified with `diff`).

Counts: Critical 1, Important 2, minor 2.

## P2-D1 — Critical — line 640 (B5 test preamble)
- Original: `  \`RouteGraph.build(agent.pathGraph, MovementParams())\`):`
- Changed:  `  \`RouteGraph.build(agent.pathGraph, Kanna(), MovementParams())\`):`
- Why: wrong signature for a binding interface. B5 says "Interface created: C5, exactly" (plan:637), and C5 defines
  `build(cls, path_graph: PathGraph, params: MovementParams)` (plan:285) and reads `reach_h`, `reach_v` from the
  PathGraph (plan:296-297, G17 at plan:107; `src/controller/path_graph.py:28-29` stores
  `max_horizontal_traversal_dist`/`max_vertical_traversal_dist`). B6 and B7 (plan:665, 685) and later tasks call
  `RouteGraph.build(agent.pathGraph, MovementParams())`. Code written to pass B5's tests as written breaks C5 and every
  downstream caller.

## P2-D2 — Important — lines 647-648 (B5 test 3, known case 2)
- Original: `... \`rg.path(<(101,24) node>, <(105,42) node>)\` is \`Success\` of the 2-tuple \`((101,24) node, (105,42) node)\` and that edge is \`DOWN\` with`
  / `     \`cost_s == pytest.approx(1.05)\` (teleport 0.55 + 4/8).`
- Changed:  `... \`rg.path(<(101,24) node>, <(105,42) node>)\` is \`Success\` and its last node is the \`(105,42)\` node, with`
  / `     \`rg.cost_to\` of the same pair also \`Success\`.`
- Why: leaves a target uncovered. The traceability table says B5 verifies known case 2 by "B5: DOWN edge exists"
  (plan:486; specB:L26). The weakened criterion passes for any path, including an indirect one, so no test checks the
  direct DOWN edge or its cost (C5 DOWN cost rule, plan:299-300). B7 test 2 (plan:687-688) still expects cost 1.05, so a
  B5 implementation that passes can force rework in B7.

## P2-D3 — Important — line 694 (B7 test 4, P4 formula)
- Original: `from \`(86,60)\` (d = 13) → TELEPORT;`
- Changed:  `from \`(86,60)\` (d = 13) → WALK 1625;`
- Why: test expectation contradicts the contract. C7 P4 rule: TELEPORT if T + |d − reach_h|/V < d/V
  (plan:362): 0.55 + 6/8 = 1.30 < 13/8 = 1.625, so TELEPORT; C7 states "d = 13 → TELEPORT" outright (plan:363). The
  test cannot pass against a C7-conformant planner; rework.

## P2-D4 — minor — line 680 (B7 Class line)
- Original: `the move rules (DC6, PB-D3)`
- Changed:  `the move rules (DC5, PB-D3)`
- Why: stale/wrong cite. DC5 is tolerances (plan:155); ability choice is DC6 (plan:156), which C7 implements.

## P2-D5 — minor — line 669 (B6 test 3)
- Original: `platform=3, ...)\` by A1 (|dx| = 2).`
- Changed:  `platform=3, ...)\` by A2 (|dx| = 2).`
- Why: wrong rule cite. An `Anchor` with `node=` set comes only from A1 (plan:316-317); A2 yields `Anchor(None,
  platform, L, R)` (plan:318-320). (103,42) vs (105,42): |dx| 2 ≤ align_px 2, |dy| 0, so A1 applies. Expected value
  unchanged; only the cite is wrong.

<!-- key, piece 3: /tmp/pb-ladder/keys/piece-3-answers.md, verbatim -->

# Piece 3 answer key (PB-seed-3)

Seeded file: /tmp/pb-ladder/piece-3/PB-seeded.md (1488 lines, same as the original).
Piece after seeding: lines 1188-1272 (Task B19 "Runtime wiring and the walk CLI"); no line shift.
Original: /home/user/helix-claude/docs/superpowers/plans/2026-10-23-p1b-route-walking.md (unchanged).
Counts: Critical 1 · Important 2 · minor 2.
`diff original seeded` shows changes only at lines 1191-1192, 1198, 1222, 1226-1228, 1230.

## P3-D1 · Critical · line 1222
- Original: `else `MinimapPositionSource(shared, locator)``
- Changed: `else `MinimapPositionSource(locator, shared)``
- Why: wrong signature against the plan's B16 contract, `MinimapPositionSource.__init__(self, shared, locator: CharacterLocator)`
  (plan line 1101; behaviour at 1104-1106 reads `shared.minimap_frame_lock`). Following the text, the default
  `_make_positions` passes the locator as `shared`, so the first `read()` raises `AttributeError` on
  `minimap_frame_lock`. The end-to-end test patches `_make_positions` (line 1243), so B19's tests stay green and the
  runtime path fails only in B21 (step 7, line 1326), blocking the in-game sign-off (specB:L68).

## P3-D2 · Important · lines 1226-1228
- Original: `builds the `RouteGraph`; `_start_capture`; `_wait_first_frame(30 s)`; one `positions.read()` must succeed (exit 3 otherwise); `link.connect()` (exit 4 on `Failure`); `InputLayer(...)``
- Changed: `builds the `RouteGraph`; `link.connect()` (exit 4 on `Failure`); `_start_capture`; `_wait_first_frame(30 s)`; one `positions.read()` must succeed (exit 3 otherwise); `InputLayer(...)``
- Why: wrong order. The same task's live link check states "the default `_wait_first_frame` fails (exit 3) before
  `connect`" (seeded line 1252), and the review log records the frame check preceding connect (plan line 1486, rev5 I2:
  "no minimap with the game closed → exit 3"). Connecting first makes the board connect and bond on every run that
  then exits 3 with no minimap, and contradicts line 1252; the order or the live-check text must be reworked.

## P3-D3 · Important · lines 1191-1192
- Original: `**Cloud:** no — imports `src.vision` (bettercam) and installs a Windows keyboard hook.`
- Changed: `**Cloud:** yes — imports `src.vision` (bettercam) and the Windows keyboard hook inside functions only (hard rule 9).`
- Why: wrong tag. The task table gives B19 Cloud "no" (plan line 524); hard rule 6 lists `src/route/runtime.py` and
  `walk.py` among the Windows-only modules (plan lines 59-60) and hard rule 9 (lines 70-73) says function-level imports
  only let the *tests collect* on Linux, not that the task runs there. The task's own live link check needs Windows
  and a paired board (seeded line 1251). A cloud agent would be scheduled for a task it cannot finish.

## P3-D4 · minor · line 1198
- Original: `# 0x7B = F12 (PB-D2)`
- Changed: `# 0x7B = F12 (PB-D4)`
- Why: stale cite. The F12 hotkey choice is decision PB-D2 (plan lines 1432-1442, "plan uses A with F12"); PB-D4
  (plan lines 1462-1474) is the stop guard between the stop check and `play`. Line 1267 in the same task still cites PB-D2.

## P3-D5 · minor · line 1230
- Original: `Prints "Focus the game window. Starting in 5 s. F12 stops."`
- Changed: `Prints "Focus the game window. Starting in 3 s. F12 stops."`
- Why: wording contradicts the countdown it announces: `_countdown_s = 5.0` (seeded line 1224), "waits `_countdown_s` s"
  (line 1231), and the live check's "During the 5 s countdown" (line 1263).
