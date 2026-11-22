import asyncio
import itertools

import machine  # dummy module from tests/conftest.py (CPython host)
import pytest

from config import Config
from ultrasonic_sensor import SPEED_OF_SOUND_M_S, UltrasonicSensor


@pytest.fixture
def make_sensor(monkeypatch):
    """Factory: make_sensor(pulses, samples=..., ...) -> (sensor, trig_log).

    ultrasonic_sensor.py uses machine.Pin / machine.time_pulse_us directly,
    so the fake hardware is injected by patching the dummy `machine` module:
    machine.Pin -> logging FakePin, machine.time_pulse_us -> successive
    values of `pulses` (use itertools.repeat(...) for an endless source).
    config/ntp/redis are required constructor args; they default to None
    here (fine for pure measurement tests) and can be overridden via kwargs.
    """
    def _make(pulses, samples=3, min_pulse_us=583, echo_timeout_us=50000, **kwargs):
        kwargs.setdefault("config", None)
        kwargs.setdefault("ntp", None)
        kwargs.setdefault("redis", None)
        trig_log = []
        echo_log = []
        pulses_iter = iter(pulses)

        class FakePin:
            OUT = 1
            IN = 0

            def __init__(self, pin, mode):
                self.pin = pin
                self.val = 0
                self.log = trig_log if mode == FakePin.OUT else echo_log

            def value(self, v=None):
                if v is None:
                    return self.val
                self.val = v
                self.log.append(v)

        def fake_time_pulse_us(pin, level, timeout_us):
            assert level == 1
            assert timeout_us == echo_timeout_us
            return next(pulses_iter)

        monkeypatch.setattr(machine, "Pin", FakePin)
        monkeypatch.setattr(machine, "time_pulse_us", fake_time_pulse_us)

        s = UltrasonicSensor(trigger_pin=28, echo_pin=27,
                             echo_timeout_us=echo_timeout_us,
                             min_pulse_us=min_pulse_us, samples=samples,
                             log_level="ERROR", **kwargs)
        return s, trig_log
    return _make


def us_for_distance(m):
    return m * 2_000_000.0 / SPEED_OF_SOUND_M_S


# ------------------------------------------------------------ measurement
def test_distance_conversion(make_sensor):
    # 1.00 m -> pulse of us_for_distance(1.0) us
    s, _ = make_sensor([us_for_distance(1.0)] * 3, samples=3)
    d = s.measure_distance()
    assert d == pytest.approx(1.0, abs=1e-3)
    assert s.last_distance == pytest.approx(1.0, abs=1e-3)


def test_median_ignores_outlier(make_sensor):
    pulses = [us_for_distance(1.0), us_for_distance(9.9), us_for_distance(2.0)]
    s, _ = make_sensor(pulses, samples=3)
    assert s.measure_distance() == pytest.approx(2.0, abs=1e-3)  # median of [1.0, 9.9, 2.0]


def test_failed_shot_is_skipped(make_sensor):
    pulses = [-1, us_for_distance(2.0), us_for_distance(2.1)]  # -1 = timeout
    s, _ = make_sensor(pulses, samples=3)
    d = s.measure_distance()
    assert d == pytest.approx(2.1, abs=1e-3)  # upper median of [2.0, 2.1]


def test_median_of_three(make_sensor):
    pulses = [us_for_distance(1.0), us_for_distance(2.0), us_for_distance(3.0)]
    s, _ = make_sensor(pulses, samples=3)
    assert s.measure_distance() == pytest.approx(2.0, abs=1e-3)


def test_all_shots_timeout_returns_none(make_sensor):
    s, _ = make_sensor([-1, -1, -1], samples=3)
    assert s.measure_distance() is None
    assert s.last_distance is None


def test_pulse_below_min_is_rejected(make_sensor):
    s, _ = make_sensor([100, 100, 100], samples=3, min_pulse_us=583)  # 100us < 583us min
    assert s.measure_distance() is None


def test_pulse_at_timeout_is_rejected(make_sensor):
    s, _ = make_sensor([50000, 50000], samples=2, echo_timeout_us=50000)
    assert s.measure_distance() is None


def test_trigger_pulse_sequence(make_sensor):
    s, trig_log = make_sensor([us_for_distance(1.0)], samples=1)
    s.measure_distance()
    # must end with the 10us trigger high pulse: 0 ... 1, 0
    assert trig_log[-2:] == [1, 0]
    assert trig_log[0] == 0


