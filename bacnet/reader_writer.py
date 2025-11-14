"""
BACnet read and write operations
"""
import asyncio
import logging
from typing import Any, Optional, List
from bacpypes3.app import Application
from bacpypes3.pdu import Address
from bacpypes3.basetypes import PropertyIdentifier
from bacpypes3.primitivedata import ObjectIdentifier
from models.device import BACnetDevice, BACnetObject, DeviceRegistry

logger = logging.getLogger(__name__)


class BACnetReaderWriter:
    """Handles BACnet read and write operations"""
    
    def __init__(self, app: Application, device_registry: DeviceRegistry):
        self.app = app
        self.device_registry = device_registry
    
    async def read_property(
        self,
        device_id: int,
        object_type: str,
        object_instance: int,
        property_id: str,
        array_index: Optional[int] = None
    ) -> Optional[Any]:
        """
        Read a property from a BACnet object
        
        Args:
            device_id: BACnet device ID
            object_type: BACnet object type (e.g., 'analog-input')
            object_instance: Object instance number
            property_id: Property identifier (e.g., 'present-value')
            array_index: Array index if property is an array
            
        Returns:
            Property value or None if read failed
        """
        device = self.device_registry.get_device(device_id)
        if not device:
            logger.error(f"Device {device_id} not found in registry")
            return None
        
        try:
            obj_id = ObjectIdentifier(f"{object_type},{object_instance}")
            address = Address(device.address)
            prop_id = PropertyIdentifier(property_id)
            
            logger.debug(
                f"Reading {property_id} from {object_type}:{object_instance} "
                f"on device {device_id}"
            )
            
            value = await self.app.read_property(
                address,
                obj_id,
                prop_id,
                array_index=array_index
            )
            
            # Update device's last seen timestamp
            device.update_last_seen()
            
            return value
            
        except Exception as e:
            logger.error(
                f"Error reading property from device {device_id}: {e}"
            )
            return None
    
    async def write_property(
        self,
        device_id: int,
        object_type: str,
        object_instance: int,
        property_id: str,
        value: Any,
        priority: Optional[int] = None,
        array_index: Optional[int] = None
    ) -> bool:
        """
        Write a property to a BACnet object
        
        Args:
            device_id: BACnet device ID
            object_type: BACnet object type
            object_instance: Object instance number
            property_id: Property identifier
            value: Value to write
            priority: Write priority (1-16, used for present-value)
            array_index: Array index if property is an array
            
        Returns:
            True if write succeeded, False otherwise
        """
        device = self.device_registry.get_device(device_id)
        if not device:
            logger.error(f"Device {device_id} not found in registry")
            return False
        
        try:
            obj_id = ObjectIdentifier(f"{object_type},{object_instance}")
            address = Address(device.address)
            prop_id = PropertyIdentifier(property_id)
            
            logger.info(
                f"Writing {value} to {property_id} on {object_type}:{object_instance} "
                f"of device {device_id} (priority: {priority})"
            )
            
            await self.app.write_property(
                address,
                obj_id,
                prop_id,
                value,
                priority=priority,
                array_index=array_index
            )
            
            # Update device's last seen timestamp
            device.update_last_seen()
            
            return True
            
        except Exception as e:
            logger.error(
                f"Error writing property to device {device_id}: {e}"
            )
            return False
    
    async def read_multiple_properties(
        self,
        device_id: int,
        object_type: str,
        object_instance: int,
        property_ids: List[str]
    ) -> dict:
        """
        Read multiple properties from an object
        
        Returns:
            Dictionary mapping property IDs to values
        """
        results = {}
        
        for prop_id in property_ids:
            value = await self.read_property(
                device_id,
                object_type,
                object_instance,
                prop_id
            )
            results[prop_id] = value
        
        return results
    
    async def poll_object(
        self,
        device: BACnetDevice,
        obj: BACnetObject,
        properties: List[str]
    ) -> dict:
        """
        Poll an object for specified properties and update the device model
        
        Args:
            device: BACnet device
            obj: BACnet object to poll
            properties: List of property identifiers to read
            
        Returns:
            Dictionary of property values
        """
        results = {}
        
        for prop_id in properties:
            try:
                value = await self.read_property(
                    device.device_id,
                    obj.object_type,
                    obj.object_instance,
                    prop_id
                )
                
                if value is not None:
                    # Try to get engineering units if reading present-value
                    unit = None
                    if prop_id == 'present-value':
                        try:
                            unit_value = await self.read_property(
                                device.device_id,
                                obj.object_type,
                                obj.object_instance,
                                'units'
                            )
                            if unit_value:
                                unit = str(unit_value)
                        except:
                            pass
                    
                    # Update object property
                    obj.update_property(prop_id, value, unit)
                    results[prop_id] = value
                    
            except Exception as e:
                logger.debug(
                    f"Error reading {prop_id} from "
                    f"{obj.object_type}:{obj.object_instance}: {e}"
                )
        
        return results
    
    async def poll_device_objects(
        self,
        device: BACnetDevice,
        properties: List[str]
    ):
        """
        Poll all objects in a device for specified properties
        
        Args:
            device: BACnet device
            properties: List of property identifiers to read
        """
        logger.debug(f"Polling device {device.device_id}")
        
        for obj in device.objects.values():
            try:
                await self.poll_object(device, obj, properties)
            except Exception as e:
                logger.error(
                    f"Error polling {obj.object_type}:{obj.object_instance}: {e}"
                )
        
        device.update_last_seen()


class BACnetPoller:
    """Periodic polling service for BACnet devices"""
    
    def __init__(
        self,
        reader_writer: BACnetReaderWriter,
        device_registry: DeviceRegistry,
        default_interval: int = 60,
        properties: List[str] = None
    ):
        self.reader_writer = reader_writer
        self.device_registry = device_registry
        self.default_interval = default_interval
        self.properties = properties or ['present-value', 'status-flags']
        self.running = False
        self.task = None
    
    async def start(self):
        """Start the polling service"""
        if self.running:
            logger.warning("Poller already running")
            return
        
        self.running = True
        self.task = asyncio.create_task(self._poll_loop())
        logger.info("BACnet poller started")
    
    async def stop(self):
        """Stop the polling service"""
        self.running = False
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        logger.info("BACnet poller stopped")
    
    async def _poll_loop(self):
        """Main polling loop"""
        while self.running:
            try:
                devices = self.device_registry.get_enabled_devices()
                
                for device in devices:
                    if not device.enabled:
                        continue
                    
                    try:
                        await self.reader_writer.poll_device_objects(
                            device,
                            self.properties
                        )
                    except Exception as e:
                        logger.error(f"Error polling device {device.device_id}: {e}")
                
                # Save registry after polling
                self.device_registry.save()
                
                await asyncio.sleep(self.default_interval)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in poll loop: {e}")
                await asyncio.sleep(5)
