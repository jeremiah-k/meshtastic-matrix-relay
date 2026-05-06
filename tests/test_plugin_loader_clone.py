"""Tests for plugin loader: clone_or_update_repo Git operations."""

# Decomposed from test_plugin_loader_core.py

import subprocess  # nosec B404 - tests mock subprocess behavior
from unittest.mock import call, patch

import mmrelay.plugin_loader as pl
from mmrelay.plugin_loader import clone_or_update_repo
from tests._plugin_loader_helpers import TEST_GIT_TIMEOUT, BaseGitTest


class TestPluginLoaderClone(BaseGitTest):
    """Test cases for clone_or_update_repo functionality."""

    @patch("mmrelay.plugin_loader._run_git")
    @patch("mmrelay.plugin_loader._is_repo_url_allowed")
    @patch("mmrelay.plugin_loader.logger")
    def test_clone_or_update_repo_valid_short_commit_hash(
        self, mock_logger, mock_is_allowed, mock_run_git
    ):
        """Test clone with valid short commit hash (7 characters)."""

        mock_is_allowed.return_value = True
        # Mock git operations to fail by raising exception on first call
        mock_run_git.side_effect = subprocess.CalledProcessError(1, "git")
        ref = {"type": "commit", "value": "a1b2c3d"}

        result = clone_or_update_repo(
            "https://github.com/user/repo.git", ref, self.temp_plugins_dir
        )

        # Result is False because _run_git always raises CalledProcessError
        # and both code paths return False on git failure
        # The important part is that validation passes (no "Invalid commit hash" error)
        self.assertEqual(result, False)
        # Check that no validation error was logged for the valid commit hash
        validation_errors = [
            log_call
            for log_call in mock_logger.error.call_args_list
            if "Invalid commit hash" in str(log_call)
        ]
        self.assertEqual(len(validation_errors), 0)

    @patch("mmrelay.plugin_loader._run_git")
    @patch("mmrelay.plugin_loader._is_repo_url_allowed")
    @patch("os.path.isdir")
    def test_clone_or_update_repo_new_repo_commit(
        self, mock_isdir, mock_is_allowed, mock_run_git
    ):
        """Test cloning a new repository with commit ref."""

        mock_is_allowed.return_value = True
        mock_isdir.return_value = False  # Repo doesn't exist
        ref = {"type": "commit", "value": "a1b2c3d4"}

        def mock_git_func(*args, **_kwargs):
            if "rev-parse" in args[0]:
                if "HEAD" in args[0]:
                    return subprocess.CompletedProcess(
                        args[0], 0, stdout="different_commit\n", stderr=""
                    )
                elif "a1b2c3d4^{commit}" in args[0]:
                    return subprocess.CompletedProcess(
                        args[0], 0, stdout="a1b2c3d4\n", stderr=""
                    )
                else:
                    return subprocess.CompletedProcess(
                        args[0], 0, stdout="some_commit\n", stderr=""
                    )
            else:
                return subprocess.CompletedProcess(args[0], 0, stdout="", stderr="")

        mock_run_git.side_effect = mock_git_func

        with patch("os.makedirs"):
            result = clone_or_update_repo(
                "https://github.com/user/repo.git", ref, self.temp_plugins_dir
            )

        self.assertTrue(result)

        # Verify sequence of git operations (optimized: direct checkout succeeds)
        expected_calls = [
            # Clone repository
            (
                [
                    "git",
                    "clone",
                    "--filter=blob:none",
                    "https://github.com/user/repo.git",
                    "repo",
                ],
                {
                    "cwd": self.temp_plugins_dir,
                    "timeout": TEST_GIT_TIMEOUT,
                    "retry_attempts": 1,
                },
            ),
            # Check if already at the commit
            (
                ["git", "-C", self.temp_repo_path, "rev-parse", "HEAD"],
                {"capture_output": True},
            ),
            # Check target commit hash
            (
                ["git", "-C", self.temp_repo_path, "rev-parse", "a1b2c3d4^{commit}"],
                {"capture_output": True},
            ),
            # Direct checkout succeeds (no fetch needed)
            (
                ["git", "-C", self.temp_repo_path, "checkout", "a1b2c3d4"],
                {"timeout": TEST_GIT_TIMEOUT},
            ),
        ]

        mock_run_git.assert_has_calls(
            [call(args, **kwargs) for args, kwargs in expected_calls],
            any_order=False,
        )

    @patch("mmrelay.plugin_loader._run_git")
    @patch("mmrelay.plugin_loader._is_repo_url_allowed")
    @patch("os.path.isdir")
    def test_clone_or_update_repo_existing_repo_commit(
        self, mock_isdir, mock_is_allowed, mock_run_git
    ):
        """Test updating an existing repository to a specific commit."""

        mock_is_allowed.return_value = True
        mock_isdir.return_value = True  # Repo exists
        ref = {"type": "commit", "value": "deadbeef"}

        # Configure mock to fail on rev-parse (commit not found locally) but succeed on fetch and checkout
        checkout_call_count = 0

        def side_effect(*args, **_kwargs):
            """Simulate git ops: fail rev-parse for target commit, fail first checkout."""
            nonlocal checkout_call_count
            # Fail on rev-parse for the target commit (not found locally), but succeed on HEAD rev-parse
            if "rev-parse" in args[0] and "deadbeef^{commit}" in args[0]:
                raise subprocess.CalledProcessError(
                    1, "git"
                )  # Commit not found locally
            # Fail on first checkout to force fetch, but succeed on second checkout
            if "checkout" in args[0] and "deadbeef" in args[0]:
                checkout_call_count += 1
                if checkout_call_count == 1:
                    raise subprocess.CalledProcessError(
                        1, "git"
                    )  # First checkout fails, need to fetch
            return subprocess.CompletedProcess(args[0], 0, "", "")

        mock_run_git.side_effect = side_effect

        result = clone_or_update_repo(
            "https://github.com/user/repo.git", ref, self.temp_plugins_dir
        )

        self.assertTrue(result)

        # Verify sequence of git operations (optimized behavior)
        expected_calls = [
            # Check current commit (fails)
            (
                ["git", "-C", self.temp_repo_path, "rev-parse", "HEAD"],
                {"capture_output": True},
            ),
            # Check if commit exists locally (fails with new rev-parse logic)
            (
                [
                    "git",
                    "-C",
                    self.temp_repo_path,
                    "rev-parse",
                    "deadbeef^{commit}",
                ],
                {"capture_output": True},
            ),
            # Try direct checkout (fails to trigger fetch)
            (
                ["git", "-C", self.temp_repo_path, "checkout", "deadbeef"],
                {"timeout": TEST_GIT_TIMEOUT},
            ),
            # Fetch specific commit
            (
                [
                    "git",
                    "-C",
                    self.temp_repo_path,
                    "fetch",
                    "--depth=1",
                    "origin",
                    "deadbeef",
                ],
                {
                    "timeout": TEST_GIT_TIMEOUT,
                    "retry_attempts": pl.GIT_RETRY_ATTEMPTS,
                    "retry_delay": pl.GIT_RETRY_DELAY_SECONDS,
                },
            ),
            # Checkout specific commit (succeeds after fetch)
            (
                ["git", "-C", self.temp_repo_path, "checkout", "deadbeef"],
                {"timeout": TEST_GIT_TIMEOUT},
            ),
        ]

        mock_run_git.assert_has_calls(
            [call(args, **kwargs) for args, kwargs in expected_calls],
            any_order=False,
        )

    @patch("mmrelay.plugin_loader._run_git")
    @patch("mmrelay.plugin_loader._is_repo_url_allowed")
    @patch("os.path.isdir")
    def test_clone_or_update_repo_commit_fetch_specific_fails_fallback(
        self, mock_isdir, mock_is_allowed, mock_run_git
    ):
        """Test that when specific commit fetch fails, it falls back to fetching all."""

        mock_is_allowed.return_value = True
        mock_isdir.return_value = True  # Repo exists
        ref = {"type": "commit", "value": "cafebabe"}

        # Configure mock to fail on specific commit fetch and cat-file but succeed on general fetch
        checkout_attempts = []

        def side_effect(*args, **_kwargs):
            """
            Simulate subprocess behavior for git commands used in tests.

            Returns:
                subprocess.CompletedProcess: A successful completed process with exit code 0 for commands that do not trigger error conditions.

            Raises:
                subprocess.CalledProcessError: If the command is a fetch for commit "cafebabe" in the test repository path (self.temp_repo_path) or if the command contains "cat-file".
            """
            if args[0] == [
                "git",
                "-C",
                self.temp_repo_path,
                "fetch",
                "--depth=1",
                "origin",
                "cafebabe",
            ]:
                raise subprocess.CalledProcessError(1, "git")
            if "rev-parse" in args[0] and "cafebabe^{commit}" in args[0]:
                raise subprocess.CalledProcessError(1, "git")
            # Fail first checkout to trigger fetch, succeed second checkout
            if args[0] == ["git", "-C", self.temp_repo_path, "checkout", "cafebabe"]:
                checkout_attempts.append(1)
                if len(checkout_attempts) == 1:
                    raise subprocess.CalledProcessError(1, "git")
            return subprocess.CompletedProcess(args[0], 0, "", "")

        mock_run_git.side_effect = side_effect

        result = clone_or_update_repo(
            "https://github.com/user/repo.git", ref, self.temp_plugins_dir
        )

        self.assertTrue(result)

        # Verify that both fetch attempts were made
        fetch_calls = [
            call for call in mock_run_git.call_args_list if "fetch" in call[0][0]
        ]

        self.assertEqual(
            len(fetch_calls), 2
        )  # Specific commit fetch fails, fallback fetch
        self.assertEqual(
            fetch_calls[0][0][0],
            [
                "git",
                "-C",
                self.temp_repo_path,
                "fetch",
                "--depth=1",
                "origin",
                "cafebabe",
            ],
        )
        self.assertEqual(
            fetch_calls[1][0][0],
            ["git", "-C", self.temp_repo_path, "fetch", "origin"],
        )

    @patch("mmrelay.plugin_loader._run_git")
    @patch("mmrelay.plugin_loader._is_repo_url_allowed")
    @patch("os.path.isdir")
    def test_clone_or_update_repo_commit_fetch_success_no_fallback(
        self, mock_isdir, mock_is_allowed, mock_run_git
    ):
        """Test successful commit fetch without fallback."""

        mock_is_allowed.return_value = True
        mock_isdir.return_value = True  # Repo exists
        ref = {"type": "commit", "value": "abcd1234"}

        # Configure mock to succeed on all git operations (no fallback needed)
        def mock_run_git_side_effect(*args, **kwargs):
            """
            Simulate git subprocess calls for tests with deterministic successful outcomes.

            Returns:
                subprocess.CompletedProcess: A successful CompletedProcess for the invoked git command.
            """
            cmd = args[0]
            # For rev-parse calls, return same commit hash to simulate "already at target"
            if "rev-parse" in cmd and "capture_output" in kwargs:
                return subprocess.CompletedProcess(
                    args[0], 0, stdout="abcd1234fullhash\n", stderr=""
                )
            # All other operations succeed
            return subprocess.CompletedProcess(args[0], 0, "", "")

        mock_run_git.side_effect = mock_run_git_side_effect

        result = clone_or_update_repo(
            "https://github.com/user/repo.git", ref, self.temp_plugins_dir
        )

        self.assertTrue(result)

        # Verify rev-parse calls only (no fetch/checkout needed - already at target)
        expected_calls = [
            # Check current commit (succeeds)
            (
                ["git", "-C", self.temp_repo_path, "rev-parse", "HEAD"],
                {"capture_output": True},
            ),
            # Check if target commit exists locally (succeeds with new rev-parse logic)
            (
                [
                    "git",
                    "-C",
                    self.temp_repo_path,
                    "rev-parse",
                    "abcd1234^{commit}",
                ],
                {"capture_output": True},
            ),
        ]

        mock_run_git.assert_has_calls(
            [call(args, **kwargs) for args, kwargs in expected_calls],
            any_order=False,
        )

    @patch("mmrelay.plugin_loader._run_git")
    @patch("mmrelay.plugin_loader._is_repo_url_allowed")
    @patch("os.path.isdir")
    def test_clone_or_update_repo_commit_fetch_fallback_success(
        self, mock_isdir, mock_is_allowed, mock_run_git
    ):
        """Test commit fetch that fails specific but succeeds with fallback."""
        mock_is_allowed.return_value = True
        mock_isdir.return_value = True  # Repo exists
        ref = {"type": "commit", "value": "cdef5678"}

        # Configure mock to fail on specific commit fetch but succeed on fallback
        checkout_attempts = []

        def side_effect(*args, **_kwargs):
            """
            Test helper that simulates subprocess responses for git commands in tests.

            Simulates a failing `git fetch` for exact command ["git", "-C", self.temp_repo_path, "fetch", "origin", "cdef5678"] and a failing git "rev-parse" for target commit; for all other calls it returns a successful CompletedProcess with empty stdout/stderr.

            Returns:
                subprocess.CompletedProcess: Successful result for non-matching commands.

            Raises:
                subprocess.CalledProcessError: When the command matches the specific fetch case or rev-parse for target commit.
            """
            if args[0] == [
                "git",
                "-C",
                self.temp_repo_path,
                "fetch",
                "--depth=1",
                "origin",
                "cdef5678",
            ]:
                raise subprocess.CalledProcessError(1, "git")
            # Fail on rev-parse for target commit to trigger fetch
            if "rev-parse" in args[0] and "cdef5678^{commit}" in args[0]:
                raise subprocess.CalledProcessError(1, "git")
            # Fail first checkout to trigger fetch, succeed second checkout
            if args[0] == ["git", "-C", self.temp_repo_path, "checkout", "cdef5678"]:
                checkout_attempts.append(1)
                if len(checkout_attempts) == 1:
                    raise subprocess.CalledProcessError(1, "git")
            return subprocess.CompletedProcess(args[0], 0, "", "")

        mock_run_git.side_effect = side_effect

        result = clone_or_update_repo(
            "https://github.com/user/repo.git", ref, self.temp_plugins_dir
        )

        self.assertTrue(result)

        # Verify rev-parse check, failed specific fetch, successful fallback, and checkout
        fetch_calls = [
            call for call in mock_run_git.call_args_list if "fetch" in call[0][0]
        ]

        self.assertEqual(len(fetch_calls), 2)
        self.assertEqual(
            fetch_calls[0][0][0],
            [
                "git",
                "-C",
                self.temp_repo_path,
                "fetch",
                "--depth=1",
                "origin",
                "cdef5678",
            ],
        )
        self.assertEqual(
            fetch_calls[1][0][0],
            ["git", "-C", self.temp_repo_path, "fetch", "origin"],
        )

    @patch("mmrelay.plugin_loader._run_git")
    @patch("mmrelay.plugin_loader._is_repo_url_allowed")
    @patch("mmrelay.plugin_loader.logger")
    @patch("os.path.isdir")
    def test_clone_or_update_repo_commit_fetch_fallback_failure(
        self, mock_isdir, mock_logger, mock_is_allowed, mock_run_git
    ):
        """Test commit fetch where both specific and fallback fetch fail."""
        mock_is_allowed.return_value = True
        mock_isdir.return_value = True  # Repo exists
        ref = {"type": "commit", "value": "abcd1234"}

        # Configure mock to fail on both specific and fallback fetch
        def side_effect(*args, **_kwargs):
            """
            Simulate git subprocess behavior for tests by returning a successful CompletedProcess for most commands and raising CalledProcessError for specific failing invocations.

            Raises:
                subprocess.CalledProcessError: For these git invocations:
                  - ["git", "-C", <temp_repo_path>, "fetch", "origin", "abcd1234"]
                  - ["git", "-C", <temp_repo_path>, "fetch", "origin"]
                  - any invocation whose argument list contains "cat-file"

            Returns:
                subprocess.CompletedProcess: A CompletedProcess with returncode 0 and empty stdout/stderr for commands that do not match the failing cases.
            """
            if args[0] == [
                "git",
                "-C",
                self.temp_repo_path,
                "fetch",
                "--depth=1",
                "origin",
                "abcd1234",
            ]:
                raise subprocess.CalledProcessError(1, "git")
            if args[0] == ["git", "-C", self.temp_repo_path, "fetch", "origin"]:
                # Fail fallback fetch too
                raise subprocess.CalledProcessError(1, "git")
            if "rev-parse" in args[0] and "abcd1234^{commit}" in args[0]:
                raise subprocess.CalledProcessError(1, "git")
            # Fail checkout to trigger fetch
            if args[0] == ["git", "-C", self.temp_repo_path, "checkout", "abcd1234"]:
                raise subprocess.CalledProcessError(1, "git")
            return subprocess.CompletedProcess(args[0], 0, "", "")

        mock_run_git.side_effect = side_effect

        result = clone_or_update_repo(
            "https://github.com/user/repo.git", ref, self.temp_plugins_dir
        )

        self.assertFalse(result)  # Should return False when fallback fails

        # Verify warning messages were logged
        mock_logger.warning.assert_any_call(
            "Could not fetch commit %s for %s from remote; trying general fetch",
            "abcd1234",
            "repo",
        )
        # Verify fallback failure was logged
        warning_calls = [
            warn_call[0][0]
            for warn_call in mock_logger.warning.call_args_list
            if "Fallback fetch also failed" in warn_call[0][0]
        ]
        self.assertGreater(len(warning_calls), 0, "Expected fallback failure warning")

    @patch("os.makedirs")
    @patch("mmrelay.plugin_loader._run_git")
    @patch("mmrelay.plugin_loader._is_repo_url_allowed")
    @patch("mmrelay.plugin_loader.logger")
    @patch("os.path.isdir")
    def test_clone_or_update_repo_logger_exception_on_error(
        self, mock_isdir, mock_logger, mock_is_allowed, mock_run_git, _mock_makedirs
    ):
        """Test that logger.exception is called for repository update errors."""
        mock_is_allowed.return_value = True
        mock_isdir.return_value = False  # Repo doesn't exist, will try to clone
        ref = {"type": "commit", "value": "1234abcd"}

        # Configure mock to fail on git clone
        mock_run_git.side_effect = subprocess.CalledProcessError(1, "git")

        result = clone_or_update_repo(
            "https://github.com/user/repo.git", ref, self.temp_plugins_dir
        )

        self.assertFalse(result)

        # Verify logger.exception was called with consolidated error message
        mock_logger.exception.assert_called_once()
        exception_call = mock_logger.exception.call_args[0][0]
        self.assertIn("Error cloning repository", exception_call)
        self.assertIn(
            f"please manually clone into {self.temp_repo_path}", exception_call
        )
