# Component Debug Logging

This feature allows enabling debug logging for specific external libraries to help with troubleshooting.

> **Note**: This feature is subject to change while we refine it based on user feedback and testing.

## Configuration

Add to your `config.yaml`:

```yaml
logging:
  level: info
  debug:
    matrix_nio: true # Enable matrix-nio debug logging
    bleak: true # Enable BLE debug logging
    meshtastic: true # Enable meshtastic library debug logging
```

## What it does

When enabled, this will set the following loggers to DEBUG level:

### matrix_nio: true

- `nio` - Main matrix-nio logger
- `nio.client` - Matrix client operations
- `nio.http` - HTTP requests/responses
- `nio.crypto` - Encryption/decryption operations

### bleak: true

- `bleak` - Main BLE library logger
- `bleak.backends` - Platform-specific BLE backends

### meshtastic: true

- `meshtastic` - Main meshtastic library logger
- `meshtastic.serial_interface` - Serial connection debugging
- `meshtastic.tcp_interface` - TCP connection debugging
- `meshtastic.ble_interface` - BLE connection debugging

## Use cases

- **Matrix connection issues**: Enable `matrix_nio: true` to see detailed Matrix client operations
- **BLE connection problems**: Enable `bleak: true` to debug Bluetooth connectivity
- **Meshtastic device communication**: Enable `meshtastic: true` to see device protocol details
- **Troubleshooting specific components**: Enable only the component you're debugging to avoid log noise

## Example output

With `matrix_nio: true`, you'll see detailed logs like:

```log
DEBUG:nio.http:Sending POST request to https://matrix.org/_matrix/client/r0/sync
DEBUG:nio.client:Received sync response with 5 rooms
```

With `bleak: true`, you'll see BLE operations:

```log
DEBUG:bleak:Scanning for BLE devices...
DEBUG:bleak.backends:Found device: AA:BB:CC:DD:EE:FF
```

With `meshtastic: true`, you'll see device communication:

```log
DEBUG:meshtastic:Sending packet to device
DEBUG:meshtastic.ble_interface:BLE characteristic write completed
```
