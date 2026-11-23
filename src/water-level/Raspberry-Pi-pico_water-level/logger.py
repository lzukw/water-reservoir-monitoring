"""Simple leveled logger that works on MicroPython (Pico) and CPython (host tests).

Usage:
    logger = Logger("MAIN")
    logger.info("hello")
    logger.error("something failed")
"""

_LEVELS = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "FATAL": 50}


class Logger:
    def __init__(self, name: str, level: str = "INFO"):
        self.name = name
        self.level = _LEVELS.get(str(level).upper(), 20)

    def set_level(self, level: str) -> None:
        self.level = _LEVELS.get(str(level).upper(), self.level)

    def log(self, level: str, message: str) -> None:
        lvl = _LEVELS.get(str(level).upper(), 20)
        if lvl < self.level:
            return
        print(f"[{level.upper():7s}] {self.name}: {message}")

    def debug(self, message: str) -> None:
        self.log("DEBUG", message)

    def info(self, message: str) -> None:
        self.log("INFO", message)

    def warning(self, message: str) -> None:
        self.log("WARNING", message)

    def error(self, message: str) -> None:
        self.log("ERROR", message)

    def fatal(self, message: str) -> None:
        self.log("FATAL", message)
