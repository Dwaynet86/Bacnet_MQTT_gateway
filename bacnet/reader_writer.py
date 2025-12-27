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
            
            # Add timeout to prevent hanging
            value = await asyncio.wait_for(
                self.app.read_property(
                    address,
                    obj_id,
                    prop_id,
                    array_index=array_index
                ),
                timeout=5.0  # 5 second timeout per read
            )
            
            # Update device's last seen timestamp
            device.update_last_seen()
            
            return value
            
        except asyncio.TimeoutError:
            logger.warning(
                f"Timeout reading {property_id} from {object_type}:{object_instance} "
                f"on device {device_id}"
            )
            return None
        except Exception as e:
            error_msg = str(e)
            # Only log errors that aren't common "property not supported" issues
            if 'unknown-property' in error_msg.lower():
                logger.debug(
                    f"Property {property_id} not supported on {object_type}:{object_instance} "
                    f"of device {device_id}"
                )
            elif 'buffer-overflow' in error_msg.lower():
                logger.warning(
                    f"Buffer overflow reading {property_id} from {object_type}:{object_instance} "
                    f"on device {device_id}. Value may be too large or segmentation not supported."
                )
            else:
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
        
        # Track which properties this object doesn't support to avoid repeated attempts
        if not hasattr(obj, '_unsupported_properties'):
            obj._unsupported_properties = set()
        
        # Define object types that support units
        UNITS_SUPPORTED_TYPES = {
            'analog-input', 'analog-output', 'analog-value',
            'accumulator', 'pulse-converter', 'loop',
            'large-analog-value'
        }
        
        logger.debug(f"Polling {obj.object_type}:{obj.object_instance} on device {device.device_id}")
        
        for prop_id in properties:
            # Skip properties we know this object doesn't support
            if prop_id in obj._unsupported_properties:
                logger.debug(f"  Skipping {prop_id} (marked as unsupported)")
                continue
                
            try:
                logger.debug(f"  Reading {prop_id}...")
                value = await self.read_property(
                    device.device_id,
                    obj.object_type,
                    obj.object_instance,
                    prop_id
                )
                
                logger.debug(f"  Read returned: {value} (type: {type(value).__name__ if value is not None else 'None'})")
                
                if value is not None:
                    # Try to get engineering units ONLY for analog types reading present-value
                    unit = None
                    if (prop_id == 'present-value' and 
                        obj.object_type in UNITS_SUPPORTED_TYPES and 
                        'units' not in obj._unsupported_properties):
                        try:
                            logger.debug(f"  Reading units...")
                            unit_value = await self.read_property(
                                device.device_id,
                                obj.object_type,
                                obj.object_instance,
                                'units'
                            )
                            if unit_value:
                                unit = str(unit_value)
                                logger.debug(f"  Got units: {unit}")
                        except Exception as e:
                            error_msg = str(e).lower()
                            if 'unknown-property' in error_msg:
                                obj._unsupported_properties.add('units')
                                logger.debug(f"  Units not supported")
                    
                    # Update object property
                    logger.info(f"  ✓ Updating {obj.object_type}:{obj.object_instance}.{prop_id} = {value} {unit or ''}")
                    obj.update_property(prop_id, value, unit)
                    
                    # Verify it was stored
                    stored_prop = obj.properties.get(prop_id)
                    if stored_prop:
                        logger.debug(f"  ✓ Verified stored: {stored_prop.value}")
                    else:
                        logger.error(f"  ✗ Property not stored!")
                    
                    results[prop_id] = value
                else:
                    logger.warning(f"  Got None for {prop_id}, marking as unsupported")
                    # If we got None, the property might not be supported
                    obj._unsupported_properties.add(prop_id)
                    
            except asyncio.TimeoutError:
                logger.warning(f"  ⏱ Timeout reading {prop_id} from {obj.object_type}:{obj.object_instance}")
            except Exception as e:
                error_msg = str(e).lower()
                if 'unknown-property' in error_msg:
                    # Mark this property as unsupported to avoid future attempts
                    logger.debug(f"  Property {prop_id} not supported (unknown-property error)")
                    obj._unsupported_properties.add(prop_id)
                else:
                    logger.warning(f"  ✗ Error reading {prop_id}: {e}")
        
        if results:
            logger.debug(f"✓ Poll complete for {obj.object_type}:{obj.object_instance}, got: {list(results.keys())}")
        
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
        logger.info(f"Starting poll for device {device.device_id}")
        logger.info(f"  Device enabled: {device.enabled}")
        logger.info(f"  Object count: {len(device.objects)}")
        logger.info(f"  Properties to read: {properties}")
        
        if len(device.objects) == 0:
            logger.warning(f"Device {device.device_id} has no objects to poll!")
            return
        
        logger.info(f"Polling device {device.device_id} - {len(device.objects)} objects")
        
        # Define which object types support present-value
        PRESENT_VALUE_TYPES = {
            'analog-input', 'analog-output', 'analog-value',
            'binary-input', 'binary-output', 'binary-value',
            'multi-state-input', 'multi-state-output', 'multi-state-value',
            'accumulator', 'pulse-converter', 'loop',
            'integer-value', 'positive-integer-value',
            'large-analog-value', 'octetstring-value',
            'characterstring-value', 'time-value', 'datetime-value',
            'datepattern-value', 'timepattern-value', 'datetimepattern-value'
        }
        
        successful_reads = 0
        skipped_objects = 0
        
        for obj in device.objects.values():
            try:
                # Skip objects that don't support present-value if that's what we're polling
                if 'present-value' in properties and obj.object_type not in PRESENT_VALUE_TYPES:
                    logger.debug(f"Skipping {obj.object_type}:{obj.object_instance} (doesn't support present-value)")
                    skipped_objects += 1
                    continue
                
                results = await self.poll_object(device, obj, properties)
                if results:
                    successful_reads += 1
                    logger.debug(f"Read {len(results)} properties from {obj.object_type}:{obj.object_instance}")
            except Exception as e:
                logger.error(
                    f"Error polling {obj.object_type}:{obj.object_instance}: {e}"
                )
        
        device.update_last_seen()
        logger.info(f"Polling complete for device {device.device_id}: {successful_reads} reads, {skipped_objects} skipped")

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
            except Exception as e:
                # Ignore errors during shutdown
                logger.debug(f"Error during poller shutdown: {e}")
        logger.info("BACnet poller stopped")
    
    async def _poll_loop(self):
        """Main polling loop"""
        while self.running:
            try:
                devices = self.device_registry.get_enabled_devices()
                logger.info(f"Polling {len(devices)} enabled devices")
                
                for device in devices:
                    if not self.running:
                        break
                        
                    if not device.enabled:
                        continue
                    
                    try:
                        await asyncio.wait_for(
                            self.reader_writer.poll_device_objects(
                                device,
                                self.properties
                            ),
                            timeout=60.0  # Increased timeout
                        )
                    except asyncio.TimeoutError:
                        logger.error(f"⏱ Timeout polling device {device.device_id}")
                    except Exception as e:
                        if self.running:
                            logger.error(f"✗ Error polling device {device.device_id}: {e}")
                
                # Save registry after polling
                if self.running:
                    logger.info("💾 Saving device registry...")
                    self.device_registry.save()
                    logger.info(f"✓ Polling cycle complete, sleeping {self.default_interval}s")
                
                await asyncio.sleep(self.default_interval)
                
            except asyncio.CancelledError:
                logger.info("Polling loop cancelled")
                break
            except Exception as e:
                if self.running:
                    logger.error(f"Error in poll loop: {e}")
                    await asyncio.sleep(5)
