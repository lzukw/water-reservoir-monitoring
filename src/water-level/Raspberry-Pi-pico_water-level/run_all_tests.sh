#!/usr/bin/env bash
# Run all tests: host pytest suite + on-board Pico tests + on-device syntax check.
set -uo pipefail
cd "$(dirname "$0")"
VENV=/home/zukw/python-venvs/wassergenossenschaft/.venv/bin/python
M="${MPREMOTE:-/home/zukw/.local/bin/mpremote}"

echo "===== 1) host pytest ====="
$VENV -m pytest -q || exit 1

echo "===== 2) on-board syntax check ====="
timeout 60 $M run pico_tests/syntax_check.py || exit 1

echo "===== 3) on-board tests (steps 1-5b) ====="
for t in test_step1_board test_step2_board test_step3_board test_step4_board test_step5_board test_step5b_board; do
    echo "--- $t ---"
    timeout 90 $M run "pico_tests/$t.py" || exit 1
done

echo "===== ALL TESTS PASSED ====="
