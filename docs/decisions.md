# Decisions

This file records Hong's design decisions and rulings that touch interfaces, data behaviour or what the user sees. Entries are in the order they were made; ids are stable.

## H1 (2026-10-21): Phase 1 specs approved

Specs A–D are approved, including the reliability targets and the Shipping sections.

Affects: `docs/superpowers/specs/2026-10-21-p1{a,b,c,d}-*.md`. The plans and builds follow these specs.

## H2 (2026-10-21): Answers to the five handoff questions

All five take the proposed default:
- Passive screen capture while Hong is away: yes.
- Research is done inline (no `deep-research-interactive` gate).
- Any whisper stops the bot.
- Teleport reach is measured in the first supervised session.
- Claude may unpair, re-pair and reset the board.

Also: if there is no ground truth for the spinning-arrow solver, ship it flagged "accuracy unverified".

Affects: specs A (timing research, pairing and reset), B (reach), C (spinning-arrow solver and its flag), D (whisper handling, inline research).

## H3 (2026-10-21): Teleport reach 19/23 px was not measured on FF2

The reach of 19 px horizontal and 23 px vertical was not measured on Fossil Foundry 2. Hong assumes it is the same on every map. This is unverified; it is measured in the first supervised session.

Affects: `src/classes/classes.py` (single reach value), spec B and the route walking built from it. Claims that minimap scale or reach differs per map are not to be repeated until measured.

## H4 (2026-10-21): Merge rule

Only merge code that really works. A merges when its real-hardware targets pass. B, C and D merge only after in-game sign-off. C branches off `p1b` and D off `p1c`.

Affects: the Shipping section of each spec, branch layout (`p1a-input`, `p1b-*`, `p1c-*`, `p1d-*`) and what lands on `master`.

## H5 (2026-10-21): "Prefer false stops" applies to the lie detector and white room too

"Prefer false stops over misses" applies to the lie detector and the white room as well as whispers. Fine for now. The `docs/vision.md` edit changing "GM whisper" to "whisper" is okay.

Affects: spec D (detection thresholds and stop behaviour), `docs/vision.md`.

## H6 (2026-10-21): Away mode

Away mode runs from the start of the build on 2026-10-21. Policy questions are decided as Hong would decide them, except anything that changes in-game behaviour. Those are parked for Hong. Work may continue on a provisional, reversible choice, and nothing runs in the game while Hong is away.

Affects: how the orchestrator and its agents make decisions during the Phase 1 build; anything that changes what the character does in the game waits for Hong.

Superseded in part by H9: the carve-out for in-game behaviour no longer applies.

## H7 (2026-10-21): Environment while away

The game is closed. The ESP32 is plugged in over USB and paired. Live keystroke tests are allowed, but MapleStory must be confirmed not running before keys are sent. No passive capture this run.

Affects: how plan A's real-hardware checks run (keystrokes into a test window only), and means the passive-capture permission in H2 is not used in this run.

## H8 (2026-10-21): Git actions

Hong's words: "any git action you think is needed". Bounded by the specs' Shipping rules: B, C and D are never merged to master before Hong's in-game sign-off. Process limit on top: no force-push or history rewrite of `master`, and no deleting branches.

Affects: branches, commits and pushes made during the build (including pushing docs commits to `origin/master`).

## H9 (2026-10-21): Maximum autonomy

Hong's words: "I would like for you to try do as much as you can on your own. The goal is to be as autonomous as possible. Only include me when you are unable to progress further and have concluded you have no way of achieving whatever goal you had."

Effect: supersedes H6's carve-out. The orchestrator decides as Hong on everything, including decisions that change in-game behaviour. An opus-xhigh adjudicator rules first, and each ruling is logged with its cost if wrong and how to undo it. Hong is contacted only when a goal is truly blocked after every route has been tried, and even then only that unit is parked. Git: push and merge as the specs' Shipping rules allow, without asking.

Hard rules unchanged: no bot in the game while Hong is away; live keystrokes only while MapleStory is not running; B, C and D merge to master only after Hong's in-game sign-off; no recordings of Hong as a timing source; `~/PycharmProjects/Helix` stays read-only.

Affects: how the orchestrator and its agents make decisions for the rest of the Phase 1 build; git actions (see H8).

The entries below are rulings made as Hong under H9, each after an opus-xhigh adjudicator's read of the options. "Plan A" is `docs/superpowers/plans/2026-10-21-p1a-input.md`; plan line numbers in the undo steps are as of commit `6d4f677` unless the entry names another commit (the two PA-fix1c entries are as of `75dd45e`), and they drift once the plan is edited. Ids are the orchestrator's decision ids (PA = plan A, PAr = timing-model research, `-adj` = adjudication, `-fix1c`, `-fix3d`, `-fix3e`, `-fix4b` = raised in fix units PA-fix1c, PA-fix3d, PA-fix3e, PA-fix4b, `-rev3a` = raised in review PA-rev3a; a bare plan-A task id is the task that raised it, e.g. `F1-D1` = raised in task F1).

## PAr-D1 (2026-10-21): Timing-realism tests run on the values the timing layer chose

Spec A's realism tests (KS fits per timing type, and no run of 5 repeated gaps) run on the values the timing layer chose, not on times measured at the PC. The Holds row widens to "Delivered timing": each hold, chord offset and in-action gap measured at the PC listener is within ±20 ms of the chosen value for ≥95% of each type, and the second key of a chord is never seen before the first. The verification bullet is reworded to match.

