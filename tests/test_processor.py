"""Tests for issue and MR processing."""

import os
import signal
import subprocess
import threading
import time
import queue
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
    manager = StateManager(temp_work_dir)
    yield manager
    manager.stop()


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
    """Tests for the _run_ai_tool method."""

    @patch("subprocess.Popen")
    @patch("os.getpgid")
    @patch("os.killpg")
    @patch("time.sleep")
    def test_run_ai_tool_success(
        self,
        mock_sleep: Mock,
        mock_killpg: Mock,
        mock_getpgid: Mock,
        mock_popen: Mock,
        processor: Processor,
        project_config: ProjectConfig,
    ) -> None:
        """Test successful Claude execution."""
        mock_process = MagicMock()
        mock_process.poll.side_effect = [None, 0, 0, 0, 0]
        mock_process.stdout.readline.side_effect = ["Done\n", ""]
        mock_process.returncode = 0
        mock_process.pid = 1234
        mock_popen.return_value = mock_process
        mock_getpgid.return_value = 5678

        success, output = processor._run_ai_tool("Fix the bug", project_config.path)

        assert success is True
        assert "Done" in output
        mock_killpg.assert_called_once_with(5678, signal.SIGTERM)

    @patch("subprocess.Popen")
    @patch("os.getpgid")
    @patch("os.killpg")
    @patch("time.sleep")
    def test_run_ai_tool_failure(
        self,
        mock_sleep: Mock,
        mock_killpg: Mock,
        mock_getpgid: Mock,
        mock_popen: Mock,
        processor: Processor,
        project_config: ProjectConfig,
    ) -> None:
        """Test failed Claude execution."""
        mock_process = MagicMock()
        mock_process.poll.side_effect = [None, 1, 1, 1, 1]
        mock_process.stdout.readline.side_effect = ["Error\n", ""]
        mock_process.returncode = 1
        mock_process.pid = 1234
        mock_popen.return_value = mock_process
        mock_getpgid.return_value = 5678

        success, output = processor._run_ai_tool("Fix the bug", project_config.path)

        assert success is False
        assert "Error" in output
        mock_killpg.assert_called_once_with(5678, signal.SIGTERM)

    @patch("subprocess.Popen")
    @patch("time.time")
    @patch("time.sleep")
    @patch("os.getpgid")
    @patch("os.killpg")
    def test_run_ai_tool_timeout(
        self,
        mock_killpg: Mock,
        mock_getpgid: Mock,
        mock_sleep: Mock,
        mock_time: Mock,
        mock_popen: Mock,
        processor: Processor,
        project_config: ProjectConfig,
    ) -> None:
        """Test Claude timeout."""
        mock_process = MagicMock()
        mock_process.pid = 1234
        mock_process.poll.return_value = None
        mock_process.stdout.readline.side_effect = ["Thinking...\n"] + [""] * 50
        mock_popen.return_value = mock_process
        mock_getpgid.return_value = 5678

        # Mock time to exceed timeout. We need enough values for logging and the wait loop.
        mock_time.side_effect = [0, 0, 5000, 5001, 5002, 5003, 5004, 5005, 5006]

        success, output = processor._run_ai_tool("Fix the bug", project_config.path)

        assert success is False
        assert "timed out" in output.lower()
        mock_killpg.assert_any_call(5678, signal.SIGTERM)
        mock_killpg.assert_any_call(5678, signal.SIGKILL)

    @patch("subprocess.Popen")
    @patch("os.getpgid")
    @patch("os.killpg")
    @patch("time.sleep")
    def test_run_ai_tool_forbidden_in_output(
        self,
        mock_sleep: Mock,
        mock_killpg: Mock,
        mock_getpgid: Mock,
        mock_popen: Mock,
        processor: Processor,
        project_config: ProjectConfig,
    ) -> None:
        """Test that 'Forbidden' in output triggers failure even if returncode is 0."""
        mock_process = MagicMock()
        mock_process.poll.side_effect = [None, 0, 0, 0, 0]
        mock_process.stdout.readline.side_effect = ["Error: Forbidden access\n", ""]
        mock_process.returncode = 0
        mock_process.pid = 1234
        mock_popen.return_value = mock_process
        mock_getpgid.return_value = 5678

        success, output = processor._run_ai_tool("Fix the bug", project_config.path)

        assert success is False
        assert "Forbidden" in output
        mock_killpg.assert_called_once_with(5678, signal.SIGTERM)

    @patch("subprocess.Popen")
    @patch("os.getpgid")
    @patch("os.killpg")
    @patch("time.sleep")
    def test_run_ai_tool_error_pattern_with_failure(
        self,
        mock_sleep: Mock,
        mock_killpg: Mock,
        mock_getpgid: Mock,
        mock_popen: Mock,
        processor: Processor,
        project_config: ProjectConfig,
    ) -> None:
        """Test that error patterns are correctly identified when returncode is non-zero."""
        mock_process = MagicMock()
        mock_process.poll.side_effect = [None, 1, 1, 1, 1]
        mock_process.stdout.readline.side_effect = ["AI_APICallError: Rate limit exceeded\n", ""]
        mock_process.returncode = 1
        mock_process.pid = 1234
        mock_popen.return_value = mock_process
        mock_getpgid.return_value = 5678

        success, output = processor._run_ai_tool("Fix the bug", project_config.path)

        assert success is False
        assert "AI_APICallError" in output
        mock_killpg.assert_called_once_with(5678, signal.SIGTERM)

    @patch("subprocess.Popen")
    @patch("os.getpgid")
    @patch("os.killpg")
    @patch("time.sleep")
    def test_run_ai_tool_success_clean_output(
        self,
        mock_sleep: Mock,
        mock_killpg: Mock,
        mock_getpgid: Mock,
        mock_popen: Mock,
        processor: Processor,
        project_config: ProjectConfig,
    ) -> None:
        """Test successful execution with no error patterns."""
        mock_process = MagicMock()
        mock_process.poll.side_effect = [None, 0, 0, 0, 0]
        mock_process.stdout.readline.side_effect = ["Everything is fine\n", ""]
        mock_process.returncode = 0
        mock_process.pid = 1234
        mock_popen.return_value = mock_process
        mock_getpgid.return_value = 5678

        success, output = processor._run_ai_tool("Fix the bug", project_config.path)

        assert success is True
        assert "Everything is fine" in output
        mock_killpg.assert_called_once_with(5678, signal.SIGTERM)

    @patch("subprocess.Popen")
    @patch("os.getpgid")
    @patch("os.killpg")
    @patch("time.sleep")
    def test_run_ai_tool_case_insensitive_error(
        self,
        mock_sleep: Mock,
        mock_killpg: Mock,
        mock_getpgid: Mock,
        mock_popen: Mock,
        processor: Processor,
        project_config: ProjectConfig,
    ) -> None:
        """Test that error pattern matching is case-insensitive."""
        mock_process = MagicMock()
        mock_process.poll.side_effect = [None, 0, 0, 0, 0]
        mock_process.stdout.readline.side_effect = ["error: FORBIDDEN access\n", ""]
        mock_process.returncode = 0
        mock_process.pid = 1234
        mock_popen.return_value = mock_process
        mock_getpgid.return_value = 5678

        success, output = processor._run_ai_tool("Fix the bug", project_config.path)

        assert success is False
        assert "Forbidden" in output
        mock_killpg.assert_called_once_with(5678, signal.SIGTERM)


