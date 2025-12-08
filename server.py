#!/usr/bin/env python3
"""
Robot Control WebSocket Server with Camera Streaming
Adapted for Raspberry Pi from Jetson Orin Nano version.

Usage:
    python server.py

WebSocket API (port 8765):
    1. Authenticate:    {"cmd": "auth", "token": "robot_secret_2024"}
    2. Move:            {"cmd": "move", "dir": "forward|backward|left|right|stop"}
    3. Status:          {"cmd": "status"}
    4. Ping:            {"cmd": "ping"}
    5. Raw command:     {"cmd": "raw", "command": "PING"}
    6. Start camera:    {"cmd": "camera", "action": "start"}
    7. Stop camera:     {"cmd": "camera", "action": "stop"}

HTTP Endpoints (port 8080):
    - http://<ip>:8080/             - Status page
    - http://<ip>:8080/stream       - MJPEG video stream
    - http://<ip>:8080/snapshot     - Single JPEG frame
"""

import asyncio
import base64
import json
import logging
import signal
import sys
import threading
import time
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Set, Optional, Dict

try:
    import websockets
    from websockets.asyncio.server import ServerConnection
except ImportError:
    print("ERROR: websockets package not installed!")
    print("Install with: pip install websockets")
    sys.exit(1)

try:
    import cv2
    CAMERA_AVAILABLE = True
except ImportError:
    print("WARNING: OpenCV not installed - camera features disabled")
    print("Install with: pip install opencv-python-headless")
    CAMERA_AVAILABLE = False

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
camera_subscribers: Set[ServerConnection] = set()
running = True

# Camera state
camera: Optional[cv2.VideoCapture] = None
camera_lock = threading.Lock()
current_frame: Optional[bytes] = None
camera_running = False


# ============== Camera Functions ==============

def init_camera() -> bool:
    """Initialize the camera."""
    global camera, camera_running
    
    if not CAMERA_AVAILABLE:
        return False
    
    with camera_lock:
        if camera is not None:
            return True
        
        try:
            # Try device path first, then index with V4L2 backend
            for source in ['/dev/video0', 0]:
                camera = cv2.VideoCapture(source, cv2.CAP_V4L2)
                if camera.isOpened():
                    ret, test_frame = camera.read()
                    if ret:
                        logger.info(f"Camera opened with source: {source}")
                        break
                    camera.release()
                    camera = None
            
            if camera is None or not camera.isOpened():
                logger.error("Could not open camera")
                return False
            
            # Set resolution
            camera.set(cv2.CAP_PROP_FRAME_WIDTH, config.CAMERA_WIDTH)
            camera.set(cv2.CAP_PROP_FRAME_HEIGHT, config.CAMERA_HEIGHT)
            camera.set(cv2.CAP_PROP_FPS, config.CAMERA_FPS)
            
            camera_running = True
            logger.info(f"Camera initialized ({config.CAMERA_WIDTH}x{config.CAMERA_HEIGHT})")
            return True
            
        except Exception as e:
            logger.error(f"Failed to initialize camera: {e}")
            camera = None
            return False


def stop_camera():
    """Stop and release the camera."""
    global camera, camera_running, current_frame
    
    with camera_lock:
        camera_running = False
        if camera:
            camera.release()
            camera = None
        current_frame = None
    logger.info("Camera stopped")


def capture_frame() -> Optional[bytes]:
    """Capture a single frame and return as JPEG bytes."""
    global current_frame
    
    if not camera_running or camera is None:
        return None
    
    with camera_lock:
        if camera is None:
            return None
        
        ret, frame = camera.read()
        if not ret:
            return None
        
        # Encode as JPEG
        ret, jpeg = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
        if ret:
            current_frame = jpeg.tobytes()
            return current_frame
        return None


async def camera_broadcast_loop():
    """Background task to broadcast camera frames to subscribers."""
    global running
    
    logger.info("Camera broadcast loop started")
    frame_interval = 1.0 / config.CAMERA_FPS
    
    while running:
        if camera_subscribers and camera_running:
            frame = capture_frame()
            if frame:
                # Encode as base64 for WebSocket
                frame_b64 = base64.b64encode(frame).decode('utf-8')
                message = json.dumps({
                    "type": "camera_frame",
                    "frame": frame_b64,
                    "timestamp": datetime.now().isoformat()
                })
                
                # Send to all camera subscribers
                dead_clients = set()
                for ws in camera_subscribers.copy():
                    try:
                        await ws.send(message)
                    except:
                        dead_clients.add(ws)
                
                # Remove dead clients
                for ws in dead_clients:
                    camera_subscribers.discard(ws)
        
        await asyncio.sleep(frame_interval)


# ============== HTTP Server for MJPEG ==============

