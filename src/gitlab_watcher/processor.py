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

AI_TOOL_ERROR_PATTERNS = [
    r"Forbidden",
    r"AI_APICallError",
    r"Authentication failed",
    r"Unauthorized",
    r"Permission denied",
    r"Access denied",
    r"Invalid credentials",
    r"Token expired",
    r"Rate limit exceeded",
    r"Quota exceeded",
    # Provider-specific errors that should trigger failover
    r"Provider returned error",
    r"Service unavailable",
    r"Gateway timeout",
    r"Bad gateway",
    r"Internal server error",
    r"Model not found",
    r"Model overloaded",
    r"Too many requests",
    r"Request timeout",
    r"Connection error",
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
        ai_tool_failover_model: str = "",
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
            ai_tool_failover_model: Optional failover model name for AI tool
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
        self.ai_tool_failover_model = ai_tool_failover_model
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

    def _run_ai_tool(self, prompt: str, repo_path: Path, model: str = "") -> tuple[bool, str]:
        """Run AI tool CLI with a prompt based on configured mode.

        Args:
            prompt: The prompt for AI tool
            repo_path: Path to the repository
            model: Optional model name to use (overrides default)

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
            # Ollama mode doesn't support model switching via command line easily
            # The model is specified in the "launch" command
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
            if model:
                cmd = [
                    "opencode",
                    "--print-logs",
                    "--model",
                    model,
                    "run",
                    safe_prompt,
                    "--thinking",
                    "--log-level",
                    "DEBUG",
                ]
            else:
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
                part.replace("{prompt}", safe_prompt)
                     .replace("{cwd}", str(repo_path))
                     .replace("{model}", model)
                for part in cmd_parts
            ]
        elif self.ai_tool_mode == "custom":
            if not self.ai_tool_custom_command:
                return False, "AI_TOOL_CUSTOM_COMMAND not set for custom mode"
            # Split first, then substitute to preserve multi-word values
            cmd_parts = shlex.split(self.ai_tool_custom_command)
            cmd = [
                part.replace("{prompt}", safe_prompt)
                     .replace("{cwd}", str(repo_path))
                     .replace("{model}", model)
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
                + (f" and model '{model}'" if model else "")
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
                self.logger.error(f"AI TOOL TIMEOUT: {tool_name} timed out after {self.ai_tool_timeout}s")
                # Notify Discord about the timeout – the watcher will retry later if applicable
                self.discord.notify_error(
                    "AI Tool",
                    f"AI tool ({tool_name}) timed out after {self.ai_tool_timeout}s.",
                    details="The AI process exceeded the allowed timeout. It will be retried on the next run.",
                )
                return (
                    False,
                    f"AI tool ({tool_name}) timed out after {self.ai_tool_timeout}s.\n"
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
                            f"AI TOOL ERROR PATTERN DETECTED: Exit code 0 but output contains '{pattern}'"
                        )
                        success = False
                        # Enhance error message with context
                        full_output = (
                            f"AI tool execution failed (error pattern detected: '{pattern}')\n"
                            f"Exit code: {process.returncode}\n"
                            f"Output:\n{full_output}"
                        )
                        break
            
            if not success:
                self.logger.error(f"AI TOOL EXECUTION FAILED with return code {process.returncode}:\n{full_output}")
            
            return success, full_output
        except FileNotFoundError:
            return False, f"AI TOOL NOT FOUND: CLI ({cmd[0]}) not found"
        except Exception as e:
            msg = f"AI TOOL EXECUTION ERROR ({self.ai_tool_mode}): {str(e)}"
            self.logger.exception(msg)
            return False, msg

    def _should_failover(self, error_output: str) -> bool:
        """Check if an error output indicates a failover should be attempted.
        
        Args:
            error_output: The error output from AI tool
            
        Returns:
            True if failover should be attempted, False otherwise
        """
        # Check for provider errors that indicate service issues
        for pattern in AI_TOOL_ERROR_PATTERNS:
            if re.search(pattern, error_output, re.IGNORECASE):
                self.logger.info(f"Failover triggered: error matches pattern '{pattern}'")
                return True
        
        # Special check for 524 errors (Provider returned error)
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
        # Try with default model first
        self.logger.info("Attempting AI tool execution with default configuration")
        success, output = self._run_ai_tool(prompt, repo_path, "")
        
        if success:
            self.logger.info("AI tool execution successful with default configuration")
            return True, output
        
        # Check if this error warrants a failover attempt
        if not self._should_failover(output):
            self.logger.info("Error not eligible for failover, returning failure")
            return False, output
        
        # Check if we have a failover model configured
        if not self.ai_tool_failover_model:
            self.logger.info("No failover model configured, returning original failure")
            return False, output
        
        self.logger.info(f"Attempting failover to model: {self.ai_tool_failover_model}")
        
        # Try with failover model
        success, output = self._run_ai_tool(prompt, repo_path, self.ai_tool_failover_model)
        
        if success:
            self.logger.info(f"Failover successful using model: {self.ai_tool_failover_model}")
            return True, output
        
        # Failover also failed
        self.logger.error(f"Failover failed with model: {self.ai_tool_failover_model}")
        
        # Notify Discord about the complete failure
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
        retry_count: int = 0,
    ) -> bool:
        """Process an issue: create branch, run Claude, push, create MR.

        Args:
            project: Project configuration
            issue: The issue to process
            retry_count: Number of retries attempted (to prevent infinite loops)

        Returns:
            True if successful, False if retry is needed (but only if retry_count < MAX_RETRIES)
        """
        # Prevent infinite retry loops
        MAX_RETRIES = 3
        if retry_count >= MAX_RETRIES:
            self.logger.error(f"[{project.name}] Maximum retries ({MAX_RETRIES}) reached for issue #{issue.iid} - aborting")
            self.discord.notify_error(
                project.name,
                f"Maximum retries ({MAX_RETRIES}) reached for issue #{issue.iid}",
                details="Giving up on MR creation after multiple attempts."
            )
            self.state.set_processing(project.project_id, False)
            return True  # Return True to prevent further retries
        git = self.git_factory(project.path)

        # Validate issue title
        try:
            validated_title = self._validate_issue_title(issue.title)
        except ValueError as e:
            self.logger.error(f"INVALID ISSUE TITLE: {e}")
            self.discord.notify_error(
                project.name,
                f"Invalid issue title: {e}",
            )
            self.state.set_processing(project.project_id, False)
            return False

        # Generate and validate branch name
        slug = GitOps.generate_slug(validated_title, max_length=MAX_SLUG_LENGTH)
        branch = self._validate_branch_name(f"{issue.iid}-{slug}")

        # Prevent duplicate start if another process is already running
        if self.state.is_processing(project.project_id):
            self.logger.info(f"[{project.name}] Issue #{issue.iid} is already being processed – skipping.")
            return False

        # Mark as processing
        self.state.set_processing(project.project_id, True)

        # Check if this is a retry after a failed MR creation
        is_retry = self.state.has_branch_failed_mr(project.project_id, branch)
        
        self.logger.info(
            f"[{project.name}] Processing issue #{issue.iid}: {sanitize_for_log(validated_title)}"
            f" ({'retry' if is_retry else 'new'})"
        )
        self.logger.debug(f"[{project.name}] Creating branch: {branch}")

        self.discord.notify_issue_started(
            project.name,
            validated_title,
            issue.web_url,
            branch,
            is_retry=is_retry,
        )

        # Add "In progress" label
        self.gitlab.update_issue_labels(
            project.project_id,
            issue.iid,
            issue.labels + [self.label_in_progress],
        )

        # Prepare repository - if retrying, we already have the branch
        if is_retry and git.branch_exists(branch):
            # Retry case: branch already exists, just checkout and continue
            try:
                self.logger.info(f"[{project.name}] Checking out existing branch for retry: {branch}")
                git.checkout(branch)
                # Stash any uncommitted changes to prevent conflicts
                git._run("stash", "push", "-m", "gitlab-watcher-auto-stash", check=False)
            except Exception as e:
                self.logger.error(f"[{project.name}] CHECKOUT FAILED for existing branch {branch}: {str(e)}")
                self.discord.notify_error(
                    project.name,
                    f"Could not checkout existing branch `{branch}`",
                    details=str(e),
                )
                self.state.set_processing(project.project_id, False)
                return False
        else:
            # New issue: prepare fresh checkout from default branch
            try:
                self.logger.info(f"[{project.name}] Preparing repository (fetch/checkout/pull)")
                git.fetch()
                git.checkout(self.default_branch)
                git.pull()
            except Exception as e:
                self.logger.error(f"[{project.name}] GIT PREPARATION FAILED: {str(e)}")
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
            self.logger.error(f"[{project.name}] BRANCH CREATION FAILED for {branch}: {error}")
            self.discord.notify_error(
                project.name,
                f"Could not create branch `{branch}`",
                details=error,
            )
            self.state.set_processing(project.project_id, False)
            return False

        # Check for previous work on this branch (e.g. from a timed-out run)
        continue_instruction = ""
        if is_retry:
            # Retry case: the branch already exists from a previous attempt.
            # Tell the AI to check current state - it may already be done.
            continue_instruction = (
                "\n\nNote: This is a continuation of a previous attempt. "
                "The branch already exists. Please check the current state with git log and git status. "
                "If the task is already completed and committed, simply exit. "
                "If there is unfinished work, continue from where it was left off."
            )
        elif git.has_unpushed_work(branch):
            continue_instruction = (
                "\n\nNote: This branch already has previous work (commits exist). "
                "Please review the current state of the code with git log and git diff, "
                "then continue from where the previous work left off. Do not start over."
            )

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
Do not add Co-Authored-By signature to commits.{continue_instruction}"""

        # Run AI tool
        try:
            self.logger.info(f"[{project.name}] Starting AI tool for issue #{issue.iid}")
            success, output = self._run_ai_tool_with_failover(prompt, project.path)
            
            if not success:
                self.logger.error(f"[{project.name}] AI TOOL FAILED for issue #{issue.iid}. Error details: {output}")
                self.discord.notify_error(
                    project.name,
                    f"AI tool failed for issue #{issue.iid}",
                    details=output,
                )
                return False

            self.logger.info(f"[{project.name}] AI tool completed successfully for issue #{issue.iid}")
            
            # Push branch
            if not git.push("origin", branch, set_upstream=True):
                self.logger.error(f"[{project.name}] GIT PUSH FAILED for branch {branch}")
                # Notify Discord about push failure
                self.discord.notify_error(
                    project.name,
                    f"Could not push branch `{branch}`",
                    details="Git push returned failure. No changes were pushed to remote.",
                )
                return False

            # Create MR
            mr = self.gitlab.create_merge_request(
                project.project_id,
                source_branch=branch,
                target_branch=self.default_branch,
                title=issue.title,
                description=f"{issue.description}\n\nCloses #{issue.iid}",
            )

            if mr:
                # Track the MR we just created so the watcher knows it's ours
                self.state.add_tracked_mr(project.project_id, mr.iid, mr.source_branch, created_by_watcher=True)
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
                # MR creation failed - get error details from GitLab API
                try:
                    # Try to get the latest error from GitLab API
                    recent_mrs = self.gitlab.get_merge_requests(
                        project_id=project.project_id,
                        source_branch=branch,
                        state="opened",
                    )
                    error_detail = ""
                    if recent_mrs:
                        # Find MR with matching source branch
                        matching_mr = next((mr for mr in recent_mrs if mr.source_branch == branch), None)
                        if matching_mr and matching_mr.description:
                            error_detail = matching_mr.description
                        else:
                            error_detail = "No specific error details available from GitLab API"
                    else:
                        error_detail = "No merge requests found for this branch"
                except Exception as e:
                    error_detail = f"Failed to get error details from GitLab: {str(e)}"
                
                # Mark branch as having failed MR creation for immediate retry
                self.state.mark_branch_failed_mr(project.project_id, branch)
                
                # Send detailed error notification
                self.discord.notify_error(
                    project.name,
                    "Changes done but MR creation failed - retrying immediately",
                    details=f"Branch: `{branch}`\n\nGitLab Error Details:\n{error_detail}\n\nThe watcher will immediately attempt to retry MR creation only."
                )
                
                # Return special indicator that only MR creation needs retry
                self.logger.warning(f"[{project.name}] MR creation failed for issue #{issue.iid}, returning special MR retry indicator")
                return "MR_RETRY_NEEDED"
            return True
        except Exception as e:
            self.logger.error(f"[{project.name}] UNEXPECTED ERROR during AI tool execution for issue #{issue.iid}: {str(e)}")
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
            self.discord.notify_error(
                project.name,
                f"Git preparation failed on branch `{mr.source_branch}` (fetch/checkout/pull)",
                details=str(e),
            )
            self.state.set_processing(project.project_id, False)
            return False

        # Build prompt for Claude
        continue_instruction = ""
        if git.has_unpushed_work(self.default_branch):
            continue_instruction = (

                "\n\nNote: This branch already has previous work (commits exist). "
                "Please review the current state of the code with git log and git diff, "
                "then continue from where the previous work left off. Do not start over."
            )

        prompt = f"""You are working on a merge request titled: {mr.title}
Branch: {mr.source_branch}

A reviewer left this feedback:
{comment}

Please address this feedback. Make the necessary changes and commit them.
Write commit messages in English.
Do not use conventional commit prefixes like feat:, fix:, etc.
Do not add Co-Authored-By signature to commits.{continue_instruction}"""

        # Run AI tool
        try:
            self.logger.info(f"[{project.name}] Starting AI tool for merge request !{mr.iid}")
            success, output = self._run_ai_tool_with_failover(prompt, project.path)
            
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
            
            # Push changes
            if not git.push("origin", mr.source_branch):
                self.logger.error(f"[{project.name}] Failed to push changes to MR !{mr.iid}")
                self.gitlab.create_note_award_emoji(project.project_id, mr.iid, note_id, "x")
                # Notify Discord about push failure with details
                self.discord.notify_error(
                    project.name,
                    f"Failed to push changes to merge request !{mr.iid}",
                    details="Git push returned failure. No changes were pushed to remote.",
                )
                return False

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

    def retry_mr_creation_only(
        self,
        project: ProjectConfig,
        issue: Issue,
        branch: str,
    ) -> bool:
        """Retry only MR creation for an issue that already has commits on a branch.
        
        Args:
            project: Project configuration
            issue: The issue to create MR for
            branch: The branch name that already exists with commits
            
        Returns:
            True if MR creation successful, False if retry should be attempted later
        """
        self.logger.info(f"[{project.name}] Retrying MR creation for issue #{issue.iid} on branch {branch}")
        
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
                # Track the MR we just created so the watcher knows it's ours
                self.state.add_tracked_mr(project.project_id, mr.iid, mr.source_branch, created_by_watcher=True)
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
                self.logger.info(f"[{project.name}] MR creation retry successful for issue #{issue.iid}")
                return True
            else:
                # MR creation failed again - mark it as failed for next cycle
                self.state.mark_branch_failed_mr(project.project_id, branch)
                self.logger.error(f"[{project.name}] MR creation retry failed for issue #{issue.iid}")
                return False
                
        except Exception as e:
            self.logger.error(f"[{project.name}] Exception during MR creation retry: {str(e)}")
            self.discord.notify_error(
                project.name,
                "Exception during MR creation retry",
                details=str(e),
            )
            return False


__all__ = [
    "Processor",
    "MAX_PROMPT_LENGTH",
    "MAX_TITLE_LENGTH",
    "MAX_DESCRIPTION_LENGTH",
    "MAX_SLUG_LENGTH",
    "MAX_BRANCH_LENGTH",
    "CLAUDE_CLI_TIMEOUT_SECONDS",
    "AI_TOOL_ERROR_PATTERNS",
    "retry_mr_creation_only",
]
