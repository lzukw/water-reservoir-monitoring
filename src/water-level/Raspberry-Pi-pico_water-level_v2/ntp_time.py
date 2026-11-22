"""Own NTP client + network-time service.

Written from scratch (no ntptime module) so it runs unchanged on MicroPython
(Pico) and CPython (host tests):

- NtpClient.fetch() sends a 48-byte NTPv3 UDP request and parses the reply.
  The whole call is bounded by ONE socket timeout budget (send + single recv),
  so blocking time does not accumulate. Returns a datetime tuple or None.
- NetworkTimeService keeps the RTC in sync: run_forever() resyncs periodically
  while the Pico is connected as wifi station, and never dies. If wifi/NTP is
  lost, time_status degrades ("accurate" -> "inaccurate" -> "not valid") and
  syncing resumes automatically when the connection is back.

The RTC is machine.RTC() (on the CPython host tests, the dummy machine
module from tests/conftest.py provides it).
"""

import asyncio
import socket

import machine

from logger import Logger
from timeutil import elapsed_ms as _elapsed_ms
from timeutil import now_ms as _now_ms

NTP_PORT = 123
NTP_DELTA_1900_TO_1970 = 2208988800  # seconds between 1900-01-01 and 1970-01-01
DAYS_1900_TO_1970 = 25567


