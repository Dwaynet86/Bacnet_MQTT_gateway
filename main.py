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
from models.mqtt_mapping import MQTTMappingRegistry
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
                'properties': ['present-value']
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
        
        # Initialize MQTT mapping registry first
        from models.mqtt_mapping import MQTTMappingRegistry
        self.mqtt_mapping_registry = MQTTMappingRegistry()
        
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
            retain=mqtt_config['retain'],
            mqtt_mapping_registry=getattr(self, 'mqtt_mapping_registry', None)
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
                self.mqtt_mapping_registry,
                gateway=self
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
        self.logger.info("Access the API at http://{api_config['host']}:{api_config['port']}")
        self.logger.info("View API docs at http://{api_config['host']}:{api_config['port']}/docs")
    
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
        
        # Stop poller first (with timeout)
        if self.poller:
            try:
                self.logger.info("Stopping Bacnet Poller")
                await asyncio.wait_for(self.poller.stop(), timeout=5.0)
            except asyncio.TimeoutError:
                self.logger.warning("Poller stop timed out")
            except Exception as e:
                self.logger.error(f"Error stopping poller: {e}")
        
        # Stop MQTT service
        if self.mqtt_service:
            try:
                self.logger.info("Stopping MQTT Service")
                await asyncio.wait_for(self.mqtt_service.stop(), timeout=5.0)
            except asyncio.TimeoutError:
                self.logger.warning("MQTT service stop timed out")
            except Exception as e:
                self.logger.error(f"Error stopping MQTT service: {e}")
        
        # Stop API server
        if self.api_server:
            self.api_server.should_exit = True
        
        # Save device registry
        try:
            self.device_registry.save()
        except Exception as e:
            self.logger.error(f"Error saving device registry: {e}")
        
        self.logger.info("Gateway stopped")
    
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
