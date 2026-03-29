"""
Test that all imports work correctly with mocking.

This test verifies that the mocking setup in conftest.py properly handles
all external dependencies and allows modules to be imported without errors.
"""

import os
import sys

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


from mmrelay.constants.config import (
    CONFIG_KEY_PREFIX_ENABLED,
    CONFIG_SECTION_MATRIX,
    CONFIG_SECTION_MESHTASTIC,
)


def test_basic_imports():
    """
    Verify that core project modules and functions can be imported and are accessible under the mocking setup.
    """
    # These should work with the mocking setup
    from mmrelay.matrix_utils import get_matrix_prefix, get_meshtastic_prefix
    from mmrelay.message_queue import MessageQueue

    # Test that the functions exist and are callable
    assert callable(get_matrix_prefix)
    assert callable(get_meshtastic_prefix)
    assert MessageQueue is not None


def test_external_dependencies_mocked():
    """
    Verify that external dependencies are mocked and expose expected attributes.

    Asserts that the `meshtastic` and `nio` modules provide specific attributes and that `PIL.Image` is available, confirming the mocking setup is effective.
    """
    import meshtastic
    import nio
    from PIL import Image

    # These should be MagicMock objects
    assert hasattr(meshtastic, "BROADCAST_ADDR")
    assert hasattr(nio, "AsyncClient")
    assert Image is not None


def test_prefix_functions():
    """
    Verify that the prefix-generating functions return strings when called with sample configuration dictionaries and user identifiers.
    """
    from mmrelay.matrix_utils import get_matrix_prefix, get_meshtastic_prefix

    # Test meshtastic prefix
    config = {CONFIG_SECTION_MESHTASTIC: {CONFIG_KEY_PREFIX_ENABLED: True}}
    prefix = get_meshtastic_prefix(config, "TestUser")
    assert isinstance(prefix, str)

    # Test matrix prefix
    matrix_config = {CONFIG_SECTION_MATRIX: {CONFIG_KEY_PREFIX_ENABLED: True}}
    matrix_prefix = get_matrix_prefix(
        matrix_config, "TestLong", "TestShort", "TestMesh"
    )
    assert isinstance(matrix_prefix, str)
