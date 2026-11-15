"""Read Image from the camera filming the water meter (from the
Redis-Database), extract the meter reading and store it into the
REDIS-Database."""

import argparse
import asyncio
import logging
import sys

from .redis_client import RedisClient
from .water_meter_value_extraction import WaterMeterValueExtraction


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Read meter value from camera image stored in Redis and write result to Redis."
    )
    parser.add_argument(
        "--period",
        type=float,
        default=5.0,
        help="Delay between read attempts in seconds. (Default: 5.0)",
    )
    parser.add_argument(
        "--redis-server",
        type=str,
        default="127.0.0.1",
        help="DNS-Name or IP of Redis-Server. (Default: '127.0.0.1')",
    )
    parser.add_argument(
        "--redis-port",
        type=int,
        default=6379,
        help="Port-Number of Redis-Server. (Default: 6379)",
    )
    parser.add_argument(
        "--redis-key-camera",
        type=str,
        default="cam",
        help="First part of Redis camera key, camera number will be appended with ':'. (Default: 'cam', whole key will be e.g. 'cam:0' for camera 0)",
    )
    parser.add_argument(
        "--camera-number",
        type=int,
        default=0,
        help="Global camera number. (Default: 0)",
    )
    parser.add_argument(
        "--store-camera-number",
        type=int,
        default=2,
        help="The extracted ROI with digits is stored as camera-image with this camer-number (Default: 2)",
    )
    parser.add_argument(
        "--redis-key-water-meter",
        type=str,
        default="water-meter",
        help="Redis key for water meter. (Default: 'water-meter')",
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
        "--reference-image-jsonfile",
        type=str,
        default="./water_meter/reference-image-json",
        help="Filepath to json-File describing the Reference-Image (Filepath to image and ROI)",
    )
    parser.add_argument(
        "--wrap-around-value",
        type=float,
        default=1000000.0,
        help="Wrap-around value for the water meter. (Default: 1000000.0)",
    )
    parser.add_argument(
        "--min-accuracy",
        type=float,
        default=0.9,
        help="Minimum accepted OCR/reading accuracy. (Default: 0.9)",
    )
    parser.add_argument(
        "--approx-daily-consumption",
        type=float,
        default=200.0,
        help="Appriximate daily water-consumption in m³. Used for plausability-check of read meter-value.",
    )
    parser.add_argument(
        "--allowed-change-factor",
        type=float,
        default=5.0,
        help=(
            "Based on the approx. daily consumption a maximum allowed increase in the read "
            "meter-value is calculated with this factor. Used for plausability-check of "
            "read meter-value. Default: 5.0"
        ),
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        help="Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)",
    )
    return parser.parse_args()


ARGS = parse_args()

logging.basicConfig(
    stream=sys.stdout,
    level=ARGS.log_level.upper(),
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

logger.info(
    f"Parameters for Water Meter Value Extraction: Minimum Accuracy: {ARGS.min_accuracy:.2f}, Period: {ARGS.period:.2f} seconds, Wrap-Around Value: {ARGS.wrap_around_value:.2f}"
)
logger.info(f"Redis-Server and -port: {ARGS.redis_server}:{ARGS.redis_port}")
logger.info(
    f"Water-Meter-Camera-Redis-Key: '{ARGS.redis_key_camera}:{ARGS.camera_number}' with hash keys '{ARGS.redis_hashkey_timestamp}' and '{ARGS.redis_hashkey_value}'"
)
logger.info(
    f"Water-Meter-Store-Camera-Redis-Key: '{ARGS.redis_key_camera}:{ARGS.store_camera_number}' with hash keys '{ARGS.redis_hashkey_timestamp}' and '{ARGS.redis_hashkey_value}'"
)
logger.info(
    f"Redis-Key for Water-Meter-Value: '{ARGS.redis_key_water_meter}' with hash keys '{ARGS.redis_hashkey_timestamp}' and '{ARGS.redis_hashkey_value}'"
)


async def connect_redis_forever(redis_client: RedisClient):
    """Connect to the Redis server and keep it connected. If the connection is lost, try to reconnect."""
    while True:
        redis_client.connect()
        await asyncio.sleep(5)


### Main Program
async def main():
    """Main function to read images from the camera and store them in Redis."""
    # Initialize Redis client
    redis_client = RedisClient(
        server=ARGS.redis_server,
        port=ARGS.redis_port,
        hashkey_timestamp=ARGS.redis_hashkey_timestamp,
        hashkey_value=ARGS.redis_hashkey_value,
        log_level=ARGS.log_level.upper(),
    )

    water_meter_value_extractor = WaterMeterValueExtraction(
        redis_client=redis_client,
        redis_key_cam=f"{ARGS.redis_key_camera}:{ARGS.camera_number}",
        redis_key_meter=ARGS.redis_key_water_meter,
        ref_image_json_path=ARGS.reference_image_jsonfile,
        store_redis_key_cam=f"{ARGS.redis_key_camera}:{ARGS.store_camera_number}",
        wrap_around_value=ARGS.wrap_around_value,
        min_accuracy=ARGS.min_accuracy,
        approx_daily_consumption=ARGS.approx_daily_consumption,
        allowed_change_factor=ARGS.allowed_change_factor,
        log_level=ARGS.log_level.upper(),
    )

    asyncio.create_task(connect_redis_forever(redis_client))
    # Give the Redis client a moment to connect before starting the extraction loop
    await asyncio.sleep(1)

    try:
        while True:
            water_meter_value_extractor.extract_and_store_value()
            await asyncio.sleep(ARGS.period)

    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("Shutting down...")
    finally:
        redis_client.disconnect()


asyncio.run(main())