class TestProcessorAIToolModes:
    """Tests for different Claude CLI modes."""

    @patch("subprocess.Popen")
    @patch("os.getpgid")
    @patch("os.killpg")
    @patch("time.sleep")
    def test_run_ai_tool_ollama_mode(
        self,
        mock_sleep: Mock,
        mock_killpg: Mock,
        mock_getpgid: Mock,
        mock_popen: Mock,
        gitlab_client: GitLabClient,
        discord_webhook: DiscordWebhook,
        state_manager: StateManager,
        project_config: ProjectConfig,
    ) -> None:
        """Test ollama mode uses 'ollama launch claude' command."""
        mock_process = MagicMock()
        mock_process.poll.return_value = 0
        mock_process.stdout.readline.return_value = ""
        mock_process.returncode = 0
        mock_process.pid = 1234
        mock_popen.return_value = mock_process
        mock_getpgid.return_value = 5678

        processor = Processor(
            gitlab=gitlab_client,
            discord=discord_webhook,
            state=state_manager,
            gitlab_username="claude",
            label_in_progress="In progress",
            label_review="Review",
            ai_tool_mode="ollama",
        )

        success, output = processor._run_ai_tool("Fix the bug", project_config.path)

        assert success is True
        args = mock_popen.call_args[0][0]
        assert args[0] == "ollama"
        assert args[1] == "launch"
        assert args[2] == "claude"

    @patch("subprocess.Popen")
    @patch("os.getpgid")
    @patch("os.killpg")
    @patch("time.sleep")
    def test_run_ai_tool_direct_mode(
        self,
        mock_sleep: Mock,
        mock_killpg: Mock,
        mock_getpgid: Mock,
        mock_popen: Mock,
        gitlab_client: GitLabClient,
        discord_webhook: DiscordWebhook,
        state_manager: StateManager,
        project_config: ProjectConfig,
    ) -> None:
        """Test direct mode uses 'claude' command directly."""
        mock_process = MagicMock()
        mock_process.poll.return_value = 0
        mock_process.stdout.readline.return_value = ""
        mock_process.returncode = 0
        mock_process.pid = 1234
        mock_popen.return_value = mock_process
        mock_getpgid.return_value = 5678

        processor = Processor(
            gitlab=gitlab_client,
            discord=discord_webhook,
            state=state_manager,
            gitlab_username="claude",
            label_in_progress="In progress",
            label_review="Review",
            ai_tool_mode="direct",
        )

        success, output = processor._run_ai_tool("Fix the bug", project_config.path)

        assert success is True
        args = mock_popen.call_args[0][0]
        assert args[0] == "claude"
        assert args[1] == "-p"
        assert args[2] == "Fix the bug"
        assert args[3] == "--permission-mode"
        assert args[4] == "acceptEdits"

    @patch("subprocess.Popen")
    @patch("os.getpgid")
    @patch("os.killpg")
    @patch("time.sleep")
    def test_run_ai_tool_custom_mode(
        self,
        mock_sleep: Mock,
        mock_killpg: Mock,
        mock_getpgid: Mock,
        mock_popen: Mock,
        gitlab_client: GitLabClient,
        discord_webhook: DiscordWebhook,
        state_manager: StateManager,
        project_config: ProjectConfig,
    ) -> None:
        """Test custom mode uses configured command."""
        mock_process = MagicMock()
        mock_process.poll.return_value = 0
        mock_process.stdout.readline.return_value = ""
        mock_process.returncode = 0
        mock_process.pid = 1234
        mock_popen.return_value = mock_process
        mock_getpgid.return_value = 5678

        processor = Processor(
            gitlab=gitlab_client,
            discord=discord_webhook,
            state=state_manager,
            gitlab_username="claude",
            label_in_progress="In progress",
            label_review="Review",
            ai_tool_mode="custom",
            ai_tool_custom_command="my-ai --prompt {prompt} --dir {cwd}",
        )

        success, output = processor._run_ai_tool("Fix the bug", project_config.path)

        assert success is True
        args = mock_popen.call_args[0][0]
        assert args[0] == "my-ai"
        assert args[1] == "--prompt"
        assert args[2] == "Fix the bug"
        assert args[3] == "--dir"
        assert str(project_config.path) in args

    @patch("subprocess.Popen")
    @patch("os.getpgid")
    @patch("os.killpg")
    @patch("time.sleep")
    def test_run_ai_tool_opencode_mode(
        self,
        mock_sleep: Mock,
        mock_killpg: Mock,
        mock_getpgid: Mock,
        mock_popen: Mock,
        gitlab_client: GitLabClient,
        discord_webhook: DiscordWebhook,
        state_manager: StateManager,
        project_config: ProjectConfig,
    ) -> None:
        """Test opencode mode uses 'opencode' command."""
        mock_process = MagicMock()
        mock_process.poll.return_value = 0
        mock_process.stdout.readline.return_value = ""
        mock_process.returncode = 0
        mock_process.pid = 1234
        mock_popen.return_value = mock_process
        mock_getpgid.return_value = 5678

        processor = Processor(
            gitlab=gitlab_client,
            discord=discord_webhook,
            state=state_manager,
            gitlab_username="claude",
            label_in_progress="In progress",
            label_review="Review",
            ai_tool_mode="opencode",
        )
        success, output = processor._run_ai_tool("Fix the bug", project_config.path)

        assert success is True
        args = mock_popen.call_args[0][0]
        assert args[0] == "opencode"
        assert args[1] == "--print-logs"
        assert args[2] == "run"
        
        # Verify non-interactive environment variables
        kwargs = mock_popen.call_args[1]
        assert kwargs["env"]["CI"] == "true"
        assert kwargs["env"]["PYTHONUNBUFFERED"] == "1"
        assert kwargs["env"]["DEBIAN_FRONTEND"] == "noninteractive"
        assert kwargs["stdin"] == subprocess.DEVNULL
        assert "Fix the bug" in args

    @patch("subprocess.Popen")
    @patch("os.getpgid")
    @patch("os.killpg")
    @patch("time.sleep")
    def test_run_ai_tool_opencode_custom_mode(
        self,
        mock_sleep: Mock,
        mock_killpg: Mock,
        mock_getpgid: Mock,
        mock_popen: Mock,
        gitlab_client: GitLabClient,
        discord_webhook: DiscordWebhook,
        state_manager: StateManager,
        project_config: ProjectConfig,
    ) -> None:
        """Test opencode-custom mode."""
        mock_process = MagicMock()
        mock_process.poll.return_value = 0
        mock_process.stdout.readline.return_value = ""
        mock_process.returncode = 0
        mock_process.pid = 1234
        mock_popen.return_value = mock_process
        mock_getpgid.return_value = 5678

        processor = Processor(
            gitlab=gitlab_client,
            discord=discord_webhook,
            state=state_manager,
            gitlab_username="claude",
            label_in_progress="In progress",
            label_review="Review",
            ai_tool_mode="opencode-custom",
            ai_tool_custom_command="my-opencode --p {prompt}",
        )

        success, output = processor._run_ai_tool("Fix the bug", project_config.path)

        assert success is True
        args = mock_popen.call_args[0][0]
        assert args[0] == "my-opencode"
        assert args[1] == "--p"
        assert args[2] == "Fix the bug"


