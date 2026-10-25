import tkinter as tk
import tkinter.font as tkfont
import tkinter.filedialog as filedialog
from PIL import Image, ImageTk
import networkx as nx
import matplotlib.pyplot as plt
import time
from returns.pipeline import is_successful
import tkinter.ttk as ttk
from src.controller.node import  CharacterNode
from src.controller.controller import BuildOutcome
from src.classes.classes import CharacterClass
from src.gui.map_editor import MapEditor


class MapleGUI:
    def __init__(self, controller = None, sharedInfo = None ):
        self.minimap_canvas_width = None
        self.minimap_canvas_height = None
        self.minimap_canvas = None
        self.minimap_canvas_bg_img_id = None #new
        self.gui = tk.Tk()
        self.gui_console = None #created in _setupGUI so it can be parented to the scrollable inner frame
        self.scrollable_frame = None #inner frame inside the scrolling canvas; parent for all content widgets
        self.node_count = 0
        self.controller = controller
        self.sharedInfo = sharedInfo
        self.map_editor = None  # MapEditor instance when open, None otherwise
        self.zoom_factor = 1.0
        self.min_zoom = 0.5
        self.max_zoom = 3.0
        self.zoom_step = 0.1
        self._base_font_sizes = {}

    def start(self):
        self._setupGUI()
        self._updateCanvas()
        self.gui.mainloop()

    def _setupScrollableContainer(self):
        """Build the scrollable wrapper. Returns the inner Frame that should parent all content widgets."""
        container = tk.Frame(self.gui)
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

        # Mouse wheel scroll (Windows: event.delta is ±120 per notch)
        def _on_mousewheel(event):
            if isinstance(event.widget, (tk.Text, tk.Listbox)):
                return  # let the inner scrollable widget handle its own scroll
            scroll_canvas.yview_scroll(int(-1 * (event.delta / 120)), 'units')
        scroll_canvas.bind_all('<MouseWheel>', _on_mousewheel)

        return inner

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

    def _setupGUI(self):
        self.gui.configure(bg='lightblue')
        self.gui.geometry("1000x1000")

        self._captureBaseFontSizes()
        self._bindZoomShortcuts()

        self.scrollable_frame = self._setupScrollableContainer()
        parent = self.scrollable_frame

        self.gui_console = tk.Text(parent)
        self.gui_console.pack()

        draw_blue_node_button = tk.Button(parent, text="draw blue node on character", command=self.drawBlueNodeOnCharacter)
        draw_blue_node_button.pack(side=tk.TOP, pady=5)

        draw_orange_node_button = tk.Button(parent, text="draw orange node on character", command=self.drawOrangeNodeOnCharacter)
        draw_orange_node_button.pack(side=tk.TOP, pady=5)

        clear_gui_console_button = tk.Button(parent, text = "clear console", command = self.clearGUIConsole)
        clear_gui_console_button.pack(side=tk.TOP, pady=5)

        clear_gui_minimap_button = tk.Button(parent, text = "clear drawn nodes on minimap", command = self._clearDrawnNodesOnGUIMinimap)
        clear_gui_minimap_button.pack(side=tk.TOP, pady=5)

        # --- Character picker (above Load Map) ---
        character_picker_label = tk.Label(parent, text="Character:")
        character_picker_label.pack(side=tk.TOP, pady=(10, 0))

        self._character_class_options = {cls.__name__: cls for cls in CharacterClass.__subclasses__()}
        # Empty-string sentinel is the dropdown's first entry — represents "no selection."
        self._character_picker_var = tk.StringVar(value="")
        character_picker = ttk.Combobox(
            parent,
            textvariable=self._character_picker_var,
            values=list(self._character_class_options.keys()),
            state="readonly",
            width=20,
        )
        character_picker.pack(side=tk.TOP, pady=(0, 10))
        character_picker.bind("<<ComboboxSelected>>", self._onCharacterPickerChange)

        load_map_button = tk.Button(parent, text="Load Map (Browse...)", command=self._loadMapBrowse)
        load_map_button.pack(side=tk.TOP, pady=5)

        open_editor_button = tk.Button(parent, text="Open Map Editor", command=self._openMapEditor)
        open_editor_button.pack(side=tk.TOP, pady=5)

        #delete once we have no need for direct testing or move to a separate part of GUI for testing purposes only
        # parse_CSV_button = tk.Button(parent, text = "(delete or move to a separate tab on the GUI) parse CSV", command = self.controller.parseCSV)
        # parse_CSV_button.pack(side=tk.TOP, pady=5)

        #delete once we have no need for direct testing or move to a separate part of GUI for testing purposes only
        # build_graph_button = tk.Button(parent, text = "(delete or move to a separate tab on the GUI) create edges", command = self.controller.buildGraph)
        # build_graph_button.pack(side=tk.TOP, pady=5)

        setup_agent_button = tk.Button(parent, text = "(delete or move to a separate tab on the GUI) setup agent", command = self.mockSetupAgentEnvironment)
        setup_agent_button.pack(side = tk.TOP, pady=5)

        #delete once we have no need for direct testing or move to a separate part of GUI for testing purposes only
        display_graph_button = tk.Button(parent, text="(delete or move to a separate tab on the GUI) used to visualise the graph", command=self._drawGraph)
        display_graph_button.pack(side=tk.TOP, pady=5)

        #delete once we have no need for direct testing or move to a separate part of GUI for testing purposes only
        connect_character_node_button = tk.Button(parent, text="(delete or move to a separate tab on the GUI) used to add character node to graph (node is d in just for testing purposes only)", command=self.controller.connectCharacterNode)
        connect_character_node_button.pack(side=tk.TOP, pady=5)
        #
        #delete once we have no need for direct testing or move to a separate part of GUI for testing purposes only
        delete_node_button = tk.Button(parent, text="(delete or move to a separate tab on the GUI) used to delete character node from graph (node is hardcoded in just for testing purposes only)", command=self.controller.deleteNode)
        delete_node_button.pack(side=tk.TOP, pady=5)

        # delete once we have no need for direct testing
        get_path = tk.Button(parent, text="(delete after done testing) used get the path to goal node", command=self.showPathToGoalNode)
        get_path.pack(side=tk.TOP, pady=5)

        #delete once we have no need for direct testing of the setup_agent function
        # setup_agent = tk.Button(parent, text="(delete after done testing) setup agent", command=self.controller.setupAgent)
        # setup_agent.pack(side=tk.TOP, pady=5)

        #include these once we finish main logic for agent + controller
        # tk.Button(parent, text="start controller", command=self.controller.start).pack(side = "right")
        # tk.Button(parent, text="set main node", command=self.infiniteLoop).pack(side = "right")
        # tk.Button(parent, text="set pathing node", command=self.controller.setPathingNode).pack(side = "right")
        # tk.Button(parent, text="draw path and nodes", command=self.controller.drawPathAndNodes).pack(side = "right")

        #uncomment this when you actually need info from maplestory - uncomment the other block of code in main.py that has the exact same comment
        time.sleep(5) #give minimap capture thread some time to start and possibly capture a minimap frame
        is_canvas_setup = False
        while not is_canvas_setup:
            with self.sharedInfo.minimap_frame_lock:
                if self.sharedInfo.minimap_frame is not None:
                    is_canvas_setup = True
                    self.minimap_canvas_height, self.minimap_canvas_width, _ = self.sharedInfo.minimap_frame.shape
                    self.minimap_canvas = tk.Canvas(parent, width= self.minimap_canvas_width,
                                                    height= self.minimap_canvas_height,
                                                    background='pink',
                                                    highlightthickness=0,
                                                    borderwidth=0,
                                                    relief = 'flat')
                    self.minimap_canvas.pack(side=tk.LEFT)
                else:
                    print("failed to setup GUI due to unavailable minimap image, sleeping GUI thread for 10 seconds before trying again.")
                    time.sleep(10)

    def drawBlueNodeOnCharacter(self):
        x, y  = self.controller.getCharacterCoords()
        node_type = "Blue Node"
        self.node_count += 1
        self._printToGUIConsole(f"Identifier: {self.node_count}, Type of Node: {node_type} | Character Position: ({x},{y})")
        self.minimap_canvas.create_oval(x - 2, y - 2, x + 2, y + 2, fill="lightblue", outline="lightblue", width=3, tags="node")
        self._drawNodeCount(x, y, text= self.node_count)

    def drawOrangeNodeOnCharacter(self):
        x, y = self.controller.getCharacterCoords()
        node_type = "Orange Node"
        self.node_count += 1
        self._printToGUIConsole(f"Identifier: {self.node_count}, Type of Node: {node_type} | Character Position: ({x},{y})")
        self.minimap_canvas.create_oval(x - 2, y - 2, x + 2, y + 2, fill="orange", outline="darkorange", width=3, tags = "node")
        self._drawNodeCount(x, y, text = self.node_count)

    #delete once you don't need a direct way to test this. This should only exist inside the PathGraph class
    def showPathToGoalNode(self):
        print("-----------------------------------------------")
        character_coords = self.controller.getCharacterCoords()
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
            self._printToGUIConsole(f"Failed to load map {path}: {result.failure()}")

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
        """Open the map editor Toplevel. If one is already open, raise it to the front."""
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
        self.minimap_canvas.delete("node")
        self.minimap_canvas.delete("node_tag")

    def _drawGraph(self):
        graph = self.controller.getGraph()
        node_map = self.controller.getNodeMap()
        node_positions = {node: node.minimap_position for node in node_map.values()}
        nx.draw(graph, pos = node_positions, with_labels=True, node_color='blue', node_size=10, font_size=10)
        plt.gca().invert_yaxis() #invert y-axis so origin is top-left
        plt.show()

    #draws minimap on GUI
    def _updateCanvas(self):
        with self.sharedInfo.minimap_frame_lock:
            if self.sharedInfo.minimap_frame is not None:

                # Image conversion pipeline: numpy array → PIL Image → Tkinter PhotoImage
                pil_minimap = Image.fromarray(self.sharedInfo.minimap_frame)
                minimap_img = ImageTk.PhotoImage(pil_minimap)

                # Prevent garbage collection by maintaining reference
                self.minimap_canvas.image = minimap_img
                # Canvas image management: create once, update thereafter
                if self.minimap_canvas_bg_img_id is None:
                    # Initial creation: place image at canvas center
                    self.minimap_canvas_bg_img_id = self.minimap_canvas.create_image(0,
                                                                                     0,
                                                                                     image=minimap_img,
                                                                                     anchor= tk.NW)
                else:
                    # Subsequent updates: modify existing image item (preserves layer order)
                    self.minimap_canvas.itemconfig(self.minimap_canvas_bg_img_id, image=minimap_img)

        # Schedule next frame update
        self.gui.after(10, self._updateCanvas)