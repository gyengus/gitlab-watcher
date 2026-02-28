"""Additional tests for configuration parsing."""

import tempfile
from pathlib import Path

import pytest

from gitlab_watcher.config import (
    Config,
    ProjectConfig,
    extract_project_id,
    load_config,
    parse_bash_config,
)


class TestParseBashConfigEdgeCases:
    """Tests for bash config parsing edge cases."""

    def test_parse_inline_array(self) -> None:
        """Test parsing inline bash arrays."""
        content = 'PROJECT_DIRS=("/path1" "/path2" "/path3")'
        with tempfile.NamedTemporaryFile(mode="w", suffix=".conf", delete=False) as f:
            f.write(content)
            f.flush()

            result = parse_bash_config(Path(f.name))

            assert result["PROJECT_DIRS"] == ["/path1", "/path2", "/path3"]

    def test_parse_single_value_array(self) -> None:
        """Test parsing single value in array."""
        content = '''
PROJECT_DIRS=(
  "/single/path"
)
'''
        with tempfile.NamedTemporaryFile(mode="w", suffix=".conf", delete=False) as f:
            f.write(content)
            f.flush()

            result = parse_bash_config(Path(f.name))

            assert result["PROJECT_DIRS"] == ["/single/path"]

    def test_parse_array_with_comments(self) -> None:
        """Test parsing array with inline comments."""
        content = '''
PROJECT_DIRS=(
  "/path1"
  # This is a comment
  "/path2"
)
'''
        with tempfile.NamedTemporaryFile(mode="w", suffix=".conf", delete=False) as f:
            f.write(content)
            f.flush()

            result = parse_bash_config(Path(f.name))

            assert "/path1" in result["PROJECT_DIRS"]
            assert "/path2" in result["PROJECT_DIRS"]

    def test_parse_single_quoted_values(self) -> None:
        """Test parsing single-quoted values."""
        content = "GITLAB_URL='https://git.example.com'"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".conf", delete=False) as f:
            f.write(content)
            f.flush()

            result = parse_bash_config(Path(f.name))

            assert result["GITLAB_URL"] == "https://git.example.com"

    def test_parse_unquoted_values(self) -> None:
        """Test parsing unquoted values."""
        content = "POLL_INTERVAL=30"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".conf", delete=False) as f:
            f.write(content)
            f.flush()

            result = parse_bash_config(Path(f.name))

            assert result["POLL_INTERVAL"] == "30"

    def test_parse_empty_file(self) -> None:
        """Test parsing empty file."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".conf", delete=False) as f:
            f.write("")
            f.flush()

            result = parse_bash_config(Path(f.name))

            assert result == {}

    def test_parse_only_comments(self) -> None:
        """Test parsing file with only comments."""
        content = '''
# This is a comment
# Another comment
'''
        with tempfile.NamedTemporaryFile(mode="w", suffix=".conf", delete=False) as f:
            f.write(content)
            f.flush()

            result = parse_bash_config(Path(f.name))

            assert result == {}


class TestExtractProjectId:
    """Tests for project ID extraction."""

    def test_extract_with_markdown_formatting(self) -> None:
        """Test extracting project ID with markdown bold."""
        content = "Project ID: **42**"
        with tempfile.TemporaryDirectory() as tmpdir:
            claude_md = Path(tmpdir) / "CLAUDE.md"
            claude_md.write_text(content)

            project_id = extract_project_id(claude_md)
            assert project_id == 42

    def test_extract_with_underscore(self) -> None:
        """Test extracting project ID with underscore."""
        content = "project_id: 123"
        with tempfile.TemporaryDirectory() as tmpdir:
            claude_md = Path(tmpdir) / "CLAUDE.md"
            claude_md.write_text(content)

            project_id = extract_project_id(claude_md)
            assert project_id == 123

    def test_extract_with_space(self) -> None:
        """Test extracting project ID with space separator."""
        content = "project id: 456"
        with tempfile.TemporaryDirectory() as tmpdir:
            claude_md = Path(tmpdir) / "CLAUDE.md"
            claude_md.write_text(content)

            project_id = extract_project_id(claude_md)
            assert project_id == 456

    def test_extract_no_project_id(self) -> None:
        """Test when file has no project ID."""
        content = "This is just documentation"
        with tempfile.TemporaryDirectory() as tmpdir:
            claude_md = Path(tmpdir) / "CLAUDE.md"
            claude_md.write_text(content)

            project_id = extract_project_id(claude_md)
            assert project_id is None

    def test_extract_nonexistent_file(self) -> None:
        """Test when file doesn't exist."""
        project_id = extract_project_id(Path("/nonexistent/CLAUDE.md"))
        assert project_id is None


