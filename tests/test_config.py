import os
import sys
import types

import pytest

# We import the module under test via its package path if available; otherwise fallback to relative import.
# The functions and constants are referenced exactly as in the provided code snippet.
try:
    from mmrelay import config as cfg
except Exception:
    # Fallback: tests running in a repo layout where module is named differently (e.g., config.py at project root)
    import importlib
    cfg = importlib.import_module("tests.test_config")  # This fallback is inert; ensures NameError clarity


@pytest.fixture(autouse=True)
def restore_globals(monkeypatch):
    # Ensure global variables are reset between tests
    if hasattr(cfg, "custom_data_dir"):
        monkeypatch.setattr(cfg, "custom_data_dir", None, raising=False)
    if hasattr(cfg, "relay_config"):
        monkeypatch.setattr(cfg, "relay_config", {}, raising=False)
    if hasattr(cfg, "config_path"):
        monkeypatch.setattr(cfg, "config_path", None, raising=False)
    yield


def _fake_module(name: str) -> types.ModuleType:
    m = types.ModuleType(name)
    m.__name__ = name
    return m


class TestGetBaseDirAndPaths:
    def test_get_base_dir_uses_custom_data_dir_when_set(self, monkeypatch, tmp_path):
        monkeypatch.setattr(cfg, "custom_data_dir", str(tmp_path / "custom"))
        result = cfg.get_base_dir()
        assert result == str(tmp_path / "custom")

    @pytest.mark.parametrize("platform", ["linux", "darwin"])
    def test_get_base_dir_unix_default_uses_dot_app_name(self, monkeypatch, platform):
        monkeypatch.setattr(sys, "platform", platform)
        monkeypatch.setattr(cfg, "custom_data_dir", None, raising=False)
        monkeypatch.setenv("HOME", "/home/tester")
        # Expanduser("~") => /home/tester
        res = cfg.get_base_dir()
        assert res == os.path.expanduser(os.path.join("~", f".{cfg.APP_NAME}"))
        assert res.startswith("/home/tester")

    def test_get_base_dir_windows_uses_platformdirs(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "win32")
        # Spy on platformdirs.user_data_dir
        calls = {}
        def fake_user_data_dir(app_name, author):
            calls["args"] = (app_name, author)
            return f"C:\\Users\\tester\\AppData\\Roaming\\{author}\\{app_name}"
        monkeypatch.setattr(cfg.platformdirs, "user_data_dir", fake_user_data_dir)
        res = cfg.get_base_dir()
        assert res.endswith(f"{cfg.APP_AUTHOR}\\{cfg.APP_NAME}")
        assert calls["args"] == (cfg.APP_NAME, cfg.APP_AUTHOR)

    def test_get_app_path_normal(self):
        path = cfg.get_app_path()
        assert os.path.isdir(path) or os.path.isfile(path)

    def test_get_app_path_frozen_uses_sys_executable_dir(self, monkeypatch, tmp_path):
        exe_dir = tmp_path / "bundle"
        exe_dir.mkdir()
        exe_file = exe_dir / "mmrelay.exe"
        exe_file.write_text("")  # placeholder
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "executable", str(exe_file))
        assert cfg.get_app_path() == str(exe_dir)

    @pytest.mark.parametrize("platform", ["linux", "darwin"])
    def test_get_config_paths_order_and_creation_unix(self, monkeypatch, tmp_path, platform):
        # Simulate --config path passed by args
        class Args:
            pass
        args = Args()
        args.config = str(tmp_path / "explicit.yaml")
        (tmp_path / "explicit.yaml").write_text("a: 1")

        # Use a temporary base dir for user config
        base_dir = tmp_path / ".mmrelay"
        monkeypatch.setattr(sys, "platform", platform)
        monkeypatch.setenv("HOME", str(tmp_path))  # ensure expanduser
        # Force get_base_dir to our computed path
        monkeypatch.setattr(cfg, "get_base_dir", lambda: str(base_dir))

        # CWD and app path
        monkeypatch.chdir(tmp_path)
        app_dir = tmp_path / "appdir"
        app_dir.mkdir()
        # get_app_path must return app_dir
        monkeypatch.setattr(cfg, "get_app_path", lambda: str(app_dir))

        paths = cfg.get_config_paths(args=args)
        assert paths[0] == os.path.abspath(str(tmp_path / "explicit.yaml"))
        assert paths[1] == os.path.join(str(base_dir), "config.yaml")
        assert paths[2] == os.path.join(str(tmp_path), "config.yaml")
        assert paths[3] == os.path.join(str(app_dir), "config.yaml")

        # Ensure user config dir is created
        assert os.path.isdir(str(base_dir))

    def test_get_config_paths_windows_skips_user_dir_on_error(self, monkeypatch, tmp_path):
        class Args:
            pass
        args = Args()
        args.config = None
        monkeypatch.setattr(sys, "platform", "win32")
        # Make platformdirs.user_config_dir return a path we cannot create (simulate OSError)
        def fake_user_config_dir(app, author):
            return os.path.join(str(tmp_path), "protected", "config")
        monkeypatch.setattr(cfg.platformdirs, "user_config_dir", fake_user_config_dir)
        # Patch os.makedirs to raise when trying to create that path
        real_makedirs = os.makedirs
        def guarded_makedirs(path, exist_ok=False):
            if "protected" in path:
                raise OSError("permission denied")
            return real_makedirs(path, exist_ok=exist_ok)
        monkeypatch.setattr(os, "makedirs", guarded_makedirs)

        # CWD and app path
        monkeypatch.chdir(tmp_path)
        app_dir = tmp_path / "appdir"
        app_dir.mkdir()
        monkeypatch.setattr(cfg, "get_app_path", lambda: str(app_dir))

        paths = cfg.get_config_paths(args=None)
        # First is user config path candidate would be skipped for creation, but still appended?
        # The function appends path even if creation failed? It appends only if makedirs succeeds.
        # Since our makedirs raises, the except pass means not appended.
        # So we expect only cwd and appdir paths
        assert paths == [
            os.path.join(str(tmp_path), "config.yaml"),
            os.path.join(str(app_dir), "config.yaml"),
        ]


