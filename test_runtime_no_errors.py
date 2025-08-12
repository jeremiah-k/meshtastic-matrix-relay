#!/usr/bin/env python3
"""
Test that runtime no longer throws errors for missing broadcast_enabled.
"""

import sys
import os

# Add the src directory to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

def test_runtime_no_errors():
    from mmrelay.matrix_utils import get_meshtastic_config_value
    from mmrelay.constants.config import DEFAULT_BROADCAST_ENABLED
    import mmrelay.matrix_utils
    
    # Mock config without broadcast_enabled
    mmrelay.matrix_utils.config = {
        "meshtastic": {
            "connection_type": "serial",
            "serial_port": "/dev/ttyUSB0"
        }
    }
    
    print("Testing runtime behavior for missing broadcast_enabled...")
    
    try:
        # This should NOT raise an error anymore (required=False)
        result = get_meshtastic_config_value("broadcast_enabled", DEFAULT_BROADCAST_ENABLED, required=False)
        print(f"SUCCESS: No error thrown, got default value: {result}")
        return True
    except Exception as e:
        print(f"ERROR: Still throwing exception: {e}")
        return False

if __name__ == "__main__":
    success = test_runtime_no_errors()
    sys.exit(0 if success else 1)
