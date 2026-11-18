"""Integration test for the data-collector module (src/data-collector/data_collector).

Steps:
1. Create a result folder in /tmp (deleted again after the test) with the
   sub-folders the data-collector needs:
       camera-images/camera-0
       camera-images/camera-1
       water-level
       water-meter
2. Start a local redis-server via the shell-script '010_start_test-redis-server.sh'
   (on a free port, listening on all interfaces, no password).
3. Start a producer-thread that stores dummy redis-data every --producer-period seconds:
   - 'cam:0' and 'cam:1': a small JPEG image (hash fields 'timestamp'/'value')
   - 'water-level': a dummy float value 1.23 (like dummy_linux_water-level.py)
   - 'water-meter': an increasing float value
4. Start the data-collector module (python -m data_collector) as a subprocess with
   command-line arguments pointing to the test redis-server.
   All cron-expressions are set to '* * * * *' (every minute) to speed up the test.
5. Let it run for about --duration seconds (default 300s = 5 minutes), then stop the
   data-collector, the producer and the redis-server.
6. Verify the produced files in the /tmp result folder:
   - water-level/sensor-data.csv and water-meter/sensor-data.csv exist, have the
     header 'timestamp,value' and at least MIN_COLLECTS data rows with a parseable
     ISO-timestamp and a parseable float value,
   - camera-images/camera-0 and camera-images/camera-1 contain at least MIN_COLLECTS
     valid JPEG files each.
   Print PASS/FAIL together with the list of the produced files, then delete the
   result folder in /tmp (the produced test-files are removed after the test).

Run with the project virtualenv from the repository root, e.g.:
    .venv/bin/python src/tests/050_test_data-collector.py
"""

import argparse
import csv
import logging
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import cv2
import numpy as np

import redis

TESTS_DIR = Path(__file__).resolve().parents[0]
SRC_DIR = TESTS_DIR.parent
DATA_COLLECTOR_DIR = SRC_DIR / "data-collector"
START_REDIS_SCRIPT = TESTS_DIR / "010_start_test-redis-server.sh"
# Result-data folder in /tmp (deleted again after the test). The pid in the name
# avoids collisions between concurrent test runs.
RESULT_DATA_DIR = (
    Path(tempfile.gettempdir()) / f"data-collector-test-result-data-{os.getpid()}"
)

CAMERA_IMAGES_DIR = RESULT_DATA_DIR / "camera-images"
CAMERA_SUBFOLDERS = [CAMERA_IMAGES_DIR / "camera-0", CAMERA_IMAGES_DIR / "camera-1"]
WATER_LEVEL_DIR = RESULT_DATA_DIR / "water-level"
WATER_METER_DIR = RESULT_DATA_DIR / "water-meter"
WATER_LEVEL_CSV = WATER_LEVEL_DIR / "sensor-data.csv"
WATER_METER_CSV = WATER_METER_DIR / "sensor-data.csv"

KEY_CAM_0 = "cam:0"
KEY_CAM_1 = "cam:1"
KEY_WATER_LEVEL = "water-level"
KEY_WATER_METER = "water-meter"

