# On-board test for Step 2 (own Redis client + service). Run on the Pico with:
#   mpremote run pico_tests/test_step2_board.py
# Expects the laptop to be reachable (e.g. joined the Pico's AP) with a
# redis-server running on it. The laptop IP is read from /test_redis_host.txt
# (written by the test orchestration), default 192.168.4.2.
import sys

if "/pico_tests" not in sys.path:
    sys.path.insert(0, "/pico_tests")

from config import Config
from redis_client import RedisClient, RedisError, RedisClientService
from tinytest import run_tests


def _host():
    try:
        with open("/test_redis_host.txt") as f:
            return f.read().strip()
    except OSError:
        return "192.168.4.2"


def _password():
    c = Config("config.json")
    c.read_from_file()
    return c.get("redis_password", "") or ""


def test_connect_ping_hset_hgetall():
    c = RedisClient(_host(), 6379, timeout=5, password=_password())
    assert c.connect() is True
    assert c.connected is True
    assert c.ping() is True
    assert c.hset("water-level-test", {"timestamp": "2026-11-21T15:00:00.000Z", "value": "1.23"})
    assert c.hgetall("water-level-test") == {"timestamp": "2026-11-21T15:00:00.000Z", "value": "1.23"}
    c.close()
    assert c.connected is False


def test_refused_port_fails_cleanly():
    c = RedisClient(_host(), 6399, timeout=3)  # nothing listens on 6399
    assert c.connect() is False
    assert c.connected is False
    try:
        c.execute("PING")
        raise AssertionError("execute should have raised RedisError")
    except RedisError:
        pass


def test_service_publish_latest_only():
    svc = RedisClientService(_host(), 6379, timeout=5, password=_password())
    assert svc.connect_once() is True
    assert svc.status == "connected"
    assert svc.publish_hash("water-level-test", {"value": "0.1"}) is True
    assert svc.publish_hash("water-level-test", {"timestamp": "t2", "value": "9.9"}) is True
    # only the latest value is stored (no buffering of skipped values)
    assert svc.client.hgetall("water-level-test") == {"timestamp": "t2", "value": "9.9"}


def test_service_down_server_is_skipped():
    svc = RedisClientService(_host(), 6399, retry_interval=1, check_interval=1, timeout=3)
    assert svc.connect_once() is False
    assert svc.status == "disconnected"
    assert svc.publish_hash("water-level-test", {"value": "x"}) is False
    assert svc.last_error != ""


if __name__ == "__main__":
    run_tests(globals())
