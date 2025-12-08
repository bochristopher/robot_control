#!/usr/bin/env python3
"""
Camera Streaming Module for Robot Control
Provides video streaming capabilities for the robot.

Note: This module is optional and requires additional dependencies:
    pip install opencv-python picamera2 (for Pi Camera)
    or
    pip install opencv-python (for USB webcam only)
"""

import asyncio
import base64
import logging
import threading
import time
from typing import Optional, Generator

import config

logger = logging.getLogger(__name__)

# Try to import camera libraries
CAMERA_AVAILABLE = False
PICAMERA_AVAILABLE = False
CV2_AVAILABLE = False

try:
    import cv2
    CV2_AVAILABLE = True
except ImportError:
    logger.warning("OpenCV (cv2) not installed. Camera features disabled.")

try:
    from picamera2 import Picamera2
    PICAMERA_AVAILABLE = True
except ImportError:
    logger.debug("picamera2 not installed. Using USB webcam fallback.")


class CameraStream:
    """
    Handles camera capture and streaming.
    Supports both Pi Camera (via picamera2) and USB webcams (via OpenCV).
    """
    
    def __init__(self, width: int = None, height: int = None, fps: int = None):
        self.width = width or config.CAMERA_WIDTH
        self.height = height or config.CAMERA_HEIGHT
        self.fps = fps or config.CAMERA_FPS
        
        self.camera = None
        self.running = False
        self.frame = None
        self.lock = threading.Lock()
        self._capture_thread: Optional[threading.Thread] = None
        
    def start(self) -> bool:
        """
        Start the camera capture.
        Returns True if successful.
        """
        if not CV2_AVAILABLE:
            logger.error("OpenCV not installed. Cannot start camera.")
            return False
        
        if self.running:
            return True
        
        # Try Pi Camera first, then USB webcam
        if PICAMERA_AVAILABLE:
            try:
                return self._start_picamera()
            except Exception as e:
                logger.warning(f"Pi Camera failed: {e}. Trying USB webcam...")
        
        return self._start_usb_camera()
    
    def _start_picamera(self) -> bool:
        """Start Pi Camera using picamera2."""
        try:
            self.camera = Picamera2()
            camera_config = self.camera.create_preview_configuration(
                main={"size": (self.width, self.height), "format": "RGB888"}
            )
            self.camera.configure(camera_config)
            self.camera.start()
            
            self.running = True
            self._capture_thread = threading.Thread(target=self._picamera_loop, daemon=True)
            self._capture_thread.start()
            
            logger.info(f"Pi Camera started ({self.width}x{self.height})")
            return True
            
        except Exception as e:
            logger.error(f"Failed to start Pi Camera: {e}")
            self.camera = None
            return False
    
    def _start_usb_camera(self) -> bool:
        """Start USB webcam using OpenCV."""
        try:
            self.camera = cv2.VideoCapture(0)
            
            if not self.camera.isOpened():
                logger.error("Could not open USB webcam")
                return False
            
            # Set resolution
            self.camera.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
            self.camera.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
            self.camera.set(cv2.CAP_PROP_FPS, self.fps)
            
            self.running = True
            self._capture_thread = threading.Thread(target=self._opencv_loop, daemon=True)
            self._capture_thread.start()
            
            logger.info(f"USB Camera started ({self.width}x{self.height})")
            return True
            
        except Exception as e:
            logger.error(f"Failed to start USB camera: {e}")
            self.camera = None
            return False
    
    def _picamera_loop(self):
        """Capture loop for Pi Camera."""
        while self.running:
            try:
                frame = self.camera.capture_array()
                # Convert RGB to BGR for OpenCV compatibility
                frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
                
                with self.lock:
                    self.frame = frame
                    
            except Exception as e:
                logger.error(f"Pi Camera capture error: {e}")
                time.sleep(0.1)
    
    def _opencv_loop(self):
        """Capture loop for USB camera."""
        while self.running:
            try:
                ret, frame = self.camera.read()
                if ret:
                    with self.lock:
                        self.frame = frame
                else:
                    time.sleep(0.01)
                    
            except Exception as e:
                logger.error(f"USB Camera capture error: {e}")
                time.sleep(0.1)
    
    def stop(self):
        """Stop the camera capture."""
        self.running = False
        
        if self._capture_thread:
            self._capture_thread.join(timeout=2.0)
        
        if self.camera:
            if PICAMERA_AVAILABLE and isinstance(self.camera, Picamera2):
                self.camera.stop()
                self.camera.close()
            elif CV2_AVAILABLE:
                self.camera.release()
        
        self.camera = None
        self.frame = None
        logger.info("Camera stopped")
    
    def get_frame(self) -> Optional[bytes]:
        """
        Get current frame as JPEG bytes.
        Returns None if no frame available.
        """
        with self.lock:
            if self.frame is None:
                return None
            
            # Encode frame as JPEG
            ret, jpeg = cv2.imencode('.jpg', self.frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
            if ret:
                return jpeg.tobytes()
            return None
    
    def get_frame_base64(self) -> Optional[str]:
        """
        Get current frame as base64-encoded JPEG.
        Useful for WebSocket transmission.
        """
        frame_bytes = self.get_frame()
        if frame_bytes:
            return base64.b64encode(frame_bytes).decode('utf-8')
        return None
    
    def generate_mjpeg(self) -> Generator[bytes, None, None]:
        """
        Generator for MJPEG streaming.
        Yields JPEG frames with HTTP multipart boundaries.
        """
        while self.running:
            frame = self.get_frame()
            if frame:
                yield (
                    b'--frame\r\n'
                    b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n'
                )
            time.sleep(1.0 / self.fps)


# Global camera instance
camera_stream: Optional[CameraStream] = None


def get_camera() -> Optional[CameraStream]:
    """Get or create the global camera instance."""
    global camera_stream
    
    if not config.CAMERA_ENABLED:
        return None
    
    if camera_stream is None:
        camera_stream = CameraStream()
        if not camera_stream.start():
            camera_stream = None
    
    return camera_stream


def stop_camera():
    """Stop the global camera instance."""
    global camera_stream
    
    if camera_stream:
        camera_stream.stop()
        camera_stream = None


# Standalone test
if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    
    print("Testing Camera Stream...")
    print(f"OpenCV available: {CV2_AVAILABLE}")
    print(f"PiCamera available: {PICAMERA_AVAILABLE}")
    print("-" * 40)
    
    if not CV2_AVAILABLE:
        print("ERROR: OpenCV not installed!")
        print("Install with: pip install opencv-python")
        exit(1)
    
    camera = CameraStream()
    
    if camera.start():
        print("Camera started! Press Ctrl+C to stop.")
        
        try:
            while True:
                frame = camera.get_frame()
                if frame:
                    print(f"Frame captured: {len(frame)} bytes")
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        finally:
            camera.stop()
    else:
        print("Failed to start camera!")

