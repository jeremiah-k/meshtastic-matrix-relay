# CLI Restructure: Grouped Commands Implementation

## Overview

This document outlines the restructuring of MMRelay's CLI from a flat command structure to a professional grouped command system that follows industry standards and provides better extensibility.

## Current State Analysis

### Existing CLI Structure

**Current Commands:**
- `mmrelay` - Start the relay (main functionality)
- `mmrelay --version` - Show version
- `mmrelay --generate-config` - Generate sample config (deprecated flag)
- `mmrelay --check-config` - Validate config (deprecated flag)
- `mmrelay --install-service` - Install systemd service (deprecated flag)
- `mmrelay --auth` - Matrix authentication (deprecated flag)

**Modern Subcommands (partially implemented):**
- `mmrelay auth` - Authentication management
- `mmrelay check-config` - Config validation
- `mmrelay generate-config` - Config generation
- `mmrelay install-service` - Service installation

### Problems with Current Structure

1. **Mixed paradigms** - Both flags (`--generate-config`) and subcommands (`auth`) coexist
2. **Deprecated options in help** - Confuses new users with outdated syntax
3. **Poor extensibility** - Flat structure doesn't scale well
4. **Inconsistent with industry standards** - Modern CLI tools use grouped commands

## Proposed Grouped Command Structure

### Core Design Principles

1. **Logical grouping** - Related commands under common prefixes
2. **Industry alignment** - Follow patterns from Docker, Kubernetes, Git
3. **Extensibility** - Easy to add new commands within existing groups
4. **Backward compatibility** - Keep old commands working with deprecation warnings
5. **Clean help text** - No deprecated options visible in help

### Command Groups

#### 1. CONFIG Group
```bash
mmrelay config generate    # Create sample config.yaml
mmrelay config check       # Validate configuration file
```

**Current Mapping:**
- `mmrelay --generate-config` → `mmrelay config generate`
- `mmrelay --check-config` → `mmrelay config check`
- `mmrelay generate-config` → `mmrelay config generate`
- `mmrelay check-config` → `mmrelay config check`

#### 2. AUTH Group
```bash
mmrelay auth login         # Authenticate with Matrix (renamed from 'auth')
mmrelay auth status        # Check authentication status
```

**Current Mapping:**
- `mmrelay --auth` → `mmrelay auth login`
- `mmrelay auth` → `mmrelay auth login`

#### 3. SERVICE Group
```bash
mmrelay service install    # Install systemd service
mmrelay service start      # Start the service
mmrelay service stop       # Stop the service
mmrelay service status     # Check service status
mmrelay service restart    # Restart the service
mmrelay service logs       # View service logs
```

**Current Mapping:**
- `mmrelay --install-service` → `mmrelay service install`
- `mmrelay install-service` → `mmrelay service install`

**New Commands:**
- `mmrelay service start/stop/status/restart/logs` - Full service management

### Global Commands

```bash
mmrelay                    # Start the relay (unchanged)
mmrelay --version          # Show version (unchanged)
mmrelay --help             # Show help (unchanged)
```

**Global Options (work with all commands):**
- `--config` - Configuration file path
- `--data-dir` - Data directory path
- `--log-level` - Logging level
- `--logfile` - Log file path

## Implementation Strategy

### Phase 1: Infrastructure
1. **Implement nested argparse subparsers** - Support grouped commands
2. **Hide deprecated flags from help** - Keep functional but not visible
3. **Add deprecation warnings** - Guide users to new commands
4. **Maintain backward compatibility** - All existing commands still work

### Phase 2: Command Groups
1. **CONFIG group** - Implement `mmrelay config generate/check`
2. **AUTH group** - Implement `mmrelay auth login/status`
3. **SERVICE group** - Implement full service management

### Phase 3: Documentation
1. **Update all documentation** - Use new command format exclusively
2. **Update error messages** - Suggest new commands
3. **Migration guide** - Help users transition

