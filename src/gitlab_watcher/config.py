"""Configuration handling with bash config compatibility."""

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# Default configuration file path
DEFAULT_CONFIG_PATH = "~/.claude/config/gitlab_watcher.conf"


@dataclass
class ProjectConfig:
    """Configuration for a monitored project."""

    project_id: int
    path: Path
    name: str


@dataclass
class Config:
    """Global configuration loaded from bash-style config file."""

    gitlab_url: str = ""
    gitlab_token: str = ""
    discord_webhook: str = ""
    label_in_progress: str = "In progress"
    label_review: str = "Review"
    gitlab_username: str = "claude"
    poll_interval: int = 30
    project_dirs: list[str] = field(default_factory=list)
    projects: list[ProjectConfig] = field(default_factory=list)


def parse_bash_config(config_path: Path) -> dict[str, str | list[str]]:
    """Parse bash-style config file into a dictionary.

    Handles:
    - Simple key=value assignments
    - Bash arrays: KEY=(val1 val2 "quoted val") - single or multi-line
    - Comments (lines starting with #)
    - Quoted values
    """
    result: dict[str, str | list[str]] = {}
    content = config_path.read_text()

    lines = content.splitlines()
    i = 0

    while i < len(lines):
        line = lines[i].strip()

        # Skip empty lines and comments
        if not line or line.startswith("#"):
            i += 1
            continue

        # Handle bash arrays: KEY=(...)
        array_start = re.match(r"^(\w+)=\($", line)
        if array_start:
            key = array_start.group(1)
            values: list[str] = []
            i += 1

            # Collect array values until closing parenthesis
            while i < len(lines):
                array_line = lines[i].strip()

                # Skip comments
                if array_line.startswith("#"):
                    i += 1
                    continue

                # Check for closing parenthesis
                if array_line == ")":
                    i += 1
                    break

                # Extract value from line (handle quoted strings)
                if array_line:
                    # Remove surrounding quotes if present
                    value = array_line
                    if (value.startswith('"') and value.endswith('"')) or \
                       (value.startswith("'") and value.endswith("'")):
                        value = value[1:-1]
                    values.append(value)

                i += 1

            result[key] = values
            continue

        # Handle inline bash arrays: KEY=(val1 val2)
        inline_array = re.match(r"^(\w+)=\((.+)\)$", line)
        if inline_array:
            key = inline_array.group(1)
            values_str = inline_array.group(2)

            # Parse array values, handling quoted strings
            values = []
            current = ""
            in_quotes = False

            for char in values_str:
                if char == '"':
                    in_quotes = not in_quotes
                elif char == " " and not in_quotes:
                    if current:
                        values.append(current.strip('"'))
                        current = ""
                else:
                    current += char

            if current:
                values.append(current.strip('"'))

            result[key] = values
            i += 1
            continue

        # Handle simple key=value
        simple_match = re.match(r'^(\w+)=(.*)$', line)
        if simple_match:
            key = simple_match.group(1)
            value = simple_match.group(2).strip()

            # Remove surrounding quotes
            if (value.startswith('"') and value.endswith('"')) or \
               (value.startswith("'") and value.endswith("'")):
                value = value[1:-1]

            result[key] = value

        i += 1

    return result


def extract_project_id(claude_md_path: Path) -> Optional[int]:
    """Extract Project ID from CLAUDE.md file.

    Supports formats like:
    - Project ID: 31
    - Project ID: **31**
    - project_id: 31
    """
    if not claude_md_path.exists():
        return None

    content = claude_md_path.read_text()

    # Match patterns with optional markdown formatting
    match = re.search(r"(?i)project[_\s]*id:?\s*\*{0,2}(\d+)\*{0,2}", content)
    if match:
        return int(match.group(1))

    return None


def load_config(config_path: str) -> Config:
    """Load configuration from file and discover projects."""
    config_file = Path(config_path).expanduser()

    if not config_file.exists():
        raise FileNotFoundError(f"Config file not found: {config_file}")

    raw_config = parse_bash_config(config_file)

    config = Config(
        gitlab_url=str(raw_config.get("GITLAB_URL", "")),
        gitlab_token=str(raw_config.get("GITLAB_TOKEN", "")),
        discord_webhook=str(raw_config.get("DISCORD_WEBHOOK", "")),
        label_in_progress=str(raw_config.get("LABEL_IN_PROGRESS", "In progress")),
        label_review=str(raw_config.get("LABEL_REVIEW", "Review")),
        gitlab_username=str(raw_config.get("GITLAB_USERNAME", "claude")),
        poll_interval=int(raw_config.get("POLL_INTERVAL", "30")),
    )

    # Get project directories
    project_dirs = raw_config.get("PROJECT_DIRS", [])
    if isinstance(project_dirs, str):
        project_dirs = [project_dirs]

    config.project_dirs = project_dirs

    # Discover projects
    seen_ids: set[int] = set()

    for project_dir in project_dirs:
        project_path = Path(project_dir).expanduser()

        # Skip commented entries
        if project_dir.strip().startswith("#"):
            continue

        if not project_path.exists():
            continue

        claude_md = project_path / "CLAUDE.md"
        project_id = extract_project_id(claude_md)

        if project_id is None:
            continue

        if project_id in seen_ids:
            continue

        seen_ids.add(project_id)

        config.projects.append(ProjectConfig(
            project_id=project_id,
            path=project_path,
            name=project_path.name,
        ))

    if not config.projects:
        raise ValueError("No valid projects found in configuration")

    return config