#!/usr/bin/env bash
# Deploy the project to the Pico filesystem (wipes the Pico FS first) and reset.
# Usage: ./deploy.sh [--noreset]
set -euo pipefail
cd "$(dirname "$0")"
M="${MPREMOTE:-/home/zukw/.local/bin/mpremote}"

echo "== wiping Pico filesystem =="
for entry in $($M fs ls : 2>/dev/null | awk '{print $NF}'); do
    $M fs rm -r ":$entry" 2>/dev/null || true
done

echo "== copying files =="
for f in boot.py config.json logger.py config.py timeutil.py ping.py \
         redis_client.py ntp_time.py ultrasonic_sensor.py wifi.py web_app.py main.py; do
    if [ -f "$f" ]; then $M fs cp "$f" ":$f"; fi
done
$M fs cp -r lib :
$M fs cp -r templates :
$M fs mkdir :pico_tests 2>/dev/null || true
for t in pico_tests/*.py; do $M fs cp "$t" ":pico_tests/$(basename "$t")"; done
$M fs mkdir :manual_pico_tests 2>/dev/null || true
for t in manual_pico_tests/*.py; do $M fs cp "$t" ":manual_pico_tests/$(basename "$t")"; done
# redis host for on-board tests (used by pico_tests/test_step2_board.py)
if [ -f tests/redis_host.txt ]; then $M fs cp tests/redis_host.txt :test_redis_host.txt; fi
# optional: deploy the laptop-redis test config instead of the project config
if [ -f tests/config.pico.test.json ] && [ "${DEPLOY_TEST_CONFIG:-0}" = "1" ]; then
    $M fs cp tests/config.pico.test.json :config.json
fi

echo "== Pico filesystem now =="
$M fs ls -r :

if [ "${1:-}" != "--noreset" ]; then
    echo "== resetting Pico =="
    $M reset
fi
