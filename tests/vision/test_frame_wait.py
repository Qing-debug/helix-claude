"""`src/vision/frame_wait.py`: the capture's grab waits inside Windows for a new desktop image (H77, QA116-1).

The helper reuses bettercam 1.0.0's private grab path, so these tests drive it with a fake camera and a fake
duplication that writes into the frame info the helper passes by reference; `test_bettercam_is_pinned` fails on an
upgrade before the copied path can break live. Windows only: the helper imports bettercam (comtypes) at module level.
"""
import ctypes
import importlib.metadata
import inspect
import sys
from types import SimpleNamespace

import pytest

if sys.platform != "win32":
    pytest.skip("bettercam and comtypes are Windows only", allow_module_level=True)

import bettercam  # noqa: E402
import bettercam.core  # noqa: E402
import bettercam.processor  # noqa: E402
import comtypes  # noqa: E402
import numpy as np  # noqa: E402
from bettercam._libs.d3d11 import ID3D11Texture2D  # noqa: E402
from bettercam._libs.dxgi import DXGI_ERROR_ACCESS_LOST, DXGI_ERROR_NOT_FOUND, DXGI_ERROR_WAIT_TIMEOUT  # noqa: E402

import src.vision.frame_wait as frame_wait  # noqa: E402
import src.vision.vision_helper_lib as helper  # noqa: E402


def com_error(hresult: int) -> comtypes.COMError:
    return comtypes.COMError(ctypes.c_int32(hresult).value, "fake", None)


class FakeDuplication:
    """The IDXGIOutputDuplication at `camera._duplicator.duplicator`; `present_time` 0 means a pointer-only update."""

    def __init__(self, calls: list, error: BaseException | None = None, present_time: int = 1):
        self.calls, self.error, self.present_time = calls, error, present_time

    def AcquireNextFrame(self, timeout_ms, info_ref, resource_ref):
        self.calls.append(("acquire", timeout_ms))
        if self.error is not None:
            raise self.error
        info_ref._obj.LastPresentTime = self.present_time


class FakeCamera:
    """The parts of `bettercam.BetterCam` the helper reads; `_validate_region` is bettercam's own method."""

    _validate_region = bettercam.BetterCam._validate_region

    def __init__(self, error: BaseException | None = None, present_time: int = 1):
        self.calls: list = []
        self.width, self.height, self.rotation_angle = 1920, 1080, 0
        self.region = (0, 0, 1920, 1080)
        self.frame = np.zeros((2, 2, 3), dtype=np.uint8)
        duplication = FakeDuplication(self.calls, error, present_time)
        duplication.ReleaseFrame = lambda: self.calls.append(("release",))
        self._duplicator = SimpleNamespace(duplicator=duplication)
        self._device = SimpleNamespace(im_context=SimpleNamespace(
            CopyResource=lambda dst, src: self.calls.append(("copy", dst, src))))
        self._stagesurf = SimpleNamespace(texture="stage texture", map=self._map,
                                          unmap=lambda: self.calls.append(("unmap",)))
        self._processor = SimpleNamespace(process=self._process)

    def _map(self):
        self.calls.append(("map",))
        return "mapped rect"

    def _process(self, rect, width, height, region, rotation_angle):
        self.calls.append(("process", rect, width, height, region, rotation_angle))
        return self.frame

    def _on_output_change(self):
        self.calls.append(("output_change",))

    def grab(self, *args):
        raise AssertionError("cameraGrab must not use bettercam's own grab (time-out 0)")


@pytest.fixture
def texture(monkeypatch):
    monkeypatch.setattr(frame_wait, "_query_texture", lambda resource: "duplicated texture")


def test_the_acquire_waits_for_a_new_frame_instead_of_asking_once(texture):
    camera = FakeCamera()
    frame_wait.grab_new_frame(camera)
    assert frame_wait.NEW_FRAME_TIMEOUT_MS == 100
    assert camera.calls[0] == ("acquire", frame_wait.NEW_FRAME_TIMEOUT_MS)


def test_a_time_out_returns_none_and_holds_nothing(texture):
    camera = FakeCamera(error=com_error(DXGI_ERROR_WAIT_TIMEOUT))
    assert frame_wait.grab_new_frame(camera) is None
    assert camera.calls == [("acquire", 100)]


