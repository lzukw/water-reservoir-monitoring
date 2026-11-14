# podman Redis-Microservice

## Quick start

```sh
sudo ln -s ~/water-reservoir-monitoring/podman/water-reservoir-podman.network /etc/containers/systemd/
sudo ln -s ~/water-reservoir-monitoring/podman/water-reservoir-redis-podman.container /etc/containers/systemd/
sudo systemctl daemon-reload
sudo systemctl start water-reservoir-redis-podman.service
```

## Troubleshooting

Use `sudo journalctl -f` to monitor each command, especially `daemon-reload`. Errors in the quadlet files are only shown there.

## Redis

Redis is a NoSQL database kept in RAM. It is a simple key-value store whose values can be written or read over the network.

### Testing from the command line when the container is running

From another machine:

```sh
sudo apt update && sudo apt install redis-tools
```

```sh
zukw@zukw-surf-pro8:~$ redis-cli -h fd17::8ad2:a2d4:6ed8:d5ae -p 6379
[fd17::8ad2:a2d4:6ed8:d5ae]:6379> PING
PONG
[fd17::8ad2:a2d4:6ed8:d5ae]:6379> SET kamera:bild:1 "Here would be binary data"
OK
[fd17::8ad2:a2d4:6ed8:d5ae]:6379> GET kamera:bild:1
"Here w\xc3\xa4ren Bin\xc3\xa4rdaten"
[fd17::8ad2:a2d4:6ed8:d5ae]:6379> exit
zukw@zukw-surf-pro8:~$
```

Also HSET and HGET can be used to store and read dictionaries (hashes) in Redis.

### Using Redis with Python

```sh
sudo apt update && sudo apt install python3-redis
```

```python
>>> import redis
>>> redis_client = redis.Redis(host='127.0.0.1', port=6379, decode_responses=False)
>>> redis_client.set('kamera:1:bild', b'BinaryData')
True
>>> redis_client.get('kamera:1:bild')
b'BinaryData'
>>>
```

The `redis_client.set` and `redis_client.get` methods should be wrapped in try-except blocks to catch `redis.RedisError` xceptions, e.g. when the Redis server is not running.

Using Redis hashes (i.e. dictionaries)

```python
>>> redis_client.hset('kamera:1', mapping={'bild': b'BinaryData', 'timestamp': 12345})
2
>>> redis_client.hgetall('kamera:1')
{b'bild': b'BinaryData', b'timestamp': b'12345'}
>>>
```
