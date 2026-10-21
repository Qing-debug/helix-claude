# Helix — Vision

*Written 2026-10-21 from a brainstorming conversation. This is the "what and why" for the whole bot. Each feature gets its own spec under `docs/superpowers/specs/` when we start it; this document does not try to be that spec.*

## What the bot is

Helix plays MapleStory (GMS, PC client) on its own. It reads the screen, follows a farming route around a map, and presses keys through an ESP32-S3 that acts as a real Bluetooth keyboard. The PC and the ESP32 talk to each other without a person in the loop.

At full scope it:

- **Moves around the map along a route**, using the character's movement abilities to get between nodes.
- **Attacks while it travels between nodes** (it does not stop at each node to attack). It kills as many mobs as it can.
- **Gets loot by passing over drops.** A pet picks items up; the bot never has to stop for loot.
- **Solves runes.** When a rune spawns, the bot comes to a good stopping point, goes to the rune, reads the arrows — including spinning arrows — and inputs the answer.
- **Handles the game's human checks.** It solves the lie detector the way it solves runes, and keeps its input timing human-like.

## Why runes matter

A rune left on the map eventually curses it: after a few minutes, mobs stop giving XP and drops. The bot does not need to reach a rune the instant it spawns — finishing what it is doing first is fine — but it must solve it soon after. **A missed rune is the clearest failure**, because it silently ruins everything else the bot is doing.

## What success looks like

Most maps have an *ideal* rotation and a *lazy* one, each with a known XP/hour and gold/hour. The long-term goal is for the bot to match the ideal rotation's numbers.

**What would make the user wince:** a rune not answered, or rotations run badly.

## Open on purpose

These are not decided, and should not be guessed at. They will become clear as the project goes:

- **How attacks are chosen.** Whether there is a generic attack behaviour, or the user configures exactly which abilities are used where and when. The naive answer is "as configurable as possible", but that is not decided.
- **What makes a rotation "good"** in terms the bot can act on. We don't yet know which capabilities a good rotation needs.
- **How to produce human-like input timing** (see constraints).
- **The lie detector's format** on GMS (see Phase 4).

## Phases

Features are rolled out in phases, each grouping features that do a similar job. Phase order agreed with the user 2026-10-21. Several features are adapted from prior art (see below).

### Phase 1 — core loop: move and solve runes (milestone 1)

