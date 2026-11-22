import os
import sys

# Make the Pico project files (logger.py, config.py, ...) importable on the host.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
# microdot / utemplate live in lib/
LIB = os.path.join(PROJECT_ROOT, "lib")
if LIB not in sys.path:
    sys.path.insert(0, LIB)