HASHKEY_TIMESTAMP = "timestamp"
HASHKEY_VALUE = "value"
CRON_EVERY_MINUTE = "* * * * *"
JPEG_MAGIC = b"\xff\xd8\xff"
# With cron '* * * * *' (plus the 5s buffer of the data-collector) and a 300s run
# we expect about 4-5 collects per source; require at least 2 to be robust.
MIN_COLLECTS = 2


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Integration test for the data-collector module: local redis-server (via 010_start_test-redis-server.sh), dummy redis-data producers (cam:0, cam:1, water-level, water-meter), data_collector subprocess with all cron-expressions '* * * * *', verification of the produced files in a result folder in /tmp (deleted after the test)."
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=300.0,
        help="Total test duration in seconds. (Default: 300.0 = 5 minutes)",
    )
    parser.add_argument(
        "--producer-period",
        type=float,
        default=1.0,
        help="Interval in seconds between storing fresh dummy values in Redis. (Default: 1.0)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=0,
        help="Port for the test redis-server. 0 = pick a free port automatically. (Default: 0)",
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


def iso_now_z() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def ensure_result_dirs() -> None:
    """Create the sub-folders the data-collector needs inside the result folder in /tmp:
    camera-images/camera-0, camera-images/camera-1, water-level, water-meter."""
    for d in [*CAMERA_SUBFOLDERS, WATER_LEVEL_DIR, WATER_METER_DIR]:
        d.mkdir(parents=True, exist_ok=True)


def remove_result_dir() -> None:
    """Delete the result folder in /tmp with all produced test-files after the test."""
    if RESULT_DATA_DIR.exists():
        shutil.rmtree(RESULT_DATA_DIR)
        logger.info(f"Deleted result-data folder '{RESULT_DATA_DIR}' after the test")


def make_test_jpeg(bgr_color: tuple[int, int, int]) -> bytes:
    """Create a small solid-color JPEG image (as bytes) to store as dummy camera-image in Redis."""
    img = np.zeros((48, 64, 3), dtype=np.uint8)
    img[:] = bgr_color
    ok, encoded = cv2.imencode(".jpg", img)
    assert ok
    return encoded.tobytes()


def producer_loop(
    client: redis.Redis, stop_event: threading.Event, period: float
) -> None:
    """Store dummy redis-data every `period` seconds until stop_event is set:
    JPEG images in 'cam:0'/'cam:1', a dummy water-level value (1.23, like
    dummy_linux_water-level.py) and an increasing water-meter value."""
    cam_images = {
        KEY_CAM_0: make_test_jpeg((200, 30, 30)),
        KEY_CAM_1: make_test_jpeg((30, 200, 30)),
    }
    counter = 0
    while not stop_event.is_set():
        ts = iso_now_z()
        try:
            for key, jpeg_bytes in cam_images.items():
                client.hset(
                    key, mapping={HASHKEY_TIMESTAMP: ts, HASHKEY_VALUE: jpeg_bytes}
                )
            client.hset(
                KEY_WATER_LEVEL, mapping={HASHKEY_TIMESTAMP: ts, HASHKEY_VALUE: 1.23}
            )
            client.hset(
                KEY_WATER_METER,
                mapping={HASHKEY_TIMESTAMP: ts, HASHKEY_VALUE: 843346.0 + counter},
            )
            counter += 1
        except (redis.RedisError, OSError) as e:
            logger.warning(f"Producer could not store values in Redis: {e}")
        stop_event.wait(period)


def verify_csv_file(csv_path: Path, name: str, min_rows: int) -> tuple[list[str], int]:
    """Check that the CSV file exists, has the header 'timestamp,value' and at least
    min_rows data rows with a parseable ISO-timestamp and a parseable float value.
    Returns (failures, number_of_valid_rows)."""
    failures: list[str] = []
    if not csv_path.is_file():
        return [f"{name}: CSV file was not produced: {csv_path}"], 0
    with csv_path.open("r", newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        headers = next(reader, None)
        if headers != [HASHKEY_TIMESTAMP, HASHKEY_VALUE]:
            failures.append(
                f"{name}: CSV file '{csv_path}' has wrong headers: {headers} "
                f"(expected ['{HASHKEY_TIMESTAMP}', '{HASHKEY_VALUE}'])"
            )
        valid_rows = 0
        for row in reader:
            if len(row) != 2:
                failures.append(f"{name}: CSV row with wrong number of columns: {row}")
                continue
            ts, value_str = row
            try:
                datetime.fromisoformat(ts)
            except ValueError:
                failures.append(f"{name}: CSV timestamp not parseable: '{ts}'")
                continue
            try:
                float(value_str)
            except ValueError:
                failures.append(
                    f"{name}: CSV value not parseable as float: '{value_str}'"
                )
                continue
            valid_rows += 1
    if valid_rows < min_rows:
        failures.append(
            f"{name}: too few data rows in '{csv_path}': got {valid_rows}, expected at least "
            f"{min_rows} (cron '{CRON_EVERY_MINUTE}' within {ARGS.duration}s)"
        )
    else:
        logger.info(f"{name}: CSV '{csv_path}' has {valid_rows} valid data rows")
    return failures, valid_rows


def verify_camera_subfolder(folder: Path, min_images: int) -> tuple[list[str], int]:
    """Check that the camera sub-folder contains at least min_images valid JPEG files.
    Returns (failures, number_of_valid_images)."""
    failures: list[str] = []
    if not folder.is_dir():
        return [f"camera sub-folder missing: {folder}"], 0
    jpg_files = sorted(folder.glob("*.jpg"))
    valid = 0
    for jpg in jpg_files:
        data = jpg.read_bytes()
        is_jpeg = (
            data.startswith(JPEG_MAGIC)
            and cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
            is not None
        )
        if not is_jpeg:
            failures.append(
                f"camera image '{jpg.name}' in '{folder.name}' is not a valid JPEG"
            )
        else:
            valid += 1
    if valid < min_images:
        failures.append(
            f"camera sub-folder '{folder.name}': too few valid images: got {valid}, expected "
            f"at least {min_images} (cron '{CRON_EVERY_MINUTE}' within {ARGS.duration}s)"
        )
    else:
        logger.info(f"camera sub-folder '{folder.name}': {valid} valid JPEG images")
    return failures, valid


def main() -> int:
    ensure_result_dirs()
    port = ARGS.port if ARGS.port != 0 else find_free_port()
    logger.info(
        f"Result-data folder in /tmp (deleted after the test): '{RESULT_DATA_DIR}'"
    )
    logger.info(
        f"Test redis-server will listen on 127.0.0.1:{port}, keys: '{KEY_CAM_0}', "
        f"'{KEY_CAM_1}', '{KEY_WATER_LEVEL}', '{KEY_WATER_METER}'"
    )
    logger.info(
        f"data_collector args: --redis-server=127.0.0.1 --redis-port={port} "
        f"--camera-images-folder={CAMERA_IMAGES_DIR} --water-level-csv-file={WATER_LEVEL_CSV} "
        f"--water-meter-csv-file={WATER_METER_CSV} --camera-cron-expression='{CRON_EVERY_MINUTE}' "
        f"--water-level-cron-expression='{CRON_EVERY_MINUTE}' "
        f"--water-meter-cron-expression='{CRON_EVERY_MINUTE}'"
    )
    logger.info(
        f"Test duration: {ARGS.duration}s (producers store fresh values every "
        f"{ARGS.producer_period}s, data-collector cron collects every minute)"
    )

    with tempfile.TemporaryDirectory(prefix="data-collector-test-") as tmpdir:
        redis_log_path = Path(tmpdir) / "redis-server.log"
        collector_log_path = Path(tmpdir) / "data-collector-module.log"

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
        collector_proc: subprocess.Popen | None = None
        producer_stop = threading.Event()
        producer_thread: threading.Thread | None = None
        try:
            if not wait_for_redis(client):
                logger.error(
                    f"redis-server did not answer PING on port {port} within 10s. Log tail:\n{redis_log_path.read_text()[-2000:]}"
                )
                remove_result_dir()
                return 2
            logger.info("redis-server is up (PONG)")

            # 2. Start the producer-thread storing dummy redis-data
            producer_thread = threading.Thread(
                target=producer_loop,
                args=(client, producer_stop, ARGS.producer_period),
                daemon=True,
            )
            producer_thread.start()
            logger.info(
                f"Started producer-thread (keys '{KEY_CAM_0}', '{KEY_CAM_1}', "
                f"'{KEY_WATER_LEVEL}', '{KEY_WATER_METER}', every {ARGS.producer_period}s)"
            )

            # 3. Start the data-collector module
            with collector_log_path.open("w") as collector_log:
                collector_proc = subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "data_collector",
                        "--redis-server",
                        "127.0.0.1",
                        "--redis-port",
                        str(port),
                        "--redis-key-camera",
                        "cam",
                        "--redis-key-water-level",
                        KEY_WATER_LEVEL,
                        "--redis-key-water-meter",
                        KEY_WATER_METER,
                        "--redis-hashkey-timestamp",
                        HASHKEY_TIMESTAMP,
                        "--redis-hashkey-value",
                        HASHKEY_VALUE,
                        "--camera-images-folder",
                        str(CAMERA_IMAGES_DIR),
                        "--camera-images-subfolder-prefix",
                        "camera-",
                        "--water-level-csv-file",
                        str(WATER_LEVEL_CSV),
                        "--water-meter-csv-file",
                        str(WATER_METER_CSV),
                        "--camera-cron-expression",
                        CRON_EVERY_MINUTE,
                        "--water-level-cron-expression",
                        CRON_EVERY_MINUTE,
                        "--water-meter-cron-expression",
                        CRON_EVERY_MINUTE,
                        "--log-level",
                        ARGS.log_level.upper(),
                    ],
                    cwd=DATA_COLLECTOR_DIR,
                    stdout=collector_log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
            logger.info(
                f"Started data_collector module 'python -m data_collector' (pid {collector_proc.pid}, "
                f"cwd={DATA_COLLECTOR_DIR}), log: {collector_log_path}"
            )

            # 4. Let everything run for --duration seconds
            logger.info(f"Running for {ARGS.duration}s ...")
            time.sleep(ARGS.duration)
        finally:
            if collector_proc is not None:
                logger.info("Stopping data_collector module...")
                stop_process_group(collector_proc, signal.SIGINT)
            if producer_thread is not None:
                producer_stop.set()
                producer_thread.join(timeout=5)
                logger.info("Stopped producer-thread")
            logger.info("Stopping redis-server (via 010 script)...")
            stop_process_group(redis_proc, signal.SIGTERM)

        # 5. Verify the produced files in the /tmp result folder (deleted afterwards)
        logger.info(
            f"Verifying produced files in the /tmp result folder '{RESULT_DATA_DIR}':"
        )
        failures: list[str] = []

        wl_failures, wl_rows = verify_csv_file(
            WATER_LEVEL_CSV, "water-level", MIN_COLLECTS
        )
        failures.extend(wl_failures)
        wm_failures, wm_rows = verify_csv_file(
            WATER_METER_CSV, "water-meter", MIN_COLLECTS
        )
        failures.extend(wm_failures)

        cam_counts: dict[str, int] = {}
        for folder in CAMERA_SUBFOLDERS:
            cam_failures, n = verify_camera_subfolder(folder, MIN_COLLECTS)
            failures.extend(cam_failures)
            cam_counts[folder.name] = n

        logger.info(
            f"Produced files (water-level rows: {wl_rows}, water-meter rows: {wm_rows}, "
            f"camera images: {cam_counts}):"
        )

        for p in sorted(RESULT_DATA_DIR.rglob("*")):
            if p.is_file():
                logger.info(
                    f"  {p.relative_to(RESULT_DATA_DIR)} ({p.stat().st_size} bytes)"
                )

        # Remove the produced test-files after the test (result folder is in /tmp).
        remove_result_dir()

        if failures:
            for f in failures:
                logger.error(f"FAIL: {f}")
            logger.error(
                f"data_collector module log tail:\n{collector_log_path.read_text()[-3000:]}"
            )
            return 1
        logger.info(
            f"PASS: data_collector collected the redis-data every minute (cron "
            f"'{CRON_EVERY_MINUTE}') into '{RESULT_DATA_DIR}' (water-level rows: {wl_rows}, "
            f"water-meter rows: {wm_rows}, camera images: {cam_counts})"
        )

        return 0


if __name__ == "__main__":
    raise SystemExit(main())
