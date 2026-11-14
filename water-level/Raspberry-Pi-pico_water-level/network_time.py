import asyncio
import time

import ntptime

from logger import Logger
from wifi import Wifi


class NetworkTime:

    def __init__(self, wifi: Wifi, log_level: str = "INFO", period: int = 24*60*60, valid_time_threshold: int = 10*24*60*60):
        self._wlan : Wifi = wifi
        self._logger : Logger = Logger(name="NetworkTime", log_level=log_level)
        self._period : int = period
        self._valid_time_threshold : int = valid_time_threshold
        self._last_updated_ticks : int | None = None  # Store the last updated time in ticks   

    @property
    def last_updated(self) -> float | None:
        """Returns the time time in seconds since the local-time was upddated
        with the network time from the NTP-Server. If the local-time was never updated, it returns -1."""
        if self._last_updated_ticks is None:
            return None
        else:
            return time.ticks_diff(time.ticks_ms(), self._last_updated_ticks) / 1000.0

    @property
    def time_status(self) -> str:
        """Returns a string indicating the status of the local time:
        "not valid", "inaccurate" or "accurate". """
        last_updated = self.last_updated
        if last_updated is None:
            return "not valid"  
        elif last_updated > self._valid_time_threshold:
            return "inaccurate"
        else:
            return "accurate"

    def get_current_time_UTC_iso(self) -> str:
        """Returns the current UTC time as an ISO 8601 formatted string."""
        current_time = time.gmtime()
        iso_time = f"{current_time[0]:04d}-{current_time[1]:02d}-{current_time[2]:02d}T{current_time[3]:02d}:{current_time[4]:02d}:{current_time[5]:02d}.000Z"
        return iso_time

    async def get_network_time_forever(self):
        while True:
            self._logger.log("INFO", "Attempting to get network time from NTP-Server...")
            if self._wlan.status == "connected as client":
                try:
                    ntptime.settime()
                    self._last_updated_ticks = time.ticks_ms()
                    self._logger.log("INFO", "Successfully got network time from NTP-Server.")
                except (OSError, ValueError) as e:
                    self._logger.log("ERROR", f"Failed to get network time from NTP-Server. Error: {e}")
            else:
                last_updated = self.last_updated
                self._logger.log("INFO", "Not connected as client. Cannot get network time.")
                if last_updated is None:
                    self._logger.log("WARNING", f"Local time was never updated with network time. Current time is wrong: {self.get_current_time_UTC_iso()}.")
                elif last_updated > self._valid_time_threshold:
                    self._logger.log("WARNING", f"Local time was last updated with network time more than {self._valid_time_threshold} seconds ago. Current time could not be accurate: {self.get_current_time_UTC_iso()}.")

            # Wait for `period` seconds before trying again
            sleep_time = self._period if self.time_status == "accurate" else 10  # If time is not accurate, try again in 10 seconds
            self._logger.log("INFO", f"Current time (UTC): {self.get_current_time_UTC_iso()}. Next attempt to get network time in {sleep_time} seconds.")
            await asyncio.sleep(sleep_time)  


if __name__ == "__main__":
    from config import Config

    config = Config("config.json", log_level="INFO")
    config.read_from_file()
    assert config.ap_ssid_prefix is not None, "AP SSID prefix must be set in config."
    assert config.ap_password is not None, "AP password must be set in config."
    assert config.station_ssid is not None, "Station SSID must be set in config."
    assert config.station_password is not None, "Station password must be set in config."

    wifi = Wifi(ap_ssid_prefix=config.ap_ssid_prefix, 
                ap_password=config.ap_password,
                station_ssid=config.station_ssid,
                station_password=config.station_password,
                log_level="INFO")

    # For test-mode get Network-Time every 10 seconds, oherwise every 24 hours (default)
    network_time = NetworkTime(wifi=wifi, log_level="DEBUG", period=10)

    async def main():
        asyncio.create_task(wifi.connect_forever_as_station_with_fallback_as_ap())
        asyncio.create_task(network_time.get_network_time_forever())
        while True:
            await asyncio.sleep(1)
            print(f"Current time: {network_time.get_current_time_UTC_iso()}, updated {network_time.last_updated} seconds ago. Time-status: {network_time.time_status}")
    
    asyncio.run(main())
