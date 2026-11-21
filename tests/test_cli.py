"""Tests for Click CLI entry points in gitlab_watcher.cli."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from gitlab_watcher.cli import cli
from gitlab_watcher.config import Config, ProjectConfig


class TestCLI:
    """Unit tests for CLI commands and error handling."""

    def test_cli_default_runs_watcher(self) -> None:
        """Invoking CLI without subcommand starts Watcher.run()."""
        runner = CliRunner()
        with patch("gitlab_watcher.cli.Watcher") as mock_watcher_cls:
            result = runner.invoke(cli, ["--config", "/tmp/fake.conf", "--verbose"])
            assert result.exit_code == 0
            mock_watcher_cls.assert_called_once_with(config_path="/tmp/fake.conf", verbose=True)
            mock_watcher_cls.return_value.run.assert_called_once()

    def test_cli_default_handles_known_errors(self) -> None:
        """Missing config or ValueError exits cleanly with code 1 and message."""
        runner = CliRunner()
        with patch("gitlab_watcher.cli.Watcher", side_effect=FileNotFoundError("Config missing")):
            result = runner.invoke(cli, [])
            assert result.exit_code == 1
            assert "Error: Config missing" in result.output

    def test_cli_default_unexpected_error_non_verbose_vs_verbose(self) -> None:
        """Unexpected errors exit with code 1 in normal mode and raise in --verbose mode."""
        runner = CliRunner()
        with patch("gitlab_watcher.cli.Watcher", side_effect=RuntimeError("boom")):
            res_normal = runner.invoke(cli, [])
            assert res_normal.exit_code == 1
            assert "Error: boom" in res_normal.output

        with patch("gitlab_watcher.cli.Watcher", side_effect=ZeroDivisionError("div0")):
            res_quiet = runner.invoke(cli, [])
            assert res_quiet.exit_code == 1
            assert "An unexpected error occurred: div0" in res_quiet.output

            res_verbose = runner.invoke(cli, ["--verbose"])
            assert res_verbose.exit_code == 1
            assert isinstance(res_verbose.exception, ZeroDivisionError)

    def test_run_subcommand_handles_errors(self) -> None:
        """run subcommand handles known and unexpected errors cleanly."""
        runner = CliRunner()
        with patch("gitlab_watcher.cli.Watcher", side_effect=ValueError("bad cfg")):
            res = runner.invoke(cli, ["run"])
            assert res.exit_code == 1
            assert "Error: bad cfg" in res.output

        with patch("gitlab_watcher.cli.Watcher", side_effect=ZeroDivisionError("div0")):
            res_quiet = runner.invoke(cli, ["run"])
            assert res_quiet.exit_code == 1
            assert "An unexpected error occurred: div0" in res_quiet.output

            res_verbose = runner.invoke(cli, ["run", "--verbose"])
            assert res_verbose.exit_code == 1
            assert isinstance(res_verbose.exception, ZeroDivisionError)

    def test_init_components_creates_work_dir_and_rejects_symlink(self, tmp_path: Path) -> None:
        """_init_components creates StateManager in test work_dir and rejects symlinks."""
        from gitlab_watcher.cli import _init_components

        proj = tmp_path / "proj"
        proj.mkdir()
        (proj / "PROJECT.md").write_text("Project ID: 10\n", encoding="utf-8")
        conf = tmp_path / "config.conf"
        conf.write_text(
            f'GITLAB_URL="https://git.example.com"\n'
            f'GITLAB_TOKEN="glpat-1234567890abcdefghij"\n'
            f'PROJECT_DIRS=("{proj}")\n',
            encoding="utf-8",
        )
        cfg, state = _init_components(str(conf))
        assert cfg.projects[0].project_id == 10
        state.stop()

        # Non-pytest branch with symlinked work_dir should exit(1)
        real_dir = tmp_path / "real"
        real_dir.mkdir()
        link_dir = tmp_path / "link"
        link_dir.symlink_to(real_dir)
        conf.write_text(
            f'GITLAB_URL="https://git.example.com"\n'
            f'GITLAB_TOKEN="glpat-1234567890abcdefghij"\n'
            f'WORK_DIR="{link_dir}"\n'
            f'PROJECT_DIRS=("{proj}")\n',
            encoding="utf-8",
        )
        with (
            patch("gitlab_watcher.cli.sys.modules", {}),
            patch.dict("os.environ", {}, clear=True),
        ):
            import pytest as _pytest

            with _pytest.raises(SystemExit) as exc_info:
                _init_components(str(conf))
            assert exc_info.value.code == 1
    def test_run_subcommand_success(self) -> None:
        """Explicit `run` subcommand starts Watcher.run()."""
        runner = CliRunner()
        with patch("gitlab_watcher.cli.Watcher") as mock_watcher_cls:
            result = runner.invoke(cli, ["run", "-c", "/tmp/c.conf"])
            assert result.exit_code == 0
            mock_watcher_cls.assert_called_once_with(config_path="/tmp/c.conf", verbose=False)
            mock_watcher_cls.return_value.run.assert_called_once()

    def test_sync_state_unknown_project(self, tmp_path: Path) -> None:
        """sync-state exits with code 1 when project name is not in config."""
        runner = CliRunner()
        cfg = Config(projects=[ProjectConfig(project_id=1, path=tmp_path, name="alpha")])
        with patch("gitlab_watcher.cli._init_components", return_value=(cfg, MagicMock())):
            result = runner.invoke(cli, ["sync-state", "nonexistent"])
            assert result.exit_code == 1
            assert "Project 'nonexistent' not found in config." in result.output

    def test_sync_state_pushes_unpushed_commits(self, tmp_path: Path) -> None:
        """sync-state clears processing flag and pushes unpushed commits."""
        runner = CliRunner()
        project = ProjectConfig(project_id=42, path=tmp_path, name="myproj")
        cfg = Config(projects=[project], discord_webhook="")
        mock_state = MagicMock()

        with (
            patch("gitlab_watcher.cli._init_components", return_value=(cfg, mock_state)),
            patch("gitlab_watcher.cli.GitOps") as mock_git_cls,
            patch("gitlab_watcher.cli.DiscordWebhook"),
        ):
            git = mock_git_cls.return_value
            git.get_current_branch.return_value = "42-feature"
            git.has_unpushed_commits.return_value = True
            git.push.return_value = True

            result = runner.invoke(cli, ["sync-state", "myproj"])
            assert result.exit_code == 0
            mock_state.set_processing.assert_called_once_with(42, False)
            git.push.assert_called_once_with("origin", "42-feature")
            assert "Pushed unpushed work on branch '42-feature'." in result.output

    def test_sync_state_push_failure_notifies_discord(self, tmp_path: Path) -> None:
        """sync-state notifies Discord when git push fails."""
        runner = CliRunner()
        project = ProjectConfig(project_id=42, path=tmp_path, name="myproj")
        cfg = Config(projects=[project], discord_webhook="https://discord.com/api/webhooks/1/a")
        mock_state = MagicMock()

        with (
            patch("gitlab_watcher.cli._init_components", return_value=(cfg, mock_state)),
            patch("gitlab_watcher.cli.GitOps") as mock_git_cls,
            patch("gitlab_watcher.cli.DiscordWebhook") as mock_discord_cls,
        ):
            git = mock_git_cls.return_value
            git.get_current_branch.return_value = "42-feature"
            git.has_unpushed_commits.return_value = True
            git.push.return_value = False

            result = runner.invoke(cli, ["sync-state", "myproj"])
            assert result.exit_code == 0
            mock_discord_cls.return_value.notify_error.assert_called_once()
            assert "Push failed" in result.output

    def test_sync_state_no_unpushed_work(self, tmp_path: Path) -> None:
        """sync-state reports synchronized state when no unpushed commits exist."""
        runner = CliRunner()
        project = ProjectConfig(project_id=42, path=tmp_path, name="myproj")
        cfg = Config(projects=[project], discord_webhook="")
        mock_state = MagicMock()

        with (
            patch("gitlab_watcher.cli._init_components", return_value=(cfg, mock_state)),
            patch("gitlab_watcher.cli.GitOps") as mock_git_cls,
            patch("gitlab_watcher.cli.DiscordWebhook"),
        ):
            git = mock_git_cls.return_value
            git.get_current_branch.return_value = "master"
            git.has_unpushed_commits.return_value = False

            result = runner.invoke(cli, ["sync-state", "myproj"])
            assert result.exit_code == 0
            assert "No unpushed work detected" in result.output

