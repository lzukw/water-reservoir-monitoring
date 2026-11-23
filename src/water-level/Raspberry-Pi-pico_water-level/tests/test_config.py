import json
import os

import pytest

from config import Config


@pytest.fixture
def cfg_file(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({
        "hostname": "rpi-pico-water-level",
        "ap_ssid_prefix": "RPi-Pico-Water-Level",
        "ap_password": "1234567890",
        "station_ssid": "MyWifi",
        "station_password": "secret",
        "redis_server": "main-raspberry-pi",
        "redis_key": "water-level",
        "redis_hashkey_timestamp": "timestamp",
        "redis_hashkey_value": "value",
        "redis_port": 6379,
        "distance_to_bottom": 5.25,
        "some_extra_key": "preserved",
    }))
    return str(path)


def test_read_all_keys(cfg_file):
    c = Config(cfg_file)
    assert c.read_from_file() is True
    assert c.status == "loaded"
    assert c.hostname == "rpi-pico-water-level"
    assert c.ap_ssid_prefix == "RPi-Pico-Water-Level"
    assert c.ap_password == "1234567890"
    assert c.station_ssid == "MyWifi"
    assert c.station_password == "secret"
    assert c.redis_server == "main-raspberry-pi"
    assert c.redis_port == 6379
    assert c.redis_key == "water-level"
    assert c.redis_hashkey_timestamp == "timestamp"
    assert c.redis_hashkey_value == "value"
    assert c.distance_to_bottom == 5.25


def test_missing_file(tmp_path):
    c = Config(str(tmp_path / "nope.json"))
    assert c.read_from_file() is False
    assert c.status == "not loaded"
    assert c.hostname is None


def test_broken_json(tmp_path):
    p = tmp_path / "config.json"
    p.write_text("{ not valid json !!!")
    c = Config(str(p))
    assert c.read_from_file() is False
    assert c.status == "not loaded"


def test_json_array_is_rejected(tmp_path):
    p = tmp_path / "config.json"
    p.write_text("[1, 2, 3]")
    c = Config(str(p))
    assert c.read_from_file() is False


def test_write_roundtrip_preserves_unknown_keys(cfg_file):
    c = Config(cfg_file)
    c.read_from_file()
    c.hostname = "new-host"
    c.redis_port = 6380
    c.distance_to_bottom = 4.5
    assert c.write_to_file() is True
    assert c.status == "saved"

    c2 = Config(cfg_file)
    c2.read_from_file()
    assert c2.hostname == "new-host"
    assert c2.redis_port == 6380
    assert c2.distance_to_bottom == 4.5
    assert c2.get("some_extra_key") == "preserved"  # unknown keys survive a rewrite


def test_set_marks_modified(cfg_file):
    c = Config(cfg_file)
    c.read_from_file()
    assert c.status == "loaded"
    c.station_ssid = "OtherWifi"
    assert c.status == "modified"
    assert c.station_ssid == "OtherWifi"


def test_get_default_for_missing_key(cfg_file):
    c = Config(cfg_file)
    c.read_from_file()
    assert c.get("does_not_exist") is None
    assert c.get("does_not_exist", 42) == 42


def test_real_project_config_json():
    """The config.json shipped with the project must load and contain all required keys."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    c = Config(os.path.join(root, "config.json"))
    assert c.read_from_file() is True
    for key in ("hostname", "ap_ssid_prefix", "ap_password", "station_ssid", "station_password",
                "redis_server", "redis_port", "redis_key", "redis_hashkey_timestamp",
                "redis_hashkey_value", "distance_to_bottom"):
        assert c.get(key) is not None, f"missing key in project config.json: {key}"
