import asyncio
import gc
import time

import network

from logger import Logger


class Wifi:
    def __init__(self, ap_ssid_prefix: str, 
                       ap_password: str, 
                       station_ssid: str, 
                       station_password: str,
                       log_level: str = "INFO",
                       connect_timeout : int = 20,
                       try_station_connect_interval: int = 60*5
                       ) -> None:
        self._ap_ssid_prefix : str = ap_ssid_prefix
        self._ap_password : str = ap_password
        self._station_ssid : str = station_ssid
        self._station_password: str = station_password
        self._connect_timeout : int = connect_timeout
        self._try_station_connect_interval : int = try_station_connect_interval
        
        self._logger : Logger = Logger(name="Wifi", log_level=log_level)

        self._status : str = "not connected"
        self._ip_address : str = ""
        self._netmask : str = ""
        self._ip_gateway: str = ""
        self._ip_dns_server : str = ""
        self._ntp_server : str = ""

        self._wlan : network.WLAN | None = None

    @property
    def status(self) -> str:
        """returned status is "not connected", "connecting, "connected as client" or 
        "connected as AP"."""
        if self._status == "connected as client" and self._wlan and not self._wlan.isconnected():
            self._logger.log("INFO", "Lost connection to Wifi as Client")
            self._status = "not connected"
            self._wlan = None
            gc.collect()

        return self._status

    @property
    def ip_addresss(self) -> str:
        return self._ip_address

    @property
    def netmask(self) -> str:
        return self._netmask

    @property
    def ip_gateway(self) -> str:
        return self._ip_gateway

    @property
    def ip_dns_server(self) -> str:
        return self._ip_dns_server

    @property
    def ntpserver(self) -> str:
        return self._ntp_server

    def get_mac_address(self, separator=":") -> str:
        must_turn_off_wlan = False
        if self._wlan is None:
            self._wlan = network.WLAN(network.STA_IF)
            self._wlan.active(True)
            must_turn_off_wlan = True

        mac_bytes = self._wlan.config('mac')
        mac_str = separator.join(f'{b:02X}' for b in mac_bytes)
        if must_turn_off_wlan:
            self._wlan.active(False)
            self._wlan = None
            gc.collect()
        return mac_str

    def connect_as_ap(self):
        """Connect as Access Point to the Wifi."""
        self._wlan = None
        gc.collect()
        self._wlan = network.WLAN(network.AP_IF)
        self._wlan.active(True)
        ap_ssid = f"{self._ap_ssid_prefix}_{self.get_mac_address(separator='')[-6:]}"
        self._wlan.config(essid=ap_ssid, password=self._ap_password)
        self._ip_address, self._netmask, self._ip_gateway, self._ip_dns_server = self._wlan.ifconfig()
        self._logger.log("INFO", f"Successfully connected to Wifi as Access Point. SSID='{ap_ssid}', password='{self._ap_password}'")
        self._logger.log("INFO", f"Got IP-Addreess {self._ip_address}, netmask {self._netmask}, Gateway: {self._ip_gateway}, DNS: {self._ip_dns_server}")
        self._logger.log("INFO", f"IP-Address {self._ip_address}, netmask {self._netmask}, Gateway: {self._ip_gateway}, DNS: {self._ip_dns_server}")
        self._ntp_server = ""
        self._logger.log("INFO", "No NTP-Server in Access point mode available.")
        self._status = "connected as AP"     

    async def connect_as_client(self) -> bool:
        """Connect as client to the Wifi from a router. Returns True or False,
        depending on success."""
        self._wlan = None
        gc.collect()
        self._wlan = network.WLAN(network.STA_IF)
        # Turn off WLAN to tear down prevous connection to Router. So the
        # Router knows, we are gone.
        self._wlan.active(False)
        self._status = "connecting"
        await asyncio.sleep(1)
        self._wlan.active(True)
        self._wlan.connect(self._station_ssid, self._station_password)
        self._logger.log("INFO", f"Trying to Connect as Client/Station to SSID '{self._station_ssid}' with password '{self._station_password}'")
        end_time = time.ticks_add( time.ticks_ms(), 10*1000)
        while not self._wlan.isconnected():
            if time.ticks_diff(end_time, time.ticks_ms()) < 0:
                self._logger.log("INFO", "Failed to connect to Wi-Fi as client.")
                self._status = "not connected"
                self._wlan.active(False)
                self._wlan = None
                gc.collect()
                return False
            self._logger.log("DEBUG", f"...Current WLAN-Status {self._wlan.status()}")
            await asyncio.sleep(1)
        self._ip_address, self._netmask, self._ip_gateway, self._ip_dns_server = self._wlan.ifconfig()
        self._ntp_server = ""
        self._logger.log("INFO", "Successfully connected to Wifi as Client.")
        self._logger.log("INFO", f"IP-Address {self._ip_address}, netmask {self._netmask}, Gateway: {self._ip_gateway}, DNS: {self._ip_dns_server}")
        # In sation-mode, we assume that our Gateway is also a Timeserver
        self._ntp_server = self._ip_gateway
        self._logger.log("INFO", f"Assuming NTP-Server is also gateway: {self._ntp_server}")
        self._status = "connected as client"
        return True

    async def connect_forever_as_station_with_fallback_as_ap(self):
        """Try to connect as client to the Wifi from a router. If this fails, 
        connect as Access Point.
        Then run forever:
        If we are connected as client, check the connection periodically and
        fallback to Access Point if the connection is lost and cannot be 
        established again.
        In Access Point mode, we will try to connect as client again after a 
        certain time."""

        if self._status == "not connected":
            await self.connect_as_client()
        if self._status == "connected as client":
            self._logger.log("INFO", "Successfully connected to Wifi as Client.")
            await asyncio.sleep(10)
        else:
            self._logger.log("INFO", "Fallback to Access Point mode.")
            self.connect_as_ap()
            await asyncio.sleep(self._try_station_connect_interval)

        while True:
            if self._status == "connected as client":
                assert self._wlan is not None, "WLAN should not be None when connected as client."
                if self._wlan.isconnected():
                    self._logger.log("DEBUG", "We are connected as Client and connection is still up.")
                    await asyncio.sleep(10)
                    continue
                self._logger.log("INFO", "Lost connection to Wifi as Client. Trying to reconnect.")
                await self.connect_as_client()
                if self._status == "connected as client":
                    self._logger.log("INFO", "Successfully reconnected as Client.")
                    await asyncio.sleep(10)
                    continue
                else:
                    self._logger.log("INFO", "Failed to reconnect as Client. Fallback to Access Point.")
                    self.connect_as_ap()
                    await asyncio.sleep(self._try_station_connect_interval)
                    continue            
            elif self._status == "connected as AP":
                self._logger.log("INFO", "We are in Access Point mode. Trying to connect as Client again. Connection could be lost for a moment.")
                await self.connect_as_client()
                if self._status == "connected as client":
                    self._logger.log("INFO", "Successfully connected as Client again. Access Point is down now.")
                    await asyncio.sleep(10)
                    continue
                else:
                    self._logger.log("INFO", "Failed to connect as Client again. Staying in Access Point mode.")
                    self.connect_as_ap()
                    await asyncio.sleep(self._try_station_connect_interval)
                    continue


