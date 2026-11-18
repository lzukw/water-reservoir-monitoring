import argparse
import asyncio
import csv
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

from croniter import croniter

from .camera_image_collector import CameraImageCollector
from .redis_client import RedisClient
from .sensor_value_collector import SensorValueCollector


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Collect data from Redis and store it in a local database folder."
    )
    parser.add_argument(
        "--redis-server",
        type=str,
        default="main-raspberry-pi",
        help="DNS-Name or IP of Redis-Server (Default: 'main-raspberry-pi')",
    )
    parser.add_argument(
        "--redis-port",
        type=int,
        default=6379,
        help="Port-Number of Redis-Server (Default: 6379)",
    )
    parser.add_argument(
        "--redis-key-camera",
        type=str,
        default="cam",
        help="First Part of Redis-Key for Camera-Images, whole key will add : and camera-number. (Default: 'cam', whole key will be e.g. 'cam:0' for camera 0)",
    )
    parser.add_argument(
        "--redis-key-water-level",
        type=str,
        default="water-level",
        help="Redis key for water level values. (Default: 'water-level')",
    )
    parser.add_argument(
        "--redis-key-water-meter",
        type=str,
        default="water-meter",
        help="Redis key for water meter values. (Default: 'water-meter')",
    )
    parser.add_argument(
        "--redis-hashkey-timestamp",
        type=str,
        default="timestamp",
        help="Hash(Dicctionary)Key for timestamps. (Default: 'timestamp')",
    )
    parser.add_argument(
        "--redis-hashkey-value",
        type=str,
        default="value",
        help="Hash(Dicctionary)Key for values. (Default: 'value')",
    )
    parser.add_argument(
        "--camera-images-folder",
        type=str,
        default="../../data/camera-images",
        help="Folder for storing camera images (Default: '../../data/camera-images')",
    )
    parser.add_argument(
        "--camera-images-subfolder-prefix",
        type=str,
        default="camera-",
        help="Sub-folder-prefix for storing camera images (Default: 'camera-'",
    )
    parser.add_argument(
        "--water-level-csv-file",
        type=str,
        default="../../data/water-level/sensor-data.csv",
        help="CSV-File for storing water-level values (Default: '../../data/water-level/sensor-data.csv')",
    )
    parser.add_argument(
        "--water-meter-csv-file",
        type=str,
        default="../../data/water-meter/sensor-data.csv",
        help="CSV-File for storing water-meter values (Default: '../../data/water-meter/sensor-data.csv')",
    )
    parser.add_argument(
        "--camera-cron-expression",
        type=str,
        default="0 0 * * *",
        help="Cron-Expression for timestamps for storing camera images in database-folder (Default: '0 0 * * *' = every day at midnight)",
    )
    parser.add_argument(
        "--water-level-cron-expression",
        type=str,
        default="*/5 * * * *",
        help="Cron-Expression for timestamps for storing water-level values in the database-folder (Default: '*/5 * * * *' = every 5 minutes)",
    )
    parser.add_argument(
        "--water-meter-cron-expression",
        type=str,
        default="*/5 * * * *",
        help="Cron-Expression for timestamps for storing water-meter values in the database-folder (Default: '*/5 * * * *' = every 5 minutes)",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        help="Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL). (Default: 'INFO')",
    )
    return parser.parse_args()


ARGS = parse_args()

logging.basicConfig(
    stream=sys.stdout,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    level=ARGS.log_level.upper(),
)
logger = logging.getLogger(__name__)

