"""
Emulator service for managing Android emulators.
"""
import asyncio
import json
import logging
import os
import re
import subprocess
import time
from typing import Dict, List, Optional, Tuple
import cv2
import numpy as np
from PIL import Image
import io
from aiortc import RTCPeerConnection, RTCSessionDescription, VideoStreamTrack
from aiortc.contrib.media import MediaPlayer, MediaRecorder
from aiortc.mediastreams import MediaStreamError
from av import VideoFrame
from colorama import Fore, Style, init

from src.models.emulator_models import EmulatorInfo

# Initialize colorama
init(autoreset=True)

# Configure logging
logger = logging.getLogger("EmulatorService")

# Default configuration
DEFAULT_EMULATOR = "Pixel_6_API_33"
DEFAULT_ADB_PORT = 5554
DEFAULT_WS_PORT = 8040
DEFAULT_RTC_PORT = 8041

class EmulatorVideoTrack(VideoStreamTrack):
    """A video track that captures the emulator screen."""
    
    def __init__(self, emulator_service):
        super().__init__()
        self.emulator_service = emulator_service
        self._start = time.time()
        self._timestamp = 0
        self._frame = None
        self._frame_lock = asyncio.Lock()

    async def recv(self):
        # Get the next timestamp (pts, time_base) for the frame
        pts, time_base = await self.next_timestamp()
        async with self._frame_lock:
            # Capture the screen using ADB
            frame = await self.emulator_service.capture_screen()
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

class EmulatorService:
    """Service for managing the Android emulator."""

    def __init__(
        self, 
        emulator_name: str = DEFAULT_EMULATOR, 
        adb_port: int = DEFAULT_ADB_PORT,
        ws_port: int = DEFAULT_WS_PORT,
        rtc_port: int = DEFAULT_RTC_PORT
    ):
        self.emulator_name = emulator_name
        self.adb_port = adb_port
        self.ws_port = ws_port
        self.rtc_port = rtc_port
        self.emulator_process = None
        self.is_running = False
        self.emulator_info = {}
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

    async def exec_adb(self, adb_command: List[str]) -> Dict:
        """Execute an ADB command."""
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
            
            # Return result
            return {
                "success": process.returncode == 0,
                "stdout": stdout.decode(),
                "stderr": stderr.decode() if stderr else "",
                "exit_code": process.returncode
            }
        
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }

    async def cleanup(self):
        """Clean up resources."""
        # Stop the emulator if it's running
        if self.is_running:
            await self.stop_emulator() 