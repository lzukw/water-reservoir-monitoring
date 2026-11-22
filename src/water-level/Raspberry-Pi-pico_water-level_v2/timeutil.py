"""Portable millisecond clock helpers (MicroPython ticks vs CPython monotonic)."""

import time


def now_ms() -> int:
    if hasattr(time, "ticks_ms"):
        return time.ticks_ms()
    return int(time.monotonic() * 1000)


def elapsed_ms(t0: int) -> int:
    if hasattr(time, "ticks_diff"):
        return time.ticks_diff(now_ms(), t0)
    return int(time.monotonic() * 1000) - t0
