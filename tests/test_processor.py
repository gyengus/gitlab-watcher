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
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="ollama", timeout=3600)

        success, output = processor._run_claude("Fix the bug", project_config.path)

        assert success is False
        assert "timed out" in output.lower()
        assert "ollama" in output


    @patch("subprocess.run")
    def test_run_claude_timeout_with_output(
        self,
        mock_run: Mock,
        processor: Processor,
        project_config: ProjectConfig,
    ) -> None:
        """Test Claude timeout with partial output."""
        mock_run.side_effect = subprocess.TimeoutExpired(
            cmd="ollama", timeout=3600, output="Partial output", stderr="Some error"
        )

        success, output = processor._run_claude("Fix the bug", project_config.path)

        assert success is False
        assert "Partial output" in output
        assert "Some error" in output

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


class TestProcessorAIToolModes:
    """Tests for different Claude CLI modes."""

    @patch("subprocess.run")
    def test_run_claude_ollama_mode(
        self,
        mock_run: Mock,
        gitlab_client: GitLabClient,
        discord_webhook: DiscordWebhook,
        state_manager: StateManager,
        project_config: ProjectConfig,
    ) -> None:
        """Test ollama mode uses 'ollama launch claude' command."""
        mock_run.return_value = Mock(returncode=0, stdout="Done", stderr="")

        processor = Processor(
            gitlab=gitlab_client,
            discord=discord_webhook,
            state=state_manager,
            gitlab_username="claude",
            label_in_progress="In progress",
            label_review="Review",
            ai_tool_mode="ollama",
        )

        success, output = processor._run_claude("Fix the bug", project_config.path)

        assert success is True
        args = mock_run.call_args[0][0]
        assert args[0] == "ollama"
        assert args[1] == "launch"
        assert args[2] == "claude"

    @patch("subprocess.run")
    def test_run_claude_direct_mode(
        self,
        mock_run: Mock,
        gitlab_client: GitLabClient,
        discord_webhook: DiscordWebhook,
        state_manager: StateManager,
        project_config: ProjectConfig,
    ) -> None:
        """Test direct mode uses 'claude' command directly."""
        mock_run.return_value = Mock(returncode=0, stdout="Done", stderr="")

        processor = Processor(
            gitlab=gitlab_client,
            discord=discord_webhook,
            state=state_manager,
            gitlab_username="claude",
            label_in_progress="In progress",
            label_review="Review",
            ai_tool_mode="direct",
        )

        success, output = processor._run_claude("Fix the bug", project_config.path)

        assert success is True
        args = mock_run.call_args[0][0]
        assert args[0] == "claude"
        assert args[1] == "-p"
        assert args[2] == "Fix the bug"
        assert args[3] == "--permission-mode"
        assert args[4] == "acceptEdits"
        assert "ollama" not in args

    @patch("subprocess.run")
    def test_run_claude_custom_mode(
        self,
        mock_run: Mock,
        gitlab_client: GitLabClient,
        discord_webhook: DiscordWebhook,
        state_manager: StateManager,
        project_config: ProjectConfig,
    ) -> None:
        """Test custom mode uses configured command."""
        mock_run.return_value = Mock(returncode=0, stdout="Done", stderr="")

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

        success, output = processor._run_claude("Fix the bug", project_config.path)

        assert success is True
        args = mock_run.call_args[0][0]
        assert args[0] == "my-ai"
        assert args[1] == "--prompt"
        assert args[2] == "Fix the bug"
        assert args[3] == "--dir"
        assert str(project_config.path) in args

    def test_run_claude_custom_mode_missing_command(
        self,
        gitlab_client: GitLabClient,
        discord_webhook: DiscordWebhook,
        state_manager: StateManager,
        project_config: ProjectConfig,
    ) -> None:
        """Test custom mode returns error when command not set."""
        processor = Processor(
            gitlab=gitlab_client,
            discord=discord_webhook,
            state=state_manager,
            gitlab_username="claude",
            label_in_progress="In progress",
            label_review="Review",
            ai_tool_mode="custom",
            ai_tool_custom_command="",
        )

        success, output = processor._run_claude("Fix the bug", project_config.path)

        assert success is False
        assert "AI_TOOL_CUSTOM_COMMAND" in output

    @patch("subprocess.run")
    def test_run_claude_opencode_mode(
        self,
        mock_run: Mock,
        gitlab_client: GitLabClient,
        discord_webhook: DiscordWebhook,
        state_manager: StateManager,
        project_config: ProjectConfig,
    ) -> None:
        """Test opencode mode uses 'opencode' command."""
        mock_run.return_value = Mock(returncode=0, stdout="Done", stderr="")

        processor = Processor(
            gitlab=gitlab_client,
            discord=discord_webhook,
            state=state_manager,
            gitlab_username="claude",
            label_in_progress="In progress",
            label_review="Review",
            ai_tool_mode="opencode",
        )

        success, output = processor._run_claude("Fix the bug", project_config.path)

        assert success is True
        args = mock_run.call_args[0][0]
        assert args[0] == "opencode"
        assert args[1] == "run"
        assert "Fix the bug" in args
        assert "--print-logs" in args

    def test_run_claude_invalid_mode(
        self,
        gitlab_client: GitLabClient,
        discord_webhook: DiscordWebhook,
        state_manager: StateManager,
        project_config: ProjectConfig,
    ) -> None:
        """Test invalid mode returns error."""
        processor = Processor(
            gitlab=gitlab_client,
            discord=discord_webhook,
            state=state_manager,
            gitlab_username="claude",
            label_in_progress="In progress",
            label_review="Review",
            ai_tool_mode="invalid",
        )

        success, output = processor._run_claude("Fix the bug", project_config.path)

        assert success is False
        assert "Unknown AI_TOOL_MODE" in output


