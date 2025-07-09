# Fix MMRelay Hanging During Shutdown

## Problem

MMRelay was hanging during shutdown when users pressed Ctrl+C, getting stuck at "Closing Meshtastic client..." and requiring multiple interrupt signals to force termination. This affected both interactive use and systemd service management.

## Root Cause Analysis

The issue had two components that required fixes at different layers:

### 1. Application Layer (MMRelay)
Active pub/sub event subscriptions that were never cleaned up during shutdown:
- MMRelay subscribes to 3 Meshtastic events during connection
- During shutdown, `meshtastic_client.close()` was called but event subscriptions remained active
- Active callbacks and event handlers could interfere with clean shutdown

### 2. Library Layer (meshtastic-python BLE interface)
Event loop shutdown issues in the BLE interface:
- Event loop task cancellation could hang when trying to run tasks on stopped loops
- Thread join timeouts were too long, causing delays
- Insufficient error handling for event loop state transitions

## Solution

### MMRelay Changes (Application Layer)
Added proper cleanup of pub/sub subscriptions during shutdown:

**New `cleanup_subscriptions()` Function:**
- Safely unsubscribes from all Meshtastic events with error handling
- Resets subscription flags to prevent duplicate subscriptions on reconnect
- Includes debug logging for troubleshooting

**Updated Shutdown Sequence:**
- Added cleanup call in signal handler shutdown function
- Added cleanup call in finally block as safety net
- Ensures subscriptions are cleaned up before closing Meshtastic client

### meshtastic-python Changes (Library Layer)
Improved BLE event loop shutdown to prevent hanging:

**Enhanced Event Loop Management:**
- Added event loop state checks before attempting task cancellation
- Reduced timeout for task cancellation from 3s to 2s
- Added safety checks to prevent running tasks on stopped event loops

**Aggressive Shutdown Handling:**
- Improved error handling for event loop stop operations
- Reduced thread join timeout from 5s to 3s for faster shutdown
- Added force task cancellation when thread doesn't shut down cleanly

## Implementation

**MMRelay (src/mmrelay/meshtastic_utils.py):**
```python
def cleanup_subscriptions():
    """Unsubscribes from all Meshtastic pub/sub events to ensure clean shutdown."""
    # Safely unsubscribe from all events with error handling
    # Reset subscription flags to prevent duplicates
```

**MMRelay (src/mmrelay/main.py):**
```python
async def shutdown():
    meshtastic_utils.shutting_down = True
    # Clean up pub/sub subscriptions to prevent hanging during shutdown
    meshtastic_utils.cleanup_subscriptions()
    shutdown_event.set()
```

**meshtastic-python (meshtastic/ble_interface.py):**
```python
# Enhanced event loop shutdown with state checks
if not self._eventLoop.is_closed() and not self._eventLoop.is_running():
    # Only try to cancel tasks if the event loop can handle it
    # Reduced timeouts and improved error handling
```

## Dependencies

This fix requires the updated meshtastic-python library:
- **Repository**: https://github.com/jeremiah-k/meshtastic-python
- **Branch**: fix-ble-shutdown-stability-v4
- **Commit**: 34c3879 - "Improve BLE event loop shutdown to prevent hanging"

The requirements.txt has been updated to use this specific commit.

## Impact

- **Fixed Hanging**: MMRelay now shuts down cleanly on Ctrl+C without hanging
- **Improved Service Management**: systemd services can stop/restart reliably
- **Better User Experience**: No more multiple interrupt signals required
- **Enhanced Reliability**: Proper resource cleanup prevents memory leaks
- **Maintained Functionality**: All existing features work unchanged

## Testing

This fix addresses the specific hanging scenario where:
- User presses Ctrl+C during normal operation
- MMRelay logs "Closing Meshtastic client..." and then hangs
- Multiple Ctrl+C presses were required to force termination

After this fix, shutdown should be immediate and clean.

## Compatibility

- No breaking changes to existing functionality
- Maintains all current connection and reconnection behavior
- Compatible with all connection types (BLE, TCP, Serial)
- No configuration changes required
- Works with both interactive and service deployments
