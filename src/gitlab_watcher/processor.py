"""Issue and MR processing logic."""

import logging
import re
import shlex
import os
import queue
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

from .config import ProjectConfig
from .discord import DiscordWebhook
from .git_ops import GitOps
from .gitlab_client import GitLabClient, Issue, MergeRequest, Note
from .logging_utils import SensitiveDataFilter, sanitize_for_log
from .protocols import GitOperations
from .state import StateManager

# Security constants for prompt sanitization
MAX_PROMPT_LENGTH = 10000
FORBIDDEN_PATTERNS = [
    r"ignore\s+all\s+previous",
    r"system\s+message",
]

# Input validation constants
MAX_TITLE_LENGTH = 255
MAX_DESCRIPTION_LENGTH = 50000
MAX_SLUG_LENGTH = 50
MAX_BRANCH_LENGTH = 100

# Default AI tool timeout (1 hour)
DEFAULT_AI_TOOL_TIMEOUT = 3600
CLAUDE_CLI_TIMEOUT_SECONDS = DEFAULT_AI_TOOL_TIMEOUT


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
        ai_tool_timeout: int = DEFAULT_AI_TOOL_TIMEOUT,
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
            ai_tool_timeout: Timeout for AI tool in seconds
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
        self.ai_tool_timeout = ai_tool_timeout
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
            match = re.search(pattern, prompt)
            if match:
                matched_text = match.group(0)
                # Truncate matched text if it's very long
                if len(matched_text) > 100:
                    matched_text = matched_text[:97] + "..."
                raise ValueError(f"Prompt contains forbidden pattern: '{pattern}' (found matching text: '{matched_text}')")

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

    def _run_ai_tool(self, prompt: str, repo_path: Path) -> tuple[bool, str]:
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
            cmd = ["claude", "-p", safe_prompt, "--permission-mode", "acceptEdits"]
        elif self.ai_tool_mode == "opencode":
            cmd = [
                "opencode",
                "--print-logs",
                "run",
                safe_prompt,
                "--thinking",
                "--log-level",
                "DEBUG",
            ]
        elif self.ai_tool_mode == "opencode-custom":
            if not self.ai_tool_custom_command:
                return False, "AI_TOOL_CUSTOM_COMMAND not set for opencode-custom mode"
            cmd_parts = shlex.split(self.ai_tool_custom_command)
            cmd = [
                part.replace("{prompt}", safe_prompt).replace("{cwd}", str(repo_path))
                for part in cmd_parts
            ]
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
            # Setup environment for non-interactive execution
            # Start with current env and override/add specific flags
            env = dict(os.environ)
            env.update({
                "CI": "true",
                "PYTHONUNBUFFERED": "1",
                "DEBIAN_FRONTEND": "noninteractive",
                "CLAUDECODE": "",
            })
            
            self.logger.info(
                f"Running AI tool ({self.ai_tool_mode}) with timeout {self.ai_tool_timeout}s"
            )

            process = subprocess.Popen(
                cmd,
                cwd=repo_path,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,  # Prevent interactive hangs
                text=True,
                env=env,
                bufsize=1,  # Line buffered
                preexec_fn=os.setsid,  # Create new process group for cleanup
            )

            all_output = []
            output_queue: queue.Queue = queue.Queue()

            def reader(pipe, q):
                try:
                    for line in iter(pipe.readline, ""):
                        q.put(line)
                finally:
                    pipe.close()

            thread = threading.Thread(
                target=reader, 
                args=(process.stdout, output_queue),
                name=f"AiToolReader-{process.pid}"
            )
            thread.daemon = True
            thread.start()

            start_time = time.time()
            timed_out = False
            try:
                while True:
                    try:
                        # Check for output every 100ms
                        line = output_queue.get(timeout=0.1)
                        all_output.append(line)

                        # Log to watcher log in real-time
                        stripped = line.strip()
                        if stripped:
                            self.logger.info(f"[{self.ai_tool_mode}] {stripped}")
                    except queue.Empty:
                        if process.poll() is not None:
                            # Process finished
                            break

                    # Check for timeout
                    if time.time() - start_time > self.ai_tool_timeout:
                        timed_out = True
                        break
            finally:
                # Always cleanup the process group (including orphans)
                try:
                    pgid = os.getpgid(process.pid)
                    # Use SIGTERM first
                    os.killpg(pgid, signal.SIGTERM)
                    
                    # Give it a moment (up to 2s) to exit gracefully
                    wait_start = time.time()
                    while time.time() - wait_start < 2:
                        if process.poll() is not None:
                            break
                        time.sleep(0.1)
                    
                    # If still running, use SIGKILL
                    if process.poll() is None:
                        os.killpg(pgid, signal.SIGKILL)
                except ProcessLookupError:
                    # Process or process group already gone
                    pass
                except Exception as e:
                    self.logger.error(f"Error cleaning up process group: {e}")

            # Ensure pipe is closed to unblock reader if process is gone
            try:
                process.stdout.close()
            except Exception:
                pass

            # Wait for thread to finish reading remaining output
            thread.join(timeout=2)
            if thread.is_alive():
                self.logger.warning(f"Reader thread for process {process.pid} still alive after join timeout")

            full_output = "".join(all_output)
            
            if timed_out:
                tool_name = (
                    "Claude" if self.ai_tool_mode == "direct" else self.ai_tool_mode
                )
                self.logger.error(f"AI tool ({tool_name}) timed out after {self.ai_tool_timeout}s")
                return (
                    False,
                    f"AI tool ({tool_name}) timed out after {self.ai_tool_timeout}s.\n"
                    f"Command: `{shlex.join(cmd[:3])}...` (truncated)\n\n"
                    f"--- Captured Output ---\n{full_output}",
                )

            success = process.returncode == 0
            if not success:
                self.logger.error(f"AI tool failed with return code {process.returncode}:\n{full_output}")
            
            return success, full_output
        except FileNotFoundError:
            return False, f"AI tool CLI ({cmd[0]}) not found"
        except Exception as e:
            msg = f"AI tool execution failed ({self.ai_tool_mode}): {str(e)}"
            self.logger.exception(msg)
            return False, msg

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
            f"[{project.name}] Processing issue #{issue.iid}: {sanitize_for_log(validated_title)}"
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
        try:
            self.logger.info(f"[{project.name}] Preparing repository (fetch/checkout/pull)")
            git.fetch()
            git.checkout(self.default_branch)
            git.pull()
        except Exception as e:
            self.logger.error(f"[{project.name}] Git preparation failed: {str(e)}")
            self.discord.notify_error(
                project.name,
                f"Git preparation failed on branch `{self.default_branch}` (fetch/checkout/pull)",
                details=str(e),
            )
            self.state.set_processing(project.project_id, False)
            return False

        self.logger.info(f"[{project.name}] Creating branch: {branch}")
        success, error = git.checkout(branch, create=True)
        if not success:
            self.logger.error(f"[{project.name}] Could not create branch {branch}: {error}")
            self.discord.notify_error(
                project.name,
                f"Could not create branch `{branch}`",
                details=error,
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

        # Run AI tool
        try:
            self.logger.info(f"[{project.name}] Starting AI tool for issue #{issue.iid}")
            success, output = self._run_ai_tool(prompt, project.path)
            
            if not success:
                self.logger.error(f"[{project.name}] AI tool failed for issue #{issue.iid}: {output}")
                self.discord.notify_error(
                    project.name,
                    f"AI tool failed for issue #{issue.iid}",
                    details=output,
                )
                return False

            self.logger.info(f"[{project.name}] AI tool completed successfully for issue #{issue.iid}")
            
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
            return True
        except Exception as e:
            self.logger.error(f"[{project.name}] Unexpected error during AI tool execution: {str(e)}")
            self.discord.notify_error(
                project.name,
                f"Unexpected error during AI tool execution (issue #{issue.iid})",
                details=str(e),
            )
            return False
        finally:
            self.state.set_processing(project.project_id, False)

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
        try:
            self.logger.info(f"[{project.name}] Preparing repository (fetch/checkout/pull/rebase)")
            git.fetch()
            git.checkout(mr.source_branch)
            git.pull("origin", mr.source_branch)
        except Exception as e:
            self.logger.error(f"[{project.name}] Git preparation failed: {str(e)}")
            self.discord.notify_error(
                project.name,
                f"Git preparation failed on branch `{mr.source_branch}` (fetch/checkout/pull)",
                details=str(e),
            )
            self.state.set_processing(project.project_id, False)
            return False

        # Build prompt for Claude
        prompt = f"""You are working on a merge request titled: {mr.title}
Branch: {mr.source_branch}

A reviewer left this feedback:
{comment}

Please address this feedback. Make the necessary changes and commit them.
Write commit messages in English.
Do not use conventional commit prefixes like feat:, fix:, etc.
Do not add Co-Authored-By signature to commits."""

        # Run AI tool
        try:
            self.logger.info(f"[{project.name}] Starting AI tool for merge request !{mr.iid}")
            success, output = self._run_ai_tool(prompt, project.path)
            
            if not success:
                self.logger.error(f"[{project.name}] AI tool failed for MR !{mr.iid}: {output}")
                self.discord.notify_error(
                    project.name,
                    f"AI tool failed for merge request !{mr.iid}",
                    details=output,
                )
                return False

            self.logger.info(f"[{project.name}] AI tool completed successfully for MR !{mr.iid}")
            
            # Push changes
            git.push("origin", mr.source_branch)
            self.discord.notify_changes_applied(
                project.name,
                mr.title,
                mr.web_url,
            )
            return True
        except Exception as e:
            self.logger.error(f"[{project.name}] Unexpected error during AI tool execution: {str(e)}")
            self.discord.notify_error(
                project.name,
                f"Unexpected error during AI tool execution (MR !{mr.iid})",
                details=str(e),
            )
            return False
        finally:
            self.state.set_processing(project.project_id, False)

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
