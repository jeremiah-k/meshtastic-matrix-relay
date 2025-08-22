"""
Test suite for constants validation.

This module tests that all constants are properly defined, have valid values,
and maintain logical relationships with each other.
"""

from mmrelay.constants import (
    app,
    config,
    database,
    formats,
    messages,
    network,
    queue,
)


class TestConstantsValidity:
    """Test that all constants are properly defined and valid."""

    def test_config_keys_are_strings(self):
        """
        Verifies that all configuration keys in the config module are non-empty strings without leading or trailing whitespace, and that at least 20 such keys are defined.
        """
        config_keys = [
            getattr(config, attr)
            for attr in dir(config)
            if attr.startswith("CONFIG_KEY_")
        ]
        assert len(config_keys) >= 20  # We have 39 defined
        for key in config_keys:
            assert isinstance(key, str)
            assert len(key) > 0
            assert key.strip() == key  # No leading/trailing whitespace

    def test_config_sections_are_strings(self):
        """
        Verify that all configuration section constants in the config module are non-empty strings without leading or trailing whitespace, and that at least five such sections are defined.
        """
        config_sections = [
            getattr(config, attr)
            for attr in dir(config)
            if attr.startswith("CONFIG_SECTION_")
        ]
        assert len(config_sections) >= 5  # We have multiple sections
        for section in config_sections:
            assert isinstance(section, str)
            assert len(section) > 0
            assert section.strip() == section

    def test_connection_types_consistency(self):
        """
        Validates that connection type constants are non-empty strings, mostly unique, and that the legacy 'network' type is a valid alias for TCP.
        """
        types = [
            network.CONNECTION_TYPE_TCP,
            network.CONNECTION_TYPE_SERIAL,
            network.CONNECTION_TYPE_BLE,
            network.CONNECTION_TYPE_NETWORK,
        ]

        # All should be strings
        for conn_type in types:
            assert isinstance(conn_type, str)
            assert len(conn_type) > 0

        # No duplicates except network (legacy alias)
        unique_types = set(types)
        assert len(unique_types) >= 3  # tcp, serial, ble (network may be alias)

        # Network should be alias for tcp (legacy support)
        assert network.CONNECTION_TYPE_NETWORK in ["tcp", "network"]

    def test_queue_constants_relationships(self):
        """
        Verify that queue-related constants maintain logical ordering and represent reasonable proportions of the maximum queue size.
        """
        assert queue.QUEUE_HIGH_WATER_MARK < queue.MAX_QUEUE_SIZE
        assert queue.QUEUE_MEDIUM_WATER_MARK < queue.QUEUE_HIGH_WATER_MARK
        assert queue.QUEUE_LOG_THRESHOLD >= 1
        assert queue.DEFAULT_MESSAGE_DELAY >= network.MINIMUM_MESSAGE_DELAY

        # Water marks should be reasonable percentages
        high_percentage = queue.QUEUE_HIGH_WATER_MARK / queue.MAX_QUEUE_SIZE
        medium_percentage = queue.QUEUE_MEDIUM_WATER_MARK / queue.MAX_QUEUE_SIZE
        assert 0.5 <= high_percentage <= 0.9  # 50-90%
        assert 0.3 <= medium_percentage <= 0.7  # 30-70%

    def test_database_limits_reasonable(self):
        """
        Verify that database-related constants have values within sensible and safe numeric ranges.
        """
        assert 10 <= database.DEFAULT_MSGS_TO_KEEP <= 10000
        assert 10 <= database.DEFAULT_MAX_DATA_ROWS_PER_NODE_BASE <= 1000
        assert database.DEFAULT_TEXT_TRUNCATION_LENGTH >= 10
        assert database.DEFAULT_DISTANCE_KM_FALLBACK > 0
        assert database.DEFAULT_RADIUS_KM > 0
        assert database.PROGRESS_TOTAL_STEPS > 0
        assert database.PROGRESS_COMPLETE <= database.PROGRESS_TOTAL_STEPS

    def test_app_metadata_valid(self):
        """
        Verify that application metadata constants are non-empty strings and that the Windows platform identifier is set to "win32".
        """
        assert isinstance(app.APP_NAME, str)
        assert len(app.APP_NAME) > 0
        assert isinstance(app.APP_DISPLAY_NAME, str)
        assert len(app.APP_DISPLAY_NAME) > 0
        assert app.WINDOWS_PLATFORM == "win32"

    def test_format_constants_valid(self):
        """
        Validates that message format prefix constants are non-empty strings containing format placeholders.

        Ensures that `DEFAULT_MESHTASTIC_PREFIX` and `DEFAULT_MATRIX_PREFIX` in the `formats` module are valid strings and include curly brace placeholders for formatting.
        """
        assert isinstance(formats.DEFAULT_MESHTASTIC_PREFIX, str)
        assert isinstance(formats.DEFAULT_MATRIX_PREFIX, str)
        assert len(formats.DEFAULT_MESHTASTIC_PREFIX) > 0
        assert len(formats.DEFAULT_MATRIX_PREFIX) > 0

        # Should contain format placeholders
        assert "{" in formats.DEFAULT_MESHTASTIC_PREFIX
        assert "{" in formats.DEFAULT_MATRIX_PREFIX

    def test_network_timeouts_reasonable(self):
        """
        Verify that network timeout and error code constants have reasonable and expected values.
        """
        assert network.DEFAULT_BACKOFF_TIME > 0
        assert network.MINIMUM_MESSAGE_DELAY >= 0
        assert network.MILLISECONDS_PER_SECOND == 1000
        assert network.ERRNO_BAD_FILE_DESCRIPTOR == 9

    def test_message_constants_valid(self):
        """
        Validate that message-related constants have positive or non-negative values and correct multipliers, and that the emoji flag value is defined and non-negative in the formats module.
        """
        assert messages.DEFAULT_LOG_SIZE_MB > 0
        assert messages.DEFAULT_LOG_BACKUP_COUNT >= 0
        assert messages.LOG_SIZE_BYTES_MULTIPLIER == 1024 * 1024
        assert messages.PORTNUM_NUMERIC_VALUE >= 0
        assert messages.DEFAULT_CHANNEL_VALUE >= 0

        # EMOJI_FLAG_VALUE is in formats module, not messages
        assert formats.EMOJI_FLAG_VALUE >= 0


