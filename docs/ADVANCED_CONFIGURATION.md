# Advanced Configuration Options

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

## Environment Variable Overrides

> **Note**: Environment variables are provided for advanced deployment scenarios and are **not recommended for most users**. The config.yaml approach is simpler, more maintainable, and easier to troubleshoot. Use environment variables only when you have specific deployment requirements that cannot be met with config.yaml.

Environment variables can override specific config.yaml settings for specialized deployment scenarios. They are primarily useful for:

- **CI/CD pipelines** with dynamic configuration values
- **Container orchestration** (Kubernetes, Docker Swarm) with secrets injection
- **Multi-environment deployments** (dev/staging/prod) using the same image
- **External configuration management** systems

Precedence at startup:

1. Environment variables
2. config.yaml
3. Built-in defaults

### Available Environment Variables

These environment variables can override config.yaml settings:

#### Meshtastic Connection Settings

- **`MMRELAY_MESHTASTIC_CONNECTION_TYPE`**: Connection method (`tcp`, `serial`, or `ble`)
- **`MMRELAY_MESHTASTIC_HOST`**: TCP host address (for `tcp` connections)
- **`MMRELAY_MESHTASTIC_PORT`**: TCP port number (for `tcp` connections, default: 4403)
- **`MMRELAY_MESHTASTIC_SERIAL_PORT`**: Serial device path (for `serial` connections, e.g., `/dev/ttyUSB0`)
- **`MMRELAY_MESHTASTIC_BLE_ADDRESS`**: Bluetooth MAC address (for `ble` connections)

#### Operational Settings

- **`MMRELAY_MESHTASTIC_BROADCAST_ENABLED`**: Enable Matrix→Meshtastic messages (`true`/`false`)
- **`MMRELAY_MESHTASTIC_MESHNET_NAME`**: Display name for the mesh network
- **`MMRELAY_MESHTASTIC_MESSAGE_DELAY`**: Delay between messages in seconds (minimum: 2.0; values below are clamped at startup)

#### System Configuration

- **`MMRELAY_LOGGING_LEVEL`**: Log level (`debug`, `info`, `warning`, `error`, `critical`)
- **`MMRELAY_LOG_FILE`**: Path to log file (enables file logging when set)
- **`MMRELAY_DATABASE_PATH`**: Path to SQLite database file

### Why Config.yaml is Usually Better

**For typical home/personal deployments, config.yaml provides:**

- **Centralized configuration** - All settings in one place
- **Easy change tracking** - Version control friendly
- **Better security** - No secrets visible in `docker inspect`
- **Simpler management** - Edit one file instead of multiple environment variables
- **Better documentation** - Comments and structure in the config file

**Environment variables add complexity:**

- **Scattered configuration** - Settings spread across multiple places
- **Harder to troubleshoot** - Must check both config file and environment
- **Security concerns** - Values visible in process lists and `docker inspect`
- **No validation** - Typos in variable names fail silently

### When Environment Variables Make Sense

Use environment variables **only** when:

- You're deploying with container orchestration that injects secrets
- You need different values per environment (dev/staging/prod) with the same image
- External systems manage your configuration
- You're building CI/CD pipelines with dynamic values

### Setting Environment Variables

**For docker-compose users:** Add to your `.env` file or docker-compose environment section.

**For Portainer users:** Set them in Portainer's environment variables section.

**Important:** Environment variables override corresponding config.yaml settings when present. Use them sparingly, document which settings you're overriding, and avoid placing secrets in env where they can appear in process lists and `docker inspect`.

### Environment Variable to Config.yaml Mapping

| Environment Variable                   | Config.yaml Path               | Type    | Description                                      |
| -------------------------------------- | ------------------------------ | ------- | ------------------------------------------------ |
| `MMRELAY_MESHTASTIC_CONNECTION_TYPE`   | `meshtastic.connection_type`   | string  | Connection method (`tcp`, `serial`, `ble`)       |
| `MMRELAY_MESHTASTIC_HOST`              | `meshtastic.host`              | string  | TCP host address                                 |
| `MMRELAY_MESHTASTIC_PORT`              | `meshtastic.port`              | integer | TCP port (default: 4403)                         |
| `MMRELAY_MESHTASTIC_SERIAL_PORT`       | `meshtastic.serial_port`       | string  | Serial device path                               |
| `MMRELAY_MESHTASTIC_BLE_ADDRESS`       | `meshtastic.ble_address`       | string  | Bluetooth MAC address                            |
| `MMRELAY_MESHTASTIC_BROADCAST_ENABLED` | `meshtastic.broadcast_enabled` | boolean | Enable Matrix→Meshtastic                         |
| `MMRELAY_MESHTASTIC_MESHNET_NAME`      | `meshtastic.meshnet_name`      | string  | Display name for mesh                            |
| `MMRELAY_MESHTASTIC_MESSAGE_DELAY`     | `meshtastic.message_delay`     | float   | Delay between messages in seconds (minimum: 2.0) |
| `MMRELAY_LOGGING_LEVEL`                | `logging.level`                | string  | Log level (`debug`, `info`, `warning`, `error`, `critical`) |
| `MMRELAY_LOG_FILE`                     | `logging.filename`             | string  | Log file path                                    |
| `MMRELAY_DATABASE_PATH`                | `database.path`                | string  | SQLite database path                             |

## Tips for Advanced Configuration

### Performance Considerations

- **Debug logging can be verbose**: Only enable the components you need to troubleshoot
- **Prefix customization is lightweight**: No performance impact from custom formats
- **Environment variables have no performance impact**: They're processed once at startup
- **Test changes gradually**: Make one configuration change at a time for easier troubleshooting

### Configuration Validation

MMRelay includes built-in configuration validation:

```bash
# Check your configuration for errors
mmrelay config check
```

This will validate your prefix formats and other configuration options before starting the relay.

### Getting Help

If you encounter issues with these advanced features:

1. **Check the logs** for warning messages about invalid configurations
2. **Use `mmrelay config check`** to validate your settings
3. **Enable debug logging** for the relevant component
4. **Ask for help** in the MMRelay Matrix room with your configuration and log excerpts
