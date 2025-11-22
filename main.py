async def _register_with_bbmd(self, bbmd_config: dict):
        """Register as a Foreign Device with a BBMD"""
        try:
            bbmd_address = bbmd_config.get('address')
            bbmd_port = bbmd_config.get('port', 47808)
            ttl = bbmd_config.get('ttl', 30)
            
            if not bbmd_address:
                self.logger.error("BBMD enabled but no address specified")
                return
            
            self.logger.info(f"Registering as Foreign Device with BBMD at {bbmd_address}:{bbmd_port"""
Main application entry point for BACnet-MQTT Gateway
"""
import asyncio
import logging
import signal
import sys
from pathlib import Path
import yaml
from logging.handlers import RotatingFileHandler

from bacpypes3.pdu import Address
from bacpypes3.primitivedata import ObjectIdentifier
from bacpypes3.app import Application
from bacpypes3.local.device import DeviceObject

try:
    from bacpypes3.ipv4.app import NormalApplication
except ImportError:
    # Fallback if NormalApplication is not available
    from bacpypes3.app import Application as NormalApplication

from models.device import DeviceRegistry
from bacnet.discovery import BACnetDiscovery
from bacnet.reader_writer import BACnetReaderWriter, BACnetPoller
from mqtt.publisher import MQTTPublisher, MQTTPublishingService
from api.control import APIController
import uvicorn


class BACnetMQTTGateway:
    """Main gateway application"""
    
    def __init__(self, config_path: str = "config.yaml"):
        self.config = self._load_config(config_path)
        self._setup_logging()
        
        self.logger = logging.getLogger(__name__)
        self.logger.info("Initializing BACnet-MQTT Gateway")
        
        # Initialize components
        self.device_registry = DeviceRegistry(
            self.config['devices']['persistence_file']
        )
        
        self.bacnet_app = None
        self.discovery = None
        self.reader_writer = None
        self.poller = None
        self.mqtt_publisher = None
        self.mqtt_service = None
        self.api_controller = None
        self.api_server = None
        
        self.running = False
    
    def _load_config(self, config_path: str) -> dict:
        """Load configuration from YAML file"""
        try:
            with open(config_path, 'r') as f:
                return yaml.safe_load(f)
        except FileNotFoundError:
            print(f"Config file {config_path} not found, using defaults")
            return self._default_config()
        except Exception as e:
            print(f"Error loading config: {e}")
            sys.exit(1)
    
    def _default_config(self) -> dict:
        """Return default configuration"""
        return {
            'bacnet': {
                'device_id': 999999,
                'device_name': 'BACnet-MQTT Gateway',
                'ip_address': '0.0.0.0',
                'port': 47808
            },
            'discovery': {
                'auto_discover': True,
                'discovery_interval': 300
            },
            'polling': {
                'enabled': True,
                'default_interval': 60,
                'properties': ['present-value', 'status-flags']
            },
            'mqtt': {
                'broker': 'localhost',
                'port': 1883,
                'topic_prefix': 'bacnet',
                'qos': 1,
                'retain': True
            },
            'devices': {
                'persistence_file': 'devices.json'
            },
            'api': {
                'enabled': True,
                'host': '0.0.0.0',
                'port': 8080
            },
            'logging': {
                'level': 'INFO',
                'console': True
            }
        }
    
    def _setup_logging(self):
        """Setup logging configuration"""
        log_config = self.config.get('logging', {})
        level = getattr(logging, log_config.get('level', 'INFO'))
        
        # Create formatters
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        
        # Root logger
        root_logger = logging.getLogger()
        root_logger.setLevel(level)
        
        # Console handler
        if log_config.get('console', True):
            console_handler = logging.StreamHandler()
            console_handler.setFormatter(formatter)
            root_logger.addHandler(console_handler)
        
        # File handler
        if 'file' in log_config:
            file_handler = RotatingFileHandler(
                log_config['file'],
                maxBytes=log_config.get('max_bytes', 10485760),
                backupCount=log_config.get('backup_count', 5)
            )
            file_handler.setFormatter(formatter)
            root_logger.addHandler(file_handler)
    
    async def initialize(self):
        """Initialize all components"""
        self.logger.info("Initializing components")
        
        # Initialize BACnet application
        await self._initialize_bacnet()
        
        # Initialize discovery
        self.discovery = BACnetDiscovery(
            self.bacnet_app,
            self.device_registry,
            on_device_discovered=self._on_device_discovered
        )
        
        # Initialize reader/writer
        self.reader_writer = BACnetReaderWriter(
            self.bacnet_app,
            self.device_registry
        )
        
        # Initialize poller if enabled
        if self.config['polling']['enabled']:
            self.poller = BACnetPoller(
                self.reader_writer,
                self.device_registry,
                self.config['polling']['default_interval'],
                self.config['polling']['properties']
            )
        
        # Initialize MQTT publisher
        mqtt_config = self.config['mqtt']
        self.mqtt_publisher = MQTTPublisher(
            broker=mqtt_config['broker'],
            port=mqtt_config['port'],
            username=mqtt_config.get('username', ''),
            password=mqtt_config.get('password', ''),
            topic_prefix=mqtt_config['topic_prefix'],
            qos=mqtt_config['qos'],
            retain=mqtt_config['retain']
        )
        
        # Initialize MQTT publishing service
        self.mqtt_service = MQTTPublishingService(
            self.mqtt_publisher,
            self.device_registry,
            publish_interval=5
        )
        
        # Initialize API if enabled
        if self.config['api']['enabled']:
            self.api_controller = APIController(
                self.device_registry,
                self.discovery,
                self.reader_writer,
                gateway=self  # Pass reference to gateway for BBMD operations
            )
        
        self.logger.info("Initialization complete")
    
    async def _initialize_bacnet(self):
        """Initialize BACnet application"""
        bacnet_config = self.config['bacnet']
        
        # Create device object with all required properties
        device_id = bacnet_config['device_id']
        device_address = bacnet_config['ip_address']
        device_port = bacnet_config.get('port', 47808)
        
        # Create the device object
        device = DeviceObject(
            objectIdentifier=('device', device_id),
            objectName=bacnet_config['device_name'],
            maxApduLengthAccepted=bacnet_config.get('max_apdu_length', 1476),
            segmentationSupported=bacnet_config.get('segmentation_supported', 'segmentedBoth'),
            vendorIdentifier=bacnet_config.get('vendor_id', 15),
            vendorName="BACnet-MQTT Gateway",
            modelName="Gateway v1.0",
            description="BACnet to MQTT Gateway"
        )
        
        # Create address with proper format for BACpypes3
        # Format: "ip_address/netmask:port" for proper broadcast support
        if device_address == "0.0.0.0":
            # For binding to all interfaces, we need to determine the actual IP
            import socket
            import netifaces
            try:
                # Try to get default gateway interface
                gws = netifaces.gateways()
                default_interface = gws['default'][netifaces.AF_INET][1]
                addrs = netifaces.ifaddresses(default_interface)
                ip_info = addrs[netifaces.AF_INET][0]
                device_address = ip_info['addr']
                netmask = ip_info.get('netmask', '255.255.255.0')
                self.logger.info(f"Using network interface {default_interface}: {device_address}/{netmask}")
            except Exception as e:
                self.logger.warning(f"Could not determine network interface using netifaces: {e}")
                # Fallback method
                try:
                    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    s.connect(("8.8.8.8", 80))
                    device_address = s.getsockname()[0]
                    s.close()
                    netmask = '255.255.255.0'  # Assume /24 network
                    self.logger.info(f"Using network interface: {device_address}/{netmask}")
                except Exception:
                    device_address = "127.0.0.1"
                    netmask = "255.0.0.0"
                    self.logger.warning("Could not determine network interface, using 127.0.0.1")
        else:
            # Use the configured address with a default netmask
            netmask = bacnet_config.get('netmask', '255.255.255.0')
        
        # Create address with netmask for broadcast support
        # Format: "ip/netmask:port"
        address = Address(f"{device_address}/{netmask}:{device_port}")
        
        # Initialize the application using NormalApplication for IPv4
        try:
            self.bacnet_app = NormalApplication(device, address)
            self.logger.info(
                f"BACnet application initialized: "
                f"Device {device_id} at {address}"
            )
            
            # Register as Foreign Device with BBMD if configured
            bbmd_config = bacnet_config.get('bbmd', {})
            if bbmd_config.get('enabled', False):
                await self._register_with_bbmd(bbmd_config)
            
        except Exception as e:
            self.logger.error(f"Failed to initialize BACnet application: {e}")
            raise
    
    async def _register_with_bbmd(self, bbmd_config: dict):
        """Register as a Foreign Device with a BBMD"""
        try:
            bbmd_address = bbmd_config.get('address')
            bbmd_port = bbmd_config.get('port', 47808)
            ttl = bbmd_config.get('ttl', 30)
            
            if not bbmd_address:
                self.logger.error("BBMD enabled but no address specified")
                return
            
            self.logger.info(f"Registering as Foreign Device with BBMD at {bbmd_address}:{bbmd_port}")
            
            # Create BBMD address
            bbmd_addr = Address(f"{bbmd_address}:{bbmd_port}")
            
            try:
                # Try to use the BIP Simple method if available
                if hasattr(self.bacnet_app, 'bip') and hasattr(self.bacnet_app.bip, 'register'):
                    await self.bacnet_app.bip.register(bbmd_addr, ttl)
                    self.logger.info(f"Successfully registered with BBMD using bip.register (TTL: {ttl}s)")
                    
                    # Set up periodic re-registration if TTL > 0
                    if ttl > 0:
                        asyncio.create_task(self._periodic_bbmd_registration(bbmd_addr, ttl))
                    return
                    
            except AttributeError:
                pass  # Method not available, try next approach
            except Exception as e:
                self.logger.debug(f"bip.register failed: {e}")
            
            # Try accessing the BVLL layer directly
            try:
                from bacpypes3.ipv4.bvll import RegisterForeignDevice, BVLPDU
                
                # Create the registration PDU
                register_pdu = RegisterForeignDevice(ttl)
                register_pdu.pduDestination = bbmd_addr
                
                # Find the BVLL service access point
                bvll_sap = None
                if hasattr(self.bacnet_app, 'bip'):
                    bvll_sap = self.bacnet_app.bip
                elif hasattr(self.bacnet_app, 'bvll'):
                    bvll_sap = self.bacnet_app.bvll
                elif hasattr(self.bacnet_app, 'nse') and hasattr(self.bacnet_app.nse, 'clientPeer'):
                    # Try to get BVLL from NSE's client peer
                    bvll_sap = self.bacnet_app.nse.clientPeer
                
                if bvll_sap and hasattr(bvll_sap, 'request'):
                    await bvll_sap.request(register_pdu)
                    self.logger.info(f"Sent Foreign Device registration to BBMD (TTL: {ttl}s)")
                    
                    # Set up periodic re-registration
                    if ttl > 0:
                        asyncio.create_task(self._periodic_bbmd_registration(bbmd_addr, ttl))
                    return
                else:
                    self.logger.warning("Could not find BVLL service access point")
                    
            except ImportError as e:
                self.logger.error(f"Cannot import BVLL classes: {e}")
            except Exception as e:
                self.logger.debug(f"BVLL direct access failed: {e}")
            
            # Last resort: try using the application request method with proper PDU
            try:
                from bacpypes3.ipv4.bvll import RegisterForeignDevice
                
                register_pdu = RegisterForeignDevice(ttl)
                register_pdu.pduDestination = bbmd_addr
                
                # Try sending through the application
                await self.bacnet_app.request(register_pdu)
                self.logger.info(f"Sent Foreign Device registration via app.request (TTL: {ttl}s)")
                
                # Set up periodic re-registration
                if ttl > 0:
                    asyncio.create_task(self._periodic_bbmd_registration(bbmd_addr, ttl))
                    
            except Exception as e:
                self.logger.error(f"All BBMD registration methods failed: {e}")
                self.logger.info(
                    "BBMD registration not successful. You may need to:\n"
                    "  1. Upgrade bacpypes3 to a version with BBMD support\n"
                    "  2. Use a different tool to register as Foreign Device\n"
                    "  3. Contact the BACnet network administrator"
                )
                
        except Exception as e:
            self.logger.error(f"Error registering with BBMD: {e}", exc_info=True)
    
    async def _periodic_bbmd_registration(self, bbmd_addr: Address, ttl: int):
        """Periodically re-register with BBMD before TTL expires"""
        try:
            # Re-register at 50% of TTL to ensure continuous connection
            interval = max(ttl // 2, 5)  # Minimum 5 seconds
            
            self.logger.info(f"Starting periodic BBMD registration (every {interval}s)")
            
            while self.running:
                await asyncio.sleep(interval)
                
                if not self.running:
                    break
                
                try:
                    # Try bip.register first
                    if hasattr(self.bacnet_app, 'bip') and hasattr(self.bacnet_app.bip, 'register'):
                        await self.bacnet_app.bip.register(bbmd_addr, ttl)
                        self.logger.debug(f"Re-registered with BBMD (TTL: {ttl}s)")
                        continue
                    
                    # Fallback to BVLL layer
                    from bacpypes3.ipv4.bvll import RegisterForeignDevice
                    
                    register_pdu = RegisterForeignDevice(ttl)
                    register_pdu.pduDestination = bbmd_addr
                    
                    # Find BVLL SAP
                    bvll_sap = None
                    if hasattr(self.bacnet_app, 'bip'):
                        bvll_sap = self.bacnet_app.bip
                    elif hasattr(self.bacnet_app, 'bvll'):
                        bvll_sap = self.bacnet_app.bvll
                    elif hasattr(self.bacnet_app, 'nse') and hasattr(self.bacnet_app.nse, 'clientPeer'):
                        bvll_sap = self.bacnet_app.nse.clientPeer
                    
                    if bvll_sap and hasattr(bvll_sap, 'request'):
                        await bvll_sap.request(register_pdu)
                        self.logger.debug(f"Re-registered with BBMD (TTL: {ttl}s)")
                    
                except Exception as e:
                    self.logger.error(f"Error re-registering with BBMD: {e}")
                    
        except asyncio.CancelledError:
            self.logger.info("Stopping BBMD registration task")
        except Exception as e:
            self.logger.error(f"Error in BBMD registration loop: {e}")
    
    async def _on_device_discovered(self, device):
        """Callback when a device is discovered"""
        self.logger.info(
            f"Device discovered: {device.device_id} "
            f"({device.device_name}) at {device.address}"
        )
        
        # Automatically discover objects for new devices
        await self.discovery.discover_device_objects(device)
        
        # Save registry
        self.device_registry.save()
    
    async def start(self):
        """Start the gateway"""
        if self.running:
            self.logger.warning("Gateway already running")
            return
        
        self.running = True
        self.logger.info("Starting BACnet-MQTT Gateway")
        
        # Start MQTT service with error handling
        try:
            await self.mqtt_service.start()
        except ConnectionRefusedError:
            self.logger.error(
                "MQTT broker connection failed. The gateway will continue without MQTT publishing. "
                "Please check your MQTT broker configuration in config.yaml"
            )
        except Exception as e:
            self.logger.error(f"Error starting MQTT service: {e}")
            self.logger.warning("Continuing without MQTT publishing")
        
        # Start poller if enabled
        if self.poller:
            await self.poller.start()
        
        # Initial discovery if enabled
        if self.config['discovery']['auto_discover']:
            self.logger.info("Starting initial device discovery")
            try:
                await self.discovery.discover_devices(
                    timeout=self.config['discovery'].get('who_is_timeout', 5)
                )
            except Exception as e:
                self.logger.error(f"Error during initial discovery: {e}")
        
        # Start API server if enabled
        if self.api_controller:
            api_config = self.config['api']
            config = uvicorn.Config(
                self.api_controller.app,
                host=api_config['host'],
                port=api_config['port'],
                log_level="info"
            )
            self.api_server = uvicorn.Server(config)
            
            # Run API server in background
            asyncio.create_task(self.api_server.serve())
            self.logger.info(
                f"API server started on "
                f"{api_config['host']}:{api_config['port']}"
            )
        
        # Periodic discovery if configured
        if self.config['discovery']['auto_discover']:
            asyncio.create_task(self._periodic_discovery())
        
        self.logger.info("Gateway started successfully")
        self.logger.info("Access the API at http://localhost:8080")
        self.logger.info("View API docs at http://localhost:8080/docs")
    
    async def _periodic_discovery(self):
        """Periodically discover new devices"""
        interval = self.config['discovery'].get('discovery_interval', 300)
        timeout = self.config['discovery'].get('who_is_timeout', 5)
        
        while self.running:
            await asyncio.sleep(interval)
            if self.running:
                self.logger.info("Running periodic device discovery")
                try:
                    await self.discovery.discover_devices(timeout=timeout)
                except Exception as e:
                    self.logger.error(f"Error in periodic discovery: {e}")
    
    async def stop(self):
        """Stop the gateway"""
        if not self.running:
            return
        
        self.logger.info("Stopping BACnet-MQTT Gateway")
        self.running = False
        
        # Unregister from BBMD if registered
        bbmd_config = self.config.get('bacnet', {}).get('bbmd', {})
        if bbmd_config.get('enabled', False):
            await self._unregister_from_bbmd(bbmd_config)
        
        # Stop poller
        if self.poller:
            await self.poller.stop()
        
        # Stop MQTT service
        if self.mqtt_service:
            await self.mqtt_service.stop()
        
        # Stop API server
        if self.api_server:
            self.api_server.should_exit = True
        
        # Save device registry
        self.device_registry.save()
        
        self.logger.info("Gateway stopped")
    
    async def _unregister_from_bbmd(self, bbmd_config: dict):
        """Unregister from BBMD when shutting down"""
        try:
            bbmd_address = bbmd_config.get('address')
            bbmd_port = bbmd_config.get('port', 47808)
            
            if not bbmd_address:
                return
            
            self.logger.info(f"Unregistering from BBMD at {bbmd_address}:{bbmd_port}")
            
            bbmd_addr = Address(f"{bbmd_address}:{bbmd_port}")
            
            try:
                # Try bip.register with TTL=0
                if hasattr(self.bacnet_app, 'bip') and hasattr(self.bacnet_app.bip, 'register'):
                    await self.bacnet_app.bip.register(bbmd_addr, 0)
                    self.logger.info("Unregistered from BBMD")
                    return
            except:
                pass
            
            # Fallback to BVLL
            try:
                from bacpypes3.ipv4.bvll import RegisterForeignDevice
                
                register_pdu = RegisterForeignDevice(0)  # TTL=0 to unregister
                register_pdu.pduDestination = bbmd_addr
                
                # Find BVLL SAP
                bvll_sap = None
                if hasattr(self.bacnet_app, 'bip'):
                    bvll_sap = self.bacnet_app.bip
                elif hasattr(self.bacnet_app, 'bvll'):
                    bvll_sap = self.bacnet_app.bvll
                elif hasattr(self.bacnet_app, 'nse') and hasattr(self.bacnet_app.nse, 'clientPeer'):
                    bvll_sap = self.bacnet_app.nse.clientPeer
                
                if bvll_sap and hasattr(bvll_sap, 'request'):
                    await bvll_sap.request(register_pdu)
                    self.logger.info("Unregistered from BBMD")
                    
            except Exception as e:
                self.logger.debug(f"Error during BBMD unregistration: {e}")
                
        except Exception as e:
            self.logger.error(f"Error unregistering from BBMD: {e}")
    
    async def run(self):
        """Run the gateway"""
        await self.initialize()
        await self.start()
        
        # Wait for shutdown signal
        try:
            while self.running:
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            self.logger.info("Received shutdown signal")
        finally:
            await self.stop()


async def main():
    """Main entry point"""
    gateway = BACnetMQTTGateway()
    
    # Setup signal handlers
    loop = asyncio.get_event_loop()
    
    def signal_handler():
        asyncio.create_task(gateway.stop())
    
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, signal_handler)
    
    # Run gateway
    await gateway.run()


if __name__ == "__main__":
    asyncio.run(main())
