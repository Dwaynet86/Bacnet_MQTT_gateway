# Bacnet2MQTT
A BACnet to MQTT gateway using bacpypes3
The gateway consists of several key modules:
1. Configuration (config.yaml)

BACnet settings (device ID, network parameters)
Discovery settings (auto-discovery, intervals)
Polling configuration
MQTT broker settings and topic structure
Device persistence
API configuration
Logging settings

2. Data Models (models/device.py)

BACnetProperty: Represents property values with timestamps and units
BACnetObject: Represents BACnet objects with properties
BACnetDevice: Complete device model with objects and metadata
DeviceRegistry: Manages device persistence and retrieval

3. BACnet Discovery (bacnet/discovery.py)

WHO-IS/I-AM protocol implementation
Automatic device discovery with configurable intervals
Device property enumeration
Object list reading and discovery

4. Read/Write Operations (bacnet/reader_writer.py)

Property read/write operations
Multiple property reading
BACnetPoller: Automatic periodic polling service
Property caching and timestamp tracking

5. MQTT Publishing (mqtt/publisher.py)

Dynamic topic creation: bacnet/{device_id}/{object_type}/{instance}/{property}
JSON payloads with metadata (device info, timestamps, units)
Automatic reconnection handling
MQTTPublishingService: Background publishing service

6. REST API (api/control.py)

GET /devices - List all devices
GET /devices/{id} - Get device details
POST /devices/discover - Trigger discovery
POST /devices/{id}/discover-objects - Discover objects
PUT /devices/{id}/enable|disable - Enable/disable devices
DELETE /devices/{id} - Remove device
POST /read - Read property
POST /write - Write property
GET /devices/{id}/objects - List objects

7. Main Application (main.py)
Orchestrates all components
Signal handling for graceful shutdown
Periodic discovery scheduling
Configuration management

Installation
git clone https://github.com/Dwaynet86/Bacnet_MQTT_gateway.git
cd Bacnet_MQTT_gateway
python3 -m venv bacnet_mqtt
source banet_mqtt/bin/activate
# Install dependencies
pip install -r requirements.txt
