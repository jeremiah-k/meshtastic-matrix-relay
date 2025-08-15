# Extra Configuration Options

This document covers advanced configuration options for MMRelay that go beyond the basic setup covered in the main [Installation Guide](INSTRUCTIONS.md).

## Message Prefix Customization

MMRelay allows you to customize how sender names appear in relayed messages between Matrix and Meshtastic networks. This feature helps you control message formatting and save precious character space on Meshtastic devices.

### Default Behavior

By default, MMRelay adds prefixes to identify message sources:

- **Matrix → Meshtastic**: `Alice[M]: Hello world` (sender name + platform indicator)
- **Meshtastic → Matrix**: `[Alice/MyMesh]: Hello world` (sender name + mesh network)

### Customizing Prefixes

You can customize these prefixes by adding configuration options to your `config.yaml`:

```yaml
# Matrix → Meshtastic direction
meshtastic:
  prefix_enabled: true # Enable/disable prefixes (default: true)
  prefix_format: "{display5}[M]: " # Custom format (default shown)

# Meshtastic → Matrix direction
matrix:
  prefix_enabled: true # Enable/disable prefixes (default: true)
  prefix_format: "[{long}/{mesh}]: " # Custom format (default shown)
```

### Available Variables

**For Matrix → Meshtastic messages:**

- `{display}` - Display name (room-specific if set, otherwise global display name)
- `{display5}`, `{display10}`, etc. - Truncated display names (e.g., "Alice", "Alice Smit")
- `{user}` - Full Matrix user ID (e.g., "@alice:matrix.org")
- `{username}` - Username part only (e.g., "alice" from "@alice:matrix.org")
- `{server}` - Server part only (e.g., "matrix.org" from "@alice:matrix.org")

**For Meshtastic → Matrix messages:**

- `{long}` - Full long name from Meshtastic device
- `{long4}`, `{long8}`, etc. - Truncated long names
- `{short}` - Short name from Meshtastic device (usually 2-4 characters)
- `{mesh}` - Mesh network name
- `{mesh6}`, `{mesh10}`, etc. - Truncated mesh names

### Example Customizations

**Shorter prefixes to save message space:**

```yaml
meshtastic:
  prefix_format: "{display3}> " # "Ali> Hello world" (5 chars)

matrix:
  prefix_format: "({long4}): " # "(Alic): Hello world" (8 chars)
```

**Different styles:**

```yaml
meshtastic:
  prefix_format: "{display}→ " # "Alice Smith→ Hello world"

matrix:
  prefix_format: "[{mesh6}] {short}: " # "[MyMesh] Ali: Hello world"
```

**Disable prefixes entirely:**

```yaml
meshtastic:
  prefix_enabled: false # No prefixes on messages to mesh

matrix:
  prefix_enabled: false # No prefixes on messages to Matrix
```

### Character Efficiency Tips

- **Default formats use 10 characters** (`Alice[M]:`) leaving ~200 characters for message content
- **Use shorter truncations** like `{display3}` or `{long4}` to save space
- **Consider your mesh network's message limits** when choosing prefix lengths
- **Test your formats** with typical usernames in your community

### Error Handling

If you specify an invalid format (like `{invalid_variable}`), MMRelay will:

1. Log a warning message
2. Fall back to the default format
3. Continue operating normally

This ensures your relay keeps working even with configuration mistakes.

## Component Debug Logging

This feature allows enabling debug logging for specific external libraries to help with troubleshooting connection and communication issues.

> **Note**: This feature is subject to change while we refine it based on user feedback and testing.

### Configuration

Add to your `config.yaml`:

```yaml
logging:
  level: info
  debug:
    matrix_nio: true # Enable matrix-nio debug logging
    bleak: true # Enable BLE debug logging
    meshtastic: true # Enable meshtastic library debug logging
```

### What it does

When enabled, this will set the following loggers to DEBUG level:

#### matrix_nio: true

- `nio` - Main matrix-nio logger
- `nio.client` - Matrix client operations
- `nio.http` - HTTP requests/responses
- `nio.crypto` - Encryption/decryption operations

#### bleak: true

- `bleak` - Main BLE library logger
- `bleak.backends` - Platform-specific BLE backends

#### meshtastic: true

- `meshtastic` - Main meshtastic library logger
- `meshtastic.serial_interface` - Serial connection debugging
- `meshtastic.tcp_interface` - TCP connection debugging
- `meshtastic.ble_interface` - BLE connection debugging

### Use Cases

- **Matrix connection issues**: Enable `matrix_nio: true` to see detailed Matrix client operations
- **BLE connection problems**: Enable `bleak: true` to debug Bluetooth connectivity
- **Meshtastic device communication**: Enable `meshtastic: true` to see device protocol details
- **Troubleshooting specific components**: Enable only the component you're debugging to avoid log noise

### Example Output

**With `matrix_nio: true`, you'll see detailed logs like:**

```log
DEBUG:nio.http:Sending POST request to https://matrix.org/_matrix/client/r0/sync
DEBUG:nio.client:Received sync response with 5 rooms
```

**With `bleak: true`, you'll see BLE operations:**

```log
DEBUG:bleak:Scanning for BLE devices...
DEBUG:bleak.backends:Found device: AA:BB:CC:DD:EE:FF
```

**With `meshtastic: true`, you'll see device communication:**

```log
DEBUG:meshtastic:Sending packet to device
DEBUG:meshtastic.ble_interface:BLE characteristic write completed
```

## Tips for Advanced Configuration

### Performance Considerations

- **Debug logging can be verbose**: Only enable the components you need to troubleshoot
- **Prefix customization is lightweight**: No performance impact from custom formats
- **Test changes gradually**: Make one configuration change at a time for easier troubleshooting

### Configuration Validation

MMRelay includes built-in configuration validation:

```bash
# Check your configuration for errors
mmrelay check-config
```

This will validate your prefix formats and other configuration options before starting the relay.

### Getting Help

If you encounter issues with these advanced features:

1. **Check the logs** for warning messages about invalid configurations
2. **Use `mmrelay check-config`** to validate your settings
3. **Enable debug logging** for the relevant component
4. **Ask for help** in the MMRelay Matrix room with your configuration and log excerpts
