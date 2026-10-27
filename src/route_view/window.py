"""The route view's Tk window and replay controls (route view plan task RV-7; spec 5.4, 5.5, 5.7).

`RouteViewWindow` draws one step of a `RunModel` (the enlarged minimap, the nodes, the route, the aim, the character and
the last moves), shows the model's side panel and "why" log, and has the replay bar. It reads the model and the files in
the run folder and nothing else; it holds no lock and no thread.

Focus (spec 5.7): the window never calls `focus_force`, `focus_set`, `lift`, `tkraise`, `grab_set` or `-topmost`, opens
no message box or dialog, and calls none of `resizable`, `overrideredirect`, `transient` or `attributes` (in any
spelling): once Tk has made its wrapper window, each of those makes Tk build a new wrapper, which silently drops the
no-activate style RV-8 sets on it (plan RV-7, "The window never calls ..."; `tests/route_view/test_view_window.py`
test 10 walks every `src/route_view` source for them). Errors go to the status line.

Where the plan is silent (each decided here, local and reversible):
- The title's mode part is `LIVE` while live; `ended: <reason>` (`ended: no reason` for an `end` without one) for a run
  with an `end` event; `REPLAY (from legs.jsonl)` for a legs-only run; `REPLAY (did not end)` for a steps folder
  without `end`. `set_mode` overrides it; `set_live` puts the default back (call `set_mode` after it).
- `show` reports a background that could not be read in `background_ok` (False), so RV-8's follower can retry at its
  next poll (PF81 m5); the image is read by the named method `_load_image(path)`, which RV-8's tests spy on.
- A failed read keeps the last good picture only; it does not fall through to `minimap.png` or the dark canvas (the
  plan: "keep the last good picture (or the dark canvas if there is none)"). A step with no frame to ask for (none
  logged yet, no `minimap.png`) shows `minimap.png` or the dark canvas and forgets the last good picture.
- The status line text for a read failure (and for "no steps logged") is cleared by the next `show` that succeeds, if
  nothing else has replaced it.
- Moving by hand (a button, the slider, `lap`/`leg` and Enter) stops a running playback; `show` itself does not.
- `1x` and `4x` pressed while the other is playing switch the speed; pressed at the last step they play nothing.
- A dark canvas with no `# minimap_size=` is the bounding box of the nodes and every logged position plus 10 px, at
  least 40 x 40 (spec 5.5).
"""

import tkinter as tk
from collections.abc import Callable
from pathlib import Path
from tkinter import font as tkfont

from PIL import Image, ImageTk

from src.route.step_log import SNAPSHOT
from src.route_view.model import DEFAULT_SCALE, FRAMES_DIR, SCALES, RunModel, StepView

# Copied, not imported (src.gui is forbidden, spec 5.8): map_editor.py:28, :40, :1029-1030, :1050-1056
NODE_R = 3
ORANGE_R = 2
RING_R = 5
LABEL_FG = "white"
BLUE_FILL, BLUE_LINE = "lightblue", "blue"
ORANGE_FILL, ORANGE_LINE = "orange", "darkorange"
RING = "yellow"
ROUTE, CHARACTER, AIM, RECENT, DARK = "cyan", "red", "magenta", "gray60", "#202020"

UNITS = ("event", "move", "leg")
PANEL_WIDTH_PX = 450  # the side panel's fixed width: long lines wrap, the window does not jump from step to step
WHY_LINES = 10
SLIDER_PX = 380
NO_STEPS = "no steps logged"
_MIN_SIZE = 40  # a dark canvas is at least this many minimap pixels each way
_MARGIN = 10  # ... and this far past the nodes and logged positions
_CURRENT = "current"

Pos = tuple[int, int]


