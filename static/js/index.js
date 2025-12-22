const { useState, useEffect } = React;

function App() {
    const [devices, setDevices] = useState([]);
    const [selectedItem, setSelectedItem] = useState(null);
    const [expandedNodes, setExpandedNodes] = useState(new Set());
    const [status, setStatus] = useState({ devices: 0, bbmd: false });
    const [loading, setLoading] = useState(false);
    const [lowLimit, setLowLimit] = useState('240001');
    const [highLimit, setHighLimit] = useState('250000');
    const [discoverTimeout, setDiscoverTimeout] = useState('10');

    const API_BASE = window.location.origin;

    useEffect(() => {
        loadDevices();
        loadStatus();
        const interval = setInterval(loadDevices, 5000);
        return () => clearInterval(interval);
    }, []);

    useEffect(() => {
        if (selectedItem && selectedItem.type === 'device') {
            const interval = setInterval(async () => {
                const updated = await loadDeviceDetails(selectedItem.id);
                if (updated) {
                    setSelectedItem({ ...selectedItem, data: updated });
                }
            }, 5000);
            return () => clearInterval(interval);
        }
    }, [selectedItem]);

    const loadDevices = async () => {
        try {
            const response = await fetch(`${API_BASE}/devices`);
            const data = await response.json();
            setDevices(data);
        } catch (error) {
            console.error('Error loading devices:', error);
        }
    };

    const loadStatus = async () => {
        try {
            const response = await fetch(`${API_BASE}/status`);
            const data = await response.json();
            setStatus({
                devices: data.devices_count || 0,
                bbmd: data.bbmd?.enabled || false
            });
        } catch (error) {
            console.error('Error loading status:', error);
        }
    };

    const handleDiscover = async () => {
        setLoading(true);
        try {
            const response = await fetch(`${API_BASE}/devices/discover`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    low_limit: parseInt(lowLimit),
                    high_limit: parseInt(highLimit),
                    timeout: parseInt(discoverTimeout)
                })
            });
            const data = await response.json();
            alert(`Discovery complete: ${data.devices_found} devices found`);
            await loadDevices();
            await loadStatus();
        } catch (error) {
            alert('Discovery failed: ' + error.message);
        } finally {
            setLoading(false);
        }
    };

    const toggleNode = (nodeId) => {
        setExpandedNodes(prev => {
            const newSet = new Set(prev);
            if (newSet.has(nodeId)) {
                newSet.delete(nodeId);
            } else {
                newSet.add(nodeId);
            }
            return newSet;
        });
    };

    const selectItem = (item) => {
        setSelectedItem(item);
    };

    const loadDeviceDetails = async (deviceId) => {
        try {
            const response = await fetch(`${API_BASE}/devices/${deviceId}`);
            const data = await response.json();
            return data;
        } catch (error) {
            console.error('Error loading device details:', error);
            return null;
        }
    };

    return (
        <div>
            <Header status={status} />
            <div className="container">
                <Sidebar 
                    devices={devices}
                    selectedItem={selectedItem}
                    expandedNodes={expandedNodes}
                    onToggle={toggleNode}
                    onSelect={selectItem}
                    onLoadDetails={loadDeviceDetails}
                    loading={loading}
                    onDiscover={handleDiscover}
                    lowLimit={lowLimit}
                    setLowLimit={setLowLimit}
                    highLimit={highLimit}
                    setHighLimit={setHighLimit}
                    discoverTimeout={discoverTimeout}
                    setDiscoverTimeout={setDiscoverTimeout}
                />
                <DetailPanel selectedItem={selectedItem} />
            </div>
        </div>
    );
}

function Header({ status }) {
    return (
        <div className="header">
            <div className="header-title">BACnet-MQTT Gateway</div>
            <div className="status-bar">
                <div className="status-item">
                    <span className="status-dot"></span>
                    <span>Gateway Online</span>
                </div>
                <div className="status-item">
                    <span>📡 {status.devices} Devices</span>
                </div>
                {status.bbmd && (
                    <div className="status-item">
                        <span>🌐 BBMD Connected</span>
                    </div>
                )}
            </div>
        </div>
    );
}

