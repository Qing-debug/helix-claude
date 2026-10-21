# Phase 1 · Feature C — Runes

*Design, 2026-10-21. Part of Phase 1 in `docs/vision.md`; read that first. Depends on Features A (input) and B (moving).*

## Why

A rune left on the map curses it after a few minutes: mobs stop giving XP and drops. A missed rune is the clearest failure the bot can have. It doesn't have to be solved instantly, but soon after it spawns.

## Goals

1. The bot **notices a rune has spawned** on the map.
2. It **finishes the leg it's on** (reaches the node it was travelling to), then goes to the rune.
3. At the rune it presses `Space` to interact, **reads the arrow panel** and **presses the arrows left to right** with human-like timing (Feature A).
4. **Spinning arrows**: it works out the real underlying direction and presses that.
5. **On a failed solve, it waits 8 seconds and retries**, repeating until the rune is solved or gone. Every attempt is logged (Feature D).
6. Then it resumes the rotation.

## Non-goals

- Instant reaction to a spawn; the few-minutes window is the budget.
- Changing channel after repeated failures (Phase 3).
- ONNX deployment: `onnxruntime` is broken in Helix's environment and the `.onnx` Stage B export has a known miss (Helix `ISSUES.md` #7). Ship with `.pt` weights for now.

## Decided by the user

- Interaction: `Space`, then the arrows shown. Spinning arrows need their real direction.
- Retry: 8 s after a failure (Claude's call, recorded in the vision: repeat until solved or the rune disappears).
- **Start from Helix's rune work** rather than rebuild: the local, unpushed `rune_solver` branch in `~/PycharmProjects/Helix` (HEAD `f0d2be4`) **including its uncommitted changes** to `src/rune_solver/pipeline.py`, `src/vision/vision.py`, `src/vision/vision_helper_lib.py`. Helix `src/main.py` on that branch is a test harness; don't bring it over as-is.
- **Spinning-arrow method is Claude's choice**, picked by measured accuracy on real spinning bursts with known answers. Three candidates:
  1. a YOLO-pose model with two keypoints (tip, tail), trained on synthetic rotated sprites (Helix `RESUME.md` v8.31 plan);
  2. the classical base-and-tip method in Helix `debugging_scripts/opus5.5-2026-09-23-stageb-on-spinning/` (`keypoints.py`; unaudited) and `docs/learning/rune-cv-workflow/workflow-3-arrow-base-tip.md`;
  3. the idea in `pipeline.py`'s TODO: across a burst, the arrow briefly moves against its spin direction, and that angle is the real direction.
  Or a combination, if it measures better.

## Constraints and facts

- Two-stage detector, shipping weights in `assets/models/`:
  - Stage A: full screenshot → `Rune_Panel` box. Frozen test mAP50 0.995.
  - Stage B: panel crop → 5 classes `up, down, left, right, spinning`. Frozen test mAP50 0.9949; user-verified 192/192 boxes on 49 test panels. `spinning` is a state, not a direction.
- yolo26 is NMS-free: `iou=` on predict is ignored; dedupe with `torchvision.ops.nms(..., 0.5)`. Deploy confidence 0.25. (Helix `modeltraining/README.md`, `docs/reference/grounding/ultralytics.md`.)
- `pipeline.py` already: runs both stages, requires exactly 4 arrows, bursts the panel region once for all spinning arrows, cuts an expanded square per arrow per frame. The direction solver is a placeholder.
- Data in Helix: `modeltraining/stage_{a,b}/dataset/` (with test splits), `data/rune_spinning_mining/`, `data/rune_dedup_DETERMINISTIC/` (scenes grouped by map), saved bursts in `debugging_scripts/`. Helix `ISSUES.md` #6: Stage A misses some fill-less Traditional-Chinese-client frames (not GMS).
- Unverified game facts (threat model M1): the time limit to enter arrows, curse timings.

## Verification (first guess — tried early, gaps filled by Claude first)

- **Static arrows:** Stage A/B test splits with known answers — end-to-end panel → arrow sequence.
- **Spinning arrows:** real spinning bursts with known correct answers. **This is the expected gap**: existing bursts have no recorded answers and the base/tip swipe audit was never done. Claude first tries to establish ground truth itself (e.g. frames in a burst where the arrow is momentarily readable, agreement across independent methods, checked by eye on a sample); if it can't reach a trustworthy set, it asks the user — e.g. noting the accepted answer when solving runes by hand.
- **Spawn detection:** full screenshots from the Helix datasets that show a rune on the minimap / on screen, and ones that don't.
- **In-game:** supervised sessions for sign-off.

## Left to the builder

- How a rune spawn is detected, and how its location is turned into a movement target for Feature B.
- How "finish the current leg" behaves at the edges (rune behind the bot, very long leg).
- The spinning-arrow method.
- How success/failure of a solve is read from the screen.
