import argparse  # noqa: I001
import asyncio
import json
import logging
import sys
from datetime import UTC, datetime

from .abstract_camera import AbstractCamera

from .redis_client import RedisClient

### Select the camera class to use. You can change this to DummyCamera, PiCamera, or UsbCamera as needed.
from .pi_camera import PiCamera as Camera
#from .usb_camera import UsbCamera as Camera
#from .dummy_camera import DummyCamera as Camera


### Parse command line arguments
def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description="Read images from a camera and store them in Redis.")
    parser.add_argument("--device", type=str, default="0", help="Camera device path. (Default: '0', which is the first camera device)")
    parser.add_argument("--camera-width", type=int, default=640, help="Camera-width in pixels. (Default: 640)")
    parser.add_argument("--camera-height", type=int, default=480, help="Camera-height in pixels. (Default: 480)")
    parser.add_argument("--camera-period", type=float, default=0.2, help="Time-Interval in seconds for storing images to Redis-Database. (Default: 0.2)")
    parser.add_argument("--rois-to-mark", type=str, default="[ [0,0,640,480] ]", help="Regions of Interrest (ROIs) that will be marked with rectangles in the image as json-List of Lists [left, upper, width, height]-Pixels (Default: '[ [0,0,640,480] ]', which is poduces rectange outside the image-area, and therefore is not visible")
    parser.add_argument("--redis-server", type=str, default="main-raspberry-pi", help="DNS-Name or IP of Redis-Server. (Default: 'main-raspberry-pi')")
    parser.add_argument("--redis-port", type=int, default=6379, help="Port-Number of Redis-Server. (Default: 6379)")
    parser.add_argument("--redis-key-camera", type=str, default="cam", help="First Part of Redis-Key, whole key will add : and camera-number. (Default: 'cam', whole key will be e.g. 'cam:0' for camera 0)")
    parser.add_argument("--camera-number", type=int, default=0, help="Global camera number for Redis. (Default: 0)")
    parser.add_argument("--redis-hashkey-timestamp", type=str, default="timestamp", help="Hash(Dicctionary)Key for timestamps. (Default: 'timestamp')")
    parser.add_argument("--redis-hashkey-value", type=str, default="value", help="Hash(Dicctionary)Key for image-bytes. (Default: 'value')")
    parser.add_argument("--log-level", type=str, default="INFO", help="Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)")
    return parser.parse_args()

ARGS = parse_args()

### Configure logging
logging.basicConfig(
    stream=sys.stdout,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    level=ARGS.log_level.upper()
)
logger = logging.getLogger(__name__)

### Extract ROIs to mark from command line argument
rois_to_mark_json : list[list[int]]
try:
    rois_to_mark_json = [list(row) for row in json.loads(ARGS.rois_to_mark)]
except json.JSONDecodeError:
    logger.error(f"Argument for --rois-to-mark is invalid JSON: {ARGS.rois_to_mark}")
    rois_to_mark_json = [ ]

if not all(len(row) == 4 and all(type(x) is int for x in row) for row in rois_to_mark_json):    
    logger.error(f"Argument for --rois-to-mark is invalid: {ARGS.rois_to_mark}")
    rois_to_mark_json = [ ]

rois_to_mark :list[tuple[int,int,int,int]] = [ ] 
for roi in rois_to_mark_json:
    roi_tuple = tuple(roi)
    assert len(roi_tuple) == 4
    rois_to_mark.append(roi_tuple)

logger.info(f"Using camera device: {ARGS.device}, width: {ARGS.camera_width}, height: {ARGS.camera_height}, period for storing in Redis: {ARGS.camera_period}s")
logger.info(f"ROIs to mark: {rois_to_mark}")
logger.info(f"Redis server: {ARGS.redis_server}:{ARGS.redis_port}, Redis key: {ARGS.redis_key_camera}:{ARGS.camera_number}, hashkey for timestamp: {ARGS.redis_hashkey_timestamp}, hashkey for value: {ARGS.redis_hashkey_value}")

### Extract camera device number or path from command line argument
try:
    camera_device : int | str = int(ARGS.device)
except ValueError:
    camera_device: int | str = ARGS.device


### Loops for camera and redis

async def open_camera_forever(camera: AbstractCamera):
    """Open the camera and keep it open. If the camera is closed or in error state, try to reopen it."""
    while True:
        camera.open()
        await asyncio.sleep(5)

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
        log_level=ARGS.log_level.upper()
    )


    # Initialize camera
    camera: AbstractCamera = Camera(
        device=camera_device,
        width=ARGS.camera_width,
        height=ARGS.camera_height,
        rois=rois_to_mark,
        log_level=ARGS.log_level.upper()
    )

    asyncio.create_task(open_camera_forever(camera))
    asyncio.create_task(connect_redis_forever(redis_client))
    await asyncio.sleep(1)  # Give some time for camera and Redis to initialize

    next_redis_store_timestamp = datetime.now(UTC).timestamp()

    try:
        while True:
            # Capture image from the camera
            image_bytes = camera.read_frame_encoded()
            frame_timestamp = datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")  # ISO 8601 format with 'Z' for UTC
            await asyncio.sleep(0.02)

            if datetime.now(UTC).timestamp() >= next_redis_store_timestamp:
                next_redis_store_timestamp += ARGS.camera_period
                # Store image and timestamp in Redis
                redis_client.store_hash(f"{ARGS.redis_key_camera}:{ARGS.camera_number}", frame_timestamp, image_bytes)

    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("Shutting down...")
    finally:
       camera.close()
       redis_client.disconnect()

asyncio.run(main())
