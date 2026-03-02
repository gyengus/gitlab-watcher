"""Tests for watcher main functionality."""

import tempfile
from pathlib import Path
from unittest.mock import Mock, MagicMock, patch

import pytest

from gitlab_watcher.config import Config, ProjectConfig
from gitlab_watcher.discord import DiscordWebhook
from gitlab_watcher.gitlab_client import GitLabClient, Issue, MergeRequest, Note
from gitlab_watcher.processor import Processor
from gitlab_watcher.state import StateManager, ProjectState
from gitlab_watcher.watcher import Watcher


@pytest.fixture
def temp_work_dir(tmp_path: Path) -> Path:
    """Create a temporary work directory."""
    return tmp_path / "work"


@pytest.fixture
def config_file(tmp_path: Path) -> Path:
    """Create a test config file."""
    project_dir = tmp_path / "test-project"
    project_dir.mkdir()
    (project_dir / "CLAUDE.md").write_text("Project ID: 42\n")

    config_content = f'''
GITLAB_URL="https://git.example.com"
GITLAB_TOKEN="test-token"
DISCORD_WEBHOOK=""
LABEL_IN_PROGRESS="In progress"
LABEL_REVIEW="Review"
GITLAB_USERNAME="claude"
POLL_INTERVAL=30

PROJECT_DIRS=(
  "{project_dir}"
)
'''
    config_path = tmp_path / "gitlab_watcher.conf"
    config_path.write_text(config_content)
    return config_path


@pytest.fixture
def mock_gitlab() -> MagicMock:
    """Create a mock GitLab client."""
    return MagicMock(spec=GitLabClient)


@pytest.fixture
def mock_discord() -> MagicMock:
    """Create a mock Discord webhook."""
    return MagicMock(spec=DiscordWebhook)


@pytest.fixture
def mock_processor() -> MagicMock:
    """Create a mock processor."""
    return MagicMock(spec=Processor)


@pytest.fixture
def state_manager(tmp_path: Path) -> StateManager:
    """Create a state manager with temp directory."""
    work_dir = tmp_path / "work"
    work_dir.mkdir(parents=True, exist_ok=True)
    return StateManager(work_dir)


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


@pytest.fixture
def sample_note() -> Note:
    """Create a sample note for testing."""
    return Note(
        id=123,
        body="Please fix this",
        author_username="reviewer",
    )


class TestStateManager:
    """Tests for state management."""

    def test_load_creates_new_state(self, temp_work_dir: Path) -> None:
        """Test that loading creates a new state if not exists."""
        state_manager = StateManager(temp_work_dir)
        state = state_manager.load(42)

        assert state.last_mr_iid is None
        assert state.last_note_id == 0
        assert state.processing is False

    def test_save_and_load(self, temp_work_dir: Path) -> None:
        """Test saving and loading state."""
        state_manager = StateManager(temp_work_dir)

        state = state_manager.load(42)
        state.last_mr_iid = 1
        state.last_branch = "feature-branch"
        state_manager.save(42)

        new_manager = StateManager(temp_work_dir)
        loaded = new_manager.load(42)

        assert loaded.last_mr_iid == 1
        assert loaded.last_branch == "feature-branch"

    def test_is_processing(self, temp_work_dir: Path) -> None:
        """Test processing flag."""
        state_manager = StateManager(temp_work_dir)

        assert state_manager.is_processing(42) is False

        state_manager.set_processing(42, True)
        assert state_manager.is_processing(42) is True

        state_manager.load(42)
        assert state_manager.is_processing(42) is True

        state_manager.init_state(42)
        assert state_manager.is_processing(42) is False

    def test_update_mr_state(self, temp_work_dir: Path) -> None:
        """Test updating MR state."""
        state_manager = StateManager(temp_work_dir)

        state_manager.update_mr_state(
            project_id=42,
            mr_iid=1,
            mr_state="opened",
            note_id=123,
            branch="feature-branch",
        )

        state = state_manager.load(42)
        assert state.last_mr_iid == 1
        assert state.last_mr_state == "opened"
        assert state.last_note_id == 123
        assert state.last_branch == "feature-branch"

    def test_reset(self, temp_work_dir: Path) -> None:
        """Test resetting state."""
        state_manager = StateManager(temp_work_dir)

        state_manager.update_mr_state(42, 1, "opened", 123, "feature")
        state_manager.reset(42)

        state = state_manager.load(42)
        assert state.last_mr_iid is None
        assert state.last_note_id == 0
        assert state.last_branch is None

    def test_get_set_value(self, temp_work_dir: Path) -> None:
        """Test get and set value methods."""
        state_manager = StateManager(temp_work_dir)

        assert state_manager.get(42, "processing") is False

        state_manager.set(42, "last_mr_iid", 5)
        assert state_manager.get(42, "last_mr_iid") == 5


