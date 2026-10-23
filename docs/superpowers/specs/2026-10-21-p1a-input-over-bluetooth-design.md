# Phase 1 · Feature A — Input over Bluetooth

*Design, 2026-10-21. Part of Phase 1 in `docs/vision.md`; read that first. Features B–D depend on this one.*

## Why

Nothing in the bot can act until the PC can press keys through the ESP32. Today it can't: a BLE write reaches the board, but a keypress triggered from inside the write handler never reaches the PC (Helix `ISSUES.md` #5). The traced cause is that the HID library's send is deferred onto the same NimBLE host task that runs `onWrite`, so by the time it transmits, the key-up report has already overwritten the key-down. The recorded fix direction is: `onWrite` only enqueues; `loop()` dequeues and sends the keys.

This feature also owns **how human-like the timing is**, because every keypress the bot ever makes passes through it.

## Goals

1. The PC can **press, hold, and release** any key the bot needs — at least the four arrows, `E` (teleport), `Alt` (jump), `Space` (interact) — including **chords** (arrow + `E` teleports; `Down` + `Alt` drops through a platform) and **holds** (walking is holding an arrow).
2. Every command the PC sends either produces the intended key events or fails visibly to the PC. No silent drops.
3. **Human-like timing** is built into this layer: key-hold durations, gaps between keys, chord offsets (the two keys of a chord never land in the exact same instant), and reaction delays all vary the way a person's do. Callers ask for an action ("teleport left"); the input layer decides the exact timings.
4. The PC↔ESP32 connection is established and held for a session.

## Non-goals

- Mouse input (Phase 4).
- Reconnecting after a drop. In Phase 1, if the connection drops the bot does nothing special; the session log records it (Feature D).
- Deciding *which* keys to press — that's Features B and C.

## Decided by the user

- **Transport is Bluetooth (BLE) only at runtime**, for both commands and the keyboard. No USB data link between PC and ESP32 while the bot runs. A USB cable is used during development only, to flash and to read the serial log.
- **No software input injection** (`SendInput` etc.). All input is the ESP32 acting as a real HID keyboard.
- **No recordings of the user playing** as a source of timing data. The timing model must come from elsewhere (published human-performance / keystroke-dynamics data, or a defensible parametric model), and be researched and justified, not invented.
- Key bindings: teleport `E`, jump `Alt`, interact `Space`, arrows for direction. Teleport = arrow + `E`. Drop = `Down` + `Alt`.
- **The firmware sketch is copied into this repo** (e.g. `firmware/Helix_BLE/`) and that copy is the one Claude edits and flashes. The user's sketchbook copy (`~/Documents/Arduino/Helix_BLE/Helix_BLE.ino`) is left untouched.
- Claude may **compile and flash the board** whenever it needs to (board is plugged in over USB and paired over Bluetooth).
- Claude may send **live keystrokes on this PC into a test window**, but **only while MapleStory is not running**.
- Claude may **unpair and re-pair "Guiness" in Windows and reset the board over USB serial** itself (user, 2026-10-21).
- The timing model is **researched inline**, not through the `deep-research-interactive` gate, with its sources cited in this spec (user, 2026-10-21).

## Constraints and facts

