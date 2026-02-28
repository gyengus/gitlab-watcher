"""Tests for issue and MR processing."""

import subprocess
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

import pytest

from gitlab_watcher.config import ProjectConfig
from gitlab_watcher.discord import DiscordWebhook
from gitlab_watcher.gitlab_client import GitLabClient, Issue, MergeRequest
from gitlab_watcher.processor import Processor
from gitlab_watcher.state import StateManager


@pytest.fixture
def temp_work_dir(tmp_path: Path) -> Path:
    """Create a temporary work directory."""
    return tmp_path / "work"


@pytest.fixture
def state_manager(temp_work_dir: Path) -> StateManager:
    """Create a state manager for testing."""
    return StateManager(temp_work_dir)


@pytest.fixture
def gitlab_client() -> GitLabClient:
    """Create a mock GitLab client."""
    return GitLabClient(url="https://git.example.com", token="test-token")


@pytest.fixture
def discord_webhook() -> DiscordWebhook:
    """Create a Discord webhook with empty URL (no notifications)."""
    return DiscordWebhook(webhook_url="")


@pytest.fixture
def processor(
    gitlab_client: GitLabClient,
    discord_webhook: DiscordWebhook,
    state_manager: StateManager,
) -> Processor:
    """Create a processor for testing."""
    return Processor(
        gitlab=gitlab_client,
        discord=discord_webhook,
        state=state_manager,
        gitlab_username="claude",
        label_in_progress="In progress",
        label_review="Review",
    )


@pytest.fixture
def project_config(tmp_path: Path) -> ProjectConfig:
    """Create a project config for testing."""
    project_dir = tmp_path / "test-project"
    project_dir.mkdir()
    return ProjectConfig(
        project_id=42,
        path=project_dir,
        name="test-project",
    )


@pytest.fixture
def sample_issue() -> Issue:
    """Create a sample issue for testing."""
    return Issue(
        iid=1,
        title="Fix the bug",
        description="This is a bug description",
        web_url="https://git.example.com/issues/1",
        labels=["bug"],
    )


@pytest.fixture
def sample_mr() -> MergeRequest:
    """Create a sample merge request for testing."""
    return MergeRequest(
        iid=1,
        title="Fix the bug",
        web_url="https://git.example.com/merge_requests/1",
        source_branch="1-fix-the-bug",
        state="opened",
    )


class TestProcessorRunClaude:
    """Tests for the _run_claude method."""

    @patch("subprocess.run")
    def test_run_claude_success(
        self,
        mock_run: Mock,
        processor: Processor,
        project_config: ProjectConfig,
    ) -> None:
        """Test successful Claude execution."""
        mock_run.return_value = Mock(returncode=0, stdout="Done", stderr="")

        success, output = processor._run_claude("Fix the bug", project_config.path)

        assert success is True
        assert "Done" in output

    @patch("subprocess.run")
    def test_run_claude_failure(
        self,
        mock_run: Mock,
        processor: Processor,
        project_config: ProjectConfig,
    ) -> None:
        """Test failed Claude execution."""
        mock_run.return_value = Mock(returncode=1, stdout="", stderr="Error")

        success, output = processor._run_claude("Fix the bug", project_config.path)

        assert success is False
        assert "Error" in output

    @patch("subprocess.run")
    def test_run_claude_timeout(
        self,
        mock_run: Mock,
        processor: Processor,
        project_config: ProjectConfig,
    ) -> None:
        """Test Claude timeout."""
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="ollama", timeout=600)

        success, output = processor._run_claude("Fix the bug", project_config.path)

        assert success is False
        assert "timed out" in output.lower()

    @patch("subprocess.run")
    def test_run_claude_not_found(
        self,
        mock_run: Mock,
        processor: Processor,
        project_config: ProjectConfig,
    ) -> None:
        """Test Claude CLI not found."""
        mock_run.side_effect = FileNotFoundError()

        success, output = processor._run_claude("Fix the bug", project_config.path)

        assert success is False
        assert "not found" in output.lower()


class TestProcessorProcessIssue:
    """Tests for the process_issue method."""

    @patch("gitlab_watcher.processor.GitOps")
    @patch("subprocess.run")
    def test_process_issue_success(
        self,
        mock_run: Mock,
        mock_git_ops_class: Mock,
        processor: Processor,
        project_config: ProjectConfig,
        sample_issue: Issue,
    ) -> None:
        """Test successful issue processing."""
        # Mock GitOps
        mock_git = MagicMock()
        mock_git.checkout.return_value = True
        mock_git_ops_class.return_value = mock_git

        # Mock Claude CLI
        mock_run.return_value = Mock(returncode=0, stdout="Done", stderr="")

        # Mock GitLab client methods
        processor.gitlab.update_issue_labels = Mock(return_value=True)
        processor.gitlab.create_merge_request = Mock(
            return_value=MergeRequest(
                iid=1,
                title="Fix the bug",
                web_url="https://git.example.com/merge_requests/1",
                source_branch="1-fix-the-bug",
                state="opened",
            )
        )

        # Initialize state
        processor.state.init_state(project_config.project_id)

        result = processor.process_issue(project_config, sample_issue)

        assert result is True
        processor.gitlab.update_issue_labels.assert_called()
        mock_git.checkout.assert_called()
        mock_git.push.assert_called()
        processor.gitlab.create_merge_request.assert_called()

    @patch("gitlab_watcher.processor.GitOps")
    def test_process_issue_branch_creation_fails(
        self,
        mock_git_ops_class: Mock,
        processor: Processor,
        project_config: ProjectConfig,
        sample_issue: Issue,
    ) -> None:
        """Test issue processing when branch creation fails."""
        # Mock GitOps
        mock_git = MagicMock()
        mock_git.checkout.return_value = False
        mock_git_ops_class.return_value = mock_git

        # Mock GitLab client methods
        processor.gitlab.update_issue_labels = Mock(return_value=True)

        # Initialize state
        processor.state.init_state(project_config.project_id)

        result = processor.process_issue(project_config, sample_issue)

        assert result is False

    @patch("gitlab_watcher.processor.GitOps")
    @patch("subprocess.run")
    def test_process_issue_claude_fails(
        self,
        mock_run: Mock,
        mock_git_ops_class: Mock,
        processor: Processor,
        project_config: ProjectConfig,
        sample_issue: Issue,
    ) -> None:
        """Test issue processing when Claude fails."""
        # Mock GitOps
        mock_git = MagicMock()
        mock_git.checkout.return_value = True
        mock_git_ops_class.return_value = mock_git

        # Mock Claude CLI failure
        mock_run.return_value = Mock(returncode=1, stdout="", stderr="Error")

        # Mock GitLab client methods
        processor.gitlab.update_issue_labels = Mock(return_value=True)

        # Initialize state
        processor.state.init_state(project_config.project_id)

        result = processor.process_issue(project_config, sample_issue)

        assert result is False