function Sidebar({ devices, selectedItem, expandedNodes, onToggle, onSelect, onLoadDetails, 
                   loading, onDiscover, lowLimit, setLowLimit, highLimit, setHighLimit, 
                   discoverTimeout, setDiscoverTimeout }) {
    return (
        <div className="sidebar">
            <div className="sidebar-header">
                <div className="sidebar-title">Devices</div>
                <button className="btn btn-primary btn-small" onClick={onDiscover} disabled={loading}>
                    {loading ? '⏳' : '🔍'} Discover
                </button>
            </div>
            
            <div className="control-panel" style={{margin: '1rem', marginTop: 0}}>
                <div className="control-group">
                    <div className="form-group">
                        <label className="form-label">Low Limit</label>
                        <input 
                            type="number" 
                            className="form-input" 
                            value={lowLimit}
                            onChange={(e) => setLowLimit(e.target.value)}
                        />
                    </div>
                    <div className="form-group">
                        <label className="form-label">High Limit</label>
                        <input 
                            type="number" 
                            className="form-input" 
                            value={highLimit}
                            onChange={(e) => setHighLimit(e.target.value)}
                        />
                    </div>
                    <div className="form-group" style={{maxWidth: '100px'}}>
                        <label className="form-label">Timeout (s)</label>
                        <input 
                            type="number" 
                            className="form-input" 
                            value={discoverTimeout}
                            onChange={(e) => setDiscoverTimeout(e.target.value)}
                        />
                    </div>
                </div>
            </div>

            <DeviceTree 
                devices={devices}
                selectedItem={selectedItem}
                expandedNodes={expandedNodes}
                onToggle={onToggle}
                onSelect={onSelect}
                onLoadDetails={onLoadDetails}
            />
        </div>
    );
}

function DeviceTree({ devices, selectedItem, expandedNodes, onToggle, onSelect, onLoadDetails }) {
    if (devices.length === 0) {
        return (
            <div className="empty-state">
                <div className="empty-icon">📡</div>
                <div>No devices discovered yet</div>
                <div style={{fontSize: '0.85rem', marginTop: '0.5rem'}}>
                    Click "Discover" to find BACnet devices
                </div>
            </div>
        );
    }

    const sortedDevices = [...devices].sort((a, b) => a.device_id - b.device_id);
    const devicesByNetwork = {};
    
    sortedDevices.forEach(device => {
        const networkNum = device.network_number !== null && device.network_number !== undefined 
            ? String(device.network_number) 
            : 'unknown';
        
        if (!devicesByNetwork[networkNum]) {
            devicesByNetwork[networkNum] = [];
        }
        devicesByNetwork[networkNum].push(device);
    });

    const sortedNetworks = Object.keys(devicesByNetwork).sort((a, b) => {
        if (a === 'unknown') return 1;
        if (b === 'unknown') return -1;
        return parseInt(a) - parseInt(b);
    });

    if (sortedNetworks.length === 1) {
        return (
            <div className="tree">
                {sortedDevices.map(device => (
                    <DeviceNode
                        key={device.device_id}
                        device={device}
                        selectedItem={selectedItem}
                        expandedNodes={expandedNodes}
                        onToggle={onToggle}
                        onSelect={onSelect}
                        onLoadDetails={onLoadDetails}
                    />
                ))}
            </div>
        );
    }

    return (
        <div className="tree">
            {sortedNetworks.map(networkNum => (
                <NetworkNode
                    key={networkNum}
                    networkNum={networkNum}
                    devices={devicesByNetwork[networkNum]}
                    selectedItem={selectedItem}
                    expandedNodes={expandedNodes}
                    onToggle={onToggle}
                    onSelect={onSelect}
                    onLoadDetails={onLoadDetails}
                />
            ))}
        </div>
    );
}

