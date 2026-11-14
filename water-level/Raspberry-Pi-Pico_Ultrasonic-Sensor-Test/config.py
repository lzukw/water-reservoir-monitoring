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

        self._redis_server : str | None = None
        self._redis_port: int | None = None
        self._redis_key: str | None = None
        self._redis_hashkey_timestamp: str | None = None
        self._redis_hashkey_value: str | None = None

        self._distance_to_bottom: float | None = None

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
            self._redis_server = None
            self._redis_port = None
            self._redis_key = None
            self._redis_hashkey_timestamp = None
            self._redis_hashkey_value = None
            self._distance_to_bottom = None
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

        self._redis_server = config.get("redis_server")
        if self._redis_server is None:
            self._logger.log("ERROR", "key 'redis_server' missing in config-file")

        self._redis_port = config.get("redis_port")
        if self._redis_port is None:
            self._logger.log("ERROR", "key 'redis_port' missing in config-file")

        self._redis_key = config.get("redis_key")
        if self._redis_key is None:
            self._logger.log("ERROR", "key 'redis_key' missing in config-file")

        self._redis_hashkey_timestamp = config.get("redis_hashkey_timestamp")
        if self._redis_hashkey_timestamp is None:
            self._logger.log("ERROR", "key 'redis_hashkey_timestamp' missing in config-file")

        self._redis_hashkey_value = config.get("redis_hashkey_value")
        if self._redis_hashkey_value is None:
            self._logger.log("ERROR", "key 'redis_hashkey_value' missing in config-file")

        self._distance_to_bottom = config.get("distance_to_bottom")
        if self._distance_to_bottom is None:
            self._logger.log("ERROR", "key 'distance_to_bottom' missing in config-file")

        gc.collect()

    def write_to_file(self):
        config = {
            "hostname": self._hostname or "",
            "ap_ssid_prefix": self._ap_ssid_prefix or "",
            "ap_password": self._ap_password or "",
            "station_ssid": self._station_ssid or "",
            "station_password": self._station_password or "",
            "redis_server": self._redis_server or "",
            "redis_port": self._redis_port or 0,
            "redis_key": self._redis_key or "",
            "redis_hashkey_timestamp": self._redis_hashkey_timestamp or "",
            "redis_hashkey_value": self._redis_hashkey_value or "",
            "distance_to_bottom": self._distance_to_bottom or 0.0
        }
        try:
            with open(self._config_file, "w") as f:
                json.dump(config, f, separators=(',\n', ':')) # type: ignore #
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
    def redis_server(self) -> str | None:
        return self._redis_server

    @redis_server.setter
    def redis_server(self, value: str | None):
        self._redis_server = value
        self._config_status = "modified"    

    @property
    def redis_port(self) -> int | None:
        return self._redis_port

    @redis_port.setter
    def redis_port(self, value: int | None):
        self._redis_port = value
        self._config_status = "modified"    

    @property
    def redis_key(self) -> str | None:
        return self._redis_key

    @redis_key.setter
    def redis_key(self, value: str | None):
        self._redis_key = value
        self._config_status = "modified"

    @property
    def redis_hashkey_timestamp(self) -> str | None:
        return self._redis_hashkey_timestamp

    @redis_hashkey_timestamp.setter
    def redis_hashkey_timestamp(self, value: str | None):
        self._redis_hashkey_timestamp = value
        self._config_status = "modified"

    @property
    def redis_hashkey_value(self) -> str | None:
        return self._redis_hashkey_value

    @redis_hashkey_value.setter
    def redis_hashkey_value(self, value: str | None):
        self._redis_hashkey_value = value
        self._config_status = "modified"

    @property
    def distance_to_bottom(self) -> float | None:
        return self._distance_to_bottom

    @distance_to_bottom.setter
    def distance_to_bottom(self, value: float | None):
        self._distance_to_bottom = value
        self._config_status = "modified"


if __name__ == "__main__":
    config = Config("config.json", log_level="DEBUG")
    config.read_from_file()
    print("Hostname:", config.hostname)
    print("AP SSID Prefix:", config.ap_ssid_prefix)
    print("AP Password:", config.ap_password)
    print("Station SSID:", config.station_ssid)
    print("Station Password:", config.station_password)
    print("Redis Server:", config.redis_server)
    print("Redis Port:", config.redis_port)
    print("Redis Key:", config.redis_key)
    print("Redis Hashkey Timestamp:", config.redis_hashkey_timestamp)
    print("Redis Hashkey Value:", config.redis_hashkey_value)
    print("Distance to Bottom:", config.distance_to_bottom) 

    config.write_to_file()
    print("Config written to file again (whithout modifications).")
