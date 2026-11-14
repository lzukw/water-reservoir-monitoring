# Data-collector-Service

The data-collector service is responsible for collecting data at certian timestamps from the Redis-Database (Water-level and Water-Meter-Value, as well as camera-images) an store them in a "file-system-database" (A csv-File and a folder with images). 

The timestamps for collecting sensor-values and camera-images are defined as cron-expressions. See command line arguments below for more information (or run with `pyton data_collector.py --help`). 

See also the data-Folder for sample data-sets.
