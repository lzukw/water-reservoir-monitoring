# On-board test for Step 5b: STA recovery after a connection drop (no AP involved).
#   mpremote run pico_tests/test_step5b_board.py
import sys

if "/pico_tests" not in sys.path:
    sys.path.insert(0, "/pico_tests")

import asyncio

import network

from config import Config
from tinytest import run_tests
from wifi import WifiManager


def test_sta_recovery_after_drop():
    cfg = Config("config.json")
    cfg.read_from_file()
    mgr = WifiManager(cfg.ap_ssid_prefix, cfg.ap_password,
                      cfg.station_ssid or "", cfg.station_password or "",
                      connect_timeout=15, retry_interval=5, log_level="INFO")

    async def scenario():
        await mgr._step_once()  # make sure we are connected
        assert mgr.status_sta == "connected as client", mgr.status_sta
        ip_before = mgr.ip
        sta = network.WLAN(network.STA_IF)
        sta.active(False)  # simulate wifi loss (router gone)
        assert mgr.status_sta == "not connected"
        await mgr._step_once()  # state machine must reconnect (router is back)
        assert mgr.status_sta == "connected as client", mgr.status_sta
        assert mgr.status_ap == "not connected"  # AP stays off: router reachable
        print("reconnected, ip:", mgr.ip, "(before:", ip_before, ")")

    asyncio.run(scenario())


if __name__ == "__main__":
    run_tests(globals())
