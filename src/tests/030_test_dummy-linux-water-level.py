"""Integration test for the dummy water-level process (src/water-level/dummy_linux_water-level.py).

Steps:
1. Start a local redis-server via the shell-script '010_start_test-redis-server.sh'
   (on a free port, listening on all interfaces, no password).
2. Start 'dummy_linux_water-level.py' as a subprocess with command-line arguments
   pointing to that redis-server.
3. For about --duration seconds (default 10s), poll the redis hash 'water-level' and:
   - check that a new value is stored roughly every --wait-time-seconds seconds
     (by comparing the stored 'timestamp' hash-field),
   - verify that every stored 'value' is a valid float.
4. Stop the dummy process and the redis-server and print PASS/FAIL.

Run with the project virtualenv from the repository root, e.g.:
    .venv/bin/python src/tests/040_test_dummy-linux-water-level.py
"""

import argparse
import logging
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

import redis

TESTS_DIR = Path(__file__).resolve().parents[0]
SRC_DIR = TESTS_DIR.parent
DUMMY_SCRIPT = SRC_DIR / "water-level" / "dummy_linux_water-level.py"
START_REDIS_SCRIPT = TESTS_DIR / "010_start_test-redis-server.sh"


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Integration test for dummy_linux_water-level.py: local redis-server (via 010_start_test-redis-server.sh), dummy-process subprocess, periodic value checks in Redis."
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=10.0,
        help="Total test duration in seconds. (Default: 10.0)",
    )
    parser.add_argument(
        "--wait-time-seconds",
        type=float,
        default=1.0,
        help="--wait-time-seconds argument passed to the dummy process: interval in seconds between storing values in Redis. (Default: 1.0)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=0,
        help="Port for the test redis-server. 0 = pick a free port automatically. (Default: 0)",
    )
    parser.add_argument(
        "--redis-key-water-level",
        type=str,
        default="water-level",
        help="Redis key used by the dummy process. (Default: 'water-level')",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        help="Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL). (Default: 'INFO')",
    )
    return parser.parse_args()


ARGS = parse_args()

