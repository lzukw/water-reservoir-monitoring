import asyncio

import machine  # dummy from conftest.py (CPython host)
import network  # dummy from conftest.py (CPython host)
import pytest

import wifi as wifi_module
from ping import ping as real_ping
from wifi import WifiManager


@pytest.fixture(autouse=True)
def _restore_ping_and_fast(monkeypatch):
    """Undo the module-level ping monkeypatch, disable the STA settle delay
    (module constant) and reset the dummy machine.reset() counter."""
    monkeypatch.setattr(wifi_module, "STA_ACTIVATE_SETTLE_S", 0)
    machine.reset_calls = 0
    yield
    wifi_module.ping = real_ping


class FakeWLAN:
    """Fake for one network.WLAN interface (STA or AP)."""
    PM_NONE = 0  # on the Pico W: network.WLAN.PM_NONE (disable power-save)

    def __init__(self):
        self._active = False
        self._connected = False
        self._status = network.STAT_IDLE
        self._ifconfig = ("0.0.0.0", "0.0.0.0", "0.0.0.0", "0.0.0.0")
        self.connect_should_succeed = True
        self.stuck_connecting = False  # simulate a wedged CYW43 driver
        self.connect_calls = []
        self.calls = []  # ordered log of disconnect/connect calls
        self._cfg = {"mac": b"\xAA\xBB\xCC\xD1\xC9\x30"}
        self.raise_once = False
        self.gateway_ok = True  # what the fake ping answers for the gateway

    def active(self, v=None):
        if v is None:
            return self._active
        self._active = bool(v)

    def connect(self, ssid, psk):
        self.connect_calls.append((ssid, psk))
        self.calls.append(("connect", ssid))
        if self.stuck_connecting:
            self._connected = False
            self._status = network.STAT_CONNECTING  # driver wedged
        elif self.connect_should_succeed:
            self._connected = True
            self._status = network.STAT_GOT_IP
            self._ifconfig = ("192.168.178.28", "255.255.255.0", "192.168.178.1", "192.168.178.1")
        else:
            self._connected = False
            self._status = network.STAT_WRONG_PASSWORD

    def disconnect(self):
        self.calls.append(("disconnect",))
        self._connected = False
        self._status = network.STAT_IDLE

    def isconnected(self):
        if self.raise_once:
            self.raise_once = False
            raise OSError("simulated glitch")
        return self._connected and self._active

    def status(self):
        return self._status if self._active else network.STAT_IDLE

    def ifconfig(self):
        return self._ifconfig

    def config(self, key=None, **kw):
        if key is not None and not kw:
            return self._cfg.get(key)
        for k, v in kw.items():
            self._cfg[k] = v

    def drop(self):  # simulate losing the connection (router gone)
        self._connected = False
        self._status = network.STAT_IDLE
        self.gateway_ok = False

    def lose_gateway(self):
        """Router behind the AP is down, but the WLAN link itself stays up -
        this is the case isconnected()/STAT_GOT_IP do NOT detect on the Pico."""
        self.gateway_ok = False

    def restore_gateway(self):
        self.gateway_ok = True


def make_mgr(sta, ap, **kw):
    # monkeypatch the module-level ping used by WifiManager (host has no raw sockets)
    wifi_module.ping = lambda host, timeout_ms: sta.gateway_ok
    # register the fakes in the dummy network module; WifiManager creates its
    # interfaces via network.WLAN(STA_IF/AP_IF) in its constructor
    network.register_wlan(network.STA_IF, sta)
    network.register_wlan(network.AP_IF, ap)
    args = dict(ap_ssid_prefix="RPi-Pico-Water-Level", ap_password="1234567890",
                station_ssid="TestWifi", station_password="pw",
                connect_timeout=2.0, retry_interval=0.1,
                log_level="ERROR")
    args.update(kw)
    return WifiManager(**args)


def step(mgr):
    asyncio.run(mgr._step_once())


def test_sta_success_connects_and_ap_stays_off():
    sta, ap = FakeWLAN(), FakeWLAN()
    mgr = make_mgr(sta, ap)
    step(mgr)
    assert mgr.status == "connected as client"
    assert mgr.status_sta == "connected as client"
    assert ap.active() is False
    assert sta.connect_calls == [("TestWifi", "pw")]


def test_sta_failure_starts_ap_with_mac_suffix():
    sta, ap = FakeWLAN(), FakeWLAN()
    sta.connect_should_succeed = False
    mgr = make_mgr(sta, ap)
    step(mgr)
    assert mgr.status == "connected as AP"
    assert ap.active() is True
    assert ap.config("essid") == "RPi-Pico-Water-Level-D1C930"  # mac ...D1C930
    assert ap.config("password") == "1234567890"
    assert mgr.ap_ssid == "RPi-Pico-Water-Level-D1C930"


def test_no_station_ssid_goes_straight_to_ap():
    sta, ap = FakeWLAN(), FakeWLAN()
    mgr = make_mgr(sta, ap, station_ssid="")
    step(mgr)
    assert mgr.status == "connected as AP"
    assert sta.connect_calls == []  # never tried


