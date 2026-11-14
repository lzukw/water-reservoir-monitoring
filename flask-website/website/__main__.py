import argparse
import logging
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from flask import Flask, Response, render_template, request, send_file

from .camera_data import CameraData
from .sensor_data import SensorData


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description="Website for Reservoir Monitoring")
    parser.add_argument("--flask-host", type=str, default="127.0.0.1", help="Host for Flask-Server (Default: '127.0.0.1')")
    parser.add_argument("--flask-port", type=int, default=5000, help="Port for Flask-Server (Default: 5000)")
    parser.add_argument("--redis-server", type=str, default="main-raspberry-pi", help="DNS-Name or IP of Redis-Server (Default: 'main-raspberry-pi')")
    parser.add_argument("--redis-port", type=int, default=6379, help="Port-Number of Redis-Server (Default: 6379)")
    parser.add_argument("--redis-key-camera", type=str, default="cam", help="First Part of Redis-Key for Camera-Images, whole key will add : and camera-number. (Default: 'cam', whole key will be e.g. 'cam:0' for camera 0)")
    parser.add_argument("--redis-key-water-level", type=str, default="water-level", help="Redis key for water level values (Default: 'water-level')")
    parser.add_argument("--redis-key-water-meter", type=str, default="water-meter", help="Redis key for water meter values (Default: 'water-meter')")
    parser.add_argument("--redis-hashkey-timestamp", type=str, default="timestamp", help="Hash(Dicctionary)Key for timestamps. (Default: 'timestamp')")
    parser.add_argument("--redis-hashkey-value", type=str, default="value", help="Hash(Dicctionary)Key for values (Default: 'value')")
    parser.add_argument("--camera-images-folder", type=str, default="./data/camera-images", help="Folder for storing cameraimages (Default: './data/camera-images')")
    parser.add_argument("--camera-images-subfolder-prefix", type=str, default="camera-", help="Sub-folder-prefix for storing camera images (Default: 'camera-'")
    parser.add_argument("--water-level-csv-file", type=str, default="./data/water-level/sensor-data.csv", help="CSV-File for storing water level values (Default: './data/water-level/sensor-data.csv')")
    parser.add_argument("--water-meter-csv-file", type=str, default="./data/water-meter/sensor-data.csv", help="CSV-File for storing water meter values (Default: './data/water-meter/sensor-data.csv')")
    parser.add_argument("--log-level", type=str, default="INFO", help="Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)")
    return parser.parse_args()

ARGS = parse_args()

LOG_LEVEL: str = str(ARGS.log_level).upper()

logging.basicConfig(
    stream=sys.stdout,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    level=LOG_LEVEL,
)
logger = logging.getLogger(__name__)

logger.info("Flask host and port: %s:%s", ARGS.flask_host, ARGS.flask_port)
logger.info("Redis server and port: %s:%s", ARGS.redis_server, ARGS.redis_port)
logger.info("Redis keys: camera='%s', water-level='%s', water-meter='%s'", ARGS.redis_key_camera, ARGS.redis_key_water_level, ARGS.redis_key_water_meter)
logger.info("Redis hash keys: timestamp='%s', value='%s'", ARGS.redis_hashkey_timestamp, ARGS.redis_hashkey_value)
logger.info("Camera images folder: %s and subfolder prefix: %s", ARGS.camera_images_folder, ARGS.camera_images_subfolder_prefix)
logger.info("Water-level CSV file: %s", ARGS.water_level_csv_file)
logger.info("Water-meter CSV file: %s", ARGS.water_meter_csv_file)
logger.info("Log level: %s", LOG_LEVEL)


camera_data = CameraData(
    redis_server=ARGS.redis_server,
    redis_port=ARGS.redis_port,
    redis_key_camera=ARGS.redis_key_camera,
    redis_hashkey_timestamp=ARGS.redis_hashkey_timestamp,
    redis_hashkey_value=ARGS.redis_hashkey_value,
    camera_images_folder=Path(ARGS.camera_images_folder),
    camera_images_subfolder_prefix=ARGS.camera_images_subfolder_prefix,
    log_level=LOG_LEVEL
)