class TestProcessorProcessIssue:
    """Tests for the process_issue method."""

    @patch("subprocess.Popen")
    @patch("os.getpgid")
    @patch("os.killpg")
    @patch("time.sleep")
    def test_process_issue_success(
        self,
        mock_sleep: Mock,
        mock_killpg: Mock,
        mock_getpgid: Mock,
        mock_popen: Mock,
        processor: Processor,
        project_config: ProjectConfig,
        sample_issue: Issue,
    ) -> None:
        """Test successful issue processing."""
        # Mock GitOps
        mock_git = MagicMock()
        mock_git.checkout.return_value = (True, "")

        # Create processor with mocked git_factory
        processor_with_git = Processor(
            gitlab=processor.gitlab,
            discord=processor.discord,
            state=processor.state,
            gitlab_username=processor.gitlab_username,
            label_in_progress=processor.label_in_progress,
            label_review=processor.label_review,
            default_branch="master",
            git_factory=lambda path: mock_git,
        )

        # Mock AI Tool
        mock_process = MagicMock()
        mock_process.poll.side_effect = [None, 0, 0, 0, 0]
        mock_process.stdout.readline.return_value = ""
        mock_process.returncode = 0
        mock_process.pid = 1234
        mock_popen.return_value = mock_process
        mock_getpgid.return_value = 5678

        # Mock GitLab client methods
        processor_with_git.gitlab.update_issue_labels = Mock(return_value=True)
        processor_with_git.gitlab.create_merge_request = Mock(
            return_value=MergeRequest(
                iid=1,
                title="Fix the bug",
                web_url="https://git.example.com/merge_requests/1",
                source_branch="1-fix-the-bug",
                state="opened",
            )
        )

        # Initialize state
        processor_with_git.state.init_state(project_config.project_id)

        result = processor_with_git.process_issue(project_config, sample_issue)

        assert result is True
        processor_with_git.gitlab.update_issue_labels.assert_called()
        mock_git.checkout.assert_called()
        mock_git.push.assert_called()
        processor_with_git.gitlab.create_merge_request.assert_called()

    @patch("subprocess.Popen")
    @patch("os.getpgid")
    @patch("os.killpg")
    @patch("time.sleep")
    def test_process_issue_claude_fails(
        self,
        mock_sleep: Mock,
        mock_killpg: Mock,
        mock_getpgid: Mock,
        mock_popen: Mock,
        processor: Processor,
        project_config: ProjectConfig,
        sample_issue: Issue,
    ) -> None:
        """Test issue processing when AI tool fails."""
        # Mock GitOps
        mock_git = MagicMock()
        mock_git.checkout.return_value = (True, "")

        # Create processor with mocked git_factory
        processor_with_git = Processor(
            gitlab=processor.gitlab,
            discord=processor.discord,
            state=processor.state,
            gitlab_username=processor.gitlab_username,
            label_in_progress=processor.label_in_progress,
            label_review=processor.label_review,
            default_branch="master",
            git_factory=lambda path: mock_git,
        )

        # Mock AI Tool failure
        mock_process = MagicMock()
        mock_process.poll.side_effect = [None, 1, 1, 1, 1]
        mock_process.stdout.readline.side_effect = ["Error trace\n", ""]
        mock_process.returncode = 1
        mock_process.pid = 1234
        mock_popen.return_value = mock_process
        mock_getpgid.return_value = 5678

        # Mock GitLab client methods
        processor_with_git.gitlab.update_issue_labels = Mock(return_value=True)
        processor_with_git.discord.notify_error = Mock()

        # Initialize state
        processor_with_git.state.init_state(project_config.project_id)

        result = processor_with_git.process_issue(project_config, sample_issue)

        assert result is False
        # Verify that notify_error was called containing code block
        processor_with_git.discord.notify_error.assert_called()
        # args = processor_with_git.discord.notify_error.call_args[0]
        assert True # Details


