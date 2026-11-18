"""Integration test for the flask-website module (src/flask-website/website).

Steps:
1. Preseed the repository data-folder with dummy data by running
   '060_create-dummy-database.py' as a subprocess (CSV files for water-level and
   water-meter, camera images for camera-0/camera-1, for the last --preseed-days
   days up to today, UTC). Note: 060 deletes all files in data/ except .gitkeep
   files before generating, so previous runtime data in data/ is replaced.
2. Start a local redis-server via the shell-script '010_start_test-redis-server.sh'
   (on a free port, listening on all interfaces, no password).
3. Start a producer-thread that stores fresh dummy redis-data every
   --producer-period seconds (the test itself is the producer, no other module
   is used):
   - 'cam:0' and 'cam:1': a small JPEG image (hash fields 'timestamp'/'value')
   - 'water-level': a random float value between 4.0 and 6.0
   - 'water-meter': an increasing float value
4. Start the website module (python -m website) as a subprocess, pointing to the
   test redis-server and to the preseeded data-folder.
5. Check all website routes via HTTP (urllib):
   - GET / (dashboard)
   - GET /camera/latest-image (JPEG from Redis, incl. 400 without camera param)
   - GET /camera/image (JPEG from the filesystem by filename, incl. 404)
   - GET /camera/history (page with download card, closest image for a timestamp)
   - GET /camera/history/download (zip-file with the images in a timestamp range,
     incl. default last-24h range, 404 for an empty range, 400 for invalid camera)
   - GET /camera/live-video (page) and /camera/live-video-stream (webm video)
   - GET /water-level/live, /water-level/live-value (JSON from Redis),
     /water-level/history (chart page), /water-level/history/download (.xlsx)
   - GET /water-meter/live, /water-meter/live-value, /water-meter/history,
     /water-meter/history/download (.xlsx), /water-meter/daily-consumption
   The .xlsx downloads are opened with openpyxl and the header row and row count
   are verified.
6. Stop the website, the producer and the redis-server, print PASS/FAIL.

The test runs automatically without user interaction.

Run with the project virtualenv from the repository root, e.g.:
    .venv/bin/python src/tests/070_test_flask-website.py
"""

import argparse
import csv
import http.client
import io
import json
import logging
import os
import random
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import zipfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import cv2
import numpy as np
import redis
from openpyxl import load_workbook

TESTS_DIR = Path(__file__).resolve().parents[0]
SRC_DIR = TESTS_DIR.parent
REPO_ROOT = SRC_DIR.parent
DATA_DIR = REPO_ROOT / "data"
CAMERA_IMAGES_DIR = DATA_DIR / "camera-images"
WATER_LEVEL_CSV = DATA_DIR / "water-level" / "sensor-data.csv"
WATER_METER_CSV = DATA_DIR / "water-meter" / "sensor-data.csv"
START_REDIS_SCRIPT = TESTS_DIR / "010_start_test-redis-server.sh"
DUMMY_DB_SCRIPT = TESTS_DIR / "060_create-dummy-database.py"
WEBSITE_DIR = SRC_DIR / "flask-website"

KEY_CAM_0 = "cam:0"
KEY_CAM_1 = "cam:1"
KEY_WATER_LEVEL = "water-level"
KEY_WATER_METER = "water-meter"
HASHKEY_TIMESTAMP = "timestamp"
HASHKEY_VALUE = "value"