logger.info(f"Redis-Server and -port: {ARGS.redis_server}:{ARGS.redis_port}")
logger.info(
    f"Redis-key for camera images: '{ARGS.redis_key_camera}:<camera-number>' with hash keys '{ARGS.redis_hashkey_timestamp}' and '{ARGS.redis_hashkey_value}'"
)
logger.info(
    f"Redis-key for water level values: '{ARGS.redis_key_water_level}' with hash keys '{ARGS.redis_hashkey_timestamp}' and '{ARGS.redis_hashkey_value}'"
)
logger.info(
    f"Redis-key for water meter values: '{ARGS.redis_key_water_meter}' with hash keys '{ARGS.redis_hashkey_timestamp}' and '{ARGS.redis_hashkey_value}'"
)
logger.info(f"Camera-images-folder for storing data: '{ARGS.camera_images_folder}'")
logger.info(f"Water-level CSV-file for storing data: '{ARGS.water_level_csv_file}'")
logger.info(f"Water-meter CSV-file for storing data: '{ARGS.water_meter_csv_file}'")
logger.info(
    f"Cron-Expression for water-level values: '{ARGS.water_level_cron_expression}'"
)
logger.info(
    f"Cron-Expression for water-meter values: '{ARGS.water_meter_cron_expression}'"
)
logger.info(f"Cron-Expression for camera-images: '{ARGS.camera_cron_expression}'")


def ensure_csv_file(csv_file: Path | str) -> Path:
    if isinstance(csv_file, str):
        csv_file = Path(csv_file)
    """Create the water-level CSV file with fixed headers if it does not exist yet."""
    if not csv_file.parent.exists() or not csv_file.parent.is_dir():
        raise AssertionError(
            f"Parent folder for water-level CSV file does not exist: {csv_file.parent}"
        )

    to_be_created: bool = False

    if not csv_file.exists():
        to_be_created = True
    else:
        with csv_file.open("r", newline="", encoding="utf-8") as csv_file_read:
            reader = csv.reader(csv_file_read)
            headers = next(reader, None)
            if headers != [ARGS.redis_hashkey_timestamp, ARGS.redis_hashkey_value]:
                logger.warning(
                    f"CSV file '{csv_file}' exists but has different headers: {headers}. It will be overwritten with the correct headers."
                )
                to_be_created = True

    if to_be_created:
        with csv_file.open("w", newline="", encoding="utf-8") as csv_file_write:
            writer = csv.writer(csv_file_write)
            writer.writerow([ARGS.redis_hashkey_timestamp, ARGS.redis_hashkey_value])
        logger.info(f"Created water-level CSV file with headers: {csv_file}")
    return csv_file


def get_camera_image_sub_folder(camera_number: int) -> Path | None:
    """Return the path to the sub-folder for the given camera number. If the sub-folder does not exist,
    return None. The subfolder ist constructed from the camera-images-folder and
    the camera-images-subfolder-prefix and the camera number."""
    camera_images_path = Path(ARGS.camera_images_folder)
    if not camera_images_path.exists() or not camera_images_path.is_dir():
        raise AssertionError(
            f"Camera-images-folder does not exist: {ARGS.camera_images_folder}"
        )
    sub_folder = (
        camera_images_path / f"{ARGS.camera_images_subfolder_prefix}{camera_number}"
    )
    if not sub_folder.exists():
        return None
    elif not sub_folder.is_dir():
        logger.error(
            f"Sub-folder for camera {camera_number} is not a directory: {sub_folder}"
        )
        return None
    return sub_folder


async def connect_redis_forever(redis_client: RedisClient):
    """Connect to the Redis server and keep it connected. If the connection is lost, try to reconnect."""
    while True:
        redis_client.connect()
        await asyncio.sleep(5)


async def collect_sensor_value_forever(
    sensor_value_collector: SensorValueCollector, cron_expression: str
):
    """Collect water-level values from Redis and store them in the CSV file according to the cron expression."""

    now = datetime.now(UTC)
    cron = croniter(cron_expression, now)

    while True:
        next_run = cron.get_next(datetime)
        sleep_time = (next_run - datetime.now(UTC)).total_seconds()
        await asyncio.sleep(
            sleep_time + 5
        )  # a few seconds buffer to ensure the next run is in the future
        logger.debug(
            f"Collecting sensor value for '{sensor_value_collector.name}' at {datetime.now(UTC)} according to cron expression '{cron_expression}'"
        )
        sensor_value_collector.collect()


