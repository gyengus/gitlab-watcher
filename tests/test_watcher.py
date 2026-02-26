"""Tests for watcher main functionality."""

from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from gitlab_watcher.state import ProjectState, StateManager


@pytest.fixture
def temp_work_dir(tmp_path: Path) -> Path:
    """Create a temporary work directory."""
    return tmp_path / "work"


class TestStateManager:
    """Tests for state management."""

    def test_load_creates_new_state(
        self,
        temp_work_dir: Path,
    ) -> None:
        """Test that loading creates a new state if not exists."""
        state_manager = StateManager(temp_work_dir)

        state = state_manager.load(42)

        assert state.last_mr_iid is None
        assert state.last_note_id == 0
        assert state.processing is False

    def test_save_and_load(
        self,
        temp_work_dir: Path,
    ) -> None:
        """Test saving and loading state."""
        state_manager = StateManager(temp_work_dir)

        # Load and modify
        state = state_manager.load(42)
        state.last_mr_iid = 1
        state.last_branch = "feature-branch"
        state_manager.save(42)

        # Create new state manager and load
        new_manager = StateManager(temp_work_dir)
        loaded = new_manager.load(42)

        assert loaded.last_mr_iid == 1
        assert loaded.last_branch == "feature-branch"

    def test_is_processing(
        self,
        temp_work_dir: Path,
    ) -> None:
        """Test processing flag."""
        state_manager = StateManager(temp_work_dir)

        assert state_manager.is_processing(42) is False

        state_manager.set_processing(42, True)
        assert state_manager.is_processing(42) is True

        # Regular load does NOT reset processing flag
        state_manager.load(42)
        assert state_manager.is_processing(42) is True

        # init_state resets processing flag (like on startup)
        state_manager.init_state(42)
        assert state_manager.is_processing(42) is False

    def test_update_mr_state(
        self,
        temp_work_dir: Path,
    ) -> None:
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

    def test_reset(
        self,
        temp_work_dir: Path,
    ) -> None:
        """Test resetting state."""
        state_manager = StateManager(temp_work_dir)

        # Set some state
        state_manager.update_mr_state(42, 1, "opened", 123, "feature")

        # Reset
        state_manager.reset(42)

        state = state_manager.load(42)
        assert state.last_mr_iid is None
        assert state.last_note_id == 0
        assert state.last_branch is None


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