import pytest

from ultrasonic_sensor import SPEED_OF_SOUND_M_S, UltrasonicSensor


class FakePin:
    def __init__(self, log):
        self.log = log
        self.val = 0

    def value(self, v=None):
        if v is None:
            return self.val
        self.val = v
        self.log.append(v)


def make_sensor(pulses, samples=3, min_pulse_us=583, echo_timeout_us=50000):
    """pulses: list of values returned by successive pulse_fn calls."""
    trig_log = []
    echo_log = []
    it = iter(pulses)

    def pin_factory(pin, mode):
        log = trig_log if mode == "out" else echo_log
        return FakePin(log)

    def pulse_fn(pin, level, timeout_us):
        assert level == 1
        assert timeout_us == echo_timeout_us
        return next(it)

    s = UltrasonicSensor(trigger_pin=28, echo_pin=27, echo_timeout_us=echo_timeout_us,
                         min_pulse_us=min_pulse_us, samples=samples,
                         pin_factory=pin_factory, pulse_fn=pulse_fn, log_level="ERROR")
    return s, trig_log


def us_for_distance(m):
    return m * 2_000_000.0 / SPEED_OF_SOUND_M_S


def test_distance_conversion():
    # 1.00 m -> pulse of us_for_distance(1.0) us
    s, _ = make_sensor([us_for_distance(1.0)] * 3, samples=3)
    d = s.measure_distance()
    assert d == pytest.approx(1.0, abs=1e-3)
    assert s.last_distance == pytest.approx(1.0, abs=1e-3)


def test_median_ignores_outlier():
    pulses = [us_for_distance(1.0), us_for_distance(9.9), us_for_distance(2.0)]
    s, _ = make_sensor(pulses, samples=3)
    assert s.measure_distance() == pytest.approx(2.0, abs=1e-3)  # median of [1.0, 9.9, 2.0]


def test_failed_shot_is_skipped():
    pulses = [-1, us_for_distance(2.0), us_for_distance(2.1)]  # -1 = timeout
    s, _ = make_sensor(pulses, samples=3)
    d = s.measure_distance()
    assert d == pytest.approx(2.1, abs=1e-3)  # upper median of [2.0, 2.1]


def test_median_of_three():
    pulses = [us_for_distance(1.0), us_for_distance(2.0), us_for_distance(3.0)]
    s, _ = make_sensor(pulses, samples=3)
    assert s.measure_distance() == pytest.approx(2.0, abs=1e-3)


def test_all_shots_timeout_returns_none():
    s, _ = make_sensor([-1, -1, -1], samples=3)
    assert s.measure_distance() is None
    assert s.last_distance is None


def test_pulse_below_min_is_rejected():
    s, _ = make_sensor([100, 100, 100], samples=3, min_pulse_us=583)  # 100us < 583us min
    assert s.measure_distance() is None


def test_pulse_at_timeout_is_rejected():
    s, _ = make_sensor([50000, 50000], samples=2, echo_timeout_us=50000)
    assert s.measure_distance() is None


def test_trigger_pulse_sequence():
    s, trig_log = make_sensor([us_for_distance(1.0)], samples=1)
    s.measure_distance()
    # must end with the 10us trigger high pulse: 0 ... 1, 0
    assert trig_log[-2:] == [1, 0]
    assert trig_log[0] == 0


def test_water_level():
    s, _ = make_sensor([us_for_distance(1.25)] * 3, samples=3)
    d, level = s.measure_water_level(5.25)
    assert d == pytest.approx(1.25, abs=1e-3)
    assert level == pytest.approx(4.0, abs=1e-3)


def test_water_level_unknown_when_no_distance():
    s, _ = make_sensor([-1], samples=1)
    d, level = s.measure_water_level(5.25)
    assert d is None and level is None