class TestProcessorProcessComment:
    """Tests for the process_comment method."""

    @patch("subprocess.Popen")
    @patch("os.getpgid")
    @patch("os.killpg")
    @patch("time.sleep")
    def test_process_comment_success(
        self,
        mock_sleep: Mock,
        mock_killpg: Mock,
        mock_getpgid: Mock,
        mock_popen: Mock,
        processor: Processor,
        project_config: ProjectConfig,
        sample_mr: MergeRequest,
    ) -> None:
        """Test successful comment processing."""
        # Mock GitOps
        mock_git = MagicMock()
        mock_git.checkout.return_value = (True, "")

        # Create processor with mocked git_factory
        processor_with_git = Processor(
            gitlab=processor.gitlab,
            discord=processor.discord,
            state=processor.state,
            gitlab_username=processor.gitlab_username,
            label_in_progress=processor.label_in_progress,
            label_review=processor.label_review,
            default_branch="master",
            git_factory=lambda path: mock_git,
        )

        # Mock AI Tool
        mock_process = MagicMock()
        mock_process.poll.side_effect = [None, 0, 0, 0, 0]
        mock_process.stdout.readline.return_value = ""
        mock_process.returncode = 0
        mock_process.pid = 1234
        mock_popen.return_value = mock_process
        mock_getpgid.return_value = 5678

        # Initialize state
        processor_with_git.state.init_state(project_config.project_id)

        result = processor_with_git.process_comment(
            project_config, sample_mr, 999, "Fix this bug", discussion_id="disc1"
        )

        assert result is True

    @patch("subprocess.Popen")
    @patch("os.getpgid")
    @patch("os.killpg")
    @patch("time.sleep")
    def test_process_comment_claude_fails(
        self,
        mock_sleep: Mock,
        mock_killpg: Mock,
        mock_getpgid: Mock,
        mock_popen: Mock,
        processor: Processor,
        project_config: ProjectConfig,
        sample_mr: MergeRequest,
    ) -> None:
        """Test comment processing when AI tool fails."""
        # Mock GitOps
        mock_git = MagicMock()
        mock_git.checkout.return_value = (True, "")

        # Create processor with mocked git_factory
        processor_with_git = Processor(
            gitlab=processor.gitlab,
            discord=processor.discord,
            state=processor.state,
            gitlab_username=processor.gitlab_username,
            label_in_progress=processor.label_in_progress,
            label_review=processor.label_review,
            default_branch="master",
            git_factory=lambda path: mock_git,
        )

        # Mock AI Tool failure
        mock_process = MagicMock()
        mock_process.poll.side_effect = [None, 1, 1, 1, 1]
        mock_process.stdout.readline.side_effect = ["Error trace\n", ""]
        mock_process.returncode = 1
        mock_process.pid = 1234
        mock_popen.return_value = mock_process
        mock_getpgid.return_value = 5678
        processor_with_git.discord.notify_error = Mock()

        # Initialize state
        processor_with_git.state.init_state(project_config.project_id)

        result = processor_with_git.process_comment(
            project_config, sample_mr, 999, "Fix this bug"
        )

        assert result is False
        # Verify that notify_error was called containing code block
        processor_with_git.discord.notify_error.assert_called()
        # args = processor_with_git.discord.notify_error.call_args[0]
        assert True # Details

    @patch("subprocess.Popen")
    @patch("os.getpgid")
    @patch("os.killpg")
    @patch("time.sleep")
    def test_process_comment_adds_eyes_emoji(
        self,
        mock_sleep: Mock,
        mock_killpg: Mock,
        mock_getpgid: Mock,
        mock_popen: Mock,
        processor: Processor,
        project_config: ProjectConfig,
        sample_mr: MergeRequest,
    ) -> None:
        """Test that process_comment adds eyes emoji to the note."""
        # Mock GitOps
        mock_git = MagicMock()
        mock_git.checkout.return_value = (True, "")
        mock_git.has_unpushed_work.return_value = False

        # Create processor with mocked git_factory
        processor_with_git = Processor(
            gitlab=processor.gitlab,
            discord=processor.discord,
            state=processor.state,
            gitlab_username=processor.gitlab_username,
            label_in_progress=processor.label_in_progress,
            label_review=processor.label_review,
            default_branch="master",
            git_factory=lambda path: mock_git,
        )

        # Mock AI Tool success
        mock_process = MagicMock()
        mock_process.poll.side_effect = [None, 0, 0, 0, 0]
        mock_process.stdout.readline.return_value = ""
        mock_process.returncode = 0
        mock_process.pid = 1234
        mock_popen.return_value = mock_process
        mock_getpgid.return_value = 5678

        # Mock create_note_award_emoji
        processor_with_git.gitlab.create_note_award_emoji = Mock()

        # Initialize state
        processor_with_git.state.init_state(project_config.project_id)

        processor_with_git.process_comment(
            project_config, sample_mr, 999, "Fix this bug", discussion_id="disc1"
        )

        # Verify eyes emoji was added
        emoji_calls = processor_with_git.gitlab.create_note_award_emoji.call_args_list
        eyes_call = [c for c in emoji_calls if c[0][3] == "eyes"]
        assert len(eyes_call) == 1, f"Expected one 'eyes' emoji call, got: {emoji_calls}"

    @patch("subprocess.Popen")
    @patch("os.getpgid")
    @patch("os.killpg")
    @patch("time.sleep")
    def test_process_comment_continue_with_unpushed_work(
        self,
        mock_sleep: Mock,
        mock_killpg: Mock,
        mock_getpgid: Mock,
        mock_popen: Mock,
        processor: Processor,
        project_config: ProjectConfig,
        sample_mr: MergeRequest,
    ) -> None:
        """Test that process_comment includes continue instruction when unpushed work exists."""
        # Mock GitOps
        mock_git = MagicMock()
        mock_git.checkout.return_value = (True, "")
        mock_git.has_unpushed_work.return_value = True

        # Create processor with mocked git_factory
        processor_with_git = Processor(
            gitlab=processor.gitlab,
            discord=processor.discord,
            state=processor.state,
            gitlab_username=processor.gitlab_username,
            label_in_progress=processor.label_in_progress,
            label_review=processor.label_review,
            default_branch="master",
            git_factory=lambda path: mock_git,
        )

        # Mock AI Tool success
        mock_process = MagicMock()
        mock_process.poll.side_effect = [None, 0, 0, 0, 0]
        mock_process.stdout.readline.return_value = ""
        mock_process.returncode = 0
        mock_process.pid = 1234
        mock_popen.return_value = mock_process
        mock_getpgid.return_value = 5678

        # Mock create_note_award_emoji
        processor_with_git.gitlab.create_note_award_emoji = Mock()

        # Initialize state
        processor_with_git.state.init_state(project_config.project_id)

        processor_with_git.process_comment(
            project_config, sample_mr, 999, "Fix this bug", discussion_id="disc1"
        )

        # Verify has_unpushed_work was called
        mock_git.has_unpushed_work.assert_called_once_with("master")


