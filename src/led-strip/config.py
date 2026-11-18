import gc
import json

from logger import Logger


class Config:
    def __init__(self, config_file: str, log_level: str = "INFO"):
        self._config_file : str = config_file
        self._logger = Logger(name="Config", log_level=log_level)

        self._hostname: str | None = None
        self._ap_ssid_prefix: str | None = None
        self._ap_password: str | None= None
        self._station_ssid: str | None = None
        self._station_password: str | None = None

        self._led_count: int | None = None
        self._led_brightness: int | None = None

        self._config_status: str = "Not loaded"
        gc.collect()

    def read_from_file(self):
        try:
            with open(self._config_file, "r") as f:
                config = json.load(f)
        except (OSError, ValueError) as e:
            self._logger.log("FATAL", f"Error reading config file: {e}")
            self._hostname = None
            self._ap_ssid_prefix = None
            self._ap_password = None
            self._station_ssid = None
            self._station_password = None
            self._led_count = None
            self._led_brightness = None
            self._config_status = "not loaded"
            gc.collect()
            return

        self._hostname = config.get("hostname")
        if self._hostname is None:
            self._logger.log("ERROR", "key 'hostname' missing in config-file")
        
        self._ap_ssid_prefix = config.get("ap_ssid_prefix")
        if self._ap_ssid_prefix is None:
            self._logger.log("ERROR", "key 'ap_ssid_prefix' missing in config-file")

        self._ap_password = config.get("ap_password")
        if self._ap_password is None:
            self._logger.log("ERROR", "key 'ap_password' missing in config-file") 

        self._station_ssid = config.get("station_ssid")
        if self._station_ssid is None:
            self._logger.log("ERROR", "key 'station_ssid' missing in config-file")

        self._station_password = config.get("station_password")
        if self._station_password is None:
            self._logger.log("ERROR", "key 'station_password' missing in config-file")

        self._led_count = config.get("ledCount")
        if self._led_count is None:
            self._logger.log("ERROR", "key 'ledCount' missing in config-file")

        self._led_brightness = config.get("ledBrightness")
        if self._led_brightness is None:
            self._logger.log("ERROR", "key 'ledBrightness' missing in config-file")

        self._config_status = "loaded"
        gc.collect()

    def write_to_file(self):
        config = {
            "hostname": self._hostname or "",
            "ap_ssid_prefix": self._ap_ssid_prefix or "",
            "ap_password": self._ap_password or "",
            "station_ssid": self._station_ssid or "",
            "station_password": self._station_password or "",
            "ledCount": self._led_count or 0,
            "ledBrightness": self._led_brightness or 0
        }
        try:
            with open(self._config_file, "w") as f:
                json.dump(config, f, separators=(',\n', ': ')) # type: ignore #
        except OSError as e:
            self._logger.log("FATAL", f"Error writing config file: {e}")
            gc.collect()
            return
               
        self._config_status = "saved"
        gc.collect()
        

    @property
    def hostname(self) -> str | None:
        return self._hostname

    @hostname.setter
    def hostname(self, value: str | None):
        self._hostname = value
        self._config_status = "modified"

    @property
    def ap_ssid_prefix(self) -> str | None:
        return self._ap_ssid_prefix

    @ap_ssid_prefix.setter
    def ap_ssid_prefix(self, value: str | None):
        self._ap_ssid_prefix = value
        self._config_status = "modified"

    @property
    def ap_password(self) -> str | None:
        return self._ap_password

    @ap_password.setter
    def ap_password(self, value: str | None):
        self._ap_password = value
        self._config_status = "modified"

    @property
    def station_ssid(self) -> str | None:
        return self._station_ssid

    @station_ssid.setter
    def station_ssid(self, value: str | None):
        self._station_ssid = value
        self._config_status = "modified"

    @property
    def station_password(self) -> str | None:
        return self._station_password

    @station_password.setter
    def station_password(self, value: str | None):
        self._station_password = value
        self._config_status = "modified"

    @property
    def led_count(self) -> int | None:
        return self._led_count

    @led_count.setter
    def led_count(self, value: int | None):
        self._led_count = value
        self._config_status = "modified"

    @property
    def led_brightness(self) -> int | None:
        return self._led_brightness

    @led_brightness.setter
    def led_brightness(self, value: int | None):
        self._led_brightness = value
        self._config_status = "modified"


if __name__ == "__main__":
    config = Config("config.json", log_level="DEBUG")
    config.read_from_file()
    print("Hostname:", config.hostname)
    print("AP SSID Prefix:", config.ap_ssid_prefix)
    print("AP Password:", config.ap_password)
    print("Station SSID:", config.station_ssid)
    print("Station Password:", config.station_password)
    print("LED Count:", config.led_count)
    print("LED Brightness:", config.led_brightness)

    config.write_to_file()
    print("Config written to file again (whithout modifications).")
