import sys
import threading
from src.vision.vision import capture_and_process_minimap #using to V2
from src.gui.gui import MapleGUI
from src.controller.controller import Controller
from src.route import runtime


class SharedInfo:
    def __init__(self):
        self.minimap_frame_lock = threading.Lock()
        self.minimap_frame = None #note: numpy array storing data in RGB format (vision converts BGR to RGB before storing it, `LostTracker.publish`, src/vision/minimap_follow.py).
        self.minimap_lost = False #note: True while the capture has lost a minimap it had found (VIS162-FIX). Written with minimap_frame under minimap_frame_lock, by the vision thread only; minimap_frame keeps its last picture meanwhile, so the walker reads "no new frame".

def main():
    # MapleStory runs as administrator, so F12 only stops a walk if Helix does too (RT-FIX-1, H82): an unelevated start
    # runs this command again elevated, one Windows prompt, and this copy ends.
    elevation = runtime.ensure_elevated()
    if elevation is runtime.Elevation.RELAUNCHED:
        print("Helix is starting again as administrator in a new window; this one has nothing more to do.")
        return
    notice = None if elevation is runtime.Elevation.ELEVATED else runtime.NOT_ELEVATED_TEXT  # declined, or opted out
    if notice is not None:
        print(f"warning: {notice}", file=sys.stderr)

    sharedInfo = SharedInfo()

    # the capture thread: the main window does not wait for it, it shows the minimap once the first frame arrives
    minimap_capture_worker = threading.Thread(target=capture_and_process_minimap,args= (sharedInfo,) ,daemon=True)
    minimap_capture_worker.start()

    controller = Controller(sharedInfo = sharedInfo, CSV = None, characterClass = None)
    gui = MapleGUI(controller = controller , sharedInfo=sharedInfo)
    gui.start(startup_notice = notice) #this starts another thread for the GUI, nothing after this line will run.


if __name__ == "__main__":
    main()
