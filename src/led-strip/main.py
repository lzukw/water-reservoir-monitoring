import asyncio

import machine
import utime

from config import Config
from ledstrip import LEDStrip
from logger import Logger
from microdot_app import MicrodotApp
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

ledstrip = LEDStrip(num_leds=config.led_count or 20, 
                    brightness=config.led_brightness or 255)

microdot_app = MicrodotApp(config=config, 
                           wifi=wifi, 
                           led_strip=ledstrip)


async def main():
    logger.log("INFO", "Starting Wifi connection...")
    asyncio.create_task(wifi.connect_ap_and_sta_forever())
    starttime = utime.ticks_ms()
    while utime.ticks_diff(utime.ticks_ms(), starttime) < 10000:
        if wifi.status.startswith("connected as"):
            break
        await asyncio.sleep(1)

    logger.log("INFO", "Starting web server on http://0.0.0.0:80")
    app = microdot_app.get_app()
    server_task = asyncio.create_task(app.start_server(host="0.0.0.0", port=80))

    await server_task
    logger.log("INFO", "Web server stopped. Will reset the device now.")
    machine.reset()

asyncio.run(main())
        