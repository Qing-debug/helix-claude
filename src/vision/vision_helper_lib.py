import cv2
import numpy as np
from enum import Enum


def findInGameMinimapBorder(matched_templates, minimap_border_templates, corners: list[str]) -> dict[str, (int,int)]:
    in_game_border_offsets = {}
    print(matched_templates)
    for corner in corners:
        matched_template = matched_templates[corner]
        template_height, template_width = minimap_border_templates[corner].shape
        center_x = matched_template.x + (template_width // 2)
        center_y = matched_template.y + (template_height // 2)
        in_game_border_offsets[corner] = (center_x, center_y)
    return in_game_border_offsets


EDGE_ALIGNMENT_TOLERANCE_PX = 6  # corners may differ by their template-size deltas (a few px) plus match jitter

#checks the confidence rating for each matched template loc, ensures left/right and top/bottom ordering, and that the
#four corners actually form a coherent rectangle (opposite edges aligned) rather than just being correctly ordered
def isMatchedTemplateLocValid(matched_templates: dict, corners: list[str], template_min_conf_score: float = 0.95,
                              edge_alignment_tolerance_px: int = EDGE_ALIGNMENT_TOLERANCE_PX) -> bool:
    for corner in corners:
        matched_template = matched_templates[corner]
        if matched_template.conf_score < template_min_conf_score:
            return False

    left_top, right_top, right_bot, left_bot = corners
    left_top_template, right_top_template, right_bot_template, left_bot_template = (matched_templates[left_top],
                                                                                    matched_templates[right_top],
                                                                                    matched_templates[right_bot],
                                                                             matched_templates[left_bot])
    if left_top_template.x >= right_top_template.x or left_bot_template.x >= right_bot_template.x:
        print("Template matched locations are invalid: left-most x coord >= right-most x coord")
        return False
    if left_top_template.y >= left_bot_template.y or right_top_template.y >= right_bot_template.y:
        print("Template matched locations are invalid: top-most y coord >= bottom-most y coord")
        return False
    # rectangle consistency: ordering alone allows skewed quads (e.g. a slanted top edge). Require opposite edges to
    # actually align within tolerance so an incoherent set of high-confidence matches is rejected, not just used.
    if (abs(left_top_template.y - right_top_template.y) > edge_alignment_tolerance_px or
            abs(left_bot_template.y - right_bot_template.y) > edge_alignment_tolerance_px):
        print("Template matched locations are invalid: top/bottom corners are not horizontally aligned")
        return False
    if (abs(left_top_template.x - left_bot_template.x) > edge_alignment_tolerance_px or
            abs(right_top_template.x - right_bot_template.x) > edge_alignment_tolerance_px):
        print("Template matched locations are invalid: left/right corners are not vertically aligned")
        return False
    return True

##only return valid frames; each grab waits inside Windows for a new image (frame_wait.py, H77)
def cameraGrab(camera ,minimap_detection_region_of_interest : (int, int, int, int) = None):
    import src.vision.frame_wait as frame_wait  # not at module level: it loads bettercam, and detection_check imports this file without it
    frame = None
    while frame is None:
        if minimap_detection_region_of_interest is not None:
            frame = frame_wait.grab_new_frame(camera, minimap_detection_region_of_interest)
        else:
            frame = frame_wait.grab_new_frame(camera) #captures entire screen
    return frame

#checks if the proposed region to grab a frame is valid according to bettercamera's.grab() function. Screen dimensions are passed as separate width/height ints to prevent (H,W) vs (W,H) tuple-order mix-ups.
def isProposedMinimapRegionValid(minimap_region: (int, int, int, int), screen_width: int, screen_height: int) -> bool:
    x1, y1, x2, y2 = minimap_region[0], minimap_region[1], minimap_region[2], minimap_region[3]
    if x1 < 0 or y1 < 0 or x2 > screen_width or y2 > screen_height:
        return False
    if x1 >= x2 or y1 >= y2:
        return False

    return True

def isMinimapBlack(minimap_frame):
    hsv_minimap_frame = cv2.cvtColor(minimap_frame, cv2.COLOR_BGR2HSV)
    black_pixel_range = np.array([0,0,0], dtype=np.uint8) #only targets black pixel range since HSV for back pixel = [0,0,0].
    normalised_mask = cv2.inRange(hsv_minimap_frame, black_pixel_range, black_pixel_range) // 255 #normalise to only 0 and 1
    (row_mask, col_mask) = normalised_mask.shape
    total_elem_in_mask = row_mask * col_mask
    total_black_pixel_in_mask = np.sum(normalised_mask)
    black_pixel_percentage_threshold = 95
    if percentOfBlackPixInMinimap(total_black_pixel_in_mask, total_elem_in_mask) >= black_pixel_percentage_threshold:
        print(percentOfBlackPixInMinimap(total_black_pixel_in_mask, total_elem_in_mask))
        return True
    return False

def percentOfBlackPixInMinimap(total_black_pixel, total_elem):
    return round(total_black_pixel / total_elem * 100)