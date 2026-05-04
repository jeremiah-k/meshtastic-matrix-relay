"""Tests for plugin loader: Community plugin security, state files, thread safety."""

# Decomposed from test_plugin_loader.py

import importlib
import os
import subprocess
import sys
import tempfile
import time
import unittest
from types import ModuleType
from unittest.mock import MagicMock, patch

import mmrelay.plugin_loader as pl
from mmrelay.plugin_loader import _SYS_MODULES_LOCK, _exec_plugin_module, _run

# Used by ExecPluginModuleThreadSafety tests
HERE = os.path.dirname(os.path.abspath(__file__))


class TestCommunityPluginSecurityHelpers(unittest.TestCase):
    """Focused tests for community plugin security-hardening helpers."""

    def test_build_compare_url_supports_https_github(self):
        """Compare URL should be generated for https GitHub repository URLs."""
        compare_url = pl._build_compare_url(
            "https://github.com/owner/repo.git",
            "aaaabbbb",
            "ccccdddd",
        )
        self.assertEqual(
            compare_url,
            "https://github.com/owner/repo/compare/aaaabbbb...ccccdddd",
        )

    def test_build_compare_url_supports_ssh_github(self):
        """Compare URL should be generated for git@github.com repository URLs."""
        compare_url = pl._build_compare_url(
            "git@github.com:owner/repo.git",
            "11112222",
            "33334444",
        )
        self.assertEqual(
            compare_url,
            "https://github.com/owner/repo/compare/11112222...33334444",
        )

    def test_state_file_read_write_round_trip(self):
        """State files should persist and reload plugin state safely."""
        with tempfile.TemporaryDirectory() as repo_path:
            expected_state = {
                "last_notified_upstream_head": "abc123",
                "last_checked_at": "2026-01-01T00:00:00+00:00",
                pl.PLUGIN_STATE_LAST_INSTALLED_REQUIREMENTS_COMMIT: (
                    "0123456789abcdef0123456789abcdef01234567"
                ),
            }
            pl._save_plugin_state(repo_path, expected_state)
            loaded_state = pl._load_plugin_state(repo_path)
            self.assertEqual(loaded_state, expected_state)

    def test_state_file_invalid_json_returns_empty(self):
        """Invalid state JSON should be ignored without raising."""
        with tempfile.TemporaryDirectory() as repo_path:
            state_path = os.path.join(repo_path, pl.PLUGIN_STATE_FILENAME)
            with open(state_path, "w", encoding="utf-8") as handle:
                handle.write("{invalid json}")
            self.assertEqual(pl._load_plugin_state(repo_path), {})

    @patch("mmrelay.plugin_loader._run_git")
    def test_resolve_remote_default_branch_parses_symref_output(self, mock_run_git):
        """Default branch resolver should parse `git ls-remote --symref` output."""
        mock_run_git.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=(
                "ref: refs/heads/main\tHEAD\n"
                "0123456789abcdef0123456789abcdef01234567\tHEAD\n"
            ),
            stderr="",
        )
        with tempfile.TemporaryDirectory() as repo_path:
            self.assertEqual(pl._resolve_remote_default_branch(repo_path), "main")

    @patch("mmrelay.plugin_loader._resolve_remote_branch_head_commit")
    @patch("mmrelay.plugin_loader._run_git")
    def test_resolve_remote_default_branch_falls_back_to_main_master(
        self, mock_run_git, mock_resolve_remote_branch_head
    ):
        """Default branch resolver should fallback to main/master when symref parse fails."""
        mock_run_git.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="0123456789abcdef0123456789abcdef01234567\tHEAD\n",
            stderr="",
        )
        mock_resolve_remote_branch_head.side_effect = [
            "0123456789abcdef0123456789abcdef01234567",
            None,
        ]

        with tempfile.TemporaryDirectory() as repo_path:
            resolved = pl._resolve_remote_default_branch(repo_path)
            self.assertEqual(resolved, "main")
            mock_resolve_remote_branch_head.assert_any_call(repo_path, "main")

    @patch("mmrelay.plugin_loader._run_git")
    def test_resolve_local_head_commit_rejects_non_full_sha(self, mock_run_git):
        """Local HEAD resolver should only accept full 40-char SHAs."""
        mock_run_git.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="deadbeef\n",
            stderr="",
        )
        with tempfile.TemporaryDirectory() as repo_path:
            self.assertIsNone(pl._resolve_local_head_commit(repo_path))

    @patch("mmrelay.plugin_loader._run_git")
    def test_resolve_remote_branch_head_commit_rejects_non_full_sha(self, mock_run_git):
        """Remote branch head resolver should only accept full 40-char SHAs."""
        mock_run_git.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="deadbeef\trefs/heads/main\n",
            stderr="",
        )
        with tempfile.TemporaryDirectory() as repo_path:
            self.assertIsNone(pl._resolve_remote_branch_head_commit(repo_path, "main"))

    @patch("mmrelay.plugin_loader._resolve_local_head_commit")
    @patch("mmrelay.plugin_loader._resolve_remote_default_branch")
    @patch("mmrelay.plugin_loader._resolve_remote_branch_head_commit")
    @patch("mmrelay.plugin_loader.logger")
    def test_check_commit_pin_update_detection_and_dedupe(
        self,
        mock_logger,
        mock_resolve_remote_head,
        mock_resolve_default_branch,
        mock_resolve_local_head,
    ):
        """Commit update notification should persist and dedupe by upstream head."""
        pinned_sha = "0123456789abcdef0123456789abcdef01234567"
        upstream_sha = "89abcdef0123456789abcdef0123456789abcdef"
        mock_resolve_local_head.return_value = pinned_sha
        mock_resolve_default_branch.return_value = "main"
        mock_resolve_remote_head.return_value = upstream_sha
        compare_url = (
            "https://github.com/owner/repo/compare/"
            "0123456789abcdef0123456789abcdef01234567..."
            "89abcdef0123456789abcdef0123456789abcdef"
        )

        with tempfile.TemporaryDirectory() as repo_path:
            pl._check_commit_pin_for_upstream_updates(
                "demo-plugin",
                "https://github.com/owner/repo.git",
                repo_path,
            )
            state = pl._load_plugin_state(repo_path)
            self.assertEqual(state.get("last_notified_upstream_head"), upstream_sha)
            self.assertIsInstance(state.get("last_checked_at"), str)
            mock_logger.warning.assert_any_call(
                "Plugin '%s' is pinned to %s, upstream is now %s",
                "demo-plugin",
                pinned_sha,
                upstream_sha,
            )
            mock_logger.warning.assert_any_call("Compare: %s", compare_url)

            # Force re-check by setting an old timestamp, but keep same notified head.
            state["last_checked_at"] = "2000-01-01T00:00:00+00:00"
            pl._save_plugin_state(repo_path, state)
            mock_logger.warning.reset_mock()

            pl._check_commit_pin_for_upstream_updates(
                "demo-plugin",
                "https://github.com/owner/repo.git",
                repo_path,
            )

            self.assertFalse(
                any(
                    call_args.args
                    and call_args.args[0]
                    == "Plugin '%s' is pinned to %s, upstream is now %s"
                    for call_args in mock_logger.warning.call_args_list
                ),
                "Expected no duplicate pinned-vs-upstream warning",
            )


