# Raspberry Pi Pico for Ultrasonic-distance-measurement of water-level

This folder is a Raspberry Pi Pico project for VSCode.

## Instructions for Users

1. Power the Raspberry Pi Pico with 5V (e.g. USB-Micro cable and phone charger)

1. Connect to the Wi-Fi "RPi-Pico-Water-Level-XX:XX:XX:XX:XX:XX" (the XX are the MAC address of the Pico) with the password "1234567890"

1. Open `http://192.168.4.1` in the browser

1. Set a hostname (the Pico will then be visible under this name in the Fritzbox), the Client-Wi-Fi SSID and the Client-Wi-Fi password of the Fritzbox, and press the "Save and Apply" button. The Pico restarts and now connects as a client to the specified Wi-Fi. It then no longer provides the "RPi-Pico-LED-Strip-XX:XX:XX:XX:XX:XX" network itself, but connects to the specified Fritzbox instead.

   Only if the Raspberry Pi Pico cannot connect to this new Wi-Fi, it will provide the "RPi-Pico-LED-Strip-XX:XX:XX:XX:XX:XX" network again, and you can start over from the beginning.

1. If necessary, configure Redis-server, -port and -keys as needed.

1. For reconfiguration: On the Fritzbox >> Home Network >> Mesh, the Pico is visible after a successful connection. You can either click on it directly, or find its IP address and then connect in the web browser to `http://<ip-address-of-the-Raspberry-Pi-Pico>`

## Wiring

VSYS from the Pico (Pin No. 39) <-- Power-Supply 5V
GND  from the Pico (Pin No. 38) <-- Power-Supply GND
3.3V from the Pico (Pin No. 36) --> VCC of the Ultrasonic-Sensor
GPIO pin 28 on the Pico (Pin No. 34) --> TRIG of the Ultrasonic-Sensor
GPIO pin 27 on the Pico (Pin No. 32) --> ECHO of the Ultrasonic-Sensor
GND  from the Pico (Pin No. 28) --> GND of the Ultrasonic-Sensor

## Setting up the Raspberry Pi Pico

1. Hold down the BOOTSEL button and connect the Pico to the PC via a USB-micro cable, then release the button
1. The Pico registers as a USB device
1. Download the correct firmware. You can identify a Raspberry Pi Pico (W) 2 by the imprint on the board. An `H` in the version name makes no difference — it only means the headers are already soldered on:

   - [Firmware for the Raspberry Pi Pico W(H)](https://micropython.org/download/rp2-pico-w/rp2-pico-w-latest.uf2)
   - [Firmware for the Raspberry Pi Pico 2 W(H)](https://micropython.org/download/RPI_PICO2_W/RPI_PICO2_W-latest.uf2)

1. Copy the uf2 file onto the Raspberry Pi Pico, which acts like a "USB stick". After a few seconds the Pico restarts and MicroPython is installed

1. You could connect via picocom, but in VSCode the REPL opens immediately. (`picocom -b 115200 /dev/ttyACMO`; the baud rate probably doesn't matter — 9600 baud worked too, just leaving `-b xxx` away, works also).

1. Open the filesystem on the Pico with VSCode (button at the bottom "Toggle Mpy FS")

1. The following files must be present in the Pico filesystem:

   - Folder `lib` ...Contains slightly modified microdot and utemplate
   - Folder `templates` 
   - `boot.py`
   - `config.json`
   - `config.py`
   - `logger.py`
   - `main.py`
   - `microdot_app.py`
   - `network_time.py`
   - `redis_server.py`
   - `wifi.py`
  

## Microdot

Microdot is a Flask-inspired web server for the Raspberry Pi Pico.

- [Documentation on Read-the-docs](https://microdot.readthedocs.io/en/latest/)
- [GitHub page of Microdot](https://github.com/miguelgrinberg/microdot/tree/main/src/microdot)

**Note:** The Source-Code in `lib/microdot` and `lib/utemplate` had to be slightly changed: The mpremote can't deal wiht line-continuations using backslashes at the end of the line, so those were removed and parenthesis were used instead.
