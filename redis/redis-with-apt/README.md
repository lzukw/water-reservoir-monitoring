# Setup Redis-Server with apt on Linux (e.g. Raspberry Pi OS)

```sh
sudo apt install redis-server
```

To allow access to the Redis-server from other devices, without any authentication or restrictions, you need to edit the configuration file and restart the service.

*Note:* A Firewall (Fritzbox) is used to restrict access to the Redis-server from outside the local network. The Redis-server is only accessible from devices in the same LAN.

Edit file `/etc/redis/redis.conf`:

- Look for line `bind 127.0.0.1 -::1` and replace with `bind 0.0.0.0 ::`
- Look for line `protected-mode yes` and replace with `protected-mode no`

Restart the redis-service: `sudo systemctl restart redis-server`

Test the Redis-server from another device in the same LAN (In the following example, the Redis-server is running on a Raspberry Pi with hostname `main-raspberry-pi`):

```sh
$ redis-cli -h main-raspberry-pi
main-raspberry-pi:6379> SET test hello
OK
main-raspberry-pi:6379> GET test
"hello"
main-raspberry-pi:6379> quit
$ 
```