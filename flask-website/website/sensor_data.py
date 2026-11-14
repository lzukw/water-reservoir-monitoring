import csv
import logging
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import redis

__all__ = ["SensorData"]


class SensorData:
    
    def __init__(
        self,
        csv_path: str | Path,
        redis_key: str,
        redis_server: str = "127.0.0.1",
        redis_port: int = 6379,
        redis_hashkey_timestamp: str = "timestamp",
        redis_hashkey_value: str = "value",
        log_level: str = "INFO",
    ):
        self._csv_path : Path = Path(csv_path)
        self._redis_key : str = redis_key
        self._redis_server : str = redis_server
        self._redis_port : int = redis_port
        self._redis_hashkey_timestamp : str = redis_hashkey_timestamp
        self._redis_hashkey_value : str = redis_hashkey_value

        logging.basicConfig(
            stream=sys.stdout,
            format="%(asctime)s [%(levelname)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
            level=log_level.upper(),
        )
        self._logger = logging.getLogger(__name__)

        self._redis_client = redis.Redis(host=self._redis_server, port=self._redis_port)
        try:
            self._redis_client.ping()
        except redis.RedisError as e:
            self._logger.error(f"[REDIS] Error while connecting to Redis: {e}")

        # Check CSV file for required columns
        if self._csv_path.exists():
            with self._csv_path.open("r", newline="", encoding="utf-8") as csv_file:
                reader = csv.DictReader(csv_file)
                for i, row in enumerate(reader):
                    if i >= 100:
                        break
                    for column in (self._redis_hashkey_timestamp, self._redis_hashkey_value):
                        if column not in row:
                            self._logger.error(f"CSV: Missing column '{column}' in row {i + 1}")

    def get_sensor_rows(
        self,
        start_timestamp: datetime | None = None,
        end_timestamp: datetime | None = None,
    ) -> list[dict[str, datetime | float| None]]:
        if not self._csv_path.exists():
            return []

        if start_timestamp is None:
            start_timestamp = datetime.min.replace(tzinfo=UTC)
        if end_timestamp is None:
            end_timestamp = datetime.max.replace(tzinfo=UTC)

        rows: list[dict[str, datetime | float| None]] = []
        seen_timestamps: set[datetime] = set()
        with self._csv_path.open("r", newline="", encoding="utf-8") as csv_file:
            reader = csv.DictReader(csv_file)
            for row in reader:
                assert isinstance(row, dict), f"Expected dict for CSV row, got {type(row)}" 
                assert self._redis_hashkey_timestamp in row, f"Missing {self._redis_hashkey_timestamp} column in CSV row: {row}"
                assert self._redis_hashkey_value in row, f"Missing {self._redis_hashkey_value} column in CSV row: {row}"
                row_timestamp_str = row[self._redis_hashkey_timestamp]
                if row_timestamp_str in (None, ""):
                    self._logger.warning(f"CSV: Missing timestamp in row: {row}")
                    continue

                try:
                    row_timestamp : datetime = datetime.fromisoformat(row_timestamp_str)
                    if row_timestamp.tzinfo is None:
                        self._logger.warning(f"CSV: Timestamp '{row_timestamp_str}' in csv-File is naive (no timezone info). Assuming UTC. Row of csv-File: {row}")
                        row_timestamp = row_timestamp.replace(tzinfo=UTC)
                except ValueError:
                    continue
                if start_timestamp <= row_timestamp <= end_timestamp and row_timestamp not in seen_timestamps:
                    seen_timestamps.add(row_timestamp)
                    value : float | None
                    row_value = row[self._redis_hashkey_value]
                    if row_value in (None, ""):
                        self._logger.warning(f"CSV: Missing value in row: {row}")
                        value = None
                    else:
                        try:
                            value = float(row_value)
                        except ValueError:
                            self._logger.warning(f"CSV: Invalid float value '{row_value}' in row: {row}")
                            value = None
                    rows.append({
                        self._redis_hashkey_timestamp: row_timestamp,
                        self._redis_hashkey_value: value
                    })

        rows.sort(key=lambda x: x.get(self._redis_hashkey_timestamp) or datetime.min.replace(tzinfo=UTC))   
        return rows

    def get_values_before(self, list_of_timestamps: list[datetime]) -> list[dict[str, datetime | float | None]]:
        """For each timestamp in list_of_timestamps, return the latest sensor value and its timestamp 
        before that timestamp. Sensor values and their timestamps are read from the CSV file."""
        if not list_of_timestamps:
            return []

        begin_timestamp = min(list_of_timestamps) - timedelta(days=2)
        end_timestamp = max(list_of_timestamps) + timedelta(days=1)
        rows : list[dict[str, datetime | float | None]] = self.get_sensor_rows(begin_timestamp, end_timestamp+timedelta(days=1))

        values: list[dict[str, datetime | float | None]] = []
        seen_timestamps: set[datetime] = set()
        for ts in list_of_timestamps:
            latest_row : dict[str, datetime | float | None] | None = None
            latest_row_timestamp: datetime | None = None
            for row in rows:
                row_timestamp = row.get(self._redis_hashkey_timestamp)
                assert isinstance(row_timestamp, datetime), f"Expected datetime for row timestamp, got {type(row_timestamp)}"
                if row_timestamp <= ts and (latest_row_timestamp is None or row_timestamp > latest_row_timestamp):
                    latest_row_timestamp = row_timestamp
                    latest_row = row
               
            if latest_row is not None and latest_row_timestamp is not None and latest_row_timestamp not in seen_timestamps:
                seen_timestamps.add(latest_row_timestamp)
                values.append(latest_row)
            else:
                values.append({self._redis_hashkey_timestamp: None, self._redis_hashkey_value: None})

        values.sort(key=lambda x: x.get(self._redis_hashkey_timestamp) or datetime.min.replace(tzinfo=UTC))
        return values

    def get_daily_value_increases(self, start_timestamp: datetime, end_timestamp: datetime) -> list[dict[str, datetime | float | None]]:
        if end_timestamp < start_timestamp:
            start_timestamp, end_timestamp = end_timestamp, start_timestamp

        current_dt = start_timestamp.replace(hour=0, minute=0, second=0, microsecond=0)
        end_dt = end_timestamp.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)

        timestamps: list[datetime] = []
        while current_dt <= end_dt:
            timestamps.append(current_dt)
            current_dt += timedelta(days=1)

        sensor_values : list[dict[str, datetime | float | None]] = self.get_values_before(timestamps)
        daily_value_increase_data: list[dict[str, datetime | float | None]] = []

        for i in range(len(timestamps)-1):
            val_start = sensor_values[i].get(self._redis_hashkey_value)
            val_end = sensor_values[i + 1].get(self._redis_hashkey_value)
            if  val_start is not None and val_end is not None:
                assert isinstance(val_end, float) and isinstance(val_start, float), "Expected float values for water meter readings"
                daily_value_increase: float = val_end - val_start
                daily_value_increase_data.append({
                    "date": timestamps[i],
                    "consumption": daily_value_increase,
                })
            else:
                daily_value_increase_data.append({
                    "date": timestamps[i],
                    "consumption": None,
                })

        return daily_value_increase_data

    def get_latest_sensor_value_from_csv(self) -> dict[str, datetime | float | None]:
        rows = self.get_sensor_rows(datetime.now(UTC) - timedelta(days=2), datetime.now(UTC))
        if not rows:
            return {self._redis_hashkey_timestamp: None, self._redis_hashkey_value: None}

        return rows[-1]

    def get_latest_sensor_value_from_redis(self) -> dict[str, datetime | float | None]:
        empty_retval: dict[str, datetime | float | None] = {self._redis_hashkey_timestamp: None, self._redis_hashkey_value: None}
        try:
            sensor_data = self._redis_client.hgetall(self._redis_key)
            if not isinstance(sensor_data, dict):
                self._logger.error(f"Redis: Expected dict for sensor data, got {type(sensor_data)}")
                return empty_retval
            if self._redis_hashkey_timestamp.encode() not in sensor_data:
                self._logger.error(f"Redis: Missing timestamp for key {self._redis_key}: {sensor_data}")
                return empty_retval
            if self._redis_hashkey_value.encode() not in sensor_data:
                self._logger.error(f"Redis: Missing value for key {self._redis_key}: {sensor_data}")
                return empty_retval
        except redis.RedisError as e:
            self._logger.error(f"[REDIS] Error while reading sensor data from Redis: {e}")
            return empty_retval


        timestamp_bytes = sensor_data[self._redis_hashkey_timestamp.encode()]
        value_bytes = sensor_data[self._redis_hashkey_value.encode()]
        assert isinstance(timestamp_bytes, bytes), f"Expected bytes for timestamp, got {type(timestamp_bytes)}"
        assert isinstance(value_bytes, bytes), f"Expected bytes for value, got {type(value_bytes)}"
        value_str = value_bytes.decode("utf-8")
        timestamp_str = timestamp_bytes.decode("utf-8")

        try:
            timestamp = datetime.fromisoformat(timestamp_str)
        except ValueError:
            self._logger.error(f"Redis: Invalid timestamp for key {self._redis_key} - {self._redis_hashkey_timestamp}: '{timestamp_str}'")
            return empty_retval

        try:
            value_float = float(value_str)
        except ValueError:
            self._logger.error(f"Redis: Invalid float value for key {self._redis_key} - {self._redis_hashkey_value}: '{value_str}'")
            return {self._redis_hashkey_timestamp: timestamp, self._redis_hashkey_value: None}

        return { self._redis_hashkey_timestamp: timestamp, self._redis_hashkey_value: value_float }
    

