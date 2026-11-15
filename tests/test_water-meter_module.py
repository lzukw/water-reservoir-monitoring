"""Integration test for the water_meter module.

Steps:
1. Start a redis-server listening on localhost (on a free port).
2. Store a water-meter photo as camera-image '<redis-key-camera>:<camera-number>'
   (default 'cam:0') in Redis.
3. Start the water_meter module (python -m water_meter) as a subprocess.
4. Start a parallel task displaying the '<redis-key-camera>:<store-camera-number>'
   image (default 'cam:2', the extracted digits-ROI stored by the water_meter
   module) in an OpenCV window.
5. Read the water-meter-value stored by the water_meter module from Redis
   (key 'water-meter') and print it whenever it changes.
6. After --duration seconds, stop the module, the display and the redis-server.

Run with the project virtualenv from the repository root, e.g.:
    .venv/bin/python tests/test_water-meter_module.py
"""

import argparse
import logging
import socket
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import cv2
import numpy as np
import redis

PROJECT_ROOT = Path(__file__).resolve().parents[1]
WATER_METER_DIR = PROJECT_ROOT / "water-meter"
DEFAULT_IMAGE = (
    "water-meter/Experiments/data/2026-10-32_Fotos_water-meter/IMG_20261029_121519.jpg"
)


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Integration test for the water_meter module: local redis-server, one cam-image, module subprocess, display of extracted digits-image and print of meter-value."
    )
    parser.add_argument(
        "--redis-server",
        type=str,
        default="127.0.0.1",
        help="Address the test redis-server binds to. (Default: '127.0.0.1')",
    )
    parser.add_argument(
        "--redis-port",
        type=int,
        default=0,
        help="Port of the test redis-server. 0 = pick a free port automatically. (Default: 0)",
    )
    parser.add_argument(
        "--image",
        type=str,
        default=DEFAULT_IMAGE,
        help=f"Water-meter photo stored as cam-image in Redis (relative to repo-root or absolute). (Default: '{DEFAULT_IMAGE}')",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=30.0,
        help="Stop the test after this many seconds. (Default: 30.0)",
    )
    parser.add_argument(
        "--period",
        type=float,
        default=2.0,
        help="Period of the water_meter module in seconds. (Default: 2.0)",
    )
    parser.add_argument(
        "--store-period",
        type=float,
        default=2.0,
        help="Re-store the cam-image with a fresh timestamp every this many seconds (simulates the camera, keeps the plausibility-check of the module happy). (Default: 2.0)",
    )
    parser.add_argument(
        "--redis-key-camera",
        type=str,
        default="cam",
        help="First part of Redis camera key. (Default: 'cam')",
    )
    parser.add_argument(
        "--camera-number",
        type=int,
        default=0,
        help="Camera number of the input image. (Default: 0)",
    )
    parser.add_argument(
        "--store-camera-number",
        type=int,
        default=2,
        help="Camera number under which the module stores the extracted digits-image. (Default: 2)",
    )
    parser.add_argument(
        "--redis-key-water-meter",
        type=str,
        default="water-meter",
        help="Redis key of the water-meter-value. (Default: 'water-meter')",
    )
    parser.add_argument(
        "--module-log-level",
        type=str,
        default="DEBUG",
        help="Log-level of the water_meter module subprocess. (Default: 'DEBUG')",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        help="Logging level of this test script (DEBUG, INFO, WARNING, ERROR, CRITICAL)",
    )
    return parser.parse_args()


ARGS = parse_args()

REDIS_SERVER: str = ARGS.redis_server
REDIS_KEY_CAMERA: str = f"{ARGS.redis_key_camera}:{ARGS.camera_number}"
REDIS_KEY_STORE_CAMERA: str = f"{ARGS.redis_key_camera}:{ARGS.store_camera_number}"
REDIS_KEY_WATER_METER: str = ARGS.redis_key_water_meter
HASHKEY_TIMESTAMP: bytes = b"timestamp"
HASHKEY_VALUE: bytes = b"value"

