# tinytest.py - minimal test runner for MicroPython, runs ON the Pico.
# MicroPython has no pytest, so pico_tests/*.py use plain asserts + this runner:
#
#     from tinytest import run_tests
#     def test_something():
#         assert 1 + 1 == 2
#     if __name__ == "__main__":
#         run_tests(globals())
#
# Run on the board from the laptop with:
#     mpremote run pico_tests/test_xxx.py
import sys


def run_tests(namespace):
    passed = 0
    failed = 0
    for name in sorted(namespace):
        obj = namespace[name]
        if name.startswith("test_") and callable(obj):
            try:
                obj()
                print("PASS", name)
                passed += 1
            except Exception as e:
                print("FAIL", name, "->", repr(e))
                failed += 1
    print("--- {} passed, {} failed ---".format(passed, failed))
    if failed:
        sys.exit(1)
