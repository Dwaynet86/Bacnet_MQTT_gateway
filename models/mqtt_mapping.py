"""
MQTT topic mapping model
"""
from dataclasses import dataclass, asdict
from typing import Dict, Optional
import json


@dataclass
class MQTTMapping:
    """Represents a BACnet to MQTT topic mapping"""
    device_id: int
    object_type: str
    object_instance: int
    mqtt_topic: str
    custom_topic: Optional[str] = None
    enabled: bool = True
    
    def to_dict(self) -> Dict:
        return asdict(self)
    
    @staticmethod
    def from_dict(data: Dict) -> 'MQTTMapping':
        return MQTTMapping(**data)
    
    def get_key(self) -> str:
        """Get unique key for this mapping"""
        return f"{self.device_id}:{self.object_type}:{self.object_instance}"


class MQTTMappingRegistry:
    """Registry to manage MQTT topic mappings"""
    
    def __init__(self, persistence_file: str = "mqtt_mappings.json"):
        self.mappings: Dict[str, MQTTMapping] = {}
        self.persistence_file = persistence_file
        self.load()
    
    def add_mapping(self, mapping: MQTTMapping):
        """Add or update a mapping"""
        key = mapping.get_key()
        self.mappings[key] = mapping
        self.save()
    
    def remove_mapping(self, device_id: int, object_type: str, object_instance: int) -> bool:
        """Remove a mapping"""
        key = f"{device_id}:{object_type}:{object_instance}"
        if key in self.mappings:
            del self.mappings[key]
            self.save()
            return True
        return False
    
    def get_mapping(self, device_id: int, object_type: str, object_instance: int) -> Optional[MQTTMapping]:
        """Get a specific mapping"""
        key = f"{device_id}:{object_type}:{object_instance}"
        return self.mappings.get(key)
    
    def get_all_mappings(self) -> list:
        """Get all mappings"""
        return list(self.mappings.values())
    
    def get_enabled_mappings(self) -> list:
        """Get all enabled mappings"""
        return [m for m in self.mappings.values() if m.enabled]
    
    def save(self):
        """Save mappings to file"""
        try:
            data = {key: mapping.to_dict() for key, mapping in self.mappings.items()}
            with open(self.persistence_file, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"Error saving MQTT mappings: {e}")
    
    def load(self):
        """Load mappings from file"""
        try:
            with open(self.persistence_file, 'r') as f:
                data = json.load(f)
                for key, mapping_data in data.items():
                    self.mappings[key] = MQTTMapping.from_dict(mapping_data)
        except FileNotFoundError:
            pass
        except Exception as e:
            print(f"Error loading MQTT mappings: {e}")
