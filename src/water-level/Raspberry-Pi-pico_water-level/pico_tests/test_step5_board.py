# On-board test for Step 5 (WifiManager) - STA MODE ONLY, no AP test on the board
# (per user instruction; AP fallback is covered by host tests with fakes).
#   mpremote run pico_tests/test_step5_board.py
import sys

if "/pico_tests" not in sys.path:
    sys.path.insert(0, "/pico_tests")

import asyncio

from config import Config
from wifi import WifiManager
from tinytest import run_tests


def make_mgr():
    cfg = Config("config.json")
    cfg.read_from_file()
    return WifiManager(ap_ssid_prefix=cfg.ap_ssid_prefix, ap_password=cfg.ap_password,
                       station_ssid=cfg.station_ssid, station_password=cfg.station_password,
                       connect_timeout=15, retry_interval=5, log_level="INFO")


def test_sta_connected_and_ap_off():
    mgr = make_mgr()
    asyncio.run(mgr._step_once())
    print("status:", mgr.status, "| ip:", mgr.ip, "| gateway:", mgr.gateway, "| dns:", mgr.dns)
    assert mgr.status == "connected as client"
    assert mgr.ip.startswith("192.168.178.")
    assert mgr.gateway == "192.168.178.1"
    assert mgr.status_ap == "not connected"  # AP must stay off while STA works


def test_ap_ssid_format():
    mgr = make_mgr()
    print("AP SSID would be:", mgr.ap_ssid)
    assert mgr.ap_ssid.startswith("RPi-Pico-Water-Level-")
    assert len(mgr.ap_ssid) == len("RPi-Pico-Water-Level-") + 6


if __name__ == "__main__":
    run_tests(globals())
