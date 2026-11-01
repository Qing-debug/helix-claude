# src/vision: minimap capture

How it works is in the root CLAUDE.md ("Vision", "Threads and shared state"). No test runs this package; two read `vision.py` as text (the last bullet under Traps).

## Traps

- Don't rename or restructure `vision.py` or `vision_helper_lib.py`: Feature C starts from Helix's rune work including its uncommitted changes to both files (`docs/superpowers/specs/2026-10-21-p1c-runes-design.md:28`), and a restructure here turns that port into a hand merge.
- `bettercam` is imported at module scope (vision.py:1), so importing `src.vision.vision` needs Windows and bettercam. `src/route` may import it only inside a function (`src/route/harness/record_minimap.py:163`); `tests/route/test_import_rules.py` and import-linter contract e enforce that.
- When the corner match fails, the loop goes straight back (`continue`, vision.py:61-63) to another full-monitor grab and match, with no sleep. Inside the minimap loop, `time.sleep(FRAME_DELAY_MS / 1000)` and `time.sleep(THREAD_SLEEP_DURATION_S)` (vision.py:83-84) pace it.
- `vision_helper_lib.py:3` imports `enum.Enum` unused; ruff's F401 is ignored for that file in `pyproject.toml` on purpose, so the file stays as Helix has it until the port.
- The capture loop opens no OpenCV window: the per-frame pause is `time.sleep(FRAME_DELAY_MS / 1000)` (vision.py:83), which replaced a window's key wait (H67, spec 5.12-5). Two tests in `tests/test_anti_cheat_names.py` read `vision.py` as text: the name scan (spec 5.12-4 and 5.12-5) fails if one of 5.12-5's three OpenCV window and key-wait names comes back, and `test_capture_loop_keeps_its_pause` fails if the pause line goes.
