# src/vision: minimap capture

How it works is in the root CLAUDE.md ("Vision", "Threads and shared state"). No tests cover this package.

## Traps

- Don't rename or restructure `vision.py` or `vision_helper_lib.py`: Feature C starts from Helix's rune work including its uncommitted changes to both files (`docs/superpowers/specs/2026-10-21-p1c-runes-design.md:28`), and a restructure here turns that port into a hand merge.
- `bettercam` is imported at module scope (vision.py:1), so importing `src.vision.vision` needs Windows and bettercam. `src/route` may import it only inside a function (`src/route/harness/record_minimap.py:163`); `tests/route/test_import_rules.py` and import-linter contract e enforce that.
- `capture_and_process_minimap` shows a `cv2.imshow("Minimap", ...)` debug window and pumps it every frame (vision.py:83-84).
- When the corner match fails, the loop goes straight back (`continue`, vision.py:61-63) to another full-monitor grab and match, with no sleep. Inside the minimap loop, `cv2.waitKey(FRAME_DELAY_MS)` and `time.sleep(THREAD_SLEEP_DURATION_S)` (vision.py:84-85) pace it.
- `vision_helper_lib.py:3` imports `enum.Enum` unused; ruff's F401 is ignored for that file in `pyproject.toml` on purpose, so the file stays as Helix has it until the port.
