"""Integration test for the camera module (src/camera/camera).

Steps:
1. Start a local redis-server via the shell-script '010_start_test-redis-server.sh'
   (on a free port, listening on all interfaces, no password).
2. Start the camera module (python -m camera) as a subprocess with command-line
   arguments pointing to that redis-server. On a machine without a real camera
   the module falls back to UsbCamera/DummyCamera, which produce images anyway.
3. For about --duration seconds (default 10s), poll the redis hash 'cam:0' and:
   - check that a new image is stored roughly every --camera-period seconds
     (by comparing the stored 'timestamp' hash-field),
   - verify that every stored image is a valid JPEG (magic bytes + cv2.imdecode),
   - display the latest image in an OpenCV window (if a GUI is available).
4. Stop the camera module and the redis-server and print PASS/FAIL.

Run with the project virtualenv from the repository root, e.g.:
    .venv/bin/python src/tests/020_test_camera_module.py
"""

import argparse
import logging
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

import redis

TESTS_DIR = Path(__file__).resolve().parents[0]
SRC_DIR = TESTS_DIR.parent
CAMERA_DIR = SRC_DIR / "camera"
START_REDIS_SCRIPT = TESTS_DIR / "010_start_test-redis-server.sh"

JPEG_MAGIC = b"\xff\xd8\xff"


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Integration test for the camera module: local redis-server (via 010_start_test-redis-server.sh), camera-module subprocess, periodic JPEG-image checks in Redis, live display."
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=10.0,
        help="Total test duration in seconds. (Default: 10.0)",
    )
    parser.add_argument(
        "--camera-period",
        type=float,
        default=0.2,
        help="--camera-period argument passed to the camera module: interval in seconds between storing images in Redis. (Default: 0.2)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=0,
        help="Port for the test redis-server. 0 = pick a free port automatically. (Default: 0)",
    )
    parser.add_argument(
        "--camera-number",
        type=int,
        default=0,
        help="Camera number used in the redis key 'cam:<camera-number>'. (Default: 0)",
    )
    parser.add_argument(
        "--redis-key-camera",
        type=str,
        default="cam",
        help="First part of the redis camera key. (Default: 'cam')",
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
    level=ARGS.log_level.upper(),
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

REDIS_KEY = f"{ARGS.redis_key_camera}:{ARGS.camera_number}"
HASHKEY_TIMESTAMP = b"timestamp"
HASHKEY_VALUE = b"value"


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


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


def stop_process_group(
    proc: subprocess.Popen, sig: int = signal.SIGTERM, timeout: float = 5.0
) -> None:
    """Send sig to the whole process group of proc (script + its children), then wait; SIGKILL as fallback."""
    if proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, sig)
    except (ProcessLookupError, PermissionError):
        proc.terminate()
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            proc.kill()
        proc.wait(timeout=timeout)


def parse_timestamp(ts: str) -> datetime:
    # camera stores ISO 8601 with 'Z' suffix, e.g. '2026-11-16T18:53:29.796Z'
    return datetime.fromisoformat(ts)


