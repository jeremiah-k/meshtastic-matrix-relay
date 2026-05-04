"""Tests for plugin loader: URL validation, host allowlisting, requirement filtering."""

# Decomposed from test_plugin_loader.py

import os
import unittest
from unittest.mock import patch

import mmrelay.plugin_loader as pl
from mmrelay.constants.plugins import DEFAULT_ALLOWED_COMMUNITY_HOSTS
from mmrelay.plugin_loader import _filter_risky_requirements, _is_repo_url_allowed
from tests._plugin_loader_helpers import BaseGitTest


class TestPluginSecurityGuards(BaseGitTest):
    """Tests for plugin security helper utilities."""

    def setUp(self):
        """
        Prepare test fixture by invoking the superclass setup, attaching the module-level plugin loader to `self.pl`, and saving its current `config` attribute (or `None`) to `self.original_config` for later restoration.
        """
        super().setUp()
        self.pl = pl
        self.original_config = getattr(pl, "config", None)

    def tearDown(self):
        """
        Cleans up temporary test directories and resources created for Git-related tests.
        """
        super().tearDown()

    def test_repo_url_allowed_https_known_host(self):
        self.pl.config = {}
        self.assertTrue(_is_repo_url_allowed("https://github.com/example/project.git"))

    def test_repo_url_rejected_for_unknown_host(self):
        self.pl.config = {}
        self.assertFalse(
            _is_repo_url_allowed("https://malicious.example.invalid/repo.git")
        )

    def test_repo_url_rejected_for_http_scheme(self):
        self.pl.config = {}
        self.assertFalse(_is_repo_url_allowed("http://github.com/example/project.git"))

    def test_repo_url_allows_custom_host_from_config(self):
        self.pl.config = {"security": {"community_repo_hosts": ["example.org"]}}
        self.assertTrue(_is_repo_url_allowed("https://code.example.org/test.git"))

    def test_local_repo_requires_opt_in(self):
        temp_path = os.path.abspath("some/local/path")
        self.pl.config = {}
        self.assertFalse(_is_repo_url_allowed(temp_path))
        self.pl.config = {"security": {"allow_local_plugin_paths": True}}
        with patch("os.path.exists", return_value=True):
            self.assertTrue(_is_repo_url_allowed(temp_path))

    def test_filter_risky_requirements_blocks_vcs_by_default(self):
        self.pl.config = {}
        requirements = [
            "safe-package==1.0.0",
            "git+https://github.com/example/risky.git",
            "--extra-index-url https://mirror.example",
            "another-safe",
        ]
        safe, flagged, _allow = _filter_risky_requirements(requirements)
        self.assertFalse(_allow)
        self.assertEqual(
            flagged,
            [
                "git+https://github.com/example/risky.git",
                "--extra-index-url https://mirror.example",
            ],
        )
        self.assertIn("safe-package==1.0.0", safe)
        self.assertIn("another-safe", safe)

    def test_filter_risky_requirements_can_allow_via_config(self):
        self.pl.config = {"security": {"allow_untrusted_dependencies": True}}
        requirements = ["pkg @ git+ssh://github.com/example/pkg.git"]
        safe, flagged, _allow = _filter_risky_requirements(requirements)
        self.assertTrue(_allow)
        # With new behavior, flagged requirements are still classified as flagged
        # Configuration decision happens in caller
        self.assertEqual(safe, [])
        self.assertEqual(flagged, requirements)

    def test_get_allowed_repo_hosts_empty_list_override(self):
        """Explicit empty list should override default hosts."""
        self.pl.config = {"security": {"community_repo_hosts": []}}
        from mmrelay.plugin_loader import _get_allowed_repo_hosts

        result = _get_allowed_repo_hosts()
        self.assertEqual(result, [])

    def test_get_allowed_repo_hosts_none_uses_default(self):
        """None config should use default hosts."""
        self.pl.config = {"security": {"community_repo_hosts": None}}
        from mmrelay.plugin_loader import _get_allowed_repo_hosts

        result = _get_allowed_repo_hosts()
        expected = list(DEFAULT_ALLOWED_COMMUNITY_HOSTS)
        self.assertEqual(result, expected)

    def test_get_allowed_repo_hosts_string_is_accepted(self):
        """String value coerces to a single host entry."""
        self.pl.config = {"security": {"community_repo_hosts": "invalid"}}
        from mmrelay.plugin_loader import _get_allowed_repo_hosts

        result = _get_allowed_repo_hosts()
        # String gets converted to list, then filtered
        expected = ["invalid"]
        self.assertEqual(result, expected)

    def test_get_allowed_repo_filters_empty_strings(self):
        """Empty strings should be filtered out."""
        self.pl.config = {
            "security": {
                "community_repo_hosts": ["github.com", "", "gitlab.com", "   "]
            }
        }
        from mmrelay.plugin_loader import _get_allowed_repo_hosts

        result = _get_allowed_repo_hosts()
        expected = ["github.com", "gitlab.com"]
        self.assertEqual(result, expected)

    def test_get_allowed_repo_hosts_integer_type_uses_default(self):
        """Integer type should use default hosts."""
        self.pl.config = {"security": {"community_repo_hosts": 123}}
        from mmrelay.plugin_loader import _get_allowed_repo_hosts

        result = _get_allowed_repo_hosts()
        expected = list(DEFAULT_ALLOWED_COMMUNITY_HOSTS)
        self.assertEqual(result, expected)


