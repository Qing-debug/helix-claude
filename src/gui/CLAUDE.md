# src/gui: main window and map editor

camelCase package (root CLAUDE.md, "Naming"). `tests/controller/test_map_csv_characterization.py` calls `MapEditor._buildPreviewGraph` on a stand-in `self` to pin the preview order, and `tests/controller/test_map_load_errors.py` calls `MapleGUI._loadMapBrowse` the same way, with `filedialog` and `messagebox` patched, to pin the bad-map message box (decision H16). `tests/controller/test_gui_startup.py` is the one test that builds a real `MapleGUI` on a real, withdrawn Tk root (no mainloop; `after` is replaced by a recorder); it retries the Tk start a few times and skips if Tk still cannot start, because Tk now and then fails to read its own `init.tcl` on the dev PC.

## Traps

- Preview order: `_buildPreviewGraph` (map_editor.py:837) inserts nodes in `map_format.ordered_local_ids` order (map_editor.py:853), the order `write_editor_nodes` (map_csv.py) writes, so the preview's edges match the runtime's after save→load. Change the order only in `ordered_local_ids`.
- The preview builds real `Node`s (map_editor.py:858), so every preview bumps the global `Node.node_id_counter` (`src/controller/CLAUDE.md`); editor loads don't.
- Startup does not wait for the game (decision H18). `_setupGUI` ends with a "Minimap not found yet" label (gui.py:215); `_updateCanvas` (gui.py:393) builds the minimap Canvas in its place on the first frame (`_createMinimapCanvas`, gui.py:376), like `MapEditor._waitForMinimapAndBuildCanvas` does. Until then `minimap_canvas` is None, so any new code that touches it or reads the character from the frame must start with `if not self._minimapReady(): return` (gui.py:218), as the four minimap buttons do. Read `minimap_frame` under `minimap_frame_lock` into a local and release the lock before any Tk call or sleep (the old startup loop slept 10 s with the lock held and starved the vision thread).
- The buttons labelled "(delete or move to a separate tab on the GUI)" and "(delete after done testing)" (gui.py:184-201) are test leftovers: `connectCharacterNode` and `deleteNode` are wired with no node argument (controller.py:43-47). Don't build on them.
- The character picker uses `CharacterClass.__subclasses__()` (gui.py:157): direct subclasses only, so a new class must subclass `CharacterClass` directly.
- The GUI reaches past the `Controller` facade into `controller.agent` (gui.py:253, :257, :304, :339); the map editor does too (`buildPathGraphFor`, `characterClass`).
- To find the character in new code use route's `CharacterLocator`, not `getCharacterCoords` (`src/controller/CLAUDE.md`).
