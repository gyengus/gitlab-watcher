import pytest
from unittest.mock import MagicMock, patch

from gitlab_watcher.processor import Processor
from gitlab_watcher.config import ProjectConfig
from pathlib import Path


@pytest.fixture
def project_config():
    return ProjectConfig(
        project_id=1,
        path=Path("/tmp/fake_repo"),
        name="testproj",
        default_branch="master",
        discord_webhook_url="",
    )


@pytest.fixture
def processor(project_config: ProjectConfig):
    # Mock dependencies
    gitlab_mock = MagicMock()
    discord_mock = MagicMock()
    state_mock = MagicMock()
    state_mock.is_processing.return_value = True  # Simulate already processing
    processor = Processor(
        gitlab=gitlab_mock,
        discord=discord_mock,
        state=state_mock,
        gitlab_username="bot",
        label_in_progress="In progress",
        label_review="Review",
        ai_tool_mode="opencode",  # mode not relevant for this test
    )
    return processor


def test_process_issue_duplicate_start_skips_notification_and_git_ops(processor: Processor, project_config: ProjectConfig):
    # Minimal issue object with required attributes
    issue = MagicMock()
    issue.iid = 42
    issue.title = "Sample Issue"
    issue.description = "description"
    issue.labels = []
    issue.web_url = "http://example.com/issue/42"

    # Run process_issue – should early‑return False due to processing flag
    result = processor.process_issue(project_config, issue)

    assert result is False
    # No Discord start notification should be sent
    processor.discord.notify_issue_started.assert_not_called()
    # No git operations should be performed (fetch, checkout, etc.)
    processor.gitlab.update_issue_labels.assert_not_called()
    # State should have been queried for processing flag
    processor.state.is_processing.assert_called_once_with(project_config.project_id)
