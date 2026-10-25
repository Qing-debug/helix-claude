# src/controller: controller, map CSVs, path graph

camelCase package (root CLAUDE.md, "Naming"). Results, not exceptions (root, "Error handling").

## Edge choice (path_graph.py)

- For each node and direction, `_findClosestLeftNode`, `_findClosestRightNode`, `_findClosestNodeAbove` and `_findClosestNodeBelow` (path_graph.py:230, :276, :319, :376) take the closest node that is aligned within the x/y allowance and within `max_horizontal_traversal_dist` / `max_vertical_traversal_dist`.
- Vertical edges (decision H17): `_findClosestNodeAbove` / `_findClosestNodeBelow` skip every candidate that fails `_isVerticalEdgeValid` (:347, :404) and take the nearest of the rest, so a nearer platform end Kanna can't teleport to gives way to a farther node she can. Pinned in `tests/controller/test_path_graph_characterization.py` (section 4b, with the issue-6 S/M/T example). Route walking's own finder (`src/route/terrain.py:140-164`) still keeps the nearest node only, with no second try.
- `_isVerticalEdgeValid` (:414-441): a target flagged left platform edge is valid from a source whose x is ≥ the target's x; flagged right edge, from x ≤; flagged both, from anywhere; flagged neither, always.
- Ties: the distance comparisons are strict `>` (:260, :304, :352, :409), so among equally close nodes the first inserted wins and **`node_map` insertion order changes edges**.
- `src/route/terrain.py:163` calls `PathGraph._isVerticalEdgeValid`: don't rename it without that caller (and terrain.py is frozen, see `src/route/CLAUDE.md`).

## Map CSV readers

- The format constants and helpers are in `map_format.py` (pure: no `Node` or `EditorNode`). Readers: `csv_parser.py` (runtime) and `map_csv.py` (editor); the third, in the route simulator, is in `src/route/CLAUDE.md`.
- Both readers check the whole file with `map_format.read_rows` before building anything and return the same message (`problems_message`, plus `not_found_message` / `unreadable_message`): every bad row is reported with its file line number, a bad header is reported alone, `#` and blank lines are skipped (decision H16). The wording is what the user sees, pinned word for word in `tests/controller/test_map_csv_characterization.py`; change it there and in `map_format.py` together.
- Differences left, pinned in the same file: a bad `# minimap_size=` line passes the runtime and fails the editor (the editor's own check, `map_csv._parse_metadata_lines`, which runs before the row check, so in a file with a bad metadata line and bad rows the editor shows only the metadata problem and the runtime lists the rows); `CSVParser()` with no path fails with "CSV file is a None object, failed to load CSV file." (the editor always has a path); runtime loads create `Node`s, editor loads don't (below).
- A header-only file (the header, maybe `#` and blank lines, but no node rows) is refused by both readers with `no_nodes_message` (decision H19): `read_rows` itself accepts it, so each reader checks `if not rows` after the `problems` check. The editor never writes such a file (`_validateForSave` blocks a save with no blue node), so no editor round trip is refused. `path_graph.py`'s "Node_dict is empty" is now reached only by the editor's preview of an empty canvas.

## Nodes

`Node.node_id_counter` (node.py:8) is one global class counter, and every `Node(...)` and `CharacterNode(...)` takes the next value as its `uuid` (node.py:23, :62, :85): `uuid` is an int, not a UUID. The map editor's preview creates `Node`s too (`src/gui/CLAUDE.md`).

## Other traps

- `class Path(list)` (path_graph.py:10) shadows the name of `pathlib.Path`; don't import both unqualified into one module.
- `MapleAgent.getCharacterCoords` (controller.py:144) converts the RGB frame with `cv2.COLOR_BGR2GRAY` (:149). New code finds the character with `src/route/character.py`'s `CharacterLocator`.
- Output is `print`, not `logging` (path_graph.py, csv_parser.py).
- `Controller` (controller.py:28) only forwards to `MapleAgent` (controller.py:57); the GUI also calls `controller.agent` directly (`src/gui/CLAUDE.md`).

## Tests

`python -m pytest tests/controller -q` (about 1.5 s).
