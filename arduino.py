"""
Arduino Serial Communication Handler

Manages serial connection to Arduino Mega 2560 with auto-reconnect capability.
"""

import asyncio
import logging
import serial
import serial.tools.list_ports
from typing import Optional, Callable

import config

logger = logging.getLogger(__name__)


class ArduinoHandler:
    """Handles serial communication with Arduino motor controller."""

    def __init__(
        self,
        port: str = config.SERIAL_PORT,
        baud: int = config.SERIAL_BAUD,
        timeout: float = config.SERIAL_TIMEOUT,
    ):
        self.port = port
        self.baud = baud
        self.timeout = timeout
        self._serial: Optional[serial.Serial] = None
        self._connected = False
        self._lock = asyncio.Lock()
        self._reconnect_task: Optional[asyncio.Task] = None
        self._on_disconnect_callback: Optional[Callable] = None

    @property
    def connected(self) -> bool:
        """Check if Arduino is connected and responsive."""
        return self._connected and self._serial is not None and self._serial.is_open

    def set_disconnect_callback(self, callback: Callable) -> None:
        """Set callback to be called when Arduino disconnects."""
        self._on_disconnect_callback = callback

    async def connect(self) -> bool:
        """
        Establish serial connection to Arduino.
        Returns True if successful, False otherwise.
        """
        async with self._lock:
            if self.connected:
                return True

            try:
                # Close existing connection if any
                if self._serial:
                    try:
                        self._serial.close()
                    except Exception:
                        pass

                logger.info(f"Connecting to Arduino on {self.port} at {self.baud} baud...")

                self._serial = serial.Serial(
                    port=self.port,
                    baudrate=self.baud,
                    timeout=self.timeout,
                    write_timeout=self.timeout,
                )

                # Wait for Arduino to reset after serial connection
                await asyncio.sleep(2.0)

                # Clear any startup messages (Arduino may send status on connect)
                for _ in range(5):  # Try multiple times to drain buffer
                    if self._serial.in_waiting:
                        startup_msg = self._serial.read(self._serial.in_waiting)
                        logger.debug(f"Cleared startup data: {startup_msg}")
                        await asyncio.sleep(0.1)
                    else:
                        break

                # Test connection with PING
                if await self._ping():
                    self._connected = True
                    logger.info("Arduino connected and responding!")
                    return True
                else:
                    logger.warning("Arduino connected but not responding to PING")
                    self._serial.close()
                    return False

            except serial.SerialException as e:
                logger.error(f"Failed to connect to Arduino: {e}")
                self._connected = False
                return False

    async def _ping(self) -> bool:
        """Send PING command and check for response."""
        try:
            if not self._serial or not self._serial.is_open:
                return False

            self._serial.write(b"PING\n")
            self._serial.flush()

            # Wait for response with retry
            for attempt in range(3):
                await asyncio.sleep(0.15)

                if self._serial.in_waiting:
                    response = self._serial.readline().decode("utf-8").strip()
                    logger.debug(f"PING response (attempt {attempt + 1}): {response}")
                    
                    # Accept OK:PING or any OK response as success
                    if response == "OK:PING":
                        return True
                    elif response.startswith("OK:"):
                        logger.info(f"Arduino responded with {response}, accepting as connected")
                        return True

            logger.warning("No response to PING after 3 attempts")
            return False
        except Exception as e:
            logger.error(f"PING failed: {e}")
            return False

    async def disconnect(self) -> None:
        """Close the serial connection."""
        async with self._lock:
            self._connected = False
            if self._reconnect_task:
                self._reconnect_task.cancel()
                self._reconnect_task = None

            if self._serial:
                try:
                    # Send stop command before disconnecting
                    self._serial.write(b"STOP\n")
                    self._serial.flush()
                    await asyncio.sleep(0.1)
                    self._serial.close()
                except Exception as e:
                    logger.warning(f"Error during disconnect: {e}")
                finally:
                    self._serial = None
                    logger.info("Arduino disconnected")

    async def send_command(self, command: str) -> tuple[bool, str]:
        """
        Send a command to Arduino and wait for response.

        Args:
            command: Command string (e.g., "FORWARD", "STOP")

        Returns:
            Tuple of (success: bool, response: str)
        """
        if not self.connected:
            # Try to reconnect
            if not await self.connect():
                return False, "Arduino not connected"

        async with self._lock:
            try:
                # Send command
                cmd_bytes = f"{command}\n".encode("utf-8")
                self._serial.write(cmd_bytes)
                self._serial.flush()
                logger.debug(f"Sent: {command}")

                # Wait for response
                await asyncio.sleep(0.05)

                response = ""
                if self._serial.in_waiting:
                    response = self._serial.readline().decode("utf-8").strip()
                    logger.debug(f"Received: {response}")

                # Check for expected response format: OK:COMMAND
                expected = f"OK:{command}"
                if response == expected:
                    return True, response
                else:
                    logger.warning(f"Unexpected response: '{response}' (expected '{expected}')")
                    return False, response or "No response"

            except serial.SerialException as e:
                logger.error(f"Serial error: {e}")
                self._connected = False
                self._handle_disconnect()
                return False, f"Serial error: {e}"

            except Exception as e:
                logger.error(f"Command error: {e}")
                return False, str(e)

    def _handle_disconnect(self) -> None:
        """Handle unexpected disconnection."""
        self._connected = False
        if self._on_disconnect_callback:
            self._on_disconnect_callback()

        # Start reconnection task
        if self._reconnect_task is None or self._reconnect_task.done():
            self._reconnect_task = asyncio.create_task(self._auto_reconnect())

    async def _auto_reconnect(self) -> None:
        """Attempt to reconnect to Arduino automatically."""
        attempts = 0

        while attempts < config.MAX_RECONNECT_ATTEMPTS:
            attempts += 1
            logger.info(f"Reconnection attempt {attempts}/{config.MAX_RECONNECT_ATTEMPTS}...")

            await asyncio.sleep(config.SERIAL_RECONNECT_DELAY)

            if await self.connect():
                logger.info("Reconnected to Arduino!")
                return

        logger.error("Max reconnection attempts reached. Giving up.")

    async def move(self, direction: str) -> tuple[bool, str]:
        """
        Send movement command to Arduino.

        Args:
            direction: One of 'forward', 'backward', 'left', 'right', 'stop'

        Returns:
            Tuple of (success: bool, message: str)
        """
        direction = direction.lower()

        if direction not in config.VALID_DIRECTIONS:
            return False, f"Invalid direction: {direction}"

        command = config.DIRECTION_TO_COMMAND[direction]
        return await self.send_command(command)

    @staticmethod
    def find_arduino_ports() -> list[str]:
        """Find all potential Arduino serial ports."""
        ports = []
        for port in serial.tools.list_ports.comports():
            # Arduino Mega 2560 typically shows as ttyACM*
            if "Arduino" in (port.manufacturer or "") or "ttyACM" in port.device:
                ports.append(port.device)
                logger.debug(f"Found Arduino port: {port.device} ({port.description})")
        return ports


# Standalone test
if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.DEBUG, format=config.LOG_FORMAT)

    async def test():
        arduino = ArduinoHandler()

        # Find ports
        ports = ArduinoHandler.find_arduino_ports()
        print(f"Found Arduino ports: {ports}")

        # Connect
        if not await arduino.connect():
            print("Failed to connect to Arduino")
            sys.exit(1)

        # Test commands
        for direction in ["forward", "stop", "backward", "stop", "left", "stop", "right", "stop"]:
            success, response = await arduino.move(direction)
            print(f"{direction}: {success} - {response}")
            await asyncio.sleep(0.5)

        await arduino.disconnect()

    asyncio.run(test())

