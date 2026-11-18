import logging
import time
from pathlib import Path

try:
    from typing import override  # type: ignore  # noqa: I001
except ImportError:  # Python < 3.12
    from typing_extensions import override

import cv2
import numpy as np

from .abstract_camera import AbstractCamera, CameraStatus

__all__ = ["DummyCamera"]

TEST_IMAGE_PATH = Path(__file__).resolve().parent / "test_image.jpg"
NOISE_AMPLITUDE = 20  # max +/- pixel noise added to the test image


class DummyCamera(AbstractCamera):
    @override
    def __init__(
        self,
        device: int | str = 0,
        width: int = 640,
        height: int = 480,
        rois: list[tuple[int, int, int, int]] | None = None,
        log_level: int = logging.INFO,
    ):
        self._device = device
        self._width = width
        self._height = height
        self._rois: list[tuple[int, int, int, int]] = rois or []
        self._status: CameraStatus = "not initialized"
        self._logger: logging.Logger = logging.getLogger(f"{__name__}.DummyCamera")
        self._logger.setLevel(log_level)
        self._rng = np.random.default_rng()
        self._test_image = cv2.imread(str(TEST_IMAGE_PATH), cv2.IMREAD_COLOR)
        if self._test_image is None:
            self._logger.warning(
                "Cannot read test image %s - falling back to random noise images.",
                TEST_IMAGE_PATH,
            )
        elif self._test_image.shape != (self._height, self._width, 3):
            self._test_image = cv2.resize(self._test_image, (self._width, self._height))

    @property
    @override
    def status(self) -> CameraStatus:
        """Return the current status of the DummyCamera. With a very small probability, the camera is
        set to "not working" to simulate a camera failure."""
        if (
            self._status == "open" and self._rng.random() < 0.01
        ):  # 1% chance to simulate a camera failure
            self._logger.error("Simulating: DummyCamera has failed. Closing camera.")
            self._status = "closed"
        return self._status

    @override
    def open(self) -> None:
        """Open the camera device."""
        if self._status == "open":
            return
        self._status = "open"
        self._logger.info(
            "DummyCamera is open. Active camera resolution: %sx%s",
            self._width,
            self._height,
        )

    @override
    def close(self) -> None:
        """Close the camera device."""
        if self._status == "closed":
            return
        self._status = "closed"
        self._logger.info("DummyCamera is closed.")

    @override
    def read_frame(self) -> np.ndarray:
        """Read a frame and return it as numpy-array. The dummy-camera returns the
        test image (TEST_IMAGE_PATH) with some random noise added. If the camera is
        "not working" (or the test image could not be loaded), a random noise image
        is returned instead. This behaviour is expected by all camera implementations,
        so that the rest of the system can continue to work even if the camera is not working."""
        if self._status == "not initialized":
            raise RuntimeError("Cannot read frame: DummyCamera is not open.")
        if self._status == "open":
            time.sleep(
                0.03
            )  # Simulate a delay of 30ms to mimic a real camera's frame capture time
            if self._test_image is not None:
                frame = np.clip(
                    self._test_image.astype(np.int16)
                    + self._rng.integers(
                        -NOISE_AMPLITUDE,
                        NOISE_AMPLITUDE + 1,
                        self._test_image.shape,
                        dtype=np.int16,
                    ),
                    0,
                    255,
                ).astype(np.uint8)
            else:
                frame = self._rng.integers(
                    0, 256, (self._height, self._width, 3), dtype=np.uint8
                )
        else:
            frame = self._rng.integers(
                0, 256, (self._height, self._width, 3), dtype=np.uint8
            )

        self._mark_rois(frame)
        return frame
