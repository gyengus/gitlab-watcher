"""Tests for configuration parsing."""

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


def test_parse_bash_config_simple() -> None:
    """Test parsing simple key=value lines."""
    content = """
GITLAB_URL="https://git.example.com"
GITLAB_TOKEN="secret-token"
POLL_INTERVAL=30
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".conf", delete=False) as f:
        f.write(content)
        f.flush()

        result = parse_bash_config(Path(f.name))

        assert result["GITLAB_URL"] == "https://git.example.com"
        assert result["GITLAB_TOKEN"] == "secret-token"
        assert result["POLL_INTERVAL"] == "30"


def test_parse_bash_config_array() -> None:
    """Test parsing bash arrays."""
    content = """
PROJECT_DIRS=(
  "/path/to/project1"
  "/path/to/project2"
)
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".conf", delete=False) as f:
        f.write(content)
        f.flush()

        result = parse_bash_config(Path(f.name))

        assert "PROJECT_DIRS" in result
        assert "/path/to/project1" in result["PROJECT_DIRS"]
        assert "/path/to/project2" in result["PROJECT_DIRS"]


def test_parse_bash_config_comments() -> None:
    """Test that comments are ignored."""
    content = """
# This is a comment
GITLAB_URL="https://git.example.com"
# Another comment
POLL_INTERVAL=30
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".conf", delete=False) as f:
        f.write(content)
        f.flush()

        result = parse_bash_config(Path(f.name))

        assert "GITLAB_URL" in result
        assert "POLL_INTERVAL" in result
        assert len(result) == 2


def test_extract_project_id() -> None:
    """Test extracting project ID from CLAUDE.md."""
    content = """
# Project documentation

Some text here.

Project ID: 42

More text.
"""
    with tempfile.TemporaryDirectory() as tmpdir:
        claude_md = Path(tmpdir) / "CLAUDE.md"
        claude_md.write_text(content)

        project_id = extract_project_id(claude_md)
        assert project_id == 42


def test_extract_project_id_case_insensitive() -> None:
    """Test that project ID extraction is case insensitive."""
    content = "project_id: 123"
    with tempfile.TemporaryDirectory() as tmpdir:
        claude_md = Path(tmpdir) / "CLAUDE.md"
        claude_md.write_text(content)

        project_id = extract_project_id(claude_md)
        assert project_id == 123


def test_extract_project_id_not_found() -> None:
    """Test when project ID is not found."""
    content = "No project ID here"
    with tempfile.TemporaryDirectory() as tmpdir:
        claude_md = Path(tmpdir) / "CLAUDE.md"
        claude_md.write_text(content)

        project_id = extract_project_id(claude_md)
        assert project_id is None


def test_load_config_integration() -> None:
    """Test full config loading integration."""
    config_content = """
GITLAB_URL="https://git.example.com"
GITLAB_TOKEN="secret"
DISCORD_WEBHOOK="https://discord.com/api/webhooks/..."
LABEL_IN_PROGRESS="In progress"
LABEL_REVIEW="Review"
GITLAB_USERNAME="claude"
POLL_INTERVAL=30

PROJECT_DIRS=(
  "/tmp/test-project"
)
"""
    claude_md_content = "Project ID: 31\n\nSome documentation."

    with tempfile.TemporaryDirectory() as tmpdir:
        # Create config file
        config_file = Path(tmpdir) / "gitlab_watcher.conf"
        config_file.write_text(config_content)

        # Create project directory with CLAUDE.md
        project_dir = Path(tmpdir) / "test-project"
        project_dir.mkdir()
        (project_dir / "CLAUDE.md").write_text(claude_md_content)

        # Update PROJECT_DIRS in config
        config_file.write_text(
            config_content.replace('"/tmp/test-project"', f'"{project_dir}"')
        )

        config = load_config(str(config_file))

        assert config.gitlab_url == "https://git.example.com"
        assert config.gitlab_token == "secret"
        assert config.poll_interval == 30
        assert len(config.projects) == 1
        assert config.projects[0].project_id == 31
        assert config.projects[0].name == "test-project"


def test_load_config_with_claude_mode(tmp_path: Path) -> None:
    """Test loading config with AI_TOOL_MODE and AI_TOOL_CUSTOM_COMMAND."""
    config_file = tmp_path / "gitlab_watcher.conf"
    config_file.write_text(
        """
GITLAB_URL="https://git.example.com"
GITLAB_TOKEN="test-token"
AI_TOOL_MODE="direct"
AI_TOOL_CUSTOM_COMMAND="my-tool {{prompt}}"
PROJECT_DIRS=(
    "{}"
)
""".format(tmp_path / "project")
    )

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / "CLAUDE.md").write_text("Project ID: 42\n")

    config = load_config(str(config_file))

    assert config.ai_tool_mode == "direct"
    assert config.ai_tool_custom_command == "my-tool {prompt}"


def test_load_config_default_claude_mode(tmp_path: Path) -> None:
    """Test default AI_TOOL_MODE is ollama."""
    config_file = tmp_path / "gitlab_watcher.conf"
    config_file.write_text(
        """
GITLAB_URL="https://git.example.com"
GITLAB_TOKEN="test-token"
PROJECT_DIRS=(
    "{}"
)
""".format(tmp_path / "project")
    )

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / "CLAUDE.md").write_text("Project ID: 42\n")

    config = load_config(str(config_file))

    assert config.ai_tool_mode == "ollama"
    assert config.ai_tool_custom_command == ""


def test_load_config_with_timeout(tmp_path: Path) -> None:
    """Test loading config with AI_TOOL_TIMEOUT."""
    config_file = tmp_path / "gitlab_watcher.conf"
    config_file.write_text(
        """
GITLAB_URL="https://git.example.com"
GITLAB_TOKEN="test-token"
AI_TOOL_TIMEOUT=1234
PROJECT_DIRS=(
    "{}"
)
""".format(tmp_path / "project")
    )

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / "CLAUDE.md").write_text("Project ID: 42\n")

    config = load_config(str(config_file))

    assert config.ai_tool_timeout == 1234
