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

The entries below are rulings made as Hong under H9, each after an opus-xhigh adjudicator's read of the options. "Plan A" is `docs/superpowers/plans/2026-10-21-p1a-input.md`; plan line numbers in the undo steps are as of commit `8faa312` and drift once the plan is edited. Ids are the orchestrator's decision ids (PA = plan A, PAr = timing-model research, `-adj` = adjudication).

## PAr-D1 (2026-10-21): Timing-realism tests run on the values the timing layer chose

Spec A's realism tests (KS fits per timing type, and no run of 5 repeated gaps) run on the values the timing layer chose, not on times measured at the PC. The Holds row widens to "Delivered timing": each hold, chord offset and in-action gap measured at the PC listener is within ±20 ms of the chosen value for ≥95% of each type, and the second key of a chord is never seen before the first. The verification bullet is reworded to match.

Reason: the Bluetooth connection interval puts measured times on a grid (confirmed by the adjudicator's Monte Carlo), so testing them would gate the merge on the link rather than on the generator. Delivered timing still catches chord-offset and gap delivery bugs that testing only the chosen values would miss (about 68% of offset-ignoring chords are more than 20 ms off).

Affects: spec A (Reliability targets rows "Delivered timing" and "Timing realism", and the Verification bullet), commit `121c59f`; the merge gate of A under H4; the analysis task of plan A (its task H4, not decision H4) and the harness, which logs both chosen and measured values.

Ruling as Hong under H9; undo: revert `121c59f` and re-run the analysis step. If it proves too lax, a later finding can be re-analysed offline from the soak log, which holds chosen and measured values.

## PA-D2 (2026-10-21): Module layout for the input layer

New `src/input/` package, harness in `src/input/harness/`, firmware in a top-level `firmware/` directory, `src/ble/` left untouched, and an `InputLayer` facade that Features B–D call.

Reason: the adjudicator confirmed every load-bearing claim (no `src/input` exists in Helix or upstream; imports tested on Python 3.14), and the alternative mixes in the broken experiments in `src/ble/`.

Affects: module boundaries for A and everything B–D import; plan A task C3 and the Files lines P1–D1.

Ruling as Hong under H9; undo: one `git mv` plus an import rewrite after P1. The undo list is plan C3 (lines 159–226) and the Files lines P1–D1, and also needs tasks V1–V3 and D1.

## PA-D6 (2026-10-21): Timing model documented in spec A as built

Task D1 appends a "Timing model (as built)" section to spec A, built from `src/input/timing.py` `MODEL` at HEAD with differences from task C4 listed. Citations are in full with the quoted figures and carry no pointers into `.superpowers/` (gitignored). A new step P2 Step 0 re-reads the primary source of any figure that was seen only through a summary (Woods 2015) before `MODEL` is coded. The soak task S1's precondition becomes "V3 passes and D1 committed".

Reason: it meets spec A's requirement that the timing model's sources be cited in the spec, keeps those sources in git, and surfaces a source mismatch before the 2-hour-plus soak.

Affects: spec A (new section at D1), plan A tasks D1, P2 and S1.

Ruling as Hong under H9; undo: drop the D1 bullet 3 (plan lines 838–839), drop P2 Step 0, and restore the S1 precondition line (plan line 844).

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

Ruling as Hong under H9; undo: if B's supervised session shows lifting the arrow between moves hurts movement, add op 0x04 PLAY_OPEN behind the version and op bytes (1–2 tasks, plus re-running A's hardware checks). Plan lines to revert: 119–120, 463–464, 482–485, 583–585, 523–530 (plus a reflash).

## PA-adj1b-D1 (2026-10-21): InputLayer sends one release_all() after an uncertain failure

After `play()` returns NOTIFY_FAILED, ACK_TIMEOUT or WRITE_FAILED, `InputLayer` sends one `release_all()` before returning the Failure. Not after REJECTED, TOO_LONG or ABORTED, and LINK_LOST is impossible here. Adds P5 lines and a test.

Reason: the PC cannot know whether a key is down in those cases, and leaving the release to callers risks a stuck key.

