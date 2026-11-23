"""Main entry point of the LED-strip Pico (runs after boot.py).

Starts all subsystems as parallel asyncio tasks:
  - wifi:  station first, access-point fallback, auto-recovery
           (WifiManager.run_forever, shared with the water-level project)
  - web:   configuration website on port 80 (microdot)

The LED strip itself is no task: it is driven synchronously whenever the
config changes (see microdot_app).

Robustness (same pattern as the water-level project): every loop shields its
own exceptions and never dies. A watchdog resets the device only if a core
task unexpectedly ends - the wifi loop, or the web server. The web server
also ends on purpose after a new config was applied via the web form, so the
watchdog reset boots the device into the new configuration.
"""

import asyncio
import gc
import time

import machine
import network

from config import Config
from ledstrip import LEDStrip
from logger import Logger
from microdot_app import MicrodotApp
from timeutil import elapsed_ms, now_ms
from wifi import WifiManager

GLOBAL_LOG_LEVEL = "INFO"  # DEBUG, INFO, WARNING, ERROR, FATAL

logger = Logger("MAIN", level=GLOBAL_LOG_LEVEL)

WIFI_WAIT_TIMEOUT_S = 60
WATCHDOG_INTERVAL_S = 5


async def main():
    config = Config("config.json", log_level=GLOBAL_LOG_LEVEL)
    config.read_from_file()
    logger.info(f"config loaded, hostname={config.hostname}")

    try:
        if config.hostname:
            network.hostname(config.hostname)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"could not set network hostname: {e}")

    wifi = WifiManager(
        ap_ssid_prefix=config.ap_ssid_prefix or "RPi-Pico-LED-Strip",
        ap_password=config.ap_password or "1234567890",
        station_ssid=config.station_ssid or "",
        station_password=config.station_password or "",
        connect_timeout=20,
        retry_interval=10,
        log_level=GLOBAL_LOG_LEVEL,
    )

    ledstrip = LEDStrip(
        num_leds=config.led_count or 20,
        brightness=config.led_brightness or 255,
        log_level=GLOBAL_LOG_LEVEL,
    )

    async def apply_config_and_restart():
        # give the HTTP response time to reach the client, then reset
        await asyncio.sleep(2)
        logger.info("Configuration applied -> resetting device.")
        network.WLAN(network.STA_IF).disconnect()
        network.WLAN(network.AP_IF).active(False)
        time.sleep(1)
        machine.reset()

    microdot_app = MicrodotApp(
        config=config,
        wifi=wifi,
        led_strip=ledstrip,
        on_apply=apply_config_and_restart,
        log_level=GLOBAL_LOG_LEVEL,
    )

    await asyncio.sleep(1) # let wifi chip initialize
    wifi_task = asyncio.create_task(wifi.run_forever())

    # wait until wifi is up in some mode (client or AP) before starting the web server
    t0 = now_ms()
    while wifi.status == "not connected":
        if elapsed_ms(t0) > WIFI_WAIT_TIMEOUT_S * 1000:
            logger.warning(
                f"wifi not up after {WIFI_WAIT_TIMEOUT_S} s - starting web server anyway"
            )
            break
        await asyncio.sleep(1)
    logger.info(f"wifi status: {wifi.status} (ip={wifi.ip})")

    app = microdot_app.get_app()
    logger.info("starting web server on http://0.0.0.0:80")
    server_task = asyncio.create_task(app.start_server(host="0.0.0.0", port=80))

    background = {"wifi": wifi_task}
    while True:
        if server_task.done():
            logger.info(
                "web server task ended (config applied or server stopped) -> resetting device"
            )
            break
        dead = [name for name, task in background.items() if task.done()]
        if dead:
            for name in dead:
                logger.error(f"watchdog: task '{name}' ended unexpectedly")
            logger.error("watchdog: resetting device to recover")
            break
        gc.collect()
        await asyncio.sleep(WATCHDOG_INTERVAL_S)

    network.WLAN(network.STA_IF).disconnect()
    network.WLAN(network.AP_IF).active(False)
    time.sleep(1)
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
