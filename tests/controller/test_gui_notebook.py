"""The main window's Notebook: the Setup tab (today's window, moved as it was) and the Route tab's frame (spec 5.1, 5.7,
contract C4), and the Windows focus record the app-focus stop will rely on (spec 9).

Same real-Tk-root setup as test_gui_startup.py (skipped where Tk cannot open a display; root withdrawn, no mainloop).
No Route tab is built here (`route_tab_factory=None`) unless a test passes a stub: `RouteTab` is the default from RT-7 on,
and these tests are about the window around it."""
import sys
import tkinter as tk
from types import SimpleNamespace

import pytest

import test_gui_startup

make_gui = test_gui_startup.make_gui  # the fixture, shared (no conftest.py under tests/)

WM_SETFOCUS = 0x0007  # Microsoft Learn: WM_SETFOCUS, "sent to a window after it has gained the keyboard focus"
WM_KILLFOCUS = 0x0008  # WM_KILLFOCUS, "sent to a window immediately before it loses the keyboard focus"

# The Setup tab's buttons, in the order they are packed: today's window (src/gui/gui.py before this task, :155-215).
SETUP_BUTTON_TEXTS = [
    "draw blue node on character",
    "draw orange node on character",
    "clear console",
    "clear drawn nodes on minimap",
    "Load Map (Browse...)",
    "Open Map Editor",
    "(delete or move to a separate tab on the GUI) setup agent",
    "(delete or move to a separate tab on the GUI) used to visualise the graph",
    "(delete or move to a separate tab on the GUI) used to add character node to graph "
    "(node is d in just for testing purposes only)",
    "(delete or move to a separate tab on the GUI) used to delete character node from graph "
    "(node is hardcoded in just for testing purposes only)",
    "(delete after done testing) used get the path to goal node",
]


def _is_under(widget, ancestor) -> bool:
    return str(widget).startswith(str(ancestor) + ".")


@pytest.mark.skipif(sys.platform != "win32", reason="sends Windows' own focus messages to Tk's window")
def test_focus_record_follows_windows_focus_messages(make_gui, monkeypatch):
    """Tk's real Windows path, in-process: WM_SETFOCUS and WM_KILLFOCUS sent to one of the app's own widgets (the
    Setup tab's console) become <FocusIn>/<FocusOut>, and `focus_get()` (Tk's own record, no Windows call) follows
    them. Nothing is shown, no focus moves on screen, no key is sent and no other window is addressed. A generated
    <FocusIn> cannot replace this: it fires the binding but leaves the record unchanged. Not covered: that Windows
    sends WM_KILLFOCUS when another program takes the keyboard (Hong's check by hand, spec 9)."""
    import ctypes
    from ctypes import wintypes

    gui, _, _ = make_gui(route_tab_factory=None)
    root = gui.gui
    monkeypatch.delattr(root, "after")  # make_gui's recorder would swallow after_idle: use Tk's own
    print("Tk patch level:", root.tk.call("info", "patchlevel"))

    reread = []

    def schedule_reread(event=None):
        root.after_idle(lambda: reread.append(root.focus_get()))

    root.bind("<FocusIn>", schedule_reread, add="+")
    root.bind("<FocusOut>", schedule_reread, add="+")
    send_message = ctypes.WinDLL("user32").SendMessageW
    send_message.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    send_message.restype = wintypes.LPARAM
    hwnd = gui.gui_console.winfo_id()

    send_message(hwnd, WM_SETFOCUS, 0, 0)
    root.update()
    assert root.focus_get() is not None
    assert reread and reread[-1] is not None

    reread.clear()
    send_message(hwnd, WM_KILLFOCUS, 0, 0)
    root.update()
    assert root.focus_get() is None
    assert reread and reread[-1] is None
    assert root.state() == "withdrawn"


def test_two_tabs_setup_first(make_gui, monkeypatch):
    sizes = []

    def record_geometry(root, new_geometry=None):  # a withdrawn root reads back 1x1+0+0: watch the call instead
        sizes.append(new_geometry)
        return tk.Wm.wm_geometry(root, new_geometry)

    monkeypatch.setattr(tk.Tk, "geometry", record_geometry)
    gui, _, _ = make_gui(route_tab_factory=None)

    tabs = gui.notebook.tabs()
    assert [gui.notebook.tab(tab, "text") for tab in tabs] == ["Setup", "Route"]
    assert tabs == (str(gui.setup_tab), str(gui.route_tab_frame))
    for widget in (gui.gui_console, gui.draw_blue_node_button, gui.character_picker, gui.minimap_status_label):
        assert _is_under(widget, gui.setup_tab)
    assert sizes == ["1120x1000"]
    assert gui.isSetupTabShown()
    assert gui.route_tab is None and gui.route_tab_frame.winfo_children() == []


def test_setup_widgets_unchanged(make_gui):
    gui, _, _ = make_gui(route_tab_factory=None)

    packed = gui.scrollable_frame.pack_slaves()
    assert [w.cget("text") for w in packed if w.winfo_class() == "Button"] == SETUP_BUTTON_TEXTS
    assert all(_is_under(w, gui.setup_tab) for w in packed)
    assert gui.scrollable_frame.winfo_parent() != str(gui.gui)  # no longer straight in the root


def test_close_handler_is_installed(make_gui, monkeypatch):
    gui, _, _ = make_gui(route_tab_factory=None)
    destroyed = []
    real_destroy = gui.gui.destroy
    monkeypatch.setattr(gui.gui, "destroy", lambda: destroyed.append(True))

    name = gui.gui.protocol("WM_DELETE_WINDOW")
    assert name  # set: Tk's own default would be an empty string
    gui.gui.tk.call(name)  # what the window manager's close button runs

    assert destroyed == [True]
    monkeypatch.setattr(gui.gui, "destroy", real_destroy)  # make_gui's teardown destroys the real root


def test_close_hands_destroy_to_the_route_tab(make_gui):
    built, closed = [], []
    stub = SimpleNamespace(onAppClose=closed.append)

    def factory(parent, owner):
        built.append((parent, owner))
        return stub

    gui, _, _ = make_gui(route_tab_factory=factory)
    assert built == [(gui.route_tab_frame, gui)] and gui.route_tab is stub

    gui._onClose()

    assert closed == [gui.gui.destroy]
