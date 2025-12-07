#!/usr/bin/env python3
"""
Test client for Robot Control WebSocket Server

Usage: python test_client.py
"""

import asyncio
import json
import sys

import websockets

# Configuration
WS_URL = "ws://localhost:8765"
AUTH_TOKEN = "robot_secret_2024"  # Match config.py


async def test_robot():
    """Run test sequence against the robot control server."""
    print(f"\n{'='*50}")
    print("🤖 Robot Control Test Client")
    print(f"{'='*50}\n")
    
    print(f"Connecting to {WS_URL}...")
    
    try:
        async with websockets.connect(WS_URL) as ws:
            # Receive welcome message
            welcome = await ws.recv()
            welcome_data = json.loads(welcome)
            print(f"✅ Connected!")
            print(f"   Server: {welcome_data.get('message')}")
            print(f"   Arduino: {'Connected' if welcome_data.get('arduino_connected') else '❌ NOT Connected'}")
            print(f"   Commands: {welcome_data.get('commands')}\n")
            
            # Test 1: Status (before auth)
            print("📊 Test 1: Status check (before auth)")
            await ws.send(json.dumps({"cmd": "status"}))
            response = json.loads(await ws.recv())
            print(f"   Authenticated: {response.get('authenticated')}")
            print(f"   Arduino: {response.get('arduino_connected')}\n")
            
            # Test 2: Authenticate
            print("🔐 Test 2: Authentication")
            await ws.send(json.dumps({"cmd": "auth", "token": AUTH_TOKEN}))
            response = json.loads(await ws.recv())
            if response.get("success"):
                print(f"   ✅ {response.get('message')}\n")
            else:
                print(f"   ❌ Auth failed: {response.get('message')}")
                return
            
            # Test 3: Status (after auth)
            print("📊 Test 3: Status check (after auth)")
            await ws.send(json.dumps({"cmd": "status"}))
            response = json.loads(await ws.recv())
            print(f"   Authenticated: {response.get('authenticated')}")
            print(f"   Clients connected: {response.get('clients_connected')}")
            print(f"   Clients authenticated: {response.get('clients_authenticated')}\n")
            
            # Test 4: Ping
            print("🏓 Test 4: Ping")
            await ws.send(json.dumps({"cmd": "ping"}))
            response = json.loads(await ws.recv())
            print(f"   Response: {response.get('type')} at {response.get('timestamp')}\n")
            
            # Test 5: Movement commands
            if not welcome_data.get('arduino_connected'):
                print("⚠️  Skipping movement tests - Arduino not connected\n")
            else:
                print("🚗 Test 5: Movement sequence")
                print("   Sending FORWARD...")
                await ws.send(json.dumps({"cmd": "move", "dir": "forward"}))
                response = json.loads(await ws.recv())
                print(f"   → Success: {response.get('success')}, Response: {response.get('response')}")
                
                print("   Waiting 2 seconds...")
                await asyncio.sleep(2)
                
                print("   Sending STOP...")
                await ws.send(json.dumps({"cmd": "move", "dir": "stop"}))
                response = json.loads(await ws.recv())
                print(f"   → Success: {response.get('success')}, Response: {response.get('response')}\n")
            
            # Test 6: Raw command (Arduino PING)
            print("📡 Test 6: Raw Arduino command (PING)")
            await ws.send(json.dumps({"cmd": "raw", "command": "PING"}))
            response = json.loads(await ws.recv())
            print(f"   Success: {response.get('success')}")
            print(f"   Response: {response.get('response')}\n")
            
            print(f"{'='*50}")
            print("✅ All tests completed!")
            print(f"{'='*50}\n")
            
    except websockets.exceptions.ConnectionRefused:
        print("❌ Connection refused - is the server running?")
        print(f"   Start it with: cd /jetson/projects/robot_control && python server.py")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(test_robot())

