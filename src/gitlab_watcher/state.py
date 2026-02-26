"""State management for tracking processed items."""

import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional


@dataclass
class ProjectState:
    """State for a single project."""

    last_mr_iid: Optional[int] = None
    last_mr_state: Optional[str] = None
    last_note_id: int = 0
    last_branch: Optional[str] = None
    processing: bool = False


class StateManager:
    """Manages state for multiple projects."""

    def __init__(self, work_dir: Path) -> None:
        """Initialize state manager.

        Args:
            work_dir: Directory to store state files
        """
        self.work_dir = work_dir
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self._states: dict[int, ProjectState] = {}

    def _state_file(self, project_id: int) -> Path:
        """Get the state file path for a project."""
        return self.work_dir / f"state_{project_id}.json"

    def _load_from_file(self, project_id: int, reset_processing: bool = False) -> ProjectState:
        """Load state from file without caching.

        Args:
            project_id: The project ID
            reset_processing: If True, reset processing flag to False

        Returns:
            ProjectState instance
        """
        state_file = self._state_file(project_id)

        if state_file.exists():
            try:
                data = json.loads(state_file.read_text())
                state = ProjectState(**data)
            except (json.JSONDecodeError, TypeError):
                state = ProjectState()
        else:
            state = ProjectState()

        if reset_processing:
            state.processing = False

        return state

    def load(self, project_id: int) -> ProjectState:
        """Load state for a project, returning cached state if available.

        This does NOT reset the processing flag. Use init_state() for
        startup initialization.
        """
        if project_id in self._states:
            return self._states[project_id]

        state = self._load_from_file(project_id)
        self._states[project_id] = state
        return state

    def init_state(self, project_id: int) -> ProjectState:
        """Initialize state for a project, resetting processing flag.

        This matches the Bash script's init_state behavior which resets
        processing on watcher startup. Call this once at startup for each
        project.
        """
        state = self._load_from_file(project_id, reset_processing=True)
        self._states[project_id] = state
        self.save(project_id)
        return state

    def save(self, project_id: int) -> None:
        """Save state for a project."""
        if project_id not in self._states:
            return

        state_file = self._state_file(project_id)
        state_file.write_text(json.dumps(asdict(self._states[project_id]), indent=2))

    def get(self, project_id: int, key: str) -> Optional[str | int | bool]:
        """Get a state value."""
        state = self.load(project_id)
        return getattr(state, key, None)

    def set(
        self,
        project_id: int,
        key: str,
        value: Optional[str | int | bool],
    ) -> None:
        """Set a state value."""
        state = self.load(project_id)
        if hasattr(state, key):
            setattr(state, key, value)
            self.save(project_id)

    def is_processing(self, project_id: int) -> bool:
        """Check if a project is currently processing."""
        state = self.load(project_id)
        return state.processing

    def set_processing(self, project_id: int, processing: bool) -> None:
        """Set the processing flag."""
        self.set(project_id, "processing", processing)

    def update_mr_state(
        self,
        project_id: int,
        mr_iid: Optional[int],
        mr_state: Optional[str],
        note_id: int,
        branch: Optional[str],
    ) -> None:
        """Update MR tracking state."""
        state = self.load(project_id)
        state.last_mr_iid = mr_iid
        state.last_mr_state = mr_state
        state.last_note_id = note_id
        state.last_branch = branch
        self.save(project_id)

    def reset(self, project_id: int) -> None:
        """Reset state for a project."""
        self._states[project_id] = ProjectState()
        self.save(project_id)