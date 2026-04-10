# What's New in MMRelay 1.2

MMRelay 1.2 introduces Matrix End-to-End Encryption support, enhanced authentication management, and improved Docker deployment capabilities. This release focuses on security, reliability, and ease of deployment for users upgrading from 1.1.4.

## 🔐 Matrix End-to-End Encryption (E2EE) Support

**The flagship feature of 1.2** - Full Matrix E2EE support enabling secure communication in encrypted Matrix rooms.

### Key Features

- **Seamless E2EE Integration**: Participate in encrypted Matrix rooms with automatic encryption/decryption
- **Device Identity Management**: Maintains persistent device identity and encryption keys
- **Mixed Room Support**: Handle both encrypted and unencrypted rooms in the same relay
- **Cross-Platform**: Full support on Linux/macOS, regular Matrix communication on Windows

### New E2EE Installation

```bash
# Install with E2EE support (Linux/macOS only)
pipx install 'mmrelay[e2e]'
```

### E2EE Configuration

```yaml
matrix:
  e2ee:
    enabled: true
    store_path: ~/.mmrelay/store
```

## 🔑 Enhanced Authentication Management

### New Authentication Commands

- **`mmrelay auth logout`**: Secure session cleanup with server-side token invalidation
  - Verifies Matrix password for security
  - Removes credentials and E2EE store
  - Invalidates server-side access tokens

### Improved Authentication Flow

- **Credentials Management**: `mmrelay auth login` creates persistent credentials stored in `~/.mmrelay/matrix/credentials.json`
- **Persistent Device ID**: Maintains a consistent device identity across restarts for E2EE
- **Interactive Setup**: Guided prompts for homeserver, username, and password

## 🎨 Matrix HTML Formatting Support

**New rich text messaging capability** - Send formatted messages to Matrix rooms.

### HTML Features

- **Rich Text Messages**: Support for bold, italic, links, and other HTML formatting
- **Plugin Integration**: Plugins can send formatted messages using `formatted=True`
- **Backward Compatibility**: Plain text fallback for clients that don't support HTML

## 🐳 Enhanced Docker Support

### New Docker Features

- **Prebuilt Docker Images**: Official prebuilt images for faster deployment
- **Enhanced Docker Compose**: Comprehensive examples and improved documentation
- **Secure File Permissions**: Automatic secure permissions for configuration files
- **Improved Volume Management**: Better separation of config and data volumes

## ⚙️ Configuration & Validation Improvements

### New Configuration Features

- **Enhanced Validation**: `mmrelay config check` with detailed error reporting and fix suggestions
- **Secure File Creation**: Automatic secure permissions for configuration files
- **Improved Error Messages**: Better debugging information and recovery suggestions

## 🧪 Quality & Infrastructure Improvements

### Testing Enhancements

- **Improved Test Coverage**: Significant increase from ~70% to 90%+ total coverage
- **Full Test Suite CI**: CI covers Python 3.10-3.12; runtime supports Python 3.10+
- **Component Coverage**: Dedicated coverage tracking for major feature areas

### Migration Note

MMRelay 1.2 requires Python 3.10 or newer. If you are still on Python 3.8 or 3.9, upgrade Python before upgrading MMRelay to 1.2.

### Documentation & Developer Experience

- **Docker Guide Restructure**: Clearer deployment paths and comprehensive examples
- **Enhanced E2EE Documentation**: Complete setup and troubleshooting guides
- **Improved Error Messages**: Better debugging information and recovery suggestions

## ⬆️ Upgrading from 1.1.4

### Simple Upgrade Process

```bash
# Upgrade MMRelay with E2EE support
pipx install --upgrade 'mmrelay[e2e]'

# Set up modern authentication (recommended)
mmrelay auth login
```

### Compatibility Notes

- **Existing Configurations**: Most 1.1.4 settings continue to work after the runtime Python upgrade
- **Gradual Migration**: Add new features (E2EE, HTML formatting, enhanced Docker support) as desired
- **Python Upgrade Required**: Python 3.8 and 3.9 users must upgrade their runtime before installing MMRelay 1.2

---

**MMRelay 1.2** brings secure end-to-end encryption, modern authentication, and enhanced deployment options for Python 3.10+ environments.