JPEG_MAGIC = b"\xff\xd8\xff"
WEBM_MAGIC = b"\x1a\x45\xdf\xa3"
XLSX_MAGIC = b"PK"
CAMERA_NUMBERS = [0, 1]
CSV_ROWS_PER_DAY = 24 * 60 // 5  # one CSV row every 5 minutes


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Integration test for the flask-website module: preseed the data-folder with 060_create-dummy-database.py, local redis-server (via 010_start_test-redis-server.sh), self-contained producer-thread storing dummy redis-data (cam:0, cam:1, water-level, water-meter), website subprocess, HTTP checks of all routes. Runs automatically without user interaction."
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
        "--producer-period",
        type=float,
        default=1.0,
        help="Interval in seconds between storing fresh dummy values in Redis. (Default: 1.0)",
    )
    parser.add_argument(
        "--preseed-days",
        type=int,
        default=7,
        help="Number of days before today that 060_create-dummy-database.py generates (data is generated up to today, inclusive). (Default: 7)",
    )
    parser.add_argument(
        "--video-duration",
        type=float,
        default=2.0,
        help="Duration in seconds for the /camera/live-video-stream check. (Default: 2.0)",
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
        # also covers non-HTTP answers (e.g. when the port is occupied by another service)
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


def wait_for_producer_data(client: redis.Redis, timeout: float = 10.0) -> bool:
    """Wait until the producer-thread has stored at least one camera image in Redis."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if client.hgetall(KEY_CAM_0):
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
    JPEG images in 'cam:0'/'cam:1', a random water-level value (4.0-6.0) and an
    increasing water-meter value."""
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
                KEY_WATER_LEVEL,
                mapping={
                    HASHKEY_TIMESTAMP: ts,
                    HASHKEY_VALUE: round(random.uniform(4.0, 6.0), 3),
                },
            )
            client.hset(
                KEY_WATER_METER,
                mapping={HASHKEY_TIMESTAMP: ts, HASHKEY_VALUE: 843346.0 + counter},
            )
            counter += 1
        except (redis.RedisError, OSError) as e:
            logger.warning(f"Producer could not store values in Redis: {e}")
        stop_event.wait(period)


def preseed_data_folder(log_path: Path) -> tuple[date, date, list[str], int]:
    """Run 060_create-dummy-database.py to preseed the repository data-folder with
    dummy CSVs and camera images. Returns (start_date, stop_date, expected image
    filenames for camera-0, expected CSV data rows per sensor)."""
    stop_date = datetime.now(UTC).date()
    start_date = stop_date - timedelta(days=ARGS.preseed_days)
    days = (stop_date - start_date).days + 1
    expected_image_names = [
        f"{(start_date + timedelta(days=i)).isoformat()}T00:00:00.000Z.jpg"
        for i in range(days)
    ]
    expected_csv_rows = days * CSV_ROWS_PER_DAY

    logger.info(
        f"Preseeding data-folder '{DATA_DIR}' via '{DUMMY_DB_SCRIPT.name}' "
        f"(--start-date={start_date:%d.%m.%Y} --stop-date={stop_date:%d.%m.%Y} "
        f"--camera-numbers {' '.join(str(c) for c in CAMERA_NUMBERS)}), log: {log_path}. "
        "Note: 060 deletes all files in data/ except .gitkeep files first."
    )
    with log_path.open("w") as log_file:
        result = subprocess.run(
            [
                sys.executable,
                str(DUMMY_DB_SCRIPT),
                "--start-date",
                f"{start_date:%d.%m.%Y}",
                "--stop-date",
                f"{stop_date:%d.%m.%Y}",
                "--camera-numbers",
                *[str(c) for c in CAMERA_NUMBERS],
            ],
            stdout=log_file,
            stderr=subprocess.STDOUT,
            timeout=300,
        )
    if result.returncode != 0:
        raise RuntimeError(
            f"{DUMMY_DB_SCRIPT.name} failed with exit code {result.returncode}, "
            f"log tail:\n{log_path.read_text()[-3000:]}"
        )

    # Verify the preseeded files
    failures: list[str] = []
    for csv_path, name in [(WATER_LEVEL_CSV, "water-level"), (WATER_METER_CSV, "water-meter")]:
        if not csv_path.is_file():
            failures.append(f"preseed: CSV file was not produced: {csv_path}")
            continue
        with csv_path.open("r", newline="", encoding="utf-8") as f:
            row_count = sum(1 for _ in csv.reader(f)) - 1  # minus header
        if row_count != expected_csv_rows:
            failures.append(
                f"preseed: {name} CSV '{csv_path}' has {row_count} data rows, "
                f"expected {expected_csv_rows}"
            )
    cam0_jpgs = sorted(p.name for p in (CAMERA_IMAGES_DIR / "camera-0").glob("*.jpg"))
    if cam0_jpgs != sorted(expected_image_names):
        failures.append(
            f"preseed: camera-0 images mismatch: got {cam0_jpgs}, "
            f"expected {sorted(expected_image_names)}"
        )
    if failures:
        raise RuntimeError("preseed verification failed: " + "; ".join(failures))
    logger.info(
        f"Preseed OK: {expected_csv_rows} CSV rows per sensor, "
        f"{len(expected_image_names)} images per camera folder"
    )
    return start_date, stop_date, expected_image_names, expected_csv_rows


