# Radio Backend Decoupling - Design Document

## Executive Summary

This document outlines the architectural changes required to decouple MMRelay from its current hard dependency on Meshtastic, transforming it into a flexible multi-radio relay framework. This refactoring will enable support for multiple radio backend implementations while maintaining backward compatibility.

## Goals

1. **Abstraction Layer**: Create a clean abstraction layer between the relay core and radio-specific implementations
2. **Pluggable Architecture**: Enable radio backends to be loaded as plugins/adapters
3. **Multi-Radio Support**: Allow running multiple radio backends simultaneously (e.g., different radio types broadcasting to the same Matrix room)
4. **Backward Compatibility**: Maintain full compatibility with existing Meshtastic configurations
5. **Optional Radio**: Support running without any radio backend (Matrix-only mode for testing/development)

## Non-Goals

- Changing the Matrix side of the relay
- Modifying the plugin system architecture
- Breaking existing user configurations
- Supporting platforms other than Matrix (future consideration only)

## Current Architecture Analysis

### Tight Coupling Points

The current codebase has Meshtastic tightly coupled in several areas:

1. **`main.py`**:
   - Direct imports: `from mmrelay.meshtastic_utils import connect_meshtastic`
   - Line 136-138: Direct Meshtastic connection initialization
   - Hardcoded Meshtastic startup sequence

2. **`meshtastic_utils.py`**:
   - ~2500 lines of Meshtastic-specific code
   - Global state management (client, interface, locks)
   - Message handling (`on_meshtastic_message`)
   - Connection management, reconnection logic
   - BLE, Serial, TCP connection handling
   - Message formatting and relay logic

3. **`matrix_utils.py`**:
   - Direct import: `from mmrelay.meshtastic_utils import connect_meshtastic, send_text_reply`
   - Calls to Meshtastic-specific functions in message handlers
   - Meshtastic packet construction in Matrix message handlers

4. **`plugins/base_plugin.py`**:
   - `send_message()` method directly calls Meshtastic queue
   - Meshtastic-specific message formatting
   - Direct dependency on `meshtastic_utils`

5. **Configuration Structure**:
   - `config.yaml` has hardcoded `meshtastic:` section
   - Channel mapping assumes Meshtastic channel structure
   - Connection types (serial, BLE, TCP) are Meshtastic-specific

## Proposed Architecture

### 1. Radio Backend Interface

Create an abstract base class that defines the contract for all radio backends:

```python
# src/mmrelay/radio/base_backend.py

from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, Optional

class BaseRadioBackend(ABC):
    """Abstract base class for radio backends."""

    # Backend identification
    @property
    @abstractmethod
    def backend_name(self) -> str:
        """Unique identifier for this radio backend (e.g., 'meshtastic', 'meshcore')"""
        pass

    @property
    @abstractmethod
    def backend_prefix(self) -> str:
        """Default message prefix for this backend (e.g., '[MT]', '[MC]')"""
        pass

    # Connection management
    @abstractmethod
    async def connect(self, config: Dict[str, Any]) -> bool:
        """Initialize and connect to the radio backend."""
        pass

    @abstractmethod
    async def disconnect(self) -> None:
        """Disconnect from the radio backend and cleanup resources."""
        pass

    @abstractmethod
    def is_connected(self) -> bool:
        """Check if the radio backend is currently connected."""
        pass

    # Message handling
    @abstractmethod
    def register_message_callback(self, callback: Callable[[Dict[str, Any]], None]) -> None:
        """Register a callback to be invoked when messages are received from the radio."""
        pass

    @abstractmethod
    async def send_message(
        self,
        text: str,
        channel: Optional[int] = None,
        destination_id: Optional[str] = None,
        **kwargs
    ) -> bool:
        """Send a message via the radio backend."""
        pass

    # Node/contact management
    @abstractmethod
    def get_nodes(self) -> Dict[str, Any]:
        """Get information about known nodes/contacts on the network."""
        pass

    @abstractmethod
    def get_node_info(self, node_id: str) -> Optional[Dict[str, Any]]:
        """Get detailed information about a specific node."""
        pass

    # Metadata
    @abstractmethod
    def get_meshnet_name(self) -> str:
        """Get the name of this mesh network."""
        pass

    @abstractmethod
    def supports_feature(self, feature: str) -> bool:
        """Check if backend supports a specific feature (e.g., 'telemetry', 'location')."""
        pass
```

### 2. Radio Registry (Current Implementation)

