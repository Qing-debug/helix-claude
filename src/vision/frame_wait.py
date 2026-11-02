"""Grab the next desktop image, waiting inside Windows until there is one (H77, Hong's QA116-1 answer).

bettercam 1.0.0's `BetterCam.grab` asks Windows' desktop duplication for a new image with a time-out of 0
(`bettercam/core/duplicator.py:25-29`), so with nothing new it returns None at once and the caller asks again.
`grab_new_frame` is a copy of `BetterCam.grab`/`_grab` (`bettercam/bettercam.py:70-94`) that passes
`NEW_FRAME_TIMEOUT_MS` instead: the call sleeps in the OS (the GIL released) and returns as soon as Windows has a new
desktop image, or None after the time-out on a screen where nothing changed. The time-out is finite because the wait
cannot be cancelled. Same API, same monitor, same pixels as before (H58).

It reads bettercam's private parts (`_duplicator`, `_device`, `_stagesurf`, `_processor`, `_on_output_change`,
`_validate_region`), so bettercam stays pinned at 1.0.0; `tests/vision/test_frame_wait.py` fails on an upgrade.
Differences from bettercam's grab: an update that moved only the mouse pointer (no new desktop image,
`LastPresentTime` 0) is released and gives None, and a failed texture query releases the frame and gives None
instead of copying the previous texture.
"""
import ctypes

import comtypes
import numpy as np
from bettercam._libs.d3d11 import ID3D11Texture2D
from bettercam._libs.dxgi import (
    DXGI_ERROR_ACCESS_LOST,
    DXGI_ERROR_WAIT_TIMEOUT,
    DXGI_OUTDUPL_FRAME_INFO,
    IDXGIResource,
)

NEW_FRAME_TIMEOUT_MS = 100

# COMError carries the HRESULT as a signed 32-bit int, as bettercam compares it (duplicator.py:31-33).
_WAIT_TIMEOUT = ctypes.c_int32(DXGI_ERROR_WAIT_TIMEOUT).value
_ACCESS_LOST = ctypes.c_int32(DXGI_ERROR_ACCESS_LOST).value


def _query_texture(resource):
    return resource.QueryInterface(ID3D11Texture2D)


def grab_new_frame(camera, region: tuple[int, int, int, int] | None = None) -> np.ndarray | None:
    """The next new desktop image of `camera` (a `bettercam.BetterCam`) cropped to `region`, or None.

    None after `NEW_FRAME_TIMEOUT_MS` with no new image, on a pointer-only update, on a failed texture query, and
    when the display changed (bettercam's `_on_output_change` rebuilds the duplication first). A bad region raises
    ValueError before any wait, as `BetterCam.grab` does; any other COM error is raised.
    """
    if region is None:
        region = camera.region
    else:
        camera._validate_region(region)
    duplication = camera._duplicator.duplicator
    info = DXGI_OUTDUPL_FRAME_INFO()
    resource = ctypes.POINTER(IDXGIResource)()
    try:
        duplication.AcquireNextFrame(NEW_FRAME_TIMEOUT_MS, ctypes.byref(info), ctypes.byref(resource))
    except comtypes.COMError as error:
        if error.args[0] == _WAIT_TIMEOUT:
            return None
        if error.args[0] == _ACCESS_LOST:
            camera._on_output_change()
            return None
        raise
    try:
        if info.LastPresentTime == 0:  # only the pointer changed
            return None
        try:
            texture = _query_texture(resource)
        except comtypes.COMError:
            return None
        camera._device.im_context.CopyResource(camera._stagesurf.texture, texture)
    finally:
        duplication.ReleaseFrame()
    rect = camera._stagesurf.map()
    frame = camera._processor.process(rect, camera.width, camera.height, region, camera.rotation_angle)
    camera._stagesurf.unmap()
    return frame