class TestProcessorProcessIssue:
    """Tests for the process_issue method."""

    @patch("subprocess.run")
    def test_process_issue_success(
        self,
        mock_run: Mock,
        processor: Processor,
        project_config: ProjectConfig,
        sample_issue: Issue,
    ) -> None:
        """Test successful issue processing."""
        # Mock GitOps
        mock_git = MagicMock()
        mock_git.checkout.return_value = True

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

        # Mock Claude CLI
        mock_run.return_value = Mock(returncode=0, stdout="Done", stderr="")

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

    def test_process_issue_branch_creation_fails(
        self,
        processor: Processor,
        project_config: ProjectConfig,
        sample_issue: Issue,
    ) -> None:
        """Test issue processing when branch creation fails."""
        # Mock GitOps
        mock_git = MagicMock()
        mock_git.checkout.return_value = False

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

        # Mock GitLab client methods
        processor_with_git.gitlab.update_issue_labels = Mock(return_value=True)

        # Initialize state
        processor_with_git.state.init_state(project_config.project_id)

        result = processor_with_git.process_issue(project_config, sample_issue)

        assert result is False

    @patch("subprocess.run")
    def test_process_issue_claude_fails(
        self,
        mock_run: Mock,
        processor: Processor,
        project_config: ProjectConfig,
        sample_issue: Issue,
    ) -> None:
        """Test issue processing when Claude fails."""
        # Mock GitOps
        mock_git = MagicMock()
        mock_git.checkout.return_value = True

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

        # Mock Claude CLI failure
        mock_run.return_value = Mock(returncode=1, stdout="", stderr="Error")

        # Mock GitLab client methods
        processor_with_git.gitlab.update_issue_labels = Mock(return_value=True)

        # Initialize state
        processor_with_git.state.init_state(project_config.project_id)

        result = processor_with_git.process_issue(project_config, sample_issue)

        assert result is False


