"""Overall integration test for the whole water-reservoir-monitoring system.

Steps:
1. Clean the data-folder: delete everything except folders containing a .gitkeep
   file (i.e. keep the tracked folder structure with the .gitkeep files), then make
   sure the sub-folders the data-collector needs exist:
       data/camera-images/camera-0, camera-1, camera-2
       data/water-level, data/water-meter
2. Start a local redis-server via the shell-script '010_start_test-redis-server.sh'
   (on a free port, listening on all interfaces, no password).
3. Start the producing modules:
   - camera module (python -m camera) for cam:0 and cam:1 (falls back to the
     DummyCamera with the test image when no USB camera is present)
   - dummy water-level process (src/water-level/dummy_linux_water-level.py),
     stores dummy values in the redis key 'water-level'
   - water_meter module (python -m water_meter), reads camera images from 'cam:0',
     extracts the meter value into the redis key 'water-meter' and stores the
     digits-ROI image in 'cam:2'
4. Start the data-collector (python -m data_collector) with all cron-expressions
   '* * * * *' (collect every minute), writing CSV files and camera images into
   the data-folder.
5. Start the website (python -m website) on a free flask port, connected to the
   test redis-server and the data-folder.
6. For about --duration seconds (default 600 = 10 minutes), every --check-interval
   seconds check:
   - Redis: the keys cam:0, cam:1, water-level, water-meter contain data with a
     parseable timestamp and a valid value (JPEG for cameras, float for sensors)
   - Website: GET /, /camera/latest-image?camera=0|1, /camera/history,
     /camera/history/download (zip), /camera/live-video, /water-level/live,
     /water-level/live-value, /water-level/history, /water-meter/live,
     /water-meter/live-value, /water-meter/history, /water-meter/daily-consumption
     answer as expected (once: also /camera/live-video-stream)
   - data-folder: row counts of the CSV files and image counts per camera folder
     (logged; verified after the run)
7. Stop all modules and the redis-server, then verify the produced files:
   CSV header 'timestamp,value' + at least --min-collects rows with parseable
   timestamp and float value, and at least --min-collects valid JPEG images in each
   of camera-0 and camera-1.
8. Clean the data-folder again (same as step 1). Print PASS/FAIL.

The test runs automatically without user interaction.

Run with the project virtualenv from the repository root, e.g.:
    .venv/bin/python src/tests/080_overall-test.py
"""

import argparse
import csv
import http.client
import io
import json
import logging
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import cv2
import numpy as np

import redis

TESTS_DIR = Path(__file__).resolve().parents[0]
SRC_DIR = TESTS_DIR.parent
REPO_ROOT = SRC_DIR.parent
DATA_DIR = REPO_ROOT / "data"
CAMERA_IMAGES_DIR = DATA_DIR / "camera-images"
CAMERA_SUBFOLDERS = [CAMERA_IMAGES_DIR / f"camera-{i}" for i in range(3)]
WATER_LEVEL_DIR = DATA_DIR / "water-level"
WATER_METER_DIR = DATA_DIR / "water-meter"
WATER_LEVEL_CSV = WATER_LEVEL_DIR / "sensor-data.csv"
WATER_METER_CSV = WATER_METER_DIR / "sensor-data.csv"

START_REDIS_SCRIPT = TESTS_DIR / "010_start_test-redis-server.sh"
CAMERA_DIR = SRC_DIR / "camera"
DUMMY_WATER_LEVEL_SCRIPT = SRC_DIR / "water-level" / "dummy_linux_water-level.py"
WATER_METER_MODULE_DIR = SRC_DIR / "water-meter"
DATA_COLLECTOR_DIR = SRC_DIR / "data-collector"
WEBSITE_DIR = SRC_DIR / "flask-website"