## Technical Implementation Details

### Argument Parser Structure

```python
# Main parser
parser = argparse.ArgumentParser(description="Meshtastic Matrix Relay")

# Global options
parser.add_argument("--config", help="Path to config file")
parser.add_argument("--data-dir", help="Base directory for all data")
parser.add_argument("--log-level", choices=["error", "warning", "info", "debug"])
parser.add_argument("--version", action="store_true", help="Show version")

# Deprecated flags (hidden from help)
parser.add_argument("--generate-config", action="store_true", help=argparse.SUPPRESS)
parser.add_argument("--check-config", action="store_true", help=argparse.SUPPRESS)
parser.add_argument("--install-service", action="store_true", help=argparse.SUPPRESS)
parser.add_argument("--auth", action="store_true", help=argparse.SUPPRESS)

# Subcommands
subparsers = parser.add_subparsers(dest="command", help="Available commands")

# CONFIG group
config_parser = subparsers.add_parser("config", help="Configuration management")
config_subparsers = config_parser.add_subparsers(dest="config_command")
config_subparsers.add_parser("generate", help="Create sample config.yaml")
config_subparsers.add_parser("check", help="Validate configuration file")

# AUTH group
auth_parser = subparsers.add_parser("auth", help="Authentication management")
auth_subparsers = auth_parser.add_subparsers(dest="auth_command")
auth_login_parser = auth_subparsers.add_parser("login", help="Authenticate with Matrix")
auth_login_parser.add_argument("--force", action="store_true", help="Force re-authentication")
auth_subparsers.add_parser("status", help="Check authentication status")

# SERVICE group
service_parser = subparsers.add_parser("service", help="Service management")
service_subparsers = service_parser.add_subparsers(dest="service_command")
service_subparsers.add_parser("install", help="Install systemd service")
service_subparsers.add_parser("start", help="Start the service")
service_subparsers.add_parser("stop", help="Stop the service")
service_subparsers.add_parser("status", help="Check service status")
service_subparsers.add_parser("restart", help="Restart the service")
service_subparsers.add_parser("logs", help="View service logs")
```

### Command Handling

```python
def handle_command(args):
    """Handle grouped commands."""
    if args.command == "config":
        return handle_config_command(args)
    elif args.command == "auth":
        return handle_auth_command(args)
    elif args.command == "service":
        return handle_service_command(args)
    else:
        # Handle legacy flags with deprecation warnings
        return handle_legacy_commands(args)

def handle_config_command(args):
    """Handle config subcommands."""
    if args.config_command == "generate":
        return generate_sample_config()
    elif args.config_command == "check":
        return check_config(args)

def handle_auth_command(args):
    """Handle auth subcommands."""
    if args.auth_command == "login":
        return handle_auth_login(args)
    elif args.auth_command == "status":
        return handle_auth_status(args)

def handle_service_command(args):
    """Handle service subcommands."""
    if args.service_command == "install":
        return handle_service_install()
    elif args.service_command == "start":
        return handle_service_start()
    elif args.service_command == "stop":
        return handle_service_stop()
    elif args.service_command == "status":
        return handle_service_status()
    elif args.service_command == "restart":
        return handle_service_restart()
    elif args.service_command == "logs":
        return handle_service_logs()
```

### Service Management Implementation

The service group will leverage existing functionality in `setup_utils.py` and add new capabilities:

**Existing Functions to Reuse:**
- `install_service()` - For `mmrelay service install`
- `is_service_active()` - For status checking
- `show_service_status()` - For `mmrelay service status`
- `start_service()` - For `mmrelay service start`

**New Functions to Implement:**
- `stop_service()` - Stop the systemd service
- `restart_service()` - Restart the systemd service
- `show_service_logs()` - Display recent service logs

## Migration Strategy

### Backward Compatibility

1. **Keep all existing commands working** - No breaking changes
2. **Add deprecation warnings** - Only when old commands are used
3. **Hide deprecated options** - Not shown in help text
4. **Clear migration guidance** - Tell users the new command format

