"""Start/stop a local redis-server for the on-board (Pico) tests.

Usage:
    .venv/bin/python tests/redis_lab.py start   # redis on 127.0.0.1 + LAN IP, requirepass set
    .venv/bin/python tests/redis_lab.py stop

The Pico connects to <LAN_IP>:6379 with the password (config key
"redis_password" on the Pico's config.json). No persistence (test instance).
"""
import os
import signal
import socket
import subprocess
import sys
import time

PORT = 6379
LAN_IP = "192.168.178.20"  # laptop's IP on the test router
PASSWORD = "wasser-test-123"
HERE = os.path.dirname(os.path.abspath(__file__))
PIDFILE = os.path.join(HERE, "redis_lab.pid")
LOGFILE = os.path.join(HERE, "redis_lab.log")


def start():
    stop()
    if os.path.exists(PIDFILE):
        os.remove(PIDFILE)
    # daemonize yes -> double-fork, survives the calling shell (process-group safe)
    subprocess.Popen(
        ["redis-server", "--port", str(PORT),
         "--bind", "127.0.0.1", LAN_IP,
         "--requirepass", PASSWORD,
         "--save", "", "--appendonly", "no",
         "--daemonize", "yes",
         "--pidfile", PIDFILE, "--logfile", LOGFILE])
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            s = socket.create_connection(("127.0.0.1", PORT), 0.3)
            s.close()
            break
        except OSError:
            time.sleep(0.1)
    else:
        print("redis-server did not open the port; see", LOGFILE)
        sys.exit(1)
    pid = open(PIDFILE).read().strip() if os.path.exists(PIDFILE) else "?"
    print("redis lab STARTED pid=%s on 127.0.0.1 + %s port %d (requirepass set)" % (pid, LAN_IP, PORT))


def stop():
    if os.path.exists(PIDFILE):
        pid = int(open(PIDFILE).read().strip())
        try:
            os.kill(pid, signal.SIGTERM)
            print("redis lab STOPPED pid=%d" % pid)
        except OSError:
            print("redis lab pid=%d already gone" % pid)
        os.remove(PIDFILE)
    else:
        print("redis lab not running (no pidfile)")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    if sys.argv[1] == "start":
        start()
    elif sys.argv[1] == "stop":
        stop()
