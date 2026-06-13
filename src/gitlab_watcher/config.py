"""Configuration handling with bash config compatibility."""

import logging
import os
import re
import shlex
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Default configuration file path
DEFAULT_CONFIG_PATH = os.path.expanduser("~/.config/gitlab-watcher/config.conf")


@dataclass
class ProjectConfig:
    """Configuration for a monitored project."""

    project_id: int
    path: Path
    name: str
    discord_webhook_url: str = ""


@dataclass
class Config:
    """Global configuration loaded from bash-style config file."""

    gitlab_url: str = ""
    gitlab_token: str = ""
    gitlab_ssl_verify: bool = True
    discord_webhook: str = ""
    label_in_progress: str = "In progress"
    label_review: str = "Review"
    poll_interval: int = 30
    ai_tool_mode: str = "ollama"
    ai_tool_custom_command: str | list[str] = ""
    ai_tool_failover_model: str = ""
    ai_tool_timeout: int = 3600
    log_file: str = "/var/log/gitlab-watcher.log"
    log_level: str = "INFO"
    gitlab_username: str = "OpenCode"
    default_branch: str = "master"
    project_dirs: list[str] = field(default_factory=list)
    projects: list[ProjectConfig] = field(default_factory=list)

    def get_project_by_name(self, name: str) -> Optional[ProjectConfig]:
        """Find a project by its name."""
        for project in self.projects:
            if project.name == name:
                return project
        return None


def parse_bash_config(config_path: Path) -> dict[str, str | list[str]]:
    """Parse bash-style config file into a dictionary.

    Handles:
    - Simple key=value assignments
    - Bash arrays: KEY=(val1 val2 "quoted val") - single or multi-line
    - Comments (lines starting with #)
    - Quoted values
    """
    result: dict[str, str | list[str]] = {}
    content = config_path.read_text(encoding="utf-8")

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

                if array_line:
                    # Parse array values using shlex
                    values.extend(shlex.split(array_line))

                i += 1

            result[key] = values
            continue

        # Handle inline bash arrays: KEY=(val1 val2)
        inline_array = re.match(r"^(\w+)=\((.+)\)$", line)
        if inline_array:
            key = inline_array.group(1)
            values_str = inline_array.group(2)

            # Parse array values using shlex
            result[key] = shlex.split(values_str)
            i += 1
            continue

        # Handle simple key=value
        simple_match = re.match(r"^(\w+)=(.*)$", line)
        if simple_match:
            key = simple_match.group(1)
            value_str = simple_match.group(2).strip()

            # Parse simple values using shlex
            parts = shlex.split(value_str)
            if parts:
                result[key] = parts[0]
            else:
                result[key] = ""

        i += 1

    return result


def extract_project_id(project_file_path: Path) -> Optional[int]:
    """Extract Project ID from PROJECT.md, AGENTS.md, or CLAUDE.md file.

    Case-insensitive. Supports markdown formatting on the value, label, or entire line:
    - Project ID: 31
    - project id: 31
    - PROJECT_ID: 31
    - Project ID: **31** / *31* / ***31***
    - Project ID: __31__ / _31_
    - Project ID: `31`
    - **Project ID: 31** / **Project ID:** 31
    - `Project ID: 31`
    """
    if not project_file_path.exists():
        return None

    content = project_file_path.read_text(encoding="utf-8")

    match = re.search(r"(?i)(?:\*{1,3}|_{1,3}|`{1,2})?project[_\s]*id:?[*_`\s]*(\d+)(?:\*{1,3}|_{1,3}|`{1,2})?", content)
    if match:
        project_id = int(match.group(1))
        # Range check for project ID: must be a positive integer
        if project_id <= 0:
            return None
        return project_id

    return None


