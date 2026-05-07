"""Tests for plugin loader: Scheduler lifecycle, clone input validation, branch/tag infrastructure."""

# Decomposed from test_plugin_loader_deps.py

import subprocess  # nosec B404 - tests assert subprocess error handling
import threading
from typing import cast
from unittest.mock import ANY, MagicMock, call, patch

import mmrelay.plugin_loader as pl
from mmrelay.constants.plugins import DEFAULT_BRANCHES
from mmrelay.plugin_loader import (
    _clone_new_repo_to_branch_or_tag,
    _update_existing_repo_to_branch_or_tag,
    _validate_clone_inputs,
    clear_plugin_jobs,
    clone_or_update_repo,
    schedule_job,
    start_global_scheduler,
    stop_global_scheduler,
)
from tests._plugin_loader_helpers import TEST_GIT_TIMEOUT, BaseGitTest


class TestSchedulerAndCloneInfrastructure(BaseGitTest):
    """Test cases for scheduler lifecycle, clone input validation, and clone/update infrastructure."""

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

        original_thread = pl._global_scheduler_thread
        original_event = pl._global_scheduler_stop_event
        pl._global_scheduler_thread = None
        pl._global_scheduler_stop_event = None

        mock_event = MagicMock()
        mock_threading.Event.return_value = mock_event
        mock_thread = MagicMock()
        mock_threading.Thread.return_value = mock_thread

        try:
            start_global_scheduler()

            # Event should be called since schedule is available (mocked)
            mock_threading.Event.assert_called_once()
            mock_threading.Thread.assert_called_once()
            mock_thread.start.assert_called_once()
        finally:
            pl._global_scheduler_thread = original_thread
            pl._global_scheduler_stop_event = original_event

    def test_start_global_scheduler_runs_pending_once(self):
        """scheduler_loop should call schedule.run_pending when available."""

        run_event = threading.Event()

        class FakeSchedule:
            def __bool__(self) -> bool:
                """Always truthy to simulate available schedule."""
                return True

            def run_pending(self) -> None:
                """Signal run and stop the scheduler thread."""
                run_event.set()
                if pl._global_scheduler_stop_event:
                    pl._global_scheduler_stop_event.set()

            def clear(self) -> None:
                """No-op clear for test."""

        original_schedule = pl.schedule
        original_thread = pl._global_scheduler_thread
        original_event = pl._global_scheduler_stop_event
        pl.schedule = FakeSchedule()
        pl._global_scheduler_thread = None
        pl._global_scheduler_stop_event = None

        try:
            start_global_scheduler()
            run_event.wait(timeout=1.0)
            stop_global_scheduler()
        finally:
            pl.schedule = original_schedule
            pl._global_scheduler_thread = original_thread
            pl._global_scheduler_stop_event = original_event

        self.assertTrue(
            run_event.is_set(), "Scheduler did not call run_pending within timeout"
        )

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

        original_thread = pl._global_scheduler_thread
        # Simulate already running thread
        pl._global_scheduler_thread = MagicMock()
        pl._global_scheduler_thread.is_alive.return_value = True

        try:
            start_global_scheduler()

            # Should not create new thread
            mock_threading.Thread.assert_not_called()
        finally:
            pl._global_scheduler_thread = original_thread

    @patch("mmrelay.plugin_loader.threading")
    @patch("mmrelay.plugin_loader.schedule")
    def test_stop_global_scheduler_stops_thread(self, mock_schedule, mock_threading):
        """Test that stop_global_scheduler stops the scheduler thread."""

        original_thread = pl._global_scheduler_thread
        original_event = pl._global_scheduler_stop_event
        # Setup running thread
        mock_event = MagicMock()
        mock_thread = MagicMock()
        mock_thread.is_alive.side_effect = [True, False]
        pl._global_scheduler_thread = mock_thread
        pl._global_scheduler_stop_event = mock_event

        try:
            stop_global_scheduler()

            mock_event.set.assert_called_once()
            mock_thread.join.assert_called_once_with(timeout=5)
            mock_schedule.clear.assert_called_once()
            self.assertIsNone(pl._global_scheduler_thread)
        finally:
            pl._global_scheduler_thread = original_thread
            pl._global_scheduler_stop_event = original_event

    @patch("mmrelay.plugin_loader.threading")
    def test_stop_global_scheduler_no_thread(self, mock_threading):
        """Test that stop_global_scheduler exits early when no thread exists."""

        original_thread = pl._global_scheduler_thread
        # Ensure no thread is running
        pl._global_scheduler_thread = None

        try:
            stop_global_scheduler()

            # Should not call any threading methods
            mock_threading.Event.assert_not_called()
        finally:
            pl._global_scheduler_thread = original_thread

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
        error_messages = [str(c) for c in mock_logger.error.call_args_list]
        self.assertTrue(
            all("Invalid ref type" not in msg for msg in error_messages),
            f"Unexpected 'Invalid ref type' error logged: {error_messages}",
        )

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
        result = _validate_clone_inputs(cast(str, None), ref)

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
        }  # 64 chars (exceeds 40-char SHA-1 limit)
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

        def _mock_git_responses(cmd, **_kwargs):
            """Return appropriate git responses based on command patterns."""
            if "rev-parse" in cmd and "HEAD" in cmd:
                return subprocess.CompletedProcess(
                    cmd, 0, stdout="some_commit\n", stderr=""
                )
            elif "rev-parse" in cmd:
                return subprocess.CompletedProcess(
                    cmd, 0, stdout="tag_commit\n", stderr=""
                )
            else:
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        mock_run_git.side_effect = _mock_git_responses
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
                ["git", "-C", self.temp_repo_path, "rev-parse", "HEAD"],
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
            subprocess.CompletedProcess(
                [], 0, stdout="abc123\n", stderr=""
            ),  # current commit
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
