# Test helper: start ALL subsystems + the web server on the Pico (port 80),
# running forever. Used by the laptop-side test with:
#   mpremote run pico_tests/run_web_server.py   (in background)
# then query http://<pico-ip>/ from the laptop, then stop mpremote.
import sys

if "/pico_tests" not in sys.path:
    sys.path.insert(0, "/pico_tests")

import asyncio

from config import Config
from ntp_time import NetworkTimeService
from redis_client import RedisClientService
from ultrasonic_sensor import UltrasonicSensor
from web_app import WebApp
from wifi import WifiManager

cfg = Config("config.json")
cfg.read_from_file()

wifi = WifiManager(cfg.ap_ssid_prefix, cfg.ap_password,
                   cfg.station_ssid or "", cfg.station_password or "",
                   connect_timeout=15, retry_interval=10, log_level="INFO")
ntp = NetworkTimeService(wifi, log_level="INFO")
redis = RedisClientService(wifi, cfg.redis_server, cfg.redis_port or 6379, log_level="INFO")
sensor = UltrasonicSensor(log_level="INFO")


async def main():
    asyncio.create_task(wifi.run_forever())
    asyncio.create_task(ntp.run_forever())
    asyncio.create_task(redis.run_forever())
    app = WebApp(cfg, wifi, ntp, redis, sensor).get_app()
    print("WEB READY on port 80")
    await app.start_server(host="0.0.0.0", port=80)


asyncio.run(main())
