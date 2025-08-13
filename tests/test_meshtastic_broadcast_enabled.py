import importlib
import os
import sys

import pytest

# Ensure src is importable if project uses a src layout similar to the provided script
THIS_DIR = os.path.dirname(__file__)
CANDIDATE_SRC = os.path.join(THIS_DIR, "src")
if os.path.isdir(CANDIDATE_SRC) and CANDIDATE_SRC not in sys.path:
    sys.path.insert(0, CANDIDATE_SRC)

pytestmark = pytest.mark.unit


def _import_targets():
    """
    Helper to import the function and constant under test.
    The repository structure may vary; we follow the import path from the diff:
      - mmrelay.config.get_meshtastic_config_value
      - mmrelay.constants.config.DEFAULT_BROADCAST_ENABLED
    """
    cfg_mod = importlib.import_module("mmrelay.config")
    const_mod = importlib.import_module("mmrelay.constants.config")
    func = cfg_mod.get_meshtastic_config_value
    default_val = const_mod.DEFAULT_BROADCAST_ENABLED
    return func, default_val


def _base_config_no_broadcast():
    return {
        "meshtastic": {
            "connection_type": "serial",
            "serial_port": "/dev/ttyUSB0",
        }
    }


def _base_config_with(value):
    cfg = _base_config_no_broadcast()
    cfg["meshtastic"]["broadcast_enabled"] = value
    return cfg


class TestBroadcastEnabledConfig:
    def test_missing_key_required_false_returns_default(self):
        get_val, DEFAULT = _import_targets()
        cfg = _base_config_no_broadcast()

        # Per PR: missing broadcast_enabled should not raise when required=False
        result = get_val(cfg, "broadcast_enabled", DEFAULT, required=False)
        assert (
            result == DEFAULT
        ), "Expected missing broadcast_enabled to return the default value"

    def test_missing_key_without_required_param_uses_passed_default(self):
        get_val, DEFAULT = _import_targets()
        cfg = _base_config_no_broadcast()
        custom_default = not DEFAULT  # flip the default to detect which was used
        result = get_val(cfg, "broadcast_enabled", custom_default)
        assert (
            result == custom_default
        ), "Expected missing key to use the provided default when required not explicitly True"

    def test_missing_key_with_required_true_raises(self):
        get_val, DEFAULT = _import_targets()
        cfg = _base_config_no_broadcast()
        with pytest.raises(Exception):  # noqa
            # We don't tie to a specific exception type unless the code exports one;
            # if the implementation uses KeyError/ValueError, this assertion still holds.
            get_val(cfg, "broadcast_enabled", DEFAULT, required=True)

    def test_present_true_is_returned(self):
        get_val, DEFAULT = _import_targets()
        cfg = _base_config_with(True)
        assert get_val(cfg, "broadcast_enabled", DEFAULT) is True

    def test_present_false_is_returned(self):
        get_val, DEFAULT = _import_targets()
        cfg = _base_config_with(False)
        assert get_val(cfg, "broadcast_enabled", DEFAULT) is False

    def test_unexpected_string_value_either_converted_or_raises(self):
        get_val, DEFAULT = _import_targets()
        cfg = _base_config_with("true")
        try:
            result = get_val(cfg, "broadcast_enabled", DEFAULT)
        except Exception:
            # If the implementation validates types strictly, this is acceptable.
            return
        # Otherwise, if it allows pass-through, assert a truthy conversion.
        # We accept either True or the literal "true" if the function does not coerce.
        assert result in (True, "true")

    def test_missing_meshtastic_section_required_false_returns_default(self):
        get_val, DEFAULT = _import_targets()
        cfg = {}  # no meshtastic section at all
        result = get_val(cfg, "broadcast_enabled", DEFAULT, required=False)
        assert result == DEFAULT

    def test_missing_meshtastic_section_required_true_raises(self):
        get_val, DEFAULT = _import_targets()
        cfg = {}
        with pytest.raises(Exception):  # noqa
            get_val(cfg, "broadcast_enabled", DEFAULT, required=True)

    def test_default_constant_is_boolean(self):
        _, DEFAULT = _import_targets()
        assert isinstance(
            DEFAULT, bool
        ), "DEFAULT_BROADCAST_ENABLED should be a boolean"

    def test_override_default_parameter_is_respected(self):
        get_val, DEFAULT = _import_targets()
        cfg = _base_config_no_broadcast()
        override = not DEFAULT
        result = get_val(cfg, "broadcast_enabled", override, required=False)
        assert (
            result == override
        ), "Explicit default passed to function should be returned when key is absent and required=False"
