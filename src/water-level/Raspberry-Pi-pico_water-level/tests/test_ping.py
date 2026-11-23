import socket
import time

from ping import tcp_ping


def _listener():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    return s


def test_tcp_ping_success():
    s = _listener()
    try:
        port = s.getsockname()[1]
        assert tcp_ping("127.0.0.1", port, 1000) is True
    finally:
        s.close()


def test_tcp_ping_refused_is_fast_and_false():
    """Nothing listens on the port -> RST -> False immediately, not after the
    full timeout."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    t0 = time.monotonic()
    assert tcp_ping("127.0.0.1", port, 2000) is False
    assert time.monotonic() - t0 < 1.5


def test_tcp_ping_never_raises_on_garbage():
    assert tcp_ping("", 6379, 100) is False
    assert tcp_ping("256.256.256.256", 6379, 100) is False  # unresolvable
