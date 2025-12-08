#!/usr/bin/env python3
"""
Arduino Handler for Robot Control
Manages serial communication with Arduino for motor control.
"""

import serial
import serial.tools.list_ports
import time
import logging
import threading
from typing import Optional

import config

logger = logging.getLogger(__name__)


class ArduinoHandler:
    """
    Handles serial communication with Arduino for motor control.
    Thread-safe with automatic reconnection support.
    """
    
    # Valid movement commands
    VALID_COMMANDS = {'FORWARD', 'BACKWARD', 'LEFT', 'RIGHT', 'STOP', 'PING'}
    
    def __init__(self):
        self.serial: Optional[serial.Serial] = None
        self.lock = threading.Lock()
        self.connected = False
        self.last_command_time = time.time()
        
    def find_arduino_port(self) -> Optional[str]:
        """
        Find Arduino by checking common serial ports.
        Returns port path if found, None otherwise.
        """
        # Common Arduino ports on Raspberry Pi
        common_ports = [
            config.SERIAL_PORT,  # Try configured port first
            "/dev/ttyACM0",
            "/dev/ttyACM1",
            "/dev/ttyUSB0",
            "/dev/ttyUSB1",
            "/dev/ttyAMA0",
        ]
        
        for port in common_ports:
            try:
                test_serial = serial.Serial(port, config.SERIAL_BAUD, timeout=1)
                test_serial.close()
                logger.info(f"Found Arduino at: {port}")
                return port
            except (serial.SerialException, OSError):
                continue
        
        # List available ports for debugging
        available = [p.device for p in serial.tools.list_ports.comports()]
        logger.warning(f"Arduino not found. Available ports: {available}")
        return None
    
    def connect(self) -> bool:
        """
        Establish serial connection with Arduino.
        Returns True if successful.
        """
        with self.lock:
            if self.serial and self.serial.is_open:
                return True
            
            port = self.find_arduino_port()
            if not port:
                logger.error("Could not find Arduino port")
                return False
            
            try:
                self.serial = serial.Serial(
                    port=port,
                    baudrate=config.SERIAL_BAUD,
                    timeout=config.SERIAL_TIMEOUT,
                    write_timeout=1.0
                )
                
                # Wait for Arduino to reset after connection
                time.sleep(2)
                
                # Clear buffers
                self.serial.reset_input_buffer()
                self.serial.reset_output_buffer()
                
                # Wait for Arduino ready signal (optional)
                start_time = time.time()
                while time.time() - start_time < 3:
                    if self.serial.in_waiting > 0:
                        line = self.serial.readline().decode('utf-8', errors='ignore').strip()
                        logger.debug(f"Arduino startup: {line}")
                        if "READY" in line.upper():
                            break
                    time.sleep(0.1)
                
                self.connected = True
                logger.info(f"Arduino connected on {port}")
                
                # Test connection
                response = self._send_raw("PING")
                if response and "OK" in response:
                    logger.info("Arduino PING successful")
                else:
                    logger.warning("Arduino PING failed, but continuing...")
                
                return True
                
            except serial.SerialException as e:
                logger.error(f"Failed to connect to Arduino: {e}")
                self.serial = None
                self.connected = False
                return False
    
    def disconnect(self):
        """Close the serial connection."""
        with self.lock:
            if self.serial and self.serial.is_open:
                try:
                    self._send_raw("STOP")  # Safety stop
                except:
                    pass
                self.serial.close()
            self.serial = None
            self.connected = False
            logger.info("Arduino disconnected")
    
    def _send_raw(self, command: str) -> Optional[str]:
        """
        Internal method to send raw command (no lock).
        Returns response string or None.
        """
        if not self.serial or not self.serial.is_open:
            return None
        
        try:
            # Send command with newline
            self.serial.write((command + "\n").encode('utf-8'))
            self.serial.flush()
            
            # Read response (with timeout)
            start_time = time.time()
            while time.time() - start_time < config.SERIAL_TIMEOUT:
                if self.serial.in_waiting > 0:
                    response = self.serial.readline().decode('utf-8', errors='ignore').strip()
                    if response:
                        return response
                time.sleep(0.01)
            
            return None
            
        except serial.SerialException as e:
            logger.error(f"Serial error: {e}")
            self.connected = False
            return None
    
    def send_command(self, command: str) -> tuple[bool, str]:
        """
        Send command to Arduino.
        Returns (success, response_or_error_message).
        """
        command = command.upper().strip()
        
        if command not in self.VALID_COMMANDS:
            return False, f"Invalid command: {command}"
        
        with self.lock:
            if not self.serial or not self.serial.is_open:
                # Try to reconnect
                logger.info("Arduino not connected, attempting reconnect...")
                if not self.connect():
                    return False, "Arduino not connected"
            
            response = self._send_raw(command)
            self.last_command_time = time.time()
            
            if response:
                logger.info(f"Command: {command} -> Response: {response}")
                if response.startswith("OK"):
                    return True, response
                elif response.startswith("ERROR"):
                    return False, response
                else:
                    return True, response  # Unknown but got response
            else:
                logger.warning(f"No response for command: {command}")
                return True, "No response (command sent)"
    
    def move(self, direction: str) -> tuple[bool, str]:
        """
        Send movement command.
        Direction: forward, backward, left, right, stop
        """
        direction_map = {
            'forward': 'FORWARD',
            'backward': 'BACKWARD',
            'back': 'BACKWARD',
            'left': 'LEFT',
            'right': 'RIGHT',
            'stop': 'STOP',
        }
        
        cmd = direction_map.get(direction.lower())
        if not cmd:
            return False, f"Invalid direction: {direction}"
        
        return self.send_command(cmd)
    
    def is_connected(self) -> bool:
        """Check if Arduino is connected."""
        return self.connected and self.serial is not None and self.serial.is_open
    
    def get_status(self) -> dict:
        """Get Arduino connection status."""
        return {
            "connected": self.is_connected(),
            "port": self.serial.port if self.serial else None,
            "last_command_time": self.last_command_time,
        }


# Standalone test
if __name__ == "__main__":
    logging.basicConfig(
        level=logging.DEBUG,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )
    
    print("Testing Arduino Handler...")
    print("-" * 40)
    
    arduino = ArduinoHandler()
    
    if arduino.connect():
        print(f"Status: {arduino.get_status()}")
        
        # Test commands
        commands = ["PING", "FORWARD", "STOP"]
        for cmd in commands:
            print(f"\nSending: {cmd}")
            success, response = arduino.send_command(cmd)
            print(f"  Success: {success}, Response: {response}")
            time.sleep(0.5)
        
        arduino.disconnect()
    else:
        print("Failed to connect to Arduino!")
        print("Available ports:", [p.device for p in serial.tools.list_ports.comports()])

