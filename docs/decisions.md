# Decisions

This file records Hong's design decisions and rulings that touch interfaces, data behaviour or what the user sees. Entries are in the order they were made; ids are stable.

## Hong's standing facts

Facts and principles Hong would give the same way every time (his answer to Q7, H28, 2026-10-26). Agents and orchestrators check this list before they ask him anything: a question it answers is not sent. Follow it and log `Ruling [standing fact]`. The list grows only with his yes. (The last two sentences are rules of the orchestrator skill, not his words.)

- Maps never use ropes or ladders. Kanna teleports only to a pixel exactly above or below.
- People build maps in Hong's map editor, not by editing files.
- Hong's keyboard and mouse are wired. The minimap finder works on his client.
- Fix real problems, not ones we don't know will happen. Make checks only as strict as they need to be.
- Inputs must still look human generated. Bad map files must be obvious, with how to fix them.

Sources: `.superpowers/orchestrator/2026-10-21-phase1/notes/QA60-page/index.html`, section "The proposal" (the five bullets, copied in plain sentences); `notes/QA60-a.md` section 5f (the questions behind them: Q13, Q27, Q36, Q57, Q40, Q30, Q35, Q55, Q29); ledger-B.md:610 (Hong's yes to writing them down).

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

## H15 (2026-10-26): agents may run the live tools and flash the board when MapleStory is closed and the PC is idle

Question: must an agent ask Hong before each run of the key-sending harness tools (`probe`, `soak`, `session_starts`) or each flash or reset of the board?

Ruling [Hong]: no. Hong's decision (2026-10-26): "you dont nneed my explicit permission though. As long as maplestory isnt running annd if you need it to continue work, you may use the tools". Asked whether flashing or resetting the board falls under it: "Yes, same rule". Asked whether agents must also make sure nobody is using the PC: "Only when idle". An agent may therefore run `probe`, `soak` or `session_starts`, and flash or reset the board, without asking, when all three hold: (1) the work needs it; (2) MapleStory is not running (the tools' own check still applies and is never bypassed); (3) nobody is using the PC (check the time since the last keyboard or mouse input, or ask Hong).

Reason: the board's keys go to whichever window has focus, and a person's typing during a run is an extra key event that fails `analyze`'s Delivery check. Everything else in the safety rules still holds: the "Helix input test sink" window, a real Bluetooth link, `keys.jsonl` stays in the gitignored `data/`, and no push to `upstream`.

Affects: the "Safety rules" list in the root CLAUDE.md, the "Harness tools" section of `src/input/CLAUDE.md` and `firmware/CLAUDE.md`. It generalises H13, which allowed two named live runs (the A-M6fix re-run and the V3 2 h soak) unattended on the night of 2026-10-23 only, into a standing permission that adds the idle-PC condition.

Cost if wrong: a run with stray keys in the focused window, or a run aborted by the MapleStory check; rerun it.

Undo: revert the `docs(safety)` commit that adds H15; agents then ask Hong before each live run again.

Pointer: root CLAUDE.md "Safety rules"; `src/input/CLAUDE.md` "Harness tools"; H13.

## H16 (2026-10-26): a map file is checked against the expected format, and the user is told plainly what is wrong

Question: what should happen when a map CSV does not match the format (behaviour issue 7: a file with no header, or a row with an extra cell, crashed the runtime parser with `TypeError`, and the crash left the bad file marked as loaded)?

Ruling [Hong]: fix it. Hong's words (2026-10-26): "Fix, check the map file to ensure it matches what we expect, make it obvious to the user if it isnt what we expect so it can be easily fixed."

As built: `map_format.read_rows` checks the whole file before any `Node` is built: no header row (empty file, or only `#` lines), a wrong header (reported alone, with the expected and the found line), a row with the wrong number of cells (a stray comma at the end, or an empty row of commas, gets its own message), an unknown node type, a bad `TRUE`/`FALSE` cell and a bad `(x, y)` position. `#` lines and blank lines are skipped. Every problem is reported, each with its line number in the file, under one line that names the file and says to fix it in a text editor and load it again; at most 20 are listed. A bad file returns `Failure`, `loadMap` resets `CSV_file` to `None` as before, and the main window shows the message in a message box as well as in its console. The map editor's reader uses the same check and the same messages (raised to Hong as ISS7-D1, made provisionally by the implementer); its own `# minimap_size=` check gives its message in the same shape (the file, the line number, what the line must be, how to fix it).

Follow-up [Hong, 2026-10-26]: in a file with a bad `# minimap_size=` line and bad rows, the editor keeps reporting the size line on its own, and the row problems show on the next load; no change. Only a hand-edited file can have both.

Affects: `src/controller/map_format.py` (`read_rows`, `problems_message`), `csv_parser.py`, `map_csv.py`, `src/gui/gui.py` (`_loadMapBrowse`), `src/gui/map_editor.py` (load error box), the pins in `tests/controller/test_map_csv_characterization.py`, `src/controller/CLAUDE.md` "Map CSV readers". Not changed: the route simulator's `_write_offset_csv` has no check of its own, but it reads only files that already passed `loadMap`, so it has no gap in practice.

Cost if wrong: a file the editor used to accept is now refused (a row with an extra cell, which it ignored); the message says which line to fix. One change goes the other way: a whitespace-only line is now skipped by both readers (it was refused before).

Undo: revert the `feat(controller)` commit that adds `read_rows`. To undo only the editor part (ISS7-D1), restore `read_editor_nodes`'s own checks from 20e57bf and drop the editor assertion in the test helper `_both_refuse`.

Pointer: `.superpowers/orchestrator/2026-10-21-phase1/notes/ISS7.md` (every message, verbatim), `notes/CW-EXPL.md` issue 7, `ledger-cleanup.md:78`.

## H17 (2026-10-26): an up/down link skips a node Kanna can't teleport to and goes to the next one she can

Question: when the nearest node above or below fails the platform-edge rule (behaviour issue 6: the node is the end of a platform that starts beside the character, not above her), should the path graph give up on that direction, as it did, or link to the next aligned node within reach that passes the rule?

Ruling [Hong]: link to the next one that passes (option B on the issue-6 page). Hong's words (2026-10-26): "Kanna has to be exactly in line with that platform for the to bring it there. If there is no pixel above/below kanna to teleport to, then the teleport will fail. It makes sense to only draw links between nodes that can actually be connected, so with your example, it would make sense to have S(70,60) connect to T(70,40) instead of it not connecting to M(72,50) and stopping there when T(70,40) is allowed."

As built: `_findClosestNodeAbove` and `_findClosestNodeBelow` (`src/controller/path_graph.py`) skip every candidate that fails `_isVerticalEdgeValid`, then take the nearest of the rest; `_createUpEdges` / `_createDownEdges` no longer check the rule a second time. The rule itself is unchanged (a target flagged left platform end is reachable from x at or right of it, flagged right end from x at or left of it, flagged both or neither from anywhere in the 2 px column), and so are the one-edge-per-direction rule, the reach limits and the rest of the first-inserted tie-break. One insertion-order effect is gone: before H17, an unreachable platform-end node inserted first won the tie against an equally near plain node and then failed the rule, so the source got no up/down link; now the end is skipped and the link goes to the plain node in either order (source (70,60), left end (71,50), plain (69,50): linked to (69,50) either way; pinned in `test_unreachable_platform_end_does_not_block_the_link_whichever_is_inserted_first`). In the page's example S(70,60) now links up to T(70,40) and T links down to S, past M(72,50), the left end of the middle platform. A node carries only its position and the two platform-end flags, so "a pixel of the target platform exactly above or below" is decided from those flags; an unflagged node is taken to have platform under the whole column.

Follow-up [Hong, 2026-10-26, ISS6-B-D1]: keep as built. Whether there is platform pixel exactly above or below Kanna is judged from node positions plus the two platform-end flags only; the map file stores no platform widths. Known cost: a target 1-2 px beside Kanna's column that is not marked as an end is assumed to have platform there, so if the platform really stops in that gap the link is drawn and the teleport fails (route walking then re-plans from where Kanna lands). The user marks platform ends in the map editor to avoid this. Storing platform widths was rejected: it changes the map format, the editor and both readers, which is large.

Affects: `src/controller/path_graph.py` (the two vertical finders and their callers); `tests/controller/test_path_graph_characterization.py` (the no-fallback pin, up and down, now expects the farther node; new test for the S/M/T example); `src/controller/CLAUDE.md` "Edge choice". Every user of the graph sees the new links: the runtime, the map editor's preview and route walking (`RouteGraph.build` copies every `PathGraph` edge). Not changed: route walking's own terrain finder (`src/route/terrain.py`, frozen until the split-platform fix), which still keeps the nearest node only. The FF2 map builds the same 40 edges as before (no node in it is flagged as a platform end).

Cost if wrong: on a map with platform ends flagged, a node can get an up/down link to a farther platform where it had none; if the game can't make that teleport (for example it can't pass through the nearer platform), route walking tries it, misses and re-plans from where Kanna lands.

Undo: revert the `feat(controller)` commit that adds the filter (restores the no-fallback pin too).

Pointer: `.superpowers/orchestrator/2026-10-21-phase1/notes/ISS6-PAGE.md` (the example and its probe), `notes/ISS6-R.md` (which tests flip), `notes/ISS6-B.md` (as built).

## H18 (2026-10-26): the main window appears at once instead of sleeping until the minimap is found

Question: may the startup wait be removed (behaviour issue 1: before showing the main window, `_setupGUI` slept 5 s and then polled for the first minimap frame every 10 s, holding the frame lock while it slept, so the window could take up to one extra 10 s to appear)?

Ruling [Hong]: yes. Hong's words (2026-10-26): "Fix sounds fine". The main window no longer sleeps and polls for the minimap before it is shown: it appears at once, and the minimap area appears on the first frame.

Affects: `src/gui/gui.py` startup. Another unit implements it on its own branch; this entry records only the decision.

Cost if wrong: the window shows before there is a minimap to draw; the minimap area stays empty until the first frame arrives.

Undo: revert that unit's commit.

Pointer: `.superpowers/orchestrator/2026-10-21-phase1/notes/CW-EXPL.md` issue 1, `notes/ISS1-ANS.md`.


## H19 (2026-10-26): a map file with a header but no nodes is refused when it is loaded

Question: what should happen when a map CSV has the right header but no node rows (behaviour issue 7, finding m1: the format check let it through, the main window then failed late with "Node_dict is empty. Cannot create any edges." and kept the file marked as loaded)?

Ruling [Hong]: refuse it at once. Hong's words (2026-10-26): "Refuse it at once". Asked in the same round whether an Excel-saved file (a UTF-8 byte-order mark in front of the header) should be accepted, he answered: "i expect that they'll use my map creator and not actually need to edit the maps files themselves". That is context, not a ruling to add anything: no byte-order-mark support was added, and the H16 wording for a wrong header is unchanged.

As built: after `map_format.read_rows` finds no problems, both readers (`CSVParser._readRows` and `map_csv.read_editor_nodes`) return `Failure(map_format.no_nodes_message(path))` when it also found no rows. This covers a header alone and a header with only `#` and blank lines. The message is `The map file <path> has no nodes, so it was not loaded. Place nodes in the map editor and save the map, then load it again.` It names the file and says how to fix it, but it is not a "fix it in a text editor" problem line, so it does not go through `problems_message`. `loadMap` resets `CSV_file` to `None` on the Failure as for every other refused file, so the main window does not keep the file marked as loaded, and it shows the message in the same box as H16 ("Could not load map"). The map editor's Load shows it in its load-error box. The old late "Node_dict is empty" message can no longer be reached from a header-only file.

Affects: `src/controller/map_format.py` (`no_nodes_message`), `csv_parser.py`, `map_csv.py`; the test that pinned the old behaviour (`test_header_only_file_is_accepted`, now `test_header_only_file_is_refused`) and new cases in `tests/controller/test_map_load_errors.py`; `src/controller/CLAUDE.md` "Map CSV readers". Not changed: `read_rows` itself still accepts a header-only file, a file with node rows but no blue node still loads (with an empty rotation), and the route simulator's reader.

Cost if wrong: a file the editor could load before (header only) is now refused by the editor too. The editor never writes such a file (it blocks a save with no blue node), so this only affects a hand-made or hand-emptied file; start a new map in the editor instead.

