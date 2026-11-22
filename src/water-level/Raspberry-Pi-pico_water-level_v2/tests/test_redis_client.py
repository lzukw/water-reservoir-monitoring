import asyncio
import socket
import subprocess
import threading
import time

import pytest

import redis_client as redis_module
from redis_client import RedisClient, RedisError, RedisClientService

REDIS_PORT = 6399  # dedicated test instance, does not touch the main redis on 6379


@pytest.fixture(autouse=True)
def _fake_host_ping(monkeypatch):
    """RedisClient.connect() pre-pings host:port via ping.py tcp_ping; fake a
    successful ping on the CPython host (tests that need a failing ping
    override this again)."""
    monkeypatch.setattr(redis_module, "tcp_ping", lambda host, port, timeout_ms: True)


def _wait_port_open(port, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            s = socket.create_connection(("127.0.0.1", port), timeout=0.5)
            s.close()
            return True
        except OSError:
            time.sleep(0.1)
    return False


def _wait_port_closed(port, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            s = socket.create_connection(("127.0.0.1", port), timeout=0.5)
            s.close()
            time.sleep(0.1)
        except OSError:
            return True
    return False


class FakeWifi:
    """Duck-typed stand-in for WifiManager: the redis service only needs .status."""

    def __init__(self, status="connected as client"):
        self.status = status


class RedisServerProc:
    """A throwaway redis-server subprocess for tests (launch a local redis-server)."""

    def __init__(self, port=REDIS_PORT, password=""):
        self.port = port
        self.password = password
        self.proc = None

    def start(self):
        cmd = ["redis-server", "--port", str(self.port), "--bind", "127.0.0.1",
               "--save", "", "--appendonly", "no", "--protected-mode", "no",
               "--daemonize", "no", "--logfile", ""]
        if self.password:
            cmd += ["--requirepass", self.password]
        self.proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        assert _wait_port_open(self.port), "test redis-server did not start"
        return self

    def stop(self):
        if self.proc is not None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=5)
            self.proc = None
        assert _wait_port_closed(self.port), "test redis-server did not stop"

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()


@pytest.fixture
def redis():
    with RedisServerProc() as r:
        yield r


# ------------------------------------------------------------- RedisClient
def test_connect_ping_hset_hgetall(redis):
    c = RedisClient("127.0.0.1", redis.port)
    assert c.connected is False
    assert c.connect() is True
    assert c.connected is True
    assert c.ping() is True
    assert c.hset("water-level", {"timestamp": "2026-11-21T15:00:00.000Z", "value": "1.23"}) is True
    data = c.hgetall("water-level")
    assert data == {"timestamp": "2026-11-21T15:00:00.000Z", "value": "1.23"}
    c.close()
    assert c.connected is False


def test_connect_refused_when_server_down():
    RedisServerProc().stop()  # make sure nothing listens on the test port
    c = RedisClient("127.0.0.1", REDIS_PORT, timeout=1.0)
    assert c.connect() is False
    assert c.connected is False
    with pytest.raises(RedisError):
        c.execute("PING")


def test_server_error_reply_raises_but_connection_survives(redis):
    c = RedisClient("127.0.0.1", redis.port)
    c.connect()
    with pytest.raises(RedisError):
        c.execute("NOSUCHCOMMAND")
    # an error reply (-ERR ...) must not kill the connection
    assert c.ping() is True
    c.close()


def test_hgetall_missing_key_returns_empty_dict(redis):
    c = RedisClient("127.0.0.1", redis.port)
    c.connect()
    assert c.hgetall("no-such-key") == {}
    c.close()


def test_hset_overwrites_latest_value(redis):
    c = RedisClient("127.0.0.1", redis.port)
    c.connect()
    c.hset("k", {"value": "1.0"})
    c.hset("k", {"value": "2.0"})  # only the latest value matters
    assert c.hgetall("k") == {"value": "2.0"}
    c.close()


def test_reconnect_after_server_restart():
    r = RedisServerProc().start()
    c = RedisClient("127.0.0.1", r.port)
    assert c.connect() is True
    c.hset("k", {"value": "before-restart"})
    r.stop()
    # after the kill, commands must fail cleanly (no exception escaping as OSError)
    assert c.ping() is False
    with pytest.raises(RedisError):
        c.execute("PING")
    assert c.connected is False
    # server comes back -> client can reconnect
    r.start()
    assert c.connect() is True
    c.hset("k", {"value": "after-restart"})
    assert c.hgetall("k") == {"value": "after-restart"}
    c.close()
    r.stop()


def test_numeric_and_float_args_are_encoded():
    r = RedisServerProc().start()
    c = RedisClient("127.0.0.1", r.port)
    c.connect()
    # INCR returns integer reply (':' type), SET with int arg must work
    assert c.execute("SET", "n", 42) == "OK"
    assert c.execute("INCR", "n") == 43
    assert c.execute("GET", "n") == b"43"
    c.close()
    r.stop()


# ------------------------------------------------------------ RedisClientService
def test_service_publish_when_down_is_skipped_and_latest_is_published_after_recovery():
    r = RedisServerProc()
    svc = RedisClientService(FakeWifi(), "127.0.0.1", REDIS_PORT, log_level="ERROR",
                       retry_interval=0.2, check_interval=0.3)
    # server down: connect fails, publish returns False (nothing buffered)
    assert svc.connect_once() is False
    assert svc.status == "disconnected"
    assert svc.publish_hash("water-level", {"value": "0.1"}) is False
    # server comes up
    r.start()
    assert svc.connect_once() is True
    assert svc.status == "connected"
    # only the *latest* value is published - earlier skipped ones are gone by design
    assert svc.publish_hash("water-level", {"timestamp": "t1", "value": "0.2"}) is True
    assert svc.publish_hash("water-level", {"timestamp": "t2", "value": "9.9"}) is True
    data = svc.client.hgetall("water-level")
    assert data == {"timestamp": "t2", "value": "9.9"}
    # server dies again -> publish fails cleanly, status flips to disconnected
    r.stop()
    assert svc.publish_hash("water-level", {"value": "x"}) is False
    assert svc.status == "disconnected"
    assert svc.last_error != ""
    r.start()
    assert svc.connect_once() is True
    assert svc.publish_hash("water-level", {"timestamp": "t3", "value": "3.3"}) is True
    assert svc.client.hgetall("water-level") == {"timestamp": "t3", "value": "3.3"}
    r.stop()


def test_service_run_forever_recovers_automatically():
    async def scenario():
        r = RedisServerProc().start()
        svc = RedisClientService(FakeWifi(), "127.0.0.1", REDIS_PORT, log_level="ERROR",
                           retry_interval=0.2, check_interval=0.3)
        task = asyncio.create_task(svc.run_forever())
        try:
            for _ in range(50):
                if svc.status == "connected":
                    break
                await asyncio.sleep(0.1)
            assert svc.status == "connected"
            assert svc.publish_hash("k", {"v": "1"}) is True

            r.stop()  # simulate Redis going down
            for _ in range(50):
                if svc.status == "disconnected":
                    break
                await asyncio.sleep(0.1)
            assert svc.status == "disconnected"
            assert svc.publish_hash("k", {"v": "2"}) is False

            r.start()  # simulate Redis coming back
            for _ in range(50):
                if svc.status == "connected":
                    break
                await asyncio.sleep(0.1)
            assert svc.status == "connected"
            assert svc.publish_hash("k", {"v": "3"}) is True
            assert svc.client.hgetall("k") == {"v": "3"}
            assert svc.publish_count >= 2
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            r.stop()

    asyncio.run(scenario())


def test_auth_password():
    r = RedisServerProc(password="secret123").start()
    try:
        assert RedisClient("127.0.0.1", r.port, timeout=2).connect() is False  # no password
        assert RedisClient("127.0.0.1", r.port, timeout=2, password="wrong").connect() is False
        c = RedisClient("127.0.0.1", r.port, timeout=2, password="secret123")
        assert c.connect() is True
        assert c.ping() is True
        c.hset("k", {"v": "1"})
        assert c.hgetall("k") == {"v": "1"}
        c.close()
    finally:
        r.stop()


def test_blocking_time_does_not_accumulate_hung_server():
    """Server accepts TCP but never replies: connect()+PING must be bounded by
    ONE timeout budget, not timeout-per-recv (which would accumulate)."""
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    stop = {"flag": False}

    def hang():
        while not stop["flag"]:
            try:
                srv.settimeout(0.2)
                c, _ = srv.accept()
                time.sleep(5)  # accepted, but never sends a reply
                c.close()
            except socket.timeout:
                pass
            except OSError:
                break

    th = threading.Thread(target=hang, daemon=True)
    th.start()
    try:
        t0 = time.monotonic()
        c = RedisClient("127.0.0.1", port, timeout=1.0)
        ok = c.connect()  # connect succeeds fast, PING read must hit the deadline
        elapsed = time.monotonic() - t0
        assert ok is False
        assert elapsed >= 0.8, f"returned too early ({elapsed:.2f}s)?"
        assert elapsed < 2.0, f"connect() blocked {elapsed:.2f}s - blocking accumulated!"
    finally:
        stop["flag"] = True
        srv.close()
        th.join(timeout=2)


def test_blocking_time_bounded_for_unroutable_host():
    """Non-routable address: connect() must stay within ~one timeout budget."""
    t0 = time.monotonic()
    c = RedisClient("10.255.255.1", 6379, timeout=1.0)  # typically DROPped
    ok = c.connect()
    elapsed = time.monotonic() - t0
    assert ok is False
    assert elapsed < 2.0, f"connect() blocked {elapsed:.2f}s - blocking accumulated!"


def test_service_run_forever_survives_unreachable_host():
    """Point at a port where nothing listens: loop must keep running, not die."""
    async def scenario():
        RedisServerProc().stop()
        svc = RedisClientService(FakeWifi(), "127.0.0.1", REDIS_PORT, log_level="ERROR",
                           retry_interval=0.2, check_interval=0.3, timeout=0.5)
        task = asyncio.create_task(svc.run_forever())
        await asyncio.sleep(1.0)
        assert not task.done()          # loop is alive
        assert svc.status == "disconnected"
        assert svc.publish_hash("k", {"v": "1"}) is False
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(scenario())


def test_service_skips_connect_while_wifi_not_sta(monkeypatch):
    """AP mode (router down): no DNS server exists, so a connect attempt would
    block the event loop for seconds inside getaddrinfo(). While wifi is not
    'connected as client' the service must NOT attempt to connect at all."""
    svc = RedisClientService(FakeWifi("connected as AP"), "host-that-needs-dns", REDIS_PORT,
                       log_level="ERROR", retry_interval=0.05)
    calls = []
    monkeypatch.setattr(svc, "connect_once", lambda: calls.append(1) or False)

    async def scenario():
        task = asyncio.create_task(svc.run_forever())
        await asyncio.sleep(0.3)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(scenario())
    assert calls == []                       # no connect attempt was made
    assert svc.status == "disconnected"
    assert svc.last_error == "wifi not connected as client"


def test_service_connects_normally_when_wifi_is_sta():
    """With wifi 'connected as client' the guard is inactive and connecting
    works as before."""
    r = RedisServerProc().start()
    try:
        svc = RedisClientService(FakeWifi("connected as client"), "127.0.0.1", r.port,
                           log_level="ERROR")
        assert svc.connect_once() is True
        assert svc.status == "connected"
        assert svc.publish_hash("k", {"v": "1"}) is True
    finally:
        r.stop()


def test_connect_aborts_fast_when_host_does_not_ping(monkeypatch):
    """If the TCP pre-ping to host:port fails, connect() must abort immediately
    instead of burning the TCP connect timeout - this keeps the loop live."""
    monkeypatch.setattr(redis_module, "tcp_ping", lambda host, port, timeout_ms: False)
    svc = RedisClientService(FakeWifi(), "10.255.255.1", REDIS_PORT, log_level="ERROR",
                       timeout=5.0)
    t0 = time.monotonic()
    assert svc.connect_once() is False
    assert time.monotonic() - t0 < 2.0  # returned fast, no 5 s TCP timeout
    assert svc.status == "disconnected"
    assert "failed" in svc.last_error


def test_connect_proceeds_when_host_pings():
    """Host answers the pre-ping -> normal TCP connect + AUTH/PING happens."""
    r = RedisServerProc().start()
    try:
        svc = RedisClientService(FakeWifi(), "127.0.0.1", r.port, log_level="ERROR", timeout=2.0)
        assert svc.connect_once() is True  # ping is faked True by the autouse fixture
        assert svc.status == "connected"
        assert svc.publish_hash("k", {"v": "1"}) is True
    finally:
        r.stop()