function NetworkNode({ networkNum, devices, selectedItem, expandedNodes, onToggle, onSelect, onLoadDetails }) {
    const nodeId = `network-${networkNum}`;
    const isExpanded = expandedNodes.has(nodeId);
    const displayName = networkNum === 'unknown' ? 'Unknown Network' : `Network ${networkNum}`;

    return (
        <div className="tree-node">
            <div className="tree-item" onClick={() => onToggle(nodeId)}>
                <span className="tree-icon">{isExpanded ? '▼' : '▶'}</span>
                <span>🌐 {displayName}</span>
                <span className="badge badge-info" style={{marginLeft: 'auto', fontSize: '0.75rem'}}>
                    {devices.length}
                </span>
            </div>
            {isExpanded && (
                <div className="tree-children">
                    {devices.map(device => (
                        <DeviceNode
                            key={device.device_id}
                            device={device}
                            selectedItem={selectedItem}
                            expandedNodes={expandedNodes}
                            onToggle={onToggle}
                            onSelect={onSelect}
                            onLoadDetails={onLoadDetails}
                        />
                    ))}
                </div>
            )}
        </div>
    );
}

function DeviceNode({ device, selectedItem, expandedNodes, onToggle, onSelect, onLoadDetails }) {
    const nodeId = `device-${device.device_id}`;
    const isExpanded = expandedNodes.has(nodeId);
    const isSelected = selectedItem?.type === 'device' && selectedItem?.id === device.device_id;
    const [deviceDetails, setDeviceDetails] = useState(null);

    useEffect(() => {
        if (isExpanded && !deviceDetails) {
            onLoadDetails(device.device_id).then(setDeviceDetails);
        }
    }, [isExpanded, device.device_id, deviceDetails, onLoadDetails]);

    const handleClick = () => {
        onToggle(nodeId);
        onSelect({ type: 'device', id: device.device_id, data: device });
    };

    const displayName = device.device_name || `Device ${device.device_id}`;

    return (
        <div className="tree-node">
            <div className={`tree-item ${isSelected ? 'selected' : ''}`} onClick={handleClick}>
                <span className="tree-icon">{isExpanded ? '▼' : '▶'}</span>
                <span>🖥️ {displayName}</span>
                {device.object_count > 0 && (
                    <span className="badge badge-success" style={{marginLeft: 'auto', fontSize: '0.75rem'}}>
                        {device.object_count}
                    </span>
                )}
            </div>
            {isExpanded && deviceDetails && (
                <div className="tree-children">
                    {Object.keys(deviceDetails.objects || {}).length > 0 ? (
                        <ObjectTypeGroups 
                            objects={Object.values(deviceDetails.objects || {})}
                            deviceId={device.device_id}
                            selectedItem={selectedItem}
                            expandedNodes={expandedNodes}
                            onToggle={onToggle}
                            onSelect={onSelect}
                        />
                    ) : (
                        <div style={{padding: '0.5rem 1rem', color: '#9ca3af', fontSize: '0.85rem'}}>
                            No objects discovered yet
                        </div>
                    )}
                </div>
            )}
        </div>
    );
}

function ObjectTypeGroups({ objects, deviceId, selectedItem, expandedNodes, onToggle, onSelect }) {
    const objectsByType = {};
    objects.forEach(obj => {
        const type = obj.object_type;
        if (!objectsByType[type]) {
            objectsByType[type] = [];
        }
        objectsByType[type].push(obj);
    });

    const sortedTypes = Object.keys(objectsByType).sort();

    return (
        <>
            {sortedTypes.map(type => (
                <ObjectTypeNode
                    key={`${deviceId}-${type}`}
                    objectType={type}
                    objects={objectsByType[type]}
                    deviceId={deviceId}
                    selectedItem={selectedItem}
                    expandedNodes={expandedNodes}
                    onToggle={onToggle}
                    onSelect={onSelect}
                />
            ))}
        </>
    );
}

