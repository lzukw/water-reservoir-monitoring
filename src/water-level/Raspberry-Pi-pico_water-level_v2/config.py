"""Configuration handling for the water-level Pico.

Reads/writes a flat JSON file (config.json on the Pico filesystem).
Works on MicroPython and CPython. Unknown/extra keys are preserved, so the
web form can add new settings without losing them.
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
    "redis_server": str,
    "redis_port": int,
    "redis_key": str,
    "redis_hashkey_timestamp": str,
    "redis_hashkey_value": str,
    "distance_to_bottom": float,
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
    def redis_server(self):
        return self.get("redis_server")

    @redis_server.setter
    def redis_server(self, value):
        self.set("redis_server", value)

    @property
    def redis_port(self):
        return self.get("redis_port", 6379)

    @redis_port.setter
    def redis_port(self, value):
        self.set("redis_port", int(value))

    @property
    def redis_key(self):
        return self.get("redis_key")

    @redis_key.setter
    def redis_key(self, value):
        self.set("redis_key", value)

    @property
    def redis_hashkey_timestamp(self):
        return self.get("redis_hashkey_timestamp")

    @redis_hashkey_timestamp.setter
    def redis_hashkey_timestamp(self, value):
        self.set("redis_hashkey_timestamp", value)

    @property
    def redis_hashkey_value(self):
        return self.get("redis_hashkey_value")

    @redis_hashkey_value.setter
    def redis_hashkey_value(self, value):
        self.set("redis_hashkey_value", value)

    @property
    def distance_to_bottom(self):
        """Distance from the ultrasonic sensor to the bottom of the reservoir, in meters."""
        return self.get("distance_to_bottom")

    @distance_to_bottom.setter
    def distance_to_bottom(self, value):
        self.set("distance_to_bottom", float(value))