logging.basicConfig(
    stream=sys.stdout,
    level=ARGS.log_level.upper(),
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def utc_timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((REDIS_SERVER, 0))
        return int(s.getsockname()[1])


def start_redis_server(port: int) -> subprocess.Popen:
    logger.info(f"Starting redis-server on {REDIS_SERVER}:{port}")
    return subprocess.Popen(
        [
            "redis-server",
            "--bind",
            REDIS_SERVER,
            "--port",
            str(port),
            "--save",
            "",
            "--appendonly",
            "no",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def wait_for_redis(client: redis.Redis, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if client.ping():
                return True
        except (redis.RedisError, OSError):
            pass
        time.sleep(0.2)
    return False


def start_water_meter_module(port: int) -> subprocess.Popen:
    logger.info(
        f"Starting water_meter module (cwd='{WATER_METER_DIR}', redis={REDIS_SERVER}:{port})"
    )
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "water_meter",
            "--redis-server",
            REDIS_SERVER,
            "--redis-port",
            str(port),
            "--period",
            str(ARGS.period),
            "--redis-key-camera",
            ARGS.redis_key_camera,
            "--camera-number",
            str(ARGS.camera_number),
            "--store-camera-number",
            str(ARGS.store_camera_number),
            "--redis-key-water-meter",
            ARGS.redis_key_water_meter,
            "--reference-image-jsonfile",
            "./water_meter/reference_image.json",
            "--log-level",
            ARGS.module_log_level.upper(),
        ],
        cwd=WATER_METER_DIR,
    )


def display_cam_image_loop(
    client: redis.Redis,
    redis_key: str,
    window_name: str,
    stop_event: threading.Event,
) -> None:
    """Parallel task: read the image stored under redis_key from Redis and
    display it in an OpenCV window until stop_event is set or 'q' is pressed."""
    gui_available = True
    while not stop_event.is_set():
        try:
            data = client.hgetall(redis_key)
            if data:
                frame_bytes = data.get(HASHKEY_VALUE)
                timestamp_bytes = data.get(HASHKEY_TIMESTAMP)
                if isinstance(frame_bytes, (bytes, bytearray, memoryview)):
                    frame = cv2.imdecode(
                        np.frombuffer(bytes(frame_bytes), dtype=np.uint8),
                        cv2.IMREAD_COLOR,
                    )
                    if frame is not None and gui_available:
                        cv2.imshow(window_name, frame)
                        logger.debug(f"Displayed frame from {timestamp_bytes}")
            if gui_available and cv2.waitKey(30) & 0xFF == ord("q"):
                logger.info("Stop requested with key 'q'")
                stop_event.set()
        except cv2.error as e:
            # No display available (headless): keep polling Redis, just skip imshow.
            gui_available = False
            logger.warning(
                f"OpenCV GUI not available ({e}). Continuing without image display."
            )
            time.sleep(0.1)
    if gui_available:
        cv2.destroyAllWindows()


def read_water_meter_value(client: redis.Redis) -> tuple[str, float] | None:
    data = client.hgetall(REDIS_KEY_WATER_METER)
    if not data:
        return None
    timestamp_bytes = data.get(HASHKEY_TIMESTAMP)
    value_bytes = data.get(HASHKEY_VALUE)
    if timestamp_bytes is None or value_bytes is None:
        return None
    return (timestamp_bytes.decode("utf-8", errors="replace"), float(value_bytes))


def main() -> int:
    try:
        import cv2 as _cv2, numpy as _np, redis as _redis  # noqa: F401
    except ImportError:
        print(
            "Missing dependencies. Run this test with the project virtualenv, e.g.:\n"
            "    .venv/bin/python tests/test_water-meter_module.py",
            file=sys.stderr,
        )
        return 2

    image_path = Path(ARGS.image)
    if not image_path.is_absolute():
        image_path = PROJECT_ROOT / image_path
    if not image_path.is_file():
        logger.error(f"Image file not found: '{image_path}'")
        return 2

    port = ARGS.redis_port if ARGS.redis_port != 0 else find_free_port()
    logger.info(f"Redis-Server: {REDIS_SERVER}:{port}")
    logger.info(
        f"Input camera key: '{REDIS_KEY_CAMERA}', digits-image key: '{REDIS_KEY_STORE_CAMERA}', water-meter key: '{REDIS_KEY_WATER_METER}'"
    )
    logger.info(f"Image: '{image_path}'")

    redis_proc = start_redis_server(port)
    water_meter_proc: subprocess.Popen | None = None
    display_thread: threading.Thread | None = None
    stop_event = threading.Event()

    try:
        client = redis.Redis(host=REDIS_SERVER, port=port)
        if not wait_for_redis(client):
            logger.error(f"redis-server did not become ready on {REDIS_SERVER}:{port}")
            return 1
        logger.info("redis-server is ready")

        # Start with a clean database
        client.delete(REDIS_KEY_CAMERA, REDIS_KEY_STORE_CAMERA, REDIS_KEY_WATER_METER)

        # Step 2: store the water-meter photo as camera-image in Redis
        image_bytes = image_path.read_bytes()
        client.hset(
            REDIS_KEY_CAMERA,
            mapping={
                HASHKEY_TIMESTAMP.decode(): utc_timestamp(),
                HASHKEY_VALUE.decode(): image_bytes,
            },
        )
        logger.info(
            f"Stored image '{image_path.name}' ({len(image_bytes)} bytes) in Redis key '{REDIS_KEY_CAMERA}'"
        )

        # Step 3: start the water_meter module
        water_meter_proc = start_water_meter_module(port)

        # Step 4: parallel task displaying the digits-image stored by the module
        window_name = f"Redis - {REDIS_KEY_STORE_CAMERA}"
        display_thread = threading.Thread(
            target=display_cam_image_loop,
            args=(client, REDIS_KEY_STORE_CAMERA, window_name, stop_event),
            daemon=True,
        )
        display_thread.start()

        # Steps 5+6: refresh the cam-image (like a camera would), read and print
        # the water-meter-value until the duration has elapsed
        deadline = time.monotonic() + ARGS.duration
        last_store = 0.0
        last_printed: tuple[str, float] | None = None
        value_received = False
        while time.monotonic() < deadline and not stop_event.is_set():
            now = time.monotonic()
            if now - last_store >= ARGS.store_period:
                client.hset(
                    REDIS_KEY_CAMERA,
                    mapping={
                        HASHKEY_TIMESTAMP.decode(): utc_timestamp(),
                        HASHKEY_VALUE.decode(): image_bytes,
                    },
                )
                last_store = now

            result = read_water_meter_value(client)
            if result is not None and result != last_printed:
                timestamp, value = result
                print(
                    f"Water-meter-value from Redis key '{REDIS_KEY_WATER_METER}': {value} (timestamp: {timestamp})",
                    flush=True,
                )
                last_printed = result
                value_received = True

            time.sleep(0.5)

        logger.info(f"Test duration of {ARGS.duration:.0f}s elapsed. Stopping.")
        if last_printed is not None:
            logger.info(
                f"Last water-meter-value: {last_printed[1]} (timestamp: {last_printed[0]})"
            )
        else:
            logger.warning(
                "No water-meter-value was received from the water_meter module during the test."
            )
        return 0 if value_received else 1

    except KeyboardInterrupt:
        logger.info("Stop requested by keyboard interrupt")
        return 0
    finally:
        stop_event.set()
        if display_thread is not None:
            display_thread.join(timeout=3.0)
        if water_meter_proc is not None:
            water_meter_proc.terminate()
            try:
                water_meter_proc.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                water_meter_proc.kill()
            logger.info("water_meter module stopped")
        redis_proc.terminate()
        try:
            redis_proc.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            redis_proc.kill()
        logger.info("redis-server stopped")


if __name__ == "__main__":
    raise SystemExit(main())
