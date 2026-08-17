from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

import pytest

import mmrelay.log_utils as lu
from mmrelay.constants.app import APP_DISPLAY_NAME


@pytest.fixture(autouse=True)
def _reset_logging_state():
    original_config = lu.config
    original_cli_mode = lu._cli_mode
    lu._close_shared_file_handler()
    lu._registered_logger_names.clear()
    lu._component_attached_handlers.clear()
    lu._logger_config_generations.clear()
    lu._config_generation = 0
    lu.log_file_path = None
    try:
        yield
    finally:
        lu._close_shared_file_handler()
        for name in list(lu._registered_logger_names):
            logger = logging.getLogger(name)
            for handler in list(logger.handlers):
                logger.removeHandler(handler)
                handler.close()
        lu._registered_logger_names.clear()
        for name, handlers in lu._component_attached_handlers.items():
            logger = logging.getLogger(name)
            for handler in handlers:
                if handler in logger.handlers:
                    logger.removeHandler(handler)
        lu._component_attached_handlers.clear()
        lu._logger_config_generations.clear()
        lu._config_generation = 0
        lu.log_file_path = None
        lu.config = original_config
        lu._cli_mode = original_cli_mode


def _file_handler(logger: logging.Logger) -> RotatingFileHandler:
    handlers = [
        handler
        for handler in logger.handlers
        if isinstance(handler, RotatingFileHandler)
    ]
    assert len(handlers) == 1
    return handlers[0]


def test_resolve_log_file_honors_path_override_environment(tmp_path, monkeypatch):
    config_logfile = tmp_path / "config.log"
    env_logfile = tmp_path / "env.log"
    lu.config = {"logging": {"filename": str(config_logfile)}}
    monkeypatch.setenv("MMRELAY_LOG_PATH", str(env_logfile))

    result = lu._resolve_log_file(None)

    assert result == str(env_logfile.resolve())


def test_resolve_log_file_expands_user_path(tmp_path, monkeypatch):
    lu.config = {"logging": {"filename": "~/logs/mmrelay.log"}}
    monkeypatch.setenv("HOME", str(tmp_path))

    result = lu._resolve_log_file(None)

    assert result == str((tmp_path / "logs" / "mmrelay.log").resolve())


def test_loggers_share_single_rotating_file_handler(tmp_path):
    log_path = tmp_path / "mmrelay.log"
    lu.config = {
        "logging": {
            "log_to_file": True,
            "filename": str(log_path),
            "max_log_size": 4096,
            "backup_count": 2,
            "color_enabled": False,
        }
    }

    first = lu.get_logger("test_shared_file_first")
    second = lu.get_logger("test_shared_file_second")

    first_handler = _file_handler(first)
    second_handler = _file_handler(second)
    assert first_handler is second_handler

    first.info("message from first logger")
    second.info("message from second logger")
    first_handler.flush()
    contents = log_path.read_text(encoding="utf-8")
    assert "message from first logger" in contents
    assert "message from second logger" in contents


def test_shared_handler_keeps_all_loggers_on_active_file_after_rotation(tmp_path):
    log_path = tmp_path / "mmrelay.log"
    lu.config = {
        "logging": {
            "log_to_file": True,
            "filename": str(log_path),
            "max_log_size": 180,
            "backup_count": 3,
            "color_enabled": False,
        }
    }
    loggers = [lu.get_logger(f"rotation-{index}") for index in range(3)]
    shared = _file_handler(loggers[0])
    assert all(_file_handler(logger) is shared for logger in loggers)

    for index in range(20):
        loggers[index % len(loggers)].info("entry-%02d %s", index, "x" * 40)
    shared.flush()

    assert (tmp_path / "mmrelay.log.1").exists()
    assert "entry-19" in log_path.read_text(encoding="utf-8")
    assert all(_file_handler(logger) is shared for logger in loggers)


def test_main_log_path_is_not_advertised_when_file_handler_fails(tmp_path, monkeypatch):
    log_path = tmp_path / "mmrelay.log"
    lu.config = {"logging": {"log_to_file": True, "filename": str(log_path)}}

    def _raise_permission_error(*args, **kwargs):
        raise PermissionError("denied")

    monkeypatch.setattr(lu, "RotatingFileHandler", _raise_permission_error)
    logger = lu.get_logger(APP_DISPLAY_NAME)

    assert isinstance(logger, logging.Logger)
    assert lu.log_file_path is None
    assert not any(
        isinstance(handler, RotatingFileHandler) for handler in logger.handlers
    )


def test_refresh_reattaches_shared_handler_to_component_loggers(tmp_path):
    log_path = tmp_path / "mmrelay.log"
    lu.config = {
        "logging": {
            "log_to_file": True,
            "filename": str(log_path),
            "color_enabled": False,
            "debug": {"meshtastic": True},
        }
    }

    main_logger = lu.get_logger(APP_DISPLAY_NAME)
    lu.configure_component_debug_logging()
    component_logger = logging.getLogger("meshtastic.ble_interface")
    old_handler = _file_handler(component_logger)
    assert old_handler is _file_handler(main_logger)

    lu.refresh_all_loggers()

    new_handler = _file_handler(main_logger)
    assert new_handler is _file_handler(component_logger)
    assert new_handler is not old_handler
    component_logger.debug("component survives refresh")
    new_handler.flush()
    assert "component survives refresh" in log_path.read_text(encoding="utf-8")


def test_refresh_replaces_component_console_handler_without_duplicates(tmp_path):
    log_path = tmp_path / "mmrelay.log"
    lu.config = {
        "logging": {
            "log_to_file": True,
            "filename": str(log_path),
            "color_enabled": False,
            "debug": {"matrix_nio": True},
        }
    }

    main_logger = lu.get_logger(APP_DISPLAY_NAME)
    lu.configure_component_debug_logging()
    component_logger = logging.getLogger("nio")
    original_handlers = set(component_logger.handlers)
    assert original_handlers == set(main_logger.handlers)

    lu.refresh_all_loggers()

    assert set(component_logger.handlers) == set(main_logger.handlers)
    assert original_handlers.isdisjoint(component_logger.handlers)


def test_close_shared_file_handler_detaches_every_logger_and_resets_state(tmp_path):
    log_path = tmp_path / "mmrelay.log"
    lu.config = {
        "logging": {
            "log_to_file": True,
            "filename": str(log_path),
            "color_enabled": False,
            "debug": {"meshtastic": True},
        }
    }

    main_logger = lu.get_logger(APP_DISPLAY_NAME)
    lu.configure_component_debug_logging()
    component_logger = logging.getLogger("meshtastic.ble_interface")
    shared_handler = _file_handler(main_logger)
    assert shared_handler in component_logger.handlers
    assert lu._shared_file_handler_key is not None
    assert lu.log_file_path == str(log_path)

    lu._close_shared_file_handler()

    assert lu._shared_file_handler is None
    assert lu._shared_file_handler_key is None
    assert lu.log_file_path is None
    assert shared_handler not in main_logger.handlers
    assert shared_handler not in component_logger.handlers
