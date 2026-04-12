import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path
from gitlab_watcher.watcher import Watcher
from gitlab_watcher.gitlab_client import Note

def test_check_mr_status_sequential_processing(
    config_file: Path,
    mock_gitlab: MagicMock,
    mock_discord: MagicMock,
    mock_processor: MagicMock,
    state_manager: MagicMock,
) -> None:
    """Test that multiple comments are processed one by one in chronological order."""
    # Setup multiple comments (ID 100, 101, 102)
    notes = [
        Note(id=100, body="First kérés", author_username="user", system=False),
        Note(id=101, body="Second kérés", author_username="user", system=False),
        Note(id=102, body="Third kérés", author_username="user", system=False),
    ]
    # get_notes will be called with sort="asc", so we return them in that order
    mock_gitlab.get_notes.return_value = notes
    mock_gitlab.get_merge_requests.return_value = [MagicMock(iid=1, source_branch="feat")]
    
    # State says we last saw ID 99
    state_manager.load.return_value.last_note_id = 99
    state_manager.is_processing.return_value = False

    watcher = Watcher(
        config_path=str(config_file),
        gitlab=mock_gitlab,
        discord=mock_discord,
        processor=mock_processor,
        state=state_manager,
    )
    project = watcher.config.projects[0]

    # FIRST CALL: Should pick up ID 100 and STOP
    watcher.check_mr_status(project)
    
    # Verify ONLY the first comment was processed
    mock_processor.process_comment.assert_called_once_with(project, mock_gitlab.get_merge_requests.return_value[0], "First kérés")
    # Verify state was updated to ID 100
    state_manager.update_mr_state.assert_called_with(project.project_id, 1, MagicMock().state, 100, "feat")
    
    # Reset mocks for second call
    mock_processor.process_comment.reset_mock()
    state_manager.update_mr_state.reset_mock()
    
    # SECOND CALL: State now has ID 100 as last_note_id
    state_manager.load.return_value.last_note_id = 100
    watcher.check_mr_status(project)
    
    # Verify ID 101 was processed
    mock_processor.process_comment.assert_called_once_with(project, mock_gitlab.get_merge_requests.return_value[0], "Second kérés")
    state_manager.update_mr_state.assert_called_with(project.project_id, 1, MagicMock().state, 101, "feat")

def test_check_mr_status_skips_system_and_self(
    config_file: Path,
    mock_gitlab: MagicMock,
    mock_discord: MagicMock,
    mock_processor: MagicMock,
    state_manager: MagicMock,
) -> None:
    """Test that system notes and own notes are skipped but their ID is acknowledged."""
    notes = [
        Note(id=100, body="System approved", author_username="system", system=True),
        Note(id=101, body="Claude did something", author_username="claude-bot", system=False), # same as config.gitlab_username
        Note(id=102, body="Human request", author_username="user", system=False),
    ]
    mock_gitlab.get_notes.return_value = notes
    mock_gitlab.get_merge_requests.return_value = [MagicMock(iid=1, source_branch="feat")]
    
    state_manager.load.return_value.last_note_id = 99
    state_manager.is_processing.return_value = False

    watcher = Watcher(
        config_path=str(config_file),
        gitlab=mock_gitlab,
        discord=mock_discord,
        processor=mock_processor,
        state=state_manager,
    )
    # Set username to match
    watcher.config.gitlab_username = "claude-bot"
    project = watcher.config.projects[0]

    # Should skip 100, skip 101, and process 102 in ONE go if they are skipable, 
    # but the logic says it updates state and CONTINUES the loop for skipables, 
    # and only returns for valid human comments.
    
    watcher.check_mr_status(project)
    
    # Should process 102
    mock_processor.process_comment.assert_called_once_with(project, mock_gitlab.get_merge_requests.return_value[0], "Human request")
    
    # Should have updated state for 100, 101 AND 102
    assert state_manager.update_mr_state.call_count == 3
