import asyncio

from config import Config
from ledstrip import LEDStrip
from logger import Logger
from microdot import Microdot, Response
from microdot.utemplate import Template
from wifi import Wifi

Template.initialize(template_dir="templates")


def escape_html(value) -> str:
    text = str(value)
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def clamp_int(value, min_value: int, max_value: int, fallback: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return fallback

    if number < min_value:
        return min_value
    if number > max_value:
        return max_value
    return number


class MicrodotApp:
    def __init__(self, config: Config, wifi: Wifi, led_strip: LEDStrip):
        self._config: Config = config
        self._wifi: Wifi = wifi
        self._led_strip: LEDStrip = led_strip

        self._app = Microdot()
        self._logger = Logger(name="Web", log_level="INFO")
        self._register_routes()

    def get_app(self) -> Microdot:
        return self._app

    async def _delayed_shutdown(self, delay_seconds: int = 1):
        self._logger.log(
            "WARNING", f"Delaying shutdown of Webserver for {delay_seconds} seconds..."
        )
        await asyncio.sleep(delay_seconds)
        self._app.shutdown()

    async def _render_config_page(self, message=""):
        # global config ...only accessing global variable, not modifying it
        # global sensor_distance_in_m ...only accessing global variable, not modifying it
        hostname = self._config.hostname
        if hostname == "":
            hostname = "water-level-" + self._wifi.get_mac_address(separator="")

        return (
            Template("config.html").render(
                hostname=escape_html(hostname),
                ap_ssid_prefix=escape_html(self._config.ap_ssid_prefix),
                ap_password=escape_html(self._config.ap_password),
                station_ssid=escape_html(self._config.station_ssid),
                station_password=escape_html(self._config.station_password),
                led_count=clamp_int(self._config.led_count, 0, 100, 0),
                led_brightness=clamp_int(self._config.led_brightness, 0, 255, 0),
                message=escape_html(message),
            ),
            200,
            {"Content-Type": "text/html"},
        )

    def _register_routes(self):
        app = self._app

        @app.route("/test")
        async def test(request):
            return "<p>Test</p>", 200, {"Content-Type": "text/html"}

        @app.get("/")
        async def index(request):
            # global config ...only accessing global variable, not modifying it
            # global sensor_distance_in_m ...only accessing global variable, not modifying it
            self._logger.log("INFO", f"GET-Request to / from {request.client_addr}")
            return await self._render_config_page()

        @app.post("/")
        async def save_and_apply(request):
            self._logger.log("INFO", f"POST-Request to / from {request.client_addr}")
            old_hostname = self._config.hostname
            old_ap_ssid_prefix = self._config.ap_ssid_prefix
            old_ap_password = self._config.ap_password
            old_station_ssid = self._config.station_ssid
            old_station_password = self._config.station_password
            form = request.form or {}
            if not form.get("hostname"):
                return await self._render_config_page(
                    "Error: Hostname cannot be empty."
                )
            if not form.get("ap_ssid_prefix") or not form.get("ap_password"):
                return await self._render_config_page(
                    "Error: SSID and Password for Access-Point-Mode cannot be empty."
                )
            if not form.get("station_ssid") or not form.get("station_password"):
                return await self._render_config_page(
                    "Error: SSID and Password for Client-Mode cannot be empty."
                )
            if not form.get("led_count") or not form.get("led_brightness"):
                return await self._render_config_page(
                    "Error: LED Count and LED Brightness cannot be empty."
                )

            self._config.hostname = form.get("hostname")
            self._config.ap_ssid_prefix = form.get("ap_ssid_prefix")
            self._config.ap_password = form.get("ap_password")
            self._config.station_ssid = form.get("station_ssid")
            self._config.station_password = form.get("station_password")
            self._config.led_count = clamp_int(form.get("led_count"), 0, 100, 0)
            self._config.led_brightness = clamp_int(
                form.get("led_brightness"), 0, 255, 0
            )

            self._config.write_to_file()
            self._led_strip.num_leds = self._config.led_count
            self._led_strip.brightness = self._config.led_brightness

            if (
                old_hostname != self._config.hostname
                or old_ap_ssid_prefix != self._config.ap_ssid_prefix
                or old_ap_password != self._config.ap_password
                or old_station_ssid != self._config.station_ssid
                or old_station_password != self._config.station_password
                or old_station_password != self._config.station_password
            ):
                self._logger.log(
                    "INFO",
                    "Wi-Fi credentials or Redis settings changed. Device will reset to apply new settings.",
                )
                asyncio.create_task(self._delayed_shutdown())
                return await self._render_config_page(
                    "Configuration saved. Device will reset to apply new settings."
                )
            else:
                return await self._render_config_page(
                    "Configuration saved. Wi-Fi credentials did not change."
                )

        @app.post("/test")
        async def test_led_strip(request):
            self._logger.log(
                "INFO", f"POST-Request to /test from {request.client_addr}"
            )
            form = request.form or {}
            if not form.get("led_count") or not form.get("led_brightness"):
                return await self._render_config_page(
                    "Error: LED Count and LED Brightness cannot be empty."
                )

            led_count = clamp_int(form.get("led_count"), 0, 100, 0)
            led_brightness = clamp_int(form.get("led_brightness"), 0, 255, 0)

            self._config.led_count = led_count
            self._config.led_brightness = led_brightness
            self._led_strip.num_leds = led_count
            self._led_strip.brightness = led_brightness

            return Response.redirect("/", status_code=303)


if __name__ == "__main__":
    config = Config("config.json", log_level="INFO")
    config.read_from_file()

    assert config.ap_ssid_prefix is not None, "AP SSID prefix must be set in config."
    assert config.ap_password is not None, "AP password must be set in config."

    wifi = Wifi(
        ap_ssid_prefix=config.ap_ssid_prefix,
        ap_password=config.ap_password,
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
        asyncio.create_task(wifi.connect_forever_as_station_with_fallback_as_ap())
        app = microdot_app.get_app()
        print("Starting web server on http://0.0.0.0:80")
        server_task = asyncio.create_task(app.start_server(host="0.0.0.0", port=80))

        await server_task
        print("Server stopped (Button Save and Apply was clicked).")

    asyncio.run(main())