function ObjectTypeNode({ objectType, objects, deviceId, selectedItem, expandedNodes, onToggle, onSelect }) {
    const nodeId = `type-${deviceId}-${objectType}`;
    const isExpanded = expandedNodes.has(nodeId);
    const displayName = objectType.split('-').map(word => 
        word.charAt(0).toUpperCase() + word.slice(1)
    ).join(' ');
    const sortedObjects = [...objects].sort((a, b) => a.object_instance - b.object_instance);

    return (
        <div className="tree-node">
            <div className="tree-item" onClick={() => onToggle(nodeId)}>
                <span className="tree-icon">{isExpanded ? '▼' : '▶'}</span>
                <span>📁 {displayName}</span>
                <span className="badge badge-info" style={{marginLeft: 'auto', fontSize: '0.75rem'}}>
                    {objects.length}
                </span>
            </div>
            {isExpanded && (
                <div className="tree-children">
                    {sortedObjects.map(obj => (
                        <ObjectNode
                            key={`${obj.object_type}-${obj.object_instance}`}
                            object={obj}
                            deviceId={deviceId}
                            selectedItem={selectedItem}
                            onSelect={onSelect}
                        />
                    ))}
                </div>
            )}
        </div>
    );
}

function ObjectNode({ object, deviceId, selectedItem, onSelect }) {
    const isSelected = selectedItem?.type === 'object' && 
                      selectedItem?.deviceId === deviceId &&
                      selectedItem?.data.object_instance === object.object_instance;

    const icon = object.object_type.includes('input') ? '📥' :
                object.object_type.includes('output') ? '📤' :
                object.object_type.includes('value') ? '💾' : '📊';

    const displayName = object.object_name || `${object.object_instance}`;

    return (
        <div 
            className={`tree-item ${isSelected ? 'selected' : ''}`}
            onClick={() => onSelect({ 
                type: 'object', 
                deviceId, 
                id: `${object.object_type}-${object.object_instance}`,
                data: object 
            })}
            style={{paddingLeft: '1rem'}}
        >
            <span className="tree-icon">{icon}</span>
            <span>{displayName}</span>
        </div>
    );
}

function DetailPanel({ selectedItem }) {
    if (!selectedItem) {
        return (
            <div className="main-content">
                <div className="empty-state">
                    <div className="empty-icon">👈</div>
                    <div>Select a device or object to view details</div>
                </div>
            </div>
        );
    }

    if (selectedItem.type === 'device') {
        return <DeviceDetails device={selectedItem.data} />;
    } else if (selectedItem.type === 'object') {
        return <ObjectDetails object={selectedItem.data} deviceId={selectedItem.deviceId} />;
    }

    return null;
}

