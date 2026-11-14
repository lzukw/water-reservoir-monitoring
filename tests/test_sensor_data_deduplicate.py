import importlib.util
from datetime import UTC, datetime
from pathlib import Path

module_path = Path(__file__).resolve().parents[1] / "website" / "sensor_data.py"
spec = importlib.util.spec_from_file_location("sensor_data_under_test", module_path)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
SensorData = module.SensorData


def test_get_sensor_rows_removes_duplicate_timestamps(tmp_path: Path):
    csv_path = tmp_path / "sensor-data.csv"
    csv_path.write_text(
        "timestamp,value\n"
        "2024-01-01T00:00:00Z,10\n"
        "2024-01-01T00:00:00Z,99\n"
        "2024-01-01T00:00:01Z,20\n",
        encoding="utf-8",
    )

    sensor_data = SensorData(csv_path=csv_path, redis_key="water-meter")
    rows = sensor_data.get_sensor_rows()

    assert len(rows) == 2
    assert rows[0]["timestamp"] == datetime.fromisoformat("2024-01-01T00:00:00+00:00")
    assert rows[0]["value"] == 10.0
    assert rows[1]["timestamp"] == datetime.fromisoformat("2024-01-01T00:00:01+00:00")
    assert rows[1]["value"] == 20.0
