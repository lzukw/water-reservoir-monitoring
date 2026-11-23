# Raspberry Pi Pico for LED Strips

This folder is a Raspberry Pi Pico project for VSCode.

## Instructions for Users

1. Power the Raspberry Pi Pico with 5V (e.g., USB micro cable and phone charger)

1. Connect to the Wi-Fi "RPi-Pico-LED-Strip-XX:XX:XX:XX:XX:XX" (the XX are the MAC address of the Pico) with password "1234567890"

1. Open `http://192.168.4.1` in your browser

1. Set the number of LEDs and brightness (0..255) and test with the Test button

1. Set a hostname (under which the Pico will then be visible in the Fritzbox), the Wi-Fi SSID, and the Wi-Fi password of the Fritzbox, then press the "Save and Apply" button. The Pico will restart and now connect as a client to the specified Wi-Fi. It will no longer provide the "RPi-Pico-LED-Strip-XX:XX:XX:XX:XX:XX" network itself, but instead connect to the specified Fritzbox.

   Only if the Raspberry Pi Pico cannot connect to this new Wi-Fi will it provide the "RPi-Pico-LED-Strip-XX:XX:XX:XX:XX:XX" network again, and you can start over from the beginning.

1. For reconfiguration: In the Fritzbox >> Home Network >> Mesh, the Pico is visible after a successful connection You should also change its hostname there. Then you can either click on it directly, or find its IP address and then connect in your web browser to `http://<ip-address-of-the-Raspberry-Pi-Pico>`

## Wiring

Power-Supply via the USB-Micro-Plug (no Data-lines are necessary, only red and black wire for the power-supply must be connected in the USB-micro-cable).

VSYS from the Pico (pin no. 39) --> +5V of the LED strip
GND  from the Pico (pin no. 38) --> GND of the LED strip
GPIO pin 28 on the Pico (pin no. 34) --> signal of the RGBW LED strip

## Setting Up the Raspberry Pi Pico

1. Hold down the BOOTSEL button and connect the Pico to your PC via USB micro cable, then release the button
1. The Pico registers as a USB device
1. Download the correct firmware. You can identify a Raspberry Pi Pico (W) 2 by the label on the board. An `H` in the version makes no difference — it only means the headers are already soldered on:

   - [Firmware for the Raspberry Pi Pico W(H)](https://micropython.org/download/rp2-pico-w/rp2-pico-w-latest.uf2)
   - [Firmware for the Raspberry Pi Pico 2 W(H)](https://micropython.org/download/RPI_PICO2_W/RPI_PICO2_W-latest.uf2)

1. Copy the uf2 file onto the Raspberry Pi Pico, which acts like a "USB stick". After a few seconds the Pico restarts and MicroPython is installed

1. You could connect via picocom, but in VSCode the REPL opens immediately. (`picocom -b 115200 /dev/ttyACMO`, the baud rate probably doesn't matter — 9600 baud also worked)

1. Open the filesystem on the Pico with VSCode (button at the bottom "Toggle Mpy FS")

You can deploy the files with the deploy.sh-script:

```sh
cd /path/to/this/folder
bash ./deploy.sh --wipe-first
```

If the deploy.sh-script is run without `--wipe-first` only files that are not already synced will be synced to the board-filesystem. The script also has a `--dry-run` command-line-arg.

The script requires `mpremote` (set `MPREMOTE=/path/to/mpremote` to override).

You can also deploy manually all files to the Pico (for example using Thonny). The following files an folders must be copied to the Pico's filesystem:

- Folder `lib` with contents (3rd-party libraries Microdot and utemplate)
- Folder `templates` (HTML templates for the web app)
- `boot.py`
- `config.json` (the configuration file)
- `config.py`
- `ledstrip.py`
- `logger.py`
- `main.py`
- `microdot_app.py`
- `ping.py`
- `timeutil.py`
- `wifi.py`

# Development-Documentation

## Microdot

Microdot is a Flask-inspired web server for the Raspberry Pi Pico.

- [Documentation on Read the Docs](https://microdot.readthedocs.io/en/latest/)
- [GitHub page of Microdot](https://github.com/miguelgrinberg/microdot/tree/main/src/microdot)

## Microdot Installation

To install Microdot with an extension (see [Microdot ReadTheDocs - Installation](https://microdot.readthedocs.io/en/latest/intro.html)), it is sufficient to create a folder `lib/microdot` on the Raspberry Pi Pico and place the files `__init__.py`, `microdot.py`, and the respective extension file (here `utemplate.py`) in it.

However, `utemplate.py` has an external dependency on a package of the same name, `utemplate`. This is included as a whole folder in the Git repository. This folder must be copied with its contents to the Raspberry Pi Pico filesystem (also into `lib/`).

## Differences between Flask and Microdot

Handler functions decorated with `@app.route` should be `async`:

```python
@app.route('/')
async def index(request):
    return _render_config_page()
```

The default return value is `Content-Type: text/plain`. Therefore one should return a tuple with three values:

```python
    return "<p>HTML page</p>", 200, {"Content-Type": "text/html"}
```

## Working with Templates

```python
from microdot.utemplate import Template

rendered = Template("config.html").render(val1="value1", val2=3, ...)
```

The template file `config.html` must be in a folder `templates` and contain placeholders: `{{ val1 }}`, `{{ val2 }}`, etc.

The return value of `Template("template-file.html").render(...)` is a str containing the rendered HTML text.

## Parallel processing with async

On the [Microdot Read the Docs page](microdot.readthedocs.io/en/latest/users-guide/intro.html) it is briefly explained how to run the web server in parallel with other asynchronous tasks. The server shutdown is no longer used; instead, after a configuration change a new task is started that resets the machine after a 2-second wait: machine.reset().

A link to the MicroPython async documentation is also provided:

- [MicroPython asyncio](https://docs.micropython.org/en/latest/library/asyncio.html)

It is important that async functions never block the event loop. A problem are for example network requests that run into a timeout. Therefore, the `ping.py` module was created, which implements a ping function with a timeout. The ping function is used to check whether the Wi-Fi connection is still working by pinging the gateway (router). In the water-level-project the ping function is also used to check whether the Redis server is still reachable before trying to send the measured water level to it.
