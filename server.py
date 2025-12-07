#!/usr/bin/env python3
"""
Robot Control WebSocket Server

Receives JSON commands from WebSocket clients and forwards them to Arduino.
Designed for remote control from Rokid AR glasses / Android app.
"""

import asyncio
import json
import logging
import signal
import sys
from datetime import datetime
from typing import Set

import websockets
from websockets.server import WebSocketServerProtocol

import config
from arduino import ArduinoHandler

# Setup logging
logging.basicConfig(level=getattr(logging, config.LOG_LEVEL), format=config.LOG_FORMAT)
logger = logging.getLogger(__name__)

# Global state
arduino = ArduinoHandler()
connected_clients: Set[WebSocketServerProtocol] = set()
authenticated_clients: Set[WebSocketServerProtocol] = set()


async def broadcast(message: dict, only_authenticated: bool = True) -> None:
    """Send a message to all connected (and optionally authenticated) clients."""
    targets = authenticated_clients if only_authenticated else connected_clients

    if not targets:
        return

    msg_str = json.dumps(message)
    await asyncio.gather(
        *[client.send(msg_str) for client in targets],
        return_exceptions=True,
    )


async def handle_auth(websocket: WebSocketServerProtocol, data: dict) -> dict:
    """Handle authentication request."""
    token = data.get("token", "")

    if token == config.AUTH_TOKEN:
        authenticated_clients.add(websocket)
        logger.info(f"Client authenticated: {websocket.remote_address}")
        return {
            "type": "auth",
            "success": True,
            "message": "Authenticated successfully",
        }
    else:
        logger.warning(f"Authentication failed for: {websocket.remote_address}")
        return {
            "type": "auth",
            "success": False,
            "message": "Invalid token",
        }


async def handle_move(websocket: WebSocketServerProtocol, data: dict) -> dict:
    """Handle movement command."""
    if websocket not in authenticated_clients:
        return {
            "type": "error",
            "message": "Not authenticated",
        }

    direction = data.get("dir", "").lower()

    if not direction:
        return {
            "type": "error",
            "message": "Missing 'dir' parameter",
        }

    if direction not in config.VALID_DIRECTIONS:
        return {
            "type": "error",
            "message": f"Invalid direction: {direction}. Valid: {config.VALID_DIRECTIONS}",
        }

    success, response = await arduino.move(direction)

    return {
        "type": "move",
        "success": success,
        "direction": direction,
        "response": response,
        "timestamp": datetime.now().isoformat(),
    }


async def handle_status(websocket: WebSocketServerProtocol, data: dict) -> dict:
    """Handle status request."""
    return {
        "type": "status",
        "arduino_connected": arduino.connected,
        "authenticated": websocket in authenticated_clients,
        "clients_connected": len(connected_clients),
        "clients_authenticated": len(authenticated_clients),
        "timestamp": datetime.now().isoformat(),
    }


async def handle_ping(websocket: WebSocketServerProtocol, data: dict) -> dict:
    """Handle ping request (for connection keepalive)."""
    return {
        "type": "pong",
        "timestamp": datetime.now().isoformat(),
    }


async def handle_raw(websocket: WebSocketServerProtocol, data: dict) -> dict:
    """Handle raw command to Arduino (for debugging)."""
    if websocket not in authenticated_clients:
        return {
            "type": "error",
            "message": "Not authenticated",
        }

    command = data.get("command", "").upper()

    if not command:
        return {
            "type": "error",
            "message": "Missing 'command' parameter",
        }

    success, response = await arduino.send_command(command)

    return {
        "type": "raw",
        "success": success,
        "command": command,
        "response": response,
        "timestamp": datetime.now().isoformat(),
    }


# Command handlers
HANDLERS = {
    "auth": handle_auth,
    "move": handle_move,
    "status": handle_status,
    "ping": handle_ping,
    "raw": handle_raw,
}


