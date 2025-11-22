# BBMD Configuration Guide

## What is BBMD?

BBMD (BACnet Broadcast Management Device) allows BACnet devices on different IP subnets to communicate. When your gateway is on a different subnet than your BACnet devices, you need to register as a "Foreign Device" with a BBMD.

## Configuration

Edit `config.yaml`:

```yaml
bacnet:
  device_id: 999999
  device_name: "BACnet-MQTT Gateway"
  ip_address: "0.0.0.0"
  netmask: "255.255.255.0"
  port: 47808
  
  # BBMD Configuration
  bbmd:
    enabled: true              # Enable BBMD registration
    address: "10.0.1.1"        # IP address of your BBMD server
    port: 47808                # BBMD port (usually 47808)
    ttl: 30                    # Registration timeout in seconds
```

## Setup Steps

### 1. Find Your BBMD Address

Your BACnet network should have a BBMD server. Common locations:
- A BACnet controller that acts as BBMD
- A dedicated BACnet/IP router
- Building automation server

To find it:
- Check your BACnet network documentation
- Ask your building automation team
- Look for a device that bridges subnets

### 2. Configure the Gateway

Set these values in `config.yaml`:
- `bbmd.enabled`: Set to `true`
- `bbmd.address`: IP address of your BBMD (e.g., "10.0.1.100")
- `bbmd.port`: Usually 47808
- `bbmd.ttl`: Registration timeout (30 seconds is typical)

### 3. Restart the Gateway

```bash
python main.py
```

You should see log messages:
```
INFO - Registering as Foreign Device with BBMD at 10.0.1.100:47808
INFO - Successfully registered with BBMD (TTL: 30s)
INFO - Starting periodic BBMD registration (every 15s)
```

### 4. Verify Registration

Check the status API:
```bash
curl http://localhost:8080/status
```

Look for the `bbmd` section:
```json
{
  "bbmd": {
    "enabled": true,
    "address": "10.0.1.100",
    "port": 47808,
    "ttl": 30
  }
}
```

### 5. Test Discovery

Use the API to discover devices:
```bash
curl -X POST http://localhost:8080/devices/discover \
  -H "Content-Type: application/json" \
  -d '{"low_limit": 240001, "high_limit": 250000, "timeout": 10}'
```

## Troubleshooting

### No Devices Found After BBMD Registration

1. **Verify BBMD is reachable:**
   ```bash
   ping 10.0.1.100
   ```

2. **Check firewall rules:**
   - UDP port 47808 must be open
   - Both inbound and outbound

3. **Verify device ID range:**
   - Your devices are 240001+
   - Use those values for low_limit and high_limit

4. **Check BBMD logs:**
   - Look for registration messages
   - Verify Foreign Device table shows your gateway

### Registration Fails

**Error: "timeout" or "no response"**
- BBMD may be offline or unreachable
- Check IP address and port
- Verify network routing

**Error: "refused" or "rejected"**
- BBMD may not allow Foreign Device registration
- Check BBMD configuration
- May need to whitelist your gateway's IP

### Devices Intermittently Disappear

**TTL too short:**
- Increase `ttl` value (try 60 or 90)
- Check network stability

**BBMD restarted:**
- Gateway will auto-re-register
- Check logs for re-registration messages

## Advanced Configuration

### Permanent Registration

Set `ttl: 0` for permanent registration (until explicitly unregistered):
```yaml
bbmd:
  enabled: true
  address: "10.0.1.1"
  ttl: 0  # Permanent
```

### Multiple BBMDs

If you have multiple BBMDs or subnets, you may need to:
1. Register with primary BBMD
2. Use BDT (Broadcast Distribution Table) on BBMD
3. Configure routing on your network

This typically requires BBMD administrator access.

## API Endpoints

### Check Status
```
GET /status
```

Returns BBMD configuration and registration status.

### Manual Re-registration
```
POST /bbmd/register
```

Manually trigger BBMD registration (useful for troubleshooting).

## Network Diagram Example

```
[Your Gateway]          [BBMD]              [BACnet Devices]
10.0.10.5/24    <--->   10.0.1.100   <--->  10.0.1.50-100
   Subnet A             (Router)            Subnet A
                           |
                           |
                        Router
                           |
                    [More Devices]
                     10.0.2.x/24
                      Subnet B
```

Your gateway registers with the BBMD as a Foreign Device, allowing it to:
- Send WHO-IS broadcasts across subnets
- Receive I-AM responses from remote devices
- Read/write properties on devices in other subnets

## Notes

- Registration renews automatically (every TTL/2 seconds)
- Gateway unregisters on shutdown (sends TTL=0)
- Monitor logs for registration issues
- BBMD address must be on a routable network from your gateway
