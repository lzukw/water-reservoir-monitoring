import asyncio
import math

from config import Config
from logger import Logger
from ntp_time import NetworkTimeService
from redis_client import RedisClientService
from wifi import WifiManager

GLOBAL_LOG_LEVEL = "INFO"
PUBLISH_INTERVAL_S = 2.0
PRINT_INTERVAL_S = 0.5

config = Config("config.json", log_level=GLOBAL_LOG_LEVEL)
config.read_from_file()
logger = Logger("MAIN", level=GLOBAL_LOG_LEVEL)

wifi = WifiManager(ap_ssid_prefix=config.ap_ssid_prefix or "RPi-Pico-Water-Level",
                    ap_password=config.ap_password or "1234567890",
                    station_ssid=config.station_ssid or "",
                    station_password=config.station_password or "",
                    connect_timeout=20, retry_interval=10,
                    log_level=GLOBAL_LOG_LEVEL)

ntp_service = NetworkTimeService(wifi, period=7, valid_time_threshold=21, retry_interval=4,
                                 log_level=GLOBAL_LOG_LEVEL)

redis = RedisClientService(wifi, config.redis_server or "", config.redis_port or 6379,
                           retry_interval=5, check_interval=30, log_level="DEBUG",
                           password=config.get("redis_password", "") or "")

print("Manual test 030 (redis publisher):\n"
      "- wifi, ntp and redis services run as background tasks\n"
      f"- every {PUBLISH_INTERVAL_S} s a simulated water level is published (HSET) to redis\n"
      f"- every {PRINT_INTERVAL_S} s one status line: redis status, publish count, last error\n"
      "- stop/start the redis server to watch the reconnect behaviour\n\n"
      "Suggested test-sequence:\n"
      "- Stop remote redis-server and wifi-connection"
      "- Start wifi-connection"
      "- Later start also redis-server and watch the reconnect behaviour\n"
      "- Stop redis-server and after some time start it again. Watch the\n"
      "  reconnect behaviour\n"
      "- Stop wifi-connection and after some time start it again. Watch the\n"
      "  reconnect behaviour\n"
      "- Stop the test with Ctrl-C\n" \
      "\n"
      "During the test, redis-cli can be used on another device to watch the\n"
      "published values.\n\n"
      "The print-loop shall continue to print status lines every 0.5 seconds.\n" \
      "If the print-loop stops, it indicates that background tasks are blocing,\n"
      "which is a bug.\n")
print()


async def publish_loop() -> None:
    """Publish a simulated, timestamped water level every PUBLISH_INTERVAL_S."""
    i = 0
    while True:
        level = 2.5 + 0.5 * math.sin(i * 0.3)  # simulated water level in m
        redis.publish_hash(config.redis_key, {
            config.redis_hashkey_timestamp: ntp_service.current_time_utc_iso(),
            config.redis_hashkey_value: f"{level:.2f}",
        })
        i += 1
        await asyncio.sleep(PUBLISH_INTERVAL_S)


async def main() -> None:
    tasks = [
        asyncio.create_task(wifi.run_forever()),
        asyncio.create_task(ntp_service.run_forever()),
        asyncio.create_task(redis.run_forever()),
        asyncio.create_task(publish_loop()),
    ]
    try:
        while True:
            print(f"{ntp_service.current_time_utc_iso()}: redis={redis.status} published={redis.publish_count} "
                  f"last_error='{redis.last_error}'")
            await asyncio.sleep(PRINT_INTERVAL_S)
    except KeyboardInterrupt:
        print("\nKeyboardInterrupt - stopping tasks")
    finally:
        for t in tasks:
            t.cancel()


asyncio.run(main())
