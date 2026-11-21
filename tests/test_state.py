"""Tests for StateManager persistence, migration, recovery, and cleanup."""

import json
from pathlib import Path

from gitlab_watcher.state import StateManager


class TestStateManagerCore:
    """Core unit tests for StateManager edge cases and recovery."""

    def test_corrupt_json_falls_back_to_fresh_state(self, tmp_path: Path) -> None:
        """Corrupt JSON in state file must not crash; resets to clean ProjectState."""
        manager = StateManager(tmp_path)
        state_file = tmp_path / "state_42.json"
        state_file.write_text("{not valid json!!!", encoding="utf-8")

        state = manager.load(42)
        assert state.last_mr_iid is None
        assert state.processing is False
        assert state.tracked_mrs == {}
        manager.stop()

    def test_legacy_state_migration_populates_tracked_mrs(self, tmp_path: Path) -> None:
        """Legacy state without tracked_mrs migrates last_mr_iid into tracked_mrs."""
        legacy_payload = {
            "last_mr_iid": 77,
            "last_mr_state": "opened",
            "last_branch": "77-legacy-fix",
            "last_processed_note_id": 500,
            "processing": True,
            "unknown_extra_field": "ignored",
        }
        (tmp_path / "state_7.json").write_text(json.dumps(legacy_payload), encoding="utf-8")

        manager = StateManager(tmp_path)
        state = manager.init_state(7)
        assert state.processing is False
        assert "77" in state.tracked_mrs
        assert state.tracked_mrs["77"]["branch"] == "77-legacy-fix"
        assert state.tracked_mrs["77"]["last_processed_note_id"] == 500
        manager.stop()

    def test_update_last_processed_note_is_monotonic(self, tmp_path: Path) -> None:
        """Older note IDs must never overwrite a newer last_processed_note_id."""
        manager = StateManager(tmp_path)
        manager.update_last_processed_note(10, 100)
        manager.update_last_processed_note(10, 50)
        assert manager.get(10, "last_processed_note_id") == 100

        manager.update_last_processed_note(10, 150)
        assert manager.get(10, "last_processed_note_id") == 150
        manager.stop()

    def test_remove_tracked_mr_clears_matching_legacy_fields(self, tmp_path: Path) -> None:
        """Removing the MR that matches last_mr_iid also clears legacy MR fields."""
        manager = StateManager(tmp_path)
        manager.add_tracked_mr(5, 12, "12-feat", created_by_watcher=True)
        manager.update_mr_state(5, 12, "opened", "12-feat")

        manager.remove_tracked_mr(5, 12)
        state = manager.load(5)
        assert "12" not in state.tracked_mrs
        assert state.last_mr_iid is None
        assert state.last_branch is None
        assert state.last_mr_state is None
        manager.stop()

    def test_debounced_save_and_force_save_all(self, tmp_path: Path) -> None:
        """force_save_all flushes all dirty states immediately to disk."""
        manager = StateManager(tmp_path, save_delay=60.0)
        manager.set(99, "last_branch", "99-branch")
        assert not (tmp_path / "state_99.json").exists()

        manager.force_save_all()
        assert (tmp_path / "state_99.json").exists()
        saved = json.loads((tmp_path / "state_99.json").read_text(encoding="utf-8"))
        assert saved["last_branch"] == "99-branch"
        manager.stop()

