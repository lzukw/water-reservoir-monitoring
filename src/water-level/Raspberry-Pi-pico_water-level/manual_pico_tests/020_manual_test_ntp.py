import asyncio

from config import Config
from logger import Logger
from ntp_time import NetworkTimeService
from wifi import WifiManager

config = Config("config.json", log_level="INFO")
config.read_from_file()
logger = Logger("MAIN", level="INFO")

wifi = WifiManager(ap_ssid_prefix=config.ap_ssid_prefix or "RPi-Pico-Water-Level",
                    ap_password=config.ap_password or "1234567890",
                    station_ssid=config.station_ssid or "",
                    station_password=config.station_password or "",
                    connect_timeout=20, retry_interval=10,
                    log_level="INFO")

ntp_service = NetworkTimeService(wifi, period=5, valid_time_threshold=15, retry_interval=4, log_level="DEBUG")

print("During this test much shorter times are used:\n"
      "\n"
      "- The network time is fetched every 5 seconds.\n"
      "- If it cannot be fetched for 15 seconds, the network-time-status changes\n"
      "  from accurate to inaccurate.\n"
      "\n"
      "1. Start with Router turned off, so the Pico the network-time-status should be\n"
      "   'not valid'\n"
      "2. Then turn on the Router. The status should change to 'accurate'\n"
      "3. Turn off the Router again. After 15 seconds the status should change to\n"
      "   'inaccurate'\n")
print()

async def main():
    tasks = [
        asyncio.create_task(wifi.run_forever()),
        asyncio.create_task(ntp_service.run_forever()),
    ]

    try:
        while True:
            print(f"         ---NTP-Time status: {ntp_service.time_status}, current time: {ntp_service.current_time_utc_iso()}")
            await asyncio.sleep(0.5)
    except KeyboardInterrupt:
        print("\nKeyboardInterrupt - stopping tasks")
    finally:
        for t in tasks:
            t.cancel()

asyncio.run(main())