class TestGitOps:
    """Tests for Git operations."""

    def test_generate_slug_simple(self) -> None:
        """Test slug generation with simple title."""
        from gitlab_watcher.git_ops import GitOps

        slug = GitOps.generate_slug("Add new feature")
        assert slug == "add-new-feature"

    def test_generate_slug_special_chars(self) -> None:
        """Test slug generation with special characters."""
        from gitlab_watcher.git_ops import GitOps

        slug = GitOps.generate_slug("Fix bug #123!!!")
        assert slug == "fix-bug-123"

    def test_generate_slug_truncation(self) -> None:
        """Test slug truncation."""
        from gitlab_watcher.git_ops import GitOps

        long_title = "This is a very long title that should be truncated"
        slug = GitOps.generate_slug(long_title, max_length=20)
        assert len(slug) == 20


class TestWatcherInit:
    """Tests for Watcher initialization."""

    def test_watcher_init_with_injected_dependencies(
        self,
        config_file: Path,
        mock_gitlab: MagicMock,
        mock_discord: MagicMock,
        mock_processor: MagicMock,
        state_manager: StateManager,
    ) -> None:
        """Test watcher initialization with injected dependencies."""
        watcher = Watcher(
            config_path=str(config_file),
            gitlab=mock_gitlab,
            discord=mock_discord,
            processor=mock_processor,
            state=state_manager,
        )

        assert watcher.gitlab is mock_gitlab
        assert watcher.discord is mock_discord
        assert watcher.processor is mock_processor
        assert watcher.state is state_manager
        assert len(watcher.config.projects) == 1
        assert watcher.config.projects[0].project_id == 42

    def test_watcher_missing_config(self) -> None:
        """Test watcher with missing config file."""
        with pytest.raises(FileNotFoundError):
            Watcher(config_path="/nonexistent/config.conf")


