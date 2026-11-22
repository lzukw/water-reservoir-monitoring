"""Minimal ICMP echo ("ping") for MicroPython on the Pico W (also runs on CPython).

Why this exists: WLAN.isconnected() / status()==STAT_GOT_IP only tell that the
radio link to the access point is up - they stay True even when the router
(gateway) behind the AP is powered off. To really know that the station
connection is usable, the gateway must be pinged.

A raw ICMP socket is used when the platform allows it (Pico W firmware does).
If raw sockets are unavailable (e.g. CPython without root), we fall back to a
TCP connect to the host on port 53/80, which gateways normally answer.

Never raises - any error simply means "not reachable" (False).
"""

import socket

try:
    import struct
except ImportError:  # very old MicroPython only has ustruct
    import ustruct as struct

from timeutil import elapsed_ms, now_ms

_ICMP_ECHO_REQUEST = 8
_ICMP_ECHO_REPLY = 0
# MicroPython on some Pico W firmware versions has no socket.IPPROTO_ICMP
# constant, but raw sockets with the protocol number 1 (= ICMP) work.
_IPPROTO_ICMP = getattr(socket, "IPPROTO_ICMP", 1)


def _checksum(data: bytes) -> int:
    """Internet checksum (RFC 1071) over the given bytes."""
    if len(data) % 2:
        data += b"\x00"
    total = 0
    for i in range(0, len(data), 2):
        total += (data[i] << 8) + data[i + 1]
    while total >> 16:
        total = (total & 0xFFFF) + (total >> 16)
    return (~total) & 0xFFFF


def _echo_reply_matches(data: bytes, ident: int, seq: int) -> bool:
    # A raw socket may deliver the packet including the 20-byte IPv4 header.
    off = 20 if len(data) >= 28 and (data[0] >> 4) == 4 else 0
    if len(data) < off + 8:
        return False
    icmp_type, _code, _csum, rid, rseq = struct.unpack("!BBHHH", data[off:off + 8])
    return icmp_type == _ICMP_ECHO_REPLY and rid == ident and rseq == seq


def _icmp_ping(addr: str, timeout_ms: int) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_RAW, _IPPROTO_ICMP)
    try:
        ident = now_ms() & 0xFFFF
        seq = 0
        payload = b"pico-wifi-check"  # 16 bytes
        packet = struct.pack("!BBHHH", _ICMP_ECHO_REQUEST, 0, 0, ident, seq) + payload
        packet = struct.pack("!BBHHH", _ICMP_ECHO_REQUEST, 0, _checksum(packet), ident, seq) + payload
        s.sendto(packet, (addr, 0))
        t0 = now_ms()
        while elapsed_ms(t0) < timeout_ms:
            remaining_ms = timeout_ms - elapsed_ms(t0)
            s.settimeout(max(0.05, remaining_ms / 1000.0))
            try:
                data = s.recv(1024)
            except OSError:
                return False  # timeout, no echo reply
            if _echo_reply_matches(data, ident, seq):
                return True
            # some other ICMP packet arrived - keep waiting until the deadline
        return False
    finally:
        s.close()


def _tcp_probe(addr: str, timeout_ms: int) -> bool:
    for port in (53, 80):
        s = None
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(timeout_ms / 1000.0)
            s.connect((addr, port))
            return True
        except OSError:
            pass
        finally:
            if s is not None:
                s.close()
    return False


def ping(host: str, timeout_ms: int = 1000) -> bool:
    """True if `host` answers one ICMP echo request within timeout_ms.
    Falls back to a TCP connect (ports 53/80) when raw sockets are unavailable.
    Never raises."""
    if not host:
        return False
    try:
        addr = socket.getaddrinfo(host, 1)[0][-1][0]
    except Exception:  # noqa: BLE001
        return False
    try:
        return _icmp_ping(addr, timeout_ms)
    except Exception:  # noqa: BLE001
        # raw sockets not permitted/available on this platform -> TCP fallback
        # (also covers missing constants like socket.IPPROTO_ICMP)
        try:
            return _tcp_probe(addr, timeout_ms)
        except Exception:  # noqa: BLE001
            return False
