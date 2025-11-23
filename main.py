"""
Main application entry point for BACnet-MQTT Gateway
"""
import asyncio
import logging
import signal
import sys
from pathlib import Path
import yaml
from logging.handlers import RotatingFileHandler

from bacpypes3.argparse import SimpleArgumentParser
from bacpypes3.app import Application

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
        """Initialize BACnet application using SimpleArgumentParser"""
        bacnet_config = self.config['bacnet']
        bbmd_config = bacnet_config.get('bbmd', {})
        
        try:
            # Build argument list for SimpleArgumentParser
            args_list = [
                '--name', bacnet_config['device_name'],
                '--instance', str(bacnet_config['device_id']),
            ]
            
            # Add address if specified (otherwise auto-detect)
            if bacnet_config['ip_address'] != "0.0.0.0":
                args_list.extend(['--address', bacnet_config['ip_address']])
            
            # Add BBMD/Foreign Device configuration if enabled
            if bbmd_config.get('enabled', False):
                bbmd_address = bbmd_config.get('address')
                bbmd_port = bbmd_config.get('port', 47808)
                ttl = bbmd_config.get('ttl', 30)
                
                if bbmd_address:
                    args_list.extend([
                        '--foreign', f"{bbmd_address}:{bbmd_port}",
                        '--ttl', str(ttl)
                    ])
                    self.logger.info(
                        f"Configuring Foreign Device: BBMD={bbmd_address}:{bbmd_port}, TTL={ttl}s"
                    )
            
            # Parse arguments using BACpypes3's SimpleArgumentParser
            parser = SimpleArgumentParser()
            args = parser.parse_args(args_list)
            
            self.logger.debug(f"Parsed args: {args}")
            
            # Create the application - SimpleArgumentParser handles all the setup
            self.bacnet_app = Application.from_args(args)
            
            self.logger.info(
                f"BACnet application initialized: Device {bacnet_config['device_id']}"
            )
            
            # Log the actual address being used
            if hasattr(self.bacnet_app, 'nse') and hasattr(self.bacnet_app.nse, 'localAddress'):
                self.logger.info(f"Local address: {self.bacnet_app.nse.localAddress}")
            
            # Log BBMD registration status
            if bbmd_config.get('enabled', False):
                if hasattr(self.bacnet_app, 'bip'):
                    self.logger.info("✓ Foreign Device registration configured")
                else:
                    self.logger.warning("BBMD configured but bip layer not found")
            
        except Exception as e:
            self.logger.error(f"Failed to initialize BACnet application: {e}", exc_info=True)
            self.logger.info(
                "Tip: Check your config.yaml settings:\n"
                "  - device_id must be unique\n"
                "  - BBMD address must be reachable\n"
                "  - Network interface must be available"
            )
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
            
            # Method 1: Try bip.register if available
            try:
                if hasattr(self.bacnet_app, 'bip') and hasattr(self.bacnet_app.bip, 'register'):
                    self.logger.debug("Trying method 1: bip.register()")
                    await self.bacnet_app.bip.register(bbmd_addr, ttl)
                    self.logger.info(f"✓ Successfully registered with BBMD (TTL: {ttl}s)")
                    
                    if ttl > 0:
                        asyncio.create_task(self._periodic_bbmd_registration(bbmd_addr, ttl))
                    return
            except Exception as e:
                self.logger.debug(f"Method 1 failed: {e}")
            
            # Method 2: Direct BVLL layer access
            try:
                from bacpypes3.ipv4 import bvll
                
                self.logger.debug("Trying method 2: Direct BVLL access")
                self.logger.debug(f"Available BVLL classes: {dir(bvll)}")
                
                # Find RegisterForeignDevice class
                if hasattr(bvll, 'RegisterForeignDevice'):
                    RegFD = bvll.RegisterForeignDevice
                    self.logger.debug(f"Found RegisterForeignDevice: {RegFD}")
                    
                    # Try to create instance with different signatures
                    register_pdu = None
                    try:
                        # Try: RegisterForeignDevice(ttl)
                        register_pdu = RegFD(ttl)
                        self.logger.debug(f"Created with RegisterForeignDevice({ttl})")
                    except:
                        try:
                            # Try: RegisterForeignDevice()
                            register_pdu = RegFD()
                            # Set TTL via different possible attribute names
                            for attr in ['bvlciTimeToLive', 'ttl', 'timeToLive']:
                                if hasattr(register_pdu, attr):
                                    setattr(register_pdu, attr, ttl)
                                    self.logger.debug(f"Set {attr} = {ttl}")
                                    break
                        except Exception as e:
                            self.logger.debug(f"Could not create RegisterForeignDevice: {e}")
                    
                    if register_pdu:
                        register_pdu.pduDestination = bbmd_addr
                        
                        # Find BVLL service point
                        bvll_sap = None
                        for attr in ['bip', 'bvll', 'annexj']:
                            if hasattr(self.bacnet_app, attr):
                                bvll_sap = getattr(self.bacnet_app, attr)
                                self.logger.debug(f"Found BVLL SAP: {attr} = {type(bvll_sap)}")
                                break
                        
                        if not bvll_sap and hasattr(self.bacnet_app, 'nse'):
                            if hasattr(self.bacnet_app.nse, 'clientPeer'):
                                bvll_sap = self.bacnet_app.nse.clientPeer
                                self.logger.debug(f"Found BVLL SAP via nse.clientPeer: {type(bvll_sap)}")
                        
                        if bvll_sap:
                            # Try different send methods
                            for method in ['request', 'indication', 'confirmation']:
                                if hasattr(bvll_sap, method):
                                    try:
                                        self.logger.debug(f"Trying bvll_sap.{method}()")
                                        method_func = getattr(bvll_sap, method)
                                        result = method_func(register_pdu)
                                        if asyncio.iscoroutine(result):
                                            await result
                                        self.logger.info(f"✓ Registered via {method} (TTL: {ttl}s)")
                                        
                                        if ttl > 0:
                                            asyncio.create_task(self._periodic_bbmd_registration(bbmd_addr, ttl))
                                        return
                                    except Exception as e:
                                        self.logger.debug(f"bvll_sap.{method}() failed: {e}")
                        
            except Exception as e:
                self.logger.debug(f"Method 2 failed: {e}", exc_info=True)
            
            # Method 3: Manual UDP packet construction
            try:
                import socket
                import struct
                
                self.logger.info("Trying method 3: Manual UDP registration")
                
                # BACnet/IP BVLL Register-Foreign-Device packet
                # BVLL Type: 0x81 (BACnet/IP)
                # Function: 0x05 (Register-Foreign-Device)
                # Length: 0x0006 (6 bytes)
                # TTL: 2 bytes
                packet = struct.pack('!BBHHs', 
                    0x81,  # BVLL Type
                    0x05,  # Register-Foreign-Device function
                    0x0006,  # Length (6 bytes total)
                    ttl,  # Time-to-live
                    0x00  # Padding
                )
                
                # Actually, correct format is simpler:
                packet = struct.pack('!BBHH',
                    0x81,  # BVLL Type  
                    0x05,  # Register-Foreign-Device
                    0x0006,  # Length
                    ttl  # TTL in seconds
                )
                
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.sendto(packet, (bbmd_address, bbmd_port))
                sock.close()
                
                self.logger.info(f"✓ Sent manual BBMD registration (TTL: {ttl}s)")
                
                if ttl > 0:
                    asyncio.create_task(self._periodic_bbmd_registration(bbmd_addr, ttl))
                return
                
            except Exception as e:
                self.logger.error(f"Method 3 failed: {e}", exc_info=True)
            
            self.logger.error("All BBMD registration methods failed")
            self.logger.info(
                "Troubleshooting steps:\n"
                "  1. Verify BBMD is reachable: ping " + bbmd_address + "\n"
                "  2. Check UDP port 47808 is not blocked\n"
                "  3. Verify BBMD address is correct\n"
                "  4. Check if BBMD allows Foreign Device registration"
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
        
        # BACpypes3 SimpleArgumentParser handles BBMD unregistration automatically
        # when the application is cleaned up
        
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
