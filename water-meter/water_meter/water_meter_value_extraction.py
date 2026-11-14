import logging
import random
from datetime import UTC, datetime
from typing import Literal

import numpy as np

from .redis_client import RedisClient, RedisHash

__all__ = ["WaterMeterValueExtraction", "WaterMeterValueExtractionState"]

ALLOWED_CHANGE_FACTOR = 5.0  # Allowable factor for change in meter value based on estimated change


WaterMeterValueExtractionState = Literal["open", "error"]

class WaterMeterValueExtraction:
    def __init__(self, 
                 redis_client: RedisClient,
                 redis_key_cam: str,
                 redis_key_meter: str,
                 rois: list[tuple[int,int,int,int]] | None = None,
                 wrap_around_value: float = 10000000.0,
                 accuracy_threshold: float = 0.9,
                 log_level: int = logging.INFO):
        self._redis_client : RedisClient = redis_client
        self._redis_key_cam : str = redis_key_cam
        self._redis_key_meter : str = redis_key_meter
        self._rois: list[tuple[int,int,int,int]] = rois or []
        self._wrap_around_value : float = wrap_around_value
        self._accuracy_threshold : float = accuracy_threshold

        self._logger : logging.Logger = logging.getLogger(f"{__name__}.DummyCamera")
        self._logger.setLevel(log_level)

        self._last_meter_value: RedisHash = RedisHash(
            datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"), 
            0.0 
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
            self._logger.debug("Cannot retrieve frame from Redis: Redis client is not connected.")
            return None
        result = self._redis_client.retrieve_hash(self._redis_key_cam)
        if result is None:
            self._logger.debug("Cannot retrieve frame from Redis. No frame data found.")
            return None
        frame_timestamp, frame_bytes = result
        assert isinstance(frame_timestamp, str)
        frame_np = np.frombuffer(frame_bytes, dtype=np.uint8)
        return frame_timestamp, frame_np

    def _store_meter_value_to_redis(self, water_meter_value: RedisHash) -> None:
        if self._redis_client.status == "connected":
            self._redis_client.store_hash(self._redis_key_meter, water_meter_value.timestamp, water_meter_value.value)
            self._logger.debug(f"Stored meter value {water_meter_value.value} to Redis with timestamp {water_meter_value.timestamp}")
        else:
            self._logger.debug("Cannot store meter value to Redis: Redis client is not connected.")

    def _extract_value_from_frame(self, frame_np: np.ndarray) -> tuple[float,float]:
        """Extracts the water meter value from the given frame. Returns a tuple of (extracted_value, accuracy)."""
        
        #TODO: For now, this is just a simulation. In a real implementation
        _ = frame_np  # This is just to avoid unused variable warning. In a real implementation, this would be used for image processing.
        time_diff = datetime.now(UTC) - datetime.fromisoformat(self._last_meter_value.timestamp)
        simulated_value_change = time_diff.total_seconds() / (24*3600) * 200 * random.uniform(0.9, 1.1)  # Simulate a value change of about 200 units per day with some randomness
        extracted_value: float = (self._last_meter_value.value + simulated_value_change) % self._wrap_around_value
        accuracy: float = random.uniform(0.7, 1.0)  # Simulate an accuracy between 70% and 100%
        
        return extracted_value, accuracy
        
    def extract_and_store_value(self):
        """Reads the latest frame from Redis, extracts the water meter value, and stores it back to Redis.
        This is only done, if the extracted value is accurate, plausible, and not a duplicate of the last stored value."""
        result = self._get_cam_frame_from_redis()
        if result is None:
            return
        frame_timestamp, frame_np = result

        # Has this frame already been processed? If so, skip extraction to avoid duplicates.
        if self._last_meter_value.timestamp == frame_timestamp:
            self._logger.debug(f"Duplicate frame timestamp {frame_timestamp}. Skipping extraction.")
            return

        extracted_value, accuracy = self._extract_value_from_frame(frame_np)

        # Check for accuracy: If the accuracy of the extracted value is below the threshold, skip storing it.
        if accuracy < self._accuracy_threshold:
            self._logger.debug(f"Extracted value {extracted_value} from frame at {frame_timestamp} has low accuracy ({accuracy:.2f}). Skipping storage.")
            return

        # Check for plausibility: The extracted value should not be less than the last stored value unless it has wrapped around.
        change = (extracted_value - self._last_meter_value.value) % self._wrap_around_value
        time_diff = datetime.fromisoformat(frame_timestamp) - datetime.fromisoformat(self._last_meter_value.timestamp)
        estimated_change = time_diff.total_seconds() / (24*3600) * 200  # Estimate change based on time difference and expected daily usage
        if  change >= estimated_change * ALLOWED_CHANGE_FACTOR:
            self._logger.debug(f"Extracted value {extracted_value} from frame at {frame_timestamp} is not plausible. Skipping storage. Change: {change}, Estimated Change: {estimated_change}, Allowed Factor: {ALLOWED_CHANGE_FACTOR}")
            return

        # store the new value in Redis and update the last stored value
        new_water_meter_value = RedisHash(frame_timestamp, extracted_value)
        self._last_meter_value = new_water_meter_value
        self._store_meter_value_to_redis(new_water_meter_value)