Undo: revert the `fix(controller)` commit that adds `no_nodes_message` (restores the old pin too).

Pointer: `.superpowers/orchestrator/2026-10-21-phase1/notes/ISS7-R.md` (m1), `notes/ISS7-m1.md` (as built).

## B15-D1 (2026-10-26): the character locator scrubs a match score only above 1 + 1e-3, not above 1

Question: the plan (task B15, Behaviour) says the locator "scrubs non-finite and > 1 scores as `vision.py` does", but the locator as built scrubs a score only above 1 + 1e-3. Keep the built rule and amend the plan, or scrub at exactly 1 as the plan says?

Ruling [Hong]: option A: keep the 1 + 1e-3 scrub as built in 0a41d5a, amend the plan line to match, and record it here. Hong's words (2026-10-26), after a plain explanation: "Record A."

Reason: the explanation Hong was given: float32 rounding can put a perfect match at 1.00000012, so a scrub at "> 1" would turn the best possible match into a miss.

As built: `_SCORE_CEILING = 1.0 + 1e-3` (`src/route/character.py:29`, with the comment at :24-27); `locate` sets every non-finite score and every score above it to `_SCRUBBED` (:91). The plan's B15 Behaviour line now says "scrubs non-finite scores and scores above 1 + 1e-3", and its "Amended after approval" line lists B15-D1.

Affects: the plan `docs/superpowers/plans/2026-10-23-p1b-route-walking.md` (B15 Behaviour line, Status block). No code changed.

Cost if wrong: one constant and its tests; a bogus score between 1 and 1.001 would be taken as a real score.

Undo: set `_SCORE_CEILING` to `1.0` in `src/route/character.py`, run `tests/route/test_character.py`, and revert the plan amendment (the `docs(decisions)` commit that records this).

