"""
Emulator controller for handling API endpoints.
"""
import json
import logging
from typing import Dict, List, Optional, Any
from fastapi import APIRouter, HTTPException, Request, Query, Body, Path, Depends
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field
from colorama import Fore

from src.services.emulator_service import EmulatorService
from src.models.emulator_models import EmulatorInfo

logger = logging.getLogger("EmulatorController")

# Create separate routers for API and UI routes
router = APIRouter(tags=["API"])
ui_router = APIRouter(tags=["UI"])

# Create a single instance of the emulator service
emulator_service = EmulatorService()

# Pydantic models for request and response objects
class EmulatorStartRequest(BaseModel):
    emulator_name: Optional[str] = Field(None, description="Name of the emulator to start")

class EmulatorListResponse(BaseModel):
    emulators: List[str] = Field(..., description="List of available emulators")

class AdbCommandRequest(BaseModel):
    adb_command: List[str] = Field(..., description="The ADB command as an array of strings")

class InputEventRequest(BaseModel):
    event_type: str = Field(..., description="Type of input event (touch, swipe, key)")
    data: Dict[str, Any] = Field(..., description="Event-specific data")

class WebRTCOfferRequest(BaseModel):
    offer: Dict[str, Any] = Field(..., description="The WebRTC offer object")

class ApiResponse(BaseModel):
    command: str = Field(..., description="The command that was executed")
    success: bool = Field(..., description="Whether the command was successful")
    error: Optional[str] = Field(None, description="Error message if the command failed")

class ExecAdbResponse(ApiResponse):
    stdout: Optional[str] = Field(None, description="Standard output from the command")
    stderr: Optional[str] = Field(None, description="Standard error from the command")
    exit_code: Optional[int] = Field(None, description="Exit code from the command")

class WebRTCAnswerResponse(ApiResponse):
    answer: Optional[Dict[str, Any]] = Field(None, description="The WebRTC answer object")