class RouteViewWindow:
    shown_background: str  # "frames/<file>" | "minimap.png" | "dark": what the canvas shows now
    shown_image: Image.Image | None  # that picture at the minimap's own size (RGB), None for the dark canvas
    shown_photo: ImageTk.PhotoImage | None  # the scaled picture the canvas holds (the same object while a read fails)
    background_ok: bool  # False when the last `show` asked for a picture file and could not read it
    index: int | None  # the step shown; None with no steps

    def __init__(self, root: tk.Tk, folder: Path, model: RunModel | None, *, live: bool) -> None:
        self._root = root
        self.folder = Path(folder)
        self.model = model
        self.live = live
        self.scale = DEFAULT_SCALE
        self.index = None
        self.shown_background = "dark"
        self.shown_image = None
        self.shown_photo = None
        self.background_ok = True
        self._view: StepView | None = None
        self._background: str | None = None  # the `background` of the last show, kept for a redraw at a new scale
        self._mode_override: str | None = None
        self._extra_warnings: list[str] = []
        self._read_error: str | None = None  # the status text a failed read set; cleared by the next good read
        self._why_shown = 0
        self._why_last = ""
        self._speed: float | None = None  # the playback speed, None when not playing
        self._play_gen = 0  # a callback whose generation is not this one does nothing
        self._extent = [0, 0]  # the largest x and y of the nodes and logged positions seen (a dark canvas's size)
        self._extent_events = 0
        self._photo_source: Image.Image | None = None  # the picture `shown_photo` was made from, and at what scale
        self._photo_scale = 0
        self._build()
        self._reset_model_state()
        self._redraw()

    # -----------------------------------------------------------------------------------------------------------
    # layout

    def _build(self) -> None:
        root = self._root
        self._status = tk.StringVar(master=root, value="")
        self.status_label = tk.Label(root, textvariable=self._status, anchor="w", justify="left")
        self.status_label.pack(side="bottom", fill="x")
        self.bar = tk.Frame(root)
        if not self.live:
            self.bar.pack(side="bottom", fill="x")
        self.warn_label = tk.Label(root, text="", fg="red", anchor="w", justify="left")
        self.main = tk.Frame(root)
        self.main.pack(side="top", fill="x")
        self.canvas = tk.Canvas(self.main, width=_MIN_SIZE, height=_MIN_SIZE, bg=DARK, highlightthickness=0, bd=0)
        self.canvas.pack(side="left")
        self.panel_box = tk.Frame(self.main, width=PANEL_WIDTH_PX, height=1)
        self.panel_box.pack_propagate(False)
        self.panel_box.pack(side="left", fill="y")
        self.panel_label = tk.Label(self.panel_box, text="", anchor="nw", justify="left", font="TkFixedFont",
                                    wraplength=PANEL_WIDTH_PX - 16, padx=8, pady=4)
        self.panel_label.pack(side="top", fill="both", expand=True)
        self._label_font = tkfont.nametofont("TkDefaultFont", root=root).copy()
        self._label_font.configure(size=8)

        self.why_frame = tk.Frame(root)
        self.why_frame.pack(side="top", fill="both", expand=True)
        self.why_scroll = tk.Scrollbar(self.why_frame, orient="vertical")
        self.why_text = tk.Text(self.why_frame, height=WHY_LINES, wrap="none", state="disabled", font="TkFixedFont",
                                yscrollcommand=self.why_scroll.set)
        self.why_scroll.configure(command=self.why_text.yview)
        self.why_scroll.pack(side="right", fill="y")
        self.why_text.pack(side="left", fill="both", expand=True)
        self.why_text.tag_configure(_CURRENT, background="#b5d5ff")
        self._build_bar()

    def _build_bar(self) -> None:
        bar = self.bar

        def button(text: str, command: Callable[[], None]) -> tk.Button:
            made = tk.Button(bar, text=text, command=command, padx=4, pady=0)
            made.pack(side="left")
            return made

        self.btn_first = button("|<", self._first)
        self.btn_prev = button("<", lambda: self._step(-1))
        self.btn_next = button(">", lambda: self._step(1))
        self.btn_last = button(">|", self._last)
        self.unit_var = tk.StringVar(master=self._root, value=UNITS[0])
        self.unit_menu = tk.OptionMenu(bar, self.unit_var, *UNITS)
        self.unit_menu.pack(side="left")
        self.slider = tk.Scale(bar, from_=0, to=0, orient="horizontal", showvalue=False, length=SLIDER_PX,
                               command=self.on_slider)
        self.slider.pack(side="left", padx=4)
        self.step_label = tk.Label(bar, text="")
        self.step_label.pack(side="left")
        tk.Label(bar, text=" play").pack(side="left")
        self.play_buttons = {speed: self._play_button(speed) for speed in (1, 4)}
        tk.Label(bar, text=" lap").pack(side="left")
        self.lap_var = tk.StringVar(master=self._root, value="")
        self.lap_entry = tk.Entry(bar, textvariable=self.lap_var, width=4)
        self.lap_entry.pack(side="left")
        tk.Label(bar, text="leg").pack(side="left")
        self.leg_var = tk.StringVar(master=self._root, value="")
        self.leg_entry = tk.Entry(bar, textvariable=self.leg_var, width=4)
        self.leg_entry.pack(side="left")
        for entry in (self.lap_entry, self.leg_entry):
            entry.bind("<Return>", self.go_to_leg)
        tk.Label(bar, text=" zoom").pack(side="left")
        self.scale_buttons = {s: self._scale_button(s) for s in SCALES}

    def _play_button(self, speed: int) -> tk.Button:
        made = tk.Button(self.bar, text=f"{speed}x", padx=4, pady=0, command=lambda: self.toggle_play(float(speed)))
        made.pack(side="left")
        return made

    def _scale_button(self, s: int) -> tk.Button:
        made = tk.Button(self.bar, text=f"{s}x", padx=4, pady=0, command=lambda: self.set_scale(s))
        made.pack(side="left")
        return made

    # -----------------------------------------------------------------------------------------------------------
    # the public interface (RV-8 builds on it)

    def set_model(self, model: RunModel) -> None:
        """RV-8: the live header arrived, so there is a model now."""
        self._stop_play()
        self.model = model
        self._reset_model_state()
        self._redraw()

    def show(self, k: int, *, background: str | None = None) -> None:
        """Draw step `k`. `background` (live, RV-8) is a file in `frames/` to use instead of the step's own frame."""
        model = self.model
        self._background = background
        self.sync_why()
        view: StepView | None = None
        if model is not None and model.events:
            view = model.step(k)
            self.index = k
        else:
            self.index = None
        self._view = view
        self._update_background(view, background)
        self._draw(view)
        self._update_panel(view)
        self._update_controls(view)
        self._update_title()

    def sync_why(self) -> None:
        """Add the model's why lines this window has not shown. A repeat the model collapsed into its last line
        rewrites that one line (found by comparing its text). The log is never rebuilt."""
        model = self.model
        if model is None:
            return
        why = model.why
        shown = self._why_shown
        rewrite = shown > 0 and self._why_last != why[shown - 1]
        lines = why[shown - 1:] if rewrite else why[shown:]
        if not lines:
            return
        text = self.why_text
        text.configure(state="normal")
        try:
            if rewrite:
                text.delete(f"{shown}.0", "end-1c")
            text.insert("end-1c", ("\n" if shown and not rewrite else "") + "\n".join(lines))
        finally:
            text.configure(state="disabled")
        self._why_shown, self._why_last = len(why), why[-1]

    def set_scale(self, s: int) -> None:
        if s not in SCALES:
            raise ValueError(f"scale must be one of {SCALES}, not {s!r}")
        self.scale = s
        self._redraw_here()

    def set_live(self, live: bool) -> None:
        """Live: the replay bar is hidden (spec 5.4 "Replay only"). Puts the title's default mode back."""
        self.live = live
        self._stop_play()
        self._mode_override = None
        if live:
            self.bar.pack_forget()
        elif self.bar.winfo_manager() != "pack":
            self.bar.pack(side="bottom", fill="x", after=self.status_label)
        self._update_title()

    def set_mode(self, text: str) -> None:
        self._mode_override = text
        self._update_title()

    def set_status(self, text: str) -> None:
        self._status.set(text)

    def add_warning(self, text: str) -> None:
        self._extra_warnings.append(text)
        self._sync_warnings()

    # -----------------------------------------------------------------------------------------------------------
    # the replay controls

    def _first(self) -> None:
        self._go(0)

    def _last(self) -> None:
        if self.model is not None:
            self._go(len(self.model.events) - 1)

    def _step(self, delta: int) -> None:
        if self.model is None or self.index is None:
            return
        self._go(self.model.seek(self.index, self.unit_var.get(), delta))

    def _go(self, k: int) -> None:
        """A move by hand: stops a running playback and shows step `k` (nothing with no steps)."""
        if self.model is None or self.index is None or not 0 <= k < len(self.model.events):
            return
        self._stop_play()
        if k != self.index:
            self.show(k)

    def on_slider(self, value: str) -> None:
        """The slider's command (Tk passes the new value as text). Tk may also call it for the window's own
        `slider.set(index)` in `show` (not seen in a withdrawn window; a mapped one is untested here): that value is the
        shown step, so it is ignored and does not stop a playback."""
        try:
            k = int(float(value))
        except ValueError:
            return
        if self.index is None or k == self.index:
            return
        self._go(k)

    def go_to_leg(self, _event: object = None) -> None:
        """Enter in the `lap` or `leg` entry: jump to that leg's first event."""
        try:
            lap, leg = int(self.lap_var.get()), int(self.leg_var.get())
        except ValueError:
            self.set_status("lap and leg must be whole numbers")
            return
        k = self.model.find_leg(lap, leg) if self.model is not None else None
        if k is None:
            self.set_status(f"no lap {lap} leg {leg} in this run")
            return
        self._go(k)

    def toggle_play(self, speed: float) -> None:
        """Play from the shown step at `speed` on the logged times; pressed again at the same speed, stop."""
        if self._speed == speed:
            self._stop_play()
            return
        self._stop_play()
        if self.model is None or self.index is None or self.index + 1 >= len(self.model.events):
            return
        self._speed = speed
        self._schedule_play()
        for s, made in self.play_buttons.items():
            made.configure(relief="sunken" if float(s) == speed else "raised")

    def _schedule_play(self) -> None:
        model, index, speed = self.model, self.index, self._speed
        if model is None or index is None or speed is None:
            return
        gen = self._play_gen
        self._root.after(model.play_delay_ms(index, speed), lambda: self._play_tick(gen))

    def _play_tick(self, gen: int) -> None:
        model, index = self.model, self.index
        if gen != self._play_gen or self._speed is None or model is None or index is None:
            return
        nxt = index + 1
        if nxt >= len(model.events):
            self._stop_play()
            return
        self.show(nxt)
        if nxt + 1 < len(model.events):
            self._schedule_play()
        else:
            self._stop_play()

    def _stop_play(self) -> None:
        self._speed = None
        self._play_gen += 1  # a callback already scheduled finds a new generation and does nothing
        for made in self.play_buttons.values():
            made.configure(relief="raised")

    # -----------------------------------------------------------------------------------------------------------
    # state

    def _reset_model_state(self) -> None:
        """The model changed (or is first set): the why log starts empty, the warnings and the extent are recomputed."""
        self.index = None
        self._view = None
        self._why_shown, self._why_last = 0, ""
        self.why_text.configure(state="normal")
        self.why_text.delete("1.0", "end")
        self.why_text.configure(state="disabled")
        self._extent = [0, 0]
        self._extent_events = 0
        if self.model is not None:
            for mark in self.model.nodes:
                self._grow_extent(mark.pos)
        self._sync_warnings()

    def _redraw(self) -> None:
        """Show the newest step, or the empty view when there is none."""
        events = self.model.events if self.model is not None else []
        self.show(len(events) - 1 if events else 0, background=self._background)

    def _redraw_here(self) -> None:
        self.show(self.index if self.index is not None else 0, background=self._background)

    def _sync_warnings(self) -> None:
        lines = [*(self.model.warnings if self.model is not None else ()), *self._extra_warnings]
        if lines:
            self.warn_label.configure(text="\n".join(lines))
            if self.warn_label.winfo_manager() != "pack":
                self.warn_label.pack(side="top", fill="x", before=self.main)
        else:
            self.warn_label.configure(text="")
            self.warn_label.pack_forget()

    def _mode(self) -> str:
        if self._mode_override is not None:
            return self._mode_override
        model = self.model
        if self.live:
            return "LIVE"
        if model is None:
            return "REPLAY"
        if model.origin == "legs":
            return "REPLAY (from legs.jsonl)"
        if model.ended:
            return f"ended: {model.end_reason or 'no reason'}"
        return "REPLAY (did not end)"

    def _update_title(self) -> None:
        view = self._view
        parts = ["Route view", self._mode(), str(self.folder)]
        if view is None:
            parts.append("no steps")
        else:
            if view.lap is not None and view.leg is not None:
                parts.append(f"lap {view.lap}")
                label = f" -> {view.target.label}" if view.target is not None else ""
                parts.append(f"leg {view.leg}{label}")
            parts.append(view.state)
        self._root.title(" · ".join(parts))

    def _update_panel(self, view: StepView | None) -> None:
        self.panel_label.configure(text="\n".join(view.panel) if view is not None else "")
        self._mark_current(view)

    def _mark_current(self, view: StepView | None) -> None:
        text = self.why_text
        text.tag_remove(_CURRENT, "1.0", "end")
        if view is None:
            return
        line = view.why_line + 1  # Tk's text lines count from 1
        text.tag_add(_CURRENT, f"{line}.0", f"{line}.end")
        text.see(f"{line}.0")

    def _update_controls(self, view: StepView | None) -> None:
        model = self.model
        count = len(model.events) if model is not None else 0
        self.slider.configure(to=max(0, count - 1))
        self.slider.set(self.index if self.index is not None else 0)
        self.step_label.configure(text=f"{self.index + 1} / {count}" if self.index is not None else "0 / 0")
        if view is not None:
            if view.lap is not None and view.leg is not None:
                self.lap_var.set(str(view.lap))
                self.leg_var.set(str(view.leg))
            if self._status.get() == NO_STEPS:
                self._status.set("")
        elif self._status.get() in ("", NO_STEPS):
            self._status.set(NO_STEPS)

    # -----------------------------------------------------------------------------------------------------------
    # the picture

    def _load_image(self, path: Path) -> Image.Image:
        """Read one picture file whole (`load()` decodes it, so a half-written PNG fails here) as RGB. RV-8's tests
        spy on this method to count the decodes. Raises OSError (Pillow 12.2.0 raises `OSError("image file is
        truncated")`, `UnidentifiedImageError`, a subclass of OSError, or `FileNotFoundError`) or ValueError."""
        with Image.open(path) as image:
            image.load()
            return image.convert("RGB")

    def _wanted(self, view: StepView | None, background: str | None) -> tuple[str, Path] | None:
        """The picture file spec 5.5 asks for, best first: the given or step's frame, else the snapshot."""
        name = background or (view.frame if view is not None else None)
        if name:
            return f"{FRAMES_DIR}/{name}", self.folder / FRAMES_DIR / name
        snapshot = self.folder / SNAPSHOT
        return (SNAPSHOT, snapshot) if snapshot.exists() else None

    def _update_background(self, view: StepView | None, background: str | None) -> None:
        wanted = self._wanted(view, background)
        self.background_ok = True
        if wanted is None:
            self._use_picture("dark", None)
            return
        label, path = wanted
        try:
            image = self._load_image(path)
        except (OSError, ValueError) as err:
            self.background_ok = False
            self._read_error = f"cannot read {path.name}: {err}; showing the last good picture"
            self._status.set(self._read_error)
            return  # the last good picture (or the dark canvas) stays
        if self._read_error is not None and self._status.get() == self._read_error:
            self._status.set("")
        self._read_error = None
        self._use_picture(label, image)

    def _use_picture(self, label: str, image: Image.Image | None) -> None:
        self.shown_background, self.shown_image = label, image
        if image is None:
            self.shown_photo = None
            self._photo_scale = 0

    def _photo(self) -> ImageTk.PhotoImage | None:
        """The shown picture at the current scale; the same PhotoImage object while nothing changed."""
        image = self.shown_image
        if image is None:
            return None
        if self.shown_photo is None or self._photo_source is not image or self._photo_scale != self.scale:
            w, h = image.size
            scaled = image.resize((w * self.scale, h * self.scale), Image.Resampling.NEAREST)
            self.shown_photo = ImageTk.PhotoImage(scaled, master=self._root)
            self._photo_source, self._photo_scale = image, self.scale
        return self.shown_photo

    def _grow_extent(self, pos: Pos) -> None:
        self._extent[0] = max(self._extent[0], pos[0])
        self._extent[1] = max(self._extent[1], pos[1])

    def _base_size(self) -> Pos:
        """The canvas size in minimap pixels: the picture's, else the map's `# minimap_size=`, else the bounding box."""
        if self.shown_image is not None:
            return self.shown_image.size
        model = self.model
        if model is not None and model.minimap_size is not None:
            return model.minimap_size
        if model is not None:
            for event in model.events[self._extent_events:]:
                for value in event.data.values():
                    if isinstance(value, tuple) and len(value) == 2 and all(isinstance(c, int) for c in value):
                        self._grow_extent((value[0], value[1]))
            self._extent_events = len(model.events)
        return max(_MIN_SIZE, self._extent[0] + _MARGIN), max(_MIN_SIZE, self._extent[1] + _MARGIN)

    # -----------------------------------------------------------------------------------------------------------
    # drawing

    def _draw(self, view: StepView | None) -> None:
        s = self.scale
        canvas = self.canvas
        w, h = self._base_size()
        canvas.configure(width=w * s, height=h * s)
        canvas.delete("all")
        photo = self._photo()
        if photo is not None:
            canvas.create_image(0, 0, image=photo, anchor="nw", tags="bg")
        if view is not None:
            for a, b in view.recent:
                canvas.create_line(a[0] * s, a[1] * s, b[0] * s, b[1] * s, fill=RECENT, dash=(4, 3), width=2,
                                   tags="recent")
            if view.route:
                points = [c * s for p in view.route for c in p]
                canvas.create_line(*(points if len(view.route) > 1 else points * 2), fill=ROUTE, width=2, tags="route")
        self._draw_nodes()
        if view is None:
            return
        if view.target is not None:
            x, y, r = view.target.pos[0] * s, view.target.pos[1] * s, RING_R * s
            canvas.create_oval(x - r, y - r, x + r, y + r, outline=RING, width=2, tags="target")
        for hop in view.hops:
            canvas.create_text(hop.landing[0] * s, hop.landing[1] * s, text=str(hop.n), fill=ROUTE, anchor="s",
                               font=self._label_font, tags="hopnum")
        if view.aim is not None:
            x, y, r = view.aim[0] * s, view.aim[1] * s, 2 * s
            for coords in ((x - r, y - r, x + r, y + r), (x - r, y + r, x + r, y - r)):
                canvas.create_line(*coords, fill=AIM, width=2, tags="aim")
        if view.character is not None:
            x, y, r = view.character[0] * s, view.character[1] * s, 2 * s
            canvas.create_oval(x - r, y - r, x + r, y + r, fill=CHARACTER, outline="white", tags="character")

    def _draw_nodes(self) -> None:
        if self.model is None:
            return
        s = self.scale
        for mark in self.model.nodes:
            x, y = mark.pos[0] * s, mark.pos[1] * s
            r = (NODE_R if mark.blue else ORANGE_R) * s
            self.canvas.create_oval(x - r, y - r, x + r, y + r, fill=BLUE_FILL if mark.blue else ORANGE_FILL,
                                    outline=BLUE_LINE if mark.blue else ORANGE_LINE, width=1, tags="node")
            if mark.blue:
                self.canvas.create_text(x + r + 2, y, text=mark.label, anchor="w", fill=LABEL_FG,
                                        font=self._label_font, tags="label")
