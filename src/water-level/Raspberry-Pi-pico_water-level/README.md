# Raspberry Pi Pico (2 W) — Water-Level Monitoring (v2, rewritten from scratch)

Measures the water level of a reservoir with an ultrasonic sensor (HC-SR04 or its waterproof-version JSN-SR04T) and publishes the latest value to a Redis server.

Clock keeps synced via NTP, and a small website iis served for configuration and status. 

Runs all of this as parallel asyncio tasks and is built to survive outages: loss of WiFi, Redis or NTP is tolerated and everything recovers automatically when the connection is back.

## Setting Up the Raspberry Pi Pico

1. Hold down the BOOTSEL button and connect the Pico to your PC via USB micro cable, then release the button
1. The Pico registers as a USB device
1. Download the correct firmware. You can identify a Raspberry Pi Pico (W) 2 by the label on the board. An `H` in the version makes no difference — it only means the headers are already soldered on:

   - [Firmware for the Raspberry Pi Pico W(H)](https://micropython.org/download/rp2-pico-w/rp2-pico-w-latest.uf2)
   - [Firmware for the Raspberry Pi Pico 2 W(H)](https://micropython.org/download/RPI_PICO2_W/RPI_PICO2_W-latest.uf2)

1. Copy the uf2 file onto the Raspberry Pi Pico, which acts like a "USB stick". After a few seconds the Pico restarts and MicroPython is installed

1. You could connect via picocom, but in VSCode the REPL opens immediately. (`picocom -b 115200 /dev/ttyACMO`, the baud rate probably doesn't matter — 9600 baud also worked)

### Deploy to the Pico

Disconnect any serial monitor to the pico, then run:

```sh
cd /path/to/this/folder
bash ./deploy.sh --wipe-first
```

If the deploy.sh-script is run without `--wipe-first` only files that are not already synced will be synced to the board-filesystem. The script also has a `--dry-run` command-line-arg.

The script requires `mpremote` (set `MPREMOTE=/path/to/mpremote` to override).

If you want to deploay manually all files to the Pico (for example using Thonny), the following files an folders must be copied to the Pico's filesystem:

- Folder `lib` with contents (3rd-party libraries Microdot and utemplate)
- Folder `templates` (HTML templates for the web app)
- `boot.py`
- `config.json` (the configuration file)
- `config.py`
- `logger.py`
- `main.py`
- `ntp_time.py`
- `ping.py`
- `redis_client.py`
- `timeutil.py`
- `ultrasonic_sensor.py`
- `web_app.py`
- `wifi.py`

## Instructions for Users

1. Power the Raspberry Pi Pico with 5 V (VSYS pin 39 → 5 V, GND pin 38 → GND of the power supply — see Wiring below)

1. Connect to the Wi-Fi "RPi-Pico-Water-Level-XXXXXX" (the XXXXXX are the last 6 hex digits of the MAC address of the Pico) with password "1234567890"

1. Open `http://192.168.4.1` in your browser

1. Fill in the configuration form and press the "Save and apply" button:

   - Hostname (under which the Pico will then be visible in the Fritzbox)
   - Station SSID and Station password — the Wi-Fi of your router (Fritzbox)
   - Redis server, Redis port, Redis key and the two Redis hash-field names (timestamp / value) — where the measured water level is published
   - Distance from the ultrasonic sensor to the bottom of the reservoir in cm

   The Pico will restart and now connect as a client to the specified Wi-Fi. It will no longer provide the "RPi-Pico-Water-Level-XXXXXX" network itself, but instead connect to the specified router.

   Only if the Raspberry Pi Pico cannot connect to this new Wi-Fi will it provide the "RPi-Pico-Water-Level-XXXXXX" network again, and you can start over from the beginning.

1. For reconfiguration and status: In the Fritzbox >> Home Network >> Mesh, the Pico is visible after a successful connection. You should also change its hostname there. Then you can either click on it directly, or find its IP address and then connect in your web browser to `http://<ip-address-of-the-Raspberry-Pi-Pico>` — the configuration form is shown again, the live status page is at `http://<ip-address-of-the-Raspberry-Pi-Pico>/status-page` and the raw status as JSON at `http://<ip-address-of-the-Raspberry-Pi-Pico>/status`

## Architecture

```
main.py ──► WifiManager (wifi.py)        station first, AP fallback, auto-recovery
        ──► NetworkTimeService (ntp_time.py)   own NTP client (UDP), RTC sync, resync loop
        ──► RedisClientService (redis_client.py)     own RESP client, AUTH, auto-reconnect,
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

| Pico | Power-supply |
|---|---|
| VSYS (pin 39) | 5 V supply |
| GND (pin 38) | GND supply |


| Pico | JSN-SR04T |
|---|---|
| 3.3 V (pin 36) | VCC |
| GPIO 28 (pin 34) | TRIG |
| GPIO 27 (pin 32) | ECHO |
| GND (pin 28) | GND |

## Config (`config.json`)

Keys: `hostname`, `ap_ssid_prefix`, `ap_password`, `station_ssid`,
`station_password`, `redis_server`, `redis_port`, `redis_key`,
`redis_hashkey_timestamp`, `redis_hashkey_value`, `distance_to_bottom` (m).

Not implemented yet: `redis_password` (sent as AUTH if set), `measurement_interval_s`
(default 5).

The web form at `http://<pico-ip>/` edits all of these; on change the device
restarts to apply. `/status` returns JSON, `/status-page` a live HTML page.

## Tests

### Manual tests

The tests in `manual_pico_tests/` have been developed without AI and were used to find blocking-situations when Wifi-Client or Redis-Connection is lost and Wifi switches from Client- (Station-) to Access-Point-mode. These tests can still be used for testing. Each test prints out instructions on how to perform the manual test-sequence. Use them in the order given by the file-numbers (This also was the order, in which the project was developed and complexity was increased step by step)

### Tests used by the AI during development

The following tests were created and used by AI during development and have not been reviewed manually.

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

**Laptop-side redis for on-board tests:**s

```
.venv/bin/python tests/redis_lab.py start   # redis on 127.0.0.1 + LAN IP, requirepass
.venv/bin/python tests/redis_lab.py stop
```

Then deploy `tests/config.pico.test.json` as the Pico's `config.json`
(it points `redis_server` at the laptop and sets `redis_password`; the project
`config.json` keeps the original values). **Note:** the laptop must stay on the
same router as the Pico; do not switch the laptop to the Pico's AP without
asking (it would cut the laptop off the internet).
