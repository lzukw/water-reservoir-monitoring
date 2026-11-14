from .dummy_camera import DummyCamera

# On Linux-PCs there is no Picamera2
try:
    from .pi_camera import PiCamera
except ImportError:
    pass

from .redis_client import RedisClient, RedisHash, RedisStatus
from .usb_camera import UsbCamera

__all__ = [ "DummyCamera", "PiCamera", "RedisClient","RedisHash", "RedisStatus", "UsbCamera" ]
            