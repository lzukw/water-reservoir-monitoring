import asyncio

from microdot import Microdot
from microdot.utemplate import Template

from config import Config
from logger import Logger
from network_time import NetworkTime
from redis_server import RedisServer
from ultrasonic_sensor import UltrasonicSensor
from wifi import Wifi

Template.initialize(template_dir='templates')

def escape_html(value) -> str:
    text = str(value)
    return (text.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace('"', "&quot;")
                .replace("'", "&#39;"))

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
    def __init__(self, 
                 config: Config, 
                 wifi: Wifi, 
                 network_time: NetworkTime,
                 redis_server: RedisServer,
                 ultrasonic_sensor: UltrasonicSensor):
        self._config : Config = config
        self._wifi : Wifi = wifi
        self._network_time : NetworkTime= network_time
        self._redis_server : RedisServer= redis_server
        self._ultrasonic_sensor : UltrasonicSensor = ultrasonic_sensor

        self._app = Microdot()
        self._logger = Logger(name="Web", log_level="INFO")
        self._register_routes()

    def get_app(self) -> Microdot:
        return self._app

    async def _delayed_shutdown(self, delay_seconds: int = 1):
        self._logger.log("WARNIUNG", f"Delaying shutdown of Webserver for {delay_seconds} seconds...")
        await asyncio.sleep(delay_seconds)
        self._app.shutdown()

    async def _render_config_page(self, message=""):
        # global config ...only accessing global variable, not modifying it
        # global sensor_distance_in_m ...only accessing global variable, not modifying it
        hostname = self._config.hostname
        if hostname == "":
            hostname = "water-level-" + self._wifi.get_mac_address(separator="")

        return Template("config.html").render(
            hostname=escape_html(hostname),
            ap_ssid_prefix = escape_html(self._config.ap_ssid_prefix),
            ap_password=escape_html(self._config.ap_password),
            station_ssid=escape_html(self._config.station_ssid),
            station_password=escape_html(self._config.station_password),
            redis_server=escape_html(self._config.redis_server),
            redis_port=escape_html(self._config.redis_port),
            redis_key=escape_html(self._config.redis_key),
            redis_hashkey_timestamp=escape_html(self._config.redis_hashkey_timestamp),
            redis_hashkey_value=escape_html(self._config.redis_hashkey_value),
            distance_to_bottom_in_cm=clamp_int(int(self._config.distance_to_bottom * 100.0), 25, 20000, 25),
            message=escape_html(message)
        ), 200, {"Content-Type": "text/html"}

    async def _render_status_page(self):
        return Template("status-page.html").render(), 200, {"Content-Type": "text/html"}

    def _register_routes(self):
        app = self._app

        @app.route('/test')
        async def test(request):
            return "<p>Test</p>", 200, {"Content-Type": "text/html"}

        @app.get('/')
        async def index(request):
            # global config ...only accessing global variable, not modifying it
            # global sensor_distance_in_m ...only accessing global variable, not modifying it
            self._logger.log("INFO", f"GET-Request to / from {request.client_addr}")
            return await self._render_config_page()
            
        @app.post('/')
        async def save_and_apply(request):
            self._logger.log("INFO", f"POST-Request to / from {request.client_addr}")
            old_hostname = self._config.hostname
            old_ap_ssid_prefix = self._config.ap_ssid_prefix
            old_ap_password = self._config.ap_password
            old_station_ssid = self._config.station_ssid
            old_station_password = self._config.station_password
            form = request.form or {}
            if not form.get("hostname"):
                return await self._render_config_page("Error: Hostname cannot be empty.")
            if not form.get("ap_ssid_prefix") or not form.get("ap_password"):
                return await self._render_config_page("Error: SSID and Password for Access-Point-Mode cannot be empty.")
            if not form.get("station_ssid") or not form.get("station_password"):
                return await self._render_config_page("Error: SSID and Password for Client-Mode cannot be empty.")
            if not form.get("redis_server") or not form.get("redis_port"):
                return await self._render_config_page("Error: Redis Server and Redis Port cannot be empty.")
            if not form.get("redis_key") or not form.get("redis_hashkey_timestamp") or not form.get("redis_hashkey_value"):
                return await self._render_config_page("Error: Redis Key, Redis Hashkey Timestamp, and Redis Hashkey Value cannot be empty.")
            if not form.get("distance_to_bottom_in_cm"):
                return  await self._render_config_page("Error: Distance to Bottom of Reservoir cannot be empty.")
            
            self._config.hostname = form.get("hostname")
            self._config.ap_ssid_prefix = form.get("ap_ssid_prefix")
            self._config.ap_password = form.get("ap_password")
            self._config.station_ssid = form.get("station_ssid")
            self._config.station_password = form.get("station_password")
            self._config.redis_server = form.get("redis_server")
            self._config.redis_port = clamp_int(form.get("redis_port"), 1, 65535, 6379)
            self._config.redis_key = form.get("redis_key")
            self._config.redis_hashkey_timestamp = form.get("redis_hashkey_timestamp")
            self._config.redis_hashkey_value = form.get("redis_hashkey_value")
            self._config.distance_to_bottom = int(form.get("distance_to_bottom_in_cm")) / 100.0  # convert to meters
            self._config.write_to_file()

            if ( old_hostname != self._config.hostname or 
                    old_ap_ssid_prefix != self._config.ap_ssid_prefix or 
                    old_ap_password != self._config.ap_password or 
                    old_station_ssid != self._config.station_ssid or 
                    old_station_password != self._config.station_password 
                    or old_station_password != self._config.station_password ):
                self._logger.log("INFO", "Wi-Fi credentials or Redis settings changed. Device will reset to apply new settings.")
                asyncio.create_task(self._delayed_shutdown())
                return await self._render_config_page("Configuration saved. Device will reset to apply new settings.")
            else:
                return await self._render_config_page("Configuration saved. Wi-Fi credentials did not change.")

        @app.get("/status")
        async def status(request):
            self._logger.log("INFO", f"GET-Request to /status from {request.client_addr}")

            ultrasonic_distance: str
            distance = self._ultrasonic_sensor.measure_distance()
            if distance is None:
                ultrasonic_distance = "Got invalid value from sensor"
            else:
                ultrasonic_distance = f"{distance:.2f} m"

            distance_to_bottom = self._config.distance_to_bottom
            water_level = f"{distance_to_bottom - distance:.2f} m" if distance is not None else "Unknown"

            return {
                "wifi_status": self._wifi.status,
                "network_time_status": self._network_time.time_status(),
                "network_time": self._network_time.now_as_isoformat(),
                "redis_server_status": self._redis_server.status,
                "ultrasonic_distance": ultrasonic_distance,
                "distance_to_bottom": f"{distance_to_bottom:.2f} m",
                "water_level": water_level
            }, 200, {"Content-Type": "application/json; charset=UTF-8"}

        @app.get('/status-page')
        async def status_page(request):
            self._logger.log("INFO", f"GET-Request to /status-page from {request.client_addr}")

            return await self._render_status_page()
 