def test_water_level(make_sensor):
    s, _ = make_sensor([us_for_distance(1.25)] * 3, samples=3)
    d, level = s.measure_water_level(5.25)
    assert d == pytest.approx(1.25, abs=1e-3)
    assert level == pytest.approx(4.0, abs=1e-3)


def test_water_level_unknown_when_no_distance(make_sensor):
    s, _ = make_sensor([-1], samples=1)
    d, level = s.measure_water_level(5.25)
    assert d is None and level is None


# ------------------------------------------------------------- run_forever
class FakeNtp:
    def __init__(self, time_status="accurate"):
        self.time_status = time_status

    def current_time_utc_iso(self):
        return "2026-10-05T12:00:00.000Z"


class FakeRedis:
    def __init__(self, status="connected", publish_ok=True):
        self.status = status
        self.publish_ok = publish_ok
        self.published = []

    def publish_hash(self, key, mapping):
        self.published.append((key, dict(mapping)))
        return self.publish_ok


def make_config():
    cfg = Config("none.json", log_level="ERROR")
    cfg.set("distance_to_bottom", 5.0)
    cfg.set("redis_key", "waterlevel")
    cfg.set("redis_hashkey_timestamp", "ts")
    cfg.set("redis_hashkey_value", "level")
    return cfg


def run_loop_for(sensor, seconds):
    """Run sensor.run_forever() for `seconds`, then cancel it. Never raises."""
    async def scenario():
        task = asyncio.create_task(sensor.run_forever())
        await asyncio.sleep(seconds)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return task
    return asyncio.run(scenario())


def test_constructor_requires_config_ntp_redis():
    # config, ntp and redis are mandatory constructor arguments now
    with pytest.raises(TypeError):
        UltrasonicSensor()


def test_run_forever_requires_wiring(make_sensor):
    # constructed with config=None/ntp=None/redis=None (fixture defaults):
    # run_forever must fail fast with a clear error
    s, _ = make_sensor(itertools.repeat(us_for_distance(1.0)))
    with pytest.raises(RuntimeError):
        asyncio.run(s.run_forever())


def test_run_forever_publishes_water_level(make_sensor):
    s, _ = make_sensor(itertools.repeat(us_for_distance(1.0)),  # 1.00 m echo
                       config=make_config(), ntp=FakeNtp("accurate"),
                       redis=(redis := FakeRedis("connected")), interval_s=0.01)
    run_loop_for(s, 0.08)
    assert len(redis.published) >= 2
    key, mapping = redis.published[0]
    assert key == "waterlevel"
    # distance_to_bottom 5.0 m - measured 1.0 m = water level 4.0 m
    assert mapping == {"ts": "2026-10-05T12:00:00.000Z", "level": "4.00"}
    assert s.last_distance == pytest.approx(1.0, abs=1e-3)


def test_run_forever_skips_publish_when_ntp_not_valid(make_sensor):
    redis = FakeRedis("connected")
    s, _ = make_sensor(itertools.repeat(us_for_distance(1.0)),
                       config=make_config(), ntp=FakeNtp("not valid"),
                       redis=redis, interval_s=0.01)
    run_loop_for(s, 0.05)
    assert redis.published == []  # measured, but not published
    assert s.last_distance == pytest.approx(1.0, abs=1e-3)


def test_run_forever_skips_publish_when_redis_disconnected(make_sensor):
    redis = FakeRedis("disconnected")
    s, _ = make_sensor(itertools.repeat(us_for_distance(1.0)),
                       config=make_config(), ntp=FakeNtp("accurate"),
                       redis=redis, interval_s=0.01)
    run_loop_for(s, 0.05)
    assert redis.published == []


def test_run_forever_survives_publish_failure(make_sensor):
    redis = FakeRedis("connected", publish_ok=False)
    s, _ = make_sensor(itertools.repeat(us_for_distance(1.0)),
                       config=make_config(), ntp=FakeNtp("accurate"),
                       redis=redis, interval_s=0.01)
    task = run_loop_for(s, 0.05)
    assert task.cancelled()  # loop kept running despite the failing publishes
    assert len(redis.published) >= 1  # attempts were made


def test_run_forever_never_raises(make_sensor):
    def boom():
        raise OSError("no echo hardware")
        yield
    s, _ = make_sensor(boom(), config=make_config(), ntp=FakeNtp(),
                       redis=FakeRedis(), interval_s=0.01)
    task = run_loop_for(s, 0.05)
    # the loop shielded the exceptions and was still alive when we cancelled it
    assert task.cancelled()
