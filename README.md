# Android Emulator Manager

A Python application that spins up an Android emulator and provides a WebSocket connection to interact with it, similar to how you'd connect to a Chromium browser.

## Features

- Start and stop Android emulators
- Web interface for monitoring and controlling emulators
- WebSocket API for programmatic control
- Real-time emulator status updates
- Execute ADB commands remotely

## Requirements

- Python 3.8+
- Android SDK with emulator and platform-tools installed
- Android Virtual Device (AVD) configured

## Setup

1. Make sure you have the Android SDK installed and available in your PATH
2. Ensure you have an Android Virtual Device (AVD) created (default is "Pixel_6_API_33")
3. Install the required Python dependencies:

```bash
python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Running the Application

Run the application with:

```bash
python android_emulator_manager.py
```

This will start:
- A web server at http://localhost:8039
- A WebSocket server at ws://localhost:8040

## Configuration

The application has several default settings which can be changed by modifying the constants at the top of the script:

- `DEFAULT_PORT` (8039) - Web interface port
- `DEFAULT_WS_PORT` (8040) - WebSocket server port
- `DEFAULT_EMULATOR` ("Pixel_6_API_33") - Default AVD name
- `DEFAULT_ADB_PORT` (5554) - ADB port for the emulator

## WebSocket API

You can connect to the WebSocket server at `ws://localhost:8040` and send JSON commands:

### Get Emulator Info
```json
{"command": "get_info"}
```

### Start Emulator
```json
{"command": "start_emulator"}
```

### Stop Emulator
```json
{"command": "stop_emulator"}
```

### Execute ADB Command
```json
{
  "command": "exec_adb",
  "adb_command": ["shell", "dumpsys", "window"]
}
```

## Example JavaScript Client

```javascript
const ws = new WebSocket("ws://localhost:8040");

ws.onopen = () => {
  console.log("Connected to Android Emulator Manager");
  // Get current status
  ws.send(JSON.stringify({ command: "get_info" }));
};

ws.onmessage = (event) => {
  const data = JSON.parse(event.data);
  console.log("Received:", data);
};

// Start the emulator
function startEmulator() {
  ws.send(JSON.stringify({ command: "start_emulator" }));
}

// Run an ADB command
function runAdbCommand(adbCommand) {
  ws.send(JSON.stringify({
    command: "exec_adb",
    adb_command: adbCommand
  }));
}
```

## Comparison with Chrome DevTools Protocol

This application provides WebSocket connectivity to Android emulators similar to how the Chrome DevTools Protocol (CDP) works for Chrome/Chromium browsers. Key differences:

1. **Protocol:** This uses a simpler custom JSON protocol rather than the full CDP
2. **Commands:** Focused on Android emulator management rather than browser debugging
3. **Connection:** Uses a single WebSocket connection rather than multiple specialized ones

## Next Steps

Future enhancements could include:
- Support for multiple concurrent emulators
- More detailed device information
- Screen capture and remote control capabilities
- Full CDP-like protocol implementation
- Support for physical devices 