Reason: the Bluetooth connection interval puts measured times on a grid (confirmed by the adjudicator's Monte Carlo), so testing them would gate the merge on the link rather than on the generator. Delivered timing still catches chord-offset and gap delivery bugs that testing only the chosen values would miss (about 68% of offset-ignoring chords are more than 20 ms off).

Affects: spec A (Reliability targets rows "Delivered timing" and "Timing realism", and the Verification bullet), commit `121c59f`; the merge gate of A under H4; the analysis task of plan A (its task H4, not decision H4) and the harness, which logs both chosen and measured values.

Ruling as Hong under H9, ratified by Hong at S1 (2026-10-23, A-final-R3-D1 option A); undo: revert `121c59f` and re-run the analysis step. If it proves too lax, a later finding can be re-analysed offline from the soak log, which holds chosen and measured values.

## PA-D2 (2026-10-21): Module layout for the input layer

New `src/input/` package, harness in `src/input/harness/`, firmware in a top-level `firmware/` directory, `src/ble/` left untouched, and an `InputLayer` facade that Features B–D call.

Reason: the adjudicator confirmed every load-bearing claim (no `src/input` exists in Helix or upstream; imports tested on Python 3.14), and the alternative mixes in the broken experiments in `src/ble/`.

Affects: module boundaries for A and everything B–D import; plan A task C3 and the Files lines P1–D1.

Ruling as Hong under H9; undo: one `git mv` plus an import rewrite after P1, and the same for `firmware/`. The undo list, with plan lines as of `6d4f677`: C3 (:203-296); the Files lines of P1–P5 and H1–D1 (:647, :788, :825, :853, :1027, :1071, :1136, :1206, :1306, :1380, :1423, :1498); the command and import lines (H1's `$PY -m src.input.harness.listener` :1127, P4's logger and import text `src.input.link` :896, :934, :979, G1's test layout rule :456); tasks V1–V3 and D1; and the `firmware/` half: C1's shared `firmware/test/vectors.json` line (:179), F1 (:472-483), F2 (:490-523), F3 (:529 and its flash command :552-553), F4 (:598-599 and its compile and g++ commands :636, :639), F5 (:665 and its flash command :764), P1's vectors test (:649), V1's probe build (:1159-1160), H2's `firmware/README.md` reference (:1237), V3's reflash (:1450-1452) and D1 (:1498, :1505). `grep -nE 'src[./]input|firmware/' <plan>` lists every site except G1's test layout rule (it names `tests/input/`).

Superseded in part by H14: `src/ble/` was deleted on 2026-10-25.

## PA-D6 (2026-10-21): Timing model documented in spec A as built

Task D1 appends a "Timing model (as built)" section to spec A, built from `src/input/timing.py` `MODEL` at HEAD with differences from task C4 listed. Citations are in full with the quoted figures and carry no pointers into `.superpowers/` (gitignored). A new step P2 Step 0 re-reads the primary source of any figure that was seen only through a summary (Woods 2015) before `MODEL` is coded. The soak task S1's precondition becomes "V3 passes and D1 committed".

Reason: it meets spec A's requirement that the timing model's sources be cited in the spec, keeps those sources in git, and surfaces a source mismatch before the 2-hour-plus soak.

Affects: spec A (new section at D1), plan A tasks D1, P2 and S1.

Ruling as Hong under H9; undo: drop the D1 bullet 3 (:1506-1530), drop P2 Step 0 (:790-798), and restore the S1 precondition line (:1535-1536).

## PA-adj2-D1 (2026-10-21): release_all() pre-empts a play() in flight from another thread

`release_all()` can be called from another thread while a `play()` is in flight. It writes RELEASE_ALL at once and the pending `play()` returns ABORTED. Specified in tasks C3, P4 and P5, with a P4 test (`release_all` during a fake 3 s play: both return within 0.5 s, play ABORTED). V1 measures release-all to all-keys-up mid-hold at the listener and reports it; no new A target.

Reason: spec D needs a stop within 500 ms that overrides B and C, and the board already pre-empts; leaving this out means D would rework merged A code.

Affects: `InputLayer` interface and concurrency model used by B, C and D; plan A tasks C3, P4, P5, V1. Overlaps PA-D5 and PA-adj1b-D1.

Ruling as Hong under H9; undo: drop the C3, P4 and P5 lines and the test.

## PA-D1 (2026-10-21): Binary v1 wire protocol between PC and board

Commands PLAY, RELEASE_ALL and PING; responses ACCEPTED, DONE, REJECTED and PONG on the notify characteristic `89f7c2ca-89b5-4234-905c-d4a931feaa3b`. A play step is 3 bytes, at most 16 steps. The board adds a random start delay U(0, connection interval) (START_JITTER, on).

Reason: every failure path behaves the same under JSON (the adjudicator's 20-row cases table), and binary needs no parser on the board. Jitter never hurts and helps when the connection interval is between 20 and 25.8 ms.

Affects: the PC↔board interface (spec A "Left to the builder": command encoding and ack channel); `vectors.json`, `protocol.h`, `protocol.py`, both parsers, firmware (F5).

Ruling as Hong under H9; undo: a v2 format can go behind the version byte (about one task), and a mismatch fails visibly with BAD_VERSION. To revert: `vectors.json`, `protocol.h`, `protocol.py` and a reflash (F5 step 5); set `START_JITTER` to 0 to turn the jitter off.

## PA-adj1-D1 (2026-10-21): connect() fails if the negotiated MTU is under 56

P4 `connect()` returns Failure if the PONG-reported MTU is below 56. Adds a P4 test and a rule in F3 step 7: "mtu ≥ 56 else stop, [decision]".

Reason: at the default 23-byte MTU every DONE of 4 or more steps is truncated silently by NimBLE (`ble_att.c:421-432`) while `sendValue` returns true, which gives false ACK_TIMEOUTs after keys were pressed. This fails loudly and early (spec A, goal 2: no silent drops).

Affects: the `InputLayer` connect contract; plan A tasks P4 and F3.

Ruling as Hong under H9; undo: delete the check. If Windows grants only 23 bytes, F3 stops and B/C decide a per-PLAY check or a fixed 20-byte DONE then.

## PA-adj1-D2 (2026-10-21): Per-step cap of 6.5535 s in the protocol

C1 adds a validation rule: a step delta above 65,535 (6.5535 s) is a Failure, with a reject vector and a P3 test that `walk()` returns Failure. PA-adj1b found no revisit needed (adjacent FF2 nodes are at most 17 px apart; longer walks chain).

Reason: a u16 step cannot hold a longer delta and an exception would break the Result pattern. Soak walks (3 s or less) are unaffected.

Affects: protocol validation in `protocol.py` and `protocol.h`, `vectors.json`; plan A tasks C1 and P3.

Ruling as Hong under H9; undo: revert the vector and the rule. If B needs walks above 6.5 s, move to 1 ms units or u24, which changes the format in both parsers.

## PA-D5 (2026-10-21): Closed schedules: every PLAY ends with all keys up

Every PLAY ends all-up, its total is at most 10 s, and each step changes one key.

Reason: B gets feedback release through `walk(dir, upper_bound)` plus `release_all()` with no protocol extension (the planner's "needs an extension" claim was refuted; spec A lines 13 and 15 read at the level of an action, so this is no departure from them). A closed schedule bounds every hold even if the PC hangs, whereas an open-ended PLAY with a watchdog stays fed by the keepalive during a hung caller.

Affects: protocol validation rules and the firmware state model; how B releases arrows (supervised session to check that lifting the arrow between moves does not hurt movement).

Ruling as Hong under H9; undo: if B's supervised session shows lifting the arrow between moves hurts movement, add op 0x04 PLAY_OPEN behind the version and op bytes (1–2 tasks, plus re-running A's hardware checks). Plan lines to revert, as of `6d4f677`: C1's "last mask is 0" rule :132-133, its closed-schedules paragraph :139-142 and the schedule-rule rejects :183; F4's reject-vector list :623-624; P1's invalid-schedule test :649-650; P3's validate and ends-all-up test :839-841; F5's `loop()` abort and all-up text :741-745; P4's shape-rule REJECTED text and test :895, :978; and the feedback-release text that rests on closed schedules (C3 :275, P5 :1033, P5's test :1060). `grep -n "PA-D5\|last mask\|invalid schedule\|all-up" <plan>` lists them, with a few other all-up lines (plus a reflash).

## PA-adj1b-D1 (2026-10-21): InputLayer sends one release_all() after an uncertain failure

After `play()` returns NOTIFY_FAILED, ACK_TIMEOUT or WRITE_FAILED, `InputLayer` sends one `release_all()` before returning the Failure. Not after REJECTED, TOO_LONG or ABORTED, and LINK_LOST is impossible here. Adds P5 lines and a test.

Reason: the PC cannot know whether a key is down in those cases, and leaving the release to callers risks a stuck key.

Affects: the failure behaviour B, C and D see from `InputLayer`; plan A task P5.

Ruling as Hong under H9; undo: drop the P5 lines and the test. Cost if wrong is a redundant release when the keys were already up (inferred to be harmless).

## PA-D4 (2026-10-21): Firmware sends HID reports through a direct NimBLE notify

Keep the T-vK / Berg0162 ESP32 NimBLE Keyboard library for the HID setup, bypass its `sendReport` using the public NimBLE lookup copied from `NimBLEHIDDevice.cpp:157-167` (run in `onStarted`), and send with `notify(report, 8, connHandle)`.

Amended by A-F5-D2 (below): F5 sends with `notify()`'s two NimBLE calls (`ble_hs_mbuf_from_flat` + `ble_gattc_notify_custom`) made directly, so DONE carries the NimBLE rc.

Reason: all 15 claims checked out. `sendReport` coalesces key-down and key-up (`ble_gatts.c:2139-2146`, `:2370`) and returns void, and the alternatives end in the same notify call with more code to own.

Affects: firmware send path and the dependency use; plan A task F5.

Ruling as Hong under H9; undo: about half a task to switch; revert F5 step 4's HID input-report lookup bullet (:678-691) and send-path bullet (:707-713), plan lines as of `6d4f677`, and reflash. The subscription-tracking bullet between them (:692-706, PA-adj1c-D1) uses the lookup's `inputReport` pointer and changes with it.

## PA-adj1c-D1 (2026-10-21): Board tracks HID subscription and rejects PLAY if not subscribed

The board tracks the HID input-report subscription per connection handle (own callbacks into `onSubscribe`) and answers PLAY with REJECTED reason 6 HID_NOT_SUBSCRIBED when the commanding peer is not subscribed. PONG flags bit 3 means subscribed. P4 `connect()` waits, with a time limit, for bit 3 and otherwise returns Failure. Needs vectors, both parsers and one P4 test.

Reason: NimBLE's notify does not check subscription, so a report sent before Windows subscribes returns DONE OK with no key typed (the same class as Helix `ISSUES.md` #5).

Affects: the reject-code table and PONG flags in the protocol; the connect contract; firmware F5; plan A tasks C1 and P4.

Ruling as Hong under H9; undo: C1 tables (:144-171), `vectors.json`, `protocol.h`, `protocol.py` and the P4 check. If Windows never sets the CCCD as expected, `connect()` fails loudly and V1 measures it.

## PA-D3 (2026-10-21): Python 3.14 venv and pinned dependency files

Python 3.14 venv. `requirements.txt` pinned to the versions Helix runs. `requirements-dev.txt` holds pytest and `scipy==1.18.0`, and also pins `winrt-Windows.Devices.Radios==3.2.1` for the H2 `faults.py`. Four additions: (1) no `requirements-rune.txt` in A (Feature C writes, installs and tests it; spec C), (2) G1 step 2 expects Python 3.14.0 because networkx 3.6.1 refuses 3.14.1, (3) the G1 commit body records `pip freeze` (seven transitive packages resolve newer than Helix), (4) the winrt pin above.

Reason: every file A ships is then installed and tested by A (H4). All pins have cp314 wheels (dry-run resolved, no torch), and the rune dependencies go to the feature that imports them.

Affects: dependency files and the venv created by plan A task G1 (spec A "Left to the builder": groundwork).

Ruling as Hong under H9; undo: four small plan edits, all in G1 (plan lines as of `6d4f677`). Item 1: restore the step 3 rune paragraph (:432-434, which now says no `requirements-rune.txt` is created) and list `requirements-rune.txt` in the Files line (:419), the note under the heading (:420) and the step 7 commit (:463). Item 2: revert step 2's expected `Python 3.14.0` line (:424-427). Item 3: drop the freeze sentence (step 7, :464-466). Item 4: drop the winrt pin (step 4, :437-439). H2 (:1245-1246) cross-refers to the pin and D1 (:1500-1502) repeats the Python 3.14.0 and rune-dependency facts, so both change with them.

## PA-D7 (2026-10-21): Harness listener blocks the bot's keys by default

The harness listener's low-level keyboard hook blocks the 7 bot keys by default, with a `--no-block` flag. The Tk sink window stays focused, with a foreground check before every command.

Reason: it is the only option that keeps keys out of other windows if focus changes mid-command. The hook records before it returns, so measurements are unchanged (`lowlevelkeyboardproc.md:94`), and unblocked Alt taps put the Tk sink into menu mode (`tkWinMenu.c:2222-2226`). This departs from spec A's wording "into a focused test window".

Affects: where harness keystrokes land; what a person at the PC can type during a run (arrows, E, Alt and Space are blocked system-wide while a run is on). Does not remove the check that MapleStory is not running (the hook is no barrier against the game's Raw Input; H7).

Ruling as Hong under H9, ratified by Hong at S1 (2026-10-23, A-final-R3-D1 option A), when spec A's listener bullet was reworded to what was built; undo: plan task H1 blocking bullet (:1091-1101) to default off, plus its default-flag test, and restore the spec bullet; no re-run of V1–V3.

## PA-fix1c-D1 (2026-10-21): SendReport.accepted is None for RELEASE_ALL

C3's `SendReport.accepted` is `Accepted | None`, with None for RELEASE_ALL. This matches the protocol as built: RELEASE_ALL gets no ACCEPTED (PA-D1).

Reason: nothing reads the field from a release report; the report keeps `sent_ns` and `written_ns` for V1's release-all measurement; and the alternative (option B) would slow, and add a false-failure mode to, D's stop path.

Affects: the `SendReport` interface that B, C and D see; plan A tasks C3 and P4. Plan lines as of `75dd45e`: C3 `SendReport` :244-245; P4 `release_all` bullet :868 and its test :949 (the "raised as PA-fix1c-D1" parenthesis is gone). Applied in the PA-rev fix round (`f57a844`).

Cost if wrong: a later consumer dereferences `.accepted` on a release report (an AttributeError, which the tests catch), or a type checker demands narrowing on play reports.

Ruling as Hong under H9; undo: revert the one C3 line (:244), or move to option C (about 5 plan lines).

## PA-fix1c-D1-adj-D1 (2026-10-21): SendReport records accepted_ns and done_ns so a latency miss can be split

`SendReport` gains `accepted_ns: int | None` and `done_ns: int` (both `perf_counter_ns`). P4's notify callback records them when it decodes an ACCEPTED or a DONE, on the loop thread; release reports have `accepted_ns=None`. P4's test checks `sent_ns ≤ written_ns ≤ accepted_ns ≤ done_ns` (amended by PA-rev3a-D1, below: no order between `written_ns` and `accepted_ns`). C5 adds "for a Failure the timing fields are null; `status`/`error` carry the InputError". The M7 split is defined at V1's M7 bullet.

Reason: V3 reports a latency miss "with the M7 split", which needs `accepted_ns`; recording is free on the loop thread and additive; and the alternative (option B) would force another soak of at least 2 h to split a latency miss.

Affects: data behaviour (the C5 timing fields), the log format and the `SendReport` interface that B to D see; plan A tasks C3, C5, P4, V1, H3 and V3. Plan lines as of `75dd45e`: C3 :244-245; C5 :334-338; P4 notify callback :855 and :906-907, `release_all` :868, tests :933-934 and :949; V1 M7 split :1114-1118; H3 Latency :1245 and its test :1259; V3 step 6 :1377. Applied in the PA-rev fix round (`6d48485`).

Cost if wrong: two unused fields on the facade.

Ruling as Hong under H9; undo: delete the two fields at C3 (:244-245) and C5 (:334, :336), and the test clause (:933-934).

## PA-fix3d-D1 (2026-10-21): faults.jsonl records each fault's stamps, and an entry with a null stamp FAILs H3 Faults

Keep as applied: C5 `faults.jsonl` gains `launch_ns`, `fault_seen_ns` and `failure_ns`, and H3's Faults check fails an entry whose `injected_ns` or `reported_ns` is null. PA-rev3b's G1–G4 are additive clarifications (about 4 plan lines) for the fix round.

Reason: the opus-xhigh adjudicator (PA-rev3b piece 1) found that reverting re-opens PA-rev I5, and a null entry would then crash the check or pass silently.

Affects: the log format (C5 `faults.jsonl`) and H3's Faults check; plan A tasks C5, H2 and H3. Plan lines as of `6d4f677`: C5 `faults.jsonl` :358-368; H3 Faults :1341-1344. Applied in `0146783`; G1–G4 applied in `5de889f`, and cited in C5 by m-i in `6d4f677`.

Cost if wrong: one more field set in a log that no consumer outside H3 reads.

Ruling as Hong under H9; undo: revert the C5 `faults.jsonl` line (:358-368) and the H3 Faults null-entry sentence (:1343-1344) in `0146783`.

## PA-rev3a-D1 (2026-10-21): P4's test does not order written_ns against accepted_ns (amends PA-fix1c-D1-adj-D1's test clause)

P4's test asserts `sent_ns ≤ written_ns` and `sent_ns ≤ accepted_ns ≤ done_ns`, with no order between `written_ns` and `accepted_ns`, using a fake that sends the notification before the write completes: one early-notification test covers PONG, ACCEPTED, PLAY DONE and RELEASE_ALL DONE, shared with PA-rev3a N1. The M7 split's second part becomes `accepted_ns − sent_ns`. P4's ACCEPTED-wait wording is fixed (ACCEPTED within 300 ms of the write's completion; one that arrived while the write was in progress counts). This amends PA-fix1c-D1-adj-D1's test clause `sent_ns ≤ written_ns ≤ accepted_ns ≤ done_ns`; every field that ruling added stays.

Reason: NimBLE runs `onWrite` before it sends the Write Response (`ble_att_svr.c:2345` → `:2353`, confirmed by the adjudicator), so the hardware does not order written against accepted.

Affects: P4's test and its ACCEPTED wait; the M7 split defined at V1 and printed by H3; plan A tasks P4, V1 and H3. Plan lines as of `6d4f677`: P4 `play()` :884-886, tests :972-975 and the early-notification test :980-983; V1 M7 split :1173-1179; H3 Step 1 test :1355-1356. Applied in `c491d7e`.

Cost if wrong: about zero (C5 logs all four stamps, so any split can be recomputed offline).

Ruling as Hong under H9; undo: restore the clause at P4 (:972-974) and the V1 split (:1174-1176).

## PA-rev3a-D2 (2026-10-21): close() fails pending commands with NOT_CONNECTED; connect() on a live session fails

`close()` fails pending commands with NOT_CONNECTED (not a fault), and `connect()` on a live session returns `Failure("already connected")`. Wording added with it: `close()` with no session is a no-op; a PLAY parked at `_before_write` is never written after `close()`; H2 and V2 reconnect with `close()` then `connect()`; one C3 bullet states both.

Reason: NOT_CONNECTED is the only existing InputError that is neither a fault nor a feedback signal. Option B's ABORTED would read as "arrived", and option C's LINK_LOST could false-PASS H3 Faults. Without the reconnect wording, V2's `reset_board()` rows false-FAIL.

Affects: the `BleLink` session contract that B to D see (C3); plan A tasks C3, P4, H2 and V2. Plan lines as of `6d4f677`: C3 "Session end" :284-286; P4 abort seam :917-922, Session state :957-967, test :1005-1007; V2 steps 3-4 :1429-1434. Applied in `c491d7e`.

Cost if wrong: one new InputError value later.

Ruling as Hong under H9; undo: drop the C3 bullet (:284-286) and the P4/H2/V2 sentences (P4 :919-921, :957-959, :963 and :965-967, the last naming H2's soak driver; V2 :1430-1431, :1433-1434).

## PA-rev3a-D3 (2026-10-21): Sample carries its steps and wire value; TimingType gains HOLD_CLIPPED

A amended: C3 `Sample` gains `step_from: int | None`, `step_to: int | None` and `wire_100us: int | None` (None only for REACTION). `TimingType` gains `HOLD_CLIPPED = "hold_clipped"`, which is not a `MODEL` key (P2 says "for each MODEL type"). C5 drops `first_key`, which has had no producer or reader since `8faa312`.

Reason: P3, C5 and H4's gated delivered-timing check already use these fields, and `ActionRecord.reaction` is a REACTION Sample with no steps.

Affects: the C3 contract that B to D see and the C5 log format; plan A tasks C3, C5, P2, P3 and P5. Plan lines as of `6d4f677`: C3 :224-226 and the `MODEL` comment :231; C5 `commands.jsonl` :338-341; P2 :801-802; P3 :834-838; P5 :1050. Applied in `c491d7e`.

Cost if wrong: one C3 edit; no code exists yet.

Ruling as Hong under H9; undo: revert the C3 Sample/TimingType lines (:224-226) and restore `first_key` in C5 (:338-341).

## PA-fix3e-D1 (2026-10-21): The soak log keeps a harness-only FOCUS_LOST row and session.json focus_pauses

Keep as applied the harness-only C5 `FOCUS_LOST` row and `session.json` `focus_pauses`, completed by PA-rev3b's m-e and m-f text fixes.

Reason: the opus-xhigh adjudicator (PA-rev3b2) found that C3's InputError is unchanged and specs B–D don't read the row. Option B leaves recovered pauses untraceable (extras typed during a pause look like link faults), and option C leaves V3's invalid-run rule nothing to key on.

Affects: the log format (C5 `commands.jsonl` and `session.json`), read only by the harness; plan A tasks C5, H1, H2, H3, H4, V1, V2 and V3. Applied in `0ef978c`; m-e and m-f applied in `a488f00` and `bab0d3d`; cited in C5 by m-i in `6d4f677`.

Cost if wrong: one unread diagnostic field (`focus_pauses`).

Ruling as Hong under H9; undo: revert C5 :346-351 and :369-371 and the uses at H1 :1103-1104, H2 :1220-1224 / :1289-1294, H3 :1329-1331 / :1371-1373, V2 :1437 and V3 :1460-1465 (lines as of `6d4f677`; the ruling gave them as of `95ae0ed`).

## PA-fix4b-D1 (2026-10-21): link.log stamps perf_counter_ns on every record; session.json has no clock_anchor

How a `link.log` time is tied to `perf_counter_ns`, the clock of every other C5 file: B. Drop `session.json` `clock_anchor`. A `logging.Filter` on H2's `link.log` handler sets `record.perf_ns = time.perf_counter_ns()` on every record, and C5's `link.log` format prints it. H2 Step 1 gains one test clause: a record logged through the handler carries a `perf_ns` that lies between two `perf_counter_ns()` reads taken before and after the log call. The other options were A (`clock_anchor`, one `[time_ns, perf_counter_ns]` pair read before `connect()`, applied provisionally in `5de889f`) and A′ (A plus a second pair at run end).

Reason: the opus-xhigh adjudicator (PA-rev4a) showed that B is exact by construction and survives a wall-clock step and a hard crash over an unattended ≥2 h soak, and that no program parses `link.log`, so the line-format change breaks nothing.

Affects: the log format (C5 `link.log` and `session.json`), read only by a person offline; plan A tasks C5 and H2. Plan lines as of `5af1e74`: C5 `session.json` :369-374, `link.log` :375-382; H2 "Log C5 files" :1227-1229, link log :1230-1234, Step 1 test :1276-1277. Applied in `5af1e74`.

Cost if wrong: one extra number per `link.log` line.

Ruling as Hong under H9; undo: revert the fix-round hunks (`5af1e74`: C5 :371-374 and :377-379; H2 :1227, :1231-1234 and :1276-1277) and restore `5de889f`'s m-c hunks (C5 `session.json`, C5 `link.log`, H2).

## F1-D1 (2026-10-22): .gitignore also ignores firmware/Helix_BLE/build/

A. Keep `9e6c31a`, which adds `firmware/Helix_BLE/build/` to `.gitignore` beside the `firmware/build/` line from Plan A task F1 step 3 (:482). The other options were B (keep only the plan's line; each firmware task deletes the folder) and C (find a setting that stops the copy).

Reason: the opus-xhigh adjudicator (A-F1-R) found that every compile with `--output-dir` (F1, F2, F3, F4, F5, V3 and the V1 probe) leaves about 26 MB of binaries in `firmware/Helix_BLE/build/`, which the plan's `firmware/build/` line does not match (it matches only at the repo root), and that no arduino-cli setting stops it (`always_export_binaries` is already false; the esp32 core 3.3.10 postsavehex hooks, `platform.txt:202-235`, make the copy). One line covers all seven compiling tasks with no command change, B leaves a 26 MB staging trap in seven tasks, and C has no setting and would rewrite six tasks' commands or Hong's global Arduino core.

Affects: `.gitignore` only; no plan command changes. Applied in `9e6c31a`; its comment's cause reworded (F1 review M1) in `8d70f4a`.

Cost if wrong: deleting `.gitignore:40-41`, and a reviewer flags the untracked folder.

Ruling as Hong under H9 (away mode, opus-xhigh adjudicated in A-F1-R); undo: delete `.gitignore:40-41` (`git revert 9e6c31a` alone conflicts, `8d70f4a` rewrote :40).

## A-PF-D1 (2026-10-22): TimingModel.draws gives each Sample its index

A′ (amended A). C3 `TimingModel` gains a read-only `draws: int`: the values its six methods have returned so far, 0 at construction, rejected truncation candidates not counted. A builder sets `index = model.draws - 1` right after each draw, so `Sample.index` is the 0-based draw ordinal (with the seed it fixes the value's place in the RNG stream). The P3 and P5 stubs count their calls in `draws`, C3 notes that one thread draws at a time, and V1 M4's probe-only override calls the base method. The other option was B (the six methods return a partial `Sample`); C (a second `sample()` API beside the floats) and D (drop `index`) were also weighed and rejected.

Reason: the opus-xhigh adjudicator (A-PF-adj1) found that A′ is additive, keeps the float returns that P2's tests and the P3/P5 stubs assume, and that no gate check reads `index` (H3 and H4 never do; only P3's and P5's unit tests assert it). numpy 2.5.1's PCG64 exposes no draw counter, so a model-level ordinal is the only integer form of PAr-D1 §3's "stream position". B changes six C3 return types and makes every P2 test unwrap `.ms`. A′'s one weakness, a read-after-call race, needs two threads drawing at once, which no Phase 1 caller has.

Affects: the C3 interface (`TimingModel.draws`, the meaning of `Sample.index`); plan A tasks P2, P3, P5 and V1. Plan lines as of `fd06197`: C3 :228 and :233-236, P2 :810-812, P3 :840-842 and :853-855, P5 :1066-1071, V1 :1186-1189. Applied in `fd06197`.

Cost if wrong: low. `index` is investigation-only: if B–D later draw from two threads, logged indexes can repeat or skip, which corrupts no gated result, and a later switch to B changes six signatures before P2 lands.

Ruling as Hong under H9 (away mode, opus-xhigh adjudicated in A-PF-adj1); undo: `git revert fd06197`.

## A-PF-D3 (2026-10-22): V1 runs after H4 and imports H2-H4's logic

A′ (amended A). Plan A's task order becomes H1, H2, H3, H4, V1, V2, V3, and the V1 section moves after H4. H2, H3 and H4 expose importable functions (the C5 run writer and the focus policy in `soak.py`, Delivery matching with its per-command windows and the M7 split in `analyze.py`, the pairing of samples to listener intervals in `realism.py`); V1's `probe.py` imports them, re-implements none, and sets `stimulus_ns` before each command. V1's foreground pre-check moves into H2 step 3, whose 200-command dry run becomes the first run of `BleLink`/`InputLayer` on the board. The M7 definition stays at V1's M7 bullet (PA-fix1c-D1-adj-D1, PA-rev3a-D1). The other options were B (keep the order; V1 creates `harness/runlog.py`, which H2 imports) and C (split V1 into a part before H2 and a part after H4).

Reason: the opus-xhigh adjudicator (A-PF-adj2) found that V1 used H2-H4 logic (C5 writer, focus policy, Delivery matching, pairing, M7 split) before those tasks exist. A′ removes the duplicated logic, keeps it in tasks that have tests, and weakens no hard rule. B leaves matching, pairing and the M7 split duplicated or moves them into a task with no test file, and adds a module to C3's list; C still needs a writer and a focus policy before H2, plus a new task id and a second hardware session.

Affects: plan A task order and the module boundaries / data flow between H2-H4 and V1. Plan lines as of `56443e6`: task table :423-427, H2 :1191, :1195-1199 and step 3 :1280-1285, H3 :1330-1331, H4 :1407-1408, V1 :1426-1502 (moved; Files :1428-1432, :1448-1449, M2 :1450-1453, M4 :1465, M7 :1478-1479). Applied in `dcb59a1`.

Cost if wrong: the release-all ordering check now runs after H2's 200-command dry run, so the worst case is one ≤ 10 s schedule of bot keys (no Enter) into the test window during that dry run; and P4/P5 hardware bugs surface in H2's dry run, where V1's narrower probes would have isolated them faster.

Ruling as Hong under H9 (away mode, opus-xhigh adjudicated in A-PF-adj2); undo: `git revert dcb59a1` (V1 back before H2; revert `56443e6` first if its H2 hunks conflict). At a later HEAD the revert conflicts (later commits edit the same bullets, e.g. a848c35 extends E3.11's, cb333ee rewrote E4.3's); remove only this ruling's clauses by hand.

## A-PF-D4 (2026-10-22): C5 logs link faults and session starts for H3's Connection check

Amended A. C5 gains `link_faults.jsonl` (`{"qpc_ns", "error"}`, one line per `BleLink.faults()` entry, appended by H2's soak driver after each command and on every exit path; a `LINK_LOST` line is one drop, and `close()` adds none) and `session_starts.jsonl` (one row per start: attempt, condition, result, error, seconds, start_ns, end_ns; written by V2's `session_starts.py` into its own run dir `data/starts/<run>/`). H3's Connection check becomes two parts with tests: starts (`--starts <file>`: 20 rows split 8/6/6, all Success) and drops (`LINK_LOST` lines, gated only in a run with no injected fault: 0 drops over a connected span ≥ 2 h, from the first command's `stimulus_ns` to the last non-null `done_ns`). V2 gets a writer test step (step 2b) and passes `--starts` to `analyze`. The other option was B (count `LINK_LOST`/`NOT_CONNECTED` rows in `commands.jsonl`).

Reason: the opus-xhigh adjudicator (A-PF-adj2) found that H3's Connection check read two inputs that nothing defined. B miscounts deliberate `close()` reconnects as drops (`close()` fails pending commands with `NOT_CONNECTED`, which is not a fault, PA-rev3a-D2), cannot see a drop after the last command, has no drop times, and leaves the session-start log undefined. A is additive to C5 and measures spec:L61's quantities directly.

Affects: the C5 file format (two new files) and the data flow H2 / V2 → H3; plan A tasks H2, H3, V2 and V3. Plan lines as of `56443e6`: C5 heading :341 and bullets :390-401, H2 :1192-1194 and :1277-1279, H3 :1343-1351 and :1371-1375, V2 :1506-1507, :1513-1515, :1518-1520 and :1524-1526, V3 :1557-1558. Applied in `56443e6`.

Cost if wrong: two extra harness files and their tests.

Ruling as Hong under H9 (away mode, opus-xhigh adjudicated in A-PF-adj2); undo: `git revert 56443e6`. At a later HEAD the revert conflicts (later commits edit the same bullets, e.g. a848c35 extends E3.11's, cb333ee rewrote E4.3's); remove only this ruling's clauses by hand.

## A-PF-D2 (2026-10-22): BleLink.take_pongs() feeds C5 pongs.jsonl

A′ (amended A). C3 `BleLink` gains `take_pongs() -> list[tuple[int, Pong]]`: every PONG that completes a pending PING (connect's ready wait, the keepalive, `ping()`), stamped with `perf_counter_ns` by the notify callback on decode, returned oldest first and drained. P4 bounds the buffer at `PONG_BUFFER_MAX = 4096` (drop-oldest, one WARNING per `take_pongs()` interval on `src.input.link`) and keeps it across reconnects. C5 `pongs.jsonl` now covers every PONG the link received, defines `qpc_ns` as that callback stamp, and rebuilds `flags` from `Pong`'s four bools (reserved bits 4-7 log as 0). H2 writes one row per entry after every command and on every exit path. The other option was B (H2's own 250 ms `ping()` thread); C (an `on_pong` callback) was also weighed and rejected.

Reason: the opus-xhigh adjudicator (A-PF-adj1) found that the keepalive PONGs C5 asked for never left `BleLink`, and that `qpc_ns` and `flags` had no defined source. A′ reads the keepalive PONGs themselves with the decode-time stamp (the PA-fix1c-D1-adj-D1 pattern), adds no radio traffic, and keeps memory bounded for Features B-D (plain A grows ≈ 332 B per PONG, measured, ≈ 115 MB a day at 4 Hz). B doubles PING writes in the soak only, so the soak would measure latency under a load production never has; C runs harness I/O on the loop thread that stamps `accepted_ns`/`done_ns`.

Affects: the C3 interface (`BleLink.take_pongs()`), P4's notify dispatch and the C5 `pongs.jsonl` definition; plan A tasks P4 and H2. Plan lines as of `cf2f9a1`: C3 :261, C5 :363-368, P4 :1011-1017 and Step 1 :1063-1067, H2 :1214-1215 and :1274-1276. Applied in `cb333ee`.

Cost if wrong: low. If 4096 is too small between two drains, PONG rows drop with a WARNING (visible in `link.log`) and H2 must drain more often; the gap between drains is ≤ ~1 min (≤ ~240 PONGs).

Ruling as Hong under H9 (away mode, opus-xhigh adjudicated in A-PF-adj1); undo: `git revert cb333ee` (revert `90af6b1` first if its H2 hunks conflict). At a later HEAD the revert conflicts (later commits edit the same bullets, e.g. a848c35 extends E3.11's, cb333ee rewrote E4.3's); remove only this ruling's clauses by hand.

## A-PF-adj1-D3 (2026-10-22): C5 logs the REACTION sample's index

A. C5 `commands.jsonl` gains `"reaction_index"` next to `"reaction_ms"`: the drawn R's `Sample.index` (C3, as A-PF-D1 defines it), null when no reaction was drawn and on a Failure row (so also on a `FOCUS_LOST` row, which draws none). H2 maps it from `ActionRecord.reaction.index`, and H2's schema test covers it. The other options were B (no field; P5 fixes the draw order, so R's index is inferred from the Plan's last sample index) and C (leave it out).

Reason: the opus-xhigh adjudicator that raised it (A-PF-adj1) found that PAr-D1 §3 (`PAr-D1.md:85-86`) asks for the RNG seed and stream position of every chosen value, reaction included, and that C5 dropped the index P5 puts in `ActionRecord.reaction`. A is additive and does not rely on an unstated draw order; B breaks if the order ever changes; C leaves PAr-D1 §3 unmet for reaction. No second adjudicator: a single additive log field.

Affects: the C5 file format (one field); plan A task H2. Plan lines as of `cf2f9a1`: C5 :348, :353-354 and :359-360, H2 :1216 and :1276-1277. Applied in `90af6b1`.

Cost if wrong: one unused JSON field.

Ruling as Hong under H9 (away mode, opus-xhigh adjudicated in A-PF-adj1); undo: `git revert 90af6b1`.

## A-F2-D1 (2026-10-22): capture monitors keep DTR and RTS off

A′ (amended A). Every capture monitor (Plan A tasks F2 step 6, F3 step 3 and F5 step 5) runs `"$CLI" monitor -p "$PORT" -c baudrate=115200,dtr=off,rts=off --timestamp`. The capture procedure reads the "Guiness" last-connected time (LCT) after stopping the monitor and before the reset, and again after the capture, and compares the two readings. The other options were A (the same flags, but the LCT read sits between the reset and the reopen) and B (keep the plan: default `dtr=on,rts=on`).

Reason: the opus-xhigh adjudicator (A-F2-R) found that serial-monitor v0.15.0 (`main.go`) has `dtr` and `rts` settings that default to `on` and that its `getMode()` passes them as the port's initial status bits, and that arduino-cli v1.5.1 (`service_monitor.go`) applies `-c` settings before it opens the port, so `dtr=off,rts=off` opens with both lines low and (inferred, one control run) nothing changes at close. HWCDC keeps output written before the host opens the port in a 256-byte TX ring (once the ring is full, the not-connected path drops the oldest bytes and the connected path drops the later line, HWCDC.cpp:293-341 vs :586-604; A-F5-R I-1), so the plan's "output before the open is lost" inference was wrong while output stays at or under 256 bytes. A′ stops the unrequested reboot at every monitor close (F2 saw it on 3 of 3 default closes and on none with both off). A's LCT read in the reset-to-reopen gap delays the reopen against F3's 3 s host wait and the ring, and may read LCT before Windows reconnects; B reboots the board at every capture close, can add a spurious disconnect in F5 step 6, and keeps the disproved inference.

Affects: capture data behaviour (which output and which reboots a capture contains) and the capture procedure that F3 and F5 reuse; plan A tasks F2, F3 and F5. Plan lines as of `3ffe9d8`: F2 step 6 :542-558, F3 step 3 :591-594, F3 step 7 :626-629, F5 step 5 :818-820. `firmware/README.md` as of `63683fb`: Monitor row :51, capture procedure :68-93. Applied in `3ffe9d8` (plan) and `63683fb` (README).

Cost if wrong: if `dtr=off,rts=off` ever loses boot lines, F3's first capture shows it and the flag comes out (one plan line per step).

Ruling as Hong under H9 (away mode, opus-xhigh adjudicated in A-F2-R); undo: `git revert 63683fb 3ffe9d8`.

## walk-T (2026-10-22): C5 commands.jsonl logs a walk's intended T as intended_ms

A, kept with two amends. C5 `commands.jsonl` keeps `"intended_ms"` after `"action"` (`cf2f9a1`): the T passed to `walk()`, on every WALK row (Success, Failure, `FOCUS_LOST`) and null on other actions; it is not nulled on Failure or `FOCUS_LOST` rows (driver input, like `action`). H2's Step 1 test now states this, and H4's "WALK error stats" is defined as `chosen_ms − intended_ms` per Success WALK row (mean, SD; reported, not gated). The other option was to drop the field and define the stat without it.

Reason: the opus-xhigh adjudicator (PA-rev6b Q5) found that T ~ U(300, 3000) is a per-walk driver draw (H2) logged nowhere else (`chosen_ms` and `steps` hold the drawn hold, not T), so without the field H4 could only re-run H2's seeded RNG, which couples H4 to H2's draw order. PAr-D1.md:86 asks to log "For walks, the intended T" regardless of H4, and "WALK error stats" had no definition since `8faa312`. Only H4 reads the field (H3 never does); Failure and `FOCUS_LOST` WALK rows have null `samples`, so they give no pair and H4 skips them.

Affects: the C5 file format (one nullable field); plan A tasks H2 (test) and H4 (stat definition). Plan lines as of `468d593`: C5 :345-346, H2 Step 1 :1319-1320, H4 :1494-1495. Applied in `cf2f9a1` (C5) and `cf37a9e` (amends).

Cost if wrong: one unused nullable field in `commands.jsonl`.

Ruling as Hong under H9 (away mode, opus-xhigh adjudicated in PA-rev6b); undo: remove the `intended_ms` clause at C5 :345-346 by hand (`git revert cf2f9a1` would also undo K1 and K4) and `git revert cf37a9e`.

## PA-rev6b-I-1 (2026-10-22): F5's abortRequested count is atomic and never reset

A. F5's `abortRequested` count (A-PF m9: `onWrite` increments it for each RELEASE_ALL, the executor decrements it on each, and a PLAY is aborted while it is > 0) is never reset by the executor: a count > 0 with an empty queue is a RELEASE_ALL that `onWrite` has counted but not yet queued. `onWrite` counts only a RELEASE_ALL it queues (on QUEUE_FULL it takes the increment back before replying), and every change is atomic (`std::atomic<int>` or a critical section), because the host task and the loop task both change it. This replaces `a848c35`'s "If the count is > 0 and the queue is empty, the executor sends all-up and sets it to 0"; m9's "> 0" and "decrements" stay (PA-rev6b Q8). The other option was to keep the reset to 0.

Reason: the opus-xhigh adjudicator (PA-rev6b) found that the reset races `onWrite` (an inferred interleaving, from the plan text): `onWrite` runs on the host task and the executor on the loop task, so an executor pass between `onWrite`'s increment and its enqueue sees count 1 and an empty queue, sets 0, then dequeues the RELEASE_ALL and decrements to −1. A signed count then never pre-empts a running PLAY again; an unsigned one wraps and every PLAY is ABORTED; either is stuck until reboot. `++`/`--` from two tasks on the dual-core S3 without an atomic can also lose an update (P4 names two concurrent releases), with the same results. The old bool's "clears" was idempotent, so the race arrived with the count.

Affects: the firmware concurrency model (F5's `onWrite` and executor); plan A task F5. Plan lines as of `468d593`: F5 :795-799. Applied in `0418d94`.

Cost if wrong: one F5 paragraph rewritten again before F5 is built.

Ruling as Hong under H9 (away mode, opus-xhigh adjudicated in PA-rev6b); undo: `git revert 0418d94`.

Amended by PA-rev6c-D1 (below): each queued RELEASE_ALL carries a `counted` flag that decrements the count exactly once at dequeue.

## A-F3-R-a (2026-10-22): F3's HID check matches the Keyboard class by board address

B. Plan A task F3 step 6 checks that Windows installed the keyboard with `Get-PnpDevice -PresentOnly -Class Keyboard | Where-Object InstanceId -match '1020BA4E2771' | Select-Object Status, Class, FriendlyName` and expects one row `OK Keyboard HID Keyboard Device`. The other options were A (keep `Get-PnpDevice -PresentOnly | Where-Object FriendlyName -match 'Guiness'`, expecting a Bluetooth entry plus a HID entry) and C (walk `DEVPKEY_Device_Children` from the "Guiness" BTHLE device, as `data/F3-pnp.ps1` does).

Reason: the opus-xhigh adjudicator (A-F3-R) ran the plan's check on 2026-10-22 after F3's re-pair and it printed only the Bluetooth entry: the HID entries are named `Bluetooth Low Energy GATT compliant HID device` and `HID Keyboard Device`, not "Guiness", but each carries the board's address `1020ba4e2771` in its instance id. So A reports "no HID entry" while the keyboard is installed and a re-run would raise a false BLOCKED. B is one line and printed exactly the expected row; C is four lines and its script sits in the gitignored `data/`, so the plan cannot cite it.

Affects: a manual check in plan A task F3 (D1's README pairing procedure inherits it). Plan lines as of `210d18b`: F3 step 6 :616-625. Applied in `01aa21d`.

Cost if wrong: one check line.

Ruling as Hong under H9 (away mode, opus-xhigh adjudicated in A-F3-R); undo: `git revert 01aa21d`.

## A-F3-R-b (2026-10-22): F5 tags each CONN line with its event and logs CIREQ

C. In Plan A task F5 step 4 the firmware's `CONN` line becomes `CONN ev=%s handle=%u itvl_ms=%.2f latency=%u timeout_ms=%u mtu=%u bonded=%d encrypted=%d auth=%d`, with `ev` `connect` (`onConnect`), `auth` (`onAuthenticationComplete`) or `params` (`onConnParamsUpdate`), and `loop()` prints `CIREQ handle=%u` right after it sends the CI request. F5 step 6's PONG check compares `itvl` with the CI (`itvl_ms` / 1.25) of the latest `CONN` line for that handle instead of "the latest `onConnParamsUpdate` `CONN` line". F3 is not re-flashed. The other options were A (keep the format and explain the lines in notes) and B (an F3 follow-up flash now).

Reason: the opus-xhigh adjudicator (A-F3-R) found that F3's three callbacks printed one format, so no `CONN` line in F3's captures can be tied to the CI request (notes/A-F3-R.md I-1): a rejected request prints the same line, because NimBLE raises the update event with a non-zero status on failure (`ble_gap.c:7781-7785`, `:1372-1382`) and `NimBLEServer.cpp:600-606` does not check it, and (inferred) a request Windows accepts without a change prints none. That also made F5 step 6's check unrunnable as written. M1 still passes (CI 15.00 ms on every line). C is about six lines in the file F5 already edits and is verified by F5 step 5's capture; B costs an extra board cycle on a sketch F5 rewrites, with no gate depending on it; A leaves the check unrunnable and the request's outcome unknown into V1.

Affects: the firmware's serial log format (no H, V or D task parses `CONN` lines); plan A task F5. Plan lines as of `210d18b`: F5 step 4 :831-837, step 5 :843, step 6 :851-854. Applied in `b100b5b`.

Cost if wrong: M1's request outcome stays unobserved until F5; nothing is flashed until F5 runs.

Ruling as Hong under H9 (away mode, opus-xhigh adjudicated in A-F3-R); undo: `git revert b100b5b`, which conflicts at F5 step 5 :846-849 (plan lines as of `4d21160`) only because `eded7b0` rewrapped the three lines above (:846-848); resolve it by keeping the current wrap and restoring "a `CONN` line" (or `git revert 210d18b eded7b0` first, which makes this revert clean, but also undoes A-F3-R-c).

## A-F3-R-c (2026-10-22): scripted capture monitors hold stdin open

A. When a capture monitor is run from a script, it gets a stdin that stays open for the whole capture, for example `timeout 60 "$CLI" monitor ... < <(sleep 65)`: Plan A task F2 step 6 (3) (the capture procedure F3 and F5 reuse), F5 step 5, and `firmware/README.md` capture procedure step 3. The other options were B (leave it to D1's README rewrite) and C (add a wrapper script).

Reason: the opus-xhigh adjudicator (A-F3-R) confirmed it in the arduino-cli 1.5.1 source (https://raw.githubusercontent.com/arduino/arduino-cli/v1.5.1/internal/cli/monitor/monitor.go): `feedback.InteractiveStreams()` returns `os.Stdin` for Text output with no TTY check (`internal/cli/feedback/terminal.go:33-41`), and the stdin-to-port `io.Copy` calls `cancel()` when it returns (`monitor.go:292-303`), so with stdin at EOF the monitor exits at once with exit 0 and an empty log. F3's first capture hit this; its `< <(sleep N)` fix worked for its other five captures. B lets F5, which runs before D1, lose its first capture the same way; C gold-plates it. H2 and V1-V3 never run the monitor, and a console stdin (Hong) is unaffected.

Affects: the capture procedure (plan A tasks F2, F3 by reuse, F5) and `firmware/README.md`. Plan lines as of `210d18b`: F2 step 6 (3) :551-555, F5 step 5 :840-842. `firmware/README.md` as of `210d18b`: capture procedure step 3 :76-80. Applied in `eded7b0` (plan) and `210d18b` (README).

Cost if wrong: none (harmless with a terminal).

Ruling as Hong under H9 (away mode, opus-xhigh adjudicated in A-F3-R); undo: `git revert 210d18b eded7b0`.

## PA-rev6c-D1 (2026-10-22): F5's queued RELEASE_ALLs carry a `counted` flag that decrements once at dequeue

A. In Plan A task F5, `onWrite` increments `abortRequested` first and then queues the RELEASE_ALL at the back with a `counted` flag set in its queue item, so the count is never below the number of queued RELEASE_ALLs. Every dequeued item with the flag decrements the count exactly once, whatever happens to it: it runs (DONE OK or NOTIFY_FAILED), `parse` rejects it, or it is dropped because its handle left the table. PA-rev6b-I-1's "never reset" and atomic change, and m9's "> 0" and "decrements", stay. PA-fix6e's QUEUE_FULL take-back stays as written and fits the flag rule: a RELEASE_ALL refused with QUEUE_FULL is never queued, so it is never dequeued, and the take-back is its one decrement. The other options (named by the orchestrator's ruling; no option block was written) were B (an executor reset of the count, which re-opens the reset races PA-rev6b-I-1 closed) and C (keep `0418d94`'s text, which leaves the stuck-until-reboot state below).

Reason: the opus-xhigh reviewer (PA-rev6c N-1) found that after `0418d94` the only decrement was "RELEASE_ALL then sends all-up, answers DONE OK and decrements the count", so a counted RELEASE_ALL that `loop()` drops because its link left the table before dequeue (confirmed from the plan text), or (inferred) that `parse` rejects or that ends NOTIFY_FAILED, is never decremented; with the reset gone, every later PLAY is ABORTED until reboot. The window is narrow (a link drop between enqueue and dequeue; V2 step 4 and H2's `bluetooth_off_on` drop links without rebooting the board) but the result is sticky. A closes the drop, reject and NOTIFY_FAILED paths with one invariant (each increment has exactly one decrement) and keeps I-1's "never reset".

Affects: the firmware concurrency model (F5's `onWrite`, queue item and executor); plan A task F5. Plan lines as of `94c376c`: F5 :795-798, :809-813. Applied in `b3b476b`; "at dequeue" placement added in `94c376c` (PA-rev6d m-2).

Cost if wrong: one F5 paragraph rewritten before F5 runs.

Ruling as Hong under H9 (away mode, opus-xhigh reviewed in PA-rev6c); undo: `git revert 94c376c b3b476b` (reverse-applies clean as of `94c376c`; `b3b476b` alone now conflicts). At a later HEAD the revert conflicts (A-final-R M-3.2: the plan diff fails at plan :807); remove only this ruling's clauses from F5 by hand. The ruling also lives in the firmware (`Helix_BLE.ino:125` `bool counted`, `:130` "Never reset", `:285`): remove the counted-flag lines there too, then reflash.

## A-F4-R-D1 (2026-10-22): `vectors.json` is ratified as F4 committed it (one top-level array)

A. Ratify `firmware/test/vectors.json` as committed in `ea2d641`: the file is one top-level JSON array of entries. A valid PLAY lists its steps as `expect.steps = [[delta_100us, mask], ...]`; the schedule-rule rejects and the pc_only entry carry the same pair shape as a top-level `"steps"`, and no entry has both. Plan A contract C1 now names the container (it gave only the two entry shapes). The other option was B (wrap the file as `{"version": 1, "vectors": [...]}`, which edits `vectors.json` and the C++ test's `main()`).

Reason: the opus-xhigh adjudicator (A-F4-R-D1) found that the file has exactly two readers, F4's C++ host test and P1's Python test, both versioned with it in git. The C++ test already requires the array (`firmware/test/test_protocol.cpp:245`). A file-level `version` would have no defined trigger beside the wire's per-frame `ver` (C1), which every vector already carries in `expect.ver`. Reversing later is about three test-side edits, with no wire change, no firmware reflash and no runtime code change.

Affects: the shared golden-vector file format and its two readers (`firmware/test/test_protocol.cpp`, `tests/input/test_protocol.py`); plan A contract C1. Plan lines as of `eabd3bb`: C1 :179-190. Applied in `ea2d641` (the file, by F4) and `eabd3bb` (C1 container clause).

Cost if wrong: wrap `vectors.json`, edit the C++ test's `main()` and P1's loader line, re-run both tests.

Ruling as Hong under H9 (away mode, opus-xhigh adjudicated in A-F4-R-D1); undo: no commit to revert for the ratification (it keeps `ea2d641` as is), so make the test-side edits: `vectors.json` line 1 `[` → `{"version": 1, "vectors": [` and line 41 `]` → `]}`; `test_protocol.cpp` `main()` :244-248 takes `root.at("vectors")`, checks it is a non-empty array and that `root.at("version").num == 1`, and loops over it; `tests/input/test_protocol.py:30` loads `["vectors"]` and asserts `version == 1`. Then re-run F4 step 2's g++ command (expect `ALL PASS`) and `$PY -m pytest -q tests/input/test_protocol.py`. For the C1 clause, `git revert eabd3bb` (reverse-applies clean as of this entry's commit), then write the wrapper into C1 instead. Line numbers are as of `67667fe`.

## A-F5-D1 (2026-10-22): F5's `onWrite` treats only a PING / RELEASE_ALL that `proto::parse` accepts as special

Question: how does `onWrite` recognise the two frames it handles itself (PING → PONG at once; RELEASE_ALL → count `abortRequested`, then queue)? Options: A. run the full `proto::parse` in `onWrite`; only a *valid* PING or RELEASE_ALL is special, and every other frame (any PLAY, and a PING or RELEASE_ALL that `proto::parse` rejects) is queued uncounted, and the executor rejects it if `proto::parse` does (as built in `747b708`; wording corrected per A-F5-R m-1). B. test the header bytes only (`len >= 2`, `ver == 1`, op 3 → PONG, op 2 → counted), so a malformed PING still gets a PONG and a malformed RELEASE_ALL still aborts the running PLAY before it is rejected.

Ruling: A (ruled as Hong while away, adjudicated by A-F5-adj).

Reason: the opus-xhigh adjudicator (A-F5-adj) found that A keeps one definition of validity (`proto::parse`, shared with F4 and P1 through `vectors.json`) and that a rejected frame never has a side effect. Under B a malformed PING is answered with a PONG, against C1's "the board rejects" and F4's reject-2 vector for that frame, and a malformed RELEASE_ALL aborts and is then REJECTED. B's only gain, a malformed RELEASE_ALL that still aborts, does not occur: the PC's encoders always emit the 4-byte header (`src/input/protocol.py:116-121`), every schedule is closed (≤ 10 s, ends all-up, PA-D5), and InputLayer's recovery uses the valid encoder (PA-adj1b-D1). Under A F5's "`parse` rejects it" branch for a counted item is unreachable but harmless, since the decrement happens at dequeue before `parse`.

Affects: the firmware's frame handling in `onWrite` (concurrency: which frames bypass the queue or raise the abort count); plan A task F5. Plan lines as of `4174c3e`: F5 :793, :801-806. Applied in `747b708` (firmware, `CommandCallbacks::onWrite`) and `2de8cae` (plan text, E1; rewrapped in `4174c3e`).

Cost if wrong: a malformed RELEASE_ALL, which only a PC bug or a foreign writer can produce, does not pre-empt; the running hold ends on its own within ≤ 10 s, all-up. No stuck key.

Undo: in `CommandCallbacks::onWrite` (`firmware/Helix_BLE/Helix_BLE.ino:178-186` as of `5a779b6`), keep the `proto::parse` call (it still fills `cmd.seq` when `len >= 4`, `protocol.h:48`) and replace the two `valid && cmd.op == proto::PING` / `proto::RELEASE_ALL` tests with header tests: `item.len >= 2 && item.bytes[0] == proto::VER && item.bytes[1] == proto::PING` (and `proto::RELEASE_ALL`). Then compile, flash and check the serial log per F5 step 5. For the plan text, restore F5 :793 and :801-806 to their `79ba1b2` wording (`git show 79ba1b2:docs/superpowers/plans/2026-10-21-p1a-input.md`, :789, :797-800); `git revert 4174c3e 2de8cae` undoes all four F5 rulings' plan text at once.

## A-F5-D2 (2026-10-22): F5 sends HID reports with notify()'s two NimBLE calls, made directly, so DONE carries the rc

Question: how does the HID report-send helper send, given C1 says a DONE NOTIFY_FAILED carries "`rc` = NimBLE code" but `NimBLECharacteristic::notify()` returns only a bool? Options: A. call `ble_hs_mbuf_from_flat(report, 8)` (NULL → `BLE_HS_ENOMEM`) and `ble_gattc_notify_custom(connHandle, inputReport->getHandle(), om)` directly and return that rc (as built in `747b708`). B. `return inputReport->notify(report, 8, handle) ? 0 : -1;`, so every failure is −1.

Ruling: A (ruled as Hong while away, adjudicated by A-F5-adj). This amends PA-D4's send call (decisions.md:166, "send with `notify(report, 8, connHandle)`"): the same NimBLE calls `notify()` makes, made directly so DONE carries the rc. PA-D4's substance stands: the library's `sendReport` is bypassed and each report goes out as a direct per-handle notify.

Reason: the opus-xhigh adjudicator (A-F5-adj) found that `notify(value, len, handle)` → `sendValue`'s single-peer custom branch makes exactly these two calls (NimBLE-Arduino 2.5.0, `NimBLECharacteristic.cpp:275-293`) but on failure only logs the rc and returns false (`:322-325`), and the log is compiled out at our FQBN's default debug level. So only A meets C1's "`rc` = NimBLE code" (C1 :153, :160-161); B would make ENOMEM, ENOTCONN and HCI errors all −1, the same as "lookup failed". Plan F5's old "returning false with `rc` on failure" misdescribed `notify()`. The rc is diagnostic only (the PC decodes it into `Done.rc` and branches on nothing), so A costs nothing on the PC.

Affects: the firmware send path (`sendKeyReport`) and the meaning of DONE's `rc` field; amends PA-D4; plan A contract C1 and task F5. Plan lines as of `4174c3e`: C1 :153, F5 :756, :773-781, :815. Applied in `747b708` (firmware, `sendKeyReport`) and `2de8cae` (plan text, E2a-E2d).

Cost if wrong: about 5 lines of low-level NimBLE C API to keep in step with the library: a later NimBLE-Arduino that changes `sendValue` (for example, adds a check) is not inherited. Low risk while the library stays 2.5.0.

Undo: replace the body of `sendKeyReport` (`firmware/Helix_BLE/Helix_BLE.ino:147-154` as of `5a779b6`) with `if (inputReport == nullptr) return -1; uint8_t report[8]; proto::mask_to_report(mask, report); return inputReport->notify(report, 8, handle) ? 0 : -1;`, then compile, flash and check the serial log per F5 step 5. In the plan, C1 :153 and :160-161 then say "`rc` = −1 (notify() exposes no NimBLE code)", and F5 :756, :773-781, :815 go back to their `79ba1b2` wording (:756, :773-775, :809); `git revert 4174c3e 2de8cae` undoes all four F5 rulings' plan text at once.

## A-F5-D3 (2026-10-22): F5's per-command serial log lines are always on, printed after the command's last notification

Question: plan F5 step 5 asked for "`ACCEPTED`/`DONE` lines per command at debug level"; are these lines always on, and in what form? Options: A. always on, in the format as built in `747b708`, printed only after the command's last notification and never inside a schedule or in `onWrite`: `ACCEPTED seq=%u t0_us=%lu` with `DONE seq=%u status=%u …` or `DROP seq=%u disconnected after n=%u`; `REJECTED seq=%u reason=%u`; `DONE seq=%u status=3 n=0 (queued)`; `DONE seq=%u release_all status=%u rc=%d`; plus the connect-time `PEERS count=%u` / `WARN PEERS count=%u`. B. the per-command lines behind a compile-time sketch flag (off by default).

Ruling: A (ruled as Hong while away, adjudicated by A-F5-adj), on condition that A-F5-adj-D1 → A holds (it does: `5a779b6`). Had A-F5-adj-D1 been B, D3 would have switched to B with the flag off by default.

Reason: the opus-xhigh adjudicator (A-F5-adj) found that no task after F5 opens a serial monitor or parses these lines (F5 step 6 checks notifications on the PC; A-F3-R-b already records that no H, V or D task parses `CONN` lines), so the format is free, and they are the only on-board record of each command. "At debug level" read as the core's `log_d` would be compiled out by our FQBN (DebugLevel none), so step 5 could not see them; read as a sketch flag it is B. Printing after the last notification keeps them off the timing path: all-up and DONE go out before the print.

Affects: the firmware's serial log format (`runPlay`, `runReleaseAll`, `loop()`, `onConnect`); plan A task F5. Plan lines as of `4174c3e`: F5 :782-783 (PEERS), :866-869 (step 5). Applied in `747b708` (firmware) and `2de8cae` (plan text, E3).

Cost if wrong: if serial writes could block, the loop task would stall ≈ 2 s after each command once a monitor had been closed in the current boot (latency, QUEUE_FULL, F5 step 6's 100 ms check). No stuck key. That hazard is covered by A-F5-adj-D1.

Undo: delete the per-command `Serial.printf` calls in `firmware/Helix_BLE/Helix_BLE.ino` (as of `5a779b6`: `runPlay` :349, :360, :365, :371; `runReleaseAll` :381; `loop()` :406, :413, :418; each with its continuation line), or wrap each in `#if HELIX_CMD_LOG` after `#ifndef HELIX_CMD_LOG` / `#define HELIX_CMD_LOG 0` / `#endif`; leave `onConnect`'s `PEERS` line, since F5 requires the count to be logged. Then compile, flash and check the serial log per F5 step 5. For the plan text, restore F5 :866-869 to `79ba1b2`'s :853 and drop the PEERS clause at :782-783; `git revert 4174c3e 2de8cae` undoes all four F5 rulings' plan text at once.

## A-F5-adj-D1 (2026-10-22): F5's firmware makes serial log writes non-blocking (`Serial.setTxTimeoutMs(0)`)

Question: should the firmware make serial writes non-blocking, given that the ESP32-S3's USB serial (HWCDC, esp32 core 3.3.10) can block each write ≈ 2 s once a host has read the port and then stopped reading? Options: A. one line after `Serial.begin(115200)`: `Serial.setTxTimeoutMs(0);` (`HWCDC.cpp:504-506`), so a line that does not fit the TX ring is dropped instead of waited for. B. no code change; a procedure rule that a capture monitor must be reading (or the board freshly reset) during any command run.

Ruling: A (ruled as Hong while away; analysed and raised by A-F5-adj, which recommended verifying the stall first, then A). A-F5b's probe confirmed it before the fix was kept.

Reason: a log write must never stall the executor or `onWrite` in timing-critical firmware. With the default 100 ms timeout, `HWCDC::write` retries up to 20 × 100 ms while the host is still "plugged" but not reading (`HWCDC.cpp:582-603`) and leaves `connected` true, so every later line waits again. That would stall `loop()` ≈ 2 s per command (D3's lines) and the NimBLE host task ≈ 2 s per `CONN`/`SUB`/`DISC` line (`onWrite`, PONG, P4 `connect()`'s PING after a reconnect). B is fragile: every V/H soak and P4 `connect()` after a capture would inherit the stall. A-F5b's probe (no keystrokes; monitor opened and closed, then 15 RELEASE_ALLs with no monitor) measured write→DONE ≈ 30 ms × 8 then ≈ 1994-2010 ms × 7 on `747b708`, and 15/15 at 29.7-43.9 ms on `5a779b6`. `HWCDC.cpp` 3.3.10 :544, :590, :593-603, :625-627 confirm that at timeout 0 a write returns and drops the line if there is no room.

Affects: serial logging timing for every line the firmware prints (loop and NimBLE host tasks); plan A task F5. Plan lines as of `4174c3e`: F5 :853-859. Applied in `5a779b6` (firmware, `setup()`) and `2de8cae` (plan text).

Cost if wrong (no real hazard): one harmless line. Cost of the fix itself: a serial log line is dropped whole when no host reads, or when another task holds the serial lock at that moment (`HWCDC.cpp:544`; A-F5-R m-2), so captures keep a monitor open and reading for the whole run. Boot and connect lines past the first ≈ 256 bytes queued before the monitor opens are dropped newest-first (connected path, `HWCDC.cpp:586-604`, ring 256 B `:422`), so link state after a reset is read from a PING's PONG, not from `CONN` lines (A-F5-R I-1; plan F2 step 6, F3 step 7, F5 steps 4-6). `Serial.flush()` at timeout 0 discards pending output (`HWCDC.cpp:646-658`); the sketch never calls it.

Undo: `git revert 5a779b6` (it only adds the call and its comment in `setup()`, `firmware/Helix_BLE/Helix_BLE.ino:299-302`), then compile, flash and check the serial log per F5 step 5. For the plan text, delete F5 :853-859.

## A-P3-D1 (2026-10-22): `sequence` clips a hold against the next later press of the same key, adjacent or not

Question: C4 clipped a hold only when key *i+1* equals key *i*; should `actions.sequence` also clip against a later, non-adjacent press of the same key (for example `[RIGHT, LEFT, RIGHT]` with a first HOLD longer than both gaps)? Options: A. key *i*'s up is clipped to 15 ms before the down of the next later press of the same key, adjacent or not, and logged `hold_clipped` (as built in `b8791e8`, `src/input/actions.py:158`). B. adjacent only, as C4 was written; a HOLD that outlasts the gaps to a non-adjacent re-press makes the key go down while still down, so `sequence` returns `Failure` ("must change exactly one key"), which P5 maps to TOO_LONG.

Ruling: A (ruled as Hong while away, adjudicated by A-P3-R).

Reason: the opus-xhigh adjudicator (A-P3-R) found that A makes `sequence` valid by construction for 1-8 keys over every draw within `MODEL`'s bounds: downs are ≥ 60 ms apart, and each press of a key ends ≥ 15 ms before that key's next down, so no key goes down twice and every clipped hold stays above 0. At the bounds (stub HOLD 250 ms, GAP 60 ms), all 9 840 key lists of length 1-8 over 3 keys give A 0 Failures and B 8 934. With the real `MODEL` the two rarely differ (a repeat two keys later: A clips ≈ 1.7e-5 per pair, B fails ≈ 5.0e-6; three or more keys later, none in 1e8), but B would hand rare, mislabelled TOO_LONG Failures to Features B-D and fail V3's Delivery check in ≈ 0.16% of soaks. The 15 ms release margin C4 wants before an adjacent re-press applies equally to a non-adjacent one. Correction to A-P3's rationale: the plan's 1 000-seed property test passes under B too; only the stub tests tell them apart: test_sequence_clips_a_non_adjacent_repeat_too and, added for A-P3-R m3, test_sequences_over_three_keys_validate_with_every_hold_and_gap_at_its_extreme (`732f842`).

Affects: what `actions.sequence` schedules (departs from C4's text as first written); plan A contract C4 and task P3. Plan lines as of `e24945c`: C4 :330-334, P3 :960-965. No edit needed in H4 :1546-1550 ("no repeated key long enough to clip" stays true), V3 :1723 or D1 :1749-1752 (D1 diffs `MODEL` against C4's table, and the code matches C4's rule as edited). Applied in `dabc542` (code and tests, branch `p1a-input`; cherry-picked from `b8791e8` on `p1a-p2`; sweep test in `732f842` and the comment citing this entry in `0ed0c7a`) and `e24945c` (plan text).

Cost if wrong: ≈ 0.005 extra `hold_clipped` samples per 10 000-command soak compared with C4's literal text. KS is unchanged (it uses the unclipped `chosen_ms`), and H4 gates `hold_clipped` separately either way.

Undo: in `src/input/actions.py` replace the `nxt = next(...)` line (:158 at `dabc542`, :164 at `0ed0c7a`) with `nxt = n + 1 if n + 1 < len(keys) and keys[n + 1] == key else None` and delete the comment above it that cites A-P3-D1 (:161-163 at `0ed0c7a`); delete or invert `test_sequence_clips_a_non_adjacent_repeat_too` (`tests/input/test_actions.py`, :313-319 at `dabc542`) and `test_sequences_over_three_keys_validate_with_every_hold_and_gap_at_its_extreme` (`732f842`; it fails under B). Not `git revert dabc542`, which removes all of P3. Not `git revert 732f842` alone either: `732f842` (= `aed5141` on `p1a-p2`) also adds the three A-P3-R m1 walk tests, which are red until `0ed0c7a` (= `48e4f80`), so reverting it alone silently drops them and leaves `MAX_WALK_INTENDED_MS` with no boundary test, and `git revert 0ed0c7a` alone leaves red tests; revert `732f842` and `0ed0c7a` together or not at all, and `git bisect skip 732f842` (A-P4-R Q4). Then revert the docs: `git revert e24945c`, and revert the commit that added this entry (`git log --format=%h -S 'A-P3-D1 (2026-10-22)' -- docs/decisions.md`).

## A-H1-D2 (2026-10-22): the listener's `start()` raises `OSError` on a setup failure instead of returning a Result

Question: when the keyboard hook or the Tk sink window does not come up within 5 s, does `start(out_dir, block=True)` raise or return a `Result`? Options: A. raise `OSError` after `stop()` has cleaned up, keeping the plan's signature `start(...) -> Listener` (as built in `cd794a4`). B. return `Result[Listener, str]`.

Ruling: A (ruled [spec-text] by the orchestrator from the plan's own signature); confirmed KEEP by A-H1-R.

Reason: the plan fixes `start(out_dir, block=True) -> Listener` (A-PF I5), and H2, V1 and V2 are written to it. A `Result` return would change three downstream tasks' interface for no gain, since none of them can recover from "no hook / no sink window"; each stops. Hard rule 5 and CLAUDE.md "Error handling" scope the Result pattern to controller, parser and graph code; `listener.py` is harness start-up code, where an `OSError` for a Win32 setup failure is the ordinary idiom. `_start` calls `stop()` before raising (A-H1-R: `listener.py:293,296` at `cd794a4`). The three start-up waits (writer, hook, sink) run one after the other, each within 5 s, so the worst case is about 15 s and about 10 s in practice (see Updated below; first written as "two 5 s waits, about 10 s", A-H1-R m4).

Affects: `src/input/harness/listener.py` `start()`; plan A task H1, public API bullet (unchanged: it already gives `-> Listener`). Applied in `cd794a4`.

Updated (2026-10-22, A-H1-fix and A-H1-fix2, reviewed in A-H1-fix-R and A-H1-fix2-R): the decision (raise `OSError`, signature unchanged) stands; the start-up behaviour behind it grew. `start()` now starts the keys writer thread alone and waits for it to be ready (5 s), and only then starts the hook thread and the Tk sink thread, each of which must be ready within 5 s (`0ab5d27`, `9230a6c`, `bea9920`). That makes three sequential 5 s waits: about 15 s worst case, reached only if each stage succeeds late, and about 10 s in practice (a wait only adds time when the stage before it succeeded late; A-H1-fix-R Q3, A-H1-fix2-R). A failure of any part (the `keys.jsonl` open, the hook install, the sink window) raises `OSError` naming the part, after `stop()` has cleaned up: `cannot open <path>: ...` or `writer thread did not start`; `SetWindowsHookExW failed, GetLastError=<n>` or `hook thread did not start`; `test sink window failed: <repr>` or `test sink window did not come up` (`listener.py:256,314,241,321,283,325`; A-H1-fix m3 and m4, A-H1-fix2). The Question's "hook or sink window ... within 5 s" therefore also covers the writer.

Cost if wrong: about 5 lines in `start()` plus a plan edit to H1's API bullet and its H2/V1/V2 callers.

Undo: make `start()` in `src/input/harness/listener.py` return `Success(listener)` / `Failure(message)` instead of raising, update H1's public API bullet and the H2/V1/V2 call sites in the plan, and revert the commit that added this entry (`git log --format=%h -S 'A-H1-D2 (2026-10-22)' -- docs/decisions.md`).

## A-P4-D1 (2026-10-22): each `InputError` value is its member name

Question: are `InputError`'s values the member names (`"LINK_LOST"`) or `auto()` lower case (`"link_lost"`)? C5's `link_faults.jsonl` writes "its InputError" and H3 counts `LINK_LOST` lines. Options: A. value == name (as built, pinned by a test). B. `auto()` lower case; H2 and H3 write and read `.name`.

Ruling: A (ruled as Hong while away, adjudicated by A-P4-R, opus-xhigh); confirmed KEEP by A-P4-R2.

Reason: JSON writes a StrEnum's value. Under `auto()` a writer that forgets `.name` writes `"link_lost"`, so H3's `LINK_LOST` drop count reads 0 and the Drops gate passes falsely. Under A, `str()` and `json` both give `"LINK_LOST"`. A-P4-R2 found that the C5 and H3 case tables all use upper-case literals.

Affects: `InputError` in `src/input/link.py` (C3 interface) and every C5 field that carries an InputError (`link_faults.jsonl` and `faults.jsonl` `error`). Applied in `7df1a43` on `p1a-input` (cherry-pick of `a7b58ad` from `p1a-p2`); plan text in `82f9c7d` (C3's `InputError` line).

Cost if wrong: none known.

Undo: `git revert 7df1a43` (still clean: the fix round does not touch the enum, A-P4-R2 Q1), then restore C3's `InputError` line from `82f9c7d^`.

## A-P4-D2 (2026-10-22): a failed public `ping()` is returned and never ends the session

Question: when a public `ping()` fails while the session is live, does `BleLink` only return the error, or end the session? Options: A. return `WRITE_FAILED` / `ACK_TIMEOUT` with the session left live (as first built in `7c0de44`). A′. A, plus A-P4-R I1's `ACK_TIMEOUT` `faults()` entry (A-P4-R-D1). B. a `LINK_LOST` fault that ends the session.

Ruling: A′ (ruled as Hong while away, adjudicated by A-P4-R); confirmed KEEP by A-P4-R2.

Reason: the keepalive already detects a dead link within about 0.75 s (amended by A-FCdbg-D1, below: on the soft-miss path, a board that answers writes but sends no PONG, it is ~1.0 s after the first unanswered PING), and ending the session from `ping()` would add a third session-ending path the plan lacks (only a keepalive miss and `disconnected_callback` end a session; P4 Session state). V2 step 3 is the only Phase 1 caller and writes no `link_faults.jsonl`, so the entry has no Phase 1 consumer and no gate effect. No session end does not mean no fault: a missed PONG is an `ACK_TIMEOUT` fault entry, while a write that raises is `WRITE_FAILED` with no entry. One asymmetry is by design (A-P4-R2 Q2): a `ping()` whose write blocks for the whole 0.5 s is `ACK_TIMEOUT` (a fault), because `_ping_cmd` has one limit over write + PONG, while a `play()` / `release_all()` whose write blocks is `WRITE_FAILED` (not a fault). A `ping()` blocked in its write is not cut short at session end; it returns `LINK_LOST` / `NOT_CONNECTED` within its own 0.5 s (A-P4-R2 Q4).

Affects: `BleLink.ping()` (C3); plan A task P4, `ping()` bullet and the Keepalive bullet's last sentence. Applied in `7c0de44` (`_public_ping`, cherry-pick of `1ce4acb`), the fault entry in `d15c32b` (= `ad50320`), pinned by `2691a42` (= `4ebb922`); plan text in `82f9c7d`.

Cost if wrong: a probe `ping()` cannot end a session.

Undo: in `src/input/link.py` `_public_ping`, on a Failure while the same session is live call `_end_session(LINK_LOST)` instead of returning it; invert `test_ping_ack_timeout_is_a_fault_and_the_session_stays_live` and `test_ping_write_failure_is_returned_and_the_session_stays_live` (`tests/input/test_link.py`, `2691a42`); then revert the `ping()` bullet and C3's `ping` comment from `82f9c7d`.

## A-P4-R-D1 (2026-10-22): `faults()` records `ACK_TIMEOUT` beside `LINK_LOST`

Question: which failures besides `LINK_LOST` are `faults()` entries? Options: A. `ACK_TIMEOUT` only; `WRITE_FAILED` and `NOTIFY_FAILED` are not faults. B. also `WRITE_FAILED` and `NOTIFY_FAILED`.

Ruling: A (ruled as Hong while away; raised and analysed by A-P4-R, opus-xhigh, with no second adjudicator, as with A-F5-adj-D1); confirmed KEEP by A-P4-R2.

Reason: `ACK_TIMEOUT` is the one the plan names (P4 Notification dispatch: "fails with `ACK_TIMEOUT`, which is a fault"), and H3 treats non-`LINK_LOST` lines as faults but not drops. A-P4-R2 checked C5 `faults.jsonl`: `fault_seen_ns` can now be an `ACK_TIMEOUT` entry, but each one is appended in the same loop step as the Failure it accompanies (or just before `ping()` returns it), so `reported_ns` and `error` are what `failure_ns` alone gives. Under B, `WRITE_FAILED` / `NOTIFY_FAILED` entries would add nothing a command row does not already carry with its seq. The entries are a missed ACCEPTED or DONE, a DONE status on the `ACK_TIMEOUT` path, and a public `ping()`'s `ACK_TIMEOUT` while its session is live; a keepalive miss adds only its `LINK_LOST`, and ready-wait PINGs and `close()` add none.

Affects: what `BleLink.faults()` holds (C3); C5 `link_faults.jsonl` and `faults.jsonl` `fault_seen_ns`; H3's Connection check. Applied in `d15c32b` (= `ad50320`); plan text in `82f9c7d` (C3 `faults()` comment, C5 `fault_seen_ns` and `link_faults.jsonl`, P4 fault list).

Cost if wrong: H3 undercounts write failures.

Undo: add `WRITE_FAILED` and `NOTIFY_FAILED` to the fault append in `src/input/link.py` (the `_fault` callers, as of `e95df27`), flip `test_other_failures_are_not_faults`, and reword the plan lines listed under Affects.

## A-H1b-D1 (2026-10-22): for H1 step 3, Hong clicks the sink once and the board script runs unattended

Question: how does the sink window become foreground for H1 step 3's board key check, after A-H1b saw `SetForegroundWindow` refused twice while another app was in front? Options: A. Hong's fallback: he clicks the sink and presses the keys himself (no board evidence). B. Hong clicks the sink once; the board script waits for it, then runs unattended through the same gates (one click, full board evidence, throwaway script change only). C. `AttachThreadInput` / `SPI_SETFOREGROUNDLOCKTIMEOUT` (touches Hong's window input state or a system setting).

Ruling: B (Hong's decision; he did not name an option, so it was read as the recommended one, and he was told so).

Reason: one click gives the full board evidence (real HID input, `injected=false`) with only a gitignored script changed; C is barred by H1 without a decision. A-H1c ran it and H1 step 3 passed: 94/94 lines `injected=false`, arrows, E and Space down and up, a 3 s LEFT hold gave repeat lines from 507 ms, and left Alt arrived as VK 0xA4 only.

Affects: how H1 step 3 was run (gitignored `scripts/h1b_live.py`, `scripts/h1c_listener.py`); no code or plan change.

Cost if wrong: one more click from Hong.

Undo: none needed for code or plan; option A (Hong presses the keys himself) stays available to redo step 3.

## A-H1c-D1 (2026-10-22): H1 step 3's pass-through check uses one Shift press from Hong with the listener blocking

Question: the board cannot send Shift (PLAY's mask has seven keys), so how does H1 step 3 show a key that is not blocked going on through `CallNextHookEx` (A-H1-R item 5)? Options: A. shift-human: the listener blocks as the plan says, the board taps LEFT as a control, Hong presses Shift once, and a record-only observer hook installed before the listener logs a key only if the listener passed it on. B. noblock: the listener runs with `--no-block` and the board taps LEFT. C. add Shift to the firmware (out of scope).

Ruling: A (ruled [local] by the orchestrator: a test method inside the brief that keeps the plan's blocking mode; Hong was present and agreed to help).

Reason: A-H1c's second run passed. The observer saw Hong's left Shift (VK 0xA0, in both logs, `injected=false`) but not the board's LEFT (blocked), which shows both the `CallNextHookEx` path and blocking.

Affects: H1 step 3 evidence only (gitignored `scripts/h1c_observer.py` and `data/`); no code or plan change.

Cost if wrong: one more press from Hong.

Undo: none needed; option B (a `--no-block` run) stays available as a control.

## A-D3-LF (2026-10-22): every soak log file ends its lines in LF

Question: C5 had the LF rule for `keys.jsonl` only (A-H1-fix-R Q1). Should every soak log file H2 writes end lines in LF?

Ruling: A, every soak `.jsonl` file (`commands`, `pongs`, `faults`, `keys`, as built also `link_faults`) ends lines in LF, not only `keys.jsonl` (Hong's decision).

Reason: one line terminator across the run dir; `json.loads` and text-mode iteration read either ending, so H3 and H4 need no change. A-H2-2a built it: `session.json` is LF too, and `link.log` stays outside the rule (not JSONL; `logging.FileHandler`).

Affects: plan A C5 header (the rule now sits above the file list; the `keys.jsonl` bullet points to it); `src/input/harness/soak.py` writers (`newline="\n"`, `RunWriter` and `session.json`).

Cost if wrong: none; analysis reads both endings.

Undo: drop `newline="\n"` from the `RunWriter` opens (`soak.py`) and reword the C5 header sentence.

Pointer: ledger.md:516; plan text in `83e5e93`.

## A-H2-D1 (2026-10-22): the fault injectors take an optional `FaultStamps` out-parameter

Question: `reset_board()` and `bluetooth_off_on()` are specified as `-> Result[None, str]`, but the driver needs their stamps (launch, injected). How do the stamps reach it? Option A: keep the return type and add an optional `stamps: FaultStamps` out-parameter that is filled on every path, failures included. (The other options are not recorded in the ledger.)

Ruling: A (Hong's decision).

Reason: the return type stays as the plan wrote it, a bare call from V2 still works, and the times exist even when the injector returns a `Failure` (C5 `faults.jsonl` writes an entry for every attempt).

Affects: `src/input/harness/faults.py` signatures `reset_board(stamps=None, *, on_line=None)` and `bluetooth_off_on(seconds, stamps=None, ...)`; plan A H2 (new "Signatures" paragraph). `on_line` is a later, backward-compatible addition for the G4 check (A-H2-D3). Applied in `cd8199e`; plan text in `83e5e93`.

Cost if wrong: a signature change in `faults.py` and its callers.

Undo: revert `cd8199e`'s signatures (return the stamps another way, e.g. a result object) and update the driver.

Pointer: ledger.md:521 (raised in A-H2, notes/A-H2.md).

## H10 (2026-10-22): Hong is asked only about spec deviations and human-only tasks

Hong's words, as the ledger records them: ask Hong only for (1) implementing something that deviates from the spec, and (2) a non-subjective task the orchestrator and agents cannot do (for example clicking the sink window). Implementation detail that leaves the spec intact (signatures, log formats, internal mechanics) is decided by the orchestrator as a `Ruling [local]`, or, if it touches an interface, an adjudicator first is not required. Push, merge to master and deletes still need his yes.

Scope: the whole Helix project (plans A to D, every orchestrator, Hong present or away); first given for plan A's run and then extended to all of it by Hong, relayed by retired orchestrator 17 in a cross-session message. It replaces agent-rules section 3 for him: agents raise `DECISION NEEDED` only for a spec deviation or a human-only action, and otherwise record `[fyi] ruled: ...`.

Reason: A-D3-LF and A-H2-D1 were implementation detail he did not need to see.

Affects: how agents and orchestrators route decisions for the rest of the build. Entries below that are not Hong's are marked as orchestrator rulings.

Cost if wrong: a decision that mattered to him is made without him; each such ruling is logged with its cost and undo.

Undo: Hong says so; restore agent-rules section 3.

Note: H8 and H9 let the orchestrator push and merge as the specs' Shipping rules allow, without asking; the ledger line for this rule says push, merge to master and deletes still need his yes. The ledger does not say how the two relate, and this entry records only what it says.

Pointer: ledger.md:527 (first statement), ledger.md:548 (whole project); saved to project memory `maximum-autonomy.md`.

## H11 (2026-10-22): the `helix-h34` worktree and `p1a-h34` branch are deleted after the merge

Ruling: after `p1a-h34` was merged into `p1a-input` and the suite passed, delete the worktree `C:/Users/Qing_programming/Desktop/helix-h34` and the branch `p1a-h34` (Hong said yes; a delete needs his yes under H10).

Done: the merge is `117d138`; `git worktree list` and `git branch --list 'p1a*'` no longer show `helix-h34` or `p1a-h34` (checked 2026-10-22 by the docs unit A-H2-docs).

Cost if wrong: none; the commits are on `p1a-input`.

Pointer: ledger.md:564.

## H12 (2026-10-22): helper worktrees and branches are deleted after the merge, and merged into a local feature branch, without asking

Hong's words, as the ledger records them: yes, delete the `helix-docs`, `helix-v1` and `helix-v2` worktrees and branches after each is merged and the suite passes; and, as a standing rule for the whole project, deleting orchestrator-created helper worktrees and branches after their work is merged into the local feature branch and the suite passes, and merging them into a local feature branch (not `master`), need no ask ("another example of something you dont need my permission for").

Scope: the whole Helix project. It narrows the H10 line that deletes still need his yes: that line no longer applies to orchestrator-created helper worktrees and branches that meet both conditions (merged into the local feature branch, suite green). A merge to `master`, a push and a delete of anything else still follow H8 to H10.

Reason: these deletes lose nothing; the commits stay on the feature branch (see H11).

Affects: how the orchestrator closes helper worktrees such as `helix-docs`, `helix-v1` and `helix-v2` and their branches.

Cost if wrong: none while the merge is on the feature branch; an unmerged branch would lose work, so the merge and a green suite are checked first.

Undo: Hong says so; ask again before deleting.

Pointer: ledger.md:581; saved to project memory `maximum-autonomy.md`.

## A-H3-D1 (2026-10-22): commands.jsonl writes `"error": null` and `status` `"OK"` on a success row

Question: C5 names `status` and `error` but not what a Success row holds. Which value marks success?

Ruling [local] (orchestrator ruling under H10, not Hong's): a Success row has `error` `null` and `status` `"OK"`. The id was given by the docs unit; the ledger line has none.

Reason: H3 (`analyze.py`) and H4 (`realism.py`) count a command as a success iff `error` is null, so this matches the readers. `status` `"OK"` was chosen by A-H2-2a (the plan does not fix it) and is only a label; the readers key off `error`. A-H3-R Q1 and A-H2-R Q5 confirmed it (KEEP).

Affects: plan A C5 `commands.jsonl`; `soak.py` `command_row` (`8d69417`). Plan text in `83e5e93`.

Cost if wrong: one field change in H2 or H3.

Undo: change `command_row` and the reader's success test together.

Pointer: ledger.md:518 (ruling), A-H3 DONE (the [review] it answers), notes/A-H2.md (2a, "status OK").

## A-H2-D2 (2026-10-22): the PnP fallback is two PowerShell processes, the off period timed in Python

Question: how does the `Disable-PnpDevice`/`Enable-PnpDevice` fallback of `bluetooth_off_on` keep the adapter off for `seconds`? The plan says only "fall back to Disable-PnpDevice/Enable-PnpDevice".

Ruling [local] (orchestrator ruling under H10, not Hong's): A, two PowerShell processes, one per step; the off period is timed in Python, and each step's failure is reported separately. A-H2-R confirmed it (KEEP).

Reason: internal to the fallback path, reversible, and the plan states neither.

Affects: `src/input/harness/faults.py` (`_pnp_off_cmd`, `_pnp_on_cmd`, `PNP_ON_DONE`; `cd8199e`); plan A H2 `faults.py` bullet.

Cost if wrong: about 250 ms of PowerShell start-up per call and a small rewrite.

Undo: drop the `PNP_ON` step and run one process that disables, sleeps and enables.

Pointer: ledger.md:522.

## A-H2-2a-D1 (2026-10-22): `commands.jsonl` `bytes` is the hex of the PLAY command

Question: C5 lists a `bytes` field and nothing defines it. What does it hold? Options: A. the hex of the PLAY command re-encoded with `encode_play(report.seq, steps)`. B. a byte count.

Ruling [local] (orchestrator ruling under H10, not Hong's): A.

Reason: nothing reads it, and it carries the exact bytes sent (`encode_play` is pure, so the re-encoding equals what the link wrote; A-H2-R checked `link.py:465` and `:480`).

Affects: plan A C5 `commands.jsonl`; `soak.py` `command_row` (`8d69417`).

Cost if wrong: none.

Undo: change one line in `command_row`.

Pointer: ledger.md:529.

## A-H2-D3 (2026-10-22): the soak starts by waiting for the operator's click; G4 is a standalone script run after the dry run

Question: the agent-launched sink window never gets the foreground, so how does a soak start? And how is G4 (is esptool's reset line read before it exits) checked "during the dry run"?

Ruling [local] (orchestrator ruling under H10, not Hong's; the id was given by the docs unit): the CLI waits for the operator to click the sink, before `connect()`, for at most 120 s, polling `GetForegroundWindow()` every 0.25 s after one bring-to-foreground call; on a timeout it aborts with exit 1, writes `session.json` and never opens BLE. The MapleStory 60 s check also runs during the wait. G4 is a standalone script, `g4_check.py`, run in the same sitting right after the dry run: it judges the spread from the first esptool line to the reset line (500 ms or more is `READ_BEFORE_EXIT`) and prints, without judging, exit minus `injected_ns`.

Reason: neither the spec nor the plan fixes the start behaviour (so no spec deviation); starting before `connect()` means a timeout leaves the board untouched; the click is the human-only task H10 allows. The bot's keys stay blocked through the wait because the listener reads `block` once at start, and shortening that would be an H1 change, out of scope. A-H2-3b's dry run used it: Hong's click at 48.3 s, 200 commands OK; G4 exit 0 (spread 2166 ms, unbuffered).

Affects: `soak.py` (`wait_for_click`, `START_WAIT_S`), `faults.reset_board(on_line=...)`, new `src/input/harness/g4_check.py`, plan A H2 (new "Start" bullet, step 3). Applied in `ff2f72d`; plan text in `83e5e93`. Plan step 3's "stop BLOCKED" is now only for a timeout (A-H2-R M4).

Cost if wrong: a small CLI revert.

Undo: revert `ff2f72d`'s `wait_for_click` call in `run_soak` (the first command then waits up to 10 s for focus, as in `daa3270`).

Pointer: ledger.md:545 (ruling), ledger.md:549 (A-H2-3a), ledger.md:556 (M4).

## A-H2-R-D1 (2026-10-22): session.json `connected_ns`; H3 ignores key events before it

Question: the operator's keys pressed before `connect()` (for example during the click wait) count as Delivery extras in H3. How are they kept out without weakening Delivery?

Ruling [local] (orchestrator ruling under H10, not Hong's): A. H2 writes `connected_ns` to `session.json`, a `perf_counter_ns` stamp taken right after the first `connect()` succeeds (null if it never does). H3 counts a transition before the first command's window as an extra only if `qpc_ns >= connected_ns`; earlier ones are ignored and printed as `ignored before connect: N`. A run without `connected_ns` keeps the old behaviour. A-H2-fix-R confirmed it (KEEP; the listener runs in the soak process, so one clock).

Reason: the board sends nothing before `connect()`, so nothing before it can be a delivery error. It is a C5 field plus one `analyze.py` rule, the spec's Delivery target is unchanged, and it is the smallest change.

Affects: C5 `session.json`; H2 (`RunWriter.record_connect`, `482e0bb`); H3 (`load_run`, `DeliveryMatch.pre_connect`, `c1974d2`); plan A C5, H2, H3. Plan text in `83e5e93`.

Cost if wrong: one field and one H3 rule reverted.

Undo: revert `482e0bb` and `c1974d2`; pre-connect keys count as extras again, so the operator must not touch the keyboard before the connect.

Pointer: ledger.md:557.

## A-H2-R-D2 (2026-10-22): a soak aborts on a link loss outside an injected fault

Question: when a command returns `NOT_CONNECTED` or `LINK_LOST` and no fault was injected, nothing reconnects, so the run goes on failing (with `--min-hours` it never ends). What should the driver do?

Ruling [local] (orchestrator ruling under H10, not Hong's): A. Abort through the normal abort path (`release_all()`, `session.json`, exit 1) when a command returns `NOT_CONNECTED` or `LINK_LOST` outside the one command that follows an injected fault. Only the command at a `--faults` index is exempt (also when its injector failed); a loss on a later command after the reconnect aborts. A-H2-fix-R confirmed it (KEEP).

Reason: a drop already fails the Connection target (spec:L61) and the abort removes the hang. It is a plan gap, not a spec deviation (A-H2-R).

Affects: `soak.py` `run_soak` (`LINK_LOSS`; `5b58b9f`); plan A H2. Plan text in `83e5e93`.

Cost if wrong: about 10 lines.

Undo: delete the new `if` block in `run_soak`.

Pointer: ledger.md:558; ledger.md:567 (the fix).

## A-H2-fix-R-D1 (2026-10-22): after N commands, abort after K = 10 consecutive failed commands

Question: a `--min-hours` run that has had one success and then only non-link failures (`ACK_TIMEOUT`, `REJECTED`) never ends (the span check never completes). Options: A. let it run. B. once `seq >= N`, abort via the normal abort path (exit 1) after K = 10 consecutive failed commands.

Ruling [local] (orchestrator ruling under H10, not Hong's): B. Implemented in A-H2-fix3 (`505dd64`): the abort fires only while the `--min-hours` span is still short (`span_short()`), the streak counts failures from before N, and K is `MAX_CONSECUTIVE_FAILURES` in `soak.py:79`; a run without `--min-hours`, or whose span is already met, still ends at N with exit 0.

Reason: such a run can no longer grow its connected span and has already failed its target; it matches A-H2-R-D2 and Q3 (a plan gap, not a spec deviation, per the reviewer).

Affects: `soak.py` `run_soak`; plan A H2 (same bullet as A-H2-R-D2). Plan text in `83e5e93`.

Cost if wrong: about 10 lines.

Undo: remove the consecutive-failure counter in `run_soak`.

Pointer: ledger.md:572; ledger.md:573 (A-H2-fix3).

## A-H2-fix-R-m2 (2026-10-22): keys.jsonl starts fresh on a reused `--out` dir, like link.log

Question: the listener opens `keys.jsonl` in append mode, so a reused `--out` dir mixes runs (A-H2-fix-R m2). Should each run start with an empty file?

Ruling [local] (orchestrator ruling under H10, not Hong's): yes, fresh per run, as A-H2-R M1 did for `link.log` (`mode="w"`); implemented in A-H2-fix3 (item 2) in `RunWriter.open`, not in the listener. Waived if plan or spec text said append; the plan says nothing either way.

Reason: stale keys from an earlier run would corrupt H3's Delivery analysis (with A-H2-R-D1 a same-boot earlier run's keys fall before `connected_ns` and are only reported as ignored, but after a reboot the QPC clock restarts and old keys could land inside new windows; A-H2-fix-R m2).

Affects: `src/input/harness/soak.py` (`RunWriter.open` truncates `keys.jsonl` at `:216`, before `start_sink`); `listener.py` is unchanged and still appends (`:254`), so the standalone listener CLI keeps appending; plan A C5 `keys.jsonl` and `link.log` bullets. Plan text in `83e5e93`.

Cost if wrong: one small revert.

Undo: remove the truncate in `RunWriter.open` (`soak.py:216`) and its test.

Pointer: ledger.md:576.

## A-V2stamp-C1 (2026-10-22): the `reset_board` stamp stays at esptool's reset line; analyze adds a reset worst-case gate measured from the last PONG before the first report

Question: A-V2stamp found the spec silent on the fault moment (spec:57) and plan C5 stamping `reset_board` at esptool's reset line. On v2-faults-3, PONGs stopped 476–582 ms after esptool started, `LINK_LOST` came 967–980 ms later, and the stamp ~1.0 s after that, so the time from the fault to the stamp was not observed (true fault-to-report ~0.70–0.98 s). Move the stamp, or keep it and check the unobserved time another way?

Ruling [spec-text + local] (orchestrator ruling under H10, not Hong's): "reset stamp stays at esptool's reset line (plan C5 :386-387 word for word); add the C1 worst-case check to analyze.py as an extra stricter gate (report it alongside, PASS needs both ≤1 s)". Its definition, a second Ruling [local]: "keep A-LF's worst-case definition (first report − last PONG before that report, report searched after launch)".

Reason: the check "closes the unobserved-time gap without a spec deviation and without a live re-run"; the definition, "because the last PONG is the last evidence the board was alive, so it is the conservative bound" (the brief's "last PONG before esptool start" gave 1490–1740 ms, because the board still answers after esptool starts). A-LFK-R kept the definition (Q2 KEEP), with the caveat that the figure reads low by ~RTT/2 (A-LFK-R m1). The V2 step 4 re-run (A-V2link-D1) measured a worst case of 706–730 ms.

Affects: `src/input/harness/analyze.py` reset worst-case gate (`0c1089a`; an uncomputable worst case is a FAIL). The C5 stamp and the `faults.jsonl` format are unchanged.

Cost if wrong: one analyze.py check to drop; for the definition, "one `< first` → `< launch` edit + soak re-run".

Undo: revert `0c1089a`.

Pointer: ledger.md:636 (ruling), :646 (definition), :650 (A-LFK-R), :710 (v2-faults-4).

## A-V2stamp-Q4 (2026-10-22): closing the sink window ends it cleanly, and the window is large, centred, topmost, bright yellow and flashes the taskbar

Question: Hong missed the sink window twice, and closing it raised a TclError in the listener (v2-faults-1). Should the sink window change (A-V2stamp Q4: (a) a close handler, (b) a window that is easy to find)?

Ruling [local] (orchestrator ruling under H10, not Hong's): "sink-window fixes Q4(a)+(b) go in now (no-op close handler; centred, larger, topmost, bright, taskbar flash)". As built by A-LF: the close button sets the same stop flag as `stop()` (`FOCUS_LOST` unchanged), and the window is ¼ of the screen width (at least 600×300), centred, topmost, yellow with a plain label, and calls `FlashWindowEx`.

Reason: "because Hong missed the window twice and V1/V3 live runs need it; implementation detail, focus check unaffected per A-V2stamp's test" (topmost leaves `GetForegroundWindow` unchanged; `FlashWindowEx` does not change the active window). A-LFK-R approved the commits.

Affects: what the user sees during a soak or probe run: the sink window in `src/input/harness/listener.py` (`c7bc1e9` close handler, `10ffa2a` look). The run sheets and plan V2/V3 steps describe the window (`7e7012c`, `533cbbd`).

Cost if wrong: revert one listener commit.

Undo: revert `10ffa2a` (look) and/or `c7bc1e9` (close handler).

Pointer: ledger.md:635 (A-V2stamp), :637 (ruling), :645 (A-LF), :650 (A-LFK-R).

## A-V2link-D1 (2026-10-22): after F1 lands, the V2 step 4 fault soak is re-run live

Question: A-V2link traced the ~0.97 s link-loss detection to a cancelled PING write that WinRT still awaits (fix F1, 706–731 ms offline). V3 cannot re-measure faults (plan:1779, :1793, :1795), and the live re-measure needs Hong, the board and an elevated launch (human-only). Re-run V2 step 4 after F1?

Ruling: A, "after F1 lands, re-run the V2 step 4 fault soak live (right after V1 live, same sitting)" (Hong's decision).

Reason: not recorded in the ledger (ledger.md:647 gives the ruling only). The re-run (A-V2refault, v2-faults-4 at `0615c19`) passed every gate: 10/10 faults, reset worst case 706–730 ms (v2-faults-3 re-analysed: 967–980 ms), so the A-LK fix was confirmed live with ~270 ms margin; A-V2refault-V verified it.

Affects: the order of the live runs (V1, then the V2 step 4 re-run); data in `data/soak/v2-faults-4`. No code or plan change comes from the decision itself.

Cost if wrong: one ~15-min attended soak.

Undo: none needed.

Pointer: ledger.md:642 (A-V2link), :647 (decision), :710 (A-V2refault), :712 (A-V2refault-V).

## A-M6-D1 (2026-10-22): the live m6 diagnostic runs in the next live session, after A-V2refault and before V3

Question: A-M6 found that in 47/50 m6 bursts the PC never got one board reply (all 16 key changes delivered), and that the firmware discarded reply-send results (`Helix_BLE.ino:129-131` at the time). When should the live diagnostic run? It needs Hong at the PC (human-only).

Ruling: A, "live m6 diagnostic (log reply-send results on board, serial capture, link DEBUG) in the next live session after A-V2refault, before V3" (Hong's decision).

Reason: V2/V3 traffic cannot trigger the burst (A-M6), so V2 could go first; the diagnostic had to come before V3. A-M6diag built it (`NOTIFY_DIAG` firmware flag + `probe --link-debug`, merged `a93886b`); A-M6live ran it and found the cause (the board's BLE stack refuses the DONE notify after the 16-report burst, `done_rc=6`).

Affects: the live-run order; `NOTIFY_DIAG` (off by default) in `firmware/Helix_BLE`; `probe.py --link-debug`.

Cost if wrong: one attended live session.

Undo: none needed; the flag stays off in the shipped build.

Pointer: ledger.md:671 (A-M6), :675 (decision), :713 (A-M6diag-R), :717 (A-M6live).

## A-M8-fix (2026-10-22): the m8 tick-granularity estimator becomes a grid fit, with no live re-run

Question: m8 failed live (NOT CONFIRMED). A-M8 found an estimator bug, not a regression: every recording has `tick_ms` on a 15.625 ms grid, but the estimator (p99 − p1 of tick − qpc) assumes the lag is under one tick, while BLE keys lag over ~2 ticks. Fix the estimator, and re-run m8 live?

Ruling [local] (orchestrator ruling under H10, not Hong's): "m8 → fix A (grid-fit estimator + red test + offline re-run on v1-20261022c), no live re-run", with A-V1live-D1 B (probe.py utf-8 stdout) folded into the same unit.

Reason: "because it is a code bug against the plan's own gate (plan:1708), no spec deviation". A-M8fix's offline re-run gave CONFIRMED ~15.625 ms on v1-20261022c (old 27.355 ms) and on all 15 recorded runs; A-M8fix-R upheld the ruling (Q1–Q4 KEEP).

Affects: `src/input/harness/probe.py` m8 (`bbdc10c`; utf-8 stdout `6f56244`; merged `0615c19`); later `1499505` and `16a8d78` (A-FC-D2).

Cost if wrong: revert one probe.py commit.

Undo: revert `bbdc10c`.

Pointer: ledger.md:668 (A-M8), :669 (ruling), :677 (A-M8fix), :703 (A-M8fix-R).

## A-M6live-D1 (2026-10-23): the m6diag-2 data stands although the agent raised the sink window by a Win32 call

Question: in A-M6live's run m6diag-2 the agent, not Hong, brought the sink window to the front by a Win32 call (no key typed, `focus_pauses` empty). Does the run count?

Ruling [local] (orchestrator ruling under H10, not Hong's): A, "m6diag-2 stands although the agent raised the sink window by Win32 call, no keys typed, focus_pauses []". A-M6fix-R upheld it (Q4 UPHOLD).

Reason: "because done_rc is measured on the board and doesn't depend on who raised the window; not a spec deviation".

Affects: the m6 diagnosis (`data/probe/m6diag-2`) behind A-M6-fix. The live run sheets now let the agent raise the sink itself when the operator cannot (`7e7012c`, `533cbbd`).

Cost if wrong: one ~10-min attended re-run.

Undo: re-run the m6 diagnostic with Hong clicking the sink.

Pointer: ledger.md:717 (A-M6live), :718 (ruling), :729 (A-M6fix-R).

## A-M6-fix (2026-10-23): the firmware retries a refused reply notify for a bounded time

Question: the board's BLE stack refuses the DONE notify after m6's 16-report burst (`done_rc=6`, mbuf or ENOMEM), so the PC sees `ACK_TIMEOUT`. How is it fixed (A-M6 Q4)?

Ruling [local] (orchestrator ruling under H10, not Hong's): "fix A-M6 by firmware bounded retry of refused reply notifies (A-M6 Q4 option 3), no wire-format change". As built (A-M6fix): a refused ACCEPTED / DONE / loop-task REJECTED notify is retried with `vTaskDelay(1)` for up to 30 ms, stopping on success or when the peer leaves; a final failure is dropped and counted as before; PONG and the `onWrite` QUEUE_FULL REJECTED stay one attempt (A-F5-D3). A-M6fix-R upheld it (Q1–Q3 KEEP).

Reason: "because the data points to it directly and it stays inside the spec (delivery + visible failure unchanged)". Live (A-M6fixlive, unattended): m6 DONE OK 50/50, 0 `ACK_TIMEOUT`, 0 `NOTIFY_FAILED`.

Affects: `firmware/Helix_BLE/Helix_BLE.ino`, `firmware/Helix_BLE/notify_retry.h` and `firmware/test/test_notify_retry.cpp` (`d4a3fec`, merged `16ee18c`). Extended by an A-M6fix-R [review] (the all-up HID report after a refused key report, and the one in `runReleaseAll`, were sent once), batched into Final cleanup (A) and implemented by A-FC piece 1 (`08646be`, `notify_retry::send_all_up` on the loop task), approved by A-FC1-R; the RELEASE_ALL-abort all-up stays one attempt (`812411e` comment).

Cost if wrong: revert one firmware commit (and reflash).

Undo: revert `08646be`, then `d4a3fec`, and reflash.

Pointer: ledger.md:719 (ruling), :724 (A-M6fix), :729 (A-M6fix-R), :731 (A-M6fixlive), :743 (A-FC piece 1), :765 (A-FC1-R).

## H13 (2026-10-23): live tests run unattended on the night of 2026-10-23

Question: can the A-M6fix live re-run and the V3 2 h soak run while Hong is away? The conditions set out to him: PC on and unlocked with no sleep or lock, MapleStory closed, board plugged in; the risk that a pop-up stealing focus takes a few stray bot keys before the soak pauses; the sink can be raised by script, with no human click.

Ruling: "run the live test unattended tonight" (Hong's decision, 2026-10-23 night). It applies to the A-M6fix live re-run (flash the fix, m6 alone, flash back) and the V3 2 h soak. Live briefs: the agent raises the sink by Win32 call, checks the session is unlocked before each run (MapleStory too, until Hong said it is closed), never types, and on any doubt stops and leaves the board on HEAD / verified firmware.

Reason: Hong was away (decide-as-Hong mode from that night). Both runs passed: A-M6fixlive (m6 DONE OK 50/50) and A-V3 (all 6 V3 rows PASS, verified by A-V3-V).

Affects: how A-M6fixlive and A-V3 were run; the run sheets' sink-raise text (`7e7012c`, `533cbbd`).

Cost if wrong: a live run repeated with Hong present.

Undo: none needed.

Pointer: ledger.md:721 (decision), :722 (away mode), :723 (MapleStory), :731, :734, :736.

## A-FC-D1 (2026-10-23): failed `commands.jsonl` rows keep null stage times

Question: a Failure row in `commands.jsonl` (e.g. `ACK_TIMEOUT`) has null `sent` / `written` / `accepted` / `done` times, which hides which stage timed out (A-M6 [review]). Keep the C5 format, or record the times on Failure rows?

Ruling [local] (orchestrator ruling under H10, not Hong's): A, "no change: commands.jsonl Failure rows keep null times, record it". A-FC1-R kept it (Q5 KEEP, plan C5 :359).

Reason: "because A keeps the C5 format as specced (no deviation) and NOTIFY_DIAG + retry cover the diagnosis".

Affects: nothing changes; the C5 `commands.jsonl` format stays as specced.

Cost if wrong: one small format unit later (Hong-gated).

Undo: none needed.

Pointer: ledger.md:744 (ruling), :765 (A-FC1-R); notes/A-M6.md:169-171.

## A-FC-D2 (2026-10-23): m8 granularity is n/a below 10 distinct tick offsets

Question: with 2–9 distinct tick offsets, m8 printed CONFIRMED or NOT CONFIRMED from too little data (A-M8fix-R m1 measured 10–40% false CONFIRMED at n 3–5). Should m8 report n/a there?

Ruling [local] (orch 25; orchestrator ruling under H10, not Hong's): A, "m8 n/a below 10 distinct offsets + test".

Reason: "because plan M8/spec set no minimum, so this adds an honest 'not enough data' verdict without contradicting spec text (not a spec deviation; m8 sends 100 taps, <10 offsets = mostly-dead listener)".

Affects: what the m8 report says (`src/input/harness/probe.py`, `16a8d78`).

Cost if wrong: one revert.

Undo: revert `16a8d78`.

Pointer: ledger.md:755.

## A-FC-D3 (2026-10-23): analyze keeps Faults n/a on an empty `faults.jsonl`; the V2 run sheet checks the fault count instead

Question: analyze reports Faults n/a, not FAIL, when `faults.jsonl` is empty or does not match the `--faults` spec (A-V2refault-V [review]). Should it FAIL?

Ruling [local] (orch 25; orchestrator ruling under H10, not Hong's): A, "no analyze change; V2 step 5 run-sheet check line 'Faults 10/10 pass, 5 reset_board + 5 bluetooth_off_on' → run-sheet piece".

Reason: "because an empty faults.jsonl is how a no-fault run is defined (FAIL would break V3), plan Faults sets no expected-count input, and the reviewer recommended the run-sheet line".

Affects: plan A V2 step 5 check line (`3a7c31c`); `analyze.py` unchanged.

Cost if wrong: an empty fault soak still reads n/a (caught by the run-sheet check).

Undo: revert `3a7c31c`.

Pointer: ledger.md:763.

## A-FC-D4 (2026-10-23): the m6 report says how many reports other failed commands delivered

Question: the m6 report counts failed commands other than `NOTIFY_FAILED` without saying their keys were delivered (A-M6 [fyi]). Add a line?

Ruling [local] (orch 25; orchestrator ruling under H10, not Hong's): A, "m6 report adds 'reports seen on other failed commands', informational, no gate change".

Reason: "because the fact is derivable from run files and removes a hand check; plan M6 text doesn't forbid extra report lines".

Affects: what the m6 report shows (`m6_report` in `src/input/harness/probe.py`, `0a78e4e`); no verdict changes.

Cost if wrong: one revert.

Undo: revert `0a78e4e`.

Pointer: ledger.md:764.

## A-FC1-R-Q4 (2026-10-23): after p1a-cleanup merges, the live check is A-FC1-R Q4, with no soak

Question: A-FC piece 1 changed the firmware (`08646be`) after V3 ran. What live check does the merged HEAD need before S1?

Ruling [process] (orch 25; orchestrator ruling under H10, not Hong's): "live check after p1a-cleanup merges = A-FC1-R Q4 (flash merged HEAD flag-off, verify-flash, tee output into data/, m6 alone expecting DONE OK 50/50 + release_ordering probe), no soak".

Reason: "because the change only touches the all-up after a refusal, which no live run has produced, so the check verifies the image".

Affects: the live step after the p1a-cleanup merge, before S1.

Cost if wrong: "a regression in a path V3 exercised, found later".

Undo: run a V3 soak at the merged HEAD.

Pointer: ledger.md:765 (A-FC1-R), :766 (ruling).

## A-FCdbg-D1 (2026-10-23): a keepalive PING the board answered is a soft miss; only a hard miss or 3 soft misses in a row end the session

Question: an m6 burst exhausts the board's NimBLE buffers for a moment, so a keepalive PING written then gets an ATT error response (0x11 Insufficient Resources) or no PONG, and the PC ended a live session as `LINK_LOST` (A-FCdbg). How should Helix stop that without slowing real-loss detection? Options raised: A. `LINK_LOST` after 2 consecutive failed keepalives; B. an ATT error response counts as the peer alive; C. skip or delay keepalives near a PLAY; D. firmware msys pool 12 → 24 blocks; E (the adjudicator's): B's classification widened to "the board answered the write", plus A's consecutive count applied only to those soft misses.

Ruling: ruled as Hong (decide-as-Hong), adjudicated by A-FCdbg-adj. The ledger line, verbatim: "Ruling (as Hong) [plan change to link fault semantics; adjudicated, not a spec deviation]: A-FCdbg-D1 → E (hard miss = LINK_LOST at once; soft miss = WARNING, no fault; 3 soft misses in a row = LINK_LOST), because it covers both observed/inferred failure modes, leaves real-loss detection and every harness gate (reset worst case < 1 s, test_link.py:335-342) unchanged, needs no firmware change, and A/B/C/D each break a gate or leave a mode open; cost if wrong: a real loss that looks like soft misses is reported up to ~0.75 s later, or a flaky board hides behind WARNINGs; undo: revert the fix commit + plan commit. → verdict Q for the fix review and A-final-R; flag to Hong in the end summary (interface/data-behaviour, top)."

Reason: E covers both failure modes (ATT error response, and a completed write with no PONG); a hard miss (the write still pending at the 0.5 s deadline, or any write error other than `BleakGATTProtocolError`) still ends the session at once, so the reset worst case (706–730 ms, A-V2link-D1) and `test_keepalive_pong_loss_is_fault_within_0_8s_of_the_last_pong` are unchanged; no firmware change. A delays reset detection by 250 ms (~956–980 ms against the 1 s gate), B leaves the no-PONG mode open, C can break spec:L57 during a hold, D needs a new image and reflash. Not a spec deviation (the spec has no keepalive text); a plan change to P4, the same class as A-P4-D2.

Affects: `src/input/link.py` (`_keepalive_ping`, `_ping_cmd` returns whether the board answered, `KEEPALIVE_SOFT_MISS_LIMIT = 3`, `_Session.soft_misses` reset by any PONG in `_on_notify`), tests T1–T6 in `tests/input/test_link.py` (`9683e23`); plan A P4 Keepalive, `ping()`, Session state, fault list, C5 and the P4 test list (`f40a08e`). A soft miss logs one WARNING on `src.input.link` and adds no `faults()` / `link_faults.jsonl` entry.

Cost if wrong: a real loss that looks like soft misses is reported up to ~0.75 s later, or a flaky board hides behind WARNINGs; a board that answers writes but never sends PONG ends ~1.0 s after its first unanswered PING instead of 0.5 s. Analyze's reset worst case (last PONG → first report, gated at 1 s) grows by ~250 ms per soft miss just before a loss, because the last PONG is then an earlier PING's: one soft miss → ~0.95–0.98 s, two → ~1.2 s and a Faults FAIL, although detection from the loss itself is unchanged (A-KA-R m1, A-final-R M-1.1); a Faults FAIL with soft-miss WARNINGs just before the reset is this proxy cost, not slower detection.

Undo: revert `9683e23` and `f40a08e`.

Pointer: ledger.md:783 (raised), :785 (A-FCdbg-adj), :786 (ruling); notes/A-FCdbg.md, notes/A-FCdbg-adj.md, notes/A-KA.md.

## PB-B19 (2026-10-23): B19 walk failure exit code and link-check MapleStory guard

Question: the PB plan-review ladder left two B19 gaps: `main` in `src/route/walk.py` has no stated reason or exit code when `run()` returns a `Failure` or raises, and the live link check sends real keys with no MapleStory-process check. Fix them in the approved plan before the build (B), or leave them as notes for the B19 builder (A)?

Ruling [local] (orch 29; orchestrator ruling under H10, not Hong's): "B19 concerns → option B (amend plan B19 before the build: failure reason + exit code when run() returns Failure or raises; MapleStory-process check in the B19 link-check script, not in B20/B21 in-game runs)".

Reason: 3 of the 4 ladder reviewers raised each gap on their own (PB-ladder-report §4), and the second is key safety: the link check types into the sink window, and with the game open the same keys would land in the game. The check reuses `soak.maple_check()` (`src/input/harness/soak.py:518`), which `probe`, `soak` and `session_starts` already call. B20 and B21 send keys into the game on purpose, so they get no check.

Affects: plan `docs/superpowers/plans/2026-10-23-p1b-route-walking.md`: B19's exit codes (new: 6 = `run()` returned a `Failure`, 7 = `run()` raised; both print `walk failed: ...` / `walk crashed: ...` to stderr and write `stop.json` with `"reason": "error"` and an `"error"` text), B19's tests (cases a-c), B19's live link check (a guard run before the listener starts, a guard as the script's first statement, a 60 s re-check that stops the walker, exit 8, and a no-game guard test), and hard rule 2. The plan's `Status: approved` line stays, with an "Amended after approval" line under it. B19's Cloud tag stays `no`. No code changed.

Cost if wrong: two more exit codes and a few test cases for the B19 builder; a guard that fails closed (an unreadable process list counts as MapleStory running) can stop a legitimate link check.

Undo: revert the `docs(plan): amend B19 ...` commit on `p1b-route-walking`.

Pointer: ledger.md:854 (ruling); notes/PB-amend.md; PB-ladder-report.md §4.

## B11-D1 (2026-10-24): the leg log ends a torn last line before it appends

Question: when the leg log opens an existing `legs.jsonl` or `laps.jsonl` whose last line a crash left without its newline, should it leave the file alone (so the next session's first record is glued to the torn line and both are lost), or write a newline first?

Ruling [Hong]: option A: "when the log opens an existing file whose last byte is not `\n`, it writes a `\n` first". Empty or missing files are unchanged.

Reason: contract C9 says a crash loses at most the record being written. Gluing the next session's first record onto a torn line loses a second, whole record.

Affects: `src/route/leg_log.py` (`_open_for_append`, used by `JsonlLegLog`), tests in `tests/route/test_leg_log.py`. The torn line itself stays in the file as its own (unparseable) line, so a Feature D reader must skip a line that does not parse.

Cost if wrong: one extra byte in a file after a crash; nothing else changes.

Undo: revert the `fix(route): leg log ends a torn last line ...` commit on `p1b-route-walking` (it also carries B11-D2, so this undoes B11-D2 as well).

Pointer: `W/ab/B11/judge.md` (X2 silent area "torn lines"); `W/notes/B11-fix.md`.

## B11-D2 (2026-10-24): the leg log writes strict JSON and refuses NaN and infinity

Question: should a leg or lap record with a NaN or infinite float be written as the bare words `NaN` / `Infinity` (the `json.dumps` default, not valid JSON), or refused with a `Failure`?

Ruling [Hong]: option A: strict JSON. "A record containing NaN or +-Infinity is refused: the write method returns a `Failure` and writes nothing."

Reason: the bare words break any non-Python reader of the log (`jq`, a browser). The refusal uses the `Failure` path the log already has for an unserialisable record.

Affects: `src/route/leg_log.py` (`json.dumps(..., allow_nan=False)` in `JsonlLegLog._append`; the module docstring no longer says NaN is written), tests in `tests/route/test_leg_log.py`. A walker that produced a NaN `walk_ms` or `cost_to_go_s` now gets a `Failure` from `write_leg` / `write_lap` and loses that record.

Cost if wrong: a record with a non-finite float is dropped instead of logged with a non-JSON token.

Undo: revert the same `fix(route): leg log ends a torn last line ...` commit (it carries both decisions).

Pointer: `W/ab/B11/judge.md` (X2 m1; X3 :173 had the same choice); `W/notes/B11-fix.md`.

## H14 (2026-10-25): `src/ble/` is deleted

Question: may the codebase cleanup delete `src/ble/`, which PA-D2 left untouched?

Ruling [Hong]: yes. Hong's decision (2026-10-25, going to bed), as the ledger records it: "Deletions allowed: ... and src/ble/ (reverses docs/decisions.md:86 → record the new decision in decisions.md)".

Reason: nothing imports `src/ble/`. `ble.py` does not parse (empty `async def establish_connection`), `bleV2.py` connects to an ESP32 at import time and imports `classes.classes` (not `src.`), and `test.py` runs `asyncio.run` at import. `src/input/` replaces all three. Left in the tree they block tooling that imports or type-checks every file.

Affects: `src/ble/` (removed with `git rm -r`) and the four `src/ble` lines of CLAUDE.md "Incomplete / experimental code". `bleak` stays in `requirements.txt` (`src/input/link.py` uses it). Reverses the "`src/ble/` left untouched" clause of PA-D2 (the rest of PA-D2 stands).

Cost if wrong: the three files are experiments with no callers; nothing is lost that `src/input/` does not do.

Undo: `git checkout 60f1d7a^ -- src/ble` on `cleanup/agent-friendly` (restores only the folder; `git revert 60f1d7a` would also delete this entry and restore a CLAUDE.md sentence about code that is gone).

Pointer: `.superpowers/orchestrator/2026-10-21-phase1/ledger-B.md:270` (decision), `notes/DRV41.md` (Final executable wave plan, row 1), `notes/AUD40-p5.md` CW-12.