def test_ap_turned_off_when_sta_connects():
    sta, ap = FakeWLAN(), FakeWLAN()
    ap.active(True)
    mgr = make_mgr(sta, ap)
    step(mgr)
    assert mgr.status == "connected as client"
    assert ap.active() is False


def test_sta_drop_with_router_down_falls_back_to_ap():
    sta, ap = FakeWLAN(), FakeWLAN()
    mgr = make_mgr(sta, ap)
    step(mgr)
    assert mgr.status == "connected as client"
    sta.drop()                        # router disappears
    sta.connect_should_succeed = False
    step(mgr)
    assert mgr.status == "connected as AP"   # recovered via AP
    assert ap.active() is True


def test_sta_drop_with_router_back_reconnects_sta():
    sta, ap = FakeWLAN(), FakeWLAN()
    mgr = make_mgr(sta, ap)
    step(mgr)
    sta.drop()                        # connection lost ...
    sta.restore_gateway()             # ... but router (and gateway) is back
    sta.connect_should_succeed = True
    step(mgr)
    assert mgr.status == "connected as client"  # auto-recovered to STA
    assert ap.active() is False


def test_ip_gateway_properties_when_sta_connected():
    sta, ap = FakeWLAN(), FakeWLAN()
    mgr = make_mgr(sta, ap)
    step(mgr)
    assert mgr.ip == "192.168.178.28"
    assert mgr.netmask == "255.255.255.0"
    assert mgr.gateway == "192.168.178.1"
    assert mgr.dns == "192.168.178.1"


def test_ip_properties_when_ap_only():
    sta, ap = FakeWLAN(), FakeWLAN()
    sta.connect_should_succeed = False
    ap._ifconfig = ("192.168.4.1", "255.255.255.0", "192.168.4.1", "0.0.0.0")
    mgr = make_mgr(sta, ap)
    step(mgr)
    assert mgr.status == "connected as AP"
    assert mgr.ip == "192.168.4.1"
    assert mgr.gateway == "192.168.4.1"


def test_link_up_but_gateway_down_falls_back_to_ap():
    """The Pico case from the manual test: WLAN link stays up (isconnected()
    True, STAT_GOT_IP) but the router is off -> gateway ping fails -> the
    station must be treated as not connected and the AP must come up."""
    sta, ap = FakeWLAN(), FakeWLAN()
    mgr = make_mgr(sta, ap)
    step(mgr)
    assert mgr.status == "connected as client"
    sta.lose_gateway()                  # router off, link still up
    sta.connect_should_succeed = False  # reconnect attempts will fail too
    step(mgr)
    assert mgr.status_sta == "not connected"
    assert mgr.status == "connected as AP"
    assert ap.active() is True


def test_gateway_down_then_up_recovers_sta_without_reconnect():
    sta, ap = FakeWLAN(), FakeWLAN()
    mgr = make_mgr(sta, ap)
    step(mgr)
    sta.lose_gateway()
    sta.connect_should_succeed = False
    step(mgr)
    assert mgr.status == "connected as AP"
    sta.restore_gateway()               # router back; link never went down
    sta.connect_should_succeed = True
    step(mgr)
    assert mgr.status == "connected as client"
    assert mgr.status_sta == "connected as client"
    assert ap.active() is False


def test_connect_sta_does_not_succeed_when_gateway_unreachable():
    """STAT_GOT_IP alone is not enough: _connect_sta must only return True
    after the gateway answered a ping."""
    sta, ap = FakeWLAN(), FakeWLAN()
    sta.gateway_ok = False              # link will come up, gateway dead
    mgr = make_mgr(sta, ap, connect_timeout=1.0)
    ok = asyncio.run(mgr._connect_sta())
    assert ok is False
    assert mgr.status_sta == "not connected"
    sta.gateway_ok = True
    ok = asyncio.run(mgr._connect_sta())
    assert ok is True
    assert mgr.status_sta == "connected as client"


def test_ping_exception_is_treated_as_unreachable():
    sta, ap = FakeWLAN(), FakeWLAN()
    mgr = make_mgr(sta, ap, connect_timeout=1.0)
    def boom(host, timeout_ms):
        raise OSError("no raw socket")
    wifi_module.ping = boom
    step(mgr)
    assert mgr.status == "connected as AP"  # ping failure -> not connected -> AP


def test_power_save_disabled_on_sta():
    """wifi.py must set pm=PM_NONE on the STA interface (Pico-W robustness)."""
    sta, ap = FakeWLAN(), FakeWLAN()
    mgr = make_mgr(sta, ap)
    step(mgr)
    assert sta._cfg.get("pm") == FakeWLAN.PM_NONE


