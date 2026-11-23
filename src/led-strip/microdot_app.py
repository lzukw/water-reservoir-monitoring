"""Web interface (microdot) for the LED-strip Pico.

Same structure as the WebApp of the water-level project
(src/water-level/Raspberry-Pi-pico_water-level/web_app.py).

Routes:
  GET  /           configuration form (wifi AP/station, LED strip)
  POST /           validate + save config.json; if anything changed, the
                   on_apply callback is scheduled (main.py uses it to restart
                   the device so the new config takes effect)
  POST /test       apply the LED count/brightness from the form to the strip
                   immediately, without saving (live test), then redirect to /
  GET  /test       plain "ok" (connectivity check)
  GET  /status     JSON status of all subsystems (polled by the status page)
  GET  /status-page  HTML status page polling /status every second

All values rendered into HTML are escaped. Runs on MicroPython and CPython.
"""

import asyncio

from microdot import Microdot, Response
from microdot.utemplate import Template

from config import Config
from ledstrip import LEDStrip
from logger import Logger
from wifi import WifiManager

Template.initialize(template_dir="templates")

_FORM_FIELDS = ("hostname", "ap_ssid_prefix", "ap_password", "station_ssid",
                "station_password", "led_count", "led_brightness")


def escape_html(value) -> str:
    text = "" if value is None else str(value)
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&#39;"))


def clamp_int(value, min_value, max_value, fallback):
    try:
        n = int(value)
    except (TypeError, ValueError):
        return fallback
    return max(min_value, min(max_value, n))


class MicrodotApp:
    def __init__(self,
                 config: Config,
                 wifi: WifiManager,
                 led_strip: LEDStrip,
                 on_apply=None,
                 log_level: str = "INFO"):
        self._config = config
        self._wifi = wifi
        self._led_strip = led_strip
        self._on_apply = on_apply  # async callable, scheduled when config changed
        self._logger = Logger("Web", log_level)
        self._app = Microdot()
        self._register_routes()

    def get_app(self) -> Microdot:
        return self._app

    # ------------------------------------------------------------ helpers
    async def _render_config(self, message: str = ""):
        cfg = self._config
        html = Template("config.html").render(
            hostname=escape_html(cfg.hostname),
            ap_ssid_prefix=escape_html(cfg.ap_ssid_prefix),
            ap_password=escape_html(cfg.ap_password),
            station_ssid=escape_html(cfg.station_ssid),
            station_password=escape_html(cfg.station_password),
            led_count=escape_html(clamp_int(cfg.led_count, 0, 100, 0)),
            led_brightness=escape_html(clamp_int(cfg.led_brightness, 0, 255, 0)),
            message=escape_html(message),
        )
        return html, 200, {"Content-Type": "text/html"}

    def _status_dict(self) -> dict:
        return {
            "hostname": self._config.hostname or "",
            "wifi_status": self._wifi.status,
            "wifi_sta": self._wifi.status_sta,
            "wifi_ap": self._wifi.status_ap,
            "ap_ssid": self._wifi.ap_ssid,
            "ip": self._wifi.ip,
            "gateway": self._wifi.gateway,
            "dns": self._wifi.dns,
            "led_count": self._config.led_count,
            "led_brightness": self._config.led_brightness,
            "config_status": self._config.status,
        }

    # ------------------------------------------------------------- routes
    def _register_routes(self):
        app = self._app

        @app.get("/test")
        async def test(request):
            return "ok", 200, {"Content-Type": "text/plain"}

        @app.get("/")
        async def index(request):
            self._logger.info(f"GET / from {getattr(request, 'client_addr', '?')}")
            return await self._render_config()

        @app.post("/")
        async def save(request):
            form = request.form or {}
            missing = [f for f in _FORM_FIELDS if not str(form.get(f, "")).strip()]
            if missing:
                return await self._render_config(f"Error: missing fields: {', '.join(missing)}")

            old = {k: self._config.get(k) for k in
                   ("hostname", "ap_ssid_prefix", "ap_password", "station_ssid",
                    "station_password", "led_count", "led_brightness")}

            self._config.hostname = str(form.get("hostname")).strip()
            self._config.ap_ssid_prefix = str(form.get("ap_ssid_prefix")).strip()
            self._config.ap_password = str(form.get("ap_password"))
            self._config.station_ssid = str(form.get("station_ssid")).strip()
            self._config.station_password = str(form.get("station_password"))
            self._config.led_count = clamp_int(form.get("led_count"), 0, 100, 0)
            self._config.led_brightness = clamp_int(form.get("led_brightness"), 0, 255, 0)

            if not self._config.write_to_file():
                return await self._render_config("Error: could not write config.json")

            changed = any(old[k] != self._config.get(k) for k in old)
            if changed:
                self._logger.info("Configuration changed -> scheduling apply (restart).")
                if self._on_apply is not None:
                    asyncio.create_task(self._on_apply())
                return await self._render_config("Saved. Device restarts to apply the new configuration.")
            return await self._render_config("Saved (no changes).")

        @app.post("/test")
        async def test_led_strip(request):
            # live-test the LED settings from the form without saving them
            self._logger.info(f"POST /test from {getattr(request, 'client_addr', '?')}")
            form = request.form or {}
            led_count = clamp_int(form.get("led_count"), 0, 100, 0)
            led_brightness = clamp_int(form.get("led_brightness"), 0, 255, 0)
            self._led_strip.num_leds = led_count
            self._led_strip.brightness = led_brightness
            return Response.redirect("/", status_code=303)

        @app.get("/status")
        async def status(request):
            return self._status_dict(), 200, {"Content-Type": "application/json; charset=UTF-8"}

        @app.get("/status-page")
        async def status_page(request):
            html = Template("status_page.html").render()
            return html, 200, {"Content-Type": "text/html"}


if __name__ == "__main__":
    config = Config("config.json", log_level="INFO")
    config.read_from_file()

    wifi = WifiManager(
        ap_ssid_prefix=config.ap_ssid_prefix or "RPi-Pico-LED-Strip",
        ap_password=config.ap_password or "1234567890",
        station_ssid=config.station_ssid or "",
        station_password=config.station_password or "",
        log_level="INFO",
    )

    ledstrip = LEDStrip(
        num_leds=config.led_count or 0,
        brightness=config.led_brightness or 0,
        log_level="DEBUG",
    )

    microdot_app = MicrodotApp(config=config, wifi=wifi, led_strip=ledstrip)

    async def main():
        asyncio.create_task(wifi.run_forever())
        app = microdot_app.get_app()
        print("Starting web server on http://0.0.0.0:80")
        server_task = asyncio.create_task(app.start_server(host="0.0.0.0", port=80))

        await server_task
        print("Server stopped.")

    asyncio.run(main())