function DeviceDetails({ device }) {
    return (
        <div className="main-content">
            <div className="content-header">
                <div className="content-title">
                    {device.device_name || `Device ${device.device_id}`}
                </div>
                <div className="content-subtitle">
                    Device ID: {device.device_id} • {device.address}
                </div>
            </div>
            <div className="content-body">
                <div className="info-grid">
                    <div className="info-card">
                        <div className="info-label">Status</div>
                        <div className="info-value">
                            <span className={`badge ${device.enabled ? 'badge-success' : 'badge-warning'}`}>
                                {device.enabled ? 'Enabled' : 'Disabled'}
                            </span>
                        </div>
                    </div>
                    <div className="info-card">
                        <div className="info-label">Vendor</div>
                        <div className="info-value">{device.vendor_name || 'Unknown'}</div>
                    </div>
                    <div className="info-card">
                        <div className="info-label">Objects</div>
                        <div className="info-value">{device.object_count || 0}</div>
                    </div>
                    <div className="info-card">
                        <div className="info-label">Last Seen</div>
                        <div className="info-value timestamp">
                            {new Date(device.last_seen).toLocaleString()}
                        </div>
                    </div>
                </div>

                <div style={{marginTop: '1.5rem'}}>
                    <h3 style={{marginBottom: '1rem'}}>Device Information</h3>
                    <table className="properties-table">
                        <tbody>
                            <tr>
                                <td><strong>Device ID</strong></td>
                                <td>{device.device_id}</td>
                            </tr>
                            <tr>
                                <td><strong>Address</strong></td>
                                <td>{device.address}</td>
                            </tr>
                            <tr>
                                <td><strong>Vendor Name</strong></td>
                                <td>{device.vendor_name || 'N/A'}</td>
                            </tr>
                            <tr>
                                <td><strong>Model Name</strong></td>
                                <td>{device.model_name || 'N/A'}</td>
                            </tr>
                            <tr>
                                <td><strong>Firmware</strong></td>
                                <td>{device.firmware_revision || 'N/A'}</td>
                            </tr>
                        </tbody>
                    </table>
                </div>
            </div>
        </div>
    );
}

