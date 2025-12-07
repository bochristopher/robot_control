#!/usr/bin/env python3
"""
MJPEG Camera Streaming Server for Jetson

Low-latency video streaming optimized for Jetson Orin Nano.
Streams at http://<ip>:8080/video

Uses GStreamer pipeline for hardware-accelerated capture when available.
"""

import asyncio
import logging
import signal
import sys
import time
from typing import Optional

import cv2

# Configuration
CAMERA_DEVICE = "/dev/video1"  # USB camera (HD 1080P PC-Camera)
# CAMERA_DEVICE = "/dev/video0"  # CSI camera (imx219)
CAMERA_INDEX = 0  # Sensor ID for CSI cameras
STREAM_PORT = 8080
FRAME_WIDTH = 640
FRAME_HEIGHT = 480
TARGET_FPS = 30
JPEG_QUALITY = 80  # 0-100, higher = better quality, more bandwidth

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)


class CameraCapture:
    """Handles camera capture with optional GStreamer acceleration."""
    
    def __init__(
        self,
        camera_index: int = CAMERA_INDEX,
        width: int = FRAME_WIDTH,
        height: int = FRAME_HEIGHT,
        fps: int = TARGET_FPS,
    ):
        self.camera_index = camera_index
        self.width = width
        self.height = height
        self.fps = fps
        self.cap: Optional[cv2.VideoCapture] = None
        self._frame: Optional[bytes] = None
        self._frame_time: float = 0
        self._lock = asyncio.Lock()
        self._running = False
        self._needs_resize = False
        
    def _create_jetson_csi_pipeline(self) -> str:
        """Create GStreamer pipeline for Jetson CSI camera with hardware acceleration."""
        return (
            f"nvarguscamerasrc sensor-id={self.camera_index} ! "
            f"video/x-raw(memory:NVMM),width={self.width},height={self.height},"
            f"framerate={self.fps}/1,format=NV12 ! "
            f"nvvidconv ! "
            f"video/x-raw,format=BGRx ! "
            f"videoconvert ! "
            f"video/x-raw,format=BGR ! "
            f"appsink drop=1 max-buffers=1"
        )
    
    def _create_gstreamer_pipeline(self) -> str:
        """Create GStreamer pipeline for USB camera with MJPEG."""
        return (
            f"v4l2src device=/dev/video{self.camera_index} ! "
            f"image/jpeg,width={self.width},height={self.height},framerate={self.fps}/1 ! "
            f"jpegdec ! "
            f"videoconvert ! "
            f"video/x-raw,format=BGR ! "
            f"appsink drop=1 max-buffers=1"
        )
    
    def _create_gstreamer_raw_pipeline(self) -> str:
        """Fallback GStreamer pipeline for raw capture with resize."""
        return (
            f"v4l2src device=/dev/video{self.camera_index} ! "
            f"video/x-raw ! "
            f"videoscale ! "
            f"video/x-raw,width={self.width},height={self.height} ! "
            f"videoconvert ! "
            f"video/x-raw,format=BGR ! "
            f"appsink drop=1 max-buffers=1"
        )
        
    def open(self) -> bool:
        """Open camera with best available method."""
        # Try Jetson CSI pipeline first (nvarguscamerasrc)
        logger.info("Trying Jetson CSI pipeline (nvarguscamerasrc)...")
        pipeline = self._create_jetson_csi_pipeline()
        logger.debug(f"Pipeline: {pipeline}")
        self.cap = cv2.VideoCapture(pipeline, cv2.CAP_GSTREAMER)
        
        if self.cap.isOpened():
            logger.info("✓ Jetson CSI pipeline opened successfully")
            return True
        
        # Try GStreamer MJPEG pipeline for USB cameras
        logger.info("Trying GStreamer MJPEG pipeline...")
        pipeline = self._create_gstreamer_pipeline()
        self.cap = cv2.VideoCapture(pipeline, cv2.CAP_GSTREAMER)
        
        if self.cap.isOpened():
            logger.info("✓ GStreamer MJPEG pipeline opened successfully")
            return True
            
        # Try GStreamer raw pipeline with resize
        logger.info("Trying GStreamer raw pipeline with resize...")
        pipeline = self._create_gstreamer_raw_pipeline()
        self.cap = cv2.VideoCapture(pipeline, cv2.CAP_GSTREAMER)
        
        if self.cap.isOpened():
            logger.info("✓ GStreamer raw pipeline opened successfully")
            return True
            
        # Fallback to standard V4L2
        logger.info("Falling back to V4L2...")
        self.cap = cv2.VideoCapture(CAMERA_DEVICE, cv2.CAP_V4L2)
        
        if self.cap.isOpened():
            # Try MJPEG format (lower CPU, many USB cameras support it)
            self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
            self.cap.set(cv2.CAP_PROP_FPS, self.fps)
            self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            
            actual_w = self.cap.get(cv2.CAP_PROP_FRAME_WIDTH)
            actual_h = self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
            actual_fps = self.cap.get(cv2.CAP_PROP_FPS)
            
            # Check if resize needed
            if actual_w != self.width or actual_h != self.height:
                logger.info(f"✓ V4L2 opened: {actual_w}x{actual_h} @ {actual_fps}fps (will resize)")
                self._needs_resize = True
            else:
                logger.info(f"✓ V4L2 opened: {actual_w}x{actual_h} @ {actual_fps}fps")
            return True
            
        logger.error("Failed to open camera with any method")
        return False
        
    def close(self):
        """Release camera."""
        if self.cap:
            self.cap.release()
            self.cap = None
            
    def read_frame(self) -> Optional[bytes]:
        """Read frame and encode as JPEG."""
        if not self.cap or not self.cap.isOpened():
            return None
            
        ret, frame = self.cap.read()
        if not ret or frame is None:
            return None
        
        # Resize if needed (for V4L2 fallback with high-res cameras)
        if self._needs_resize:
            frame = cv2.resize(frame, (self.width, self.height))
            
        # Encode as JPEG
        encode_params = [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY]
        _, jpeg = cv2.imencode('.jpg', frame, encode_params)
        return jpeg.tobytes()
        
    async def capture_loop(self):
        """Continuously capture frames in background."""
        self._running = True
        frame_interval = 1.0 / self.fps
        
        logger.info(f"Starting capture loop at {self.fps} FPS")
        
        while self._running:
            start = time.monotonic()
            
            frame_data = self.read_frame()
            if frame_data:
                async with self._lock:
                    self._frame = frame_data
                    self._frame_time = time.monotonic()
                    
            # Maintain target FPS
            elapsed = time.monotonic() - start
            sleep_time = frame_interval - elapsed
            if sleep_time > 0:
                await asyncio.sleep(sleep_time)
            else:
                await asyncio.sleep(0.001)  # Yield to event loop
                
    async def get_frame(self) -> Optional[bytes]:
        """Get the latest captured frame."""
        async with self._lock:
            return self._frame
            
    def stop(self):
        """Stop the capture loop."""
        self._running = False


