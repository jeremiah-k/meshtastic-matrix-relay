"""Tests for plugin loader: Dependency installation, requirements management."""

# Decomposed from test_plugin_loader.py

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import ANY, MagicMock, call, patch

import mmrelay.plugin_loader as pl
from mmrelay.constants.plugins import DEFAULT_BRANCHES
from mmrelay.plugin_loader import (
    _clone_new_repo_to_branch_or_tag,
    _collect_requirements,
    _install_requirements_for_repo,
    _is_repo_url_allowed,
    _run,
    _update_existing_repo_to_branch_or_tag,
    _validate_clone_inputs,
    clear_plugin_jobs,
    clone_or_update_repo,
    schedule_job,
    start_global_scheduler,
    stop_global_scheduler,
)
from tests._plugin_loader_helpers import TEST_GIT_TIMEOUT, BaseGitTest


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

    def test_requirements_install_target_identity_unknown_user_site_is_stable(self):
        """Missing user-site support should not produce cwd-dependent target IDs."""
        with (
            patch.object(pl, "_PLUGIN_DEPS_DIR", None),
            patch.dict(os.environ, {}, clear=True),
            patch("sys.prefix", "/fake/prefix"),
            patch("sys.base_prefix", "/fake/prefix"),
            patch("site.getusersitepackages", side_effect=AttributeError),
        ):
            self.assertEqual(pl._requirements_install_target_identity(), "user:unknown")

    def test_requirements_install_target_pipx_uses_site_packages_marker_dir(self):
        """pipx fallback target should use site-packages as marker location."""
        site_packages = os.path.join(self.temp_dir, "pipx-venv", "site-packages")
        os.makedirs(site_packages)
        with (
            patch.object(pl, "_PLUGIN_DEPS_DIR", None),
            patch.dict(os.environ, {"PIPX_HOME": "/pipx/home"}, clear=True),
            patch("sys.prefix", os.path.join(self.temp_dir, "pipx-venv")),
            patch(
                "mmrelay.plugin_loader._site_packages_for_prefix",
                return_value=site_packages,
            ),
        ):
            target = pl._requirements_install_target()

        self.assertTrue(target.identity.startswith("pipx:"))
        self.assertIn(os.path.abspath(site_packages), target.identity)
        self.assertEqual(target.marker_dir, site_packages)

    def test_requirements_install_target_venv_uses_site_packages_marker_dir(self):
        """venv fallback target should use site-packages as marker location."""
        site_packages = os.path.join(self.temp_dir, "venv", "site-packages")
        os.makedirs(site_packages)
        with (
            patch.object(pl, "_PLUGIN_DEPS_DIR", None),
            patch.dict(os.environ, {"VIRTUAL_ENV": "/venv"}, clear=True),
            patch("sys.prefix", os.path.join(self.temp_dir, "venv")),
            patch("sys.base_prefix", "/usr"),
            patch(
                "mmrelay.plugin_loader._site_packages_for_prefix",
                return_value=site_packages,
            ),
        ):
            target = pl._requirements_install_target()

        self.assertTrue(target.identity.startswith("python:"))
        self.assertIn(os.path.abspath(site_packages), target.identity)
        self.assertEqual(target.marker_dir, site_packages)

    def test_requirements_install_target_user_uses_user_site_marker_dir(self):
        """user fallback target should use user-site as marker location."""
        user_site = os.path.join(self.temp_dir, "user-site")
        os.makedirs(user_site)
        with (
            patch.object(pl, "_PLUGIN_DEPS_DIR", None),
            patch.dict(os.environ, {}, clear=True),
            patch("sys.prefix", "/fake/prefix"),
            patch("sys.base_prefix", "/fake/prefix"),
            patch("site.getusersitepackages", return_value=user_site),
        ):
            target = pl._requirements_install_target()

        self.assertTrue(
            target.identity.startswith(f"user:{os.path.abspath(user_site)}")
        )
        self.assertEqual(target.marker_dir, user_site)

    def test_requirements_install_target_identity_stable_after_marker_write(self):
        """Writing a colocated marker should not change the target identity."""
        user_site = os.path.join(self.temp_dir, "user-site")
        os.makedirs(user_site)
        with (
            patch.object(pl, "_PLUGIN_DEPS_DIR", None),
            patch.dict(os.environ, {}, clear=True),
            patch("sys.prefix", "/fake/prefix"),
            patch("sys.base_prefix", "/fake/prefix"),
            patch("site.getusersitepackages", return_value=user_site),
        ):
            before = pl._requirements_install_target_identity()
            pl._write_requirements_install_marker(
                self.repo_path, "test-plugin", "hash", before
            )
            after = pl._requirements_install_target_identity()

        self.assertEqual(after, before)

    def test_requirements_install_marker_path_falls_back_to_repo_when_unwritable(self):
        """Marker should fall back to repo path when install marker dir is unavailable."""
        user_site = os.path.join(self.temp_dir, "user-site")
        os.makedirs(user_site)
        with (
            patch.object(pl, "_PLUGIN_DEPS_DIR", None),
            patch.dict(os.environ, {}, clear=True),
            patch("sys.prefix", "/fake/prefix"),
            patch("sys.base_prefix", "/fake/prefix"),
            patch("site.getusersitepackages", return_value=user_site),
            patch("mmrelay.plugin_loader._is_writable_directory", return_value=False),
        ):
            marker_path = pl._requirements_install_marker_path(
                self.repo_path, "test-plugin"
            )

        self.assertTrue(marker_path.startswith(self.repo_path + os.sep))

    def test_site_packages_for_prefix_uses_posix_prefix_scheme(self):
        """site-packages discovery should include the POSIX prefix scheme."""
        prefix = os.path.join(self.temp_dir, "venv")
        site_packages = os.path.join(prefix, "lib", "python3.12", "site-packages")
        os.makedirs(site_packages)

        def fake_get_path(name, scheme, **_kwargs):
            if name == "purelib" and scheme == "posix_prefix":
                return site_packages
            return None

        with (
            patch("sys.platform", "linux"),
            patch("site.getsitepackages", return_value=[]),
            patch(
                "mmrelay.plugin_loader.sysconfig.get_path",
                side_effect=fake_get_path,
            ),
        ):
            self.assertEqual(pl._site_packages_for_prefix(prefix), site_packages)

    def test_site_packages_for_prefix_uses_windows_nt_scheme(self):
        """site-packages discovery should include the Windows nt scheme."""
        prefix = os.path.join(self.temp_dir, "venv")
        site_packages = os.path.join(prefix, "Lib", "site-packages")
        os.makedirs(site_packages)

        def fake_get_path(name, scheme, **_kwargs):
            if name == "purelib" and scheme == "nt":
                return site_packages
            return None

        with (
            patch("sys.platform", "win32"),
            patch("site.getsitepackages", return_value=[]),
            patch(
                "mmrelay.plugin_loader.sysconfig.get_path",
                side_effect=fake_get_path,
            ),
        ):
            self.assertEqual(pl._site_packages_for_prefix(prefix), site_packages)

    def test_resolve_plugin_deps_dir_uses_paths_module_for_primary_root(self):
        """Canonical deps dir should come from paths.py for the primary plugin root."""
        plugin_root = os.path.join(self.temp_dir, "plugins")
        canonical_deps = os.path.join(self.temp_dir, "canonical-deps")

        with (
            patch(
                "mmrelay.plugin_loader.paths_module.get_plugins_dir",
                return_value=plugin_root,
            ),
            patch(
                "mmrelay.plugin_loader.paths_module.resolve_all_paths",
                return_value={"deps_dir": canonical_deps},
            ),
        ):
            self.assertEqual(
                pl._resolve_plugin_deps_dir_for_root(plugin_root),
                os.path.abspath(canonical_deps),
            )

    def test_resolve_plugin_deps_dir_falls_back_when_paths_module_fails(self):
        """Deps dir setup should fall back safely if paths.py resolution fails."""
        plugin_root = os.path.join(self.temp_dir, "plugins")

        with patch(
            "mmrelay.plugin_loader.paths_module.get_plugins_dir",
            side_effect=RuntimeError("boom"),
        ):
            self.assertEqual(
                pl._resolve_plugin_deps_dir_for_root(plugin_root),
                os.path.join(plugin_root, pl.PLUGIN_DEPS_DIRNAME),
            )

    def test_requirements_hash_includes_safe_pip_options(self):
        """Requirement state hash covers all safe effective lines, including options."""
        requirements = ["--pre", "requests==2.28.0"]
        self.assertEqual(
            pl._requirements_hash(requirements),
            hashlib.sha256("--pre\nrequests==2.28.0".encode("utf-8")).hexdigest(),
        )

    def test_installable_requirement_lines_filters_option_lines(self):
        """Install execution only runs when non-option requirement lines are present."""
        self.assertEqual(
            pl._installable_requirement_lines(["--pre", "requests==2.28.0"]),
            ["requests==2.28.0"],
        )
        self.assertEqual(pl._installable_requirement_lines(["--pre"]), [])

    def test_temporary_requirements_file_cleans_up_after_success(self):
        """Temporary requirements helper should remove the file after normal use."""
        with (
            patch(
                "mmrelay.plugin_loader.tempfile.NamedTemporaryFile",
                wraps=tempfile.NamedTemporaryFile,
            ) as mock_temp_file,
            pl._temporary_requirements_file(["requests==2.28.0"]) as temp_path,
        ):
            self.assertTrue(os.path.exists(temp_path))
            with open(temp_path, encoding="utf-8") as temp_file:
                self.assertEqual(temp_file.read(), "requests==2.28.0\n")

        self.assertFalse(os.path.exists(temp_path))
        self.assertEqual(
            mock_temp_file.call_args.kwargs["encoding"], pl.DEFAULT_TEXT_ENCODING
        )

    def test_temporary_requirements_file_cleans_up_after_failure(self):
        """Temporary requirements helper should remove the file when callers fail."""
        temp_path = None
        with self.assertRaises(RuntimeError):
            with pl._temporary_requirements_file(["requests==2.28.0"]) as path:
                temp_path = path
                raise RuntimeError("boom")

        self.assertIsNotNone(temp_path)
        self.assertFalse(os.path.exists(temp_path))

    def test_requirements_install_target_valid_empty_deps_dir_is_invalid(self):
        """An empty persistent deps dir should not satisfy cached install state."""
        with patch.object(pl, "_PLUGIN_DEPS_DIR", self.deps_dir):
            self.assertFalse(
                pl._requirements_install_target_valid(
                    self.repo_path, "test-plugin", "hash", "target"
                )
            )

    def test_requirements_install_target_valid_deps_dir_marker_is_valid(self):
        """A marker in the persistent deps dir should satisfy cached install state."""
        with patch.object(pl, "_PLUGIN_DEPS_DIR", self.deps_dir):
            pl._write_requirements_install_marker(
                self.repo_path, "test-plugin", "hash", "target"
            )
            self.assertTrue(
                pl._requirements_install_target_valid(
                    self.repo_path, "test-plugin", "hash", "target"
                )
            )

    def test_requirements_install_target_valid_marker_hash_mismatch_is_invalid(self):
        """A stale marker with a different requirements hash should not validate."""
        with patch.object(pl, "_PLUGIN_DEPS_DIR", self.deps_dir):
            pl._write_requirements_install_marker(
                self.repo_path, "test-plugin", "old-hash", "target"
            )
            self.assertFalse(
                pl._requirements_install_target_valid(
                    self.repo_path, "test-plugin", "new-hash", "target"
                )
            )

    def test_requirements_install_target_valid_marker_target_mismatch_is_invalid(self):
        """A marker for a different install target should not validate."""
        with patch.object(pl, "_PLUGIN_DEPS_DIR", self.deps_dir):
            pl._write_requirements_install_marker(
                self.repo_path, "test-plugin", "hash", "old-target"
            )
            self.assertFalse(
                pl._requirements_install_target_valid(
                    self.repo_path, "test-plugin", "hash", "new-target"
                )
            )

    def test_requirements_install_target_valid_marker_repo_mismatch_is_invalid(self):
        """A marker with a mismatched repo payload should not validate."""
        with patch.object(pl, "_PLUGIN_DEPS_DIR", self.deps_dir):
            marker_path = pl._requirements_install_marker_path(
                self.repo_path, "test-plugin"
            )
            with open(marker_path, "w", encoding="utf-8") as marker_file:
                json.dump(
                    {
                        "requirements_hash": "hash",
                        "target": "target",
                        "repo": "other-plugin",
                    },
                    marker_file,
                )
            self.assertFalse(
                pl._requirements_install_target_valid(
                    self.repo_path, "test-plugin", "hash", "target"
                )
            )

    def test_metadata_distribution_key_reads_egg_info_pkg_info(self):
        """egg-info distribution names should be read from PKG-INFO."""
        egg_info_dir = os.path.join(self.temp_dir, "python_dateutil-2.8.2.egg-info")
        os.makedirs(egg_info_dir)
        with open(
            os.path.join(egg_info_dir, "PKG-INFO"),
            "w",
            encoding=pl.DEFAULT_TEXT_ENCODING,
        ) as metadata_file:
            metadata_file.write("Metadata-Version: 2.1\nName: python-dateutil\n")

        self.assertEqual(
            pl._metadata_distribution_key(
                "python_dateutil-2.8.2.egg-info", egg_info_dir
            ),
            "python-dateutil",
        )

    def test_requirements_install_target_valid_unrelated_deps_file_is_invalid(self):
        """Unrelated files in the shared deps dir should not validate a plugin."""
        with open(os.path.join(self.deps_dir, "unrelated.txt"), "w") as handle:
            handle.write("not a marker\n")

        with patch.object(pl, "_PLUGIN_DEPS_DIR", self.deps_dir):
            self.assertFalse(
                pl._requirements_install_target_valid(
                    self.repo_path, "test-plugin", "hash", "target"
                )
            )

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

        def fake_run(cmd, **_kwargs):
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

        def fake_run(cmd, **_kwargs):
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

        def fake_run(cmd, **_kwargs):
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

        def fake_run(cmd, **_kwargs):
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

        def fake_run(cmd, **_kwargs):
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

        def fake_run(cmd, **_kwargs):
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

    def test_schedule_job_creates_job_with_tag(self):
        """Test that schedule_job creates a job with the correct tag."""
        with patch("mmrelay.plugin_loader.schedule") as mock_schedule:
            mock_job = MagicMock()
            mock_schedule.every.return_value = mock_job

            result = schedule_job("test_plugin", 5)

            mock_schedule.every.assert_called_once_with(5)
            mock_job.tag.assert_called_once_with("test_plugin")
            self.assertEqual(result, mock_job)

    def test_schedule_job_returns_none_when_schedule_unavailable(self):
        """Test that schedule_job returns None when schedule library is not available."""
        with patch("mmrelay.plugin_loader.schedule", None):
            result = schedule_job("test_plugin", 5)
            self.assertIsNone(result)

    def test_clear_plugin_jobs_calls_schedule_clear(self):
        """Test that clear_plugin_jobs calls schedule.clear with plugin name."""
        with patch("mmrelay.plugin_loader.schedule") as mock_schedule:
            clear_plugin_jobs("test_plugin")
            mock_schedule.clear.assert_called_once_with("test_plugin")

    def test_clear_plugin_jobs_handles_none_schedule(self):
        """Test that clear_plugin_jobs handles None schedule gracefully."""
        with patch("mmrelay.plugin_loader.schedule", None):
            # Should not raise an exception
            clear_plugin_jobs("test_plugin")

    @patch("mmrelay.plugin_loader.threading")
    @patch("mmrelay.plugin_loader.schedule")
    def test_start_global_scheduler_starts_thread(self, mock_schedule, mock_threading):
        """Test that start_global_scheduler creates and starts a daemon thread."""

        # Reset global state before test
        pl._global_scheduler_thread = None
        pl._global_scheduler_stop_event = None

        mock_event = MagicMock()
        mock_threading.Event.return_value = mock_event
        mock_thread = MagicMock()
        mock_threading.Thread.return_value = mock_thread

        start_global_scheduler()

        # Event should be called since schedule is available (mocked)
        mock_threading.Event.assert_called_once()
        mock_threading.Thread.assert_called_once()
        mock_thread.start.assert_called_once()

    def test_start_global_scheduler_runs_pending_once(self):
        """scheduler_loop should call schedule.run_pending when available."""
        import threading

        run_event = threading.Event()

        class FakeSchedule:
            def __bool__(self):
                """
                Make the object always evaluate as truthy.

                Returns:
                    True indicating the object is truthy.
                """
                return True

            def run_pending(self):
                """
                Signal the scheduler to execute pending jobs now and request the global scheduler thread to stop.

                Sets the local run event to trigger immediate execution of pending jobs. If a global scheduler stop event is present, sets that event to request the global scheduler thread to terminate after processing.
                """
                run_event.set()
                if pl._global_scheduler_stop_event:
                    pl._global_scheduler_stop_event.set()

            def clear(self):
                """
                Remove all scheduled jobs associated with this plugin.

                This method clears any entries the global scheduler has registered for the plugin instance so no future scheduled tasks for this plugin will run.
                """
                pass

        original_schedule = pl.schedule
        pl.schedule = FakeSchedule()
        pl._global_scheduler_thread = None
        pl._global_scheduler_stop_event = None

        try:
            start_global_scheduler()
            run_event.wait(timeout=1.0)
            stop_global_scheduler()
        finally:
            pl.schedule = original_schedule

        self.assertTrue(run_event.is_set())

    @patch("mmrelay.plugin_loader.threading")
    @patch("mmrelay.plugin_loader.schedule", None)
    def test_start_global_scheduler_no_schedule_library(self, mock_threading):
        """Test that start_global_scheduler exits early when schedule is None."""
        start_global_scheduler()

        # Should not create thread when schedule is None
        mock_threading.Thread.assert_not_called()

    @patch("mmrelay.plugin_loader.threading")
    @patch("mmrelay.plugin_loader.schedule")
    def test_start_global_scheduler_already_running(
        self, mock_schedule, mock_threading
    ):
        """Test that start_global_scheduler exits early when already running."""

        # Simulate already running thread
        pl._global_scheduler_thread = MagicMock()
        pl._global_scheduler_thread.is_alive.return_value = True

        start_global_scheduler()

        # Should not create new thread
        mock_threading.Thread.assert_not_called()

    @patch("mmrelay.plugin_loader.threading")
    @patch("mmrelay.plugin_loader.schedule")
    def test_stop_global_scheduler_stops_thread(self, mock_schedule, mock_threading):
        """Test that stop_global_scheduler stops the scheduler thread."""

        # Setup running thread
        mock_event = MagicMock()
        mock_thread = MagicMock()
        mock_thread.is_alive.side_effect = [True, False]
        pl._global_scheduler_thread = mock_thread
        pl._global_scheduler_stop_event = mock_event

        stop_global_scheduler()

        mock_event.set.assert_called_once()
        mock_thread.join.assert_called_once_with(timeout=5)
        mock_schedule.clear.assert_called_once()
        self.assertIsNone(pl._global_scheduler_thread)

    @patch("mmrelay.plugin_loader.threading")
    def test_stop_global_scheduler_no_thread(self, mock_threading):
        """Test that stop_global_scheduler exits early when no thread exists."""

        # Ensure no thread is running
        pl._global_scheduler_thread = None

        stop_global_scheduler()

        # Should not call any threading methods

        mock_threading.Event.assert_not_called()

    @patch("mmrelay.plugin_loader._run_git")
    @patch("mmrelay.plugin_loader._is_repo_url_allowed")
    @patch("mmrelay.plugin_loader.logger")
    @patch("os.path.isdir")
    def test_clone_or_update_repo_commit_ref_type_validation(
        self, mock_isdir, mock_logger, mock_is_allowed, mock_run_git
    ):
        """Test that 'commit' is accepted as a valid ref type."""
        mock_is_allowed.return_value = True
        mock_isdir.return_value = False  # Repo doesn't exist
        mock_run_git.side_effect = subprocess.CalledProcessError(
            1, "git"
        )  # Git operations fail
        ref = {"type": "commit", "value": "deadbeef"}

        result = clone_or_update_repo(
            "https://github.com/user/repo.git", ref, self.temp_plugins_dir
        )

        self.assertFalse(
            result
        )  # Function should return False on git operation failures
        # Verify no "Invalid ref type" error was logged (commit ref type should be accepted)
        for call_args in mock_logger.error.call_args_list:
            self.assertNotIn("Invalid ref type", str(call_args))

    def test_validate_clone_inputs_valid_branch(self):
        """Test _validate_clone_inputs with valid branch ref."""
        ref = {"type": "branch", "value": "main"}
        result = _validate_clone_inputs("https://github.com/user/repo.git", ref)

        is_valid, repo_url, ref_type, ref_value, repo_name = result
        self.assertTrue(is_valid)
        self.assertEqual(repo_url, "https://github.com/user/repo.git")
        self.assertEqual(ref_type, "branch")
        self.assertEqual(ref_value, "main")
        self.assertEqual(repo_name, "repo")

    def test_validate_clone_inputs_valid_tag(self):
        """Test _validate_clone_inputs with valid tag ref."""
        ref = {"type": "tag", "value": "v1.0.0"}
        result = _validate_clone_inputs("https://github.com/user/repo.git", ref)

        is_valid, repo_url, ref_type, ref_value, repo_name = result
        self.assertTrue(is_valid)
        self.assertEqual(repo_url, "https://github.com/user/repo.git")
        self.assertEqual(ref_type, "tag")
        self.assertEqual(ref_value, "v1.0.0")
        self.assertEqual(repo_name, "repo")

    def test_validate_clone_inputs_valid_commit(self):
        """Test _validate_clone_inputs with valid commit ref."""
        ref = {"type": "commit", "value": "a1b2c3d4"}
        result = _validate_clone_inputs("https://github.com/user/repo.git", ref)

        is_valid, repo_url, ref_type, ref_value, repo_name = result
        self.assertTrue(is_valid)
        self.assertEqual(repo_url, "https://github.com/user/repo.git")
        self.assertEqual(ref_type, "commit")
        self.assertEqual(ref_value, "a1b2c3d4")
        self.assertEqual(repo_name, "repo")

    def test_validate_clone_inputs_invalid_ref_type(self):
        """Test _validate_clone_inputs with invalid ref type."""
        ref = {"type": "invalid", "value": "main"}
        result = _validate_clone_inputs("https://github.com/user/repo.git", ref)

        self.assertEqual(result, (False, None, None, None, None))

    def test_validate_clone_inputs_missing_ref_value(self):
        """Test _validate_clone_inputs with missing ref value."""
        ref = {"type": "branch"}
        result = _validate_clone_inputs("https://github.com/user/repo.git", ref)

        self.assertEqual(result, (False, None, None, None, None))

    def test_validate_clone_inputs_empty_url(self):
        """Test _validate_clone_inputs with empty URL."""
        ref = {"type": "branch", "value": "main"}
        result = _validate_clone_inputs("", ref)

        self.assertEqual(result, (False, None, None, None, None))

    def test_validate_clone_inputs_none_url(self):
        """Test _validate_clone_inputs with None URL."""
        ref = {"type": "branch", "value": "main"}
        # The function handles None by converting to empty string internally
        result = _validate_clone_inputs(None, ref)  # type: ignore[arg-type]

        self.assertEqual(result, (False, None, None, None, None))

    def test_validate_clone_inputs_invalid_commit_too_short(self):
        """Test _validate_clone_inputs with commit hash too short (< 7 chars)."""
        ref = {"type": "commit", "value": "abc123"}  # 6 chars
        result = _validate_clone_inputs("https://github.com/user/repo.git", ref)

        self.assertEqual(result, (False, None, None, None, None))

    def test_validate_clone_inputs_invalid_commit_too_long(self):
        """Test _validate_clone_inputs with commit hash too long (> 40 chars)."""
        ref = {
            "type": "commit",
            "value": "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2c3d4",
        }  # 41 chars
        result = _validate_clone_inputs("https://github.com/user/repo.git", ref)

        self.assertEqual(result, (False, None, None, None, None))

    def test_validate_clone_inputs_invalid_commit_non_hex(self):
        """Test _validate_clone_inputs with commit hash containing non-hex characters."""
        ref = {"type": "commit", "value": "g1b2c3d"}  # contains 'g'
        result = _validate_clone_inputs("https://github.com/user/repo.git", ref)

        self.assertEqual(result, (False, None, None, None, None))

    @patch("mmrelay.plugin_loader._run_git")
    @patch("mmrelay.plugin_loader.logger")
    def test_clone_new_repo_to_branch_or_tag_default_branch_success(
        self, mock_logger, mock_run_git
    ):
        """Test _clone_new_repo_to_branch_or_tag with default branch success."""
        result = _clone_new_repo_to_branch_or_tag(
            "https://github.com/user/repo.git",
            self.temp_repo_path,
            "branch",
            "main",
            "repo",
            self.temp_plugins_dir,
            True,  # is_default_branch
        )

        self.assertTrue(result)
        # Should clone with --branch main and --filter=blob:none
        mock_run_git.assert_called_with(
            [
                "git",
                "clone",
                "--filter=blob:none",
                "--branch",
                "main",
                "https://github.com/user/repo.git",
                "repo",
            ],
            cwd=self.temp_plugins_dir,
            timeout=TEST_GIT_TIMEOUT,
            retry_attempts=1,
        )
        mock_logger.info.assert_called_with(
            "Cloned repository %s from %s at %s %s",
            "repo",
            "https://github.com/user/repo.git",
            "branch",
            "main",
        )

    @patch("mmrelay.plugin_loader._run_git")
    def test_clone_new_repo_to_branch_or_tag_default_branch_fallback(
        self, mock_run_git
    ):
        """Test _clone_new_repo_to_branch_or_tag with default branch fallback."""
        # First call fails, second succeeds
        mock_run_git.side_effect = [
            subprocess.CalledProcessError(1, "git"),  # main fails
            None,  # master succeeds
        ]

        result = _clone_new_repo_to_branch_or_tag(
            "https://github.com/user/repo.git",
            self.temp_repo_path,
            "branch",
            "main",
            "repo",
            self.temp_plugins_dir,
            True,  # is_default_branch
        )

        self.assertTrue(result)
        # Should try main first, then master
        calls = mock_run_git.call_args_list
        self.assertEqual(len(calls), 2)
        self.assertEqual(
            calls[0][0][0],
            [
                "git",
                "clone",
                "--filter=blob:none",
                "--branch",
                "main",
                "https://github.com/user/repo.git",
                "repo",
            ],
        )
        self.assertEqual(
            calls[1][0][0],
            [
                "git",
                "clone",
                "--filter=blob:none",
                "--branch",
                "master",
                "https://github.com/user/repo.git",
                "repo",
            ],
        )

    @patch("mmrelay.plugin_loader._run_git")
    def test_clone_new_repo_to_branch_or_tag_default_branch_final_fallback(
        self, mock_run_git
    ):
        """Test _clone_new_repo_to_branch_or_tag with final fallback to default branch."""
        # Both main and master fail, fallback to clone without branch
        mock_run_git.side_effect = [
            subprocess.CalledProcessError(1, "git"),  # main fails
            subprocess.CalledProcessError(1, "git"),  # master fails
            None,  # clone succeeds
        ]

        result = _clone_new_repo_to_branch_or_tag(
            "https://github.com/user/repo.git",
            self.temp_repo_path,
            "branch",
            "main",
            "repo",
            self.temp_plugins_dir,
            True,  # is_default_branch
        )

        self.assertTrue(result)
        # Should try main, master, then clone without branch
        calls = mock_run_git.call_args_list
        self.assertEqual(len(calls), 3)
        self.assertEqual(
            calls[2][0][0],
            [
                "git",
                "clone",
                "--filter=blob:none",
                "https://github.com/user/repo.git",
                "repo",
            ],
        )

    @patch("mmrelay.plugin_loader._run_git")
    @patch("mmrelay.plugin_loader.logger")
    def test_clone_new_repo_to_branch_or_tag_tag_success(
        self, mock_logger, mock_run_git
    ):
        """Test _clone_new_repo_to_branch_or_tag with tag success."""
        mock_run_git.side_effect = lambda *args, **_kwargs: (
            subprocess.CompletedProcess(args[0], 0, stdout="some_commit\n", stderr="")
            if "rev-parse" in args[0] and "HEAD" in args[0]
            else (
                subprocess.CompletedProcess(
                    args[0], 0, stdout="tag_commit\n", stderr=""
                )
                if "rev-parse" in args[0]
                else subprocess.CompletedProcess(args[0], 0, stdout="", stderr="")
            )
        )
        result = _clone_new_repo_to_branch_or_tag(
            "https://github.com/user/repo.git",
            self.temp_repo_path,
            "tag",
            "v1.0.0",
            "repo",
            self.temp_plugins_dir,
            False,  # not default branch
        )

        self.assertTrue(result)
        # Should clone default branch, then fetch and checkout tag
        expected_calls = [
            call(
                [
                    "git",
                    "clone",
                    "--filter=blob:none",
                    "https://github.com/user/repo.git",
                    "repo",
                ],
                cwd=self.temp_plugins_dir,
                timeout=TEST_GIT_TIMEOUT,
                retry_attempts=1,
            ),
            # Check if already at the tag's commit
            call(
                ["git", "-C", f"{self.temp_plugins_dir}/repo", "rev-parse", "HEAD"],
                capture_output=True,
            ),
            call(
                [
                    "git",
                    "-C",
                    f"{self.temp_plugins_dir}/repo",
                    "rev-parse",
                    "v1.0.0^{commit}",
                ],
                capture_output=True,
            ),
            call(
                [
                    "git",
                    "-C",
                    f"{self.temp_plugins_dir}/repo",
                    "fetch",
                    "origin",
                    "refs/tags/v1.0.0",
                ],
                timeout=TEST_GIT_TIMEOUT,
                retry_attempts=pl.GIT_RETRY_ATTEMPTS,
                retry_delay=pl.GIT_RETRY_DELAY_SECONDS,
            ),
            call(
                ["git", "-C", f"{self.temp_plugins_dir}/repo", "checkout", "v1.0.0"],
                timeout=TEST_GIT_TIMEOUT,
            ),
        ]
        mock_run_git.assert_has_calls(expected_calls)
        mock_logger.info.assert_any_call(
            "Cloned repository %s from %s at %s %s",
            "repo",
            "https://github.com/user/repo.git",
            "tag",
            "default branch",
        )
        mock_logger.info.assert_any_call(
            "Successfully fetched and checked out tag %s for %s", "v1.0.0", "repo"
        )

    @patch("mmrelay.plugin_loader._run_git")
    def test_clone_new_repo_to_branch_or_tag_tag_fetch_fallback(self, mock_run_git):
        """Test _clone_new_repo_to_branch_or_tag with tag fetch fallback."""
        # Clone succeeds, rev-parse succeed but don't match, then fetch and checkout succeed
        mock_run_git.side_effect = [
            subprocess.CompletedProcess([], 0),  # clone succeeds
            subprocess.CompletedProcess(
                [], 0, stdout="different_commit\n"
            ),  # rev-parse HEAD
            subprocess.CompletedProcess([], 0, stdout="tag_commit\n"),  # rev-parse tag
            subprocess.CompletedProcess([], 0),  # fetch succeeds
            subprocess.CompletedProcess([], 0),  # checkout succeeds
        ]

        result = _clone_new_repo_to_branch_or_tag(
            "https://github.com/user/repo.git",
            self.temp_repo_path,
            "tag",
            "v1.0.0",
            "repo",
            self.temp_plugins_dir,
            False,  # not default branch
        )

        self.assertTrue(result)
        # Should fetch and checkout after clone
        calls = mock_run_git.call_args_list
        self.assertEqual(len(calls), 5)
        self.assertEqual(
            calls[3][0][0],
            ["git", "-C", self.temp_repo_path, "fetch", "origin", "refs/tags/v1.0.0"],
        )
        self.assertEqual(
            calls[4][0][0], ["git", "-C", self.temp_repo_path, "checkout", "v1.0.0"]
        )

    @patch("mmrelay.plugin_loader._run_git")
    def test_clone_new_repo_to_branch_or_tag_tag_fetch_fallback_alt(self, mock_run_git):
        """Test _clone_new_repo_to_branch_or_tag with alternative tag fetch."""
        # Clone succeeds, rev-parse succeed but don't match, first fetch fails, alternative fetch succeeds, checkout succeeds
        mock_run_git.side_effect = [
            subprocess.CompletedProcess([], 0),  # clone succeeds
            subprocess.CompletedProcess(
                [], 0, stdout="different_commit\n"
            ),  # rev-parse HEAD
            subprocess.CompletedProcess([], 0, stdout="tag_commit\n"),  # rev-parse tag
            subprocess.CalledProcessError(1, "git"),  # first fetch fails
            subprocess.CompletedProcess([], 0),  # alternative fetch succeeds
            subprocess.CompletedProcess([], 0),  # checkout succeeds
        ]

        result = _clone_new_repo_to_branch_or_tag(
            "https://github.com/user/repo.git",
            self.temp_repo_path,
            "tag",
            "v1.0.0",
            "repo",
            self.temp_plugins_dir,
            False,  # not default branch
        )

        self.assertTrue(result)
        # Should try alternative fetch format
        calls = mock_run_git.call_args_list
        self.assertEqual(
            calls[4][0][0],
            [
                "git",
                "-C",
                self.temp_repo_path,
                "fetch",
                "--tags",
            ],
        )

    @patch("mmrelay.plugin_loader._run_git")
    def test_clone_new_repo_to_branch_or_tag_tag_as_branch_fallback(self, mock_run_git):
        """Test _clone_new_repo_to_branch_or_tag with tag as branch fallback."""
        mock_run_git.side_effect = [
            subprocess.CalledProcessError(1, "git"),  # clone --branch fails
            None,  # clone without branch succeeds
            subprocess.CalledProcessError(1, "git"),  # fetch tag fails
            subprocess.CalledProcessError(1, "git"),  # alt fetch fails
            subprocess.CalledProcessError(1, "git"),  # fetch as branch fails
        ]
        result = _clone_new_repo_to_branch_or_tag(
            "https://github.com/user/repo.git",
            self.temp_repo_path,
            "tag",
            "v1.0.0",
            "repo",
            self.temp_plugins_dir,
            False,  # not default branch
        )
        self.assertFalse(
            result
        )  # Tag checkout should fail, so overall operation should fail

    @patch("mmrelay.plugin_loader._run_git")
    @patch("mmrelay.plugin_loader.logger")
    def test_clone_new_repo_to_branch_or_tag_clone_failure(
        self, mock_logger, mock_run_git
    ):
        """Test _clone_new_repo_to_branch_or_tag with clone failure."""
        # All clone attempts fail
        mock_run_git.side_effect = subprocess.CalledProcessError(1, "git")

        result = _clone_new_repo_to_branch_or_tag(
            "https://github.com/user/repo.git",
            self.temp_repo_path,
            "branch",
            "main",
            "repo",
            self.temp_plugins_dir,
            True,  # is_default_branch
        )

        self.assertFalse(result)
        mock_logger.error.assert_called_with(
            "Error cloning repository %s; please manually clone into %s: %s",
            "repo",
            self.temp_repo_path,
            ANY,
        )

    @patch("mmrelay.plugin_loader._run_git")
    @patch("mmrelay.plugin_loader.logger")
    def test_clone_new_repo_to_branch_or_tag_file_not_found(
        self, mock_logger, mock_run_git
    ):
        """Test _clone_new_repo_to_branch_or_tag with FileNotFoundError."""
        mock_run_git.side_effect = FileNotFoundError("git not found")
        result = _clone_new_repo_to_branch_or_tag(
            "https://github.com/user/repo.git",
            self.temp_repo_path,
            "branch",
            "main",
            "repo",
            self.temp_plugins_dir,
            True,  # is_default_branch
        )
        self.assertFalse(result)
        mock_logger.exception.assert_called_with(
            "Error cloning repository repo; git not found."
        )

    @patch("mmrelay.plugin_loader._run_git")
    @patch("mmrelay.plugin_loader.logger")
    def test_update_existing_repo_to_branch_or_tag_default_branch_already_on_branch(
        self, mock_logger, mock_run_git
    ):
        """Test updating when already on the correct default branch."""
        # Mock fetch, checkout, and pull sequence
        mock_run_git.side_effect = [
            subprocess.CompletedProcess([], 0),  # fetch succeeds
            subprocess.CompletedProcess([], 0),  # checkout succeeds
            subprocess.CompletedProcess([], 0),  # pull succeeds
        ]

        result = _update_existing_repo_to_branch_or_tag(
            self.temp_repo_path,
            "branch",
            "main",
            "repo",
            True,  # is_default_branch
            list(DEFAULT_BRANCHES),
        )

        self.assertTrue(result)
        mock_logger.info.assert_called_with(
            "Updated repository %s to %s %s", "repo", "branch", "main"
        )

    @patch("mmrelay.plugin_loader._run_git")
    @patch("mmrelay.plugin_loader.logger")
    def test_update_existing_repo_to_branch_or_tag_default_branch_switch(
        self, mock_logger, mock_run_git
    ):
        """Test switching to a different default branch."""
        # Mock fetch, checkout, and pull sequence
        mock_run_git.side_effect = [
            subprocess.CompletedProcess([], 0),  # fetch succeeds
            subprocess.CompletedProcess([], 0),  # checkout main succeeds
            subprocess.CompletedProcess([], 0),  # pull succeeds
        ]

        result = _update_existing_repo_to_branch_or_tag(
            self.temp_repo_path,
            "branch",
            "main",
            "repo",
            True,  # is_default_branch
            list(DEFAULT_BRANCHES),
        )

        self.assertTrue(result)
        mock_logger.info.assert_called_with(
            "Updated repository %s to %s %s", "repo", "branch", "main"
        )

    @patch("mmrelay.plugin_loader._run_git")
    @patch("mmrelay.plugin_loader.logger")
    def test_update_existing_repo_to_branch_or_tag_non_default_branch(
        self, mock_logger, mock_run_git
    ):
        """Test updating a non-default branch."""
        mock_run_git.side_effect = [
            subprocess.CompletedProcess([], 0),  # fetch succeeds
            subprocess.CompletedProcess([], 0),  # checkout succeeds
            subprocess.CompletedProcess([], 0),  # pull succeeds
        ]

        result = _update_existing_repo_to_branch_or_tag(
            self.temp_repo_path,
            "branch",
            "feature-branch",
            "repo",
            False,  # is_default_branch
            list(DEFAULT_BRANCHES),
        )

        self.assertTrue(result)
        mock_logger.info.assert_called_with(
            "Updated repository %s to %s %s", "repo", "branch", "feature-branch"
        )

    @patch("mmrelay.plugin_loader._run_git")
    @patch("mmrelay.plugin_loader.logger")
    def test_update_existing_repo_to_branch_or_tag_tag_update(
        self, mock_logger, mock_run_git
    ):
        """Test updating to a tag."""
        mock_run_git.side_effect = [
            subprocess.CompletedProcess([], 0),  # fetch succeeds
            MagicMock(stdout="abc123\n"),  # current commit
            subprocess.CalledProcessError(
                1, "git"
            ),  # rev-parse tag fails (tag not local)
            subprocess.CompletedProcess([], 0),  # fetch tag succeeds
            subprocess.CompletedProcess([], 0),  # checkout succeeds
        ]

        result = _update_existing_repo_to_branch_or_tag(
            self.temp_repo_path,
            "tag",
            "v1.0.0",
            "repo",
            False,  # is_default_branch (tags are not default branches)
            list(DEFAULT_BRANCHES),
        )

        self.assertTrue(result)
        mock_logger.info.assert_called_with(
            "Successfully fetched and checked out tag %s for %s", "v1.0.0", "repo"
        )

    @patch("mmrelay.plugin_loader._run_git")
    @patch("mmrelay.plugin_loader.logger")
    def test_update_existing_repo_to_branch_or_tag_fetch_failure(
        self, mock_logger, mock_run_git
    ):
        """Test handling of fetch failure."""
        mock_run_git.side_effect = subprocess.CalledProcessError(
            1, "git"
        )  # all git operations fail

        result = _update_existing_repo_to_branch_or_tag(
            self.temp_repo_path,
            "branch",
            "main",
            "repo",
            True,  # is_default_branch
            list(DEFAULT_BRANCHES),
        )

        # Should return False when all operations fail
        self.assertFalse(result)
        mock_logger.warning.assert_called()


