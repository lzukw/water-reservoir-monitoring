import asyncio

import pytest

import wifi as wifi_module
from ping import ping as real_ping
from wifi import WifiManager

STAT_GOT_IP = 5


@pytest.fixture(autouse=True)
def _restore_ping():
    """Undo the module-level ping monkeypatch after each test."""
    yield
    wifi_module.ping = real_ping


class FakeWLAN:
    def __init__(self):
        self._active = False
        self._connected = False
        self._status = 0
        self._ifconfig = ("0.0.0.0", "0.0.0.0", "0.0.0.0", "0.0.0.0")
        self.connect_should_succeed = True
        self.connect_calls = []
        self._cfg = {"mac": b"\xAA\xBB\xCC\xD1\xC9\x30"}
        self.raise_once = False
        self.gateway_ok = True  # what the fake ping answers for the gateway

    def active(self, v=None):
        if v is None:
            return self._active
        self._active = bool(v)

    def connect(self, ssid, psk):
        self.connect_calls.append((ssid, psk))
        if self.connect_should_succeed:
            self._connected = True
            self._status = STAT_GOT_IP
            self._ifconfig = ("192.168.178.28", "255.255.255.0", "192.168.178.1", "192.168.178.1")
        else:
            self._connected = False
            self._status = 2  # STAT_WRONG_PASSWORD

    def disconnect(self):
        self._connected = False
        self._status = 0

    def isconnected(self):
        if self.raise_once:
            self.raise_once = False
            raise OSError("simulated glitch")
        return self._connected and self._active

    def status(self):
        return self._status if self._active else 0

    def ifconfig(self):
        return self._ifconfig

    def config(self, key=None, **kw):
        if key is not None and not kw:
            return self._cfg.get(key)
        for k, v in kw.items():
            self._cfg[k] = v

    def drop(self):  # simulate losing the connection (router gone)
        self._connected = False
        self._status = 0
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
    args = dict(ap_ssid_prefix="RPi-Pico-Water-Level", ap_password="1234567890",
                station_ssid="TestWifi", station_password="pw",
                connect_timeout=2.0, retry_interval=0.1,
                wlan_ap=ap, wlan_sta=sta, stat_got_ip=STAT_GOT_IP,
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