- Board: ESP32-S3, BLE name "Guiness". Custom service `a723b49a-2b53-46a6-a2db-02bb9e9a577c`; command characteristic `61adcd67-20e9-4c38-b1d0-879445c5b732` (READ | WRITE); notify characteristic `89f7c2ca-89b5-4234-905c-d4a931feaa3b` (created on the firmware side at `Helix_BLE.ino:85`; only its `notify()` call is commented out, at `:71`). Stack: NimBLE-Arduino 2.x + T-vK / Berg0162 ESP32 NimBLE Keyboard. Full reference: Helix `docs/reference/firmware.md`, `docs/superpowers/ble-communication/spec.md`, `ISSUES.md` #2–#5.
- **Bonding must be re-added to the firmware.** The HID input report needs an encrypted link (`READ_ENC`, NimBLE-Arduino `NimBLEHIDDevice.cpp:181`), and without a bond on the board side every paired reconnect is killed by Windows ~270 ms in (Helix `ISSUES.md` #3). Helix fixed this with `setSecurityAuth(true, false, true)` + `setSecurityIOCap(BLE_HS_IO_NO_INPUT_OUTPUT)` (user-verified 2026-06-11, Helix commit `3544179`), but that firmware copy was later removed and the current external sketch sets no security. Separately, Python only reaches the custom characteristics after pair → reset → reconnect, not straight after a fresh pair (Helix `ISSUES.md` #4, root cause not investigated).
- Python side: `bleak`, with the existing "event loop on a daemon thread + blocking bridge" pattern in `src/ble/`. `src/ble/ble.py` doesn't parse; `bleV2.py` connects at import time. Both are experiments to replace, not APIs to preserve.
- Helix's earlier design chose strict lock-step (one command, confirm on screen, next). That is a movement-layer concern for Feature B; this layer just has to deliver key events reliably and report failures.

## Verification (first guess — tried early, gaps filled by Claude first)

- **Serial log** from the board over USB: what it received and when.
- **A keystroke listener on this PC** that records which key events arrive from the HID keyboard and their timing, then blocks the bot's keys so they reach no window (`--no-block` lets them through). A test window stays focused for the whole run, and the game is not running.
- From that: every command type (single, hold, chord) arrives correctly, ordered, with no drops, over a long run (thousands of commands).
- **Timing realism** checked statistically against the chosen human-timing model (distributions, no two identical sequences, no fixed rhythm), on the values the timing layer chose; what arrives at the PC is compared with those values separately.

## Reliability targets

*Set by Claude; the user can override any of them.* All are measured offline, game not running.

| What | Target |
|---|---|
| Delivery | 0 dropped, extra, or out-of-order key events over a soak of ≥10,000 commands mixing single presses, holds and chords (0 in 10,000 bounds the true error rate below 0.03% at 95% confidence). |
| Failures are visible | Every injected fault (board reset mid-soak, PC Bluetooth off) is reported to the caller within 1 s. 0 silent losses. |
| Latency | p99 from command sent to key-down seen by the PC listener ≤ 100 ms. Provisional: re-set once first measured. |
| Delivered timing | Each hold, chord offset and gap within one action, as measured at the PC listener, is within ±20 ms of the value the timing layer chose, for ≥95% of each type. The second key of every chord is never seen before the first. |
| Timing realism | Each timing type (hold, gap, chord offset, reaction) fits its model (KS test, α = 0.01, ≥1,000 samples each), tested on the values the timing layer chose, not on times measured at the PC (the Bluetooth connection interval puts those on a grid; the Delivered timing row checks them). No run of ≥5 consecutive chosen gaps repeats anywhere in the soak. |
| Connection | Session start connects 20/20 times without re-pairing, including after a board reset and after the PC's Bluetooth is toggled. A connected session holds 2 h with no drop. |

## Shipping

Only code that really works is merged (user, 2026-10-21). Branch `p1a-input` off `master`. A's checks run on the real board, over the real Bluetooth link, into a real window, so passing them shows it works: merge to `master` once the targets above pass, then push to `origin`. That the game accepts the keys is confirmed in B's first supervised session.

## Left to the builder

- The command encoding between PC and board, and whether to use the notify channel for acks.
- The firmware fix for `ISSUES.md` #5 (the queue approach is the recorded direction, not a requirement).
- The timing model and its parameters, with sources.
- Groundwork done as the plan's opening tasks: a Python environment for this repo, a dependency file, a test runner.
- Bringing Helix's `rune_solver`-branch refactor of the camera/vision code over is Feature C's job, but if the groundwork tasks touch the same files, coordinate there.

## Timing model (as built)

*Written 2026-10-23, after the input layer was built and soaked. It records what the code does and the sources the model rests on, as "Decided by the user" asks (the model was researched inline, with its sources cited in this spec). No requirement above changes.*

### The model

The table is generated from `MODEL` in `src/input/timing.py` at commit `16ee18c`. `MODEL` is the single source for both the sampling and the Kolmogorov-Smirnov (KS) reference CDFs. All values are milliseconds.

Every value is drawn on the PC from one seeded `numpy.random.Generator`, independently of every other draw (this is what gives "no fixed rhythm"), and is quantised to 100 µs when it is encoded for the board. Truncation is by rejection sampling, so the reference distribution for a KS test is the truncated CDF, (F(x) − F(a)) / (F(b) − F(a)) on [a, b].

| Type | Family | Parameters in `MODEL` | Truncation | Source |
|---|---|---|---|---|
| `hold` (a tap; the second key of a chord; each key of a sequence) | lognormal | μ_ln 4.732633, σ_ln 0.204732, loc 0 (mean 116, SD 24; median 113.6) | [60, 250] | Mean and SD: Dhakal et al. Table 3 "All" keypress duration, 116.25 / 23.88. Family and bounds: judgement, no direct source. |
| `gap` (key-down to the next key-down inside one action) | 60 + lognormal | μ_ln 4.937721, σ_ln 0.524314, loc 60 (lognormal part: mean 160, SD 90; total mean 220) | [60, 1000] on the total | The 60 floor is Dhakal et al. p.5, "lower bound of about 60 ms"; the total mean is near their Table 3 "Trained" IKI, 223.55. SD 90, the family and the 1000 bound: judgement, no direct source. |
| `chord_offset` (first key-down to second key-down) | lognormal | μ_ln 3.218876 (= ln 25), σ_ln 0.5, loc 0 (median 25) | [8, 100] | Judgement, no direct source. The scale is anchored to Dhakal et al.'s rollover overlap (p.8), which is the overlap of sequential keys, not the onset gap of a press meant to be simultaneous. |
| `release_lag` (second key up to first key up, chords) | uniform | low 0, high 60 | support 0 to 60, no truncation | Judgement, no direct source. |
| `reaction` | ex-Gaussian | μ 195, σ 20, τ 35: candidate = Normal(μ, σ) + Exponential(mean τ) (mean 230, SD 40.31; scipy `exponnorm` K = τ/σ = 1.75) | [110, 1000] | Mean and SD: Woods et al. Table 2, Experiment 1, SRT 230.8 and IS-SD 40.0. Bounds: their SRT windowing. The split into μ, σ and τ and the family: judgement, no direct source. |
| `walk` (a long hold of intended duration T) | T + Normal(0, k·T² + σ_i²) | √k 0.04, σ_i 65, min_frac 0.5 | result ≥ T/2 (redrawn below it; no upper bound) | Laje et al.'s generalized Weber's law; √k and σ_i are read off the paper's figures (inferred, see Sources). The T/2 floor: judgement, no direct source. |

`hold_clipped` is not a `MODEL` key. It is the type the action builders give a hold that had to be cut short (below).

### Differences from the planned model

No differences from the planned model. Every parameter and bound in `MODEL` equals the plan's table of timing values (`docs/superpowers/plans/2026-10-21-p1a-input.md`, contract C4), and the derived figures match the digits printed there: hold σ_ln 0.2047 and μ_ln 4.7326, gap 0.5243 and 4.9377, chord offset 3.2189 and 0.5, reaction mean 230 and SD 40.3. `MODEL` was created in commit `3193a40` (`feat(input): add human timing model with sourced parameters`), and the only later commit that touched `src/input/timing.py`, `6b0d25e`, changed a docstring and one comparison inside `truncated_cdf`, not a value.

Outside the table, the action builders in `src/input/actions.py` apply the rules the plan states for the model, unchanged:

- A chord's key order is functional, not random: teleport presses the arrow and then `E`, drop presses `Down` and then `Alt`. The second key's hold is a `hold` draw. The first key is released `release_lag` after the second key.
- If a key is pressed again later in a sequence and its hold would reach past 15 ms before that next key-down, the up is clipped to that point, the sample is logged as `hold_clipped`, and its chosen value stays the drawn, unclipped float (it stays in the `hold` KS test, because dropping only the long draws would bias the test against its own model).
- A sequence is at most 8 keys.

One guard was added beyond the plan's wording; it changes no distribution. The `walk` builder returns a failure up front for an intended duration that is not finite, is negative, or exceeds 13 107.1 ms. Every `walk` draw is at least T/2, so above that value the draw overflows the per-step cap of 65 535 units of 100 µs (6 553.5 ms) whatever is drawn, and the guard also stops an overflow error when T is squared for an absurd T. Commit `0ed0c7a`, `fix(input): walk returns Failure for an intended_ms the per-step cap cannot hold`.

### Sources

Quotes are verbatim from the source, with the page or table where it is printed. Where the source prints one figure two ways, each is quoted where it is printed.

**Dhakal, Feit, Kristensson and Oulasvirta (2018).** "Observations on Typing from 136 Million Keystrokes". *CHI 2018*, April 21-26, 2018, Montréal, QC, Canada. https://doi.org/10.1145/3173574.3174220 , PDF https://www.pokristensson.com/pubs/DhakalEtAlCHI2018.pdf . An online typing study; the abstract says "168,000 volunteers".

- Hold, p.5, text: "In contrast, the average keypress duration is only 116.24 ms (SD = 23.88) and is not shown to vary greatly even for slow typists (80–150 ms)." Table 3 (p.5), column "All", row "Keypr. duration": 116.25 and 23.88. The text and the table differ by 0.01 ms in the source; the model uses 116 and 24.
- Gap, p.5, text: "Average inter-key interval is 238.656 ms (SD = 111.6). A lower bound of about 60 ms can be observed." Table 3 (p.5), row "IKI (ms)": column "All" 238.66 and 111.60, column "Trained" 223.55 and 107.78.
- Chord scale, p.8: "When rollover is used, keystrokes overlap by 30 ms, on average, and up to 100 ms."
- Limits. This is typing, so every Dhakal figure is an analogy for game key presses. The paper presents these measures as distributions "over all participants" (p.5: "Figure 2 shows the distribution of WPM, uncorrected error rate, IKI, and keypress duration over all participants"), so, inferred, the SDs in Table 3 are spreads across people, while the model applies them to one person's holds.

**Woods, Wyma, Yund, Herron and Reed (2015).** "Factors influencing the latency of simple reaction time". *Frontiers in Human Neuroscience* 9:131 (published 26 March 2015). https://doi.org/10.3389/fnhum.2015.00131 , https://pmc.ncbi.nlm.nih.gov/articles/PMC4374455/ . The full text has no page numbers, so each figure is located by table or section. Experiment 1 has 1469 participants (Table 2, row "N") who responded to a visual stimulus with a button press (Methods: "subjects responded as rapidly as possible to stimuli presented to the left or right hemifield by depressing a response button with the index finger of their dominant hand").

- Table 2, "Age-related changes in performance", column "Experiment 1" (N = 1469): row "SRT" 230.8; row "IS-SD" 40.0. Table 1 legend: "IS-SD, intrasubject (trial-to-trial) standard deviation".
- Discussion, Experiment 2: "mean SRTs differed by only 7 ms (230.8 vs. 237.8 ms) between the two studies".
- Methods, Experiment 1: "SRT windowing functions excluded response latencies less than 110 ms and greater than 1000 ms".
- Caveat: 230.8 is the mean as measured, before any correction for hardware delay. Abstract: "SRT latencies increased from 217.8 ms (200 ms when latencies were corrected for hardware delays) in the youngest subject group, to 239.1 ms (222.3 ms, delay-corrected) in the oldest subject group." The paper's own pairs differ by 17.8 and 16.8 ms, so the `reaction` row models a latency that still contains roughly that much of the experiment's apparatus delay, not a pure human latency.
- Caveat: Table 2 also has a row "SRT SD" (26.8 in Experiment 1). By the Table 1 legend ("SD, intersubject standard deviation") it is the spread between people. The model uses IS-SD, 40.0, because the ex-Gaussian describes one person's trial-to-trial spread (inferred: the model's "within-subject SD" is the paper's IS-SD). Experiment 2's IS-SD, 52.7, is not used.

**Laje, Cheng and Buonomano (2011).** "Learning of temporal motor patterns: an analysis of continuous versus reset timing". *Frontiers in Integrative Neuroscience* 5:61 (published 13 October 2011). https://doi.org/10.3389/fnint.2011.00061 , PDF https://www.frontiersin.org/journals/integrative-neuroscience/articles/10.3389/fnint.2011.00061/pdf . In Experiment 1, 12 undergraduate students learned "to reproduce a sequence of finger taps in the correct order and at the correct times" (abstract).

- p.1: "the coefficient of variation (CV, or Weber's fraction), defined as the SD over mean time, should be constant. This linear relationship is often referred to as the scalar property (Gibbon, 1977)."
- p.2: "a generalized form of Weber's law (…), in which the variance increases as T 2 plus a time-independent (residual or noise) variance component" (the parenthesis is the paper's citation list). Eq. 1 is σ² = kT² + σ²_indep.
- The values √k = 0.04 and σ_indep = 65 ms are read off the bars of Figure 2B (p.5: "Mean slope k and mean intercept σ²_indep across the 3-days of training"); the text does not state them. On day 3 the bars sit near k ≈ 1.6 × 10⁻³ (√k ≈ 0.04) and σ²_indep ≈ 0.004 s² (σ_indep ≈ 63 ms, rounded to 65). They are approximate and inferred. On day 1 the same reading gives √k ≈ 0.07 and σ_indep ≈ 130 ms, which the model does not use.
- Caveat: as Eq. 1 is written, σ = √k·T, so √k plays the part of a coefficient of variation. The paper's sentence after Eq. 1, "k (in Eq. 1) approximates the square root of the conventional Weber fraction at long intervals", does not fit that, and looks like a wording slip. The model follows Eq. 1 as written.
- Caveat: the subjects timed key-press onsets within a pattern, not sustained holds, so using the law for a held arrow key is an analogy.

**Judgement, no direct source.** The whole `chord_offset` row; `release_lag`; the μ, σ, τ split of `reaction`; every choice of family (lognormal for `hold`, `gap` and `chord_offset`, ex-Gaussian for `reaction`, uniform for `release_lag`); the `hold` bounds [60, 250]; the `gap` SD of 90 and its upper bound of 1000; the `chord_offset` bounds [8, 100]; and the T/2 floor of `walk`. No published mean or SD for the onset gap of a chord, or for release order, was found.

### Reaction as applied

A caller that has a stimulus passes the time it was seen. The input layer draws R, computes `wait = max(0, R − elapsed since the stimulus)`, waits that long, and then sends. R runs from the stimulus to the send and is not part of the schedule the board plays; an action with no stimulus has no reaction wait. So the reaction a listener on the PC sees, from the stimulus to the first key-down, is R plus the send → key-down latency, or longer when the wait was clamped to 0 because the pipeline had already used up R. This is by design in Phase 1 and is not corrected for.

Measured by the V3 soak (Plan A, Task V3; run `v3-1`, seed 71, commit `16ee18c`, 10 000 commands over 2.23 h), the residual that the delivery adds on top of R is, as the realism report prints it: "Reaction residual: INFO p50 29.639 ms p99 75.638 ms n 10000, clamped 0 excluded (first key-down − stimulus − R, the send → key-down latency; reported, not gated)". The same soak's latency check, from the command's send to the key-down seen by the PC listener, gives "M7 total p50 29.118 ms p99 75.151 ms" against the 100 ms p99 target in "Reliability targets".
