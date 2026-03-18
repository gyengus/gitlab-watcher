"""Issue and MR processing logic."""

import logging
import re
import shlex
import subprocess
from pathlib import Path
from typing import Callable

from .config import ProjectConfig
from .discord import DiscordWebhook
from .git_ops import GitOps
from .gitlab_client import GitLabClient, Issue, MergeRequest, Note
from .protocols import GitOperations
from .state import StateManager

# Security constants for prompt sanitization
MAX_PROMPT_LENGTH = 10000
FORBIDDEN_PATTERNS = [
    r"\$\([^)]+\)",  # Command substitution $(...)
    r"`[^`]+`",  # Backtick command `...`
    r"\$\{[^}]+\}",  # Variable expansion ${...}
    r"\$\w+",  # Variable reference $var
]

# Input validation constants
MAX_TITLE_LENGTH = 255
MAX_DESCRIPTION_LENGTH = 50000
MAX_SLUG_LENGTH = 50
MAX_BRANCH_LENGTH = 100

# Claude CLI timeout
CLAUDE_CLI_TIMEOUT_SECONDS = 600


class Processor:
    """Processes issues and MR comments."""

    def __init__(
        self,
        gitlab: GitLabClient,
        discord: DiscordWebhook,
        state: StateManager,
        gitlab_username: str,
        label_in_progress: str,
        label_review: str,
        ai_tool_mode: str = "ollama",
        ai_tool_custom_command: str = "",
        default_branch: str = "master",
        git_factory: Callable[[Path], GitOperations] = GitOps,
    ) -> None:
        """Initialize processor.

        Args:
            gitlab: GitLab API client
            discord: Discord webhook client
            state: State manager
            gitlab_username: GitLab username for filtering comments
            label_in_progress: Label for in-progress issues
            label_review: Label for issues under review
            ai_tool_mode: AI tool mode ("ollama", "direct", "custom", "opencode", or "opencode-custom")
            ai_tool_custom_command: Custom command for AI tool (used when mode is "custom")
            default_branch: Default branch name (default: "master")
            git_factory: Factory function to create GitOperations instances (for dependency injection)
        """
        self.gitlab = gitlab
        self.discord = discord
        self.state = state
        self.gitlab_username = gitlab_username
        self.label_in_progress = label_in_progress
        self.label_review = label_review
        self.ai_tool_mode = ai_tool_mode
        self.ai_tool_custom_command = ai_tool_custom_command
        self.default_branch = default_branch
        self.git_factory = git_factory
        self.logger = logging.getLogger(__name__)

    def _sanitize_prompt(self, prompt: str) -> str:
        """Sanitize prompt to prevent command injection.

        Args:
            prompt: The raw prompt string

        Returns:
            Sanitized prompt string

        Raises:
            ValueError: If prompt contains forbidden patterns
        """
        if len(prompt) > MAX_PROMPT_LENGTH:
            prompt = prompt[:MAX_PROMPT_LENGTH]

        for pattern in FORBIDDEN_PATTERNS:
            if re.search(pattern, prompt):
                raise ValueError(f"Prompt contains forbidden pattern: {pattern}")

        return prompt

    def _validate_issue_title(self, title: str) -> str:
        """Validate and sanitize issue title.

        Args:
            title: The raw issue title

        Returns:
            Validated and sanitized title

        Raises:
            ValueError: If title is empty or invalid
        """
        if not title or not title.strip():
            raise ValueError("Issue title cannot be empty")

        # Truncate to max length
        title = title[:MAX_TITLE_LENGTH]

        # Remove control characters
        title = "".join(c for c in title if c.isprintable())

        return title.strip()

    def _validate_branch_name(self, branch: str) -> str:
        """Validate and sanitize branch name.

        Args:
            branch: The proposed branch name

        Returns:
            Validated branch name
        """
        branch = branch.strip()

        if not branch:
            return "auto-branch"

        # Remove problematic characters for git branch names
        branch = re.sub(r"[^\w\-/.]", "-", branch)

        # Remove consecutive hyphens
        while "--" in branch:
            branch = branch.replace("--", "-")

        # Remove leading/trailing hyphens and dots
        branch = branch.strip("-.")

        # Truncate to max length
        if len(branch) > MAX_BRANCH_LENGTH:
            branch = branch[:MAX_BRANCH_LENGTH]

        return branch or "auto-branch"

    def _run_claude(self, prompt: str, repo_path: Path) -> tuple[bool, str]:
        """Run AI tool CLI with a prompt based on configured mode.

        Args:
            prompt: The prompt for AI tool
            repo_path: Path to the repository

        Returns:
            Tuple of (success, output)
        """
        # Sanitize prompt to prevent command injection
        try:
            safe_prompt = self._sanitize_prompt(prompt)
        except ValueError as e:
            return False, f"Prompt validation failed: {e}"

        # Build command based on mode
        if self.ai_tool_mode == "ollama":
            cmd = [
                "ollama",
                "launch",
                "claude",
                "--",
                "-p",
                "--permission-mode",
                "acceptEdits",
                safe_prompt,
            ]
        elif self.ai_tool_mode == "direct":
            cmd = ["claude", "-p", "--permission-mode", "acceptEdits", safe_prompt]
        elif self.ai_tool_mode == "opencode":
            cmd = ["opencode", safe_prompt]
        elif self.ai_tool_mode == "custom":
            if not self.ai_tool_custom_command:
                return False, "AI_TOOL_CUSTOM_COMMAND not set for custom mode"
            # Split first, then substitute to preserve multi-word values
            cmd_parts = shlex.split(self.ai_tool_custom_command)
            cmd = [
                part.replace("{prompt}", safe_prompt).replace("{cwd}", str(repo_path))
                for part in cmd_parts
            ]
        else:
            return False, f"Unknown AI_TOOL_MODE: {self.ai_tool_mode}"

        try:
            env = {"CLAUDECODE": ""}
            result = subprocess.run(
                cmd,
                cwd=repo_path,
                capture_output=True,
                text=True,
                env=env,
                timeout=CLAUDE_CLI_TIMEOUT_SECONDS,
            )
            return result.returncode == 0, result.stdout + result.stderr
        except subprocess.TimeoutExpired:
            return False, "Claude timed out"
        except FileNotFoundError:
            return False, "Claude CLI not found"

    def process_issue(
        self,
        project: ProjectConfig,
        issue: Issue,
    ) -> bool:
        """Process an issue: create branch, run Claude, push, create MR.

        Args:
            project: Project configuration
            issue: The issue to process

        Returns:
            True if successful, False otherwise
        """
        git = self.git_factory(project.path)

        # Validate issue title
        try:
            validated_title = self._validate_issue_title(issue.title)
        except ValueError as e:
            self.logger.error(f"Invalid issue title: {e}")
            self.discord.notify_error(
                project.name,
                f"Invalid issue title: {e}",
            )
            self.state.set_processing(project.project_id, False)
            return False

        # Generate and validate branch name
        slug = GitOps.generate_slug(validated_title, max_length=MAX_SLUG_LENGTH)
        branch = self._validate_branch_name(f"{issue.iid}-{slug}")

        self.logger.info(
            f"[{project.name}] Processing issue #{issue.iid}: {validated_title}"
        )
        self.logger.debug(f"[{project.name}] Creating branch: {branch}")

        self.discord.notify_issue_started(
            project.name,
            validated_title,
            issue.web_url,
            branch,
        )

        # Add "In progress" label
        self.gitlab.update_issue_labels(
            project.project_id,
            issue.iid,
            issue.labels + [self.label_in_progress],
        )

        # Create branch
        git.fetch()
        git.checkout(self.default_branch)
        git.pull()

        if not git.checkout(branch, create=True):
            self.discord.notify_error(
                project.name,
                f"Could not create branch `{branch}`",
            )
            self.state.set_processing(project.project_id, False)
            return False

        # Build prompt for Claude (truncate description if too long)
        description = issue.description or ""
        if len(description) > MAX_DESCRIPTION_LENGTH:
            description = description[:MAX_DESCRIPTION_LENGTH]

        prompt = f"""You are working on issue #{issue.iid}: {validated_title}

Issue description:
{description}

Please complete this task. Make the necessary changes and commit them.
Write commit messages in English.
Do not use conventional commit prefixes like feat:, fix:, etc.
Do not add Co-Authored-By signature to commits."""

        # Run Claude
        success, output = self._run_claude(prompt, project.path)

        if success:
            # Push branch
            git.push("origin", branch, set_upstream=True)

            # Create MR
            mr = self.gitlab.create_merge_request(
                project.project_id,
                source_branch=branch,
                target_branch=self.default_branch,
                title=issue.title,
                description=f"{issue.description}\n\nCloses #{issue.iid}",
            )

            if mr:
                # Move issue to Review
                self.gitlab.update_issue_labels(
                    project.project_id,
                    issue.iid,
                    [self.label_review],
                )
                self.discord.notify_mr_created(
                    project.name,
                    issue.title,
                    mr.web_url,
                    issue.iid,
                )
            else:
                self.discord.notify_error(
                    project.name,
                    "Changes done but MR creation failed",
                )
        else:
            self.discord.notify_error(
                project.name,
                f"Processing failed for issue #{issue.iid}",
                output,
            )

        self.state.set_processing(project.project_id, False)
        return success

    def process_comment(
        self,
        project: ProjectConfig,
        mr: MergeRequest,
        comment: str,
    ) -> bool:
        """Process an MR comment: checkout branch, run Claude, push.

        Args:
            project: Project configuration
            mr: The merge request
            comment: The comment to process

        Returns:
            True if successful, False otherwise
        """
        git = self.git_factory(project.path)

        self.discord.send(
            f"🤖 **Processing Comment** [{project.name}]\n"
            f"[{mr.title}]({mr.web_url})\n\n"
            f"Starting to work on: {comment}"
        )

        # Switch to MR branch
        git.fetch()
        if not git.checkout(mr.source_branch):
            self.discord.notify_error(
                project.name,
                f"Could not checkout branch `{mr.source_branch}`",
            )
            self.state.set_processing(project.project_id, False)
            return False

        git.pull("origin", mr.source_branch)

        # Build prompt for Claude
        prompt = f"""You are working on a merge request titled: {mr.title}
Branch: {mr.source_branch}

A reviewer left this feedback:
{comment}

Please address this feedback. Make the necessary changes and commit them.
Write commit messages in English.
Do not use conventional commit prefixes like feat:, fix:, etc.
Do not add Co-Authored-By signature to commits."""

        # Run Claude
        success, output = self._run_claude(prompt, project.path)

        if success:
            # Push changes
            git.push("origin", mr.source_branch)
            self.discord.notify_changes_applied(
                project.name,
                mr.title,
                mr.web_url,
            )
        else:
            self.discord.notify_error(
                project.name,
                f"Processing failed for MR !{mr.iid}",
                output,
            )

        self.state.set_processing(project.project_id, False)
        return success

    def cleanup_after_merge(
        self,
        project: ProjectConfig,
        branch: str,
        mr_title: str,
        mr_url: str,
    ) -> None:
        """Cleanup after MR merge: switch to default branch, delete branch.

        Args:
            project: Project configuration
            branch: The merged branch name
            mr_title: The MR title
            mr_url: The MR URL
        """
        git = self.git_factory(project.path)

        self.discord.notify_mr_merged(project.name, mr_title, mr_url)

        # Switch to default branch and pull
        git.checkout(self.default_branch)
        git.pull()

        # Delete branch
        if branch:
            git.delete_branch(branch, force=True)
            self.discord.notify_cleanup_complete(project.name, branch)

        # Reset state
        self.state.reset(project.project_id)


__all__ = [
    "Processor",
    "MAX_PROMPT_LENGTH",
    "MAX_TITLE_LENGTH",
    "MAX_DESCRIPTION_LENGTH",
    "MAX_SLUG_LENGTH",
    "MAX_BRANCH_LENGTH",
    "CLAUDE_CLI_TIMEOUT_SECONDS",
]
