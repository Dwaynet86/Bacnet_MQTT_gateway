#!/usr/bin/env python3
"""
BACnet Network Diagnostic Tool
Run this to troubleshoot BACnet discovery issues
"""
import asyncio
import sys
from bacpypes3.pdu import Address, GlobalBroadcast
from bacpypes3.primitivedata import Unsigned
from bacpypes3.apdu import IAmRequest, WhoIsRequest
from bacpypes3.local.device import DeviceObject
from bacpypes3.ipv4.app import NormalApplication
import logging

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)


async def test_bacnet_discovery(device_range_low=None, device_range_high=None):
    """Test BACnet WHO-IS/I-AM discovery"""
    
    print("\n" + "="*70)
    print("BACnet Network Diagnostic Tool")
    print("="*70 + "\n")
    
    # Step 1: Detect network interface
    print("Step 1: Detecting network interface...")
    import socket
    import netifaces
    
    try:
        gws = netifaces.gateways()
        default_interface = gws['default'][netifaces.AF_INET][1]
        addrs = netifaces.ifaddresses(default_interface)
        ip_info = addrs[netifaces.AF_INET][0]
        ip_address = ip_info['addr']
        netmask = ip_info.get('netmask', '255.255.255.0')
        print(f"✓ Interface: {default_interface}")
        print(f"✓ IP Address: {ip_address}")
        print(f"✓ Netmask: {netmask}")
    except Exception as e:
        print(f"✗ Error detecting network: {e}")
        return
    
    # Step 2: Initialize BACnet application
    print("\nStep 2: Initializing BACnet application...")
    try:
        device = DeviceObject(
            objectIdentifier=('device', 999998),
            objectName="Diagnostic Tool",
            maxApduLengthAccepted=1476,
            segmentationSupported='segmentedBoth',
            vendorIdentifier=15
        )
        
        address = Address(f"{ip_address}/{netmask}:47808")
        print(f"✓ Local Address: {address}")
        
        app = NormalApplication(device, address)
        print("✓ BACnet application initialized")
        
        # Check broadcast address
        if hasattr(app, 'nse') and hasattr(app.nse, 'broadcastAddress'):
            print(f"✓ Broadcast Address: {app.nse.broadcastAddress}")
        
    except Exception as e:
        print(f"✗ Error initializing BACnet: {e}")
        import traceback
        traceback.print_exc()
        return
    
    # Step 3: Send WHO-IS and listen for I-AM
    print("\nStep 3: Sending WHO-IS broadcast...")
    received_iams = []
    
    async def capture_iam(apdu):
        if isinstance(apdu, IAmRequest):
            device_id = apdu.iAmDeviceIdentifier[1]
            source = str(apdu.pduSource)
            received_iams.append((device_id, source))
            print(f"✓ I-AM received: Device {device_id} at {source}")
    
    original_do_IAmRequest = getattr(app, 'do_IAmRequest', None)
    
    async def custom_do_IAmRequest(apdu):
        await capture_iam(apdu)
        if original_do_IAmRequest and callable(original_do_IAmRequest):
            try:
                result = original_do_IAmRequest(apdu)
                if asyncio.iscoroutine(result):
                    await result
            except:
                pass
    
    app.do_IAmRequest = custom_do_IAmRequest
    
    try:
        # Create WHO-IS request
        who_is = WhoIsRequest()
        
        if device_range_low is not None:
            who_is.deviceInstanceRangeLowLimit = Unsigned(device_range_low)
            print(f"  Device range low: {device_range_low}")
        
        if device_range_high is not None:
            who_is.deviceInstanceRangeHighLimit = Unsigned(device_range_high)
            print(f"  Device range high: {device_range_high}")
        
        who_is.pduDestination = GlobalBroadcast()
        print(f"  Destination: GlobalBroadcast")
        
        # Send WHO-IS
        print("  Sending WHO-IS...")
        await app.request(who_is)
        print("  ✓ WHO-IS sent")
        
        # Wait for responses
        timeout = 10
        print(f"\nStep 4: Waiting {timeout} seconds for I-AM responses...")
        await asyncio.sleep(timeout)
        
    except Exception as e:
        print(f"✗ Error during discovery: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if original_do_IAmRequest:
            app.do_IAmRequest = original_do_IAmRequest
    
    # Step 5: Summary
    print("\n" + "="*70)
    print("RESULTS")
    print("="*70)
    print(f"Devices discovered: {len(received_iams)}")
    
    if received_iams:
        print("\nDiscovered devices:")
        for device_id, source in received_iams:
            print(f"  - Device {device_id} at {source}")
    else:
        print("\n⚠ No devices discovered. Troubleshooting tips:")
        print("  1. Verify BACnet devices are on the same subnet")
        print("  2. Check if firewall is blocking UDP port 47808")
        print("  3. Verify device IDs are in the expected range")
        print("  4. Try specifying device range with -l and -h options")
        print("  5. Check if devices require specific BACnet network number")
        print("  6. Use Wireshark to capture BACnet traffic on port 47808")
    
    print("\n" + "="*70 + "\n")


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="BACnet Network Diagnostic Tool")
    parser.add_argument("-l", "--low", type=int, help="Device instance range low limit")
    parser.add_argument("-H", "--high", type=int, help="Device instance range high limit")
    
    args = parser.parse_args()
    
    asyncio.run(test_bacnet_discovery(args.low, args.high))
