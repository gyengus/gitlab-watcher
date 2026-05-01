import pytest
from unittest.mock import MagicMock, patch

from gitlab_watcher.processor import Processor
from gitlab_watcher.config import ProjectConfig
from pathlib import Path


@pytest.fixture
def project_config(tmp_path):
    repo_dir = tmp_path / "fake_repo"
    repo_dir.mkdir()
    return ProjectConfig(
        project_id=1,
        path=repo_dir,
        name="testproj",
        default_branch="master",
        discord_webhook_url="",
    )


@pytest.fixture
def processor(project_config: ProjectConfig):
    gitlab_mock = MagicMock()
    discord_mock = MagicMock()
    state_mock = MagicMock()
    mock_git = MagicMock()
    mock_git.fetch.return_value = True
    mock_git.checkout.return_value = (True, "")
    mock_git.pull.return_value = True
    mock_git.has_unpushed_work.return_value = False
    processor = Processor(
        gitlab=gitlab_mock,
        discord=discord_mock,
        state=state_mock,
        gitlab_username="bot",
        label_in_progress="In progress",
        label_review="Review",
        ai_tool_mode="ollama",
        default_branch="master",
        git_factory=lambda path: mock_git,
    )
    return processor


@patch.object(Processor, "_run_ai_tool")
def test_process_issue_calls_notify_issue_started(
    mock_run_ai: MagicMock,
    processor: Processor,
    project_config: ProjectConfig,
):
    """Test that process_issue sends Discord notification when starting."""
    mock_run_ai.return_value = (False, "Error output")

    issue = MagicMock()
    issue.iid = 42
    issue.title = "Sample Issue"
    issue.description = "description"
    issue.labels = []
    issue.web_url = "http://example.com/issue/42"

    result = processor.process_issue(project_config, issue)

    assert result is False
    # Discord notification should have been sent
    processor.discord.notify_issue_started.assert_called()