class Logger:
    LEVELS: dict[str,int] = {"DEBUG": 3, "INFO": 2, "WARNING": 1, "ERROR": 0, "FATAL": 0}  # noqa: RUF012
    def __init__(self, name: str, log_level: str = "INFO"):
        self._name = name
        self._log_level : int = self.LEVELS.get(log_level.upper(), 2)

    def log(self, level: str, message: str):
        if self.LEVELS.get(level, -1) > self._log_level:
            return
        print(f"[{level}] {self._name}: {message}")

    def set_level(self, log_level: int):
        self._log_level = log_level

if __name__ == "__main__":
    logger = Logger("TESTLogger", "INFO")
    print("DEBUG-Message (should not be printed):")
    logger.log("DEBUG", "This is a debug message.")
    logger.log("INFO", "This is an info message.")
    logger.log("WARNING", "This is a warning message.")
    logger.log("ERROR", "This is an error message.")
    logger.log("FATAL", "This is a fatal message.")