def civil_from_ntp_seconds(ntp_secs: int):
    """Convert seconds since 1900-01-01 00:00:00 UTC (NTP epoch) to
    (year, month, day, weekday, hour, minute, second). Pure integer math,
    portable across MicroPython (epoch 2000) and CPython (epoch 1970).
    weekday: 0 = Monday (1900-01-01 was a Monday)."""
    days_since_1900 = ntp_secs // 86400
    secs_of_day = ntp_secs % 86400
    z = days_since_1900 - DAYS_1900_TO_1970  # days since 1970-01-01
    z += 719468
    era = (z if z >= 0 else z - 146096) // 146097
    doe = z - era * 146097
    yoe = (doe - doe // 1460 + doe // 36524 - doe // 146096) // 365
    year = yoe + era * 400
    doy = doe - (365 * yoe + yoe // 4 - yoe // 100)
    mp = (5 * doy + 2) // 153
    day = doy - (153 * mp + 2) // 5 + 1
    month = mp + 3 if mp < 10 else mp - 9
    if month <= 2:
        year += 1
    weekday = days_since_1900 % 7  # 0 = Monday
    hour = secs_of_day // 3600
    minute = (secs_of_day % 3600) // 60
    second = secs_of_day % 60
    return (year, month, day, weekday, hour, minute, second)


class NtpClient:
    """Minimal NTPv3 client (UDP). fetch() blocks at most ~timeout seconds."""

    def __init__(self, timeout: float = 3.0):
        self._timeout = timeout

    def build_request(self) -> bytes:
        # LI=0, VN=3, Mode=3 (client) -> 0x1B, rest zero, 48 bytes total
        req = bytearray(48)
        req[0] = 0x1B
        return bytes(req)

    def parse_reply(self, data: bytes):
        """Parse a 48-byte NTP reply -> datetime tuple, or None if invalid."""
        if len(data) < 48:
            return None
        mode = data[0] & 0x07
        stratum = data[1]
        if mode != 4 or stratum == 0:  # not a server reply / kiss-of-death
            return None
        secs = int.from_bytes(data[40:44], "big")  # transmit timestamp
        if secs == 0:
            return None
        return civil_from_ntp_seconds(secs)

    def fetch(self, server: str, port: int = NTP_PORT):
        """Fetch current time from `server`. Returns datetime tuple
        (year, month, day, weekday, hour, minute, second) or None on any
        failure. Blocks at most ~self._timeout seconds (one budget for the
        whole call; DNS resolution of `server` is the only unbounded part)."""
        sock = None
        try:
            addr = socket.getaddrinfo(server, port, 0, socket.SOCK_DGRAM)[0][-1]
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(self._timeout)
            sock.sendto(self.build_request(), addr)
            data, _ = sock.recvfrom(48)
            return self.parse_reply(data)
        except (OSError, ValueError):
            return None
        finally:
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass


class NetworkTimeService:
    """Keeps the RTC synced via NTP while the Pico has a wifi station connection.

    Robustness: run_forever() never raises. If wifi/NTP is lost, it retries
    every `retry_interval` seconds and resumes syncing automatically when the
    connection returns."""

    def __init__(self, wifi, ntp_server: str = "", ntp_port: int = NTP_PORT,
                 period: int = 6 * 60 * 60, valid_time_threshold: int = 24 * 60 * 60,
                 retry_interval: float = 10.0, timeout: float = 3.0,
                 log_level: str = "INFO"):
        self._wifi = wifi  # duck-typed: needs .status and .gateway
        self._ntp_server = ntp_server
        self._ntp_port = ntp_port
        self._period = period
        self._valid_time_threshold = valid_time_threshold
        self._retry_interval = retry_interval
        self._client = NtpClient(timeout=timeout)
        self._logger = Logger("NTP", log_level)
        self._rtc = machine.RTC()
        self._last_sync_ticks = None  # ms ticks of last successful sync

    # ------------------------------------------------------------- status
    @property
    def last_sync_age(self):
        """Seconds since the last successful NTP sync, or None if never synced."""
        if self._last_sync_ticks is None:
            return None
        return _elapsed_ms(self._last_sync_ticks) / 1000.0

    @property
    def time_status(self) -> str:
        """'not valid' (never synced), 'inaccurate' (stale) or 'accurate'."""
        age = self.last_sync_age
        if age is None:
            return "not valid"
        if age > self._valid_time_threshold:
            return "inaccurate"
        return "accurate"

    def current_time_utc_iso(self) -> str:
        """Current UTC time from the RTC as ISO 8601 string (ms precision)."""
        dt = self._rtc.datetime()
        assert dt is not None, "RTC datetime is None. This should never happen."
        return f"{dt[0]:04d}-{dt[1]:02d}-{dt[2]:02d}T{dt[4]:02d}:{dt[5]:02d}:{dt[6]:02d}.000Z"

    # --------------------------------------------------------------- sync
    def _server(self) -> str:
        if self._ntp_server:
            return self._ntp_server
        gw = getattr(self._wifi, "gateway", "") or ""
        if gw:
            return gw  # assume the router is also an NTP server
        return "pool.ntp.org"

    def sync_once(self) -> bool:
        """One sync attempt. Returns True if the RTC was set from NTP.
        Bounded blocking: at most ~timeout seconds (+DNS)."""
        if getattr(self._wifi, "status", "") != "connected as client":
            self._logger.info("Not connected as wifi client - cannot sync time now.")
            return False
        server = self._server()
        dt = self._client.fetch(server, self._ntp_port)
        if dt is None:
            self._logger.warning(f"NTP fetch from {server}:{self._ntp_port} failed.")
            return False
        # machine.RTC().datetime expects (y, m, d, weekday, h, min, s, subsec)
        self._rtc.datetime((dt[0], dt[1], dt[2], dt[3], dt[4], dt[5], dt[6], 0))
        self._last_sync_ticks = _now_ms()
        self._logger.info(f"Time synced from NTP {server}:{self._ntp_port} -> {self.current_time_utc_iso()}")
        return True

    async def run_forever(self) -> None:
        """Resync forever. Never raises; retries automatically after outages."""
        while True:
            try:
                if self.time_status != "accurate":
                    self.sync_once()
                else:
                    # periodic refresh while accurate
                    if self._elapsed_since_sync() >= self._period:
                        self.sync_once()
            except Exception as e:  # shield: this loop must never die  # noqa: BLE001
                self._logger.error(f"Unexpected error in NTP loop: {e}")
            sleep_time = self._period if self.time_status == "accurate" else self._retry_interval
            await asyncio.sleep(sleep_time)

    def _elapsed_since_sync(self) -> int:
        age = self.last_sync_age
        return 10**9 if age is None else int(age)
