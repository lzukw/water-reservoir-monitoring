# Raspberry Pi Pico (2 W) — Water-Level Monitoring (v2, rewritten from scratch)

Measures the water level of a reservoir with an ultrasonic sensor (HC-SR04),
publishes the latest value to a Redis server, keeps the clock synced via NTP,
and serves a small website for configuration and status. Runs all of this as
parallel asyncio tasks and is built to survive outages: loss of WiFi, Redis or
NTP is tolerated and everything recovers automatically when the connection is
back.

## Architecture

```
main.py ──► WifiManager (wifi.py)        station first, AP fallback, auto-recovery
        ──► NetworkTimeService (ntp_time.py)   own NTP client (UDP), RTC sync, resync loop
        ──► RedisService (redis_client.py)     own RESP client, AUTH, auto-reconnect,
                                               latest-value-only publish (no buffering)
        ──► sensor_loop → UltrasonicSensor (ultrasonic_sensor.py)  median of N shots
        ──► WebApp (web_app.py + templates/)   config form + /status JSON + /status-page
        ──► watchdog: resets only if a core task dies or config was applied
```

- `config.py` / `config.json` — flat JSON config (unknown keys are preserved).
- `logger.py` — leveled logger; `timeutil.py` — portable ms clock.
- `lib/` — microdot + utemplate (3rd-party web framework).

### Robustness rules
- Every `*_forever` loop shields its own exceptions and never dies; the watchdog
  (`main.py`) resets the device only if a core task unexpectedly ends.
- Blocking I/O is deadline-bounded (one time budget per call, measured on the
  Pico: worst case ~3 s total, never accumulating across send/recv).
- Redis down → publish is skipped, latest value is kept and published on the
  next cycle after Redis returns. NTP down → time_status degrades and resyncs
  automatically. WiFi down → AP fallback; STA reconnects automatically.
- `main.py` resets on any unexpected fatal `Exception` (device never sits dead
  at the REPL). A deliberate Ctrl-C (developer/mpremote) drops to the REPL on
  purpose, to avoid reset loops with USB tools; field operation never sends
  Ctrl-C.

## Wiring (same as v1)

| Pico | HC-SR04 |
|---|---|
| VSYS (pin 39) | 5 V supply |
| GND (pin 38) | GND (supply) |
| 3.3 V (pin 36) | VCC |
| GPIO 28 (pin 34) | TRIG |
| GPIO 27 (pin 32) | ECHO |
| GND (pin 28) | GND |

## Config (`config.json`)

Keys: `hostname`, `ap_ssid_prefix`, `ap_password`, `station_ssid`,
`station_password`, `redis_server`, `redis_port`, `redis_key`,
`redis_hashkey_timestamp`, `redis_hashkey_value`, `distance_to_bottom` (m).
Optional: `redis_password` (sent as AUTH if set), `measurement_interval_s`
(default 5).

The web form at `http://<pico-ip>/` edits all of these; on change the device
restarts to apply. `/status` returns JSON, `/status-page` a live HTML page.

## Deploy to the Pico

```
./deploy.sh            # wipes Pico FS, copies all files, resets the board
```

Requires `mpremote` (set `MPREMOTE=/path/to/mpremote` to override).

## Tests

Two layers (MicroPython has no pytest):

- **Host (laptop), CPython venv:** `…/.venv/bin/python -m pytest` in this
  folder — 63 tests: logger, config, redis client/service (against a throwaway
  local redis-server subprocess, incl. kill/restart recovery, AUTH, and
  non-accumulating blocking-time bounds), NTP (against a fake UDP NTP server +
  fake RTC), ultrasonic sensor (fake pins), wifi state machine (fake WLAN:
  STA fail → AP, drop → recovery, etc.), web app (microdot on 127.0.0.1 queried
  with httpx).
- **On the board:** `mpremote run pico_tests/<test>.py` — plain-assert tests
  with `tinytest.py`: `syntax_check.py` compiles every project file on the
  Pico; `test_step1..5b_board.py` test config/logger, redis (real TCP to the
  laptop's redis), NTP (real sync from the router, RTC set), the real
  ultrasonic sensor, and wifi STA connect/recovery.

### Laptop-side redis for on-board tests

```
.venv/bin/python tests/redis_lab.py start   # redis on 127.0.0.1 + LAN IP, requirepass
.venv/bin/python tests/redis_lab.py stop
```

Then deploy `tests/config.pico.test.json` as the Pico's `config.json`
(it points `redis_server` at the laptop and sets `redis_password`; the project
`config.json` keeps the original values). **Note:** the laptop must stay on the
same router as the Pico; do not switch the laptop to the Pico's AP without
asking (it would cut the laptop off the internet).
