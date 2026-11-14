# Camera Process

Images are continuously read from the camera and stored in the Redis RAM database.

## Requirements

```sh
sudo apt update
sudo apt install python3-opencv
sudo apt install python3-redis
# When using pi-cam (connected with ribbon-cable)
sudo apt install python3-picamera2
```

On a Raspberry-Pi python-picamera2 should already be installed.

On a Linux-PC when using an USB-Camera: In `camera/__init__.py` comment out the line `camera/__init__.py`

### Selecting the concrete Camera-Class

There are three Camera-Classes available:

1. `DummyCamera` producing just random images (like white-noise-images)
1. `UsbCamera`
1. `PiCamera`

In `__main__.py` look for the comment 

```python
### Select the camera class to use. You can change this to DummyCamera, PiCamera, or UsbCamera as needed.
```

Below this comment there are three import-Statements. Just one of them must be active, the other two must be commented out. Select the one you want tu use.

## Command-line arguments

The program is started with:

```sh
python3 -m /path/to/camera \
     --camera-number 0 \
     --device 0 \
     --redis-server=localhost
```

**Attention:* `path/to/camera` means the folder `camera` containing `__main__.py` and `__init__.py`

Use `--help` to get a list of all command-line arguments.

The `--camera-number` is the global camera number in the Redis-Database. It is used for the key in the database, for example `cam:0` (here, `cam` is the prefix, which can be changed with the command-line argument `--redis-key-camera`).

For a USB-Camera the `--device` can be either an integer or the path to the Video-Device like `/dev/video0`. If there is only one Camera connected to the device, just use the default-Value `0`.

If using a Raspberry Pi with a Pi-Cam: The Pi-cam is connected with a ribbon-cable to the 15-pin-connector on the Raspberry Pi (metallic pins facing away from the flap). For `--device` an integer must be passed (0 or 1, if there are two cameras connected to the Raspberry Pi). If there is only one Camera just leave the default-Value of 0

If a Raspberry Pi has multiple cameras, multiple processes can be started accordingly.

### Command-line-argument `--rois-to-mark`

This is list of list of four integers each, for example --rois-to-mark='[[10,10,620,460],[20,20,600,440]]' The integers define a rectangle that will be drawn onto the camera-image to visualize Regions of Interrest (ROIs). First integer is x-position of the top-left-corner of the rectange, the second inteteger is the y-position. Third integer is the  width and the fourth integer the height of the rectangle.

## Starting as a systemd-service

A sample systemd-service-file is available in `water-reservoir-camera-1.service`. The command-line arguments and the path to the `camera`-Folder (containing `__init__.py`, `__main__.py`) in `ExecStart` must be adjusted there as needed.
