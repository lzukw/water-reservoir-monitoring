# Water-level observing process

The water-level observing process periodically measures the water level of a water reservoir, and stores the measurement-value with its corresponding timestamp in the Redis-Database.

There is a project for a Raspberry Pi Pico W using a HCSR04 ultrasonic sensor in the folder `Raspberry-Pi-Pico_water-level`

Issue: The Redis-Server, and the Redis-Keys and -hashkeys are hardcoded into the Raspberry-Pi-Pico-Micropython-Code.

## Dummy-water-level process

The `dummy_linux_water-leve.py`-process simulates the water-level observing process on a Linux machine. It periodically generates a random water-level value, and stores it in the Redis-Database with its corresponding timestamp.

This can be used while the Raspberry-Pi-Pico is not available, or to test the Redis-Database and the Redis-Receiver process.

There is also a systemd service file `water-reservoir-dummy-linux-water-level.service` for the dummy-water-level process, which can be used to start the process automatically on system startup.