def test_disconnect_always_called_before_connect():
    """connect() must never be issued while a previous attempt may still run:
    a disconnect() must precede every connect() (CYW43 anti-wedge)."""
    sta, ap = FakeWLAN(), FakeWLAN()
    mgr = make_mgr(sta, ap)
    step(mgr)
    kinds = [c[0] for c in sta.calls]
    assert "connect" in kinds
    first_connect = kinds.index("connect")
    assert "disconnect" in kinds[:first_connect]
    # second attempt (e.g. after a drop) also disconnects first
    sta.drop()
    sta.connect_should_succeed = False
    step(mgr)
    kinds = [c[0] for c in sta.calls]
    last_connect = len(kinds) - 1 - kinds[::-1].index("connect")
    assert "disconnect" in kinds[:last_connect]


def test_run_forever_survives_glitch_and_recovers():
    async def scenario():
        sta, ap = FakeWLAN(), FakeWLAN()
        sta.connect_should_succeed = False
        mgr = make_mgr(sta, ap)
        task = asyncio.create_task(mgr.run_forever())
        try:
            for _ in range(50):         # wait for the AP fallback to engage
                if mgr.status == "connected as AP":
                    break
                await asyncio.sleep(0.1)
            assert mgr.status == "connected as AP"
            sta.raise_once = True         # glitch: isconnected() raises once
            sta.connect_should_succeed = True  # router comes back
            for _ in range(50):
                if mgr.status == "connected as client":
                    break
                await asyncio.sleep(0.1)
            assert not task.done()        # loop still alive
            assert mgr.status == "connected as client"
            assert ap.active() is False
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    asyncio.run(scenario())


# ------------------------------------------------------------- watchdog
def _force_watchdog_due(monkeypatch):
    """Make every _watchdog_tick() evaluate immediately (interval = 0 s)."""
    monkeypatch.setattr(wifi_module, "WATCHDOG_INTERVAL_S", 0)


def test_watchdog_resets_when_stuck_in_connecting(monkeypatch):
    """Station not connected, raw STA status STAT_CONNECTING (wedged driver,
    router up) -> machine.reset() must fire once the interval elapsed."""
    _force_watchdog_due(monkeypatch)
    sta, ap = FakeWLAN(), FakeWLAN()
    mgr = make_mgr(sta, ap)
    sta.active(True)
    sta._connected = False
    sta._status = network.STAT_CONNECTING
    with pytest.raises(machine.ResetCalled):
        mgr._watchdog_tick()
    assert machine.reset_calls == 1


def test_watchdog_no_reset_when_router_down(monkeypatch):
    """STA status in ROUTER_DOWN_STATUSES (router genuinely down/rejecting)
    -> AP fallback is correct, NO reset."""
    _force_watchdog_due(monkeypatch)
    for st in (network.STAT_NO_AP_FOUND, network.STAT_CONNECT_FAIL,
               network.STAT_WRONG_PASSWORD):
        machine.reset_calls = 0
        sta, ap = FakeWLAN(), FakeWLAN()
        mgr = make_mgr(sta, ap)
        sta.active(True)
        sta._connected = False
        sta._status = st
        mgr._watchdog_tick()  # must not raise
        assert machine.reset_calls == 0


def test_watchdog_no_reset_when_connected_as_client(monkeypatch):
    _force_watchdog_due(monkeypatch)
    sta, ap = FakeWLAN(), FakeWLAN()
    mgr = make_mgr(sta, ap)
    step(mgr)
    assert mgr.status_sta == "connected as client"
    mgr._watchdog_tick()
    assert machine.reset_calls == 0


def test_watchdog_no_reset_without_station_ssid(monkeypatch):
    _force_watchdog_due(monkeypatch)
    sta, ap = FakeWLAN(), FakeWLAN()
    mgr = make_mgr(sta, ap, station_ssid="")
    sta.active(True)
    sta._status = network.STAT_CONNECTING
    mgr._watchdog_tick()
    assert machine.reset_calls == 0


def test_watchdog_respects_interval():
    """Before WATCHDOG_INTERVAL_S of system time has elapsed since the last
    check, no evaluation happens -> no reset even if the driver is stuck."""
    sta, ap = FakeWLAN(), FakeWLAN()
    mgr = make_mgr(sta, ap)  # WATCHDOG_INTERVAL_S stays at the 125 s default
    sta.active(True)
    sta._status = network.STAT_CONNECTING
    for _ in range(10):
        mgr._watchdog_tick()
    assert machine.reset_calls == 0


def test_run_forever_watchdog_resets_stuck_sta(monkeypatch):
    """Integration: run_forever with a wedged STA (stuck STAT_CONNECTING)
    must be killed by the watchdog's machine.reset() (simulated as a
    BaseException that propagates out of run_forever, like a real reboot)."""
    async def scenario():
        monkeypatch.setattr(wifi_module, "WATCHDOG_INTERVAL_S", 0)  # every tick checks
        sta, ap = FakeWLAN(), FakeWLAN()
        sta.stuck_connecting = True  # connect() wedges in STAT_CONNECTING
        mgr = make_mgr(sta, ap, connect_timeout=0.5)
        with pytest.raises(machine.ResetCalled):
            await mgr.run_forever()

    asyncio.run(scenario())
    assert machine.reset_calls >= 1