water_level_data = SensorData(
    csv_path=Path(ARGS.water_level_csv_file),
    redis_key=ARGS.redis_key_water_level,
    redis_server=ARGS.redis_server,
    redis_port=ARGS.redis_port,
    redis_hashkey_timestamp=ARGS.redis_hashkey_timestamp,
    redis_hashkey_value=ARGS.redis_hashkey_value,
    log_level=LOG_LEVEL
)
water_meter_data = SensorData(
    csv_path=Path(ARGS.water_meter_csv_file),
    redis_key=ARGS.redis_key_water_meter,
    redis_server=ARGS.redis_server,
    redis_port=ARGS.redis_port,
    redis_hashkey_timestamp=ARGS.redis_hashkey_timestamp,
    redis_hashkey_value=ARGS.redis_hashkey_value,
    log_level=LOG_LEVEL
)

def convert_to_isoformat_with_z(dt: datetime | None) -> str| None:
    """Convert a datetime object to an ISO 8601 string with 'Z' for UTC timezone."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        logger.warning(f"The datetime object {dt} is naive (no timezone info). Assuming UTC.")
        dt = dt.replace(tzinfo=UTC)
    return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")

app = Flask(__name__, template_folder="templates", static_folder="static")


@app.route("/")
def dashboard():
    """The main page for the user, showing a card for camera, water level and water meter.
    From here the user can navigate to the camera history, live video, water level history 
    water level live value, and water meter live value and water meter history pages."""
    selected_camera = request.args.get("camera", default="0")

    try:
        selected_camera_int = int(selected_camera)
    except (TypeError, ValueError):
        selected_camera_int = 0

    camera_numbers = camera_data.get_camera_numbers()
    if not camera_numbers:
        camera_numbers = [0]
    if selected_camera_int not in camera_numbers:
        selected_camera_int = camera_numbers[0]

    latest_image = camera_data.get_latest_image_from_redis(selected_camera_int)
    latest_image_timestamp : datetime | None = latest_image[0] if latest_image is not None else None
    
    water_level = water_level_data.get_latest_sensor_value_from_redis()
    water_level_timestamp = water_level.get(ARGS.redis_hashkey_timestamp)
    assert isinstance(water_level_timestamp, datetime) or water_level_timestamp is None, f"Expected water_level_timestamp to be a datetime or None, but got {type(water_level_timestamp)}"
    water_level_value = water_level.get(ARGS.redis_hashkey_value)
    assert isinstance(water_level_value, (float, int)) or water_level_value is None, f"Expected water_level_value to be a float, int or None, but got {type(water_level_value)}"

    water_meter = water_meter_data.get_latest_sensor_value_from_redis()
    water_meter_timestamp = water_meter.get(ARGS.redis_hashkey_timestamp)
    assert isinstance(water_meter_timestamp, datetime) or water_meter_timestamp is None, f"Expected water_meter_timestamp to be a datetime or None, but got {type(water_meter_timestamp)}"
    water_meter_value = water_meter.get(ARGS.redis_hashkey_value)
    assert isinstance(water_meter_value, (float, int)) or water_meter_value is None, f"Expected water_meter_value to be a float, int or None, but got {type(water_meter_value)}"

    return render_template(
        "dashboard.html",
        page_title="Reservoir Monitoring Dashboard",
        camera_numbers=camera_numbers,
        selected_camera=selected_camera_int,
        image_refresh_token=int(datetime.now(UTC).timestamp() * 1000),
        latest_image_timestamp=convert_to_isoformat_with_z(latest_image_timestamp),
        water_level_timestamp=convert_to_isoformat_with_z(water_level_timestamp),
        water_level_value=water_level_value,
        water_meter_timestamp=convert_to_isoformat_with_z(water_meter_timestamp),
        water_meter_value=water_meter_value,
    )

@app.route("/camera/history")
def camera_history():
    """Sub-page for viewing older images from a specific camera. The user can select 
    the date and hour to view the closest image."""
    camera_numbers = camera_data.get_camera_numbers()
    if not camera_numbers:
        camera_numbers = [0]

    requested_camera = request.args.get("camera", default="0")
    try:
        camera_number = int(requested_camera)
    except (TypeError, ValueError):
        camera_number = 0

    if camera_number not in camera_numbers:
        camera_number = camera_numbers[0]

    selected_utc_timestamp_iso = request.args.get("selected-utc-timestamp")
    if selected_utc_timestamp_iso is not None:
        try:
            selected_utc_timestamp = datetime.fromisoformat(selected_utc_timestamp_iso)
            assert selected_utc_timestamp.tzinfo is not None, "The provided selected-utc-timestamp must include timezone information."
        except (TypeError, ValueError):
            selected_utc_timestamp = datetime.now(UTC)
    else:
        selected_utc_timestamp = datetime.now(UTC)
        
    closest_image = camera_data.get_closest_image_from_filesystem(camera_number, selected_utc_timestamp)

    if closest_image is not None:
        closest_image_timestamp = closest_image[0]
        selected_utc_timestamp = closest_image_timestamp
        closest_image_filename = closest_image[1].name
    else:
        closest_image_timestamp = None
        closest_image_filename = ""

    return render_template(
        "camera_history.html",
        page_title=f"Camera {camera_number} history",
        camera_numbers=camera_numbers,
        selected_camera=camera_number,
        utc_time_to_select=convert_to_isoformat_with_z(selected_utc_timestamp),
        image_filename=closest_image_filename,
        image_timestamp=convert_to_isoformat_with_z(closest_image_timestamp)
    )


@app.route("/camera/live-video")
def camera_live_video():
    camera_numbers = camera_data.get_camera_numbers()
    if not camera_numbers:
        camera_numbers = [0]

    selected_camera = request.args.get("camera", default=str(camera_numbers[0]))
    try:
        selected_camera_int = int(selected_camera)
    except (TypeError, ValueError):
        selected_camera_int = camera_numbers[0]
    if selected_camera_int not in camera_numbers:
        selected_camera_int = camera_numbers[0]

    requested_duration = request.args.get("duration", default="5")
    try:
        duration_seconds = int(requested_duration)
    except (TypeError, ValueError):
        duration_seconds = 5
    duration_seconds = max(1, min(60, duration_seconds))

    latest_image = camera_data.get_latest_image_from_redis(selected_camera_int)
    preview_available = latest_image is not None
    preview_timestamp = latest_image[0] if latest_image is not None else None

    return render_template(
        "camera_live_video.html",
        page_title="Live video",
        camera_numbers=camera_numbers,
        selected_camera=selected_camera_int,
        duration_seconds=duration_seconds,
        preview_available=preview_available,
        preview_timestamp=convert_to_isoformat_with_z(preview_timestamp)
    )


@app.route("/camera/latest-image")
def latest_image():
    """Used internally by dashboard and video-stream-banner to fetch the latest image from Redis as a JPEG."""
    if "camera" not in request.args:
        return "Missing camera parameter", 400
    camera_number_str = request.args.get("camera")
    if camera_number_str is None:
        return "Missing camera parameter", 400
    try:
        camera_number = int(camera_number_str)
    except (TypeError, ValueError):
        return "Invalid camera", 400

    img = camera_data.get_latest_image_from_redis(camera_number)
    if not img:
        return "No image available", 404
    _, image_bytes = img
    response = Response(image_bytes, mimetype="image/jpeg")
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


@app.route("/camera/image")
def image_by_filename():
    """Used internally by /camera/history to fetch a specific image by its filename."""
    if "camera" not in request.args:
        return "Missing camera parameter", 400
    camera_number_str = request.args.get("camera")
    if camera_number_str is None:
        return "Missing camera parameter", 400
    try:
        camera_number = int(camera_number_str)
    except (TypeError, ValueError):
        return "Invalid camera", 400

    if "filename" not in request.args:
        return "Missing filename parameter", 400
    filename = request.args.get("filename")
    if not filename or not isinstance(filename, str):
        return "Invalid filename", 400

    image_bytes = camera_data.get_image_bytes(camera_number, filename)
    if not image_bytes:
        return "No image available", 404
    response = Response(image_bytes, mimetype="image/jpeg")
    return response


@app.route("/camera/live-video-stream")
def camera_live_video_stream():
    """Used internally by /camera/live-video to fetch the latest video from Redis as a webm file."""
    camera_numbers = camera_data.get_camera_numbers()
    if not camera_numbers:
        return "No camera available", 404

    selected_camera = request.args.get("camera", default=str(camera_numbers[0]))
    try:
        selected_camera_int = int(selected_camera)
    except (TypeError, ValueError):
        return "Invalid camera", 400
    if selected_camera_int not in camera_numbers:
        return "Camera not available", 404

    requested_duration = request.args.get("duration", default="5")
    try:
        duration_seconds = int(requested_duration)
    except (TypeError, ValueError):
        return "Invalid duration", 400
    if duration_seconds < 1 or duration_seconds > 60:
        return "Duration must be between 1 and 60 seconds", 400

    video_path = camera_data.create_video_from_redis(
        camera_number=selected_camera_int,
        duration_in_seconds=float(duration_seconds),
    )
    if video_path is None or not video_path.exists():
        return "Unable to create video", 503

    response = send_file(
        video_path,
        mimetype="video/webm",
        as_attachment=False,
        download_name=f"camera-{selected_camera_int}_live.webm",
        max_age=0,
    )
    response.headers["Cache-Control"] = "no-store, max-age=0"

    @response.call_on_close
    def _cleanup_temp_video() -> None:
        try:
            video_path.unlink(missing_ok=True)
        except OSError:
            pass

    return response

@app.route("/water-level/live")
def water_level_live_page():
    """Sub-page for viewing the live water level data. The user can see the 
    latest water level value and its timestamp."""
    return render_template(
        "water_level_live.html",
        page_title="Water Level Live Data",
    )

@app.route("/water-level/history")
def water_level_history():
    """Sub-page for viewing water level data between to timestamps. The user 
    can select the start and end timestamp to view the water level values in 
    that range."""

    start = request.args.get("start-utc-timestamp") or None
    end = request.args.get("end-utc-timestamp") or None

    if start is None:
        start_dt = datetime.now(UTC) - timedelta(hours=24)
    else:
        try: 
            start_dt = datetime.fromisoformat(start)
        except ValueError:
            start_dt = datetime.now(UTC) - timedelta(hours=24) 

    if end is None:
        end_dt = datetime.now(UTC)
    else:
        try:
            end_dt = datetime.fromisoformat(end)
        except ValueError:
            end_dt = datetime.now(UTC)
    
    if end_dt < start_dt:
        start_dt, end_dt = end_dt, start_dt

    rows = water_level_data.get_sensor_rows(start_dt, end_dt)
    chart_data = []
    for row in rows:
        row_timestamp = row.get(ARGS.redis_hashkey_timestamp)
        assert isinstance(row_timestamp, datetime)
        value = row.get(ARGS.redis_hashkey_value)
        assert isinstance(value, (float, int)) or value is None
        chart_data.append({
            "timestamp": convert_to_isoformat_with_z(row_timestamp),
            "value": float(value) if value is not None else None,
        })

    return render_template(
        "water_level_history.html",
        page_title="Water Level Live Data",
        start_utc_timestamp=convert_to_isoformat_with_z(start_dt),
        end_utc_timestamp=convert_to_isoformat_with_z(end_dt),
        chart_data=chart_data,
    )

@app.route("/water-meter/live")
def water_meter_live():
    """Sub-page for viewing the live water meter data. The user can see the 
    latest water meter value and its timestamp."""
    return render_template(
        "water_meter_live.html",
        page_title="Water Meter Live Data",
    )


@app.route("/water-meter/history")
def water_meter_history():
    """Sub-page for viewing water meter data between two timestamps."""
    start = request.args.get("start-utc-timestamp") or None
    end = request.args.get("end-utc-timestamp") or None

    if start is None:
        start_dt = datetime.now(UTC) - timedelta(hours=24)
    else:
        try: 
            start_dt = datetime.fromisoformat(start)
        except ValueError:
            start_dt = datetime.now(UTC) - timedelta(hours=24) 

    if end is None:
        end_dt = datetime.now(UTC)
    else:
        try:
            end_dt = datetime.fromisoformat(end)
        except ValueError:
            end_dt = datetime.now(UTC)
    
    if end_dt < start_dt:
        start_dt, end_dt = end_dt, start_dt

    rows = water_meter_data.get_sensor_rows(start_dt, end_dt)
    chart_data = []
    for row in rows:
        row_timestamp = row.get(ARGS.redis_hashkey_timestamp)
        assert isinstance(row_timestamp, datetime)
        value = row.get(ARGS.redis_hashkey_value)
        assert isinstance(value, (float, int)) or value is None
        chart_data.append({
            "timestamp": convert_to_isoformat_with_z(row_timestamp),
            "value": float(value) if value is not None else None,
        })


    return render_template(
        "water_meter_history.html",
        page_title="Water Meter History",
        start_utc_timestamp=convert_to_isoformat_with_z(start_dt),
        end_utc_timestamp=convert_to_isoformat_with_z(end_dt),
        chart_data=chart_data,
    )

@app.route("/water-meter/daily-consumption")
def water_meter_daily_consumption():
    """Sub-page for viewing the daily water consumption based on water meter data."""
    today : datetime = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    default_end : datetime = today
    default_start : datetime = today - timedelta(days=7)

    raw_start : str | None = request.args.get("start")
    raw_end : str | None = request.args.get("end")

    if raw_start:
        try:
            start_dt = datetime.fromisoformat(raw_start).replace(tzinfo=UTC)
        except ValueError:
            start_dt = default_start
    else:
        start_dt = default_start

    if raw_end:
        try:
            end_dt = datetime.fromisoformat(raw_end).replace(tzinfo=UTC)
        except ValueError:
            end_dt = default_end
    else:
        end_dt = default_end

    if end_dt < start_dt:
        start_dt, end_dt = end_dt, start_dt

    daily_consumption_data = water_meter_data.get_daily_value_increases(start_dt, end_dt)

    return render_template(
        "water_meter_daily_consumption.html",
        page_title="Water Meter Daily Consumption",
        start_value=start_dt.strftime("%Y-%m-%d"),
        end_value=end_dt.strftime("%Y-%m-%d"),
        chart_data=daily_consumption_data,
    )


@app.route("/water-level/live-value")
def water_level_live_value():
    """Used internally by /water-level/live to fetch the latest sensor values from Redis as json."""
    return water_level_data.get_latest_sensor_value_from_redis()


@app.route("/water-meter/live-value")
def water_meter_live_value():
    """Used internally by /water-meter/live to fetch the latest sensor values from Redis as json."""
    return water_meter_data.get_latest_sensor_value_from_redis()


if __name__ == "__main__":
    app.run(host=ARGS.flask_host, port=ARGS.flask_port, debug=False)

# Command-line Args for debugging:
# --camera-images-folder src/data/camera-images --water-level-csv-file src/data/water-level/sensor-data.csv --water-meter-csv-file src/data/water-meter/sensor-data.csv --redis-server main-raspberry-pi --log-level=DEBUG