class TestProcessorProcessComment:
    """Tests for the process_comment method."""

    @patch("subprocess.run")
    def test_process_comment_success(
        self,
        mock_run: Mock,
        processor: Processor,
        project_config: ProjectConfig,
        sample_mr: MergeRequest,
    ) -> None:
        """Test successful comment processing."""
        # Mock GitOps
        mock_git = MagicMock()
        mock_git.checkout.return_value = True

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

        # Mock Claude CLI
        mock_run.return_value = Mock(returncode=0, stdout="Done", stderr="")

        # Initialize state
        processor_with_git.state.init_state(project_config.project_id)

        result = processor_with_git.process_comment(
            project_config, sample_mr, "Fix this bug"
        )

        assert result is True
        mock_git.checkout.assert_called_with("1-fix-the-bug")
        mock_git.pull.assert_called()

    def test_process_comment_checkout_fails(
        self,
        processor: Processor,
        project_config: ProjectConfig,
        sample_mr: MergeRequest,
    ) -> None:
        """Test comment processing when checkout fails."""
        # Mock GitOps
        mock_git = MagicMock()
        mock_git.checkout.return_value = False

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

        result = processor_with_git.process_comment(
            project_config, sample_mr, "Fix this bug"
        )

        assert result is False

    @patch("subprocess.run")
    def test_process_comment_claude_fails(
        self,
        mock_run: Mock,
        processor: Processor,
        project_config: ProjectConfig,
        sample_mr: MergeRequest,
    ) -> None:
        """Test comment processing when Claude fails."""
        # Mock GitOps
        mock_git = MagicMock()
        mock_git.checkout.return_value = True

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

        # Mock Claude CLI failure
        mock_run.return_value = Mock(returncode=1, stdout="", stderr="Error")

        # Initialize state
        processor_with_git.state.init_state(project_config.project_id)

        result = processor_with_git.process_comment(
            project_config, sample_mr, "Fix this bug"
        )

        assert result is False

        # Mock Claude CLI failure
        mock_run.return_value = Mock(returncode=1, stdout="", stderr="Error")

        # Initialize state
        processor.state.init_state(project_config.project_id)

        result = processor.process_comment(project_config, sample_mr, "Fix this bug")

        assert result is False


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
            note_id=123,
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

    def test_cleanup_after_merge_no_branch(
        self,
        processor: Processor,
        project_config: ProjectConfig,
    ) -> None:
        """Test cleanup when no branch is provided."""
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

        processor_with_git.cleanup_after_merge(
            project=project_config,
            branch="",
            mr_title="Fix the bug",
            mr_url="https://git.example.com/merge_requests/1",
        )

        mock_git.checkout.assert_called_with("master")
        mock_git.delete_branch.assert_not_called()