logging.basicConfig(
    stream=sys.stdout,
    level=ARGS.log_level.upper(),
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

HASHKEY_TIMESTAMP = b"timestamp"
HASHKEY_VALUE = b"value"


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def wait_for_redis(client: redis.Redis, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if client.ping():
                return True
        except (redis.RedisError, OSError):
            pass
        time.sleep(0.2)
    return False


def stop_process_group(proc: subprocess.Popen, sig: int = signal.SIGTERM, timeout: float = 5.0) -> None:
    """Send sig to the whole process group of proc, then wait; SIGKILL as fallback."""
    if proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, sig)
    except (ProcessLookupError, PermissionError):
        proc.terminate()
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            proc.kill()
        proc.wait(timeout=timeout)


def parse_timestamp(ts: str) -> datetime:
    # dummy process stores ISO 8601 with 'Z' suffix, e.g. '2026-11-16T18:53:29.796Z'
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def main() -> int:
    port = ARGS.port if ARGS.port != 0 else find_free_port()
    redis_key = ARGS.redis_key_water_level
    logger.info(f"Test redis-server will listen on 127.0.0.1:{port} (all interfaces), redis key '{redis_key}'")
    logger.info(f"Dummy-process args: --redis-server=127.0.0.1 --redis-port={port} --redis-key-water-level={redis_key} --wait-time-seconds={ARGS.wait_time_seconds}")
    logger.info(f"Test duration: {ARGS.duration}s (expecting a value every {ARGS.wait_time_seconds}s)")

    with tempfile.TemporaryDirectory(prefix="water-level-test-") as tmpdir:
        redis_log_path = Path(tmpdir) / "redis-server.log"
        dummy_log_path = Path(tmpdir) / "dummy-water-level.log"

        # 1. Start local redis-server via the shell script
        with redis_log_path.open("w") as redis_log:
            redis_proc = subprocess.Popen(
                ["bash", str(START_REDIS_SCRIPT), str(port)],
                stdout=redis_log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        logger.info(f"Started '{START_REDIS_SCRIPT.name} {port}' (pid {redis_proc.pid}), log: {redis_log_path}")

        client = redis.Redis(host="127.0.0.1", port=port)
        dummy_proc: subprocess.Popen | None = None
        try:
            if not wait_for_redis(client):
                logger.error(f"redis-server did not answer PING on port {port} within 10s. Log tail:\n{redis_log_path.read_text()[-2000:]}")
                return 2
            logger.info("redis-server is up (PONG)")

            # 2. Start the dummy water-level process
            with dummy_log_path.open("w") as dummy_log:
                dummy_proc = subprocess.Popen(
                    [
                        sys.executable,
                        str(DUMMY_SCRIPT),
                        "--redis-server", "127.0.0.1",
                        "--redis-port", str(port),
                        "--redis-key-water-level", redis_key,
                        "--wait-time-seconds", str(ARGS.wait_time_seconds),
                        "--log-level", ARGS.log_level.upper(),
                    ],
                    stdout=dummy_log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
            logger.info(f"Started '{DUMMY_SCRIPT.name}' (pid {dummy_proc.pid}), log: {dummy_log_path}")

            # 3. Poll redis for ~duration seconds, check timestamps + float values
            observations: list[tuple[datetime, float]] = []
            all_float_ok = True
            last_ts: str | None = None

            deadline = time.monotonic() + ARGS.duration
            while time.monotonic() < deadline:
                data = client.hgetall(redis_key)
                if data:
                    ts_bytes = data.get(HASHKEY_TIMESTAMP)
                    value_bytes = data.get(HASHKEY_VALUE)
                    if isinstance(ts_bytes, bytes) and isinstance(value_bytes, bytes):
                        ts = ts_bytes.decode("utf-8", errors="replace")
                        if ts != last_ts:
                            last_ts = ts
                            try:
                                value = float(value_bytes.decode("utf-8", errors="replace"))
                                float_ok = True
                            except ValueError:
                                value = float("nan")
                                float_ok = False
                            all_float_ok = all_float_ok and float_ok
                            observations.append((parse_timestamp(ts), value))
                            logger.info(f"New value in '{redis_key}' at {ts}: {value} (float-valid: {float_ok})")
                time.sleep(0.2)

            # 4. Analyse the observations
            logger.info(f"Collected {len(observations)} distinct values in {ARGS.duration}s")
            failures: list[str] = []

            min_expected = max(3, int(ARGS.duration / ARGS.wait_time_seconds) // 2)
            if len(observations) < min_expected:
                failures.append(
                    f"too few values: got {len(observations)}, expected at least {min_expected} "
                    f"(one every {ARGS.wait_time_seconds}s within {ARGS.duration}s)"
                )

            if not all_float_ok:
                failures.append("not all stored values are valid floats")

            gaps = [
                (observations[i + 1][0] - observations[i][0]).total_seconds()
                for i in range(len(observations) - 1)
            ]
            if gaps:
                logger.info(f"Gaps between stored values (s): {[round(g, 2) for g in gaps]}")
                bad_gaps = [g for g in gaps if not (0.5 * ARGS.wait_time_seconds <= g <= 2.5 * ARGS.wait_time_seconds)]
                if bad_gaps:
                    failures.append(
                        f"storing period not respected: gaps {bad_gaps} outside "
                        f"[{0.5 * ARGS.wait_time_seconds}, {2.5 * ARGS.wait_time_seconds}]s (wait-time={ARGS.wait_time_seconds}s)"
                    )
            elif len(observations) < 2:
                failures.append("fewer than 2 values - cannot check the storing period")

            if failures:
                for f in failures:
                    logger.error(f"FAIL: {f}")
                logger.error(f"Dummy-process log tail:\n{dummy_log_path.read_text()[-2000:]}")
                return 1
            logger.info("PASS: dummy water-level process stores a valid float value in Redis roughly every "
                        f"{ARGS.wait_time_seconds}s (key '{redis_key}', hashfields 'timestamp'/'value')")
            return 0
        finally:
            if dummy_proc is not None:
                logger.info("Stopping dummy water-level process...")
                stop_process_group(dummy_proc, signal.SIGINT)
            logger.info("Stopping redis-server (via 010 script)...")
            stop_process_group(redis_proc, signal.SIGTERM)


if __name__ == "__main__":
    raise SystemExit(main())
