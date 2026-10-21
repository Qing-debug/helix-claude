# Handoff — 2026-10-21

*For the next Claude session. Read this, then `docs/vision.md`, then the four Phase 1 specs. `CLAUDE.md` covers the code.*

## Where we are

- **Repo:** `Qing-debug/helix-claude` (private), cloned from `Qing-debug/Helix` with full history. Remotes: `origin` = helix-claude, `upstream` = Helix (fetch-only, push disabled). Unlike upstream, this repo tracks `CLAUDE.md` and `docs/`.
- **Vision approved** by the user: `docs/vision.md` (commit `c5d5c5d`).
- **Phase 1 specs written, NOT yet approved**, in `docs/superpowers/specs/`:
  - `2026-10-21-p1a-input-over-bluetooth-design.md`
  - `2026-10-21-p1b-route-walking-design.md`
  - `2026-10-21-p1c-runes-design.md`
  - `2026-10-21-p1d-watching-and-logging-design.md`
- A builder-check reviewer read all four. Its questions were sorted; **five are waiting on the user** (below). Once answered: fold the answers into the specs, get the user's approval of the specs, then write the plans (`superpowers:writing-plans`), then build A → B → C → D.

## The user's goal for the next step

The user wants **all of Phase 1 designed up front so they can set Claude off and leave.** After the specs are approved, Claude plans and builds while the user is away, stopping only for things that change in-game behaviour.

## Questions waiting on the user (asked 2026-10-21, unanswered)

Each was offered with a default; the user may just say "defaults fine".

1. **Passive screen capture while the game runs and the user is away** (no input), with Kanna left logged in on Fossil Foundry 2 on the second monitor — for real minimap frames, rune-spawn appearance, gameplay negatives. *Default: yes.*
2. **Research without the deep-research "go" gate** (the user's global CLAUDE.md routes deep research through `deep-research-interactive`): for the human-timing model (spec A) and GM-whisper / white-room appearance (spec D). *Default: research inline, cite sources in the spec.*
3. **Which whispers stop the bot**: any whisper, or only confirmed-GM ones? Prefer false stops or possible misses? *Default: any whisper stops it.*
4. **Was Kanna's 19/23 px teleport reach measured on Fossil Foundry 2?** Minimap scale differs per map (FF2 is 162×82; older test maps were 186×74). *Default if unknown: measure in the first supervised session.*
5. **May Claude unpair/re-pair "Guiness" in Windows and reset the board over USB serial itself?** Bonding must be re-added to the firmware (HID input report needs an encrypted link; Helix `ISSUES.md` #3/#4). *Default: yes.*

Also proposed (default, not yet confirmed): if spinning-arrow ground truth can't be established without the user, **ship the best-measuring method flagged "accuracy unverified"**, relying on the 8 s retry.

## Decided and recorded (don't re-ask)

Already reflected in the vision/specs unless noted:

- **Autonomy while away:** live keystrokes into a test window only while MapleStory is NOT running; **no running the bot in the game while the user is away** (the early bot would look more bot-like — no attacking, rough timing — and a flagged account gets lie detectors it would fail); may compile and flash the ESP32 whenever needed.
- **Git:** one branch per feature, merge to `master` once its checks pass, push to `origin`. *(Not yet written into the specs — add to the plans.)*
- **Reliability targets:** Claude sets a number per feature, records it in that spec, and the user can override later. *(Not yet written in — add when folding answers in.)*
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