class TestURLValidation(unittest.TestCase):
    """Test cases for URL validation and security functions."""

    def setUp(self):
        """
        Set up the test by attaching the plugin loader and saving its original configuration.

        Stores the supplied plugin loader instance on self.pl and records its current
        `config` attribute (or `None` if absent) in `self.original_config` for later restoration.
        """
        self.pl = pl
        self.original_config = getattr(pl, "config", None)

    def tearDown(self):
        """
        Restore the original plugin loader configuration after a test.

        Reassigns the saved original configuration back to the plugin loader's `config` attribute to restore global state modified during the test.
        """
        self.pl.config = self.original_config

    def test_normalize_repo_target_ssh_git_at(self):
        """Test SSH URL normalization with git@ prefix."""
        from mmrelay.plugin_loader import _normalize_repo_target

        scheme, host = _normalize_repo_target("git@github.com:user/repo.git")
        self.assertEqual(scheme, "ssh")
        self.assertEqual(host, "github.com")

    def test_normalize_repo_target_ssh_git_at_with_port(self):
        """Test SSH URL normalization with port."""
        from mmrelay.plugin_loader import _normalize_repo_target

        scheme, host = _normalize_repo_target("git@github.com:2222:user/repo.git")
        self.assertEqual(scheme, "ssh")
        self.assertEqual(host, "github.com")

    def test_normalize_repo_target_https_url(self):
        """Test HTTPS URL normalization."""
        from mmrelay.plugin_loader import _normalize_repo_target

        scheme, host = _normalize_repo_target("https://github.com/user/repo.git")
        self.assertEqual(scheme, "https")
        self.assertEqual(host, "github.com")

    def test_normalize_repo_target_git_ssh_scheme(self):
        """Test git+ssh scheme normalization."""
        from mmrelay.plugin_loader import _normalize_repo_target

        scheme, host = _normalize_repo_target("git+ssh://github.com/user/repo.git")
        self.assertEqual(scheme, "ssh")
        self.assertEqual(host, "github.com")

    def test_normalize_repo_target_ssh_git_scheme(self):
        """Test ssh+git scheme normalization."""
        from mmrelay.plugin_loader import _normalize_repo_target

        scheme, host = _normalize_repo_target("ssh+git://github.com/user/repo.git")
        self.assertEqual(scheme, "ssh")
        self.assertEqual(host, "github.com")

    def test_normalize_repo_target_empty_string(self):
        """Test empty URL normalization."""
        from mmrelay.plugin_loader import _normalize_repo_target

        scheme, host = _normalize_repo_target("")
        self.assertEqual(scheme, "")
        self.assertEqual(host, "")

    def test_normalize_repo_target_none(self):
        """Test None URL normalization."""
        from mmrelay.plugin_loader import _normalize_repo_target

        scheme, host = _normalize_repo_target(None)  # type: ignore[arg-type]
        self.assertEqual(scheme, "")
        self.assertEqual(host, "")

    def test_host_in_allowlist_exact_match(self):
        """Test exact host match in allowlist."""
        from mmrelay.plugin_loader import _host_in_allowlist

        result = _host_in_allowlist("github.com", ["github.com", "gitlab.com"])
        self.assertTrue(result)

    def test_host_in_allowlist_subdomain_match(self):
        """Test subdomain match in allowlist."""
        from mmrelay.plugin_loader import _host_in_allowlist

        result = _host_in_allowlist("api.github.com", ["github.com", "gitlab.com"])
        self.assertTrue(result)

    def test_host_in_allowlist_case_insensitive(self):
        """Test case insensitive matching."""
        from mmrelay.plugin_loader import _host_in_allowlist

        result = _host_in_allowlist("GitHub.com", ["github.com"])
        self.assertTrue(result)

    def test_host_in_allowlist_empty_host(self):
        """Test empty host handling."""
        from mmrelay.plugin_loader import _host_in_allowlist

        result = _host_in_allowlist("", ["github.com"])
        self.assertFalse(result)

    def test_host_in_allowlist_none_host(self):
        """Test None host handling."""
        from mmrelay.plugin_loader import _host_in_allowlist

        result = _host_in_allowlist(None, ["github.com"])  # type: ignore[arg-type]
        self.assertFalse(result)

    def test_repo_url_rejected_for_dash_prefix(self):
        """Test that URLs starting with dash are rejected."""
        self.pl.config = {}
        result = _is_repo_url_allowed("-evil-option")
        self.assertFalse(result)

    @patch("mmrelay.plugin_loader.logger")
    def test_repo_url_rejected_for_file_scheme(self, mock_logger):
        """Test that file:// URLs are rejected by default."""
        self.pl.config = {}
        result = _is_repo_url_allowed("file:///local/path")
        self.assertFalse(result)
        mock_logger.error.assert_called_with(
            "file:// repositories are disabled for security reasons."
        )

    def test_repo_url_allows_file_scheme_with_opt_in(self):
        """Test that file:// URLs are allowed when local paths are enabled."""
        self.pl.config = {"security": {"allow_local_plugin_paths": True}}
        result = _is_repo_url_allowed("file:///local/path")
        self.assertTrue(result)

    @patch("mmrelay.plugin_loader.logger")
    def test_repo_url_rejected_for_unsupported_scheme(self, mock_logger):
        """Test that unsupported schemes are rejected."""
        self.pl.config = {}
        result = _is_repo_url_allowed("ftp://github.com/user/repo.git")
        self.assertFalse(result)
        mock_logger.error.assert_called_with(
            "Unsupported repository scheme '%s' for %s",
            "ftp",
            "ftp://github.com/user/repo.git",
        )

    @patch("mmrelay.plugin_loader.logger")
    def test_repo_url_local_path_nonexistent(self, mock_logger):
        """Test local path validation when path doesn't exist."""
        self.pl.config = {"security": {"allow_local_plugin_paths": True}}
        with patch("os.path.exists", return_value=False):
            result = _is_repo_url_allowed("/nonexistent/path")
            self.assertFalse(result)
        mock_logger.error.assert_called_with(
            "Local repository path does not exist: %s", "/nonexistent/path"
        )

    @patch("mmrelay.plugin_loader.logger")
    def test_repo_url_local_path_disabled(self, mock_logger):
        """Test local path validation when local paths are disabled."""
        self.pl.config = {}
        result = _is_repo_url_allowed("/local/path")
        self.assertFalse(result)
        mock_logger.error.assert_called_with(
            "Invalid repository '%s'. Local paths are disabled, and remote URLs must include a scheme (e.g., 'https://').",
            "/local/path",
        )

    @patch("mmrelay.plugin_loader.logger")
    def test_repo_url_empty_string(self, mock_logger):
        """Test empty URL handling."""
        self.pl.config = {}
        result = _is_repo_url_allowed("")
        self.assertFalse(result)

    @patch("mmrelay.plugin_loader.logger")
    def test_repo_url_whitespace_only(self, mock_logger):
        """Test whitespace-only URL handling."""
        self.pl.config = {}
        result = _is_repo_url_allowed("   ")
        self.assertFalse(result)


