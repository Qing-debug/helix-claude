"""The real-ground and live-read fixtures from Hong's Fossil Foundry 2 runs (off-graph fix plan, Task 1, OFFD140).

`ff2_real_ground.json` is the ground the character really stands on, in the map's own reference (the row of the map
editor's nodes: the real ground row minus 3). `ff2_live_reads.json` is the positions the planner logged in the eight
saved runs, with no key data. The expected numbers come from OFFD140 sections 1 and 3, not from the fixtures.
"""

import ast
import csv
import json
from pathlib import Path

from returns.pipeline import is_successful

from src.route.sim.world import SimTerrain, load_terrain

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
REAL_GROUND = FIXTURES / "ff2_real_ground.json"
LIVE_READS = FIXTURES / "ff2_live_reads.json"
OLD_TERRAIN = FIXTURES / "ff2_terrain.json"
MAP = FIXTURES / "fossil_foundry_2.csv"
EVENTS = {"leg", "no_plan", "planned", "reached", "settled", "read_failed"}


def _terrain() -> SimTerrain:
    result = load_terrain(REAL_GROUND)
    assert is_successful(result), result
    return result.unwrap()


def _map_nodes() -> list[tuple[int, int]]:
    with MAP.open(newline="", encoding="utf-8") as f:
        rows = csv.DictReader(line for line in f if not line.startswith("#"))
        return [ast.literal_eval(row["pos_in_minimap"]) for row in rows]


def _platform_at(terrain: SimTerrain, x: int, y: float) -> tuple[str, float]:
    """The platform whose surface at x is nearest to y, with that surface."""
    found = [(p.name, p.surface_y(x)) for p in terrain.platforms if p.surface_y(x) is not None]
    assert found, f"no platform spans x={x}"
    return min(found, key=lambda item: abs(item[1] - y))


def test_real_ground_loads() -> None:
    real = _terrain()
    old = load_terrain(OLD_TERRAIN).unwrap()
    assert [(p.name, p.droppable) for p in real.platforms] == [(p.name, p.droppable) for p in old.platforms]
    assert real.minimap_size == old.minimap_size


def test_top_ramp_matches_the_recordings() -> None:
    top = next(p for p in _terrain().platforms if p.name == "top")
    # OFFG-T1-D1 (option A): the recordings put the ground at row 30 at x=68 (OFFD140 section 1 said 31 for x <= 68).
    expected = {x: 28 for x in range(50, 68)}
    expected.update({68: 27, 69: 26, 70: 26, 71: 25})
    expected.update({x: 24 for x in range(72, 101)})
    assert {x: top.surface_y(x) for x in expected} == expected


def test_flat_levels_under_the_nodes() -> None:
    terrain = _terrain()
    for x, y in _map_nodes():
        name, surface = _platform_at(terrain, x, y)
        if name == "top":
            want = 28 if x <= 66 else 24
        else:
            want = {"floor": 61, "mid-left": 46, "mid-right": 43}[name]
        assert surface == want, f"node ({x}, {y}) on {name}: surface {surface}, expected {want}"


def _live() -> dict:
    return json.loads(LIVE_READS.read_text(encoding="utf-8"))


def _keys_anywhere(value: object) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {k for v in value.values() for k in _keys_anywhere(v)}
    if isinstance(value, list):
        return {k for v in value for k in _keys_anywhere(v)}
    return set()


def test_live_reads_counts() -> None:
    live = _live()
    assert len(live["runs"]) == 8
    events = [e for run in live["runs"] for e in run["events"]]
    assert {e[1] for e in events} <= EVENTS
    counts = {name: sum(1 for e in events if e[1] == name) for name in ("no_plan", "planned", "reached")}
    assert counts == {"no_plan": 1229, "planned": 159, "reached": 55}
    assert len(live["side_teleports"]) == 48
    assert not {"keys", "sent"} & _keys_anywhere(live)
    text = LIVE_READS.read_text(encoding="utf-8")
    assert '"keys"' not in text and '"sent"' not in text


def test_standing_reads_sit_on_the_ground() -> None:
    terrain = _terrain()
    settled = [e for run in _live()["runs"] for e in run["events"] if e[1] == "settled"]
    assert settled
    on_ground = 0
    for _t, _ev, x, y in settled:
        _name, surface = _platform_at(terrain, x, y + 3)
        on_ground += abs(y + 3 - surface) <= 1
    assert on_ground / len(settled) >= 0.95, f"{on_ground} of {len(settled)} settled reads sit on the ground"
