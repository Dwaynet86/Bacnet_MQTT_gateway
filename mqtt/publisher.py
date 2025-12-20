"""
MQTT publishing service for BACnet data
"""
import json
import logging
import asyncio
from typing import Optional, Dict, Any
import paho.mqtt.client as mqtt
from models.device import BACnetDevice, BACnetObject, DeviceRegistry

logger = logging.getLogger(__name__)


class MQTTPublisher:
    """Publishes BACnet data to MQTT topics"""
    
    def __init__(
        self,
        broker: str,
        port: int = 1883,
        username: str = "",
        password: str = "",
        client_id: str = "bacnet_gateway",
        topic_prefix: str = "bacnet",
        qos: int = 1,
        retain: bool = True,
        keepalive: int = 60,
        mqtt_mapping_registry = None
    ):
        self.broker = broker
        self.port = port
        self.username = username
        self.password = password
        self.client_id = client_id
        self.topic_prefix = topic_prefix
        self.qos = qos
        self.retain = retain
        self.keepalive = keepalive
        self.mqtt_mapping_registry = mqtt_mapping_registry
        
        self.client: Optional[mqtt.Client] = None
        self.connected = False
        self.reconnect_delay = 5
        self.max_reconnect_delay = 300
        self.current_reconnect_delay = 5
    
    def connect(self):
        """Connect to MQTT broker"""
        try:
            self.client = mqtt.Client(client_id=self.client_id)
            
            # Set callbacks
            self.client.on_connect = self._on_connect
            self.client.on_disconnect = self._on_disconnect
            self.client.on_publish = self._on_publish
            
            # Set credentials if provided
            if self.username:
                self.client.username_pw_set(self.username, self.password)
            
            # Connect
            logger.info(f"Connecting to MQTT broker {self.broker}:{self.port}")
            self.client.connect(self.broker, self.port, self.keepalive)
            
            # Start network loop in background
            self.client.loop_start()
            
        except ConnectionRefusedError:
            logger.error(
                f"Connection refused by MQTT broker at {self.broker}:{self.port}. "
                "Please check if the MQTT broker is running and accessible."
            )
            raise
        except Exception as e:
            logger.error(f"Error connecting to MQTT broker: {e}")
            raise
    
    def disconnect(self):
        """Disconnect from MQTT broker"""
        if self.client:
            self.client.loop_stop()
            self.client.disconnect()
            self.connected = False
            logger.info("Disconnected from MQTT broker")
    
    def _on_connect(self, client, userdata, flags, rc):
        """Callback for when connection is established"""
        if rc == 0:
            self.connected = True
            self.current_reconnect_delay = self.reconnect_delay
            logger.info("Connected to MQTT broker")
        else:
            self.connected = False
            logger.error(f"Failed to connect to MQTT broker: {rc}")
    
    def _on_disconnect(self, client, userdata, rc):
        """Callback for when connection is lost"""
        self.connected = False
        if rc != 0:
            logger.warning(f"Unexpected disconnect from MQTT broker: {rc}")
    
    def _on_publish(self, client, userdata, mid):
        """Callback for when message is published"""
        logger.debug(f"Message {mid} published")
    
    def _build_topic(
        self,
        device_id: int,
        object_type: str,
        object_instance: int,
        property_id: str
    ) -> str:
        """
        Build MQTT topic for a BACnet property
        Format: {prefix}/{device_id}/{object_type}/{object_instance}/{property}
        """
        # Sanitize object type (replace spaces with underscores)
        obj_type_sanitized = object_type.replace(' ', '_').replace('-', '_')
        
        return f"{self.topic_prefix}/{device_id}/{obj_type_sanitized}/{object_instance}/{property_id}"
    
    def _build_payload(
        self,
        value: Any,
        device: BACnetDevice,
        obj: BACnetObject,
        property_id: str,
        timestamp: str,
        unit: Optional[str] = None
    ) -> str:
        """
        Build JSON payload for MQTT message
        """
        payload = {
            "value": value,
            "timestamp": timestamp,
            "device": {
                "id": device.device_id,
                "name": device.device_name,
                "address": device.address
            },
            "object": {
                "type": obj.object_type,
                "instance": obj.object_instance,
                "name": obj.object_name
            },
            "property": property_id
        }
        
        if unit:
            payload["unit"] = unit
        
        return json.dumps(payload)
    
    def publish_property(
        self,
        device: BACnetDevice,
        obj: BACnetObject,
        property_id: str
    ) -> bool:
        """
        Publish a single property to MQTT
        
        Returns:
            True if published successfully
        """
        if not self.connected:
            logger.warning("Not connected to MQTT broker")
            return False
        
        try:
            # Get property from object
            prop = obj.properties.get(property_id)
            if not prop:
                logger.debug(f"Property {property_id} not found in object")
                return False
            
            # Check if there's a custom mapping for this object
            topic = None
            if self.mqtt_mapping_registry:
                mapping = self.mqtt_mapping_registry.get_mapping(
                    device.device_id,
                    obj.object_type,
                    obj.object_instance
                )
                if mapping and mapping.enabled:
                    topic = mapping.mqtt_topic
                    logger.debug(f"Using mapped topic: {topic}")
            
            # Fall back to default topic if no mapping
            if not topic:
                topic = self._build_topic(
                    device.device_id,
                    obj.object_type,
                    obj.object_instance,
                    property_id
                )
            
            payload = self._build_payload(
                prop.value,
                device,
                obj,
                property_id,
                prop.timestamp,
                prop.unit
            )
            
            # Publish
            result = self.client.publish(
                topic,
                payload,
                qos=self.qos,
                retain=self.retain
            )
            
            if result.rc == mqtt.MQTT_ERR_SUCCESS:
                logger.debug(f"Published to {topic}")
                return True
            else:
                logger.error(f"Failed to publish to {topic}: {result.rc}")
                return False
                
        except Exception as e:
            logger.error(f"Error publishing property: {e}")
            return False
    
    def publish_object(self, device: BACnetDevice, obj: BACnetObject) -> int:
        """
        Publish all properties of an object to MQTT
        
        Returns:
            Number of properties published
        """
        count = 0
        for property_id in obj.properties.keys():
            if self.publish_property(device, obj, property_id):
                count += 1
        return count
    
    def publish_device(self, device: BACnetDevice) -> int:
        """
        Publish all objects and properties of a device to MQTT
        
        Returns:
            Number of properties published
        """
        count = 0
        for obj in device.objects.values():
            count += self.publish_object(device, obj)
        return count
    
    def publish_device_status(self, device: BACnetDevice):
        """Publish device status/availability"""
        if not self.connected:
            return
        
        topic = f"{self.topic_prefix}/{device.device_id}/status"
        payload = json.dumps({
            "device_id": device.device_id,
            "device_name": device.device_name,
            "address": device.address,
            "online": device.enabled,
            "last_seen": device.last_seen,
            "object_count": len(device.objects)
        })
        
        self.client.publish(topic, payload, qos=self.qos, retain=True)


