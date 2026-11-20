"""Integration test for the water_meter module (src/water-meter/water_meter).

Steps:
1. Start a local redis-server via the shell-script '010_start_test-redis-server.sh'
   (on a free port, listening on all interfaces, no password).
2. Load the test-image 'src/water-meter/water_meter/test_image.jpg', add a little
   random noise and store it as camera-image in Redis (key 'cam:0'). A fresh noisy
   image is stored roughly every --period seconds, so the module sees new frames.
3. Start the water_meter module (python -m water_meter) as a subprocess with
   --period (default 1s, to speed up the test) and the other redis/camera arguments.
   The water_meter-module reads the noisy test-image from Redis (key 'cam:0'),
   extracts the digits-ROI and the meter-value, and stores them in Redis
   (keys 'cam:2' and 'water-meter').
4. For about --duration seconds (default 10s), read the extracted digits-images from
   Redis (key 'cam:2'), display them in an OpenCV window (if a GUI is available) and
   check their timestamps. Also read the extracted meter-values (key 'water-meter').
5. Print all extracted meter-values and compare them with --expected-value
   (default 843346, the value shown in the test-image).
6. Stop the module and the redis-server after the duration.

Run with the project virtualenv from the repository root, e.g.:
    .venv/bin/python src/tests/040_test_water_meter_module.py
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
from datetime import UTC, datetime
from pathlib import Path

import cv2
import numpy as np

import redis

TESTS_DIR = Path(__file__).resolve().parents[0]
SRC_DIR = TESTS_DIR.parent
WATER_METER_DIR = SRC_DIR / "water-meter"
TEST_IMAGE = WATER_METER_DIR / "water_meter" / "test_image.jpg"
START_REDIS_SCRIPT = TESTS_DIR / "010_start_test-redis-server.sh"

JPEG_MAGIC = b"\xff\xd8\xff"
HASHKEY_TIMESTAMP = b"timestamp"
HASHKEY_VALUE = b"value"

# Change directry to the directory, where water_meter is located The water_meter-
# module is always started from here, and paths in the json-file are relative to
# this directory.
# os.chdir(WATER_METER_DIR)


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Integration test for the water_meter module: local redis-server (via 010_start_test-redis-server.sh), noisy test-image as cam:0, module subprocess, cam:2 digits-images + water-meter values from Redis."
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=60.0,
        help="Total test duration in seconds. (Default: 60.0)",
    )
    parser.add_argument(
        "--period",
        type=float,
        default=10.0,
        help="--period argument passed to the water_meter module (and interval for storing fresh noisy cam:0 images). (Default: 10.0)",
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
        help="Input camera number: the test-image is stored as 'cam:<camera-number>'. (Default: 0)",
    )
    parser.add_argument(
        "--store-camera-number",
        type=int,
        default=2,
        help="Camera number under which the module stores the extracted digits-image ('cam:<store-camera-number>'). (Default: 2)",
    )
    parser.add_argument(
        "--expected-value",
        type=float,
        default=843346.0,
        help="Expected meter value shown in the test-image. (Default: 843346)",
    )
    parser.add_argument(
        "--noise",
        type=int,
        default=5,
        help="Amplitude of the random noise added to the test-image before storing it. (Default: 5)",
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

KEY_CAM_IN = f"cam:{ARGS.camera_number}"
KEY_CAM_ROI = f"cam:{ARGS.store_camera_number}"
KEY_METER = "water-meter"


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
    """Send sig to the whole process group of proc, then wait; SIGKILL as fallback."""
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


def store_noisy_cam_image(
    client: redis.Redis, img: np.ndarray, rng: np.random.Generator, noise: int
) -> str:
    """Add a little random noise to img, encode as JPEG and store it as cam:<camera-number> in Redis. Returns the timestamp used."""
    noisy = np.clip(
        img.astype(np.int16)
        + rng.integers(-noise, noise + 1, img.shape, dtype=np.int16),
        0,
        255,
    ).astype(np.uint8)
    ok, encoded = cv2.imencode(".jpg", noisy, [cv2.IMWRITE_JPEG_QUALITY, 95])
    assert ok
    ts = datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    client.hset(
        KEY_CAM_IN, mapping={HASHKEY_TIMESTAMP: ts, HASHKEY_VALUE: encoded.tobytes()}
    )
    return ts


def main() -> int:
    if not TEST_IMAGE.is_file():
        logger.error(f"Test-image not found: {TEST_IMAGE}")
        return 2

    port = ARGS.port if ARGS.port != 0 else find_free_port()
    logger.info(
        f"Test redis-server will listen on 127.0.0.1:{port}, keys: '{KEY_CAM_IN}' (input), '{KEY_CAM_ROI}' (digits-image), '{KEY_METER}' (meter-value)"
    )
    logger.info(
        f"water_meter args: --redis-server=127.0.0.1 --redis-port={port} --camera-number={ARGS.camera_number} --store-camera-number={ARGS.store_camera_number} --period={ARGS.period}"
    )
    logger.info(
        f"Test duration: {ARGS.duration}s, expected meter value: {ARGS.expected_value}"
    )

    img = cv2.imread(str(TEST_IMAGE), cv2.IMREAD_COLOR)
    if img is None:
        logger.error(f"Cannot read test-image: {TEST_IMAGE}")
        return 2
    rng = np.random.default_rng()

    with tempfile.TemporaryDirectory(prefix="water-meter-test-") as tmpdir:
        redis_log_path = Path(tmpdir) / "redis-server.log"
        module_log_path = Path(tmpdir) / "water-meter-module.log"

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
        module_proc: subprocess.Popen | None = None
        gui_available = True
        try:
            if not wait_for_redis(client):
                logger.error(
                    f"redis-server did not answer PING on port {port} within 10s. Log tail:\n{redis_log_path.read_text()[-2000:]}"
                )
                return 2
            logger.info("redis-server is up (PONG)")

            # 2. Store first noisy test-image as cam:<camera-number>
            store_noisy_cam_image(client, img, rng, ARGS.noise)
            logger.info(
                f"Stored noisy test-image ({TEST_IMAGE.name}, noise +/-{ARGS.noise}) as '{KEY_CAM_IN}'"
            )

            # 3. Start the water_meter module
            with module_log_path.open("w") as module_log:
                module_proc = subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "water_meter",
                        "--redis-server",
                        "127.0.0.1",
                        "--redis-port",
                        str(port),
                        "--redis-key-camera",
                        "cam",
                        "--camera-number",
                        str(ARGS.camera_number),
                        "--store-camera-number",
                        str(ARGS.store_camera_number),
                        "--redis-key-water-meter",
                        KEY_METER,
                        "--period",
                        str(ARGS.period),
                        "--reference-image-jsonfile",
                        "./water_meter/reference_images.json",
                        "--log-level",
                        ARGS.log_level.upper(),
                    ],
                    cwd=WATER_METER_DIR,
                    stdout=module_log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
            logger.info(
                f"Started water_meter module 'python -m water_meter' (pid {module_proc.pid}, cwd={WATER_METER_DIR}), log: {module_log_path}"
            )

            # 4. Poll redis for ~duration seconds: store fresh noisy cam:0 images every --period seconds,
            #    collect cam:2 digits-images (display them) and water-meter values
            window_name = (
                f"water_meter test - {KEY_CAM_ROI} digits-ROI (press 'q' to stop early)"
            )
            roi_observations: list[tuple[datetime, bytes]] = []
            meter_values: list[tuple[datetime, float]] = []
            all_roi_jpeg_ok = True
            last_roi_ts: str | None = None
            last_meter_ts: str | None = None
            next_cam_store = time.monotonic()

            deadline = time.monotonic() + ARGS.duration
            while time.monotonic() < deadline:
                now = time.monotonic()
                if now >= next_cam_store:
                    ts = store_noisy_cam_image(client, img, rng, ARGS.noise)
                    next_cam_store = now + 1
                    logger.debug(f"Stored fresh noisy '{KEY_CAM_IN}' at {ts}")

                # cam:2 digits-image produced by the module
                roi_data = client.hgetall(KEY_CAM_ROI)
                if roi_data:
                    ts_b = roi_data.get(HASHKEY_TIMESTAMP)
                    val_b = roi_data.get(HASHKEY_VALUE)
                    if isinstance(ts_b, bytes) and isinstance(val_b, bytes):
                        ts = ts_b.decode("utf-8", errors="replace")
                        if ts != last_roi_ts:
                            last_roi_ts = ts
                            roi_observations.append((datetime.fromisoformat(ts), val_b))
                            is_jpeg = (
                                val_b.startswith(JPEG_MAGIC)
                                and cv2.imdecode(
                                    np.frombuffer(val_b, dtype=np.uint8),
                                    cv2.IMREAD_COLOR,
                                )
                                is not None
                            )
                            all_roi_jpeg_ok = all_roi_jpeg_ok and is_jpeg
                            logger.info(
                                f"New digits-image in '{KEY_CAM_ROI}' at {ts}: {len(val_b)} bytes, JPEG-valid: {is_jpeg}"
                            )
                            if gui_available:
                                frame = cv2.imdecode(
                                    np.frombuffer(val_b, dtype=np.uint8),
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

                # water-meter value produced by the module
                meter_data = client.hgetall(KEY_METER)
                if meter_data:
                    ts_b = meter_data.get(HASHKEY_TIMESTAMP)
                    val_b = meter_data.get(HASHKEY_VALUE)
                    if isinstance(ts_b, bytes) and isinstance(val_b, bytes):
                        ts = ts_b.decode("utf-8", errors="replace")
                        if ts != last_meter_ts:
                            last_meter_ts = ts
                            value = float(val_b.decode("utf-8", errors="replace"))
                            meter_values.append((datetime.fromisoformat(ts), value))
                            logger.info(
                                f"Extracted meter-value in '{KEY_METER}' at {ts}: {value} (expected: {ARGS.expected_value}, diff: {value - ARGS.expected_value:+.6f})"
                            )

                time.sleep(0.2)

            if gui_available:
                try:
                    cv2.destroyAllWindows()
                except cv2.error:
                    pass

            # 5. Analyse: cam:2 timestamps at ~period, cam:2 images valid JPEG, meter-values vs expected
            logger.info(
                f"Collected {len(roi_observations)} digits-images ('{KEY_CAM_ROI}') and {len(meter_values)} meter-values ('{KEY_METER}') in {ARGS.duration}s"
            )
            failures: list[str] = []

            # ROI extraction (SIFT feature matching) takes several seconds per frame,
            # so within --duration seconds only a few digits-images can appear.
            min_expected_roi = 1
            if len(roi_observations) < min_expected_roi:
                failures.append(
                    f"no digits-image produced: got {len(roi_observations)} in '{KEY_CAM_ROI}' within {ARGS.duration}s"
                )
            if not all_roi_jpeg_ok:
                failures.append(f"not all '{KEY_CAM_ROI}' images are valid JPEG images")

            roi_gaps = [
                (roi_observations[i + 1][0] - roi_observations[i][0]).total_seconds()
                for i in range(len(roi_observations) - 1)
            ]
            if roi_gaps:
                logger.info(
                    f"Gaps between digits-image timestamps (s): {[round(g, 2) for g in roi_gaps]}"
                )
                bad_gaps = [
                    g
                    for g in roi_gaps
                    if not (0.5 * ARGS.period <= g <= 2.5 * ARGS.period)
                ]
                if bad_gaps:
                    failures.append(
                        f"digits-image period not respected: gaps {bad_gaps} outside "
                        f"[{0.5 * ARGS.period}, {2.5 * ARGS.period}]s (period={ARGS.period}s)"
                    )

            if not meter_values:
                failures.append("no meter-value was extracted/stored in 'water-meter'")
            else:
                for ts, v in meter_values:
                    logger.info(f"extracted meter-value at {ts.isoformat()}: {v}")
                bad_values = [
                    v for _, v in meter_values if abs(v - ARGS.expected_value) > 0.5
                ]
                if bad_values:
                    failures.append(
                        f"extracted meter-values {bad_values} do not match the expected value {ARGS.expected_value} "
                        f"(value shown in test-image '{TEST_IMAGE.name}')"
                    )

            if failures:
                for f in failures:
                    logger.error(f"FAIL: {f}")
                logger.error(
                    f"water_meter module log tail:\n{module_log_path.read_text()[-3000:]}"
                )
                return 1
            logger.info(
                f"PASS: water_meter module produced digits-images ('{KEY_CAM_ROI}') roughly every {ARGS.period}s "
                f"and extracted meter-value(s) {sorted({v for _, v in meter_values})} matching expected {ARGS.expected_value}"
            )
            return 0
        finally:
            if module_proc is not None:
                logger.info("Stopping water_meter module...")
                stop_process_group(module_proc, signal.SIGINT)
            logger.info("Stopping redis-server (via 010 script)...")
            stop_process_group(redis_proc, signal.SIGTERM)


if __name__ == "__main__":
    raise SystemExit(main())