if __name__ == "__main__":
    config = Config("config.json", log_level="INFO")
    config.read_from_file()

    assert config.ap_ssid_prefix is not None, "AP SSID prefix must be set in config."
    assert config.ap_password is not None, "AP password must be set in config."
    
    wifi = Wifi(ap_ssid_prefix=config.ap_ssid_prefix,
                ap_password=config.ap_password,
                station_ssid=config.station_ssid or "",
                station_password=config.station_password or "",
                log_level="INFO")
    
    network_time = NetworkTime(wifi=wifi, log_level="INFO")

    redis_server = RedisServer(config=config, log_level="INFO")

    ultrasonic_sensor = UltrasonicSensor(config=config, 
                                         network_time=network_time, 
                                         redis_server=redis_server, 
                                         log_level="INFO")
    
    microdot_app = MicrodotApp(config=config, 
                               wifi=wifi, 
                               network_time=network_time,
                               redis_server=redis_server,
                               ultrasonic_sensor=ultrasonic_sensor)

    async def main():
        asyncio.create_task(wifi.connect_forever_as_station_with_fallback_as_ap())
        asyncio.create_task(network_time.get_network_time_forever())
        asyncio.create_task(redis_server.connect_forever())
        asyncio.create_task(ultrasonic_sensor.measure_and_publish_distance_forever())
        app = microdot_app.get_app()
        print("Starting web server on http://0.0.0.0:80")
        server_task = asyncio.create_task(app.start_server(host="0.0.0.0", port=80))

        await server_task
        print("Server stopped (Button Save and Apply was clicked).")

    asyncio.run(main())