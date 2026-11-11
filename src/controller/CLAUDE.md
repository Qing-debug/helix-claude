# src/controller: controller, map CSVs, path graph

camelCase package (root CLAUDE.md, "Naming"). Results, not exceptions (root, "Error handling").

## Edge choice (path_graph.py)

- For each node and direction, `_findClosestLeftNode`, `_findClosestRightNode`, `_findClosestNodeAbove` and `_findClosestNodeBelow` (path_graph.py:230, :276, :319, :376) take the closest node that is aligned within the x/y allowance and within `max_horizontal_traversal_dist` / `max_vertical_traversal_dist`.
- Vertical edges (decision H17): `_findClosestNodeAbove` / `_findClosestNodeBelow` skip every candidate that fails `_isVerticalEdgeValid` (:347, :404) and take the nearest of the rest, so a nearer platform end Kanna can't teleport to gives way to a farther node she can. Pinned in `tests/controller/test_path_graph_characterization.py` (section 4b, with the issue-6 S/M/T example). Route walking's own finder (`src/route/terrain.py:140-164`) still keeps the nearest node only, with no second try.
- `_isVerticalEdgeValid` (:414-441): a target flagged left platform edge is valid from a source whose x is ≥ the target's x; flagged right edge, from x ≤; flagged both, from anywhere; flagged neither, always.
- Ties: the distance comparisons are strict `>` (:260, :304, :352, :409), so among equally close nodes the first inserted wins and **`node_map` insertion order changes edges**. One such effect is gone for vertical edges (H17): an unreachable platform end inserted first no longer blocks the up/down link, which now goes to the equally near plain node in either order (pinned in `test_unreachable_platform_end_does_not_block_the_link_whichever_is_inserted_first`); the rest of the tie-break is unchanged.
- `src/route/terrain.py:163` calls `PathGraph._isVerticalEdgeValid`: don't rename it without that caller (and terrain.py is frozen, see `src/route/CLAUDE.md`).

## Map CSV readers

- The format constants and helpers are in `map_format.py` (pure: no `Node` or `EditorNode`). Readers: `csv_parser.py` (runtime) and `map_csv.py` (editor); the third, in the route simulator, is in `src/route/CLAUDE.md`.
- Both readers check the whole file with `map_format.read_rows` before building anything and return the same message (`problems_message`, plus `not_found_message` / `unreadable_message`): every bad row is reported with its file line number, a bad header is reported alone, `#` and blank lines are skipped (decision H16). The wording is what the user sees, pinned word for word in `tests/controller/test_map_csv_characterization.py`; change it there and in `map_format.py` together.
- Differences left, pinned in the same file: a bad `# minimap_size=` line passes the runtime and fails the editor (the editor's own check, `map_csv._parse_metadata_lines`, which runs before the row check, so in a file with a bad metadata line and bad rows the editor shows only the metadata problem and the runtime lists the rows); `CSVParser()` with no path fails with "CSV file is a None object, failed to load CSV file." (the editor always has a path); runtime loads create `Node`s, editor loads don't (below).
- A header-only file (the header, maybe `#` and blank lines, but no node rows) is refused by both readers with `no_nodes_message` (decision H19): `read_rows` itself accepts it, so each reader checks `if not rows` after the `problems` check. The editor never writes such a file (`_validateForSave` blocks a save with no blue node), so no editor round trip is refused. `path_graph.py`'s "Node_dict is empty" is now reached only by the editor's preview of an empty canvas.
- Ropes are not in the CSV (H94; plan `docs/superpowers/plans/2026-11-10-rope-fix.md`, C8 and C11): a map's ropes are in `<map stem>.ropes.json` beside it (`rope_file_for`, `rope_file.py:45`), read by `read_ropes` (:49) and written by `write_ropes` (:102) in `rope_file.py`, which also holds the format constants and the editor's keep-out (`near_rope_end`, :121, with `KEEP_OUT_X_PX` 5 and `KEEP_OUT_Y_PX` 6, Hong's QB231; `free_x`, :132, for the texts that name the free x's). `rope_finder.py` (`find_ropes`, :30, and `median_frame`) finds ropes in minimap frames for the editor's Find ropes. Both are pure and Tk-free, because the editor, the walker's session and the simulator all read the file and `src/controller` is the one package all three may import. No file means no ropes. Any other read failure (a bad rope, a wrong `format` or `version`, a file from a newer Helix, an unreadable file) is a named `Failure`: the walk refuses it (`session.py`, exit 5, before positions, capture or link) and so does the simulator, but the map editor opens the map's nodes with no ropes and says so (ruling ROPE148R-D1 = A, C11), and a later save with no ropes marked asks whether to remove the damaged file (Hong's QB236, H94), while a save with a rope replaces it. `csv_parser.py`, `map_csv.py` and `map_format.py` never read it. One wording trap (ROPE152-R3a m-R3a-3): the editor's load message ends "Mark the ropes again and save to replace the file", while the reader's fix text for a rope file from a newer Helix says "update Helix" (`NEWER_FIX_TEXT`, `rope_file.py:25`); both are shown for that one case, and the question box on save adds the reader's message after Hong's sentence.

## Nodes

`Node.node_id_counter` (node.py:8) is one global class counter, and every `Node(...)` and `CharacterNode(...)` takes the next value as its `uuid` (node.py:23, :62, :85): `uuid` is an int, not a UUID. The map editor's preview creates `Node`s too (`src/gui/CLAUDE.md`).

## Other traps

- `class Path(list)` (path_graph.py:10) shadows the name of `pathlib.Path`; don't import both unqualified into one module.
- There is no character lookup in the controller any more: `MapleAgent.getCharacterCoords` (which always returned its best guess, never "not found") was removed in decision H20. The GUI finds the character with `src/route/character.py`'s `CharacterLocator` (`src/gui/CLAUDE.md`); the controller can't import `src.route` (import-linter contract c), so keep any new lookup out of it.
- Output is `print`, not `logging` (path_graph.py, csv_parser.py).
- `Controller` (controller.py:27) only forwards to `MapleAgent` (controller.py:53); the GUI also calls `controller.agent` directly (`src/gui/CLAUDE.md`).

## Tests

`python -m pytest tests/controller -q` (its time is in the root CLAUDE.md).
