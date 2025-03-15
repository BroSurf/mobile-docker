#!/usr/bin/env python3
"""
Android Emulator Manager
A Python application to spin up an Android emulator and provide websocket connection details.
"""

import asyncio
import json
import logging
import os
import re
import signal
import subprocess
import sys
import time
from typing import Dict, List, Optional, Tuple
import cv2
import numpy as np
from PIL import Image
import io
from aiortc import RTCPeerConnection, RTCSessionDescription, VideoStreamTrack
from aiortc.contrib.media import MediaPlayer, MediaRecorder
from aiortc.mediastreams import MediaStreamError

import websockets
from aiohttp import web
from colorama import Fore, Style, init
from av import VideoFrame

# Initialize colorama
init(autoreset=True)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("Android-Emulator-Manager")

# Default configuration
DEFAULT_PORT = 8039
DEFAULT_WS_PORT = 8040
DEFAULT_EMULATOR = "Pixel_6_API_33"
DEFAULT_ADB_PORT = 5554
DEFAULT_RTC_PORT = 8041

class EmulatorVideoTrack(VideoStreamTrack):
    """A video track that captures the emulator screen."""
    
    def __init__(self, emulator_manager):
        super().__init__()
        self.emulator_manager = emulator_manager
        self._start = time.time()
        self._timestamp = 0
        self._frame = None
        self._frame_lock = asyncio.Lock()

    async def recv(self):
        # Get the next timestamp (pts, time_base) for the frame
        pts, time_base = await self.next_timestamp()
        async with self._frame_lock:
            # Capture the screen using ADB
            frame = await self.emulator_manager.capture_screen()
            if frame is None:
                # If no frame is available return a black frame
                frame = np.zeros((480, 640, 3), dtype=np.uint8)
            
            # Convert frame to RGB (if not already in that format)
            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            
            # Create an av.VideoFrame from the numpy array
            video_frame = VideoFrame.from_ndarray(frame, format="rgb24")
            video_frame.pts = pts
            video_frame.time_base = time_base
            
            return video_frame

    @property
    def kind(self):
        return "video"

