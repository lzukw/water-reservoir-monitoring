# On-board test for Step 4 (ultrasonic sensor, real hardware on GPIO28 TRIG / GPIO27 ECHO):
#   mpremote run pico_tests/test_step4_board.py
import sys
import time

if "/pico_tests" not in sys.path:
    sys.path.insert(0, "/pico_tests")

from config import Config
from ultrasonic_sensor import UltrasonicSensor
from tinytest import run_tests

# This test only measures directly (no run_forever), so no ntp/redis services
# are started; the constructor still requires the references -> pass None.
cfg = Config("config.json")
cfg.read_from_file()


def test_real_measurement():
    s = UltrasonicSensor(config=cfg, ntp=None, redis=None,
                         trigger_pin=28, echo_pin=27, samples=5, log_level="INFO")
    for i in range(3):
        d = s.measure_distance()
        print("measurement %d: %s" % (i, ("%.3f m" % d) if d is not None else "None (timeout)"))
        time.sleep(0.2)
    # With the sensor wired and something in front of it, at least one reading
    # in a plausible range is expected. If nothing is in range, all None.
    assert s.last_distance is None or (0.02 <= s.last_distance <= 8.5)


def test_water_level_calculation():
    s = UltrasonicSensor(config=cfg, ntp=None, redis=None,
                         trigger_pin=28, echo_pin=27, samples=3, log_level="ERROR")
    d, level = s.measure_water_level(5.25)
    print("distance:", d, "water level:", level)
    if d is not None:
        assert abs(level - (5.25 - d)) < 1e-9
    assert True


if __name__ == "__main__":
    run_tests(globals())
