"""
REST API for controlling the BACnet gateway
"""
import logging
from typing import Optional, List, Any, Union
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ConfigDict
from models.device import DeviceRegistry, BACnetDevice
from models.mqtt_mapping import MQTTMappingRegistry, MQTTMapping
from bacnet.discovery import BACnetDiscovery
from bacnet.reader_writer import BACnetReaderWriter
import os

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


class MQTTMappingRequest(BaseModel):
    device_id: int
    object_type: str
    object_instance: int
    mqtt_topic: str
    custom_topic: Optional[str] = None
    enabled: bool = True


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
        reader_writer: BACnetReaderWriter,
        gateway=None  # Reference to main gateway for BBMD operations
    ):
        self.device_registry = device_registry
        self.discovery = discovery
        self.reader_writer = reader_writer
        self.gateway = gateway
        self.app = FastAPI(title="BACnet-MQTT Gateway API")
        
        # Register routes
        self._register_routes()
    
    def _register_routes(self):
        """Register API routes"""
        app.mount("/static", StaticFiles(directory="static"), name="static")
        @self.app.get("/", response_class=HTMLResponse)
        async def root():
            """Serve the web interface"""
            # Try multiple possible locations for index.html
            possible_paths = [
                'index.html',
                os.path.join(os.path.dirname(__file__), '..', 'index.html'),
                os.path.join(os.getcwd(), 'index.html'),
            ]
            
            for html_path in possible_paths:
                if os.path.exists(html_path):
                    logger.info(f"Serving index.html from: {html_path}")
                    with open(html_path, 'r', encoding='utf-8') as f:
                        return f.read()
            
            # File not found
            logger.error("index.html not found in any expected location")
            return """
            <html>
                <body>
                    <h1>BACnet-MQTT Gateway</h1>
                    <p>Web interface file (index.html) not found.</p>
                    <p>Expected locations checked:</p>
                    <ul>""" + ''.join(f"<li>{p}</li>" for p in possible_paths) + """
                    </ul>
                    <p>API docs available at <a href="/docs">/docs</a></p>
                </body>
            </html>
            """
        
        @self.app.get("/status")
        async def get_status():
            """Get gateway status and configuration"""
            import socket
            hostname = socket.gethostname()
            
            # Get BACnet app info
            bacnet_info = {}
            if hasattr(self.discovery.app, 'localDevice'):
                device = self.discovery.app.localDevice
                bacnet_info = {
                    "device_id": device.objectIdentifier[1] if device.objectIdentifier else None,
                    "device_name": device.objectName if hasattr(device, 'objectName') else None,
                }
            
            # Get network address info
            if hasattr(self.discovery.app, 'nse'):
                nse = self.discovery.app.nse
                if hasattr(nse, 'localAddress'):
                    bacnet_info["local_address"] = str(nse.localAddress)
                if hasattr(nse, 'broadcastAddress'):
                    bacnet_info["broadcast_address"] = str(nse.broadcastAddress)
            
            # Get BBMD status
            bbmd_info = None
            if self.gateway:
                bbmd_config = self.gateway.config.get('bacnet', {}).get('bbmd', {})
                if bbmd_config.get('enabled'):
                    bbmd_info = {
                        "enabled": True,
                        "address": bbmd_config.get('address'),
                        "port": bbmd_config.get('port', 47808),
                        "ttl": bbmd_config.get('ttl', 30)
                    }
            
            return {
                "hostname": hostname,
                "bacnet": bacnet_info,
                "bbmd": bbmd_info,
                "devices_count": len(self.device_registry.get_all_devices()),
                "enabled_devices_count": len(self.device_registry.get_enabled_devices())
            }
        
        @self.app.post("/bbmd/register")
        async def register_bbmd():
            """Manually trigger BBMD registration"""
            if not self.gateway:
                raise HTTPException(status_code=500, detail="Gateway reference not available")
            
            bbmd_config = self.gateway.config.get('bacnet', {}).get('bbmd', {})
            if not bbmd_config.get('enabled'):
                raise HTTPException(status_code=400, detail="BBMD not enabled in configuration")
            
            try:
                await self.gateway._register_with_bbmd(bbmd_config)
                return {"message": "BBMD registration triggered"}
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))
        
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
                logger.info(
                    f"Discovery requested - Range: {request.low_limit}-{request.high_limit}, "
                    f"Timeout: {request.timeout}s"
                )
                
                # Run discovery in foreground so we can return results
                devices = await self.discovery.discover_devices(
                    request.low_limit,
                    request.high_limit,
                    request.timeout
                )
                
                return {
                    "message": f"Discovery complete: {len(devices)} devices found",
                    "devices_found": len(devices),
                    "device_ids": [d.device_id for d in devices]
                }
            except Exception as e:
                logger.error(f"Discovery error: {e}", exc_info=True)
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
