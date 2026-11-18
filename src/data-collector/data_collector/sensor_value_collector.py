import csv
import logging
from pathlib import Path
from typing import Literal

from .redis_client import RedisClient

SensorValueCollectorStatus = Literal["not initialized", "collecting", "error"]

class SensorValueCollector:
    """
    Collects a sensor value from a Redis server and append it in a csv-file.
    """

    def __init__(self, 
                 name: str,
                 csv_file: Path,
                 redis_client: RedisClient,
                 redis_key: str,
                 csv_header_timestamp: str = "timestamp",
                 csv_header_value: str = "value",
                 log_level: int = logging.INFO
                 ):
        self._name = name
        self._redis_key = redis_key
        self._redis_client = redis_client
        self._csv_file :Path = csv_file
        self._csv_header_timestamp = csv_header_timestamp
        self._csv_header_value = csv_header_value

        self._logger : logging.Logger = logging.getLogger(f"{__name__}.DummyCamera")
        self._logger.setLevel(log_level)

        self._status : SensorValueCollectorStatus = "not initialized"

    @property
    def name(self) -> str:
        """Return the name of the SensorValueCollector."""
        return self._name

    @property
    def status(self) -> SensorValueCollectorStatus:
        """Return the current status of the SensorValueCollector."""
        if self._status == "collecting":
            if self._redis_client.status != "connected":
                self._logger.warning("Lost Connection to Redis server. Setting status to 'error'.")
                self._status = "error"
            elif self._redis_client.retrieve_hash(self._redis_key) is None:
                self._logger.error(f"No sensor value found in Redis for key '{self._redis_key}'. Setting status to 'error'.")
                self._status = "error"
            elif not self._test_csv_file():
                self._logger.error("CSV file does not exist or has wrong headers. Setting status to 'error'.")
                self._status = "error"
        return self._status

    def _test_csv_file(self) -> bool:
        try:
            with self._csv_file.open("r", newline="", encoding="utf-8") as csv_file:
                reader = csv.reader(csv_file)
                headers = next(reader, None)
                if headers == [self._csv_header_timestamp, self._csv_header_value]:
                    return True
        except (FileNotFoundError, PermissionError, OSError):
            pass
        return False
    
    def collect(self):
        """Collects sensor values from the Redis server and appendds them to the csv-file.
        """
        # First ensure that Redis is connected (this call returns immediately, if Redis is already connected.
        if self._status != "collecting":
            self._redis_client.connect()
            result_redis = self._redis_client.retrieve_hash(self._redis_key)
            result_csv = self._test_csv_file()
            if result_redis is not None and result_csv is True:
                self._logger.info("Successfully connected to Redis and CSV file is valid. Starting to collect sensor values.")
                self._status = "collecting"
            else:
                if self._status != "error":
                    self._logger.error("Could not connect to Redis or CSV file is invalid. Setting status to 'error'.")
                self._status = "error"
                return
            
        # Check connection again (in case we were in state "collecting" before).
        # Getting property self.status also checks if Redis and CSV-File are working as expected.
        if self.status != "collecting":
            return

        result = self._redis_client.retrieve_hash(self._redis_key)
        if not result:
            self._logger.error("No sensor value found in Redis for key '%s'.", self._redis_key)
            self._status = "error"
            return
        timestamp, value_bytes = result

        try:
            value = float(value_bytes.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as e:
            self._logger.error(f"Could not decode sensor value from Redis for key '{self._redis_key}': {e}")
            self._status = "error"
            return
        
        try:
            with self._csv_file.open("a", newline="", encoding="utf-8") as csv_file:
                writer = csv.writer(csv_file)
                writer.writerow([timestamp, value])
            self._logger.debug(f"Appended sensor value to CSV file '{self._csv_file}': timestamp='{timestamp}', value='{value}'")
        except (FileNotFoundError, PermissionError, OSError) as e:
            self._logger.error(f"Could not write to CSV file '{self._csv_file}': {e}")
            self._status = "error"