async def collect_camera_image_forever(
    camera_image_collector: CameraImageCollector, cron_expression: str
):
    """Collect camera images from Redis and store them in the camera-images-folder according to the cron expression."""

    now = datetime.now(UTC)
    cron = croniter(cron_expression, now)

    while True:
        next_run = cron.get_next(datetime)
        sleep_time = (next_run - datetime.now(UTC)).total_seconds()
        await asyncio.sleep(
            sleep_time + 5
        )  # a few seconds buffer to ensure the next run is in the future
        logger.debug(
            f"Collecting camera image for '{camera_image_collector.camera_number}' at {datetime.now(UTC)} according to cron expression '{cron_expression}'"
        )
        camera_image_collector.collect()


async def main():
    """Main function to ensure all necessary files and folders exist."""
    water_level_csv_path: Path = ensure_csv_file(ARGS.water_level_csv_file)
    water_meter_csv_path: Path = ensure_csv_file(ARGS.water_meter_csv_file)

    redis_client: RedisClient = RedisClient(
        server=ARGS.redis_server,
        port=ARGS.redis_port,
        hashkey_timestamp=ARGS.redis_hashkey_timestamp,
        hashkey_value=ARGS.redis_hashkey_value,
        log_level=logger.level,
    )

    redis_task = asyncio.create_task(connect_redis_forever(redis_client))
    await asyncio.sleep(1)  # Give some time for Redis to initialize

    water_level_collector = SensorValueCollector(
        name="Water Level Collector",
        csv_file=water_level_csv_path,
        redis_client=redis_client,
        redis_key=ARGS.redis_key_water_level,
        csv_header_timestamp=ARGS.redis_hashkey_timestamp,
        csv_header_value=ARGS.redis_hashkey_value,
        log_level=logger.level,
    )

    water_level_task = asyncio.create_task(
        collect_sensor_value_forever(
            water_level_collector, ARGS.water_level_cron_expression
        )
    )

    water_meter_collector = SensorValueCollector(
        name="Water Meter Collector",
        csv_file=water_meter_csv_path,
        redis_client=redis_client,
        redis_key=ARGS.redis_key_water_meter,
        csv_header_timestamp=ARGS.redis_hashkey_timestamp,
        csv_header_value=ARGS.redis_hashkey_value,
        log_level=logger.level,
    )

    water_meter_task = asyncio.create_task(
        collect_sensor_value_forever(
            water_meter_collector, ARGS.water_meter_cron_expression
        )
    )

    # Hardcoded. Camera-Numbers are between 0 and 9.
    camera_image_collector_tasks: list[asyncio.Task] = []
    for camera_number in range(10):
        sub_folder = get_camera_image_sub_folder(camera_number)
        if sub_folder is None:
            continue
        camera_image_collector = CameraImageCollector(
            camera_number=camera_number,
            camera_images_subfolder=sub_folder,
            redis_client=redis_client,
            redis_key=f"{ARGS.redis_key_camera}:{camera_number}",
            redis_hashkey_timestamp=ARGS.redis_hashkey_timestamp,
            redis_hashkey_value=ARGS.redis_hashkey_value,
            log_level=logger.level,
        )
        camera_image_collector_task = asyncio.create_task(
            collect_camera_image_forever(
                camera_image_collector, ARGS.camera_cron_expression
            )
        )
        camera_image_collector_tasks.append(camera_image_collector_task)

    try:
        await asyncio.gather(
            redis_task,
            water_level_task,
            water_meter_task,
            *camera_image_collector_tasks,
        )
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt received, shutting down...")
    except asyncio.CancelledError:
        logger.info("Tasks cancelled, shutting down...")


asyncio.run(main())