class TestConstantsImports:
    """Test that constants can be imported correctly."""

    def test_constants_package_imports(self):
        """
        Verify that all submodules in the mmrelay.constants package can be imported without ImportError.
        """
        # These should not raise ImportError
        from mmrelay.constants import app  # noqa: F401
        from mmrelay.constants import config  # noqa: F401
        from mmrelay.constants import database  # noqa: F401
        from mmrelay.constants import formats  # noqa: F401
        from mmrelay.constants import messages  # noqa: F401
        from mmrelay.constants import network  # noqa: F401
        from mmrelay.constants import queue  # noqa: F401

    def test_common_constants_available_from_init(self):
        """
        Verify that commonly used constants are accessible directly from the mmrelay.constants package's __init__.py.
        """
        from mmrelay.constants import (  # noqa: F401
            APP_NAME,
            CONFIG_SECTION_MATRIX,
            CONFIG_SECTION_MESHTASTIC,
            DEFAULT_MATRIX_PREFIX,
            DEFAULT_MESHTASTIC_PREFIX,
            DEFAULT_MESSAGE_DELAY,
            MAX_QUEUE_SIZE,
        )

    def test_constants_used_in_codebase_are_accessible(self):
        """
        Verify that a representative sample of constants used in the main codebase are importable and have the expected types.
        """
        # Test a sampling of constants that are imported in the main codebase
        from mmrelay.constants.config import (
            CONFIG_SECTION_MATRIX,
            E2EE_KEY_SHARING_DELAY_SECONDS,
        )
        from mmrelay.constants.formats import TEXT_MESSAGE_APP
        from mmrelay.constants.network import CONNECTION_TYPE_TCP
        from mmrelay.constants.queue import DEFAULT_MESSAGE_DELAY

        assert isinstance(CONFIG_SECTION_MATRIX, str)
        assert isinstance(CONNECTION_TYPE_TCP, str)
        assert isinstance(DEFAULT_MESSAGE_DELAY, (int, float))
        assert isinstance(TEXT_MESSAGE_APP, (int, str))
        assert isinstance(E2EE_KEY_SHARING_DELAY_SECONDS, int)


class TestConstantsConsistency:
    """Test consistency between related constants."""

    def test_config_keys_match_expected_patterns(self):
        """
        Verify that configuration key values in the config module follow consistent naming patterns, requiring them to be lowercase or contain underscores.
        """
        config_keys = [
            attr
            for attr in dir(config)
            if attr.startswith("CONFIG_KEY_") and not attr.startswith("__")
        ]

        for key_name in config_keys:
            key_value = getattr(config, key_name)
            # Key values should be lowercase with underscores
            assert key_value.islower() or "_" in key_value

    def test_connection_types_used_consistently(self):
        """
        Verify that connection type constants are lowercase and match expected string values.

        Ensures that TCP, serial, and BLE connection type constants in the network module are consistently defined as lowercase strings and correspond to their expected identifiers.
        """
        # Test that connection types are defined and used consistently
        tcp_type = network.CONNECTION_TYPE_TCP
        serial_type = network.CONNECTION_TYPE_SERIAL
        ble_type = network.CONNECTION_TYPE_BLE

        # Should be lowercase
        assert tcp_type.islower()
        assert serial_type.islower()
        assert ble_type.islower()

        # Should be single words or common abbreviations
        assert tcp_type in ["tcp", "network"]
        assert serial_type == "serial"
        assert ble_type == "ble"

    def test_queue_size_calculations_correct(self):
        """
        Verify that queue water marks are correctly calculated as integer percentages of the maximum queue size.

        Asserts that `QUEUE_HIGH_WATER_MARK` is 75% and `QUEUE_MEDIUM_WATER_MARK` is 50% of `MAX_QUEUE_SIZE`.
        """
        # Test that water marks are calculated as percentages
        max_size = queue.MAX_QUEUE_SIZE
        high_mark = queue.QUEUE_HIGH_WATER_MARK
        medium_mark = queue.QUEUE_MEDIUM_WATER_MARK

        # Should be calculated as percentages (allowing for int conversion)
        expected_high = int(max_size * 0.75)
        expected_medium = int(max_size * 0.50)

        assert high_mark == expected_high
        assert medium_mark == expected_medium

    def test_default_values_are_sensible(self):
        """
        Verify that default values for message delay, queue size, database retention, and log size fall within reasonable ranges suitable for production environments.
        """
        # Message delay should be at least the firmware minimum
        assert queue.DEFAULT_MESSAGE_DELAY >= network.MINIMUM_MESSAGE_DELAY

        # Queue size should be reasonable for memory usage
        assert 100 <= queue.MAX_QUEUE_SIZE <= 10000

        # Database retention should be reasonable
        assert 50 <= database.DEFAULT_MSGS_TO_KEEP <= 5000

        # Log size should be reasonable
        assert 1 <= messages.DEFAULT_LOG_SIZE_MB <= 100

        # E2EE key sharing delay should be reasonable
        assert 1 <= config.E2EE_KEY_SHARING_DELAY_SECONDS <= 30
