from __future__ import annotations

import csv
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path

import cv2
import numpy as np

YEARS_TO_GENERATE = 0.2


def create_csv() -> None:
	project_root = Path(__file__).resolve().parents[2]
	data_dir = project_root / "data"
	data_dir.mkdir(parents=False, exist_ok=True)

	now = datetime.now(UTC).replace(second=0, microsecond=0)
	start = now - timedelta(days=int(365 * YEARS_TO_GENERATE))
	step = timedelta(minutes=5)

	water_level_dir = data_dir / "water-level"
	water_meter_dir = data_dir / "water-meter"
	water_level_dir.mkdir(parents=False, exist_ok=True)
	water_meter_dir.mkdir(parents=False, exist_ok=True)

	water_level_file = water_level_dir / "sensor-data.csv"
	water_meter_file = water_meter_dir / "sensor-data.csv"

	water_meter_value: float = 1000.0
	base_increment: float = 200.0 * 5 / (24 * 60)

	for output_file in [water_level_file, water_meter_file]:
		with output_file.open("w", newline="", encoding="utf-8") as f:
			writer = csv.writer(f)
			writer.writerow(["timestamp", "value"])

			print(f"Create CSV-File {output_file}", flush=True)
			last_year = None
			current = start
			while current <= now:
				if output_file == water_level_file:
					value = random.uniform(4.0, 6.0)
				else:
					water_meter_value += base_increment * random.uniform(0.8, 1.2)
					value = water_meter_value

				writer.writerow([
					current.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
					f"{value:.3f}",
				])

				if last_year is None or current.year != last_year:
					print(f"  Year: {current.year}", flush=True)
					last_year = current.year
				current += step
		print()

def create_images() -> None:
	project_root = Path(__file__).resolve().parents[2]
	data_dir = project_root / "data" / "camera-images"
	data_dir.mkdir(parents=False, exist_ok=True)

	cam_folders = [data_dir / f"camera-{i}" for i in range(2)]
	for folder in cam_folders:
		folder.mkdir(parents=False, exist_ok=True)

	now = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
	start = now - timedelta(days=int(365 * YEARS_TO_GENERATE))

	print("Creating image files...", flush=True)
	last_year = None
	current = start
	while current <= now:
		filename = f'{current.isoformat(timespec="milliseconds").replace("+00:00", "Z")}.jpg'

		for folder in cam_folders:
			image = np.random.randint(0, 256, (480, 640, 3), dtype=np.uint8)
			if not cv2.imwrite(str(folder / filename), image):
				raise RuntimeError(f"Failed to write image: {folder / filename}")

		if last_year is None or current.year != last_year:
			print(f"  Year: {current.year}", flush=True)
			last_year = current.year
		current += timedelta(days=1)
	print()

if __name__ == "__main__":
	create_csv()
	create_images()
