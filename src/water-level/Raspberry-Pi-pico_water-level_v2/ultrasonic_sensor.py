"""Ultrasonic distance sensor (HC-SR04 style) for the water level.

Works on MicroPython (machine.Pin + machine.time_pulse_us) and on CPython
(host tests) via injectable pin_factory / pulse_fn.

One measurement takes `samples` shots and returns the median (robust against
single outliers). Each shot blocks at most `echo_timeout_us` (default 50 ms),
so a full measurement blocks at most ~samples * echo_timeout_us (default
3 * 50 ms = 150 ms) - bounded, non-accumulating across the measurement loop.
"""

import time

from logger import Logger

SPEED_OF_SOUND_M_S = 343.2  # at ~20 deg C

DEFAULT_TRIGGER_PIN = 28
DEFAULT_ECHO_PIN = 27
DEFAULT_ECHO_TIMEOUT_US = 50000   # 50 ms -> max range ~8.5 m
DEFAULT_MIN_PULSE_US = 583         # ~10 cm minimum (filters mount echoes)
DEFAULT_SAMPLES = 3


def _sleep_us(us: int) -> None:
    if hasattr(time, "sleep_us"):
        time.sleep_us(us)
    else:
        time.sleep(us / 1_000_000.0)


def _default_pin_factory(pin, mode):
    import machine
    return machine.Pin(pin, machine.Pin.OUT if mode == "out" else machine.Pin.IN)


def _default_pulse_fn(pin, level, timeout_us):
    import machine
    return machine.time_pulse_us(pin, level, timeout_us)


class UltrasonicSensor:
    def __init__(self, trigger_pin: int = DEFAULT_TRIGGER_PIN,
                 echo_pin: int = DEFAULT_ECHO_PIN,
                 echo_timeout_us: int = DEFAULT_ECHO_TIMEOUT_US,
                 min_pulse_us: int = DEFAULT_MIN_PULSE_US,
                 samples: int = DEFAULT_SAMPLES,
                 pin_factory=None, pulse_fn=None,
                 log_level: str = "INFO"):
        self._echo_timeout_us = echo_timeout_us
        self._min_pulse_us = min_pulse_us
        self._samples = max(1, samples)
        self._logger = Logger("Sensor", log_level)
        pin_factory = pin_factory or _default_pin_factory
        pulse_fn = pulse_fn or _default_pulse_fn
        self._trig = pin_factory(trigger_pin, "out")
        self._trig.value(0)
        self._echo = pin_factory(echo_pin, "in")
        self._pulse_fn = pulse_fn
        self.last_distance = None  # meters, updated by measure_distance()

    def _one_shot(self):
        """Single trigger+echo. Returns distance in meters or None.
        Blocks at most echo_timeout_us."""
        self._trig.value(0)
        _sleep_us(2)
        self._trig.value(1)
        _sleep_us(10)   # 10 us trigger pulse
        self._trig.value(0)
        pulse = self._pulse_fn(self._echo, 1, self._echo_timeout_us)
        if pulse is None or pulse < 0:
            return None  # timeout (MicroPython returns -1)
        if not (self._min_pulse_us <= pulse < self._echo_timeout_us):
            self._logger.debug("echo pulse %d us out of valid range" % pulse)
            return None
        return pulse * SPEED_OF_SOUND_M_S / 2_000_000.0

    def measure_distance(self):
        """Median of `samples` shots, in meters. None if all shots failed.
        Blocks at most ~samples * echo_timeout_us. Updates last_distance."""
        vals = []
        for _ in range(self._samples):
            d = self._one_shot()
            if d is not None:
                vals.append(d)
            _sleep_us(1000)  # 1 ms gap between shots
        if not vals:
            self.last_distance = None
            self._logger.warning("all %d shots failed (timeout/out of range)" % self._samples)
            return None
        vals.sort()
        median = vals[len(vals) // 2]
        self.last_distance = median
        self._logger.debug("measured %.3f m (samples: %s)" % (median, ["%.3f" % v for v in vals]))
        return median

    def measure_water_level(self, distance_to_bottom):
        """Returns (distance_m, water_level_m); water_level is None if unknown."""
        d = self.measure_distance()
        if d is None or distance_to_bottom is None:
            return (d, None)
        return (d, distance_to_bottom - d)
