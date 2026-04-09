from unittest.mock import MagicMock, patch

import pytest

import mmrelay.meshtastic_utils as mu


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestResolvePluginTimeout:
    def test_timeout_with_bool_value(self):
        from mmrelay.meshtastic.plugins import _resolve_plugin_timeout

        cfg = {"meshtastic": {"plugin_timeout": True}}
        result = _resolve_plugin_timeout(cfg)
        assert isinstance(result, float)
        assert result > 0

    def test_timeout_with_non_positive_value(self):
        from mmrelay.meshtastic.plugins import _resolve_plugin_timeout

        cfg = {"meshtastic": {"plugin_timeout": -5}}
        result = _resolve_plugin_timeout(cfg)
        assert result > 0

    def test_timeout_with_none_cfg(self):
        from mmrelay.meshtastic.plugins import _resolve_plugin_timeout

        result = _resolve_plugin_timeout(None)
        assert result > 0


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestResolvePluginResult:
    def test_awaitable_submit_returns_none(self):
        from mmrelay.meshtastic.plugins import _resolve_plugin_result

        plugin = MagicMock()
        plugin.plugin_name = "test_plugin"
        loop = MagicMock()

        async def handler():
            return True

        coro = handler()
        try:
            with patch.object(mu, "_submit_coro", return_value=None):
                result = _resolve_plugin_result(coro, plugin, 5.0, loop)
            assert result is False
        finally:
            coro.close()

    def test_sync_false_result(self):
        from mmrelay.meshtastic.plugins import _resolve_plugin_result

        plugin = MagicMock()
        plugin.plugin_name = "test_plugin"
        loop = MagicMock()
        result = _resolve_plugin_result(False, plugin, 5.0, loop)
        assert result is False

    def test_awaitable_timeout_returns_true(self):
        from concurrent.futures import TimeoutError as FuturesTimeoutError

        from mmrelay.meshtastic.plugins import _resolve_plugin_result

        plugin = MagicMock()
        plugin.plugin_name = "test_plugin"
        loop = MagicMock()

        fake_awaitable = MagicMock()
        fake_awaitable.__await__ = lambda self: iter([None])

        with patch.object(mu, "_submit_coro", return_value=MagicMock()):
            with patch.object(mu, "_wait_for_result", side_effect=FuturesTimeoutError):
                result = _resolve_plugin_result(fake_awaitable, plugin, 1.0, loop)
        assert result is True


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestRunMeshtasticPlugins:
    def test_no_plugins_returns_false(self):
        from mmrelay.meshtastic.plugins import _run_meshtastic_plugins

        with patch("mmrelay.plugin_loader.load_plugins", return_value=[]):
            result = _run_meshtastic_plugins(
                packet={},
                formatted_message="test",
                longname="user",
                meshnet_name="mesh",
                loop=MagicMock(),
                cfg={},
            )
        assert result is False

    def test_plugin_handles_message(self):
        from mmrelay.meshtastic.plugins import _run_meshtastic_plugins

        plugin = MagicMock()
        plugin.plugin_name = "test_plugin"
        plugin.handle_meshtastic_message.return_value = True

        with patch("mmrelay.plugin_loader.load_plugins", return_value=[plugin]):
            with patch.object(mu, "_resolve_plugin_result", return_value=True):
                result = _run_meshtastic_plugins(
                    packet={},
                    formatted_message="test",
                    longname="user",
                    meshnet_name="mesh",
                    loop=MagicMock(),
                    cfg={},
                )
        assert result is True

    def test_plugin_does_not_handle(self):
        from mmrelay.meshtastic.plugins import _run_meshtastic_plugins

        plugin = MagicMock()
        plugin.plugin_name = "test_plugin"
        plugin.handle_meshtastic_message.return_value = False

        with patch("mmrelay.plugin_loader.load_plugins", return_value=[plugin]):
            with patch.object(mu, "_resolve_plugin_result", return_value=False):
                result = _run_meshtastic_plugins(
                    packet={},
                    formatted_message="test",
                    longname="user",
                    meshnet_name="mesh",
                    loop=MagicMock(),
                    cfg={},
                )
        assert result is False

    def test_keyword_args_mode(self):
        from mmrelay.meshtastic.plugins import _run_meshtastic_plugins

        plugin = MagicMock()
        plugin.plugin_name = "test_plugin"
        plugin.handle_meshtastic_message.return_value = True

        with patch("mmrelay.plugin_loader.load_plugins", return_value=[plugin]):
            with patch.object(mu, "_resolve_plugin_result", return_value=True):
                _run_meshtastic_plugins(
                    packet={},
                    formatted_message="test",
                    longname="user",
                    meshnet_name="mesh",
                    loop=MagicMock(),
                    cfg={},
                    use_keyword_args=True,
                    log_with_portnum=True,
                    portnum="TELEMETRY_APP",
                )
        plugin.handle_meshtastic_message.assert_called_with(
            {},
            formatted_message="test",
            longname="user",
            meshnet_name="mesh",
        )

    def test_plugin_exception_continues(self):
        from mmrelay.meshtastic.plugins import _run_meshtastic_plugins

        bad_plugin = MagicMock()
        bad_plugin.plugin_name = "bad_plugin"
        bad_plugin.handle_meshtastic_message.side_effect = RuntimeError("fail")

        good_plugin = MagicMock()
        good_plugin.plugin_name = "good_plugin"
        good_plugin.handle_meshtastic_message.return_value = True

        with patch(
            "mmrelay.plugin_loader.load_plugins",
            return_value=[bad_plugin, good_plugin],
        ):
            with patch.object(mu, "_resolve_plugin_result", return_value=True):
                result = _run_meshtastic_plugins(
                    packet={},
                    formatted_message="test",
                    longname="user",
                    meshnet_name="mesh",
                    loop=MagicMock(),
                    cfg={},
                )
        assert result is True

    def test_timeout_with_attribute_error(self):
        from mmrelay.meshtastic.plugins import _resolve_plugin_timeout

        cfg = {"meshtastic": None}
        result = _resolve_plugin_timeout(cfg)
        assert result > 0