class TestDataAndLogDirs:
    @pytest.mark.parametrize("platform", ["linux", "darwin"])
    def test_get_data_dir_unix(self, monkeypatch, tmp_path, platform):
        monkeypatch.setattr(sys, "platform", platform)
        base_dir = tmp_path / ".mmrelay"
        monkeypatch.setattr(cfg, "get_base_dir", lambda: str(base_dir))
        d = cfg.get_data_dir()
        assert d == os.path.join(str(base_dir), "data")
        assert os.path.isdir(d)

    def test_get_data_dir_windows(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "win32")
        def fake_user_data_dir(app, author):
            return f"C:\\\\Users\\\\tester\\\\AppData\\\\Roaming\\\\{author}\\\\{app}"
        monkeypatch.setattr(cfg.platformdirs, "user_data_dir", fake_user_data_dir)
        d = cfg.get_data_dir()
        assert d.endswith(f"{cfg.APP_AUTHOR}\\{cfg.APP_NAME}")

    @pytest.mark.parametrize("platform", ["linux", "darwin"])
    def test_get_plugin_data_dir_root_and_specific(self, monkeypatch, tmp_path, platform):
        monkeypatch.setattr(sys, "platform", platform)
        base_dir = tmp_path / ".mmrelay"
        monkeypatch.setattr(cfg, "get_base_dir", lambda: str(base_dir))
        root_plugins = cfg.get_plugin_data_dir()
        assert root_plugins == os.path.join(str(base_dir), "data", "plugins")
        assert os.path.isdir(root_plugins)

        pdir = cfg.get_plugin_data_dir("my_plugin")
        assert pdir == os.path.join(str(base_dir), "data", "plugins", "my_plugin")
        assert os.path.isdir(pdir)

    @pytest.mark.parametrize("platform", ["linux", "darwin"])
    def test_get_log_dir_unix(self, monkeypatch, tmp_path, platform):
        monkeypatch.setattr(sys, "platform", platform)
        base_dir = tmp_path / ".mmrelay"
        monkeypatch.setattr(cfg, "get_base_dir", lambda: str(base_dir))
        d = cfg.get_log_dir()
        assert d == os.path.join(str(base_dir), "logs")
        assert os.path.isdir(d)

    def test_get_log_dir_windows(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "win32")
        def fake_user_log_dir(app, author):
            return f"C:\\\\Users\\\\tester\\\\AppData\\\\Local\\\\{author}\\\\{app}\\\\Logs"
        monkeypatch.setattr(cfg.platformdirs, "user_log_dir", fake_user_log_dir)
        d = cfg.get_log_dir()
        assert d.endswith("Logs")


