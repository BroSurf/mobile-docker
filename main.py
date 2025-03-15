#!/usr/bin/env python3
"""
Android Emulator Manager
A Python application to spin up an Android emulator and provide websocket connection details.
"""

import asyncio
import logging
import signal
import json
import websockets
from typing import Dict, List, Optional, Any
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import uvicorn

from src.services.emulator_service import EmulatorService

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("Android-Emulator-Manager")

# Default configuration
DEFAULT_PORT = 8039
DEFAULT_WS_PORT = 8040
DEFAULT_RTC_PORT = 8041

# Create a single instance of the emulator service
emulator_service = EmulatorService()

# Create FastAPI app
app = FastAPI(
    title="Android Emulator Manager",
    description="API for managing Android emulators",
    version="1.0.0",
    docs_url="/api/doc",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json"
)

# WebSocket connection manager
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

manager = ConnectionManager()

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time communication with the emulator."""
    await manager.connect(websocket)
    
    # Send current emulator info
    info = await emulator_service.update_emulator_info()
    await websocket.send_text(json.dumps(info))
    
    try:
        while True:
            data = await websocket.receive_text()
            try:
                message = json.loads(data)
                command = message.get("command", "")
                
                # Handle commands
                if command == "get_info":
                    info = await emulator_service.update_emulator_info()
                    await websocket.send_text(json.dumps(info))
                
                elif command == "start_emulator":
                    # Update emulator name if provided
                    if "emulator_name" in message:
                        emulator_service.emulator_name = message["emulator_name"]
                    
                    success = await emulator_service.start_emulator()
                    info = await emulator_service.update_emulator_info()
                    await websocket.send_text(json.dumps({
                        "command": "start_emulator",
                        "success": success,
                        "info": info
                    }))
                
                elif command == "stop_emulator":
                    success = await emulator_service.stop_emulator()
                    await websocket.send_text(json.dumps({
                        "command": "stop_emulator",
                        "success": success
                    }))
                
                elif command == "exec_adb":
                    # Run an ADB command
                    adb_command = message.get("adb_command", [])
                    if not adb_command:
                        await websocket.send_text(json.dumps({
                            "command": "exec_adb",
                            "success": False,
                            "error": "No ADB command provided"
                        }))
                        continue
                        
                    result = await emulator_service.exec_adb(adb_command)
                    await websocket.send_text(json.dumps({
                        "command": "exec_adb",
                        **result
                    }))
                
                elif command == "webrtc_offer":
                    # Handle WebRTC offer
                    offer = message.get("offer")
                    if not offer:
                        await websocket.send_text(json.dumps({
                            "command": "webrtc_answer",
                            "success": False,
                            "error": "No offer provided"
                        }))
                        continue
                        
                    answer = await emulator_service.handle_webrtc_offer(offer)
                    if answer:
                        await websocket.send_text(json.dumps({
                            "command": "webrtc_answer",
                            "success": True,
                            "answer": answer
                        }))
                    else:
                        await websocket.send_text(json.dumps({
                            "command": "webrtc_answer",
                            "success": False,
                            "error": "Failed to create answer"
                        }))
                
                elif command == "input_event":
                    # Handle input events
                    event_type = message.get("event_type")
                    event_data = message.get("data")
                    
                    if not event_type or not event_data:
                        await websocket.send_text(json.dumps({
                            "command": "input_event",
                            "success": False,
                            "error": "Invalid input event data"
                        }))
                        continue
                        
                    success = await emulator_service.send_input_event(event_type, event_data)
                    await websocket.send_text(json.dumps({
                        "command": "input_event",
                        "success": success
                    }))
                
                else:
                    await websocket.send_text(json.dumps({
                        "error": f"Unknown command: {command}"
                    }))
            
            except json.JSONDecodeError:
                await websocket.send_text(json.dumps({
                    "error": "Invalid JSON"
                }))
            
            except Exception as e:
                logger.error(f"WebSocket error: {e}")
                await websocket.send_text(json.dumps({
                    "error": str(e)
                }))
    
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        logger.error(f"WebSocket exception: {e}")
        manager.disconnect(websocket)

# Import the API routes
from src.controllers import emulator_controller

# Register the API routes
app.include_router(
    emulator_controller.router,
    prefix="/api"
)

# Register the UI routes
app.include_router(
    emulator_controller.ui_router
)

@app.on_event("startup")
async def startup_event():
    """Initialize necessary resources on startup."""
    logger.info(f"Starting Android Emulator Manager...")
    # Start WebSocket server on a separate port if needed
    # In FastAPI, WebSockets are handled on the same server

@app.on_event("shutdown")
async def shutdown_event():
    """Clean up resources on shutdown."""
    logger.info("Shutting down Android Emulator Manager...")
    await emulator_service.cleanup()

def start():
    """Start the FastAPI application with uvicorn."""
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=DEFAULT_PORT,
        log_level="info"
    )

if __name__ == "__main__":
    try:
        start()
    except KeyboardInterrupt:
        print("Application stopped by user.") 