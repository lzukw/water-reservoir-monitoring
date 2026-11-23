"""Configuration handling for the LED-strip Pico.

Reads/writes a flat JSON file (config.json on the Pico filesystem).
Works on MicroPython and CPython. Unknown/extra keys are preserved, so the
web form can add new settings without losing them.

Same structure as the Config class of the water-level project
(src/water-level/Raspberry-Pi-pico_water-level/config.py): a flat _data
dict with generic get/set, a public status attribute and one property per
known key.
"""

import json

from logger import Logger

# Keys we know about, with type coercion + defaults used when a key is missing.
_KNOWN_KEYS = {
    "hostname": str,
    "ap_ssid_prefix": str,
    "ap_password": str,
    "station_ssid": str,
    "station_password": str,
    "led_count": int,
    "led_brightness": int,
}


class Config:
    def __init__(self, path: str = "config.json", log_level: str = "INFO"):
        self._path = path
        self._logger = Logger("Config", log_level)
        self._data = {}
        self.status = "not loaded"

    # ------------------------------------------------------------------ I/O
    def read_from_file(self) -> bool:
        """Load config from JSON file. Returns True on success."""
        try:
            with open(self._path, "r") as f:
                data = json.load(f)
        except (OSError, ValueError) as e:
            self._logger.error(f"Cannot read config file {self._path}: {e}")
            self._data = {}
            self.status = "not loaded"
            return False
        if not isinstance(data, dict):
            self._logger.error(f"Config file {self._path} does not contain a JSON object.")
            self._data = {}
            self.status = "not loaded"
            return False
        self._data = data
        self.status = "loaded"
        missing = [k for k in _KNOWN_KEYS if k not in data]
        if missing:
            self._logger.warning(f"Keys missing in config file: {', '.join(missing)}")
        return True

    def write_to_file(self) -> bool:
        """Write config back to the JSON file. Returns True on success."""
        try:
            with open(self._path, "w") as f:
                # NOTE: MicroPython's json.dump does not support indent=... (TypeError),
                # so we write compact JSON. Verified on-device (MicroPython v1.29).
                json.dump(self._data, f)
                f.write("\n")
        except (OSError, TypeError, ValueError) as e:
            self._logger.error(f"Cannot write config file {self._path}: {e}")
            return False
        self.status = "saved"
        return True

    # ------------------------------------------------------------- generic
    def get(self, key, default=None):
        """Generic getter; returns default if the key is missing or None."""
        value = self._data.get(key, default)
        return default if value is None else value

    def set(self, key, value) -> None:
        self._data[key] = value
        if self.status == "loaded":
            self.status = "modified"

    def as_dict(self) -> dict:
        return dict(self._data)

    # ----------------------------------------------------------- properties
    @property
    def hostname(self):
        return self.get("hostname")

    @hostname.setter
    def hostname(self, value):
        self.set("hostname", value)

    @property
    def ap_ssid_prefix(self):
        return self.get("ap_ssid_prefix")

    @ap_ssid_prefix.setter
    def ap_ssid_prefix(self, value):
        self.set("ap_ssid_prefix", value)

    @property
    def ap_password(self):
        return self.get("ap_password")

    @ap_password.setter
    def ap_password(self, value):
        self.set("ap_password", value)

    @property
    def station_ssid(self):
        return self.get("station_ssid")

    @station_ssid.setter
    def station_ssid(self, value):
        self.set("station_ssid", value)

    @property
    def station_password(self):
        return self.get("station_password")

    @station_password.setter
    def station_password(self, value):
        self.set("station_password", value)

    @property
    def led_count(self):
        return self.get("led_count", 0)

    @led_count.setter
    def led_count(self, value):
        self.set("led_count", int(value))

    @property
    def led_brightness(self):
        return self.get("led_brightness", 0)

    @led_brightness.setter
    def led_brightness(self, value):
        self.set("led_brightness", int(value))


if __name__ == "__main__":
    config = Config("config.json", log_level="DEBUG")
    config.read_from_file()
    print("status:", config.status)
    print("Hostname:", config.hostname)
    print("AP SSID Prefix:", config.ap_ssid_prefix)
    print("AP Password:", config.ap_password)
    print("Station SSID:", config.station_ssid)
    print("Station Password:", config.station_password)
    print("LED Count:", config.led_count)
    print("LED Brightness:", config.led_brightness)

    config.write_to_file()
    print("Config written to file again (without modifications).")
