#!/usr/bin/env python3
"""
Test script for prefix customization functionality.
Demonstrates the new configurable prefix formats for both directions.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mmrelay.matrix_utils import get_matrix_prefix, get_meshtastic_prefix


def test_matrix_to_meshtastic_prefixes():
    """
    Test various configurations for generating message prefixes from Matrix to Meshtastic.

    Verifies correct prefix formatting for default, disabled, custom, truncated, and invalid format scenarios using different configuration options and user data.
    """
    print("=== Matrix → Meshtastic Prefix Tests ===\n")

    # Test data
    full_name = "Alice Smith"

    # Test 1: Default configuration (enabled)
    config1 = {"meshtastic": {"prefix_enabled": True}}
    prefix1 = get_meshtastic_prefix(config1, full_name)
    assert prefix1 == "Alice[M]: ", f"Default format failed: got '{prefix1}'"
    print("✓ Default format")

    # Test 2: Disabled prefixes
    config2 = {"meshtastic": {"prefix_enabled": False}}
    prefix2 = get_meshtastic_prefix(config2, full_name)
    assert prefix2 == "", f"Disabled prefixes failed: got '{prefix2}'"
    print("✓ Disabled prefixes")

    # Test 3: Variable length truncation
    config3 = {
        "meshtastic": {"prefix_enabled": True, "prefix_format": "{display3}[M]: "}
    }
    prefix3 = get_meshtastic_prefix(config3, full_name)
    assert prefix3 == "Ali[M]: ", f"3-char display name failed: got '{prefix3}'"
    print("✓ 3-char display name")

    # Test 4: Custom format with full display name
    config4 = {"meshtastic": {"prefix_enabled": True, "prefix_format": "[{display}]: "}}
    prefix4 = get_meshtastic_prefix(config4, full_name)
    assert (
        prefix4 == "[Alice Smith]: "
    ), f"Full display name brackets failed: got '{prefix4}'"
    print("✓ Full display name brackets")

    # Test 5: Custom format with user ID
    config5 = {"meshtastic": {"prefix_enabled": True, "prefix_format": "{display8}> "}}
    prefix5 = get_meshtastic_prefix(config5, full_name, "@alice:matrix.org")
    assert prefix5 == "Alice Sm> ", f"8-char prompt failed: got '{prefix5}'"
    print("✓ 8-char prompt")

    # Test 6: Username and server variables
    config6 = {
        "meshtastic": {"prefix_enabled": True, "prefix_format": "{username}@{server}: "}
    }
    prefix6 = get_meshtastic_prefix(config6, full_name, "@alice:matrix.org")
    assert (
        prefix6 == "alice@matrix.org: "
    ), f"Username/server format failed: got '{prefix6}'"
    print("✓ Username/server format")

    # Test 7: Invalid format (should fallback)
    config7 = {"meshtastic": {"prefix_enabled": True, "prefix_format": "{invalid}: "}}
    prefix7 = get_meshtastic_prefix(config7, full_name)
    assert prefix7 == "Alice[M]: ", f"Invalid format (fallback) failed: got '{prefix7}'"
    print("✓ Invalid format (fallback)")

    print()


def test_meshtastic_to_matrix_prefixes():
    """Test Meshtastic to Matrix prefix customization."""
    print("=== Meshtastic → Matrix Prefix Tests ===\n")

    # Test data
    longname = "Alice"
    shortname = "Ali"
    meshnet = "MyMeshNetwork"

    # Test 1: Default configuration (enabled)
    config1 = {"matrix": {"prefix_enabled": True}}
    prefix1 = get_matrix_prefix(config1, longname, shortname, meshnet)
    assert (
        prefix1 == "[Alice/MyMeshNetwork]: "
    ), f"Default format failed: got '{prefix1}'"
    print("✓ Default format")

    # Test 2: Disabled prefixes
    config2 = {"matrix": {"prefix_enabled": False}}
    prefix2 = get_matrix_prefix(config2, longname, shortname, meshnet)
    assert prefix2 == "", f"Disabled prefixes failed: got '{prefix2}'"
    print("✓ Disabled prefixes")

    # Test 3: Variable length truncation
    config3 = {"matrix": {"prefix_enabled": True, "prefix_format": "({long4}): "}}
    prefix3 = get_matrix_prefix(config3, longname, shortname, meshnet)
    assert prefix3 == "(Alic): ", f"4-char longname failed: got '{prefix3}'"
    print("✓ 4-char longname")

    # Test 4: Custom format with truncated mesh
    config4 = {
        "matrix": {"prefix_enabled": True, "prefix_format": "[{mesh6}] {short}: "}
    }
    prefix4 = get_matrix_prefix(config4, longname, shortname, meshnet)
    assert prefix4 == "[MyMesh] Ali: ", f"6-char mesh failed: got '{prefix4}'"
    print("✓ 6-char mesh")

    # Test 5: Invalid format (should fallback)
    config5 = {"matrix": {"prefix_enabled": True, "prefix_format": "{invalid}: "}}
    prefix5 = get_matrix_prefix(config5, longname, shortname, meshnet)
    assert (
        prefix5 == "[Alice/MyMeshNetwork]: "
    ), f"Invalid format (fallback) failed: got '{prefix5}'"
    print("✓ Invalid format (fallback)")

    print()


def test_prefix_symmetry():
    """Test prefix symmetry scenarios."""
    print("=== Prefix Symmetry Tests ===\n")

    # Test scenario: Both directions disabled
    config_both_off = {
        "meshtastic": {"prefix_enabled": False},
        "matrix": {"prefix_enabled": False},
    }

    m2m_prefix = get_meshtastic_prefix(config_both_off, "Alice Smith")
    mesh2m_prefix = get_matrix_prefix(config_both_off, "Bob", "Bob", "TestMesh")

    print("Both disabled:")
    print(f"  Matrix→Mesh: '{m2m_prefix}Hello'")
    print(f"  Mesh→Matrix: '{mesh2m_prefix}Hi there'")
    print()

    # Test scenario: Minimal custom formats
    config_minimal = {
        "meshtastic": {"prefix_enabled": True, "prefix_format": "{display4}: "},
        "matrix": {"prefix_enabled": True, "prefix_format": "{short}: "},
    }

    m2m_prefix = get_meshtastic_prefix(config_minimal, "Alice Smith")
    mesh2m_prefix = get_matrix_prefix(config_minimal, "Bob", "Bob", "TestMesh")

    print("Minimal symmetric:")
    print(f"  Matrix→Mesh: '{m2m_prefix}Hello'")
    print(f"  Mesh→Matrix: '{mesh2m_prefix}Hi there'")
    print()


def test_edge_cases():
    """Test edge cases and error handling."""
    print("=== Edge Case Tests ===\n")

    # Test with empty/None values
    config = {
        "meshtastic": {"prefix_enabled": True},
        "matrix": {"prefix_enabled": True},
    }

    # Empty names
    prefix1 = get_meshtastic_prefix(config, "")
    prefix2 = get_matrix_prefix(config, "", "", "")
    print("Empty names:")
    print(f"  Matrix→Mesh: '{prefix1}message'")
    print(f"  Mesh→Matrix: '{prefix2}message'")
    print()

    # Very long names
    long_name = "VeryLongUserNameThatExceedsNormalLimits"
    prefix3 = get_meshtastic_prefix(config, long_name)
    prefix4 = get_matrix_prefix(
        config, long_name, long_name[:3], "VeryLongMeshNetworkName"
    )
    print("Long names:")
    print(f"  Matrix→Mesh: '{prefix3}message'")
    print(f"  Mesh→Matrix: '{prefix4}message'")
    print()


if __name__ == "__main__":
    print("Testing Prefix Customization System\n")

    test_matrix_to_meshtastic_prefixes()
    test_meshtastic_to_matrix_prefixes()
    test_prefix_symmetry()
    test_edge_cases()

    print("All tests completed!")