### Deprecation Warning Format

```
Warning: --generate-config is deprecated. Use 'mmrelay config generate' instead.
```

### Documentation Updates

All user-facing documentation will be updated to use the new grouped command format:
- README.md quick start commands
- docs/INSTRUCTIONS.md CLI reference
- Error messages and help text
- Sample configurations

## File Structure Changes

### New Files to Create
- `src/mmrelay/commands/` - Directory for command group modules
- `src/mmrelay/commands/__init__.py` - Command module initialization
- `src/mmrelay/commands/config.py` - Config command group implementation
- `src/mmrelay/commands/auth.py` - Auth command group implementation
- `src/mmrelay/commands/service.py` - Service command group implementation

### Modified Files
- `src/mmrelay/cli.py` - Main CLI parser restructure
- `src/mmrelay/setup_utils.py` - Add new service management functions
- All documentation files - Update command references

## Service Management Extensions

### New Service Functions Needed

```python
def stop_service():
    """Stop the systemd user service."""
    try:
        subprocess.run(["/usr/bin/systemctl", "--user", "stop", "mmrelay.service"], check=True)
        print("Service stopped successfully")
        return True
    except subprocess.CalledProcessError as e:
        print(f"Error stopping service: {e}")
        return False

def restart_service():
    """Restart the systemd user service."""
    try:
        subprocess.run(["/usr/bin/systemctl", "--user", "restart", "mmrelay.service"], check=True)
        print("Service restarted successfully")
        return True
    except subprocess.CalledProcessError as e:
        print(f"Error restarting service: {e}")
        return False

def show_service_logs(lines=50):
    """Show recent service logs."""
    try:
        result = subprocess.run([
            "/usr/bin/journalctl", "--user", "-u", "mmrelay.service",
            "-n", str(lines), "--no-pager"
        ], check=True, capture_output=True, text=True)
        print(result.stdout)
        return True
    except subprocess.CalledProcessError as e:
        print(f"Error getting service logs: {e}")
        return False
```

### Auth Status Implementation

```python
def check_auth_status():
    """Check Matrix authentication status."""
    from mmrelay.config import get_config_paths
    import os

    # Check for credentials.json
    config_paths = get_config_paths()
    for config_path in config_paths:
        config_dir = os.path.dirname(config_path)
        credentials_path = os.path.join(config_dir, "credentials.json")
        if os.path.exists(credentials_path):
            print(f"✅ Found credentials.json at: {credentials_path}")
            # TODO: Validate credentials and check if they're still valid
            return True

    print("❌ No credentials.json found")
    print("Run 'mmrelay auth login' to authenticate")
    return False
```

## Benefits

### For Users
- **Professional CLI experience** - Matches industry standards
- **Better discoverability** - `mmrelay config --help` shows all config commands
- **Logical organization** - Related commands grouped together
- **Comprehensive service management** - Full control over systemd service

### For Developers
- **Extensible architecture** - Easy to add new command groups
- **Maintainable code** - Clear separation of concerns
- **Consistent patterns** - Standardized command handling
- **Future-proof design** - Ready for plugin system, diagnostics, etc.

## Testing Strategy

1. **Backward compatibility testing** - All existing commands still work
2. **New command testing** - All grouped commands function correctly
3. **Help text validation** - No deprecated options visible
4. **Error handling** - Proper error messages for invalid commands
5. **Integration testing** - Commands work with global options

## Rollout Plan

1. **Implementation** - Build grouped command infrastructure
2. **Testing** - Comprehensive testing of all command paths
3. **Documentation** - Update all user-facing text
4. **Release** - Deploy with clear migration guidance
5. **Feedback** - Monitor user adoption and address issues

This restructure will transform MMRelay's CLI into a professional, extensible interface that users expect from modern command-line tools while maintaining full backward compatibility during the transition period.
