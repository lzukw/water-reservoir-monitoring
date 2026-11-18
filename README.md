# Project for the monitoring the water reservoir of the Zwettl an der Rodl water cooperative

Cameras and sensors are used to monitor the water reservoir of the Zwettl an der Rodl water cooperative. The images and sensor-data should be viewable from any mobile phone or other device via a web browser.

## Equipment and Overview:

- A Fritzbox, reachable from the internet via a resolvable DNS name. Portforwarding to the Main-Raspberry-Pi
- Main Raspberry Pi providing the Web-interface, data-processing and Data-Exchange between devices.
- Raspberry Pi Picos controlling LED strips (background lighting for camera images)
- Raspberry Pis (possibly Raspberry PiZeros) with webcams
- One of the cameras records the water meter reading, which is used to measure the consumption of the entire local water network. This reading is to be recognized from the camera image using image processing, and recorded as a sensor value
- Raspberry Pi Pico reading with an ultrasonic distance sensor the water level in the reservoir.

The project is a distributed system, and communication takes place over the Fritzbox WLAN.

### Main-Raspberry Pi

The following processes / services run on the Main-Raspberry-Pi:

- Redis database for Data-Exchange between all processes and devices. Here all Raspberry Pi (Pico) devices with cameras or sensors continuously store their current image or sensor-measureent-values. Images and measurements are timestamped.
- Filesystem "database": In the `data` directory, a data service process stores:
  - sensor measurements (water level, water meter reading) at reasonable intervals, e.g. every 5 minutes
  - history of camera images
- Web server (Python Flask) for the user website
- Reverse proxy for the web server

For not yet implemented processes, there are dummy processes that generate constant or random sensor-valuesor images. These are placeholders for the real processes that will be implemented later. The duummy processes also run on the main Raspberry Pi.

The Main-Raspberry-Pi could also run the first camera-Process, and the Process for extracting the water meter reading from the camera image.

### Other Raspberry Pi devices

Another Raspberry Pi (could be a Raspberry Pi Zero) captures images from a camera observing the water-reservoir and the water coming from the springs.

### Raspberry Pi Pico devices

One Raspberry Pi Pico is connected to an ultrasonic distance sensor and continuously measures the water level in the reservoir. The measured values are stored in the Redis-Database under the key `water-level`.

Other Raspberry Pi Pico devices are connected to LED strips and control the background lighting for the cameras. The LED strips are turned on all the time, and there is no communication with between the Raspberry Pi Picos and the Main-Raspberry-Pi.

## File- and Folder-structure in this repository

All source-code is in the folder `src`; runtime data (camera images, sensor CSV-files) is in the folder `data` at the repository root.

### Folder `redis`

How to setup the redis-database on the Main-Raspberry-Pi. The redis-database is used as central Data-Exchange-point between all processes and devices.

**First variant with podman (Folder** `redis-with-podman`**):**

Here are quadlet-files to start Microservices in Containers (e.g Redis-Database) on the Main-Raspberry-Pi.

**Second variant Redis installed directly on the Main-Raspberry-Pi (Folder** `redis-with-apt`**):**

Here are instructions to install Redis directly on the Main-Raspberry-Pi with `apt install redis-server` and configure it.

### Folder `src/camera`

Contains the python-file for a Camera-Process, which captures camera-images and stores them in a redis-database.

There is also a dummy-camera-python-process, and systemd-service-unit-files. for letting systemd manage the processes

### Folder `src/water-meter`

Contains a python-Process for continously reading the water meter value from the camera image (fetched from the redis-database). The meter-value is extraced with image-processing and stored in the Redis-Database.

There is also a systemd-service-unit-file for letting systemd manage the process.

### Folder `src/water-level`

- Subfolder `Raspberry_Pi_Pico`: Contains the code for the Raspberry Pi Pico, which is connected to an ultrasonic distance sensor and continuously measures the water level in the reservoir. The measured values are stored in the Redis-Database.

There is also a dummy-process that can be started on a Raspberry Pi, and that stores dummy-water-level-values in the redis-database. This can be used for testing instead of the Raspberry Pi Pico. Also, there is a systemd-service-unit-file for this dummy process.