class TestWatcherCheckIssues:
    """Tests for check_issues method."""

    def test_check_issues_no_issues(
        self,
        config_file: Path,
        mock_gitlab: MagicMock,
        mock_discord: MagicMock,
        mock_processor: MagicMock,
        state_manager: StateManager,
    ) -> None:
        """Test check_issues when there are no issues."""
        mock_gitlab.get_issues.return_value = []

        watcher = Watcher(
            config_path=str(config_file),
            gitlab=mock_gitlab,
            discord=mock_discord,
            processor=mock_processor,
            state=state_manager,
        )
        project = watcher.config.projects[0]

        watcher.check_issues(project)

        mock_gitlab.get_issues.assert_called_once_with(
            project_id=42,
            state="opened",
            assignee_username="claude",
        )

    def test_check_issues_already_processing(
        self,
        config_file: Path,
        mock_gitlab: MagicMock,
        mock_discord: MagicMock,
        mock_processor: MagicMock,
        state_manager: StateManager,
    ) -> None:
        """Test check_issues when already processing."""
        watcher = Watcher(
            config_path=str(config_file),
            gitlab=mock_gitlab,
            discord=mock_discord,
            processor=mock_processor,
            state=state_manager,
        )
        project = watcher.config.projects[0]

        # Set processing flag
        state_manager.set_processing(project.project_id, True)

        watcher.check_issues(project)

        # Should not call get_issues when processing
        mock_gitlab.get_issues.assert_not_called()

    def test_check_issues_with_backlog_issue(
        self,
        config_file: Path,
        mock_gitlab: MagicMock,
        mock_discord: MagicMock,
        mock_processor: MagicMock,
        state_manager: StateManager,
        sample_issue: Issue,
    ) -> None:
        """Test check_issues with a backlog issue (no workflow labels)."""
        mock_gitlab.get_issues.return_value = [sample_issue]
        mock_processor.process_issue.return_value = True

        watcher = Watcher(
            config_path=str(config_file),
            gitlab=mock_gitlab,
            discord=mock_discord,
            processor=mock_processor,
            state=state_manager,
        )
        project = watcher.config.projects[0]

        watcher.check_issues(project)

        mock_processor.process_issue.assert_called_once_with(project, sample_issue)

    def test_check_issues_skips_in_progress(
        self,
        config_file: Path,
        mock_gitlab: MagicMock,
        mock_discord: MagicMock,
        mock_processor: MagicMock,
        state_manager: StateManager,
    ) -> None:
        """Test check_issues skips issues with In progress label."""
        issue_with_label = Issue(
            iid=1,
            title="Fix the bug",
            description="Description",
            web_url="https://git.example.com/issues/1",
            labels=["bug", "In progress"],
        )
        mock_gitlab.get_issues.return_value = [issue_with_label]

        watcher = Watcher(
            config_path=str(config_file),
            gitlab=mock_gitlab,
            discord=mock_discord,
            processor=mock_processor,
            state=state_manager,
        )
        project = watcher.config.projects[0]

        watcher.check_issues(project)

        # Should not process issue with In progress label
        mock_processor.process_issue.assert_not_called()

    def test_check_issues_skips_review(
        self,
        config_file: Path,
        mock_gitlab: MagicMock,
        mock_discord: MagicMock,
        mock_processor: MagicMock,
        state_manager: StateManager,
    ) -> None:
        """Test check_issues skips issues with Review label."""
        issue_with_label = Issue(
            iid=1,
            title="Fix the bug",
            description="Description",
            web_url="https://git.example.com/issues/1",
            labels=["bug", "Review"],
        )
        mock_gitlab.get_issues.return_value = [issue_with_label]

        watcher = Watcher(
            config_path=str(config_file),
            gitlab=mock_gitlab,
            discord=mock_discord,
            processor=mock_processor,
            state=state_manager,
        )
        project = watcher.config.projects[0]

        watcher.check_issues(project)

        # Should not process issue with Review label
        mock_processor.process_issue.assert_not_called()

    def test_check_issues_empty_issues_list(
        self,
        config_file: Path,
        mock_gitlab: MagicMock,
        mock_discord: MagicMock,
        mock_processor: MagicMock,
        state_manager: StateManager,
    ) -> None:
        """Test check_issues returns early when issues list is empty."""
        mock_gitlab.get_issues.return_value = []

        watcher = Watcher(
            config_path=str(config_file),
            gitlab=mock_gitlab,
            discord=mock_discord,
            processor=mock_processor,
            state=state_manager,
        )
        project = watcher.config.projects[0]

        watcher.check_issues(project)

        # Should call get_issues but not process_issue
        mock_gitlab.get_issues.assert_called_once()
        mock_processor.process_issue.assert_not_called()


