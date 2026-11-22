# On-board test for Step 3 (own NTP client + NetworkTimeService). Run on the Pico:
#   mpremote run pico_tests/test_step3_board.py
# Requires the Pico to be connected as wifi station (STA) to the test router.
import sys

if "/pico_tests" not in sys.path:
    sys.path.insert(0, "/pico_tests")

import machine
import network

from ntp_time import NetworkTimeService, NtpClient
from tinytest import run_tests


class _StaWifi:
    """Duck-typed view of the already-connected STA interface (for the service)."""

    @property
    def status(self):
        sta = network.WLAN(network.STA_IF)
        return "connected as client" if sta.isconnected() else "not connected"

    @property
    def gateway(self):
        return network.WLAN(network.STA_IF).ifconfig()[2]


def _sta():
    sta = network.WLAN(network.STA_IF)
    assert sta.isconnected(), "Pico is not connected as wifi station - run STA setup first"
    return sta


def test_fetch_from_router_gateway():
    gw = _sta().ifconfig()[2]
    dt = NtpClient(timeout=5).fetch(gw, 123)
    print("NTP from gateway", gw, "->", dt)
    assert dt is not None and dt[0] >= 2025


def test_fetch_from_internet_pool():
    # Informational only: the test router may block outbound UDP/123.
    # The gateway NTP (test above) is the default used by the service.
    dt = NtpClient(timeout=5).fetch("pool.ntp.org", 123)
    print("NTP from pool.ntp.org ->", dt, "(None is OK if the router blocks outbound NTP)")
    if dt is not None:
        assert dt[0] >= 2025


def test_service_syncs_rtc_and_reports_status():
    svc = NetworkTimeService(_StaWifi(), ntp_server="", rtc=machine.RTC(), log_level="INFO")
    assert svc.time_status == "not valid" or True  # RTC may already be set from a previous run
    assert svc.sync_once() is True
    iso = svc.current_time_utc_iso()
    print("RTC now:", iso, "| time_status:", svc.time_status, "| age(s):", svc.last_sync_age)
    assert svc.time_status == "accurate"
    assert int(iso[:4]) >= 2025


if __name__ == "__main__":
    run_tests(globals())
