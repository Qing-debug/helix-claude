# tests

## Layout

- Mirrors `src/`: `tests/controller`, `tests/input`, `tests/route`, `tests/route_view`, plus `tests/test_smoke.py` and `tests/test_anti_cheat_names.py` (the name scan over `src` and `tools`, root CLAUDE.md, Safety rules); shared data in `tests/fixtures/` (`fossil_foundry_2.csv`, `ff2_terrain.json`).
- The Route tab's tests: `tests/controller/test_route_tab.py` (the tab, with fakes for the session, the clock and the view), `test_gui_notebook.py` (the Setup and Route tabs, the run lock), `test_live_minimap.py` and `tk_bans.py` (a flat helper: the source scan for the Tk calls the app may not make during a run, spec 5.12); `tests/route/test_session.py` (`RunSession`), `test_route_tab_sim.py` (`tools/route_tab_sim.py`, with no window) and `test_recording_rate.py` (`tools/recording_rate.py`).
- No `__init__.py` and no `conftest.py` anywhere under `tests/`. pytest puts each test file's directory on `sys.path`, so basenames must be unique across `tests/` (plan hard rule 8) and helpers are imported flat (`from input_fakes import ...`; the fakes are described in `src/input/CLAUDE.md`).

## Speed

- The `slow` marker (`pytest.ini`, with `--strict-markers`) is for tests of about 0.5 s or more and subprocess tests: 63 today: 42 in `tests/input`, 14 in `tests/route` (in `test_detection_check.py`, `test_preview_sim.py`, `test_route_preview.py`, `test_route_tab_sim.py`, `test_session.py`, `test_sim_steps.py`, `test_step_log.py` and `test_step_log_writer.py`) and 7 in `tests/route_view`. `python -m pytest --collect-only -m slow -q` gives the count. `python -m pytest -m "not slow"` is the fast loop; `tools/check.py` runs it and `tools/check.py --full` runs everything (times in the root CLAUDE.md).
- Times: the check totals and the `tests/route`, `tests/route_view` and `tests/controller` times are in the root CLAUDE.md (Running); `tests/input` takes about 65 s. Measured 2026-11-01 on the dev PC, each run alone.

## Traps

- Patch the module where the code under test looks the name up. `_clock` and `_sleep` are separate globals in several modules, so the probe tests patch each one: the `clock` fixture (test_probe.py:221-228) sets them on `soak`, `input_layer` and `probe`, and patches `soak._read_answer`. A move that leaves a re-export behind makes such a patch miss without any error; `tests/input/test_guard_targets.py` pins the harness names that are patched and the module each must be defined in.
- The 10 skips are the wire-only rejects in `tests/input/test_protocol.py` (marked at :71, reason at :45): the PC never builds such a frame. They do run, in `firmware/test/test_protocol.cpp`, which `tests/input/test_firmware_host.py` builds and runs with g++ (slow; skipped if g++ is not on PATH).
- `tests/test_anti_cheat_names.py` writes each banned name split into adjacent pieces, so no name is plain text anywhere in the repo. The scan counts comments, strings and docstrings in `src/` and `tools/`, so never write one there, even to explain a pause or a rule.
- `tests/input/test_guard_targets.py` is skipped off Windows; `tests/route/test_import_rules.py` reads the `src/route` sources as text (AST) and imports no `src` module.

## basedpyright baseline

- `basedpyright` (a `tools/check.py` step) reports only errors that are not in `.basedpyright/baseline.json` (146 entries when this was written). Its `include` in `pyproject.toml` is `src` plus `tests/input/input_fakes.py`.
- When you fix a baselined error, a plain `basedpyright` run rewrites the baseline smaller: commit the rewritten file with your change (`tools/check.py` warns while it is uncommitted).
- When code with a baselined error moves to another file, run `basedpyright --writebaseline` (it exits 1 even though it wrote) and check that the entry count did not rise. Don't use `--writebaseline` to hide a new error.
