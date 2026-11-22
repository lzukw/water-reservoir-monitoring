"""Minimal Redis client (RESP2 protocol over TCP) + robust reconnecting service.

Written from scratch so it runs unchanged on MicroPython (Pico) and CPython
(laptop tests). Only a handful of commands are needed: PING, HSET, HGETALL,
plus a generic execute().

Robustness (per project requirement): the service never dies. If the Redis
server is unreachable or dies, publish calls simply return False (no
buffering - only the latest measurement matters) and run_forever() keeps
trying to reconnect, so publishing resumes automatically once Redis is back.
"""

import asyncio
import socket

from logger import Logger
from ping import tcp_ping
from timeutil import now_ms
from wifi import WifiManager

# Budget for the TCP pre-ping in RedisClient.connect (ms).
TCP_PING_TIMEOUT_MS = 1000


class RedisError(Exception):
    pass


class _SockReader:
    """Small buffered reader over a socket, portable between MicroPython and CPython.

    If a deadline (ms) is set, the socket timeout is recomputed before every
    recv so that the *whole* command stays within one time budget - blocking
    time never accumulates across multiple recv calls."""

    def __init__(self, sock, deadline_ms: int|None = None):
        self._sock = sock
        self._buf = b""
        self._deadline_ms = deadline_ms

    def _remaining_s(self) -> float | None:
        """Return remaining seconds until deadline, or None if no deadline set.
        Raises RedisError if deadline already exceeded."""
        if self._deadline_ms is None:
            return None
        remaining = (self._deadline_ms - now_ms()) / 1000.0
        if remaining <= 0:
            raise RedisError("timeout: deadline exceeded")
        return max(remaining, 0.01)

    def _fill(self):
        """Fill the internal buffer with more data from the socket. Raises
        RedisError on timeout or connection closed."""
        remaining = self._remaining_s()
        if remaining is not None:
            self._sock.settimeout(remaining)
        chunk = self._sock.recv(256)
        if not chunk:
            raise RedisError("connection closed by peer")
        self._buf += chunk

    def read_line(self) -> bytes:
        """Read a line from the socket, blocking until a complete line is received.
        On Timeout or connection closed, raises RedisError. The returned line does not
        include the trailing CRLF."""
        while True:
            i = self._buf.find(b"\r\n")
            if i >= 0:
                line = self._buf[:i]
                self._buf = self._buf[i + 2:]
                return line
            self._fill()

    def read_exact(self, n: int) -> bytes:
        """Read exactly n bytes from the socket, blocking until all bytes are received.
        On Timeout or connection closed, raises RedisError."""
        while len(self._buf) < n:
            self._fill()
        data = self._buf[:n]
        self._buf = self._buf[n:]
        return data