KEY_CAM_0 = "cam:0"
KEY_CAM_1 = "cam:1"
KEY_CAM_2 = "cam:2"
KEY_WATER_LEVEL = "water-level"
KEY_WATER_METER = "water-meter"
CAM_KEYS = [KEY_CAM_0, KEY_CAM_1, KEY_CAM_2]
SENSOR_KEYS = [KEY_WATER_LEVEL, KEY_WATER_METER]

HASHKEY_TIMESTAMP = "timestamp"
HASHKEY_VALUE = "value"
CRON_EVERY_MINUTE = "* * * * *"
JPEG_MAGIC = b"\xff\xd8\xff"
WEBM_MAGIC = b"\x1a\x45\xdf\xa3"


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Overall integration test: clean data-folder, local redis-server (via 010_start_test-redis-server.sh), producer modules (camera cam:0/cam:1, dummy water-level, water_meter), data_collector with cron '* * * * *', website; periodic automated checks for --duration seconds; data-folder cleaned again at exit. Runs automatically without user interaction."
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=600.0,
        help="Total test duration in seconds. (Default: 600.0 = 10 minutes)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=0,
        help="Port for the test redis-server. 0 = pick a free port automatically. (Default: 0)",
    )
    parser.add_argument(
        "--flask-port",
        type=int,
        default=0,
        help="Port for the test flask-server. 0 = pick a free port automatically. (Default: 0)",
    )
    parser.add_argument(
        "--camera-period",
        type=float,
        default=1.0,
        help="--camera-period argument passed to the camera modules: interval in seconds between storing images in Redis. (Default: 1.0)",
    )
    parser.add_argument(
        "--water-level-period",
        type=float,
        default=5.0,
        help="--wait-time-seconds argument passed to the dummy water-level process. (Default: 5.0)",
    )
    parser.add_argument(
        "--water-meter-period",
        type=float,
        default=2.0,
        help="--period argument passed to the water_meter module. (Default: 2.0)",
    )
    parser.add_argument(
        "--check-interval",
        type=float,
        default=15.0,
        help="Interval in seconds between automated check rounds. (Default: 15.0)",
    )
    parser.add_argument(
        "--min-collects",
        type=int,
        default=2,
        help="Minimum number of collected rows/images per source after the run (cron '* * * * *'). (Default: 2)",
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


def http_get(url: str, timeout: float = 20.0) -> tuple[int, dict[str, str], bytes]:
    """GET the url and return (status, headers, body). HTTP errors (4xx/5xx) are
    returned as their status code, connection errors as status 0."""
    try:
        with urllib.request.urlopen(
            urllib.request.Request(url), timeout=timeout
        ) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {}), e.read()
    except (urllib.error.URLError, OSError, http.client.HTTPException) as e:
        return 0, {}, str(e).encode()


def header_value(headers: dict[str, str], name: str) -> str:
    """Case-insensitive lookup of a response header."""
    for key, value in headers.items():
        if key.lower() == name.lower():
            return value
    return ""


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