async def handle_message(websocket: WebSocketServerProtocol, message: str) -> dict:
    """Parse and route incoming message to appropriate handler."""
    try:
        data = json.loads(message)
    except json.JSONDecodeError as e:
        logger.warning(f"Invalid JSON from {websocket.remote_address}: {e}")
        return {
            "type": "error",
            "message": f"Invalid JSON: {e}",
        }

    cmd = data.get("cmd", "").lower()

    if not cmd:
        return {
            "type": "error",
            "message": "Missing 'cmd' field",
        }

    handler = HANDLERS.get(cmd)

    if handler:
        return await handler(websocket, data)
    else:
        return {
            "type": "error",
            "message": f"Unknown command: {cmd}. Valid commands: {list(HANDLERS.keys())}",
        }


async def client_handler(websocket: WebSocketServerProtocol) -> None:
    """Handle a single WebSocket client connection."""
    client_addr = websocket.remote_address
    logger.info(f"Client connected: {client_addr}")
    connected_clients.add(websocket)

    try:
        # Send welcome message
        await websocket.send(
            json.dumps(
                {
                    "type": "welcome",
                    "message": "Robot Control Server",
                    "version": "1.0.0",
                    "commands": list(HANDLERS.keys()),
                    "arduino_connected": arduino.connected,
                }
            )
        )

        # Handle messages
        async for message in websocket:
            logger.debug(f"Received from {client_addr}: {message}")

            response = await handle_message(websocket, message)

            await websocket.send(json.dumps(response))
            logger.debug(f"Sent to {client_addr}: {response}")

    except websockets.exceptions.ConnectionClosed as e:
        logger.info(f"Client disconnected: {client_addr} ({e.code}: {e.reason})")

    except Exception as e:
        logger.error(f"Error handling client {client_addr}: {e}")

    finally:
        connected_clients.discard(websocket)
        authenticated_clients.discard(websocket)
        logger.info(f"Client removed: {client_addr}")


async def keepalive_task() -> None:
    """Send periodic status updates to maintain Arduino connection."""
    while True:
        await asyncio.sleep(1.0)  # Check every second

        # Send PING to Arduino to prevent failsafe timeout
        if arduino.connected and authenticated_clients:
            # Only send keepalive if there are active clients
            pass  # Arduino keepalive handled by client commands

        # Broadcast status to authenticated clients periodically
        if authenticated_clients and (int(asyncio.get_event_loop().time()) % 5 == 0):
            await broadcast(
                {
                    "type": "heartbeat",
                    "arduino_connected": arduino.connected,
                    "timestamp": datetime.now().isoformat(),
                }
            )


async def main() -> None:
    """Main entry point."""
    logger.info("=" * 50)
    logger.info("Robot Control WebSocket Server Starting")
    logger.info("=" * 50)

    # Connect to Arduino
    logger.info("Connecting to Arduino...")
    if await arduino.connect():
        logger.info("Arduino ready!")
    else:
        logger.warning("Arduino not available - will retry on client commands")

    # Setup signal handlers for graceful shutdown
    loop = asyncio.get_event_loop()

    async def shutdown():
        logger.info("Shutting down...")
        # Stop motors
        if arduino.connected:
            await arduino.send_command("STOP")
        await arduino.disconnect()
        # Close all client connections
        for client in connected_clients.copy():
            await client.close(1001, "Server shutting down")

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(shutdown()))

    # Start WebSocket server
    logger.info(f"Starting WebSocket server on ws://{config.WS_HOST}:{config.WS_PORT}")

    async with websockets.serve(
        client_handler,
        config.WS_HOST,
        config.WS_PORT,
        ping_interval=20,
        ping_timeout=10,
    ):
        logger.info("Server running! Press Ctrl+C to stop.")
        logger.info(f"Connect with: ws://<jetson-ip>:{config.WS_PORT}")

        # Run keepalive task
        await keepalive_task()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Server stopped by user")
        sys.exit(0)

