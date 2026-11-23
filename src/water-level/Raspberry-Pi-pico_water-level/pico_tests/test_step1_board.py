# On-board test for Step 1 (logger + config). Run on the Pico with:
#   mpremote run pico_tests/test_step1_board.py
import sys

if "/pico_tests" not in sys.path:
    sys.path.insert(0, "/pico_tests")

from config import Config
from logger import Logger
from tinytest import run_tests


def test_logger_levels():
    log = Logger("T", "ERROR")
    assert log.level == 40
    log.set_level("debug")
    assert log.level == 10
    log.info("this must not crash")


def test_config_loads_project_file():
    c = Config("config.json")
    assert c.read_from_file() is True
    # values depend on the deployed config (project config or laptop test config)
    assert c.hostname
    assert c.ap_ssid_prefix
    assert c.ap_password
    assert c.station_ssid
    assert c.station_password
    assert c.redis_server
    assert c.redis_port == 6379
    assert c.redis_key == "water-level"
    assert c.redis_hashkey_timestamp == "timestamp"
    assert c.redis_hashkey_value == "value"
    assert c.distance_to_bottom == 5.25


def test_config_write_roundtrip_on_board():
    c = Config("config.json")
    c.read_from_file()
    c.hostname = "board-test"
    assert c.write_to_file() is True
    assert c.status == "saved"
    c2 = Config("config.json")
    assert c2.read_from_file() is True
    assert c2.hostname == "board-test"
    # restore original value
    c2.hostname = "rpi-pico-water-level"
    assert c2.write_to_file() is True


def test_config_missing_file():
    c = Config("does_not_exist.json")
    assert c.read_from_file() is False
    assert c.status == "not loaded"
    assert c.hostname is None


if __name__ == "__main__":
    run_tests(globals())
