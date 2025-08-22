# What's New in MMRelay 1.2

MMRelay 1.2 represents a major milestone with comprehensive Matrix End-to-End Encryption support, enhanced Docker deployment capabilities, and significant quality improvements. This release transforms MMRelay into a production-ready, enterprise-grade communication relay.

## 🔐 Matrix End-to-End Encryption (E2EE) Support

**The flagship feature of 1.2** - Full Matrix E2EE support enabling secure communication in encrypted Matrix rooms.

### Key Features
- **Seamless E2EE Integration**: Participate in encrypted Matrix rooms with automatic encryption/decryption
- **Device Identity Management**: Maintains persistent device identity and encryption keys
- **Mixed Room Support**: Handle both encrypted and unencrypted rooms in the same relay
- **Cross-Platform**: Full support on Linux/macOS, regular Matrix communication on Windows

### New Authentication System
- **`mmrelay auth login`**: Interactive OIDC-compatible authentication with E2EE setup
- **`mmrelay auth logout`**: Secure session cleanup with server-side token invalidation
- **Future-Proof**: Compatible with Matrix Authentication Service (MAS) and Matrix 2.0
- **Token Rotation**: Automatic handling of token refresh, preventing authentication expiration

### E2EE Configuration
```yaml
matrix:
  e2ee:
    enabled: true
    key_sharing_delay_seconds: 5
    store_path: ~/.mmrelay/store
```

## 🌤️ Weather Plugin

**New comprehensive weather forecasting plugin** with time-based predictions and robust error handling.

### Features
- **Time-Based Forecasting**: Accurate +2h and +5h predictions for any time of day
- **Cross-Midnight Support**: Works correctly with late evening forecasts using 48-hour data
- **Timezone Awareness**: Handles UTC, offset formats, and auto timezone detection
- **Day/Night Icons**: Hour-specific weather icons based on actual forecast time
- **Comprehensive Error Handling**: Graceful degradation for network, API, and data issues
- **94% Test Coverage**: Enterprise-grade reliability with extensive test suite

### Usage
```
!weather 40.7128,-74.0060
```

Returns current conditions plus 2-hour and 5-hour forecasts with weather icons and precipitation probability.

## 🐳 Enhanced Docker Support

**Comprehensive Docker deployment improvements** with flexible configuration options.

### Environment Variable Configuration
- **Matrix Authentication**: Configure homeserver, credentials via environment variables
- **Meshtastic Settings**: Connection parameters, operational settings
- **Logging Configuration**: Log levels, file paths
- **Database Paths**: Custom database locations

### Docker Compose Enhancements
- **Extensive Documentation**: Commented examples for all configuration options
- **Multiple Auth Methods**: Support for both `mmrelay auth login` and manual tokens
- **Secure File Permissions**: Automatic `0o600` permissions for sensitive files
- **Volume Management**: Clear separation of config and data volumes

## 🎨 Matrix HTML Formatting Support

**Native HTML message formatting** enabling rich text communication.

### Features
- **Rich Text Messages**: Support for bold, italic, links, and other HTML formatting
- **Plugin Integration**: Plugins can send formatted messages using `formatted=True`
- **Backward Compatibility**: Plain text fallback for clients that don't support HTML

## ⚙️ Configuration & Validation Improvements

**Enhanced configuration management** with comprehensive validation and error handling.

### New Features
- **Configuration Validation**: `mmrelay config check` with detailed error reporting
- **Environment Variable Overrides**: Operational settings can be configured via env vars
- **Secure File Creation**: Automatic secure permissions for configuration files
- **Error Recovery**: Better error messages and recovery suggestions

## 🧪 Testing & Quality Improvements

**Massive improvements in code quality and test coverage.**

### Test Coverage
- **90%+ Total Coverage**: Up from 70% in previous versions
- **Component Coverage**: Dedicated coverage tracking for major components
- **Full Test Suite**: Manual CI workflow for comprehensive testing across Python 3.10-3.12
- **Performance Tests**: Dedicated performance and stress testing

### Code Quality
- **Trunk Integration**: Updated linters and security scanners
- **Dependency Management**: Regular security updates and dependency synchronization
- **Documentation**: Comprehensive docstrings and improved documentation

## 🔧 Infrastructure & Developer Experience

### CI/CD Improvements
- **Full Test Suite Workflow**: Manual trigger for comprehensive testing
- **Component Coverage Reporting**: Detailed coverage breakdown by feature area
- **Multi-Python Testing**: Validation across Python 3.10, 3.11, and 3.12

### Documentation Restructure
- **Docker Guide Reorganization**: Clearer deployment paths from quick start to advanced
- **E2EE Documentation**: Comprehensive setup and troubleshooting guides
- **Developer Guides**: Enhanced development and testing documentation

## 🚀 Performance & Reliability

### Connection Management
- **Non-blocking Meshtastic Connection**: Reduced startup delays
- **BLE Reconnection Improvements**: Better handling of Bluetooth connection issues
- **Message Length Handling**: Proper truncation and length limit enforcement

### Error Handling
- **Graceful Degradation**: Better handling of network issues and API failures
- **Comprehensive Logging**: Improved debug information and error reporting
- **Recovery Mechanisms**: Automatic retry and fallback behaviors

## 📦 Installation & Deployment

### Package Management
- **PyPI Distribution**: Official package available via `pipx install mmrelay`
- **E2EE Optional Install**: `pipx install mmrelay[e2e]` for encryption support
- **Docker Images**: Official prebuilt Docker images for easy deployment

### Service Management
- **Systemd Integration**: `mmrelay service install` for Linux service management
- **Windows Installer**: Graphical installer with service integration
- **Cross-Platform**: Consistent experience across Linux, macOS, and Windows

## 🔄 Migration from 1.1.x

### Authentication Migration
1. **Recommended**: Use `mmrelay auth login` for future-proof authentication
2. **Legacy Support**: Existing access token configurations continue to work
3. **E2EE Upgrade**: Enable E2EE by installing `mmrelay[e2e]` and configuring E2EE settings

### Configuration Updates
- **Backward Compatibility**: All existing configurations continue to work
- **New Features**: Add E2EE, weather plugin, and other new features as desired
- **Environment Variables**: Optionally migrate to environment variable configuration for Docker

## 🎯 Looking Forward

MMRelay 1.2 establishes a solid foundation for future development with:
- **Matrix 2.0 Compatibility**: Ready for the next generation of Matrix
- **Extensible Plugin System**: Framework for additional plugins and integrations
- **Enterprise-Ready**: Production-quality reliability and security
- **Community-Driven**: Open source development with comprehensive testing

---

**Upgrade today** to experience the most secure, reliable, and feature-rich version of MMRelay yet!

```bash
pipx install --upgrade mmrelay[e2e]
mmrelay auth login  # Recommended for all users
```
