import asyncio
import calendar
import datetime
import socket
import threading
import time

import machine  # dummy from conftest.py (CPython host)
import pytest

from ntp_time import (
    NTP_DELTA_1900_TO_1970,
    NetworkTimeService,
    NtpClient,
    civil_from_ntp_seconds,
)

KNOWN_UTC = (2026, 11, 21, 15, 30, 0)  # a Saturday


def ntp_secs(y, mo, d, h, mi, s):
    return calendar.timegm((y, mo, d, h, mi, s, 0, 0, 0)) + NTP_DELTA_1900_TO_1970


class FakeNtpServer(threading.Thread):
    """Minimal UDP NTP server replying with a fixed transmit timestamp."""

    def __init__(self, reply_mode=4, stratum=2, ntp_secs_val=None, garbage=False):
        super().__init__(daemon=True)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        self.reply_mode = reply_mode
        self.stratum = stratum
        self.garbage = garbage
        self.ntp_secs = ntp_secs_val if ntp_secs_val is not None else ntp_secs(*KNOWN_UTC)
        self.stop_flag = False
        self.received = 0

    def run(self):
        self.sock.settimeout(0.2)
        while not self.stop_flag:
            try:
                data, addr = self.sock.recvfrom(1024)
            except socket.timeout:
                continue
            except OSError:
                break
            self.received += 1
            if self.garbage:
                self.sock.sendto(b"\x00" * 10, addr)
                continue
            resp = bytearray(data)
            resp[0] = (0 << 6) | (3 << 3) | self.reply_mode
            resp[1] = self.stratum
            resp[40:44] = self.ntp_secs.to_bytes(4, "big")
            resp[44:48] = (0).to_bytes(4, "big")
            self.sock.sendto(bytes(resp), addr)

    def stop(self):
        self.stop_flag = True
        self.sock.close()


class FakeWifi:
    def __init__(self, status="not connected", gateway=""):
        self.status = status
        self.gateway = gateway


@pytest.fixture(autouse=True)
def _fresh_rtc():
    """machine.RTC() is a singleton (like on the Pico) - reset it per test."""
    machine.RTC().dt = None
    yield


@pytest.fixture
def ntp_server():
    s = FakeNtpServer()
    s.start()
    yield s
    s.stop()
    s.join(timeout=2)


# ------------------------------------------------------- civil date math
def test_civil_from_ntp_seconds_matches_host_calendar():
    for (y, mo, d) in [(1970, 1, 1), (2000, 1, 1), (2000, 2, 29), (2026, 11, 21),
                       (2038, 1, 19), (2100, 3, 1), (2026, 12, 31)]:
        h, mi, s = 23, 59, 59
        got = civil_from_ntp_seconds(ntp_secs(y, mo, d, h, mi, s))
        assert got[:3] == (y, mo, d)
        assert got[3] == datetime.date(y, mo, d).weekday()  # 0 = Monday
        assert got[4:7] == (h, mi, s)


# ------------------------------------------------------------- NtpClient
def test_fetch_from_fake_server(ntp_server):
    c = NtpClient(timeout=2)
    dt = c.fetch("127.0.0.1", ntp_server.port)
    assert dt == (2026, 11, 21, 5, 15, 30, 0)  # Saturday -> weekday 5
    assert ntp_server.received == 1


def test_parse_reply_rejects_garbage_and_kiss_of_death():
    c = NtpClient()
    assert c.parse_reply(b"\x00" * 10) is None
    assert c.parse_reply(b"\x00" * 48) is None
    kod = bytearray(48)
    kod[0] = (0 << 6) | (3 << 3) | 4
    kod[1] = 0  # stratum 0 = kiss of death
    kod[40:44] = ntp_secs(*KNOWN_UTC).to_bytes(4, "big")
    assert c.parse_reply(bytes(kod)) is None