**Character: Kanna** (the class already defined in `src/classes/classes.py`). **Map:** one of the existing map CSVs in Helix `maps/` (`test2.csv` / `test3.csv` carry a 186×74 minimap size; which in-game map they are, and Kanna's movement skills and keys, are confirmed when Phase 1 starts).

The bot, on that map:

1. Presses keys reliably through the ESP32, with human-like timing.
2. Knows how far each movement skill travels. Kanna's main movement ability (teleport) is already measured by the user: 19 px horizontal, 23 px vertical on the minimap (`src/classes/classes.py`) (not measured on FF2; see docs/decisions.md H3). A calibration step is only needed for any other movement skills Phase 1 ends up using.
3. Walks the map's route (the blue-node rotation from a map-editor CSV) reliably, using the character's movement abilities. No attacking yet.
4. Notices a rune has spawned and, **once it finishes the node it is currently travelling to** (the "good stopping point"), goes to the rune, interacts with it, reads it (static and spinning arrows), and inputs the answer.
5. Recognises when a lie detector or a whisper appears, saves a screenshot and a timestamped log entry for the user to review later, and handles it as below. The user won't always be there when it happens.

### Phase 2 — combat and routines: kill mobs well

Skill configuration (cooldown, cast time, hold), auto-buffing, attacking while moving (weighted random skill choice), skills tied to specific points; points with a tolerance radius and a chance of being used; loops measured in time; several routines run in turn or at random. This is also where "how attacks are chosen" and "what makes a rotation good" get worked out. Variety in routines also helps the bot look less regular.

### Phase 3 — running unattended: keep going without the user

Stuck and death detection; reconnecting / relaunching / logging back in; changing channel (other players present, too many rune failures, elite boss); breaks; returning to the training map; phone alerts (e.g. Discord); a panic/stop key; stop conditions (time, level, XP).

### Phase 4 — human checks: pass the game's checks

- **Solving the lie detector.** Research 2026-10-21 (supersedes the threat model's UNCONFIRMED rating):
  - **It exists on GMS.** v.264 patch notes (nexon.com/maplestory/news/update/32522, Nov 2025): "If a player is flagged for unusual behavior, a Lie Detector minigame will appear during gameplay. If the player fails the minigame, they'll be sent to the Truth Booth." Testing began after the 2025-11-20 maintenance; triggers spiked in May 2026.
  - **Being shown one means the account is already flagged.** Community reports say it then repeats every 30–60 min for days, across all characters on the account, and the Truth Booth timeout escalates per failure (1 min, 3 min, … up to 24 h), dropping a level weekly.
  - **The only format seen on main GMS is "Find the Transparent Shape"**: keep the cursor on a shape as it moves and fades among decoys. It needs **mouse movement**. Community reports: ~10–15 s warning in the lower-right corner, no sound, mobs can still hit you. (Corroborated community sources, not official.)
  - MANIA's other two formats don't apply to main GMS: Violetta is KMS, and "5 clicks then type" is GMS Classic World, a separate client.
  - This phase starts by confirming the visuals from screenshots Phase 1 saves before designing a solver.
- **Mouse input — needed.** The only GMS format needs the mouse, so the ESP32 must act as both keyboard and mouse. Before building it, find out whether a combined keyboard+mouse device raises detection risk — the user's concern is that one device doing both looks like a red flag.
- **Responding to a GM / the white room**, and a friends whitelist. Not designed yet; GM contact is considered very unlikely for now.

### Phase 5 — upkeep (optional)

Restocking potions, handling a full inventory, selling items.

### Not planned

Memory/packet techniques (another class of bot), multi-client and party sync, name spoofing, and non-farming automation (cubing, starforce, familiars, dailies).

## Prior art

Two commercial bots were reviewed from their public pages on 2026-10-21 (claims, not verified):

- **MANIA** (mania.bot) — screen-reading with optional external USB-HID input (a Raspberry Pi Pico), so architecturally close to Helix. Most adopted ideas come from its docs: points with radius/probability, per-skill movement distance with calibration, time-based loops and routine modes, solving runes after the current point, channel-change triggers, breaks, return-to-map.
- **Violet** (violet.cx) — mostly memory/packet-based. Ideas taken, not techniques: pausing risky actions while a lie detector is open, auto relaunch/relog, stop conditions, panic key, GM → log out + alert.

## Constraints

- **Everything runs on the game PC.** The bot and screen reader run on the same machine as the game; the ESP32 is the only extra hardware. (This is the user's decision and overrides the threat model's "separate machine" recommendation, `anti-botting-threat-model.md` C2. MANIA's docs also say a separate machine is "required for official servers".) Separation is not designed for unless running the bot on the game PC turns out to cause detections. If it does: (1) research whether a VM would actually help avoid detection (including whether the game's anti-cheat refuses to run in, or flags, a VM), and if so how to set it up; (2) with that information, the user decides which is easier — a VM on this PC or a second PC to run the bot on.
- **The PC and the ESP32 talk only over Bluetooth (BLE)**, both for commands and for the keyboard itself. No USB data link between them at runtime: the user considers it safer. *Claude's call:* a USB cable is still used during development to flash the board and read its serial log, but it is not part of how the bot runs.
- **All input goes through the ESP32 as a real HID keyboard.** No software input injection (`SendInput` etc.) — the threat model rates that as likely detectable.
- **Input timing must look human.** Varied delays, no perfectly regular rhythms, no identical repeated loops. This lives in the input layer from the start, not bolted on later. **The user does not want to record themselves playing** to get this data, so another source is needed.
- **Ban risk is the user's to accept.** Countermeasures lower the chance of detection; they cannot rule it out. Bans can be deferred long after the behaviour that caused them (threat model C1), so a long clean run is not proof of safety.
- **Windows only** (bettercam screen capture, Windows BLE stack).

## When something happens while the user is away (Phase 1)

Decided by the user 2026-10-21. The user will see the aftermath when they get back, so what matters is that **they know exactly where to look to find what caused it.**

| Event | What the bot does |
|---|---|
| Lie detector appears | Save a screenshot + log entry, then stop all input (go AFK). |
| Any whisper (GM or not) / white room | Save a screenshot + log entry, then stop all input (go AFK). |
| Rune solve fails | Wait 8 seconds, then retry. |
| Stuck, character dies, connection drops | Nothing special in Phase 1. The user deals with it when back; Phase 3 adds recovery. |

**Claude's calls (check these):**
- Rune retries repeat every 8 s until the rune is solved or disappears, each attempt logged.
- Every session writes to one predictable place: a session log plus an incident folder per event (screenshot, timestamp, what the bot was doing), and a rolling set of recent screenshots. Even for the "do nothing" cases (stuck, death, disconnect), that trail shows the user what led up to it.
- Since a lie detector means the account is already flagged (see Phase 4), going AFK means the check is failed and the Truth Booth timeout applies. This is the accepted cost until Phase 4.

## How work gets done in this repo

This repo is a Claude-only parallel of Helix (`Qing-debug/Helix`), used by the user to learn what efficient agentic coding looks like. That shapes the process:

- **One spec per feature**, written when the feature starts, building on what earlier features taught us. No master spec.
- **Claude must be able to check its own work.** At the start of each feature, Claude says what it expects to need to verify that feature without the user — recordings, map files, hardware connected, ground-truth answers — and the user and Claude agree it looks enough before building begins. The user provides it then, not in advance. **That first list is a best guess:** some needs only show up once Claude actually runs the verification loop. So the loop is tried early in each feature. When it turns up a gap, Claude first tries to fill it itself (generate the data, write the tool, find it in the Helix folder, etc.). Only if it can't does it stop, say exactly what is missing and why, and the user steps in. The user's own in-game check is reserved for milestone sign-off.
- **Who decides what:**
  - **The user:** what the bot does, what success is, anything that changes in-game behaviour.
  - **Claude:** implementation choices, including how to read spinning arrows, and the test setup needed to verify its own work.
  - **Together:** the open questions above.

## Where things stand (2026-10-21)

Built and working (in this repo, from Helix `master`):

- Minimap capture and validation, character location on the minimap.
- Map editor that produces map CSVs, and the path graph built from them, gated by character class (only Kanna is defined).

Started elsewhere, to be brought over:

- **Rune solver** — on Helix's local, unpushed `rune_solver` branch (`~/PycharmProjects/Helix`, HEAD `f0d2be4` plus uncommitted work). Stage A (find the panel) and Stage B (read arrows; "spinning" is a class) are trained, both ~0.995 mAP50 on frozen test sets. `pipeline.py` chains them and captures a burst of crops for each spinning arrow; **the step that turns those crops into a direction is a placeholder.** helix-claude starts from this work rather than rebuilding it. Three candidate approaches exist for spinning arrows — a YOLO-pose tip/tail model, a classical base/tip method (unaudited), and the "the arrow briefly moves against its spin at its true angle" idea in `pipeline.py`'s TODO. Claude picks one or a combination by measured accuracy on real spinning bursts with known answers.

Not working yet:

- **Keystrokes from BLE commands.** A PC→ESP32 write arrives, but a keypress triggered from the write handler never reaches the PC (Helix `ISSUES.md` #5; likely cause traced, fix not implemented). The ESP32→PC reply channel is commented out. Nothing can move until this works.
- **The pathfinding/movement loop** in `controller.py` is commented out and refers to an old movement API.
- **No tests, no dependency file.**

Key references in the Helix folder: `RESUME.md`, `ISSUES.md`, `modeltraining/README.md`, `docs/reference/anti-botting-threat-model.md`, `docs/reference/firmware.md`, `docs/superpowers/rune-solver/`, `docs/superpowers/ble-communication/`. Firmware source of truth: `~/Documents/Arduino/Helix_BLE/Helix_BLE.ino`.

## Left to the builder

- Architecture of new parts (state machine for "farming vs going to a rune", the input layer, the event watcher).
- How to detect a rune spawn, a lie detector, and a GM whisper on screen.
- How "finish the current node, then go to the rune" behaves at the edges (e.g. the rune is behind the bot, the current leg is long), within the few-minutes budget.
- How reliable is reliable enough for each feature: the target is agreed at the start of each feature, alongside how it will be verified.
- How to generate human-like timing without user recordings — to be researched and proposed, not assumed.
- Test infrastructure and fixtures.