def main() -> int:
    port = ARGS.port if ARGS.port != 0 else find_free_port()
    logger.info(
        f"Test redis-server will listen on 127.0.0.1:{port} (all interfaces), redis key '{REDIS_KEY}'"
    )
    logger.info(
        f"Camera module args: --redis-server=127.0.0.1 --redis-port={port} --camera-number={ARGS.camera_number} --camera-period={ARGS.camera_period}"
    )
    logger.info(
        f"Test duration: {ARGS.duration}s (expecting an image every {ARGS.camera_period}s)"
    )

    with tempfile.TemporaryDirectory(prefix="camera-test-") as tmpdir:
        redis_log_path = Path(tmpdir) / "redis-server.log"
        camera_log_path = Path(tmpdir) / "camera-module.log"

        # 1. Start local redis-server via the shell script
        with redis_log_path.open("w") as redis_log:
            redis_proc = subprocess.Popen(
                ["bash", str(START_REDIS_SCRIPT), str(port)],
                stdout=redis_log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        logger.info(
            f"Started '{START_REDIS_SCRIPT.name} {port}' (pid {redis_proc.pid}), log: {redis_log_path}"
        )

        client = redis.Redis(host="127.0.0.1", port=port)
        camera_proc: subprocess.Popen | None = None
        gui_available = True
        try:
            if not wait_for_redis(client):
                logger.error(
                    f"redis-server did not answer PING on port {port} within 10s. Log tail:\n{redis_log_path.read_text()[-2000:]}"
                )
                return 2
            logger.info("redis-server is up (PONG)")

            # 2. Start the camera module
            with camera_log_path.open("w") as camera_log:
                camera_proc = subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "camera",
                        "--redis-server",
                        "127.0.0.1",
                        "--redis-port",
                        str(port),
                        "--camera-number",
                        str(ARGS.camera_number),
                        "--redis-key-camera",
                        ARGS.redis_key_camera,
                        "--camera-period",
                        str(ARGS.camera_period),
                        "--camera-width",
                        "640",
                        "--camera-height",
                        "480",
                        "--log-level",
                        ARGS.log_level.upper(),
                    ],
                    cwd=CAMERA_DIR,
                    stdout=camera_log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
            logger.info(
                f"Started camera module 'python -m camera' (pid {camera_proc.pid}, cwd={CAMERA_DIR}), log: {camera_log_path}"
            )

            # 3. Poll redis for ~duration seconds, check timestamps + JPEG, display image
            window_name = f"camera-module test - {REDIS_KEY} (press 'q' to stop early)"
            observations: list[tuple[datetime, bytes]] = []
            all_jpeg_ok = True
            last_ts: str | None = None

            deadline = time.monotonic() + ARGS.duration
            while time.monotonic() < deadline:
                data = client.hgetall(REDIS_KEY)
                if data:
                    ts_bytes = data.get(HASHKEY_TIMESTAMP)
                    value_bytes = data.get(HASHKEY_VALUE)
                    if isinstance(ts_bytes, bytes) and isinstance(value_bytes, bytes):
                        ts = ts_bytes.decode("utf-8", errors="replace")
                        if ts != last_ts:
                            last_ts = ts
                            observations.append((parse_timestamp(ts), value_bytes))
                            is_jpeg = (
                                value_bytes.startswith(JPEG_MAGIC)
                                and cv2.imdecode(
                                    np.frombuffer(value_bytes, dtype=np.uint8),
                                    cv2.IMREAD_COLOR,
                                )
                                is not None
                            )
                            all_jpeg_ok = all_jpeg_ok and is_jpeg
                            logger.info(
                                f"New image in '{REDIS_KEY}' at {ts}: {len(value_bytes)} bytes, "
                                f"JPEG-valid: {is_jpeg}"
                            )
                        # display latest image
                        if gui_available:
                            frame = cv2.imdecode(
                                np.frombuffer(value_bytes, dtype=np.uint8),
                                cv2.IMREAD_COLOR,
                            )
                            if frame is not None:
                                try:
                                    cv2.imshow(window_name, frame)
                                    if cv2.waitKey(1) & 0xFF == ord("q"):
                                        logger.info("Stop requested with key 'q'")
                                        break
                                except cv2.error:
                                    gui_available = False
                                    logger.warning(
                                        "No GUI available (headless) - cv2.imshow disabled, continuing without display."
                                    )
                time.sleep(0.03)

            # 4. Analyse the observations
            logger.info(
                f"Collected {len(observations)} distinct images in {ARGS.duration}s"
            )
            failures: list[str] = []

            min_expected = max(3, int(ARGS.duration / ARGS.camera_period) // 2)
            if len(observations) < min_expected:
                failures.append(
                    f"too few images: got {len(observations)}, expected at least {min_expected} "
                    f"(one every {ARGS.camera_period}s within {ARGS.duration}s)"
                )

            if not all_jpeg_ok or any(
                not v.startswith(JPEG_MAGIC) for _, v in observations
            ):
                failures.append("not all stored images are valid JPEG images")

            gaps = [
                (observations[i + 1][0] - observations[i][0]).total_seconds()
                for i in range(len(observations) - 1)
            ]
            if gaps:
                logger.info(
                    f"Gaps between stored images (s): {[round(g, 2) for g in gaps]}"
                )
                bad_gaps = [
                    g
                    for g in gaps
                    if not (0.5 * ARGS.camera_period <= g <= 2.5 * ARGS.camera_period)
                ]
                if bad_gaps:
                    failures.append(
                        f"image period not respected: gaps {bad_gaps} outside "
                        f"[{0.5 * ARGS.camera_period}, {2.5 * ARGS.camera_period}]s (period={ARGS.camera_period}s)"
                    )
            elif len(observations) < 2:
                failures.append("fewer than 2 images - cannot check the storing period")

            if failures:
                for f in failures:
                    logger.error(f"FAIL: {f}")
                logger.error(
                    f"Camera-module log tail:\n{camera_log_path.read_text()[-2000:]}"
                )
                return 1
            logger.info(
                "PASS: camera module stores a valid JPEG image in Redis roughly every "
                f"{ARGS.camera_period}s (key '{REDIS_KEY}', hashfields 'timestamp'/'value')"
            )
            return 0
        finally:
            if camera_proc is not None:
                logger.info("Stopping camera module...")
                stop_process_group(camera_proc, signal.SIGINT)
            logger.info("Stopping redis-server (via 010 script)...")
            stop_process_group(redis_proc, signal.SIGTERM)
            if gui_available:
                try:
                    cv2.destroyAllWindows()
                except cv2.error:
                    pass


if __name__ == "__main__":
    raise SystemExit(main())
