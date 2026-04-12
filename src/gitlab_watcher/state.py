"""State management for tracking processed items."""

import json
import logging
import threading
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# Default delay for debounced saves
DEFAULT_SAVE_DELAY = 1.0


@dataclass
class ProjectState:
    """State for a single project."""

    last_mr_iid: Optional[int] = None
    last_mr_state: Optional[str] = None
    last_note_id: int = 0
    last_branch: Optional[str] = None
    processing: bool = False


class StateManager:
    """Manages state for multiple projects with debounced saving."""

    def __init__(self, work_dir: Path, save_delay: float = DEFAULT_SAVE_DELAY) -> None:
        """Initialize state manager.

        Args:
            work_dir: Directory to store state files
            save_delay: Delay in seconds for debounced saves
        """
        self.work_dir = work_dir
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self._states: dict[int, ProjectState] = {}
        self._dirty: set[int] = set()
        self._save_timer: Optional[threading.Timer] = None
        self._save_delay = save_delay
        self._lock = threading.Lock()
        self._stopped = False


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

    def _schedule_save(self, project_id: int) -> None:
        """Schedule a debounced save operation."""
        with self._lock:
            if self._stopped:
                return
            self._dirty.add(project_id)
            if self._save_timer is not None:
                self._save_timer.cancel()
            self._save_timer = threading.Timer(
                self._save_delay,
                self._flush_dirty,
            )
            self._save_timer.start()

    def stop(self) -> None:
        """Stop the state manager and cancel any pending timers."""
        with self._lock:
            self._stopped = True
            if self._save_timer is not None:
                self._save_timer.cancel()
                self._save_timer = None

    def __del__(self) -> None:
        """Ensure timer is cancelled on deletion."""
        try:
            self.stop()
        except Exception:
            pass

    def _flush_dirty(self) -> None:
        """Save all dirty states."""
        with self._lock:
            for project_id in self._dirty:
                self._save_sync(project_id)
            self._dirty.clear()
            self._save_timer = None

    def _save_sync(self, project_id: int) -> None:
        """Synchronous save to file."""
        if project_id not in self._states:
            return
        state_file = self._state_file(project_id)
        try:
            state_file.write_text(json.dumps(asdict(self._states[project_id]), indent=2))
        except Exception as e:
            logger.error(f"Failed to save state for project {project_id}: {e}")

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
        self._save_sync(project_id)
        return state

    def save(self, project_id: int) -> None:
        """Save state for a project (debounced)."""
        if project_id not in self._states:
            return
        self._schedule_save(project_id)

    def force_save(self, project_id: int) -> None:
        """Immediately save state for a project."""
        with self._lock:
            self._dirty.discard(project_id)
            self._save_sync(project_id)

    def force_save_all(self) -> None:
        """Immediately save all dirty states."""
        with self._lock:
            if self._save_timer is not None:
                self._save_timer.cancel()
            for project_id in self._dirty:
                self._save_sync(project_id)
            self._dirty.clear()
            self._save_timer = None

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
        """Set the processing flag and force an immediate save."""
        self.set(project_id, "processing", processing)
        self.force_save(project_id)

    def update_mr_state(
        self,
        project_id: int,
        mr_iid: Optional[int],
        mr_state: Optional[str],
        note_id: int,
        branch: Optional[str],
    ) -> None:
        """Update MR tracking state and force an immediate save."""
        state = self.load(project_id)
        state.last_mr_iid = mr_iid
        state.last_mr_state = mr_state
        state.last_note_id = note_id
        state.last_branch = branch
        self.force_save(project_id)

    def reset(self, project_id: int) -> None:
        """Reset state for a project."""
        self._states[project_id] = ProjectState()
        self.save(project_id)


__all__ = ["StateManager", "ProjectState", "DEFAULT_SAVE_DELAY"]