def test_a_pointer_only_update_is_released_and_not_copied(texture):
    camera = FakeCamera(present_time=0)
    assert frame_wait.grab_new_frame(camera) is None
    assert camera.calls == [("acquire", 100), ("release",)]


def test_a_new_image_is_copied_released_mapped_processed_and_unmapped(texture):
    camera = FakeCamera()
    region = (10, 20, 230, 180)
    assert frame_wait.grab_new_frame(camera, region) is camera.frame
    assert camera.calls == [
        ("acquire", 100),
        ("copy", "stage texture", "duplicated texture"),
        ("release",),
        ("map",),
        ("process", "mapped rect", 1920, 1080, region, 0),
        ("unmap",),
    ]


def test_no_region_grabs_the_cameras_region(texture):
    camera = FakeCamera()
    camera.region = (0, 0, 800, 600)
    frame_wait.grab_new_frame(camera)
    assert ("process", "mapped rect", 1920, 1080, (0, 0, 800, 600), 0) in camera.calls


def test_a_bad_region_raises_as_bettercams_grab_does_before_any_acquire(texture):
    camera = FakeCamera()
    with pytest.raises(ValueError, match="Invalid Region"):
        frame_wait.grab_new_frame(camera, (0, 0, 1921, 100))
    assert camera.calls == []


def test_access_lost_rebuilds_through_bettercam_and_returns_none(texture):
    camera = FakeCamera(error=com_error(DXGI_ERROR_ACCESS_LOST))
    assert frame_wait.grab_new_frame(camera) is None
    assert camera.calls == [("acquire", 100), ("output_change",)]


def test_another_com_error_is_raised(texture):
    camera = FakeCamera(error=com_error(DXGI_ERROR_NOT_FOUND))
    with pytest.raises(comtypes.COMError):
        frame_wait.grab_new_frame(camera)
    assert camera.calls == [("acquire", 100)]


def test_a_failed_texture_query_releases_the_frame_and_returns_none(monkeypatch):
    def fail(resource):
        raise com_error(DXGI_ERROR_NOT_FOUND)

    monkeypatch.setattr(frame_wait, "_query_texture", fail)
    camera = FakeCamera()
    assert frame_wait.grab_new_frame(camera) is None
    assert camera.calls == [("acquire", 100), ("release",)]


def test_the_texture_query_asks_the_resource_for_a_d3d11_texture():
    asked = []
    resource = SimpleNamespace(QueryInterface=lambda interface: asked.append(interface) or "texture")
    assert frame_wait._query_texture(resource) == "texture"
    assert asked == [ID3D11Texture2D]


@pytest.mark.parametrize("region", [None, (10, 20, 230, 180)])
def test_camera_grab_waits_past_none_and_never_uses_bettercams_grab(monkeypatch, region):
    camera, frame = FakeCamera(), np.ones((2, 2, 3), dtype=np.uint8)
    answers, asked = [None, None, frame], []

    def grab_new_frame(cam, roi=None):
        asked.append((cam, roi))
        return answers.pop(0)

    monkeypatch.setattr(frame_wait, "grab_new_frame", grab_new_frame)
    got = helper.cameraGrab(camera) if region is None else helper.cameraGrab(camera, region)
    assert got is frame
    assert asked == [(camera, region)] * 3


def test_bettercam_is_pinned_to_the_version_the_helper_copies():
    """The helper copies bettercam 1.0.0's `BetterCam.grab`/`_grab` and reads its private parts: an upgrade fails here."""
    assert importlib.metadata.version("bettercam") == "1.0.0"
    used = {
        bettercam.BetterCam: ("self._duplicator", "self._device", "self._stagesurf", "self._processor",
                              "def _on_output_change", "def _validate_region", "self.rotation_angle", "self.region"),
        bettercam.core.Duplicator: ("duplicator: ctypes.POINTER(IDXGIOutputDuplication)", "AcquireNextFrame(",
                                    "ReleaseFrame()"),
        bettercam.core.Device: ("self.im_context",),
        bettercam.core.StageSurface: ("texture:", "def map(", "def unmap("),
        bettercam.processor.Processor: ("def process(self, rect, width, height, region, rotation_angle)",),
    }
    for cls, names in used.items():
        source = inspect.getsource(cls)
        for name in names:
            assert name in source, f"{cls.__qualname__} no longer has {name!r}"