class MJPEGHandler(BaseHTTPRequestHandler):
    """HTTP handler for MJPEG streaming and status."""
    
    def log_message(self, format, *args):
        """Suppress default logging."""
        pass
    
    def do_GET(self):
        if self.path == '/':
            self.send_status_page()
        elif self.path == '/stream':
            self.send_mjpeg_stream()
        elif self.path == '/snapshot':
            self.send_snapshot()
        else:
            self.send_error(404)
    
    def send_status_page(self):
        """Send a simple status HTML page."""
        arduino_status = "Connected" if (arduino and arduino.is_connected()) else "Disconnected"
        camera_status = "Running" if camera_running else "Stopped"
        
        html = f"""<!DOCTYPE html>
<html>
<head>
    <title>Robot Control</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; 
               background: #1a1a2e; color: #eee; padding: 20px; }}
        .container {{ max-width: 800px; margin: 0 auto; }}
        h1 {{ color: #00d4ff; }}
        .status {{ background: #16213e; padding: 20px; border-radius: 10px; margin: 20px 0; }}
        .status-item {{ display: flex; justify-content: space-between; padding: 10px 0; 
                       border-bottom: 1px solid #0f3460; }}
        .connected {{ color: #00ff88; }}
        .disconnected {{ color: #ff4757; }}
        .video-container {{ background: #000; border-radius: 10px; overflow: hidden; margin: 20px 0; }}
        img {{ width: 100%; display: block; }}
        .controls {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; max-width: 300px; margin: 20px auto; }}
        .controls button {{ padding: 20px; font-size: 18px; border: none; border-radius: 8px;
                           background: #0f3460; color: #fff; cursor: pointer; }}
        .controls button:hover {{ background: #00d4ff; }}
        .controls button:active {{ transform: scale(0.95); }}
    </style>
</head>
<body>
    <div class="container">
        <h1>🤖 Robot Control</h1>
        
        <div class="status">
            <div class="status-item">
                <span>Arduino:</span>
                <span class="{'connected' if arduino and arduino.is_connected() else 'disconnected'}">{arduino_status}</span>
            </div>
            <div class="status-item">
                <span>Camera:</span>
                <span class="{'connected' if camera_running else 'disconnected'}">{camera_status}</span>
            </div>
            <div class="status-item">
                <span>WebSocket Clients:</span>
                <span>{len(connected_clients)}</span>
            </div>
        </div>
        
        <div class="video-container">
            <img src="/stream" alt="Camera Stream">
        </div>
        
        <div class="controls">
            <div></div>
            <button onclick="move('forward')">▲</button>
            <div></div>
            <button onclick="move('left')">◄</button>
            <button onclick="move('stop')">■</button>
            <button onclick="move('right')">►</button>
            <div></div>
            <button onclick="move('backward')">▼</button>
            <div></div>
        </div>
    </div>
    
    <script>
        const ws = new WebSocket('ws://' + location.hostname + ':8765');
        ws.onopen = () => ws.send(JSON.stringify({{cmd: 'auth', token: 'robot_secret_2024'}}));
        function move(dir) {{ ws.send(JSON.stringify({{cmd: 'move', dir: dir}})); }}
    </script>
</body>
</html>"""
        
        self.send_response(200)
        self.send_header('Content-Type', 'text/html')
        self.end_headers()
        self.wfile.write(html.encode())
    
    def send_mjpeg_stream(self):
        """Send MJPEG stream."""
        self.send_response(200)
        self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=frame')
        self.send_header('Cache-Control', 'no-cache')
        self.end_headers()
        
        try:
            while camera_running:
                frame = capture_frame()
                if frame:
                    self.wfile.write(b'--frame\r\n')
                    self.wfile.write(b'Content-Type: image/jpeg\r\n\r\n')
                    self.wfile.write(frame)
                    self.wfile.write(b'\r\n')
                time.sleep(1.0 / config.CAMERA_FPS)
        except (BrokenPipeError, ConnectionResetError):
            pass
    
    def send_snapshot(self):
        """Send a single JPEG frame."""
        frame = capture_frame()
        if frame:
            self.send_response(200)
            self.send_header('Content-Type', 'image/jpeg')
            self.send_header('Content-Length', str(len(frame)))
            self.end_headers()
            self.wfile.write(frame)
        else:
            self.send_error(503, "Camera not available")


class ReusableHTTPServer(HTTPServer):
    """HTTP server that allows address reuse."""
    allow_reuse_address = True


def run_http_server():
    """Run the HTTP server in a separate thread."""
    server = ReusableHTTPServer(('0.0.0.0', 8080), MJPEGHandler)
    logger.info("HTTP server started on http://0.0.0.0:8080")
    server.serve_forever()


# ============== WebSocket Handlers ==============

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


