# Phase 1 · Feature B — Walking the route

*Design, 2026-10-21. Part of Phase 1 in `docs/vision.md`; read that first. Depends on Feature A (input).*

## Why

The core of the bot is moving around a map along a route. The route graph exists (`src/controller/path_graph.py`) but the loop that actually moves the character is commented out (`MapleAgent.executePathFinding`) and refers to an old movement API. Phase 1 needs the character to walk its rotation reliably before runes (Feature C) or, later, attacking (Phase 2) can sit on top.

## Goals

1. On **Fossil Foundry 2** (`maps/fossil_foundry_2.csv`, minimap 162×82) with **Kanna**, the bot cycles through the blue nodes in file order, forever, passing through orange nodes as needed, until stopped.
2. It moves with Kanna's abilities and **chooses which to use**:
   - **Teleport**: arrow + `E`, any of the four directions. Measured reach: 19 px horizontal, 23 px vertical on the minimap (`src/classes/classes.py`, user-measured).
   - **Walk**: hold an arrow, for short distances.
   - **Drop through a platform**: `Down` + `Alt`, only where the node has `can_fall = TRUE`.
   - Teleport-down vs drop is the bot's choice when both are possible.
3. **The bot works out the terrain from the node layout.** The user will not label slopes, steps, or walk-vs-teleport links, on this map or any other. Where nodes imply a walkable slope or a near-aligned vertical move, the bot must handle it.
4. It recovers from small errors by itself: overshooting, landing slightly off, a move that didn't happen. It re-plans from where the character actually is.
5. Lap time and per-leg outcomes are logged (feeds Phase 2's "good rotation" work and Feature D's session log).

## Known cases this must handle (from the current map)

The current graph only joins nodes that are level within 2 px or stacked within 2 px. On Fossil Foundry 2 two legs of the rotation have no path:

- `(49, 28) → (101, 24)`: the top level steps from y≈28 to y≈24 between x≈66 and x≈79. **User: it's a slope you can walk.**
- `(101, 24) → (105, 42)`: no node below `(101, 24)` within 2 px horizontally; nearest are `(105, 42)` and `(97, 43)`, 4 px to the side. `(97, 43)` has `can_fall = TRUE`.

These are test cases, not things to fix by editing the map.

## Non-goals

- Attacking (Phase 2). The rotation is walked without attacking.
- Optimising for XP/gold. That's Phase 2. Context for later: a good rotation balances mobs killed and items picked up (the pet loots when the character passes over drops) against reliably hitting target XP and gold rates; mobs respawn 7 s after the first one on the map is killed.
- Recovering from being stuck, dying, or disconnecting. In Phase 1 the bot does nothing special; Feature D's logs show what happened.
- Other characters or maps, beyond not hard-coding anything that only fits this one.

## Decided by the user

- Character: Kanna. Map: Fossil Foundry 2. Keys as above.
- Extra user-set configuration may turn out to be needed for smooth movement; that's decided **after** testing shows what's missing, not designed up front.
- The bot is **not run in the game while the user is away**. In-game runs are short, supervised sessions.

## Constraints and facts

- Character position comes from template-matching `assets/chr/chr.png` (+ mask) on the minimap frame (`MapleAgent.getCharacterCoords`).
- Minimap capture: `src/vision/vision.py`. Helix's local `rune_solver` branch has a newer `MinimapCapture` class + single locked `CameraWrapper` (commit `f0d2be4`) that Feature C brings over; this feature should build on that if it lands first.
- Known weakness: on 2026-10-21 the desktop wallpaper produced ~0.9995 corner-template matches. The rectangle checks rejected them, but minimap detection can be fooled by background imagery.
- The frame in `SharedInfo` is stored RGB despite a comment saying BGR.
- All timing comes from Feature A's human-like input layer.

## Verification (first guess — tried early, gaps filled by Claude first)

- **Offline:** a simulated character on this map (teleport reach known; walk/drop/teleport timings and variance estimated) driving the real planner and control loop, so planning, terrain inference, and recovery are tested without the game. Target: many consecutive simulated laps with every leg completed and no stalls.
- **Real minimap frames of Fossil Foundry 2** to test character detection and the sim's realism. None exist yet — Claude first tries to obtain them itself; otherwise they come from the first supervised session.
- **In-game:** supervised short runs with the user for sign-off.

## Left to the builder

- Path-graph changes (slopes, near-aligned moves, drop edges) and how terrain is inferred from nodes.
- The control loop's form (Helix's earlier design chose strict lock-step with on-screen confirmation; keep, change, or replace it), tolerances, and timeouts.
- The movement simulator.
- Ability choice (walk vs teleport vs drop).