Track the available backends and select a single active backend. Multi-backend routing can be layered on later.

```python
# src/mmrelay/radio/registry.py

class RadioRegistry:
    """Tracks available backends and a single active backend."""

    def __init__(self):
        self.backends: Dict[str, BaseRadioBackend] = {}
        self.active_backend: Optional[str] = None

    def register_backend(self, backend: BaseRadioBackend) -> None:
        """Register a backend implementation."""
        pass

    def set_active_backend(self, backend_name: Optional[str]) -> bool:
        """Select the active backend by name."""
        pass

    def get_active_backend(self) -> Optional[BaseRadioBackend]:
        """Return the active backend instance."""
        pass

    async def connect_active_backend(self, config: Dict[str, Any]) -> bool:
        """Connect the active backend."""
        pass

    async def disconnect_active_backend(self) -> None:
        """Disconnect the active backend."""
        pass
```

### 3. Message Format Standardization

Create a unified message format that all backends must conform to:

```python
# src/mmrelay/radio/message.py

@dataclass
class RadioMessage:
    """Standardized message format for all radio backends."""

    # Core fields
    text: str
    sender_id: str
    sender_name: str
    timestamp: float

    # Backend identification
    backend: str  # e.g., 'meshtastic', 'meshcore'
    meshnet_name: str

    # Channel/routing
    channel: Optional[int] = None
    is_direct_message: bool = False
    destination_id: Optional[str] = None

    # Message metadata (backend-specific data stored as dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    # Optional fields
    location: Optional[Dict[str, float]] = None  # {lat, lon, alt}
    telemetry: Optional[Dict[str, Any]] = None

    def to_matrix_event_content(self) -> Dict[str, Any]:
        """Convert to Matrix event content with embedded radio metadata."""
        pass

    @classmethod
    def from_matrix_event(cls, event: Any) -> Optional['RadioMessage']:
        """Extract radio message from Matrix event."""
        pass
```

### 4. Configuration Structure

Update configuration to support multiple radio backends:

```yaml
# New configuration structure
radios:
  # Legacy supported format (backward compatible)
  meshtastic:
    enabled: true
    connection_type: serial
    serial_port: /dev/ttyUSB0
    # ... existing config options

  # Future: additional radio backends can be added
  # other_radio:
  #   enabled: true
  #   connection_type: serial
  #   serial_port: /dev/ttyUSB1

# Matrix rooms with radio mappings
matrix_rooms:
  - id: "!roomid:matrix.org"
    radios:
      meshtastic:
        channel: 0
      # other_radio:
      #   channel: 0

# Global relay settings
relay:
  broadcast_mode: false # If true, messages from any radio go to all radios
  message_prefix_enabled: true
```

## Implementation Plan

### Phase 1: Core Abstraction Layer (No Breaking Changes)

**Files to Create:**

- `src/mmrelay/radio/__init__.py`
- `src/mmrelay/radio/base_backend.py` - Abstract base class
- `src/mmrelay/radio/message.py` - Standardized message format
- `src/mmrelay/radio/registry.py` - Backend registry (single active backend)

**Files to Modify:**
None (pure addition)

**Testing:**

- Unit tests for new abstract classes
- Integration tests for RadioRegistry

### Phase 2: Meshtastic Backend Adapter

**Files to Create:**

- `src/mmrelay/radio/backends/__init__.py`
- `src/mmrelay/radio/backends/meshtastic_backend.py` - Wrapper implementing BaseRadioBackend

**Files to Modify:**

- `src/mmrelay/meshtastic_utils.py` - Extract reusable components, mark as internal

**Strategy:**

1. Create `MeshtasticBackend` class that implements `BaseRadioBackend`
2. Wrap existing `meshtastic_utils` functionality
3. Maintain all existing behavior
4. Add compatibility layer for existing direct imports

**Testing:**

- All existing Meshtastic tests must pass
- New tests for MeshtasticBackend wrapper

### Phase 3: Main Integration

**Files to Modify:**

- `src/mmrelay/main.py` - Use RadioRegistry instead of direct imports
- `src/mmrelay/matrix_utils.py` - Use RadioMessage abstraction
- `src/mmrelay/config.py` - Support new `radios:` section (with backward compat)

**Strategy:**

1. Modify `main.py` to initialize RadioRegistry
2. Auto-detect legacy `meshtastic:` config and create MeshtasticBackend
3. Keep all existing behavior working
4. Add deprecation warnings for legacy config (not removing support)

**Testing:**

