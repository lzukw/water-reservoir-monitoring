"""Create a dummy database with test data for the water-reservoir-monitoring project.

The script generates (all timestamps in UTC):

- ``data/water-level/sensor-data.csv``: dummy water-level values (random between
  4.0 and 6.0), one row every 5 minutes
- ``data/water-meter/sensor-data.csv``: dummy water-meter readings (continuously
  increasing), one row every 5 minutes
- ``data/camera-images/camera-<N>/<timestamp>.jpg``: one random dummy image per
  day, for each used camera number N

The time range and the used camera numbers can be set via command-line
arguments, for example:

    python 060_create-dummy-database.py
    python 060_create-dummy-database.py --start-date 01.01.2026 --stop-date 31.01.2026 --camera-numbers 0 1
    python 060_create-dummy-database.py --start-date 2026-03-01 --stop-date 2026-03-07 --camera-numbers 2

Dates are accepted as DD.MM.YYYY or YYYY-MM-DD and are interpreted as UTC.
The stop date is inclusive: the whole stop day is generated.

Before creating new files, all existing files in ``data/`` are removed
(except ``.gitkeep`` files).
"""

from __future__ import annotations

import argparse
import csv
import random
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import cv2
import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"

CSV_STEP = timedelta(minutes=5)
IMAGE_STEP = timedelta(days=1)


def parse_date(text: str) -> datetime:
    """Parse a date given as DD.MM.YYYY or YYYY-MM-DD and return it as midnight UTC."""
    for date_format in ("%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, date_format).replace(tzinfo=UTC)
        except ValueError:
            continue
    raise argparse.ArgumentTypeError(
        f"invalid date {text!r}: expected DD.MM.YYYY or YYYY-MM-DD"
    )


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Create a dummy database with test data: CSV sensor-data (water-level, water-meter) with one row every 5 minutes, and one dummy camera image per day in data/camera-images/camera-<N>."
    )
    parser.add_argument(
        "--start-date",
        type=parse_date,
        default="01.01.2026",
        help="First day to generate (inclusive), format DD.MM.YYYY or YYYY-MM-DD, interpreted as UTC. (Default: 01.01.2026)",
    )
    parser.add_argument(
        "--stop-date",
        type=parse_date,
        default="31.01.2026",
        help="Last day to generate (inclusive, the whole day), format DD.MM.YYYY or YYYY-MM-DD, interpreted as UTC. (Default: 31.01.2026)",
    )
    parser.add_argument(
        "--camera-numbers",
        type=int,
        nargs="+",
        default=[0, 1, 2],
        metavar="N",
        help="Camera numbers to generate dummy images for; for each number N the folder data/camera-images/camera-N is filled. (Default: 0 1 2)",
    )
    args = parser.parse_args()
    if args.start_date > args.stop_date:
        parser.error("--start-date must not be after --stop-date")
    return args


def create_csv(start: datetime, stop: datetime) -> None:
    """Create the dummy CSV sensor-data files (water-level and water-meter).

    One row every 5 minutes for all timestamps from `start` to `stop` (inclusive, UTC).
    """
    data_dir = DATA_DIR
    data_dir.mkdir(parents=False, exist_ok=True)

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

            print(
                f"Create CSV-File {output_file} from {start:%Y-%m-%d %H:%M} to {stop:%Y-%m-%d %H:%M} UTC",
                flush=True,
            )
            last_year = None
            current = start
            while current <= stop:
                if output_file == water_level_file:
                    value = random.uniform(4.0, 6.0)
                else:
                    water_meter_value += base_increment * random.uniform(0.8, 1.2)
                    value = water_meter_value

                writer.writerow(
                    [
                        current.isoformat(timespec="milliseconds").replace(
                            "+00:00", "Z"
                        ),
                        f"{value:.3f}",
                    ]
                )

                if last_year is None or current.year != last_year:
                    print(f"  Year: {current.year}", flush=True)
                    last_year = current.year
                current += CSV_STEP
        print()


def create_images(camera_folder: Path, start: datetime, stop: datetime) -> None:
    """Create one dummy JPG image per day inside the given single `camera_folder`.

    Files are created only in `camera_folder` (e.g. data/camera-images/camera-0):
    one image per day for all timestamps from `start` to `stop` (inclusive, UTC).
    """
    camera_folder.mkdir(parents=False, exist_ok=True)

    print(f"Creating image files in {camera_folder} ...", flush=True)
    last_year = None
    current = start
    while current <= stop:
        filename = (
            f"{current.isoformat(timespec='milliseconds').replace('+00:00', 'Z')}.jpg"
        )

        image = np.random.randint(0, 256, (480, 640, 3), dtype=np.uint8)
        if not cv2.imwrite(str(camera_folder / filename), image):
            raise RuntimeError(f"Failed to write image: {camera_folder / filename}")

        if last_year is None or current.year != last_year:
            print(f"  Year: {current.year}", flush=True)
            last_year = current.year
        current += IMAGE_STEP
    print()


def clean_data_dir() -> None:
    """Remove all files in the data directory, except .gitkeep files."""
    if not DATA_DIR.is_dir():
        return
    removed_files = 0
    for path in DATA_DIR.rglob("*"):
        if path.is_file() and path.name != ".gitkeep":
            path.unlink()
            removed_files += 1
    # Remove directories left empty (e.g. stale camera folders without .gitkeep).
    for path in sorted(DATA_DIR.rglob("*"), reverse=True):
        if path.is_dir() and not any(path.iterdir()):
            shutil.rmtree(path)
    print(
        f"Removed {removed_files} old file(s) from {DATA_DIR} (.gitkeep files kept)",
        flush=True,
    )


def main() -> None:
    args = parse_args()

    start = args.start_date
    # Include the whole stop day: generate up to the end of --stop-date (UTC).
    stop = args.stop_date.replace(hour=23, minute=59, second=59, microsecond=999000)

    clean_data_dir()

    create_csv(start, stop)

    camera_images_dir = DATA_DIR / "camera-images"
    camera_images_dir.mkdir(parents=False, exist_ok=True)
    for camera_number in args.camera_numbers:
        create_images(camera_images_dir / f"camera-{camera_number}", start, stop)


if __name__ == "__main__":
    main()
