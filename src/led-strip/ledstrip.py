import gc

import neopixel
from machine import Pin

from logger import Logger

DEFAULT_SIGNAL_PIN = 28  # GPIO pin for the NeoPixel LEDs
DEFAULT_BRIGHTNESS = 255  # Default brightness for the RGBW LEDs

class LEDStrip:
    def __init__(self, 
                 signal_pin_no: int = DEFAULT_SIGNAL_PIN, 
                 num_leds: int = 1, 
                 brightness: int = DEFAULT_BRIGHTNESS,
                 log_level: str = "INFO"):
        self._signal_pin_no :int = signal_pin_no
        self._pin : Pin = Pin(self._signal_pin_no, Pin.OUT)
        self._num_leds = num_leds
        self._brightness = 0
        self._logger : Logger = Logger(name="LEDStrip", level=log_level)
        self.brightness = brightness  # Set initial brightness and update LEDs

    @property
    def pin_no(self) -> int:
        return self._signal_pin_no

    @property
    def num_leds(self) -> int:
        return self._num_leds

    @num_leds.setter
    def num_leds(self, value: int):
        self._num_leds = max(0, value)
        self._set_brightness_for_neopixels()

    @property
    def brightness(self) -> int:
        return self._brightness

    @brightness.setter
    def brightness(self, value: int):
        self._brightness = max(0, min(255, value))  # Clamp brightness between 0 and 255
        self._set_brightness_for_neopixels()

    def _set_brightness_for_neopixels(self):
        np = neopixel.NeoPixel(self._pin, self._num_leds, bpp=4)
        np.fill( (0,0,0, self._brightness) ) # alle LEDs auf Weiß setzen
        np.write()
        np = None
        gc.collect()  # Free up memory after updating the brightness
        self._logger.log("INFO", f"Brightness set to {self._brightness} for {self._num_leds} LEDs. Signal pin: {self._signal_pin_no}")


if __name__ == "__main__":
    import time
    led_strip = LEDStrip(signal_pin_no=28, num_leds=20, brightness=0)
    for brightness in range(0, 256, 25):
        led_strip.brightness = brightness
        time.sleep(0.5)

    led_strip.num_leds = 20
    led_strip.brightness = 0
    led_strip.num_leds = 0
    led_strip.brightness = 10

    for num_leds in range(21):
        led_strip.num_leds = num_leds
        time.sleep(0.5)