class AndroidEmulatorManager:
    """Manages the Android emulator and provides connection details via websocket."""

    def __init__(
        self, 
        emulator_name: str = DEFAULT_EMULATOR, 
        adb_port: int = DEFAULT_ADB_PORT,
        web_port: int = DEFAULT_PORT,
        ws_port: int = DEFAULT_WS_PORT,
        rtc_port: int = DEFAULT_RTC_PORT
    ):
        self.emulator_name = emulator_name
        self.adb_port = adb_port
        self.web_port = web_port
        self.ws_port = ws_port
        self.rtc_port = rtc_port
        self.emulator_process = None
        self.websocket_server = None
        self.connected_clients = set()
        self.emulator_info = {}
        self.is_running = False
        self.pc = None
        self.video_track = None
        self.dummy_channel = None
        self.native_width = None
        self.native_height = None
        self.display_width = None
        self.display_height = None

    async def capture_screen(self) -> Optional[np.ndarray]:
        """Capture the current screen of the emulator using ADB."""
        if not await self.check_emulator_running():
            return None
            
        try:
            # Use ADB to capture the screen
            process = await asyncio.create_subprocess_exec(
                "adb", "-s", f"emulator-{self.adb_port}", "exec-out", "screencap", "-p",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await process.communicate()
            
            if process.returncode != 0:
                logger.error(f"{Fore.RED}Failed to capture screen: {stderr.decode()}")
                return None
                
            # Read image from bytes using PIL to obtain native size
            image_stream = io.BytesIO(stdout)
            image = Image.open(image_stream)
            native_width, native_height = image.size  # (width, height)
            
            # Convert PNG data to numpy array and then to BGR for OpenCV
            frame = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
            
            # Resize frame if needed (WebRTC has size limitations)
            max_size = 1280
            height, width = frame.shape[:2]
            if width > max_size or height > max_size:
                scale = max_size / max(width, height)
                new_width = int(width * scale)
                new_height = int(height * scale)
                frame = cv2.resize(frame, (new_width, new_height))
            else:
                new_width = width
                new_height = height
            
            # Store native and display resolutions for coordinate conversion
            self.native_width = native_width
            self.native_height = native_height
            self.display_width = new_width
            self.display_height = new_height
            
            return frame
            
        except Exception as e:
            logger.error(f"{Fore.RED}Error capturing screen: {e}")
            return None

    async def send_input_event(self, event_type: str, data: Dict) -> bool:
        """Send input event to the emulator."""
        if not await self.check_emulator_running():
            return False
            
        try:
            if event_type == "touch":
                if "relativeX" in data and "relativeY" in data:
                    relativeX = data["relativeX"]
                    relativeY = data["relativeY"]
                    x_scaled = int(relativeX * self.native_width)
                    y_scaled = int(relativeY * self.native_height)
                    logger.info(
                        f"Received relative tap: ({relativeX:.2f}, {relativeY:.2f}). "
                        f"Native resolution: ({self.native_width}, {self.native_height}). "
                        f"Scaled native coordinates: ({x_scaled}, {y_scaled})."
                    )
                else:
                    logger.debug(f"Touch event data: {data}")
                    logger.debug(
                        f"Display resolution used for scaling: ({self.display_width}, {self.display_height}); "
                        f"Native resolution: ({self.native_width}, {self.native_height})."
                    )
                    x_scaled = int(data["x"] * self.native_width / self.display_width)
                    y_scaled = int(data["y"] * self.native_height / self.display_height)
                    logger.info(
                        f"Received absolute tap at display coordinates ({data['x']}, {data['y']}). "
                        f"Calculated native coordinates: ({x_scaled}, {y_scaled})."
                    )
                cmd = [
                    "adb", "-s", f"emulator-{self.adb_port}", "shell",
                    "input", "tap", str(x_scaled), str(y_scaled)
                ]
            elif event_type == "swipe":
                # Use a default duration if not provided
                duration = data.get("duration", 300)
                # Expect relative coordinates from the client.
                if all(k in data for k in ("relativeX1", "relativeY1", "relativeX2", "relativeY2")):
                    relX1 = data["relativeX1"]
                    relY1 = data["relativeY1"]
                    relX2 = data["relativeX2"]
                    relY2 = data["relativeY2"]
                    x1 = int(relX1 * self.native_width)
                    y1 = int(relY1 * self.native_height)
                    x2 = int(relX2 * self.native_width)
                    y2 = int(relY2 * self.native_height)
                    logger.info(
                        f"Received swipe: start relative ({relX1:.2f}, {relY1:.2f}), end relative ({relX2:.2f}, {relY2:.2f}). "
                        f"Native coordinates: from ({x1}, {y1}) to ({x2}, {y2}), duration: {duration}ms"
                    )
                else:
                    logger.error("Swipe event data missing required coordinates.")
                    return False
                cmd = [
                    "adb", "-s", f"emulator-{self.adb_port}", "shell",
                    "input", "swipe", str(x1), str(y1), str(x2), str(y2), str(duration)
                ]
            elif event_type == "key":
                cmd = [
                    "adb", "-s", f"emulator-{self.adb_port}", "shell",
                    "input", "keyevent", str(data["keycode"])
                ]
            else:
                return False
            
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await process.communicate()
            if process.returncode != 0:
                logger.error(f"ADB command failed: {stderr.decode().strip()}")
            return process.returncode == 0
            
        except Exception as e:
            logger.error(f"{Fore.RED}Error sending input event: {e}")
            return False

    async def handle_webrtc_offer(self, offer: Dict) -> Optional[Dict]:
        """Handle WebRTC offer and create answer with detailed ICE candidate logging and fallback timeout."""
        try:
            from aiortc import RTCConfiguration, RTCIceServer, RTCSessionDescription

            logger.info("Starting handle_webrtc_offer()")
            logger.info(f"Received offer with type: {offer.get('type')} and sdp starting with: {offer.get('sdp', '')[:60]}")

            # Create PeerConnection with proper ICE servers.
            config = RTCConfiguration(
                iceServers=[RTCIceServer(urls="stun:stun.l.google.com:19302")]
            )
            self.pc = RTCPeerConnection(configuration=config)
            logger.info("Created RTCPeerConnection with configuration.")

            # Create and store a dummy data channel to force SCTP initialization.
            self.dummy_channel = self.pc.createDataChannel("dummy")
            logger.info("Created dummy data channel to ensure SCTP transport is initialized.")

            # Add the video track.
            self.video_track = EmulatorVideoTrack(self)
            self.pc.addTrack(self.video_track)
            logger.info("Added video track to RTCPeerConnection.")

            # Prepare to collect ICE candidates.
            ice_complete = asyncio.Event()
            candidates = []

            @self.pc.on("icecandidate")
            def on_icecandidate(candidate):
                if candidate is None:
                    logger.info("ICE candidate event received a None candidate. Final candidate received.")
                    ice_complete.set()
                else:
                    logger.info(f"ICE candidate received: {candidate.candidate}")
                    candidates.append({
                        "candidate": candidate.candidate,
                        "sdpMid": candidate.sdpMid,
                        "sdpMLineIndex": candidate.sdpMLineIndex
                    })

            @self.pc.on("icegatheringstatechange")
            def on_icegatheringstatechange():
                logger.info(f"ICE gathering state changed: {self.pc.iceGatheringState}")
                if self.pc.iceGatheringState == "complete":
                    ice_complete.set()

            # Set the remote description using the offer sent by the client.
            logger.info("Setting remote description from offer.")
            remote_desc = RTCSessionDescription(sdp=offer["sdp"], type=offer["type"])
            await self.pc.setRemoteDescription(remote_desc)
            logger.info(f"Remote description set. Type: {remote_desc.type}, SDP length: {len(remote_desc.sdp)}")

            # Create the answer and set it as the local description.
            logger.info("Creating answer.")
            answer = await self.pc.createAnswer()
            logger.info("Answer created, setting as local description.")
            await self.pc.setLocalDescription(answer)
            logger.info("Local description set.")

            # Wait for ICE gathering to complete, but with a timeout fallback.
            try:
                logger.info("Waiting for ICE candidate gathering to complete (with timeout of 5 seconds)...")
                await asyncio.wait_for(ice_complete.wait(), timeout=5)
            except asyncio.TimeoutError:
                logger.warning("ICE gathering did not complete within timeout; proceeding with available candidates.")

            # Retrieve the local description, using our answer if not set.
            local_desc = self.pc.localDescription or answer
            answer_type = local_desc.type if (local_desc and local_desc.type) else "answer"
            logger.info(f"Final local description: type = {answer_type}, SDP length = {len(local_desc.sdp)}")
            logger.info(f"Collected ICE candidates: {candidates}")

            response = {
                "sdp": local_desc.sdp,
                "type": answer_type,
                "candidates": candidates
            }
            logger.info(f"Generated answer object: {response}")
            return response

        except Exception as e:
            logger.error(f"{Fore.RED}Error handling WebRTC offer: {e}")
            return None

    async def list_available_emulators(self) -> List[str]:
        """List all available Android Virtual Devices (AVDs)."""
        try:
            process = await asyncio.create_subprocess_exec(
                "emulator", "-list-avds",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await process.communicate()
            
            if process.returncode != 0:
                logger.error(f"{Fore.RED}Failed to list emulators: {stderr.decode()}")
                return []
            
            # Split the output into lines and remove empty lines
            emulators = [line.strip() for line in stdout.decode().splitlines() if line.strip()]
            
            if not emulators:
                logger.warning(f"{Fore.YELLOW}No emulators found. Please create an emulator using Android Studio.")
                return []
            
            logger.info(f"{Fore.GREEN}Available emulators: {', '.join(emulators)}")
            return emulators
            
        except Exception as e:
            logger.error(f"{Fore.RED}Error listing emulators: {e}")
            return []

    async def start_emulator(self) -> bool:
        """Start the Android emulator."""
        logger.info(f"{Fore.GREEN}Starting Android emulator: {self.emulator_name}...")
        
        # Check if emulator is already running
        if await self.check_emulator_running():
            logger.info(f"{Fore.YELLOW}Emulator already running.")
            return True
        
        try:
            # List available emulators first
            available_emulators = await self.list_available_emulators()
            if not available_emulators:
                logger.error(f"{Fore.RED}No emulators available. Please create an emulator using Android Studio.")
                return False
            
            # If the specified emulator is not available, use the first available one
            if self.emulator_name not in available_emulators:
                logger.warning(f"{Fore.YELLOW}Specified emulator '{self.emulator_name}' not found. Using first available emulator: {available_emulators[0]}")
                self.emulator_name = available_emulators[0]
            
            # Kill any existing emulator process on the same port
            await self.stop_emulator()
            
            # Start the emulator
            cmd = [
                "emulator", 
                "-avd", self.emulator_name,
                "-port", str(self.adb_port),
                "-no-snapshot",
                "-no-boot-anim"
            ]
            
            # Start the emulator process
            self.emulator_process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0
            )
            
            # Wait for emulator to boot
            boot_timeout = 60  # seconds
            start_time = time.time()
            
            logger.info(f"{Fore.CYAN}Waiting for emulator to boot (timeout: {boot_timeout}s)...")
            
            while time.time() - start_time < boot_timeout:
                if await self.check_emulator_running():
                    # Update emulator information
                    await self.update_emulator_info()
                    self.is_running = True
                    logger.info(f"{Fore.GREEN}Emulator successfully started and booted!")
                    return True
                
                # Check if process died
                if self.emulator_process.poll() is not None:
                    stdout, stderr = self.emulator_process.communicate()
                    logger.error(f"{Fore.RED}Emulator process died unexpectedly:")
                    if stdout:
                        logger.error(f"stdout: {stdout}")
                    if stderr:
                        logger.error(f"stderr: {stderr}")
                    return False
                
                await asyncio.sleep(2)
            
            logger.error(f"{Fore.RED}Emulator failed to boot within timeout period.")
            return False
            
        except Exception as e:
            logger.error(f"{Fore.RED}Failed to start emulator: {e}")
            return False

    async def check_emulator_running(self) -> bool:
        """Check if the emulator is currently running."""
        try:
            # Run adb devices to check if our emulator is connected
            process = await asyncio.create_subprocess_exec(
                "adb", "devices",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await process.communicate()
            
            # Check if our emulator port is in the output
            emulator_id = f"emulator-{self.adb_port}"
            return emulator_id in stdout.decode() and "device" in stdout.decode()
            
        except Exception as e:
            logger.error(f"{Fore.RED}Error checking emulator status: {e}")
            return False

    async def stop_emulator(self) -> bool:
        """Stop the running emulator."""
        if not self.is_running and not self.emulator_process:
            logger.info(f"{Fore.YELLOW}No emulator is currently running.")
            return True
        
        try:
            # First try to kill the emulator using ADB
            logger.info(f"{Fore.CYAN}Stopping emulator using ADB...")
            
            process = await asyncio.create_subprocess_exec(
                "adb", "-s", f"emulator-{self.adb_port}", "emu", "kill",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            await process.communicate()
            
            # If we have a process handle, terminate it
            if self.emulator_process:
                logger.info(f"{Fore.CYAN}Terminating emulator process...")
                if os.name == 'nt':
                    # On Windows, we need to terminate the process group
                    self.emulator_process.terminate()
                    try:
                        self.emulator_process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        self.emulator_process.kill()
                else:
                    # On Unix-like systems, terminate the process
                    self.emulator_process.terminate()
                    try:
                        self.emulator_process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        self.emulator_process.kill()
                
                self.emulator_process = None
            
            # Wait a bit for the emulator to fully stop
            await asyncio.sleep(2)
            
            # Double check if emulator is still running
            if await self.check_emulator_running():
                logger.error(f"{Fore.RED}Emulator is still running after stop attempt")
                return False
            
            self.is_running = False
            logger.info(f"{Fore.GREEN}Emulator stopped successfully.")
            
            # Update client connections
            await self.notify_clients({"status": "emulator_stopped"})
            return True
            
        except Exception as e:
            logger.error(f"{Fore.RED}Failed to stop emulator: {e}")
            return False

    async def update_emulator_info(self) -> Dict:
        """Update information about the running emulator."""
        if not await self.check_emulator_running():
            self.emulator_info = {"status": "not_running"}
            return self.emulator_info
        
        # Get device information
        try:
            # Get Android version
            process = await asyncio.create_subprocess_exec(
                "adb", "-s", f"emulator-{self.adb_port}", "shell", "getprop", "ro.build.version.release",
                stdout=asyncio.subprocess.PIPE
            )
            stdout, _ = await process.communicate()
            android_version = stdout.decode().strip()
            
            # Get device model
            process = await asyncio.create_subprocess_exec(
                "adb", "-s", f"emulator-{self.adb_port}", "shell", "getprop", "ro.product.model",
                stdout=asyncio.subprocess.PIPE
            )
            stdout, _ = await process.communicate()
            device_model = stdout.decode().strip()
            
            # Get device manufacturer
            process = await asyncio.create_subprocess_exec(
                "adb", "-s", f"emulator-{self.adb_port}", "shell", "getprop", "ro.product.manufacturer",
                stdout=asyncio.subprocess.PIPE
            )
            stdout, _ = await process.communicate()
            manufacturer = stdout.decode().strip()
            
            # Update emulator info
            self.emulator_info = {
                "status": "running",
                "name": self.emulator_name,
                "adb_port": self.adb_port,
                "connection_id": f"emulator-{self.adb_port}",
                "android_version": android_version,
                "device_model": device_model,
                "manufacturer": manufacturer,
                "websocket_url": f"ws://localhost:{self.ws_port}",
                "timestamp": time.time()
            }
            
            return self.emulator_info
            
        except Exception as e:
            logger.error(f"{Fore.RED}Error updating emulator info: {e}")
            self.emulator_info = {"status": "error", "error": str(e)}
            return self.emulator_info

    async def notify_clients(self, message: Dict) -> None:
        """Send a message to all connected websocket clients."""
        if not self.connected_clients:
            return
            
        # Convert message to JSON string
        message_json = json.dumps(message)
        
        # Send to all clients
        disconnected_clients = set()
        for client in self.connected_clients:
            try:
                await client.send(message_json)
            except websockets.exceptions.ConnectionClosed:
                disconnected_clients.add(client)
        
        # Remove disconnected clients
        self.connected_clients -= disconnected_clients

    async def websocket_handler(self, websocket, path):
        """Handle websocket connections."""
        # Add client to connected clients
        self.connected_clients.add(websocket)
        logger.info(f"{Fore.CYAN}New client connected. Total clients: {len(self.connected_clients)}")
        
        # Send current emulator info
        await websocket.send(json.dumps(self.emulator_info))
        
        try:
            async for message in websocket:
                try:
                    # Parse incoming messages
                    data = json.loads(message)
                    command = data.get("command", "")
                    
                    # Handle commands
                    if command == "get_info":
                        await self.update_emulator_info()
                        await websocket.send(json.dumps(self.emulator_info))
                    
                    elif command == "start_emulator":
                        # Update emulator name if provided
                        if "emulator_name" in data:
                            self.emulator_name = data["emulator_name"]
                            logger.info(f"{Fore.CYAN}Using selected emulator: {self.emulator_name}")
                        
                        success = await self.start_emulator()
                        await websocket.send(json.dumps({
                            "command": "start_emulator",
                            "success": success,
                            "info": self.emulator_info
                        }))
                    
                    elif command == "stop_emulator":
                        success = await self.stop_emulator()
                        await websocket.send(json.dumps({
                            "command": "stop_emulator",
                            "success": success
                        }))
                    
                    elif command == "exec_adb":
                        # Run an ADB command
                        adb_command = data.get("adb_command", [])
                        if not adb_command:
                            await websocket.send(json.dumps({
                                "command": "exec_adb",
                                "success": False,
                                "error": "No ADB command provided"
                            }))
                            continue
                            
                        try:
                            # Prepend device specifier to command
                            full_command = ["adb", "-s", f"emulator-{self.adb_port}"] + adb_command
                            
                            # Execute command
                            process = await asyncio.create_subprocess_exec(
                                *full_command,
                                stdout=asyncio.subprocess.PIPE,
                                stderr=asyncio.subprocess.PIPE
                            )
                            
                            stdout, stderr = await process.communicate()
                            
                            # Send result back
                            await websocket.send(json.dumps({
                                "command": "exec_adb",
                                "success": process.returncode == 0,
                                "stdout": stdout.decode(),
                                "stderr": stderr.decode() if stderr else "",
                                "exit_code": process.returncode
                            }))
                        
                        except Exception as e:
                            await websocket.send(json.dumps({
                                "command": "exec_adb",
                                "success": False,
                                "error": str(e)
                            }))
                    
                    elif command == "webrtc_offer":
                        # Handle WebRTC offer
                        offer = data.get("offer")
                        if not offer:
                            await websocket.send(json.dumps({
                                "command": "webrtc_answer",
                                "success": False,
                                "error": "No offer provided"
                            }))
                            continue
                            
                        answer = await self.handle_webrtc_offer(offer)
                        if answer:
                            await websocket.send(json.dumps({
                                "command": "webrtc_answer",
                                "success": True,
                                "answer": answer
                            }))
                        else:
                            await websocket.send(json.dumps({
                                "command": "webrtc_answer",
                                "success": False,
                                "error": "Failed to create answer"
                            }))
                    
                    elif command == "input_event":
                        # Handle input events
                        event_type = data.get("event_type")
                        event_data = data.get("data")
                        
                        if not event_type or not event_data:
                            await websocket.send(json.dumps({
                                "command": "input_event",
                                "success": False,
                                "error": "Invalid input event data"
                            }))
                            continue
                            
                        success = await self.send_input_event(event_type, event_data)
                        await websocket.send(json.dumps({
                            "command": "input_event",
                            "success": success
                        }))
                    
                    else:
                        await websocket.send(json.dumps({
                            "error": f"Unknown command: {command}"
                        }))
                
                except json.JSONDecodeError:
                    await websocket.send(json.dumps({
                        "error": "Invalid JSON"
                    }))
                
                except Exception as e:
                    await websocket.send(json.dumps({
                        "error": str(e)
                    }))
        
        finally:
            # Remove client when disconnected
            self.connected_clients.remove(websocket)
            logger.info(f"{Fore.CYAN}Client disconnected. Total clients: {len(self.connected_clients)}")

    async def start_websocket_server(self):
        """Start the websocket server."""
        logger.info(f"{Fore.GREEN}Starting websocket server on port {self.ws_port}...")
        
        # Create and start the websocket server
        self.websocket_server = await websockets.serve(
            self.websocket_handler,
            "localhost",
            self.ws_port
        )
        
        logger.info(f"{Fore.GREEN}Websocket server started at ws://localhost:{self.ws_port}")

    async def start_web_server(self):
        """Start a simple web server to provide a status page."""
        app = web.Application()
        
        # Define routes
        async def handle_root(request):
            # Get list of available emulators
            available_emulators = await self.list_available_emulators()
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
                    </div>
                    <h2>Connection Details</h2>
                    <pre id="connection-details">Loading...</pre>
                    <h2>Emulator Information</h2>
                    <pre id="emulator-info">Loading...</pre>
                    
                    <script>
                        const wsUrl = "ws://localhost:{self.ws_port}";
                        let socket;
                        let selectedEmulator = document.getElementById('emulator-select').value;
                        
                        function updateEmulator() {{
                            selectedEmulator = document.getElementById('emulator-select').value;
                        }}
                        
                        function connectWebsocket() {{
                            socket = new WebSocket(wsUrl);
                            
                            socket.onopen = function(e) {{
                                console.log("WebSocket Connected");
                                document.getElementById('connection-details').textContent = `WebSocket connected to: ${{wsUrl}}`;
                                refreshStatus();
                            }};
                            
                            socket.onmessage = function(event) {{
                                const data = JSON.parse(event.data);
                                console.log("Data received:", data);
                                
                                if (data.status) {{
                                    const statusContainer = document.getElementById('status-container');
                                    const isRunning = data.status === 'running';
                                    statusContainer.innerHTML = `
                                        <div class="status ${{isRunning ? 'running' : 'not-running'}}">
                                            Emulator status: ${{isRunning ? 'Running' : 'Not Running'}}
                                        </div>
                                    `;
                                }}
                                
                                document.getElementById('emulator-info').textContent = JSON.stringify(data, null, 2);
                            }};
                            
                            socket.onclose = function(event) {{
                                if (event.wasClean) {{
                                    console.log(`Connection closed cleanly, code=${{event.code}} reason=${{event.reason}}`);
                                }} else {{
                                    console.log('Connection died');
                                    setTimeout(connectWebsocket, 2000);
                                }}
                            }};
                            
                            socket.onerror = function(error) {{
                                console.log(`WebSocket Error: ${{error}}`);
                            }};
                        }}
                        
                        function refreshStatus() {{
                            if (socket && socket.readyState === WebSocket.OPEN) {{
                                socket.send(JSON.stringify({{ command: "get_info" }}));
                            }}
                        }}
                        
                        function startEmulator() {{
                            if (socket && socket.readyState === WebSocket.OPEN) {{
                                socket.send(JSON.stringify({{ 
                                    command: "start_emulator",
                                    emulator_name: selectedEmulator
                                }}));
                            }}
                        }}
                        
                        function stopEmulator() {{
                            if (socket && socket.readyState === WebSocket.OPEN) {{
                                socket.send(JSON.stringify({{ command: "stop_emulator" }}));
                            }}
                        }}
                        
                        function openLiveView() {{
                            if (socket && socket.readyState === WebSocket.OPEN) {{
                                // Get the current emulator info
                                socket.send(JSON.stringify({{ command: "get_info" }}));
                                socket.onmessage = function(event) {{
                                    const data = JSON.parse(event.data);
                                    if (data.status === "running") {{
                                        window.open(`/live-view?port=${{data.adb_port}}`, '_blank');
                                    }} else {{
                                        alert("Please start the emulator first!");
                                    }}
                                }};
                            }}
                        }}
                        
                        // Initialize connection when page loads
                        window.addEventListener('load', connectWebsocket);
                    </script>
                </div>
            </body>
            </html>
            """
            return web.Response(text=html, content_type='text/html')

        async def handle_live_view(request):
            """Handle the live view page."""
            adb_port = request.query.get('port', str(DEFAULT_ADB_PORT))
            
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
                    /* Use padding instead of border so the screen isn't cropped */
                    .phone-frame {{
                        position: relative;
                        width: 300px;
                        height: 600px;
                        background: #333; /* bezel color */ 
                        border-radius: 40px;
                        box-shadow: 0 0 10px rgba(0,0,0,0.5);
                        padding: 16px; /* space for bezel */
                        box-sizing: border-box;
                    }}
                    .phone-screen {{
                        width: 100%;
                        height: 100%;
                        background: #000;
                        border-radius: 20px;
                        overflow: hidden;
                    }}
                    /* object-fit 'contain' ensures the entire screen is visible */
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
                            <video id="video" autoplay playsinline></video>
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
                            
                            ws = new WebSocket("ws://localhost:{self.ws_port}");
                            
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
            return web.Response(text=html, content_type='text/html')
        
        app.add_routes([
            web.get('/', handle_root),
            web.get('/live-view', handle_live_view)
        ])
        
        # Start web server
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, 'localhost', self.web_port)
        await site.start()
        
        logger.info(f"{Fore.GREEN}Web server started at http://localhost:{self.web_port}")
        return runner

    async def start(self):
        """Start all services."""
        # Start the websocket server
        await self.start_websocket_server()
        
        # Start the web server
        web_runner = await self.start_web_server()
        
        # Update emulator info
        await self.update_emulator_info()
        
        # Return web runner for cleanup
        return web_runner

    async def cleanup(self, web_runner):
        """Clean up resources."""
        # Stop the emulator if it's running
        if self.is_running:
            await self.stop_emulator()
        
        # Close the websocket server
        if self.websocket_server:
            self.websocket_server.close()
            await self.websocket_server.wait_closed()
        
        # Shutdown the web server
        await web_runner.cleanup()
        
        logger.info(f"{Fore.GREEN}All services stopped.")


async def main():
    """Main function to run the application."""
    # Create the emulator manager
    manager = AndroidEmulatorManager()
    
    # Set up signal handlers for graceful shutdown
    loop = asyncio.get_running_loop()
    web_runner = None
    
    async def handle_shutdown():
        logger.info(f"{Fore.YELLOW}Shutting down...")
        await manager.cleanup(web_runner)
    
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(
            sig, lambda: asyncio.create_task(handle_shutdown())
        )
    
    try:
        # Start all services
        web_runner = await manager.start()
        
        # Optionally start the emulator automatically
        # await manager.start_emulator()
        
        # Keep the application running
        while True:
            await asyncio.sleep(1)
    
    except asyncio.CancelledError:
        pass
    
    finally:
        # Final cleanup if not already done
        if web_runner:
            await manager.cleanup(web_runner)


if __name__ == "__main__":
    print(f"{Fore.CYAN}======================================================")
    print(f"{Fore.CYAN}       Android Emulator Manager - Starting Up")
    print(f"{Fore.CYAN}======================================================")
    print(f"{Fore.GREEN}Web interface: http://localhost:{DEFAULT_PORT}")
    print(f"{Fore.GREEN}WebSocket endpoint: ws://localhost:{DEFAULT_WS_PORT}")
    print(f"{Fore.CYAN}======================================================")
    
    # Run the application
    asyncio.run(main()) 