### Folder `src/led-strip`

Contains the code for a Raspberry Pi Pico, which is connected to an LED strip and controls the background lighting for the cameras. The LED strip is turned on all the time, and there is no communication with between the Raspberry Pi Picos and the Main-Raspberry-Pi. The colour (R-, G, B- and W-values) of the LED strip can be set by a user via the web-interface, running direktly on the Raspberry Pi Pico.

## Setup of the Main-Raspberry-Pi

Flash SD-Card with Raspberry-Pi-Imager, setup user `pi` and strong password. Hostname is `main-raspberry-pi`. Setup Wifi to connect to the local router (Fritzbox). Enable ssh.

Always needed:

```sh
sudo apt update
sudo apt full-upgrade
sudo apt install vim net-tools picocom micropython-mpremote avahi-daemon
```

`sudo raspi-config` ausführen:

- Boot nur zur Console (keine grafische Oberfläche),
- kein autologin
- kein Slpash-Screen
- kein Screen-Blanking
- Interface-Options: login-shell über Serial-Console enablen
- Locale: de_AT.UTF8 und en_GB.UTF8, default ist de_AT.UTF8
- Locale: WLAN-Country: AT

### Network

Have a look at the files in `/etc/netplan/90-NM-...` One of them is for Ethernet, the other for WLAN. Edit the one for WLAN. Under the line `      dhcp4: true` add (equally indeted) the line `      dhcp6: true` (Which also means SLAAC, if no stateful DHCPv6-Server ist available).

Try with `sudo netplan try`. If everything works well: `sudo netplan appy`

### Install python-libraries on main-raspberry-pi

Some python-processes need special python-libraries, which are available in the apt-repos.

```sh
sudo apt update
sudo apt install -y python3-redis python3-opencv python3-croniter python3-flask python3-openpyxl
```

### Updades and regular reboots

```sh
sudo apt install -y unattended-upgrades
sudo dpkg-reconfigure unattended-upgrades
```

Answer the question with "Yes".

Create file `/etc/systemd/system/daily-reboot.service`:

```ini
[Unit]
Description=Daily reboot

[Service]
Type=oneshot
ExecStart=/usr/bin/systemctl reboot
```

Create file `/etc/systemd/system/daily-reboot.timer`:

```ini
[Unit]
Description=Reboot every day at 03:02

[Timer]
OnCalendar=*-*-* 03:02:00
Persistent=true
Unit=daily-reboot.service

[Install]
WantedBy=timers.target
```

Enable and check timer-Unit:

```sh
sudo systemctl daemon-reload
sudo systemctl enable --now daily-reboot.timer
systemctl list-timers daily-reboot.timer
```

### Setup Redis-Server

```sh
sudo apt install -y redis-server
```

Edit file `/etc/redis/redis.conf`:

- Look for line `bind 127.0.0.1 -::1` and replace with `bind 0.0.0.0 ::`
- Look for line `protected-mode yes` and replace with `protected-mode no`

Restart the redis-service: `sudo systemctl restart redis-server`

Test the Redis-server from another device in the same LAN:

```sh
$ redis-cli -h main-raspberry-pi
main-raspberry-pi:6379> SET test hello
OK
main-raspberry-pi:6379> GET test
"hello"
main-raspberry-pi:6379> quit
$ 
```

### Data-Collector service on main-raspberry-pi

Create folders:

```sh
mkdir /home/pi/water-reservoir-monitoring
mkdir /home/pi/water-reservoir-monitoring/data
mkdir /home/pi/water-reservoir-monitoring/data/camera-images
mkdir /home/pi/water-reservoir-monitoring/data/camera-images/camera-0
mkdir /home/pi/water-reservoir-monitoring/data/camera-images/camera-1
mkdir /home/pi/water-reservoir-monitoring/data/water-level
mkdir /home/pi/water-reservoir-monitoring/data/water-meter
touch /home/pi/water-reservoir-monitoring/data/water-level/sensor-data.csv
touch /home/pi/water-reservoir-monitoring/data/water-meter/sensor-data.csv
```

