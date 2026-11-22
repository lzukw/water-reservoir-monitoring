"""Ultrasonic distance sensor (HC-SR04 style) for the water level.

Runs on MicroPython (machine.Pin + machine.time_pulse_us + time.sleep_us);
on the CPython host tests the dummy `machine` module and the time.sleep_us
shim from tests/conftest.py provide the same API, and pin_factory / pulse_fn
can still be injected for fake hardware.

One measurement takes `samples` shots and returns the median (robust against
single outliers). Each shot blocks at most `echo_timeout_us` (default 50 ms),
so a full measurement blocks at most ~samples * echo_timeout_us (default
3 * 50 ms = 150 ms) - bounded, non-accumulating across the measurement loop.

run_forever() is the measurement loop: every `interval_s` it measures the
water level and publishes the latest value to redis (when redis is connected
and the NTP time has been synced at least once). It never raises.
"""

import asyncio
import gc
import time

import machine

from config import Config
from logger import Logger
from ntp_time import NetworkTimeService
from redis_client import RedisClientService

SPEED_OF_SOUND_M_S = 343.2  # at ~20 deg C

DEFAULT_TRIGGER_PIN = 28
DEFAULT_ECHO_PIN = 27
DEFAULT_ECHO_TIMEOUT_US = 50000   # 50 ms -> max range ~8.5 m
DEFAULT_MIN_PULSE_US = 583         # ~10 cm minimum (filters mount echoes)
DEFAULT_SAMPLES = 3
DEFAULT_INTERVAL_S = 5.0


class UltrasonicSensor:
    def __init__(self, 
                 config: Config,
                 ntp: NetworkTimeService,
                 redis: RedisClientService,
                 trigger_pin: int = DEFAULT_TRIGGER_PIN,
                 echo_pin: int = DEFAULT_ECHO_PIN,
                 echo_timeout_us: int = DEFAULT_ECHO_TIMEOUT_US,
                 min_pulse_us: int = DEFAULT_MIN_PULSE_US,
                 samples: int = DEFAULT_SAMPLES,
                 interval_s: float = DEFAULT_INTERVAL_S,
                 log_level: str = "INFO"):
        self._echo_timeout_us = echo_timeout_us
        self._min_pulse_us = min_pulse_us
        self._samples = max(1, samples)
        self._logger = Logger("Sensor", log_level)
        self._config = config
        self._ntp = ntp
        self._redis = redis
        self._interval_s = interval_s
        self._trig = machine.Pin(trigger_pin, machine.Pin.OUT)
        self._trig.value(0)
        self._echo = machine.Pin(echo_pin, machine.Pin.IN)
        self.last_distance = None  # meters, updated by measure_distance()

    def _one_shot(self):
        """Single trigger+echo. Returns distance in meters or None.
        Blocks at most echo_timeout_us."""
        self._trig.value(0)
        time.sleep_us(2)
        self._trig.value(1)
        time.sleep_us(10)   # 10 us trigger pulse
        self._trig.value(0)
        pulse = machine.time_pulse_us(self._echo, 1, self._echo_timeout_us)
        if pulse is None or pulse < 0:
            return None  # timeout (MicroPython returns -1)
        if not (self._min_pulse_us <= pulse < self._echo_timeout_us):
            self._logger.debug(f"echo pulse {pulse} us out of valid range")
            return None
        return pulse / 1_000_000.0* SPEED_OF_SOUND_M_S / 2

    def measure_distance(self):
        """Median of `samples` shots, in meters. None if all shots failed.
        Blocks at most ~samples * echo_timeout_us. Updates last_distance."""
        vals = []
        for _ in range(self._samples):
            d = self._one_shot()
            if d is not None:
                vals.append(d)
            time.sleep_us(1000)  # 1 ms gap between shots
        if not vals:
            self.last_distance = None
            self._logger.warning(f"all {self._samples} shots failed (timeout/out of range)")
            return None
        vals.sort()
        median = vals[len(vals) // 2]
        self.last_distance = median
        self._logger.debug(f"measured {median:.3f} m (samples: {', '.join([f'{v:.3f}' for v in vals])})")
        return median

    def measure_water_level(self, distance_to_bottom):
        """Returns (distance_m, water_level_m); water_level is None if unknown."""
        d = self.measure_distance()
        if d is None or distance_to_bottom is None:
            return (d, None)
        return (d, distance_to_bottom - d)

    async def run_forever(self) -> None:
        """Measure the water level every interval_s and publish the latest value
        to redis (when redis is connected and time has been synced at least once).
        Never raises. Requires config, ntp and redis from the constructor."""
        if self._config is None or self._ntp is None or self._redis is None:
            raise RuntimeError(
                "run_forever() needs config, ntp and redis - pass them to the constructor")
        while True:
            try:
                distance, level = self.measure_water_level(self._config.distance_to_bottom)
                if distance is not None and level is not None:
                    self._logger.debug(f"distance {distance:.2f} m -> water level {level:.2f} m")
                    if self._ntp.time_status != "not valid" and self._redis.status == "connected":
                        ok = self._redis.publish_hash(self._config.redis_key, {
                            self._config.redis_hashkey_timestamp: self._ntp.current_time_utc_iso(),
                            self._config.redis_hashkey_value: f"{level:.2f}",
                        })
                        if ok:
                            self._logger.debug(f"published water level to redis key '{self._config.redis_key}'")
                        else:
                            self._logger.warning("redis publish failed (latest value kept, will retry)")
                    else:
                        self._logger.info(f"skip publish: ntp={self._ntp.time_status} "
                                          f"redis={self._redis.status} (latest value kept)")
                else:
                    self._logger.warning("no valid distance measurement this cycle")
            except Exception as e:  # noqa: BLE001
                self._logger.error(f"sensor loop error: {e}")
            gc.collect()
            await asyncio.sleep(self._interval_s)
