# tests

## Layout

- Mirrors `src/`: `tests/controller`, `tests/input`, `tests/route`, plus `tests/test_smoke.py`; shared data in `tests/fixtures/` (`fossil_foundry_2.csv`, `ff2_terrain.json`).
- No `__init__.py` and no `conftest.py` anywhere under `tests/`. pytest puts each test file's directory on `sys.path`, so basenames must be unique across `tests/` (plan hard rule 8) and helpers are imported flat (`from input_fakes import ...`; the fakes are described in `src/input/CLAUDE.md`).

## Speed

- The `slow` marker (`pytest.ini`, with `--strict-markers`) is for tests of about 0.5 s or more and subprocess tests: 42 today, all in `tests/input`. `python -m pytest -m "not slow"` is the fast loop; `tools/check.py` runs it and `tools/check.py --full` runs everything (times in the root CLAUDE.md).
- `tests/controller` takes about 1.5 s and `tests/route` about 5 s; nearly all the rest of the full run is `tests/input`.

## Traps

- Patch the module where the code under test looks the name up. `_clock` and `_sleep` are separate globals in several modules, so the probe tests patch each one: the `clock` fixture (test_probe.py:221-228) sets them on `soak`, `input_layer` and `probe`, and patches `soak._process_names`. A move that leaves a re-export behind makes such a patch miss without any error; `tests/input/test_guard_targets.py` pins the harness names that are patched and the module each must be defined in.
- The 10 skips are the wire-only rejects in `tests/input/test_protocol.py` (marked at :71, reason at :45): the PC never builds such a frame. They do run, in `firmware/test/test_protocol.cpp`, which `tests/input/test_firmware_host.py` builds and runs with g++ (slow; skipped if g++ is not on PATH).
- `tests/input/test_guard_targets.py` is skipped off Windows; `tests/route/test_import_rules.py` reads the `src/route` sources as text (AST) and imports no `src` module.

## basedpyright baseline

- `basedpyright` (a `tools/check.py` step) reports only errors that are not in `.basedpyright/baseline.json` (170 entries when this was written). Its `include` in `pyproject.toml` is `src` plus `tests/input/input_fakes.py`.
- When you fix a baselined error, a plain `basedpyright` run rewrites the baseline smaller: commit the rewritten file with your change (`tools/check.py` warns while it is uncommitted).
- When code with a baselined error moves to another file, run `basedpyright --writebaseline` (it exits 1 even though it wrote) and check that the entry count did not rise. Don't use `--writebaseline` to hide a new error.
