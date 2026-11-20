import logging
import time

try:
    from typing import override  # type: ignore  # noqa: I001
except ImportError:  # Python < 3.12
    from typing_extensions import override

import numpy as np
from picamera2 import Picamera2

from .abstract_camera import AbstractCamera, CameraStatus

__all__ = ["PiCamera"]


class PiCamera(AbstractCamera):
    """Pi-Camera wrapper for capturing images."""

    @override
    def __init__(
        self,
        device: int | str = 0,
        width: int = 640,
        height: int = 480,
        rois: list[tuple[int, int, int, int]] | None = None,
        log_level: int = logging.INFO,
    ):
        assert isinstance(device, int), "device for PiCamera must be an int"
        self._device = device
        self._width = width
        self._height = height
        self._picam: Picamera2 | None = None

        self._rois: list[tuple[int, int, int, int]] = rois or []

        self._status: CameraStatus = "not initialized"

        self._logger: logging.Logger = logging.getLogger(f"{__name__}.PiCamera")
        self._logger.setLevel(log_level)

        self._rng = np.random.default_rng()

    def _release(self) -> None:
        if self._picam is not None:
            self._picam.close()
            self._picam = None

    @property
    @override
    def status(self) -> CameraStatus:
        """Return the current status of the PiCamera. In "open"-state," first is checked if the
        connection is still alive, and if not, the status is set to "closed"."""
        if self._status == "open" and (self._picam is None or not self._picam.started):
            self._logger.error(
                f"Lost PiCamera '{self._device}' connection. Camera is not started."
            )
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
        assert self._picam is None
        try:
            self._picam = Picamera2(self._device)
        except (RuntimeError, OSError, ValueError) as e:
            # avoid logging the error multiple times if the camera is already closed
            if self._status == "not initialized":
                self._logger.error(
                    f"Could not create Picam-Object '{self._device} ': {e}"
                )
            self._release()
            self._status = "closed"
            return

        try:
            camera_config = self._picam.create_preview_configuration(
                {"size": (self._width, self._height), "format": "RGB888"}
            )
            self._picam.align_configuration(camera_config)
            self._picam.configure(camera_config)
            self._picam.start()
            time.sleep(0.1)
            actual_camera_config = self._picam.camera_configuration()
            self._width, self._height = actual_camera_config["main"]["size"]
            if self._status == "not initialized":
                self._logger.info(
                    f"PiCamera '{self._device}' is open. Active camera resolution: %sx%s",
                    self._width,
                    self._height,
                )
            self._status = "open"
        except (RuntimeError, OSError, ValueError) as e:
            # avoid logging the error multiple times if the camera is already closed
            if self._status == "not initialized":
                self._logger.error(
                    f"Could not initialize PiCamera '{self._device}': {e}"
                )
            self._release()
            self._status = "closed"
            return

    @override
    def close(self):
        """Close the camera device."""
        self._release()
        self._status = "closed"

    @override
    def read_frame(self) -> np.ndarray:
        """Read a frame from the camera, mark ROIs in it, and return it as numpy-array.
        If the camera has been closed due to a failure (lost connection to camera), return
        a random image."""
        if self._status == "not initialized":
            raise RuntimeError(
                "Cannot read frame: PiCamera is not initialized. Call open() first."
            )
        frame: np.ndarray | None = None
        if self.status == "open":
            frame = self._picam.capture_array()
            if frame is None or frame.size == 0:
                frame = None
                self._logger.error(
                    "Could not read frame from camera '%s'. Closing Camera.",
                    self._device,
                )
                self._release()
                self._status = "error"

        if frame is None:
            # Create white-noise-image
            frame = self._rng.integers(
                0, 256, (self._height, self._width, 3), dtype=np.uint8
            )

        self._mark_rois(frame)

        return frame