class MQTTPublishingService:
    """Service that automatically publishes BACnet data to MQTT"""
    
    def __init__(
        self,
        publisher: MQTTPublisher,
        device_registry: DeviceRegistry,
        publish_interval: int = 5
    ):
        self.publisher = publisher
        self.device_registry = device_registry
        self.publish_interval = publish_interval
        self.running = False
        self.task = None
    
    async def start(self):
        """Start the publishing service"""
        if self.running:
            logger.warning("Publishing service already running")
            return
        
        self.publisher.connect()
        self.running = True
        self.task = asyncio.create_task(self._publish_loop())
        logger.info("MQTT publishing service started")
    
    async def stop(self):
        """Stop the publishing service"""
        self.running = False
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        
        self.publisher.disconnect()
        logger.info("MQTT publishing service stopped")
    
    async def _publish_loop(self):
        """Main publishing loop"""
        while self.running:
            try:
                if not self.publisher.connected:
                    logger.warning("MQTT not connected, waiting...")
                    await asyncio.sleep(5)
                    continue
                
                devices = self.device_registry.get_enabled_devices()
                
                total_published = 0
                for device in devices:
                    try:
                        # Publish device status
                        self.publisher.publish_device_status(device)
                        
                        # Publish all device data
                        count = self.publisher.publish_device(device)
                        total_published += count
                        
                    except Exception as e:
                        logger.error(
                            f"Error publishing device {device.device_id}: {e}"
                        )
                
                if total_published > 0:
                    logger.debug(f"Published {total_published} properties to MQTT")
                
                await asyncio.sleep(self.publish_interval)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in publishing loop: {e}")
                await asyncio.sleep(5)
