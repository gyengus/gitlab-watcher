import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path
from gitlab_watcher.watcher import Watcher
from gitlab_watcher.state import StateManager, ProjectState
from gitlab_watcher.gitlab_client import GitLabClient, MergeRequest, Note, Issue
from gitlab_watcher.config import ProjectConfig, Config, ProjectConfig as PC

@pytest.fixture(autouse=True)
def mock_load_config():
    with patch("gitlab_watcher.watcher.load_config") as mock:
        mock.return_value = Config(
            gitlab_url="https://git.example.com",
            gitlab_token="token",
            projects=[ProjectConfig(name="test-project", project_id=1, path=Path("/tmp/test"))]
        )
        yield mock

@pytest.fixture
def mock_gitlab():
    mock = MagicMock(spec=GitLabClient)
    mock.get_current_user.return_value = {"username": "bot"}
    return mock

@pytest.fixture
def mock_discord():
    return MagicMock()

@pytest.fixture
def mock_processor():
    return MagicMock()

@pytest.fixture
def state_manager(tmp_path):
    return StateManager(tmp_path)

@pytest.fixture
def project_config():
    return ProjectConfig(name="test-project", project_id=1, path=Path("/tmp/test"))

def test_check_mr_status_multi_cleanup(state_manager, mock_gitlab, mock_processor, mock_discord, project_config):
    # Setup state with two tracked MRs
    state_manager.add_tracked_mr(1, 12, "branch-12")
    state_manager.add_tracked_mr(1, 13, "branch-13")
    
    # Mock GitLab: MR 13 is merged, MR 12 is still opened
    def get_mr_mock(project_id, iid):
        if iid == 13:
            return MergeRequest(iid=13, title="MR 13", web_url="url-13", source_branch="branch-13", state="merged")
        if iid == 12:
            return MergeRequest(iid=12, title="MR 12", web_url="url-12", source_branch="branch-12", state="opened")
        return None
    
    mock_gitlab.get_merge_request.side_effect = get_mr_mock
    mock_gitlab.get_merge_requests.return_value = [] # No "opened" MRs returned (just for this part of test)

    watcher = Watcher(
        disable_lock=True,
        gitlab=mock_gitlab,
        discord=mock_discord,
        processor=mock_processor,
        state=state_manager,
    )
    
    # Run status check
    watcher.check_mr_status(project_config)
    
    # Verify cleanup called for MR 13
    mock_processor.cleanup_after_merge.assert_called_once()
    args, kwargs = mock_processor.cleanup_after_merge.call_args
    assert kwargs['mr_iid'] == 13
    assert kwargs['branch'] == "branch-13"
    
    # Verify state: MR 13 removed, MR 12 remains
    state = state_manager.load(1)
    assert "13" not in state.tracked_mrs
    assert "12" in state.tracked_mrs

def test_check_issues_sequential_skip(state_manager, mock_gitlab, mock_processor, mock_discord, project_config):
    # Setup state with one tracked MR
    state_manager.add_tracked_mr(1, 12, "branch-12")
    
    watcher = Watcher(
        disable_lock=True,
        gitlab=mock_gitlab,
        discord=mock_discord,
        processor=mock_processor,
        state=state_manager,
    )
    
    # Run issues check
    watcher.check_issues(project_config)
    
    # Should NOT call get_issues because an MR is tracked
    mock_gitlab.get_issues.assert_not_called()
    mock_processor.process_issue.assert_not_called()

def test_check_issues_proceeds_when_no_tracked_mrs(state_manager, mock_gitlab, mock_processor, mock_discord, project_config):
    # Setup state with NO tracked MRs
    state = state_manager.load(1)
    state.tracked_mrs = {}
    state_manager.force_save(1)
    
    mock_gitlab.get_issues.return_value = [
        Issue(iid=1, title="Issue 1", description="desc", web_url="url-1", labels=[])
    ]
    mock_gitlab.get_current_user.return_value = {"username": "bot"}

    watcher = Watcher(
        disable_lock=True,
        gitlab=mock_gitlab,
        discord=mock_discord,
        processor=mock_processor,
        state=state_manager,
    )
    
    # Run issues check
    watcher.check_issues(project_config)
    
    # Should call get_issues and process_issue
    mock_gitlab.get_issues.assert_called_once()
    mock_processor.process_issue.assert_called_once()

def test_migration_from_legacy_state(tmp_path):
    # Create legacy state file
    state_file = tmp_path / "state_1.json"
    state_file.write_text('{"last_mr_iid": 12, "last_branch": "leg-branch", "last_note_id": 100, "processing": false}')
    
    state_manager = StateManager(tmp_path)
    state = state_manager.load(1)
    
    # Verify migration
    assert "12" in state.tracked_mrs
    assert state.tracked_mrs["12"]["branch"] == "leg-branch"
    assert state.last_mr_iid == 12

def test_check_mr_status_filters_by_author(state_manager, mock_gitlab, mock_processor, mock_discord, project_config):
    # Mock GitLab username
    mock_gitlab.get_current_user.return_value = {"username": "bot-user"}
    
    watcher = Watcher(
        disable_lock=True,
        gitlab=mock_gitlab,
        discord=mock_discord,
        processor=mock_processor,
        state=state_manager,
    )
    
    # Run status check
    watcher.check_mr_status(project_config)
    
    # Verify get_merge_requests was called with author_username="bot-user"
    mock_gitlab.get_merge_requests.assert_called_with(
        project_id=1,
        state="opened",
        author_username="bot-user"
    )