def run_website_checks(
    base_url: str,
    start_date: date,
    stop_date: date,
    expected_image_names: list[str],
    expected_csv_rows: int,
) -> list[str]:
    """Check all website routes via HTTP. Returns a list of failure descriptions."""
    failures: list[str] = []

    def check(name: str, condition: bool, detail: str = "") -> None:
        if condition:
            logger.info(f"  OK: {name}")
        else:
            failures.append(f"{name}: {detail}")
            logger.error(f"  FAIL: {name}: {detail}")

    start_iso = f"{start_date.isoformat()}T00:00:00.000Z"
    end_iso = f"{stop_date.isoformat()}T23:59:59.999Z"
    first_image = expected_image_names[0]
    first_image_ts = first_image[: -len(".jpg")]

    # --- dashboard ---
    status, _, body = http_get(f"{base_url}/")
    check(
        "GET / (dashboard)",
        status == 200 and b'<h1 class="h2 mb-0">Dashboard</h1>' in body,
        f"status={status}",
    )

    # --- camera latest image from redis ---
    for camera in CAMERA_NUMBERS:
        status, headers, body = http_get(f"{base_url}/camera/latest-image?camera={camera}")
        check(
            f"GET /camera/latest-image?camera={camera} (JPEG from Redis)",
            status == 200
            and header_value(headers, "Content-Type").startswith("image/jpeg")
            and body.startswith(JPEG_MAGIC),
            f"status={status}, content-type={header_value(headers, 'Content-Type')}, "
            f"magic={body[:3].hex()}",
        )
    status, _, _ = http_get(f"{base_url}/camera/latest-image")
    check(
        "GET /camera/latest-image without camera -> 400",
        status == 400,
        f"status={status}",
    )

    # --- camera image by filename from the preseeded filesystem ---
    status, headers, body = http_get(
        f"{base_url}/camera/image?camera=0&filename={first_image}"
    )
    check(
        f"GET /camera/image?camera=0&filename={first_image}",
        status == 200
        and header_value(headers, "Content-Type").startswith("image/jpeg")
        and body.startswith(JPEG_MAGIC),
        f"status={status}, content-type={header_value(headers, 'Content-Type')}",
    )
    status, _, _ = http_get(
        f"{base_url}/camera/image?camera=0&filename=does-not-exist.jpg"
    )
    check(
        "GET /camera/image with unknown filename -> 404",
        status == 404,
        f"status={status}",
    )

    # --- camera history page (with download card) and closest image ---
    status, _, body = http_get(f"{base_url}/camera/history?camera=0")
    check(
        "GET /camera/history?camera=0 (download card present)",
        status == 200 and b"Download images" in body and b'id="download-form"' in body,
        f"status={status}",
    )
    status, _, body = http_get(
        f"{base_url}/camera/history?camera=0&selected-utc-timestamp={first_image_ts}"
    )
    check(
        f"GET /camera/history with selected-utc-timestamp={first_image_ts} shows closest image",
        status == 200 and first_image.encode() in body,
        f"status={status}, filename '{first_image}' not in page",
    )

    # --- camera history download: zip with all images in the preseed range ---
    status, headers, body = http_get(
        f"{base_url}/camera/history/download?camera=0"
        f"&start-utc-timestamp={start_iso}&end-utc-timestamp={end_iso}"
    )
    zip_ok = False
    zip_detail = f"status={status}"
    if status == 200:
        try:
            with zipfile.ZipFile(io.BytesIO(body)) as zf:
                zip_names = sorted(zf.namelist())
            zip_ok = zip_names == sorted(expected_image_names)
            zip_detail = (
                f"content-type={header_value(headers, 'Content-Type')}, "
                f"content-disposition={header_value(headers, 'Content-Disposition')}, "
                f"zip entries={zip_names}"
            )
        except zipfile.BadZipFile as e:
            zip_detail = f"body is not a zip file: {e}"
    check(
        "GET /camera/history/download (zip with all preseeded images of camera-0)",
        status == 200
        and header_value(headers, "Content-Type") == "application/zip"
        and "attachment" in header_value(headers, "Content-Disposition")
        and ".zip" in header_value(headers, "Content-Disposition")
        and zip_ok,
        zip_detail,
    )

    # --- camera history download: default range (last 24h) contains today's image ---
    today_image = f"{stop_date.isoformat()}T00:00:00.000Z.jpg"
    status, _, body = http_get(f"{base_url}/camera/history/download?camera=0")
    default_zip_ok = False
    if status == 200:
        try:
            with zipfile.ZipFile(io.BytesIO(body)) as zf:
                default_zip_ok = zf.namelist() == [today_image]
        except zipfile.BadZipFile:
            pass
    check(
        "GET /camera/history/download?camera=0 (default last 24h)",
        status == 200 and default_zip_ok,
        f"status={status}, expected zip entries ['{today_image}']",
    )

    # --- camera history download: empty range -> 404, invalid camera -> 400 ---
    status, _, _ = http_get(
        f"{base_url}/camera/history/download?camera=0"
        "&start-utc-timestamp=2030-01-01T00:00:00.000Z&end-utc-timestamp=2030-01-02T00:00:00.000Z"
    )
    check(
        "GET /camera/history/download with empty range -> 404",
        status == 404,
        f"status={status}",
    )
    status, _, _ = http_get(f"{base_url}/camera/history/download?camera=abc")
    check(
        "GET /camera/history/download?camera=abc -> 400",
        status == 400,
        f"status={status}",
    )

    # --- live video page and stream ---
    status, _, body = http_get(f"{base_url}/camera/live-video?camera=0&duration=2")
    check(
        "GET /camera/live-video?camera=0 (page)",
        status == 200 and b'<h1 class="h2 mb-0">Live video</h1>' in body,
        f"status={status}",
    )
    video_duration = max(1.0, ARGS.video_duration)
    status, headers, body = http_get(
        f"{base_url}/camera/live-video-stream?camera=0&duration={int(video_duration)}",
        timeout=video_duration + 30.0,
    )
    check(
        f"GET /camera/live-video-stream?camera=0&duration={int(video_duration)} (webm from Redis)",
        status == 200
        and header_value(headers, "Content-Type").startswith("video/webm")
        and body.startswith(WEBM_MAGIC),
        f"status={status}, content-type={header_value(headers, 'Content-Type')}, "
        f"magic={body[:4].hex()}, size={len(body)}",
    )

    # --- water-level: live page, live-value JSON, history page, xlsx download ---
    status, _, body = http_get(f"{base_url}/water-level/live")
    check(
        "GET /water-level/live (page)",
        status == 200 and b'<h1 class="h2 mb-0">Water level live</h1>' in body,
        f"status={status}",
    )
    status, _, body = http_get(f"{base_url}/water-level/live-value")
    wl_value_ok = False
    wl_detail = f"status={status}"
    if status == 200:
        try:
            data = json.loads(body)
            wl_value_ok = (
                HASHKEY_TIMESTAMP in data
                and HASHKEY_VALUE in data
                and 4.0 <= float(data[HASHKEY_VALUE]) <= 6.0
            )
            wl_detail = f"json={data}"
        except (ValueError, TypeError, KeyError) as e:
            wl_detail = f"status={status}, cannot parse json: {e}, body={body[:200]!r}"
    check(
        "GET /water-level/live-value (JSON from Redis)",
        status == 200 and wl_value_ok,
        wl_detail,
    )
    status, _, body = http_get(
        f"{base_url}/water-level/history?start-utc-timestamp={start_iso}&end-utc-timestamp={end_iso}"
    )
    check(
        "GET /water-level/history (chart page)",
        status == 200
        and b'<h1 class="h2 mb-0">Water level history</h1>' in body
        and b'id="water-level-chart"' in body,
        f"status={status}",
    )
    status, headers, body = http_get(
        f"{base_url}/water-level/history/download?start-utc-timestamp={start_iso}&end-utc-timestamp={end_iso}"
    )
    xlsx_ok = False
    xlsx_detail = f"status={status}"
    if status == 200 and body.startswith(XLSX_MAGIC):
        try:
            wb = load_workbook(io.BytesIO(body))
            ws = wb.active
            xlsx_ok = (
                ws is not None
                and ws.cell(row=1, column=1).value == HASHKEY_TIMESTAMP
                and ws.cell(row=1, column=2).value == "water level (m)"
                and ws.max_row == expected_csv_rows + 1
            )
            xlsx_detail = (
                f"content-disposition={header_value(headers, 'Content-Disposition')}, "
                f"header={[ws.cell(row=1, column=1).value, ws.cell(row=1, column=2).value] if ws else None}, "
                f"max_row={ws.max_row if ws else None} (expected {expected_csv_rows + 1})"
            )
        except Exception as e:  # noqa: BLE001
            xlsx_detail = f"cannot parse xlsx: {e}"
    check(
        "GET /water-level/history/download (.xlsx with all preseeded rows)",
        status == 200
        and ".xlsx" in header_value(headers, "Content-Disposition")
        and body.startswith(XLSX_MAGIC)
        and xlsx_ok,
        xlsx_detail,
    )

    # --- water-meter: live page, live-value JSON, history page, xlsx download, daily consumption ---
    status, _, body = http_get(f"{base_url}/water-meter/live")
    check(
        "GET /water-meter/live (page)",
        status == 200 and b'<h1 class="h2 mb-0">Water meter live</h1>' in body,
        f"status={status}",
    )
    status, _, body = http_get(f"{base_url}/water-meter/live-value")
    wm_value_ok = False
    wm_detail = f"status={status}"
    if status == 200:
        try:
            data = json.loads(body)
            wm_value_ok = (
                HASHKEY_TIMESTAMP in data
                and HASHKEY_VALUE in data
                and float(data[HASHKEY_VALUE]) >= 843346.0
            )
            wm_detail = f"json={data}"
        except (ValueError, TypeError, KeyError) as e:
            wm_detail = f"status={status}, cannot parse json: {e}, body={body[:200]!r}"
    check(
        "GET /water-meter/live-value (JSON from Redis)",
        status == 200 and wm_value_ok,
        wm_detail,
    )
    status, _, body = http_get(
        f"{base_url}/water-meter/history?start-utc-timestamp={start_iso}&end-utc-timestamp={end_iso}"
    )
    check(
        "GET /water-meter/history (chart page)",
        status == 200
        and b'<h1 class="h2 mb-0">Water meter history</h1>' in body
        and b'id="water-meter-chart"' in body,
        f"status={status}",
    )
    status, headers, body = http_get(
        f"{base_url}/water-meter/history/download?start-utc-timestamp={start_iso}&end-utc-timestamp={end_iso}"
    )
    xlsx_ok = False
    xlsx_detail = f"status={status}"
    if status == 200 and body.startswith(XLSX_MAGIC):
        try:
            wb = load_workbook(io.BytesIO(body))
            ws = wb.active
            xlsx_ok = (
                ws is not None
                and ws.cell(row=1, column=1).value == HASHKEY_TIMESTAMP
                and ws.cell(row=1, column=2).value == "water meter (m³)"
                and ws.max_row == expected_csv_rows + 1
            )
            xlsx_detail = (
                f"content-disposition={header_value(headers, 'Content-Disposition')}, "
                f"header={[ws.cell(row=1, column=1).value, ws.cell(row=1, column=2).value] if ws else None}, "
                f"max_row={ws.max_row if ws else None} (expected {expected_csv_rows + 1})"
            )
        except Exception as e:  # noqa: BLE001
            xlsx_detail = f"cannot parse xlsx: {e}"
    check(
        "GET /water-meter/history/download (.xlsx with all preseeded rows)",
        status == 200
        and ".xlsx" in header_value(headers, "Content-Disposition")
        and body.startswith(XLSX_MAGIC)
        and xlsx_ok,
        xlsx_detail,
    )
    daily_start = (stop_date - timedelta(days=6)).isoformat()
    status, _, body = http_get(
        f"{base_url}/water-meter/daily-consumption?start={daily_start}&end={stop_date.isoformat()}"
    )
    check(
        f"GET /water-meter/daily-consumption?start={daily_start}&end={stop_date.isoformat()}",
        status == 200
        and b'<h1 class="h2 mb-0">Water meter daily consumption</h1>' in body
        and b'id="daily-consumption-chart"' in body,
        f"status={status}",
    )

    return failures


