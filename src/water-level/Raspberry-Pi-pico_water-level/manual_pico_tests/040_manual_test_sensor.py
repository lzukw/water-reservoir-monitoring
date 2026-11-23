import asyncio

from config import Config
from logger import Logger
from ntp_time import NetworkTimeService
from redis_client import RedisClientService
from ultrasonic_sensor import UltrasonicSensor
from wifi import WifiManager

GLOBAL_LOG_LEVEL = "INFO"
SENSOR_LOG_LEVEL = "DEBUG"  # only the sensor logs at DEBUG (shows every single sample)
MEASUREMENT_INTERVAL_S = 2.0
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
                           retry_interval=5, check_interval=30, log_level=GLOBAL_LOG_LEVEL,
                           password=config.get("redis_password", "") or "")

sensor = UltrasonicSensor(config=config, ntp=ntp_service, redis=redis,
                          interval_s=MEASUREMENT_INTERVAL_S, log_level=SENSOR_LOG_LEVEL)

print("Manual test 040 (ultrasonic sensor):\n"
      "- wifi, ntp, redis and the sensor run as background tasks\n"
      f"- every {MEASUREMENT_INTERVAL_S} s the sensor measures the distance (GPIO28 TRIG /\n"
      "  GPIO27 ECHO) and publishes the water level (HSET) to redis\n"
      "- the sensor logs at DEBUG, so every single sample is printed\n"
      f"- every {PRINT_INTERVAL_S} s one status line: ntp status/time, redis status, publish\n"
      "  count, last error, measured distance and water level\n"
      f"- water level = distance_to_bottom ({config.distance_to_bottom} m from config.json)\n"
      "  minus measured distance\n\n"
      "Suggested test-sequence:\n"
      "- Hold a flat object (board, hand) at a known distance in front of the sensor\n"
      "  and check the printed distance / water level against the real distance\n"
      "- Move the object closer and farther away; the printed values must follow\n"
      "- Remove the object completely: all shots time out -> distance 'invalid',\n"
      "  water level 'unknown', no redis publish\n"
      "- Put the object very close (< ~10 cm): the echo is filtered as mount echo\n"
      "  (min pulse) -> also 'invalid'\n"
      "- Stop the remote redis-server and watch the 'skip publish' / 'publish failed'\n"
      "  log lines; start it again and watch the reconnect behaviour\n"
      "- Stop the wifi router and watch ntp status degrade and redis skip publishing;\n"
      "  start the router again and watch both recover\n"
      "- On another device watch the published values with redis-cli, e.g.:\n"
      f"  redis-cli -h {config.redis_server or '<redis-server>'} HGETALL {config.redis_key or '<redis_key>'}\n"
      "- Stop the test with Ctrl-C\n"
      "\n"
      "The print-loop shall continue to print status lines every 0.5 seconds.\n"
      "If the print-loop stops, it indicates that background tasks are blocking,\n"
      "which is a bug.\n")
print()


async def main() -> None:
    tasks = [
        asyncio.create_task(wifi.run_forever()),
        asyncio.create_task(ntp_service.run_forever()),
        asyncio.create_task(redis.run_forever()),
        asyncio.create_task(sensor.run_forever()),
    ]
    try:
        while True:
            distance = sensor.last_distance
            dtb = config.distance_to_bottom
            level = (dtb - distance) if (distance is not None and dtb is not None) else None
            print(f"{ntp_service.current_time_utc_iso()}: ntp={ntp_service.time_status} "
                  f"redis={redis.status} published={redis.publish_count} "
                  f"last_error='{redis.last_error}' "
                  f"distance={f'{distance:.2f} m' if distance is not None else 'invalid'} "
                  f"water_level={f'{level:.2f} m' if level is not None else 'unknown'}")
            await asyncio.sleep(PRINT_INTERVAL_S)
    except KeyboardInterrupt:
        print("\nKeyboardInterrupt - stopping tasks")
    finally:
        for t in tasks:
            t.cancel()


asyncio.run(main())
