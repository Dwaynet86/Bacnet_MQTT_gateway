"""
BACnet device discovery using WHO-IS/I-AM services
"""
import asyncio
import logging
from typing import Optional, List, Callable
from bacpypes3.app import Application
from bacpypes3.pdu import Address, GlobalBroadcast
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
        logger.info(f"Starting device discovery (timeout: {timeout}s, range: {low_limit}-{high_limit})")
        
        # Store discovered devices during this scan
        discovered = []
        received_iams = []
        
        # Create a custom handler to capture I-AM responses
        async def capture_iam(apdu):
            """Capture I-AM responses"""
            logger.debug(f"Received APDU type: {type(apdu).__name__}")
            if isinstance(apdu, IAmRequest):
                received_iams.append(apdu)
                logger.info(f"Received I-AM from device {apdu.iAmDeviceIdentifier[1]} at {apdu.pduSource}")
            else:
                logger.debug(f"Received non-IAM APDU: {apdu}")
        
        # Register the handler temporarily
        original_do_IAmRequest = getattr(self.app, 'do_IAmRequest', None)
        
        async def custom_do_IAmRequest(apdu):
            await capture_iam(apdu)
            if original_do_IAmRequest and callable(original_do_IAmRequest):
                try:
                    result = original_do_IAmRequest(apdu)
                    if asyncio.iscoroutine(result):
                        await result
                except Exception as e:
                    logger.debug(f"Error in original do_IAmRequest: {e}")
        
        self.app.do_IAmRequest = custom_do_IAmRequest
        
        try:
            # Create and send WHO-IS request
            logger.info("Sending WHO-IS broadcast...")
            
            # Try using the built-in who_is method first
            try:
                if hasattr(self.app, 'who_is'):
                    logger.debug(f"Using app.who_is method with range {low_limit}-{high_limit}")
                    await self.app.who_is(low_limit, high_limit)
                else:
                    # Manual WHO-IS construction
                    logger.debug("Constructing manual WHO-IS request")
                    who_is = WhoIsRequest()
                    
                    if low_limit is not None:
                        who_is.deviceInstanceRangeLowLimit = Unsigned(low_limit)
                        logger.debug(f"Set low limit: {low_limit}")
                    if high_limit is not None:
                        who_is.deviceInstanceRangeHighLimit = Unsigned(high_limit)
                        logger.debug(f"Set high limit: {high_limit}")
                    
                    # Set destination to broadcast
                    who_is.pduDestination = GlobalBroadcast()
                    logger.debug(f"Set destination to GlobalBroadcast: {who_is.pduDestination}")
                    
                    # Send the request
                    logger.info("Sending WHO-IS request...")
                    await self.app.request(who_is)
                    logger.info("WHO-IS request sent successfully")
                    
            except Exception as e:
                logger.error(f"Error sending WHO-IS: {e}", exc_info=True)
                return discovered
            
            # Wait for responses
            logger.info(f"Waiting {timeout} seconds for I-AM responses...")
            await asyncio.sleep(timeout)
            
            # Process received I-AMs
            logger.info(f"Processing {len(received_iams)} I-AM responses")
            if len(received_iams) == 0:
                logger.warning(
                    "No I-AM responses received. Possible issues:\n"
                    "  1. No BACnet devices on the network\n"
                    "  2. Wrong network/subnet configuration\n"
                    "  3. Firewall blocking UDP port 47808\n"
                    "  4. Devices on different BACnet network number\n"
                    "  5. Network address/mask incorrect in config"
                )
            
            for iam in received_iams:
                try:
                    device = await self._process_iam(iam)
                    if device:
                        discovered.append(device)
                        if self.on_device_discovered:
                            await self.on_device_discovered(device)
                except Exception as e:
                    logger.error(f"Error processing I-AM: {e}", exc_info=True)
            
            logger.info(f"Discovery complete: {len(discovered)} devices found")
            return discovered
            
        except Exception as e:
            logger.error(f"Error during discovery: {e}", exc_info=True)
            return discovered
        finally:
            # Restore original handler
            if original_do_IAmRequest:
                self.app.do_IAmRequest = original_do_IAmRequest
            elif hasattr(self.app, 'do_IAmRequest'):
                try:
                    delattr(self.app, 'do_IAmRequest')
                except:
                    pass
    
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
                error_msg = str(e)
                # Only log if it's not a common "property not supported" error
                if 'unknown-property' not in error_msg.lower():
                    logger.debug(f"Could not read {prop_name} from device {device.device_id}: {e}")
                # For unknown-property errors, silently skip
        
        
        
            # Network number not available - try to extract from address
            try:
                # If device address contains network info, extract it
                # BACnet address format can be "network:mac" or just "mac"
                addr_str = str(device.address)
                if ':' in addr_str:
                    # Format might be "2400:10.0.0.50" indicating network 2400
                    parts = addr_str.split(':')
                    if parts[0].isdigit():
                        device.network_number = int(parts[0])
                        logger.debug(f"Extracted network {device.network_number} from address")
            except:
                pass
            
            # If still no network number, leave it as None
            if device.network_number is None:
                logger.debug(f"Device {device.device_id} has no network number")
    
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
            error_msg = str(e)
            if 'buffer-overflow' in error_msg.lower():
                logger.warning(
                    f"Buffer overflow reading object list from device {device.device_id}. "
                    "Device may have too many objects or unsupported segmentation. "
                    "Try reading object list by index."
                )
                # Try reading object list by array index as fallback
                return await self._read_object_list_by_index(device)
            elif 'unknown-property' in error_msg.lower():
                logger.warning(f"Device {device.device_id} does not support object-list property")
            else:
                logger.error(f"Error reading object list from device {device.device_id}: {e}")
            return []
    
    async def _read_object_list_by_index(self, device: BACnetDevice) -> List[ObjectIdentifier]:
        """Read object list by iterating through array indices"""
        object_list = []
        try:
            device_obj_id = ObjectIdentifier(f"device,{device.device_id}")
            address = Address(device.address)
            
            # First, try to get the array length (index 0)
            try:
                length = await self.app.read_property(
                    address,
                    device_obj_id,
                    PropertyIdentifier('object-list'),
                    array_index=0
                )
                max_objects = int(length) if length else 100
            except:
                max_objects = 100  # Assume reasonable default
            
            logger.info(f"Reading object list by index for device {device.device_id} (max: {max_objects})")
            
            # Read each object one at a time
            for i in range(1, min(max_objects + 1, 500)):  # Cap at 500 to prevent infinite loops
                try:
                    obj_id = await self.app.read_property(
                        address,
                        device_obj_id,
                        PropertyIdentifier('object-list'),
                        array_index=i
                    )
                    if obj_id:
                        object_list.append(obj_id)
                except Exception as e:
                    # Stop when we get an error (reached end of list)
                    if 'invalid-array-index' in str(e).lower():
                        break
                    logger.debug(f"Error reading object-list[{i}]: {e}")
                    break
            
            logger.info(f"Read {len(object_list)} objects by index from device {device.device_id}")
            return object_list
            
        except Exception as e:
            logger.error(f"Error reading object list by index: {e}")
            return object_list
    
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
