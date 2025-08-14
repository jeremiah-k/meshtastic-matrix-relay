#!/usr/bin/env python3
"""
Test that runtime no longer throws errors for missing broadcast_enabled.
"""

import os
import sys

import pytest

# Add the src directory to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def test_runtime_no_errors():
    from mmrelay.config import get_meshtastic_config_value
    from mmrelay.constants.config import DEFAULT_BROADCAST_ENABLED

    # Mock config without broadcast_enabled
    config = {
        "meshtastic": {"connection_type": "serial", "serial_port": "/dev/ttyUSB0"}
    }

    print("Testing runtime behavior for missing broadcast_enabled...")

    # This should NOT raise an error anymore (required=False)
    result = get_meshtastic_config_value(
        config, "broadcast_enabled", DEFAULT_BROADCAST_ENABLED, required=False
    )
    print(f"SUCCESS: No error thrown, got default value: {result}")

    # Assert that we got the expected default value
    assert result == DEFAULT_BROADCAST_ENABLED


def test_runtime_no_errors_pytest_wrapper():
    from mmrelay.config import get_meshtastic_config_value
    from mmrelay.constants.config import DEFAULT_BROADCAST_ENABLED

    config = {
        "meshtastic": {"connection_type": "serial", "serial_port": "/dev/ttyUSB0"}
    }

    # This should NOT raise an error anymore (required=False)
    result = get_meshtastic_config_value(
        config, "broadcast_enabled", DEFAULT_BROADCAST_ENABLED, required=False
    )
    assert result == DEFAULT_BROADCAST_ENABLED


def test_runtime_missing_broadcast_enabled_required_true_raises():
    from mmrelay.config import get_meshtastic_config_value
    from mmrelay.constants.config import DEFAULT_BROADCAST_ENABLED

    config = {
        "meshtastic": {"connection_type": "serial", "serial_port": "/dev/ttyUSB0"}
    }

    # Use pytest.raises to properly test for expected exceptions
    with pytest.raises(Exception):
        get_meshtastic_config_value(
            config, "broadcast_enabled", DEFAULT_BROADCAST_ENABLED, required=True
        )
