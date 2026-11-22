"""Web interface (microdot) for the water-level Pico.

Routes:
  GET  /           configuration form (wifi AP/station, redis, sensor)
  POST /           validate + save config.json; if anything changed, the
                   on_apply callback is scheduled (main.py uses it to restart
                   the device so the new config takes effect)
  GET  /status     JSON status of all subsystems (polled by the status page)
  GET  /status-page  HTML status page polling /status every second
  GET  /test       plain "ok" (connectivity check)

All values rendered into HTML are escaped. Runs on MicroPython and CPython
(host tests start the same app on 127.0.0.1 and query it with httpx).
"""

import asyncio

from microdot import Microdot
from microdot.utemplate import Template

from logger import Logger

Template.initialize(template_dir="templates")

_FORM_FIELDS = ("hostname", "ap_ssid_prefix", "ap_password", "station_ssid",
                "station_password", "redis_server", "redis_port", "redis_key",
                "redis_hashkey_timestamp", "redis_hashkey_value",
                "distance_to_bottom_in_cm")


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


class WebApp:
    def __init__(self, config, wifi, ntp, redis_service, sensor,
                 on_apply=None, log_level: str = "INFO"):
        self._config = config
        self._wifi = wifi
        self._ntp = ntp
        self._redis = redis_service
        self._sensor = sensor
        self._on_apply = on_apply  # async callable, scheduled when config changed
        self._logger = Logger("Web", log_level)
        self._app = Microdot()
        self._register_routes()

    def get_app(self) -> Microdot:
        return self._app

    # ------------------------------------------------------------ helpers
    async def _render_config(self, message: str = ""):
        cfg = self._config
        cm = clamp_int(round((cfg.distance_to_bottom or 0) * 100), 25, 20000, 25)
        html = Template("config.html").render(
            hostname=escape_html(cfg.hostname),
            ap_ssid_prefix=escape_html(cfg.ap_ssid_prefix),
            ap_password=escape_html(cfg.ap_password),
            station_ssid=escape_html(cfg.station_ssid),
            station_password=escape_html(cfg.station_password),
            redis_server=escape_html(cfg.redis_server),
            redis_port=escape_html(cfg.redis_port),
            redis_key=escape_html(cfg.redis_key),
            redis_hashkey_timestamp=escape_html(cfg.redis_hashkey_timestamp),
            redis_hashkey_value=escape_html(cfg.redis_hashkey_value),
            distance_to_bottom_in_cm=escape_html(cm),
            message=escape_html(message),
        )
        return html, 200, {"Content-Type": "text/html"}

    def _status_dict(self) -> dict:
        distance = getattr(self._sensor, "last_distance", None)
        dtb = self._config.distance_to_bottom
        return {
            "hostname": self._config.hostname or "",
            "wifi_status": self._wifi.status,
            "wifi_sta": self._wifi.status_sta,
            "wifi_ap": self._wifi.status_ap,
            "ip": self._wifi.ip,
            "gateway": self._wifi.gateway,
            "ntp_status": self._ntp.time_status,
            "ntp_time": self._ntp.current_time_utc_iso(),
            "redis_status": self._redis.status,
            "redis_last_error": self._redis.last_error,
            "redis_publish_count": self._redis.publish_count,
            "ultrasonic_distance": ("%.2f m" % distance) if distance is not None else "invalid",
            "distance_to_bottom": ("%.2f m" % dtb) if dtb is not None else "unknown",
            "water_level": ("%.2f m" % (dtb - distance))
                           if (distance is not None and dtb is not None) else "unknown",
        }

    # ------------------------------------------------------------- routes
    def _register_routes(self):
        app = self._app

        @app.get("/test")
        async def test(request):
            return "ok", 200, {"Content-Type": "text/plain"}

        @app.get("/")
        async def index(request):
            self._logger.info("GET / from %s" % (getattr(request, "client_addr", "?"),))
            return await self._render_config()

        @app.post("/")
        async def save(request):
            form = request.form or {}
            missing = [f for f in _FORM_FIELDS if not str(form.get(f, "")).strip()]
            if missing:
                return await self._render_config("Error: missing fields: %s" % ", ".join(missing))

            old = {k: self._config.get(k) for k in
                   ("hostname", "ap_ssid_prefix", "ap_password", "station_ssid",
                    "station_password", "redis_server", "redis_port", "redis_key",
                    "redis_hashkey_timestamp", "redis_hashkey_value", "distance_to_bottom")}

            self._config.hostname = str(form.get("hostname")).strip()
            self._config.ap_ssid_prefix = str(form.get("ap_ssid_prefix")).strip()
            self._config.ap_password = str(form.get("ap_password"))
            self._config.station_ssid = str(form.get("station_ssid")).strip()
            self._config.station_password = str(form.get("station_password"))
            self._config.redis_server = str(form.get("redis_server")).strip()
            self._config.redis_port = clamp_int(form.get("redis_port"), 1, 65535, 6379)
            self._config.redis_key = str(form.get("redis_key")).strip()
            self._config.redis_hashkey_timestamp = str(form.get("redis_hashkey_timestamp")).strip()
            self._config.redis_hashkey_value = str(form.get("redis_hashkey_value")).strip()
            cm = clamp_int(form.get("distance_to_bottom_in_cm"), 25, 20000, 25)
            self._config.distance_to_bottom = cm / 100.0

            if not self._config.write_to_file():
                return await self._render_config("Error: could not write config.json")

            changed = any(old[k] != self._config.get(k) for k in old)
            if changed:
                self._logger.info("Configuration changed -> scheduling apply (restart).")
                if self._on_apply is not None:
                    asyncio.create_task(self._on_apply())
                return await self._render_config("Saved. Device restarts to apply the new configuration.")
            return await self._render_config("Saved (no changes).")

        @app.get("/status")
        async def status(request):
            return self._status_dict(), 200, {"Content-Type": "application/json; charset=UTF-8"}

        @app.get("/status-page")
        async def status_page(request):
            html = Template("status_page.html").render()
            return html, 200, {"Content-Type": "text/html"}
