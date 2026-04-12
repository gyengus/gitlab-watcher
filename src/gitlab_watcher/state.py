"""State management for tracking processed items."""

import json
import logging
import threading
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional, Any

logger = logging.getLogger(__name__)


# Default delay for debounced saves
DEFAULT_SAVE_DELAY = 1.0


@dataclass
class ProjectState:
    """State for a single project."""

    last_mr_iid: Optional[int] = None
    last_mr_state: Optional[str] = None
    last_branch: Optional[str] = None
    processing: bool = False
    tracked_mrs: dict[str, dict] = field(default_factory=dict)


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
                
                # Migration logic: if we have legacy data but no tracked_mrs, migrate it
                if "tracked_mrs" not in data:
                    data["tracked_mrs"] = {}
                
                last_iid = data.get("last_mr_iid")
                if last_iid and str(last_iid) not in data["tracked_mrs"]:
                    data["tracked_mrs"][str(last_iid)] = {
                        "branch": data.get("last_branch"),
                    }
                
                # Filter data to only include valid ProjectState fields
                valid_fields = {f.name for f in ProjectState.__dataclass_fields__.values()}
                filtered_data = {k: v for k, v in data.items() if k in valid_fields}
                state = ProjectState(**filtered_data)
            except (json.JSONDecodeError, TypeError, KeyError) as e:
                logger.warning(f"Failed to load state for project {project_id}: {e}. Creating new state.")
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
        mr_iid: int,
        mr_state: Optional[str],
        branch: Optional[str],
    ) -> None:
        """Update MR tracking state and force an immediate save."""
        state = self.load(project_id)
        
        # Update legacy fields for backward compatibility
        state.last_mr_iid = mr_iid
        state.last_mr_state = mr_state
        state.last_branch = branch
        
        # Update multi-MR tracking
        mr_id_str = str(mr_iid)
        if mr_id_str not in state.tracked_mrs:
            state.tracked_mrs[mr_id_str] = {}
        
        state.tracked_mrs[mr_id_str].update({
            "branch": branch,
        })
        
        self.force_save(project_id)

    def add_tracked_mr(self, project_id: int, mr_iid: int, branch: str) -> None:
        """Add an MR to the tracked list if not already present."""
        state = self.load(project_id)
        mr_id_str = str(mr_iid)
        if mr_id_str not in state.tracked_mrs:
            state.tracked_mrs[mr_id_str] = {
                "branch": branch,
            }
            self.force_save(project_id)

    def remove_tracked_mr(self, project_id: int, mr_iid: int) -> None:
        """Remove an MR from the tracked list."""
        state = self.load(project_id)
        mr_id_str = str(mr_iid)
        if mr_id_str in state.tracked_mrs:
            del state.tracked_mrs[mr_id_str]
            
            # If this was the last_mr_iid, clear legacy fields too
            if state.last_mr_iid == mr_iid:
                state.last_mr_iid = None
                state.last_branch = None
                state.last_mr_state = None
                
            self.force_save(project_id)

    def reset(self, project_id: int) -> None:
        """Reset state for a project."""
        self._states[project_id] = ProjectState()
        self.save(project_id)


__all__ = ["StateManager", "ProjectState", "DEFAULT_SAVE_DELAY"]