class TestCollectRequirements(unittest.TestCase):
    """Test cases for _collect_requirements function."""

    def setUp(self):
        """
        Create a temporary directory for the test and register its removal as cleanup.

        The directory path is stored on self.temp_dir and will be removed after the test
        via shutil.rmtree(self.temp_dir, ignore_errors=True).
        """
        self.temp_dir = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(self.temp_dir, ignore_errors=True))

    def test_collect_requirements_basic(self):
        """Test collecting basic requirements from a simple file."""
        req_file = os.path.join(self.temp_dir, "requirements.txt")
        with open(req_file, "w") as f:
            f.write("requests==2.28.0\n")
            f.write("numpy>=1.20.0\n")
            f.write("# This is a comment\n")
            f.write("\n")  # Blank line

        result = _collect_requirements(req_file)
        expected = ["requests==2.28.0", "numpy>=1.20.0"]
        self.assertEqual(result, expected)

    def test_collect_requirements_with_inline_comments(self):
        """Test handling inline comments."""
        req_file = os.path.join(self.temp_dir, "requirements.txt")
        with open(req_file, "w") as f:
            f.write("requests==2.28.0  # HTTP library\n")
            f.write("numpy>=1.20.0    # Numerical computing\n")

        result = _collect_requirements(req_file)
        expected = ["requests==2.28.0", "numpy>=1.20.0"]
        self.assertEqual(result, expected)

    def test_collect_requirements_with_include(self):
        """Test handling -r include directive."""
        # Create main requirements file
        main_req = os.path.join(self.temp_dir, "requirements.txt")
        included_req = os.path.join(self.temp_dir, "base.txt")

        with open(included_req, "w") as f:
            f.write("requests==2.28.0\n")
            f.write("numpy>=1.20.0\n")

        with open(main_req, "w") as f:
            f.write("-r base.txt\n")
            f.write("scipy>=1.7.0\n")

        result = _collect_requirements(main_req)
        expected = ["requests==2.28.0", "numpy>=1.20.0", "scipy>=1.7.0"]
        self.assertEqual(result, expected)

    def test_collect_requirements_with_constraint(self):
        """Test handling -c constraint directive."""
        req_file = os.path.join(self.temp_dir, "requirements.txt")
        constraint_file = os.path.join(self.temp_dir, "constraints.txt")

        with open(constraint_file, "w") as f:
            f.write("requests<=2.30.0\n")

        with open(req_file, "w") as f:
            f.write("-c constraints.txt\n")
            f.write("requests>=2.25.0\n")

        result = _collect_requirements(req_file)
        # The function appears to include constraints in the output
        expected = ["requests<=2.30.0", "requests>=2.25.0"]
        self.assertEqual(result, expected)

    def test_collect_requirements_with_complex_flags(self):
        """Test handling complex requirement flags."""
        req_file = os.path.join(self.temp_dir, "requirements.txt")
        with open(req_file, "w") as f:
            f.write("--requirement=requirements-dev.txt\n")
            f.write("--constraint=constraints.txt\n")
            f.write("package>=1.0.0 --extra-index-url https://pypi.org/simple\n")

        # Create the referenced files
        dev_req = os.path.join(self.temp_dir, "requirements-dev.txt")
        constraint_file = os.path.join(self.temp_dir, "constraints.txt")

        with open(dev_req, "w") as f:
            f.write("pytest>=6.0.0\n")

        with open(constraint_file, "w") as f:
            f.write("pytest<=7.0.0\n")

        result = _collect_requirements(req_file)
        # Requirements are kept as full lines to preserve PEP 508 syntax
        expected = [
            "pytest>=6.0.0",
            "pytest<=7.0.0",
            "package>=1.0.0 --extra-index-url https://pypi.org/simple",
        ]
        self.assertEqual(result, expected)

    def test_collect_requirements_nonexistent_file(self):
        """Test handling nonexistent requirements file."""
        nonexistent_file = os.path.join(self.temp_dir, "nonexistent.txt")

        result = _collect_requirements(nonexistent_file)
        self.assertEqual(result, [])

    def test_collect_requirements_recursive_include_detection(self):
        """Test detection of recursive includes."""
        req1 = os.path.join(self.temp_dir, "req1.txt")
        req2 = os.path.join(self.temp_dir, "req2.txt")

        with open(req1, "w") as f:
            f.write("-r req2.txt\n")
            f.write("package1>=1.0.0\n")

        with open(req2, "w") as f:
            f.write("-r req1.txt\n")  # Recursive include
            f.write("package2>=1.0.0\n")

        result = _collect_requirements(req1)
        # Should handle recursion gracefully and not crash
        self.assertIsInstance(result, list)

    @patch("mmrelay.plugin_loader.logger")
    def test_collect_requirements_malformed_requirement_directive(self, mock_logger):
        """Test handling of malformed requirement directives."""
        req_file = os.path.join(self.temp_dir, "requirements.txt")
        with open(req_file, "w") as f:
            f.write("-r \n")  # Malformed - missing file
            f.write("requests==2.28.0\n")

        result = _collect_requirements(req_file)

        # Should log warning for malformed directive
        mock_logger.warning.assert_called()
        # Should still include the valid requirement
        self.assertIn("requests==2.28.0", result)

    @patch("mmrelay.plugin_loader.logger")
    def test_collect_requirements_io_error(self, mock_logger):
        """Test handling of IO errors during file reading."""
        req_file = os.path.join(self.temp_dir, "requirements.txt")

        # Create file and then mock open to raise IOError
        with open(req_file, "w") as f:
            f.write("requests==2.28.0\n")

        with patch("builtins.open", side_effect=IOError("Permission denied")):
            result = _collect_requirements(req_file)

        # Should handle IOError gracefully
        self.assertEqual(result, [])

    def test_collect_requirements_empty_file(self):
        """Test handling empty requirements file."""
        req_file = os.path.join(self.temp_dir, "empty.txt")
        with open(req_file, "w"):
            pass  # Create empty file

        result = _collect_requirements(req_file)
        self.assertEqual(result, [])