class TestExecPluginModuleThreadSafety(unittest.TestCase):
    """Tests for thread-safe sys.modules manipulation in _exec_plugin_module."""

    def test_concurrent_exec_does_not_corrupt_sys_modules(self):
        """
        Ensure that concurrent calls to _exec_plugin_module do not leave
        sys.modules in an inconsistent state.

        Each thread registers a unique module name and asserts it is visible
        in sys.modules during execution.  After all threads finish, the
        temporary entries must be cleaned up and no stale module should remain.
        """
        import concurrent.futures
        import importlib.machinery

        module_names = [f"_mmrelay_test_mod_{i}" for i in range(8)]
        caught_exceptions = []

        # Create a minimal loader class that doesn't reference __file__
        class MinimalLoader:
            def exec_module(self, module):
                module.__dict__.setdefault("__builtins__", __builtins__)
                assert (
                    sys.modules.get(module.__name__) is module
                ), f"Module {module.__name__} not bound in sys.modules before exec_module ran"
                time.sleep(0.05)

        def _load_module(mod_name: str) -> str:
            """Simulate loading a plugin module under a unique name."""
            spec = importlib.machinery.ModuleSpec(mod_name, loader=None)
            # Use our minimal loader instead of SourceFileLoader
            spec.loader = MinimalLoader()
            module = ModuleType(mod_name)

            try:
                _exec_plugin_module(
                    spec=spec,
                    plugin_module=module,
                    module_name=mod_name,
                    plugin_dir=HERE,
                )
            except (
                Exception
            ) as e:  # noqa: BLE001 - intentional broad except for testing namespace cleanup
                # The loader may raise; that is fine, we test namespace cleanup.
                # The important assertion is that no RuntimeError from
                # sys.modules corruption is raised.
                caught_exceptions.append(e)
            return mod_name

        # Clean up any leftover entries from previous runs
        for name in module_names:
            sys.modules.pop(name, None)

        pool = concurrent.futures.ThreadPoolExecutor(max_workers=4)
        try:
            futures = [pool.submit(_load_module, name) for name in module_names]
            _done, _not_done = concurrent.futures.wait(futures, timeout=5.0)
            if _not_done:
                for future in _not_done:
                    future.cancel()
                pool.shutdown(wait=False, cancel_futures=True)
                self.fail(
                    f"Timed out waiting for {len(_not_done)} worker threads; "
                    f"exceptions so far: {caught_exceptions}",
                )

            self.assertEqual(
                len(caught_exceptions),
                0,
                f"Exceptions during concurrent load: {caught_exceptions}",
            )
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
            for name in module_names:
                sys.modules.pop(name, None)

    def test_lock_is_module_level_and_reentrant(self):
        """Verify _SYS_MODULES_LOCK is a module-level reentrant lock (RLock)."""
        import inspect

        lock = _SYS_MODULES_LOCK

        first = lock.acquire(timeout=0.1)
        self.assertTrue(first, "Lock should be acquirable")
        second = False
        try:
            second = lock.acquire(timeout=0.1)
            self.assertTrue(second, "Lock should be reentrant (RLock)")
        finally:
            if second:
                lock.release()
            if first:
                lock.release()

        src = inspect.getsource(pl)
        self.assertIn(
            "_SYS_MODULES_LOCK", src, "Lock name should appear in module source"
        )

    def test_exec_plugin_module_raises_import_error_when_no_loader(self):
        import importlib.machinery

        spec = importlib.machinery.ModuleSpec("_test_no_loader", loader=None)
        module = ModuleType("_test_no_loader")
        with self.assertRaises(ImportError) as ctx:
            _exec_plugin_module(
                spec=spec,
                plugin_module=module,
                module_name="_test_no_loader",
                plugin_dir=HERE,
            )
        self.assertIn("No loader available", str(ctx.exception))

    def test_exec_plugin_module_rollback_removes_sys_modules_on_exception(self):
        import importlib.machinery

        mod_name = "_test_rollback_remove_" + str(int(time.time() * 1000))
        sys.modules.pop(mod_name, None)

        class FailingLoader:
            def exec_module(self, module):
                raise RuntimeError("deliberate failure")

        spec = importlib.machinery.ModuleSpec(mod_name, loader=None)
        spec.loader = FailingLoader()
        module = ModuleType(mod_name)

        with self.assertRaises(RuntimeError):
            _exec_plugin_module(
                spec=spec,
                plugin_module=module,
                module_name=mod_name,
                plugin_dir=HERE,
            )
        self.assertNotIn(mod_name, sys.modules)

    def test_exec_plugin_module_rollback_restores_previous_module(self):
        import importlib.machinery

        mod_name = "_test_rollback_restore_" + str(int(time.time() * 1000))
        previous = ModuleType(mod_name)
        previous._marker = "previous"
        sys.modules[mod_name] = previous

        class FailingLoader:
            def exec_module(self, module):
                raise RuntimeError("deliberate failure")

        spec = importlib.machinery.ModuleSpec(mod_name, loader=None)
        spec.loader = FailingLoader()
        module = ModuleType(mod_name)

        try:
            with self.assertRaises(RuntimeError):
                _exec_plugin_module(
                    spec=spec,
                    plugin_module=module,
                    module_name=mod_name,
                    plugin_dir=HERE,
                )
            self.assertIs(sys.modules.get(mod_name), previous)
        finally:
            sys.modules.pop(mod_name, None)
