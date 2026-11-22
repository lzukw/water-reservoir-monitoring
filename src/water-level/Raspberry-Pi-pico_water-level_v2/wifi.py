"""Wifi manager: station (client) first, access-point as fallback.

Robustness: run_forever() never dies. Every `retry_interval` seconds it checks
the station connection; while the router is unreachable it (re)starts the
Access Point so the device stays reachable for configuration; as soon as the
station connects, the AP is turned off. If the station connection drops later,
the AP comes back automatically.

Station-mode check: `sta.isconnected()` + `status()==STAT_GOT_IP` only mean the
radio link to the AP is up - they stay True even when the router (gateway)
behind the AP is down. Therefore the station only counts as "connected as
client" if the gateway additionally answers a ping (see ping.py). The ping
always runs with fixed defaults (GATEWAY_PING_ATTEMPTS x GATEWAY_PING_TIMEOUT_MS).

On the Pico the WLAN interfaces are network.WLAN(AP_IF) / network.WLAN(STA_IF)
(created in the constructor). All tuning knobs are module-level constants
below - no constructor/config parameters for them.

Blocking notes: sta.connect()/ap.config() are quick; the station connect wait
is async (await asyncio.sleep between checks), bounded by connect_timeout.
The gateway ping is blocking but strictly bounded by
GATEWAY_PING_ATTEMPTS * GATEWAY_PING_TIMEOUT_MS.

Pico-W robustness (mitigates a wedged STAT_CONNECTING on the CYW43):
power-save is disabled on the STA interface (pm=PM_NONE), a short settle
delay (STA_ACTIVATE_SETTLE_S) is awaited after sta.active(True), and
sta.disconnect() is ALWAYS called before sta.connect() - issuing connect()
while a previous attempt may still be running is a known way to wedge the
driver in STAT_CONNECTING.

Watchdog (last resort against a wedged CYW43 driver): every
WATCHDOG_INTERVAL_S seconds of system time, if a station SSID is configured,
the station is not "connected as client", and the raw STA status does NOT
indicate a genuinely down router (ROUTER_DOWN_STATUSES), the board is
hard-reset via machine.reset(). On the Pico W this also powers down the
CYW43 (WL_REG_ON is GP23, whose pad resets to input+pull-down), so the WLAN
module comes back freshly initialized after the reboot.
"""

import asyncio

import network

from logger import Logger
from ping import ping
from timeutil import elapsed_ms, now_ms

# Fixed defaults for the gateway-reachability ping in station mode.
GATEWAY_PING_TIMEOUT_MS = 1000
GATEWAY_PING_ATTEMPTS = 2
# Settle delay after sta.active(True) before disconnect/connect (seconds).
# Connecting immediately after activation is a known cause of a stuck
# STAT_CONNECTING on the Pico W (first-connect-after-boot issue).
STA_ACTIVATE_SETTLE_S = 0.5

# Watchdog: check every WATCHDOG_INTERVAL_S seconds of system time (timeutil).
# If the station is not connected as client and the raw STA status is NOT in
# ROUTER_DOWN_STATUSES (i.e. the router should be reachable but the driver is
# wedged, e.g. stuck in network.STAT_CONNECTING), hard-reset the board with
# machine.reset().
WATCHDOG_INTERVAL_S = 125
# STA statuses that mean "the router/AP is genuinely down or rejecting us" -
# in these cases the AP fallback is the correct response, NOT a reset.
ROUTER_DOWN_STATUSES = (network.STAT_WRONG_PASSWORD,
                        network.STAT_NO_AP_FOUND,
                        network.STAT_CONNECT_FAIL)


