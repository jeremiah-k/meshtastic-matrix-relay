import os
import sys
import unittest
from unittest.mock import mock_open, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mmrelay.cli import check_config


def _base_config(health_check_value=None):
    config = {
        "matrix": {
            "homeserver": "https://matrix.org",
            "access_token": "test_token",
            "bot_user_id": "@bot:matrix.org",
        },
        "matrix_rooms": [{"id": "!room:matrix.org", "meshtastic_channel": 0}],
        "meshtastic": {
            "connection_type": "serial",
            "serial_port": "/dev/ttyUSB0",
        },
    }
    if health_check_value is not None:
        config["meshtastic"]["health_check"] = health_check_value
    return config


class TestHealthCheckValidation(unittest.TestCase):
    """Tests for health_check validation in check_config (lines 1517-1550)."""

    def _run_check_config(self, config):
        with patch("mmrelay.cli._validate_credentials_json", return_value=False):
            with patch("mmrelay.config.os.makedirs"):
                with patch("mmrelay.cli._validate_e2ee_config", return_value=True):
                    with patch("mmrelay.cli._print_unified_e2ee_analysis"):
                        with patch(
                            "mmrelay.e2ee_utils.get_e2ee_status",
                            return_value={"platform_supported": True},
                        ):
                            with patch("mmrelay.cli.os.path.isfile", return_value=True):
                                with patch("builtins.open", mock_open()):
                                    with patch(
                                        "mmrelay.cli.validate_yaml_syntax",
                                        return_value=(True, None, config),
                                    ):
                                        with patch(
                                            "sys.argv",
                                            ["mmrelay", "--config", "/test/cfg.yaml"],
                                        ):
                                            with patch("builtins.print"):
                                                return check_config()

    def test_health_check_not_a_dict_returns_false(self):
        config = _base_config(health_check_value="not_a_dict")
        self.assertFalse(self._run_check_config(config))

    def test_health_check_is_list_returns_false(self):
        config = _base_config(health_check_value=["bad"])
        self.assertFalse(self._run_check_config(config))

    def test_connect_probe_enabled_not_bool_returns_false(self):
        config = _base_config(health_check_value={"connect_probe_enabled": "true"})
        self.assertFalse(self._run_check_config(config))

    def test_connect_probe_enabled_int_returns_false(self):
        config = _base_config(health_check_value={"connect_probe_enabled": 1})
        self.assertFalse(self._run_check_config(config))

    def test_probe_timeout_is_bool_returns_false(self):
        config = _base_config(health_check_value={"probe_timeout": True})
        self.assertFalse(self._run_check_config(config))

    def test_probe_timeout_is_string_returns_false(self):
        config = _base_config(health_check_value={"probe_timeout": "10"})
        self.assertFalse(self._run_check_config(config))

    def test_probe_timeout_is_inf_returns_false(self):
        config = _base_config(health_check_value={"probe_timeout": float("inf")})
        self.assertFalse(self._run_check_config(config))

    def test_probe_timeout_is_nan_returns_false(self):
        config = _base_config(health_check_value={"probe_timeout": float("nan")})
        self.assertFalse(self._run_check_config(config))

    def test_probe_timeout_negative_returns_false(self):
        config = _base_config(health_check_value={"probe_timeout": -5})
        self.assertFalse(self._run_check_config(config))

    def test_probe_timeout_zero_returns_false(self):
        config = _base_config(health_check_value={"probe_timeout": 0})
        self.assertFalse(self._run_check_config(config))

    def test_valid_health_check_returns_true(self):
        config = _base_config(
            health_check_value={
                "connect_probe_enabled": True,
                "probe_timeout": 10,
            }
        )
        self.assertTrue(self._run_check_config(config))

    def test_no_health_check_returns_true(self):
        config = _base_config()
        self.assertTrue(self._run_check_config(config))

    def test_health_check_empty_dict_returns_true(self):
        config = _base_config(health_check_value={})
        self.assertTrue(self._run_check_config(config))

    def test_probe_timeout_float_returns_true(self):
        config = _base_config(health_check_value={"probe_timeout": 5.5})
        self.assertTrue(self._run_check_config(config))

    def test_probe_timeout_small_positive_returns_true(self):
        config = _base_config(health_check_value={"probe_timeout": 0.001})
        self.assertTrue(self._run_check_config(config))

    def test_error_message_health_check_not_dict(self):
        config = _base_config(health_check_value="not_a_dict")
        with patch("builtins.print") as mock_print:
            with patch("mmrelay.cli._validate_credentials_json", return_value=False):
                with patch("mmrelay.config.os.makedirs"):
                    with patch("mmrelay.cli._validate_e2ee_config", return_value=True):
                        with patch("mmrelay.cli._print_unified_e2ee_analysis"):
                            with patch(
                                "mmrelay.e2ee_utils.get_e2ee_status",
                                return_value={"platform_supported": True},
                            ):
                                with patch(
                                    "mmrelay.cli.os.path.isfile", return_value=True
                                ):
                                    with patch("builtins.open", mock_open()):
                                        with patch(
                                            "mmrelay.cli.validate_yaml_syntax",
                                            return_value=(True, None, config),
                                        ):
                                            with patch(
                                                "sys.argv",
                                                [
                                                    "mmrelay",
                                                    "--config",
                                                    "/test/cfg.yaml",
                                                ],
                                            ):
                                                result = check_config()
                                                self.assertFalse(result)
                                                mock_print.assert_any_call(
                                                    "Error: 'meshtastic.health_check' must be a mapping (YAML object)"
                                                )

    def test_error_message_connect_probe_enabled_wrong_type(self):
        config = _base_config(health_check_value={"connect_probe_enabled": "true"})
        with patch("builtins.print") as mock_print:
            with patch("mmrelay.cli._validate_credentials_json", return_value=False):
                with patch("mmrelay.config.os.makedirs"):
                    with patch("mmrelay.cli._validate_e2ee_config", return_value=True):
                        with patch("mmrelay.cli._print_unified_e2ee_analysis"):
                            with patch(
                                "mmrelay.e2ee_utils.get_e2ee_status",
                                return_value={"platform_supported": True},
                            ):
                                with patch(
                                    "mmrelay.cli.os.path.isfile", return_value=True
                                ):
                                    with patch("builtins.open", mock_open()):
                                        with patch(
                                            "mmrelay.cli.validate_yaml_syntax",
                                            return_value=(True, None, config),
                                        ):
                                            with patch(
                                                "sys.argv",
                                                [
                                                    "mmrelay",
                                                    "--config",
                                                    "/test/cfg.yaml",
                                                ],
                                            ):
                                                result = check_config()
                                                self.assertFalse(result)
                                                mock_print.assert_any_call(
                                                    "Error: 'meshtastic.health_check.connect_probe_enabled' "
                                                    "must be of type bool, got: true"
                                                )

    def test_error_message_probe_timeout_invalid(self):
        config = _base_config(health_check_value={"probe_timeout": -1})
        with patch("builtins.print") as mock_print:
            with patch("mmrelay.cli._validate_credentials_json", return_value=False):
                with patch("mmrelay.config.os.makedirs"):
                    with patch("mmrelay.cli._validate_e2ee_config", return_value=True):
                        with patch("mmrelay.cli._print_unified_e2ee_analysis"):
                            with patch(
                                "mmrelay.e2ee_utils.get_e2ee_status",
                                return_value={"platform_supported": True},
                            ):
                                with patch(
                                    "mmrelay.cli.os.path.isfile", return_value=True
                                ):
                                    with patch("builtins.open", mock_open()):
                                        with patch(
                                            "mmrelay.cli.validate_yaml_syntax",
                                            return_value=(True, None, config),
                                        ):
                                            with patch(
                                                "sys.argv",
                                                [
                                                    "mmrelay",
                                                    "--config",
                                                    "/test/cfg.yaml",
                                                ],
                                            ):
                                                result = check_config()
                                                self.assertFalse(result)
                                                mock_print.assert_any_call(
                                                    "Error: 'meshtastic.health_check.probe_timeout' "
                                                    "must be a positive finite number, "
                                                    "got: -1"
                                                )


if __name__ == "__main__":
    unittest.main()
