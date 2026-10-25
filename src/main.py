import threading
from src.vision.vision import capture_and_process_minimap #using to V2
from src.gui.gui import MapleGUI
from src.controller.controller import Controller


class SharedInfo:
    def __init__(self):
        self.minimap_frame_lock = threading.Lock()
        self.minimap_frame = None #note: numpy array storing data in RGB format (vision converts BGR to RGB before storing it, src/vision/vision.py:81).

if __name__ == "__main__":

    sharedInfo = SharedInfo()

    # uncomment this when you actually need info from maplestory - uncomment the other block of code in GUI that has the exact same comment
    minimap_capture_worker = threading.Thread(target=capture_and_process_minimap,args= (sharedInfo,) ,daemon=True)
    minimap_capture_worker.start()

    controller = Controller(sharedInfo = sharedInfo, CSV = None, characterClass = None)
    gui = MapleGUI(controller = controller , sharedInfo=sharedInfo)
    gui.start() #this starts another thread for the GUI, nothing after this line will run.
