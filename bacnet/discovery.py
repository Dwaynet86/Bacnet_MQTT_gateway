"""
BACnet device discovery using WHO-IS/I-AM services
"""
import asyncio
import logging
from typing import Optional, List, Callable
from bacpypes3.app import Application
from bacpypes3.pdu import Address
from bacpypes3.basetypes import PropertyIdentifier
from bacpypes3.primitivedata import ObjectIdentifier, Unsigned
from bacpypes3.apdu import IAmRequest, WhoIsRequest
from bacpypes3.local.device import DeviceObject
from models.device import BACnetDevice, DeviceRegistry

logger = logging.getLogger(__name__)


class BACnetDiscovery:
    """Handles BACnet device discovery"""
    
    def __init__(
        self,
        app: Application,
        device_registry: DeviceRegistry,
        on_device_discovered: Optional[Callable] = None
    ):
        self.app = app
        self.device_registry = device_registry
        self.on_device_discovered = on_device_discovered
        self.discovered_addresses = {}
        
    async def discover_devices(
        self,
        low_limit: Optional[int] = None,
        high_limit: Optional[int] = None,
        timeout: int = 5
    ) -> List[BACnetDevice]:
        """
        Discover BACnet devices on the network using WHO-IS
        
        Args:
            low_limit: Lower device instance range (optional)
            high_limit: Upper device instance range (optional)
            timeout: Discovery timeout in seconds
            
        Returns:
            List of discovered devices
        """
        logger.info(f"Starting device discovery (timeout: {timeout}s)")
        
        # Store discovered devices during this scan
        discovered = []
        
        # Create WHO-IS request
        who_is = WhoIsRequest()
        if low_limit is not None:
            who_is.deviceInstanceRangeLowLimit = Unsigned(low_limit)
        if high_limit is not None:
            who_is.deviceInstanceRangeHighLimit = Unsigned(high_limit)
        
        # Send as broadcast
        who_is.pduDestination = Address("*:47808")
        
        # Set up I-AM handler
        original_handler = self.app.indication
        received_iams = []
        
        def iam_handler(apdu):
            """Capture I-AM responses"""
            if isinstance(apdu, IAmRequest):
                received_iams.append(apdu)
            # Call original handler for other PDUs
            return original_handler(apdu)
        
        # Temporarily replace indication handler
        self.app.indication = iam_handler
        
        try:
            # Send WHO-IS
            await self.app.request(who_is)
            
            # Wait for responses
            await asyncio.sleep(timeout)
            
            # Process received I-AMs
            for iam in received_iams:
                try:
                    device = await self._process_iam(iam)
                    if device:
                        discovered.append(device)
                        if self.on_device_discovered:
                            await self.on_device_discovered(device)
                except Exception as e:
                    logger.error(f"Error processing I-AM: {e}")
            
            logger.info(f"Discovery complete: {len(discovered)} devices found")
            return discovered
            
        finally:
            # Restore original handler
            self.app.indication = original_handler
    
    async def _process_iam(self, iam: IAmRequest) -> Optional[BACnetDevice]:
        """Process an I-AM response and create/update device"""
        try:
            device_id = int(iam.iAmDeviceIdentifier[1])
            address = str(iam.pduSource)
            
            logger.debug(f"Processing I-AM from device {device_id} at {address}")
            
            # Check if we already know this device
            existing_device = self.device_registry.get_device(device_id)
            
            if existing_device:
                # Update existing device
                existing_device.address = address
                existing_device.max_apdu_length = int(iam.maxAPDULengthAccepted)
                existing_device.segmentation_supported = str(iam.segmentationSupported)
                existing_device.update_last_seen()
                device = existing_device
            else:
                # Create new device
                device = BACnetDevice(
                    device_id=device_id,
                    address=address,
                    max_apdu_length=int(iam.maxAPDULengthAccepted),
                    segmentation_supported=str(iam.segmentationSupported)
                )
            
            # Read device properties
            await self._read_device_properties(device)
            
            # Add to registry
            self.device_registry.add_device(device)
            self.discovered_addresses[device_id] = address
            
            return device
            
        except Exception as e:
            logger.error(f"Error processing I-AM: {e}")
            return None
    
    async def _read_device_properties(self, device: BACnetDevice):
        """Read basic properties from a device"""
        properties_to_read = [
            'object-name',
            'vendor-name',
            'model-name',
            'firmware-revision',
            'application-software-version',
            'protocol-version',
            'protocol-revision'
        ]
        
        device_obj_id = ObjectIdentifier(f"device,{device.device_id}")
        address = Address(device.address)
        
        for prop_name in properties_to_read:
            try:
                value = await self.app.read_property(
                    address,
                    device_obj_id,
                    PropertyIdentifier(prop_name)
                )
                
                # Map property to device attribute
                attr_map = {
                    'object-name': 'device_name',
                    'vendor-name': 'vendor_name',
                    'model-name': 'model_name',
                    'firmware-revision': 'firmware_revision',
                    'application-software-version': 'application_software_version',
                    'protocol-version': 'protocol_version',
                    'protocol-revision': 'protocol_revision'
                }
                
                attr = attr_map.get(prop_name)
                if attr and value is not None:
                    setattr(device, attr, str(value))
                    
            except Exception as e:
                logger.debug(f"Could not read {prop_name} from device {device.device_id}: {e}")
    
    async def read_device_object_list(self, device: BACnetDevice) -> List[ObjectIdentifier]:
        """Read the object list from a device"""
        try:
            device_obj_id = ObjectIdentifier(f"device,{device.device_id}")
            address = Address(device.address)
            
            object_list = await self.app.read_property(
                address,
                device_obj_id,
                PropertyIdentifier('object-list')
            )
            
            if object_list:
                logger.info(f"Device {device.device_id} has {len(object_list)} objects")
                return object_list
            
            return []
            
        except Exception as e:
            logger.error(f"Error reading object list from device {device.device_id}: {e}")
            return []
    
    async def discover_device_objects(self, device: BACnetDevice):
        """Discover all objects in a device"""
        object_list = await self.read_device_object_list(device)
        
        for obj_id in object_list:
            try:
                # Skip the device object itself
                if obj_id[0] == 'device':
                    continue
                
                # Create BACnetObject
                from models.device import BACnetObject
                
                obj = BACnetObject(
                    object_type=str(obj_id[0]),
                    object_instance=int(obj_id[1])
                )
                
                # Read object-name if available
                try:
                    address = Address(device.address)
                    name = await self.app.read_property(
                        address,
                        obj_id,
                        PropertyIdentifier('object-name')
                    )
                    if name:
                        obj.object_name = str(name)
                except:
                    pass
                
                device.add_object(obj)
                
            except Exception as e:
                logger.debug(f"Error processing object {obj_id}: {e}")