class TestProcessorSanitizePrompt:
    """Tests for the _sanitize_prompt method."""

    def test_sanitize_prompt_valid(self, processor: Processor) -> None:
        """Test valid prompt passes sanitization."""
        prompt = "Fix the bug in the authentication module"
        result = processor._sanitize_prompt(prompt)
        assert result == prompt

    def test_sanitize_prompt_truncates_long_prompt(self, processor: Processor) -> None:
        """Test long prompt is truncated."""
        from gitlab_watcher.processor import MAX_PROMPT_LENGTH

        long_prompt = "x" * (MAX_PROMPT_LENGTH + 100)
        result = processor._sanitize_prompt(long_prompt)
        assert len(result) == MAX_PROMPT_LENGTH

    def test_sanitize_prompt_rejects_command_substitution(
        self, processor: Processor
    ) -> None:
        """Test prompt with command substitution is rejected."""
        with pytest.raises(ValueError, match="forbidden pattern"):
            processor._sanitize_prompt("Fix $(rm -rf /)")

    def test_sanitize_prompt_rejects_backtick_command(
        self, processor: Processor
    ) -> None:
        """Test prompt with backtick command is rejected."""
        with pytest.raises(ValueError, match="forbidden pattern"):
            processor._sanitize_prompt("Fix `rm -rf /`")

    def test_sanitize_prompt_rejects_variable_expansion(
        self, processor: Processor
    ) -> None:
        """Test prompt with variable expansion is rejected."""
        with pytest.raises(ValueError, match="forbidden pattern"):
            processor._sanitize_prompt("Fix ${PATH}")

    def test_sanitize_prompt_rejects_variable_reference(
        self, processor: Processor
    ) -> None:
        """Test prompt with variable reference is rejected."""
        with pytest.raises(ValueError, match="forbidden pattern"):
            processor._sanitize_prompt("Fix $HOME")


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

    def test_validate_issue_title_whitespace_only(self, processor: Processor) -> None:
        """Test whitespace-only title is rejected."""
        with pytest.raises(ValueError, match="cannot be empty"):
            processor._validate_issue_title("   ")

    def test_validate_issue_title_truncates_long_title(
        self, processor: Processor
    ) -> None:
        """Test long title is truncated."""
        from gitlab_watcher.processor import MAX_TITLE_LENGTH

        long_title = "x" * (MAX_TITLE_LENGTH + 100)
        result = processor._validate_issue_title(long_title)
        assert len(result) == MAX_TITLE_LENGTH

    def test_validate_issue_title_removes_control_characters(
        self, processor: Processor
    ) -> None:
        """Test control characters are removed."""
        title = "Fix\nthe\tbug"
        result = processor._validate_issue_title(title)
        assert "\n" not in result
        assert "\t" not in result

    def test_validate_issue_title_strips_whitespace(self, processor: Processor) -> None:
        """Test title is stripped of leading/trailing whitespace."""
        title = "  Fix the bug  "
        result = processor._validate_issue_title(title)
        assert result == "Fix the bug"


class TestProcessorValidateBranchName:
    """Tests for the _validate_branch_name method."""

    def test_validate_branch_name_valid(self, processor: Processor) -> None:
        """Test valid branch name passes validation."""
        branch = "123-fix-the-bug"
        result = processor._validate_branch_name(branch)
        assert result == branch

    def test_validate_branch_name_empty_returns_default(
        self, processor: Processor
    ) -> None:
        """Test empty branch name returns default."""
        result = processor._validate_branch_name("")
        assert result == "auto-branch"

    def test_validate_branch_name_whitespace_returns_default(
        self, processor: Processor
    ) -> None:
        """Test whitespace-only branch name returns default."""
        result = processor._validate_branch_name("   ")
        assert result == "auto-branch"

    def test_validate_branch_name_removes_special_chars(
        self, processor: Processor
    ) -> None:
        """Test special characters are removed."""
        branch = "123-fix@the#bug!"
        result = processor._validate_branch_name(branch)
        assert "@" not in result
        assert "#" not in result
        assert "!" not in result

    def test_validate_branch_name_removes_consecutive_hyphens(
        self, processor: Processor
    ) -> None:
        """Test consecutive hyphens are removed."""
        branch = "123--fix---bug"
        result = processor._validate_branch_name(branch)
        assert "--" not in result

    def test_validate_branch_name_removes_leading_trailing_hyphens(
        self, processor: Processor
    ) -> None:
        """Test leading/trailing hyphens are removed."""
        branch = "-123-fix-bug-"
        result = processor._validate_branch_name(branch)
        assert not result.startswith("-")
        assert not result.endswith("-")

    def test_validate_branch_name_truncates_long_name(
        self, processor: Processor
    ) -> None:
        """Test long branch name is truncated."""
        from gitlab_watcher.processor import MAX_BRANCH_LENGTH

        long_branch = "x" * (MAX_BRANCH_LENGTH + 100)
        result = processor._validate_branch_name(long_branch)
        assert len(result) <= MAX_BRANCH_LENGTH