# UI Routes
@ui_router.get("/", response_class=HTMLResponse)
async def index_handler():
    """
    Landing page handler.
    """
    # Get list of available emulators
    available_emulators = await emulator_service.list_available_emulators()
    emulator_options = "\n".join([f'<option value="{emu}">{emu}</option>' for emu in available_emulators])
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Android Emulator Manager</title>
        <style>
            body {{ font-family: Arial, sans-serif; margin: 0; padding: 20px; }}
            h1 {{ color: #333; }}
            .container {{ max-width: 800px; margin: 0 auto; }}
            .status {{ margin: 20px 0; padding: 15px; border-radius: 5px; }}
            .running {{ background-color: #d4edda; color: #155724; }}
            .not-running {{ background-color: #f8d7da; color: #721c24; }}
            pre {{ background-color: #f5f5f5; padding: 10px; border-radius: 5px; }}
            button {{ padding: 8px 16px; margin: 5px; border-radius: 4px; cursor: pointer; }}
            .start-btn {{ background-color: #28a745; color: white; border: none; }}
            .stop-btn {{ background-color: #dc3545; color: white; border: none; }}
            .refresh-btn {{ background-color: #007bff; color: white; border: none; }}
            .live-view-btn {{ background-color: #6f42c1; color: white; border: none; }}
            .api-doc-btn {{ background-color: #17a2b8; color: white; border: none; }}
            select {{ padding: 8px; margin: 5px; border-radius: 4px; }}
            .emulator-select {{ margin: 20px 0; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>Android Emulator Manager</h1>
            <div id="status-container"></div>
            <div class="emulator-select">
                <label for="emulator-select">Select Emulator:</label>
                <select id="emulator-select" onchange="updateEmulator()">
                    {emulator_options}
                </select>
            </div>
            <div>
                <button class="refresh-btn" onclick="refreshStatus()">Refresh Status</button>
                <button class="start-btn" onclick="startEmulator()">Start Emulator</button>
                <button class="stop-btn" onclick="stopEmulator()">Stop Emulator</button>
                <button class="live-view-btn" onclick="openLiveView()">Live View</button>
                <button class="api-doc-btn" onclick="openApiDocs()">API Documentation</button>
            </div>
            <h2>Connection Details</h2>
            <pre id="connection-details">Loading...</pre>
            <h2>Emulator Information</h2>
            <pre id="emulator-info">Loading...</pre>
            
            <script>
                const apiBaseUrl = "/api";
                let selectedEmulator = document.getElementById('emulator-select').value;
                
                function updateEmulator() {{
                    selectedEmulator = document.getElementById('emulator-select').value;
                }}
                
                async function fetchWithJson(url, method, data) {{
                    const options = {{
                        method: method,
                        headers: {{ 'Content-Type': 'application/json' }}
                    }};
                    
                    if (data) {{
                        options.body = JSON.stringify(data);
                    }}
                    
                    const response = await fetch(url, options);
                    return await response.json();
                }}
                
                async function refreshStatus() {{
                    try {{
                        const data = await fetchWithJson(`${{apiBaseUrl}}/info`, 'GET');
                        
                        const statusContainer = document.getElementById('status-container');
                        const isRunning = data.status === 'running';
                        statusContainer.innerHTML = `
                            <div class="status ${{isRunning ? 'running' : 'not-running'}}">
                                Emulator status: ${{isRunning ? 'Running' : 'Not Running'}}
                            </div>
                        `;
                        
                        document.getElementById('emulator-info').textContent = JSON.stringify(data, null, 2);
                        document.getElementById('connection-details').textContent = isRunning 
                            ? `API endpoint: ${{apiBaseUrl}}\nWebSocket: ${{data.websocket_url}}`
                            : 'Emulator not running';
                            
                    }} catch (error) {{
                        console.error('Error refreshing status:', error);
                        document.getElementById('emulator-info').textContent = 'Error: ' + error.message;
                    }}
                }}
                
                async function startEmulator() {{
                    try {{
                        const data = await fetchWithJson(`${{apiBaseUrl}}/start`, 'POST', {{ 
                            emulator_name: selectedEmulator 
                        }});
                        
                        if (data.success) {{
                            alert('Emulator started successfully');
                        }} else {{
                            alert('Failed to start emulator');
                        }}
                        
                        refreshStatus();
                    }} catch (error) {{
                        console.error('Error starting emulator:', error);
                        alert('Error: ' + error.message);
                    }}
                }}
                
                async function stopEmulator() {{
                    try {{
                        const data = await fetchWithJson(`${{apiBaseUrl}}/stop`, 'POST');
                        
                        if (data.success) {{
                            alert('Emulator stopped successfully');
                        }} else {{
                            alert('Failed to stop emulator');
                        }}
                        
                        refreshStatus();
                    }} catch (error) {{
                        console.error('Error stopping emulator:', error);
                        alert('Error: ' + error.message);
                    }}
                }}
                
                function openLiveView() {{
                    window.open('/live-view', '_blank');
                }}
                
                function openApiDocs() {{
                    window.open('/api/doc', '_blank');
                }}
                
                // Initialize
                window.addEventListener('load', refreshStatus);
            </script>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html)

@ui_router.get("/live-view", response_class=HTMLResponse)
async def live_view_handler(port: Optional[str] = Query(None, description="ADB port")):
    """
    Live view page handler.
    """
    adb_port = port or str(emulator_service.adb_port)
    ws_port = 8039  # Using the same port as the HTTP server for WebSockets in FastAPI
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Android Emulator Live View</title>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <style>
            body {{
                font-family: Arial, sans-serif;
                margin: 0;
                padding: 20px;
                background-color: #f0f0f0;
            }}
            .container {{
                display: flex;
                flex-direction: column;
                align-items: center;
            }}
            .phone-frame {{
                position: relative;
                width: 300px;
                height: 600px;
                background: #333;
                border-radius: 40px;
                box-shadow: 0 0 10px rgba(0,0,0,0.5);
                padding: 16px;
                box-sizing: border-box;
            }}
            .phone-screen {{
                width: 100%;
                height: 100%;
                background: #000;
                border-radius: 20px;
                overflow: hidden;
            }}
            #video {{
                width: 100%;
                height: 100%;
                object-fit: contain;
            }}
            .button {{
                background-color: #555;
                color: #fff;
                padding: 5px;
                border: none;
                border-radius: 5px;
                text-align: center;
                cursor: pointer;
                user-select: none;
            }}
            .power-button {{
                position: absolute;
                top: 20px;
                right: -50px;
                width: 40px;
                height: 40px;
                line-height: 40px;
            }}
            .side-buttons {{
                position: absolute;
                left: -50px;
                top: 50%;
                transform: translateY(-50%);
                display: flex;
                flex-direction: column;
                gap: 20px;
            }}
            .side-buttons .button {{
                width: 40px;
                height: 40px;
                line-height: 40px;
            }}
            .nav-buttons-outside {{
                margin-top: 10px;
                display: flex;
                justify-content: center;
                gap: 20px;
            }}
            .nav-buttons-outside .button {{
                width: 60px;
                height: 40px;
                line-height: 40px;
                text-align: center;
                background-color: #555;
                color: #fff;
                border-radius: 5px;
                cursor: pointer;
            }}
            #status {{
                margin: 10px 0;
                padding: 10px;
                width: 300px;
                text-align: center;
                border-radius: 4px;
            }}
            .status.success {{
                background-color: #d4edda;
                color: #155724;
            }}
            .status.error {{
                background-color: #f8d7da;
                color: #721c24;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>Android Emulator Live View</h1>
            <div id="status" class="status"></div>
            <div class="phone-frame">
                <div class="phone-screen">
                    <video id="video" autoplay playsinline muted></video>
                </div>
                <div class="power-button button">⏻</div>
                <div class="side-buttons">
                    <div class="volume-up button">🔊</div>
                    <div class="volume-down button">🔉</div>
                </div>
            </div>
            <div class="nav-buttons-outside">
                <button class="back-button button">←</button>
                <button class="home-button button">⌂</button>
                <button class="overview-button button">▤</button>
            </div>
        </div>
        <script>
            // WebRTC and WebSocket setup
            let pc = null;
            let ws = null;
            const statusDiv = document.getElementById('status');
            const video = document.getElementById('video');

            function updateStatus(message, isError = false) {{
                statusDiv.textContent = message;
                statusDiv.className = `status ${{isError ? 'error' : 'success'}}`;
            }}

            async function startWebRTC() {{
                try {{
                    updateStatus('Initializing WebRTC connection...');
                    
                    const config = {{
                        iceServers: [
                            {{ urls: 'stun:stun.l.google.com:19302' }}
                        ]
                    }};
                    pc = new RTCPeerConnection(config);
                    
                    pc.addTransceiver("video", {{ direction: "recvonly" }});
                    
                    pc.ontrack = function(event) {{
                        video.srcObject = event.streams[0];
                        updateStatus('Connected to emulator');
                    }};
                    
                    pc.onicecandidate = function(event) {{
                        if (event.candidate) {{
                            if (ws && ws.readyState === WebSocket.OPEN) {{
                                ws.send(JSON.stringify({{
                                    command: "webrtc_candidate",
                                    candidate: {{
                                        candidate: event.candidate.candidate,
                                        sdpMid: event.candidate.sdpMid,
                                        sdpMLineIndex: event.candidate.sdpMLineIndex
                                    }}
                                }}));
                            }}
                        }}
                    }};
                    
                    pc.onconnectionstatechange = function() {{
                        updateStatus(`Connection state: ${{pc.connectionState}}`);
                    }};
                    
                    pc.oniceconnectionstatechange = function() {{
                        updateStatus(`ICE connection state: ${{pc.iceConnectionState}}`);
                    }};
                    
                    const offer = await pc.createOffer();
                    await pc.setLocalDescription(offer);
                    
                    ws = new WebSocket(`ws://${{window.location.host}}/ws`);
                    
                    ws.onopen = async function() {{
                        updateStatus('WebSocket connected, sending WebRTC offer...');
                        ws.send(JSON.stringify({{
                            command: "webrtc_offer",
                            offer: {{
                                sdp: pc.localDescription.sdp,
                                type: pc.localDescription.type
                            }}
                        }}));
                    }};
                    
                    ws.onmessage = async function(event) {{
                        const data = JSON.parse(event.data);
                        if (data.command === "webrtc_answer") {{
                            updateStatus('Received WebRTC answer, setting up connection...');
                            await pc.setRemoteDescription(new RTCSessionDescription(data.answer));
                            if (data.candidates) {{
                                for (const candidate of data.candidates) {{
                                    try {{
                                        await pc.addIceCandidate(new RTCIceCandidate(candidate));
                                    }} catch (e) {{
                                        console.error('Error adding ICE candidate:', e);
                                    }}
                                }}
                            }}
                        }} else if (data.command === "webrtc_candidate") {{
                            try {{
                                await pc.addIceCandidate(new RTCIceCandidate(data.candidate));
                            }} catch (e) {{
                                console.error('Error adding ICE candidate:', e);
                            }}
                        }}
                    }};
                    
                    ws.onerror = function(error) {{
                        updateStatus(`WebSocket error: ${{error}}`, true);
                    }};
                    
                    ws.onclose = function() {{
                        updateStatus('WebSocket connection closed', true);
                    }};
                    
                }} catch (error) {{
                    updateStatus(`Error: ${{error.message}}`, true);
                    console.error('WebRTC error:', error);
                }}
            }}

            // Pointer events: swipe vs tap handling
            let pointerStartX = 0, pointerStartY = 0, pointerStartTime = 0;
            let swipeDetected = false;

            video.addEventListener("pointerdown", function(e) {{
                pointerStartTime = Date.now();
                const rect = video.getBoundingClientRect();
                pointerStartX = (e.clientX - rect.left) / rect.width;
                pointerStartY = (e.clientY - rect.top) / rect.height;
                swipeDetected = false;
            }});

            video.addEventListener("pointerup", function(e) {{
                const rect = video.getBoundingClientRect();
                const endX = (e.clientX - rect.left) / rect.width;
                const endY = (e.clientY - rect.top) / rect.height;
                const dx = endX - pointerStartX;
                const dy = endY - pointerStartY;
                const distance = Math.sqrt(dx * dx + dy * dy);
                const duration = Date.now() - pointerStartTime;
                if (distance >= 0.05) {{
                    ws.send(JSON.stringify({{
                        command: "input_event",
                        event_type: "swipe",
                        data: {{
                            relativeX1: pointerStartX,
                            relativeY1: pointerStartY,
                            relativeX2: endX,
                            relativeY2: endY,
                            duration: duration
                        }}
                    }}));
                    swipeDetected = true;
                }}
            }});

            video.addEventListener("click", function(e) {{
                if (swipeDetected) {{
                    swipeDetected = false;
                    return;
                }}
                const rect = video.getBoundingClientRect();
                const relativeX = (e.clientX - rect.left) / rect.width;
                const relativeY = (e.clientY - rect.top) / rect.height;
                ws.send(JSON.stringify({{
                    command: "input_event",
                    event_type: "touch",
                    data: {{ relativeX: relativeX, relativeY: relativeY }}
                }}));
            }});

            // Button controls for power, navigation, and volume.
            function sendKey(keycode) {{
                if (ws && ws.readyState === WebSocket.OPEN) {{
                    ws.send(JSON.stringify({{
                        command: "input_event",
                        event_type: "key",
                        data: {{ keycode: keycode }}
                    }}));
                }}
            }}

            document.querySelector(".power-button").addEventListener("click", function() {{
                sendKey(26); // Power key
            }});
            document.querySelector(".volume-up").addEventListener("click", function() {{
                sendKey(24); // Volume up key
            }});
            document.querySelector(".volume-down").addEventListener("click", function() {{
                sendKey(25); // Volume down key
            }});
            document.querySelector(".back-button").addEventListener("click", function() {{
                sendKey(4); // Back key
            }});
            document.querySelector(".home-button").addEventListener("click", function() {{
                sendKey(3); // Home key
            }});
            document.querySelector(".overview-button").addEventListener("click", function() {{
                sendKey(187); // Overview key
            }});

            window.addEventListener('load', startWebRTC);
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html)

# API endpoints
@router.get("/info", summary="Get emulator information", response_model=Dict[str, Any])
async def get_info():
    """
    Get current emulator status and information.
    """
    try:
        info = await emulator_service.update_emulator_info()
        return info
    except Exception as e:
        logger.error(f"{Fore.RED}Error getting emulator info: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/start", summary="Start the emulator", response_model=Dict[str, Any])
async def start_emulator(request: EmulatorStartRequest = Body(...)):
    """
    Start the Android emulator.
    """
    try:
        if request.emulator_name:
            emulator_service.emulator_name = request.emulator_name
            
        success = await emulator_service.start_emulator()
        info = await emulator_service.update_emulator_info()
        return {
            "command": "start_emulator",
            "success": success,
            "info": info
        }
    except Exception as e:
        logger.error(f"{Fore.RED}Error starting emulator: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/stop", summary="Stop the emulator", response_model=ApiResponse)
async def stop_emulator():
    """
    Stop the running emulator.
    """
    try:
        success = await emulator_service.stop_emulator()
        return {
            "command": "stop_emulator",
            "success": success
        }
    except Exception as e:
        logger.error(f"{Fore.RED}Error stopping emulator: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/emulators", summary="List available emulators", response_model=EmulatorListResponse)
async def list_emulators():
    """
    Get a list of all available Android emulators.
    """
    try:
        emulators = await emulator_service.list_available_emulators()
        return {"emulators": emulators}
    except Exception as e:
        logger.error(f"{Fore.RED}Error listing emulators: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/exec_adb", summary="Execute ADB command", response_model=ExecAdbResponse)
async def exec_adb(request: AdbCommandRequest = Body(...)):
    """
    Execute an ADB command on the connected emulator.
    """
    try:
        adb_command = request.adb_command
        
        if not adb_command:
            return JSONResponse(
                status_code=400,
                content={
                    "command": "exec_adb",
                    "success": False,
                    "error": "No ADB command provided"
                }
            )
            
        result = await emulator_service.exec_adb(adb_command)
        return {
            "command": "exec_adb",
            **result
        }
    except Exception as e:
        logger.error(f"{Fore.RED}Error executing ADB command: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/input", summary="Send input event", response_model=ApiResponse)
async def input_event(request: InputEventRequest = Body(...)):
    """
    Send an input event to the emulator (touch, swipe, key).
    """
    try:
        event_type = request.event_type
        event_data = request.data
        
        if not event_type or not event_data:
            return JSONResponse(
                status_code=400,
                content={
                    "command": "input_event",
                    "success": False,
                    "error": "Invalid input event data"
                }
            )
            
        success = await emulator_service.send_input_event(event_type, event_data)
        return {
            "command": "input_event",
            "success": success
        }
    except Exception as e:
        logger.error(f"{Fore.RED}Error sending input event: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/webrtc", summary="Process WebRTC offer", response_model=WebRTCAnswerResponse)
async def webrtc_offer(request: WebRTCOfferRequest = Body(...)):
    """
    Process a WebRTC offer and return an answer.
    """
    try:
        offer = request.offer
        
        if not offer:
            return JSONResponse(
                status_code=400,
                content={
                    "command": "webrtc_answer",
                    "success": False,
                    "error": "No offer provided"
                }
            )
            
        answer = await emulator_service.handle_webrtc_offer(offer)
        
        if answer:
            return {
                "command": "webrtc_answer",
                "success": True,
                "answer": answer
            }
        else:
            return JSONResponse(
                status_code=500,
                content={
                    "command": "webrtc_answer",
                    "success": False,
                    "error": "Failed to create answer"
                }
            )
    except Exception as e:
        logger.error(f"{Fore.RED}Error processing WebRTC offer: {e}")
        raise HTTPException(status_code=500, detail=str(e)) 