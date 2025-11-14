"""
REST API for controlling the BACnet gateway
"""
import logging
from typing import Optional, List, Any, Union
from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel, Field, ConfigDict
from models.device import DeviceRegistry, BACnetDevice
from bacnet.discovery import BACnetDiscovery
from bacnet.reader_writer import BACnetReaderWriter

logger = logging.getLogger(__name__)


# Request/Response Models
class DiscoveryRequest(BaseModel):
    low_limit: Optional[int] = None
    high_limit: Optional[int] = None
    timeout: int = 5


class ReadPropertyRequest(BaseModel):
    device_id: int
    object_type: str
    object_instance: int
    property_id: str
    array_index: Optional[int] = None


class WritePropertyRequest(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    
    device_id: int
    object_type: str
    object_instance: int
    property_id: str
    value: Union[str, int, float, bool, None] = Field(..., description="Value to write")
    priority: Optional[int] = None
    array_index: Optional[int] = None


class DeviceResponse(BaseModel):
    device_id: int
    address: str
    device_name: str
    vendor_name: str
    enabled: bool
    object_count: int
    last_seen: str


class APIController:
    """REST API controller for the gateway"""
    
    def __init__(
        self,
        device_registry: DeviceRegistry,
        discovery: BACnetDiscovery,
        reader_writer: BACnetReaderWriter
    ):
        self.device_registry = device_registry
        self.discovery = discovery
        self.reader_writer = reader_writer
        self.app = FastAPI(title="BACnet-MQTT Gateway API")
        
        # Register routes
        self._register_routes()
    
    def _register_routes(self):
        """Register API routes"""
        
        @self.app.get("/")
        async def root():
            """API root"""
            return {
                "name": "BACnet-MQTT Gateway",
                "version": "1.0.0",
                "status": "running"
            }
        
        @self.app.get("/devices", response_model=List[DeviceResponse])
        async def get_devices():
            """Get all registered devices"""
            devices = self.device_registry.get_all_devices()
            return [
                DeviceResponse(
                    device_id=d.device_id,
                    address=d.address,
                    device_name=d.device_name,
                    vendor_name=d.vendor_name,
                    enabled=d.enabled,
                    object_count=len(d.objects),
                    last_seen=d.last_seen
                )
                for d in devices
            ]
        
        @self.app.get("/devices/{device_id}")
        async def get_device(device_id: int):
            """Get a specific device with all its data"""
            device = self.device_registry.get_device(device_id)
            if not device:
                raise HTTPException(status_code=404, detail="Device not found")
            return device.to_dict()
        
        @self.app.post("/devices/discover")
        async def discover_devices(
            request: DiscoveryRequest,
            background_tasks: BackgroundTasks
        ):
            """Trigger device discovery"""
            try:
                # Run discovery in background
                background_tasks.add_task(
                    self.discovery.discover_devices,
                    request.low_limit,
                    request.high_limit,
                    request.timeout
                )
                return {"message": "Discovery started"}
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))
        
        @self.app.post("/devices/{device_id}/discover-objects")
        async def discover_device_objects(
            device_id: int,
            background_tasks: BackgroundTasks
        ):
            """Discover all objects in a device"""
            device = self.device_registry.get_device(device_id)
            if not device:
                raise HTTPException(status_code=404, detail="Device not found")
            
            background_tasks.add_task(
                self.discovery.discover_device_objects,
                device
            )
            return {"message": f"Object discovery started for device {device_id}"}
        
        @self.app.put("/devices/{device_id}/enable")
        async def enable_device(device_id: int):
            """Enable a device"""
            device = self.device_registry.get_device(device_id)
            if not device:
                raise HTTPException(status_code=404, detail="Device not found")
            
            device.enabled = True
            self.device_registry.save()
            return {"message": f"Device {device_id} enabled"}
        
        @self.app.put("/devices/{device_id}/disable")
        async def disable_device(device_id: int):
            """Disable a device"""
            device = self.device_registry.get_device(device_id)
            if not device:
                raise HTTPException(status_code=404, detail="Device not found")
            
            device.enabled = False
            self.device_registry.save()
            return {"message": f"Device {device_id} disabled"}
        
        @self.app.delete("/devices/{device_id}")
        async def remove_device(device_id: int):
            """Remove a device from the registry"""
            if self.device_registry.remove_device(device_id):
                self.device_registry.save()
                return {"message": f"Device {device_id} removed"}
            else:
                raise HTTPException(status_code=404, detail="Device not found")
        
        @self.app.post("/read")
        async def read_property(request: ReadPropertyRequest):
            """Read a property from a BACnet device"""
            try:
                value = await self.reader_writer.read_property(
                    request.device_id,
                    request.object_type,
                    request.object_instance,
                    request.property_id,
                    request.array_index
                )
                
                if value is None:
                    raise HTTPException(
                        status_code=500,
                        detail="Failed to read property"
                    )
                
                return {
                    "device_id": request.device_id,
                    "object_type": request.object_type,
                    "object_instance": request.object_instance,
                    "property_id": request.property_id,
                    "value": str(value)
                }
                
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))
        
        @self.app.post("/write")
        async def write_property(request: WritePropertyRequest):
            """Write a property to a BACnet device"""
            try:
                success = await self.reader_writer.write_property(
                    request.device_id,
                    request.object_type,
                    request.object_instance,
                    request.property_id,
                    request.value,
                    request.priority,
                    request.array_index
                )
                
                if not success:
                    raise HTTPException(
                        status_code=500,
                        detail="Failed to write property"
                    )
                
                return {
                    "message": "Property written successfully",
                    "device_id": request.device_id,
                    "object_type": request.object_type,
                    "object_instance": request.object_instance,
                    "property_id": request.property_id,
                    "value": request.value
                }
                
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))
        
        @self.app.get("/devices/{device_id}/objects")
        async def get_device_objects(device_id: int):
            """Get all objects for a device"""
            device = self.device_registry.get_device(device_id)
            if not device:
                raise HTTPException(status_code=404, detail="Device not found")
            
            return {
                "device_id": device_id,
                "objects": [obj.to_dict() for obj in device.objects.values()]
            }
        
        @self.app.get(
            "/devices/{device_id}/objects/{object_type}/{object_instance}"
        )
        async def get_object(
            device_id: int,
            object_type: str,
            object_instance: int
        ):
            """Get a specific object"""
            device = self.device_registry.get_device(device_id)
            if not device:
                raise HTTPException(status_code=404, detail="Device not found")
            
            obj = device.get_object(object_type, object_instance)
            if not obj:
                raise HTTPException(status_code=404, detail="Object not found")
            
            return obj.to_dict()
