#!/usr/bin/env python3

import unittest
from unittest.mock import Mock, patch

import pytest

import mmrelay.meshtastic_utils as mu
from mmrelay.constants.config import (
    DEFAULT_HEALTH_CHECK_ENABLED,
)
from mmrelay.constants.network import (
    CONNECTION_TYPE_BLE,
    CONNECTION_TYPE_TCP,
    DEFAULT_MESHTASTIC_OPERATION_TIMEOUT,
)


class TestGetConnectTimeProbeSettings(unittest.TestCase):
    @pytest.mark.usefixtures("reset_meshtastic_globals")
    def test_ble_returns_disabled(self):
        enabled, timeout = mu._get_connect_time_probe_settings(
            {"meshtastic": {}}, CONNECTION_TYPE_BLE
        )
        self.assertFalse(enabled)
        self.assertEqual(timeout, float(DEFAULT_MESHTASTIC_OPERATION_TIMEOUT))

    @pytest.mark.usefixtures("reset_meshtastic_globals")
    def test_none_config_returns_defaults(self):
        enabled, timeout = mu._get_connect_time_probe_settings(
            None, CONNECTION_TYPE_TCP
        )
        self.assertEqual(enabled, DEFAULT_HEALTH_CHECK_ENABLED)
        self.assertEqual(timeout, float(DEFAULT_MESHTASTIC_OPERATION_TIMEOUT))

    @pytest.mark.usefixtures("reset_meshtastic_globals")
    def test_non_dict_config_returns_defaults(self):
        enabled, timeout = mu._get_connect_time_probe_settings(
            "not_a_dict", CONNECTION_TYPE_TCP
        )
        self.assertEqual(enabled, DEFAULT_HEALTH_CHECK_ENABLED)
        self.assertEqual(timeout, float(DEFAULT_MESHTASTIC_OPERATION_TIMEOUT))

    @pytest.mark.usefixtures("reset_meshtastic_globals")
    def test_config_without_meshtastic_key_returns_defaults(self):
        enabled, timeout = mu._get_connect_time_probe_settings(
            {"other": {}}, CONNECTION_TYPE_TCP
        )
        self.assertEqual(enabled, DEFAULT_HEALTH_CHECK_ENABLED)
        self.assertEqual(timeout, float(DEFAULT_MESHTASTIC_OPERATION_TIMEOUT))

    @pytest.mark.usefixtures("reset_meshtastic_globals")
    def test_meshtastic_not_dict_returns_defaults(self):
        enabled, timeout = mu._get_connect_time_probe_settings(
            {"meshtastic": "not_a_dict"}, CONNECTION_TYPE_TCP
        )
        self.assertEqual(enabled, DEFAULT_HEALTH_CHECK_ENABLED)
        self.assertEqual(timeout, float(DEFAULT_MESHTASTIC_OPERATION_TIMEOUT))


class TestScheduleConnectTimeCalibrationProbe(unittest.TestCase):
    @pytest.mark.usefixtures("reset_meshtastic_globals")
    @patch("mmrelay.meshtastic_utils._get_connect_time_probe_settings")
    def test_client_no_local_node_returns_early(self, mock_settings):
        mock_settings.return_value = (True, 30.0)
        client = Mock(spec=["sendData"])
        client.sendData = Mock()
        del client.localNode

        mu._schedule_connect_time_calibration_probe(
            client,
            connection_type=CONNECTION_TYPE_TCP,
            active_config={"meshtastic": {}},
        )

    @pytest.mark.usefixtures("reset_meshtastic_globals")
    @patch("mmrelay.meshtastic_utils._get_connect_time_probe_settings")
    def test_client_no_send_data_returns_early(self, mock_settings):
        mock_settings.return_value = (True, 30.0)
        client = Mock(spec=["localNode"])
        client.localNode = Mock()
        del client.sendData

        mu._schedule_connect_time_calibration_probe(
            client,
            connection_type=CONNECTION_TYPE_TCP,
            active_config={"meshtastic": {}},
        )

    @pytest.mark.usefixtures("reset_meshtastic_globals")
    @patch("mmrelay.meshtastic_utils._get_connect_time_probe_settings")
    def test_send_data_not_callable_returns_early(self, mock_settings):
        mock_settings.return_value = (True, 30.0)
        client = Mock()
        client.localNode = Mock()
        client.sendData = "not_callable"

        mu._schedule_connect_time_calibration_probe(
            client,
            connection_type=CONNECTION_TYPE_TCP,
            active_config={"meshtastic": {}},
        )

    @pytest.mark.usefixtures("reset_meshtastic_globals")
    @patch("mmrelay.meshtastic_utils._submit_metadata_probe")
    @patch("mmrelay.meshtastic_utils._get_connect_time_probe_settings")
    def test_degraded_executor_returns_early(self, mock_settings, mock_submit):
        mock_settings.return_value = (True, 30.0)
        mock_submit.side_effect = mu.MetadataExecutorDegradedError("degraded")
        client = Mock()
        client.localNode = Mock()
        client.sendData = Mock()

        mu._schedule_connect_time_calibration_probe(
            client,
            connection_type=CONNECTION_TYPE_TCP,
            active_config={"meshtastic": {}},
        )

    @pytest.mark.usefixtures("reset_meshtastic_globals")
    @patch("mmrelay.meshtastic_utils._submit_metadata_probe")
    @patch("mmrelay.meshtastic_utils._get_connect_time_probe_settings")
    def test_runtime_error_returns_early(self, mock_settings, mock_submit):
        mock_settings.return_value = (True, 30.0)
        mock_submit.side_effect = RuntimeError("executor broken")
        client = Mock()
        client.localNode = Mock()
        client.sendData = Mock()

        mu._schedule_connect_time_calibration_probe(
            client,
            connection_type=CONNECTION_TYPE_TCP,
            active_config={"meshtastic": {}},
        )

    @pytest.mark.usefixtures("reset_meshtastic_globals")
    @patch("mmrelay.meshtastic_utils._submit_metadata_probe")
    @patch("mmrelay.meshtastic_utils._get_connect_time_probe_settings")
    def test_probe_future_none_returns_early(self, mock_settings, mock_submit):
        mock_settings.return_value = (True, 30.0)
        mock_submit.return_value = None
        client = Mock()
        client.localNode = Mock()
        client.sendData = Mock()

        mu._schedule_connect_time_calibration_probe(
            client,
            connection_type=CONNECTION_TYPE_TCP,
            active_config={"meshtastic": {}},
        )
