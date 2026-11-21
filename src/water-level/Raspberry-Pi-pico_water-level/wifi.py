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
                       try_station_connect_interval: int = 60
                       ) -> None:
        self._ap_ssid_prefix : str = ap_ssid_prefix
        self._ap_password : str = ap_password
        self._station_ssid : str = station_ssid
        self._station_password: str = station_password
        self._connect_timeout : int = connect_timeout
        self._try_station_connect_interval : int = try_station_connect_interval
        
        self._logger : Logger = Logger(name="Wifi", log_level=log_level)

        # Access-Point and Station are used at the same time, so each interface
        # has its own status: "not connected", "connecting" and
        # "connected as AP" (status_ap) resp. "connected as client" (status_sta).
        self._status_ap : str = "not connected"
        self._status_sta : str = "not connected"

        self._wlan_ap = network.WLAN(network.AP_IF)
        self._wlan_sta = network.WLAN(network.STA_IF)

    @property
    def status_ap(self) -> str:
        """Status of the Access-Point interface. Returns "not connected", "connecting",
        or "connected as AP". If the Access Point was lost, the status is reset to
        "not connected"."""
        if self._status_ap == "connected as AP" and not self._wlan_ap.active():
            self._logger.log("INFO", "Lost Wifi Access Point")
            self.disconnect_ap()
        return self._status_ap

    @property
    def status_sta(self) -> str:
        """Status of the Station (client) interface. Returns "not connected", "connecting"
        or "connected as client". If the connection to the router was lost, the status is
        reset to "not connected"."""
        if ( 
            self._status_sta == "connected as client" 
            and ( not self._wlan_sta.isconnected() 
                 or self._wlan_sta.status() != network.STAT_GOT_IP )
        ):
                self._logger.log("INFO", "Lost connection to Wifi as Client")
                self.disconnect_sta()
        return self._status_sta

    @property
    def status(self) -> str:
        """Combined status of both interfaces: "connected as client" if the station is
        connected, else "connected as AP" if the Access Point is running, else
        "not connected"."""
        if self.status_sta == "connected as client":
            return "connected as client"
        if self.status_ap == "connected as AP":
            return "connected as AP"
        return "not connected"

    def _active_wlan(self):
        """Returns the interface that provides the ip-configuration: the station
        interface if connected as client, else the AP interface, else None."""
        if self.status_sta == "connected as client":
            return self._wlan_sta
        if self.status_ap == "connected as AP":
            return self._wlan_ap
        return None

    @property
    def ip_address(self) -> str:
        wlan = self._active_wlan()
        return wlan.ifconfig()[0] if wlan is not None else ""

    @property
    def netmask(self) -> str:
        wlan = self._active_wlan()
        return wlan.ifconfig()[1] if wlan is not None else ""

    @property
    def ip_gateway(self) -> str:
        wlan = self._active_wlan()
        return wlan.ifconfig()[2] if wlan is not None else ""

    @property
    def ip_dns_server(self) -> str:
        wlan = self._active_wlan()
        return wlan.ifconfig()[3] if wlan is not None else ""

    @property
    def ntpserver(self) -> str:
        """In station-mode, we assume that our gateway is also a timeserver.
        In pure Access-Point mode, no NTP-server is available."""
        if self.status_sta == "connected as client":
            return self._wlan_sta.ifconfig()[2]
        return ""

    def get_ap_mac_address(self, separator=":") -> str:
        mac_bytes = self._wlan_ap.config('mac')
        mac_str = separator.join(f'{b:02X}' for b in mac_bytes)
        return mac_str

    def disconnect_ap(self):
        """Disconnect Access-Point-Interface"""
        self._wlan_ap.disconnect()
        # Don't use asyncio.sleep() here, because this funciton should not be async. 
        # Making it async would imply another "Disconnecting" state, and also the status-
        # properties would have to be async, which is not possible. So we just use time.sleep() here.
        time.sleep(1)
        self._wlan_ap.active(False)
        self._status_ap = "not connected"
        gc.collect()

    def disconnect_sta(self):
        """Disconnect Station-Interface"""
        self._wlan_sta.disconnect()
        # Again: Don't use asyncio.sleep() here, because this function should not be async.
        time.sleep(1)
        self._wlan_sta.active(False)
        self._status_sta = "not connected"
        gc.collect()

    def disconnect(self):
        """Disconnect both interfaces: the station disconnects from the router and
        the Access Point is turned off."""
        self.disconnect_ap()
        self.disconnect_sta()

    async def connect_as_ap(self) -> bool:
        """Start the Access-Point interface. The station interface is not touched, so
        AP and STA can be active at the same time. Returns True on success."""
        if self.status_ap == "connected as AP":
            # self.status_ap only returns "connected as AP" after performing the
            # necessary checks. So if we are already connected as AP, we can just return.
            return True
        self._logger.log("DEBUG", "Starting to setup Wifi as Access Point...")
        self._status_ap = "connecting"
        await asyncio.sleep(1) 
        self._wlan_ap.active(True)
        ap_ssid = f"{self._ap_ssid_prefix}_{self.get_ap_mac_address(separator='')[-6:]}"
        self._wlan_ap.config(essid=ap_ssid, password=self._ap_password)
        await asyncio.sleep(1)
        self._status_ap = "connected as AP"
        # self.status_ap property will check if the AP is really active and
        # only return "connected as AP" if AP-mode is working.
        if self.status_ap != "connected as AP":
            self._logger.log("ERROR", "Failed to put Wifi in Access Point mode.")
            self.disconnect_ap()
            return False
        self._logger.log("INFO", f"Successfully started Wifi Access Point. SSID='{ap_ssid}', password='{self._ap_password}'")
        self._logger.log("INFO", f"AP IP-Address {self.ip_address}, netmask {self.netmask}, Gateway: {self.ip_gateway}, DNS: {self.ip_dns_server}")
        return True

    async def connect_as_client(self) -> bool:
        """Connect the station interface to the Wifi from a router. The Access-Point
        interface is not touched, so AP and STA can be active at the same time.
        Returns True or False, depending on success."""
        if self.status_sta == "connected as client":
            # self.status_sta only returns "connected as client" after performing the
            # necessary checks. So if we are already connected as client, we can just
            # return True.
            return True
        
        self._logger.log("DEBUG", "Starting to connect to Wifi as Client...")
        self._status_sta = "connecting"
        await asyncio.sleep(1)
        self._wlan_sta.active(True)
        self._wlan_sta.connect(self._station_ssid, self._station_password)
        self._logger.log("INFO", f"Trying to Connect as Client/Station to SSID '{self._station_ssid}' with password '{self._station_password}'")
        end_time = time.ticks_add( time.ticks_ms(), self._connect_timeout*1000)
        while not self._wlan_sta.isconnected():
            if time.ticks_diff(end_time, time.ticks_ms()) < 0:
                self._logger.log("INFO", "Failed to connect to Wi-Fi as client.")
                self.disconnect_sta()
                return False
            self._logger.log("DEBUG", f"...Current WLAN-Status {self._wlan_sta.status()}")
            await asyncio.sleep(1) 
        self._status_sta = "connected as client"
        # self.status_sta property will check if we are really connected and got an IP
        # and only return "connected as client" if station-mode is working.
        if self.status_sta != "connected as client":
            self._logger.log("ERROR", "Failed to connect to Wi-Fi as client.")
            self.disconnect_sta()
            return False
        self._logger.log("INFO", "Successfully connected to Wifi as Client.")
        self._logger.log("INFO", f"IP-Address {self.ip_address}, netmask {self.netmask}, Gateway: {self.ip_gateway}, DNS: {self.ip_dns_server}")
        # In station-mode, we assume that our Gateway is also a Timeserver
        self._logger.log("INFO", f"Assuming NTP-Server is also gateway: {self.ip_gateway}")
        return True

    async def connect_ap_or_sta_forever(self):
        """Run Access-Point and Station at the same time, forever:
        - The Access Point is started and kept alive, so the device is always reachable
          via its own Wifi network.
        - The Station interface is (re)connected to the configured router whenever it
          is not connected.
        Both interfaces are independent of each other and have their own status, see
        status_ap and status_sta."""

        while True:

            # The calls to status_sta performs the necessary checks to ensure, that the
            # station and access-point are really connected and have an IP.
            status_ap = self.status_ap
            status_sta = self.status_sta

            # This is the goal to reach: Only connect to the router as station, no access point.
            if status_sta == "connected as client" and status_ap == "not connected":
                self._logger.log("DEBUG", "Station is connected, Access Point is not running. Nothing to do.")

            # If station-Interface and access-point are connected, turn off the Access Point, 
            # because we don't need it anymore.
            elif status_sta == "connected as client" and status_ap == "connected as AP":
                self._logger.log("INFO", "Station is connected, Access Point is still up, so turning off Access Point.")
                self.disconnect_ap()

            # Try to connect to the router as station, if not already connected. If that is successful,
            # turn off the Access Point, because we don't need it anymore. But if connection as station fails, 
            # start the Access Point, if it is not already running.
            elif status_sta == "not connected":
                await self.connect_as_client()
                # Call to self.status_sta performs the necessary checks to ensure, that the station is really connected and has an IP.
                if self.status_sta == "connected as client":
                    if status_ap == "connected as AP":
                        self._logger.log("INFO", "Successfully connected to Wifi as Client, so turn off Access Point.")
                        self.disconnect_ap()
                else:
                    if status_ap == "not connected":
                        self._logger.log("INFO", "Failed to connect to Wifi as Client, Access Point is not running, so starting Access Point.")
                        await self.connect_as_ap()

            await asyncio.sleep(self._try_station_connect_interval)


if __name__ == "__main__":
    from config import Config

    config = Config("config.json", log_level="INFO")
    config.read_from_file()
    assert config.ap_ssid_prefix is not None, "AP SSID Prefix must be set in config.json"
    assert config.ap_password is not None, "AP Password must be set in config.json"
    assert config.station_ssid is not None, "Station SSID must be set in config.json"
    assert config.station_password is not None, "Station Password must be set in config.json"

    # In Test-Mode, we only wait 20 seconds between station connect tries. default is longer
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
        asyncio.create_task(wifi.connect_ap_or_sta_forever())
        while True:
            await asyncio.sleep(10)
            print(f"Wifi-Status AP: {wifi.status_ap}, STA: {wifi.status_sta}, IP-Address: {wifi.ip_address}, Netmask: {wifi.netmask}, Gateway: {wifi.ip_gateway}, DNS: {wifi.ip_dns_server}, NTP-Server: {wifi.ntpserver}")

    asyncio.run(main())
