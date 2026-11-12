"""What the windows show while the capture has lost the minimap (VIS162-FIX): the message in place of the frozen picture.

The vision thread raises `SharedInfo.minimap_lost` when a search finds no minimap, after it had published one, and
lowers it with the next picture (`src/vision/minimap_follow.py`). The flag is read with `getattr`: the holders the
capture also writes into (`runtime.SharedFrame`, the recorder's, test fakes) have none, and then nothing is lost.
"""

LOST_TEXT = "Minimap lost: searching for it"
LOST_TAG = "minimap_lost"


def read_minimap(shared):
    """(frame, lost), read together under `minimap_frame_lock`: the reference only, no Tk call while the lock is held."""
    with shared.minimap_frame_lock:
        return shared.minimap_frame, bool(getattr(shared, "minimap_lost", False))


def show_lost(canvas, width, height):
    """Cover the canvas (a minimap picture with node marks) with the message; drawn once, raised over what is drawn later."""
    if not canvas.find_withtag(LOST_TAG):
        canvas.create_rectangle(0, 0, width, height, fill="#202020", outline="", tags=LOST_TAG)
        canvas.create_text(width // 2, height // 2, text=LOST_TEXT, fill="white", width=max(width - 8, 20),
                           justify="center", tags=LOST_TAG)
    canvas.tag_raise(LOST_TAG)


def clear_lost(canvas):
    canvas.delete(LOST_TAG)
