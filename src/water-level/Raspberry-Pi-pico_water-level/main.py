import asyncio

import machine
import utime

from config import Config
from logger import Logger
from microdot_app import MicrodotApp
from network_time import NetworkTime
from redis_server import RedisServer
from ultrasonic_sensor import UltrasonicSensor
from wifi import Wifi

logger = Logger("MAIN", log_level="INFO")

config = Config("config.json", log_level="INFO")
config.read_from_file()

ap_ssid_prefix = config.ap_ssid_prefix
ap_password = config.ap_password
assert ap_ssid_prefix is not None, "AP SSID prefix must be set in config."
assert ap_password is not None, "AP password must be set in config."

wifi = Wifi(ap_ssid_prefix=ap_ssid_prefix,
            ap_password=ap_password,
            station_ssid=config.station_ssid or "",
            station_password=config.station_password or "",
            log_level="INFO")

network_time = NetworkTime(wifi=wifi, log_level="INFO")

redis_server = RedisServer(config=config, log_level="INFO")

ultrasonic_sensor = UltrasonicSensor(config=config, 
                                     network_time=network_time, 
                                     redis_server=redis_server, 
                                     log_level="INFO")

microdot_app = MicrodotApp(config=config, 
                           wifi=wifi, 
                           network_time=network_time,
                           redis_server=redis_server,
                           ultrasonic_sensor=ultrasonic_sensor)


async def main():
    # Start wifi-connection-loop as background task.
    logger.log("INFO", "Starting Wifi connection...")
    wifi_task = asyncio.create_task(wifi.connect_ap_or_sta_forever())
    # wait for wifi to connect (without wifi-connection, there is no use to continue anything further)
    while not wifi.status.startswith("connected as"):
        await asyncio.sleep(1)

    # start webserver as background task
    logger.log("INFO", "Starting web server on http://0.0.0.0:80")
    app = microdot_app.get_app()
    server_task = asyncio.create_task(app.start_server(host="0.0.0.0", port=80))

    # start network time synchronization as background task
    logger.log("INFO", "Starting network time synchronization...")
    network_time_task = asyncio.create_task(network_time.get_network_time_forever())
    starttime = utime.ticks_ms()
    # wait 10 seconds for network time to be valid, if it doesn't, continue anyway
    while utime.ticks_diff(utime.ticks_ms(), starttime) < 10000:
        if network_time.time_status != "not valid":
            break
        await asyncio.sleep(1)

    # start redis-connection-loop as background task
    logger.log("INFO", "Starting Redis connection...")
    redis_task = asyncio.create_task(redis_server.connect_forever())
    starttime = utime.ticks_ms()
    # wait 10 seconds for redis to connect, if it doesn't, continue anyway
    while utime.ticks_diff(utime.ticks_ms(), starttime) < 10000:
        if redis_server.status == "connected":
            break
        await asyncio.sleep(1)

    # start ultrasonic sensor measurement as background task
    logger.log("INFO", "Starting ultrasonic sensor measurement...")
    sensor_task = asyncio.create_task(ultrasonic_sensor.measure_and_publish_distance_forever())

    # Watchdog: keep references to all tasks and check them periodically.
    # - The web server task ending is the *intended* shutdown path (e.g. after
    #   "Save and Apply" in the config web page): reset to apply the new config.
    # - Any other task ending is unexpected, because the *_forever loops should
    #   never return. If one dies (unhandled exception), log it and reset the
    #   device, so it recovers into a clean, fully working state.
    background_tasks = {
        "wifi": wifi_task,
        "network_time": network_time_task,
        "redis": redis_task,
        "sensor": sensor_task,
    }
    while True:
        if server_task.done():
            logger.log("INFO", "Web server stopped. Will reset the device now.")
            break
        dead_tasks = [name for name, task in background_tasks.items() if task.done()]
        if dead_tasks:
            for name in dead_tasks:
                logger.log("ERROR", f"Watchdog: background task '{name}' ended unexpectedly.")
                try:
                    exc = background_tasks[name].exception()
                    if exc is not None:
                        logger.log("ERROR", f"Task '{name}' raised: {exc!r}")
                except Exception:
                    pass  # Task.exception() may not exist on older MicroPython versions
            logger.log("ERROR", "Watchdog: resetting device to recover.")
            break
        await asyncio.sleep(5)  # watchdog check interval: 5 seconds

    logger.log("INFO", "Resetting device...")
    machine.reset()

asyncio.run(main())
utime.sleep(1)
machine.reset()
        