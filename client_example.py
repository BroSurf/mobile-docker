#!/usr/bin/env python3
"""
Example client for Android Emulator Manager
This demonstrates how to connect to the websocket API and interact with the emulator.
"""

import asyncio
import json
import sys
import time

import websockets

# Connection settings
WS_URL = "ws://localhost:8040"

# Common Android apps and their package names
COMMON_APPS = {
    "1": {"name": "Settings", "package": "com.android.settings"},
    "2": {"name": "Chrome", "package": "com.android.chrome"},
    "3": {"name": "Gmail", "package": "com.google.android.gm"},
    "4": {"name": "Play Store", "package": "com.android.vending"},
    "5": {"name": "Camera", "package": "com.android.camera2"},
    "6": {"name": "Zomato", "package": "com.application.zomato"},
    "7": {"name": "Youtube", "package": "com.google.android.youtube"},
    "8": {"name": "Zepto", "package": "com.zeptoconsumerapp"},
}

async def connect_and_monitor():
    """Connect to the Android Emulator Manager and interact with it."""
    print(f"Connecting to Android Emulator Manager at {WS_URL}...")
    
    async with websockets.connect(WS_URL) as websocket:
        print("Connected! Waiting for initial status...")
        
        # Receive initial status
        response = await websocket.recv()
        data = json.loads(response)
        print_status(data)
        
        # Main interaction loop
        while True:
            print("\nCommands:")
            print("1. Get emulator info")
            print("2. Start emulator")
            print("3. Stop emulator")
            print("4. Run ADB command")
            print("5. Open an app")
            print("6. Exit")
            
            try:
                choice = input("Enter choice (1-6): ")
                
                if choice == "1":
                    await send_command(websocket, {"command": "get_info"})
                
                elif choice == "2":
                    await send_command(websocket, {"command": "start_emulator"})
                
                elif choice == "3":
                    await send_command(websocket, {"command": "stop_emulator"})
                
                elif choice == "4":
                    adb_cmd = input("Enter ADB command (e.g. 'shell pm list packages'): ")
                    cmd_parts = adb_cmd.split()
                    await send_command(websocket, {
                        "command": "exec_adb", 
                        "adb_command": cmd_parts
                    })
                
                elif choice == "5":
                    await handle_app_launch(websocket)
                
                elif choice == "6":
                    print("Exiting...")
                    break
                
                else:
                    print("Invalid choice. Please try again.")
            
            except KeyboardInterrupt:
                print("\nOperation cancelled by user.")
                continue
            except Exception as e:
                print(f"\nAn error occurred: {e}")
                continue

async def handle_app_launch(websocket):
    """Handle the app launching process."""
    print("\nAvailable apps:")
    for key, app in COMMON_APPS.items():
        print(f"{key}. {app['name']}")
    
    try:
        app_choice = input("\nEnter app number (1-6) or type 'custom' for a custom app: ")
        
        if app_choice.lower() == 'custom':
            package_name = input("Enter the package name of the app to launch: ")
        else:
            app = COMMON_APPS.get(app_choice)
            if not app:
                print("Invalid app choice.")
                return
            package_name = app['package']
        
        if not package_name:
            print("Invalid package name.")
            return
            
        await send_command(websocket, {
            "command": "exec_adb",
            "adb_command": ["shell", "am", "start", "-n", f"{package_name}/.MainActivity"]
        })
        
    except Exception as e:
        print(f"Error launching app: {e}")

async def send_command(websocket, command_data):
    """Send a command to the server and print the response."""
    try:
        # Send command
        await websocket.send(json.dumps(command_data))
        print(f"Sent: {json.dumps(command_data)}")
        
        # Wait for response
        print("Waiting for response...")
        response = await websocket.recv()
        data = json.loads(response)
        
        # Process response
        print_status(data)
        
        return data
    except websockets.exceptions.ConnectionClosedError:
        print("Connection to server was closed.")
        raise
    except Exception as e:
        print(f"Error sending command: {e}")
        raise

def print_status(data):
    """Print formatted status information."""
    print("\n=== Response ===")
    
    if isinstance(data, dict):
        # Check if this is a command response
        if "command" in data:
            print(f"Command: {data['command']}")
            print(f"Success: {data.get('success', False)}")
            
            # Print any error message
            if "error" in data:
                print(f"Error: {data['error']}")
            
            # Print command output for ADB commands
            if data.get("command") == "exec_adb":
                if "stdout" in data:
                    print("\nCommand Output:")
                    print(data["stdout"])
                if data.get("stderr"):
                    print("\nError Output:")
                    print(data["stderr"])
        
        # Check if this is status info
        elif "status" in data:
            print(f"Emulator Status: {data['status']}")
            
            if data["status"] == "running":
                print(f"Name: {data.get('name', 'N/A')}")
                print(f"ADB Connection: {data.get('connection_id', 'N/A')}")
                print(f"Android Version: {data.get('android_version', 'N/A')}")
                print(f"Device Model: {data.get('device_model', 'N/A')}")
                print(f"Manufacturer: {data.get('manufacturer', 'N/A')}")
            
    else:
        # Just print the raw data if not a dictionary
        print(json.dumps(data, indent=2))
    
    print("================\n")


if __name__ == "__main__":
    try:
        asyncio.run(connect_and_monitor())
    except KeyboardInterrupt:
        print("\nExiting due to user interrupt...")
    except websockets.exceptions.ConnectionClosedError:
        print("\nConnection to server was closed.")
    except ConnectionRefusedError:
        print("\nFailed to connect to the server. Is the Android Emulator Manager running?")
    except Exception as e:
        print(f"\nAn error occurred: {e}") 