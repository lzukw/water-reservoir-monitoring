# boot.py - runs once at startup, before main.py.
# All setup is done in main.py, so nothing to do here except a bit of cleanup.
import gc

gc.collect()