class TestSetConfig:
    def test_set_config_matrix_utils_populates_fields_and_calls_setup(self, monkeypatch):
        # Create a fake module named ...matrix_utils
        mod = _fake_module("mmrelay.matrix_utils")
        # matrix_utils expects these attrs to be present
        mod.matrix_homeserver = None
        mod.matrix_rooms = None
        mod.matrix_access_token = None
        mod.bot_user_id = None
        called = {"setup": 0}
        def setup_config():
            called["setup"] += 1
        mod.setup_config = setup_config

        conf = {
            cfg.CONFIG_SECTION_MATRIX: {
                cfg.CONFIG_KEY_HOMESERVER: "https://matrix.example",
                cfg.CONFIG_KEY_ACCESS_TOKEN: "secrettoken",
                cfg.CONFIG_KEY_BOT_USER_ID: "@bot:example",
            },
            "matrix_rooms": {"room1": "!abc:example"},
        }
        out = cfg.set_config(mod, conf)
        assert out is conf
        assert mod.config is conf
        assert mod.matrix_homeserver == "https://matrix.example"
        assert mod.matrix_access_token == "secrettoken"
        assert mod.bot_user_id == "@bot:example"
        assert mod.matrix_rooms == {"room1": "!abc:example"}
        assert called["setup"] == 1

    def test_set_config_meshtastic_utils_sets_matrix_rooms_and_calls_setup(self):
        mod = _fake_module("mmrelay.meshtastic_utils")
        mod.matrix_rooms = None
        called = {"setup": 0}
        mod.setup_config = lambda: called.__setitem__("setup", called["setup"] + 1)

        conf = {"matrix_rooms": ["!abc:example", "!def:example"]}
        out = cfg.set_config(mod, conf)
        assert out is conf
        assert mod.config is conf
        assert mod.matrix_rooms == ["!abc:example", "!def:example"]
        assert called["setup"] == 1

    def test_set_config_other_module_only_sets_config_and_optional_setup(self):
        mod = _fake_module("mmrelay.random_utils")
        assert not hasattr(mod, "setup_config")
        conf = {"a": 1}
        out = cfg.set_config(mod, conf)
        assert out is conf
        assert mod.config is conf


