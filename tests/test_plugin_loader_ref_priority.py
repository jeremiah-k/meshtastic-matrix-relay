"""Tests for plugin loader: ref priority and community dependency caching/loading."""

# Decomposed from test_plugin_loader_core.py

import os
import shutil
import tempfile
from unittest.mock import ANY, patch

import mmrelay.plugin_loader as pl
from mmrelay.plugin_loader import load_plugins
from tests._plugin_loader_helpers import BaseGitTest


class TestRefPriority(BaseGitTest):
    """Test cases for plugin ref priority and dependency caching/loading."""

    def setUp(self) -> None:
        """Prepare isolated plugin directories and reset plugin loader state."""
        super().setUp()
        self.test_dir = tempfile.mkdtemp()
        self.custom_dir = os.path.join(self.test_dir, "plugins", "custom")
        self.community_dir = os.path.join(self.test_dir, "plugins", "community")

        os.makedirs(self.custom_dir, exist_ok=True)
        os.makedirs(self.community_dir, exist_ok=True)

        pl.plugins_loaded = False
        pl.sorted_active_plugins = []

    def _community_repo_path(self) -> str:
        repo_path = os.path.join(self.community_dir, "repo")
        os.makedirs(repo_path, exist_ok=True)
        return repo_path

    def _write_community_requirements(self, content: str = "requests==2.28.0\n") -> str:
        repo_path = self._community_repo_path()
        with open(
            os.path.join(repo_path, pl.PLUGIN_REQUIREMENTS_FILENAME), "w"
        ) as req_file:
            req_file.write(content)
        return repo_path

    def tearDown(self) -> None:
        """Remove temporary directories and clean up resources."""
        shutil.rmtree(self.test_dir, ignore_errors=True)
        pl.plugins_loaded = False
        pl.sorted_active_plugins = []
        super().tearDown()

    @patch("mmrelay.plugin_loader.clone_or_update_repo")
    @patch("mmrelay.plugin_loader._install_requirements_for_repo")
    @patch("mmrelay.plugin_loader.load_plugins_from_directory")
    @patch("mmrelay.plugin_loader.get_community_plugin_dirs")
    @patch("mmrelay.plugin_loader.get_custom_plugin_dirs")
    @patch("mmrelay.plugin_loader.start_global_scheduler")
    @patch("mmrelay.plugin_loader._check_commit_pin_for_upstream_updates")
    def test_load_plugins_commit_priority_over_tag_and_branch(
        self,
        mock_update_check,
        mock_start_scheduler,
        mock_get_custom_dirs,
        mock_get_community_dirs,
        mock_load_from_dir,
        mock_install_reqs,
        mock_clone_repo,
    ) -> None:
        """Test that commit ref takes priority over tag and branch in plugin config."""

        config = {
            "community-plugins": {
                "test-plugin": {
                    "active": True,
                    "repository": "https://github.com/user/repo.git",
                    "commit": "deadbeef",
                    "tag": "v1.0.0",
                    "branch": "main",
                    "priority": 10,
                }
            },
            "plugins": {},  # No core plugins active
        }

        mock_get_custom_dirs.return_value = []
        mock_get_community_dirs.return_value = [self.community_dir]
        mock_clone_repo.return_value = True
        mock_load_from_dir.return_value = []

        load_plugins(config)

        # Verify that clone was called with commit ref (highest priority)
        mock_clone_repo.assert_called_once_with(
            "https://github.com/user/repo.git",
            {"type": "commit", "value": "deadbeef"},
            self.community_dir,
        )
        mock_start_scheduler.assert_called_once()
        mock_install_reqs.assert_not_called()

    @patch("mmrelay.plugin_loader.clone_or_update_repo")
    @patch("mmrelay.plugin_loader._install_requirements_for_repo")
    @patch("mmrelay.plugin_loader.load_plugins_from_directory")
    @patch("mmrelay.plugin_loader.get_community_plugin_dirs")
    @patch("mmrelay.plugin_loader.get_custom_plugin_dirs")
    @patch("mmrelay.plugin_loader.start_global_scheduler")
    def test_load_plugins_tag_priority_over_branch(
        self,
        mock_start_scheduler,
        mock_get_custom_dirs,
        mock_get_community_dirs,
        mock_load_from_dir,
        mock_install_reqs,
        mock_clone_repo,
    ):
        """Test that tag ref takes priority over branch in plugin config."""

        config = {
            "community-plugins": {
                "test-plugin": {
                    "active": True,
                    "repository": "https://github.com/user/repo.git",
                    "tag": "v1.0.0",
                    "branch": "main",
                    "priority": 10,
                }
            },
            "plugins": {},  # No core plugins active
        }

        mock_get_custom_dirs.return_value = []
        mock_get_community_dirs.return_value = [self.community_dir]
        mock_clone_repo.return_value = True
        mock_load_from_dir.return_value = []

        load_plugins(config)

        # Verify that clone was called with tag ref (higher priority than branch)
        mock_clone_repo.assert_called_once_with(
            "https://github.com/user/repo.git",
            {"type": "tag", "value": "v1.0.0"},
            self.community_dir,
        )
        mock_start_scheduler.assert_called_once()
        mock_install_reqs.assert_not_called()

    @patch("mmrelay.plugin_loader.clone_or_update_repo")
    @patch("mmrelay.plugin_loader._install_requirements_for_repo")
    @patch("mmrelay.plugin_loader.load_plugins_from_directory")
    @patch("mmrelay.plugin_loader.get_community_plugin_dirs")
    @patch("mmrelay.plugin_loader.get_custom_plugin_dirs")
    @patch("mmrelay.plugin_loader.start_global_scheduler")
    @patch("mmrelay.plugin_loader._check_commit_pin_for_upstream_updates")
    @patch("mmrelay.plugin_loader.logger")
    def test_load_plugins_commit_with_tag_and_branch_warning(
        self,
        mock_logger,
        mock_update_check,
        mock_start_scheduler,
        mock_get_custom_dirs,
        mock_get_community_dirs,
        mock_load_from_dir,
        mock_install_reqs,
        mock_clone_repo,
    ) -> None:
        """Test that warning is logged when commit is specified with tag/branch."""

        config = {
            "community-plugins": {
                "test-plugin": {
                    "active": True,
                    "repository": "https://github.com/user/repo.git",
                    "commit": "deadbeef",
                    "tag": "v1.0.0",
                    "branch": "main",
                    "priority": 10,
                }
            },
            "plugins": {},  # No core plugins active
        }

        mock_get_custom_dirs.return_value = []
        mock_get_community_dirs.return_value = [self.community_dir]
        mock_clone_repo.return_value = True
        mock_load_from_dir.return_value = []

        load_plugins(config)

        # Verify warning was logged about commit taking priority
        mock_logger.warning.assert_any_call(
            "Commit specified along with tag/branch for plugin test-plugin, using commit"
        )
        mock_start_scheduler.assert_called_once()
        mock_install_reqs.assert_not_called()

    @patch("mmrelay.plugin_loader.clone_or_update_repo")
    @patch("mmrelay.plugin_loader._install_requirements_for_repo")
    @patch("mmrelay.plugin_loader.load_plugins_from_directory")
    @patch("mmrelay.plugin_loader.get_community_plugin_dirs")
    @patch("mmrelay.plugin_loader.get_custom_plugin_dirs")
    @patch("mmrelay.plugin_loader.start_global_scheduler")
    def test_load_plugins_default_to_main_branch(
        self,
        mock_start_scheduler,
        mock_get_custom_dirs,
        mock_get_community_dirs,
        mock_load_from_dir,
        mock_install_reqs,
        mock_clone_repo,
    ):
        """Test that plugin defaults to main branch when no ref is specified."""

        config = {
            "community-plugins": {
                "test-plugin": {
                    "active": True,
                    "repository": "https://github.com/user/repo.git",
                    "priority": 10,
                }
            },
            "plugins": {},  # No core plugins active
        }

        mock_get_custom_dirs.return_value = []
        mock_get_community_dirs.return_value = [self.community_dir]
        mock_clone_repo.return_value = True
        mock_load_from_dir.return_value = []

        load_plugins(config)

        # Verify that clone was called with default main branch
        mock_clone_repo.assert_called_once_with(
            "https://github.com/user/repo.git",
            {"type": "branch", "value": "main"},
            self.community_dir,
        )
        mock_start_scheduler.assert_called_once()
        mock_install_reqs.assert_not_called()

    @patch("mmrelay.plugin_loader.clone_or_update_repo")
    @patch("mmrelay.plugin_loader._install_requirements_for_repo")
    @patch("mmrelay.plugin_loader.load_plugins_from_directory")
    @patch("mmrelay.plugin_loader.get_community_plugin_dirs")
    @patch("mmrelay.plugin_loader.get_custom_plugin_dirs")
    @patch("mmrelay.plugin_loader.start_global_scheduler")
    @patch("mmrelay.plugin_loader.logger")
    def test_load_plugins_missing_ref_warns_unsafe_default(
        self,
        mock_logger,
        mock_start_scheduler,
        mock_get_custom_dirs,
        mock_get_community_dirs,
        mock_load_from_dir,
        mock_install_reqs,
        mock_clone_repo,
    ):
        """Missing refs should warn and still default to the main branch."""

        config = {
            "community-plugins": {
                "test-plugin": {
                    "active": True,
                    "repository": "https://github.com/user/repo.git",
                }
            },
            "plugins": {},
        }

        mock_get_custom_dirs.return_value = []
        mock_get_community_dirs.return_value = [self.community_dir]
        mock_clone_repo.return_value = True
        mock_load_from_dir.return_value = []

        load_plugins(config)

        mock_logger.warning.assert_any_call(
            "No ref specified for %s; defaulting to branch '%s' is deprecated and unsafe",
            "test-plugin",
            "main",
        )
        mock_start_scheduler.assert_called_once()
        mock_install_reqs.assert_not_called()

    @patch("mmrelay.plugin_loader.clone_or_update_repo")
    @patch("mmrelay.plugin_loader._install_requirements_for_repo")
    @patch("mmrelay.plugin_loader.load_plugins_from_directory")
    @patch("mmrelay.plugin_loader.get_community_plugin_dirs")
    @patch("mmrelay.plugin_loader.get_custom_plugin_dirs")
    @patch("mmrelay.plugin_loader.start_global_scheduler")
    @patch("mmrelay.plugin_loader.logger")
    def test_load_plugins_branch_warning_for_each_explicit_branch_ref(
        self,
        mock_logger,
        mock_start_scheduler,
        mock_get_custom_dirs,
        mock_get_community_dirs,
        mock_load_from_dir,
        mock_install_reqs,
        mock_clone_repo,
    ):
        """Branch refs should always warn for explicit branch pins."""

        config = {
            "community-plugins": {
                "warn-branch": {
                    "active": True,
                    "repository": "https://github.com/user/repo.git",
                    "branch": "main",
                },
                "allow-branch": {
                    "active": True,
                    "repository": "https://github.com/user/repo2.git",
                    "branch": "main",
                },
            },
            "plugins": {},
        }

        mock_get_custom_dirs.return_value = []
        mock_get_community_dirs.return_value = [self.community_dir]
        mock_clone_repo.return_value = True
        mock_load_from_dir.return_value = []

        load_plugins(config)

        branch_warning = (
            "Community plugin '%s' uses a branch ref; branch refs are moving "
            "targets and not recommended in production"
        )
        warning_calls = [
            call_args
            for call_args in mock_logger.warning.call_args_list
            if call_args.args and call_args.args[0] == branch_warning
        ]
        self.assertEqual(
            len(warning_calls),
            2,
            "Expected one branch warning per explicitly branch-pinned plugin",
        )
        self.assertEqual(
            {call_args.args[1] for call_args in warning_calls},
            {"warn-branch", "allow-branch"},
        )
        mock_start_scheduler.assert_called_once()
        mock_install_reqs.assert_not_called()

    @patch("mmrelay.plugin_loader.clone_or_update_repo")
    @patch("mmrelay.plugin_loader._install_requirements_for_repo")
    @patch("mmrelay.plugin_loader.load_plugins_from_directory")
    @patch("mmrelay.plugin_loader.get_community_plugin_dirs")
    @patch("mmrelay.plugin_loader.get_custom_plugin_dirs")
    @patch("mmrelay.plugin_loader.start_global_scheduler")
    @patch("mmrelay.plugin_loader.logger")
    def test_load_plugins_tag_warning_once_per_startup(
        self,
        mock_logger,
        mock_start_scheduler,
        mock_get_custom_dirs,
        mock_get_community_dirs,
        mock_load_from_dir,
        mock_install_reqs,
        mock_clone_repo,
    ):
        """Tag ref warning should be emitted once per load cycle."""

        config = {
            "community-plugins": {
                "tag-one": {
                    "active": True,
                    "repository": "https://github.com/user/repo.git",
                    "tag": "v1.0.0",
                },
                "tag-two": {
                    "active": True,
                    "repository": "https://github.com/user/repo2.git",
                    "tag": "v2.0.0",
                },
            },
            "plugins": {},
        }

        mock_get_custom_dirs.return_value = []
        mock_get_community_dirs.return_value = [self.community_dir]
        mock_clone_repo.return_value = True
        mock_load_from_dir.return_value = []

        load_plugins(config)

        tag_warning = "Tags can be retargeted; commit pins are safer"
        warning_calls = [
            call_args
            for call_args in mock_logger.warning.call_args_list
            if call_args.args and call_args.args[0] == tag_warning
        ]
        self.assertEqual(len(warning_calls), 1)
        mock_start_scheduler.assert_called_once()
        mock_install_reqs.assert_not_called()

    @patch("mmrelay.plugin_loader.clone_or_update_repo")
    @patch("mmrelay.plugin_loader._install_requirements_for_repo")
    @patch("mmrelay.plugin_loader.load_plugins_from_directory")
    @patch("mmrelay.plugin_loader.get_community_plugin_dirs")
    @patch("mmrelay.plugin_loader.get_custom_plugin_dirs")
    @patch("mmrelay.plugin_loader.start_global_scheduler")
    @patch("mmrelay.plugin_loader._check_commit_pin_for_upstream_updates")
    @patch("mmrelay.plugin_loader.logger")
    def test_load_plugins_commit_ref_emits_no_branch_or_tag_warning(
        self,
        mock_logger,
        mock_update_check,
        mock_start_scheduler,
        mock_get_custom_dirs,
        mock_get_community_dirs,
        mock_load_from_dir,
        mock_install_reqs,
        mock_clone_repo,
    ):
        """Commit refs should not emit branch/tag safety warnings."""

        config = {
            "community-plugins": {
                "commit-plugin": {
                    "active": True,
                    "repository": "https://github.com/user/repo.git",
                    "commit": "0123456789abcdef0123456789abcdef01234567",
                }
            },
            "plugins": {},
        }

        mock_get_custom_dirs.return_value = []
        mock_get_community_dirs.return_value = [self.community_dir]
        mock_clone_repo.return_value = True
        mock_load_from_dir.return_value = []

        load_plugins(config)

        warning_texts = {
            call_args.args[0]
            for call_args in mock_logger.warning.call_args_list
            if call_args.args
        }
        self.assertNotIn(
            "Tags can be retargeted; commit pins are safer",
            warning_texts,
        )
        self.assertNotIn(
            "Community plugin '%s' uses a branch ref; branch refs are moving "
            "targets and not recommended in production",
            warning_texts,
        )
        mock_update_check.assert_called_once()
        mock_start_scheduler.assert_called_once()
        mock_install_reqs.assert_not_called()

    @patch("mmrelay.plugin_loader.clone_or_update_repo")
    @patch("mmrelay.plugin_loader._install_requirements_for_repo")
    @patch("mmrelay.plugin_loader.load_plugins_from_directory")
    @patch("mmrelay.plugin_loader.get_community_plugin_dirs")
    @patch("mmrelay.plugin_loader.get_custom_plugin_dirs")
    @patch("mmrelay.plugin_loader.start_global_scheduler")
    @patch("mmrelay.plugin_loader._check_commit_pin_for_upstream_updates")
    def test_load_plugins_skips_community_dep_installer_by_default(
        self,
        mock_update_check,
        mock_start_scheduler,
        mock_get_custom_dirs,
        mock_get_community_dirs,
        mock_load_from_dir,
        mock_install_reqs,
        mock_clone_repo,
    ) -> None:
        """Community dependency install should be skipped unless explicitly enabled."""

        config = {
            "community-plugins": {
                "commit-plugin": {
                    "active": True,
                    "repository": "https://github.com/user/repo.git",
                    "commit": "0123456789abcdef0123456789abcdef01234567",
                }
            },
            "plugins": {},
        }

        mock_get_custom_dirs.return_value = []
        mock_get_community_dirs.return_value = [self.community_dir]
        mock_clone_repo.return_value = True
        mock_load_from_dir.return_value = []

        load_plugins(config)

        mock_update_check.assert_called_once()
        mock_install_reqs.assert_not_called()
        mock_start_scheduler.assert_called_once()

    @patch("mmrelay.plugin_loader.clone_or_update_repo")
    @patch("mmrelay.plugin_loader._install_requirements_for_repo", return_value=True)
    @patch("mmrelay.plugin_loader._save_plugin_state")
    @patch("mmrelay.plugin_loader._load_plugin_state", return_value={})
    @patch(
        "mmrelay.plugin_loader._resolve_local_head_commit",
        return_value="0123456789abcdef0123456789abcdef01234567",
    )
    @patch("mmrelay.plugin_loader.load_plugins_from_directory")
    @patch("mmrelay.plugin_loader.get_community_plugin_dirs")
    @patch("mmrelay.plugin_loader.get_custom_plugin_dirs")
    @patch("mmrelay.plugin_loader.start_global_scheduler")
    @patch("mmrelay.plugin_loader._check_commit_pin_for_upstream_updates")
    def test_load_plugins_installs_community_requirements_when_opted_in_commit(
        self,
        mock_update_check,
        mock_start_scheduler,
        mock_get_custom_dirs,
        mock_get_community_dirs,
        mock_load_from_dir,
        mock_resolve_local_head_commit,
        mock_load_state,
        mock_save_state,
        mock_install_reqs,
        mock_clone_repo,
    ):
        """Opted-in commit-pinned community plugin should install requirements."""
        self._write_community_requirements()
        config = {
            "community-plugins": {
                "commit-plugin": {
                    "active": True,
                    "repository": "https://github.com/user/repo.git",
                    "commit": "0123456789abcdef0123456789abcdef01234567",
                    "install_requirements": True,
                }
            },
            "plugins": {},
        }

        mock_get_custom_dirs.return_value = []
        mock_get_community_dirs.return_value = [self.community_dir]
        mock_clone_repo.return_value = True
        mock_load_from_dir.return_value = []

        load_plugins(config)

        mock_update_check.assert_called_once()
        mock_resolve_local_head_commit.assert_called_once_with(
            os.path.join(self.community_dir, "repo")
        )
        mock_load_state.assert_called_once_with(
            os.path.join(self.community_dir, "repo")
        )
        mock_install_reqs.assert_called_once_with(
            os.path.join(self.community_dir, "repo"),
            "repo",
            plugin_type=pl.PLUGIN_TYPE_COMMUNITY,
            requirements_target=ANY,
        )
        saved_state = mock_save_state.call_args.args[1]
        self.assertEqual(
            saved_state.get(pl.PLUGIN_STATE_LAST_INSTALLED_REQUIREMENTS_COMMIT),
            "0123456789abcdef0123456789abcdef01234567",
        )
        mock_start_scheduler.assert_called_once()

    @patch("mmrelay.plugin_loader.clone_or_update_repo")
    @patch("mmrelay.plugin_loader._install_requirements_for_repo", return_value=True)
    @patch("mmrelay.plugin_loader._save_plugin_state")
    @patch("mmrelay.plugin_loader._load_plugin_state", return_value={})
    @patch(
        "mmrelay.plugin_loader._resolve_local_head_commit",
        return_value="0123456789abcdef0123456789abcdef01234567",
    )
    @patch("mmrelay.plugin_loader.load_plugins_from_directory")
    @patch("mmrelay.plugin_loader.get_community_plugin_dirs")
    @patch("mmrelay.plugin_loader.get_custom_plugin_dirs")
    @patch("mmrelay.plugin_loader.start_global_scheduler")
    @patch("mmrelay.plugin_loader.logger")
    def test_load_plugins_opted_in_branch_allows_dependency_install_with_warning(
        self,
        mock_logger,
        mock_start_scheduler,
        mock_get_custom_dirs,
        mock_get_community_dirs,
        mock_load_from_dir,
        mock_resolve_local_head_commit,
        mock_load_state,
        mock_save_state,
        mock_install_reqs,
        mock_clone_repo,
    ):
        """Explicit branch refs should warn but still allow dependency installation."""
        self._write_community_requirements()
        config = {
            "community-plugins": {
                "branch-plugin": {
                    "active": True,
                    "repository": "https://github.com/user/repo.git",
                    "branch": "main",
                    "install_requirements": True,
                }
            },
            "plugins": {},
        }

        mock_get_custom_dirs.return_value = []
        mock_get_community_dirs.return_value = [self.community_dir]
        mock_clone_repo.return_value = True
        mock_load_from_dir.return_value = []

        load_plugins(config)

        mock_resolve_local_head_commit.assert_called_once_with(
            os.path.join(self.community_dir, "repo")
        )
        mock_load_state.assert_called_once_with(
            os.path.join(self.community_dir, "repo")
        )
        mock_install_reqs.assert_called_once_with(
            os.path.join(self.community_dir, "repo"),
            "repo",
            plugin_type=pl.PLUGIN_TYPE_COMMUNITY,
            requirements_target=ANY,
        )
        saved_state = mock_save_state.call_args.args[1]
        self.assertEqual(
            saved_state.get(pl.PLUGIN_STATE_LAST_INSTALLED_REQUIREMENTS_COMMIT),
            "0123456789abcdef0123456789abcdef01234567",
        )
        mock_logger.warning.assert_any_call(
            "Community plugin '%s' uses install_requirements with an explicit branch "
            "ref; installs will follow moving upstream commits.",
            "branch-plugin",
        )
        mock_start_scheduler.assert_called_once()

    @patch("mmrelay.plugin_loader.clone_or_update_repo")
    @patch("mmrelay.plugin_loader._install_requirements_for_repo", return_value=True)
    @patch("mmrelay.plugin_loader._save_plugin_state")
    @patch("mmrelay.plugin_loader._load_plugin_state", return_value={})
    @patch(
        "mmrelay.plugin_loader._resolve_local_head_commit",
        return_value="0123456789abcdef0123456789abcdef01234567",
    )
    @patch("mmrelay.plugin_loader.load_plugins_from_directory")
    @patch("mmrelay.plugin_loader.get_community_plugin_dirs")
    @patch("mmrelay.plugin_loader.get_custom_plugin_dirs")
    @patch("mmrelay.plugin_loader.start_global_scheduler")
    @patch("mmrelay.plugin_loader.logger")
    def test_load_plugins_opted_in_tag_allows_dependency_install_with_warning(
        self,
        mock_logger,
        mock_start_scheduler,
        mock_get_custom_dirs,
        mock_get_community_dirs,
        mock_load_from_dir,
        mock_resolve_local_head_commit,
        mock_load_state,
        mock_save_state,
        mock_install_reqs,
        mock_clone_repo,
    ):
        """Explicit tag refs should warn but still allow dependency installation."""
        self._write_community_requirements()
        config = {
            "community-plugins": {
                "tag-plugin": {
                    "active": True,
                    "repository": "https://github.com/user/repo.git",
                    "tag": "v1.0.0",
                    "install_requirements": True,
                }
            },
            "plugins": {},
        }

        mock_get_custom_dirs.return_value = []
        mock_get_community_dirs.return_value = [self.community_dir]
        mock_clone_repo.return_value = True
        mock_load_from_dir.return_value = []

        load_plugins(config)

        mock_resolve_local_head_commit.assert_called_once_with(
            os.path.join(self.community_dir, "repo")
        )
        mock_load_state.assert_called_once_with(
            os.path.join(self.community_dir, "repo")
        )
        mock_install_reqs.assert_called_once_with(
            os.path.join(self.community_dir, "repo"),
            "repo",
            plugin_type=pl.PLUGIN_TYPE_COMMUNITY,
            requirements_target=ANY,
        )
        saved_state = mock_save_state.call_args.args[1]
        self.assertEqual(
            saved_state.get(pl.PLUGIN_STATE_LAST_INSTALLED_REQUIREMENTS_COMMIT),
            "0123456789abcdef0123456789abcdef01234567",
        )
        mock_logger.warning.assert_any_call(
            "Community plugin '%s' uses install_requirements with an explicit tag "
            "ref; tags can be retargeted.",
            "tag-plugin",
        )
        mock_start_scheduler.assert_called_once()

    @patch("mmrelay.plugin_loader.clone_or_update_repo")
    @patch("mmrelay.plugin_loader._install_requirements_for_repo")
    @patch("mmrelay.plugin_loader._resolve_local_head_commit")
    @patch("mmrelay.plugin_loader.load_plugins_from_directory")
    @patch("mmrelay.plugin_loader.get_community_plugin_dirs")
    @patch("mmrelay.plugin_loader.get_custom_plugin_dirs")
    @patch("mmrelay.plugin_loader.start_global_scheduler")
    @patch("mmrelay.plugin_loader.logger")
    def test_load_plugins_opted_in_missing_ref_skips_dependency_install(
        self,
        mock_logger,
        mock_start_scheduler,
        mock_get_custom_dirs,
        mock_get_community_dirs,
        mock_load_from_dir,
        mock_resolve_local_head_commit,
        mock_install_reqs,
        mock_clone_repo,
    ):
        """Missing refs with install_requirements should warn and skip installation."""
        config = {
            "community-plugins": {
                "default-branch-plugin": {
                    "active": True,
                    "repository": "https://github.com/user/repo.git",
                    "install_requirements": True,
                }
            },
            "plugins": {},
        }

        mock_get_custom_dirs.return_value = []
        mock_get_community_dirs.return_value = [self.community_dir]
        mock_clone_repo.return_value = True
        mock_load_from_dir.return_value = []

        load_plugins(config)

        mock_install_reqs.assert_not_called()
        mock_resolve_local_head_commit.assert_not_called()
        mock_logger.warning.assert_any_call(
            "Skipping dependency install for community plugin '%s': "
            "install_requirements requires an explicit ref; "
            "implicit default-branch refs are not eligible.",
            "default-branch-plugin",
        )
        mock_start_scheduler.assert_called_once()

    @patch("mmrelay.plugin_loader.clone_or_update_repo")
    @patch("mmrelay.plugin_loader._install_requirements_for_repo")
    @patch("mmrelay.plugin_loader.load_plugins_from_directory")
    @patch("mmrelay.plugin_loader.get_community_plugin_dirs")
    @patch("mmrelay.plugin_loader.get_custom_plugin_dirs")
    @patch("mmrelay.plugin_loader.start_global_scheduler")
    @patch("mmrelay.plugin_loader.logger")
    def test_load_plugins_opted_in_no_requirements_file_skips_cache_work(
        self,
        mock_logger,
        mock_start_scheduler,
        mock_get_custom_dirs,
        mock_get_community_dirs,
        mock_load_from_dir,
        mock_install_reqs,
        mock_clone_repo,
    ):
        """No requirements.txt should not compute install state or churn cache."""
        repo_path = self._community_repo_path()
        config = {
            "community-plugins": {
                "commit-plugin": {
                    "active": True,
                    "repository": "https://github.com/user/repo.git",
                    "commit": "0123456789abcdef0123456789abcdef01234567",
                    "install_requirements": True,
                }
            },
            "plugins": {},
        }

        mock_get_custom_dirs.return_value = []
        mock_get_community_dirs.return_value = [self.community_dir]
        mock_clone_repo.return_value = True
        mock_load_from_dir.return_value = []

        with (
            patch("mmrelay.plugin_loader._check_commit_pin_for_upstream_updates"),
            patch(
                "mmrelay.plugin_loader._resolve_local_head_commit",
                return_value="0123456789abcdef0123456789abcdef01234567",
            ),
        ):
            load_plugins(config)

        mock_install_reqs.assert_not_called()
        self.assertEqual(pl._load_plugin_state(repo_path), {})
        self.assertFalse(
            os.path.exists(pl._requirements_install_marker_path(repo_path, "repo"))
        )
        mock_load_from_dir.assert_called()
        mock_logger.debug.assert_any_call(
            "Skipping requirements install for community plugin %s; no %s found",
            "commit-plugin",
            pl.PLUGIN_REQUIREMENTS_FILENAME,
        )
        mock_start_scheduler.assert_called_once()

    @patch("mmrelay.plugin_loader.clone_or_update_repo")
    @patch("mmrelay.plugin_loader._install_requirements_for_repo")
    @patch("mmrelay.plugin_loader.load_plugins_from_directory")
    @patch("mmrelay.plugin_loader.get_community_plugin_dirs")
    @patch("mmrelay.plugin_loader.get_custom_plugin_dirs")
    @patch("mmrelay.plugin_loader.start_global_scheduler")
    def test_load_plugins_opted_in_commit_unchanged_skips_dependency_install(
        self,
        mock_start_scheduler,
        mock_get_custom_dirs,
        mock_get_community_dirs,
        mock_load_from_dir,
        mock_install_reqs,
        mock_clone_repo,
    ):
        """Dependency install should be skipped when commit is already installed."""
        repo_path = self._write_community_requirements()
        deps_dir = os.path.join(self.test_dir, "plugins", "deps")
        os.makedirs(deps_dir, exist_ok=True)
        requirements_hash = pl._requirements_hash(["requests==2.28.0"])
        with patch.object(pl, "_PLUGIN_DEPS_DIR", deps_dir):
            requirements_target = pl._requirements_install_target_identity()
            pl._write_requirements_install_marker(
                repo_path, "repo", requirements_hash, requirements_target
            )
        pl._save_plugin_state(
            repo_path,
            {
                pl.PLUGIN_STATE_LAST_INSTALLED_REQUIREMENTS_COMMIT: "0123456789abcdef0123456789abcdef01234567",
                pl.PLUGIN_STATE_LAST_INSTALLED_REQUIREMENTS_HASH: requirements_hash,
                pl.PLUGIN_STATE_LAST_INSTALLED_REQUIREMENTS_TARGET: requirements_target,
            },
        )
        config = {
            "community-plugins": {
                "commit-plugin": {
                    "active": True,
                    "repository": "https://github.com/user/repo.git",
                    "commit": "0123456789abcdef0123456789abcdef01234567",
                    "install_requirements": True,
                }
            },
            "plugins": {},
        }

        mock_get_custom_dirs.return_value = []
        mock_get_community_dirs.return_value = [self.community_dir]
        mock_clone_repo.return_value = True
        mock_load_from_dir.return_value = []

        with (
            patch.object(pl, "_PLUGIN_DEPS_DIR", deps_dir),
            patch("mmrelay.plugin_loader._check_commit_pin_for_upstream_updates"),
            patch(
                "mmrelay.plugin_loader._resolve_local_head_commit",
                return_value="0123456789abcdef0123456789abcdef01234567",
            ),
        ):
            load_plugins(config)

        mock_install_reqs.assert_not_called()
        mock_start_scheduler.assert_called_once()

    @patch("mmrelay.plugin_loader.clone_or_update_repo")
    @patch("mmrelay.plugin_loader._install_requirements_for_repo", return_value=True)
    @patch("mmrelay.plugin_loader.load_plugins_from_directory")
    @patch("mmrelay.plugin_loader.get_community_plugin_dirs")
    @patch("mmrelay.plugin_loader.get_custom_plugin_dirs")
    @patch("mmrelay.plugin_loader.start_global_scheduler")
    def test_load_plugins_matching_commit_empty_deps_reinstalls_requirements(
        self,
        mock_start_scheduler,
        mock_get_custom_dirs,
        mock_get_community_dirs,
        mock_load_from_dir,
        mock_install_reqs,
        mock_clone_repo,
    ):
        """Matching state should not skip install when the target is invalid."""
        repo_path = self._write_community_requirements()
        deps_dir = os.path.join(self.test_dir, "plugins", "deps")
        os.makedirs(deps_dir, exist_ok=True)
        requirements_hash = pl._requirements_hash(["requests==2.28.0"])
        with patch.object(pl, "_PLUGIN_DEPS_DIR", deps_dir):
            requirements_target = pl._requirements_install_target_identity()
        pl._save_plugin_state(
            repo_path,
            {
                pl.PLUGIN_STATE_LAST_INSTALLED_REQUIREMENTS_COMMIT: "0123456789abcdef0123456789abcdef01234567",
                pl.PLUGIN_STATE_LAST_INSTALLED_REQUIREMENTS_HASH: requirements_hash,
                pl.PLUGIN_STATE_LAST_INSTALLED_REQUIREMENTS_TARGET: requirements_target,
            },
        )
        config = {
            "community-plugins": {
                "commit-plugin": {
                    "active": True,
                    "repository": "https://github.com/user/repo.git",
                    "commit": "0123456789abcdef0123456789abcdef01234567",
                    "install_requirements": True,
                }
            },
            "plugins": {},
        }

        mock_get_custom_dirs.return_value = []
        mock_get_community_dirs.return_value = [self.community_dir]
        mock_clone_repo.return_value = True
        mock_load_from_dir.return_value = []

        with (
            patch.object(pl, "_PLUGIN_DEPS_DIR", deps_dir),
            patch("mmrelay.plugin_loader.logger") as mock_logger,
            patch("mmrelay.plugin_loader._check_commit_pin_for_upstream_updates"),
            patch(
                "mmrelay.plugin_loader._resolve_local_head_commit",
                return_value="0123456789abcdef0123456789abcdef01234567",
            ),
        ):
            load_plugins(config)

        mock_install_reqs.assert_called_once_with(
            repo_path,
            "repo",
            plugin_type=pl.PLUGIN_TYPE_COMMUNITY,
            requirements_target=requirements_target,
        )
        saved_state = pl._load_plugin_state(repo_path)
        self.assertEqual(
            saved_state.get(pl.PLUGIN_STATE_LAST_INSTALLED_REQUIREMENTS_HASH),
            requirements_hash,
        )
        self.assertEqual(
            saved_state.get(pl.PLUGIN_STATE_LAST_INSTALLED_REQUIREMENTS_TARGET),
            requirements_target,
        )
        mock_logger.debug.assert_any_call(
            "Reinstalling requirements for community plugin %s; install state matches but marker validation failed for target %s",
            "commit-plugin",
            requirements_target,
        )
        mock_start_scheduler.assert_called_once()

    def test_load_plugins_matching_commit_stale_hash_reinstalls_requirements(self):
        """Matching commit should reinstall when saved requirements hash is stale."""
        repo_path = self._write_community_requirements()
        deps_dir = os.path.join(self.test_dir, "plugins", "deps")
        os.makedirs(deps_dir, exist_ok=True)
        current_hash = pl._requirements_hash(["requests==2.28.0"])
        with patch.object(pl, "_PLUGIN_DEPS_DIR", deps_dir):
            current_target = pl._requirements_install_target_identity()
            pl._write_requirements_install_marker(
                repo_path, "repo", current_hash, current_target
            )
        pl._save_plugin_state(
            repo_path,
            {
                pl.PLUGIN_STATE_LAST_INSTALLED_REQUIREMENTS_COMMIT: "0123456789abcdef0123456789abcdef01234567",
                pl.PLUGIN_STATE_LAST_INSTALLED_REQUIREMENTS_HASH: "stale-hash",
                pl.PLUGIN_STATE_LAST_INSTALLED_REQUIREMENTS_TARGET: current_target,
            },
        )
        config = {
            "community-plugins": {
                "commit-plugin": {
                    "active": True,
                    "repository": "https://github.com/user/repo.git",
                    "commit": "0123456789abcdef0123456789abcdef01234567",
                    "install_requirements": True,
                }
            },
            "plugins": {},
        }

        with (
            patch("mmrelay.plugin_loader.get_custom_plugin_dirs", return_value=[]),
            patch(
                "mmrelay.plugin_loader.get_community_plugin_dirs",
                return_value=[self.community_dir],
            ),
            patch("mmrelay.plugin_loader.clone_or_update_repo", return_value=True),
            patch(
                "mmrelay.plugin_loader._resolve_local_head_commit",
                return_value="0123456789abcdef0123456789abcdef01234567",
            ),
            patch("mmrelay.plugin_loader.load_plugins_from_directory", return_value=[]),
            patch("mmrelay.plugin_loader.start_global_scheduler"),
            patch("mmrelay.plugin_loader._check_commit_pin_for_upstream_updates"),
            patch(
                "mmrelay.plugin_loader._install_requirements_for_repo",
                return_value=True,
            ) as mock_install_reqs,
            patch.object(pl, "_PLUGIN_DEPS_DIR", deps_dir),
        ):
            load_plugins(config)

        mock_install_reqs.assert_called_once_with(
            repo_path,
            "repo",
            plugin_type=pl.PLUGIN_TYPE_COMMUNITY,
            requirements_target=current_target,
        )
        saved_state = pl._load_plugin_state(repo_path)
        self.assertEqual(
            saved_state[pl.PLUGIN_STATE_LAST_INSTALLED_REQUIREMENTS_HASH],
            current_hash,
        )
        self.assertEqual(
            saved_state[pl.PLUGIN_STATE_LAST_INSTALLED_REQUIREMENTS_TARGET],
            current_target,
        )

    def test_load_plugins_matching_commit_stale_target_reinstalls_requirements(self):
        """Matching commit should reinstall when saved target identity is stale."""
        repo_path = self._write_community_requirements()
        deps_dir = os.path.join(self.test_dir, "plugins", "deps")
        os.makedirs(deps_dir, exist_ok=True)
        current_hash = pl._requirements_hash(["requests==2.28.0"])
        with patch.object(pl, "_PLUGIN_DEPS_DIR", deps_dir):
            current_target = pl._requirements_install_target_identity()
            pl._write_requirements_install_marker(
                repo_path, "repo", current_hash, current_target
            )
        pl._save_plugin_state(
            repo_path,
            {
                pl.PLUGIN_STATE_LAST_INSTALLED_REQUIREMENTS_COMMIT: "0123456789abcdef0123456789abcdef01234567",
                pl.PLUGIN_STATE_LAST_INSTALLED_REQUIREMENTS_HASH: current_hash,
                pl.PLUGIN_STATE_LAST_INSTALLED_REQUIREMENTS_TARGET: "target:/stale",
            },
        )
        config = {
            "community-plugins": {
                "commit-plugin": {
                    "active": True,
                    "repository": "https://github.com/user/repo.git",
                    "commit": "0123456789abcdef0123456789abcdef01234567",
                    "install_requirements": True,
                }
            },
            "plugins": {},
        }

        with (
            patch("mmrelay.plugin_loader.get_custom_plugin_dirs", return_value=[]),
            patch(
                "mmrelay.plugin_loader.get_community_plugin_dirs",
                return_value=[self.community_dir],
            ),
            patch("mmrelay.plugin_loader.clone_or_update_repo", return_value=True),
            patch(
                "mmrelay.plugin_loader._resolve_local_head_commit",
                return_value="0123456789abcdef0123456789abcdef01234567",
            ),
            patch("mmrelay.plugin_loader.load_plugins_from_directory", return_value=[]),
            patch("mmrelay.plugin_loader.start_global_scheduler"),
            patch("mmrelay.plugin_loader._check_commit_pin_for_upstream_updates"),
            patch(
                "mmrelay.plugin_loader._install_requirements_for_repo",
                return_value=True,
            ) as mock_install_reqs,
            patch.object(pl, "_PLUGIN_DEPS_DIR", deps_dir),
        ):
            load_plugins(config)

        mock_install_reqs.assert_called_once_with(
            repo_path,
            "repo",
            plugin_type=pl.PLUGIN_TYPE_COMMUNITY,
            requirements_target=current_target,
        )
        saved_state = pl._load_plugin_state(repo_path)
        self.assertEqual(
            saved_state[pl.PLUGIN_STATE_LAST_INSTALLED_REQUIREMENTS_HASH],
            current_hash,
        )
        self.assertEqual(
            saved_state[pl.PLUGIN_STATE_LAST_INSTALLED_REQUIREMENTS_TARGET],
            current_target,
        )

    @patch("mmrelay.plugin_loader.clone_or_update_repo")
    @patch("mmrelay.plugin_loader._install_requirements_for_repo", return_value=False)
    @patch("mmrelay.plugin_loader.load_plugins_from_directory")
    @patch("mmrelay.plugin_loader.get_community_plugin_dirs")
    @patch("mmrelay.plugin_loader.get_custom_plugin_dirs")
    @patch("mmrelay.plugin_loader.start_global_scheduler")
    def test_load_plugins_failed_requirements_install_does_not_update_state(
        self,
        mock_start_scheduler,
        mock_get_custom_dirs,
        mock_get_community_dirs,
        mock_load_from_dir,
        mock_install_reqs,
        mock_clone_repo,
    ):
        """Failed dependency installs should not refresh plugin install state."""
        repo_path = self._write_community_requirements()
        deps_dir = os.path.join(self.test_dir, "plugins", "deps")
        os.makedirs(deps_dir, exist_ok=True)
        config = {
            "community-plugins": {
                "commit-plugin": {
                    "active": True,
                    "repository": "https://github.com/user/repo.git",
                    "commit": "0123456789abcdef0123456789abcdef01234567",
                    "install_requirements": True,
                }
            },
            "plugins": {},
        }

        mock_get_custom_dirs.return_value = []
        mock_get_community_dirs.return_value = [self.community_dir]
        mock_clone_repo.return_value = True
        mock_load_from_dir.return_value = []

        with (
            patch.object(pl, "_PLUGIN_DEPS_DIR", deps_dir),
            patch("mmrelay.plugin_loader._check_commit_pin_for_upstream_updates"),
            patch(
                "mmrelay.plugin_loader._resolve_local_head_commit",
                return_value="0123456789abcdef0123456789abcdef01234567",
            ),
        ):
            load_plugins(config)

        mock_install_reqs.assert_called_once()
        self.assertEqual(pl._load_plugin_state(repo_path), {})
        mock_start_scheduler.assert_called_once()

    @patch("mmrelay.plugin_loader.clone_or_update_repo")
    @patch("mmrelay.plugin_loader._install_requirements_for_repo", return_value=True)
    @patch("mmrelay.plugin_loader._save_plugin_state")
    @patch(
        "mmrelay.plugin_loader._load_plugin_state",
        return_value={
            pl.PLUGIN_STATE_LAST_INSTALLED_REQUIREMENTS_COMMIT: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        },
    )
    @patch(
        "mmrelay.plugin_loader._resolve_local_head_commit",
        return_value="0123456789abcdef0123456789abcdef01234567",
    )
    @patch("mmrelay.plugin_loader.load_plugins_from_directory")
    @patch("mmrelay.plugin_loader.get_community_plugin_dirs")
    @patch("mmrelay.plugin_loader.get_custom_plugin_dirs")
    @patch("mmrelay.plugin_loader.start_global_scheduler")
    @patch("mmrelay.plugin_loader._check_commit_pin_for_upstream_updates")
    def test_load_plugins_opted_in_commit_changed_installs_requirements(
        self,
        mock_update_check,
        mock_start_scheduler,
        mock_get_custom_dirs,
        mock_get_community_dirs,
        mock_load_from_dir,
        mock_resolve_local_head_commit,
        mock_load_state,
        mock_save_state,
        mock_install_reqs,
        mock_clone_repo,
    ):
        """Dependency install should run when commit changes."""
        self._write_community_requirements()
        config = {
            "community-plugins": {
                "commit-plugin": {
                    "active": True,
                    "repository": "https://github.com/user/repo.git",
                    "commit": "0123456789abcdef0123456789abcdef01234567",
                    "install_requirements": True,
                }
            },
            "plugins": {},
        }

        mock_get_custom_dirs.return_value = []
        mock_get_community_dirs.return_value = [self.community_dir]
        mock_clone_repo.return_value = True
        mock_load_from_dir.return_value = []

        load_plugins(config)

        mock_update_check.assert_called_once()
        mock_resolve_local_head_commit.assert_called_once_with(
            os.path.join(self.community_dir, "repo")
        )
        mock_load_state.assert_called_once_with(
            os.path.join(self.community_dir, "repo")
        )
        mock_install_reqs.assert_called_once_with(
            os.path.join(self.community_dir, "repo"),
            "repo",
            plugin_type=pl.PLUGIN_TYPE_COMMUNITY,
            requirements_target=ANY,
        )
        saved_state = mock_save_state.call_args.args[1]
        self.assertEqual(
            saved_state.get(pl.PLUGIN_STATE_LAST_INSTALLED_REQUIREMENTS_COMMIT),
            "0123456789abcdef0123456789abcdef01234567",
        )
        mock_start_scheduler.assert_called_once()
