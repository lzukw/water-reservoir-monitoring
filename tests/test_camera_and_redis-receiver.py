"""Read image bytes from Redis and display them with OpenCV.
See command-line arguments for configuration options (or use --help
from the command-line to see all options)
"""

import argparse
import logging
import sys

import cv2
import numpy as np

import redis


def parse_args() -> argparse.Namespace:
	"""Parse command line arguments."""
	parser = argparse.ArgumentParser(description="Read images from Redis and display them.")
	parser.add_argument("--camera-number", type=int, default=0, help="Global camera number")
	parser.add_argument("--redis-server", type=str, default="main-raspberry-pi", help="DNS-Name or IP of Redis-Server. (Default: 'main-raspberry-pi')")
	parser.add_argument("--redis-port", type=int, default=6379, help="Port-Number of Redis-Server")
	parser.add_argument("--redis-key-camera", type=str, default="cam", help="First Part of Redis-Key, whole key will add : and camera-number")
	parser.add_argument("--redis-hashkey-timestamp", type=str, default="timestamp", help="Hash(Dicctionary)Key for timestamps")
	parser.add_argument("--redis-hashkey-value", type=str, default="value", help="Hash(Dicctionary)Key for image-bytes")
	parser.add_argument("--log-level", type=str, default="INFO", help="Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)")
	return parser.parse_args()

ARGS = parse_args()

CAMERA_NUMBER: int = ARGS.camera_number

REDIS_SERVER: str = ARGS.redis_server
REDIS_PORT: int = ARGS.redis_port
REDIS_KEY_CAMERA: str = ARGS.redis_key_camera
REDIS_KEY: str = f"{REDIS_KEY_CAMERA}:{CAMERA_NUMBER}"
REDIS_HASHKEY_VALUE: bytes = ARGS.redis_hashkey_value.encode("ascii")
REDIS_HASHKEY_TIMESTAMP: bytes = ARGS.redis_hashkey_timestamp.encode("ascii")
LOG_LEVEL: str = ARGS.log_level.upper()

logging.basicConfig(
	stream=sys.stdout,
	level=getattr(logging, LOG_LEVEL, logging.INFO),
	format="%(asctime)s [%(levelname)s] %(message)s",
	datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

logger.info(f"Redis-Server and -port: {REDIS_SERVER}:{REDIS_PORT}")
logger.info(f"Camera number: {CAMERA_NUMBER}")
logger.info(f"Redis key for camera: '{REDIS_KEY}' with hash keys '{REDIS_HASHKEY_TIMESTAMP.decode()}' and '{REDIS_HASHKEY_VALUE.decode()}'")


def main() -> int:
	
	redis_client = redis.Redis(host=REDIS_SERVER, port=REDIS_PORT)
	redis_client.ping()

	window_name = f"Redis - {REDIS_KEY}"
	logger.info("Read loop started for Redis key '%s'", REDIS_KEY)

	try:
		while True:
			redis_val = redis_client.hgetall(REDIS_KEY)
			assert isinstance(redis_val, dict)
			frame_bytes = redis_val[REDIS_HASHKEY_VALUE]
			frame_timestamp = redis_val[REDIS_HASHKEY_TIMESTAMP]

			image_bytes: bytes | None = None
			if isinstance(frame_bytes, (bytes, bytearray, memoryview)):
				image_bytes = bytes(frame_bytes)
			elif isinstance(frame_bytes, str):
				image_bytes = frame_bytes.encode("utf-8")

			if image_bytes is not None:
				frame_array = np.frombuffer(image_bytes, dtype=np.uint8)
				frame = cv2.imdecode(frame_array, cv2.IMREAD_COLOR)

				if frame is not None:
					cv2.imshow(window_name, frame)

					if isinstance(frame_timestamp, bytes):
						timestamp_text = frame_timestamp.decode("utf-8", errors="replace")
					elif isinstance(frame_timestamp, str):
						timestamp_text = frame_timestamp
					else:
						timestamp_text = "<unknown>"

					logger.debug("Displayed frame from %s", timestamp_text)

			if cv2.waitKey(30) & 0xFF == ord("q"):
				logger.info("Stop requested with key 'q'")
				break
	except KeyboardInterrupt:
		logger.info("Stop requested by keyboard interrupt")
	finally:
		cv2.destroyAllWindows()

	return 0


if __name__ == "__main__":
	raise SystemExit(main())