if __name__ == "__main__":
    # Example usage
    sensor_data = SensorData(
        csv_path=Path(__file__).parents[1] / "data" / "water-meter" / "sensor-data.csv",
        redis_key="water-meter",
        redis_server="main-raspberry-pi",
        redis_port=6379,
        log_level="DEBUG"
    )

    latest_csv_value = sensor_data.get_latest_sensor_value_from_csv()
    print(f"Latest sensor value from CSV: {latest_csv_value}")

    latest_redis_value = sensor_data.get_latest_sensor_value_from_redis()
    print(f"Latest sensor value from Redis: {latest_redis_value}")

    import pprint
    sensor_rows = sensor_data.get_sensor_rows( datetime.now(UTC) - timedelta(days=1), datetime.now(UTC))
    print("Sensor rows from CSV in the last 24 hours:")
    pprint.pprint(sensor_rows)

    timestamps: list[datetime] = [
        datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=i) for i in range(7)
    ]      

    values = sensor_data.get_values_before(timestamps)
    print("Latest sensor values before the given timestamps:")
    pprint.pprint(values)

    daily_value_increases = sensor_data.get_daily_value_increases(datetime.now(UTC) - timedelta(days=7), datetime.now(UTC))
    print("Daily value-increases for the last 7 days:")
    pprint.pprint(daily_value_increases)
