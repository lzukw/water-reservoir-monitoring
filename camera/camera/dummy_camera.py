import logging
import time

try:
    from typing import override # type: ignore  # noqa: I001
except ImportError:  # Python < 3.12
    from typing_extensions import override

import numpy as np

from .abstract_camera import AbstractCamera, CameraStatus

__all__ = ["DummyCamera"]


class DummyCamera(AbstractCamera):

    @override
    def __init__(self,
                 device: int | str = 0,
                 width: int = 640,
                 height: int = 480,
                 rois: list[tuple[int,int,int,int]] | None= None,
                 log_level: int = logging.INFO,):
        self._device = device
        self._width = width
        self._height = height
        self._rois : list[tuple[int,int,int,int]] = rois or []
        self._status : CameraStatus = "not initialized"
        self._logger : logging.Logger = logging.getLogger(f"{__name__}.DummyCamera")
        self._logger.setLevel(log_level)
        self._rng = np.random.default_rng()

    @property
    @override
    def status(self) -> CameraStatus:
        """Return the current status of the DummyCamera. With a very small probability, the camera is 
        set to "not working" to simulate a camera failure."""
        if self._status == "open" and self._rng.random() < 0.01:  # 1% chance to simulate a camera failure
            self._logger.error("Simulating: DummyCamera has failed. Closing camera.")
            self._status = "closed"
        return self._status

    @override
    def open(self) -> None:
        """Open the camera device."""
        if self._status == "open":
            return
        self._status = "open"
        self._logger.info("DummyCamera is open. Active camera resolution: %sx%s", self._width, self._height)

    @override
    def close(self) -> None:
        """Close the camera device."""
        if self._status == "closed":
            return
        self._status = "closed"
        self._logger.info("DummyCamera is closed.")

    @override
    def read_frame(self) -> np.ndarray:
        """Read a frame and return it as numpy-array. The dummy-camera returns a 
        random image. If the camera is "not working", also return a random image.
        This behaviour is expected by all camera implementations, so that the rest of the system can continue to work even if the camera is not working."""
        if self._status == "not initialized":
            raise RuntimeError("Cannot read frame: DummyCamera is not open.")
        # Generate a random image
        if self._status == "open":
            time.sleep(0.03) # Simulate a delay of 30ms to mimic a real camera's frame capture time
            frame = np.ones((self._height, self._width, 3), dtype=np.uint8) * self._rng.integers(0, 256, size=(), dtype=np.uint8)
        else:
            frame = self._rng.integers(0, 256, (self._height, self._width, 3), dtype=np.uint8)

        self._mark_rois(frame)
        return frame