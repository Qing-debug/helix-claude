# Handoff — 2026-10-21

*For the next Claude session. Read this, then `docs/vision.md`, then the four Phase 1 specs. `CLAUDE.md` covers the code.*

## Where we are

- **Repo:** `Qing-debug/helix-claude` (private), cloned from `Qing-debug/Helix` with full history. Remotes: `origin` = helix-claude, `upstream` = Helix (fetch-only, push disabled). Unlike upstream, this repo tracks `CLAUDE.md` and `docs/`.
- **Vision approved** by the user: `docs/vision.md` (commit `c5d5c5d`).
- **Phase 1 specs approved by the user 2026-10-21**, in `docs/superpowers/specs/` (open questions answered and folded in, plus per-feature reliability targets and a Shipping section in each):
  - `2026-10-21-p1a-input-over-bluetooth-design.md`
  - `2026-10-21-p1b-route-walking-design.md`
  - `2026-10-21-p1c-runes-design.md`
  - `2026-10-21-p1d-watching-and-logging-design.md`
- **Next:** write the plans (`superpowers:writing-plans`) into `docs/superpowers/plans/`, then build A → B → C → D.

## The user's goal for the next step

The user wants **all of Phase 1 designed up front so they can set Claude off and leave.** After the specs are approved, Claude plans and builds while the user is away, stopping only for things that change in-game behaviour.

## Answered by the user 2026-10-21 (folded into the specs)

1. **Passive screen capture while the user is away**: yes (no input; Kanna logged in on FF2, second monitor).
2. **Research inline** for the timing model (A) and whisper / white-room appearance (D), without the `deep-research-interactive` gate; cite sources in the spec.
3. **Any whisper stops the bot**; false stops preferred over misses (Claude extended this to lie detector and white room; user OK'd). `vision.md` updated to say "whisper".
4. **Teleport reach 19/23 px was NOT measured on FF2.** The user assumes it's the same on every map; unverified (a different minimap size could be a bigger map at the same scale). Measure in the first supervised session; keep reach a single value in `classes.py`.
5. **Claude may unpair/re-pair "Guiness" and reset the board over USB serial.** Bonding must be re-added to the firmware.
6. **Spinning arrows without ground truth**: ship the best-measuring method flagged "accuracy unverified".
7. **Only merge code that really works.** A merges on its real-hardware checks; B, C, D merge only after in-game sign-off. C branches off B's branch, D off C's (stacked, pushed to `origin`, rebased if the base changes).

## Decided and recorded (don't re-ask)

Already reflected in the vision/specs unless noted:

- **Autonomy while away:** live keystrokes into a test window only while MapleStory is NOT running; **no running the bot in the game while the user is away** (the early bot would look more bot-like — no attacking, rough timing — and a flagged account gets lie detectors it would fail); may compile and flash the ESP32 whenever needed.
- **Git:** one branch per feature, pushed to `origin`; merge rule as in item 7 above. Written into each spec's Shipping section.
- **Reliability targets:** Claude sets a number per feature, records it in that spec, and the user can override later. Written into each spec.
- **Firmware** copied into this repo and flashed from there; the user's sketchbook copy stays untouched.
- **Keys:** teleport = arrow + `E`; jump `Alt`; drop through platform = `Down` + `Alt` (only where `can_fall = TRUE`); rune = `Space` then arrows left→right; walking = hold arrow. Walk vs teleport vs drop is Claude's choice.
- **Map:** `maps/fossil_foundry_2.csv` (Riena Strait – Fossil Foundry 2, minimap 162×82), made by the user this session. `maps/` is gitignored. Two rotation legs have no path in the current graph — `(49,28)→(101,24)` (user: it's a walkable slope) and `(101,24)→(105,42)` (4 px offset). These are **test cases**: the bot must infer terrain from node layout; the user won't label slopes on other maps. Extra user config only if testing shows it's needed.
- **Runtime transport is BLE only**; USB only for flashing/serial during development.
- **Unattended events (Phase 1):** lie detector / GM whisper / white room → screenshot + log, then stop all input. Rune fail → retry after 8 s. Stuck / death / disconnect → nothing.
- **Human-like timing must not come from recordings of the user.**

## Facts found this session (sources in the vision/specs)

- GMS lie detector exists (v.264 patch notes, Nov 2025); appears only on flagged accounts; main-GMS format is "Find the Transparent Shape" (**mouse**), ~10–15 s warning lower-right, no sound. Research files from the subagent are in the old session's scratchpad (not durable).
- Helix's rune work is on its **local, unpushed `rune_solver` branch** (`~/PycharmProjects/Helix`, HEAD `f0d2be4`) **plus uncommitted changes** to `pipeline.py`, `vision.py`, `vision_helper_lib.py`. Not in this repo yet — Feature C brings it over.
- Minimap corner templates matched the desktop wallpaper at ~0.9995 confidence; the rectangle checks rejected it, but it's a weakness.
- `test2.csv`/`test3.csv` origin unknown (not Glacial Observatory for certain, not Blue Mushroom Forest 2). Irrelevant now.

## Environment state

- **No Python env for this repo yet.** This session ran code with Helix's venv: `~/PycharmProjects/Helix/.venv/Scripts/python.exe` (Python 3.14; has bettercam, cv2, networkx, returns, PIL, matplotlib). Creating this repo's own env is the first groundwork task in plan A.
- **Run the app:** `python -m src.main` from the repo root; the game must be on the **second monitor** (`output_idx = 1`). Earlier launches from a background shell exited right after detection; launching as a detached process worked.
- **The Helix GUI may still be running** (started detached, pid 26176). Its logs: old session scratchpad (not durable).
- **Recording:** `data/recordings/ff2_2026-10-21/` (gitignored, 307 full-screen frames of monitor 1 over 90 s, ~747 MB, character standing still). The user accidentally opened a browser tab on that monitor during it: **every 5th frame was checked and all show a valid 162×82 minimap**, but other parts of the screen may show the tab — check full frames before using them as gameplay negatives. The recorder was a throwaway script (bettercam grab of output 1 at ~5 fps, PNG per frame).
- ESP32: assumed plugged in over USB and paired; not touched this session.

## How the user likes to work

- One question at a time when designing; lead with substance, no filler (see global CLAUDE.md).
- Verify library behaviour against docs/source before asserting it; cite sources.
- Claude tries to fill verification gaps itself first; asks the user only when it can't.
