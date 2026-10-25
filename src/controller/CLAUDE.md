# src/controller: controller, map CSVs, path graph

camelCase package (root CLAUDE.md, "Naming"). Results, not exceptions (root, "Error handling").

## Edge choice (path_graph.py)

- For each node and direction, `_findClosestLeftNode`, `_findClosestRightNode`, `_findClosestNodeAbove` and `_findClosestNodeBelow` (path_graph.py:230, :276, :318, :371) take the closest node that is aligned within the x/y allowance and within `max_horizontal_traversal_dist` / `max_vertical_traversal_dist`.
- Vertical edges: the closest node above or below is picked first and only then checked with `_isVerticalEdgeValid` (:313, :366). If it fails there is no up/down edge at all, even when a farther node would pass (no fallback). This is pinned as current behaviour in `tests/controller/test_path_graph_characterization.py`; changing it is a behaviour decision, not a cleanup.
- `_isVerticalEdgeValid` (:405-432): a target flagged left platform edge is valid from a source whose x is ≥ the target's x; flagged right edge, from x ≤; flagged both, from anywhere; flagged neither, always.
- Ties: the distance comparisons are strict `>` (:260, :304, :348, :401), so among equally close nodes the first inserted wins and **`node_map` insertion order changes edges**.
- `src/route/terrain.py:163` calls `PathGraph._isVerticalEdgeValid`: don't rename it without that caller (and terrain.py is frozen, see `src/route/CLAUDE.md`).

## Map CSV readers

- The format constants and helpers are in `map_format.py` (pure: no `Node` or `EditorNode`). Readers: `csv_parser.py` (runtime) and `map_csv.py` (editor); the third, in the route simulator, is in `src/route/CLAUDE.md`.
- Both readers check the whole file with `map_format.read_rows` before building anything and return the same message (`problems_message`, plus `not_found_message` / `unreadable_message`): every bad row is reported with its file line number, a bad header is reported alone, `#` and blank lines are skipped (decision H16). The wording is what the user sees, pinned word for word in `tests/controller/test_map_csv_characterization.py`; change it there and in `map_format.py` together.
- Differences left, pinned in the same file: a bad `# minimap_size=` line passes the runtime and fails the editor (the editor's own check, `map_csv._parse_metadata_lines`); `CSVParser()` with no path fails with "CSV file is a None object, failed to load CSV file." (the editor always has a path); runtime loads create `Node`s, editor loads don't (below).
- A header-only file (no node rows) passes both readers; the runtime then fails later, when the graph is built from an empty `node_map`.

## Nodes

`Node.node_id_counter` (node.py:8) is one global class counter, and every `Node(...)` and `CharacterNode(...)` takes the next value as its `uuid` (node.py:23, :62, :85): `uuid` is an int, not a UUID. The map editor's preview creates `Node`s too (`src/gui/CLAUDE.md`).

## Other traps

- `class Path(list)` (path_graph.py:10) shadows the name of `pathlib.Path`; don't import both unqualified into one module.
- `MapleAgent.getCharacterCoords` (controller.py:144) converts the RGB frame with `cv2.COLOR_BGR2GRAY` (:149). New code finds the character with `src/route/character.py`'s `CharacterLocator`.
- Output is `print`, not `logging` (path_graph.py, csv_parser.py).
- `Controller` (controller.py:28) only forwards to `MapleAgent` (controller.py:57); the GUI also calls `controller.agent` directly (`src/gui/CLAUDE.md`).

## Tests

`python -m pytest tests/controller -q` (about 1.5 s).
