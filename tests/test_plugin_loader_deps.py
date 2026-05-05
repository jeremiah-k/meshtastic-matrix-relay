"""Tests for plugin loader: Dependency installation."""

# Decomposed from test_plugin_loader.py

import json
import os
import shutil
import subprocess  # nosec B404 - tests assert subprocess error handling
import sys
import tempfile
import unittest
from typing import Any
from unittest.mock import patch

import mmrelay.plugin_loader as pl
from mmrelay.plugin_loader import _install_requirements_for_repo
from tests._plugin_loader_helpers import BaseGitTest


class TestDependencyInstallation(BaseGitTest):
    """Test cases for dependency installation functionality."""

    def setUp(self):
        """Set up mocks and temporary directory."""
        super().setUp()
        self.temp_dir = tempfile.mkdtemp()
        self.repo_path = os.path.join(self.temp_dir, "test-plugin")
        self.requirements_path = os.path.join(self.repo_path, "requirements.txt")
        self.deps_dir = os.path.join(self.temp_dir, "deps")
        os.makedirs(self.deps_dir, exist_ok=True)
        os.makedirs(self.repo_path, exist_ok=True)
        with open(self.requirements_path, "w") as f:
            f.write("requests==2.28.0\n")

        # Prevent tests from interfering with each other
        self.pl_patcher = patch("mmrelay.plugin_loader.config", new=None)
        self.pl_patcher.start()
        self.addCleanup(self.pl_patcher.stop)

    def tearDown(self):
        """Clean up temporary directory."""
        shutil.rmtree(self.temp_dir)
        super().tearDown()

    @patch("mmrelay.plugin_loader.logger")
    @patch("mmrelay.plugin_loader._run")
    @patch("mmrelay.plugin_loader._collect_requirements")
    def test_install_community_requirements_warns_once_when_enabled(
        self, mock_collect, mock_run, mock_logger
    ):
        """Community dependency auto-install should emit the risk warning once."""
        mock_collect.return_value = ["requests==2.28.0"]
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
        with patch.object(pl, "_community_dep_install_warning_logged", new=False):
            _install_requirements_for_repo(
                self.repo_path,
                "test-plugin",
                plugin_type=pl.PLUGIN_TYPE_COMMUNITY,
            )
            _install_requirements_for_repo(
                self.repo_path,
                "test-plugin",
                plugin_type=pl.PLUGIN_TYPE_COMMUNITY,
            )

        risk_warning = (
            "Community plugin dependencies execute arbitrary code and are unsafe"
        )
        warning_calls = [
            call_args
            for call_args in mock_logger.warning.call_args_list
            if call_args.args and call_args.args[0] == risk_warning
        ]
        self.assertEqual(len(warning_calls), 1)

    @patch("mmrelay.plugin_loader._collect_requirements")
    def test_install_plugin_requirements_no_file(self, mock_collect):
        """Test dependency installation when requirements file doesn't exist."""
        os.remove(self.requirements_path)
        _install_requirements_for_repo(self.repo_path, "test-plugin")
        mock_collect.assert_not_called()

    @patch("mmrelay.plugin_loader.logger")
    @patch("mmrelay.plugin_loader._run")
    @patch("mmrelay.plugin_loader._filter_risky_requirement_lines")
    @patch("mmrelay.plugin_loader._collect_requirements")
    @patch("shutil.which", return_value=None)
    @patch("mmrelay.plugin_loader.tempfile.NamedTemporaryFile")
    @patch("mmrelay.plugin_loader.os.unlink")
    def test_install_plugin_requirements_pip_in_venv(
        self,
        mock_unlink,
        mock_temp_file,
        mock_which,
        mock_collect,
        mock_filter,
        mock_run,
        mock_logger,
    ):
        """Test dependency installation with pip in virtual environment."""
        mock_collect.return_value = ["requests==2.28.0"]
        mock_filter.return_value = (["requests==2.28.0"], [])

        # Mock the temporary file
        mock_file = mock_temp_file.return_value.__enter__.return_value
        temp_req = os.path.join(self.temp_dir, "test_requirements.txt")
        mock_file.name = temp_req

        with (
            patch.dict(os.environ, {"VIRTUAL_ENV": "/venv"}, clear=True),
            patch.object(pl, "_PLUGIN_DEPS_DIR", self.deps_dir),
            patch("mmrelay.plugin_loader._refresh_dependency_paths") as mock_refresh,
            patch("mmrelay.plugin_loader._write_requirements_install_marker"),
        ):
            _install_requirements_for_repo(self.repo_path, "test-plugin")

        # Check that the command uses -r with the temporary file
        called_cmd = mock_run.call_args[0][0]
        expected_base = [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-input",
            "--target",
        ]
        assert called_cmd[:7] == expected_base
        target_path = called_cmd[called_cmd.index("--target") + 1]
        assert target_path != self.deps_dir
        assert os.path.dirname(target_path) == os.path.dirname(self.deps_dir)
        assert "-r" in called_cmd
        assert called_cmd[called_cmd.index("-r") + 1] == temp_req
        mock_run.assert_called_once_with(called_cmd, timeout=600)
        mock_unlink.assert_called_once_with(temp_req)
        mock_which.assert_not_called()
        mock_refresh.assert_called_once()
        mock_logger.info.assert_called()

    @patch("mmrelay.plugin_loader.logger")
    @patch("mmrelay.plugin_loader._run")
    @patch("mmrelay.plugin_loader._filter_risky_requirement_lines")
    @patch("mmrelay.plugin_loader._collect_requirements")
    @patch("shutil.which", return_value="/usr/bin/pipx")
    def test_install_plugin_requirements_pipx_injection(
        self,
        mock_which,
        mock_collect,
        mock_filter,
        mock_run,
        mock_logger,
    ):
        """Test dependency installation with pipx."""
        mock_collect.return_value = [
            "requests==2.28.0",
            "--extra-index-url https://pypi.org/simple",
        ]
        mock_filter.return_value = (
            ["requests==2.28.0", "--extra-index-url https://pypi.org/simple"],
            [],
        )
        with (
            patch.dict(os.environ, {"PIPX_HOME": "/pipx/home"}, clear=True),
            patch.object(pl, "_PLUGIN_DEPS_DIR", None),
            patch("mmrelay.plugin_loader._refresh_dependency_paths") as mock_refresh,
        ):
            _install_requirements_for_repo(self.repo_path, "test-plugin")

        # Verify the call uses temporary file approach
        mock_run.assert_called_once()
        call_args = mock_run.call_args
        cmd = call_args[0][0]

        # Should be pipx inject with --requirement containing requirements file
        assert cmd[0] == "/usr/bin/pipx"
        assert cmd[1] == "inject"
        assert cmd[2] == "mmrelay"
        assert cmd[3] == "--requirement"

        # The --requirement should point to a temporary file path
        req_file = cmd[4]
        assert req_file.endswith(".txt")

        # Verify timeout
        assert call_args[1]["timeout"] == 600
        mock_which.assert_called_once_with("pipx")
        mock_refresh.assert_called_once()
        mock_logger.info.assert_called()

    def test_install_plugin_requirements_pip_install(self):
        """Test dependency installation with pip."""
        with (
            patch("mmrelay.plugin_loader.logger"),
            patch("mmrelay.plugin_loader._run") as mock_run,
            patch(
                "mmrelay.plugin_loader._filter_risky_requirement_lines"
            ) as mock_filter,
            patch("mmrelay.plugin_loader._collect_requirements") as mock_collect,
            patch("sys.prefix", "/fake/prefix"),
            patch("sys.base_prefix", "/fake/prefix"),
            patch("shutil.which", return_value=None),
            patch.object(pl, "_PLUGIN_DEPS_DIR", None),
            patch("mmrelay.plugin_loader._refresh_dependency_paths") as mock_refresh,
            patch(
                "mmrelay.plugin_loader.tempfile.NamedTemporaryFile"
            ) as mock_temp_file,
            patch("mmrelay.plugin_loader.os.unlink"),
            patch("mmrelay.plugin_loader._write_requirements_install_marker"),
        ):
            mock_collect.return_value = ["requests==2.28.0"]
            mock_filter.return_value = (["requests==2.28.0"], [])

            # Mock temporary file
            mock_file = mock_temp_file.return_value.__enter__.return_value
            mock_file.name = os.path.join(
                tempfile.gettempdir(), "test_requirements.txt"
            )

            with patch.dict(os.environ, {}, clear=True):
                _install_requirements_for_repo(self.repo_path, "test-plugin")

            # Check that command uses -r with temporary file and --user flag
            called_cmd = mock_run.call_args[0][0]
            expected_base = [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--no-input",
                "--user",
            ]
            assert called_cmd[:7] == expected_base
            assert "-r" in called_cmd
            assert called_cmd[called_cmd.index("-r") + 1] == os.path.join(
                tempfile.gettempdir(), "test_requirements.txt"
            )
            mock_run.assert_called_once_with(called_cmd, timeout=600)
            mock_refresh.assert_called_once()

    def test_install_plugin_requirements_uses_target_when_deps_dir_set(self):
        """Persistent deps dir should use pip --target instead of --user."""
        with (
            patch("mmrelay.plugin_loader._run") as mock_run,
            patch("mmrelay.plugin_loader._refresh_dependency_paths") as mock_refresh,
            patch.object(pl, "_PLUGIN_DEPS_DIR", self.deps_dir),
            patch("sys.prefix", "/fake/prefix"),
            patch("sys.base_prefix", "/fake/prefix"),
            patch.dict(os.environ, {}, clear=True),
        ):
            _install_requirements_for_repo(self.repo_path, "test-plugin")

        cmd = mock_run.call_args.args[0]
        self.assertEqual(
            cmd[:6],
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--no-input",
            ],
        )
        self.assertIn("--target", cmd)
        target_path = cmd[cmd.index("--target") + 1]
        self.assertNotEqual(target_path, self.deps_dir)
        self.assertEqual(os.path.dirname(target_path), os.path.dirname(self.deps_dir))
        self.assertNotIn("--user", cmd)
        self.assertNotIn("inject", cmd)
        mock_refresh.assert_called_once()

    def test_install_plugin_requirements_deps_dir_overrides_pipx(self):
        """Persistent deps dir should be preferred even when pipx is detected."""
        with (
            patch("mmrelay.plugin_loader._run") as mock_run,
            patch("mmrelay.plugin_loader._refresh_dependency_paths") as mock_refresh,
            patch.object(pl, "_PLUGIN_DEPS_DIR", self.deps_dir),
            patch("shutil.which", return_value="/usr/bin/pipx") as mock_which,
            patch.dict(os.environ, {"PIPX_HOME": "/pipx/home"}, clear=True),
        ):
            _install_requirements_for_repo(self.repo_path, "test-plugin")

        cmd = mock_run.call_args.args[0]
        self.assertEqual(cmd[:4], [sys.executable, "-m", "pip", "install"])
        self.assertIn("--target", cmd)
        target_path = cmd[cmd.index("--target") + 1]
        self.assertNotEqual(target_path, self.deps_dir)
        self.assertEqual(os.path.dirname(target_path), os.path.dirname(self.deps_dir))
        self.assertNotIn("inject", cmd)
        mock_which.assert_not_called()
        mock_refresh.assert_called_once()

    def test_install_plugin_requirements_without_deps_dir_uses_pipx(self):
        """Without a persistent deps dir, pipx environments should use pipx inject."""
        with (
            patch("mmrelay.plugin_loader._run") as mock_run,
            patch.object(pl, "_PLUGIN_DEPS_DIR", None),
            patch("shutil.which", return_value="/usr/bin/pipx"),
            patch("mmrelay.plugin_loader._refresh_dependency_paths") as mock_refresh,
            patch.dict(os.environ, {"PIPX_HOME": "/pipx/home"}, clear=True),
        ):
            _install_requirements_for_repo(self.repo_path, "test-plugin")

        cmd = mock_run.call_args.args[0]
        self.assertEqual(
            cmd[:4], ["/usr/bin/pipx", "inject", "mmrelay", "--requirement"]
        )
        mock_refresh.assert_called_once()

    def test_install_plugin_requirements_without_deps_dir_no_venv_uses_user(self):
        """Without deps dir, non-venv pip installs should use --user."""
        with (
            patch("mmrelay.plugin_loader._run") as mock_run,
            patch.object(pl, "_PLUGIN_DEPS_DIR", None),
            patch("sys.prefix", "/fake/prefix"),
            patch("sys.base_prefix", "/fake/prefix"),
            patch("mmrelay.plugin_loader._refresh_dependency_paths") as mock_refresh,
            patch.dict(os.environ, {}, clear=True),
        ):
            _install_requirements_for_repo(self.repo_path, "test-plugin")

        cmd = mock_run.call_args.args[0]
        self.assertEqual(cmd[:4], [sys.executable, "-m", "pip", "install"])
        self.assertIn("--user", cmd)
        self.assertNotIn("--target", cmd)
        mock_refresh.assert_called_once()

    def test_install_plugin_requirements_success_writes_marker(self):
        """Successful installs should write a per-plugin marker with hash and target."""
        with (
            patch("mmrelay.plugin_loader._run"),
            patch("mmrelay.plugin_loader._refresh_dependency_paths") as mock_refresh,
            patch.object(pl, "_PLUGIN_DEPS_DIR", self.deps_dir),
        ):
            result = _install_requirements_for_repo(self.repo_path, "test-plugin")

        self.assertTrue(result)
        mock_refresh.assert_called_once()
        requirements_hash = pl._requirements_hash(["requests==2.28.0"])
        target = f"target:{os.path.abspath(self.deps_dir)}"
        with patch.object(pl, "_PLUGIN_DEPS_DIR", self.deps_dir):
            marker_path = pl._requirements_install_marker_path(
                self.repo_path, "test-plugin"
            )
        with open(marker_path, encoding="utf-8") as marker_file:
            marker = json.load(marker_file)
        self.assertEqual(marker["requirements_hash"], requirements_hash)
        self.assertEqual(marker["target"], target)
        self.assertEqual(marker["repo"], "test-plugin")

    def test_install_plugin_requirements_stages_target_and_replaces_matching_items(
        self,
    ):
        """Persistent installs should merge staged outputs without deleting unrelated deps."""
        package_dir = os.path.join(self.deps_dir, "requests")
        dist_info_dir = os.path.join(self.deps_dir, "requests-2.27.0.dist-info")
        unrelated_dir = os.path.join(self.deps_dir, "unrelated")
        os.makedirs(package_dir)
        os.makedirs(dist_info_dir)
        os.makedirs(unrelated_dir)
        with open(os.path.join(package_dir, "__init__.py"), "w") as handle:
            handle.write("old = True\n")
        with open(os.path.join(package_dir, "old.py"), "w") as handle:
            handle.write("stale = True\n")
        with open(os.path.join(dist_info_dir, "METADATA"), "w") as handle:
            handle.write("old metadata\n")
        with open(os.path.join(unrelated_dir, "keep.txt"), "w") as handle:
            handle.write("keep\n")

        staged_targets: list[str] = []

        def fake_run(cmd: Any, **_kwargs: Any) -> None:
            staged_target = cmd[cmd.index("--target") + 1]
            staged_targets.append(staged_target)
            os.makedirs(os.path.join(staged_target, "requests"))
            os.makedirs(os.path.join(staged_target, "requests-2.28.0.dist-info"))
            with open(
                os.path.join(staged_target, "requests", "__init__.py"), "w"
            ) as handle:
                handle.write("new = True\n")
            with open(
                os.path.join(staged_target, "requests-2.28.0.dist-info", "METADATA"),
                "w",
            ) as handle:
                handle.write("new metadata\n")

        with (
            patch("mmrelay.plugin_loader._run", side_effect=fake_run) as mock_run,
            patch("mmrelay.plugin_loader._refresh_dependency_paths") as mock_refresh,
            patch.object(pl, "_PLUGIN_DEPS_DIR", self.deps_dir),
        ):
            result = _install_requirements_for_repo(self.repo_path, "test-plugin")

        self.assertTrue(result)
        mock_run.assert_called_once()
        mock_refresh.assert_called_once()
        self.assertEqual(len(staged_targets), 1)
        self.assertNotEqual(staged_targets[0], self.deps_dir)
        self.assertFalse(os.path.exists(staged_targets[0]))
        self.assertEqual(
            os.listdir(os.path.join(self.deps_dir, "requests")),
            ["__init__.py"],
        )
        with open(
            os.path.join(self.deps_dir, "requests", "__init__.py"),
            encoding="utf-8",
        ) as handle:
            self.assertEqual(handle.read(), "new = True\n")
        self.assertFalse(os.path.exists(dist_info_dir))
        self.assertTrue(
            os.path.exists(os.path.join(self.deps_dir, "requests-2.28.0.dist-info"))
        )
        self.assertTrue(os.path.exists(os.path.join(unrelated_dir, "keep.txt")))

    def test_install_plugin_requirements_preserves_namespace_package_siblings(self):
        """Staged merge should not delete existing namespace package subpackages."""
        existing_namespace = os.path.join(self.deps_dir, "zope", "interface")
        os.makedirs(existing_namespace)
        with open(os.path.join(existing_namespace, "__init__.py"), "w") as handle:
            handle.write("interface = True\n")

        staged_targets: list[str] = []

        def fake_run(cmd: Any, **_kwargs: Any) -> None:
            staged_target = cmd[cmd.index("--target") + 1]
            staged_targets.append(staged_target)
            staged_namespace = os.path.join(staged_target, "zope", "proxy")
            os.makedirs(staged_namespace)
            with open(os.path.join(staged_namespace, "__init__.py"), "w") as handle:
                handle.write("proxy = True\n")
            dist_info = os.path.join(staged_target, "zope_proxy-5.2.dist-info")
            os.makedirs(dist_info)
            with open(
                os.path.join(dist_info, "METADATA"),
                "w",
                encoding=pl.DEFAULT_TEXT_ENCODING,
            ) as handle:
                handle.write("Name: zope.proxy\n")

        with (
            patch("mmrelay.plugin_loader._run", side_effect=fake_run),
            patch("mmrelay.plugin_loader._refresh_dependency_paths") as mock_refresh,
            patch.object(pl, "_PLUGIN_DEPS_DIR", self.deps_dir),
        ):
            result = _install_requirements_for_repo(self.repo_path, "test-plugin")

        self.assertTrue(result)
        mock_refresh.assert_called_once()
        self.assertFalse(os.path.exists(staged_targets[0]))
        self.assertTrue(
            os.path.exists(os.path.join(self.deps_dir, "zope", "interface"))
        )
        self.assertTrue(os.path.exists(os.path.join(self.deps_dir, "zope", "proxy")))

    def test_install_plugin_requirements_pkgutil_namespace_keeps_siblings(self):
        """pkgutil.extend_path namespace dirs should merge and keep existing subpackages."""
        existing_ns = os.path.join(self.deps_dir, "google", "cloud")
        os.makedirs(existing_ns)
        with open(os.path.join(self.deps_dir, "google", "__init__.py"), "w") as handle:
            handle.write(
                "__path__ = __import__('pkgutil').extend_path(__path__, __name__)\n"
            )
        with open(os.path.join(existing_ns, "__init__.py"), "w") as handle:
            handle.write("cloud = True\n")

        staged_targets: list[str] = []

        def fake_run(cmd: Any, **_kwargs: Any) -> None:
            staged_target = cmd[cmd.index("--target") + 1]
            staged_targets.append(staged_target)
            staged_ns = os.path.join(staged_target, "google", "ads")
            os.makedirs(staged_ns)
            with open(
                os.path.join(staged_target, "google", "__init__.py"), "w"
            ) as handle:
                handle.write(
                    "__path__ = __import__('pkgutil').extend_path(__path__, __name__)\n"
                )
            with open(os.path.join(staged_ns, "__init__.py"), "w") as handle:
                handle.write("ads = True\n")
            dist_info = os.path.join(staged_target, "google_ads-1.0.dist-info")
            os.makedirs(dist_info)
            with open(os.path.join(dist_info, "METADATA"), "w") as handle:
                handle.write("Name: google-ads\n")

        with (
            patch("mmrelay.plugin_loader._run", side_effect=fake_run),
            patch("mmrelay.plugin_loader._refresh_dependency_paths") as mock_refresh,
            patch.object(pl, "_PLUGIN_DEPS_DIR", self.deps_dir),
        ):
            result = _install_requirements_for_repo(self.repo_path, "test-plugin")

        self.assertTrue(result)
        mock_refresh.assert_called_once()
        self.assertTrue(os.path.exists(os.path.join(self.deps_dir, "google", "cloud")))
        self.assertTrue(os.path.exists(os.path.join(self.deps_dir, "google", "ads")))

    def test_install_plugin_requirements_pkg_resources_namespace_keeps_siblings(self):
        """pkg_resources.declare_namespace dirs should merge and keep existing subpackages."""
        existing_ns = os.path.join(self.deps_dir, "zope", "event")
        os.makedirs(existing_ns)
        with open(os.path.join(self.deps_dir, "zope", "__init__.py"), "w") as handle:
            handle.write("__import__('pkg_resources').declare_namespace(__name__)\n")
        with open(os.path.join(existing_ns, "__init__.py"), "w") as handle:
            handle.write("event = True\n")

        staged_targets: list[str] = []

        def fake_run(cmd: Any, **_kwargs: Any) -> None:
            staged_target = cmd[cmd.index("--target") + 1]
            staged_targets.append(staged_target)
            staged_ns = os.path.join(staged_target, "zope", "proxy")
            os.makedirs(staged_ns)
            with open(
                os.path.join(staged_target, "zope", "__init__.py"), "w"
            ) as handle:
                handle.write(
                    "__import__('pkg_resources').declare_namespace(__name__)\n"
                )
            with open(os.path.join(staged_ns, "__init__.py"), "w") as handle:
                handle.write("proxy = True\n")
            dist_info = os.path.join(staged_target, "zope_proxy-5.2.dist-info")
            os.makedirs(dist_info)
            with open(os.path.join(dist_info, "METADATA"), "w") as handle:
                handle.write("Name: zope.proxy\n")

        with (
            patch("mmrelay.plugin_loader._run", side_effect=fake_run),
            patch("mmrelay.plugin_loader._refresh_dependency_paths") as mock_refresh,
            patch.object(pl, "_PLUGIN_DEPS_DIR", self.deps_dir),
        ):
            result = _install_requirements_for_repo(self.repo_path, "test-plugin")

        self.assertTrue(result)
        mock_refresh.assert_called_once()
        self.assertTrue(os.path.exists(os.path.join(self.deps_dir, "zope", "event")))
        self.assertTrue(os.path.exists(os.path.join(self.deps_dir, "zope", "proxy")))

    def test_install_plugin_requirements_regular_package_replaces_stale_files(self):
        """Regular packages with normal __init__.py should be replaced, removing stale files."""
        package_dir = os.path.join(self.deps_dir, "requests")
        os.makedirs(package_dir)
        with open(os.path.join(package_dir, "__init__.py"), "w") as handle:
            handle.write("old = True\n")
        with open(os.path.join(package_dir, "stale_module.py"), "w") as handle:
            handle.write("stale = True\n")

        staged_targets: list[str] = []

        def fake_run(cmd: Any, **_kwargs: Any) -> None:
            staged_target = cmd[cmd.index("--target") + 1]
            staged_targets.append(staged_target)
            os.makedirs(os.path.join(staged_target, "requests"))
            with open(
                os.path.join(staged_target, "requests", "__init__.py"), "w"
            ) as handle:
                handle.write("new = True\n")
            dist_info = os.path.join(staged_target, "requests-2.28.0.dist-info")
            os.makedirs(dist_info)
            with open(os.path.join(dist_info, "METADATA"), "w") as handle:
                handle.write("Name: requests\n")

        with (
            patch("mmrelay.plugin_loader._run", side_effect=fake_run),
            patch("mmrelay.plugin_loader._refresh_dependency_paths") as mock_refresh,
            patch.object(pl, "_PLUGIN_DEPS_DIR", self.deps_dir),
        ):
            result = _install_requirements_for_repo(self.repo_path, "test-plugin")

        self.assertTrue(result)
        mock_refresh.assert_called_once()
        self.assertEqual(
            sorted(os.listdir(os.path.join(self.deps_dir, "requests"))),
            ["__init__.py"],
        )
        with open(
            os.path.join(self.deps_dir, "requests", "__init__.py"),
            encoding="utf-8",
        ) as handle:
            self.assertEqual(handle.read(), "new = True\n")
        self.assertFalse(
            os.path.exists(os.path.join(self.deps_dir, "requests", "stale_module.py"))
        )

    def test_install_plugin_requirements_failed_staged_target_leaves_deps_unchanged(
        self,
    ):
        """Failed persistent installs should not merge staged outputs or write markers."""
        package_dir = os.path.join(self.deps_dir, "requests")
        unrelated_dir = os.path.join(self.deps_dir, "unrelated")
        os.makedirs(package_dir)
        os.makedirs(unrelated_dir)
        with open(os.path.join(package_dir, "__init__.py"), "w") as handle:
            handle.write("old = True\n")
        with open(os.path.join(unrelated_dir, "keep.txt"), "w") as handle:
            handle.write("keep\n")

        staged_targets: list[str] = []

        def fake_run(cmd: Any, **_kwargs: Any) -> None:
            staged_target = cmd[cmd.index("--target") + 1]
            staged_targets.append(staged_target)
            os.makedirs(os.path.join(staged_target, "requests"))
            with open(
                os.path.join(staged_target, "requests", "__init__.py"), "w"
            ) as handle:
                handle.write("new = True\n")
            raise subprocess.CalledProcessError(1, cmd)

        with (
            patch("mmrelay.plugin_loader._run", side_effect=fake_run),
            patch("mmrelay.plugin_loader._refresh_dependency_paths") as mock_refresh,
            patch.object(pl, "_PLUGIN_DEPS_DIR", self.deps_dir),
        ):
            result = _install_requirements_for_repo(self.repo_path, "test-plugin")

        self.assertFalse(result)
        self.assertEqual(len(staged_targets), 1)
        self.assertFalse(os.path.exists(staged_targets[0]))
        with open(
            os.path.join(self.deps_dir, "requests", "__init__.py"),
            encoding="utf-8",
        ) as handle:
            self.assertEqual(handle.read(), "old = True\n")
        self.assertTrue(os.path.exists(os.path.join(unrelated_dir, "keep.txt")))
        with patch.object(pl, "_PLUGIN_DEPS_DIR", self.deps_dir):
            marker_path = pl._requirements_install_marker_path(
                self.repo_path, "test-plugin"
            )
        self.assertFalse(os.path.exists(marker_path))
        mock_refresh.assert_not_called()

    def test_install_plugin_requirements_no_packages_writes_marker(self):
        """No-installable-package requirements should still mark successful handling."""
        with open(self.requirements_path, "w") as f:
            f.write("--pre\n")

        with (
            patch("mmrelay.plugin_loader._run") as mock_run,
            patch("mmrelay.plugin_loader._refresh_dependency_paths") as mock_refresh,
            patch.object(pl, "_PLUGIN_DEPS_DIR", self.deps_dir),
        ):
            result = _install_requirements_for_repo(self.repo_path, "test-plugin")

        self.assertTrue(result)
        mock_run.assert_not_called()
        mock_refresh.assert_not_called()
        requirements_hash = pl._requirements_hash(["--pre"])
        with patch.object(pl, "_PLUGIN_DEPS_DIR", self.deps_dir):
            self.assertTrue(
                pl._requirements_install_target_valid(
                    self.repo_path,
                    "test-plugin",
                    requirements_hash,
                    f"target:{os.path.abspath(self.deps_dir)}",
                )
            )

    def test_install_plugin_requirements_failure_writes_no_marker(self):
        """Failed installs should not write a marker."""
        with (
            patch(
                "mmrelay.plugin_loader._run",
                side_effect=subprocess.CalledProcessError(1, "pip"),
            ),
            patch.object(pl, "_PLUGIN_DEPS_DIR", self.deps_dir),
        ):
            result = _install_requirements_for_repo(self.repo_path, "test-plugin")

        self.assertFalse(result)
        with patch.object(pl, "_PLUGIN_DEPS_DIR", self.deps_dir):
            marker_path = pl._requirements_install_marker_path(
                self.repo_path, "test-plugin"
            )
        self.assertFalse(os.path.exists(marker_path))

    @patch("mmrelay.plugin_loader.logger")
    @patch("mmrelay.plugin_loader._run")
    @patch("mmrelay.plugin_loader._filter_risky_requirement_lines")
    @patch("mmrelay.plugin_loader._collect_requirements")
    def test_install_plugin_requirements_with_flagged_deps(
        self,
        mock_collect,
        mock_filter,
        mock_run,
        mock_logger,
    ):
        """Test dependency installation with flagged dependencies."""
        mock_collect.return_value = [
            "requests==2.28.0",
            "git+https://github.com/user/repo.git",
        ]
        mock_filter.return_value = (
            ["requests==2.28.0"],
            ["git+https://github.com/user/repo.git"],
        )
        _install_requirements_for_repo(self.repo_path, "test-plugin")
        mock_logger.warning.assert_called_with(
            "Skipping %d flagged dependency entries for %s. Set security.allow_untrusted_dependencies=True to override.",
            1,
            "test-plugin",
        )
        mock_run.assert_called_once()

    @patch("mmrelay.plugin_loader.logger")
    @patch("mmrelay.plugin_loader._run")
    @patch("mmrelay.plugin_loader._filter_risky_requirement_lines")
    @patch("mmrelay.plugin_loader._collect_requirements")
    def test_install_plugin_requirements_installation_error(
        self,
        mock_collect,
        mock_filter,
        mock_run,
        mock_logger,
    ):
        """Test handling of installation errors."""
        mock_collect.return_value = ["requests==2.28.0"]
        mock_filter.return_value = (["requests==2.28.0"], [])
        mock_run.side_effect = subprocess.CalledProcessError(1, "pip")
        _install_requirements_for_repo(self.repo_path, "test-plugin")
        mock_logger.exception.assert_called()

    def test_install_plugin_requirements_pipx_not_found(self):
        """Test fallback to pip when pipx is not found."""
        with (
            patch("mmrelay.plugin_loader.logger"),
            patch("mmrelay.plugin_loader._run") as mock_run,
            patch(
                "mmrelay.plugin_loader._filter_risky_requirement_lines"
            ) as mock_filter,
            patch("mmrelay.plugin_loader._collect_requirements") as mock_collect,
            patch("sys.prefix", "/fake/prefix"),
            patch("sys.base_prefix", "/fake/prefix"),
            patch("shutil.which", return_value=None),
            patch.object(pl, "_PLUGIN_DEPS_DIR", None),
            patch(
                "mmrelay.plugin_loader.tempfile.NamedTemporaryFile"
            ) as mock_temp_file,
            patch("mmrelay.plugin_loader.os.unlink"),
            patch("mmrelay.plugin_loader._write_requirements_install_marker"),
        ):
            mock_collect.return_value = ["requests==2.28.0"]
            mock_filter.return_value = (["requests==2.28.0"], [])

            # Mock temporary file
            mock_file = mock_temp_file.return_value.__enter__.return_value
            mock_file.name = os.path.join(
                tempfile.gettempdir(), "test_requirements.txt"
            )

            with patch.dict(os.environ, {}, clear=True):
                _install_requirements_for_repo(self.repo_path, "test-plugin")

            # Should fall back to pip with -r and temporary file
            called_cmd = mock_run.call_args[0][0]
            expected_base = [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--no-input",
                "--user",
            ]
            assert called_cmd[:7] == expected_base
            assert "-r" in called_cmd
            assert called_cmd[called_cmd.index("-r") + 1] == os.path.join(
                tempfile.gettempdir(), "test_requirements.txt"
            )
            mock_run.assert_called_once_with(called_cmd, timeout=600)

    @patch("mmrelay.plugin_loader.logger")
    @patch("mmrelay.plugin_loader._run")
    @patch("mmrelay.plugin_loader._filter_risky_requirement_lines")
    @patch("mmrelay.plugin_loader._collect_requirements")
    @patch("shutil.which", return_value="/usr/bin/pipx")
    def test_install_plugin_requirements_pipx_inject_fails(
        self,
        mock_which,
        mock_collect,
        mock_filter,
        mock_run,
        mock_logger,
    ):
        """Test handling of pipx inject failure."""
        mock_collect.return_value = ["requests==2.28.0"]
        mock_filter.return_value = (["requests==2.28.0"], [])
        mock_run.side_effect = subprocess.CalledProcessError(1, "pipx")

        with (
            patch.dict(os.environ, {"PIPX_HOME": "/pipx/home"}, clear=True),
            patch.object(pl, "_PLUGIN_DEPS_DIR", None),
        ):
            _install_requirements_for_repo(self.repo_path, "test-plugin")

        # Should log error and warning
        mock_logger.exception.assert_called()
        mock_logger.warning.assert_called_with(
            "Plugin %s may not work correctly without its dependencies",
            "test-plugin",
        )

    @patch("mmrelay.plugin_loader.logger")
    @patch("mmrelay.plugin_loader._run")
    @patch("mmrelay.plugin_loader._filter_risky_requirement_lines")
    @patch("mmrelay.plugin_loader._collect_requirements")
    def test_install_plugin_requirements_pipx_no_packages(
        self, mock_collect, mock_filter, mock_run, mock_logger
    ):
        """Test pipx injection when no packages to install."""
        mock_collect.return_value = ["--extra-index-url https://pypi.org/simple"]
        mock_filter.return_value = (
            ["--extra-index-url https://pypi.org/simple"],
            [],
        )

        with open(self.requirements_path, "w") as f:
            f.write("--extra-index-url https://pypi.org/simple\n")

        with (
            patch.dict(os.environ, {"PIPX_HOME": "/pipx/home"}, clear=True),
            patch.object(pl, "_PLUGIN_DEPS_DIR", None),
            patch("shutil.which", return_value="/usr/bin/pipx"),
            patch("mmrelay.plugin_loader._refresh_dependency_paths") as mock_refresh,
        ):
            _install_requirements_for_repo(self.repo_path, "test-plugin")

        # Should not call pipx inject when no packages
        mock_run.assert_not_called()
        mock_refresh.assert_not_called()
        mock_logger.info.assert_called_with(
            "No dependency installation run for plugin %s",
            "test-plugin",
        )

    @patch("mmrelay.plugin_loader.logger")
    @patch("mmrelay.plugin_loader._run")
    @patch("mmrelay.plugin_loader._filter_risky_requirement_lines")
    @patch("mmrelay.plugin_loader._collect_requirements")
    def test_install_plugin_requirements_allow_untrusted(
        self, mock_collect, mock_filter, mock_run, mock_logger
    ):
        """Test dependency installation with untrusted dependencies allowed."""
        mock_collect.return_value = [
            "requests==2.28.0",
            "git+https://github.com/user/repo.git",
        ]
        mock_filter.return_value = (
            ["requests==2.28.0"],
            ["git+https://github.com/user/repo.git"],
        )

        with open(self.requirements_path, "w") as f:
            f.write("requests==2.28.0\ngit+https://github.com/user/repo.git\n")

        # Mock the config to allow untrusted dependencies
        with patch(
            "mmrelay.plugin_loader.config",
            {"security": {"allow_untrusted_dependencies": True}},
        ):
            _install_requirements_for_repo(self.repo_path, "test-plugin")

        # Should log warning but still install
        mock_logger.warning.assert_called_with(
            "Allowing %d flagged dependency entries for %s due to security.allow_untrusted_dependencies=True",
            1,
            "test-plugin",
        )
        mock_run.assert_called()
