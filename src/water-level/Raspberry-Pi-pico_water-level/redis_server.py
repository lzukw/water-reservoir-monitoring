import asyncio
import gc

from picoredis import Redis, RedisError, RedisTimeout

from config import Config
from logger import Logger


class RedisServer:
    def __init__(self, config: Config, reconnect_count: int = 10, log_level: str = "INFO"):
        self._config = config
        self._logger = Logger(name="RedisServer", log_level=log_level)
        self._reconnect_count = reconnect_count
        self._status = "disconnected"
        self._redis : Redis | None = None

    @property
    def status(self) -> str:
        """Returns "initializing", "connected", or "disconnected"."""
        if self._status == "connected":
            assert self._redis is not None
            result = self._redis.do_cmd("PING")
            if result != b"PONG":
                self._status = "disconnected"
                self._redis = None
                gc.collect()
        return self._status

    async def connect(self) -> bool:
        self._status = "initializing"
        self._logger.log("INFO", f"Connecting to Redis server at {self._config.redis_server}:{self._config.redis_port}")
        redis_server = self._config.redis_server
        redis_port = self._config.redis_port
        if not redis_server or not redis_port:
            self._logger.log("ERROR", "Redis server or port not configured.")
            self._status = "disconnected"
            return False
        for attempt in range(self._reconnect_count):
            try:
                self._redis = Redis(host=redis_server, port=redis_port)
                result = self._redis.do_cmd("PING")
                if result == b"PONG":
                    self._status = "connected"
                    self._logger.log("INFO", f"Connected to Redis server at {self._config.redis_server}:{self._config.redis_port}.")
                    self._status = "connected"
                    return True
                else:
                    raise RedisError(f"Unexpected response from Redis server: {result}")
            except (RedisError, RedisTimeout, OSError) as e:
                self._logger.log("DEBUG", f"Attempt {attempt + 1}/{self._reconnect_count} failed: {e}")
                self._redis = None
                gc.collect()
                await asyncio.sleep(1)
        self._status = "disconnected"
        self._logger.log("ERROR", f"Failed to connect to Redis server at {self._config.redis_server}:{self._config.redis_port} after {self._reconnect_count} attempts.")
        return False
    
    async def connect_forever(self):
        while True:
            if self._status != "connected":
                success = await self.connect()
                if not success:
                    # Try more frequently if the connection fails
                    await asyncio.sleep(5)
                    continue
            await asyncio.sleep(60)  # Check connection status every 60 seconds

    def hset(self, key: str, hash: dict) -> bool:
        if self._status != "connected":
            return False
        try:
            fields_to_send = []
            for hashkey, hashvalue in hash.items():
                fields_to_send.append(hashkey)
                fields_to_send.append(hashvalue)
            # unpack the list of fields into the HSET command with *
            assert self._redis is not None
            self._redis.do_cmd("HSET", key, *fields_to_send)
            return True 
        except RedisError as e:
            self._logger.log("ERROR", f"Failed to set hash for key {key}: {e}")
            return False

if __name__ == "__main__":
    from network_time import NetworkTime
    from wifi import Wifi

    config = Config("config.json", log_level="INFO")
    config.read_from_file()

    ap_ssid_prefix = config.ap_ssid_prefix
    ap_password = config.ap_password
    assert ap_ssid_prefix is not None, "AP SSID prefix is not configured."
    assert ap_password is not None, "AP password is not configured."

    wifi = Wifi(ap_ssid_prefix=ap_ssid_prefix,
                ap_password=ap_password,
                station_ssid=config.station_ssid or "",
                station_password=config.station_password or "",
                log_level="INFO")

    network_time = NetworkTime(wifi=wifi, log_level="INFO")

    redis_server = RedisServer(config=config, log_level="DEBUG")

    async def main():
        asyncio.create_task(wifi.connect_forever_as_station_with_fallback_as_ap())
        asyncio.create_task(network_time.get_network_time_forever())
        asyncio.create_task(redis_server.connect_forever())

        redis_key = config.redis_key
        redis_hashkey_timestamp = config.redis_hashkey_timestamp
        redis_hashkey_value = config.redis_hashkey_value

        assert redis_key is not None, "Redis key is not configured."
        assert redis_hashkey_timestamp is not None, "Redis hash key for timestamp is not configured."
        assert redis_hashkey_value is not None, "Redis hash key for value is not configured."

        val: float = 0.0
        while True:
            await asyncio.sleep(1)
            success = redis_server.hset(redis_key, {
                redis_hashkey_timestamp: network_time.get_current_time_UTC_iso(),
                redis_hashkey_value: f"{val:.2f}"
                })
            if success:
                print(f"Successfully set hash for key {redis_key}: timestamp={network_time.get_current_time_UTC_iso()}, value={val:.2f}")
                val += 0.1

    asyncio.run(main())
        