class MJPEGStreamServer:
    """Simple async HTTP server for MJPEG streaming."""
    
    BOUNDARY = b"--frame"
    
    def __init__(self, camera: CameraCapture, port: int = STREAM_PORT):
        self.camera = camera
        self.port = port
        self.server = None
        self._clients = 0
        
    async def handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ):
        """Handle incoming HTTP connection."""
        addr = writer.get_extra_info('peername')
        
        try:
            # Read HTTP request
            request = await reader.readline()
            request_str = request.decode('utf-8', errors='ignore')
            
            # Read headers (discard)
            while True:
                line = await reader.readline()
                if line == b'\r\n' or line == b'':
                    break
                    
            # Parse request path
            parts = request_str.split()
            path = parts[1] if len(parts) > 1 else "/"
            
            if path == "/video" or path == "/stream":
                await self._stream_mjpeg(writer, addr)
            elif path == "/snapshot" or path == "/shot":
                await self._send_snapshot(writer)
            elif path == "/status":
                await self._send_status(writer)
            else:
                await self._send_index(writer)
                
        except (ConnectionResetError, BrokenPipeError):
            pass
        except Exception as e:
            logger.error(f"Error handling {addr}: {e}")
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
                
    async def _stream_mjpeg(self, writer: asyncio.StreamWriter, addr):
        """Stream MJPEG to client."""
        self._clients += 1
        logger.info(f"Client connected: {addr} (total: {self._clients})")
        
        # Send HTTP headers
        headers = (
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: multipart/x-mixed-replace; boundary=frame\r\n"
            b"Cache-Control: no-cache, no-store, must-revalidate\r\n"
            b"Pragma: no-cache\r\n"
            b"Access-Control-Allow-Origin: *\r\n"
            b"\r\n"
        )
        writer.write(headers)
        await writer.drain()
        
        frame_interval = 1.0 / TARGET_FPS
        last_frame_time = 0
        
        try:
            while True:
                frame = await self.camera.get_frame()
                
                if frame and self.camera._frame_time > last_frame_time:
                    last_frame_time = self.camera._frame_time
                    
                    # Send frame
                    writer.write(self.BOUNDARY + b"\r\n")
                    writer.write(b"Content-Type: image/jpeg\r\n")
                    writer.write(f"Content-Length: {len(frame)}\r\n".encode())
                    writer.write(b"\r\n")
                    writer.write(frame)
                    writer.write(b"\r\n")
                    await writer.drain()
                    
                await asyncio.sleep(frame_interval)
                
        except (ConnectionResetError, BrokenPipeError):
            pass
        finally:
            self._clients -= 1
            logger.info(f"Client disconnected: {addr} (remaining: {self._clients})")
            
    async def _send_snapshot(self, writer: asyncio.StreamWriter):
        """Send single JPEG snapshot."""
        frame = await self.camera.get_frame()
        
        if frame:
            headers = (
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: image/jpeg\r\n" +
                f"Content-Length: {len(frame)}\r\n".encode() +
                b"Cache-Control: no-cache\r\n"
                b"Access-Control-Allow-Origin: *\r\n"
                b"\r\n"
            )
            writer.write(headers)
            writer.write(frame)
        else:
            writer.write(b"HTTP/1.1 503 Service Unavailable\r\n\r\nNo frame available")
        await writer.drain()
        
    async def _send_status(self, writer: asyncio.StreamWriter):
        """Send JSON status."""
        import json
        status = {
            "camera": self.camera.cap is not None and self.camera.cap.isOpened(),
            "clients": self._clients,
            "resolution": f"{FRAME_WIDTH}x{FRAME_HEIGHT}",
            "fps": TARGET_FPS,
        }
        body = json.dumps(status).encode()
        headers = (
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: application/json\r\n" +
            f"Content-Length: {len(body)}\r\n".encode() +
            b"Access-Control-Allow-Origin: *\r\n"
            b"\r\n"
        )
        writer.write(headers + body)
        await writer.drain()
        
    async def _send_index(self, writer: asyncio.StreamWriter):
        """Send simple HTML index page."""
        html = f"""<!DOCTYPE html>
<html>
<head>
    <title>Robot Camera</title>
    <style>
        body {{ 
            margin: 0; padding: 20px; 
            background: #1a1a2e; color: #eee;
            font-family: system-ui, sans-serif;
        }}
        h1 {{ color: #00d9ff; }}
        img {{ 
            max-width: 100%; border: 2px solid #333; 
            border-radius: 8px;
        }}
        .links {{ margin-top: 20px; }}
        a {{ color: #00d9ff; margin-right: 20px; }}
    </style>
</head>
<body>
    <h1>🤖 Robot Camera Stream</h1>
    <img src="/video" alt="Live Stream">
    <div class="links">
        <a href="/video">MJPEG Stream</a>
        <a href="/snapshot">Snapshot</a>
        <a href="/status">Status (JSON)</a>
    </div>
    <p>Resolution: {FRAME_WIDTH}x{FRAME_HEIGHT} @ {TARGET_FPS}fps</p>
</body>
</html>"""
        body = html.encode()
        headers = (
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: text/html\r\n" +
            f"Content-Length: {len(body)}\r\n".encode() +
            b"\r\n"
        )
        writer.write(headers + body)
        await writer.drain()
        
    async def start(self):
        """Start the HTTP server."""
        self.server = await asyncio.start_server(
            self.handle_client, "0.0.0.0", self.port
        )
        logger.info(f"MJPEG server listening on http://0.0.0.0:{self.port}")
        logger.info(f"  Stream URL: http://<ip>:{self.port}/video")
        logger.info(f"  Snapshot:   http://<ip>:{self.port}/snapshot")
        
    async def stop(self):
        """Stop the server."""
        if self.server:
            self.server.close()
            await self.server.wait_closed()


async def main():
    """Main entry point."""
    logger.info("=" * 50)
    logger.info("Robot Camera MJPEG Streaming Server")
    logger.info("=" * 50)
    
    # Initialize camera
    camera = CameraCapture()
    
    if not camera.open():
        logger.error("Failed to open camera!")
        sys.exit(1)
        
    # Create server
    server = MJPEGStreamServer(camera)
    
    # Setup shutdown handler
    shutdown_event = asyncio.Event()
    
    def signal_handler():
        logger.info("Shutdown requested...")
        shutdown_event.set()
        
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, signal_handler)
        
    # Start services
    await server.start()
    capture_task = asyncio.create_task(camera.capture_loop())
    
    logger.info("Server running! Press Ctrl+C to stop.")
    logger.info(f"View stream at: http://192.168.1.215:{STREAM_PORT}/")
    
    # Wait for shutdown
    await shutdown_event.wait()
    
    # Cleanup
    camera.stop()
    capture_task.cancel()
    await server.stop()
    camera.close()
    logger.info("Server stopped.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass

