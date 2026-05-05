"""Tests for plugin loader: Requirements collection, install target identity/validation, hashing/filtering."""

# Decomposed from test_plugin_loader_deps.py

import hashlib
import json
import os
import shutil
import tempfile
import unittest
from typing import Any, Optional
from unittest.mock import patch

import mmrelay.plugin_loader as pl
from mmrelay.plugin_loader import _collect_requirements


class TestRequirementsInfrastructure(unittest.TestCase):
    """Test cases for requirements install target identity, validation, hashing, and temporary helpers."""

    def setUp(self):
        """Set up temporary directories for requirements tests."""
        self.temp_dir = tempfile.mkdtemp()
        self.repo_path = os.path.join(self.temp_dir, "test-plugin")
        self.deps_dir = os.path.join(self.temp_dir, "deps")
        os.makedirs(self.deps_dir, exist_ok=True)
        os.makedirs(self.repo_path, exist_ok=True)

    def tearDown(self):
        """Clean up temporary directory."""
        shutil.rmtree(self.temp_dir)

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

        def fake_get_path(name: str, scheme: str, **_kwargs: Any) -> Optional[str]:
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

        def fake_get_path(name: str, scheme: str, **_kwargs: Any) -> Optional[str]:
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
        assert temp_path is not None  # narrow type for pyright
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
