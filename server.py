#!/usr/bin/env python3
"""
Robot Control WebSocket Server
Adapted for Raspberry Pi from Jetson Orin Nano version.

Usage:
    python server.py

WebSocket API:
    Connect to ws://<raspberry-pi-ip>:8765
    
    1. Authenticate:  {"cmd": "auth", "token": "robot_secret_2024"}
    2. Move:          {"cmd": "move", "dir": "forward|backward|left|right|stop"}
    3. Status:        {"cmd": "status"}
    4. Ping:          {"cmd": "ping"}
    5. Raw command:   {"cmd": "raw", "command": "PING"}
"""

import asyncio
import json
import logging
import signal
import sys
from datetime import datetime
from typing import Set, Optional

try:
    import websockets
    from websockets.asyncio.server import ServerConnection
except ImportError:
    print("ERROR: websockets package not installed!")
    print("Install with: pip install websockets")
    sys.exit(1)

import config
from arduino import ArduinoHandler

# Configure logging
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(config.LOG_FILE),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Global state
arduino: Optional[ArduinoHandler] = None
connected_clients: Set[ServerConnection] = set()
authenticated_clients: Set[ServerConnection] = set()
running = True


async def send_json(websocket: ServerConnection, data: dict):
    """Send JSON response to client."""
    try:
        await websocket.send(json.dumps(data))
    except websockets.exceptions.ConnectionClosed:
        pass


async def handle_auth(websocket: ServerConnection, data: dict) -> dict:
    """Handle authentication request."""
    token = data.get("token", "")
    
    if token == config.AUTH_TOKEN:
        authenticated_clients.add(websocket)
        logger.info(f"Client authenticated: {websocket.remote_address}")
        return {
            "type": "auth",
            "success": True,
            "message": "Authenticated successfully"
        }
    else:
        logger.warning(f"Authentication failed from: {websocket.remote_address}")
        return {
            "type": "auth",
            "success": False,
            "message": "Invalid token"
        }


async def handle_move(websocket: ServerConnection, data: dict) -> dict:
    """Handle movement command."""
    if websocket not in authenticated_clients:
        return {"type": "error", "message": "Not authenticated"}
    
    direction = data.get("dir", "").lower()
    valid_directions = ["forward", "backward", "left", "right", "stop"]
    
    if direction not in valid_directions:
        return {
            "type": "error",
            "message": f"Invalid direction: {direction}. Valid: {valid_directions}"
        }
    
    if not arduino or not arduino.is_connected():
        return {
            "type": "error",
            "message": "Arduino not connected"
        }
    
    success, response = arduino.move(direction)
    
    return {
        "type": "move",
        "success": success,
        "direction": direction,
        "response": response,
        "timestamp": datetime.now().isoformat()
    }


async def handle_status(websocket: ServerConnection, data: dict) -> dict:
    """Handle status request."""
    arduino_status = arduino.get_status() if arduino else {"connected": False}
    
    return {
        "type": "status",
        "arduino_connected": arduino_status.get("connected", False),
        "arduino_port": arduino_status.get("port"),
        "authenticated": websocket in authenticated_clients,
        "clients_connected": len(connected_clients),
        "clients_authenticated": len(authenticated_clients),
        "timestamp": datetime.now().isoformat()
    }


async def handle_ping(websocket: ServerConnection, data: dict) -> dict:
    """Handle ping/keepalive request."""
    return {
        "type": "pong",
        "timestamp": datetime.now().isoformat()
    }


async def handle_raw(websocket: ServerConnection, data: dict) -> dict:
    """Handle raw Arduino command (for debugging)."""
    if websocket not in authenticated_clients:
        return {"type": "error", "message": "Not authenticated"}
    
    command = data.get("command", "").upper()
    
    if not arduino or not arduino.is_connected():
        return {"type": "error", "message": "Arduino not connected"}
    
    success, response = arduino.send_command(command)
    
    return {
        "type": "raw",
        "success": success,
        "command": command,
        "response": response,
        "timestamp": datetime.now().isoformat()
    }


async def handle_message(websocket: WebSocketServerProtocol, message: str) -> dict:
    """Parse and handle incoming WebSocket message."""
    try:
        data = json.loads(message)
    except json.JSONDecodeError:
        return {"type": "error", "message": "Invalid JSON"}
    
    cmd = data.get("cmd", "").lower()
    
    handlers = {
        "auth": handle_auth,
        "move": handle_move,
        "status": handle_status,
        "ping": handle_ping,
        "raw": handle_raw,
    }
    
    handler = handlers.get(cmd)
    if handler:
        return await handler(websocket, data)
    else:
        return {"type": "error", "message": f"Unknown command: {cmd}"}


async def client_handler(websocket: ServerConnection):
    """Handle a connected WebSocket client."""
    connected_clients.add(websocket)
    client_addr = websocket.remote_address
    logger.info(f"Client connected: {client_addr} (total: {len(connected_clients)})")
    
    try:
        async for message in websocket:
            logger.debug(f"Received from {client_addr}: {message}")
            response = await handle_message(websocket, message)
            await send_json(websocket, response)
            
    except websockets.exceptions.ConnectionClosed as e:
        logger.info(f"Client disconnected: {client_addr} (code: {e.code})")
    except Exception as e:
        logger.error(f"Error handling client {client_addr}: {e}")
    finally:
        connected_clients.discard(websocket)
        authenticated_clients.discard(websocket)
        logger.info(f"Client removed: {client_addr} (remaining: {len(connected_clients)})")


async def failsafe_monitor():
    """
    Background task to stop robot if no commands received.
    Runs every 0.5 seconds and checks if we need to stop.
    """
    global running
    
    logger.info("Failsafe monitor started")
    
    while running:
        await asyncio.sleep(0.5)
        
        if arduino and arduino.is_connected():
            import time
            time_since_command = time.time() - arduino.last_command_time
            
            if time_since_command > config.COMMAND_TIMEOUT:
                logger.warning(f"Failsafe: No command for {time_since_command:.1f}s - stopping robot")
                arduino.send_command("STOP")


async def main():
    """Main server function."""
    global arduino, running
    
    print("=" * 50)
    print("Robot Control WebSocket Server")
    print("Adapted for Raspberry Pi")
    print("=" * 50)
    
    # Initialize Arduino handler
    arduino = ArduinoHandler()
    if arduino.connect():
        print(f"✓ Arduino connected on {arduino.serial.port}")
    else:
        print("✗ Arduino not connected - commands will fail")
        print("  Check serial connection and run: ls /dev/ttyACM*")
    
    # Set up signal handlers for graceful shutdown
    def signal_handler(sig, frame):
        global running
        print("\nShutting down...")
        running = False
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Start failsafe monitor
    failsafe_task = asyncio.create_task(failsafe_monitor())
    
    # Start WebSocket server
    print(f"✓ WebSocket server starting on ws://0.0.0.0:{config.WS_PORT}")
    print(f"  Connect from your device to ws://<raspberry-pi-ip>:{config.WS_PORT}")
    print("-" * 50)
    
    try:
        async with websockets.serve(
            client_handler,
            config.WS_HOST,
            config.WS_PORT,
            ping_interval=20,
            ping_timeout=60,
        ):
            # Run until shutdown
            while running:
                await asyncio.sleep(0.1)
                
    except Exception as e:
        logger.error(f"Server error: {e}")
    finally:
        running = False
        failsafe_task.cancel()
        
        # Stop robot and disconnect Arduino
        if arduino:
            print("Stopping robot and disconnecting Arduino...")
            arduino.send_command("STOP")
            arduino.disconnect()
        
        print("Server stopped.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nInterrupted.")