def test_fetch_timeout_is_bounded_and_does_not_accumulate():
    # grab a free port, then close it -> nothing listens there
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    c = NtpClient(timeout=1.0)
    t0 = time.monotonic()
    dt = c.fetch("127.0.0.1", port)
    elapsed = time.monotonic() - t0
    assert dt is None
    assert elapsed < 2.0, f"fetch blocked {elapsed:.2f}s - blocking accumulated!"


# ---------------------------------------------------- NetworkTimeService
def test_service_default_rtc_comes_from_machine_module(ntp_server):
    """No rtc= passed -> ntp_time does `import machine; machine.RTC()`.
    On the CPython host that is the dummy machine module from conftest.py."""
    wifi = FakeWifi(status="connected as client", gateway="127.0.0.1")
    svc = NetworkTimeService(wifi, ntp_server="127.0.0.1", ntp_port=ntp_server.port,
                             log_level="ERROR")
    assert svc._rtc is machine.RTC()  # singleton, like on the Pico
    assert svc.sync_once() is True
    assert svc._rtc.datetime()[:7] == (2026, 11, 21, 5, 15, 30, 0)


def test_service_sync_status_and_iso(ntp_server):
    wifi = FakeWifi(status="connected as client", gateway="127.0.0.1")
    rtc = machine.RTC()
    svc = NetworkTimeService(wifi, ntp_server="127.0.0.1", ntp_port=ntp_server.port,
                             valid_time_threshold=3600, log_level="ERROR")
    assert svc.time_status == "not valid"
    assert svc.last_sync_age is None
    assert svc.sync_once() is True
    assert rtc.dt[:7] == (2026, 11, 21, 5, 15, 30, 0)
    assert svc.time_status == "accurate"
    assert svc.current_time_utc_iso() == "2026-11-21T15:30:00.000Z"
    assert svc.last_sync_age is not None and svc.last_sync_age < 5


def test_service_uses_gateway_as_default_ntp_server(ntp_server):
    wifi = FakeWifi(status="connected as client", gateway="127.0.0.1")
    svc = NetworkTimeService(wifi, ntp_server="", ntp_port=ntp_server.port,
                             log_level="ERROR")
    assert svc._server() == "127.0.0.1"  # fell back to gateway


def test_service_not_connected_as_client(ntp_server):
    wifi = FakeWifi(status="connected as AP")
    svc = NetworkTimeService(wifi, ntp_server="127.0.0.1", ntp_port=ntp_server.port,
                             log_level="ERROR")
    assert svc.sync_once() is False
    assert svc.time_status == "not valid"
    assert ntp_server.received == 0  # no request was sent


def test_service_inaccurate_when_stale():
    wifi = FakeWifi(status="connected as client", gateway="127.0.0.1")
    svc = NetworkTimeService(wifi, ntp_server="127.0.0.1", ntp_port=1,
                             valid_time_threshold=100, log_level="ERROR")
    svc._last_sync_ticks = int(time.monotonic() * 1000) - 200_000  # 200 s ago
    assert svc.time_status == "inaccurate"
    assert 199 <= svc.last_sync_age <= 202


def test_run_forever_recovers_after_outage(ntp_server):
    async def scenario():
        wifi = FakeWifi(status="not connected")  # outage: no sta connection
        rtc = machine.RTC()
        svc = NetworkTimeService(wifi, ntp_server="127.0.0.1", ntp_port=ntp_server.port,
                                 period=3600, valid_time_threshold=3600,
                                 retry_interval=0.2, log_level="ERROR")
        task = asyncio.create_task(svc.run_forever())
        try:
            await asyncio.sleep(0.6)
            assert not task.done()
            assert svc.time_status == "not valid"   # still in outage
            wifi.status = "connected as client"     # connection returns
            for _ in range(50):
                if svc.time_status == "accurate":
                    break
                await asyncio.sleep(0.1)
            assert svc.time_status == "accurate"     # auto-recovered
            assert rtc.dt[:7] == (2026, 11, 21, 5, 15, 30, 0)
            assert svc.current_time_utc_iso() == "2026-11-21T15:30:00.000Z"
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    asyncio.run(scenario())
