"""Tests for state management with MR failure tracking."""

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from gitlab_watcher.state import StateManager, ProjectState


@pytest.fixture
def temp_work_dir(tmp_path: Path) -> Path:
    """Create a temporary work directory."""
    return tmp_path / "work"


@pytest.fixture
def state_manager(temp_work_dir: Path) -> StateManager:
    """Create a state manager for testing."""
    work_dir = temp_work_dir
    work_dir.mkdir(parents=True, exist_ok=True)
    manager = StateManager(work_dir)
    yield manager
    manager.stop()


class TestStateManagerMrFailureTracking:
    """Tests for MR failure tracking in StateManager."""

    def test_mark_branch_failed_mr(self, state_manager: StateManager) -> None:
        """Test marking a branch as having failed MR creation."""
        project_id = 42
        
        # Initially should have no failed branches
        state = state_manager.load(project_id)
        assert len(state.branches_with_failed_mr) == 0
        
        # Mark branch as failed
        state_manager.mark_branch_failed_mr(project_id, "1-fix-the-bug")
        
        # Check that branch is marked as failed
        state = state_manager.load(project_id)
        assert "1-fix-the-bug" in state.branches_with_failed_mr

    def test_has_branch_failed_mr_true(self, state_manager: StateManager) -> None:
        """Test checking if a branch has failed MR creation (true case)."""
        project_id = 42
        
        # Mark branch as failed
        state_manager.mark_branch_failed_mr(project_id, "1-fix-the-bug")
        
        # Check that branch is marked as failed
        assert state_manager.has_branch_failed_mr(project_id, "1-fix-the-bug")

    def test_has_branch_failed_mr_false(self, state_manager: StateManager) -> None:
        """Test checking if a branch has failed MR creation (false case)."""
        project_id = 42
        
        # Check branch that hasn't failed
        assert not state_manager.has_branch_failed_mr(project_id, "1-fix-the-bug")

    def test_clear_failed_mr_flag(self, state_manager: StateManager) -> None:
        """Test clearing the failed MR creation flag."""
        project_id = 42
        
        # Mark branch as failed
        state_manager.mark_branch_failed_mr(project_id, "1-fix-the-bug")
        
        # Clear the flag
        state_manager.clear_failed_mr_flag(project_id, "1-fix-the-bug")
        
        # Check that flag is cleared
        state = state_manager.load(project_id)
        assert "1-fix-the-bug" not in state.branches_with_failed_mr

    def test_clear_failed_mr_flag_nonexistent(self, state_manager: StateManager) -> None:
        """Test clearing a failed MR flag that doesn't exist."""
        project_id = 42
        
        # Try to clear a flag that doesn't exist
        state_manager.clear_failed_mr_flag(project_id, "nonexistent-branch")
        
        # Should not raise an error
        state = state_manager.load(project_id)
        assert len(state.branches_with_failed_mr) == 0

    def test_multiple_failed_branches(self, state_manager: StateManager) -> None:
        """Test tracking multiple branches with failed MR creation."""
        project_id = 42
        
        # Mark multiple branches as failed
        state_manager.mark_branch_failed_mr(project_id, "1-fix-the-bug")
        state_manager.mark_branch_failed_mr(project_id, "2-add-feature")
        state_manager.mark_branch_failed_mr(project_id, "3-refactor")
        
        # Check all are marked
        state = state_manager.load(project_id)
        expected_branches = {"1-fix-the-bug", "2-add-feature", "3-refactor"}
        assert state.branches_with_failed_mr == expected_branches
        
        # Clear one branch
        state_manager.clear_failed_mr_flag(project_id, "2-add-feature")
        
        # Check that only the cleared branch is removed
        state = state_manager.load(project_id)
        assert state.branches_with_failed_mr == {"1-fix-the-bug", "3-refactor"}

    def test_failed_branches_persist_across_reloads(self, state_manager: StateManager) -> None:
        """Test that failed branches persist across state reloads."""
        project_id = 42
        
        # Mark branch as failed
        state_manager.mark_branch_failed_mr(project_id, "1-fix-the-bug")
        
        # Create a new state manager instance (simulating restart)
        new_state_manager = StateManager(state_manager.work_dir)
        
        # Check that branch is still marked as failed
        assert new_state_manager.has_branch_failed_mr(project_id, "1-fix-the-bug")
        
        # Cleanup
        new_state_manager.stop()

    def test_failed_branches_cleared_when_mr_succeeds(self, state_manager: StateManager) -> None:
        """Test that failed branches are cleared when MR is successfully created."""
        project_id = 42
        
        # Mark branch as failed
        state_manager.mark_branch_failed_mr(project_id, "1-fix-the-bug")
        
        # Simulate successful MR creation by calling add_tracked_mr
        state_manager.add_tracked_mr(project_id, 1, "1-fix-the-bug", created_by_watcher=True)
        
        # Check that failed flag is cleared
        assert not state_manager.has_branch_failed_mr(project_id, "1-fix-the-bug")

    def test_failed_branches_not_cleared_when_mr_not_created_by_watcher(self, state_manager: StateManager) -> None:
        """Test that failed branches are not cleared when MR is not created by watcher."""
        project_id = 42
        
        # Mark branch as failed
        state_manager.mark_branch_failed_mr(project_id, "1-fix-the-bug")
        
        # Simulate MR creation by someone else
        state_manager.add_tracked_mr(project_id, 1, "1-fix-the-bug", created_by_watcher=False)
        
        # Check that failed flag is NOT cleared
        assert state_manager.has_branch_failed_mr(project_id, "1-fix-the-bug")

    def test_state_serialization_includes_failed_branches(self, state_manager: StateManager) -> None:
        """Test that failed branches are included in state serialization."""
        project_id = 42
        
        # Mark branch as failed
        state_manager.mark_branch_failed_mr(project_id, "1-fix-the-bug")
        
        # Get state file content
        state_file = state_manager._state_file(project_id)
        assert state_file.exists()
        
        # Read and verify JSON content
        import json
        data = json.loads(state_file.read_text())
        assert "branches_with_failed_mr" in data
        assert "1-fix-the-bug" in data["branches_with_failed_mr"]

    def test_state_deserialization_migrates_data(self, state_manager: StateManager) -> None:
        """Test that old state data is migrated to include failed branches field."""
        project_id = 42
        
        # Create a legacy state file without branches_with_failed_mr
        state_file = state_manager._state_file(project_id)
        legacy_data = {
            "last_mr_iid": 1,
            "last_mr_state": "opened",
            "last_branch": "1-fix-the-bug",
            "processing": False,
            "tracked_mrs": {"1": {"branch": "1-fix-the-bug"}},
            "branches_with_failed_mr": []
        }
        state_file.write_text(json.dumps(legacy_data))
        
        # Load state - should migrate automatically
        state = state_manager.load(project_id)
        
        # Check that migration happened and fields are present
        assert state.last_mr_iid == 1
        assert state.last_mr_state == "opened"
        assert state.last_branch == "1-fix-the-bug"
        assert state.processing is False
        assert len(state.branches_with_failed_mr) == 0  # Should be empty in legacy data