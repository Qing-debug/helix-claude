"""
In-process map editor for placing/editing/ordering nodes against the live
minimap, producing CSVs the agent can load via Controller.loadMap.

The editor is a tk.Toplevel owned by MapleGUI. It subscribes to the same
SharedInfo.minimap_frame stream as the main GUI; each window owns its own
canvas and _updateCanvas loop.
"""

import copy
import tkinter as tk
import tkinter.filedialog as filedialog
import tkinter.font as tkfont
import tkinter.messagebox as messagebox
import tkinter.ttk as ttk
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import numpy as np
from PIL import Image, ImageTk
from src.controller.map_csv import EditorNode, read_editor_nodes, write_editor_nodes
from src.controller.map_format import ordered_local_ids
from src.controller.node import Node
from src.controller.rope_file import RopeFile, RopeMark, free_x, near_rope_end, read_ropes, rope_file_for, write_ropes
from src.controller.rope_finder import SAME_ROPE_PX, find_ropes, median_frame
from returns.pipeline import is_successful


RENDER_SCALE = 2  # minimap canvas + node coords rendered at this multiplier of native px

SELECT_RADIUS_PX = 3           # native coords; 6 px screen hit radius at RENDER_SCALE=2
NODE_DRAW_RADIUS_PX = 3        # native coords; 6 px screen radius / 12 px diameter at RENDER_SCALE=2
SELECTED_RING_RADIUS_PX = 1    # native coords; 2 px screen radius / 4 px diameter

FONT_BODY_SIZE = 10            # Tk default is 9pt; one above for slight legibility bump without crowding
FONT_HEADER_SIZE = 16
FONT_NODE_LABEL_SIZE = 7       # local_id text drawn next to blue-node dots on the minimap (not zoom-aware). Sized to roughly match the 12px on-screen dot diameter.
BUTTON_PADX = 12
BUTTON_PADY = 6
PANEL_PADX = 12
PANEL_PADY = 12
COLUMN_MIN_WIDTH_PX = 240      # fits the longest checkbox label ("fall_dist_exceeds_vertical_tp") at body size
SECTION_HEADER_FG = '#1a2b4a'  # dark navy; section-label hierarchy on the lightblue panel
NODE_LABEL_FG = 'white'        # local_id text color over the (typically dark) minimap

WINDOW_INITIAL_WIDTH = 800     # sized so the 3-column panel fits without clipping the rotation column
WINDOW_INITIAL_HEIGHT = 800

ZOOM_MIN = 0.5
ZOOM_MAX = 3.0
ZOOM_STEP = 0.1

CANVAS_TAG_NODE = "editor_node"
CANVAS_TAG_SELECTED = "editor_selected"
CANVAS_TAG_EDGE = "editor_edge"
CANVAS_TAG_BAND = "editor_band"
CANVAS_TAG_ORPHAN = "editor_orphan"
CANVAS_TAG_ROPE = "editor_rope"
CANVAS_TAG_KEEPOUT = "rope_keepout"

ROPE_LINE_WIDTH = 1            # screen px; a mark is a yellow line from (x, top_y) to (x, bottom_y)
ROPE_SELECTED_LINE_WIDTH = 3
KEEPOUT_RING_EXTRA_PX = 3      # screen px added to the node dot's radius for the red keep-out ring

# Hong's wording (QB236, 2026-11-11); the question a save asks when the map's rope file is damaged and no rope is marked.
ROPE_FILE_DAMAGED_QUESTION = ("This map's rope file is damaged and no ropes are marked. "
                              "Save with no ropes and remove the rope file?")
ROPE_FILE_NOT_LOADED_TEXT = ("The map's nodes were loaded without its ropes. "
                             "Mark the ropes again and save to replace the file.")

# The Ropes panel (H92, plan Task 3b, D3).
ROPE_SCAN_FRAMES = 30          # Find ropes collects this many frames ...
ROPE_SCAN_EVERY_MS = 100       # ... one every this many ms (3 s in all)
ROPE_SELECT_PX = 2             # native px; a click this close to a rope's line selects the rope
ROPE_BOX_MAX = 999             # the number boxes' arrow limit (typed values are checked, not clamped)
FIND_ROPES_TEXT = "Finding ropes: keep the character moving for 3 seconds..."
ADD_ROPE_TOP_TEXT = "Add rope: click the rope's top end, where a node on the platform above would sit."
ADD_ROPE_BOTTOM_TEXT = "Now click its bottom end, where a node on the platform below would sit."
MINIMAP_NOT_FOUND_TEXT = "Minimap not found yet"
ROPE_BOX_LABELS = {"x": "x", "top_y": "top y", "bottom_y": "bottom y"}


@dataclass
class MapEditorState:
    """Holds the editor's mutable state, isolated from the runtime PathGraph."""
    nodes: Dict[int, EditorNode] = field(default_factory=dict)
    rotation_order: List[int] = field(default_factory=list)  # blue_node local_ids
    selected_id: Optional[int] = None
    next_local_id: int = 1                                   # monotonic
    placement_nodetype: str = "orange_node"                  # default for new placements
    current_file: Optional[str] = None
    dirty: bool = False                                      # any unsaved mutation since last save/load
    # Ropes (H91, H94): marks in map coordinates. After any change to `ropes` or `selected_rope`, call
    # `_recomputeDirty()` then `_renderNodes()`; the dirty flag, the red rings and the Save block follow from `ropes`.
    ropes: List[RopeMark] = field(default_factory=list)
    selected_rope: Optional[int] = None                      # index into `ropes`, drawn thicker
    rope_extra: Dict[str, object] = field(default_factory=dict)  # the loaded rope file's unknown keys; a save keeps them
    # A rope file was read at load without error, or this editor has written one since. A map with no rope file beside
    # it leaves this false, so saving a map without ropes writes no rope file.
    rope_file_read: bool = False
    # "Add rope" (Task 3b): `add_rope_armed` is set by the button and cleared by the second click or a load;
    # `adding_rope` is the first click (the top end), None until it is made. `Optional[Tuple[int, int]]` has no value
    # for "armed, no click yet", hence the second field.
    adding_rope: Optional[Tuple[int, int]] = None
    add_rope_armed: bool = False