class TestLoadConfigErrors:
    """Tests for config loading error cases."""

    def test_load_config_missing_file(self) -> None:
        """Test loading config from nonexistent file."""
        with pytest.raises(FileNotFoundError):
            load_config("/nonexistent/config.conf")

    def test_load_config_no_projects(self) -> None:
        """Test loading config with no valid projects."""
        content = '''
GITLAB_URL="https://git.example.com"
GITLAB_TOKEN="secret"
POLL_INTERVAL=30
PROJECT_DIRS=()
'''
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.conf"
            config_path.write_text(content)

            with pytest.raises(ValueError, match="No valid projects"):
                load_config(str(config_path))

    def test_load_config_project_without_claude_md(self) -> None:
        """Test loading config with project missing CLAUDE.md."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir) / "project"
            project_dir.mkdir()

            config_content = f'''
GITLAB_URL="https://git.example.com"
GITLAB_TOKEN="secret"
POLL_INTERVAL=30
PROJECT_DIRS=(
  "{project_dir}"
)
'''
            config_path = Path(tmpdir) / "config.conf"
            config_path.write_text(config_content)

            with pytest.raises(ValueError, match="No valid projects"):
                load_config(str(config_path))

    def test_load_config_project_without_project_id(self) -> None:
        """Test loading config with CLAUDE.md missing project ID."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir) / "project"
            project_dir.mkdir()
            (project_dir / "CLAUDE.md").write_text("No project ID here")

            config_content = f'''
GITLAB_URL="https://git.example.com"
GITLAB_TOKEN="secret"
POLL_INTERVAL=30
PROJECT_DIRS=(
  "{project_dir}"
)
'''
            config_path = Path(tmpdir) / "config.conf"
            config_path.write_text(config_content)

            with pytest.raises(ValueError, match="No valid projects"):
                load_config(str(config_path))

    def test_load_config_duplicate_project_ids(self) -> None:
        """Test that duplicate project IDs are skipped."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir1 = Path(tmpdir) / "project1"
            project_dir1.mkdir()
            (project_dir1 / "CLAUDE.md").write_text("Project ID: 42")

            project_dir2 = Path(tmpdir) / "project2"
            project_dir2.mkdir()
            (project_dir2 / "CLAUDE.md").write_text("Project ID: 42")

            config_content = f'''
GITLAB_URL="https://git.example.com"
GITLAB_TOKEN="secret"
POLL_INTERVAL=30
PROJECT_DIRS=(
  "{project_dir1}"
  "{project_dir2}"
)
'''
            config_path = Path(tmpdir) / "config.conf"
            config_path.write_text(config_content)

            config = load_config(str(config_path))

            # Only one project should be loaded (first one)
            assert len(config.projects) == 1
            assert config.projects[0].project_id == 42

    def test_load_config_nonexistent_project_dir(self) -> None:
        """Test that nonexistent project directories are skipped."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir) / "project"
            project_dir.mkdir()
            (project_dir / "CLAUDE.md").write_text("Project ID: 42")

            config_content = f'''
GITLAB_URL="https://git.example.com"
GITLAB_TOKEN="secret"
POLL_INTERVAL=30
PROJECT_DIRS=(
  "{project_dir}"
  "/nonexistent/path"
)
'''
            config_path = Path(tmpdir) / "config.conf"
            config_path.write_text(config_content)

            config = load_config(str(config_path))

            assert len(config.projects) == 1