class TestWatcherCheckMRStatus:
    """Tests for check_mr_status method."""

    def test_check_mr_status_no_mrs(
        self,
        config_file: Path,
        mock_gitlab: MagicMock,
        mock_discord: MagicMock,
        mock_processor: MagicMock,
        state_manager: StateManager,
    ) -> None:
        """Test check_mr_status when there are no MRs."""
        mock_gitlab.get_merge_requests.return_value = []

        watcher = Watcher(
            config_path=str(config_file),
            gitlab=mock_gitlab,
            discord=mock_discord,
            processor=mock_processor,
            state=state_manager,
        )
        project = watcher.config.projects[0]

        watcher.check_mr_status(project)

        mock_gitlab.get_merge_requests.assert_called_once_with(
            project_id=42,
            state="opened",
            author_username="claude",
        )

    def test_check_mr_status_already_processing(
        self,
        config_file: Path,
        mock_gitlab: MagicMock,
        mock_discord: MagicMock,
        mock_processor: MagicMock,
        state_manager: StateManager,
    ) -> None:
        """Test check_mr_status when already processing."""
        watcher = Watcher(
            config_path=str(config_file),
            gitlab=mock_gitlab,
            discord=mock_discord,
            processor=mock_processor,
            state=state_manager,
        )
        project = watcher.config.projects[0]

        # Set processing flag
        state_manager.set_processing(project.project_id, True)

        watcher.check_mr_status(project)

        # Should not call get_merge_requests when processing
        mock_gitlab.get_merge_requests.assert_not_called()

    def test_check_mr_status_merged(
        self,
        config_file: Path,
        mock_gitlab: MagicMock,
        mock_discord: MagicMock,
        mock_processor: MagicMock,
        state_manager: StateManager,
    ) -> None:
        """Test check_mr_status when MR is merged."""
        merged_mr = MergeRequest(
            iid=1,
            title="Fix the bug",
            web_url="https://git.example.com/merge_requests/1",
            source_branch="1-fix-the-bug",
            state="merged",
        )
        mock_gitlab.get_merge_request.return_value = merged_mr

        watcher = Watcher(
            config_path=str(config_file),
            gitlab=mock_gitlab,
            discord=mock_discord,
            processor=mock_processor,
            state=state_manager,
        )
        project = watcher.config.projects[0]

        # Set up state as if we were tracking an MR
        state_manager.update_mr_state(
            project.project_id,
            mr_iid=1,
            mr_state="opened",
            note_id=0,
            branch="1-fix-the-bug",
        )

        watcher.check_mr_status(project)

        mock_processor.cleanup_after_merge.assert_called_once()

    def test_check_mr_status_new_comment(
        self,
        config_file: Path,
        mock_gitlab: MagicMock,
        mock_discord: MagicMock,
        mock_processor: MagicMock,
        state_manager: StateManager,
        sample_mr: MergeRequest,
        sample_note: Note,
    ) -> None:
        """Test check_mr_status with new comment from reviewer."""
        mock_gitlab.get_merge_requests.return_value = [sample_mr]
        mock_gitlab.get_notes.return_value = [sample_note]
        mock_gitlab.get_merge_request.return_value = None
        mock_processor.process_comment.return_value = True

        watcher = Watcher(
            config_path=str(config_file),
            gitlab=mock_gitlab,
            discord=mock_discord,
            processor=mock_processor,
            state=state_manager,
        )
        project = watcher.config.projects[0]

        watcher.check_mr_status(project)

        # Should process the comment
        mock_processor.process_comment.assert_called_once()

    def test_check_mr_status_ignores_own_comments(
        self,
        config_file: Path,
        mock_gitlab: MagicMock,
        mock_discord: MagicMock,
        mock_processor: MagicMock,
        state_manager: StateManager,
        sample_mr: MergeRequest,
    ) -> None:
        """Test check_mr_status ignores comments from the bot user."""
        # Comment from the bot user itself
        own_note = Note(
            id=123,
            body="I made changes",
            author_username="claude",  # Same as gitlab_username
        )
        mock_gitlab.get_merge_requests.return_value = [sample_mr]
        mock_gitlab.get_notes.return_value = [own_note]
        mock_gitlab.get_merge_request.return_value = None

        watcher = Watcher(
            config_path=str(config_file),
            gitlab=mock_gitlab,
            discord=mock_discord,
            processor=mock_processor,
            state=state_manager,
        )
        project = watcher.config.projects[0]

        watcher.check_mr_status(project)

        # Should NOT process own comments
        mock_processor.process_comment.assert_not_called()

    def test_check_mr_status_same_comment_not_processed(
        self,
        config_file: Path,
        mock_gitlab: MagicMock,
        mock_discord: MagicMock,
        mock_processor: MagicMock,
        state_manager: StateManager,
        sample_mr: MergeRequest,
        sample_note: Note,
    ) -> None:
        """Test check_mr_status doesn't reprocess same comment."""
        mock_gitlab.get_merge_requests.return_value = [sample_mr]
        mock_gitlab.get_notes.return_value = [sample_note]
        mock_gitlab.get_merge_request.return_value = None

        watcher = Watcher(
            config_path=str(config_file),
            gitlab=mock_gitlab,
            discord=mock_discord,
            processor=mock_processor,
            state=state_manager,
        )
        project = watcher.config.projects[0]

        # Set state as if this comment was already seen
        state_manager.update_mr_state(
            project.project_id,
            mr_iid=sample_mr.iid,
            mr_state="opened",
            note_id=sample_note.id,  # Same note ID
            branch=sample_mr.source_branch,
        )

        watcher.check_mr_status(project)

        # Should not process the same comment again
        mock_processor.process_comment.assert_not_called()


