""" Dummy water-level observing process for testing purposes.
This process runs on a linux-machine (Rapsberry Pi) and provides every
second a dummy water-level value of 1.23, and stores it in the
Redis-Database."""

import argparse
import logging
import sys
import time
from datetime import UTC, datetime

import redis


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description="Create dummy water-level values and store them in Redis.")
    parser.add_argument("--wait-time-seconds", type=float, default=1.0, help="Delay between measurements in seconds. (Default: 1.0")
    parser.add_argument("--redis-server", type=str, default="main-raspberry-pi", help="DNS-Name or IP of Redis-Server. (Default: 'main-raspberry-pi')")
    parser.add_argument("--redis-port", type=int, default=6379, help="Port-Number of Redis-Server. (Default: 6379)")
    parser.add_argument("--redis-key-water-level", type=str, default="water-level", help="Redis key for water level values. (Default: 'water-level')")
    parser.add_argument("--redis-hashkey-timestamp", type=str, default="timestamp", help="Hash(Dicctionary)Key for timestamps. (Default: 'timestamp')")
    parser.add_argument("--redis-hashkey-value", type=str, default="value", help="Hash(Dicctionary)Key for water level value. (Default: 'value')")
    parser.add_argument("--log-level", type=str, default="INFO", help="Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL). (Default: 'INFO')")
    return parser.parse_args()


ARGS = parse_args()

REDIS_SERVER: str = ARGS.redis_server
REDIS_PORT: int = ARGS.redis_port
REDIS_KEY_WATER_LEVEL: str = ARGS.redis_key_water_level
REDIS_HASHKEY_TIMESTAMP: str = ARGS.redis_hashkey_timestamp
REDIS_HASHKEY_VALUE: str = ARGS.redis_hashkey_value
WAIT_TIME_SECONDS: float = ARGS.wait_time_seconds
LOG_LEVEL: str = ARGS.log_level.upper()

logging.basicConfig(
    stream=sys.stdout,
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

logger.info(f"Redis-Server and -port: {REDIS_SERVER}:{REDIS_PORT}")
logger.info(f"Water-Level-Redis-Key: '{REDIS_KEY_WATER_LEVEL}' with hash keys '{REDIS_HASHKEY_TIMESTAMP}' and '{REDIS_HASHKEY_VALUE}'")


def measure_water_level() -> float:
    """Dummy function to measure water level. Returns a constant value."""
    return 1.23

def main() -> int:
    logger.info("Bringing up connection to Redis server...")
    redis_client = redis.Redis(host=REDIS_SERVER, port=REDIS_PORT)
    redis_client.ping()
    logger.info(f"Connected to Redis server at {REDIS_SERVER}:{REDIS_PORT}")

    logger.info("Start Water-Level-Read loop for key '%s'", REDIS_KEY_WATER_LEVEL)

    try:
        while True:
            water_level = measure_water_level()
            try:
                redis_client.hset(
                    REDIS_KEY_WATER_LEVEL,
                    mapping={
                        REDIS_HASHKEY_TIMESTAMP: datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                        REDIS_HASHKEY_VALUE: water_level
                    }
                )
                
                logger.debug(f"Stored water level: {water_level}")
            except redis.RedisError as e:
                logger.error(f"Error storing water level in Redis: {e}")
                time.sleep(5)
                continue

            time.sleep(WAIT_TIME_SECONDS)

    except KeyboardInterrupt:
        logger.info("Water-Level-Read loop interrupted by user.")
        return 0

if __name__ == "__main__":
    raise SystemExit(main())