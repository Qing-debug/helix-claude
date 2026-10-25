# src/gui: main window and map editor

camelCase package (root CLAUDE.md, "Naming"). No test drives Tk; `tests/controller/test_map_csv_characterization.py` calls `MapEditor._buildPreviewGraph` on a stand-in `self` to pin the preview order.

## Traps

- Preview order: `_buildPreviewGraph` (map_editor.py:843) inserts nodes in `map_format.ordered_local_ids` order (map_editor.py:859), the order `write_editor_nodes` (map_csv.py) writes, so the preview's edges match the runtime's after save→load. Change the order only in `ordered_local_ids`.
- The preview builds real `Node`s (map_editor.py:864), so every preview bumps the global `Node.node_id_counter` (`src/controller/CLAUDE.md`); editor loads don't.
- KNOWN BUG, don't copy: the canvas-setup loop holds `minimap_frame_lock` (the `with` at gui.py:211) while it sleeps 10 s (gui.py:224) waiting for a frame, which blocks the vision thread's writes for that time.
- The buttons labelled "(delete or move to a separate tab on the GUI)" and "(delete after done testing)" (gui.py:178-195) are test leftovers: `connectCharacterNode` and `deleteNode` are wired with no node argument (controller.py:43-47). Don't build on them.
- The character picker uses `CharacterClass.__subclasses__()` (gui.py:151): direct subclasses only, so a new class must subclass `CharacterClass` directly.
- The GUI reaches past the `Controller` facade into `controller.agent` (gui.py:247, :251, :296, :331); the map editor does too (`buildPathGraphFor`, `characterClass`).
- To find the character in new code use route's `CharacterLocator`, not `getCharacterCoords` (`src/controller/CLAUDE.md`).
