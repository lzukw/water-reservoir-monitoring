# Water-Meter-Observing process

One of the camera films the water-meter, which is metering the amount if water flowing from to the reservoir to all consumers. The Water-Meter-Observing process reads this image from the Redis RAM database, extracts the water-meter value and stores it in the Redis RAM database (along with the timestamp).

Run with the following command:

```bash
cd path/to/water-meter  

# Current Working directors now contains the subfolder water-meter

python3 -m water_meter --wrap-around-value=1000000.0 --camera-number=0 --rois='[[10,10,8,8],[20,10,8,8],[30,10,8,8],[40,10,8,8],[50,10,8,8],[60,10,8,8]]' --redis-server=main-raspberry-pi --log-level=INFO
```

See the command-line arguments for configuration options (or use --help from the command-line to see all options).

There is also a systemd service file `water-reservoir-water-meter-observing.service` for the water-meter-observing process, which can be used to start the process automatically on system startup.
