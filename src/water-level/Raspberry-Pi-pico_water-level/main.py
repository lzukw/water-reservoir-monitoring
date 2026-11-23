"""Main entry point of the water-level Pico (runs after boot.py).

Starts all subsystems as parallel asyncio tasks:
  - wifi:      station first, access-point fallback, auto-recovery
  - ntp:       RTC sync from the router/internet, auto-resync after outages
  - redis:     reconnecting publisher (latest value only, no buffering)
  - sensor:    ultrasonic measurement loop, publishes water level to redis
  - web:       configuration + status website on port 80

Robustness: every loop shields its own exceptions and never dies. A watchdog
resets the device only if a core task unexpectedly ends (or after a config
change was applied via the web form). Blocking I/O (redis, ntp, sensor) is
bounded by deadlines, so the event loop is never blocked for long.
"""

import asyncio
import gc
import time

import machine
import network

from config import Config
from logger import Logger
from ntp_time import NetworkTimeService
from redis_client import RedisClientService
from timeutil import elapsed_ms, now_ms
from ultrasonic_sensor import UltrasonicSensor
from web_app import WebApp
from wifi import WifiManager

GLOBAL_LOG_LEVEL = "INFO"  # DEBUG, INFO, WARNING, ERROR, CRITICAL

logger = Logger("MAIN", level=GLOBAL_LOG_LEVEL)

WIFI_WAIT_TIMEOUT_S = 60
WATCHDOG_INTERVAL_S = 5

DEFUALT_MEASUREMENT_INTERVAL_S = 5  # seconds


async def main():
    config = Config("config.json", log_level=GLOBAL_LOG_LEVEL)
    config.read_from_file()
    logger.info(f"config loaded (status={config.status}), hostname={config.hostname}")

    try:
        if config.hostname:
            network.hostname(config.hostname)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"could not set network hostname: {e}")

    wifi = WifiManager(ap_ssid_prefix=config.ap_ssid_prefix or "RPi-Pico-Water-Level",
                       ap_password=config.ap_password or "1234567890",
                       station_ssid=config.station_ssid or "",
                       station_password=config.station_password or "",
                       connect_timeout=20, retry_interval=10,
                       log_level=GLOBAL_LOG_LEVEL)
    
    ntp = NetworkTimeService(wifi, log_level=GLOBAL_LOG_LEVEL)

    redis = RedisClientService(wifi, 
                               config.redis_server or "", 
                               config.redis_port or 6379,
                               retry_interval=5, 
                               check_interval=30, 
                               log_level=GLOBAL_LOG_LEVEL,
                               password=config.get("redis_password", "") or "")
    
    sensor = UltrasonicSensor(config=config, 
                              ntp=ntp, 
                              redis=redis,
                              interval_s=DEFUALT_MEASUREMENT_INTERVAL_S, 
                              log_level=GLOBAL_LOG_LEVEL)

    async def apply_config_and_restart():
        # give the HTTP response time to reach the client, then reset
        await asyncio.sleep(2)
        logger.info("Configuration applied -> resetting device.")
        machine.reset()

    web = WebApp(config, wifi, ntp, redis, sensor, on_apply=apply_config_and_restart)

    wifi_task = asyncio.create_task(wifi.run_forever())

    # wait until wifi is up in some mode (client or AP) before starting the web server
    t0 = now_ms()
    while wifi.status == "not connected":
        if elapsed_ms(t0) > WIFI_WAIT_TIMEOUT_S * 1000:
            logger.warning(f"wifi not up after {WIFI_WAIT_TIMEOUT_S} s - starting web server anyway")
            break
        await asyncio.sleep(1)
    logger.info(f"wifi status: {wifi.status} (ip={wifi.ip})")

    ntp_task = asyncio.create_task(ntp.run_forever())
    redis_task = asyncio.create_task(redis.run_forever())
    sensor_task = asyncio.create_task(sensor.run_forever())

    app = web.get_app()
    logger.info("starting web server on http://0.0.0.0:80")
    server_task = asyncio.create_task(app.start_server(host="0.0.0.0", port=80))

    background = {"wifi": wifi_task, "ntp": ntp_task, "redis": redis_task, "sensor": sensor_task}
    while True:
        if server_task.done():
            logger.info("web server task ended -> resetting device")
            break
        dead = [name for name, task in background.items() if task.done()]
        if dead:
            for name in dead:
                logger.error(f"watchdog: task '{name}' ended unexpectedly")
            logger.error("watchdog: resetting device to recover")
            break
        gc.collect()
        await asyncio.sleep(WATCHDOG_INTERVAL_S)

    machine.reset()


try:
    asyncio.run(main())
except KeyboardInterrupt:
    # Ctrl-C (e.g. from a developer with mpremote attached): drop to the REPL
    # on purpose - resetting here would cause a reset loop with USB tools.
    # In field operation no Ctrl-C ever arrives.
    logger.warning("interrupted (Ctrl-C) - dropping to REPL")
except Exception as e:  # noqa: BLE001
    # Any unexpected fatal error: reset so the device recovers into the
    # running application instead of sitting dead at the REPL.
    logger.fatal(f"fatal error in main: {e} - resetting")
    time.sleep(2)
    machine.reset()