class WifiManager:
    def __init__(self, ap_ssid_prefix: str, ap_password: str,
                 station_ssid: str, station_password: str,
                 connect_timeout: float = 20.0, retry_interval: float = 30.0,
                 log_level: str = "INFO"):
        self._ap_ssid_prefix = ap_ssid_prefix
        self._ap_password = ap_password
        self._station_ssid = station_ssid
        self._station_password = station_password
        self._connect_timeout = connect_timeout
        self._retry_interval = retry_interval
        # True only after the gateway was verified reachable by ping (station mode)
        self._sta_verified = False
        # watchdog bookkeeping: system time (ms) of the last watchdog check
        self._wd_last_check_ms = now_ms()
        self._logger = Logger("Wifi", log_level)
        self._ap = network.WLAN(network.AP_IF)
        self._sta = network.WLAN(network.STA_IF)

    # ------------------------------------------------------------- status
    def _sta_link_up(self) -> bool:
        """Link-level check only: associated with the AP and got an IP.
        NOTE: on the Pico this stays True even when the router/gateway
        behind the AP is down - use _verify_sta() for the real check."""
        try:
            return bool(self._sta.isconnected()) and self._sta.status() == network.STAT_GOT_IP
        except OSError:
            return False

    def _gateway_ip(self) -> str:
        """Gateway of the station interface ('' if none/unknown)."""
        try:
            return self._sta.ifconfig()[2] or ""
        except (OSError, IndexError):
            return ""

    def _gateway_reachable(self) -> bool:
        """Ping the gateway (station mode). Blocking, but bounded by
        GATEWAY_PING_ATTEMPTS * GATEWAY_PING_TIMEOUT_MS. Never raises."""
        gw = self._gateway_ip()
        if not gw or gw == "0.0.0.0":
            return False
        for _ in range(GATEWAY_PING_ATTEMPTS):
            try:
                success = ping(gw, GATEWAY_PING_TIMEOUT_MS)
                self._logger.debug(f"Could ping gateway '{gw}' ping: {success}")
                if success:
                    return True
            except Exception as e:  # noqa: BLE001 - a ping error must never kill the loop
                self._logger.debug(f"gateway ping raised: {e}")
            self._logger.debug(f"gateway {gw} not reachable (ping failed)")
        return False

    def _verify_sta(self) -> bool:
        """Full station check: link up AND gateway reachable. Updates the
        cached flag that status_sta relies on, so a lost gateway is detected
        within one retry_interval even if the WLAN link itself stays up."""
        if not self._sta_link_up():
            self._sta_verified = False
            return False
        ok = self._gateway_reachable()
        if ok != self._sta_verified:
            if ok:
                self._logger.info(f"gateway {self._gateway_ip()} reachable again")
            else:
                self._logger.warning(f"station link up but gateway {self._gateway_ip()} "
                                   f"unreachable -> treating station as not connected")
        self._sta_verified = ok
        return ok

    @property
    def status_sta(self) -> str:
        """'connected as client' if the STA link is up AND the gateway was
        verified reachable by ping, else 'not connected'."""
        if self._sta_link_up() and self._sta_verified:
            return "connected as client"
        return "not connected"

    @property
    def status_ap(self) -> str:
        """'connected as AP' if the AP interface is active, else 'not connected'."""
        try:
            if self._ap.active():
                return "connected as AP"
        except OSError:
            pass
        return "not connected"

    @property
    def status(self) -> str:
        """'connected as client' if STA is up, else 'connected as AP' if the AP
        is up, else 'not connected'."""
        if self.status_sta == "connected as client":
            return "connected as client"
        if self.status_ap == "connected as AP":
            return "connected as AP"
        return "not connected"

    def _active_if(self):
        if self.status_sta == "connected as client":
            return self._sta
        if self.status_ap == "connected as AP":
            return self._ap
        return None

    @property
    def ip(self) -> str:
        w = self._active_if()
        return w.ifconfig()[0] if w is not None else ""

    @property
    def netmask(self) -> str:
        w = self._active_if()
        return w.ifconfig()[1] if w is not None else ""

    @property
    def gateway(self) -> str:
        w = self._active_if()
        return w.ifconfig()[2] if w is not None else ""

    @property
    def dns(self) -> str:
        w = self._active_if()
        return w.ifconfig()[3] if w is not None else ""

    @property
    def ap_ssid(self) -> str:
        mac = self._ap.config("mac")
        # NOTE: no nested f-string here - MicroPython's f-string parser rejects it
        hexmac = "".join(f"{b:02X}" for b in mac)
        return f"{self._ap_ssid_prefix}-{hexmac[-6:]}"

    # ------------------------------------------------------------ actions
    def _sta_power_save_off(self) -> None:
        """Disable WiFi power management on the station interface (Pico W:
        sta.config(pm=PM_NONE)). Power-save is a known trigger for stuck
        joins. Host fakes without a PM_NONE attribute are skipped."""
        pm_none = getattr(self._sta, "PM_NONE", None)
        if pm_none is None:
            return
        try:
            self._sta.config(pm=pm_none)
            self._logger.debug(f"station power-save disabled (pm=PM_NONE={pm_none})")
        except (OSError, AttributeError, TypeError) as e:
            self._logger.debug(f"could not set pm=PM_NONE: {e}")

    def _start_ap(self) -> None:
        self._ap.active(True)
        self._ap.config(essid=self.ap_ssid, password=self._ap_password)
        self._logger.info(f"Access Point started: SSID={self.ap_ssid} "
                          f"password={self._ap_password} ip={self._ap.ifconfig()[0]}")

    def _stop_ap(self) -> None:
        try:
            self._ap.active(False)
            self._logger.info("Access Point turned off (station is connected).")
        except OSError as e:
            self._logger.warning(f"could not turn off AP: {e}")

    async def _connect_sta(self) -> bool:
        """Try to connect the station interface. Async wait bounded by
        connect_timeout seconds (no blocking accumulation). Success requires
        link up AND gateway reachable (ping), not just STAT_GOT_IP."""
        if not self._station_ssid:
            self._logger.warning("no station SSID configured")
            self._sta_verified = False
            return False
        self._sta.active(True)
        self._sta_power_save_off()
        # let the chip settle after active(True) before (re)connecting
        if STA_ACTIVATE_SETTLE_S > 0:
            await asyncio.sleep(STA_ACTIVATE_SETTLE_S)
        if self._sta_link_up() and not self._gateway_reachable():
            # link up but gateway dead (e.g. router off): the unconditional
            # disconnect below forces a clean reconnect
            self._logger.info("link up but gateway unreachable - forcing clean reconnect")
        # NEVER call connect() while a previous attempt may still be running:
        # always disconnect() first, otherwise the CYW43 driver can wedge
        # permanently in STAT_CONNECTING
        try:
            self._sta.disconnect()
        except OSError:
            pass
        self._logger.info(f"Trying to connect as client to SSID '{self._station_ssid}' ...")
        try:
            self._sta.connect(self._station_ssid, self._station_password)
        except OSError as e:
            self._logger.warning(f"sta.connect() failed: {e}")
            self._sta_verified = False
            return False
        t0 = now_ms()
        while True:
            if self._sta_link_up() and self._gateway_reachable():
                self._sta_verified = True
                self._logger.info(f"Connected as client: ip={self._sta.ifconfig()[0]} "
                                  f"gateway={self._sta.ifconfig()[2]} dns={self._sta.ifconfig()[3]} "
                                  f"(gateway verified by ping)")
                return True
            if elapsed_ms(t0) > int(self._connect_timeout * 1000):
                self._logger.warning(f"station connect timed out after {self._connect_timeout} s "
                                     f"(status={self._sta.status()}, link_up={self._sta_link_up()})")
                self._sta_verified = False
                return False
            self._logger.debug(f"station not connected yet. Status: {self._sta.status()}")
            await asyncio.sleep(1.0)

    async def _step_once(self) -> None:
        """One iteration of the state machine (kept separate for testing)."""
        if self._verify_sta():  # link up AND gateway reachable
            if self.status_ap == "connected as AP":
                self._logger.debug("State-Machine: Station connected, AP is on: turning off AP.")
                self._stop_ap()  # goal reached: station only, AP off
            else:
                self._logger.debug("State-Machine: Station connected, AP is off: nothing to do.")
            
            return  # goal reached: station only, AP off
        
        # station not connected: try it, fall back to AP on failure
        self._logger.debug("State-Machine: Station not connected: trying to connect...")
        success = await self._connect_sta()
        if success:
            if self.status_ap == "connected as AP":
                self._logger.debug("State-Machine: Station now connected, AP is on: turning off AP.")
                self._stop_ap()
        else:
            if self.status_ap != "connected as AP":
                self._logger.debug("State-Machine: Failed to connect as client, AP is off: turning on AP.")
                self._start_ap()
            else:
                self._logger.debug("State-Machine: Failed to connect as client, AP is on: nothing to do.")

    # ---------------------------------------------------------- watchdog
    def _watchdog_tick(self) -> None:
        """Hard-reset watchdog, called once per run_forever cycle.

        Every WATCHDOG_INTERVAL_S seconds of system time: if a station SSID
        is configured, the station is NOT 'connected as client', and the raw
        STA status is NOT in ROUTER_DOWN_STATUSES (i.e. the router should be
        reachable but the CYW43 driver is wedged, e.g. stuck in
        network.STAT_CONNECTING), hard-reset the board via machine.reset().
        On the Pico W this also powers down the CYW43 (WL_REG_ON = GP23,
        whose pad resets to input+pull-down), so the WLAN module is freshly
        initialized after the reboot. Never raises (a real machine.reset()
        does not return).
        """
        if elapsed_ms(self._wd_last_check_ms) < WATCHDOG_INTERVAL_S * 1000:
            return
        self._wd_last_check_ms = now_ms()
        try:
            if not self._station_ssid:
                return  # no station configured -> AP-only is the goal, no reset
            if self.status_sta == "connected as client":
                return
            try:
                sta_status = self._sta.status()
            except OSError:
                return  # cannot tell -> stay conservative, no reset
            if sta_status in ROUTER_DOWN_STATUSES:
                return  # router genuinely down/rejecting -> AP fallback, no reset
            self._logger.error(f"watchdog: station not connected, STA status={sta_status} "
                               f"does not indicate a down router -> hard reset "
                               f"(machine.reset(); on Pico W this also resets the CYW43)")
            import machine
            machine.reset()  # never returns on real hardware
        except Exception as e:  # noqa: BLE001 - the watchdog must never kill the loop
            self._logger.error(f"watchdog error: {e}")

    async def run_forever(self) -> None:
        """State machine forever. Never raises (except a watchdog reset)."""
        await asyncio.sleep(1.0)  # Give wifi-chip some time after power up of the board.
        while True:
            try:
                self._logger.debug("State-Machine: Beginning next wifi loop iteration")
                await self._step_once()
            except Exception as e:  # shield: this loop must never die  # noqa: BLE001
                self._logger.error(f"Unexpected error in wifi loop: {e}")
            self._watchdog_tick()
            await asyncio.sleep(self._retry_interval)