def wait_for_http(base_url: str, timeout: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status, _, _ = http_get(base_url + "/", timeout=5.0)
        if status == 200:
            return True
        time.sleep(0.5)
    return False


def wait_for_redis_keys(
    client: redis.Redis, keys: list[str], timeout: float = 90.0
) -> bool:
    """Wait until all given redis keys contain data."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if all(client.hgetall(key) for key in keys):
                return True
        except (redis.RedisError, OSError):
            pass
        time.sleep(0.5)
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


def clean_data_dir() -> int:
    """Clean the data-folder: delete all files except .gitkeep files, and remove all
    directories that do not contain a .gitkeep file (i.e. keep the tracked folder
    structure). Returns the number of removed files."""
    removed = 0
    if not DATA_DIR.is_dir():
        return removed
    for path in DATA_DIR.rglob("*"):
        if path.is_file() and path.name != ".gitkeep":
            path.unlink()
            removed += 1
    for path in sorted(DATA_DIR.rglob("*"), reverse=True):
        if path.is_dir() and not any(path.iterdir()):
            shutil.rmtree(path)
    return removed


def ensure_data_subfolders() -> None:
    """Create the sub-folders the data-collector discovers at startup."""
    for d in [*CAMERA_SUBFOLDERS, WATER_LEVEL_DIR, WATER_METER_DIR]:
        d.mkdir(parents=True, exist_ok=True)


def start_logged_process(
    name: str, argv: list[str], cwd: Path, log_path: Path
) -> subprocess.Popen:
    with log_path.open("w") as log_file:
        proc = subprocess.Popen(
            argv,
            cwd=cwd,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    logger.info(f"Started {name} (pid {proc.pid}, cwd={cwd}), log: {log_path}")
    return proc


def redis_timestamp_and_value(
    client: redis.Redis, key: str
) -> tuple[datetime | None, bytes | None]:
    """Return (timestamp, value-bytes) stored in the redis hash `key`, or (None, None)."""
    try:
        data = client.hgetall(key)
    except (redis.RedisError, OSError) as e:
        logger.warning(f"Redis error reading '{key}': {e}")
        return None, None
    if not data:
        return None, None
    ts_bytes = data.get(HASHKEY_TIMESTAMP.encode())
    value_bytes = data.get(HASHKEY_VALUE.encode())
    assert ts_bytes is None or isinstance(ts_bytes, bytes)
    assert value_bytes is None or isinstance(value_bytes, bytes)
    if ts_bytes is None or value_bytes is None:
        return None, value_bytes
    try:
        ts = datetime.fromisoformat(ts_bytes.decode())
    except ValueError:
        return None, value_bytes
    return ts, value_bytes


def check_redis_producers(
    client: redis.Redis,
    first_ts: dict[str, datetime | None],
    last_ts: dict[str, datetime | None],
) -> list[str]:
    """Check that all producer keys contain data with parseable timestamps and valid
    values (JPEG for camera keys, float for sensor keys). Updates first_ts/last_ts."""
    failures: list[str] = []
    for key in [*CAM_KEYS, *SENSOR_KEYS]:
        ts, value = redis_timestamp_and_value(client, key)
        if ts is None or value is None:
            failures.append(f"redis key '{key}' has no data with timestamp+value")
            continue
        if key in CAM_KEYS and not value.startswith(JPEG_MAGIC):
            failures.append(f"redis key '{key}': value is not a JPEG image")
        if key in SENSOR_KEYS:
            try:
                float(value.decode())
            except (ValueError, UnicodeDecodeError):
                failures.append(
                    f"redis key '{key}': value is not a float: {value[:50]!r}"
                )
        if first_ts.get(key) is None:
            first_ts[key] = ts
        last_ts[key] = ts
    return failures


def check_website(base_url: str, stream_checked: list[bool]) -> list[str]:
    """Check the main website routes via HTTP. Returns a list of failures."""
    failures: list[str] = []

    def check(name: str, condition: bool, detail: str = "") -> None:
        if not condition:
            failures.append(f"{name}: {detail}")

    status, _, body = http_get(f"{base_url}/")
    check(
        "GET / (dashboard)", status == 200 and b"Dashboard" in body, f"status={status}"
    )

    for camera in (0, 1):
        status, headers, body = http_get(
            f"{base_url}/camera/latest-image?camera={camera}"
        )
        check(
            f"GET /camera/latest-image?camera={camera}",
            status == 200
            and header_value(headers, "Content-Type").startswith("image/jpeg")
            and body.startswith(JPEG_MAGIC),
            f"status={status}",
        )

    status, _, body = http_get(f"{base_url}/camera/history?camera=0")
    check(
        "GET /camera/history?camera=0",
        status == 200 and b"History" in body and b"Download images" in body,
        f"status={status}",
    )

    # zip download with default range (last 24h): 404 is fine until the first image was collected
    status, headers, body = http_get(f"{base_url}/camera/history/download?camera=0")
    if status == 200:
        try:
            with zipfile.ZipFile(io.BytesIO(body)) as zf:
                entries = zf.namelist()
            check(
                "GET /camera/history/download?camera=0 (zip)",
                header_value(headers, "Content-Type") == "application/zip"
                and len(entries) >= 1
                and all(name.endswith(".jpg") for name in entries),
                f"zip entries={entries}",
            )
        except zipfile.BadZipFile as e:
            check(
                "GET /camera/history/download?camera=0 (zip)", False, f"not a zip: {e}"
            )
    else:
        logger.info(
            f"  (info) /camera/history/download returned {status} (no image collected yet)"
        )

    status, _, body = http_get(f"{base_url}/camera/live-video?camera=0&duration=1")
    check(
        "GET /camera/live-video?camera=0",
        status == 200 and b"Live video" in body,
        f"status={status}",
    )

    if not stream_checked[0]:
        stream_checked[0] = True
        status, headers, body = http_get(
            f"{base_url}/camera/live-video-stream?camera=0&duration=1", timeout=30.0
        )
        check(
            "GET /camera/live-video-stream?camera=0&duration=1 (webm)",
            status == 200
            and header_value(headers, "Content-Type").startswith("video/webm")
            and body.startswith(WEBM_MAGIC),
            f"status={status}, size={len(body)}",
        )

    start_iso = (datetime.now(UTC) - timedelta(days=1)).isoformat(timespec="seconds")
    end_iso = datetime.now(UTC).isoformat(timespec="seconds")

    status, _, body = http_get(f"{base_url}/water-level/live")
    check(
        "GET /water-level/live",
        status == 200 and b"Water level live" in body,
        f"status={status}",
    )

    status, _, body = http_get(f"{base_url}/water-level/live-value")
    ok = False
    if status == 200:
        try:
            data = json.loads(body)
            ok = HASHKEY_TIMESTAMP in data and float(data[HASHKEY_VALUE]) is not None
        except (ValueError, TypeError, KeyError):
            ok = False
    check(
        "GET /water-level/live-value (JSON)", status == 200 and ok, f"status={status}"
    )

    status, _, body = http_get(
        f"{base_url}/water-level/history?start-utc-timestamp={start_iso}&end-utc-timestamp={end_iso}"
    )
    check(
        "GET /water-level/history",
        status == 200 and b"Water level history" in body,
        f"status={status}",
    )

    status, _, body = http_get(f"{base_url}/water-meter/live")
    check(
        "GET /water-meter/live",
        status == 200 and b"Water meter live" in body,
        f"status={status}",
    )

    status, _, body = http_get(f"{base_url}/water-meter/live-value")
    ok = False
    if status == 200:
        try:
            data = json.loads(body)
            ok = HASHKEY_TIMESTAMP in data and float(data[HASHKEY_VALUE]) is not None
        except (ValueError, TypeError, KeyError):
            ok = False
    check(
        "GET /water-meter/live-value (JSON)", status == 200 and ok, f"status={status}"
    )

    status, _, body = http_get(
        f"{base_url}/water-meter/history?start-utc-timestamp={start_iso}&end-utc-timestamp={end_iso}"
    )
    check(
        "GET /water-meter/history",
        status == 200 and b"Water meter history" in body,
        f"status={status}",
    )

    status, _, body = http_get(f"{base_url}/water-meter/daily-consumption")
    check(
        "GET /water-meter/daily-consumption",
        status == 200 and b"Water meter daily consumption" in body,
        f"status={status}",
    )

    return failures


def count_csv_rows(csv_path: Path) -> int:
    if not csv_path.is_file():
        return 0
    with csv_path.open("r", newline="", encoding="utf-8") as f:
        return max(0, sum(1 for _ in csv.reader(f)) - 1)


def count_images(folder: Path) -> int:
    if not folder.is_dir():
        return 0
    return len(list(folder.glob("*.jpg")))


def verify_csv_file(csv_path: Path, name: str, min_rows: int) -> list[str]:
    """Check that the CSV file exists, has the header 'timestamp,value' and at least
    min_rows data rows with a parseable ISO-timestamp and a parseable float value."""
    failures: list[str] = []
    if not csv_path.is_file():
        return [f"{name}: CSV file was not produced: {csv_path}"]
    with csv_path.open("r", newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        headers = next(reader, None)
        if headers != [HASHKEY_TIMESTAMP, HASHKEY_VALUE]:
            failures.append(f"{name}: wrong CSV header: {headers}")
        valid_rows = 0
        for row in reader:
            if len(row) != 2:
                failures.append(f"{name}: CSV row with wrong column count: {row}")
                continue
            ts, value_str = row
            if not ts or not value_str:
                failures.append(f"{name}: CSV row with empty values: {row}")
                continue
            try:
                datetime.fromisoformat(ts)
                float(value_str)
                valid_rows += 1
            except ValueError:
                failures.append(f"{name}: unparseable CSV row: {row}")
    if valid_rows < min_rows:
        failures.append(
            f"{name}: too few valid data rows in '{csv_path}': got {valid_rows}, "
            f"expected at least {min_rows} (cron '{CRON_EVERY_MINUTE}' within {ARGS.duration}s)"
        )
    else:
        logger.info(f"{name}: CSV '{csv_path.name}' has {valid_rows} valid data rows")
    return failures


def verify_camera_subfolder(folder: Path, min_images: int) -> list[str]:
    """Check that the camera sub-folder contains at least min_images valid JPEG files."""
    failures: list[str] = []
    if not folder.is_dir():
        return [f"camera sub-folder missing: {folder}"]
    valid = 0
    for jpg in sorted(folder.glob("*.jpg")):
        data = jpg.read_bytes()
        if (
            data.startswith(JPEG_MAGIC)
            and cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
            is not None
        ):
            valid += 1
        else:
            failures.append(
                f"camera image '{jpg.name}' in '{folder.name}' is not a valid JPEG"
            )
    if valid < min_images:
        failures.append(
            f"camera sub-folder '{folder.name}': too few valid images: got {valid}, "
            f"expected at least {min_images} (cron '{CRON_EVERY_MINUTE}' within {ARGS.duration}s)"
        )
    else:
        logger.info(f"camera sub-folder '{folder.name}': {valid} valid JPEG images")
    return failures


def main() -> int:
    port = ARGS.port if ARGS.port != 0 else find_free_port()
    flask_port = ARGS.flask_port if ARGS.flask_port != 0 else find_free_port()
    base_url = f"http://127.0.0.1:{flask_port}"
    log_level = ARGS.log_level.upper()

    removed = clean_data_dir()
    logger.info(
        f"Cleaned data-folder '{DATA_DIR}' (removed {removed} files, kept folders with .gitkeep)"
    )
    ensure_data_subfolders()
    logger.info(f"Test redis-server will listen on 127.0.0.1:{port}")
    logger.info(f"Test website will listen on {base_url}")
    logger.info(
        f"Test duration: {ARGS.duration}s, check-interval {ARGS.check_interval}s, "
        f"data-collector cron '{CRON_EVERY_MINUTE}' (collect every minute)"
    )

    redis_proc: subprocess.Popen | None = None
    module_procs: list[tuple[str, subprocess.Popen]] = []
    failures: list[str] = []
    try:
        with tempfile.TemporaryDirectory(prefix="overall-test-") as tmpdir:
            tmp = Path(tmpdir)

            # 1. Start local redis-server via the shell script
            redis_log_path = tmp / "redis-server.log"
            with redis_log_path.open("w") as redis_log:
                redis_proc = subprocess.Popen(
                    ["bash", str(START_REDIS_SCRIPT), str(port)],
                    stdout=redis_log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
            logger.info(
                f"Started '{START_REDIS_SCRIPT.name} {port}' (pid {redis_proc.pid})"
            )

            client = redis.Redis(host="127.0.0.1", port=port)
            if not wait_for_redis(client):
                logger.error(
                    f"redis-server did not answer PING on port {port} within 10s. Log tail:\n{redis_log_path.read_text()[-2000:]}"
                )
                return 2
            logger.info("redis-server is up (PONG)")

            # 2. Start the producing modules
            for camera_number in (0, 1):
                module_procs.append(
                    (
                        f"camera cam:{camera_number}",
                        start_logged_process(
                            f"camera module (cam:{camera_number})",
                            [
                                sys.executable,
                                "-m",
                                "camera",
                                "--redis-server",
                                "127.0.0.1",
                                "--redis-port",
                                str(port),
                                "--redis-key-camera",
                                "cam",
                                "--camera-number",
                                str(camera_number),
                                "--camera-period",
                                str(ARGS.camera_period),
                                "--camera-width",
                                "640",
                                "--camera-height",
                                "480",
                                "--log-level",
                                log_level,
                            ],
                            CAMERA_DIR,
                            tmp / f"camera-module-cam{camera_number}.log",
                        ),
                    )
                )
            module_procs.append(
                (
                    "dummy water-level",
                    start_logged_process(
                        "dummy water-level process",
                        [
                            sys.executable,
                            str(DUMMY_WATER_LEVEL_SCRIPT),
                            "--redis-server",
                            "127.0.0.1",
                            "--redis-port",
                            str(port),
                            "--redis-key-water-level",
                            KEY_WATER_LEVEL,
                            "--wait-time-seconds",
                            str(ARGS.water_level_period),
                            "--log-level",
                            log_level,
                        ],
                        SRC_DIR,
                        tmp / "dummy-water-level.log",
                    ),
                )
            )
            module_procs.append(
                (
                    "water_meter",
                    start_logged_process(
                        "water_meter module",
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
                            "0",
                            "--store-camera-number",
                            "2",
                            "--redis-key-water-meter",
                            KEY_WATER_METER,
                            "--period",
                            str(ARGS.water_meter_period),
                            "--reference-image-jsonfile",
                            "./water_meter/reference_images.json",
                            "--log-level",
                            log_level,
                        ],
                        WATER_METER_MODULE_DIR,
                        tmp / "water-meter-module.log",
                    ),
                )
            )

            # 3. Start the data-collector (collect every minute)
            module_procs.append(
                (
                    "data_collector",
                    start_logged_process(
                        "data_collector module",
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
                            log_level,
                        ],
                        DATA_COLLECTOR_DIR,
                        tmp / "data-collector-module.log",
                    ),
                )
            )

            # 4. Start the website
            module_procs.append(
                (
                    "website",
                    start_logged_process(
                        "website module",
                        [
                            sys.executable,
                            "-m",
                            "website",
                            "--flask-host",
                            "127.0.0.1",
                            "--flask-port",
                            str(flask_port),
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
                            "--log-level",
                            log_level,
                        ],
                        WEBSITE_DIR,
                        tmp / "website-module.log",
                    ),
                )
            )

            if not wait_for_http(base_url):
                logger.error(
                    f"website did not answer HTTP 200 on {base_url} within 30s. Log tail:\n{(tmp / 'website-module.log').read_text()[-3000:]}"
                )
                return 2
            logger.info(f"website is up on {base_url}")

            if not wait_for_redis_keys(
                client, [KEY_CAM_0, KEY_CAM_1, KEY_WATER_LEVEL, KEY_WATER_METER]
            ):
                logger.error(
                    f"producer keys '{KEY_CAM_0}', '{KEY_CAM_1}', '{KEY_WATER_LEVEL}', "
                    f"'{KEY_WATER_METER}' did not all contain data within 90s"
                )
                return 2
            logger.info("all producer keys contain data in redis")

            # 5. Run the automated checks for --duration seconds
            first_ts: dict[str, datetime | None] = {}
            last_ts: dict[str, datetime | None] = {}
            stream_checked = [False]
            logger.info(f"Running automated checks for {ARGS.duration}s ...")
            deadline = time.monotonic() + ARGS.duration
            round_no = 0
            while time.monotonic() < deadline:
                round_no += 1
                round_failures: list[str] = []
                round_failures += check_redis_producers(client, first_ts, last_ts)
                round_failures += check_website(base_url, stream_checked)
                failures.extend(round_failures)
                for f in round_failures:
                    logger.error(f"  round {round_no} FAIL: {f}")
                logger.info(
                    f"  round {round_no}: redis cam:0 ts={last_ts.get(KEY_CAM_0)}, "
                    f"water-level ts={last_ts.get(KEY_WATER_LEVEL)}, "
                    f"water-meter ts={last_ts.get(KEY_WATER_METER)} | "
                    f"CSV rows: water-level={count_csv_rows(WATER_LEVEL_CSV)}, "
                    f"water-meter={count_csv_rows(WATER_METER_CSV)} | "
                    f"images: camera-0={count_images(CAMERA_SUBFOLDERS[0])}, "
                    f"camera-1={count_images(CAMERA_SUBFOLDERS[1])}, "
                    f"camera-2={count_images(CAMERA_SUBFOLDERS[2])}"
                )
                remaining = deadline - time.monotonic()
                time.sleep(min(ARGS.check_interval, max(0.0, remaining)))

            # producers must have advanced their timestamps during the run
            for key in [KEY_CAM_0, KEY_CAM_1, KEY_WATER_LEVEL, KEY_WATER_METER]:
                first_ts_val = first_ts.get(key)
                last_ts_val = last_ts.get(key)
                if first_ts_val is not None and last_ts_val is not None:
                    assert isinstance(first_ts_val, datetime), (
                        f"Expected first_ts[{key}] to be a datetime, but got {type(first_ts_val)}"
                    )
                    assert isinstance(last_ts_val, datetime), (
                        f"Expected last_ts[{key}] to be a datetime, but got {type(last_ts_val)}"
                    )
                    if last_ts_val <= first_ts_val:
                        failures.append(
                            f"redis key '{key}': timestamp did not advance during the run "
                            f"(first={first_ts_val}, last={last_ts_val})"
                        )
                else:
                    failures.append(
                        f"redis key '{key}': no timestamp observed during the run"
                    )
    finally:
        # 6. Stop all modules and the redis-server
        for name, proc in reversed(module_procs):
            logger.info(f"Stopping {name}...")
            stop_process_group(
                proc,
                signal.SIGINT
                if name in ("website", "data_collector")
                else signal.SIGTERM,
            )
        if redis_proc is not None:
            logger.info("Stopping redis-server (via 010 script)...")
            stop_process_group(redis_proc, signal.SIGTERM)

        # 7. Verify the produced files, then clean the data-folder again at test-exit
        logger.info("Verifying produced files in the data-folder ...")
        failures += verify_csv_file(WATER_LEVEL_CSV, "water-level", ARGS.min_collects)
        failures += verify_csv_file(WATER_METER_CSV, "water-meter", ARGS.min_collects)
        failures += verify_camera_subfolder(CAMERA_SUBFOLDERS[0], ARGS.min_collects)
        failures += verify_camera_subfolder(CAMERA_SUBFOLDERS[1], ARGS.min_collects)
        cam2_failures = verify_camera_subfolder(CAMERA_SUBFOLDERS[2], 1)
        if cam2_failures:
            logger.warning(
                f"camera-2 (water-meter ROI images) not fully collected: {cam2_failures}"
            )

        removed = clean_data_dir()
        logger.info(
            f"Cleaned data-folder '{DATA_DIR}' again at test-exit (removed {removed} files)"
        )

    if failures:
        for f in failures:
            logger.error(f"FAIL: {f}")
        return 1
    logger.info(
        "PASS: overall test - producers stored data in redis, data_collector collected "
        "every minute into the data-folder, website served all routes, data-folder cleaned at exit"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
