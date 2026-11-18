# Water-Meter-Observing process

One of the cameras films the water-meter, which meters the amount of water flowing from the reservoir to all consumers. The Water-Meter-Observing process (`water_meter` module) works in a loop:

1. Reads the latest camera-image (JPEG bytes) from the Redis RAM database (key `cam:<camera-number>`).
2. Extracts the ROI with the digits of the meter: SIFT feature-matching against a reference image, filtering to distinct matches, homography via RANSAC, perspective-warp of the camera image and cutting out the ROI defined in the reference-image JSON.
3. Stores the extracted digits-image as a new "camera-image" in Redis (key `cam:<store-camera-number>`), so it can be viewed like any other camera stream.
4. Extracts the meter-value from the digits-image.
5. Checks accuracy and plausibility (the value may only increase by a plausible amount since the last stored value, considering the wrap-around). Only then is it stored in Redis (key `water-meter`) together with the frame timestamp.

## Running

Run from this directory (`src/water-meter/`) with the project virtualenv active (or any Python with `opencv`, `numpy` and `redis` installed):

```bash
cd path/to/water-reservoir-monitoring/src/water-meter

python3 -m water_meter --redis-server=main-raspberry-pi --log-level=INFO
```

The current working directory must be `src/water-meter/`, because the reference-image JSON (`--reference-image-jsonfile`, default `./water_meter/reference_images.json`) and the reference-image path inside it (`./water_meter/water-meter-reference-image.jpg`) are relative to it.

All options can be listed with `python3 -m water_meter --help`.

## Most important command-line arguments

| Argument | Default | Meaning |
|---|---|---|
| `--redis-server` | `main-raspberry-pi` | DNS name or IP of the Redis server |
| `--redis-port` | `6379` | Port of the Redis server |
| `--period` | `5.0` | Delay between read attempts in seconds |
| `--redis-key-camera` | `cam` | First part of the input camera key; the full key is `<redis-key-camera>:<camera-number>` (e.g. `cam:0`) |
| `--camera-number` | `0` | Camera number of the water-meter camera |
| `--store-camera-number` | `2` | Camera number under which the extracted digits-image is stored (e.g. `cam:2`) |
| `--redis-key-water-meter` | `water-meter` | Redis key under which the extracted meter-value is stored |
| `--redis-hashkey-timestamp` | `timestamp` | Hash field for the timestamp (used for all keys) |
| `--redis-hashkey-value` | `value` | Hash field for the value (JPEG bytes for camera keys, float for the meter key) |
| `--reference-image-jsonfile` | `./water_meter/reference_images.json` | JSON file describing the reference image (see below) |
| `--wrap-around-value` | `1000000.0` | Value at which the meter reading wraps around to 0 |
| `--min-accuracy` | `0.9` | Minimum accepted accuracy of a read value; lower values are not stored |
| `--approx-daily-consumption` | `200.0` | Approximate daily water consumption in m³, used for the plausibility check |
| `--allowed-change-factor` | `5.0` | Allowed factor on top of the estimated consumption increase before a value is rejected as implausible |
| `--log-level` | `INFO` | Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL) |

## Redis data layout

Each key is a Redis hash with two fields (`timestamp`, `value` by default):

- `cam:0` — input: latest camera-image of the water-meter as JPEG bytes (written by the camera process)
- `cam:2` — output: extracted digits-ROI as JPEG bytes (written by this module)
- `water-meter` — output: extracted meter-value as float

## Reference image

`water_meter/reference_images.json` describes the reference images used for feature matching:

```json
{
    "reference_image_filepath" : "./water_meter/water-meter-reference-image.jpg",
    "roi" : [155, 194, 444, 85]
}
```

- `reference_image_filepath`: path to the reference image (relative to the working directory). If the image has an alpha channel (4 channels, e.g. PNG), the alpha channel is thresholded and used as feature-search mask.
- `roi`: `[x, y, width, height]` of the digits region in the reference image.

## systemd service

`water-reservoir-water-meter.service` starts the process automatically on system startup. Adjust `WorkingDirectory` and the arguments in `ExecStart` to your setup (note: the shipped example still contains arguments like `--rois` that this module does not accept — remove them).

## Integration test

`src/tests/040_test_water_meter_module.py` runs the whole chain locally: it starts its own redis-server on localhost, stores a water-meter photo as `cam:0`, starts the module as a subprocess, displays the resulting `cam:2` digits-image and prints the `water-meter` value from Redis until a duration elapses:

```bash
cd path/to/water-reservoir-monitoring
.venv/bin/python src/tests/040_test_water_meter_module.py --duration 30
```

## Experiments

The `Experiments/` folder contains the Jupyter notebooks (`010_`–`050_`) and image data used to develop the feature-matching / ROI-extraction pipeline, plus `200_Experiments-for-lightning` with photos for lighting experiments.
