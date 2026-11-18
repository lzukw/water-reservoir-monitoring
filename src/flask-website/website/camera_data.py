import io
import logging
import os
import sys
import tempfile
import time
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import cv2
import numpy as np

import redis

__all__ = ["CameraData"]


class CameraData:

    def __init__(self,
                 redis_server: str = "127.0.0.1",
                 redis_port: int = 6379,
                 redis_key_camera: str = "cam",
                 redis_hashkey_timestamp: str = "timestamp",
                 redis_hashkey_value: str = "value",
                 camera_images_folder: Path = Path("../../data/camera-images"),
                 camera_images_subfolder_prefix: str = "camera-",
                 log_level: str = "INFO"
                ):

        self._redis_server : str = redis_server
        self._redis_port : int = redis_port
        self._redis_key_camera: str = redis_key_camera
        self._redis_hashkey_value : bytes = redis_hashkey_value.encode()
        self._redis_hashkey_timestamp : bytes = redis_hashkey_timestamp.encode()
        self._camera_images_folder: Path = camera_images_folder
        self._camera_images_subfolder_prefix : str = camera_images_subfolder_prefix

        logging.basicConfig(
            stream=sys.stdout,
            format="%(asctime)s [%(levelname)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
            level=log_level.upper()
        )

        self._logger = logging.getLogger(__name__)

        self._logger.info(f"Beginning Connection to Redis-Server '{self._redis_server}:{self._redis_port}'")
        self._redis_client = redis.Redis(host=self._redis_server, port= self._redis_port)
        try:
            self._redis_client.ping()
        except redis.RedisError as e:
            self._logger.error(f"[REDIS] Error while connecting to Redis: {e}")
        self._logger.info(f"Connected to Redis-Server '{self._redis_server}:{self._redis_port}'")

        # Check if Redis contains data for cameras and log errors if not
        for camera_number in self.get_camera_numbers():
            try:
                camera_data = self._redis_client.hgetall(f"{self._redis_key_camera}:{camera_number}")
                if not isinstance(camera_data, dict):
                    self._logger.error(f"Redis: Expected dict for camera_data, got {type(camera_data)}")
                    continue
                if self._redis_hashkey_timestamp not in camera_data:
                    self._logger.error(f"Redis: Missing timestamp for camera {camera_number}")
                    continue
                if self._redis_hashkey_value not in camera_data:
                    self._logger.error(f"Redis: Missing image for camera {camera_number}")
                    continue
            except redis.RedisError as e:
                self._logger.error(f"[REDIS] Error while reading camera data from Redis for camera {camera_number}: {e}")

    def _try_get_image_folder(self, camera_number: int) -> Path:
        return Path(self._camera_images_folder) / f"{self._camera_images_subfolder_prefix}{camera_number}"

    def _get_image_folder(self, camera_number : int) -> Path | None:
        p = self._try_get_image_folder(camera_number)
        if not p.exists() or not p.is_dir():
            self._logger.error(f"Expexted Camera-Image-Folder not existing: '{p}'")
            return None
        return p

    def get_camera_numbers(self) -> list[int]:
        """Return Camera-Numbers for which images are available (i.e. folders exist)"""
        camera_numbers: list[int] = []
        # Hard-coded: Allowed Camera-numbers are 0...9
        for camera_number in range(10):
            p = self._try_get_image_folder(camera_number)
            if p.exists() and p.is_dir():
                camera_numbers.append(camera_number)
        return camera_numbers

    def _get_image_files_with_timestamps(self, camera_number: int) -> list[tuple[datetime, Path]]:
        """Return a list of (timestamp, filepath) tuples for all image files in the camera
        folder, sorted by timestamp. The timestamp is parsed from the filename."""
        camera_folder = self._get_image_folder(camera_number)
        if not camera_folder or not camera_folder.exists():
            return []

        image_files: list[tuple[datetime, Path]] = []
        for filepath in camera_folder.iterdir():
            if not filepath.is_file():
                continue
            try:
                # Hard-coded: The filename format is expected to be ISO 8601 with timezone info (e.g. "2023-08-01T12:34:56.789Z")
                image_timestamp = datetime.fromisoformat(filepath.stem)
                if image_timestamp.tzinfo is None:
                    self._logger.warning(f"Image file '{filepath}' has no timezone info in filename. Assuming UTC.")
                    image_timestamp = image_timestamp.replace(tzinfo=UTC)
            except ValueError:
                # Don't include files that don't match the expected filename format
                continue
            image_files.append((image_timestamp, filepath))

        image_files.sort(key=lambda item: item[0])
        return image_files

    def _get_image_filepaths_for_camera(self, camera_number: int) -> list[Path]:
        """Return a list of Path objects for all image files in the camera folder, sorted by filename."""
        return [filepath for _, filepath in self._get_image_files_with_timestamps(camera_number)]

    def get_latest_image_from_filesystem(self, camera_number: int) -> Path | None:
        """Rerturn the lastest image for a given camera_number, or None if no image is
        available. The images are fetched from the local filesystem, not from Redis."""
        camera_folder = self._get_image_folder(camera_number)
        if not camera_folder or not camera_folder.exists():
            return None

        image_files = self._get_image_filepaths_for_camera(camera_number)
        if not image_files:
            return None

        return image_files[-1]

    def get_latest_image_from_redis(self, camera_number: int) -> tuple[datetime,bytes] | None:
        """Return the timestamp and image bytes for the latest image for a given camera_number from Redis,
        or None if no image is available."""
        try:
            camera_data = self._redis_client.hgetall(f"{self._redis_key_camera}:{camera_number}")
            if not isinstance(camera_data, dict):
                self._logger.error(f"Redis: Expected dict for camera_data, got {type(camera_data)}")
                return None
            if self._redis_hashkey_value not in camera_data:
                self._logger.error(f"Redis: Missing image for camera {camera_number}")
                return None
            if self._redis_hashkey_timestamp not in camera_data:
                self._logger.error(f"Redis: Missing timestamp for camera {camera_number}")
                return None
            image_bytes = camera_data[self._redis_hashkey_value]
            timestamp_bytes = camera_data[self._redis_hashkey_timestamp]
            assert isinstance(image_bytes, bytes), f"Expected bytes for image data, got {type(image_bytes)}"
            assert isinstance(timestamp_bytes, bytes), f"Expected bytes for timestamp, got {type(timestamp_bytes)}"
            try:
                timestamp = datetime.fromisoformat(timestamp_bytes.decode())
                if timestamp.tzinfo is None:
                    self._logger.warning(f"Redis: Timestamp for camera {camera_number} has no timezone info. Assuming UTC.")
                    timestamp = timestamp.replace(tzinfo=UTC)
            except ValueError:
                self._logger.error(f"Redis: Invalid timestamp format for camera {camera_number}: {timestamp_bytes.decode()}")
                return None
            return timestamp, image_bytes
        except redis.RedisError as e:
            self._logger.error(f"[REDIS] Error while reading camera data from Redis for camera {camera_number}: {e}")
            return None

    def get_closest_image_from_filesystem(self, camera_number: int, timestamp: datetime) -> tuple[datetime, Path] | None:
        """Return the image for a given camera_number and timestamp, or None if no image is available.
        The fetched image is from the local filesystem. The returned image is the one closest to the
        given timestamp."""
        camera_folder = self._get_image_folder(camera_number)
        if not camera_folder or not camera_folder.exists():
            return None

        image_files = self._get_image_filepaths_for_camera(camera_number)
        if not image_files:
            return None

        # Find the image file with the closest timestamp
        closest_image_file: Path | None = None
        closest_image_timestamp: datetime | None = None
        closest_time_diff: float = float("inf")
        for image_file in image_files:
            try:
                # Hard-coded: The filename format is expected to be ISO 8601 with timezone info (e.g. "2023-08-01T12:34:56.789Z")
                image_timestamp = datetime.fromisoformat(image_file.stem)
                if image_timestamp.tzinfo is None:
                    self._logger.warning(f"Image file '{image_file}' has no timezone info in filename. Assuming UTC.")
                    image_timestamp = image_timestamp.replace(tzinfo=UTC)
            except ValueError:
                self._logger.error(f"Cannot parse timestamp from image file '{image_file}'")
                continue
            time_diff = abs((timestamp - image_timestamp).total_seconds())
            if time_diff < closest_time_diff:
                closest_time_diff = time_diff
                closest_image_file = image_file
                closest_image_timestamp = image_timestamp
        if closest_image_file is None or closest_image_timestamp is None:
            return None

        return closest_image_timestamp, closest_image_file

    def get_image_bytes(self, camera_number: int, filename: str) -> bytes | None:
        """Return the image bytes for the image file given by its filename and the camera_mumber, or
        None if the file does not exist."""
        cam_images_folder = self._get_image_folder(camera_number)
        if not cam_images_folder:
            return None
        file_path = cam_images_folder / filename
        if not file_path.exists() or not file_path.is_file():
            self._logger.error(f"Image file does not exist: '{file_path}'")
            return None
        try:
            with open(file_path, "rb") as f:
                return f.read()
        except Exception as e:  # noqa: BLE001
            self._logger.error(f"Error reading image file '{file_path}': {e}")
            return None

    def create_zip_of_images(self, camera_number: int, start_timestamp: datetime, end_timestamp: datetime) -> bytes | None:
        """Create a zip-file containing all images of the given camera whose timestamp is
        between start_timestamp and end_timestamp (inclusive), and return it as bytes.
        Returns None if no images are available in the given range. Naive timestamps are
        assumed to be UTC; if end_timestamp is before start_timestamp, the two are swapped."""
        if start_timestamp.tzinfo is None:
            start_timestamp = start_timestamp.replace(tzinfo=UTC)
        if end_timestamp.tzinfo is None:
            end_timestamp = end_timestamp.replace(tzinfo=UTC)
        if end_timestamp < start_timestamp:
            start_timestamp, end_timestamp = end_timestamp, start_timestamp

        selected_images = [
            (timestamp, filepath)
            for timestamp, filepath in self._get_image_files_with_timestamps(camera_number)
            if start_timestamp <= timestamp <= end_timestamp
        ]
        if not selected_images:
            self._logger.warning(
                f"No images for camera {camera_number} between {start_timestamp} and {end_timestamp}"
            )
            return None

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as zip_file:
            for _, filepath in selected_images:
                zip_file.write(filepath, arcname=filepath.name)
        self._logger.info(
            f"Created zip with {len(selected_images)} image(s) for camera {camera_number} "
            f"between {start_timestamp} and {end_timestamp}"
        )
        return buffer.getvalue()

    def create_video_from_redis(self, camera_number: int, duration_in_seconds: float) -> Path | None:
        """Create a video from the images stored in Redis for the given camera_number.
        The path of the created video (in the temp-Filesystem is returned)."""
        if duration_in_seconds <= 0:
            self._logger.error("duration_in_seconds must be greater than 0")
            return None

        frames: list[tuple[datetime, np.ndarray]] = []
        seen_timestamps: set[datetime] = set()


        end_time = time.monotonic() + duration_in_seconds
        while time.monotonic() < end_time:
            camera_data = self.get_latest_image_from_redis(camera_number)
            if camera_data is None:
                time.sleep(0.03)
                continue
            timestamp, image_bytes = camera_data

            if timestamp in seen_timestamps:
                time.sleep(0.03)
                continue
            seen_timestamps.add(timestamp)

            nparr = np.frombuffer(image_bytes, np.uint8)
            frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            if frame is None:
                self._logger.warning(f"Failed to decode image bytes for camera {camera_number} at {timestamp}")
                time.sleep(0.03)
                continue

            frames.append((timestamp, frame))
            time.sleep(0.03)

        if not frames:
            self._logger.error(f"No valid frames collected from Redis for camera {camera_number}")
            return None

        frames.sort(key=lambda item: item[0])
        first_frame = frames[0][1]
        height, width = first_frame.shape[:2]

        fps = max(1.0, (len(frames)+1) / duration_in_seconds)
        output_fd, output_path_str = tempfile.mkstemp(prefix=f"camera-{camera_number}_", suffix=".webm")
        Path(output_path_str).unlink(missing_ok=True)
        try:
            # Close the low-level file descriptor from mkstemp before OpenCV writes to the path.
            os.close(output_fd)
        except OSError:
            pass

        output_video_path = Path(output_path_str)
        fourcc = cv2.VideoWriter_fourcc(*"VP80") # type: ignore
        video_writer = cv2.VideoWriter(str(output_video_path), fourcc, fps, (width, height))
        if not video_writer.isOpened():
            self._logger.error(f"Failed to create video writer for '{output_video_path}'")
            return None

        try:
            for _, frame in frames:
                if frame.shape[0] != height or frame.shape[1] != width:
                    frame = cv2.resize(frame, (width, height))
                video_writer.write(frame)
        finally:
            video_writer.release()

        self._logger.info(
            f"Video created at '{output_video_path}' from {len(frames)} frames (duration={duration_in_seconds:.2f}s, fps={fps:.2f})"
        )
        return output_video_path