function ObjectDetails({ object, deviceId }) {
    const [customTopic, setCustomTopic] = useState('');
    const [isMappingMode, setIsMappingMode] = useState(false);
    const [savedMapping, setSavedMapping] = useState(null);
    const [isReading, setIsReading] = useState(false);
    const [currentValue, setCurrentValue] = useState(null);
    const [lastUpdate, setLastUpdate] = useState(null);

    const objectKey = `${deviceId}-${object.object_type}-${object.object_instance}`;

    useEffect(() => {
        setIsMappingMode(false);
        setCustomTopic('');
        setSavedMapping(null);
        // Set initial value from object if it exists
        const presentValue = object.properties?.['present-value'];
        if (presentValue) {
            setCurrentValue(presentValue.value);
            setLastUpdate(presentValue.timestamp);
        } else {
            setCurrentValue(null);
            setLastUpdate(null);
        }
        loadMapping();
    }, [objectKey]);

    const loadMapping = async () => {
        try {
            const response = await fetch(`${window.location.origin}/mqtt/mapping/${deviceId}/${object.object_type}/${object.object_instance}`);
            if (response.ok) {
                const data = await response.json();
                setSavedMapping(data);
                setCustomTopic(data.custom_topic || '');
            }
        } catch (error) {
            setSavedMapping(null);
            setCustomTopic('');
        }
    };

    const handleReadValue = async () => {
        setIsReading(true);
        try {
            const response = await fetch(`${window.location.origin}/read`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    device_id: deviceId,
                    object_type: object.object_type,
                    object_instance: object.object_instance,
                    property_id: 'present-value'
                })
            });
            
            if (response.ok) {
                const data = await response.json();
                setCurrentValue(data.value);
                setLastUpdate(new Date().toISOString());
            } else {
                const error = await response.json();
                alert('Failed to read value: ' + (error.detail || 'Unknown error'));
            }
        } catch (error) {
            alert('Error reading value: ' + error.message);
        } finally {
            setIsReading(false);
        }
    };

    const handleSaveMapping = async () => {
        const defaultTopic = `bacnet/${deviceId}/${object.object_type.replace(/-/g, '_')}/${object.object_instance}/present-value`;
        const topic = customTopic || defaultTopic;
        try {
            const response = await fetch(`${window.location.origin}/mqtt/mapping`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    device_id: deviceId,
                    object_type: object.object_type,
                    object_instance: object.object_instance,
                    mqtt_topic: topic,
                    custom_topic: customTopic || null,
                    enabled: true
                })
            });
            
            if (response.ok) {
                const data = await response.json();
                setSavedMapping(data);
                setIsMappingMode(false);
                alert('MQTT mapping saved successfully!');
            } else {
                const error = await response.text();
                alert('Failed to save mapping: ' + error);
            }
        } catch (error) {
            alert('Error saving mapping: ' + error.message);
        }
    };

    const handleDeleteMapping = async () => {
        if (!confirm('Delete this MQTT mapping?')) return;
        
        try {
            const response = await fetch(`${window.location.origin}/mqtt/mapping/${deviceId}/${object.object_type}/${object.object_instance}`, {
                method: 'DELETE'
            });
            
            if (response.ok) {
                setSavedMapping(null);
                setCustomTopic('');
                alert('MQTT mapping deleted');
            }
        } catch (error) {
            alert('Error deleting mapping: ' + error.message);
        }
    };

    const defaultTopic = `bacnet/${deviceId}/${object.object_type.replace(/-/g, '_')}/${object.object_instance}/present-value`;
    const presentValue = object.properties?.['present-value'];
    const displayValue = currentValue !== null ? currentValue : presentValue?.value;
    const displayTimestamp = lastUpdate || presentValue?.timestamp;
    const hasValue = displayValue !== undefined;

    return (
        <div className="main-content">
            <div className="content-header">
                <div className="content-title">
                    {object.object_name || `${object.object_type} ${object.object_instance}`}
                </div>
                <div className="content-subtitle">
                    {object.object_type} • Instance {object.object_instance}
                </div>
            </div>
            <div className="content-body">
                <div style={{marginBottom: '1rem'}}>
                    <button 
                        className="btn btn-primary" 
                        onClick={handleReadValue}
                        disabled={isReading}
                    >
                        {isReading ? '⏳ Reading...' : '🔄 Read Current Value'}
                    </button>
                </div>

                {hasValue && (
                    <div style={{
                        background: 'linear-gradient(135deg, #10b98115 0%, #05966915 100%)',
                        padding: '2rem',
                        borderRadius: '12px',
                        border: '2px solid #10b981',
                        marginBottom: '1.5rem',
                        textAlign: 'center'
                    }}>
                        <div style={{fontSize: '0.9rem', color: '#065f46', marginBottom: '0.5rem', fontWeight: 600}}>
                            CURRENT VALUE
                        </div>
                        <div style={{fontSize: '3rem', fontWeight: 700, color: '#047857', marginBottom: '0.5rem'}}>
                            {String(displayValue)}
                            {presentValue?.unit && (
                                <span style={{fontSize: '1.5rem', marginLeft: '0.5rem', color: '#059669'}}>
                                    {presentValue.unit}
                                </span>
                            )}
                        </div>
                        {displayTimestamp && (
                            <div className="timestamp">
                                Last updated: {new Date(displayTimestamp).toLocaleString()}
                            </div>
                        )}
                    </div>
                )}

                <div className="info-grid">
                    <div className="info-card">
                        <div className="info-label">Object Type</div>
                        <div className="info-value">{object.object_type}</div>
                    </div>
                    <div className="info-card">
                        <div className="info-label">Instance</div>
                        <div className="info-value">{object.object_instance}</div>
                    </div>
                    <div className="info-card">
                        <div className="info-label">Properties</div>
                        <div className="info-value">{Object.keys(object.properties || {}).length}</div>
                    </div>
                    {object.last_poll && (
                        <div className="info-card">
                            <div className="info-label">Last Poll</div>
                            <div className="info-value timestamp">
                                {new Date(object.last_poll).toLocaleString()}
                            </div>
                        </div>
                    )}
                </div>

                <div className="mqtt-mapping-section">
                    <div className="mqtt-mapping-header">
                        <h3 style={{margin: 0, display: 'flex', alignItems: 'center', gap: '0.5rem'}}>
                            📡 MQTT Topic Mapping
                        </h3>
                        {savedMapping && !isMappingMode && (
                            <span className="badge badge-success">Active</span>
                        )}
                    </div>

                    {!isMappingMode && !savedMapping && (
                        <div>
                            <p style={{color: '#6b7280', marginBottom: '1rem', fontSize: '0.9rem'}}>
                                Map this BACnet point to an MQTT topic for real-time publishing
                            </p>
                            <button className="btn btn-primary" onClick={() => setIsMappingMode(true)}>
                                ➕ Create MQTT Mapping
                            </button>
                        </div>
                    )}

                    {savedMapping && !isMappingMode && (
                        <div>
                            <div className="info-card" style={{marginBottom: '1rem'}}>
                                <div className="info-label">Current MQTT Topic</div>
                                <div className="info-value" style={{
                                    fontFamily: 'monospace',
                                    fontSize: '0.9rem',
                                    color: '#667eea',
                                    wordBreak: 'break-all'
                                }}>
                                    {savedMapping.mqtt_topic}
                                </div>
                            </div>
                            <div style={{display: 'flex', gap: '0.5rem'}}>
                                <button className="btn btn-primary btn-small" onClick={() => setIsMappingMode(true)}>
                                    ✏️ Edit
                                </button>
                                <button className="btn btn-danger btn-small" onClick={handleDeleteMapping}>
                                    🗑️ Delete
                                </button>
                            </div>
                        </div>
                    )}

                    {isMappingMode && (
                        <div>
                            <div className="form-group" style={{marginBottom: '1rem'}}>
                                <label className="form-label">Default Topic (auto-generated)</label>
                                <input 
                                    type="text" 
                                    className="form-input" 
                                    value={defaultTopic}
                                    disabled
                                    style={{fontFamily: 'monospace', fontSize: '0.85rem'}}
                                />
                            </div>

                            <div className="form-group" style={{marginBottom: '1rem'}}>
                                <label className="form-label">Custom Topic (optional)</label>
                                <input 
                                    type="text" 
                                    className="form-input" 
                                    value={customTopic}
                                    onChange={(e) => setCustomTopic(e.target.value)}
                                    placeholder="e.g., building/floor1/temp/sensor1"
                                    style={{fontFamily: 'monospace', fontSize: '0.85rem'}}
                                />
                                <div style={{fontSize: '0.8rem', color: '#6b7280', marginTop: '0.25rem'}}>
                                    Leave empty to use the default topic
                                </div>
                            </div>

                            <div className="mqtt-topic-preview">
                                <strong>Preview:</strong><br/>
                                <code>{customTopic || defaultTopic}</code>
                            </div>

                            <div style={{display: 'flex', gap: '0.5rem'}}>
                                <button className="btn btn-success" onClick={handleSaveMapping}>
                                    💾 Save Mapping
                                </button>
                                <button 
                                    className="btn" 
                                    onClick={() => {
                                        setIsMappingMode(false);
                                        setCustomTopic(savedMapping?.custom_topic || '');
                                    }}
                                    style={{background: '#e5e7eb', color: '#1f2937'}}
                                >
                                    Cancel
                                </button>
                            </div>
                        </div>
                    )}
                </div>

                {Object.keys(object.properties || {}).length > 0 && (
                    <div style={{marginTop: '1.5rem'}}>
                        <h3 style={{marginBottom: '1rem'}}>All Properties</h3>
                        <table className="properties-table">
                            <thead>
                                <tr>
                                    <th>Property</th>
                                    <th>Value</th>
                                    <th>Unit</th>
                                    <th>Timestamp</th>
                                </tr>
                            </thead>
                            <tbody>
                                {Object.entries(object.properties).map(([key, prop]) => (
                                    <tr key={key}>
                                        <td><strong>{prop.property_id}</strong></td>
                                        <td>{String(prop.value)}</td>
                                        <td>{prop.unit || '-'}</td>
                                        <td className="timestamp">
                                            {new Date(prop.timestamp).toLocaleString()}
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                )}
            </div>
        </div>
    );
}
ReactDOM.render(<App />, document.getElementById('root'));