class RedisClient:
    """Synchronous, single-connection Redis client speaking RESP2."""

    def __init__(self, host: str, port: int = 6379, timeout: float = 3.0,
                 password: str = ""):
        self._host = host
        self._port = int(port)
        self._timeout = timeout
        self._password = password or ""
        self._sock = None
        self._reader = None

    @property
    def connected(self) -> bool:
        return self._sock is not None

    @property
    def host(self) -> str:
        return self._host

    @property
    def port(self) -> int:
        return self._port

    def connect(self) -> bool:
        """Open the TCP connection and verify with AUTH/PING. Single attempt.
        First the server is TCP-pinged on its redis port (ping.py tcp_ping,
        bounded to TCP_PING_TIMEOUT_MS ~1 s): if that fails, the attempt is
        aborted immediately - otherwise a dead server would burn the whole
        TCP connect timeout and stall the caller. Returns True on success;
        on any failure the socket is closed and False is returned (never raises).
        Note: getaddrinfo() (DNS) is the only part not covered by the budget."""
        self.close()
        if not tcp_ping(self._host, self._port, TCP_PING_TIMEOUT_MS):
            return False  # server down: quit early, run_forever retries later
        deadline = now_ms() + int(self._timeout * 1000)
        try:
            addr = socket.getaddrinfo(self._host, self._port)[0][-1]
            sock = socket.socket()
            remaining = (deadline - now_ms()) / 1000.0
            if remaining <= 0:
                raise RedisError("timeout during DNS resolution")
            sock.settimeout(max(remaining, 0.01))
            sock.connect(addr)
            self._sock = sock
            self._reader = _SockReader(sock, deadline_ms=deadline)
            if self._verify_session(deadline):
                return True
            self.close()
            return False
        except (OSError, RedisError):
            self.close()
            return False

    def _verify_session(self, deadline) -> bool:
        """AUTH (if a password is set) + PING on the current connection."""
        if (
            self._password and
            self._execute_with_deadline("AUTH", self._password, deadline=deadline) != "OK"
        ):
            return False
        return self._execute_with_deadline("PING", deadline=deadline) == "PONG"

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
        self._sock = None
        self._reader = None

    # ------------------------------------------------------------ protocol
    def execute(self, *args):
        """Send a command and return the parsed reply. The whole call is
        bounded by one `timeout` budget (deadline shared across send/recv).
        Raises RedisError on connection problems or server error replies."""
        deadline = now_ms() + int(self._timeout * 1000)
        return self._execute_with_deadline(*args, deadline=deadline)

    def _execute_with_deadline(self, *args, deadline=None):
        if self._sock is None or self._reader is None:
            raise RedisError("not connected")
        self._reader._deadline_ms = deadline
        parts = [(f"*{len(args)}\r\n").encode()]
        for arg in args:
            if isinstance(arg, str):
                arg = arg.encode()
            elif isinstance(arg, (int, float)) or not isinstance(arg, (bytes, bytearray)):
                arg = str(arg).encode()
            parts.append((f"${len(arg)}\r\n").encode())
            parts.append(bytes(arg))
            parts.append(b"\r\n")
        try:
            if deadline is not None:
                remaining = (deadline - now_ms()) / 1000.0
                if remaining <= 0:
                    raise RedisError("timeout: deadline exceeded")
                self._sock.settimeout(max(remaining, 0.01))
            self._sock.sendall(b"".join(parts))
            return self._read_reply()
        except OSError as e:
            self.close()
            raise RedisError(f"socket error: {e}")

    def _read_reply(self):
        line = self._reader.read_line()
        if not line:
            raise RedisError("empty reply")
        prefix = line[:1]
        payload = line[1:]
        if prefix == b"+":
            return payload.decode()
        if prefix == b"-":
            raise RedisError(payload.decode())
        if prefix == b":":
            return int(payload.decode())
        if prefix == b"$":
            n = int(payload.decode())
            if n == -1:
                return None
            data = self._reader.read_exact(n)
            self._reader.read_exact(2)  # trailing CRLF
            return data
        if prefix == b"*":
            n = int(payload.decode())
            if n == -1:
                return None
            return [self._read_reply() for _ in range(n)]
        raise RedisError(f"unexpected reply: {line}")

    # ------------------------------------------------------------ commands
    def ping(self) -> bool:
        try:
            return self.execute("PING") == "PONG"
        except RedisError:
            return False

    def hset(self, key: str, mapping: dict) -> bool:
        """HSET key field value [field value ...]. Returns True on success."""
        args = ["HSET", key]
        for field, value in mapping.items():
            args.append(field)
            args.append(value)
        self.execute(*args)
        return True

    def hgetall(self, key: str) -> dict:
        """HGETALL key -> {field: value} as str dict (empty dict if key missing)."""
        reply = self.execute("HGETALL", key)
        result = {}
        if reply:
            for i in range(0, len(reply), 2):
                result[reply[i].decode()] = reply[i + 1].decode()
        return result


