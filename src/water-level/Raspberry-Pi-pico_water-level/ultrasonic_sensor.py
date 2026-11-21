import asyncio

from machine import Pin, time_pulse_us
from utime import sleep_us

from config import Config
from logger import Logger
from network_time import NetworkTime
from redis_server import RedisServer

DEFUALT_TRIGGER_PIN = 28
DEFAULT_ECHO_PIN = 27
DEFAULT_ECHO_TIMEOUT_US = 50000  # 50 millisecond
DEFAULT_SENSOR_LOOP_PERIOD = 5  # seconds


class UltrasonicSensor:
    def __init__(self, 
                 config: Config,
                 network_time: NetworkTime, 
                 redis_server: RedisServer, 
                 trigger_pin = DEFUALT_TRIGGER_PIN, 
                 echo_pin = DEFAULT_ECHO_PIN, 
                 echo_timeout_us : int =DEFAULT_ECHO_TIMEOUT_US, 
                 sensor_loop_period : int = DEFAULT_SENSOR_LOOP_PERIOD,
                 log_level: str = "INFO"):
        
        self._trigger_pin = Pin(trigger_pin, mode=Pin.OUT)
        self._trigger_pin.value(0)
        
        self._echo_pin = Pin(echo_pin, mode=Pin.IN)
        
        self._echo_timeout_us = echo_timeout_us
        self._sensor_loop_period = sensor_loop_period

        self._config = config
        self._redis_server = redis_server
        self._network_time = network_time
        
        self._logger = Logger(name="UltrasonicSensor", log_level=log_level)

    def measure_distance(self) -> float | None:
        """Measures the distance using the ultrasonic sensor.
        Returns the distance in meters, or None if the measurement failed.
        """
        # Send a 10us pulse to trigger the ultrasonic sensor
        self._trigger_pin.value(0)
        sleep_us(12)
        self._trigger_pin.value(1)
        sleep_us(12)
        self._trigger_pin.value(0)
        sleep_us(12)

        # Measure the duration of the echo pulse
        echo_pulse_duration : int = time_pulse_us(self._echo_pin, 1, self._echo_timeout_us)

        # 583 microseconds corresponds to 10 cm
        if 583<= echo_pulse_duration < self._echo_timeout_us:
            sensor_distance_in_m = echo_pulse_duration * 343.2 / 1_000_000 / 2
            self._logger.log("DEBUG", f"Sensorloop: Measured {sensor_distance_in_m:.2f} m")
            return sensor_distance_in_m
        else:
            self._logger.log("WARNING", f"Sensorloop: Got Echo-Pulse-duration {echo_pulse_duration}, indicating timeout, no object detected within range, or sensor malfunction.")
            return None

    def measure_and_publish_distance(self):
        """Measures the distance and publishes it to the Redis server."""
        distance = self.measure_distance()
        
        if distance is None:
            self._logger.log("INFO", "No distance measured. Skipping publishing to Redis server.")
            return
        if self._network_time.time_status == "not valid":
            self._logger.log("INFO", "Network time is not valid. Skipping publishing distance to Redis server.")
            return
        if self._redis_server.status != "connected":
            self._logger.log("INFO", "Redis server is not connected. Skipping publishing distance to Redis server.")
            return
        redis_key = self._config.redis_key
        redis_hashkey_timestamp = self._config.redis_hashkey_timestamp
        redis_hashkey_value = self._config.redis_hashkey_value
        if not redis_key or not redis_hashkey_timestamp or not redis_hashkey_value:
            self._logger.log("ERROR", "Redis key or hash keys are not set. Skipping publishing distance to Redis server.")
            return

        distance_to_botton = self._config.distance_to_bottom
        if distance_to_botton is None:
            self._logger.log("ERROR", "Distance to bottom is not set in config. Skipping publishing distance to Redis server.")
            return
        
        timestamp = self._network_time.get_current_time_UTC_iso()
        success = self._redis_server.hset(redis_key, {
            redis_hashkey_timestamp: timestamp,
            redis_hashkey_value: f"{distance_to_botton - distance:.2f}"
            })
        if success:
            self._logger.log("DEBUG", f"Published distance {distance:.2f} m to Redis server with timestamp {timestamp}.")
        else:
            self._logger.log("ERROR", "Failed to publish distance to Redis server.")    

    async def measure_and_publish_distance_forever(self):
        """Continuously measures the distance and publishes it to the Redis server."""
        while True:
            self.measure_and_publish_distance()
            await asyncio.sleep(self._sensor_loop_period)

if __name__ == "__main__":
    from wifi import Wifi
    config = Config("config.json", log_level="INFO")
    config.read_from_file()

    ap_ssid_prefix = config.ap_ssid_prefix or "PicoProbe"
    ap_password = config.ap_password or "raspberry"
    assert config.station_ssid is not None, "Station SSID must be set in config.json"
    assert config.station_password is not None, "Station Password must be set in config.json"

    wifi = Wifi(ap_ssid_prefix=ap_ssid_prefix,
                ap_password=ap_password,
                station_ssid=config.station_ssid or "",
                station_password=config.station_password or "",
                log_level="INFO")

    network_time = NetworkTime(wifi=wifi)

    redis_server = RedisServer(config=config)

    ultrasonic_sensor = UltrasonicSensor(config=config,
                                         network_time=network_time,
                                         redis_server=redis_server,
                                         log_level="DEBUG")

    async def main():
        asyncio.create_task(wifi.connect_ap_or_sta_forever())
        asyncio.create_task(network_time.get_network_time_forever())
        asyncio.create_task(redis_server.connect_forever())
        asyncio.create_task(ultrasonic_sensor.measure_and_publish_distance_forever())

        while True:
            await asyncio.sleep(1)

    asyncio.run(main())
