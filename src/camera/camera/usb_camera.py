import logging

try:
    from typing import override # type: ignore  # noqa: I001
except ImportError:  # Python < 3.12
    from typing_extensions import override

import cv2
import numpy as np

from .abstract_camera import AbstractCamera, CameraStatus

__all__ = ["UsbCamera"]


class UsbCamera(AbstractCamera):
    """USB-Camera wrapper (based on OpenCV) for capturing images."""

    @override
    def __init__(self,
                 device: int | str = 0,
                 width: int = 640,
                 height: int = 480,
                 rois: list[tuple[int,int,int,int]] | None = None,
                 log_level: int = logging.INFO,):
        # Anything accepted by cv2.VideoCapture: device index, device path
        # (e.g. "/dev/video0"), video file name or stream URL.
        self._device = device
        self._width = width
        self._height = height
        self._cap: cv2.VideoCapture | None = None

        self._rois : list[tuple[int,int,int,int]] = rois or []

        self._status : CameraStatus = "not initialized"

        self._logger : logging.Logger = logging.getLogger(f"{__name__}.UsbCamera")
        self._logger.setLevel(log_level)

        self._rng = np.random.default_rng()

    def _release(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    @property
    @override
    def status(self) -> CameraStatus:
        """Return the current status of the UsbCamera. In "open"-state, first is checked if the
        connection is still alive, and if not, the status is set to "closed"."""
        if self._status == "open" and (self._cap is None or not self._cap.isOpened()):
            self._logger.error(f"Lost UsbCamera '{self._device}' connection. Camera is not opened.")
            self._status = "closed"
            self._release()

        return self._status

    @override
    def open(self):
        """Open the camera device"""
        # If the camera is already open, do nothing. `.status` also checks if the camera
        # is still alive, and if not, sets the status to "closed".
        if self.status == "open":
            return

        # Status here is either "not initialized" or "closed"
        assert self._cap is None
        try:
            cap = cv2.VideoCapture(self._device)
            if not cap.isOpened():
                cap.release()
                raise OSError("VideoCapture could not be opened")
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, self._width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self._height)
            self._cap = cap
            self._width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            self._height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            if self._status == "not initialized":
                self._logger.info(f"UsbCamera '{self._device}' is open. Active camera resolution: %sx%s", self._width, self._height)
            self._status = "open"
        except (RuntimeError, OSError, ValueError, cv2.error) as e:
            # avoid logging the error multiple times if the camera is already closed
            if self._status == "not initialized":
                self._logger.error(f"Could not open UsbCamera '{self._device}': {e}")
            self._cap = None
            self._status = "closed"
            return

    @override
    def close(self):
        """Close the camera device."""
        if self._status != "closed":
            self._logger.info("Closing UsbCamera '%s'", self._device)
            return
        self._release()
        self._status = "closed"

    @override
    def read_frame(self) -> np.ndarray:
        """Read a frame from the camera, mark ROIs in it, and return it as numpy-array.
        If the camera has been closed due to a failure (lost connection to camera), return
        a random image."""
        if self._status == "not initialized":
            raise RuntimeError("Cannot read frame: UsbCamera is not initialized. Call open() first.")
        frame: np.ndarray | None = None
        if self.status == "open":
            assert self._cap is not None
            ok, frame = self._cap.read()
            if not ok:
                frame = None
            if frame is None or frame.size == 0:
                self._logger.error("Could not read frame from camera '%s'. Closing Camera.", self._device)
                frame = None
                self._release()
                self._status = "error"

        if frame is None:
            # Create white-noise-image
            frame = self._rng.integers(0, 256, (self._height, self._width, 3), dtype=np.uint8)

        self._mark_rois(frame)
        return frame
