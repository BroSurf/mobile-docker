"""
Data models for the Android Emulator Manager.
"""
from dataclasses import dataclass
from typing import Dict, List, Optional
import time

@dataclass
class EmulatorInfo:
    """Information about the running emulator."""
    status: str
    name: str = ""
    adb_port: int = 0
    connection_id: str = ""
    android_version: str = ""
    device_model: str = ""
    manufacturer: str = ""
    websocket_url: str = ""
    timestamp: float = 0.0
    error: Optional[str] = None
    
    @classmethod
    def create_not_running(cls):
        """Create an EmulatorInfo object for a non-running emulator."""
        return cls(status="not_running")
    
    @classmethod
    def create_error(cls, error_message: str):
        """Create an EmulatorInfo object for an error state."""
        return cls(status="error", error=error_message)
    
    def to_dict(self) -> Dict:
        """Convert the EmulatorInfo to a dictionary."""
        result = {
            "status": self.status,
        }
        
        if self.status == "running":
            result.update({
                "name": self.name,
                "adb_port": self.adb_port,
                "connection_id": self.connection_id,
                "android_version": self.android_version,
                "device_model": self.device_model,
                "manufacturer": self.manufacturer,
                "websocket_url": self.websocket_url,
                "timestamp": self.timestamp
            })
        elif self.status == "error" and self.error:
            result["error"] = self.error
            
        return result 