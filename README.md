# Robot Control WebSocket Server

Remote control system for a Jetson Orin Nano robot with Arduino-controlled mecanum wheels.

## Architecture

```
┌─────────────────┐     WebSocket      ┌─────────────────┐     Serial      ┌─────────────────┐
│  Rokid Glasses  │ ←───────────────→  │  Jetson Orin    │ ←─────────────→ │  Arduino Mega   │
│  / Android App  │    JSON/WS:8765    │  (this server)  │   /dev/ttyACM0  │  Motor Control  │
└─────────────────┘                    └─────────────────┘                 └─────────────────┘
```

## Setup

### 1. Prerequisites

```bash
# Add user to dialout group (required for serial access)
sudo usermod -a -G dialout $USER
# Log out and back in for changes to take effect
```

### 2. Install Dependencies

```bash
cd ~/robot_control
pip install -r requirements.txt
```

### 3. Verify Arduino Connection

```bash
# Check Arduino is connected
ls /dev/ttyACM*

# Test the Arduino handler directly
python arduino.py
```

### 4. Run the Server

```bash
python server.py
```

## WebSocket API

Connect to `ws://<jetson-ip>:8765`

### Authentication

First, authenticate with the server:

```json
{"cmd": "auth", "token": "robot_secret_2024"}
```

Response:
```json
{"type": "auth", "success": true, "message": "Authenticated successfully"}
```

### Movement Commands

```json
{"cmd": "move", "dir": "forward"}
{"cmd": "move", "dir": "backward"}
{"cmd": "move", "dir": "left"}
{"cmd": "move", "dir": "right"}
{"cmd": "move", "dir": "stop"}
```

Response:
```json
{"type": "move", "success": true, "direction": "forward", "response": "OK:FORWARD", "timestamp": "..."}
```

### Status Check

```json
{"cmd": "status"}
```

Response:
```json
{
  "type": "status",
  "arduino_connected": true,
  "authenticated": true,
  "clients_connected": 1,
  "clients_authenticated": 1,
  "timestamp": "..."
}
```

### Ping (Keepalive)

```json
{"cmd": "ping"}
```

Response:
```json
{"type": "pong", "timestamp": "..."}
```

### Raw Command (Debug)

Send raw command to Arduino:

```json
{"cmd": "raw", "command": "PING"}
```

## Configuration

Edit `config.py` to change:

- `SERIAL_PORT` - Arduino serial port (default: `/dev/ttyACM0`)
- `SERIAL_BAUD` - Baud rate (default: `9600`)
- `WS_PORT` - WebSocket port (default: `8765`)
- `AUTH_TOKEN` - Authentication token (change in production!)

## Testing with wscat

```bash
# Install wscat
npm install -g wscat

# Connect to server
wscat -c ws://localhost:8765

# Then send commands:
> {"cmd": "auth", "token": "robot_secret_2024"}
> {"cmd": "move", "dir": "forward"}
> {"cmd": "move", "dir": "stop"}
```

## Testing with Python

```python
import asyncio
import websockets
import json

async def test():
    async with websockets.connect("ws://localhost:8765") as ws:
        # Authenticate
        await ws.send(json.dumps({"cmd": "auth", "token": "robot_secret_2024"}))
        print(await ws.recv())
        
        # Move forward
        await ws.send(json.dumps({"cmd": "move", "dir": "forward"}))
        print(await ws.recv())
        
        # Stop
        await ws.send(json.dumps({"cmd": "move", "dir": "stop"}))
        print(await ws.recv())

asyncio.run(test())
```

## Systemd Service (Optional)

Create `/etc/systemd/system/robot-control.service`:

```ini
[Unit]
Description=Robot Control WebSocket Server
After=network.target

[Service]
Type=simple
User=bo
WorkingDirectory=/home/bo/robot_control
ExecStart=/usr/bin/python3 /home/bo/robot_control/server.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Enable and start:
```bash
sudo systemctl enable robot-control
sudo systemctl start robot-control
sudo systemctl status robot-control
```

## Arduino Commands

The Arduino firmware expects these commands (sent as text with newline):

| Command    | Response      | Action                    |
|------------|---------------|---------------------------|
| `FORWARD`  | `OK:FORWARD`  | Move forward              |
| `BACKWARD` | `OK:BACKWARD` | Move backward             |
| `LEFT`     | `OK:LEFT`     | Strafe/turn left          |
| `RIGHT`    | `OK:RIGHT`    | Strafe/turn right         |
| `STOP`     | `OK:STOP`     | Stop all motors           |
| `PING`     | `OK:PING`     | Connection test           |

**Failsafe:** Motors automatically stop if no command received for 2 seconds.

## Troubleshooting

### Permission Denied on Serial Port

```bash
# Check if user is in dialout group
groups $USER

# If not, add and re-login
sudo usermod -a -G dialout $USER
```

### Arduino Not Found

```bash
# List all serial devices
ls /dev/tty*

# Check dmesg for Arduino connection
dmesg | grep -i arduino
dmesg | grep -i ttyACM
```

### WebSocket Connection Refused

```bash
# Check if server is running
ps aux | grep server.py

# Check if port is open
netstat -tlnp | grep 8765
```