*Note:* For the following commands, the current working directory is assumed have been copied to the main-raspberry-pi `/home/pi/water-reservoir-monitoring/src`, for example with:

```sh
# On the main-raspberry-pi:
mkdir -p /home/pi/water-reservoir-monitoring/src

# On the development machine:
rsync -av /path/to/water-reservoir-monitoring/ pi@main-raspberry-pi:/home/pi/water-reservoir-monitoring/src/`
```

The data-collector source-code is already at `/home/pi/water-reservoir-monitoring/src/data-collector/` (copied above).

Create file `/home/pi/water-reservoir-monitoring/water-reservoir-data-collector.service` based on `src/data-collector/water-reservoir-data-collector.service`:

```sh
cp /home/pi/water-reservoir-monitoring/src/data-collector/water-reservoir-data-collector.service /home/pi/water-reservoir-monitoring/
```

Create symlink in `/etc/systemd/system/`, then enable and start service:

```sh
sudo ln -s /home/pi/water-reservoir-monitoring/water-reservoir-data-collector.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now water-reservoir-data-collector.service
# Check for errors:
sudo systemctl status water-reservoir-data-collector.service
sudo journalctl -f -u water-reservoir-data-collector.service
```

### Picam as Camera 0 on main-raspberry-pi and as Camera 1 on cam1-raspberry-pi

The main-raspberry-pi also has a pi-camera, connected with a ribbon-cable to the 15-pole-connector. The camera is observing the water-meter.

```sh
# Test camera (also works without GUI in headless mode)
rpicam-hello
```

The camera source-code is already at `/home/pi/water-reservoir-monitoring/src/camera/` (copied above).

Choose Camera-Type to use: DummyCamera, PiCamera or UsbCamera: Edit `/home/pi/water-reservoir-monitoring/src/camera/camera/__main__.py` and uncomment just one of the three lines under the line `### Select the camera class to use. ...`

Create a file `/home/pi/water-reservoir-monitoring/water-reservoir-pi-cam-0.service`:

```sh
cp /home/pi/water-reservoir-monitoring/src/camera/water-reservoir-camera-1.service /home/pi/water-reservoir-monitoring/water-reservoir-camera-0.service
```

**Changes to this file:**

- This file is for Camera 1 (on the cam1-raspberry-pi) and must be slightly changed: As commandline-arguments use `--camera-number 0` and `--redis-server=127.0.0.1`.

- Later, when the service is running and the camera is mounted in its final position, the camera-image can be tested: From another machine `python src/tests/test_camera_and_redis-receiver.py --camera-number 0 --redis-server=main-raspberry-pi` can be used show the live images and adjust the camera-position.

- '--rois-to-mark` is not used anymore, because the Water-Meter-Algorithm finds the position of the ROI containing the water-meter-digits automatically.

On the `cam1-raspberry-pi` this file can be used as is: `water-reservoir-pi-cam-1.service`. 

Create a link in `/etc/systemd/system` and enable and start the service

```sh
sudo ln -s /home/pi/water-reservoir-monitoring/water-reservoir-camera-0.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now water-reservoir-camera-0.service
# Check for errors:
sudo systemctl status water-reservoir-camera-0.service
sudo journalctl -f -u water-reservoir-camera-0.service
```

Test from other device in the same LAN with redis-cli:

```sh
$ redis-cli -h main-raspberry-pi
main-raspberry-pi:6379> hget cam:0 timestamp
"2026-10-25 18:09:53.286"
main-raspberry-pi:6379> hget cam:0 timestamp
"2026-10-25 18:09:54.670"
main-raspberry-pi:6379> hget cam:0 timestamp
"2026-10-25 18:09:55.451"
main-raspberry-pi:6379> quit
$
```

...or test with `python src/tests/test_camera_and_redis-receiver.py --camera-number 0 --redis-server=main-raspberry-pi`

### Water-meter service on main-raspberry-pi

The water-meter source-code is already at `/home/pi/water-reservoir-monitoring/src/water-meter/` (copied above).

