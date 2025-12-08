"""
Configuration for Robot Control WebSocket Server
Adapted for Raspberry Pi
"""

# Serial Communication Settings
SERIAL_PORT = "/dev/ttyACM0"  # Raspberry Pi default for Arduino
SERIAL_BAUD = 9600
SERIAL_TIMEOUT = 1.0  # seconds

# WebSocket Server Settings
WS_HOST = "0.0.0.0"  # Listen on all interfaces
WS_PORT = 8765

# Authentication
AUTH_TOKEN = "robot_secret_2024"  # CHANGE THIS IN PRODUCTION!

# Failsafe Settings
COMMAND_TIMEOUT = 2.0  # seconds - stop if no command received

# Logging
LOG_LEVEL = "INFO"  # DEBUG, INFO, WARNING, ERROR
LOG_FILE = "robot_control.log"

# Camera Settings
CAMERA_ENABLED = True
CAMERA_WIDTH = 640
CAMERA_HEIGHT = 480
CAMERA_FPS = 15  # Lower FPS for smoother streaming over network

