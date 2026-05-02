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
from typing import Any, Callable, Optional

from .config import ProjectConfig
from .constants import (
    AI_TOOL_ERROR_PATTERNS,
    FORBIDDEN_PATTERNS,
    MAX_BRANCH_LENGTH,
    MAX_DESCRIPTION_LENGTH,
    MAX_SLUG_LENGTH,
    MAX_TITLE_LENGTH,
    NO_CHANGES_ERROR_HINTS,
    SILENCE_TIMEOUT,
)
from .discord import DiscordWebhook
from .exceptions import GitLabAPIError
from .git_ops import GitOps
from .gitlab_client import GitLabClient, Issue, MergeRequest, Note
from .logging_utils import SensitiveDataFilter, sanitize_for_log
from .protocols import GitOperations
from .state import StateManager

_thread_cls = threading.Thread

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
        ai_tool_failover_model: str = "",
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
            ai_tool_failover_model: Failover model name (empty string = no failover)
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
        self.ai_tool_failover_model = ai_tool_failover_model
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

            # Record PGID immediately while process is alive
            pgid = os.getpgid(process.pid)

            all_output = []
            output_queue: queue.Queue = queue.Queue()

            def reader(pipe, q):
                try:
                    for line in iter(pipe.readline, ""):
                        q.put(line)
                finally:
                    pipe.close()

            thread = _thread_cls(
                target=reader, 
                args=(process.stdout, output_queue),
                name=f"AiToolReader-{process.pid}"
            )
            thread.daemon = True
            thread.start()

            start_time = time.time()
            last_activity_time = start_time
            timed_out = False
            silence_timed_out = False
            try:
                while True:
                    try:
                        # Check for output every 100ms
                        line = output_queue.get(timeout=0.1)
                        all_output.append(line)
                        last_activity_time = time.time()

                        # Log to watcher log in real-time
                        stripped = line.strip()
                        if stripped:
                            self.logger.info(f"[{self.ai_tool_mode}] {stripped}")
                    except queue.Empty:
                        if process.poll() is not None:
                            # Process finished
                            break

                    # Check for overall timeout
                    current_time = time.time()
                    if current_time - start_time > self.ai_tool_timeout:
                        timed_out = True
                        break

                    # Check for silence timeout (no output for SILENCE_TIMEOUT seconds)
                    if current_time - last_activity_time > SILENCE_TIMEOUT:
                        silence_timed_out = True
                        break
            finally:
                # Always cleanup the process group (including orphans)
                # Using saved pgid to work even if the leader process is already dead
                try:
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
                    # Process group already gone
                    pass
                except Exception as e:
                    self.logger.error(f"Error cleaning up process group {pgid}: {e}")

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

            if silence_timed_out:
                tool_name = (
                    "Claude" if self.ai_tool_mode == "direct" else self.ai_tool_mode
                )
                self.logger.error(f"AI tool silence timeout: no output for {SILENCE_TIMEOUT}s")
                return (
                    False,
                    f"AI tool ({tool_name}) silence timeout: no output for {SILENCE_TIMEOUT}s.\n"
                    f"Command: `{shlex.join(cmd[:3])}...` (truncated)\n\n"
                    f"--- Captured Output ---\n{full_output}",
                )

            success = process.returncode == 0
            
            # Additional output inspection for error patterns
            # Some tools return exit code 0 but output error messages
            if success and full_output:
                for pattern in AI_TOOL_ERROR_PATTERNS:
                    if re.search(pattern, full_output, re.IGNORECASE):
                        self.logger.error(
                            f"AI tool error pattern detected: exit code 0 but output contains '{pattern}'"
                        )
                        success = False
                        full_output = (
                            f"AI tool execution failed (error pattern detected: '{pattern}')\n"
                            f"Exit code: {process.returncode}\n"
                            f"Output:\n{full_output}"
                        )
                        break
            
            if not success:
                self.logger.error(f"AI tool failed with return code {process.returncode}:\n{full_output}")
            
            return success, full_output
        except FileNotFoundError:
            return False, f"AI tool CLI ({cmd[0]}) not found"
        except Exception as e:
            msg = f"AI tool execution failed ({self.ai_tool_mode}): {str(e)}"
            self.logger.exception(msg)
            return False, msg

    def _should_failover(self, error_output: str) -> bool:
        """Check if an error output indicates a failover should be attempted.

        Args:
            error_output: The error output from AI tool

        Returns:
            True if failover should be attempted, False otherwise
        """
        for pattern in AI_TOOL_ERROR_PATTERNS:
            if re.search(pattern, error_output, re.IGNORECASE):
                self.logger.info(f"Failover triggered: error matches pattern '{pattern}'")
                return True

        if "524" in error_output and "Provider returned error" in error_output:
            self.logger.info("Failover triggered: 524 Provider returned error detected")
            return True

        return False

    def _run_ai_tool_with_failover(self, prompt: str, repo_path: Path) -> tuple[bool, str]:
        """Run AI tool with failover capability.

        Args:
            prompt: The prompt for AI tool
            repo_path: Path to the repository

        Returns:
            Tuple of (success, output)
        """
        self.logger.info("Attempting AI tool execution with default configuration")
        success, output = self._run_ai_tool(prompt, repo_path)

        if success:
            self.logger.info("AI tool execution successful with default configuration")
            return True, output

        if not self._should_failover(output):
            self.logger.info("Error not eligible for failover, returning failure")
            return False, output

        if not self.ai_tool_failover_model:
            self.logger.info("No failover model configured, returning original failure")
            return False, output

        self.logger.info(f"Attempting failover to model: {self.ai_tool_failover_model}")

        success, output = self._run_ai_tool(prompt, repo_path)

        if success:
            self.logger.info(f"Failover successful using model: {self.ai_tool_failover_model}")
            return True, output

        self.logger.error(f"Failover failed with model: {self.ai_tool_failover_model}")

        self.discord.notify_error(
            "AI Tool",
            f"Both default and failover models failed",
            details=f"Default model failed.\nFailover model '{self.ai_tool_failover_model}' also failed.\n\nError output:\n{output}"
        )

        return False, output

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

        # Read project documentation files
        doc_content = self._read_project_docs(project.path)

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

        # Check for previous work on this branch (e.g. from a timed-out run)
        continue_instruction = ""
        has_unpushed = git.has_unpushed_work(self.default_branch)
        has_uncommitted = git.has_uncommitted_changes()
        if has_uncommitted:
            continue_instruction = (
                "\n\nNote: This branch has uncommitted changes from a previous run. "
                "Please review them with git diff and git status, commit all changes, "
                "then push the branch and continue from where the previous work left off. "
                "Do not start over."
            )
        elif has_unpushed:
            continue_instruction = (
                "\n\nNote: This branch already has previous work (commits exist but not pushed). "
                "Please review the current state with git log and git diff, "
                "push the existing commits, then continue from where the previous work left off. "
                "Do not start over."
            )

        # Build prompt for Claude (truncate description if too long)
        description = issue.description or ""
        if len(description) > MAX_DESCRIPTION_LENGTH:
            description = description[:MAX_DESCRIPTION_LENGTH]

        prompt = f"""You are working on issue #{issue.iid}: {validated_title}

Issue description:
{description}
{doc_content}

INSTRUCTIONS:
1. Complete the task as described.
2. YOU MUST COMMIT your changes using git and PUSH the branch before finishing.
3. Write commit messages in English.
4. Do not use conventional commit prefixes (feat:, fix:, etc.).
5. Do not add Co-Authored-By signatures.
6. If you need temporary files, use /tmp/opencode/ instead of /tmp/ directly.{continue_instruction}"""

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

            # Create MR, or reuse existing one if already open for this branch
            try:
                mr = self.gitlab.create_merge_request(
                    project.project_id,
                    source_branch=branch,
                    target_branch=self.default_branch,
                    title=issue.title,
                    description=f"{issue.description}\n\nCloses #{issue.iid}",
                )
            except GitLabAPIError as api_err:
                if api_err.status_code != 409:
                    raise
                # GitLab 409: an open MR already exists for this branch.
                # This is not an error — find and reuse the existing MR.
                self.logger.info(
                    f"[{project.name}] MR already exists for branch `{branch}` (HTTP 409) "
                    "— reusing the existing open MR"
                )
                open_mrs = self.gitlab.get_merge_requests(
                    project.project_id,
                    state="opened",
                )
                mr = next((m for m in open_mrs if m.source_branch == branch), None)
                if mr is None:
                    # Defensive: 409 but we can't locate the MR — re-raise
                    raise

            if mr:
                # Only track MR if AI tool made changes
                if git.has_unpushed_work(self.default_branch) or git.has_uncommitted_changes():
                    # Track the MR we just created so the watcher knows it's ours
                    self.state.add_tracked_mr(project.project_id, mr.iid, mr.source_branch, created_by_watcher=True)
                    # Move to Review
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
                    self.logger.info(f"[{project.name}] AI tool made no changes for issue #{issue.iid} - keeping in progress")
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

    def _read_project_docs(self, repo_path: Path) -> str:
        """Read existing project documentation files and return their content."""
        doc_files = ["AGENTS.md", "CLAUDE.md", "CONTRIBUTING.md"]
        content_parts = []
        
        for filename in doc_files:
            file_path = repo_path / filename
            if file_path.exists():
                try:
                    content = file_path.read_text(encoding="utf-8")
                    content_parts.append(f"\n\n=== {filename} ===\n{content}")
                except Exception as e:
                    self.logger.warning(f"Could not read {filename}: {e}")
        
        return "".join(content_parts)

    def process_comment(
        self,
        project: ProjectConfig,
        mr: MergeRequest,
        note_id: int,
        comment: str,
        discussion_id: str = "",
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

        # Read project documentation files
        doc_content = self._read_project_docs(project.path)

        self.discord.send(
            f"🤖 **Processing Comment** [{project.name}]\n"
            f"[{mr.title}]({mr.web_url})\n\n"
            f"Starting to work on: {comment}"
        )

        # Add eyes emoji to indicate processing has started
        self.gitlab.create_note_award_emoji(project.project_id, mr.iid, note_id, "eyes")

        # Switch to MR branch
        try:
            self.logger.info(f"[{project.name}] Preparing repository (fetch/checkout/pull/rebase)")
            git.fetch()
            git.checkout(mr.source_branch)
            git.pull("origin", mr.source_branch)
        except Exception as e:
            self.logger.error(f"[{project.name}] Git preparation failed: {str(e)}")
            self.gitlab.create_note_award_emoji(project.project_id, mr.iid, note_id, "x")
            self.discord.notify_error(
                project.name,
                f"Git preparation failed on branch `{mr.source_branch}` (fetch/checkout/pull)",
                details=str(e),
            )
            self.state.set_processing(project.project_id, False)
            return False

        # Build prompt for Claude
        continue_instruction = ""
        has_unpushed = git.has_unpushed_work(self.default_branch)
        has_uncommitted = git.has_uncommitted_changes()
        if has_uncommitted:
            continue_instruction = (
                "\n\nNote: This branch has uncommitted changes from a previous run. "
                "Please review them with git diff and git status, commit all changes, "
                "then push the branch and continue from where the previous work left off. "
                "Do not start over."
            )
        elif has_unpushed:
            continue_instruction = (
                "\n\nNote: This branch already has previous work (commits exist but not pushed). "
                "Please review the current state with git log and git diff, "
                "push the existing commits, then continue from where the previous work left off. "
                "Do not start over."
            )

        prompt = f"""You are working on a merge request titled: {mr.title}
Branch: {mr.source_branch}

A reviewer left this feedback:
{comment}
{doc_content}

INSTRUCTIONS:
1. Address the feedback as described.
2. YOU MUST COMMIT your changes using git and PUSH the branch before finishing.
3. Write commit messages in English.
4. Do not use conventional commit prefixes (feat:, fix:, etc.).
5. Do not add Co-Authored-By signatures.
6. If you need temporary files, use /tmp/opencode/ instead of /tmp/ directly.{continue_instruction}"""

        # Snapshot HEAD so we can detect whether the LLM made any new commits
        pre_ai_commit = git.get_current_commit()

        # Run AI tool
        try:
            self.logger.info(f"[{project.name}] Starting AI tool for merge request !{mr.iid}")
            success, output = self._run_ai_tool(prompt, project.path)
            
            if not success:
                self.logger.error(f"[{project.name}] AI tool failed for MR !{mr.iid}: {output}")
                self.gitlab.create_note_award_emoji(project.project_id, mr.iid, note_id, "x")
                self.discord.notify_error(
                    project.name,
                    f"AI tool failed for merge request !{mr.iid}",
                    details=output,
                )
                return False

            self.logger.info(f"[{project.name}] AI tool completed successfully for MR !{mr.iid}")

            # Determine whether the LLM actually produced any work
            post_ai_commit = git.get_current_commit()
            has_new_commits = post_ai_commit and post_ai_commit != pre_ai_commit
            has_uncommitted = git.has_uncommitted_changes()
            llm_made_changes = has_new_commits or has_uncommitted

            if not llm_made_changes:
                # Distinguish: intentional no-op vs silent failure
                suspected_error_pattern = None
                for hint in NO_CHANGES_ERROR_HINTS:
                    if re.search(hint, output, re.IGNORECASE):
                        suspected_error_pattern = hint
                        break

                if suspected_error_pattern:
                    self.logger.error(
                        f"[{project.name}] AI tool made no changes for MR !{mr.iid} and output "
                        f"contains a suspected error indicator ('{suspected_error_pattern}'). "
                        "Treating as failure."
                    )
                    self.gitlab.create_note_award_emoji(
                        project.project_id, mr.iid, note_id, "x"
                    )
                    # Include a short excerpt so the team can see what went wrong
                    output_excerpt = output[-1000:] if len(output) > 1000 else output
                    self.discord.notify_error(
                        project.name,
                        f"AI tool made no changes for MR !{mr.iid} — possible silent failure",
                        details=output_excerpt,
                    )
                    return False

                # Clean output, no error hints → LLM reviewed and found nothing to do
                self.logger.info(
                    f"[{project.name}] AI tool made no changes for MR !{mr.iid} "
                    "and output contains no error indicators — treating as no-op review."
                )
                # The 'eyes' emoji added at the start of processing remains as the marker.
                # If the comment is part of a discussion thread, reply to explain.
                if discussion_id:
                    self.gitlab.create_note_reply(
                        project.project_id,
                        mr.iid,
                        discussion_id,
                        "\u2705 Reviewed. No code changes were necessary to address this feedback.",
                    )
                self.discord.notify_no_changes_needed(
                    project.name,
                    mr.title,
                    mr.web_url,
                )
                return True

            # Push changes
            git.push("origin", mr.source_branch)
            success = self.gitlab.create_note_award_emoji(
                project.project_id, 
                mr.iid,
                note_id, 
                "white_check_mark"
            )
            
            if not success and discussion_id:
                self.logger.warning(f"Failed to add emoji to note {note_id}, using fallback reply to discussion {discussion_id}.")
                self.gitlab.create_note_reply(
                    project.project_id,
                    mr.iid,
                    discussion_id,
                    "Handled by AI bot ✅"
                )
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


    def retry_mr_creation_only(
        self,
        project: ProjectConfig,
        issue: Issue,
        branch: str,
    ) -> bool:
        """Retry creating an MR for an issue that already has a branch.

        Args:
            project: Project configuration
            issue: The issue to create MR for
            branch: The branch name to create MR from

        Returns:
            True if MR was created successfully, False otherwise
        """
        try:
            # Create MR
            mr = self.gitlab.create_merge_request(
                project.project_id,
                source_branch=branch,
                target_branch=self.default_branch,
                title=issue.title,
                description=f"{issue.description}\n\nCloses #{issue.iid}",
            )

            if mr:
                # Track the MR we just created
                self.state.add_tracked_mr(
                    project.project_id, mr.iid, mr.source_branch, created_by_watcher=True
                )
                # Move to Review
                self.gitlab.update_issue_labels(
                    project.project_id,
                    issue.iid,
                    [self.label_review],
                )
                self.logger.info(f"[{project.name}] Successfully created MR !{mr.iid} for branch {branch}")
                return True
            else:
                self.logger.error(f"[{project.name}] Failed to create MR for branch {branch}")
                self.state.mark_branch_failed_mr(project.project_id, branch)
                return False
        except Exception as e:
            self.logger.error(f"[{project.name}] Error creating MR for branch {branch}: {str(e)}")
            return False

    def cleanup_after_merge(
        self,
        project: ProjectConfig,
        branch: str,
        mr_title: str,
        mr_url: str,
        mr_iid: Optional[int] = None,
    ) -> None:
        """Cleanup after MR merge: switch to default branch, delete branch.

        Args:
            project: Project configuration
            branch: The merged branch name
            mr_title: The MR title
            mr_url: The MR URL
            mr_iid: Optional MR IID for specific state cleanup
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

        # Reset specific MR state if IID is provided, otherwise we rely on the caller or reset
        if mr_iid is not None:
            self.state.remove_tracked_mr(project.project_id, mr_iid)
        else:
            # Legacy behavior: reset EVERYTHING if no IID provided
            # (though Watcher now always tries to be specific)
            self.state.reset(project.project_id)


__all__ = [
    "Processor",
    "MAX_PROMPT_LENGTH",
    "MAX_TITLE_LENGTH",
    "MAX_DESCRIPTION_LENGTH",
    "MAX_SLUG_LENGTH",
    "MAX_BRANCH_LENGTH",
    "CLAUDE_CLI_TIMEOUT_SECONDS",
    "AI_TOOL_ERROR_PATTERNS",
    "SILENCE_TIMEOUT",
]
