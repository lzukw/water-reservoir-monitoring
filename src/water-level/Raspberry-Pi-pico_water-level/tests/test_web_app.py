import json
import socket
import threading
import time

import httpx
import pytest

from config import Config
from web_app import WebApp

PORT = 18080
BASE = "http://127.0.0.1:%d" % PORT

CONFIG_DATA = {
    "hostname": "rpi-pico-water-level",
    "ap_ssid_prefix": "RPi-Pico-Water-Level",
    "ap_password": "1234567890",
    "station_ssid": "FRITZ!Box 6825 NR",
    "station_password": "12345678",
    "redis_server": "main-raspberry-pi",
    "redis_key": "water-level",
    "redis_hashkey_timestamp": "timestamp",
    "redis_hashkey_value": "value",
    "redis_port": 6379,
    "distance_to_bottom": 5.25,
}

FORM_DATA = {
    "hostname": "rpi-pico-water-level",
    "ap_ssid_prefix": "RPi-Pico-Water-Level",
    "ap_password": "1234567890",
    "station_ssid": "FRITZ!Box 6825 NR",
    "station_password": "12345678",
    "redis_server": "main-raspberry-pi",
    "redis_port": "6379",
    "redis_key": "water-level",
    "redis_hashkey_timestamp": "timestamp",
    "redis_hashkey_value": "value",
    "distance_to_bottom_in_cm": "525",
}


class FakeWifi:
    status = "connected as client"
    status_sta = "connected as client"
    status_ap = "not connected"
    ip = "192.168.178.28"
    gateway = "192.168.178.1"


class FakeNtp:
    time_status = "accurate"

    def current_time_utc_iso(self):
        return "2026-11-21T15:30:00.000Z"


class FakeRedis:
    status = "connected"
    last_error = ""
    publish_count = 42


class FakeSensor:
    last_distance = 0.46


@pytest.fixture
def server(tmp_path):
    cfg_path = str(tmp_path / "config.json")
    with open(cfg_path, "w") as f:
        json.dump(CONFIG_DATA, f)
    cfg = Config(cfg_path)
    assert cfg.read_from_file()
    applied = []

    async def on_apply():
        applied.append(True)

    app = WebApp(cfg, FakeWifi(), FakeNtp(), FakeRedis(), FakeSensor(), on_apply=on_apply)
    md = app.get_app()
    th = threading.Thread(target=md.run, kwargs={"host": "127.0.0.1", "port": PORT}, daemon=True)
    th.start()
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            s = socket.create_connection(("127.0.0.1", PORT), 0.3)
            s.close()
            break
        except OSError:
            time.sleep(0.1)
    else:
        raise RuntimeError("test web server did not start")
    yield BASE, cfg, cfg_path, applied
    try:
        md.shutdown()
    except Exception:
        pass


def test_get_test_route(server):
    base, *_ = server
    r = httpx.get(base + "/test")
    assert r.status_code == 200 and r.text == "ok"


def test_get_index_shows_config_form(server):
    base, *_ = server
    r = httpx.get(base + "/")
    assert r.status_code == 200
    assert 'name="hostname"' in r.text
    assert 'value="rpi-pico-water-level"' in r.text
    assert 'value="525"' in r.text  # distance_to_bottom_in_cm


def test_get_status_json(server):
    base, *_ = server
    r = httpx.get(base + "/status")
    assert r.status_code == 200
    d = r.json()
    assert d["wifi_status"] == "connected as client"
    assert d["ip"] == "192.168.178.28"
    assert d["ntp_status"] == "accurate"
    assert d["ntp_time"] == "2026-11-21T15:30:00.000Z"
    assert d["redis_status"] == "connected"
    assert d["redis_publish_count"] == 42
    assert d["ultrasonic_distance"] == "0.46 m"
    assert d["distance_to_bottom"] == "5.25 m"
    assert d["water_level"] == "4.79 m"


def test_get_status_page(server):
    base, *_ = server
    r = httpx.get(base + "/status-page")
    assert r.status_code == 200
    assert "wifi_status" in r.text and "water_level" in r.text and "/status" in r.text


def test_post_unchanged_config_saves_without_apply(server):
    base, cfg, cfg_path, applied = server
    r = httpx.post(base + "/", data=FORM_DATA)
    assert r.status_code == 200
    assert "no changes" in r.text.lower()
    assert applied == []
    cfg2 = Config(cfg_path)
    cfg2.read_from_file()
    assert cfg2.station_ssid == "FRITZ!Box 6825 NR"


def test_post_changed_wifi_triggers_apply_and_persists(server):
    base, cfg, cfg_path, applied = server
    data = dict(FORM_DATA)
    data["station_ssid"] = "OtherNetwork"
    data["redis_port"] = "6380"
    r = httpx.post(base + "/", data=data)
    assert r.status_code == 200
    assert "restart" in r.text.lower()
    cfg2 = Config(cfg_path)
    cfg2.read_from_file()
    assert cfg2.station_ssid == "OtherNetwork"
    assert cfg2.redis_port == 6380
    assert cfg2.distance_to_bottom == 5.25
    # on_apply was scheduled on the server loop -> wait for it
    deadline = time.time() + 3
    while not applied and time.time() < deadline:
        time.sleep(0.05)
    assert applied == [True]


def test_post_missing_field_rejected(server):
    base, cfg, cfg_path, applied = server
    data = dict(FORM_DATA)
    data["station_password"] = ""
    r = httpx.post(base + "/", data=data)
    assert r.status_code == 200
    assert "missing fields" in r.text
    assert "station_password" in r.text
    cfg2 = Config(cfg_path)
    cfg2.read_from_file()
    assert cfg2.station_password == "12345678"  # unchanged
    assert applied == []


def test_post_bad_port_and_cm_are_clamped(server):
    base, cfg, cfg_path, applied = server
    data = dict(FORM_DATA)
    data["redis_port"] = "99999"
    data["distance_to_bottom_in_cm"] = "1"
    httpx.post(base + "/", data=data)
    cfg2 = Config(cfg_path)
    cfg2.read_from_file()
    assert cfg2.redis_port == 65535
    assert cfg2.distance_to_bottom == 0.25  # clamped to 25 cm


def test_html_escaping_in_config_page(server, tmp_path):
    base, cfg, cfg_path, applied = server
    cfg.hostname = '<script>alert(1)</script>'
    r = httpx.get(base + "/")
    assert "<script>alert(1)</script>" not in r.text
    assert "&lt;script&gt;" in r.text