Affects: the failure behaviour B, C and D see from `InputLayer`; plan A task P5.

Ruling as Hong under H9; undo: drop the P5 lines and the test. Cost if wrong is a redundant release when the keys were already up (inferred to be harmless).

## PA-D4 (2026-10-21): Firmware sends HID reports through a direct NimBLE notify

Keep the T-vK / Berg0162 ESP32 NimBLE Keyboard library for the HID setup, bypass its `sendReport` using the public NimBLE lookup copied from `NimBLEHIDDevice.cpp:157-167` (run in `onStarted`), and send with `notify(report, 8, connHandle)`.

Reason: all 15 claims checked out. `sendReport` coalesces key-down and key-up (`ble_gatts.c:2139-2146`, `:2370`) and returns void, and the alternatives end in the same notify call with more code to own.

Affects: firmware send path and the dependency use; plan A task F5.

Ruling as Hong under H9; undo: about half a task to switch; revert F5 step 4 bullets 2–3 (plan lines 509–517) and reflash.

## PA-adj1c-D1 (2026-10-21): Board tracks HID subscription and rejects PLAY if not subscribed

The board tracks the HID input-report subscription per connection handle (own callbacks into `onSubscribe`) and answers PLAY with REJECTED reason 6 HID_NOT_SUBSCRIBED when the commanding peer is not subscribed. PONG flags bit 3 means subscribed. P4 `connect()` waits, with a time limit, for bit 3 and otherwise returns Failure. Needs vectors, both parsers and one P4 test.

Reason: NimBLE's notify does not check subscription, so a report sent before Windows subscribes returns DONE OK with no key typed (the same class as Helix `ISSUES.md` #5).

Affects: the reject-code table and PONG flags in the protocol; the connect contract; firmware F5; plan A tasks C1 and P4.

Ruling as Hong under H9; undo: C1 tables (plan lines 123–135), `vectors.json`, `protocol.h`, `protocol.py` and the P4 check. If Windows never sets the CCCD as expected, `connect()` fails loudly and V1 measures it.

## PA-D3 (2026-10-21): Python 3.14 venv and pinned dependency files

Python 3.14 venv. `requirements.txt` pinned to the versions Helix runs. `requirements-dev.txt` holds pytest and `scipy==1.18.0`, and also pins `winrt-Windows.Devices.Radios==3.2.1` for the H2 `faults.py`. Four additions: (1) no `requirements-rune.txt` in A (Feature C writes, installs and tests it; spec C), (2) G1 step 2 expects Python 3.14.0 because networkx 3.6.1 refuses 3.14.1, (3) the G1 commit body records `pip freeze` (seven transitive packages resolve newer than Helix), (4) the winrt pin above.

Reason: every file A ships is then installed and tested by A (H4). All pins have cp314 wheels (dry-run resolved, no torch), and the rune dependencies go to the feature that imports them.

Affects: dependency files and the venv created by plan A task G1 (spec A "Left to the builder": groundwork).

Ruling as Hong under H9; undo: four small plan edits. Restore plan lines 319–321 and `requirements-rune.txt` in plan line 342, revert G1 step 2's expected line, drop the freeze sentence and the H2 pin.

## PA-D7 (2026-10-21): Harness listener blocks the bot's keys by default

The harness listener's low-level keyboard hook blocks the 7 bot keys by default, with a `--no-block` flag. The Tk sink window stays focused, with a foreground check before every command.

Reason: it is the only option that keeps keys out of other windows if focus changes mid-command. The hook records before it returns, so measurements are unchanged (`lowlevelkeyboardproc.md:94`), and unblocked Alt taps put the Tk sink into menu mode (`tkWinMenu.c:2222-2226`). This departs from spec A's wording "into a focused test window".

Affects: where harness keystrokes land; what a person at the PC can type during a run (arrows, E, Alt and Space are blocked system-wide while a run is on). Does not remove the check that MapleStory is not running (the hook is no barrier against the game's Raw Input; H7).

Ruling as Hong under H9; undo: plan task H1 blocking bullet (plan lines 657–659) to default off, plus its default-flag test; no re-run of V1–V3.