class RedisClientService:
    """Robust wrapper around RedisClient: keeps the connection alive forever.

    - run_forever() reconnects automatically when Redis is down and verifies
      the connection periodically with PING.
    - publish_hash() writes the *latest* value only; if Redis is currently
      down it returns False and nothing is queued (by design).
    - If `wifi` is given (duck-typed, needs .status) and the station is not
      "connected as client" (e.g. router down, AP mode), connect attempts are
      SKIPPED: without a router there is no DNS server and getaddrinfo()
      would block the whole event loop for seconds.
    - RedisClient.connect() pre-pings the server with a TCP connect to the
      redis port (ping.py tcp_ping, ~1 s max) and aborts the attempt if that
      fails, so a dead redis server costs at most ~1 s per retry instead of
      the full TCP connect timeout.
    """

    def __init__(
            self, 
            wifi: WifiManager, 
            host: str, 
            port: int | str = 6379, 
            retry_interval: float = 5.0, 
            check_interval: float = 30.0,
            timeout: float = 3.0, 
            password: str = "",
            log_level: str = "INFO"
            ):
        self._host = host
        self._port = int(port)
        self._timeout = timeout
        self._client = RedisClient(host, port, timeout=timeout, password=password)
        self._logger = Logger("Redis", log_level)
        self._retry_interval = retry_interval
        self._check_interval = check_interval
        self._wifi = wifi
        self._status = "disconnected"  # "disconnected" | "connecting" | "connected"
        self.last_error = ""
        self.publish_count = 0

    @property
    def status(self) -> str:
        """Cached connection status (no I/O, safe to call from the web loop)."""
        return self._status

    @property
    def client(self) -> RedisClient:
        return self._client

    def connect_once(self) -> bool:
        """Single connect attempt (blocking up to the socket timeout)."""
        self._status = "connecting"
        self._logger.debug(f"Connecting to Redis at {self._host}:{self._port}...")
        ok = self._client.connect()
        if ok:
            self._status = "connected"
            self.last_error = ""
            self._logger.info(f"Connected to Redis at {self._host}:{self._port}")
        else:
            self._status = "disconnected"
            self._logger.warning(f"Connect to Redis at {self._host}:{self._port} failed")
            self.last_error = f"connect to {self._host}:{self._port} failed"
        return ok

    def publish_hash(self, key: str, mapping: dict) -> bool:
        """Publish the latest measurement (HSET). Returns False if Redis is
        not connected or the write failed; never raises. Nothing is buffered."""
        if self._status != "connected" or not self._client.connected:
            self._logger.debug(f"publish_hash skipped: Redis not connected (status={self._status})")
            return False
        try:
            self._logger.debug(f"Publishing to Redis: HSET {key} {mapping}")
            self._client.hset(key, mapping)
            self.publish_count += 1
            self.last_error = ""
            self._logger.debug(f"Finished publishing to Redis: HSET {key} {mapping}")
            return True
        except RedisError as e:
            self._status = "disconnected"
            self.last_error = str(e)
            self._logger.warning(f"HSET failed ({e}); will reconnect in background")
            return False

    async def run_forever(self) -> None:
        """Keep the Redis connection alive forever. Never raises."""
        while True:
            try:
                if self._wifi.status != "connected as client":
                    # router down / AP mode: no DNS server -> a connect attempt
                    # would block the event loop for seconds in getaddrinfo()
                    self._status = "disconnected"
                    self.last_error = "wifi not connected as client"
                    self._logger.debug("wifi not connected as client - skipping redis connect")
                elif self._status != "connected":
                    self._logger.debug("Redis not connected - attempting connect_once()")
                    self.connect_once()
                else:
                    # liveness check: PING; on failure mark disconnected
                    self._logger.debug("Redis connected - performing PING liveness check")
                    if not self._client.ping():
                        self._status = "disconnected"
                        self.last_error = "PING failed"
                        self._logger.warning(f"Lost connection to Redis at {self._host}:{self._port}")
            except Exception as e:  # shield: this loop must never die  # noqa: BLE001
                self._status = "disconnected"
                self.last_error = repr(e)
                self._logger.error(f"Unexpected error in redis loop: {e}")
            sleep_time = self._check_interval if self._status == "connected" else self._retry_interval
            await asyncio.sleep(sleep_time)