if __name__ == "__main__":
    from config import Config

    config = Config("config.json", log_level="INFO")
    config.read_from_file()
    assert config.ap_ssid_prefix is not None, "AP SSID Prefix must be set in config.json"
    assert config.ap_password is not None, "AP Password must be set in config.json"
    assert config.station_ssid is not None, "Station SSID must be set in config.json"
    assert config.station_password is not None, "Station Password must be set in config.json"

    # In Test-Mode, we only remain 20 seconds in ap-mode. default is 5 minutes
    wifi = Wifi(ap_ssid_prefix=config.ap_ssid_prefix, 
                ap_password=config.ap_password, 
                station_ssid=config.station_ssid, 
                station_password=config.station_password, 
                log_level="DEBUG", 
                try_station_connect_interval=20)

    # Print status-codes for wlan-status of module network
    keys = dir(network)
    for key in keys:
        if key.startswith("STAT_"):
            print(f"network.{key} = {getattr(network, key)}")

    async def main():
        asyncio.create_task(wifi.connect_forever_as_station_with_fallback_as_ap())
        while True:
            await asyncio.sleep(10)
            print(f"Wifi-Status: {wifi.status}, IP-Address: {wifi.ip_addresss}, Netmask: {wifi.netmask}, Gateway: {wifi.ip_gateway}, DNS: {wifi.ip_dns_server}, NTP-Server: {wifi.ntpserver}")

    asyncio.run(main())
    