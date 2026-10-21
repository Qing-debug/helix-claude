# Phase 1 · Feature D — Watching and logging

*Design, 2026-10-21. Part of Phase 1 in `docs/vision.md`; read that first. Depends on Feature A (to stop input); records events from B and C.*

## Why

The user won't always be there. When something happens — a lie detector, a GM whisper, a rune failure, the bot stuck or dead — they'll see the aftermath when they get back, and they need to **know exactly where to look to find what caused it.**

## Goals

1. **Detect a lie detector** appearing, **any whisper** (GM or not), and the white room a GM may teleport the character to.
2. On any of those: **save a screenshot and a timestamped log entry, then stop all input** (go AFK). Nothing else.
3. **Every session writes to one predictable place**: a session log, an incident folder per notable event (screenshot, time, what the bot was doing — current leg, state), and a rolling set of recent screenshots so stuck/death/disconnect cases (where the bot does nothing special in Phase 1) still leave a trail of what led up to them.
4. Rune attempts and their outcomes (Feature C), and route legs (Feature B), are recorded in the session log.

## Non-goals

- Solving the lie detector (Phase 4).
- Responding to GMs, or alerting the user remotely (Phase 3/4).
- Recovering from stuck, death, or disconnect (Phase 3).

## Decided by the user

- Lie detector, whisper, white room → screenshot + log, then AFK.
- **Any whisper stops the bot**, not only confirmed-GM ones. A false stop is preferred to a missed GM whisper (user, 2026-10-21). Claude applies the same leaning to the lie detector and the white room.
- Stuck / death / disconnect → nothing special; the user deals with it when back.
- Claude may **download the ~10 Reddit screenshots** of the GMS lie detector found in research (thread r/Maplestory `1tu542t`) for use as test images.
- The user has **no GM-whisper screenshot**; Claude finds what whispers and the white room look like itself, **researched inline** (not through the `deep-research-interactive` gate), with sources cited in this spec (user, 2026-10-21).
- Passive screen capture while the user is away is allowed (no input; see Feature B), for gameplay negatives.

## Constraints and facts

- **GMS lie detector** (research 2026-10-21, see `docs/vision.md` Phase 4): appears only on accounts flagged for unusual behaviour (v.264 patch notes). The only format seen on main GMS is "Find the Transparent Shape". Community reports: a ~10–15 s warning in the **lower-right corner** before it starts, **no sound**, mobs can still hit you. Going AFK means failing it; the Truth Booth timeout (escalating 1 min → 24 h) is the accepted cost until Phase 4.
- A GM whisper arrives in chat; a GM's looks like any whisper except the sender. Since any whisper stops the bot, the sender doesn't need to be read. The white room is an all-white/blank map.
- Detection is screen-reading only.

## Verification (first guess — tried early, gaps filled by Claude first)

- The Reddit lie-detector screenshots as positives; ordinary gameplay frames (Helix datasets, FF2 recording and passive capture) as negatives — to measure false alarms, which matter because a false alarm stops the bot.
- Whisper and white-room examples Claude sources itself (online screenshots/videos), plus ordinary non-whisper chat as negatives.
- Session-log output inspected from simulated and supervised runs.

## Reliability targets

*Set by Claude; the user can override any of them.* The positive sets are small and come from other people's screens, so a perfect score on them is weak evidence. They're re-checked against real frames when real events happen.

| What | Target |
|---|---|
| Lie detector | Every positive screenshot detected. 0 false alarms on ≥5,000 ordinary gameplay frames. |
| Whispers | Every whisper example detected. 0 false alarms on ≥5,000 frames, including ordinary non-whisper chat. |
| White room | Every example detected. 0 false alarms on the same negatives. |
| Stopping | From detection to all keys released and no further input, ≤500 ms, in 100/100 test-window trials, including mid-hold and mid-chord. |
| Trail | In simulated runs, every incident produces its folder (screenshot, time, state), and the session log has every leg and rune attempt. |
| In-game sign-off (supervised, later) | Over the supervised sessions, with D running: 0 false stops, and the session log and incident trail are complete. A lie detector can't be triggered on demand, so in-game detection of it is only confirmed if one actually appears. |

## Shipping

Only code that really works is merged (user, 2026-10-21). Branch `p1d-watching` off `p1c-runes` and push it to `origin`, rebasing if C changes. D **merges to `master` only after C has merged and D's in-game sign-off passes**.

## Left to the builder

- Detection methods and thresholds.
- Where the logs live (somewhere predictable and git-ignored) and their format.
- How "stop all input" is enforced (it must override anything B or C is doing).
- The Phase 1 top-level runner that ties A–D together (start/stop, the "farming vs going to a rune vs AFK" states), if not already built by B or C.
