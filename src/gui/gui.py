from pathlib import Path
import tkinter as tk
import tkinter.font as tkfont
import tkinter.filedialog as filedialog
import tkinter.messagebox as messagebox
from PIL import Image, ImageTk
import networkx as nx
import matplotlib.pyplot as plt
from returns.pipeline import is_successful
import tkinter.ttk as ttk
from src.gui.minimap_lost import clear_lost, read_minimap, show_lost
from src.controller.node import  CharacterNode
from src.controller.controller import BuildOutcome
from src.classes.classes import CharacterClass
from src.gui.map_editor import MapEditor
from src.gui.route_tab import RouteTab
from src.route.character import CharacterLocator

# Shown where the minimap will appear, and in the console by the buttons that need it, until the first frame arrives.
MINIMAP_NOT_FOUND_LABEL = ("Minimap not found yet. Waiting for the game's minimap to be visible on the captured "
                           "monitor; it appears here as soon as it is found.")
MINIMAP_NOT_FOUND_MESSAGE = "Minimap not found yet: make the game's minimap visible on the captured monitor, then try again."

# The box the Route tab asks on a second press of the close button, 10 s into a cleanup that has not ended (spec 5.5,
# H79). The question is Hong's mockup (QB182).
CLOSE_ANYWAY_TITLE = "Helix"
CLOSE_ANYWAY_MESSAGE = "The run is still cleaning up. Close anyway?"

# The character icon the buttons look for on the minimap (the route walker's finder, decision H20).
_CHARACTER_ASSETS = Path(__file__).resolve().parent.parent.parent / "assets" / "chr"
CHARACTER_ICON_PATH = _CHARACTER_ASSETS / "chr.png"
CHARACTER_MASK_PATH = _CHARACTER_ASSETS / "chr_mask.png"
# Shown when the finder says no; the reason is its Failure text ("miss", "ambiguous", or "bad frame: ...").
CHARACTER_NOT_FOUND_MESSAGE = "Character not found on the minimap ({reason})."
CHARACTER_NOT_FOUND_REASONS = {
    "miss": "no place on the minimap looks like the character icon",
    "ambiguous": "two separate places look almost equally like the character icon",
}


