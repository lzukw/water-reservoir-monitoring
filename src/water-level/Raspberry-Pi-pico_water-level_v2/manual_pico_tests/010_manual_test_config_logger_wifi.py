import asyncio

import network

from config import Config
from logger import Logger
from wifi import WifiManager

GLOBAL_LOG_LEVEL = "DEBUG"  # DEBUG, INFO, WARNING, ERROR, CRITICAL

config = Config("config.json", log_level=GLOBAL_LOG_LEVEL)
config.read_from_file()
logger = Logger("MAIN", level=GLOBAL_LOG_LEVEL)
WIFI_WAIT_TIMEOUT_S = 60

wifi = WifiManager(ap_ssid_prefix=config.ap_ssid_prefix or "RPi-Pico-Water-Level",
                    ap_password=config.ap_password or "1234567890",
                    station_ssid=config.station_ssid or "",
                    station_password=config.station_password or "",
                    connect_timeout=20, retry_interval=10,
                    log_level=GLOBAL_LOG_LEVEL)

print("During this test: \n"
      "- Make sure the WiFi router is running and reachable, and ping the pico\n"
      "- Shutdown the WiFi router, wait for the Access Point to become available\n"
      "- Connect the PC to the Pico's Access Point and check the IP address\n"
      "- Ping the Pico's AP IP (default 192.168.4.1)\n"
      "- Start the Wifi-router again, and check that the Pico detects the\n"
      "  reachable gateway again, connects to the router and gets an IP address\n"
      "- Connect the PC to the Wifi Router's network and ping the Pico's IP again\n")

# Print status-codes for wlan-status of module network
keys = dir(network)
for key in keys:
      if key.startswith("STAT_"):
            print(f"network.{key} = {getattr(network, key)}")
print()


asyncio.run(wifi.run_forever())
