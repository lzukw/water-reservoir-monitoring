import os
import sys
import types

# Make the Pico project files (logger.py, config.py, ...) importable on the host.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
# microdot / utemplate live in lib/
LIB = os.path.join(PROJECT_ROOT, "lib")
if LIB not in sys.path:
    sys.path.insert(0, LIB)

# ---------------------------------------------------------------------------
# Dummy MicroPython-only modules for CPython host tests. Installed into
# sys.modules BEFORE any test module imports wifi.py (which does
# `import network` at module level). On the Pico these come from firmware.
# ---------------------------------------------------------------------------
if "network" not in sys.modules:  # CPython has no `network` module
    _network = types.ModuleType("network")
    _network.STA_IF = 0
    _network.AP_IF = 1
    # WLAN link status codes (same values as MicroPython's network module)
    _network.STAT_IDLE = 0
    _network.STAT_CONNECTING = 1
    _network.STAT_WRONG_PASSWORD = 2
    _network.STAT_NO_AP_FOUND = 3
    _network.STAT_CONNECT_FAIL = 4
    _network.STAT_GOT_IP = 5
    _wlan_registry = {}

    def _wlan(if_id):
        """Stand-in for network.WLAN(if_id): returns the FakeWLAN that the
        test registered for this interface via register_wlan()."""
        return _wlan_registry[if_id]

    _network.WLAN = _wlan
    _network.register_wlan = lambda if_id, wlan: _wlan_registry.__setitem__(if_id, wlan)
    sys.modules["network"] = _network

if "machine" not in sys.modules:  # CPython has no `machine` module
    import time as _time

    _machine = types.ModuleType("machine")

    class ResetCalled(BaseException):
        """Raised by the dummy machine.reset() - a real reset never returns,
        so this simulates the reboot (BaseException: not caught by the
        wifi loop's `except Exception` shield)."""

    _machine.ResetCalled = ResetCalled
    _machine.reset_calls = 0

    def _reset():
        _machine.reset_calls += 1
        raise ResetCalled("simulated machine.reset()")

    _machine.reset = _reset

    class _DummyRTC:
        """Stand-in for machine.RTC() on CPython host tests: stores the
        datetime tuple in `.dt`, cannot set the system clock. datetime()
        with no argument returns the stored tuple or the current UTC time."""

        def __init__(self):
            self.dt = None

        def datetime(self, dt=None):
            if dt is None:
                if self.dt is not None:
                    return self.dt
                t = _time.gmtime()
                return (t[0], t[1], t[2], t[6], t[3], t[4], t[5], 0)
            self.dt = tuple(dt)

    # like on the Pico, machine.RTC() is a singleton
    _rtc_singleton = _DummyRTC()
    _machine.RTC = lambda: _rtc_singleton
    sys.modules["machine"] = _machine
