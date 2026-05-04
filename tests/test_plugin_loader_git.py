"""Tests for plugin loader: Git operations (clone, update, fetch, checkout)."""

# Decomposed from test_plugin_loader.py

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from types import ModuleType
from unittest.mock import MagicMock, call, patch

import mmrelay.plugin_loader as pl
from mmrelay.constants.plugins import DEFAULT_BRANCHES, GIT_CHECKOUT_CMD
from mmrelay.plugin_loader import (
    _clone_new_repo_to_branch_or_tag,
    _is_repo_url_allowed,
    _run,
    _update_existing_repo_to_branch_or_tag,
    clone_or_update_repo,
    load_plugins,
    load_plugins_from_directory,
)
from tests._plugin_loader_helpers import TEST_GIT_TIMEOUT, BaseGitTest


class TestGitOperations(BaseGitTest):
    """Test cases for Git operations and repository management."""

    def setUp(self):
        super().setUp()

        self.pl = pl
        self.original_config = getattr(pl, "config", None)

    def tearDown(self):
        """
        Restore the plugin loader's configuration saved before the test.

        Reassigns the original configuration back to the plugin loader instance and invokes the base class tearDown to complete cleanup.
        """
        self.pl.config = self.original_config
        super().tearDown()

    @patch("mmrelay.plugin_loader._run")
    def test_run_git_with_defaults(self, mock_run):
        """Test _run_git does not force retries unless explicitly requested."""
        from mmrelay.plugin_loader import _run_git

        _run_git(["git", "status"])

        # Check that _run was called with the right parameters, including env
        call_args = mock_run.call_args
        self.assertEqual(call_args[0][0], ["git", "status"])
        self.assertEqual(call_args[1]["timeout"], pl.GIT_COMMAND_TIMEOUT_SECONDS)
        self.assertNotIn("retry_attempts", call_args[1])
        self.assertNotIn("retry_delay", call_args[1])
        self.assertIn("env", call_args[1])
        self.assertEqual(call_args[1]["env"]["GIT_TERMINAL_PROMPT"], "0")

    @patch("mmrelay.plugin_loader._run_git")
    def test_run_git_with_custom_settings(self, mock_run):
        """Test _run_git accepts custom settings."""
        from mmrelay.plugin_loader import _run_git

        _run_git(["git", "clone"], timeout=300, retry_attempts=5)

        mock_run.assert_called_once_with(
            ["git", "clone"], timeout=300, retry_attempts=5
        )

    @patch("mmrelay.plugin_loader.logger")
    def test_raise_install_error(self, mock_logger):
        """Test _raise_install_error logs and raises exception."""
        from mmrelay.plugin_loader import _raise_install_error

        with self.assertRaises(subprocess.CalledProcessError):
            _raise_install_error("test-package")

        mock_logger.warning.assert_called_once_with(
            "Auto-install blocked by policy; cannot install test-package. See docs for enabling."
        )

    @patch("mmrelay.plugin_loader._is_repo_url_allowed")
    @patch("mmrelay.plugin_loader.logger")
    def test_clone_or_update_repo_invalid_ref_type(self, mock_logger, mock_is_allowed):
        """Test clone with invalid ref type."""

        mock_is_allowed.return_value = True
        ref = {"type": "invalid", "value": "main"}

        result = clone_or_update_repo(
            "https://github.com/user/repo.git", ref, self.temp_plugins_dir
        )

        self.assertFalse(result)
        mock_logger.error.assert_called_with(
            "Invalid ref type %r (expected 'tag', 'branch', or 'commit') for %r",
            "invalid",
            "https://github.com/user/repo.git",
        )

    @patch("mmrelay.plugin_loader._is_repo_url_allowed")
    @patch("mmrelay.plugin_loader.logger")
    def test_clone_or_update_repo_missing_ref_value(self, mock_logger, mock_is_allowed):
        """
        Verify clone_or_update_repo returns False and logs an error when a ref specifies a type but has an empty value.

        Asserts that the function rejects a ref with an empty 'value' field, returns False, and logs an error mentioning the ref type and repository URL.
        """

        mock_is_allowed.return_value = True
        ref = {"type": "branch", "value": ""}

        result = clone_or_update_repo(
            "https://github.com/user/repo.git", ref, self.temp_plugins_dir
        )

        self.assertFalse(result)
        mock_logger.error.assert_called_with(
            "Missing ref value for %s on %r",
            "branch",
            "https://github.com/user/repo.git",
        )

    @patch("mmrelay.plugin_loader._is_repo_url_allowed")
    @patch("mmrelay.plugin_loader.logger")
    def test_clone_or_update_repo_ref_starts_with_dash(
        self, mock_logger, mock_is_allowed
    ):
        """Test clone with ref value starting with dash."""

        mock_is_allowed.return_value = True
        ref = {"type": "branch", "value": "-evil"}

        result = clone_or_update_repo(
            "https://github.com/user/repo.git", ref, self.temp_plugins_dir
        )

        self.assertFalse(result)
        mock_logger.error.assert_called_with(
            "Ref value looks invalid (starts with '-'): %r", "-evil"
        )

    @patch("mmrelay.plugin_loader._is_repo_url_allowed")
    @patch("mmrelay.plugin_loader.logger")
    def test_clone_or_update_repo_invalid_ref_chars(self, mock_logger, mock_is_allowed):
        """Test clone with invalid characters in ref value."""

        mock_is_allowed.return_value = True
        ref = {"type": "branch", "value": "invalid@branch"}

        result = clone_or_update_repo(
            "https://github.com/user/repo.git", ref, self.temp_plugins_dir
        )

        self.assertFalse(result)
        mock_logger.error.assert_called_with(
            "Invalid %s name supplied: %r", "branch", "invalid@branch"
        )

    @patch("mmrelay.plugin_loader._is_repo_url_allowed")
    def test_clone_or_update_repo_invalid_url_empty(self, mock_is_allowed):
        """Test clone with empty URL."""
        mock_is_allowed.return_value = False
        ref = {"type": "branch", "value": "main"}
        with tempfile.TemporaryDirectory() as tmpdir:
            result = clone_or_update_repo("", ref, tmpdir)
        self.assertFalse(result)

    @patch("mmrelay.plugin_loader._is_repo_url_allowed")
    def test_clone_or_update_repo_invalid_url_whitespace(self, mock_is_allowed):
        """Test clone with whitespace-only URL."""
        mock_is_allowed.return_value = False
        ref = {"type": "branch", "value": "main"}
        with tempfile.TemporaryDirectory() as tmpdir:
            result = clone_or_update_repo("   ", ref, tmpdir)
        self.assertFalse(result)

    @patch("mmrelay.plugin_loader._run_git")
    def test_clone_or_update_repo_pull_current_branch_fails(self, mock_run_git):
        """Test that clone_or_update_repo handles checkout failure on default branches."""
        mock_run_git.side_effect = [
            None,  # fetch
            subprocess.CalledProcessError(1, "git checkout"),  # checkout main fails
            subprocess.CalledProcessError(
                1, "git fetch"
            ),  # force-sync fetch main fails
            subprocess.CalledProcessError(1, "git checkout"),  # checkout master fails
            subprocess.CalledProcessError(
                1, "git fetch"
            ),  # force-sync fetch master fails
        ]
        repo_url = "https://github.com/test/plugin.git"
        ref = {"type": "branch", "value": "main"}
        with tempfile.TemporaryDirectory() as plugins_dir:
            repo_path = os.path.join(plugins_dir, "plugin")
            os.makedirs(repo_path)
            result = clone_or_update_repo(repo_url, ref, plugins_dir)
            self.assertFalse(result)
            expected_calls = [
                call(
                    ["git", "-C", repo_path, "fetch", "origin"],
                    timeout=TEST_GIT_TIMEOUT,
                    retry_attempts=pl.GIT_RETRY_ATTEMPTS,
                    retry_delay=pl.GIT_RETRY_DELAY_SECONDS,
                ),
                call(
                    ["git", "-C", repo_path, "checkout", "main"],
                    timeout=TEST_GIT_TIMEOUT,
                ),
                call(
                    ["git", "-C", repo_path, "fetch", "origin", "main"],
                    timeout=TEST_GIT_TIMEOUT,
                    retry_attempts=pl.GIT_RETRY_ATTEMPTS,
                    retry_delay=pl.GIT_RETRY_DELAY_SECONDS,
                ),
                call(
                    ["git", "-C", repo_path, "checkout", "master"],
                    timeout=TEST_GIT_TIMEOUT,
                ),
                call(
                    ["git", "-C", repo_path, "fetch", "origin", "master"],
                    timeout=TEST_GIT_TIMEOUT,
                    retry_attempts=pl.GIT_RETRY_ATTEMPTS,
                    retry_delay=pl.GIT_RETRY_DELAY_SECONDS,
                ),
            ]
            self.assertEqual(mock_run_git.call_args_list, expected_calls)

    @patch("mmrelay.plugin_loader._run_git")
    def test_clone_or_update_repo_checkout_and_pull_branch(self, mock_run_git):
        """Test that clone_or_update_repo handles checkout and pull for a different branch."""

        # Mock successful fetch, checkout and pull
        mock_run_git.side_effect = [
            None,  # fetch succeeds
            None,  # checkout succeeds
            None,  # pull succeeds
        ]

        repo_url = "https://github.com/test/plugin.git"
        ref = {"type": "branch", "value": "main"}

        with tempfile.TemporaryDirectory() as plugins_dir:
            repo_path = os.path.join(plugins_dir, "plugin")
            os.makedirs(repo_path)  # It's an existing repo

            result = clone_or_update_repo(repo_url, ref, plugins_dir)
            self.assertTrue(result)

    @patch("mmrelay.plugin_loader._run_git")
    def test_clone_or_update_repo_branch_pull_failure_force_syncs(self, mock_run_git):
        """Branch pull failures should fallback to a force-sync against origin."""
        mock_run_git.side_effect = [
            None,  # initial fetch in update flow
            None,  # checkout branch succeeds
            subprocess.CalledProcessError(1, "git pull"),  # pull fails
            None,  # fetch origin branch for force sync succeeds
            None,  # checkout -B branch origin/branch succeeds
        ]

        repo_url = "https://github.com/test/plugin.git"
        ref = {"type": "branch", "value": "main"}

        with tempfile.TemporaryDirectory() as plugins_dir:
            repo_path = os.path.join(plugins_dir, "plugin")
            os.makedirs(repo_path)

            result = clone_or_update_repo(repo_url, ref, plugins_dir)
            self.assertTrue(result)
            expected_calls = [
                call(
                    ["git", "-C", repo_path, "fetch", "origin"],
                    timeout=TEST_GIT_TIMEOUT,
                    retry_attempts=pl.GIT_RETRY_ATTEMPTS,
                    retry_delay=pl.GIT_RETRY_DELAY_SECONDS,
                ),
                call(
                    ["git", "-C", repo_path, "checkout", "main"],
                    timeout=TEST_GIT_TIMEOUT,
                ),
                call(
                    ["git", "-C", repo_path, "pull", "origin", "main"],
                    timeout=TEST_GIT_TIMEOUT,
                ),
                call(
                    ["git", "-C", repo_path, "fetch", "origin", "main"],
                    timeout=TEST_GIT_TIMEOUT,
                    retry_attempts=pl.GIT_RETRY_ATTEMPTS,
                    retry_delay=pl.GIT_RETRY_DELAY_SECONDS,
                ),
                call(
                    [
                        "git",
                        "-C",
                        repo_path,
                        "checkout",
                        "-B",
                        "main",
                        "origin/main",
                    ],
                    timeout=TEST_GIT_TIMEOUT,
                ),
            ]
            self.assertEqual(mock_run_git.call_args_list, expected_calls)

    @patch("mmrelay.plugin_loader._run_git")
    def test_clone_or_update_repo_checkout_and_pull_tag(self, mock_run_git):
        """Test that clone_or_update_repo handles checkout and pull for a tag."""

        def mock_run_git_side_effect(*args, **_kwargs):
            """
            Simulate git subprocess responses for tests, returning success for common commands and a commit-containing result for `rev-parse`.

            Parameters:
                *args: Positional arguments forwarded from the mocked runner; the first positional argument is expected to be the git command (string or sequence) inspected by this helper.
                **_kwargs: Ignored keyword arguments forwarded by the mock.

            Returns:
                None for successful commands such as `fetch`, `checkout`, and `pull`; otherwise an object whose `stdout` is a string commit hash for `rev-parse` invocations.
            """
            cmd = args[0]
            if "fetch" in cmd:
                return None  # fetch succeeds
            elif "rev-parse" in cmd and "HEAD" in cmd:
                return subprocess.CompletedProcess(
                    cmd, 0, stdout="abc123commit\n", stderr=""
                )  # current commit
            elif "rev-parse" in cmd:
                return subprocess.CompletedProcess(
                    cmd, 0, stdout="def456commit\n", stderr=""
                )  # tag commit (different)
            elif "checkout" in cmd:
                return None  # checkout succeeds
            elif "pull" in cmd:
                return None  # pull succeeds
            return None

        mock_run_git.side_effect = mock_run_git_side_effect

        repo_url = "https://github.com/test/plugin.git"
        ref = {"type": "tag", "value": "v1.0.0"}

        with tempfile.TemporaryDirectory() as plugins_dir:
            repo_path = os.path.join(plugins_dir, "plugin")
            os.makedirs(repo_path)  # It's an existing repo
            result = clone_or_update_repo(repo_url, ref, plugins_dir)
            self.assertTrue(result)

    @patch("mmrelay.plugin_loader._run_git")
    def test_clone_or_update_repo_checkout_fails_fallback(self, mock_run_git):
        """Test that clone_or_update_repo handles checkout failure and tries fallback."""
        mock_run_git.side_effect = [
            None,  # fetch
            subprocess.CalledProcessError(1, "git checkout"),  # checkout main
            subprocess.CalledProcessError(
                1, "git fetch"
            ),  # force-sync fetch main fails
            subprocess.CalledProcessError(1, "git checkout"),  # checkout master
            subprocess.CalledProcessError(
                1, "git fetch"
            ),  # force-sync fetch master fails
        ]
        repo_url = "https://github.com/test/plugin.git"
        ref = {"type": "branch", "value": "main"}
        with tempfile.TemporaryDirectory() as plugins_dir:
            repo_path = os.path.join(plugins_dir, "plugin")
            os.makedirs(repo_path)
            result = clone_or_update_repo(repo_url, ref, plugins_dir)
            self.assertFalse(result)
            expected_calls = [
                call(
                    ["git", "-C", repo_path, "fetch", "origin"],
                    timeout=TEST_GIT_TIMEOUT,
                    retry_attempts=pl.GIT_RETRY_ATTEMPTS,
                    retry_delay=pl.GIT_RETRY_DELAY_SECONDS,
                ),
                call(
                    ["git", "-C", repo_path, "checkout", "main"],
                    timeout=TEST_GIT_TIMEOUT,
                ),
                call(
                    ["git", "-C", repo_path, "fetch", "origin", "main"],
                    timeout=TEST_GIT_TIMEOUT,
                    retry_attempts=pl.GIT_RETRY_ATTEMPTS,
                    retry_delay=pl.GIT_RETRY_DELAY_SECONDS,
                ),
                call(
                    ["git", "-C", repo_path, "checkout", "master"],
                    timeout=TEST_GIT_TIMEOUT,
                ),
                call(
                    ["git", "-C", repo_path, "fetch", "origin", "master"],
                    timeout=TEST_GIT_TIMEOUT,
                    retry_attempts=pl.GIT_RETRY_ATTEMPTS,
                    retry_delay=pl.GIT_RETRY_DELAY_SECONDS,
                ),
            ]
            self.assertEqual(mock_run_git.call_args_list, expected_calls)

    @patch("mmrelay.plugin_loader._run_git")
    def test_try_checkout_and_pull_ref_tag_failure_returns_false_no_force_sync(
        self, mock_run_git
    ):
        mock_run_git.side_effect = subprocess.CalledProcessError(1, "git checkout")
        result = pl._try_checkout_and_pull_ref(
            self.temp_repo_path,
            "v1.0.0",
            "test-repo",
            ref_type="tag",
        )
        self.assertFalse(result)
        self.assertEqual(
            len(mock_run_git.call_args_list),
            1,
            "tag checkout failure should not trigger any additional git calls",
        )
        self.assertIn(
            GIT_CHECKOUT_CMD,
            mock_run_git.call_args_list[0][0][0],
            "the only call should be the initial checkout attempt",
        )

    @patch("mmrelay.plugin_loader.logger")
    @patch("mmrelay.plugin_loader._run_git")
    def test_try_checkout_and_pull_ref_branch_logs_original_failure_at_debug(
        self, mock_run_git, mock_logger
    ):
        """Branch pull failure should log the original exception at debug level before force-sync."""
        exc = subprocess.CalledProcessError(1, "git checkout")
        mock_run_git.side_effect = [
            exc,  # checkout fails
            None,  # fetch origin branch for force sync succeeds
            None,  # checkout -B branch origin/branch succeeds
        ]

        result = pl._try_checkout_and_pull_ref(
            self.temp_repo_path,
            "main",
            "test-repo",
            ref_type="branch",
        )

        self.assertTrue(result)
        mock_logger.debug.assert_any_call(
            "Pull/checkout failed for %s branch %s: %s",
            "test-repo",
            "main",
            exc,
        )

    @patch("mmrelay.plugin_loader._run")
    def test_run_git_merges_custom_env(self, mock_run):
        """Custom environment variables should be merged into git subprocess env."""
        pl._run_git(
            ["git", "status"],
            env={"CUSTOM_FLAG": "1", "GIT_TERMINAL_PROMPT": "1"},
        )

        env = mock_run.call_args.kwargs["env"]
        self.assertEqual(env["CUSTOM_FLAG"], "1")
        # GIT_TERMINAL_PROMPT is enforced to "0" (cannot be overridden).
        self.assertEqual(env["GIT_TERMINAL_PROMPT"], "0")

    @patch("mmrelay.plugin_loader.logger")
    @patch("mmrelay.plugin_loader._run_git")
    def test_update_existing_repo_to_commit_logs_exception_on_final_checkout_failure(
        self, mock_run_git, mock_logger
    ):
        """Final checkout failure after fetch should hit the outer error handler."""
        checkout_calls = 0

        def _side_effect(cmd, *args, **kwargs):
            nonlocal checkout_calls
            if cmd[-2:] == ["rev-parse", "HEAD"]:
                return subprocess.CompletedProcess(cmd, 0, stdout="head\n", stderr="")
            if cmd[-1].endswith("^{commit}"):
                raise subprocess.CalledProcessError(1, cmd)
            if cmd[-2:] == ["checkout", "deadbeef"]:
                checkout_calls += 1
                raise subprocess.CalledProcessError(1, cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        mock_run_git.side_effect = _side_effect

        result = pl._update_existing_repo_to_commit(
            self.temp_repo_path,
            "deadbeef",
            "repo",
        )

        self.assertFalse(result)
        self.assertEqual(checkout_calls, 2)
        mock_logger.exception.assert_called_with(
            "Failed to checkout commit %s for %s",
            "deadbeef",
            "repo",
        )

    @patch("mmrelay.plugin_loader._run_git")
    def test_try_fetch_and_checkout_tag_uses_explicit_refspec_fallback(
        self, mock_run_git
    ):
        """Tag fetch should fall back to explicit refspec when direct and --tags fail."""

        def _side_effect(cmd, *args, **kwargs):
            if cmd == [
                "git",
                "-C",
                self.temp_repo_path,
                "fetch",
                "origin",
                "refs/tags/v1.0.0",
            ] or cmd == ["git", "-C", self.temp_repo_path, "fetch", "--tags"]:
                raise subprocess.CalledProcessError(1, cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        mock_run_git.side_effect = _side_effect

        result = pl._try_fetch_and_checkout_tag(self.temp_repo_path, "v1.0.0", "repo")
        self.assertTrue(result)
        self.assertIn(
            call(
                [
                    "git",
                    "-C",
                    self.temp_repo_path,
                    "fetch",
                    "origin",
                    "refs/tags/v1.0.0:refs/tags/v1.0.0",
                ],
                timeout=TEST_GIT_TIMEOUT,
                retry_attempts=pl.GIT_RETRY_ATTEMPTS,
                retry_delay=pl.GIT_RETRY_DELAY_SECONDS,
            ),
            mock_run_git.call_args_list,
        )

    @patch("mmrelay.plugin_loader.logger")
    def test_fallback_to_default_branches_logs_warning_when_all_fail(self, mock_logger):
        """Default branch fallback should warn when no branch can be checked out."""
        with patch(
            "mmrelay.plugin_loader._run_git",
            side_effect=subprocess.CalledProcessError(1, "git"),
        ):
            result = pl._fallback_to_default_branches(
                self.temp_repo_path,
                list(DEFAULT_BRANCHES),
                "v1.0.0",
                "repo",
            )

        self.assertFalse(result)
        mock_logger.warning.assert_called_with(
            "Could not checkout any branch for repo, using current state"
        )

    @patch("mmrelay.plugin_loader.logger")
    def test_update_existing_repo_to_branch_or_tag_handles_missing_git_binary(
        self, mock_logger
    ):
        """Missing git during remote fetch should be logged and return False."""
        with patch("mmrelay.plugin_loader._run_git", side_effect=FileNotFoundError()):
            result = pl._update_existing_repo_to_branch_or_tag(
                self.temp_repo_path,
                "branch",
                "main",
                "repo",
                False,
                list(DEFAULT_BRANCHES),
            )
        self.assertFalse(result)
        mock_logger.exception.assert_called_with(
            "Error updating repository %s; git not found.",
            "repo",
        )

    @patch("mmrelay.plugin_loader._run_git")
    def test_update_existing_repo_to_branch_or_tag_returns_true_when_tag_matches_head(
        self, mock_run_git
    ):
        """Tag update should short-circuit when HEAD already matches tag commit."""

        def _side_effect(cmd, *args, **kwargs):
            if cmd[-2:] == ["fetch", "origin"]:
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
            if cmd[-2:] == ["rev-parse", "HEAD"]:
                return subprocess.CompletedProcess(cmd, 0, stdout="abc123\n", stderr="")
            if cmd[-1] == "v1.0.0^{commit}":
                return subprocess.CompletedProcess(cmd, 0, stdout="abc123\n", stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        mock_run_git.side_effect = _side_effect

        result = pl._update_existing_repo_to_branch_or_tag(
            self.temp_repo_path,
            "tag",
            "v1.0.0",
            "repo",
            False,
            list(DEFAULT_BRANCHES),
        )

        self.assertTrue(result)
        # Verify the short-circuit: rev-parse calls were made but no fetch/checkout
        self.assertIn(
            call(
                ["git", "-C", self.temp_repo_path, "rev-parse", "HEAD"],
                capture_output=True,
            ),
            mock_run_git.call_args_list,
        )
        self.assertIn(
            call(
                [
                    "git",
                    "-C",
                    self.temp_repo_path,
                    "rev-parse",
                    "v1.0.0^{commit}",
                ],
                capture_output=True,
            ),
            mock_run_git.call_args_list,
        )
        # Verify no checkout was called (short-circuit worked - fetch may still occur)
        for call_args in mock_run_git.call_args_list:
            call_list = call_args[0][0] if call_args[0] else []
            self.assertNotIn("checkout", call_list)

    @patch("mmrelay.plugin_loader._run_git")
    def test_update_existing_repo_to_branch_or_tag_uses_tag_fetch_helper(
        self, mock_run_git
    ):
        """When tag differs from HEAD, update flow should call tag fetch/checkout helper."""

        def _side_effect(cmd, *args, **kwargs):
            if cmd[-2:] == ["fetch", "origin"]:
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
            if cmd[-2:] == ["rev-parse", "HEAD"]:
                return subprocess.CompletedProcess(cmd, 0, stdout="abc123\n", stderr="")
            if cmd[-1] == "v1.0.1^{commit}":
                return subprocess.CompletedProcess(cmd, 0, stdout="def456\n", stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        mock_run_git.side_effect = _side_effect

        result = pl._update_existing_repo_to_branch_or_tag(
            self.temp_repo_path,
            "tag",
            "v1.0.1",
            "repo",
            False,
            list(DEFAULT_BRANCHES),
        )

        self.assertTrue(result)
        self.assertIn(
            call(
                [
                    "git",
                    "-C",
                    self.temp_repo_path,
                    "fetch",
                    "origin",
                    "refs/tags/v1.0.1",
                ],
                timeout=TEST_GIT_TIMEOUT,
                retry_attempts=pl.GIT_RETRY_ATTEMPTS,
                retry_delay=pl.GIT_RETRY_DELAY_SECONDS,
            ),
            mock_run_git.call_args_list,
        )
        self.assertIn(
            call(
                ["git", "-C", self.temp_repo_path, "checkout", "v1.0.1"],
                timeout=TEST_GIT_TIMEOUT,
            ),
            mock_run_git.call_args_list,
        )

    @patch("mmrelay.plugin_loader._run_git")
    def test_clone_new_repo_to_branch_or_tag_tag_returns_when_commit_matches(
        self, mock_run_git
    ):
        """Tag clones should return early when cloned HEAD already equals tag commit."""
        with patch("mmrelay.plugin_loader.os.path.isdir", return_value=False):

            def _side_effect(cmd, *args, **kwargs):
                if cmd[:3] == ["git", "clone", "--filter=blob:none"]:
                    return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
                if cmd[-2:] == ["rev-parse", "HEAD"]:
                    return subprocess.CompletedProcess(
                        cmd, 0, stdout="abc123\n", stderr=""
                    )
                if cmd[-1] == "v2.0.0^{commit}":
                    return subprocess.CompletedProcess(
                        cmd, 0, stdout="abc123\n", stderr=""
                    )
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

            mock_run_git.side_effect = _side_effect

            result = pl._clone_new_repo_to_branch_or_tag(
                "https://github.com/user/repo.git",
                self.temp_repo_path,
                "tag",
                "v2.0.0",
                "repo",
                self.temp_plugins_dir,
                False,
            )

            self.assertTrue(result)
            self.assertNotIn(
                call(
                    [
                        "git",
                        "-C",
                        self.temp_repo_path,
                        "fetch",
                        "origin",
                        "refs/tags/v2.0.0",
                    ],
                    timeout=TEST_GIT_TIMEOUT,
                ),
                mock_run_git.call_args_list,
            )

    @patch("mmrelay.plugin_loader.logger")
    @patch("mmrelay.plugin_loader._run_git")
    def test_clone_or_update_repo_validated_handles_update_exception(
        self, mock_run_git, mock_logger
    ):
        """Validated update path should catch and log raised git errors."""
        with patch("mmrelay.plugin_loader.os.path.isdir", return_value=True):
            mock_run_git.side_effect = FileNotFoundError("git")

            result = pl._clone_or_update_repo_validated(
                "https://github.com/user/repo.git",
                "commit",
                "deadbeef",
                "repo",
                self.temp_plugins_dir,
            )

            self.assertFalse(result)
            mock_logger.exception.assert_called_once()

    @patch("mmrelay.plugin_loader.logger")
    @patch("mmrelay.plugin_loader._run_git")
    def test_clone_or_update_repo_validated_handles_clone_exception(
        self, mock_run_git, mock_logger
    ):
        """Validated clone path should catch and log raised git errors."""
        with patch("mmrelay.plugin_loader.os.path.isdir", return_value=False):
            mock_run_git.side_effect = FileNotFoundError("git")

            result = pl._clone_or_update_repo_validated(
                "https://github.com/user/repo.git",
                "commit",
                "deadbeef",
                "repo",
                self.temp_plugins_dir,
            )

            self.assertFalse(result)
            mock_logger.exception.assert_called_once()

    def test_load_plugins_from_directory_auto_install_uses_pipx_inject(self):
        """Missing deps should use pipx inject when running inside pipx."""
        plugin_file = os.path.join(self.temp_plugins_dir, "pipx_plugin.py")
        with open(plugin_file, "w", encoding="utf-8") as handle:
            handle.write("import missingdep_pipx\n\nclass Plugin:\n    pass\n")

        orig_pipx_home = os.environ.get("PIPX_HOME")
        orig_pipx_local_venvs = os.environ.get("PIPX_LOCAL_VENVS")
        orig_virtual_env = os.environ.get("VIRTUAL_ENV")
        orig_missingdep = sys.modules.get("missingdep_pipx")

        for var in ("PIPX_HOME", "PIPX_LOCAL_VENVS", "VIRTUAL_ENV"):
            os.environ.pop(var, None)
        os.environ["PIPX_HOME"] = os.path.join(self.temp_plugins_dir, "pipx-home")

        def _run_side_effect(cmd, *args, **kwargs):
            sys.modules["missingdep_pipx"] = ModuleType("missingdep_pipx")
            return subprocess.CompletedProcess(args=cmd, returncode=0)

        try:
            with (
                patch(
                    "mmrelay.plugin_loader.shutil.which", return_value="/usr/bin/pipx"
                ),
                patch(
                    "mmrelay.plugin_loader._run", side_effect=_run_side_effect
                ) as mock_run,
            ):
                plugins = load_plugins_from_directory(self.temp_plugins_dir)
        finally:
            if orig_pipx_home is not None:
                os.environ["PIPX_HOME"] = orig_pipx_home
            else:
                os.environ.pop("PIPX_HOME", None)
            if orig_pipx_local_venvs is not None:
                os.environ["PIPX_LOCAL_VENVS"] = orig_pipx_local_venvs
            else:
                os.environ.pop("PIPX_LOCAL_VENVS", None)
            if orig_virtual_env is not None:
                os.environ["VIRTUAL_ENV"] = orig_virtual_env
            else:
                os.environ.pop("VIRTUAL_ENV", None)
            if orig_missingdep is not None:
                sys.modules["missingdep_pipx"] = orig_missingdep
            else:
                sys.modules.pop("missingdep_pipx", None)

        self.assertEqual(len(plugins), 1)
        mock_run.assert_any_call(
            ["/usr/bin/pipx", "inject", "mmrelay", "missingdep_pipx"],
            timeout=pl.PIP_INSTALL_MISSING_DEP_TIMEOUT,
        )

    def test_load_plugins_from_directory_auto_install_pip_adds_user_flag(self):
        """Outside a venv, auto-install should append --user to pip install command."""
        plugin_file = os.path.join(self.temp_plugins_dir, "pip_plugin.py")
        with open(plugin_file, "w", encoding="utf-8") as handle:
            handle.write("import missingdep_pip\n\nclass Plugin:\n    pass\n")

        orig_pipx_home = os.environ.get("PIPX_HOME")
        orig_pipx_local_venvs = os.environ.get("PIPX_LOCAL_VENVS")
        orig_virtual_env = os.environ.get("VIRTUAL_ENV")
        orig_missingdep = sys.modules.get("missingdep_pip")

        for var in ("PIPX_HOME", "PIPX_LOCAL_VENVS", "VIRTUAL_ENV"):
            os.environ.pop(var, None)

        def _run_side_effect(cmd, *args, **kwargs):
            sys.modules["missingdep_pip"] = ModuleType("missingdep_pip")
            return subprocess.CompletedProcess(args=cmd, returncode=0)

        try:
            with (
                patch.object(pl.sys, "prefix", "/usr"),
                patch.object(pl.sys, "base_prefix", "/usr"),
                patch(
                    "mmrelay.plugin_loader._run", side_effect=_run_side_effect
                ) as mock_run,
            ):
                plugins = load_plugins_from_directory(self.temp_plugins_dir)
        finally:
            if orig_pipx_home is not None:
                os.environ["PIPX_HOME"] = orig_pipx_home
            else:
                os.environ.pop("PIPX_HOME", None)
            if orig_pipx_local_venvs is not None:
                os.environ["PIPX_LOCAL_VENVS"] = orig_pipx_local_venvs
            else:
                os.environ.pop("PIPX_LOCAL_VENVS", None)
            if orig_virtual_env is not None:
                os.environ["VIRTUAL_ENV"] = orig_virtual_env
            else:
                os.environ.pop("VIRTUAL_ENV", None)
            if orig_missingdep is not None:
                sys.modules["missingdep_pip"] = orig_missingdep
            else:
                sys.modules.pop("missingdep_pip", None)

        self.assertEqual(len(plugins), 1)
        pip_calls = [c for c in mock_run.call_args_list if "pip" in c.args[0]]
        self.assertTrue(pip_calls)
        self.assertIn("--user", pip_calls[0].args[0])

    @patch("mmrelay.plugin_loader.logger")
    def test_load_plugins_from_directory_auto_install_failure_logs_manual_instructions(
        self, mock_logger
    ):
        """Auto-install subprocess errors should log manual install guidance."""
        plugin_file = os.path.join(self.temp_plugins_dir, "fail_plugin.py")
        with open(plugin_file, "w", encoding="utf-8") as handle:
            handle.write("import missingdep_fail\n\nclass Plugin:\n    pass\n")

        orig_pipx_home = os.environ.get("PIPX_HOME")
        orig_pipx_local_venvs = os.environ.get("PIPX_LOCAL_VENVS")
        orig_virtual_env = os.environ.get("VIRTUAL_ENV")

        for var in ("PIPX_HOME", "PIPX_LOCAL_VENVS", "VIRTUAL_ENV"):
            os.environ.pop(var, None)

        try:
            with patch(
                "mmrelay.plugin_loader._run",
                side_effect=subprocess.CalledProcessError(1, "pip"),
            ):
                plugins = load_plugins_from_directory(self.temp_plugins_dir)
        finally:
            if orig_pipx_home is not None:
                os.environ["PIPX_HOME"] = orig_pipx_home
            else:
                os.environ.pop("PIPX_HOME", None)
            if orig_pipx_local_venvs is not None:
                os.environ["PIPX_LOCAL_VENVS"] = orig_pipx_local_venvs
            else:
                os.environ.pop("PIPX_LOCAL_VENVS", None)
            if orig_virtual_env is not None:
                os.environ["VIRTUAL_ENV"] = orig_virtual_env
            else:
                os.environ.pop("VIRTUAL_ENV", None)

        self.assertEqual(plugins, [])
        self.assertTrue(
            any(
                "Failed to automatically install missingdep_fail" in str(c.args[0])
                for c in mock_logger.exception.call_args_list
            )
        )
        self.assertTrue(
            any(
                "pip install missingdep_fail" in str(c.args[0])
                for c in mock_logger.exception.call_args_list
            )
        )
