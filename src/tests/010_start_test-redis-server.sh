#!/bin/bash
# Starts a redis-server for local testing:
#   - listens on ALL interfaces (IPv4 0.0.0.0 and IPv6 ::), port 6379 (or the port given as 1st argument)
#   - no password and protected-mode disabled (NO protection - only use in a trusted LAN!)
#   - no persistence (nothing is written to disk)
#   - no configuration files are read or modified - all settings are passed as command-line arguments
# The server is stopped automatically when this script is interrupted (Ctrl+C) or killed (SIGTERM/SIGINT).
#
# Usage: ./010_start_test-redis-server.sh [port]

set -u

PORT="${1:-6379}"

if ! command -v redis-server >/dev/null 2>&1; then
    echo "ERROR: 'redis-server' was not found - redis-server is not installed."
    echo
    echo "Install it on Debian 13 (trixie) with apt:"
    echo "    sudo apt update"
    echo "    sudo apt install -y redis-server"
    echo
    echo "The package automatically enables and starts a systemd-service 'redis-server',"
    echo "which occupies port 6379 with its own (protected) configuration."
    echo "Disable and stop that service, so it does not conflict with this test-server:"
    echo "    sudo systemctl disable --now redis-server"
    echo
    echo "Then run this script again."
    exit 1md
fi

# Start in foreground (as background job of this script), all settings via CLI args only.
redis-server \
    --bind 0.0.0.0 :: \
    --protected-mode no \
    --port "$PORT" \
    --save "" \
    --appendonly no \
    --daemonize no &
REDIS_PID=$!

cleanup() {
    trap - INT TERM EXIT
    if [ -n "${REDIS_PID:-}" ] && kill -0 "$REDIS_PID" 2>/dev/null; then
        echo
        echo "Stopping redis-server (pid $REDIS_PID)..."
        kill "$REDIS_PID" 2>/dev/null
        wait "$REDIS_PID" 2>/dev/null
    fi
}
trap cleanup INT TERM EXIT

echo "redis-server started (pid $REDIS_PID):"
echo "  - listening on ALL interfaces (0.0.0.0 and ::), port $PORT"
echo "  - protected-mode no, no password (unprotected - trusted LAN only!)"
echo "  - persistence off (nothing written to disk)"
echo "Press Ctrl+C (or send SIGINT/SIGTERM to this script) to stop the server."

wait "$REDIS_PID"
