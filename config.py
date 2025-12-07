"""
Robot Control Server Configuration
"""

# Serial connection settings
SERIAL_PORT = "/dev/ttyACM0"
SERIAL_BAUD = 9600
SERIAL_TIMEOUT = 1.0  # seconds

# Arduino failsafe timeout (must send commands more frequently than this)
ARDUINO_FAILSAFE_MS = 2000

# WebSocket server settings
WS_HOST = "0.0.0.0"  # Listen on all interfaces
WS_PORT = 8765

# Authentication
AUTH_TOKEN = "robot_secret_2024"  # Change this in production!

# Valid movement commands
VALID_DIRECTIONS = ["forward", "backward", "left", "right", "stop"]

# Command mapping: JSON direction -> Arduino command
DIRECTION_TO_COMMAND = {
    "forward": "FORWARD",
    "backward": "BACKWARD",
    "left": "LEFT",
    "right": "RIGHT",
    "stop": "STOP",
}

# Reconnection settings
SERIAL_RECONNECT_DELAY = 2.0  # seconds between reconnection attempts
MAX_RECONNECT_ATTEMPTS = 10

# Logging
LOG_LEVEL = "DEBUG"  # DEBUG, INFO, WARNING, ERROR
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