class TestRequirementFiltering(unittest.TestCase):
    """Test cases for requirement filtering security functions."""

    def setUp(self):
        """
        Set up the test by attaching the plugin loader and saving its original configuration.

        Stores the supplied plugin loader instance on self.pl and records its current
        `config` attribute (or `None` if absent) in `self.original_config` for later restoration.
        """
        self.pl = pl
        self.original_config = getattr(pl, "config", None)

    def tearDown(self):
        """
        Restore the original plugin loader configuration after a test.

        Reassigns the saved original configuration back to the plugin loader's `config` attribute to restore global state modified during the test.
        """
        self.pl.config = self.original_config

    def test_is_requirement_risky_vcs_prefixes(self):
        """Test VCS prefix detection."""
        from mmrelay.plugin_loader import _is_requirement_risky

        risky_requirements = [
            "git+https://github.com/user/repo.git",
            "hg+https://bitbucket.org/user/repo",
            "bzr+https://launchpad.net/project",
            "svn+https://svn.example.com/project",
        ]

        for req in risky_requirements:
            with self.subTest(req=req):
                self.assertTrue(_is_requirement_risky(req))

    def test_is_requirement_risky_url_with_at(self):
        """Test URL with @ symbol detection."""
        from mmrelay.plugin_loader import _is_requirement_risky

        risky_requirements = [
            "package@https://example.com/package.tar.gz",
            "pkg@file:///local/path",
        ]

        for req in risky_requirements:
            with self.subTest(req=req):
                self.assertTrue(_is_requirement_risky(req))

    def test_is_requirement_risky_safe_requirements(self):
        """Test safe requirement detection."""
        from mmrelay.plugin_loader import _is_requirement_risky

        safe_requirements = [
            "requests==2.28.0",
            "numpy>=1.20.0",
            "django~=4.0.0",
            "flask",
            "pytest>=6.0.0,<7.0.0",
        ]

        for req in safe_requirements:
            with self.subTest(req=req):
                self.assertFalse(_is_requirement_risky(req))

    def test_filter_risky_requirements_editable_with_url(self):
        """Test filtering editable requirements with URLs."""
        from mmrelay.plugin_loader import _filter_risky_requirements

        requirements = [
            "--editable=git+https://github.com/user/repo.git",
            "requests==2.28.0",
        ]

        safe, flagged, _allow = _filter_risky_requirements(requirements)

        self.assertIn("requests==2.28.0", safe)
        self.assertIn("--editable=git+https://github.com/user/repo.git", flagged)
        self.assertFalse(_allow)

    def test_filter_risky_requirements_editable_safe(self):
        """Test filtering safe editable requirements."""
        from mmrelay.plugin_loader import _filter_risky_requirements

        requirements = [
            "--editable=.",
            "--editable=/local/path",
            "requests==2.28.0",
        ]

        safe, flagged, _allow = _filter_risky_requirements(requirements)

        self.assertIn("requests==2.28.0", safe)
        self.assertIn("--editable=.", safe)
        self.assertIn("--editable=/local/path", safe)
        self.assertEqual(flagged, [])

    def test_filter_risky_requirements_source_flag_removal(self):
        """Test that source flags are removed with risky requirements."""
        from mmrelay.plugin_loader import _filter_risky_requirements

        requirements = [
            "--extra-index-url https://pypi.org/simple",
            "git+https://github.com/user/repo.git",
            "requests==2.28.0",
        ]

        safe, flagged, _allow = _filter_risky_requirements(requirements)

        self.assertIn("requests==2.28.0", safe)
        self.assertIn("--extra-index-url https://pypi.org/simple", flagged)
        self.assertIn("git+https://github.com/user/repo.git", flagged)
        self.assertFalse(_allow)

    def test_filter_risky_requirements_comments_and_empty(self):
        """Test filtering comments and empty strings."""
        from mmrelay.plugin_loader import _filter_risky_requirements

        requirements = [
            "# This is a comment",
            "",
            "   ",
            "requests==2.28.0",
        ]

        safe, flagged, _allow = _filter_risky_requirements(requirements)

        self.assertIn("requests==2.28.0", safe)
        self.assertEqual(flagged, [])

    def test_filter_risky_requirements_allow_untrusted(self):
        """Test that allow_untrusted=True allows risky requirements."""
        from mmrelay.plugin_loader import _filter_risky_requirements

        # Set up config to allow untrusted dependencies
        self.pl.config = {"security": {"allow_untrusted_dependencies": True}}

        requirements = [
            "git+https://github.com/user/repo.git",
            "http://example.com/package.tar.gz",
        ]

        safe, flagged, _allow = _filter_risky_requirements(requirements)

        # With new behavior, classification is independent of config
        self.assertEqual(len(safe), 0)
        self.assertEqual(len(flagged), 2)
        self.assertTrue(_allow)
        self.assertEqual(flagged, requirements)

    def test_filter_risky_requirements_short_form_flags_with_attached_values(self):
        """Test that short-form flags with attached values are properly filtered."""
        from mmrelay.plugin_loader import _filter_risky_requirements

        requirements = [
            "-ihttps://malicious.example.com/simple",  # Should be flagged
            "-fsafe-local-path",  # Should be safe (find-links with local path)
            "-egit+https://github.com/user/repo.git",  # Should be flagged (editable with VCS)
            "requests==2.28.0",  # Should be safe
        ]

        safe, flagged, _allow = _filter_risky_requirements(requirements)

        self.assertIn("requests==2.28.0", safe)
        self.assertIn("-fsafe-local-path", safe)
        self.assertIn("-ihttps://malicious.example.com/simple", flagged)
        self.assertIn("-egit+https://github.com/user/repo.git", flagged)
        self.assertFalse(_allow)