class MapEditor:
    def __init__(self, parent_tk, controller, sharedInfo):
        """
        parent_tk:   the main GUI's tk.Tk instance (used as Toplevel parent).
        controller:  shared Controller; the editor uses controller.agent (buildPathGraphFor, characterClass, the edge
                     tolerance levels). It never looks for the character itself.
        sharedInfo:  shared minimap-frame buffer + lock.
        """
        self.controller = controller
        self.sharedInfo = sharedInfo
        self.state = MapEditorState()

        # Saved baseline for state-comparison dirty tracking. Deep-copied on save/load so
        # in-place EditorNode flag toggles can't accidentally mutate the baseline. Initial
        # empty values match the empty initial state, so place-then-delete returns to clean
        # without needing an explicit __init__ snapshot.
        self._saved_nodes: Dict[int, EditorNode] = {}
        self._saved_rotation: List[int] = []
        self._saved_ropes: List[RopeMark] = []

        # Find ropes (Task 3b): the frames collected so far, None while no collection runs; the ticks done.
        self._rope_scan_frames: Optional[List[np.ndarray]] = None
        self._rope_scan_ticks = 0

        self.window = tk.Toplevel(parent_tk)
        self.window.title("Map Editor — untitled")
        self.window.geometry(f"{WINDOW_INITIAL_WIDTH}x{WINDOW_INITIAL_HEIGHT}")

        # Per-window zoom state and fonts. Per-instance tkfont.Font objects (not the global
        # named system fonts) so resizing them only affects widgets in this editor window.
        # Bindings live on self.window (not bind_all), so main GUI keystrokes don't trigger
        # editor zoom and vice versa.
        self.zoom_factor = 1.0
        self._base_font_sizes = {
            "body": FONT_BODY_SIZE,
            "header": FONT_HEADER_SIZE,
            "section": FONT_BODY_SIZE,
        }
        default_family = tkfont.nametofont("TkDefaultFont").cget("family")
        self._font_body = tkfont.Font(family=default_family, size=FONT_BODY_SIZE)
        self._font_header = tkfont.Font(family=default_family, size=FONT_HEADER_SIZE, weight="bold")
        self._font_section = tkfont.Font(family=default_family, size=FONT_BODY_SIZE, weight="bold")
        # Canvas-space label for blue-node local_ids; intentionally NOT in _base_font_sizes so it
        # doesn't scale with editor zoom (the minimap canvas itself is fixed-size, so labels are too).
        self._font_node_label = tkfont.Font(family=default_family, size=FONT_NODE_LABEL_SIZE, weight="bold")

        self.minimap_canvas = None
        self.minimap_canvas_bg_img_id = None
        self.minimap_canvas_width = None
        self.minimap_canvas_height = None

        self._buildLayout()
        self._bindZoomShortcuts()
        self._waitForMinimapAndBuildCanvas()
        self._updateCanvas()

    def _buildLayout(self):
        """Top frame holds the centered minimap canvas. Bottom frame holds a vertically-scrollable
        three-column controls panel (Placement defaults | Inspector | Rotation order).

        Wheel handling binds to self.window (NOT bind_all) so it stays scoped to this Toplevel;
        the main GUI keeps its own wheel handler unaffected.
        """
        self._canvas_frame = tk.Frame(self.window, bg='gray20')
        self._canvas_frame.pack(side=tk.TOP, fill=tk.X)

        # Status line below the canvas: why a click placed nothing (and, in the Ropes panel, what a rope tool did).
        # Two lines high from the start so a long text does not move the panel.
        self._status_label = tk.Label(
            self.window, text="", anchor='nw', justify=tk.LEFT, height=2,
            wraplength=WINDOW_INITIAL_WIDTH - 2 * PANEL_PADX, bg='lightblue', font=self._font_body,
        )
        self._status_label.pack(side=tk.TOP, fill=tk.X, padx=PANEL_PADX)

        panel_outer = tk.Frame(self.window, bg='lightblue')
        panel_outer.pack(side=tk.BOTTOM, fill=tk.BOTH, expand=True)

        self._panel_canvas = tk.Canvas(
            panel_outer, bg='lightblue', highlightthickness=0, borderwidth=0,
        )
        panel_scrollbar = tk.Scrollbar(
            panel_outer, orient='vertical', command=self._panel_canvas.yview,
        )
        self._panel_canvas.configure(yscrollcommand=panel_scrollbar.set)
        self._panel_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        panel_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self._panel_frame = tk.Frame(self._panel_canvas, bg='lightblue')
        panel_window_id = self._panel_canvas.create_window(
            (0, 0), window=self._panel_frame, anchor='nw',
        )
        self._panel_frame.bind(
            '<Configure>',
            lambda e: self._panel_canvas.configure(scrollregion=self._panel_canvas.bbox('all')),
        )
        self._panel_canvas.bind(
            '<Configure>',
            lambda e: self._panel_canvas.itemconfig(panel_window_id, width=e.width),
        )

        def _on_panel_wheel(event):
            widget = event.widget
            while widget is not None and widget is not self.window:
                if widget is panel_outer:
                    self._panel_canvas.yview_scroll(int(-1 * (event.delta / 120)), 'units')
                    return 'break'
                widget = widget.master
        self.window.bind('<MouseWheel>', _on_panel_wheel)

        tk.Label(
            self._panel_frame, text="Map Editor", bg='lightblue', font=self._font_header,
        ).grid(row=0, column=0, columnspan=3, pady=(PANEL_PADY, 8), sticky='ew')

        # Three equal-width column bays. minsize keeps the longest checkbox label legible even
        # if the user shrinks the window narrower than the natural 3-column width.
        for c in range(3):
            self._panel_frame.grid_columnconfigure(
                c, weight=1, uniform='cols', minsize=COLUMN_MIN_WIDTH_PX,
            )

        placement_col = tk.Frame(self._panel_frame, bg='lightblue')
        placement_col.grid(row=1, column=0, padx=PANEL_PADX, pady=4, sticky='nsew')
        inspector_col = tk.Frame(self._panel_frame, bg='lightblue')
        inspector_col.grid(row=1, column=1, padx=PANEL_PADX, pady=4, sticky='nsew')
        rotation_col = tk.Frame(self._panel_frame, bg='lightblue')
        rotation_col.grid(row=1, column=2, padx=PANEL_PADX, pady=4, sticky='nsew')

        # Sticky flag defaults baked into newly-placed nodes. Independent from the inspector's
        # flag checkboxes (which edit the selected node). Lets the user batch-place a run of
        # nodes that all share a flag config without ticking it per-node.
        tk.Label(
            placement_col, text="Placement defaults",
            bg='lightblue', fg=SECTION_HEADER_FG, font=self._font_section,
        ).pack(anchor='w', pady=(0, 4))

        self._placement_var = tk.StringVar(value="orange_node")
        for label, value in (("blue_node", "blue_node"), ("orange_node", "orange_node")):
            tk.Radiobutton(
                placement_col, text=label, variable=self._placement_var, value=value,
                bg='lightblue', font=self._font_body, command=self._onPlacementChange,
            ).pack(anchor='w', padx=PANEL_PADX)

        self._placement_flag_vars = {
            "can_fall": tk.BooleanVar(),
            "fall_dist_exceeds_vertical_tp": tk.BooleanVar(),
            "is_left_platform_edge": tk.BooleanVar(),
            "is_right_platform_edge": tk.BooleanVar(),
        }
        for field_name, var in self._placement_flag_vars.items():
            tk.Checkbutton(
                placement_col, text=field_name, variable=var,
                bg='lightblue', font=self._font_body,
            ).pack(anchor='w', padx=PANEL_PADX)

        tk.Label(
            inspector_col, text="Inspector",
            bg='lightblue', fg=SECTION_HEADER_FG, font=self._font_section,
        ).pack(anchor='w', pady=(0, 4))

        self._inspector_pos_label = tk.Label(
            inspector_col, text="pos: —", bg='lightblue', font=self._font_body,
        )
        self._inspector_pos_label.pack(anchor='w')

        self._inspector_nodetype_var = tk.StringVar(value="orange_node")
        nodetype_row = tk.Frame(inspector_col, bg='lightblue')
        nodetype_row.pack(anchor='w')
        for label, value in (("blue", "blue_node"), ("orange", "orange_node")):
            tk.Radiobutton(
                nodetype_row, text=label, variable=self._inspector_nodetype_var, value=value,
                bg='lightblue', font=self._font_body, command=self._onInspectorNodetypeChange,
            ).pack(side=tk.LEFT)

        self._inspector_flag_vars = {
            "can_fall": tk.BooleanVar(),
            "fall_dist_exceeds_vertical_tp": tk.BooleanVar(),
            "is_left_platform_edge": tk.BooleanVar(),
            "is_right_platform_edge": tk.BooleanVar(),
        }
        for field_name, var in self._inspector_flag_vars.items():
            tk.Checkbutton(
                inspector_col, text=field_name, variable=var,
                bg='lightblue', font=self._font_body,
                command=lambda f=field_name: self._onInspectorFlagChange(f),
            ).pack(anchor='w')

        tk.Button(
            inspector_col, text="Delete", font=self._font_body,
            padx=BUTTON_PADX, pady=BUTTON_PADY, command=self._deleteSelected,
        ).pack(anchor='w', pady=(4, 0))

        tk.Label(
            rotation_col, text="Rotation order",
            bg='lightblue', fg=SECTION_HEADER_FG, font=self._font_section,
        ).pack(anchor='w', pady=(0, 4))
        self._rotation_frame = tk.Frame(rotation_col, bg='lightblue')
        self._rotation_frame.pack(fill=tk.X)

        self._show_edges_var = tk.BooleanVar(value=False)
        self._show_edges_cb = tk.Checkbutton(
            self._panel_frame,
            text="Show edges",
            variable=self._show_edges_var,
            bg='lightblue', font=self._font_body,
            command=self._renderPreview,
        )
        self._show_edges_cb.grid(row=2, column=0, columnspan=3, sticky='w', padx=PANEL_PADX, pady=(12, 0))
        self._evaluateShowEdgesState()  # gate the checkbox on whether a character class is selected

        self._show_bands_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            self._panel_frame,
            text="Show bands",
            variable=self._show_bands_var,
            bg='lightblue', font=self._font_body,
            command=self._renderPreview,
        ).grid(row=3, column=0, columnspan=3, sticky='w', padx=PANEL_PADX)

        # Orphan highlight is a separate render path (not a "preview" overlay) — it draws
        # over the dots, not behind them, so toggling re-runs _renderNodes (which sequences
        # preview → dots → orphan X → selected ring).
        self._show_orphans_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            self._panel_frame,
            text="Highlight orphans",
            variable=self._show_orphans_var,
            bg='lightblue', font=self._font_body,
            command=self._renderNodes,
        ).grid(row=4, column=0, columnspan=3, sticky='w', padx=PANEL_PADX)

        # Ropes section (H92, D3): Find ropes, Add rope, and for the selected rope its three number boxes and Delete rope.
        # Its own child frame, like the File section below.
        ropes_section = tk.Frame(self._panel_frame, bg='lightblue')
        ropes_section.grid(row=5, column=0, columnspan=3, sticky='ew', padx=PANEL_PADX, pady=(12, 0))
        tk.Label(
            ropes_section, text="Ropes",
            bg='lightblue', fg=SECTION_HEADER_FG, font=self._font_section,
        ).pack(anchor='w', pady=(0, 4))
        rope_buttons = tk.Frame(ropes_section, bg='lightblue')
        rope_buttons.pack(anchor='w')
        tk.Button(
            rope_buttons, text="Find ropes", font=self._font_body, command=self._findRopes,
        ).pack(side=tk.LEFT, padx=(0, 8), pady=2)
        tk.Button(
            rope_buttons, text="Add rope", font=self._font_body, command=self._startAddRope,
        ).pack(side=tk.LEFT, pady=2)
        rope_boxes = tk.Frame(ropes_section, bg='lightblue')
        rope_boxes.pack(anchor='w', pady=(4, 0))
        self._rope_vars = {name: tk.StringVar() for name in ROPE_BOX_LABELS}
        self._rope_boxes = {}
        for name, label in ROPE_BOX_LABELS.items():
            tk.Label(rope_boxes, text=label, bg='lightblue', font=self._font_body).pack(side=tk.LEFT, padx=(8, 2))
            box = tk.Spinbox(
                rope_boxes, from_=0, to=ROPE_BOX_MAX, width=5, font=self._font_body,
                textvariable=self._rope_vars[name], command=lambda n=name: self._applyRopeBox(n),
            )
            box.bind('<Return>', lambda e, n=name: self._applyRopeBox(n))
            box.bind('<FocusOut>', lambda e, n=name: self._applyRopeBox(n))
            box.pack(side=tk.LEFT)
            self._rope_boxes[name] = box
        self._delete_rope_button = tk.Button(
            rope_boxes, text="Delete rope", font=self._font_body, command=self._deleteSelectedRope,
        )
        self._delete_rope_button.pack(side=tk.LEFT, padx=(12, 0))
        self._refreshRopeBoxes()  # no rope selected yet: the boxes are empty and disabled

        # File section is its own child frame so the buttons can pack(fill=tk.X) inside
        # without mixing geometry managers with the panel grid above.
        file_section = tk.Frame(self._panel_frame, bg='lightblue')
        file_section.grid(row=6, column=0, columnspan=3, sticky='ew', padx=PANEL_PADX, pady=(12, 0))
        tk.Label(
            file_section, text="File",
            bg='lightblue', fg=SECTION_HEADER_FG, font=self._font_section,
        ).pack(anchor='w', pady=(0, 4))
        tk.Button(
            file_section, text="Save", font=self._font_body, command=self._save,
        ).pack(fill=tk.X, pady=2)
        tk.Button(
            file_section, text="Save As…", font=self._font_body, command=self._saveAs,
        ).pack(fill=tk.X, pady=2)
        tk.Button(
            file_section, text="Load CSV…", font=self._font_body, command=self._load,
        ).pack(fill=tk.X, pady=2)

    def _waitForMinimapAndBuildCanvas(self):
        """
        Poll sharedInfo for a non-None minimap frame, then build the editor's
        own Canvas at minimap's native dimensions. Mirrors MapleGUI's pattern.

        Uses tk.after polling rather than time.sleep so the Tk event loop
        stays responsive while we wait.
        """
        with self.sharedInfo.minimap_frame_lock:
            frame = self.sharedInfo.minimap_frame
        if frame is None:
            self.window.after(200, self._waitForMinimapAndBuildCanvas)
            return
        h, w, _ = frame.shape
        self.minimap_canvas_height = h * RENDER_SCALE
        self.minimap_canvas_width = w * RENDER_SCALE
        self.minimap_canvas = tk.Canvas(
            self._canvas_frame,
            width=self.minimap_canvas_width,
            height=self.minimap_canvas_height,
            background='pink',
            highlightthickness=0,
            borderwidth=0,
            relief='flat',
        )
        self.minimap_canvas.pack(padx=8, pady=8)  # default side=TOP centers horizontally in the frame
        self.minimap_canvas.bind("<Button-1>", self._onCanvasClick)
        self.window.bind("<Delete>", self._onDeleteKey)
        self._refreshInspector()  # start with empty inspector since no node is selected yet
        self._refreshRotation()

    def _onPlacementChange(self):
        self.state.placement_nodetype = self._placement_var.get()

    def _onInspectorNodetypeChange(self):
        sid = self.state.selected_id
        if sid is None or sid not in self.state.nodes:
            return
        new_type = self._inspector_nodetype_var.get()
        node = self.state.nodes[sid]
        if node.nodetype == new_type:
            return
        old_type = node.nodetype
        node.nodetype = new_type
        if old_type == "blue_node" and sid in self.state.rotation_order:
            self.state.rotation_order.remove(sid)
        if new_type == "blue_node" and sid not in self.state.rotation_order:
            self.state.rotation_order.append(sid)
        self._recomputeDirty()
        self._refreshInspector()
        self._renderNodes()
        self._refreshRotation()

    def _onInspectorFlagChange(self, field_name):
        sid = self.state.selected_id
        if sid is None or sid not in self.state.nodes:
            return
        node = self.state.nodes[sid]
        setattr(node, field_name, bool(self._inspector_flag_vars[field_name].get()))
        self._recomputeDirty()
        self._renderNodes()  # is_left/right_platform_edge affect vertical edge validity

    def _refreshInspector(self):
        """Sync inspector widgets to the currently selected node."""
        sid = self.state.selected_id
        if sid is None or sid not in self.state.nodes:
            self._inspector_pos_label.config(text="pos: —")
            self._inspector_nodetype_var.set("orange_node")
            for var in self._inspector_flag_vars.values():
                var.set(False)
            return
        node = self.state.nodes[sid]
        self._inspector_pos_label.config(text=f"pos: {node.pos}")
        self._inspector_nodetype_var.set(node.nodetype)
        for field_name, var in self._inspector_flag_vars.items():
            var.set(getattr(node, field_name))

    def _refreshRotation(self):
        """Rebuild the rotation order list widgets from current state."""
        for child in self._rotation_frame.winfo_children():
            child.destroy()
        for idx, local_id in enumerate(self.state.rotation_order):
            node = self.state.nodes[local_id]
            row = tk.Frame(self._rotation_frame, bg='lightblue')
            row.pack(fill=tk.X)
            tk.Label(
                row,
                text=f"{idx + 1}. #{local_id} {node.pos}",
                bg='lightblue',
                font=self._font_body,
            ).pack(side=tk.LEFT)
            tk.Button(
                row, text="↑", width=2,
                font=self._font_body,
                command=lambda i=idx: self._moveRotation(i, -1),
            ).pack(side=tk.RIGHT)
            tk.Button(
                row, text="↓", width=2,
                font=self._font_body,
                command=lambda i=idx: self._moveRotation(i, +1),
            ).pack(side=tk.RIGHT)

    def _moveRotation(self, idx, delta):
        new_idx = idx + delta
        if new_idx < 0 or new_idx >= len(self.state.rotation_order):
            return
        order = self.state.rotation_order
        order[idx], order[new_idx] = order[new_idx], order[idx]
        self._recomputeDirty()
        self._refreshRotation()

    def _applyZoom(self):
        """Resize this editor's Font objects in place. Widgets referencing them auto-update."""
        for key, font_obj in (
            ("body", self._font_body),
            ("header", self._font_header),
            ("section", self._font_section),
        ):
            base = self._base_font_sizes[key]
            font_obj.configure(size=max(1, int(round(base * self.zoom_factor))))

    def _zoomIn(self, event=None):
        self.zoom_factor = min(ZOOM_MAX, self.zoom_factor + ZOOM_STEP)
        self._applyZoom()
        return 'break'

    def _zoomOut(self, event=None):
        self.zoom_factor = max(ZOOM_MIN, self.zoom_factor - ZOOM_STEP)
        self._applyZoom()
        return 'break'

    def _zoomReset(self, event=None):
        self.zoom_factor = 1.0
        self._applyZoom()
        return 'break'

    def _onCtrlMouseWheel(self, event):
        if event.delta > 0:
            self._zoomIn()
        else:
            self._zoomOut()
        return 'break'

    def _bindZoomShortcuts(self):
        """Scope zoom to this editor's Toplevel only — main GUI and other Toplevels unaffected."""
        self.window.bind('<Control-plus>', self._zoomIn)
        self.window.bind('<Control-equal>', self._zoomIn)
        self.window.bind('<Control-KP_Add>', self._zoomIn)
        self.window.bind('<Control-minus>', self._zoomOut)
        self.window.bind('<Control-KP_Subtract>', self._zoomOut)
        self.window.bind('<Control-0>', self._zoomReset)
        self.window.bind('<Control-MouseWheel>', self._onCtrlMouseWheel)

    def _setStatus(self, text: str) -> None:
        """Write the status line below the canvas."""
        self._status_label.config(text=text)

    def _onCanvasClick(self, event):
        """Left-click on canvas. With "Add rope" armed the click is a rope end (`_addRopeClick`). Otherwise: select an
        existing node within SELECT_RADIUS_PX; else select a rope whose line is within ROPE_SELECT_PX (`_selectRopeAt`,
        D3: node first, then rope); else place a new node, unless that spot is inside a rope's keep-out (H91): then
        nothing is placed and the status says why.

        event.x/event.y are in canvas pixels (post-scale); divide by RENDER_SCALE to get
        native minimap coords for storage and hit-testing.
        """
        native_x = event.x // RENDER_SCALE
        native_y = event.y // RENDER_SCALE
        # A click on the map gives the canvas the keyboard, so a Delete after it reaches `_onDeleteKey` and not a number
        # box that still holds the focus (ROPE150RR R-I2). Tk's focus record inside this window only.
        if self.minimap_canvas is not None:
            self.minimap_canvas.focus_set()
        if self.state.add_rope_armed:
            self._addRopeClick(native_x, native_y)
            return
        hit_id = self._findNodeAt(native_x, native_y)
        if hit_id is not None:
            self.state.selected_id = hit_id
            self.state.selected_rope = None  # a node and a rope are never selected together
            self._setStatus("")
            self._recomputeDirty()
        elif self._selectRopeAt(native_x, native_y):
            return
        else:
            rope = near_rope_end((native_x, native_y), self.state.ropes)
            if rope is not None:
                left, right = free_x(rope)
                self._setStatus(
                    f"Not placed: ({native_x}, {native_y}) is too close to the rope at x {rope.x}, "
                    f"where it meets a platform. Place it at x {left} or less, or {right} or more."
                )
                return
            self._setStatus("")
            self._placeNode(native_x, native_y)
            self._recomputeDirty()
            self._refreshRotation()
        self._refreshInspector()
        self._refreshRopeBoxes()
        self._renderNodes()

    def _findNodeAt(self, x, y):
        """Return local_id of the first node within SELECT_RADIUS_PX of (x, y), or None."""
        for node in self.state.nodes.values():
            nx, ny = node.pos
            if (nx - x) ** 2 + (ny - y) ** 2 <= SELECT_RADIUS_PX ** 2:
                return node.local_id
        return None

    def _placeNode(self, x, y):
        """Create a new EditorNode at (x, y) using the current placement nodetype + sticky flag defaults. Auto-selects it."""
        local_id = self.state.next_local_id
        self.state.next_local_id += 1
        flag_kwargs = {name: bool(var.get()) for name, var in self._placement_flag_vars.items()}
        node = EditorNode(
            local_id=local_id,
            nodetype=self.state.placement_nodetype,
            pos=(int(x), int(y)),
            **flag_kwargs,
        )
        self.state.nodes[local_id] = node
        if node.nodetype == "blue_node":
            self.state.rotation_order.append(local_id)
        self.state.selected_id = local_id
        self.state.selected_rope = None  # a node and a rope are never selected together

    def _deleteSelected(self):
        sid = self.state.selected_id
        if sid is None or sid not in self.state.nodes:
            return
        del self.state.nodes[sid]
        if sid in self.state.rotation_order:
            self.state.rotation_order.remove(sid)
        self.state.selected_id = None
        self._recomputeDirty()
        self._refreshInspector()
        self._renderNodes()
        self._refreshRotation()

    # -- the Ropes panel (H92, plan Task 3b). Every change to `state.ropes` or `state.selected_rope` below ends with the
    # contract's one rule: `_recomputeDirty()`, then `_renderNodes()`. Status texts go through `_setStatus` only.

    def _onDeleteKey(self, event=None):
        """The <Delete> key (Hong, QB235): with a rope selected it deletes that rope, as the panel's "Delete rope" does;
        otherwise `_deleteSelected` does what it always did. The binding is on the window, so a Delete typed in a number
        box (an Entry or Spinbox, which edits its own text first) reaches it too: it does nothing then (R-I2)."""
        if isinstance(getattr(event, "widget", None), (tk.Entry, tk.Spinbox, ttk.Entry)):
            return
        if self.state.selected_rope is not None:
            self._deleteSelectedRope()
        else:
            self._deleteSelected()

    def _deleteSelectedRope(self):
        index = self.state.selected_rope
        if index is None or not 0 <= index < len(self.state.ropes):
            return
        del self.state.ropes[index]
        self.state.selected_rope = None
        self._setStatus("")
        self._recomputeDirty()
        self._refreshRopeBoxes()
        self._renderNodes()

    def _selectRopeAt(self, x, y) -> bool:
        """Select the rope whose line is within ROPE_SELECT_PX of native (x, y), the nearest column first, and clear
        the node selection. Returns False, changing nothing, when no rope is that close. Called only when no node is
        hit (D3: node first, then rope, so a node next to a rope stays selectable)."""
        best = None
        for index, mark in enumerate(self.state.ropes):
            dx = abs(x - mark.x)
            if dx <= ROPE_SELECT_PX and mark.top_y - ROPE_SELECT_PX <= y <= mark.bottom_y + ROPE_SELECT_PX:
                if best is None or dx < best[0]:
                    best = (dx, index)
        if best is None:
            return False
        self.state.selected_rope = best[1]
        self.state.selected_id = None
        self._setStatus("")
        self._recomputeDirty()
        self._refreshInspector()
        self._refreshRopeBoxes()
        self._renderNodes()
        return True

    def _refreshRopeBoxes(self):
        """Sync the number boxes and Delete rope to the selected rope: its values, enabled; with none, empty, disabled."""
        index = self.state.selected_rope
        mark = self.state.ropes[index] if index is not None and 0 <= index < len(self.state.ropes) else None
        for name, var in self._rope_vars.items():
            var.set("" if mark is None else str(getattr(mark, name)))
        widget_state = tk.DISABLED if mark is None else tk.NORMAL
        for box in self._rope_boxes.values():
            box.config(state=widget_state)
        self._delete_rope_button.config(state=widget_state)

    def _applyRopeBox(self, field_name):
        """A number box was committed (Return, leaving the box or its arrows): move the selected rope. A value that is
        not a whole number of 0 or more, or that would leave top y at or below bottom y, is refused with a status text and
        the boxes go back to the rope's values (the rope file's reader would refuse such a rope)."""
        index = self.state.selected_rope
        if index is None or not 0 <= index < len(self.state.ropes):
            return
        mark = self.state.ropes[index]
        label = ROPE_BOX_LABELS[field_name]
        text = self._rope_vars[field_name].get().strip()
        if not (text.isascii() and text.isdigit()):
            self._setStatus(f"Not changed: {label} must be a whole number, 0 or more.")
            self._refreshRopeBoxes()
            return
        value = int(text)
        if value == getattr(mark, field_name):
            self._refreshRopeBoxes()  # nothing to change; tidies the text ("0107" -> "107")
            return
        moved = replace(mark, **{field_name: value})
        if moved.top_y >= moved.bottom_y:
            self._setStatus(
                f"Not changed: top y ({moved.top_y}) must be less than bottom y ({moved.bottom_y})."
            )
            self._refreshRopeBoxes()
            return
        self.state.ropes[index] = moved
        self._setStatus("")
        self._recomputeDirty()
        self._refreshRopeBoxes()
        self._renderNodes()

    def _startAddRope(self):
        """The Add rope button: the next two canvas clicks are the rope's top end and bottom end (D3)."""
        if self.minimap_canvas is None:
            self._setStatus(MINIMAP_NOT_FOUND_TEXT)
            return
        self.state.add_rope_armed = True
        self.state.adding_rope = None
        self._setStatus(ADD_ROPE_TOP_TEXT)

    def _addRopeClick(self, x, y):
        """A canvas click while "Add rope" is armed, in native minimap coordinates as a node click gives them (QB234).
        The first click stores the top end; the second sets the bottom end and adds the rope, x from the first click.
        Two clicks at one height add nothing and keep the first."""
        first = self.state.adding_rope
        if first is None:
            self.state.adding_rope = (x, y)
            self._setStatus(ADD_ROPE_BOTTOM_TEXT)
            return
        x1, y1 = first
        if y == y1:
            self._setStatus(f"Not added: both ends are at y {y}. Click the bottom end at a different height.")
            return
        mark = RopeMark(x1, min(y1, y), max(y1, y))
        self.state.adding_rope = None
        self.state.add_rope_armed = False
        self.state.ropes.append(mark)
        self.state.selected_rope = len(self.state.ropes) - 1  # selected, so the boxes can correct the clicks
        self.state.selected_id = None
        self._setStatus(f"Added the rope at x {mark.x}, from y {mark.top_y} to y {mark.bottom_y}.")
        self._recomputeDirty()
        self._refreshInspector()
        self._refreshRopeBoxes()
        self._renderNodes()

    def _findRopes(self):
        """The Find ropes button: collect ROPE_SCAN_FRAMES minimap frames, one every ROPE_SCAN_EVERY_MS through the
        window's `after`, then find the ropes in their per-pixel median (`_onRopeScanTick`). Pressed while collecting,
        it does nothing; before the minimap canvas exists it only says so."""
        if self.minimap_canvas is None:
            self._setStatus(MINIMAP_NOT_FOUND_TEXT)
            return
        if self._rope_scan_frames is not None:
            return
        self._rope_scan_frames = []
        self._rope_scan_ticks = 0
        self._setStatus(FIND_ROPES_TEXT)
        self.window.after(ROPE_SCAN_EVERY_MS, self._onRopeScanTick)

    def _onRopeScanTick(self):
        """One step of the collection. The frame is copied under `minimap_frame_lock` into a local and the lock is
        released before any Tk call (the vision thread writes it; nothing here may hold it across Tk or a sleep)."""
        frames = self._rope_scan_frames
        if frames is None or not self.window.winfo_exists():
            return
        with self.sharedInfo.minimap_frame_lock:
            frame = self.sharedInfo.minimap_frame
            copied = None if frame is None else frame.copy()
        if copied is not None:
            frames.append(copied)
        self._rope_scan_ticks += 1
        if self._rope_scan_ticks < ROPE_SCAN_FRAMES:
            self.window.after(ROPE_SCAN_EVERY_MS, self._onRopeScanTick)
            return
        self._rope_scan_frames = None
        self._finishRopeScan(frames)

    def _finishRopeScan(self, frames):
        median = median_frame(frames)
        if not is_successful(median):
            self._setStatus(median.failure())
            return
        found = find_ropes(median.unwrap())
        marked = list(self.state.ropes)  # a found rope is skipped only when an earlier mark is that close, not one found now
        added = [rope for rope in found if all(abs(rope.x - mark.x) > SAME_ROPE_PX for mark in marked)]
        self.state.ropes.extend(added)
        if len(found) == 0:
            self._setStatus("Found no rope")
        else:
            self._setStatus(f"Found {len(found)} rope" + ("" if len(found) == 1 else "s"))
        self._recomputeDirty()
        self._renderNodes()

    def _recomputeDirty(self):
        """Compare current state against the saved baseline; flip dirty + refresh title only if it changed.

        Replaces the one-way _markDirty flag. Place-then-delete, flag toggle-untoggle, and reorder-back
        correctly return the editor to clean because state matches baseline. Uses dataclass equality
        on EditorNode (auto-generated by @dataclass) and list equality on rotation_order.
        """
        was_dirty = self.state.dirty
        self.state.dirty = (
            self.state.nodes != self._saved_nodes
            or self.state.rotation_order != self._saved_rotation
            or self.state.ropes != self._saved_ropes
        )
        if self.state.dirty != was_dirty:
            self._refreshTitle()

    def _refreshTitle(self):
        """Composes the title bar from dirty marker + current file name (basename only)."""
        prefix = "* " if self.state.dirty else ""
        name = Path(self.state.current_file).name if self.state.current_file else "untitled"
        self.window.title(f"{prefix}{name} — Map Editor")

    def _save(self):
        """Save to current_file; falls through to Save As when none is set.

        Pre-save validation runs BEFORE any file dialog so the user isn't asked to pick
        a filename for a save that will be rejected.
        """
        if not self._okToSave():
            return
        if self.state.current_file is None:
            self._promptAndWrite()
        else:
            self._writeTo(self.state.current_file)

    def _saveAs(self):
        """Always prompts for a path (useful for renaming the file under edit).

        Pre-save validation runs BEFORE the file dialog opens.
        """
        if not self._okToSave():
            return
        self._promptAndWrite()

    def _okToSave(self) -> bool:
        """Run validation and surface block/warn modals. Returns True iff save should proceed."""
        verdict, message = self._validateForSave()
        if verdict == "block":
            messagebox.showerror("Cannot save", message, parent=self.window)
            return False
        if verdict == "warn":
            proceed = messagebox.askokcancel(
                "Validation warnings", message, parent=self.window,
            )
            if not proceed:
                return False
        return True

    def _promptAndWrite(self):
        """Open Save As dialog and write to the chosen path. Assumes _okToSave already passed."""
        path = filedialog.asksaveasfilename(
            parent=self.window,
            title="Save Map As",
            initialdir="maps",
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv")],
        )
        if not path:
            return
        self._writeTo(path)

    def _validateForSave(self):
        """Pre-save check. Returns one of:
          ("ok", "")        — proceed silently
          ("warn", message) — show askokcancel; OK proceeds, Cancel aborts
          ("block", message) — show showerror; abort save unconditionally

        Hard block: rotation_order empty (no blue_nodes — agent has nothing to rotate to).
        Soft warnings: duplicate positions, off-minimap positions, orphan nodes.
        """
        # rotation_order is kept in lockstep with blue_node membership by _placeNode,
        # _deleteSelected, _onInspectorNodetypeChange, and CSV load; empty ⇔ no blue_nodes.
        if not self.state.rotation_order:
            return ("block",
                    "Cannot save: at least one blue_node is required.\n"
                    "The agent has nothing to rotate to.")

        # Nodes inside a rope's keep-out (H91, Hong's pick "Refuse and block Save"): loaded from a file, or left there
        # when a rope was added or moved. The click refusal in _onCanvasClick keeps new ones out.
        near_ropes = [(n, rope) for n in self.state.nodes.values()
                      if (rope := near_rope_end(n.pos, self.state.ropes)) is not None]
        if near_ropes:
            lines = ["Cannot save: these nodes are too close to a rope where it meets a platform:"]
            for n, rope in near_ropes:
                left, right = free_x(rope)
                lines.append(
                    f"#{n.local_id} {n.pos}: rope at x {rope.x}; place it at x {left} or less, or {right} or more"
                )
            return ("block", "\n".join(lines))

        issues = []

        positions = {}
        for node in self.state.nodes.values():
            positions.setdefault(node.pos, []).append(node.local_id)
        dupes = {p: ids for p, ids in positions.items() if len(ids) > 1}
        # One bullet per affected node so each issue requiring a fix is visually distinct;
        # include position so the user can match the ID to a dot on the minimap without
        # toggling "Highlight orphans" on.
        # Duplicates group by position (one bullet per colliding pos with all node IDs), since
        # the unit of repair is the position — move one of the colliding dots. Off-bounds and
        # orphans below bullet per-node because each is fixed independently.
        if dupes:
            issues.append("Duplicate positions:")
            for pos, ids in dupes.items():
                issues.append(f"  • {pos}: nodes #{', #'.join(str(i) for i in ids)}")

        # node.pos is in native minimap coords; canvas dimensions are scaled by RENDER_SCALE,
        # so divide back to compare against the native frame. Clicks always land inside the
        # canvas (i.e., in-bounds native pos), so this check only fires for CSV-loaded nodes
        # with out-of-range coords.
        if self.minimap_canvas_width is not None and self.minimap_canvas_height is not None:
            native_w = self.minimap_canvas_width // RENDER_SCALE
            native_h = self.minimap_canvas_height // RENDER_SCALE
            off_nodes = [
                n for n in self.state.nodes.values()
                if not (0 <= n.pos[0] < native_w and 0 <= n.pos[1] < native_h)
            ]
            if off_nodes:
                issues.append(f"Nodes outside minimap bounds ({native_w}x{native_h}):")
                for n in off_nodes:
                    issues.append(f"  • #{n.local_id} at {n.pos}")

        # Same accepted tradeoff as the edge-preview path (Task 12): bumps Node.node_id_counter.
        path_graph, id_to_node = self._buildPreviewGraph()
        if path_graph is None:
            # No character selected — orphan detection requires a built graph.
            # Skip it; the other validations above (rotation_order, duplicates,
            # off-bounds) still apply, so save-with-no-character stays usable.
            pass
        else:
            connected_nodes = {n for edge in path_graph.graph.edges() for n in edge}
            orphan_nodes = [
                self.state.nodes[lid] for lid, n in id_to_node.items()
                if n not in connected_nodes
            ]
            if orphan_nodes:
                issues.append("Orphan nodes (no edges):")
                for n in orphan_nodes:
                    issues.append(f"  • #{n.local_id} at {n.pos}")

        if issues:
            return ("warn", "\n".join(issues) + "\n\nSave anyway?")
        return ("ok", "")

    def _writeTo(self, path):
        """Pure I/O step: write current editor state to path; show success/failure modal.

        Validation is the caller's responsibility (`_okToSave` runs in `_save` /
        `_saveAs` before any file dialog).
        """
        # Pass current native minimap dims so read_editor_nodes can compare on load.
        # canvas width/height are stored at RENDER_SCALE; metadata must reflect native
        # pixel space because EditorNode.pos is in native coords.
        minimap_size = None
        if self.minimap_canvas_width is not None and self.minimap_canvas_height is not None:
            minimap_size = (
                self.minimap_canvas_width // RENDER_SCALE,
                self.minimap_canvas_height // RENDER_SCALE,
            )
        # The rope file beside the CSV (H94). With no ropes marked and a damaged rope file at the target, ask before
        # anything is written (Hong, QB236): Yes removes the file, No leaves it. Either way the CSV is written.
        rope_path = rope_file_for(Path(path))
        rope_file_problem = None  # the reader's message, when the rope file beside the target cannot be read
        if not self.state.ropes:
            existing_ropes = read_ropes(rope_path)
            if not is_successful(existing_ropes):
                rope_file_problem = existing_ropes.failure()
        rope_file_damaged = rope_file_problem is not None
        # Hong's sentence stays as he worded it; the reader's own message follows it, so a rope file from a newer Helix or
        # one that cannot be opened says why it failed before anyone answers Yes (ROPE152-R3a M1).
        remove_rope_file = rope_file_damaged and messagebox.askyesno(
            "Rope file damaged", f"{ROPE_FILE_DAMAGED_QUESTION}\n\n{rope_file_problem}",
            default=messagebox.NO, parent=self.window,
        )
        result = write_editor_nodes(
            path,
            self.state.nodes,
            self.state.rotation_order,
            minimap_size=minimap_size,
        )
        if not is_successful(result):
            messagebox.showerror(
                "Save failed",
                f"Could not write {path}:\n\n{result.failure()}",
                parent=self.window,
            )
            return
        # The CSV is written. A rope file is written when ropes are marked, or when a rope file was read (so ropes
        # deleted from a good file stay deleted and its unknown keys are kept); a map with neither stays one file.
        rope_error = None
        if rope_file_damaged:
            if remove_rope_file:
                try:
                    rope_path.unlink(missing_ok=True)
                    self.state.rope_file_read = False
                except OSError as error:
                    rope_error = f"{rope_path} could not be removed: {error.strerror or error}."
        elif self.state.ropes or self.state.rope_file_read:
            rope_result = write_ropes(
                rope_path, RopeFile(tuple(self.state.ropes), minimap_size, self.state.rope_extra),
            )
            if is_successful(rope_result):
                self.state.rope_file_read = True
            else:
                rope_error = rope_result.failure()
        if rope_error is not None:
            messagebox.showerror(
                "Save failed",
                f"The map was written to {path}, but its rope file was not:\n\n{rope_error}",
                parent=self.window,
            )
            return
        self.state.current_file = path
        self._snapshotSavedBaseline()
        messagebox.showinfo(
            "Saved",
            f"Saved to {path}",
            parent=self.window,
        )

    def _snapshotSavedBaseline(self):
        """Snapshot current state as the new saved baseline; clear dirty + refresh title.

        Called after successful save or load. Deep-copies nodes so subsequent in-place flag
        toggles on EditorNode instances don't mutate the baseline; rotation_order is shallow-
        copied (its int contents are immutable).
        """
        self._saved_nodes = copy.deepcopy(self.state.nodes)
        self._saved_rotation = list(self.state.rotation_order)
        self._saved_ropes = list(self.state.ropes)  # RopeMark is frozen, so a shallow copy is a snapshot
        self.state.dirty = False
        self._refreshTitle()

    def _load(self):
        """Load a CSV into the editor.

        If state is dirty, prompts Save/Discard/Cancel first:
          - Cancel: abort the load entirely.
          - Save: run the standard save flow (silent overwrite if current_file is set,
            else Save As). Afterwards, if state is still dirty (Save As cancelled OR
            write_editor_nodes failed), abort the load. Otherwise proceed.
          - Discard: fall through to the load dialog, abandoning unsaved changes.
        """
        if self.state.dirty:
            name = Path(self.state.current_file).name if self.state.current_file else "untitled"
            resp = messagebox.askyesnocancel(
                "Unsaved changes",
                f"You have unsaved changes to {name}.\nSave before loading?",
                parent=self.window,
            )
            if resp is None:
                return  # user cancelled
            if resp is True:
                self._save()
                # If user cancelled Save As, current_file may still be None and dirty still True.
                if self.state.dirty:
                    return
            # resp is False: discard — fall through to the load dialog.

        path = filedialog.askopenfilename(
            parent=self.window,
            title="Load Map CSV",
            initialdir="maps",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if not path:
            return  # user cancelled the load dialog

        result = read_editor_nodes(path)
        if not is_successful(result):
            reason = result.failure()
            print(f"[MapEditor] Load failed for {path}: {reason}")  # dev log
            messagebox.showerror(
                "Could not load map",
                f"{reason}\n\n"
                "Your current editor state has not been changed.",
                parent=self.window,
            )
            return

        nodes_list, rotation_order, metadata = result.unwrap()

        # Metadata-based dimension gate (Task 19). When `minimap_size` metadata is
        # present, it's authoritative - mismatch aborts the load even if all nodes
        # happen to fit current bounds. Legacy CSVs (no metadata) skip this block
        # and fall through to the per-node bounds check below.
        if self.minimap_canvas_width is not None and self.minimap_canvas_height is not None:
            native_w = self.minimap_canvas_width // RENDER_SCALE
            native_h = self.minimap_canvas_height // RENDER_SCALE
            saved_dims = metadata.get('minimap_size')
            if saved_dims is not None and saved_dims != (native_w, native_h):
                messagebox.showerror(
                    "Could not load map",
                    f'Could not load "{Path(path).name}".\n\n'
                    f"Minimap dimension saved as {saved_dims[0]}x{saved_dims[1]}; "
                    f"current minimap is {native_w}x{native_h}.\n\n"
                    "Fix: ensure your current minimap dimensions match the minimap "
                    "dimensions in use when this CSV was created.\n\n"
                    "Your current editor state has not been changed.",
                    parent=self.window,
                )
                return

        # Pre-mutation bounds check. CSV stores native coords; if any node falls outside
        # the current minimap's native dimensions the file likely came from a different
        # MapleStory window size (or was hand-edited). Refuse the load so the user doesn't
        # end up with invisible off-canvas nodes silently populating state.
        if self.minimap_canvas_width is not None and self.minimap_canvas_height is not None:
            native_w = self.minimap_canvas_width // RENDER_SCALE
            native_h = self.minimap_canvas_height // RENDER_SCALE
            off_nodes = [
                n for n in nodes_list
                if not (0 <= n.pos[0] < native_w and 0 <= n.pos[1] < native_h)
            ]
            if off_nodes:
                # One bullet per offending node + its position, so each problem the user
                # needs to fix is enumerable.
                bullet_list = "\n".join(f"  • #{n.local_id} at {n.pos}" for n in off_nodes)
                messagebox.showerror(
                    "Could not load map",
                    f'Could not load "{Path(path).name}".\n\n'
                    f"Nodes are outside the current minimap bounds ({native_w}x{native_h}):\n"
                    f"{bullet_list}\n\n"
                    "Fix: ensure your current minimap dimensions match the minimap dimensions in use when this CSV was created.\n\n"
                    "Your current editor state has not been changed.",
                    parent=self.window,
                )
                return

        # The rope file beside the CSV (H94), read only now: a bad CSV, a size mismatch or nodes out of bounds were
        # refused above, before this. A bad rope file does not stop the load: the nodes open with no ropes (C11).
        rope_path = rope_file_for(Path(path))
        rope_file_existed = rope_path.exists()  # before the read: a missing file reads as Success with no ropes
        rope_result = read_ropes(rope_path)

        self.state.nodes = {n.local_id: n for n in nodes_list}
        self.state.rotation_order = list(rotation_order)
        self.state.selected_id = None
        self.state.selected_rope = None
        self.state.adding_rope = None  # an armed "Add rope" does not survive a load (ROPE148RR N-m1)
        self.state.add_rope_armed = False
        self.state.next_local_id = (max(self.state.nodes) + 1) if self.state.nodes else 1
        self.state.current_file = path
        if is_successful(rope_result):
            rope_file = rope_result.unwrap()
            self.state.ropes = list(rope_file.ropes)
            self.state.rope_extra = dict(rope_file.extra)
            self.state.rope_file_read = rope_file_existed  # assigned on every load: no stale flag from the last map
        else:
            self.state.ropes = []
            self.state.rope_extra = {}
            self.state.rope_file_read = False
        self._snapshotSavedBaseline()
        self._refreshInspector()
        self._refreshRopeBoxes()
        self._refreshRotation()
        self._renderNodes()
        if not is_successful(rope_result):
            messagebox.showerror(
                "Rope file not loaded",
                f"{rope_result.failure()}\n\n{ROPE_FILE_NOT_LOADED_TEXT}",
                parent=self.window,
            )

    def _okToClose(self):
        """Hooked to WM_DELETE_WINDOW via gui.py. Returns True if the window should be
        destroyed, False if the user cancelled (so gui.py knows whether to clear its ref).

        When dirty, prompts Save/Discard/Cancel. Cancel → False. Discard → True. Save runs
        the standard save flow; if state is still dirty after (Save As cancelled OR write
        failed), treat as a cancel and keep the window open.
        """
        if not self.state.dirty:
            return True
        name = Path(self.state.current_file).name if self.state.current_file else "untitled"
        resp = messagebox.askyesnocancel(
            "Unsaved changes",
            f"You have unsaved changes to {name}.\nSave before closing?",
            parent=self.window,
        )
        if resp is None:
            return False
        if resp is True:
            self._save()
            if self.state.dirty:
                return False
        return True

    def onCharacterClassChanged(self) -> None:
        """Called by MapleGUI after the character picker changes (gui.py). Re-
        evaluates editor state that depends on the selected character — today,
        just the Show edges checkbox, which needs a character to build a preview
        graph. This is the hook the picker handler already invokes."""
        self._evaluateShowEdgesState()

    def _buildPreviewGraph(self):
        """
        Build a throwaway PathGraph that matches what the runtime would build after
        a save → load cycle of the current editor state.

        Insertion order into node_map is map_format.ordered_local_ids, the row order
        map_csv.write_editor_nodes writes: rotation-order blue_nodes first, then
        orange_nodes sorted by local_id. This matters because
        PathGraph._findClosest* methods break distance ties by keeping the first-inserted
        candidate (strict `>` comparison), so iteration order can flip edges in tie cases.

        Returns (path_graph, id_to_node) where id_to_node maps each EditorNode's
        local_id to the Node instance built for the preview. Bumps Node.node_id_counter
        every call — accepted tradeoff documented under Task 12 in the plan; UUIDs are
        runtime-only and not persisted.
        """
        order = ordered_local_ids(self.state.nodes, self.state.rotation_order)
        id_to_node = {}
        node_map = {}
        for local_id in order:
            editor_node = self.state.nodes[local_id]
            n = Node(
                nodetype=editor_node.nodetype,
                minimap_pos=editor_node.pos,
                can_fall=editor_node.can_fall,
                fall_dist_exceeds_vertical_tp=editor_node.fall_dist_exceeds_vertical_tp,
                is_left_platform_edge=editor_node.is_left_platform_edge,
                is_right_platform_edge=editor_node.is_right_platform_edge,
            )
            node_map[n.uuid] = n
            id_to_node[editor_node.local_id] = n
        res = self.controller.agent.buildPathGraphFor(node_map)
        if not is_successful(res):
            # Reached when no character is selected (buildPathGraphFor returns
            # Failure — it needs the character's traversal caps). All three
            # callers guard on this None return: _validateForSave,
            # _renderOrphanHighlights, and _renderPreview.
            return None, id_to_node
        path_graph = res.unwrap()
        # Any buildGraph() Failure is tolerated here — the empty editor returns
        # Failure("Node_dict is empty."), and the node_map/graph-mismatch guard
        # can't trip (the graph is freshly built from this exact node_map).
        # Either way path_graph.graph stays a valid graph and callers tolerate
        # no edges, so we read it without checking the result.
        path_graph.buildGraph()
        return path_graph, id_to_node

    def _evaluateShowEdgesState(self) -> None:
        """Enable "Show edges" only when a character class is selected — the
        preview graph needs the character's traversal caps to build, so with no
        character `buildPathGraphFor` returns Failure and there is nothing to
        show. The disabled label carries the reason in-band (Tkinter has no
        native tooltip; a custom one is overkill for a single hint).

        Single-character-per-session: the class is chosen once and never
        cleared, so the only transition is the initial no-character state ->
        enabled on the first pick. There is no enabled->disabled path, hence no
        force-off-the-checkbox cleanup."""
        if self.controller.agent.characterClass is None:
            self._show_edges_cb.config(
                state=tk.DISABLED,
                text="Show edges (pick a character first)",
            )
        else:
            self._show_edges_cb.config(
                state=tk.NORMAL,
                text="Show edges",
            )

    def _renderPreview(self):
        """Render preview overlays (edges + tolerance bands). Called whenever toggles or state change.

        Edges and bands are gated independently; bands need a selected node, edges don't.
        """
        if self.minimap_canvas is None:
            return
        self.minimap_canvas.delete(CANVAS_TAG_EDGE)
        self.minimap_canvas.delete(CANVAS_TAG_BAND)
        if self._show_edges_var.get():
            path_graph, id_to_node = self._buildPreviewGraph()
            # path_graph is None when no character is selected (Task 7 disables
            # the checkbox to keep the toggle from reaching here, but other
            # redraw triggers can still call _renderPreview — guard regardless).
            # Scoped skip rather than early return so band rendering below runs.
            if path_graph is not None and id_to_node:
                # Classify edges horizontal vs vertical by Δy vs Δx; minimap_position is native
                # coords, multiply by RENDER_SCALE for canvas pixels (same pattern as _renderNodes).
                # Dedupe by unordered pair so one line renders per pair; arrowheads encode which
                # directed edges actually exist (asymmetric vertical edges with platform-edge gating
                # would otherwise look bidirectional to the eye — see _isVerticalEdgeValid).
                drawn = set()
                for u, v in path_graph.graph.edges():
                    key = frozenset((u, v))
                    if key in drawn:
                        continue
                    drawn.add(key)
                    has_uv = path_graph.graph.has_edge(u, v)
                    has_vu = path_graph.graph.has_edge(v, u)
                    if has_uv and has_vu:
                        arrow_spec = tk.BOTH
                    elif has_uv:
                        arrow_spec = tk.LAST
                    else:
                        arrow_spec = tk.FIRST
                    ux, uy = u.minimap_position
                    vx, vy = v.minimap_position
                    is_vertical = abs(vy - uy) > abs(vx - ux)
                    color = "darkgreen" if is_vertical else "limegreen"
                    sx1, sy1 = ux * RENDER_SCALE, uy * RENDER_SCALE
                    sx2, sy2 = vx * RENDER_SCALE, vy * RENDER_SCALE
                    self.minimap_canvas.create_line(
                        sx1, sy1, sx2, sy2,
                        fill=color, width=1, arrow=arrow_spec,
                        tags=CANVAS_TAG_EDGE,
                    )
        if self._show_bands_var.get() and self.state.selected_id is not None:
            sel = self.state.nodes.get(self.state.selected_id)
            if sel is not None:
                # Dashed-outline rectangles (no fill) visualize the ±y / ±x tolerance windows
                # PathGraph uses when picking neighbours for horizontal / vertical edges.
                # Tk has no native alpha blending, so dashed outline + no fill keeps the
                # minimap underneath readable.
                y_tol = self.controller.agent.edge_y_tolerance_level
                x_tol = self.controller.agent.edge_x_tolerance_level
                sx = sel.pos[0] * RENDER_SCALE
                sy = sel.pos[1] * RENDER_SCALE
                y_tol_px = y_tol * RENDER_SCALE
                x_tol_px = x_tol * RENDER_SCALE
                w = self.minimap_canvas_width
                h = self.minimap_canvas_height
                self.minimap_canvas.create_rectangle(
                    0, sy - y_tol_px, w, sy + y_tol_px,
                    outline="blue", dash=(2, 4), width=1,
                    tags=CANVAS_TAG_BAND,
                )
                self.minimap_canvas.create_rectangle(
                    sx - x_tol_px, 0, sx + x_tol_px, h,
                    outline="purple", dash=(2, 4), width=1,
                    tags=CANVAS_TAG_BAND,
                )

    def _renderOrphanHighlights(self):
        """Draw a red X within each orphan node's circle when the 'Highlight orphans' toggle is on.

        Orphan = no edges to or from this node in the throwaway preview graph. Helps the
        user visually identify isolated nodes (especially orange ones, which don't get
        local_id labels on the canvas) when the orphan warning lists ids.
        """
        if not self._show_orphans_var.get():
            return
        path_graph, id_to_node = self._buildPreviewGraph()
        if path_graph is None:
            # No character selected — no graph to compute orphans from. The
            # Highlight-orphans toggle is independent of Show-edges, so this is
            # the only guard protecting this path against the None return.
            return
        if not id_to_node:
            return
        connected_nodes = {n for edge in path_graph.graph.edges() for n in edge}
        node_r = NODE_DRAW_RADIUS_PX * RENDER_SCALE
        for local_id, node in id_to_node.items():
            if node in connected_nodes:
                continue
            editor_node = self.state.nodes[local_id]
            sx, sy = editor_node.pos[0] * RENDER_SCALE, editor_node.pos[1] * RENDER_SCALE
            # Two diagonals spanning the dot diameter form an X inside the circle.
            self.minimap_canvas.create_line(
                sx - node_r, sy - node_r, sx + node_r, sy + node_r,
                fill="red", width=2, tags=CANVAS_TAG_ORPHAN,
            )
            self.minimap_canvas.create_line(
                sx - node_r, sy + node_r, sx + node_r, sy - node_r,
                fill="red", width=2, tags=CANVAS_TAG_ORPHAN,
            )

    def _renderRopes(self):
        """Draw each rope mark as a yellow line from (x, top_y) to (x, bottom_y), the selected one thicker.
        Called by _renderNodes before the dots, so the dots sit on top of the lines."""
        if self.minimap_canvas is None:
            return
        self.minimap_canvas.delete(CANVAS_TAG_ROPE)
        for index, rope in enumerate(self.state.ropes):
            width = ROPE_SELECTED_LINE_WIDTH if index == self.state.selected_rope else ROPE_LINE_WIDTH
            self.minimap_canvas.create_line(
                rope.x * RENDER_SCALE, rope.top_y * RENDER_SCALE,
                rope.x * RENDER_SCALE, rope.bottom_y * RENDER_SCALE,
                fill="yellow", width=width, tags=CANVAS_TAG_ROPE,
            )

    def _renderNodes(self):
        """Redraw all nodes + the selected-ring overlay. Calls _renderPreview first so
        node dots sit on top of edge/band overlays.

        Render order: preview (edges + bands) → rope lines → node dots (a red keep-out ring under each node near a
        rope end) → orphan X overlay → selected ring.
        node.pos is in native minimap coords; multiply by RENDER_SCALE for canvas pixels.
        """
        if self.minimap_canvas is None:
            return
        self._renderPreview()
        self.minimap_canvas.delete(CANVAS_TAG_NODE)
        self.minimap_canvas.delete(CANVAS_TAG_SELECTED)
        self.minimap_canvas.delete(CANVAS_TAG_ORPHAN)
        self.minimap_canvas.delete(CANVAS_TAG_KEEPOUT)
        self._renderRopes()
        node_r = NODE_DRAW_RADIUS_PX * RENDER_SCALE
        keepout_r = node_r + KEEPOUT_RING_EXTRA_PX
        for node in self.state.nodes.values():
            x, y = node.pos
            sx, sy = x * RENDER_SCALE, y * RENDER_SCALE
            if near_rope_end(node.pos, self.state.ropes) is not None:
                self.minimap_canvas.create_oval(
                    sx - keepout_r, sy - keepout_r, sx + keepout_r, sy + keepout_r,
                    fill="", outline="red", width=2, tags=CANVAS_TAG_KEEPOUT,
                )
            fill = "lightblue" if node.nodetype == "blue_node" else "orange"
            outline = "blue" if node.nodetype == "blue_node" else "darkorange"
            self.minimap_canvas.create_oval(
                sx - node_r, sy - node_r,
                sx + node_r, sy + node_r,
                fill=fill, outline=outline, width=1,
                tags=CANVAS_TAG_NODE,
            )
            if node.nodetype == "blue_node":
                self.minimap_canvas.create_text(
                    sx + node_r + 2, sy,
                    text=f"#{node.local_id}",
                    anchor='w',
                    fill=NODE_LABEL_FG,
                    font=self._font_node_label,
                    tags=CANVAS_TAG_NODE,
                )
        self._renderOrphanHighlights()
        if self.state.selected_id is not None and self.state.selected_id in self.state.nodes:
            x, y = self.state.nodes[self.state.selected_id].pos
            sx, sy = x * RENDER_SCALE, y * RENDER_SCALE
            ring_r = SELECTED_RING_RADIUS_PX * RENDER_SCALE
            self.minimap_canvas.create_oval(
                sx - ring_r, sy - ring_r,
                sx + ring_r, sy + ring_r,
                fill="", outline="yellow", width=2,
                tags=CANVAS_TAG_SELECTED,
            )

    def _updateCanvas(self):
        """
        Pull the latest minimap frame and draw it. Schedules itself every 10ms
        via tk.after, matching MapleGUI._updateCanvas's cadence. Stops when the
        editor window has been destroyed.
        """
        if not self.window.winfo_exists():
            return  # window destroyed — stop the loop
        if self.minimap_canvas is None:
            self.window.after(10, self._updateCanvas)
            return
        with self.sharedInfo.minimap_frame_lock:
            frame = self.sharedInfo.minimap_frame
            if frame is not None:
                pil_minimap = Image.fromarray(frame)
                if RENDER_SCALE != 1:
                    pil_minimap = pil_minimap.resize(
                        (self.minimap_canvas_width, self.minimap_canvas_height),
                        Image.NEAREST,  # game pixels stay sharp; no interpolation blur
                    )
                minimap_img = ImageTk.PhotoImage(pil_minimap)
                self.minimap_canvas.image = minimap_img  # prevent GC
                if self.minimap_canvas_bg_img_id is None:
                    self.minimap_canvas_bg_img_id = self.minimap_canvas.create_image(
                        0, 0, image=minimap_img, anchor=tk.NW
                    )
                else:
                    self.minimap_canvas.itemconfig(
                        self.minimap_canvas_bg_img_id, image=minimap_img
                    )
        self.window.after(10, self._updateCanvas)
