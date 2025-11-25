"""
Data models for BACnet devices and objects
"""
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Any
from datetime import datetime
import json


@dataclass
class BACnetProperty:
    """Represents a BACnet property value"""
    property_id: str
    value: Any
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    unit: Optional[str] = None
    
    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class BACnetObject:
    """Represents a BACnet object"""
    object_type: str
    object_instance: int
    object_name: str = ""
    description: str = ""
    properties: Dict[str, BACnetProperty] = field(default_factory=dict)
    poll_interval: int = 60  # seconds
    last_poll: Optional[str] = None
    
    def to_dict(self) -> Dict:
        data = asdict(self)
        # Convert nested properties to dicts
        data['properties'] = {k: v for k, v in data['properties'].items()}
        return data
    
    def update_property(self, property_id: str, value: Any, unit: Optional[str] = None):
        """Update or add a property value"""
        self.properties[property_id] = BACnetProperty(
            property_id=property_id,
            value=value,
            unit=unit
        )
        self.last_poll = datetime.utcnow().isoformat()


@dataclass
class BACnetDevice:
    """Represents a BACnet device"""
    device_id: int
    address: str
    device_name: str = ""
    vendor_name: str = ""
    model_name: str = ""
    firmware_revision: str = ""
    application_software_version: str = ""
    protocol_version: int = 1
    protocol_revision: int = 0
    max_apdu_length: int = 1476
    segmentation_supported: str = "segmented-both"
    network_number: Optional[int] = None  # Add network number field
    objects: Dict[str, BACnetObject] = field(default_factory=dict)
    discovered_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    last_seen: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    enabled: bool = True
    
    def to_dict(self) -> Dict:
        data = asdict(self)
        # Convert nested objects to dicts
        data['objects'] = {k: v for k, v in data['objects'].items()}
        return data
    
    def from_dict(data: Dict) -> 'BACnetDevice':
        """Create a BACnetDevice from a dictionary"""
        objects = {}
        for obj_key, obj_data in data.get('objects', {}).items():
            properties = {}
            for prop_key, prop_data in obj_data.get('properties', {}).items():
                properties[prop_key] = BACnetProperty(**prop_data)
            obj_data['properties'] = properties
            objects[obj_key] = BACnetObject(**obj_data)
        
        data['objects'] = objects
        return BACnetDevice(**data)
    
    def add_object(self, obj: BACnetObject):
        """Add an object to this device"""
        key = f"{obj.object_type}:{obj.object_instance}"
        self.objects[key] = obj
        self.last_seen = datetime.utcnow().isoformat()
    
    def get_object(self, object_type: str, object_instance: int) -> Optional[BACnetObject]:
        """Get an object by type and instance"""
        key = f"{object_type}:{object_instance}"
        return self.objects.get(key)
    
    def update_last_seen(self):
        """Update the last seen timestamp"""
        self.last_seen = datetime.utcnow().isoformat()


class DeviceRegistry:
    """Registry to manage discovered BACnet devices"""
    
    def __init__(self, persistence_file: str = "devices.json"):
        self.devices: Dict[int, BACnetDevice] = {}
        self.persistence_file = persistence_file
        self.load()
    
    def add_device(self, device: BACnetDevice):
        """Add or update a device in the registry"""
        self.devices[device.device_id] = device
    
    def remove_device(self, device_id: int) -> bool:
        """Remove a device from the registry"""
        if device_id in self.devices:
            del self.devices[device_id]
            return True
        return False
    
    def get_device(self, device_id: int) -> Optional[BACnetDevice]:
        """Get a device by ID"""
        return self.devices.get(device_id)
    
    def get_all_devices(self) -> List[BACnetDevice]:
        """Get all registered devices"""
        return list(self.devices.values())
    
    def get_enabled_devices(self) -> List[BACnetDevice]:
        """Get all enabled devices"""
        return [d for d in self.devices.values() if d.enabled]
    
    def save(self):
        """Save devices to persistence file"""
        try:
            data = {
                device_id: device.to_dict() 
                for device_id, device in self.devices.items()
            }
            with open(self.persistence_file, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"Error saving device registry: {e}")
    
    def load(self):
        """Load devices from persistence file"""
        try:
            with open(self.persistence_file, 'r') as f:
                data = json.load(f)
                for device_id, device_data in data.items():
                    device = BACnetDevice.from_dict(device_data)
                    self.devices[int(device_id)] = device
        except FileNotFoundError:
            pass  # File doesn't exist yet
        except Exception as e:
            print(f"Error loading device registry: {e}")
