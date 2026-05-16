
import unittest
from unittest.mock import MagicMock, patch
from pathlib import Path
from src.gitlab_watcher.watcher import Watcher
from src.gitlab_watcher.config import Config, ProjectConfig, load_config
from src.gitlab_watcher.state import ProjectState, StateManager

class TestWatcherIntegrationSafety(unittest.TestCase):
    def setUp(self):
        # Create a dummy config file
        self.config_path = Path("/tmp/test_watcher_config.conf")
        self.config_path.write_text("""
GITLAB_URL="https://git.custom.com"
GITLAB_TOKEN="secret-token"
GITLAB_USERNAME="OpenCode"
PROJECT_DIRS=(
  "/tmp/test-project"
)
""")
        # Create a dummy project dir
        self.project_path = Path("/tmp/test-project")
        self.project_path.mkdir(exist_ok=True)
        (self.project_path / "CLAUDE.md").write_text("Project ID: 99")

        self.mock_gitlab = MagicMock()
        # Simulate auto-detect failure to force fallback
        self.mock_gitlab.get_current_user.side_effect = Exception("Connection error")
        
        self.mock_state_mgr = MagicMock(spec=StateManager)
        self.state_obj = ProjectState()
        self.mock_state_mgr.load.return_value = self.state_obj
        self.mock_state_mgr.is_processing.return_value = False

    def tearDown(self):
        if self.config_path.exists():
            self.config_path.unlink()
        import shutil
        if self.project_path.exists():
            shutil.rmtree(self.project_path)

    def test_config_username_persistence(self):
        """Verify that GITLAB_USERNAME is correctly loaded and used as fallback."""
        watcher = Watcher(config_path=str(self.config_path), gitlab=self.mock_gitlab, disable_lock=True)
        self.assertEqual(watcher.gitlab_username, "OpenCode")
        self.assertEqual(watcher.processor.gitlab_username, "OpenCode")

    def test_check_mr_status_type_safety(self):
        """Verify that string keys in state.tracked_mrs are correctly converted to int for API calls."""
        mock_processor = MagicMock()
        watcher = Watcher(config_path=str(self.config_path), gitlab=self.mock_gitlab, state=self.mock_state_mgr, processor=mock_processor, disable_lock=True)
        
        project = watcher.config.projects[0]
        
        # Tracked MR in state uses string keys
        self.state_obj.tracked_mrs = {"123": {"branch": "123-fix", "created_by_watcher": True}}
        
        # Mock a merged MR
        mr = MagicMock()
        mr.iid = 123
        mr.state = "merged"
        mr.title = "Fix"
        mr.web_url = "url"
        self.mock_gitlab.get_merge_request.return_value = mr
        
        # Run check
        watcher.check_mr_status(project)
        
        # It should have called get_merge_request with integer 123
        self.mock_gitlab.get_merge_request.assert_called_with(99, 123)
        # And it should have called cleanup_after_merge
        mock_processor.cleanup_after_merge.assert_called()

if __name__ == "__main__":
    unittest.main()
