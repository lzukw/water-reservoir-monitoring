import logging
from dataclasses import dataclass
from typing import Any, Literal

import redis

__all__ = ["RedisClient", "RedisHash", "RedisStatus"]


@dataclass
class RedisHash:
    timestamp: str
    value: float

RedisStatus = Literal["not initialized", "connected", "disconnected", "error"]

class RedisClient:
    """Redis client wrapper for storing images."""

    def __init__(self, 
                 server: str, 
                 port: int, 
                 hashkey_timestamp : str = "timestamp",
                 hashkey_value: str = "value",
                 log_level: int = logging.INFO,):
        self._server = server
        self._port = port
        self._hashkey_timestamp = hashkey_timestamp
        self._hashkey_value = hashkey_value 
        self._client : redis.Redis | None = None
        
        self._logger : logging.Logger = logging.getLogger(f"{__name__}.RedisClient")
        self._logger.setLevel(log_level)

        self._status : RedisStatus = "not initialized"

    def _release(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    @property
    def status(self) -> RedisStatus:
        """Return the current status of the Redis client. In "connected"-state,
        first is checked if the connection is still alive, and if not, the status 
        is set to "disconnected"."""
        if self._status == "connected":
            try:
                assert self._client is not None
                success = self._client.ping()
                if not success:
                    raise redis.ConnectionError("Ping to Redis server failed")
            except (redis.ConnectionError, OSError) as e:
                self._logger.error(f"Lost connection to Redis server at {self._server}:{self._port}: {e}")
                self._release()
                self._status = "error"
        return self._status

    def connect(self):
        # self.status also checks if the connection is still alive, and if not, 
        # sets the status to "disconnected" (and also returns "disconnected").
        # If already connected, and the connection is still alive, don't do anything.
        if self.status == "connected":
            return
        try:
            assert self._client is None
            self._client = redis.Redis(host=self._server, port=self._port)
            success = self._client.ping()
            if success is True:
                assert self._status != "connected"
                self._logger.info(f"Connected to Redis server at {self._server}:{self._port}")
                self._status = "connected"
                return
        except (redis.ConnectionError, OSError) as e:
            if self._status != "error":
                self._logger.error(f"Could not connect to Redis server at {self._server}:{self._port}: {e}")
            self._release()
            self._status = "error"

    def disconnect(self):
        if self._status != "disconnected":
            self._logger.info(f"Disconnected from Redis server at {self._server}:{self._port}")
        self._release()
        self._status = "disconnected"

    def store_hash(self, key: str, timestamp_val: str, hash_value: Any):
        """Store a hash in Redis. The hash has two hashkeys: timestamp value."""
        if self._status != "connected":
            self._logger.debug(f"Cannot store hash in Redis key '{key}' at timestamp: '{timestamp_val}' because client is not connected. Connections-state='{self._status}'")
            return
        try:
            assert self._client is not None
            self._client.hset(
                key,
                mapping={
                    self._hashkey_timestamp: timestamp_val,
                    self._hashkey_value: hash_value,
                },
            )
            self._logger.debug(f"Stored hash in Redis key '{key}' at timestamp '{timestamp_val}'")
        except (redis.RedisError, OSError) as e:
            self._logger.error(f"Error storing hash in Redis key '{key}' at timestamp: '{timestamp_val}': {e}")
            self._release()
            self._status = "error"
            self.connect() # Attempt to reconnect after an error

    def retrieve_hash(self, key: str) -> tuple[str,Any] | None:
        """Retrieve the hash from Redis. Returns a dictionary with the hashkeys and their values, or None if the hash does not exist."""
        if self._status != "connected":
            self._logger.debug(f"Cannot retrieve hash from Redis key '{key}' because client is not connected. Connections-state='{self._status}'")
            return None
        try:
            assert self._client is not None
            result = self._client.hgetall(key)
            if not result:
                self._logger.debug(f"No hash found in Redis key '{key}'")
                return None

            assert isinstance(result, dict)
            timestamp_val = result.get(self._hashkey_timestamp.encode())
            hash_value = result.get(self._hashkey_value.encode())
            if timestamp_val is None or hash_value is None:
                self._logger.error(f"Hash in Redis key '{key}' is missing expected hashkeys '{self._hashkey_timestamp}' or '{self._hashkey_value}'")
                return None
            assert isinstance(timestamp_val, bytes)
            return (timestamp_val.decode(), hash_value)
  
        except (redis.RedisError, OSError) as e:
            self._logger.error(f"Error retrieving hash from Redis key '{key}': {e}")
            self._release()
            self._status = "error"
            self.connect() # Attempt to reconnect after an error
            return None