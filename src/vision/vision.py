import bettercam
import cv2
import numpy as np
from pathlib import Path
import src.vision.vision_helper_lib as vision
import src.vision.minimap_follow as follow
import src.vision.frame_wait as frame_wait
import time
from collections import namedtuple
# Constants
MINIMAP_POSITION_RECALC_S = 22.0  # the minimap is searched for again after this many seconds (WALKFIX Task 8, QB283 2c)
_monotonic = time.monotonic  # the clock of that re-find; a seam, so the tests fake it and not the global time.monotonic
THREAD_SLEEP_DURATION_S = 0.0  # pause between pictures: none (Hong QB283 2b, lifts H80's 20 ms); Task 10B's key-timing check sets the smallest pause that keeps the keys on time, if one is needed
FOREGROUND_CHECK_SLEEP_S = 5
MINIMAP_DETECTION_RETRY_DELAY_S = 1
WINDOW_HANDLE_RETRY_DELAY_S = 3
MINIMAP_TEMPLATE_MATCH_CONFIDENCE_THRESHOLD = 0.95

MatchedTemplateInfo = namedtuple("MatchedTemplateInfo", ["x", "y", "conf_score"])

def _grab_stamped(camera, region):
    """`cameraGrab`'s retry loop over `frame_wait.grab_new_frame_stamped`: the next picture of `region` and the time
    Windows handed it over (QB283 2d). The whole-screen search keeps `vision.cameraGrab`, which publishes nothing."""
    while True:
        got = frame_wait.grab_new_frame_stamped(camera, region)
        if got is not None:
            return got

#could convert to a class if it makes more sense later on.
def capture_and_process_minimap(sharedInfo):
    # Get the project root directory
    project_root = Path(__file__).parent.parent.parent

    # nested dictionary for accessing minimap images easily
    corners = ["left_top", "right_top", "right_bot", "left_bot"]
    minimap_paths = {corner: {
        "border": project_root / "assets" / "brds" / f"{corner}_grayscale.png",
        "mask": project_root / "assets" / "brds" / f"{corner}_mask.png"
    } for corner in corners}

    # instantiate camera and find maplestory pixel location
    output_idx = 1
    print(f"target monitor for screen grab is currently monitor {output_idx}. main monitor = 0.")
    camera = bettercam.create(device_idx=0, output_idx=output_idx, output_color="BGR")

    minimap_border_templates = {}
    minimap_border_masks = {}
    for corner in corners:
        minimap_border_templates[corner] = cv2.imread(str(minimap_paths[corner]["border"]), cv2.IMREAD_GRAYSCALE)
        minimap_border_masks[corner] = cv2.imread(str(minimap_paths[corner]["mask"]), cv2.IMREAD_GRAYSCALE)

    print("Beginning minimap capture process...")
    tracker = follow.LostTracker(sharedInfo)
    while True:
        entire_monitor_frame = vision.cameraGrab(camera)
        screen_height, screen_width = entire_monitor_frame.shape[:2]
        minimap_gray = cv2.cvtColor(entire_monitor_frame, cv2.COLOR_BGR2GRAY)

        #try to find minimap from frame of entire monitor
        matched_template_loc_and_conf_score = {}
        for corner in corners:
            matched_loc = cv2.matchTemplate(minimap_gray, minimap_border_templates[corner], cv2.TM_CCORR_NORMED,
                                              mask=minimap_border_masks[corner])
            # masked TM_CCORR_NORMED can emit inf/nan and >1 scores where the masked image patch
            # has ~zero energy (e.g. pure-black screen regions); scrub them so a real match (<=1) wins.
            matched_loc[~np.isfinite(matched_loc) | (matched_loc > 1.0)] = -1.0
            _, max_val, _, max_loc = cv2.minMaxLoc(matched_loc)
            x,y = max_loc
            matched_template_loc_and_conf_score[corner] = MatchedTemplateInfo(x = x, y = y, conf_score=max_val)

        #retry from beginning if computed template location for minimap borders is not valid (could be due to invalid minimap image)
        if not vision.isMatchedTemplateLocValid(matched_template_loc_and_conf_score, corners, MINIMAP_TEMPLATE_MATCH_CONFIDENCE_THRESHOLD, verbose=False):
            tracker.search_failed()  # one console line and the lost flag, not a line per failed search; the last frame stays in sharedInfo (VIS162-FIX)
            continue

        minimap_border_locations = vision.findInGameMinimapBorder(matched_template_loc_and_conf_score, minimap_border_templates, corners)
        top_left, bottom_right = minimap_border_locations["left_top"], minimap_border_locations["right_bot"]
        lock = follow.make_lock(minimap_gray, matched_template_loc_and_conf_score, minimap_border_templates,
                                minimap_border_masks, corners, (*top_left, *bottom_right))
        minimap_valid, locked_at = True, _monotonic()

        while minimap_valid and _monotonic() - locked_at < MINIMAP_POSITION_RECALC_S:
            if not vision.isProposedMinimapRegionValid(lock.box, screen_width, screen_height):
                break
            box_frame, frame_ns = _grab_stamped(camera, lock.box)  # the minimap and its four corner templates (VIS162-FIX), with the time Windows handed it over
            if not follow.still_there(lock, cv2.cvtColor(box_frame, cv2.COLOR_BGR2GRAY)):
                break  # moved, covered or gone: publish nothing from this grab and search the whole screen at once
            crop_x1, crop_y1, crop_x2, crop_y2 = lock.crop
            minimap_frame = box_frame[crop_y1:crop_y2, crop_x1:crop_x2]

            if vision.isMinimapBlack(minimap_frame):
                print("Minimap frame is detected to be black. Retrying minimap detection...")
                minimap_valid = False
                time.sleep(MINIMAP_DETECTION_RETRY_DELAY_S)
                break

            tracker.publish(cv2.cvtColor(minimap_frame, cv2.COLOR_BGR2RGB), frame_ns)  # the one writer of minimap_frame and minimap_lost, under the lock
            time.sleep(THREAD_SLEEP_DURATION_S)  # pause between pictures: none (QB283 2b, the setting); cameraGrab itself waits in Windows for a new image (H77)