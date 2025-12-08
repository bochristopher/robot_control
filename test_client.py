#!/usr/bin/env python3
"""
Test Client for Robot Control WebSocket Server

Usage:
    python test_client.py [host] [port]
    
Examples:
    python test_client.py                    # localhost:8765
    python test_client.py 192.168.1.100      # remote:8765
    python test_client.py localhost 8765     # explicit
"""

import asyncio
import json
import sys

try:
    import websockets
except ImportError:
    print("ERROR: websockets package not installed!")
    print("Install with: pip install websockets")
    sys.exit(1)

# Default configuration
DEFAULT_HOST = "localhost"
DEFAULT_PORT = 8765
AUTH_TOKEN = "robot_secret_2024"


async def test_connection(host: str, port: int):
    """Test WebSocket connection to robot control server."""
    uri = f"ws://{host}:{port}"
    
    print("=" * 50)
    print(f"Robot Control Test Client")
    print(f"Connecting to: {uri}")
    print("=" * 50)
    
    try:
        async with websockets.connect(uri, ping_timeout=10) as ws:
            print("✓ Connected!\n")
            
            # Test 1: Authentication
            print("1. Testing Authentication...")
            await ws.send(json.dumps({"cmd": "auth", "token": AUTH_TOKEN}))
            response = json.loads(await ws.recv())
            print(f"   Response: {response}")
            
            if not response.get("success"):
                print("   ✗ Authentication failed!")
                return
            print("   ✓ Authenticated\n")
            
            # Test 2: Status check
            print("2. Testing Status...")
            await ws.send(json.dumps({"cmd": "status"}))
            response = json.loads(await ws.recv())
            print(f"   Response: {json.dumps(response, indent=2)}")
            print()
            
            # Test 3: Ping
            print("3. Testing Ping...")
            await ws.send(json.dumps({"cmd": "ping"}))
            response = json.loads(await ws.recv())
            print(f"   Response: {response}")
            print()
            
            # Test 4: Movement commands (with immediate stop)
            print("4. Testing Movement Commands...")
            
            movements = ["forward", "stop", "backward", "stop", "left", "stop", "right", "stop"]
            for direction in movements:
                await ws.send(json.dumps({"cmd": "move", "dir": direction}))
                response = json.loads(await ws.recv())
                status = "✓" if response.get("success") else "✗"
                print(f"   {status} {direction}: {response.get('response', response.get('message', 'no response'))}")
                await asyncio.sleep(0.3)  # Brief delay between commands
            
            print()
            
            # Test 5: Raw command (PING Arduino)
            print("5. Testing Raw Arduino Command...")
            await ws.send(json.dumps({"cmd": "raw", "command": "PING"}))
            response = json.loads(await ws.recv())
            print(f"   Response: {response}")
            print()
            
            print("=" * 50)
            print("All tests completed!")
            print("=" * 50)
            
    except websockets.exceptions.ConnectionRefused:
        print(f"✗ Connection refused - is the server running on {uri}?")
    except asyncio.TimeoutError:
        print(f"✗ Connection timeout - check the host and port")
    except Exception as e:
        print(f"✗ Error: {e}")


async def interactive_mode(host: str, port: int):
    """Interactive control mode."""
    uri = f"ws://{host}:{port}"
    
    print("=" * 50)
    print("Interactive Robot Control")
    print(f"Connecting to: {uri}")
    print("=" * 50)
    
    try:
        async with websockets.connect(uri) as ws:
            # Authenticate
            await ws.send(json.dumps({"cmd": "auth", "token": AUTH_TOKEN}))
            response = json.loads(await ws.recv())
            
            if not response.get("success"):
                print("Authentication failed!")
                return
            
            print("Connected and authenticated!")
            print("\nCommands:")
            print("  w/s/a/d - forward/backward/left/right")
            print("  space   - stop")
            print("  p       - ping")
            print("  t       - status")
            print("  q       - quit")
            print("-" * 50)
            
            while True:
                try:
                    cmd = input("Enter command: ").strip().lower()
                    
                    if cmd == 'q':
                        # Send stop before quitting
                        await ws.send(json.dumps({"cmd": "move", "dir": "stop"}))
                        await ws.recv()
                        break
                    elif cmd == 'w':
                        msg = {"cmd": "move", "dir": "forward"}
                    elif cmd == 's':
                        msg = {"cmd": "move", "dir": "backward"}
                    elif cmd == 'a':
                        msg = {"cmd": "move", "dir": "left"}
                    elif cmd == 'd':
                        msg = {"cmd": "move", "dir": "right"}
                    elif cmd == ' ' or cmd == 'x':
                        msg = {"cmd": "move", "dir": "stop"}
                    elif cmd == 'p':
                        msg = {"cmd": "ping"}
                    elif cmd == 't':
                        msg = {"cmd": "status"}
                    else:
                        print(f"Unknown command: {cmd}")
                        continue
                    
                    await ws.send(json.dumps(msg))
                    response = json.loads(await ws.recv())
                    print(f"  -> {response}")
                    
                except EOFError:
                    break
                    
    except websockets.exceptions.ConnectionRefused:
        print(f"Connection refused - is the server running?")
    except Exception as e:
        print(f"Error: {e}")


def main():
    """Main entry point."""
    # Parse command line arguments
    args = sys.argv[1:]
    
    if len(args) >= 1 and args[0] in ['-h', '--help']:
        print(__doc__)
        return
    
    host = args[0] if len(args) >= 1 else DEFAULT_HOST
    port = int(args[1]) if len(args) >= 2 else DEFAULT_PORT
    
    # Check for interactive mode flag
    interactive = '-i' in args or '--interactive' in args
    
    if interactive:
        asyncio.run(interactive_mode(host, port))
    else:
        asyncio.run(test_connection(host, port))


if __name__ == "__main__":
    main()

