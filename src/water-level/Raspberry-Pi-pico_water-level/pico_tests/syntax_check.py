# Runs ON THE PICO (via `mpremote run pico_tests/syntax_check.py`):
# syntax-checks (compile()) every project .py file that is deployed on the
# Pico filesystem. Prints one line per file and a final summary.
FILES = [
    "boot.py",
    "logger.py",
    "config.py",
    "timeutil.py",
    "ping.py",
    "redis_client.py",
    "ntp_time.py",
    "ultrasonic_sensor.py",
    "wifi.py",
    "web_app.py",
    "main.py",
]

ok = 0
failed = []
for fn in FILES:
    try:
        with open(fn, "r") as f:
            src = f.read()
    except OSError:
        print("MISSING  :", fn)
        failed.append(fn)
        continue
    try:
        compile(src, fn, "exec")
        print("SYNTAX OK:", fn)
        ok += 1
    except SyntaxError as e:
        print("SYNTAX ERR:", fn, e)
        failed.append(fn)

print(f"--- {ok} OK, {len(failed)} failed/missing ---")
if failed:
    raise SystemExit(1)
