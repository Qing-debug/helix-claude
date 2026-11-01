"""The Route tab's idle live minimap (spec 5.2 "What the view area shows", H70; contract C7 of
docs/superpowers/plans/2026-10-31-route-tab.md).

Shown in the tab's view area whenever no run view and no replay is: the shared minimap frame at the view's 4x, the
loaded map's nodes in the view's look and the character where route's `CharacterLocator` finds it. It is not a
`RouteViewWindow`, which is built for one run folder: it is one Canvas, built once with the tab, packed and hidden by
the tab (`RouteTab._showMinimap`, `_hideMinimap`). The tab calls `draw()` from its 100 ms drain, on the Tk thread, only
while the canvas is packed and the Route tab is in front. Nothing here prints, so a miss writes no console line.
"""
import tkinter as tk
from pathlib import Path
from tkinter import font as tkfont
from typing import TYPE_CHECKING, NamedTuple

from PIL import Image, ImageTk
from returns.pipeline import is_successful

from src.controller.map_csv import read_editor_nodes
from src.controller.map_format import BLUE_NODE
from src.route.character import CharacterLocator
from src.route_view.model import DEFAULT_SCALE
from src.route_view.window import (BLUE_FILL, BLUE_LINE, CHARACTER, DARK, LABEL_FG, NODE_R, ORANGE_FILL, ORANGE_LINE,
                                   ORANGE_R)

if TYPE_CHECKING:
    import numpy as np

    from src.gui.gui import MapleGUI

NOT_FOUND_TEXT = "Minimap not found yet"  # the canvas before the first frame (spec 5.2)
NOT_FOUND_PX = (480, 160)  # the dark canvas's size until a frame gives it the minimap's


class _Mark(NamedTuple):
    pos: tuple[int, int]
    blue: bool
    label: str  # "#1", "#2", ... for blue nodes in rotation order; "" for orange ones


def _readMarks(csv: Path | None) -> tuple[_Mark, ...]:
    """The map's nodes, read with the editor's reader (no runtime `Node` is made, so `Node.node_id_counter` stays):
    blue nodes in rotation order (file order) labelled `#1`, `#2`, ..., then the orange ones. No map, or a file that
    cannot be read: no nodes."""
    if csv is None:
        return ()
    read = read_editor_nodes(str(csv))
    if not is_successful(read):
        return ()
    nodes, rotation_order, _ = read.unwrap()
    by_id = {node.local_id: node for node in nodes}
    marks = [_Mark(by_id[i].pos, True, f"#{n}") for n, i in enumerate(rotation_order, 1)]
    marks += [_Mark(node.pos, False, "") for node in nodes if node.nodetype != BLUE_NODE]
    return tuple(marks)


class LiveMinimap:
    """C7: `canvas` is built in `parent` and not packed; `draw()` is one redraw and never raises."""

    canvas: tk.Canvas

    def __init__(self, parent: tk.Misc, gui: "MapleGUI") -> None:
        self._parent = parent
        self._gui = gui
        self.canvas = tk.Canvas(parent, width=NOT_FOUND_PX[0], height=NOT_FOUND_PX[1], bg=DARK, highlightthickness=0,
                                bd=0)
        self._label_font = tkfont.nametofont("TkDefaultFont", root=parent).copy()
        self._label_font.configure(size=8)  # as the view's labels (window.py:131-132)
        self._csv: Path | None = None  # the `CSV_file` object the marks were read from
        self._marks: tuple[_Mark, ...] = ()
        self._locator: CharacterLocator | None = None
        self._locator_tried = False  # built once; a build that failed is not retried
        self._photo: ImageTk.PhotoImage | None = None  # the shown picture, kept so Tk does not drop it

    def draw(self) -> None:
        """One redraw: the frame, the nodes, the character; the dark canvas with `NOT_FOUND_TEXT` before the first
        frame. An error leaves the canvas as last drawn (the 100 ms drain must go on; nothing is printed)."""
        try:
            self._draw()
        except Exception:
            pass

    def _draw(self) -> None:
        shared = self._gui.sharedInfo
        frame = None
        if shared is not None:
            with shared.minimap_frame_lock:  # the reference only: no Tk call or match while the lock is held
                frame = shared.minimap_frame
        canvas = self.canvas
        if frame is None:
            canvas.delete("all")
            canvas.configure(width=NOT_FOUND_PX[0], height=NOT_FOUND_PX[1])
            canvas.create_text(NOT_FOUND_PX[0] // 2, NOT_FOUND_PX[1] // 2, text=NOT_FOUND_TEXT, fill=LABEL_FG,
                               tags="notfound")
            self._photo = None
            return
        controller = self._gui.controller
        csv = controller.agent.CSVParser.CSV_file if controller is not None else None
        if csv is not self._csv:  # a new object per load (controller.py:121): a reload of the same file reads again
            self._csv = csv
            self._marks = _readMarks(csv)
        s = DEFAULT_SCALE
        image = Image.fromarray(frame)
        w, h = image.size
        photo = ImageTk.PhotoImage(image.resize((w * s, h * s), Image.Resampling.NEAREST), master=self._parent)
        character = self._character(frame)
        canvas.delete("all")
        canvas.configure(width=w * s, height=h * s)
        canvas.create_image(0, 0, image=photo, anchor="nw", tags="bg")
        self._photo = photo
        for mark in self._marks:  # the view's look (window.py:610-621)
            x, y = mark.pos[0] * s, mark.pos[1] * s
            r = (NODE_R if mark.blue else ORANGE_R) * s
            canvas.create_oval(x - r, y - r, x + r, y + r, fill=BLUE_FILL if mark.blue else ORANGE_FILL,
                               outline=BLUE_LINE if mark.blue else ORANGE_LINE, width=1, tags="node")
            if mark.blue:
                canvas.create_text(x + r + 2, y, text=mark.label, anchor="w", fill=LABEL_FG, font=self._label_font,
                                   tags="label")
        if character is not None:  # as the view draws it (window.py:606-608)
            x, y, r = character[0] * s, character[1] * s, 2 * s
            canvas.create_oval(x - r, y - r, x + r, y + r, fill=CHARACTER, outline="white", tags="character")

    def _character(self, frame: "np.ndarray") -> tuple[int, int] | None:
        """The character's centre on `frame`, or None (a miss, an unsure match, a bad frame, no icon files)."""
        if not self._locator_tried:
            self._locator_tried = True
            # Imported here: gui.py imports route_tab, which imports this module, so a top-level import is a cycle.
            from src.gui.gui import CHARACTER_ICON_PATH, CHARACTER_MASK_PATH
            built = CharacterLocator.from_files(CHARACTER_ICON_PATH, CHARACTER_MASK_PATH)
            self._locator = built.unwrap() if is_successful(built) else None
        if self._locator is None:
            return None
        found = self._locator.locate(frame)
        return found.unwrap() if is_successful(found) else None
