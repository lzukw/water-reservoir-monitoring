import json
import logging
import random
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import cv2
import numpy as np

from .redis_client import RedisClient, RedisHash

__all__ = ["WaterMeterValueExtraction", "WaterMeterValueExtractionState"]

DEFAULT_ALLOWED_CHANGE_FACTOR = (
    5.0  # Allowable factor for change in meter value based on estimated change
)
DEFAULT_APPROX_DAILY_CONSUMPTION = 200.0
DEFAULT_MIN_ACCURACY = 0.9
DEFAULT_METER_WRAP_AROUND = 10000000.0

JSON_KEY_REF_IMAGE_FILEPATH = "reference_image_filepath"
JSON_KEY_ROI = "roi"

MIN_KEYPOINTS = 15
MIN_MATCHES = 10
MIN_MATCH_RATIO = 0.75
DEFAULT_RANSAC_REPROJ_THRESHOLD = 5.0

DIGITS_IMAGE_WIDTH = 800

WaterMeterValueExtractionState = Literal["open", "error"]


class WaterMeterValueExtraction:
    def __init__(
        self,
        redis_client: RedisClient,
        redis_key_cam: str,
        redis_key_meter: str,
        ref_image_json_path: Path,
        store_redis_key_cam: str | None = None,
        wrap_around_value: float = DEFAULT_METER_WRAP_AROUND,
        min_accuracy: float = DEFAULT_MIN_ACCURACY,
        approx_daily_consumption: float = DEFAULT_APPROX_DAILY_CONSUMPTION,
        allowed_change_factor: float = DEFAULT_ALLOWED_CHANGE_FACTOR,
        log_level: int = logging.INFO,
    ):
        self._redis_client: RedisClient = redis_client
        self._redis_key_cam: str = redis_key_cam
        self._redis_key_meter: str = redis_key_meter

        with open(ref_image_json_path, "r") as f:
            ref_image_dict: dict[str, Any] = json.load(f)

        ref_image_path = ref_image_dict.get(JSON_KEY_REF_IMAGE_FILEPATH)
        assert ref_image_path is not None
        rv = cv2.imread(ref_image_path, cv2.IMREAD_UNCHANGED)
        assert rv is not None, f"Cannot open Reference-Image '{ref_image_path}'"
        assert rv.ndim == 3 and rv.shape[2] in (3, 4), (
            "Reference image must be a color image."
        )
        self._ref_img: np.ndarray = rv

        preselect_image_path = ref_image_dict.get("preselect_image_filepath")
        assert preselect_image_path is not None
        rv = cv2.imread(preselect_image_path, cv2.IMREAD_UNCHANGED)
        assert rv is not None, f"Cannot open Preselect-Image '{preselect_image_path}'"
        assert rv.ndim == 3 and rv.shape[2] in (3, 4), (
            "Preselect image must be a color image."
        )
        self._preselect_img: np.ndarray = rv

        rv = ref_image_dict.get(JSON_KEY_ROI)
        assert isinstance(rv, list)
        assert len(rv) == 4
        assert all(isinstance(val, int) for val in rv)
        self._roi: tuple[int, int, int, int] = tuple(rv)

        # Used to store the ROI extracted from the Redis-Camera-Image
        # as another camera-image to Redis.
        self._store_redis_key_cam: str | None = store_redis_key_cam

        self._wrap_around_value: float = wrap_around_value
        self._min_accuracy: float = min_accuracy
        self._allowed_change_factor = allowed_change_factor
        self._approx_daily_consumption = approx_daily_consumption

        self._logger: logging.Logger = logging.getLogger(f"{__name__}.DummyCamera")
        self._logger.setLevel(log_level)

        self._last_meter_value: RedisHash = RedisHash(
            datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            0.0,
        )

    @property
    def status(self) -> WaterMeterValueExtractionState:
        """Return the current status of the WaterMeterValueExtraction."""
        if self._redis_client.status == "connected":
            return "open"
        else:
            return "error"

    def _get_cam_frame_from_redis(self) -> tuple[str, np.ndarray] | None:
        if self._redis_client.status != "connected":
            self._logger.debug(
                "Cannot retrieve frame from Redis: Redis client is not connected."
            )
            return None
        result = self._redis_client.retrieve_hash(self._redis_key_cam)
        if result is None:
            self._logger.debug("Cannot retrieve frame from Redis. No frame data found.")
            return None
        frame_timestamp, frame_bytes = result
        assert isinstance(frame_timestamp, str)
        frame_np = cv2.imdecode(
            np.frombuffer(frame_bytes, dtype=np.uint8), cv2.IMREAD_UNCHANGED
        )
        if frame_np is None:
            return None
        return frame_timestamp, frame_np

    def _store_meter_value_to_redis(self, water_meter_value: RedisHash) -> None:
        if self._redis_client.status == "connected":
            self._redis_client.store_hash(
                self._redis_key_meter,
                water_meter_value.timestamp,
                water_meter_value.value,
            )
            self._logger.debug(
                f"Stored meter value {water_meter_value.value} to Redis with timestamp {water_meter_value.timestamp}"
            )
        else:
            self._logger.debug(
                "Cannot store meter value to Redis: Redis client is not connected."
            )

    def _store_extracted_roi_as_camimage_to_redis(
        self, frame_timestamp: str, roi_img: np.ndarray
    ) -> None:
        if self._store_redis_key_cam is None:
            return
        if self._redis_client.status == "connected":
            _, encoded = cv2.imencode(".jpg", roi_img)
            self._redis_client.store_hash(
                self._store_redis_key_cam, frame_timestamp, encoded.tobytes()
            )
        else:
            self._logger.debug(
                "Cannot store ROI-image to Redis: Redis client is not connected."
            )

    def _extract_roi_from_frame(self, camera_img: np.ndarray) -> np.ndarray | None:
        """Extract the ROI containing the digits of the water-meter from the
        camera-image. Details to this Image-Processing-Step can be found in
        `Experiments/040_feature-matching_filter-matches-to-same-points.ipynb`"""
        self._logger.debug("Projecting camera-image onto preselect-image")
        preselect_img = self._project_image(
            camera_img, self._preselect_img, DEFAULT_RANSAC_REPROJ_THRESHOLD
        )
        if preselect_img is None:
            return None
        self._logger.debug(
            "Projecting preselect-image onto reference-image (meter-dial-image)"
        )
        meter_dial_img = self._project_image(preselect_img, self._ref_img, 3.0)
        if meter_dial_img is None:
            return None

        # Cut out roi, resize to desired size and return the result.
        x1, y1, w, h = self._roi
        x2, y2 = x1 + w, y1 + h
        digits_img = meter_dial_img[y1 : y2 + 1, x1 : x2 + 1]
        new_size = (DIGITS_IMAGE_WIDTH, round(DIGITS_IMAGE_WIDTH / w * h))
        resized_digits_img = cv2.resize(digits_img, new_size)
        return resized_digits_img

    def _extract_value_from_roi(self, frame_np: np.ndarray) -> tuple[float, float]:
        """Extracts the water meter value from the given frame. Returns a tuple of (extracted_value, accuracy)."""

        # TODO: For now, this is just a simulation. In a real implementation
        _ = frame_np  # This is just to avoid unused variable warning. In a real implementation, this would be used for image processing.
        time_diff = datetime.now(UTC) - datetime.fromisoformat(
            self._last_meter_value.timestamp
        )
        simulated_value_change = (
            time_diff.total_seconds()
            / (24 * 3600)
            * self._approx_daily_consumption
            * random.uniform(0.9, 1.1)
        )  # Simulate a value change of about 200 units per day with some randomness
        extracted_value: float = (
            self._last_meter_value.value + simulated_value_change
        ) % self._wrap_around_value
        accuracy: float = random.uniform(
            0.8, 1.0
        )  # Simulate an accuracy between 80% and 100%

        return extracted_value, accuracy

    def extract_and_store_value(self):
        """Reads the latest frame from Redis, extracts the water meter value, and stores it back to Redis.
        This is only done, if the extracted value is accurate, plausible, and not a duplicate of the last stored value."""
        result = self._get_cam_frame_from_redis()
        if result is None:
            return
        frame_timestamp, camera_image = result

        # Has this frame already been processed? If so, skip extraction to avoid duplicates.
        if self._last_meter_value.timestamp == frame_timestamp:
            self._logger.debug(
                f"Duplicate frame timestamp {frame_timestamp}. Skipping extraction."
            )
            return

        # Extract ROI with digits from the image
        digits_image = self._extract_roi_from_frame(camera_image)
        if digits_image is None:
            self._logger.debug(f"Could not extract digits-ROI from camera-image")
            return

        # Store the digits-image as new "Camera"-Image, if desired.
        self._store_extracted_roi_as_camimage_to_redis(frame_timestamp, digits_image)

        # Get Meter-Value as float from the digits-image
        extracted_value, accuracy = self._extract_value_from_roi(digits_image)

        # Check for accuracy: If the accuracy of the extracted value is below the threshold, skip storing it.
        if accuracy < self._min_accuracy:
            self._logger.debug(
                f"Extracted value {extracted_value} from frame at {frame_timestamp} has low accuracy ({accuracy:.2f}). Skipping storage."
            )
            return

        # Check for plausibility: The extracted value should not be less than the last stored value unless it has wrapped around.
        change = (
            extracted_value - self._last_meter_value.value
        ) % self._wrap_around_value
        time_diff = datetime.fromisoformat(frame_timestamp) - datetime.fromisoformat(
            self._last_meter_value.timestamp
        )
        estimated_change = (
            time_diff.total_seconds() / (24 * 3600) * self._approx_daily_consumption
        )  # Estimate change based on time difference and expected daily usage
        if not 0 <= change <= estimated_change * self._allowed_change_factor:
            self._logger.info(
                f"Extracted value {extracted_value} from frame at {frame_timestamp} is not plausible. Skipping storage. Change: {change}, Estimated Change: {estimated_change}, Allowed Factor: {DEFAULT_ALLOWED_CHANGE_FACTOR}"
            )
            return

        # store the new value in Redis and update the last stored value
        new_water_meter_value = RedisHash(frame_timestamp, extracted_value)
        self._last_meter_value = new_water_meter_value
        self._store_meter_value_to_redis(new_water_meter_value)

    def _project_image(
        self,
        cur_img: np.ndarray,
        ref_img: np.ndarray,
        ransac_reproj_threshold: float = DEFAULT_RANSAC_REPROJ_THRESHOLD,
    ) -> np.ndarray | None:
        """Compute the homography between two images. Returns the homography matrix and matching
        reference and current points that were used to compute the homography.
        If the homography cannot be computed, returns None."""
        assert cur_img.ndim == 3 and cur_img.shape[2] in (3, 4), (
            "Current image must be a color image."
        )
        assert ref_img.ndim == 3 and ref_img.shape[2] in (3, 4), (
            "Reference image must be a color image."
        )

        # The mask restricts keypoint centers, not the surrounding descriptor region.
        feature_search_mask = ref_img[:, :, 3] if ref_img.shape[2] == 4 else None

        # Find features (keypoints and their descriptors) in both images
        feature_extractor = cv2.SIFT.create()
        ref_kp, ref_desc = feature_extractor.detectAndCompute(
            ref_img, mask=feature_search_mask
        )
        cur_kp, cur_desc = feature_extractor.detectAndCompute(cur_img, None)
        if ref_desc is None or len(ref_kp) < MIN_KEYPOINTS:
            self._logger.debug(
                f"Not enough keypoints detected in Reference-Image: {len(ref_kp) if ref_kp else None}"
            )
            return None
        if cur_desc is None or len(cur_kp) < MIN_KEYPOINTS:
            self._logger.debug(
                f"Not enough keypoints detected in Current Camera Image: {len(cur_kp) if cur_kp else None}"
            )
            return None

        # Match features between the two images
        norm = cv2.NORM_L2 if ref_desc.dtype == np.float32 else cv2.NORM_HAMMING
        brute_force_mathcher = cv2.BFMatcher(norm, crossCheck=False)
        raw: Sequence[Sequence[cv2.DMatch]] = brute_force_mathcher.knnMatch(
            queryDescriptors=ref_desc, trainDescriptors=cur_desc, k=2
        )

        # Apply ratio test to filter good matches in 2 steps
        # first select canditates based on the ratio test.
        candidates = []
        for m, n in raw:
            if m.distance < MIN_MATCH_RATIO * n.distance:
                candidates.append(m)
        candidates.sort(key=lambda m: m.distance)

        # Filter out matches that share the same keypoint in either image to ensure distinct matches
        good_matches = []
        used_ref_points_indices = set()
        used_cur_points_indices = set()
        for match in candidates:
            if (
                match.queryIdx in used_ref_points_indices
                or match.trainIdx in used_cur_points_indices
            ):
                continue
            good_matches.append(match)
            used_ref_points_indices.add(match.queryIdx)
            used_cur_points_indices.add(match.trainIdx)
        if len(good_matches) < max(4, MIN_MATCHES):
            self._logger.debug(
                f"Not enough distinct good matches found: Just found {len(good_matches)}, need at least {MIN_MATCHES}"
            )
            return None

        # Compute homography matrix with detected good matches
        ref_pts = np.zeros((len(good_matches), 1, 2), dtype=np.float32)
        cur_pts = np.zeros((len(good_matches), 1, 2), dtype=np.float32)
        for i, match in enumerate(good_matches):
            ref_pts[i, 0] = ref_kp[match.queryIdx].pt
            cur_pts[i, 0] = cur_kp[match.trainIdx].pt

        H, inliers = cv2.findHomography(
            ref_pts, cur_pts, cv2.RANSAC, ransac_reproj_threshold
        )
        if H is None or H.shape != (3, 3):
            self._logger.debug("Homography could not be computed.")
            return None
        if inliers is None or np.count_nonzero(inliers) < max(4, MIN_MATCHES):
            self._logger.debug(
                f"Not enough inliers after RANSAC: Only found {np.count_nonzero(inliers)}, need at least {MIN_MATCHES}"
            )
            return None

        inverse_H = np.linalg.inv(H)

        ref_height, ref_width = ref_img.shape[:2]
        projected_image = cv2.warpPerspective(
            cur_img, inverse_H, (ref_width, ref_height)
        )

        return projected_image