if __name__ == "__main__":
    # Example usage
    cam_path = Path(__file__).parents[3] / "data" / "camera-images"
    print(f"Camera images folder: {cam_path}")
    camera_data = CameraData(camera_images_folder=cam_path, redis_server="main-raspberry-pi", log_level="DEBUG")
    camera_numbers = camera_data.get_camera_numbers()
    print("Available camera numbers:", camera_numbers)
    assert len(camera_numbers) > 0, "No camera numbers found. Please ensure that the camera images folder contains subfolders for cameras."

    camera_number = camera_numbers[0]

    latest_image = camera_data.get_latest_image_from_filesystem(0)
    print(f"Latest image for camera {camera_number}: '{latest_image}'")
    latest_image_from_redis = camera_data.get_latest_image_from_redis(camera_number)
    if latest_image_from_redis:
        timestamp, image_bytes = latest_image_from_redis
        print(f"Latest image from Redis for camera {camera_number}: timestamp='{timestamp}', image_bytes={len(image_bytes)} bytes")
    else:
        print(f"No image available for camera {camera_number} in Redis")

    search_for_timestamp = datetime.now(UTC)-timedelta(days=5)
    closest_image = camera_data.get_closest_image_from_filesystem(camera_number, search_for_timestamp)
    if closest_image:
        image_path, image_filename = closest_image
        print(f"Closest image for camera {camera_number} to {search_for_timestamp}: '{image_path}' (filename='{image_filename}')")

        exact_image_bytes = camera_data.get_image_bytes(camera_number, image_filename.name)
        assert exact_image_bytes is not None, f"Failed to read image bytes for '{image_filename}'"
    else:
        print(f"No closest image found for camera {camera_number} to current time")

    video_path = camera_data.create_video_from_redis(camera_number, duration_in_seconds=5.0)
    if video_path:
        print(f"Video created from Redis images for camera {camera_number}: '{video_path}'")

    pause = input("Press Enter to delete video and extit...")
    if video_path:
        try:
            video_path.unlink()
            print(f"Deleted video file '{video_path}'")
        except OSError as e:
            print(f"Failed to delete video file '{video_path}': {e}")