Pointer: `.superpowers/orchestrator/2026-10-21-phase1/ledger-B.md:224` (provisional ruling) and :274 (Hong's answer).

## B6-D1 (2026-10-26): `attach` skips a platform segment whose two ends share an x

Question: when `locate.attach` looks for the platform segment under the character, should it keep skipping a segment whose two ends share an x (a vertical step, which has no y to interpolate), or attach to such a segment some other way?

Ruling [Hong]: option A: keep as built. Hong's words (2026-10-26): "there will never be a vertical platform though … we'll never have to use a rope/ladder due to the various movement abilities in the game".

Reason: maps never contain rope or ladder segments. Revisit only if a map ever does.

As built: `_on_segment` (`src/route/locate.py:42-61`; the same-x skip is at :56) skips a segment whose ends share an x; `tests/route/test_locate.py:187-193` pins it. The comment at `test_locate.py:191` still says "how it should attach is pending B6-D1": that comment is stale, because this entry decided it (skip). It is left as it is on purpose: `test_locate.py` is a frozen file (`src/route/CLAUDE.md`, Traps), so nobody edits it for a comment (final Feature B review, FINB54 P2-F3, ruled by the orchestrator). Read the comment as "decided in B6-D1: skip".

Affects: nothing in the code; this entry settles the open question in the pin test's comment.

Cost if wrong: a position on a vertical segment would not attach to it; fix `_on_segment` and the pin test then.

Undo: not needed (no code change).

Pointer: `.superpowers/orchestrator/2026-10-21-phase1/ledger-B.md:275`.

## B12-D1 (2026-10-26): `MoveEntry.t_ns` is the clock reading taken right after the position read, the same value as the layer's `stimulus_ns`

Question: the plan does not say which clock reading the leg log's `MoveEntry.t_ns` holds for a move. Which should it be?

Ruling [Hong]: option A: `MoveEntry.t_ns` is the `now_ns()` taken right after the position read the move was planned from, and it is the same value passed to the input layer as `stimulus_ns`. That is the moment the walker "saw" the character, so the time the move was sent minus `t_ns` is the reaction time.

As built: already so, no change needed. `read_ns = clock.now_ns()` right after `self._positions.read()` (`src/route/walker.py:141-142`); `stimulus_ns = read_ns if self._config.use_stimulus else None` (:168); `MoveEntry(t_ns=read_ns, ...)` (:172); documented at :10-11. The clock is `time.perf_counter_ns` (`RealClock`, :70-71), the clock `InputLayer` measures a stimulus on. With `use_stimulus` off (the simulator) no `stimulus_ns` is passed but `t_ns` is still `read_ns`. `tests/route/test_walker_leg.py:240-260` pins that the layer's `stimulus_ns` is the clock right after the read, and `:289-290` checks that the `t_ns` values are ordered and inside the leg. `tests/route/test_walker_t_ns.py` (added in the final Feature B review, FINB54 P2-F2) pins `MoveEntry.t_ns` itself: equal to the `stimulus_ns` the layer was given, equal to the clock right after the read, and still the read time when no stimulus is passed. It uses a clock that moves on at every reading and a layer call that takes time, so a `t_ns` taken before the read, after the call or from a second reading fails it.

Affects: the leg log's `t_ns` meaning for Feature D and for B13/B14 tests that pin it.

Cost if wrong: if `t_ns` were taken elsewhere, send minus `t_ns` would no longer be the reaction time; one line in `walk_leg`.

Undo: change the value passed as `t_ns` in `walk_leg` (`src/route/walker.py:172`).

Pointer: `.superpowers/orchestrator/2026-10-21-phase1/ledger-B.md:276`; `notes/B12-ab-runner.md`, `notes/B12-orch-opus-xhigh.md`.

## B12-fix-D1 (2026-10-26): the leg log's `misses` counts planning reads only

Question: should a leg record's `misses` count only the position reads a move is planned from, or also the reads that fail while the walker waits for the character to settle?

Ruling [Hong]: option A: keep as built. `misses` counts planning reads only (as documented in `walker.py`); a failed read in the settle is a retry and is not counted.

As built: `walk_leg` adds to `misses` when a planning read fails with anything but the `STALE` Failure, which means "no new frame yet" (`src/route/walker.py:141-147`). `_settle` (:275-298) never touches it. Documented at :14-15. The JSON key is `misses` (`src/route/leg_log.py:60`, :120).

Affects: what a Feature D reader may conclude from `misses`; the field name and key are unchanged. No code changed.

Cost if wrong: `misses` undercounts the failed reads of a leg (the settle's and the stale frames); counting them is a change in `_settle` plus the docstring and tests.

Undo: not needed (no code change).

Pointer: `.superpowers/orchestrator/2026-10-21-phase1/ledger-B.md:277`; `notes/B12-fix.md`.

## B17-D1 (2026-10-26): the minimap recorder refuses a folder that already holds a recording, exits 1 on a failed or empty recording, and takes `--out` only inside `data/`

Question: keep the recorder (`src/route/harness/record_minimap.py`, plan task B17) as built: `record` refuses an `out_dir` that already holds `index.jsonl`, `main` exits 1 when recording fails or saves nothing, and `--out` must be a folder strictly inside the repo's gitignored `data/`?

Ruling [Hong]: option A: keep as built.

As built: `record` returns a `Failure` for an `out_dir` that already holds an `index.jsonl`, because a second recording would overwrite the first one's frames (docstring `record_minimap.py:23-25`). `_resolve_out` (:128-134) refuses `data/` itself and anything outside it, and `main` returns 2 for that (:154-157). `main` returns 1 when `record` fails or saves no frame (:175-183), 0 otherwise.

Affects: the B19 brief must account for the refusal (a recording needs a new folder each time). B17-fix is not needed for these rules. No code changed.

Cost if wrong: re-recording into a used folder needs the old `index.jsonl` removed first, and a recording cannot be written outside `data/`.

Undo: not needed (no code change).

Pointer: `.superpowers/orchestrator/2026-10-21-phase1/ledger-B.md:280`; `notes/B17-ab-runner.md`.

## B14-inv-D1 (2026-10-26): the planner is fixed so an up/down move is made only from where the character can really make it

Question: the planner can loop on a platform that a small step splits in two (found by plan test 3b with +-2 px node offsets: seeds 34, 36 and 50, about 1 run in 18 on the Fossil Walk map; with world noise off it never escapes). It fires an up/down move from the character's own x as soon as the character is within 2 px of the edge's start node, the landing attaches to the other half of the platform, and the planner sends the character straight back. Four options were on the b14 page: A drop the `stalls` check from test 3b; B walk to the target's x first, as the plan words it; B' a vertical move starts at its target's x; C leave it and record the finding. Which?

Ruling [Hong]: fix the planner: option B', refined by Hong. Hong's rule for an up/down move from A toward B: (1) if A's platform extends over B's x, walk until level with B's x, then move; (2) if A's platform ends before B's x, walk to A's platform end nearest B and move from there only if the floor directly below (above, for up-moves) that point is a piece connected to B; otherwise the planner must not offer that move (find another route, or report unreachable). Walking keeps the 2 px arrive rule. The fix is checked against seeds 34, 36 and 50, a wide seed sweep with and without noise, and seed 136 (the F-b off-graph freeze). Plan test 3b keeps `stalls`.

Affects: `src/route/planner.py` (where an up/down move starts and whether it is offered), plan contract C7 and plan test 3b, and the planner tests that pin the old start rule. The files `src/route/CLAUDE.md` lists as frozen stay frozen until the fix lands. Another unit implements it on its own branch; this entry records only the decision.

Cost if wrong: contract C7 changes; until the fix lands the livelock stays in the planner and can reach B19's live sessions.

Undo: revert the planner-fix commit.

Pointer: `.superpowers/orchestrator/2026-10-21-phase1/ledger-B.md:281`; `notes/B14-inv.md`, `notes/explain40/b14.html`.

## H20 (2026-10-26): the "draw node on character" buttons say "Character not found" instead of drawing a guess

Question: should the main window's "draw blue node on character", "draw orange node on character" and the dev get-path button keep the old finder `MapleAgent.getCharacterCoords`, which always answers with its best-scoring spot and never says "not found" (behaviour issue 2: with the character icon missing from the minimap, a dot was still drawn at an arbitrary spot and that spot was printed as "Character Position")?

Ruling [Hong]: fix it, using the route walker's finder (`src/route/character.py`, `CharacterLocator`), which says "not found" or "not sure" instead of guessing. This is the "Fix" option of issue 2 in `notes/CWX48.md`, which recommended it. The brief that dispatched the work relays the decision without Hong's own words, so none are quoted here.

Reason: the on-screen position readout is the only one the user sees, so it should tell the truth, and the buttons and the route walker then agree on where the character is.

As built: `MapleGUI._findCharacter` (`src/gui/gui.py`) builds the locator from `assets/chr/chr.png` and `chr_mask.png` on the first press and keeps it, reads the minimap frame under `minimap_frame_lock`, and searches outside the lock. When the finder says "miss", "ambiguous" or rejects the frame, the console shows "Character not found on the minimap (<reason>)." and nothing else happens: no dot, `node_count` does not move, and the get-path button adds no character node and computes no path. If the icon files cannot be read, the console shows "Cannot look for the character: <why>" and the next press tries again. `MapleAgent.getCharacterCoords`, `Controller.getCharacterCoords` and the agent's icon-path attributes are removed: grep found no other caller. `src/route/character.py` is unchanged. Tests: `tests/controller/test_gui_character_buttons.py` (found, missing and two look-alikes for both draw buttons; found and missing for get-path; one locator build; matching outside the lock; unreadable icon; rejected frame).

Import edge: `src/gui/gui.py` now imports `src.route.character`, a new gui-to-route edge. No import-linter contract forbids it (contract c bars controller to route, contract d bars route to gui); the controller cannot host the lookup because of contract c. The unit that built it raised this as a module-boundary choice; the orchestrator ruled to keep the edge (ISS2-fix-D1, option A; the reviewer agreed), not Hong. Moving the finder to a shared module would be the alternative.

Not verified: the walker's finder is strict (the best match must score at least 0.95 and no other separate spot may score within 0.02 of it), and it has only been run on synthetic frames, so on a busy real minimap the buttons may say "not found" where the old finder drew a dot. That needs the game.

Affects: `src/gui/gui.py`, `src/controller/controller.py`, `src/gui/CLAUDE.md`, `src/controller/CLAUDE.md`, `tests/CLAUDE.md` (baseline count), `tests/controller/test_gui_startup.py` (two tests now stub `_findCharacter`), the basedpyright baseline (157 to 146 entries, the removed method's errors).

Cost if wrong: a true character position can be reported as "not found" (strict finder), which blocks the draw buttons until the icon is clearer; no map data is written either way.

Undo: revert the commits of branch `iss2-character-finder`; the old always-guessing finder comes back.

Pointer: `.superpowers/orchestrator/2026-10-21-phase1/notes/CWX48.md` issue 2; `notes/ISS2-fix.md`.

## H21 (2026-10-26): soak's pre-run sample check asks only for the timing kinds the realism judge tests

Question: should the soak tool's pre-run sample check stay stricter than the realism judge (CW-EXPL issue 5: it required 1,000 samples of six timing kinds, including walk and release lag, which the judge does not KS-test, so a 4,000-command run the judge could judge was refused for too few walking samples)?

Ruling [Hong]: fix it. Hong's words (2026-10-26): "so it's only as strict as it needs to be". The check now requires only the four kinds `realism` tests (hold, gap, chord offset, reaction), at the judge's own minimum of 1,000.

As built: `soak.py` imports `KS_TYPES` and `KS_MIN_N` from `realism.py` and uses them in `validate`; it keeps no copy of the numbers, and its own `KS_TYPES` and `MIN_SAMPLES_PER_TYPE` are gone. Runs of about 2,900 to 4,700 commands that were refused for walk samples are now accepted; a run too short for the judge is still refused, naming the short kind; the planned 10,000-command run still passes. `realism.py`'s judging rules are unchanged.

Affects: `src/input/harness/soak.py` (`validate`, `--short` help), `tests/input/test_soak.py`, `src/input/CLAUDE.md` (Harness tools, `soak`).

Cost if wrong: a soak that the judge accepts may carry fewer walk samples than before; nothing judges them.

Undo: revert the `fix(input)` commit.

Pointer: `.superpowers/orchestrator/2026-10-21-phase1/notes/CWX48.md` issue 5, `notes/ISS5-fix.md`.

## B18-D1 (2026-10-26): the detection check skips and reports frames it cannot crop, drafts them as null and scores them as nothing found

Question: how should `draft` and `load_frames` (`src/route/harness/detection_check.py`, plan task B18) treat a full-screen frame that cannot be cropped to a minimap? A: skip and report it. B: stop the run, and Hong moves such frames out of the recording folder by hand.

Ruling [Hong]: option A, refined; ruled as Hong while away, 2026-10-26 (maximum autonomy). Uncroppable frames are skipped and reported, and scored as "nothing found": a frame labelled `[x, y]` is a miss, so skipping can only lower the hit rate. The draft prefills them `null` and draws no sheet cell for them. Exit 2 only when no frame crops; an unreadable PNG and unreadable corner images still stop the run.

As built: `load_frames` returns `LoadedFrames(crops, uncroppable)`, `draft_labels` returns `(count, uncroppable)`, `draft` prints `uncroppable: k of N ...` and one line per frame, `check` prints `uncroppable: k of N frames (scored as nothing found)`, and the crop failure message shows scores with 4 decimals. Why: the real folder `data/recordings/ff2_2026-10-21/` has 3 of 307 frames (0046.100, 0046.365, 0046.676) with another application window over the game; that is not a crop bug. On the other 304 frames the draft and the locator agree at (106, 51).

Affects: `src/route/harness/detection_check.py`, `tests/route/test_detection_check.py`, `src/route/CLAUDE.md`, `docs/HANDOFF.md`.

Cost if wrong: a covered frame in a real folder no longer stops the run, so the user must read the `uncroppable:` line.

Undo: revert the `fix(route)` commit `1065b4f` on branch `b18-fix`.

Pointer: `.superpowers/orchestrator/2026-10-21-phase1/notes/B18-D1-adjudicator.md`; `ledger-B.md:347`; `notes/B18-fix.md`.

## B19-D1 (2026-10-26): the walk ends with exit 10 when a leg times out with no move (the character is lost)

Question: when the character is lost during `python -m src.route.walk` (off the graph, or not found on the minimap), the walker's planner sends no move and the leg would wait for ever, so the run idles until someone presses F12. Should the walk give each leg a timeout and end the run with its own reason and exit code when a leg times out without a move (A), or keep idling until F12 (B)?

Ruling [as Hong]: option A, widened (ruled as Hong while away, 2026-10-26). The ledger line: "B19-D1 → A widened: any leg that times out with 0 moves ends the run, reason `off_graph` if off_graph > 0 else `no_position`, exit 10; leg timeout 60 s as a walk.py constant written into session.json; the sink wrapper calls `walker.stop_now()` itself on the walker thread as well as `trigger()`; lost last leg of last lap → exit 10, not 0." The B19 reviewer agreed (`notes/B19-R.md`, verdict question 1).

Reason: the spec leaves leg timeouts to the builder, no key is sent in these cases, and the ruling only replaces idling until F12 with an end reason. The simulator's longest leg over 20 laps was 6.55 s and a 28,200-leg sweep had no 60 s timeout (`notes/B19-D-adjudicator.md`).

As built: `_LEG_TIMEOUT_S = 60.0` (`src/route/walk.py:73`) is passed to `WalkerConfig` (:274) and written into `session.json` as `leg_timeout_s` (:287). `_LostLegSink.write_leg` (:129-139) writes the record, then on a `TIMED_OUT` leg with no move calls the stop control's `trigger("off_graph")` or `trigger("no_position")` and `walker.stop_now()` on the walker thread. `main` returns 10 with `stop.json` reason equal to that source (:332-334), after errors (6/7) and `link_down` (1). The walker's `off_graph` counter also counts "no path to the target" (`src/route/planner.py:211-213`). Tests: the lost-leg tests in `tests/route/test_walk_cli.py` (approach leg, mid-lap off-graph with no move after the trigger, last leg of the last lap, a leg with moves that times out does not end the run, and a run where the stop control's own stop also fires). The stop control's lock is reentrant so a Ctrl+C on the main thread during that trigger cannot deadlock (B19 review M1).

Affects: the walk CLI's exit codes and `stop.json` reasons (new: 10, `off_graph`, `no_position`), `session.json` (`leg_timeout_s`), the plan's Task B19 (amendment note and exit-code list).

Cost if wrong: a real leg longer than 60 s is cut and the run ends.

Undo: remove `_LEG_TIMEOUT_S`'s use and `_LostLegSink` in `src/route/walk.py` (the walker then gets no leg timeout) and the exit-10 branch.

Pointer: `.superpowers/orchestrator/2026-10-21-phase1/ledger-B.md:337`; `notes/B19-D-adjudicator.md`, `notes/B19.md`, `notes/B19-R.md`.

## B19-D2 (2026-10-26): the walk refuses an `--out` that already holds a run, or that names a file, with exit 2

Question: `walk` appends to `legs.jsonl` and `laps.jsonl` and overwrites `session.json` and `stop.json`, and the recorder refuses an existing `frames/index.jsonl` only mid-run. Should `walk` refuse a used `--out` up front with exit 2 (A), or write into it as it is (B)?

Ruling [as Hong]: option A, widened (ruled as Hong while away, 2026-10-26). The ledger line: "B19-D2 → A widened: walk refuses with exit 2 before capture if `--out` holds any of session.json, stop.json, legs.jsonl, laps.jsonl, frames/index.jsonl; also if `--out` names an existing file (exit 2)." The B19 reviewer agreed (`notes/B19-R.md`, verdict question 2).

Reason: it is Hong's B17-D1 rule (the recorder refuses a folder that already holds a recording) applied to the walk's files; the live runs use a fresh default `--out`.

As built: `_check_out` (`src/route/walk.py:159-161`, a one-line delegate to `runtime.check_out`, `src/route/runtime.py:231-242`, which B20 shares) is called before the map is loaded and before capture (`walk.py:228`). It returns "<out> is not a folder; choose a new --out" for an existing non-folder, else "<path> already exists; choose a new --out" for the first of the five files found, and `main` prints it and returns 2. Tests: `test_out_holding_a_walk_file_is_refused` (each of the five files; nothing is written and capture never starts) and `test_out_naming_a_file_is_refused`. Leftover files from an attempt that ended with exit 9 or 2 also refuse a re-run; the message says to choose a new `--out`.

Affects: the walk CLI's exit 2 cases, the plan's Task B19 (amendment note and exit-code list).

Cost if wrong: a re-run needs a new folder name.

Undo: remove the `_check_out` call in `main` (`src/route/walk.py:228`).

Pointer: `.superpowers/orchestrator/2026-10-21-phase1/ledger-B.md:338`; `notes/B19.md`, `notes/B19-R.md`.

## B20-D1 (2026-10-26): the measurement tool writes directions as `Key` names, leaves no-op moves out of reach and walk speed, and takes the teleport settle from flat sideways teleports

Question: the plan's task B20 (`measure_moves`, the tool that measures Kanna's moves in the game) leaves eleven conventions of the file and the summary open. Keep them all as the first build chose them (A), or change the ones that would skew the numbers B21 compares (B)? The first build wrote `direction` in lower case (`"left"`), counted a move that did not happen in the means, and took the teleport settle over every teleport.

Ruling [as Hong]: option B, three changes, the rest kept as built. Ruled by the orchestrator in Hong's place while he was away, 2026-10-26 (maximum autonomy); not yet seen by Hong when this entry was written. An opus-xhigh adjudicator advised it and the ruling follows its advice (`notes/B20-D1-adjudicator.md`). It judged the changes small enough that Hong would not need to see them even as himself (summary arithmetic only: no key sending, no `--supervised`, `--reps` or `--out` guard changes, no spec text).
- B1: `direction` is the `Key` name (`"LEFT"`, `"RIGHT"`, `"UP"`, `"DOWN"`) or `null` for a drop, the same spelling as the leg log's `dir`.
- B2: a move that went 2 px or less along its axis did not happen (a no-op, as the simulator's `NO_OP`) and is left out of `reach_h_*`, `reach_v_peak_mean` and `walk_px_per_s`. This amends the plan's own B20 definitions of those keys.
- B3: `teleport_settle_s` is the median over the flat horizontal teleports kept for the reach only.
- Kept as built: the line is `dataclasses.asdict(MoveSample)` with positions as `[x, y]`; `kind` is the `MoveKind` value (`"teleport"`, `"walk"`, `"drop"`); the vertical reach is `before_y − peak_y`; `reach_h_sd` is the sample standard deviation, `null` below two samples; a key with no samples is `null` and `reach_h_n` is 0; `--reps` is at least 2; the default `--out` is `data/route/measure/<UTC stamp>`; a used `--out` is refused only for the tool's own files, on the condition that every file the tool writes is named in `_OUT_FILES`.

Reason: B1 closes a silent trap: the project turns a `Key` into text with `.name` (`src/route/leg_log.py:43`, `src/route/walker.py:175`), and with lower-case names in the summary code such a writer gives a summary of all `null` with no error (the adjudicator ran it). B2 and B3 stop two things from moving the numbers B21 acts on: with nine 19 px teleports and one no-op the reach mean is 17.1 instead of 19, which B21 step 4 would turn into a change of Kanna's reach; and a median over all teleports reads 0.5 s instead of 0.4 s because the up and down teleports include a fall, 25% off against step 5's 20% rule. Each no-op also lowers the walk speed by 10% in ten walks. If real no-ops were as common as the simulator's stress setting (6%), a run of ten would contain one with probability 0.46 (inferred, not measured).

Reviewer verdicts: B20-R (`notes/B20-R.md`): built as ruled. B20-fix-R (`notes/B20-fix-R.md`) and the final Feature B review (`notes/FINB54.md`): agree.

As built (`src/route/harness/measure_moves.py` at 1523c99): `_direction` returns `key.name` (:300-301); `_HORIZONTAL` is built from `Key.LEFT.name` and `Key.RIGHT.name` (:90) and the up test uses `Key.UP.name` (:143); `_NO_MOVE_PX = 2` (:89); in `summarize`, `flat` drops no-ops (:140-141), `reach_v` keeps only rises above 2 px (:143-144), `walks` drops no-ops (:145-146) and `teleport_settle_s` is the median of `flat` (:155); docstring :29-38. The kept conventions: `sample_to_json` (:122-124), `_MIN_REPS` and its check (:88, :502-504), the default `--out` (:505-506), `check_out` with `_OUT_FILES` = `measure.jsonl` and `summary.json` (:86, :507), `reach_h_sd` (:151). The plan's B20 `summarize` bullet carries the amendment note (`docs/superpowers/plans/2026-10-23-p1b-route-walking.md:1602`). Tests in `tests/route/test_measure_moves.py`: `test_directions_are_key_names` (:123), `test_moves_that_did_not_happen_are_left_out_of_reach_and_walk_speed` (:130), `test_teleport_settle_is_the_median_of_flat_horizontal_teleports` (:117), `test_out_holding_measure_jsonl_is_refused` (:194), `test_out_holding_summary_json_is_refused` (:216).

Affects: `measure.jsonl` and `summary.json` (a new file format), the plan's B20 summary definitions, and what B21 steps 4 and 5 read. `summary.json` has no count of the kept up-teleports (`reach_v_peak_mean` has no `_n`); the B21 report counts them from `measure.jsonl`.

Cost if wrong: small: summary arithmetic and the spelling of one field, before any real run.

Undo: the B1-B3 hunks of the piece-2 commit `252ca8f` (`feat(route): measuring procedure for the movement measurement tool (B20 piece 2)`, where they were applied): the `Key` names and `_HORIZONTAL`, `_NO_MOVE_PX` and the `flat`, `reach_v` and `walks` filters in `summarize`, the matching docstring lines, the amendment note at plan :1602 and the three tests named above.

Pointer: `.superpowers/orchestrator/2026-10-21-phase1/ledger-B.md:372-376`; `notes/B20-D1-adjudicator.md`, `notes/B20.md`.

## B20-D2 (2026-10-26): the measurement tool settles a move on one position held for 0.5 s, and only a fresh successful read confirms it

Question: how does `measure_moves` decide that a move has finished? A: poll every 10 ms, a move is settled when one position has been read unchanged for 0.5 s, give up after 5 s with no sample, and time the settle from the layer call's return to the first read of the final position. B: use the walker's own rule (two equal successful reads in a row, 2 s timeout, at least 0.15 s).

Ruling [as Hong]: option A', which is A plus a required fix: the 0.5 s of stability counts only on fresh successful reads (a failed read, whether a miss or "no new frame", neither confirms a settle nor resets it). The constants stay (10 ms, 0.5 s, 5 s). Ruled by the orchestrator in Hong's place while he was away, 2026-10-26; not yet seen by Hong when this entry was written. The fix is the one the B20 reviewer found as I1 and the adjudicator specified (`notes/B20-D2D3-adjudicator.md`, "Required change (A')").

Reason: the minimap position is re-detected every `MINIMAP_POSITION_RECALC_INTERVAL` frames (`src/vision/vision.py`), and on this PC with no game running the four matching calls alone took 0.27 s at 1920x1080 and 0.47-0.49 s at 2560x1440, so the frame feed can pause for 0.5 s or more. A stability test on wall-clock time then confirmed a settle on the last position read: probes settled a fall on (99, 41) in mid-air and a flat teleport on the position before the move (80, 60), and the reviewer's case of a teleport UP kept a peak of about 10 px instead of 23, enough to move `reach_v_peak_mean` past B21 step 4's 1 px threshold (how often it happens was not measured). Option B settles mid-fall and has a 0.15 s floor, so a flat teleport would measure the rule, not the game. A tolerance of 1 px for a flickering position was rejected: it would hide real 1 px slides and bias the reach; no sample or exit 10 is the loud, correct failure.

Reviewer verdicts: B20-R (`notes/B20-R.md`, I1): found the pause bug. B20-fix-R (`notes/B20-fix-R.md`, Q1): agree. The final Feature B review (`notes/FINB54.md`): agree; the walker's settle rule differs on purpose, because it controls movement and this one measures it.

As built (`src/route/harness/measure_moves.py` at 1523c99): `_POLL_S = 0.01`, `_STABLE_S = 0.5`, `_SETTLE_TIMEOUT_S = 5.0` (:92-94). `_Session._settle` (:392-414): the stability check is the `elif` at :410 inside `if is_successful(read)`, so only a fresh successful read confirms; `settle_s` is `(since_ns - start_ns)`, the time to the first read of the final position (:411); a timeout returns `settled=False` (:412-413). A move that does not settle gives no sample and a line on stderr (`_move`, :373-376), and the next move's `before` is settled afresh; a position that is lost, or never held, at the start of a phase or after a skipped move ends the run with exit 10 before another move (`_before`, :349-358). Docstring :44-49. Tests in `tests/route/test_measure_moves.py`: `test_a_capture_stall_does_not_settle_a_move` (:441), `test_misses_after_a_position_do_not_settle_a_move` (:449), `test_a_feed_pause_after_a_teleport_up_does_not_end_its_settle_on_a_partial_peak` (:457), `test_a_move_that_does_not_settle_gives_no_sample_and_the_next_before_is_settled_afresh` (:422), `test_character_lost_after_a_move_exits_10_after_cleanup` (:432). The new settle tests fail on `252ca8f` and pass on `eafb861`.

Not verified: a frame feed that stops for 5 s ends in "character lost" (exit 10), which can also mean a frozen capture. A teleport UP whose peak lies entirely inside a feed gap is not recovered (B20-D4 covers it as a lower bound). The check that the character's position does not flicker on recorded still frames was not run.

Affects: `measure.jsonl` values `after`, `peak_y` and `settle_s`, hence B21 steps 4 and 5; the walker's settle rule is untouched.

Cost if wrong: small: three constants, or the body of `_settle`.

Undo: to give up the fresh-read rule, revert the `_settle` hunks of `eafb861` (`fix(route): settle needs a fresh read in the measurement tool (B20 review)`); that restores the wall-clock stability the adjudicator showed settling mid-fall. `eafb861` also holds the B20 review's m2 fix (`_ask` and `_wait_line`, :246-286, a stray second Enter is dropped), which is a separate item and stays. To change the constants, edit `_POLL_S`, `_STABLE_S` and `_SETTLE_TIMEOUT_S` (first built in `252ca8f`).

Pointer: `.superpowers/orchestrator/2026-10-21-phase1/ledger-B.md:378`, `:382-383` and `:385`; `notes/B20-D2D3-adjudicator.md`, `notes/B20-R.md`, `notes/B20-fix-R.md`.

## B20-D3 (2026-10-26): the measurement tool keeps the CLI and file surface it was built with: `summary.json`, no `--seed`, no reaction wait, walk's exit codes, a prompt and countdown before each move that needs one

Question: the plan's B20 leaves the tool's surface open. As built: it writes `summary.json` (the `summarize` keys) next to `measure.jsonl`; it has no `--seed` (its timing generator is seeded from the OS) and no `session.json`; it sends moves with no stimulus, so no reaction wait; `held_ms` is the walk hold the timing model drew; its exit codes reuse walk's (0 done or stopped, 1 link down, 2 usage, 3 no first frame or no character, 4 connect failed, 5 icon failed, 6 a move failed, 7 the procedure raised, 9 stop control failed, 10 character lost); and it prompts for Enter and counts down 5 s before each phase and before each teleport-down and drop. A: keep as built. B: add `--seed` and a `session.json` like walk's.

Ruling [as Hong]: option A, no change. Ruled by the orchestrator in Hong's place while he was away, 2026-10-26; not yet seen by Hong when this entry was written. The ledger rated it send-to-Hong (CLI and file surface, extends the plan) and an opus-xhigh adjudicator advised it (`notes/B20-D2D3-adjudicator.md`).

Reason: every exit path checked matches walk's meaning, and none leaves a key held: a stop at a prompt has no move in flight, because each `play` has returned. Nothing B21 compares depends on an unrecorded random draw: the only draw that enters the summary is the walk hold, and it is recorded per sample as `held_ms`; the game's outcome cannot be reproduced from a seed anyway; a `session.json` would only add a third file to `_OUT_FILES`. `held_ms` as drawn is what the simulator uses for a walk (`src/route/sim/link.py:105-110`), so measured and simulated walk speed share a definition, and it differs from the hold the game sees by under 1% (inferred, about one connection interval). The prompt and countdown are needed because the character ends below the platform after a teleport down or a drop, and focus must move from the console to the game.

Reviewer verdicts: B20-R (`notes/B20-R.md`, Q2 and Q3): not unsafe, no exit path leaves a key held. B20-fix-R (`notes/B20-fix-R.md`, Q2): agree. The final Feature B review (`notes/FINB54.md`): agree, the exit codes are walk's list (`src/route/walk.py:6-9`) and both tools share `check_out`.

As built (`src/route/harness/measure_moves.py` at 1523c99): `_write_summary` (:417-421), run as the last cleanup step (:487); `_SUMMARY_FILE` and `_OUT_FILES` (:85-86); `TimingModel(np.random.default_rng())` (:458); `_play` passes no stimulus (:385-390); `_held_ms` (:295-297); exit codes in the docstring (:21-26); `_ask` (:246-264), `_countdown` (:289-292) with `_countdown_s = 5.0` (:102); the phases (:316-329); the stop guard (`_StopGuard`, :203-235).

Not verified: Ctrl+C at a prompt on a real console, and the 0.3 s typed-ahead Enter drop in `_ask`, have only run against pipes and fakes; Hong tries both once in B21. With a closed stdin (an agent shell) the tool exits 0 with an empty summary, so it is run in Hong's own console.

Affects: the tool's files and exit codes; B21 step 3 (how Hong runs it).

Cost if wrong: small CLI edits before B21.

Undo: nothing to revert; this ruling kept what `252ca8f` built. To switch to option B, add `--seed` and a `session.json` to `measure_moves.py` (and name `session.json` in `_OUT_FILES`).

Pointer: `.superpowers/orchestrator/2026-10-21-phase1/ledger-B.md:379-381` and `:385`; `notes/B20-D2D3-adjudicator.md`.

## B20-D4 (2026-10-26): the measured vertical reach is a lower bound, and B21 checks it on hand-teleport frames before it changes Kanna's 23

Question: the tool reads `peak_y` (the smallest y during a teleport UP) only after the teleport's key schedule has returned, about 60-310 ms after the blink (mean about 146 ms, plus the board's DONE latency; inferred from the key timings). If Kanna starts falling at once, the first frame it sees may be 1-2 px below the true peak, and B21 step 4 changes Kanna's 23 on a 1 px difference. A: no code; amend B21 so that step 2's recording includes at least 5 hand teleports UP into open space and step 4 treats `reach_v_peak_mean` as a lower bound, checked on those frames before 23 is changed. B: a watcher thread polls positions during the play and seeds `peak_y` (a thread, a test seam, another review cycle).

Ruling [as Hong]: option A. Ruled by the orchestrator in Hong's place while he was away, 2026-10-26; not yet seen by Hong when this entry was written. It changes Hong's B21 steps, so the orchestrator is to tell him before B21. The block came from the opus-xhigh adjudicator's own case analysis (`notes/B20-D2D3-adjudicator.md`), so no second adjudicator was used.

Reason: A costs nothing now and tests the assumption itself; B adds concurrent reads of one position source and a test seam to fix a bias that may not exist. Whether the game moves the character the instant the blink ends is unknown.

Reviewer verdicts: B20-fix-R (`notes/B20-fix-R.md`, Q3): agree; its m-a asked for the missing rule on how to use the frames, now in the plan. The final Feature B review (`notes/FINB54.md`): agree.

As built: no code change. `peak_y` is the smallest y read during the settle (`src/route/harness/measure_moves.py:406-407`, for a teleport UP only, :369-370), so the measured rise is at most the true one. The plan's amendment is in step 4 of task B21 (`docs/superpowers/plans/2026-10-23-p1b-route-walking.md:1627-1629`, commit `6a397b5`), with the rule for using the frames after it (:1630-1634, from this review: run `detection_check draft` on the step-2 recording, use the frames' rise if it is higher, and a rise above 24 is already conclusive).

Affects: Hong's B21 steps (step 2: at least 5 extra teleports UP into open space by hand while recording); the plan only.

Cost if wrong: a few extra teleports for Hong in B21; a peak missed inside a feed gap is not recovered, which the lower-bound rule covers.

Undo: delete the two amendment paragraphs in step 4 of task B21 in the plan (`6a397b5` and the FDOC54 docs commit).

Pointer: `.superpowers/orchestrator/2026-10-21-phase1/ledger-B.md:384` and `:386`; `notes/B20-D2D3-adjudicator.md`, `notes/B20-fix-R.md`.

Superseded in part by H37 (2026-10-27): Hong dropped the hand recording and the measuring tool, so step 2's five teleports up and step 4's check on those frames are no longer a requirement. What this entry says about `peak_y` being a lower bound is still true of the tool; the tool is just not run in B21 now.

## B19-D3 (2026-10-26): the walk refuses fewer than 1 lap with exit 2

Question: `walk --laps 0` or a negative value was accepted, and the run still walked one approach leg and sent keys before it stopped. Should `walk` refuse `--laps` below 1 up front with a message and exit 2 (A), or keep accepting it (B)? The B19 reviewer raised it as minor M3 (`notes/B19-R.md`); the plan did not say.

Ruling [Hong]: option A, decided by Hong on 2026-10-26: the walk refuses fewer than 1 lap with a message and exit 2, and no keys are sent. A typo such as `--laps 0` used to walk one approach leg.

Reason: a lap count below 1 has no meaning, and the old behaviour sent keys to the game for a typo. Leaving `--laps` out still means "until stopped".

As built: `main` checks `args.laps` right after the `--supervised` check and before `--minutes`, `--seed`, `--out`, the map load, the capture or the link (`src/route/walk.py`, the `args.laps < 1` test). It prints "error: --laps must be at least 1, got N" on stderr and returns 2. Tests: `test_laps_below_one_exits_2_before_anything_starts` (0, -1 and -5: exit 2, the message, no capture, no link, no stop control, no key, no `--out` folder) and `test_one_lap_is_accepted`, in `tests/route/test_walk_cli.py`. The exit-2 list in the `walk.py` docstring, the `--laps` help text and `src/route/CLAUDE.md` say so.

Affects: the walk CLI's exit 2 cases.

Cost if wrong: none; a user who wants a zero-lap run has no use for one.

Undo: remove the `args.laps < 1` check in `main` and the test.

Pointer: `.superpowers/orchestrator/2026-10-21-phase1/ledger-B.md:355-356` and `:555`; `notes/B19-R.md` (M3).

## B22-inv-D1 (2026-10-26): the Feature B docs are written now as B22a, with B21's results listed as not done yet

Question: plan task B22 (the "as built" docs) depends on B21, the supervised in-game session, which Hong parked on 2026-10-26. A: write the docs now as B22a, record B21's results as not measured and not signed off, note the split in the plan, and fill them in later as B22b. B: wait until B21 has run. C: write the docs now with placeholders where B21's numbers will go.

Ruling [Hong]: option A. Hong's decision (2026-10-26), as the ledger records it: "B22-inv-D1 → A, write the Feature B docs now (B22a), B21's measurements listed as not done yet, B22b after B21."

Reason: the investigation (`notes/B22-inv.md`) found that only B21's own results (reach on FF2, movement values, detection on moving frames, the 10-lap sign-off) need B21; the B14 and B18 results, the deviations and the commands are known now. B leaves the offline state undocumented for an unknown time. C would put something that looks like a number where none exists, against the plan's rule that every number comes from an output and against H3 (reach is not measured on FF2).

As built: spec B has an "As built" section (status, results against the targets with the run outputs quoted, reach and movement values as not measured on FF2, deviations, a "Not done yet" list). `src/route/CLAUDE.md` gained the harness commands (the 1000-lap target run, the detection check, the recorder, the walk CLI and the measurement tool). Plan task B22 records the split and the task list row says so. The 1000-lap run was repeated at `1257222` because the earlier recorded run came before the planner fix B14-inv-D1.

Affects: spec B, the plan's task B22 and task list, `src/route/CLAUDE.md`. No code.

Cost if wrong: none worth naming; B22b replaces the "Not done yet" list with B21's outputs.

Undo: revert the three B22a docs commits on `p1b-route-walking`.

Pointer: `.superpowers/orchestrator/2026-10-21-phase1/ledger-B.md:575` (B22-inv result) and `:577` (Hong's decision); `notes/B22-inv.md`.

## B22-inv-D2 (2026-10-26): Hong's "B21 not needed for now" does not waive the in-game sign-off before Feature B merges

Question: Hong said the B21 in-game measurement session was not needed for now, because he is fairly certain the recordings are right. Does that waive the in-game sign-off that H4 requires before Feature B merges to `master`?

Ruling [spec-text]: option A, no change. Ruled by the orchestrator from the text, not by Hong. Feature B stays unmerged and is "ready for sign-off, not signed off". H4 says "B, C and D merge only after in-game sign-off", spec B "Shipping" says "Passing the offline targets makes it ready for sign-off, not ready to merge", and plan hard rule 1 says B merges to `master` only after the in-game sign-off passes. Hong's words are about the measurement session and do not mention merging.

Reason: waiving a merge gate is Hong's to do, in his own words; reading it into a remark about recordings would be a guess. His remark is also not a measurement: the 19/23 reach is his own measurement, not made on Fossil Foundry 2 (H3), so the docs do not record reach as measured on FF2.

As built: spec B's "As built" status line reads "ready for sign-off, not signed off, and not merged", and its "Not done yet" list names the sign-off.

Affects: whether Feature B can merge (it cannot yet); the status line in spec B.

Cost if wrong: none; Hong can waive the gate at any time.

Undo: nothing to undo; to waive the gate, Hong says so and this entry is superseded.

Pointer: `.superpowers/orchestrator/2026-10-21-phase1/ledger-B.md:576` (the ruling) and `:551` (Hong's words); `notes/B22-inv.md`.

## H22 (2026-10-26): Hong is sent only the questions on a six-point list (question-review page Q1)

Question: which questions should the orchestrator send to Hong? (Q1 on the question-review page: A, the new six-point list; B, the same but internal file formats still come to him; C, keep the old list.)

Ruling [Hong]: option A. Hong answered the seven questions on the question-review page (`notes/QA60-page/index.html`, analysis in `notes/QA60-a.md`) with "i agree with all recommendations except for 5, my answer is b". Hong's decision (2026-10-26): Q1 A. A question reaches him only if:
1. It needs a fact about the game, the maps or the character that nobody has written down yet.
2. It changes what the bot does in the game or what the app shows in normal use, and the options would look different to him.
3. It changes what the spec means, a sign-off or merge rule, or what "working" means.
4. It loosens a safety rule or the "inputs must look human" rule, or overturns something he decided.
5. It spends more than the budget, or trades quality for cost.
6. It needs his hands, eyes or presence. These come as one batched list with a proposed time.

Plus, as today, every push, delete or publish still reaches him, except the two standing yeses in H24 (push) and H25 (delete). (From the page's "The proposal".)

Note: The orchestrator's readings, not on the page he saw (QA60-a section 5a; applied in the skill by SKL61): "written down" means the spec, this file and his standing facts; a plan that is silent, or worded loosely while its intent is clear, is not a departure from the spec; with Q5 B there is no per-feature budget, so point 5 reads as money he has not approved (H30 is the approved spend).

Reason (not given by Hong; the page's recommendation, which he accepted): it is his 22 October rule (H10) plus the game facts and setup facts that mattered. Of the 65 questions put to him, about 20 (code details, edge cases, most of process) would not have reached him, and in those his answer differed from the recommendation only 3 times (page, "The proposal").

Affects: the orchestrator skill's "Send it to Hong" list and "Rule on it yourself" section, and the agent rules; applied in the skill by the SKL61 unit. It narrows what H10 sends him without removing H10's two cases.

Cost if wrong: a question he wanted to see is decided for him; each such ruling is logged with its undo, and the morning summary lists the ones that change a command.

Undo: Hong says so; the skill's list goes back to "any criterion in agent-rules section 3".

Pointer: `.superpowers/orchestrator/2026-10-21-phase1/ledger-B.md:610`; `notes/QA60-a.md` section 5a.

Extended by H32: a question the asker cannot place on this list is also sent to him (Hong, 2026-10-26: "if an agent doesnt know if something needs me, assume it does").

## H23 (2026-10-26): The morning summary lists the safer-option choices, the edge cases and the command changes (question-review page Q2)

Question: what goes in the morning summary besides what needs Hong? (Q2: A, the choices of the safer option, edge cases, and changes to a command he might run; B, every decision an agent made; C, only the safer-option choices.)

Ruling [Hong]: option A. Hong answered the seven questions on the question-review page (`notes/QA60-page/index.html`, analysis in `notes/QA60-a.md`) with "i agree with all recommendations except for 5, my answer is b". Hong's decision (2026-10-26): Q2 A. The morning summary lists: choices where an agent picked the safer option (refuse bad input, never overwrite data); problems that only happen if someone edits a file by hand; and choices the plan did not cover that change a command he might run. These are decided by an agent with a second, senior agent checking, recorded with how to undo them, and listed so he can overturn them. Everything else (how the code is organised, internal file formats, log fields, test rules, tooling, process) is recorded only (in the ledger, and in this file when it is a design choice).

Reason (not given by Hong; the page's recommendation, which he accepted): the page's "The proposal", the cards "agents decide, in the morning summary" and "agents decide, logged".

Affects: the skill's morning summary and "Rule on it yourself" levels (SKL61).

Cost if wrong: a choice he would have changed sits one night in the summary before he sees it.

Undo: Hong says so.

Pointer: `.superpowers/orchestrator/2026-10-21-phase1/ledger-B.md:610`; `notes/QA60-a.md` section 5b.

## H24 (2026-10-26): Agents may push finished, reviewed feature work to origin without asking, and each push is listed in the morning summary (question-review page Q3)

Question: may agents push finished feature work to Hong's GitHub without asking each time? (Q3: A, a standing yes; B, a standing yes with each push listed in the morning summary; C, keep asking.)

Ruling [Hong]: option B. Hong answered the seven questions on the question-review page (`notes/QA60-page/index.html`, analysis in `notes/QA60-a.md`) with "i agree with all recommendations except for 5, my answer is b". Hong's decision (2026-10-26): Q3 B. This standing yes is for this job (Helix) only, not a general permission. It covers one thing: a push of a finished feature branch to `origin` (`Qing-debug/helix-claude`). Never to `master`, never to `upstream` (`Qing-debug/Helix`), never forced, and only after a review of the branch has returned Approved and the full test suite (`python tools/check.py --full`) passes on the commit pushed. (The page said "only after review and all tests pass"; "Approved", "full" and "for this job (Helix) only" are the orchestrator's reading and limit, not on the page.) Each push (branch and SHA) is listed in the morning summary.

Reason (not given by Hong; the page's recommendation, which he accepted): he had said yes to every earlier push question (5 of 5, per the page); the summary keeps him informed.

Affects: when agents may push (the orchestrator dispatches a separate unit for it). The "Safety rules" in the root CLAUDE.md still forbid any push to `upstream`. It is an exception to H10's note that a push still needs his yes (inference: Hong did not mention H10; the link is the orchestrator's reading of the two entries). The first push under it was `p1b-route-walking` at `5257662` (unit PUSH61, ledger-B.md:611 and :615).

Cost if wrong: a push of work he did not want published yet; revert with a follow-up commit.

Undo: Hong says so; pushes need his yes again.

Pointer: `.superpowers/orchestrator/2026-10-21-phase1/ledger-B.md:610`.

## H25 (2026-10-26): Agents may delete branches they made, once merged or copied elsewhere (question-review page Q4)

Question: may agents delete the branches they made without asking? (Q4: A, a standing yes once the branch is merged or copied elsewhere; B, keep asking.)

Ruling [Hong]: option A. Hong answered the seven questions on the question-review page (`notes/QA60-page/index.html`, analysis in `notes/QA60-a.md`) with "i agree with all recommendations except for 5, my answer is b". Hong's decision (2026-10-26): Q4 A. An agent-made branch may be deleted without asking once it is merged or a copy of it exists elsewhere. The model-comparison branches (`ab/*`) are kept regardless. This standing yes is for this job (Helix) only. It covers branches agents created and nothing else: deleting anything else, or a branch with no other copy, still needs his yes.

Orchestrator note (not Hong's words): H25 is an exception to H8's branch rule ("no deleting branches") for agent-made branches that are merged or copied elsewhere (`ab/*` kept); every other delete still follows H8, as narrowed by H12. The limit "for this job (Helix) only" and the sentence "deleting anything else ... still needs his yes" in the Ruling above are the orchestrator's, not on the page.

Reason (not given by Hong; the page's recommendation, which he accepted): he had said yes every time he was asked (page, Q4).

Affects: the orchestrator's branch cleanup. It extends H12 (inference, as for H24), which already lets the orchestrator delete helper worktrees and branches after their merge into a local feature branch.

Cost if wrong: a deleted branch holding work with no other copy; the condition (merged or copied) is what prevents that.

Undo: Hong says so.

Pointer: `.superpowers/orchestrator/2026-10-21-phase1/ledger-B.md:610`; H12.

## H26 (2026-10-26): The nine model questions stay with Hong, and there is no per-feature budget (question-review page Q5)

Question: which model does which work? (Q5: A, Hong sets a dollar budget per feature and agents choose models within it, reporting in the morning summary; B, keep the nine open model questions for Hong.)

Ruling [Hong]: option B, the one answer where he differed from the recommendation. Hong's decision (2026-10-26): "i agree with all recommendations except for 5, my answer is b". The nine model-table questions stay his to answer, and no per-feature budget is set.

Reason: not given beyond the answer. The recommendation had been A ("model choice is process; money is yours").

Affects: model choice (the orchestrator skill's model table and the A/B ladder). Questions 2 to 9 of the model page are still his and still open; so is his yes on the final model-table text (see H29).

Cost if wrong: model questions wait for him instead of being decided inside a budget.

Undo: Hong says so.

Pointer: `.superpowers/orchestrator/2026-10-21-phase1/ledger-B.md:610`.

Answered by H35: his answers to model page Q2 to Q9, to the three cards under "About the table text" and his yes to the whole model-table text (2026-10-27).

## H27 (2026-10-26): Agents must not overturn a decision Hong made without asking him (question-review page Q6)

Question: Hong had said decisions already in this file stay with him. Which did he mean? (Q6: A, agents must not overturn a decision he made without asking; B, every new entry in this file needs him.)

Ruling [Hong]: option A. Hong answered the seven questions on the question-review page (`notes/QA60-page/index.html`, analysis in `notes/QA60-a.md`) with "i agree with all recommendations except for 5, my answer is b". Hong's decision (2026-10-26): Q6 A. New entries in this file do not need him; overturning one he made does.

Reason (not given by Hong; the page's recommendation, which he accepted): read literally, "decisions already in docs/decisions.md stay with him" would send every new entry to him and reopen what Q1 narrows (`notes/QA60-a.md` section 4).

Affects: the orchestrator skill (a rule that nothing overturns his decisions) and the agent rules; an agent whose work seems to need that raises it.

Cost if wrong: none worth naming.

Undo: Hong says so.

Pointer: `.superpowers/orchestrator/2026-10-21-phase1/ledger-B.md:610`.

## H28 (2026-10-26): The list of Hong's standing facts is written into this file (question-review page Q7)

Question: write the list of standing facts into this file? (Q7: A, yes; B, no.)

Ruling [Hong]: option A. Hong answered the seven questions on the question-review page (`notes/QA60-page/index.html`, analysis in `notes/QA60-a.md`) with "i agree with all recommendations except for 5, my answer is b". Hong's decision (2026-10-26): Q7 A. The list is the heading "Hong's standing facts" at the top of this file.

Reason (not given by Hong; the page's recommendation, which he accepted): four of the six answers where he differed from the recommendation came from a fact or principle he would say the same way every time; writing them down once lets agents check before they ask (page, "The proposal").

Affects: the orchestrator skill and the agent rules point to the list by that heading.

Cost if wrong: none.

Undo: remove the heading; agents then ask again.

Pointer: `.superpowers/orchestrator/2026-10-21-phase1/ledger-B.md:610`; "Hong's standing facts" above.

## H29 (2026-10-26): Haiku may do routine implementation units, nothing else (model page MX61-D1; not yet in the skill)

Question: may Haiku be used at all? (MX61-D1: A, keep Haiku banned and routine implementation stays on sonnet-high; B, a narrow exception: `orch-haiku-high` for routine implementation units only, sonnet-high as fallback and comparison arm, Haiku banned everywhere else.)

Ruling [Hong]: option B. Hong's decision (2026-10-26): "ill allow haiku for routine coding". Haiku at high effort may do routine implementation units; Sonnet at high effort is the fallback and the comparison arm; Haiku stays banned for everything else.

Reason: not given. The ledger records the cost of B as an edit to the fixed model rule in `SKILL.md`, `agent-rules.md` and the `haiku-high` preset description, a preset rebuild and a session restart, with a small quality risk (0 Critical, 1 Important over 2 units). The saving is small (about $1-2 over Features C and D).

Affects: the model policy of the orchestrator skill. Not yet applied: the skill text that applies it waits for Hong's yes on the final model-table text, after the MX61 fix and re-review. Model page questions 2 to 9 remain his (H26).

Cost if wrong: a routine unit of lower quality; the Sonnet fallback and the review step catch it.

Undo: Hong says so; Haiku is banned again.

Pointer: `.superpowers/orchestrator/2026-10-21-phase1/ledger-B.md:617`; `notes/MX61-R.md`.

Confirmed by H35 (2026-10-27): Haiku at high effort builds routine units first, Sonnet at high effort is its backup and the model it is compared against (QB80), and he said yes to the whole model-table text that this entry was waiting on. The orchestrator reads the routine row as provisional until about 7 to 10 routine comparisons (see H35). The unit that applies it to the skill is MX65-apply.

## H30 (2026-10-26): Hong approves the model-ladder spend: about $87 before 2026-11-04, about $80 of data gathering, and a $200 stop that returns to him (model page MX61 money)

Question: may the model comparison spend the money its plan needs?

Ruling [Hong]: yes. Hong's decision (2026-10-26): "im fine with having to spend that money". Approved: the code-review ladder at about $87 (a range of about $60-120) before 2026-11-04; data gathering of about $80 over Features C and D; and a stop at $200 that returns to him.

Reason: not given by Hong beyond the words above. The figures are the plan's own (the $87 recomputes per `notes/MX61-R.md`; the $200 stop is the planner's proposal).

Affects: item 5 of the six-point list (H22): this spend is approved, so it does not reach him again unless the $200 stop is hit. The figures are those of the plan as MX61-R reviewed it (verdict Changes needed); the fixed plan and the final text still come back to him (H29).

Cost if wrong: money spent on a comparison that proves little.

Undo: Hong says so.

Pointer: `.superpowers/orchestrator/2026-10-21-phase1/ledger-B.md:617`.

Superseded in part by H35 (2026-10-27): the stop is $300, not $200, and every task is compared, not every third (about $72 to $120 more than every third would cost, PGQ65). The code-review comparison at about $87 stays: he confirmed it (model page Q7).

## H31 (2026-10-26): Away mode from the late evening of 2026-10-26: autonomous within the new criteria

Question: what should the orchestrator do while Hong is away? (His away policy had changed several times since H6.)

Ruling [Hong]: autonomous within the criteria he had just set. Hong's decision (2026-10-26, going to bed): "be autonomous but within the boundaries we've set, anything that you think requires my input based off our criteria ... + the input i just made". An item that meets the six-point list (H22) is parked under "Waiting on Hong". Everything else agents decide, logged or logged and listed in the morning summary (H23). Pushes of finished feature work and deletes of agent-made branches under H25's condition (merged or copied elsewhere) go ahead (H24, H25). Nothing overturns his decisions (H27). In the morning the orchestrator lists what needs him.

Reason: not given beyond the words above.

Affects: how orchestrators and agents decide while he is away; it replaces the earlier away policies (H6, H9) for this stretch (inference: his words do not mention H6 or H9).

Cost if wrong: a decision made that he would have wanted to see; the morning summary lists the kinds he may overturn.

Undo: Hong says so.

Pointer: `.superpowers/orchestrator/2026-10-21-phase1/ledger-B.md:614`.

Narrowed by H32 and H33: a case nobody can place on the six-point list is not decided by agents while he is away; it waits for him.

## H32 (2026-10-26): If an agent doesn't know whether something needs Hong, it assumes it does; every question put to him goes into a question bank (QB79)

Question: when the asker cannot tell whether a question needs Hong, should it rule on it itself? (QB79: yes or no to the proposed line "if unsure, rule on it yourself at the morning-summary level and put it at the top of the morning summary".)

Ruling [Hong]: no. Hong's decision, logged by retired orchestrator 61 after orchestrator 63 paused (ledger-B.md:681; the question bank dates it 2026-10-26, and ANS64 found the answer at 2026-10-26T09:54:10 in his session 9bd46827, line 631): "No, err on the side of caution"; "if an agent doesnt know if something needs me, assume it does." He added a standing process: every question put to him goes into a running question bank with his answer, and later periodic reviews like the question-review page (H22 to H28) use the bank to add examples of which kinds of question need him and which don't. (The sentence about the bank is the ledger's wording of his request, not his typed words.)

Orchestrator's rulings on how the bank works (not Hong's words; ledger-B.md:683 `Ruling [process]`, and :695 for the second point):
- The bank is one file for all jobs, outside this repo: `C:/Users/Qing_programming/.claude/orchestrator/question-bank.md`, because it is about how orchestrators sort questions, not about one repo.
- An entry holds the date, the job and part, who asked, the question as put to him, the options and recommendation, his answer verbatim, the send-to-Hong item (or `unsure`) and what it changed. An entry is added as `open` when a question is put to him and filled in when he answers (the skill review SKL64-R found that adding entries only when he decides would miss questions he has not answered yet).
- A review runs at the end of each feature or after 20 new entries since the last review, whichever comes first. The first one after the question-review page ran as QBR65 on 2026-10-27 (it covered QB66 to QB86); the next is due at QB106 or at the end of Feature B, whichever comes first. Any change to the send-to-Hong list that a review proposes still needs his yes.

Reason: not given in the ledger line. The memory note `unsure-means-ask-hong.md` records it as: he would rather err on the side of caution while the criteria for what needs him are still being learned from real examples.

Affects: the orchestrator skill carries both now (unit SKL64, confirmed Approved by SKL64-R2, ledger-B.md:713): `SKILL.md` "Send it to Hong" (an unsure case counts as on the list; its bank entry records the item as `unsure`) and the new "Question bank" rule under "Decisions and escalation", plus `references/records.md` and the T1 row of `references/tunables.md`. The proposed "rule it yourself" line is gone from the skill. `agent-rules.md` needed no change (its section 3 already covers an unsure case), so no preset rebuild. It extends H22: a question the asker cannot place on the six-point list is also sent to him. It narrows H31 for the unsure case (see H33 for away mode).

Cost if wrong: more questions reach him than he needs; the bank reviews are meant to show which kinds he did not need.

Undo: Hong says so; the "if you can't tell" clause in `SKILL.md` "Send it to Hong" and the "Question bank" rule are removed.

Pointer: `.superpowers/orchestrator/2026-10-21-phase1/ledger-B.md:681` (his words), `:683` (the bank rule), `:685` (ANS64, the earlier answer), `:691` to `:700` (SKL64, SKL64-R, SKL64-fix); question bank QB79; `notes/SKL64.md`, `notes/SKL64-R.md`, `notes/SKL64-R2.md`.

## H33 (2026-10-27): While Hong is away under "decide as Hong", a case nobody can place on the list waits for him (SKL64-D1, QB81)

Question: when the asker cannot tell whether a question needs him and he is away under "decide as Hong", does the case wait for him (A, the recommendation, like items 1, 5 and 6 of the H22 list) or go to the adjudicator path that items 2 to 4 take (B)? (SKL64-D1, raised by SKL64 and made provisionally as A; QB81.)

Ruling [Hong]: option A. Hong's decision (AskUserQuestion, orchestrator 64, 2026-10-27; ledger-B.md:699): "Yes, it waits (Recommended)". The skill had carried A provisionally since SKL64; it is final now.

Reason: the recommendation's own reason was his rule in H32, "assume it needs me". Hong gave none beyond the answer.

Affects: `SKILL.md` "Send it to Hong" and "When Hong is away" (an unsure case waits under "decide as Hong", with items 1, 5 and 6). FIX64 removed the provisional mark (ledger-B.md:706, confirmed by SKL64-R2 at :713). It narrows H31, whose "everything else agents decide" no longer covers a case nobody can place.

Cost if wrong: an unsure case sits until he is back instead of being ruled by an adjudicator.

Undo: Hong says so; the "waits" clause in `SKILL.md` "Send it to Hong" and the words "unsure cases" in the "When Hong is away" sentence are removed, so an unsure case goes the adjudicator path of items 2 to 4 (SKL64-D1's undo, as the ledger recorded it at :691).

Pointer: `.superpowers/orchestrator/2026-10-21-phase1/ledger-B.md:691` (the provisional decision), `:692` (sent to him), `:699` (his answer); question bank QB81; `notes/SKL64.md`.

## H34 (2026-10-27): The 45 local `probe/*` branches were backed up into one bundle file and then deleted, under Hong's explicit yes (QB6)

Question: what happens to the 45 local `probe/*` branches (the change-cost probe, made by agents on 2026-10-25; none merged, and each is the only copy of its commit, so H25 alone did not cover deleting them)? Options: A, back up all 45 into one bundle file and then delete them (the recommendation); B, delete them outright; C, keep them. (The `ab/*` half of QB6 was answered by H25: `ab/*` is kept regardless.)

Ruling [Hong]: option A. Hong's decision (AskUserQuestion, orchestrator 64, 2026-10-27; ledger-B.md:699): "Back up, then delete (Recommended)". The ledger records it as his explicit yes for this delete.

Done by unit PRB64-del (ledger-B.md:705): the bundle is `C:/Users/Qing_programming/helix-backups/probe-branches-2026-10-27.bundle` (about 94 MiB, the full history of each branch). `git bundle verify` said "The bundle records a complete history."; `git bundle list-heads` showed 45 refs with the expected tips; a test restore into a throwaway bare repo gave 45 identical branches. Then 45 `git branch -D`: a `for-each-ref` before and after differ by exactly the 45 `refs/heads/probe/*` lines, so the 59 `ab/*` branches and everything else kept their SHAs. No push, fetch or checkout; the working tree stayed clean on `p1b-route-walking`. The 45 names and tips are in `notes/PRB64-del.md`.

Restore: `git fetch C:/Users/Qing_programming/helix-backups/probe-branches-2026-10-27.bundle 'refs/heads/probe/*:refs/heads/probe/*'`

Reason: not given beyond the answer. The facts behind the question (ledger-B.md:689): nothing in the repo reads the probe branches; `ab/RESULTS.md` cites result files, not the branches.

Affects: local branches only; none were ever on `origin` or `upstream`. H25 is unchanged: the yes covers these 45 branches. Whether a bundle counts as "a copy elsewhere" under H25 was unsure, so it was sent to him (ledger-B.md:690); his answer is for these 45, and the ledger records no general ruling on it.

Cost if wrong: the branches are gone from the repo; the only copy is the bundle file, which lives outside the repo (`C:/Users/Qing_programming/helix-backups/`).

Undo: the restore command above.

Pointer: `.superpowers/orchestrator/2026-10-21-phase1/ledger-B.md:689` and `:690` (the facts and the question), `:699` (his answer), `:705` (the delete); question bank QB6; `notes/PRB64.md`, `notes/PRB64-del.md`.

## H35 (2026-10-27): Hong's answers on the model table: which model does which work, every task compared, and a $300 spend stop (model page Q2 to Q9 and the rest of the MX61 text)

Question: which model does which work, how much of it is compared, and what may that cost? The model page that orchestrator 64 published (https://claude.ai/artifact/L3EpkwqPiph6mZS5CAqgVS) had eight numbered cards (Q2 to Q9; Q1, cost counted in dollars rather than tokens, was answered on 2026-10-26: AN55-D1, ledger-B.md:585) and three more under "About the table text": Opus at medium; Sonnet high as Haiku's fallback and comparison model; the whole model-table text. H26 had left the nine model questions with him, and H29 and H30 were waiting on this yes.

Ruling [Hong]: yes to the whole text, with the answers below. All are from 2026-10-27: pasted page answers (ledger-B.md:728) unless marked chat (talking to orchestrator 65). The quotes are as the question bank has them.
- Ordinary work (several files, some design choices) [Q2]: "A. Sonnet xhigh".
- The cost check against the next good setting [Q3]: "B. No, keep it literal". The rule's cost check stays as written.
- Routine work [Q4]: "Change: need more test  for  routine row so we can  be sure." Asked whether comparing every task (below) was enough or he wanted extra routine-only runs, he answered in chat: "7-10 routine comparisons sounds good". So: no extra routine-only runs. Haiku at high effort builds routine units first (H29).
- Haiku's backup and comparison model: in chat he first wrote "use sonnnet high for routine tasks"; asked whether that meant Haiku builds routine tasks first with Sonnet high as its backup and comparison, or dropping Haiku, he chose the first (the page card read "Yes, Sonnet high for both"; same answer). The ledger and QB80 record this wording as his answer, which appears to be the orchestrator's wording of the option he accepted: "Haiku still builds routine tasks first, as you agreed earlier, with Sonnet high as its backup and the model it's compared against." H29 (Haiku for routine coding) stands.
- Subtle work (timing, control loops, concurrency, data loss) [Q5]: "A. Opus xhigh".
- Plan reviews [Q6]: "B. Keep Opus xhigh".
- The code-review comparison [Q7]: "Confirm: A. Run it". Six finished changes with bugs planted in them, each reviewed by six model settings, on this PC from 2026-10-28 to 2026-10-31 (no later than 2026-11-03, because the cloud credit ends 2026-11-04), about $87 to $89, the spend H30 approved.
- The other gaps [Q8]: "Confirm: C. The plan's mix"; in chat: "im finee with optionn C" and the card's text "The plan's mix: fill mutation testing (on the compared tasks, inside the about $80 for Features C and D), re-run after C and D, and leave the Feature A baseline out." (ledger-B.md:719).
- Ties [Q9]: "B. The cheaper in dollars".
- Opus at medium: "B. Narrow it". Per the page card, Opus at medium no longer does routine coding; it stays for data gathering and the mechanical units.
- Every task is compared, not every third (chat): "lets do every task, more data the better" (ledger-B.md:718). A second model builds every task.
- The spend stop is $300, was $200 (chat): "yes raise to 300" (ledger-B.md:725). Comparing every task costs about $72 to $120 more than every third; the total comes to about $241 to $289 (PGQ65, ledger-B.md:714), which is past the old $200 stop.
- The whole model-table text: "Yes" (with the chat changes, every task and the $300 stop).

Orchestrator notes (not Hong's words):
- "The routine row stays provisional until about 7 to 10 routine comparisons have been made" is the orchestrator's reading of his Q4 answer and his "7-10 routine comparisons sounds good" (`Ruling [spec-text]`, ledger-B.md:732). The 2 comparisons measured so far are Feature B's ladder; the other 5 to 8 come from the tasks of Features C and D.
- The page's sentence on mutation testing was wrong twice, so the text is corrected when it is applied: the bugs are planted in a model's own code and its own tests must fail on them; the second judge reviews the whole task, not each bug (ledger-B.md:715). Q8 C means mutation testing as corrected.

Reason: Hong gave a reason twice: "more data the better" (every task) and "so we can be sure" (the routine row). Otherwise not given.

Affects: the orchestrator skill's model table and the model comparison rules (`references/model-ab.md`), the fixed model rule for Haiku (H29) and for Opus at medium, and the spend stop; the plan is `notes/MX61-plan.md`. Applying it to the skill is the unit MX65-apply (dispatched, ledger-B.md:733; backups in `notes/MX65-backup/`), and the skill now carries H35's table.

Cost if wrong: money, up to the $300 stop; and a routine model picked on few units, which the provisional status and the comparison of every task are there to check.

Undo: Hong says so; the skill's table goes back to the text in `notes/MX65-backup/`.

Pointer: `.superpowers/orchestrator/2026-10-21-phase1/ledger-B.md:718`, `:719`, `:725`, `:726`, `:728`, `:731` and `:732`; question bank QB9 (q2 to q9), QB77, QB78, QB80, QB82, QB83 and QB84; `notes/MAP65.md` (which answer belongs to which card), `notes/PGQ65.md` (the costs).

## H36 (2026-10-26): Model cost is counted in dollars, as the stand-in for his plan's usage meter (model page Q1, AN55-D1)

Question: should the model comparison's cost-of-ownership clause compare setups in tokens (Part 4 of `model-ab.md` as it was written) or in dollars? (AN55-D1: A, dollars; B, tokens as written. The orchestrator recommended A.)

Ruling [Hong]: option A, dollars. Hong's decision (2026-10-26; his session 27183e64, lines 472 and 488; question bank QB9): "Cost should be that  it uses the  last amount of usage" (the ledger reads this as "least amount of usage"). Asked to confirm that he meant the usage meter on his Claude plan, he answered: "yees usage meer  on my claude p lan,  c an you research how it counts it". So cost is counted in API-equivalent dollars as the stand-in for his plan usage meter. The orchestrator logged it as A after the research he asked for (USE60) came back (`Decision (Hong)`, ledger-B.md:585).

Reason: his words above, that the cost to count is how much of his plan usage a setup uses. The research (USE60, ledger-B.md:584) found that the meter's formula is not published and that the official pages say it is token-based and weighted by model (Opus several times Sonnet, Sonnet more than Haiku), so it is closer to dollars than to raw tokens; that is the researcher's inference, not a stated fact.

Affects: how the model comparison counts cost, and so the rows of the model table that depend on the cost check. It was the first of the nine model questions that H26 left with him; the other eight were answered in H35, which also keeps the cost check literal (Q3) and settles ties by the cheaper dollars (Q9).

Cost if wrong: a setup that is cheaper in dollars but uses more of his plan meter, or the reverse. The ledger notes that only a measurement of the meter per run would give the real ratio between model families.

Undo: Hong says so; the cost clause goes back to tokens.

Pointer: `.superpowers/orchestrator/2026-10-21-phase1/ledger-B.md:516` (the question), `:584` to `:585` (USE60 and his decision); question bank QB9; `notes/USE60.md`.

## H37 (2026-10-27): B21 is shortened to two 1-lap walks and then 10 recorded laps; the measuring tool and the hand recording are dropped (QB87, QB88)

Question: the in-game walk test (plan task B21, parked by Hong on 2026-10-26) had eight steps: a 120 s hand recording with at least 5 teleports up (B20-D4), the measuring tool, a reach decision, a sim-realism comparison, a detection check on the recording, two 1-lap walks with one F12 press, and 10 laps. Hong asked first "what is this actually for?" and then why he would need to hand-record 5 teleports up and use the measuring tool. After orch 66 explained each step in chat (QB87), orch 67 asked him to confirm a shortened order (QB88; written up in `notes/WALK67.md`).

Ruling [Hong]: shortened. His words (question bank QB87): "no need for the hand recoridng and measuring tool, i measured it and it the pixels are correct". He confirmed the order in QB88 (AskUserQuestion, option "1-lap walks, then 10 (Recommended)"): B21 is now two 1-lap walks, the first with one F12 press and the second without (plan step 7, `docs/superpowers/plans/2026-10-23-p1b-route-walking.md:1659-1663`; QB88: "press F12 once"), then 10 laps with the recorder on. The 10 laps are the Feature B sign-off bar (H4; spec B Targets, "10 consecutive laps with no manual intervention", `all_reached` true on 10 consecutive laps in `laps.jsonl`). The recorder is the walk CLI's `--record`.

Reason: his words above. He measured the teleport reach and the walk speed himself, so he takes his numbers (Kanna's 19 px sideways and 23 px up, `src/classes/classes.py`) as correct on Fossil Foundry 2. The walker re-plans from the measured position after each move, so a wrong reach number should waste moves and not break the run (`notes/WALK67.md`, section 4; read from `src/route/CLAUDE.md`, "The loop", not run live).

What this leaves unmet (from `notes/WALK67.md`, "What the short order leaves unmet"; these now rest on his own measurement and on the walk itself):
- Plan steps 2 (120 s hand recording), 3 (measuring tool), 4 (reach decision) and 5 (sim realism: compare the measured `walk_px_per_s`, `teleport_settle_s`, `drop_settle_s` and platform ends with the simulator) are not run. `SimParams` and `MovementParams` stay at their estimates (`src/route/params.py:16-19`, all marked ESTIMATE) and `tests/fixtures/ff2_terrain.json` stays hand-drawn from the node layout. The 1000-lap simulator result therefore still describes the estimated movement values, not measured ones.
- Spec B Goal 2 and decision H3 (measure the reach on FF2 in the first supervised session) are met only by his own measurement, which no tool output records. The plan's "Not verified" items 1 (movement values) and 4 (vertical reach measurable by landing) are his claim, not an output.
- Plan step 6 and the spec's detection target on moving frames ("Not verified" 10: found within 1 px in at least 99.5% of frames, never more than 3 px away) are not run in B21. The detection check has so far run on standing frames only (304 of 304, one standing spot). The frames the 10-lap `--record` run saves can feed `detection_check draft` and `check` later; checking the contact sheets stays Hong's.
- B20-D4's requirement of 5 hand teleports up is superseded (pointer added at B20-D4). The B20-D3 tries (Ctrl+C at a measurement prompt, the 0.3 s typed-ahead Enter drop; spec B "Not done yet") are not done, because the tool is not run.
- Still in B21 and checked only by the walk: F12 stopping a run while MapleStory has focus (plan "Not verified" 8; so far checked against the test sink window only), and teleport semantics (2) and platform extents (3) by watching.
- B22b writes the docs after B21 under the rule "every number comes from a B14/B18/B21 output quoted in the commit body" (plan task B22). With the measurement dropped, B22b cannot quote FF2 reach or movement values from an output. Ruling [local, ledger-B.md:779]: B22b cites this entry (Hong's own measurement) as the source for the FF2 reach and walk values, because his decision makes his measurement the number of record and B22b is docs only, written after B21; cost if wrong: one docs paragraph; undo: B22b's brief.

Affects: what B21 means, the Feature B sign-off path (still the 10 laps, so H4 stands) and which spec "not verified" items stay open. The plan and spec text and the code are not changed here.

Cost if wrong: if the FF2 reach is not 19/23, the walker wastes moves or repeats a teleport up until the 60 s leg timeout, and the 10 laps might not all be reached (that would show on the 10-lap run itself). The 1000-lap simulator result would describe the wrong numbers. If he later wants the measurement, the tool is built and the plan steps are unchanged (`python -m src.route.harness.measure_moves --supervised`).

Undo: Hong says so; B21 goes back to the plan's eight steps.

Pointer: `.superpowers/orchestrator/2026-10-21-phase1/ledger-B.md:771` and `:772`; question bank QB87 and QB88; `notes/WALK67.md`, `notes/B21Q66.md`. His step list is `notes/B21-steps67.md` in the orchestrator workspace.

## H38 (2026-10-27): The shortened B21 walk test is the first thing he does at his next session (QB88)

Question: when does B21 run? The PC was in use on 2026-10-27, so no key-sending test could run that day.

Ruling [Hong]: the first item of his next session. His words (QB88): PC free "Not today"; then in chat: "i'll run this test when im next back, it sounds like  its extremely high priority seeing as it determines whether feature A and B have been built correctly so this is top priority the next time im onn".

Reason: his words above: the walk is the test that shows whether Features A and B work in the real game.

Affects: the order of work at his next session: B21 first, with him at the PC and MapleStory open. The walk tool needs his go and his presence (`src/route/CLAUDE.md`, Traps) and is not covered by H15. Agents run no key-sending tool in the meantime, while he is using the PC (QB86), so H15 is not overridden; Feature C planning goes on (H39).

Cost if wrong: none; it is an ordering.

Undo: Hong says so.

Pointer: `.superpowers/orchestrator/2026-10-21-phase1/ledger-B.md:772`; question bank QB88 and QB86; his step list `notes/B21-steps67.md` in the orchestrator workspace.

## H39 (2026-10-27): The Feature C (runes) plan is written locally, on this PC (QB89)

Question: is the Feature C plan written by an agent here (nothing for Hong to do; it uses his plan usage) or in a cloud session (it uses the cloud credit that ends 2026-11-04, and he has to start it and paste a prompt)? The orchestrator had parked this as an unsure case (H32).

Ruling [Hong]: here. His answer (AskUserQuestion): "Here, on this PC (Recommended)".

Reason: not given beyond the answer.

Affects: where the Feature C plan (spec `docs/superpowers/specs/2026-10-21-p1c-runes-design.md`) is written. C cannot merge before B (spec C "Shipping"), so B21 gates C's merge, not C's planning.

Cost if wrong: it uses his plan usage and not the cloud credit.

Undo: Hong says so.

Pointer: `.superpowers/orchestrator/2026-10-21-phase1/ledger-B.md:773`; question bank QB89.

## H40 (2026-10-27): Feature C ground truth and how the spinning-arrow reader is judged (QB90-QB93)

Question: Feature C (runes, spec `docs/superpowers/specs/2026-10-21-p1c-runes-design.md`) has to read spinning arrows, and no one has the right answers for them. What counts as ground truth, how is the arrow reader judged when Hong can only bring about 10 runes, and are all three reading methods built? Orch 67 asked QB90, QB91 and QB92; orch 68 redesigned the test for ~10 runes and asked again (QB93; numbers in `notes/D7R68.md`).

Ruling [Hong]: four parts are decided and the judging rule is open.

1. Ground truth (QB90, ledger-C.md:24). His words: "I can get video gameplay of spinning runes, i'll write down the "tells" that is used to identify the undetlying direction and i'll provide the correctt directions as well, Then you can assess your various methods until you get one that generalises ". The ground truth is his labelled videos of spinning runes, his written tells and his correct directions. This replaces the option of building the answer set ourselves.
2. Game facts (QB91, ledger-C.md:25). His words: "you can  just tap the arrow key to input the direction, you have to answer from left to right. I dont knnow how long the rune msut be on the map for the rune cuirse to start, i beleive its at least 2 minutes, it lasts until the rune is used". So the input is one arrow-key tap per arrow, left to right. The curse starts at least about 2 minutes after the rune appears (his belief, not known) and lasts until the rune is used. The arrow time limit and the distance you must stand within are still unknown (RuneParams guesses stay marked "unverified").
3. How many runes (QB92, ledger-C.md:32). His words: "ill try to  find as many as 20, but i dont think i will be able to. I reckon ill only be able to get a few like 10 in total". Asked again how many he can get (QB93 question 1), he said: "I dont  know how many i cann get, i can try my best  to  get as m any as possible but until i do it, i wonnt know". So the count is about 10 and stays unknown until he records. Each four-arrow spinning panel has 2 spinning arrows (160 of 161 panels in HX's Stage B labels, `notes/D7R68.md`), so ~10 runes are about 20 spinning arrows. (The ledger line 32 says ~40 arrows at 4 per rune; that count is wrong.)
4. Judging rule: OPEN (QB93; his pick is at ledger-C.md:43, his retraction at ledger-C.md:47). He first picked "Sealed, score once (Recommended)" and then took it back. His words: "i take back that decision, i dont  actually know the benefits/cost of each  of  the  trhee ways of doing the testing. I'll answer  it next time after  i've had time to understand the cost/beenfit". So no judging rule is decided. The three options stay open (`notes/D7R68.md`, QB93): A. sealed answers, one score on every spinning arrow he brings, bar scaled to the count (about 1 wrong per 20 arrows), game-accepted solves added later; B. leave-one-clip-out over all clips (re-runnable, but reruns flatter the score); C. a small tune/test split with a lower bar, the final say from supervised-session solves. Hong decides at his next session. Reading his phrase "three ways of doing the testing" as these three judging options is orch 68's reading (ledger-C.md:47), not his stated meaning; he can correct it, and "all three readers" (part 5) does not depend on it. The plan's 50/50 split with at least 40 held-out arrows cannot be met with ~10 runes, so it cannot stand as written.
5. Methods (QB93 question 2, ledger-C.md:41). He picked "All three, as spec (Recommended)". All three readers are built and compared as spec C:29 says. The plan's "pose model only if needed" is dropped.

Reason: his answers above; not given beyond them. What a test of this size can show (`notes/D7R68.md`, exact binomial, read not run live by this unit; for option A with 20 arrows and a bar of 19): a method that is truly 80% right per arrow passes 7% of the time, 90% passes 39% and 95% passes 74%. So it catches a bad reader (80% or worse) and cannot tell 90% from 95%. A pass does not prove the 95% target (spec C:61); the supervised-session solves keep counting toward it.

Affects: only the judging step, C11 step J (`judging.py`, which exits "no judging rule chosen yet" until he chooses), waits on the judging rule. The plan's C9-C11, D7 and [Hong] H-7 text were rewritten to leave it as one open slot (`notes/D7T68.md`, ledger-C.md:53); the plan's D3 line that game outcomes do not set the flag may still change with it. What he does: record the spinning runes, write the tells and the correct directions, and answer the judging question at his next session.

Cost if wrong: for the four decided parts, recording the wrong runes or tells costs his time. The open judging rule has no cost yet because nothing is built on it; under option A a failed batch cannot be re-tested on the same runes, and a truly-95% reader fails the bar about 26% of the time (`notes/D7R68.md`).

Undo: Hong says so.

Pointer: `.superpowers/orchestrator/2026-10-21-phase1/ledger-C.md:24`, `:25`, `:32`, `:41`, `:42`, `:43` (the pick he took back), `:47` (his retraction); question bank QB90, QB91, QB92, QB93; `.superpowers/orchestrator/2026-10-21-phase1/notes/D7R68.md`.

## H41 (2026-10-27): Before his walk test and videos, build only the independent parts of Feature C (QB94)

Question: after the Feature C plan is reviewed and fixed, should agents build any of Feature C before Hong's walk test (B21, H37-H38) and his spinning-rune videos? He first asked which C parts rely on A and B and what a redo would involve; the answer came from the plan's task table: C7, C15, C19 and C20 lean on Feature B's walking code, and C16, C18 and C20 use Feature A's typing, which is signed off.

Ruling [Hong]: build the independent parts only. His answer (AskUserQuestion): "Independent parts only (Recommended)". The list given with the question was C1-C6, C8, C10, C10b and C12, nothing touching walking or vision. QB95 (H42) then took C10b off it, so the list now is C1-C6, C8, C10 and C12. C7 and C13-C21 wait until the walk test passes. C9 and C11 wait for his videos. C22 needs him.

Reason: not given beyond the answer. The recommended option avoids patching or rewriting work that Feature B's walk test may change.

Affects: the order of Feature C building (plan `docs/superpowers/plans/2026-10-27-p1c-runes.md`, task table). Nothing in the held tasks is built until the walk test passes (H38 makes that test the first item of his next session). The plan's C10b, D7, task table and critical path are updated to match in the plan fix unit.

Cost if wrong: the independent parts could still need small changes if Feature B changes. The other options were waiting for both (nothing built, slower) and building everything that does not need him (a patch or rewrite risk for C7 and C13-C21).

Undo: Hong says so.

Pointer: `.superpowers/orchestrator/2026-10-21-phase1/ledger-C.md:80`; question bank QB94.

## H42 (2026-10-27): The pose-model arrow reader (C10b) is held and will train on real game footage (QB95)

Question: spec C:29 builds three arrow readers, one of them a pose model. The plan trained it on made-up rotated arrows. Should it train on made-up arrows, on real footage, or be dropped?

Ruling [Hong]: hold it, and train it on real game footage. His answer (AskUserQuestion): "Hold it; train on real game footage (Recommended)". His reason, raised by him unprompted before the question: "I don't think there's a need to do this because the way the underlying arrow rotation of the spinning arrows are shown, I believe, is quite unique to MapleStory. So really, you would only ever want to train the models based off how it actually runs in MapleStory since there are unique game specific tells that tellyo which unerrlyinf direcction it points in". So C10b is not built before his videos and the judging rule (H40 part 4). After them it trains on real game footage, which means a train/test split of his clips (or more clips). That split constrains the judging options for this reader, because clips used to train it cannot also score it. All three readers are still built (H40 part 5, QB93 question 2); this changes how and when the pose model is built, not whether.

Reason: his words above: the tells that show which way a spinning arrow points are unique to MapleStory, so a model trained on made-up rotated arrows would not learn them.

Affects: plan C10b, D7, the task table and the critical path, which the plan fix unit updates; QB94's early-build list (H41) loses C10b; the judging rule still to be chosen (H40 part 4), because the pose reader needs a train/test split of his clips.

Cost if wrong: C10b is built later than it could have been, and it needs enough of his clips to split into a training part and a test part. The other options were to drop the pose model (a departure from spec C:29 and a reversal of his QB93 answer) and to keep the made-up-arrows plan.

Undo: Hong says so.

Pointer: `.superpowers/orchestrator/2026-10-21-phase1/ledger-C.md:87`; question bank QB95; H40 parts 4 and 5.