- Full integration test suite
- Backward compatibility tests with old configs
- End-to-end message relay tests

### Phase 4: Plugin System Integration

**Files to Modify:**

- `src/mmrelay/plugins/base_plugin.py` - Update `send_message()` to use RadioRegistry

**Strategy:**

1. Add radio backend selection to `send_message()` signature
2. Maintain backward compatibility (default to first available backend)
3. Allow plugins to specify target backend(s)

**Testing:**

- All existing plugin tests must pass
- New tests for multi-backend plugin messaging

### Phase 5: Documentation and Migration Guide

**Files to Create/Update:**

- `docs/RADIO_BACKENDS.md` - Guide for backend developers
- `docs/MIGRATION_MULTI_RADIO.md` - User migration guide
- Update existing docs to reference new architecture

## Backward Compatibility Strategy

### Configuration Compatibility

Old format (still supported):

```yaml
meshtastic:
  connection_type: serial
  # ...
```

New format (preferred):

```yaml
radios:
  meshtastic:
    enabled: true
    # ...
```

**Strategy**: Auto-detect old format and internally convert to new format

### API Compatibility

Maintain existing public functions:

```python
# meshtastic_utils.py - keep these as compatibility wrappers
def connect_meshtastic(passed_config=None):
    """Legacy function - wraps RadioRegistry"""
    # Call RadioRegistry internally
    pass

def send_text_reply(...):
    """Legacy function - wraps RadioMessage"""
    pass
```

### Plugin Compatibility

All existing plugins continue to work with:

```python
self.send_message(text="Hello", channel=0)  # Still works, uses first backend
```

## Benefits

1. **Extensibility**: Easy to add new radio backends
2. **Testability**: Can run Matrix-only mode for testing
3. **Flexibility**: Users can run multiple radio types simultaneously
4. **Maintainability**: Clear separation of concerns
5. **Future-Proof**: Easy to add new platforms beyond Matrix

## Risks and Mitigation

### Risk 1: Breaking Existing Installations

**Mitigation**: Comprehensive backward compatibility layer, extensive testing

### Risk 2: Performance Overhead

**Mitigation**: Keep abstraction lightweight, use async properly

### Risk 3: Increased Complexity

**Mitigation**: Clear documentation, gradual rollout, maintain simple common use cases

## Testing Strategy

### Unit Tests

- Test each abstraction layer independently
- Mock backends for RadioRegistry tests
- Test message format conversions

### Integration Tests

- Test Meshtastic backend wrapper with real hardware/simulators
- Test backward compatibility with old configs
- Test Matrix ↔ Radio message flow

### End-to-End Tests

- Full relay operation with Meshtastic backend
- Matrix-only mode operation
- Plugin interaction with backends

## Success Criteria

1. All existing Meshtastic functionality works unchanged
2. Old configuration files work without modification
3. New RadioRegistry can be extended with additional backends
4. Unit test coverage >90% for new components
5. All existing integration tests pass
6. Documentation complete and clear

## Timeline Estimate

- Phase 1 (Abstraction): 1-2 weeks
- Phase 2 (Meshtastic Adapter): 2-3 weeks
- Phase 3 (Main Integration): 2-3 weeks
- Phase 4 (Plugin Integration): 1 week
- Phase 5 (Documentation): 1 week

### Total: 7-10 weeks for complete implementation

## Future Enhancements

After decoupling is complete, these become possible:

1. Additional radio backend support via community plugins
2. Radio-to-radio bridging (different technologies on same network)
3. Hybrid radio modes (e.g., LoRa with different parameters simultaneously)
4. Radio backend hot-swapping without restart
5. Platform abstraction (beyond Matrix: Discord, XMPP, etc.)

## Questions to Resolve

1. Should RadioRegistry support runtime backend registration, or only at startup?
2. How to handle backend-specific features in plugins (e.g., telemetry)?
3. Should message queue be per-backend or global?
4. How to handle different rate limits across backends?

## Appendix: Key Files and Line Counts

Current coupling points:

- `meshtastic_utils.py`: ~2500 lines (mostly stays, gets wrapped)
- `main.py`: ~530 lines (modify startup sequence)
- `matrix_utils.py`: ~1500 lines (update message handlers)
- `plugins/base_plugin.py`: ~600 lines (update send methods)

New files:

- `radio/base_backend.py`: ~200 lines
- `radio/message.py`: ~150 lines
- `radio/registry.py`: ~300 lines
- `radio/backends/meshtastic_backend.py`: ~400 lines
