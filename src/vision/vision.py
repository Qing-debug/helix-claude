import bettercam
import cv2
import numpy as np
from pathlib import Path
import src.vision.vision_helper_lib as vision
import time
from collections import namedtuple
# Constants
MINIMAP_POSITION_RECALC_INTERVAL = 1000
FRAME_DELAY_MS = 10 # main determinant in while loop execution speed
THREAD_SLEEP_DURATION_S = 0.01
FOREGROUND_CHECK_SLEEP_S = 5
MINIMAP_DETECTION_RETRY_DELAY_S = 1
WINDOW_HANDLE_RETRY_DELAY_S = 3
MINIMAP_TEMPLATE_MATCH_CONFIDENCE_THRESHOLD = 0.95

MatchedTemplateInfo = namedtuple("MatchedTemplateInfo", ["x", "y", "conf_score"])

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
        if not vision.isMatchedTemplateLocValid(matched_template_loc_and_conf_score, corners, MINIMAP_TEMPLATE_MATCH_CONFIDENCE_THRESHOLD):
            print("Minimap border template matching failed or returned invalid results. Retrying minimap detection...")
            continue

        minimap_border_locations = vision.findInGameMinimapBorder(matched_template_loc_and_conf_score, minimap_border_templates, corners)
        top_left, bottom_right = minimap_border_locations["left_top"], minimap_border_locations["right_bot"]
        minimap_region, minimap_valid, minimap_frame_counter = (*top_left, *bottom_right), True, 0

        while minimap_valid and minimap_frame_counter < MINIMAP_POSITION_RECALC_INTERVAL:
            if not vision.isProposedMinimapRegionValid(minimap_region, screen_width, screen_height):
                break
            minimap_frame = vision.cameraGrab(camera, minimap_region)

            if vision.isMinimapBlack(minimap_frame):
                print("Minimap frame is detected to be black. Retrying minimap detection...")
                minimap_valid = False
                time.sleep(MINIMAP_DETECTION_RETRY_DELAY_S)
                break

            with sharedInfo.minimap_frame_lock:
                sharedInfo.minimap_frame = cv2.cvtColor(minimap_frame, cv2.COLOR_BGR2RGB)
            minimap_frame_counter += 1
            time.sleep(FRAME_DELAY_MS / 1000)  # for some reason, longer delay time == lower occurrence of camera.grab() returning none regardless of the content
            time.sleep(THREAD_SLEEP_DURATION_S)  # give time for other threads (like the pathfinding thread) to run.