class TestProcessorProcessComment:
    """Tests for the process_comment method."""

    @patch("gitlab_watcher.processor.GitOps")
    @patch("subprocess.run")
    def test_process_comment_success(
        self,
        mock_run: Mock,
        mock_git_ops_class: Mock,
        processor: Processor,
        project_config: ProjectConfig,
        sample_mr: MergeRequest,
    ) -> None:
        """Test successful comment processing."""
        # Mock GitOps
        mock_git = MagicMock()
        mock_git.checkout.return_value = True
        mock_git_ops_class.return_value = mock_git

        # Mock Claude CLI
        mock_run.return_value = Mock(returncode=0, stdout="Done", stderr="")

        # Initialize state
        processor.state.init_state(project_config.project_id)

        result = processor.process_comment(project_config, sample_mr, "Fix this bug")

        assert result is True
        mock_git.checkout.assert_called_with("1-fix-the-bug")
        mock_git.pull.assert_called()

    @patch("gitlab_watcher.processor.GitOps")
    def test_process_comment_checkout_fails(
        self,
        mock_git_ops_class: Mock,
        processor: Processor,
        project_config: ProjectConfig,
        sample_mr: MergeRequest,
    ) -> None:
        """Test comment processing when checkout fails."""
        # Mock GitOps
        mock_git = MagicMock()
        mock_git.checkout.return_value = False
        mock_git_ops_class.return_value = mock_git

        # Initialize state
        processor.state.init_state(project_config.project_id)

        result = processor.process_comment(project_config, sample_mr, "Fix this bug")

        assert result is False

    @patch("gitlab_watcher.processor.GitOps")
    @patch("subprocess.run")
    def test_process_comment_claude_fails(
        self,
        mock_run: Mock,
        mock_git_ops_class: Mock,
        processor: Processor,
        project_config: ProjectConfig,
        sample_mr: MergeRequest,
    ) -> None:
        """Test comment processing when Claude fails."""
        # Mock GitOps
        mock_git = MagicMock()
        mock_git.checkout.return_value = True
        mock_git_ops_class.return_value = mock_git

        # Mock Claude CLI failure
        mock_run.return_value = Mock(returncode=1, stdout="", stderr="Error")

        # Initialize state
        processor.state.init_state(project_config.project_id)

        result = processor.process_comment(project_config, sample_mr, "Fix this bug")

        assert result is False


class TestProcessorCleanup:
    """Tests for the cleanup_after_merge method."""

    @patch("gitlab_watcher.processor.GitOps")
    def test_cleanup_after_merge(
        self,
        mock_git_ops_class: Mock,
        processor: Processor,
        project_config: ProjectConfig,
    ) -> None:
        """Test cleanup after merge."""
        # Mock GitOps
        mock_git = MagicMock()
        mock_git_ops_class.return_value = mock_git

        # Initialize state
        processor.state.init_state(project_config.project_id)
        processor.state.update_mr_state(
            project_config.project_id,
            mr_iid=1,
            mr_state="merged",
            note_id=123,
            branch="1-fix-the-bug",
        )

        processor.cleanup_after_merge(
            project=project_config,
            branch="1-fix-the-bug",
            mr_title="Fix the bug",
            mr_url="https://git.example.com/merge_requests/1",
        )

        mock_git.checkout.assert_called_with("master")
        mock_git.pull.assert_called()
        mock_git.delete_branch.assert_called_with("1-fix-the-bug", force=True)

    @patch("gitlab_watcher.processor.GitOps")
    def test_cleanup_after_merge_no_branch(
        self,
        mock_git_ops_class: Mock,
        processor: Processor,
        project_config: ProjectConfig,
    ) -> None:
        """Test cleanup when no branch is provided."""
        # Mock GitOps
        mock_git = MagicMock()
        mock_git_ops_class.return_value = mock_git

        processor.cleanup_after_merge(
            project=project_config,
            branch="",
            mr_title="Fix the bug",
            mr_url="https://git.example.com/merge_requests/1",
        )

        mock_git.checkout.assert_called_with("master")
        mock_git.delete_branch.assert_not_called()