async def handle_camera(websocket: ServerConnection, data: dict) -> dict:
    """Handle camera control commands."""
    if websocket not in authenticated_clients:
        return {"type": "error", "message": "Not authenticated"}
    
    action = data.get("action", "").lower()
    
    if action == "start":
        if not CAMERA_AVAILABLE:
            return {"type": "camera", "success": False, "message": "Camera not available (OpenCV not installed)"}
        
        if init_camera():
            camera_subscribers.add(websocket)
            return {
                "type": "camera",
                "success": True,
                "action": "started",
                "message": "Camera streaming started"
            }
        else:
            return {"type": "camera", "success": False, "message": "Failed to start camera"}
    
    elif action == "stop":
        camera_subscribers.discard(websocket)
        if not camera_subscribers:
            stop_camera()
        return {
            "type": "camera",
            "success": True,
            "action": "stopped",
            "message": "Camera streaming stopped"
        }
    
    else:
        return {"type": "error", "message": f"Invalid camera action: {action}"}


async def handle_status(websocket: ServerConnection, data: dict) -> dict:
    """Handle status request."""
    arduino_status = arduino.get_status() if arduino else {"connected": False}
    
    return {
        "type": "status",
        "arduino_connected": arduino_status.get("connected", False),
        "arduino_port": arduino_status.get("port"),
        "camera_available": CAMERA_AVAILABLE,
        "camera_running": camera_running,
        "camera_streaming": websocket in camera_subscribers,
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


async def handle_message(websocket: ServerConnection, message: str) -> dict:
    """Parse and handle incoming WebSocket message."""
    try:
        data = json.loads(message)
    except json.JSONDecodeError:
        return {"type": "error", "message": "Invalid JSON"}
    
    cmd = data.get("cmd", "").lower()
    
    handlers = {
        "auth": handle_auth,
        "move": handle_move,
        "camera": handle_camera,
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
        camera_subscribers.discard(websocket)
        logger.info(f"Client removed: {client_addr} (remaining: {len(connected_clients)})")


async def failsafe_monitor():
    """Background task to stop robot if no commands received."""
    global running
    
    logger.info("Failsafe monitor started")
    
    while running:
        await asyncio.sleep(0.5)
        
        if arduino and arduino.is_connected():
            time_since_command = time.time() - arduino.last_command_time
            
            if time_since_command > config.COMMAND_TIMEOUT:
                logger.warning(f"Failsafe: No command for {time_since_command:.1f}s - stopping robot")
                arduino.send_command("STOP")


async def main():
    """Main server function."""
    global arduino, running
    
    print("=" * 50)
    print("Robot Control Server with Camera Streaming")
    print("Adapted for Raspberry Pi")
    print("=" * 50)
    
    # Initialize Arduino handler
    arduino = ArduinoHandler()
    if arduino.connect():
        print(f"✓ Arduino connected on {arduino.serial.port}")
    else:
        print("✗ Arduino not connected - motor commands will fail")
    
    # Initialize camera
    if CAMERA_AVAILABLE:
        if init_camera():
            print(f"✓ Camera initialized ({config.CAMERA_WIDTH}x{config.CAMERA_HEIGHT})")
        else:
            print("✗ Camera failed to initialize")
    else:
        print("✗ Camera not available (install opencv-python-headless)")
    
    # Start HTTP server in background thread
    http_thread = threading.Thread(target=run_http_server, daemon=True)
    http_thread.start()
    print("✓ HTTP server on http://0.0.0.0:8080")
    print("  - Video stream: http://<ip>:8080/stream")
    print("  - Control page: http://<ip>:8080/")
    
    # Set up signal handlers
    def signal_handler(sig, frame):
        global running
        print("\nShutting down...")
        running = False
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Start background tasks
    failsafe_task = asyncio.create_task(failsafe_monitor())
    camera_task = asyncio.create_task(camera_broadcast_loop())
    
    # Start WebSocket server
    print(f"✓ WebSocket server on ws://0.0.0.0:{config.WS_PORT}")
    print("-" * 50)
    print("Ready! Connect your app to this Raspberry Pi.")
    print("-" * 50)
    
    try:
        async with websockets.serve(
            client_handler,
            config.WS_HOST,
            config.WS_PORT,
            ping_interval=20,
            ping_timeout=60,
        ):
            while running:
                await asyncio.sleep(0.1)
                
    except Exception as e:
        logger.error(f"Server error: {e}")
    finally:
        running = False
        failsafe_task.cancel()
        camera_task.cancel()
        
        # Cleanup
        stop_camera()
        if arduino:
            print("Stopping robot...")
            arduino.send_command("STOP")
            arduino.disconnect()
        
        print("Server stopped.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nInterrupted.")