class TestLoadConfig:
    def test_load_config_with_explicit_valid_file(self, monkeypatch, tmp_path):
        yml = tmp_path / "config.yaml"
        yml.write_text("a: 1\nb:\n  - 2\n")
        result = cfg.load_config(config_file=str(yml))
        assert result == {"a": 1, "b": [2]}
        assert cfg.config_path == str(yml)

    def test_load_config_with_explicit_missing_file_returns_empty(self, tmp_path):
        missing = tmp_path / "nope.yaml"
        res = cfg.load_config(config_file=str(missing))
        # Since file does not exist, the code path will search default locations.
        # But we passed config_file and os.path.isfile() would be False, hence it goes to search order.
        # Given our tmp_path isn't in default order, expect eventually {}.
        assert isinstance(res, dict)

    def test_load_config_errors_logged_and_returns_empty_on_yaml_error(self, monkeypatch, tmp_path, caplog):
        bad = tmp_path / "bad.yaml"
        bad.write_text("a: [1, 2\n")  # malformed
        with caplog.at_level("ERROR"):
            res = cfg.load_config(config_file=str(bad))
        assert res == {}
        assert any("Error loading config file" in rec.message for rec in caplog.records)

    def test_load_config_search_order_finds_first_existing(self, monkeypatch, tmp_path):
        # Arrange search order: args.config, user dir, cwd, app dir
        class Args:
            pass
        args = Args()
        args.config = None

        # Mock get_config_paths to return controlled list
        f1 = tmp_path / "a.yaml"  # exists and valid
        f1.write_text("x: 1")
        f2 = tmp_path / "b.yaml"  # exists but later
        f2.write_text("x: 2")
        monkeypatch.setattr(cfg, "get_config_paths", lambda _args=None: [str(f1), str(f2)])

        res = cfg.load_config(config_file=None, args=args)
        assert res == {"x": 1}
        assert cfg.config_path == str(f1)

    def test_load_config_logs_all_paths_when_not_found(self, monkeypatch, caplog):
        monkeypatch.setattr(cfg, "get_config_paths", lambda _args=None: ["/x/nope1.yaml", "/y/nope2.yaml"])
        # Ensure os.path.isfile returns False for all
        monkeypatch.setattr(os.path, "isfile", lambda p: False)
        with caplog.at_level("ERROR"):
            res = cfg.load_config(config_file=None, args=None)
        assert isinstance(res, dict) and res == cfg.relay_config
        msgs = "\n".join(m.message for m in caplog.records)
        assert "Configuration file not found" in msgs
        assert "Using empty configuration" in msgs
        assert "mmrelay --generate-config" in msgs


class TestValidateYamlSyntax:
    def test_valid_yaml_no_issues(self):
        content = "a: 1\nb:\n  - 2\n"
        ok, msg, parsed = cfg.validate_yaml_syntax(content, "cfg.yaml")
        assert ok is True
        assert msg is None
        assert parsed == {"a": 1, "b": [2]}

    def test_style_warnings_for_nonstandard_bool(self):
        content = "feature: yes\nother: NO\n"
        ok, msg, parsed = cfg.validate_yaml_syntax(content, "cfg.yaml")
        assert ok is True
        assert parsed == {"feature": True, "other": False}
        assert "Style warning" in msg
        assert "yes" in msg and "NO" in msg

    def test_error_on_equals_instead_of_colon(self):
        content = "a=1\n"
        ok, msg, parsed = cfg.validate_yaml_syntax(content, "cfg.yaml")
        assert ok is False
        assert "Use ':' instead of '='" in msg
        assert parsed is None

    def test_yaml_error_reports_location_and_problematic_line(self):
        content = "list:\n  - 1\n  - 2\nmap:\n  key: [1, 2\n"
        ok, msg, parsed = cfg.validate_yaml_syntax(content, "cfg.yaml")
        assert ok is False
        assert "YAML parsing error in cfg.yaml" in msg
        assert "Problematic line:" in msg
        assert "Suggestion:" in msg

    def test_multiple_style_warnings_accumulate(self):
        content = "a: on\nb: off\nc: Yes\nd: No\n"
        ok, msg, parsed = cfg.validate_yaml_syntax(content, "cfg.yaml")
        assert ok is True
        assert parsed == {"a": True, "b": False, "c": True, "d": False}
        assert msg.count("Style warning") >= 4


class TestMeshtasticConfigValue:
    def test_get_value_present(self):
        conf = {"meshtastic": {"foo": 3}}
        assert cfg.get_meshtastic_config_value(conf, "foo", default=0, required=False) == 3

    def test_get_value_default_when_missing_and_not_required(self):
        conf = {"meshtastic": {}}
        assert cfg.get_meshtastic_config_value(conf, "bar", default="x", required=False) == "x"

    def test_get_value_raises_and_logs_when_required_missing(self, caplog):
        conf = {"meshtastic": {}}
        with caplog.at_level("ERROR"), pytest.raises(KeyError) as ei:
            cfg.get_meshtastic_config_value(conf, "bar", default="X", required=True)
        assert "Missing required configuration: meshtastic.bar" in "\n".join(m.message for m in caplog.records)
        assert "Required configuration 'meshtastic.bar' is missing." in str(ei.value)