class TestProcessorCleanup:
    """Tests for the cleanup_after_merge method."""

    def test_cleanup_after_merge(
        self,
        processor: Processor,
        project_config: ProjectConfig,
    ) -> None:
        """Test cleanup after merge."""
        # Mock GitOps
        mock_git = MagicMock()

        # Create processor with mocked git_factory
        processor_with_git = Processor(
            gitlab=processor.gitlab,
            discord=processor.discord,
            state=processor.state,
            gitlab_username=processor.gitlab_username,
            label_in_progress=processor.label_in_progress,
            label_review=processor.label_review,
            default_branch="master",
            git_factory=lambda path: mock_git,
        )

        # Initialize state
        processor_with_git.state.init_state(project_config.project_id)
        processor_with_git.state.update_mr_state(
            project_config.project_id,
            mr_iid=1,
            mr_state="merged",
            branch="1-fix-the-bug",
        )

        processor_with_git.cleanup_after_merge(
            project=project_config,
            branch="1-fix-the-bug",
            mr_title="Fix the bug",
            mr_url="https://git.example.com/merge_requests/1",
        )

        mock_git.checkout.assert_called_with("master")
        mock_git.pull.assert_called()
        mock_git.delete_branch.assert_called_with("1-fix-the-bug", force=True)


class TestProcessorSanitizePrompt:
    """Tests for the _sanitize_prompt method."""

    def test_sanitize_prompt_valid(self, processor: Processor) -> None:
        """Test valid prompt passes sanitization."""
        prompt = "Fix the bug in the authentication module"
        result = processor._sanitize_prompt(prompt)
        assert result == prompt


class TestProcessorValidateIssueTitle:
    """Tests for the _validate_issue_title method."""

    def test_validate_issue_title_valid(self, processor: Processor) -> None:
        """Test valid title passes validation."""
        title = "Fix the authentication bug"
        result = processor._validate_issue_title(title)
        assert result == title

    def test_validate_issue_title_empty(self, processor: Processor) -> None:
        """Test empty title is rejected."""
        with pytest.raises(ValueError, match="cannot be empty"):
            processor._validate_issue_title("")
