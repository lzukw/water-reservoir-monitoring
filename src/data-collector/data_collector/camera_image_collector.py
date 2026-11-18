import logging
from pathlib import Path
from typing import Literal

from .redis_client import RedisClient

CameraImageCollectorStatus = Literal["not initialized", "collecting", "error"]


class CameraImageCollector:
    """
    Collects a camera image from a Redis server and saves it to a file.
    """

    def __init__(
        self,
        camera_number: int,
        camera_images_subfolder: Path,
        redis_client: RedisClient,
        redis_key: str,
        redis_hashkey_timestamp: str,
        redis_hashkey_value: str,
        log_level: int = logging.INFO,
    ):
        self._camera_number = camera_number
        self._redis_client = redis_client
        self._redis_key = redis_key
        self._redis_hashkey_timestamp = redis_hashkey_timestamp
        self._redis_hashkey_value = redis_hashkey_value
        self._camera_images_subfolder: Path = camera_images_subfolder

        self._logger: logging.Logger = logging.getLogger(
            f"{__name__}.CameraImageCollector"
        )
        self._logger.setLevel(log_level)

        self._status: CameraImageCollectorStatus = "not initialized"

    @property
    def camera_number(self) -> int:
        """Return the camera number of the CameraImageCollector."""
        return self._camera_number

    @property
    def status(self) -> CameraImageCollectorStatus:
        """Return the current status of the CameraImageCollector."""
        if self._status == "collecting":
            if self._redis_client.status != "connected":
                self._logger.warning(
                    "Lost Connection to Redis server. Setting status to 'error'."
                )
                self._status = "error"
            elif self._redis_client.retrieve_hash(self._redis_key) is None:
                self._logger.error(
                    f"No camera image found in Redis for key '{self._redis_key}'. Setting status to 'error'."
                )
                self._status = "error"
            elif not self._test_image_folder():
                self._logger.error(
                    f"Camera image sub-folder '{self._camera_images_subfolder}' does not exist or is not writable. Setting status to 'error'."
                )
                self._status = "error"
        return self._status

    def _test_image_folder(self) -> bool:
        return (
            self._camera_images_subfolder.exists()
            and self._camera_images_subfolder.is_dir()
        )

    def collect(self) -> None:
        """Collects the lastes camaera-image from the Redis server and writes it to a file
        in the camera images folder.
        """
        # First ensure that Redis is connected (this call returns immediately, if Redis is already connected.
        if self._status != "collecting":
            self._redis_client.connect()
            result_redis = self._redis_client.retrieve_hash(self._redis_key)
            result_img_folder = self._test_image_folder()
            if result_redis is not None and result_img_folder is True:
                self._logger.info(
                    "Successfully connected to Redis and Camera-image subfolder is valid. Starting to collect camera-images."
                )
                self._status = "collecting"
            else:
                if self._status != "error":
                    self._logger.error(
                        "Could not connect to Redis or Camera-image subfolder is invalid. Setting status to 'error'."
                    )
                self._status = "error"
                return

        # Check connection again (in case we were in state "collecting" before).
        # Getting property self.status also checks if Redis and Camera-Images-Subfolder are working as expected.
        if self.status != "collecting":
            return

        result = self._redis_client.retrieve_hash(self._redis_key)
        if not result:
            self._logger.error(
                f"No Redis-Hash found in Redis for key '{self._redis_key}'."
            )
            self._status = "error"
            return
        timestamp, img_bytes = result

        assert isinstance(timestamp, str), (
            f"Expected timestamp to be a string, but got {type(timestamp)}"
        )
        assert isinstance(img_bytes, bytes), (
            f"Expected img_bytes to be bytes, but got {type(img_bytes)}"
        )

        filename = self._camera_images_subfolder / f"{timestamp}.jpg"
        try:
            with filename.open("wb") as f:
                f.write(img_bytes)
            self._logger.debug(f"Saved camera image to file '{filename}'.")
        except OSError as e:
            self._logger.error(f"Could not save camera image to file '{filename}': {e}")
            self._status = "error"