Create the file `/home/pi/water-reservoir-monitoring/water-reservoir-water-meter.service`:

```sh
cp /home/pi/water-reservoir-monitoring/src/water-meter/water-reservoir-water-meter.service /home/pi/water-reservoir-monitoring/
```

Adjust ROIs as needed (see above)!

Create symlink in `/etc/systemd/system/`, then enable and start service:

```sh
sudo ln -s /home/pi/water-reservoir-monitoring/water-reservoir-water-meter.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now water-reservoir-water-meter.service
# Check for errors:
sudo systemctl status water-reservoir-water-meter.service
sudo journalctl -f -u water-reservoir-water-meter.service
```

Test from other device in the same LAN:

```sh
$ $ redis-cli -h main-raspberry-pi
main-raspberry-pi:6379> hgetall water-meter
1) "timestamp"
2) "2026-11-02T18:48:45.142Z"
3) "value"
4) "0.1631359466220027"
main-raspberry-pi:6379> hgetall water-meter
1) "timestamp"
2) "2026-11-02T18:49:05.328Z"
3) "value"
4) "0.20881829794049075"
main-raspberry-pi:6379> quit
$
```

### Flask-Website on main-raspberry-pi

The flask-website source-code is already at `/home/pi/water-reservoir-monitoring/src/flask-website/` (copied above).

Create the file `/home/pi/water-reservoir-monitoring/water-reservoir-flask-website.service`:

```sh
cp /home/pi/water-reservoir-monitoring/src/flask-website/water-reservoir-flask-website.service /home/pi/water-reservoir-monitoring/
```

**Note:** The flask-server is listening on Port 5000 on all interfaces. This is secure in the local LAN, because the Fritzbox is configured to not forward Port 5000 to the internet. If later a reverse-proxy is configured, the comman-line-argument in `water-reservoir-flask-website.service` can be changed to: `--flask-host=127.0.0.1`

Create symlink in `/etc/systemd/system/`, then enable and start service:

```sh
sudo ln -s /home/pi/water-reservoir-monitoring/water-reservoir-flask-website.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now water-reservoir-flask-website.service
# Check for errors:
sudo systemctl status water-reservoir-flask-website.service
sudo journalctl -f -u water-reservoir-flask-website.service
```

On another device in the same LAN open a webbrowser and navigate to `http://main-raspberry-pi:5000`

### Reverse-Proxy on main-raspberry-pi

```sh
sudo apt update
sudo apt install caddy
```

Edit `/etc/caddy/Caddyfile` to contain:

**Just for http:**

```
{
    auto_https off
}

:80 {
    reverse_proxy 127.0.0.1:5000
}
```

**For https with let's encrypt:**

TODO


Validate and format the file to avoid Information in logs:

```sh
caddy fmt /etc/caddy/Caddyfile   # take this output and edit the file again
sudo caddy validate --config /etc/caddy/Caddyfile
```

Finally restart the service: `sudo systemctl restart caddy.service`

From a Webbrowser on another device in the same LAN navigate to `http://main-raspberry-pi` or `https://main-raspberry-pi` (if https is configured).


## LED-Strips

Every Raspberry Pi Pico with an LED-strip can be configured using Wifi and a Webbrowser. See [README.md in Folder src/led-strip](src/led-strip/README.md)

## Raspberry Pi Pico with Ultrasonic Distance Sensor

A Raspberry Pi Pico is connected to an ultrasonic distance sensor and continuously measures the water level in the reservoir. The measured values are stored in the Redis-Database under the key `water-level`.

The Raspberry Pi Pico can be configured using Wifi and a Webbrowser. See [README.md in Folder src/water-level](src/water-level/README.md)

## Raspberry Pi for Camera 1

Follow the Instruction for the Main-Raspberry-Pi, but use hostname `cam1-raspberry-pi`. The camera is observing the water-reservoir and the water coming from the springs.

Only setup `water-reservoir-camera-1.service` on this Raspberry Pi. The other services (`water-reservoir-data-collector.service`, `water-reservoir-water-meter.service`, `water-reservoir-flask-website.service`) are only needed on the Main-Raspberry-Pi.
