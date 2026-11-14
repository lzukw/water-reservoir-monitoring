import logging
from abc import ABC, abstractmethod
from typing import Literal

import cv2
import numpy as np

__all__ = ["AbstractCamera", "CameraStatus"]

CameraStatus = Literal["not initialized", "closed", "open", "error"]


class AbstractCamera(ABC):
    """Abstract base class for all camera implementations."""

    def __init__(self, 
                device: int | str = 0, 
                width: int = 640, 
                height: int = 480, 
                rois: list[tuple[int,int,int,int]] | None= None,
                log_level: int = logging.INFO) -> None:
        """Initialize the camera with the given parameters."""
        self._rois : list[tuple[int,int,int,int]] = rois if rois else []
        raise ValueError("AbstractCamera is an abstract class and cannot be instantiated directly. Please use a concrete subclass.")

    @property
    @abstractmethod
    def status(self) -> CameraStatus:
        """Current status of the camera. This also checks, if the camera is still alive, and 
        if not, sets the status to "closed"."""

    @abstractmethod
    def open(self) -> None:
        """Open the camera device."""

    @abstractmethod
    def close(self) -> None:
        """Close the camera device."""

    @abstractmethod
    def read_frame(self) -> np.ndarray:
        """Read a frame and return it as numpy-array."""

    def _mark_rois(self, frame: np.ndarray) -> np.ndarray:
        """Mark the ROIs in the frame with a green rectangle."""
        for roi in self._rois:
            x, y, w, h = roi
            cv2.rectangle(frame, (x-1, y-1), (x + w + 1, y + h + 1), (0, 255, 0), 1)
        return frame

    def read_frame_encoded(self) -> bytes:
        """Read a frame via `read_frame`, encode it as jpeg and return the encoded bytes."""
        frame = self.read_frame()
        _, encoded = cv2.imencode(".jpg", frame)
        return encoded.tobytes()