class MapleGUI:
    # Built by _setupGUI (contract C4, docs/superpowers/plans/2026-10-31-route-tab.md): the window is a Notebook of the
    # Setup tab (everything the window held before the Route tab) and the Route tab's frame.
    notebook: ttk.Notebook
    setup_tab: tk.Frame
    route_tab_frame: tk.Frame
    scroll_canvas: tk.Canvas  # scrolls the Setup tab's column; set by _setupScrollableContainer, used by _onMouseWheel

    def __init__(self, controller = None, sharedInfo = None, *, route_tab_factory = RouteTab):
        self.minimap_canvas_width = None
        self.minimap_canvas_height = None
        self.minimap_canvas = None
        self.minimap_canvas_bg_img_id = None #new
        self.minimap_status_label = None #placeholder shown until the first minimap frame creates minimap_canvas
        self.gui = tk.Tk()
        self.gui_console = None #created in _setupGUI so it can be parented to the scrollable inner frame
        self.scrollable_frame = None #inner frame inside the scrolling canvas; parent for all content widgets
        self._route_tab_factory = route_tab_factory  # called as factory(route_tab_frame, self) at the end of _setupGUI;
        # None builds no Route tab (the tests of the window alone)
        self.route_tab = None  # the factory's product: None until _setupGUI has run, and with no factory
        self.node_count = 0
        self.controller = controller
        self.sharedInfo = sharedInfo
        self.map_editor = None  # MapEditor instance when open, None otherwise
        self._character_locator = None  # built on the first search for the character (_findCharacter)
        self.zoom_factor = 1.0
        self.min_zoom = 0.5
        self.max_zoom = 3.0
        self.zoom_step = 0.1
        self._base_font_sizes = {}

    def start(self, startup_notice = None):
        """Build the window and run it. `startup_notice`, when given, is the first line of the Setup tab's console box
        (`src/main.py` passes the not-elevated warning, H82)."""
        self._setupGUI()
        if startup_notice:
            self._printToGUIConsole(startup_notice)
        if self.route_tab is not None:
            self.route_tab.startPolling()  # the tab's 100 ms drain; nothing else starts it (contract C5)
        self._updateCanvas()
        self.gui.mainloop()

    def _setupScrollableContainer(self):
        """Build the scrollable wrapper in the Setup tab. Returns the inner Frame that should parent all content widgets."""
        container = tk.Frame(self.setup_tab)
        container.pack(fill=tk.BOTH, expand=True)

        scroll_canvas = tk.Canvas(container, bg='lightblue', highlightthickness=0)
        vbar = tk.Scrollbar(container, orient=tk.VERTICAL, command=scroll_canvas.yview)
        scroll_canvas.configure(yscrollcommand=vbar.set)

        vbar.pack(side=tk.RIGHT, fill=tk.Y)
        scroll_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        inner = tk.Frame(scroll_canvas, bg='lightblue')
        inner_window_id = scroll_canvas.create_window((0, 0), window=inner, anchor='nw')

        # Keep scrollregion in sync as the inner frame's size changes
        inner.bind('<Configure>', lambda e: scroll_canvas.configure(scrollregion=scroll_canvas.bbox('all')))
        # Make the inner frame's width track the canvas width so children can fill horizontally
        scroll_canvas.bind('<Configure>', lambda e: scroll_canvas.itemconfig(inner_window_id, width=e.width))

        self.scroll_canvas = scroll_canvas
        scroll_canvas.bind_all('<MouseWheel>', self._onMouseWheel)

        return inner

    def _onMouseWheel(self, event):
        """Mouse wheel scroll (Windows: event.delta is ±120 per notch). bind_all catches every widget of the window, so
        it acts only while the Setup tab is shown: the Route tab's canvas and slider keep their own wheel (spec 5.7)."""
        if not self.isSetupTabShown():
            return
        if isinstance(event.widget, (tk.Text, tk.Listbox)):
            return  # let the inner scrollable widget handle its own scroll
        self.scroll_canvas.yview_scroll(int(-1 * (event.delta / 120)), 'units')

    def _captureBaseFontSizes(self):
        # Record each named font's original size so zoom is relative to defaults, not previous zoom state.
        for name in ('TkDefaultFont', 'TkTextFont', 'TkFixedFont', 'TkMenuFont',
                     'TkHeadingFont', 'TkCaptionFont', 'TkSmallCaptionFont',
                     'TkIconFont', 'TkTooltipFont'):
            try:
                self._base_font_sizes[name] = tkfont.nametofont(name).cget('size')
            except tk.TclError:
                pass

    def _applyZoom(self):
        for name, base_size in self._base_font_sizes.items():
            # Negative sizes are pixels, positive are points — preserve sign.
            scaled = abs(base_size) * self.zoom_factor
            new_size = max(1, int(round(scaled)))
            if base_size < 0:
                new_size = -new_size
            tkfont.nametofont(name).configure(size=new_size)

    def _zoomIn(self, event=None):
        self.zoom_factor = min(self.max_zoom, self.zoom_factor + self.zoom_step)
        self._applyZoom()
        return 'break'

    def _zoomOut(self, event=None):
        self.zoom_factor = max(self.min_zoom, self.zoom_factor - self.zoom_step)
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
        # Ctrl + / - / 0 for keyboard zoom; Ctrl + mouse wheel for smooth zoom.
        # bind (not bind_all) on the Tk root scopes these to the main GUI's widget tree;
        # other Toplevels (e.g. MapEditor) have their own bindtag chain and aren't affected.
        self.gui.bind('<Control-plus>', self._zoomIn)
        self.gui.bind('<Control-equal>', self._zoomIn)
        self.gui.bind('<Control-KP_Add>', self._zoomIn)
        self.gui.bind('<Control-minus>', self._zoomOut)
        self.gui.bind('<Control-KP_Subtract>', self._zoomOut)
        self.gui.bind('<Control-0>', self._zoomReset)
        self.gui.bind('<Control-MouseWheel>', self._onCtrlMouseWheel)

    def isSetupTabShown(self):
        return self.notebook.select() == str(self.setup_tab)

    def setRunLocked(self, locked):
        """Grey out the Setup tab for a run, or bring it back (spec 5.1): the eleven buttons and the class picker. The
        console Text stays editable on purpose: a disabled Text drops _printToGUIConsole's inserts. The tab's header
        is left alone: it stays selectable (H74, Hong's answer A to RTF98-1-D1)."""
        state = "disabled" if locked else "normal"
        for button in (self.draw_blue_node_button, self.draw_orange_node_button, self.clear_gui_console_button,
                       self.clear_gui_minimap_button, self.load_map_button, self.open_editor_button,
                       self.setup_agent_button, self.display_graph_button, self.connect_character_node_button,
                       self.delete_node_button, self.get_path_button):
            button.configure(state=state)
        self.character_picker.configure(state="disabled" if locked else "readonly")

    def askCloseAnyway(self) -> bool:
        """The Route tab's "Close anyway?" box (spec 5.5, H79): a Yes/No question on the main window, No the default
        button, so Enter keeps waiting. True for Yes. It lives here and not in `route_tab.py` because the tab's source
        holds no dialog name (spec 5.12 rule 2; its test scans it); this one box is the rule's single exception."""
        return messagebox.askyesno(CLOSE_ANYWAY_TITLE, CLOSE_ANYWAY_MESSAGE, default=messagebox.NO, parent=self.gui)

    def _onClose(self):
        """WM_DELETE_WINDOW: the Route tab decides when the window may go (it waits for a live run's cleanup)."""
        if self.route_tab is None:
            self.gui.destroy()
        else:
            self.route_tab.onAppClose(self.gui.destroy)

    def _setupGUI(self):
        self.gui.configure(bg='lightblue')
        self.gui.geometry("1120x1000")
        self.gui.protocol("WM_DELETE_WINDOW", self._onClose)

        self._captureBaseFontSizes()
        self._bindZoomShortcuts()

        self.notebook = ttk.Notebook(self.gui)
        self.notebook.pack(fill=tk.BOTH, expand=True)
        self.setup_tab = tk.Frame(self.notebook, bg='lightblue')
        self.route_tab_frame = tk.Frame(self.notebook)
        self.notebook.add(self.setup_tab, text="Setup")
        self.notebook.add(self.route_tab_frame, text="Route")

        self.scrollable_frame = self._setupScrollableContainer()
        parent = self.scrollable_frame

        self.gui_console = tk.Text(parent)
        self.gui_console.pack()

        self.draw_blue_node_button = tk.Button(parent, text="draw blue node on character", command=self.drawBlueNodeOnCharacter)
        self.draw_blue_node_button.pack(side=tk.TOP, pady=5)

        self.draw_orange_node_button = tk.Button(parent, text="draw orange node on character", command=self.drawOrangeNodeOnCharacter)
        self.draw_orange_node_button.pack(side=tk.TOP, pady=5)

        self.clear_gui_console_button = tk.Button(parent, text = "clear console", command = self.clearGUIConsole)
        self.clear_gui_console_button.pack(side=tk.TOP, pady=5)

        self.clear_gui_minimap_button = tk.Button(parent, text = "clear drawn nodes on minimap", command = self._clearDrawnNodesOnGUIMinimap)
        self.clear_gui_minimap_button.pack(side=tk.TOP, pady=5)

        # --- Character picker (above Load Map) ---
        character_picker_label = tk.Label(parent, text="Character:")
        character_picker_label.pack(side=tk.TOP, pady=(10, 0))

        self._character_class_options = {cls.__name__: cls for cls in CharacterClass.__subclasses__()}
        # Empty-string sentinel is the dropdown's first entry — represents "no selection."
        self._character_picker_var = tk.StringVar(value="")
        self.character_picker = ttk.Combobox(
            parent,
            textvariable=self._character_picker_var,
            values=list(self._character_class_options.keys()),
            state="readonly",
            width=20,
        )
        self.character_picker.pack(side=tk.TOP, pady=(0, 10))
        self.character_picker.bind("<<ComboboxSelected>>", self._onCharacterPickerChange)

        self.load_map_button = tk.Button(parent, text="Load Map (Browse...)", command=self._loadMapBrowse)
        self.load_map_button.pack(side=tk.TOP, pady=5)

        self.open_editor_button = tk.Button(parent, text="Open Map Editor", command=self._openMapEditor)
        self.open_editor_button.pack(side=tk.TOP, pady=5)

        #delete once we have no need for direct testing or move to a separate part of GUI for testing purposes only
        # parse_CSV_button = tk.Button(parent, text = "(delete or move to a separate tab on the GUI) parse CSV", command = self.controller.parseCSV)
        # parse_CSV_button.pack(side=tk.TOP, pady=5)

        #delete once we have no need for direct testing or move to a separate part of GUI for testing purposes only
        # build_graph_button = tk.Button(parent, text = "(delete or move to a separate tab on the GUI) create edges", command = self.controller.buildGraph)
        # build_graph_button.pack(side=tk.TOP, pady=5)

        self.setup_agent_button = tk.Button(parent, text = "(delete or move to a separate tab on the GUI) setup agent", command = self.mockSetupAgentEnvironment)
        self.setup_agent_button.pack(side = tk.TOP, pady=5)

        #delete once we have no need for direct testing or move to a separate part of GUI for testing purposes only
        self.display_graph_button = tk.Button(parent, text="(delete or move to a separate tab on the GUI) used to visualise the graph", command=self._drawGraph)
        self.display_graph_button.pack(side=tk.TOP, pady=5)

        #delete once we have no need for direct testing or move to a separate part of GUI for testing purposes only
        self.connect_character_node_button = tk.Button(parent, text="(delete or move to a separate tab on the GUI) used to add character node to graph (node is d in just for testing purposes only)", command=self.controller.connectCharacterNode)
        self.connect_character_node_button.pack(side=tk.TOP, pady=5)
        #
        #delete once we have no need for direct testing or move to a separate part of GUI for testing purposes only
        self.delete_node_button = tk.Button(parent, text="(delete or move to a separate tab on the GUI) used to delete character node from graph (node is hardcoded in just for testing purposes only)", command=self.controller.deleteNode)
        self.delete_node_button.pack(side=tk.TOP, pady=5)

        # delete once we have no need for direct testing
        self.get_path_button = tk.Button(parent, text="(delete after done testing) used get the path to goal node", command=self.showPathToGoalNode)
        self.get_path_button.pack(side=tk.TOP, pady=5)

        #delete once we have no need for direct testing of the setup_agent function
        # setup_agent = tk.Button(parent, text="(delete after done testing) setup agent", command=self.controller.setupAgent)
        # setup_agent.pack(side=tk.TOP, pady=5)

        #include these once we finish main logic for agent + controller
        # tk.Button(parent, text="start controller", command=self.controller.start).pack(side = "right")
        # tk.Button(parent, text="set main node", command=self.infiniteLoop).pack(side = "right")
        # tk.Button(parent, text="set pathing node", command=self.controller.setPathingNode).pack(side = "right")
        # tk.Button(parent, text="draw path and nodes", command=self.controller.drawPathAndNodes).pack(side = "right")

        # The window must not wait for the game. The minimap area is built by _updateCanvas when the first frame
        # arrives; until then this label stands where it will go, and the buttons that need it say so in the console.
        self.minimap_status_label = tk.Label(parent, text=MINIMAP_NOT_FOUND_LABEL, bg='lightblue')
        self.minimap_status_label.pack(side=tk.TOP, pady=10)

        # The Route tab goes last so its constructor can read every Setup-tab widget; the focus bindings that call it
        # come after it, and only with a tab (spec 9: each focus event of the window schedules one re-read of Tk's
        # focus record). With the factory None (a test of the window alone) the tab stays an empty frame, route_tab is
        # None and nothing is bound.
        if self._route_tab_factory is not None:
            self.route_tab = self._route_tab_factory(self.route_tab_frame, self)
        if (tab := self.route_tab) is not None:
            for sequence in ("<FocusIn>", "<FocusOut>"):
                self.gui.bind(sequence, lambda _event: self.gui.after_idle(tab.rereadFocus), add="+")

    def _minimapReady(self):
        """True once the minimap area exists. Before that, say so in the console and return False: the buttons that
        draw on the minimap or read the character's position from it start with `if not self._minimapReady(): return`."""
        if self.minimap_canvas is not None:
            return True
        self._printToGUIConsole(MINIMAP_NOT_FOUND_MESSAGE)
        return False

    def _findCharacter(self):
        """The character's centre on the minimap as (x, y), or None after saying in the console why not (the finder
        says "not found" or "not sure" instead of guessing, decision H20). Call after `_minimapReady()`."""
        if self._character_locator is None:
            built = CharacterLocator.from_files(CHARACTER_ICON_PATH, CHARACTER_MASK_PATH)
            if not is_successful(built):
                self._printToGUIConsole(f"Cannot look for the character: {built.failure()}")
                return None
            self._character_locator = built.unwrap()
        # Read the frame under the lock and match outside it, as _updateCanvas does: the vision thread stores a new
        # array for every frame (src/vision/vision.py:80), so this array is not mutated while it is searched.
        shared = self.sharedInfo
        assert shared is not None  # _minimapReady() passed, so a frame came from it (the default None is for tests)
        with shared.minimap_frame_lock:
            frame = shared.minimap_frame
        found = self._character_locator.locate(frame)
        if not is_successful(found):
            reason = found.failure()
            self._printToGUIConsole(CHARACTER_NOT_FOUND_MESSAGE.format(
                reason=CHARACTER_NOT_FOUND_REASONS.get(reason, reason)))
            return None
        return found.unwrap()

    def drawBlueNodeOnCharacter(self):
        if not self._minimapReady():
            return
        coords = self._findCharacter()
        if coords is None:
            return
        x, y = coords
        node_type = "Blue Node"
        self.node_count += 1
        self._printToGUIConsole(f"Identifier: {self.node_count}, Type of Node: {node_type} | Character Position: ({x},{y})")
        self.minimap_canvas.create_oval(x - 2, y - 2, x + 2, y + 2, fill="lightblue", outline="lightblue", width=3, tags="node")
        self._drawNodeCount(x, y, text= self.node_count)

    def drawOrangeNodeOnCharacter(self):
        if not self._minimapReady():
            return
        coords = self._findCharacter()
        if coords is None:
            return
        x, y = coords
        node_type = "Orange Node"
        self.node_count += 1
        self._printToGUIConsole(f"Identifier: {self.node_count}, Type of Node: {node_type} | Character Position: ({x},{y})")
        self.minimap_canvas.create_oval(x - 2, y - 2, x + 2, y + 2, fill="orange", outline="darkorange", width=3, tags = "node")
        self._drawNodeCount(x, y, text = self.node_count)

    #delete once you don't need a direct way to test this. This should only exist inside the PathGraph class
    def showPathToGoalNode(self):
        if not self._minimapReady():
            return
        character_coords = self._findCharacter()
        if character_coords is None:
            return
        print("-----------------------------------------------")
        char_node = CharacterNode(character_coords)
        goal_node = self.controller.agent.map_rotation_order[0]
        self._drawGraph()
        self.controller.connectCharacterNode(char_node)
        self._drawGraph()
        path = self.controller.agent.pathGraph.getPathToGoalNode(char=char_node, goal=goal_node)
        if not path:
            print(f"no path found to goal node {goal_node}")
        else:
            print(f"computed path: {path} to goal node: {goal_node}")
        self.controller.deleteNode(char_node)
        # self._drawGraph()
        print("-----------------------------------------------")

    def clearGUIConsole(self):
        self.gui_console.delete(1.0, tk.END)

    def _loadMapBrowse(self):
        """Open a file dialog, then ask the controller to swap to the picked CSV."""
        path = filedialog.askopenfilename(
            parent=self.gui,
            title="Load Map CSV",
            initialdir="maps",
            filetypes=[("CSV files", "*.csv")],
        )
        if not path:
            return  # user cancelled
        result = self.controller.loadMap(path)
        if is_successful(result):
            outcome = result.unwrap()
            if outcome is BuildOutcome.BUILT:
                self._printToGUIConsole(f"Loaded map: {path}; graph built.")
            elif outcome is BuildOutcome.PENDING_CHARACTER:
                self._printToGUIConsole(
                    f"Map CSV loaded ({path}). No character selected yet — "
                    "pick one from the Character dropdown to build the graph."
                )
        else:
            # A message box, not only the console: a bad map file must be obvious to the user (decision H16).
            self._printToGUIConsole(f"Failed to load map {path}: {result.failure()}")
            messagebox.showerror("Could not load map", result.failure(), parent=self.gui)

    def _onCharacterPickerChange(self, event=None):
        """Handle character-picker selection. Sets the chosen class on the
        agent, which builds the graph if a map is already loaded (otherwise
        returns PENDING_CSV — waiting for a map). Branches on the BuildOutcome
        to message the user, and notifies the editor (if open) so it can
        re-evaluate the Show edges checkbox state."""
        selected_name = self._character_picker_var.get()
        # Idempotent guard: same-value re-selection (e.g. picking "Kanna"
        # while Kanna is already active) would otherwise trigger a
        # redundant rebuild.
        current = self.controller.agent.characterClass
        current_name = type(current).__name__ if current is not None else ""
        if current_name == selected_name:
            return
        char_class = self._character_class_options[selected_name]
        result = self.controller.setCharacterClass(char_class())
        label = f"set to {selected_name}"
        if is_successful(result):
            outcome = result.unwrap()
            if outcome is BuildOutcome.BUILT:
                self._printToGUIConsole(f"Character class {label}; graph built.")
            elif outcome is BuildOutcome.PENDING_CSV:
                self._printToGUIConsole(f"Character class {label}. No map loaded yet — use Load Map to load a CSV.")
        else:
            self._printToGUIConsole(f"Character class {label}, but graph build failed: {result.failure()}")
        if self.map_editor is not None and self.map_editor.window.winfo_exists():
            self.map_editor.onCharacterClassChanged()

    def _openMapEditor(self):
        """Open the map editor Toplevel. If one is already open, raise it to the front. Not before the first minimap
        frame: the editor's load checks and node drawing need the minimap size (see src/gui/CLAUDE.md)."""
        if not self._minimapReady():
            return
        if self.map_editor is not None and self.map_editor.window.winfo_exists():
            self.map_editor.window.lift()
            self.map_editor.window.focus_force()
            return
        self.map_editor = MapEditor(self.gui, self.controller, self.sharedInfo)
        # Close routes through MapEditor._okToClose so the unsaved-changes modal can veto.
        # We only destroy and clear our reference when it returns True; cancel keeps the window.
        def _on_editor_close():
            if self.map_editor._okToClose():
                self.map_editor.window.destroy()
                self.map_editor = None
        self.map_editor.window.protocol("WM_DELETE_WINDOW", _on_editor_close)

    #Without mocking the environment, we don't get to take advantage of setupAgent telling us it failed. We're only using this for testing purposes
    def mockSetupAgentEnvironment(self):
        setup_agent_res = self.controller.agent.setupAgent()
        if is_successful(setup_agent_res):
            print("setup agent successful")
        else:
            print(setup_agent_res)

    def _drawNodeCount(self, x, y, text, font=("Arial", 8, "bold")):
        # Draw outline (black border around text)
        # self.minimap_canvas.create_rectangle(x - 2, y - 5, x + 2, y - 3, fill='lightblue', outline='black')
        # Draw main number (bright color on top)
        self.minimap_canvas.create_text(x, y - 4, text=text, font=font, fill="yellow", tags="node_tag")


    #Incredibly limited custom implementation of the built-in print function. Only suitable for displaying strings and variables that have a valid string form, nothing else.
    def _printToGUIConsole(self, *args):
        seperator, end = ' ', '\n'
        # Join multiple arguments with a separator and handle non-string arguments
        strings = map(str, args)
        text = seperator.join(strings) + end
        self.gui_console.insert(tk.INSERT, text)
        self.gui_console.yview("end")
        # Also print to the IDE console for debugging

    def _clearDrawnNodesOnGUIMinimap(self):
        if not self._minimapReady():
            return
        self.minimap_canvas.delete("node")
        self.minimap_canvas.delete("node_tag")

    def _drawGraph(self):
        graph = self.controller.getGraph()
        node_map = self.controller.getNodeMap()
        node_positions = {node: node.minimap_position for node in node_map.values()}
        nx.draw(graph, pos = node_positions, with_labels=True, node_color='blue', node_size=10, font_size=10)
        plt.gca().invert_yaxis() #invert y-axis so origin is top-left
        plt.show()

    def _createMinimapCanvas(self, frame):
        """Build the minimap area at the frame's size, where the placeholder label stands, and return it. Called by
        _updateCanvas on the first frame."""
        self.minimap_canvas_height, self.minimap_canvas_width, _ = frame.shape
        if self.minimap_status_label is not None:
            self.minimap_status_label.destroy()
            self.minimap_status_label = None
        self.minimap_canvas = tk.Canvas(self.scrollable_frame, width= self.minimap_canvas_width,
                                        height= self.minimap_canvas_height,
                                        background='pink',
                                        highlightthickness=0,
                                        borderwidth=0,
                                        relief = 'flat')
        self.minimap_canvas.pack(side=tk.LEFT)
        return self.minimap_canvas

    #draws minimap on GUI
    def _updateCanvas(self):
        try:
            # While a run is live and the Setup tab is hidden nobody sees this picture: skip it, so the walker shares the
            # interpreter with one redraw (the Route tab's) instead of two (spec 5.7). The 10 ms loop itself goes on.
            picture_unseen = (self.route_tab is not None and self.route_tab.isRunLive()
                              and not self.isSetupTabShown())
            # Read the reference under the lock and draw outside it: the lock is never held across a Tk call.
            # The vision thread stores a new array for every frame (src/vision/vision.py:80), so the array read here is not mutated.
            frame, lost = read_minimap(self.sharedInfo)
            if frame is not None and not picture_unseen:
                canvas = self.minimap_canvas
                if canvas is None:
                    canvas = self._createMinimapCanvas(frame)
                if lost:  # the capture lost the minimap: say so in place of the frozen picture (VIS162-FIX)
                    show_lost(canvas, self.minimap_canvas_width, self.minimap_canvas_height)
                    return  # the finally below still schedules the next pass
                clear_lost(canvas)

                # Image conversion pipeline: numpy array → PIL Image → Tkinter PhotoImage
                pil_minimap = Image.fromarray(frame)
                minimap_img = ImageTk.PhotoImage(pil_minimap)

                # Prevent garbage collection by maintaining reference
                canvas.image = minimap_img
                # Canvas image management: create once, update thereafter
                if self.minimap_canvas_bg_img_id is None:
                    # Initial creation: place image at canvas center
                    self.minimap_canvas_bg_img_id = canvas.create_image(0,
                                                                        0,
                                                                        image=minimap_img,
                                                                        anchor= tk.NW)
                else:
                    # Subsequent updates: modify existing image item (preserves layer order)
                    canvas.itemconfig(self.minimap_canvas_bg_img_id, image=minimap_img)
        finally:
            # Schedule the next frame, also after a body that raised: one bad frame does not end the loop
            self.gui.after(10, self._updateCanvas)