class TestWatcherExtractFromRemote:
    """Tests for _extract_from_remote method."""

    @patch("gitlab_watcher.watcher.GitOps")
    def test_extract_from_remote_with_token(
        self,
        mock_git_ops_class: Mock,
        config_file: Path,
        mock_gitlab: MagicMock,
        mock_discord: MagicMock,
        mock_processor: MagicMock,
        state_manager: StateManager,
    ) -> None:
        """Test extracting URL and token from remote URL with user:token format."""
        mock_git = Mock()
        mock_git.get_remote_url.return_value = (
            "https://user:secret-token@git.example.com/group/project.git"
        )
        mock_git_ops_class.return_value = mock_git

        watcher = Watcher(
            config_path=str(config_file),
            gitlab=mock_gitlab,
            discord=mock_discord,
            processor=mock_processor,
            state=state_manager,
        )
        project = watcher.config.projects[0]

        url, token = watcher._extract_from_remote(project.path)

        assert url == "https://git.example.com"
        assert token == "secret-token"

    @patch("gitlab_watcher.watcher.GitOps")
    def test_extract_from_remote_token_only(
        self,
        mock_git_ops_class: Mock,
        config_file: Path,
        mock_gitlab: MagicMock,
        mock_discord: MagicMock,
        mock_processor: MagicMock,
        state_manager: StateManager,
    ) -> None:
        """Test extracting URL and token when only token is in URL."""
        mock_git = Mock()
        mock_git.get_remote_url.return_value = (
            "https://secret-token@git.example.com/group/project.git"
        )
        mock_git_ops_class.return_value = mock_git

        watcher = Watcher(
            config_path=str(config_file),
            gitlab=mock_gitlab,
            discord=mock_discord,
            processor=mock_processor,
            state=state_manager,
        )
        project = watcher.config.projects[0]

        url, token = watcher._extract_from_remote(project.path)

        assert url == "https://git.example.com"
        assert token == "secret-token"

    @patch("gitlab_watcher.watcher.GitOps")
    def test_extract_from_remote_no_token(
        self,
        mock_git_ops_class: Mock,
        config_file: Path,
        mock_gitlab: MagicMock,
        mock_discord: MagicMock,
        mock_processor: MagicMock,
        state_manager: StateManager,
    ) -> None:
        """Test extracting URL when no token in URL."""
        mock_git = Mock()
        mock_git.get_remote_url.return_value = (
            "https://git.example.com/group/project.git"
        )
        mock_git_ops_class.return_value = mock_git

        watcher = Watcher(
            config_path=str(config_file),
            gitlab=mock_gitlab,
            discord=mock_discord,
            processor=mock_processor,
            state=state_manager,
        )
        project = watcher.config.projects[0]

        url, token = watcher._extract_from_remote(project.path)

        assert url == "https://git.example.com"
        assert token is None

    @patch("gitlab_watcher.watcher.GitOps")
    def test_extract_from_remote_no_remote(
        self,
        mock_git_ops_class: Mock,
        config_file: Path,
        mock_gitlab: MagicMock,
        mock_discord: MagicMock,
        mock_processor: MagicMock,
        state_manager: StateManager,
    ) -> None:
        """Test when no remote URL is configured."""
        mock_git = Mock()
        mock_git.get_remote_url.return_value = None
        mock_git_ops_class.return_value = mock_git

        watcher = Watcher(
            config_path=str(config_file),
            gitlab=mock_gitlab,
            discord=mock_discord,
            processor=mock_processor,
            state=state_manager,
        )
        project = watcher.config.projects[0]

        url, token = watcher._extract_from_remote(project.path)

        assert url is None
        assert token is None

    @patch("gitlab_watcher.watcher.GitOps")
    def test_extract_from_remote_invalid_url(
        self,
        mock_git_ops_class: Mock,
        config_file: Path,
        mock_gitlab: MagicMock,
        mock_discord: MagicMock,
        mock_processor: MagicMock,
        state_manager: StateManager,
    ) -> None:
        """Test when remote URL doesn't match expected pattern."""
        mock_git = Mock()
        mock_git.get_remote_url.return_value = "git@github.com:user/repo.git"
        mock_git_ops_class.return_value = mock_git

        watcher = Watcher(
            config_path=str(config_file),
            gitlab=mock_gitlab,
            discord=mock_discord,
            processor=mock_processor,
            state=state_manager,
        )
        project = watcher.config.projects[0]

        url, token = watcher._extract_from_remote(project.path)

        # SSH URL pattern doesn't match HTTPS regex
        assert url is None
        assert token is None