def load_config(config_path: str) -> Config:
    """Load configuration from file and discover projects."""
    config_file = Path(config_path).expanduser()

    if not config_file.exists():
        raise FileNotFoundError(f"Config file not found: {config_file}")

    # Security check: Ensure config file is not world-writable
    mode = config_file.stat().st_mode
    if mode & stat.S_IWOTH:
        logger.warning(f"Config file {config_file} is world-writable! This is a security risk.")
    if mode & stat.S_IWGRP:
        logger.warning(f"Config file {config_file} is group-writable. Ensure this is intentional.")

    raw_config = parse_bash_config(config_file)

    # Helper function to safely convert config values
    def get_str(key: str, default: str = "") -> str:
        value = raw_config.get(key, default)
        if isinstance(value, list):
            if len(value) > 1:
                logger.warning(f"Config key '{key}' expected a single string but found a list. Using the first element.")
            return str(value[0]) if value else ""
        return str(value)

    def get_int(key: str, default: int = 0) -> int:
        value = raw_config.get(key, str(default))
        if isinstance(value, list):
            if len(value) > 1:
                logger.warning(f"Config key '{key}' expected a single int but found a list. Using the first element.")
            return int(str(value[0])) if value else default
        return int(str(value))

    def get_bool(key: str, default: bool = False) -> bool:
        value = raw_config.get(key, str(default))
        if isinstance(value, list):
            if len(value) > 1:
                logger.warning(f"Config key '{key}' expected a single bool but found a list. Using the first element.")
            val_str = str(value[0]) if value else ""
        else:
            val_str = str(value)
        return val_str.lower() in ("true", "yes", "1", "t", "y")

    config = Config(
        gitlab_url=get_str("GITLAB_URL"),
        gitlab_token=get_str("GITLAB_TOKEN"),
        gitlab_ssl_verify=get_bool("GITLAB_SSL_VERIFY", True),
        discord_webhook=get_str("DISCORD_WEBHOOK"),
        label_in_progress=get_str("LABEL_IN_PROGRESS", "In progress"),
        label_review=get_str("LABEL_REVIEW", "Review"),
        poll_interval=get_int("POLL_INTERVAL", 30),
        ai_tool_mode=get_str("AI_TOOL_MODE", "ollama"),
        ai_tool_custom_command=raw_config.get("AI_TOOL_CUSTOM_COMMAND", ""),
        ai_tool_failover_model=get_str("AI_TOOL_FAILOVER_MODEL"),
        ai_tool_timeout=get_int("AI_TOOL_TIMEOUT", 3600),
        log_file=get_str("LOG_FILE", "/var/log/gitlab-watcher.log"),
        log_level=get_str("LOG_LEVEL", "INFO").upper(),
        gitlab_username=get_str("GITLAB_USERNAME", "OpenCode"),
        default_branch=get_str("DEFAULT_BRANCH", "master"),
    )


    # Get project directories
    project_dirs = raw_config.get("PROJECT_DIRS", [])
    if isinstance(project_dirs, str):
        project_dirs = [project_dirs]

    config.project_dirs = project_dirs

    # Discover projects
    seen_ids: set[int] = set()

    for project_dir in project_dirs:
        if not project_dir or project_dir.strip().startswith("#"):
            continue

        # Canonicalize path immediately after expansion to prevent traversal bypasses
        try:
            project_path = Path(project_dir).expanduser().resolve()
        except (OSError, RuntimeError) as e:
            logger.warning(f"Could not resolve project directory {project_dir}: {e}")
            continue

        if not project_path.exists() or not project_path.is_dir():
            logger.warning(f"Project directory not found or not a directory: {project_path}")
            continue
        
        # Hardened path traversal protection: reject system-level paths
        sensitive_bases = [
            "/etc", "/root", "/var", "/bin", "/sbin", "/usr/bin", "/usr/sbin", 
            "/proc", "/sys", "/dev", "/lib", "/lib64", "/usr/lib", "/usr/lib64"
        ]
        if any(str(project_path).startswith(base) for base in sensitive_bases):
            logger.warning(f"Skipping potentially sensitive project directory: {project_path}")
            continue
            
        # Security: Ensure the project directory itself is not world-writable
        try:
            if project_path.stat().st_mode & stat.S_IWOTH:
                logger.warning(f"Skipping world-writable project directory for security: {project_path}")
                continue
        except Exception:
            continue

        # Hardened path validation: Ensure project is within a safe workspace
        # Resolve all safe bases to real paths for robust comparison
        safe_bases = [
            Path.home().resolve(),
            Path.cwd().resolve(),
            Path("/tmp").resolve(),
            Path("/var/tmp").resolve(),
        ]
        
        is_safe = False
        for base in safe_bases:
            try:
                # Use is_relative_to for strict prefix checking on canonical paths
                if project_path.is_relative_to(base):
                    is_safe = True
                    break
            except (ValueError, AttributeError):
                continue
        
        if not is_safe:
             logger.warning(f"Skipping project directory outside safe bases: {project_path}")
             continue

        project_id = None
        for filename in ["PROJECT.md", "AGENTS.md", "CLAUDE.md"]:
            project_file = project_path / filename
            project_id = extract_project_id(project_file)
            if project_id is not None:
                break

        if project_id is None:
            continue

        if project_id in seen_ids:
            continue

        seen_ids.add(project_id)

        config.projects.append(
            ProjectConfig(
                project_id=project_id,
                path=project_path,
                name=project_path.name,
            )
        )

    if not config.projects:
        raise ValueError("No valid projects found in configuration")

    return config


__all__ = [
    "DEFAULT_CONFIG_PATH",
    "ProjectConfig",
    "Config",
    "parse_bash_config",
    "extract_project_id",
    "load_config",
]