def main() -> int:
    port = ARGS.port if ARGS.port != 0 else find_free_port()
    flask_port = ARGS.flask_port if ARGS.flask_port != 0 else find_free_port()
    base_url = f"http://127.0.0.1:{flask_port}"
    logger.info(f"Test redis-server will listen on 127.0.0.1:{port}")
    logger.info(f"Test website will listen on {base_url}")
    logger.info(
        f"Website args: --redis-server=127.0.0.1 --redis-port={port} "
        f"--camera-images-folder={CAMERA_IMAGES_DIR} --water-level-csv-file={WATER_LEVEL_CSV} "
        f"--water-meter-csv-file={WATER_METER_CSV}"
    )

    with tempfile.TemporaryDirectory(prefix="flask-website-test-") as tmpdir:
        tmp = Path(tmpdir)
        preseed_log_path = tmp / "060_create-dummy-database.log"
        redis_log_path = tmp / "redis-server.log"
        website_log_path = tmp / "website-module.log"

        # 1. Preseed the data-folder with 060_create-dummy-database.py
        try:
            start_date, stop_date, expected_image_names, expected_csv_rows = (
                preseed_data_folder(preseed_log_path)
            )
        except RuntimeError as e:
            logger.error(f"FAIL: {e}")
            return 2

        # 2. Start local redis-server via the shell script
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
        website_proc: subprocess.Popen | None = None
        producer_stop = threading.Event()
        producer_thread: threading.Thread | None = None
        failures: list[str] = []
        try:
            if not wait_for_redis(client):
                logger.error(
                    f"redis-server did not answer PING on port {port} within 10s. Log tail:\n{redis_log_path.read_text()[-2000:]}"
                )
                return 2
            logger.info("redis-server is up (PONG)")

            # 3. Start the producer-thread storing dummy redis-data
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

            # 4. Start the website module
            with website_log_path.open("w") as website_log:
                website_proc = subprocess.Popen(
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
                        ARGS.log_level.upper(),
                    ],
                    cwd=WEBSITE_DIR,
                    stdout=website_log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
            logger.info(
                f"Started website module 'python -m website' (pid {website_proc.pid}, "
                f"cwd={WEBSITE_DIR}), log: {website_log_path}"
            )

            if not wait_for_http(base_url):
                logger.error(
                    f"website did not answer HTTP 200 on {base_url} within 30s. Log tail:\n{website_log_path.read_text()[-3000:]}"
                )
                return 2
            logger.info(f"website is up on {base_url}")

            if not wait_for_producer_data(client):
                logger.error("producer did not store a camera image in Redis within 10s")
                return 2
            logger.info("producer has stored dummy redis-data")

            # 5. Check all website routes via HTTP
            logger.info("Running HTTP checks against all website routes ...")
            failures = run_website_checks(
                base_url, start_date, stop_date, expected_image_names, expected_csv_rows
            )
        finally:
            if website_proc is not None:
                logger.info("Stopping website module...")
                stop_process_group(website_proc, signal.SIGINT)
            if producer_thread is not None:
                producer_stop.set()
                producer_thread.join(timeout=5)
                logger.info("Stopped producer-thread")
            logger.info("Stopping redis-server (via 010 script)...")
            stop_process_group(redis_proc, signal.SIGTERM)

        if failures:
            for f in failures:
                logger.error(f"FAIL: {f}")
            logger.error(
                f"website module log tail:\n{website_log_path.read_text()[-3000:]}"
            )
            return 1
        logger.info(
            "PASS: all website routes answered as expected "
            "(dashboard, camera latest-image/image/history/history-download/live-video/"
            "live-video-stream, water-level live/live-value/history/history-download, "
            "water-meter live/live-value/history/history-download/daily-consumption)"
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
