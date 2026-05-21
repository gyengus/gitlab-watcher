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
import shutil
from pathlib import Path
from typing import Any, Callable, Optional

from .config import ProjectConfig
from .constants import (
    AI_TOOL_ERROR_PATTERNS,
    DEFAULT_AI_TOOL_TIMEOUT,
    FORBIDDEN_PATTERNS,
    MAX_BRANCH_LENGTH,
    MAX_AI_LOG_SIZE,
    MAX_DESCRIPTION_LENGTH,
    MAX_DOC_CONTENT_LENGTH,
    MAX_SLUG_LENGTH,
    MAX_TITLE_LENGTH,
    MAX_TOTAL_PROMPT_LENGTH,
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
        ai_tool_custom_command: str | list[str] = "",
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

    def _validate_ai_binary(self, binary: str) -> str:
        """Validate that the AI tool binary exists and is allowed."""
        # Allow specific known binaries
        ALLOWED_BINARIES = {"ollama", "claude", "opencode", "opencode-run"}
        
        # Extract just the filename if it's a path
        binary_path = Path(binary)
        binary_name = binary_path.name
        
        # If it's an absolute path, ensure it's not in a sensitive system directory
        if binary_path.is_absolute():
            sensitive_dirs = ["/tmp", "/var/tmp", "/dev/shm"]
            if any(str(binary_path).startswith(sd) for sd in sensitive_dirs):
                raise ValueError(f"AI tool binary located in sensitive directory: {binary}")
            if not os.access(binary, os.X_OK):
                raise ValueError(f"AI tool binary is not executable: {binary}")
            return binary

        if binary_name in ALLOWED_BINARIES:
            resolved = shutil.which(binary)
            if resolved:
                return resolved
            return binary
            
        # If not in allowed list, check if it exists in PATH
        resolved = shutil.which(binary)
        if not resolved:
            raise ValueError(f"AI tool binary not found or not executable: {binary}")
            
        return resolved

    def _get_instructions(self, continue_instruction: str, is_mr_comment: bool = False) -> str:
        """Generate mandatory instructions for the AI tool."""
        instructions = [
            "YOU ARE AN EXPERT SOFTWARE ENGINEER. FOLLOW THESE MANDATORY RULES:",
            "1. ADHERE STRICTLY TO THE PROJECT DOCUMENTATION (AGENTS.md, CLAUDE.md, CONTRIBUTING.md).",
            "2. BEFORE ANY COMMIT, YOU MUST RUN TESTS AND VERIFY THE BUILD (e.g., pytest, linting).",
            "3. IF TESTS FAIL, DO NOT COMMIT. FIX THE ISSUES FIRST.",
            "4. YOU MUST COMMIT YOUR CHANGES AND PUSH THE BRANCH BEFORE FINISHING.",
            "5. WRITE COMMIT MESSAGES IN ENGLISH, NO CONVENTIONAL PREFIXES (feat/fix/etc).",
            "6. DO NOT ADD CO-AUTHORED-BY SIGNATURES.",
            "7. IF YOU NEED TEMPORARY FILES, YOU MUST CREATE A SECURE, UNIQUE SUBDIRECTORY IN /tmp/ (E.G. USING python -c 'import tempfile; print(tempfile.mkdtemp())'). NEVER USE PREDICTABLE PATHS."
        ]
        if is_mr_comment:
            instructions.append("8. IF YOU HAVE SUCCESSFULLY COMPLETED THE TASK, INCLUDE `/done` AT THE VERY END OF YOUR RESPONSE.")
        instructions.append(continue_instruction)
        return "\n".join(instructions)

    def _build_ai_command(self, prompt: str, repo_path: Path, model_override: Optional[str] = None) -> list[str]:
        """Build the command list for the AI tool."""
        if self.ai_tool_mode == "ollama":
            model_to_use = model_override if model_override else "claude"
            cmd = ["ollama", "launch", model_to_use, "--", "-p", "--permission-mode", "acceptEdits", prompt]
        elif self.ai_tool_mode == "direct":
            cmd = ["claude", "-p", prompt, "--permission-mode", "acceptEdits"]
            if model_override:
                cmd.extend(["--model", model_override])
        elif self.ai_tool_mode == "opencode":
            cmd = ["opencode", "--print-logs"]
            if model_override:
                cmd.extend(["--model", model_override])
            cmd.extend(["run", prompt, "--thinking", "--log-level", "DEBUG"])
        elif self.ai_tool_mode in ["opencode-custom", "custom"]:
            if not self.ai_tool_custom_command:
                raise ValueError(f"AI_TOOL_CUSTOM_COMMAND not set for {self.ai_tool_mode} mode")
            
            if isinstance(self.ai_tool_custom_command, list):
                cmd_parts = self.ai_tool_custom_command
            else:
                cmd_parts = shlex.split(self.ai_tool_custom_command)
                
            if not cmd_parts:
                raise ValueError(f"AI_TOOL_CUSTOM_COMMAND is empty")
                
            # Validate binary
            cmd_parts[0] = self._validate_ai_binary(cmd_parts[0])
            
            model_val = model_override or ""
            
            # Substituted values for custom command.
            # We use a strict exact-match replacement for placeholders to prevent
            # injection into flags (e.g. --prompt={prompt} is now discouraged
            # in favor of passing them as separate arguments).
            cmd = []
            allowed_placeholders = {"{prompt}", "{cwd}", "{model}"}
            for part in cmd_parts:
                if part in allowed_placeholders:
                    if part == "{prompt}":
                        cmd.append(prompt)
                    elif part == "{cwd}":
                        cmd.append(str(repo_path))
                    elif part == "{model}":
                        cmd.append(model_val)
                else:
                    # Fallback to string replacement with a warning in documentation
                    # if the placeholder is embedded in a string.
                    # Note: We use shlex.quote() to ensure safety even if the string
                    # is later interpreted by a sub-shell or another tool.
                    safe_prompt = shlex.quote(prompt)
                    safe_cwd = shlex.quote(str(repo_path))
                    safe_model = shlex.quote(model_val)
                    
                    replaced = part.replace("{prompt}", safe_prompt).replace("{cwd}", safe_cwd).replace("{model}", safe_model)
                    cmd.append(replaced)
        else:
            raise ValueError(f"Unknown AI_TOOL_MODE: {self.ai_tool_mode}")
        return cmd

    def _execute_ai_subprocess(self, cmd: list[str], repo_path: Path) -> tuple[int, str, bool, bool]:
        """Execute the AI tool subprocess and capture output."""
        env = dict(os.environ)
        env.update({
            "CI": "true",
            "PYTHONUNBUFFERED": "1",
            "DEBIAN_FRONTEND": "noninteractive",
            "CLAUDECODE": "",
        })
        
        self.logger.info(f"Running AI tool ({self.ai_tool_mode}) with timeout {self.ai_tool_timeout}s")

        process = subprocess.Popen(
            cmd,
            cwd=repo_path,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            env=env,
            bufsize=1,
            start_new_session=True,
        )

        pgid = process.pid
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
                    line = output_queue.get(timeout=0.1)
                    all_output.append(line)
                    last_activity_time = time.time()
                    stripped = line.strip()
                    if stripped:
                        self.logger.info(f"[{self.ai_tool_mode}] {stripped}")
                except queue.Empty:
                    if process.poll() is not None:
                        break

                current_time = time.time()
                if current_time - start_time > self.ai_tool_timeout:
                    timed_out = True
                    break
                if current_time - last_activity_time > SILENCE_TIMEOUT:
                    silence_timed_out = True
                    break
        finally:
            try:
                os.killpg(pgid, signal.SIGTERM)
                wait_start = time.time()
                while time.time() - wait_start < 2:
                    if process.poll() is not None:
                        break
                    time.sleep(0.1)
                if process.poll() is None:
                    os.killpg(pgid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except OSError as e:
                self.logger.error(f"Error cleaning up process group {pgid}: {e}")

        try:
            process.stdout.close()
        except Exception:
            pass

        thread.join(timeout=2)
        full_output = "".join(all_output)
        return process.returncode, full_output, timed_out, silence_timed_out

    def _get_error_snippet(self, output: str, max_chars: int = 2000) -> str:
        """Extract relevant error snippet from AI tool output."""
        patterns = AI_TOOL_ERROR_PATTERNS + NO_CHANGES_ERROR_HINTS + [r"error", r"fail", r"exception", r"traceback"]
        for pattern in patterns:
            match = re.search(pattern, output, re.IGNORECASE)
            if match:
                start = max(0, match.start() - 500)
                end = min(len(output), start + max_chars)
                return f"...[Relevant snippet found at index {start}]...\n" + output[start:end] + "\n..."
        
        return f"...(truncated, showing last {max_chars} chars)...\n" + output[-max_chars:]

    def _analyze_ai_output(self, returncode: int, full_output: str, timed_out: bool, silence_timed_out: bool, cmd: list[str]) -> tuple[bool, str]:
        """Analyze AI tool output for success and error patterns."""
        tool_name = "Claude" if self.ai_tool_mode == "direct" else self.ai_tool_mode
        truncated_cmd = shlex.join(cmd[:5]) + "..." if len(cmd) > 5 else shlex.join(cmd)
        
        # Dedicated subdirectory for AI logs
        ai_log_dir = self.state.work_dir / "ai_logs"
        ai_log_dir.mkdir(exist_ok=True)

        # Cleanup old log files (older than 7 days)
        try:
            from datetime import datetime, timedelta
            for pattern in ["ai_error_*.log", "ai_failure_*.log"]:
                for f in ai_log_dir.glob(pattern):
                    if datetime.fromtimestamp(f.stat().st_ctime) < datetime.now() - timedelta(days=7):
                        f.unlink()
        except Exception as e:
            self.logger.warning(f"Failed to cleanup old AI tool log files: {e}")

        if timed_out:
            error_log_path = ai_log_dir / f"ai_error_{time.time()}.log"
            self._write_ai_log(error_log_path, full_output)
            self.logger.error(f"AI tool ({tool_name}) timed out after {self.ai_tool_timeout}s")
            return (False, f"AI tool ({tool_name}) timed out after {self.ai_tool_timeout}s.\nCommand: `{truncated_cmd}`\n\n--- Captured Output ---\n{self._get_error_snippet(full_output)}")

        if silence_timed_out:
            error_log_path = ai_log_dir / f"ai_error_{time.time()}.log"
            self._write_ai_log(error_log_path, full_output)
            self.logger.error(f"AI tool silence timeout: no output for {SILENCE_TIMEOUT}s")
            return (False, f"AI tool ({tool_name}) silence timeout: no output for {SILENCE_TIMEOUT}s.\nCommand: `{truncated_cmd}`\n\n--- Captured Output ---\n{self._get_error_snippet(full_output)}")

        success = returncode == 0
        if success and full_output:
            # TODO: This regex for filtering AI tool's internal log lines is brittle.
            # Consider if the AI tool can be configured to suppress its own logs,
            # or if a more robust/configurable filtering mechanism is needed.
            sanitized_output = "\n".join([line for line in full_output.splitlines() if not re.match(r"^(INFO|DEBUG|WARN|ERROR)\s+\d{4}-\d{2}-\d{2}T", line.strip())])
            for pattern in AI_TOOL_ERROR_PATTERNS:
                if re.search(pattern, sanitized_output, re.IGNORECASE):
                    error_lines = [line.strip() for line in sanitized_output.splitlines() if re.search(pattern, line, re.IGNORECASE)]
                    error_summary = error_lines[0] if error_lines else pattern
                    error_log_path = ai_log_dir / f"ai_error_{time.time()}.log"
                    self._write_ai_log(error_log_path, full_output)
                    self.logger.error(f"AI tool error pattern detected: exit code 0 but output contains '{pattern}'. Summary: {error_summary}. Full log: {error_log_path}")
                    return (False, f"AI tool execution failed (error pattern detected: '{pattern}')\nError summary: {error_summary}\nFull log: {error_log_path}\nExit code: {returncode}\nOutput snippet:\n{self._get_error_snippet(full_output)}")
        
        if not success:
            error_log_path = ai_log_dir / f"ai_failure_{time.time()}.log"
            self._write_ai_log(error_log_path, full_output)
            self.logger.error(f"AI tool failed (rc {returncode}). Full log: {error_log_path}")
            return (False, f"AI tool execution failed (Exit code: {returncode})\nFull log: {error_log_path}\nOutput snippet:\n{self._get_error_snippet(full_output)}")
        
        return True, full_output

    def _write_ai_log(self, path: Path, output: str) -> None:
        """Write AI tool output to a log file, with size limiting.

        Args:
            path: Path to the log file
            output: Full output string
        """
        try:
            # Check size in bytes (approximate for UTF-8)
            if len(output.encode("utf-8", errors="replace")) > MAX_AI_LOG_SIZE:
                # Truncate: keep first half and last half of the limit
                half_limit = MAX_AI_LOG_SIZE // 2
                # Note: slicing characters might not be exactly half-limit in bytes if multi-byte,
                # but it's a safe approximation for text logs.
                truncated = (
                    output[:half_limit] 
                    + "\n\n... (output truncated due to size limit) ...\n\n" 
                    + output[-half_limit:]
                )
                path.write_text(truncated, encoding="utf-8", errors="replace")
            else:
                path.write_text(output, encoding="utf-8", errors="replace")
        except Exception as e:
            self.logger.error(f"Failed to write AI log to {path}: {e}")

    def _run_ai_tool(self, prompt: str, repo_path: Path, model_override: Optional[str] = None) -> tuple[bool, str]:
        """Run AI tool CLI with a prompt based on configured mode."""
        try:
            safe_prompt = self._sanitize_prompt(prompt)
            
            # Overall prompt length safety limit
            if len(safe_prompt) > MAX_TOTAL_PROMPT_LENGTH:
                self.logger.warning(f"Combined prompt too long ({len(safe_prompt)} chars), truncating to {MAX_TOTAL_PROMPT_LENGTH}")
                truncation_message = "\n\n... (prompt truncated due to length limits)"
                # Ensure the final length including the message does not exceed the max
                safe_prompt = safe_prompt[:MAX_TOTAL_PROMPT_LENGTH - len(truncation_message)] + truncation_message

            cmd = self._build_ai_command(safe_prompt, repo_path, model_override)
            returncode, full_output, timed_out, silence_timed_out = self._execute_ai_subprocess(cmd, repo_path)
            return self._analyze_ai_output(returncode, full_output, timed_out, silence_timed_out, cmd)
        except ValueError as e:
            return False, f"Prompt validation failed: {e}"
        except FileNotFoundError:
            return False, f"AI tool CLI not found"
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
        success, output = self._run_ai_tool(prompt, repo_path, model_override=None)

        if success:
            self.logger.info("AI tool execution successful with default configuration")
            return True, output

        if not self._should_failover(output):
            self.logger.info("Error not eligible for failover, returning failure")
            self.discord.notify_error(
                "AI Tool",
                f"AI tool failed with default configuration and no failover attempted.",
                details=self._get_error_snippet(output)
            )
            return False, output

        if not self.ai_tool_failover_model:
            self.logger.info("No failover model configured, returning original failure")
            return False, output

        self.logger.info(f"Attempting failover to model: {self.ai_tool_failover_model}")

        success, output = self._run_ai_tool(prompt, repo_path, model_override=self.ai_tool_failover_model)

        if success:
            self.logger.info(f"Failover successful using model: {self.ai_tool_failover_model}")
            return True, output

        self.logger.error(f"Failover failed with model: {self.ai_tool_failover_model}")

        self.discord.notify_error(
            "AI Tool",
            f"Both default and failover models failed",
            details=f"Default model failed.\nFailover model '{self.ai_tool_failover_model}' also failed.\n\nError output:\n{self._get_error_snippet(output)}"
        )

        return False, output

    def process_issue(
        self,
        project: ProjectConfig,
        issue: Issue,
        is_retry: bool = False,
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
        slug = git.generate_slug(validated_title, max_length=MAX_SLUG_LENGTH)
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
            is_retry=is_retry,
        )

        # Add "In progress" label
        self.gitlab.update_issue_labels(
            project.project_id,
            issue.iid,
            issue.labels + [self.label_in_progress],
        )

        # Create branch
        try:
            if not is_retry:
                self.logger.info(f"[{project.name}] Preparing repository (fetch/checkout/pull)")
                git.fetch()
                git.checkout(self.default_branch)
                git.pull()
            else:
                self.logger.info(f"[{project.name}] Retrying issue: skipping default branch preparation")
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
        has_unpushed = git.has_unpushed_to_remote()
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

        prompt = f"""{self._get_instructions(continue_instruction)}

        === PROJECT DOCUMENTATION ===
        {doc_content}

        === TASK ===
        You are working on issue #{issue.iid}: {validated_title}

        Issue description:
        {description}"""

        # Run AI tool
        try:
            self.logger.info(f"[{project.name}] Starting AI tool for issue #{issue.iid}")
            success, output = self._run_ai_tool_with_failover(prompt, project.path)
            
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
            if not git.push("origin", branch, set_upstream=True):
                self.logger.error(f"[{project.name}] Failed to push changes for issue #{issue.iid}")
                self.discord.notify_error(
                    project.name,
                    f"Failed to push changes for issue #{issue.iid}",
                    details="Git push returned failure. No changes were pushed to remote.",
                )
                return False

            # Create MR, or reuse existing one if already open for this branch
            mr = None
            try:
                mr = self.gitlab.create_merge_request(
                    project.project_id,
                    source_branch=branch,
                    target_branch=self.default_branch,
                    title=issue.title,
                    description=f"{issue.description}\n\nCloses #{issue.iid}",
                )
            except GitLabAPIError as api_err:
                if api_err.status_code == 409:
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
                    self.logger.error(f"[{project.name}] GitLab API Error during MR creation/lookup: {api_err}")
                    self.discord.notify_error(
                        project.name,
                        f"GitLab API Error during MR creation/lookup (issue #{issue.iid})",
                        details=f"Status Code: {api_err.status_code}\nMessage: {api_err.message}",
                    )
                    return False

            if mr:
                if git.has_unpushed_to_remote() or git.has_uncommitted_changes():
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
                    self.logger.info(f"[{project.name}] AI tool made no changes for issue #{issue.iid} - moving to Review with AI-No-Changes label")
                    self.gitlab.update_issue_labels(
                        project.project_id,
                        issue.iid,
                        [self.label_review, "AI-No-Changes"],
                    )
                    self.discord.notify_no_changes_needed(
                        project.name,
                        issue.title,
                        mr.web_url,
                    )
            else:
                self.discord.notify_error(
                    project.name,
                    f"MR creation failed for issue #{issue.iid}",
                )
                return False
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
        """Read relevant project documentation files (CLAUDE.md, ARCHITECTURE.md, etc)."""
        combined_content = ""
        doc_files = ["CLAUDE.md", "CONTRIBUTING.md", "ARCHITECTURE.md", "README.md"]
        
        for doc_file in doc_files:
            p = repo_path / doc_file
            if p.exists():
                try:
                    content = p.read_text(encoding="utf-8")
                    combined_content += f"--- {doc_file} ---\n{content}\n\n"
                except Exception as e:
                    self.logger.warning(f"Failed to read {doc_file}: {e}")
        
        if len(combined_content) > MAX_DOC_CONTENT_LENGTH:

            self.logger.warning(f"Project documentation content too long ({len(combined_content)} chars), truncating to {MAX_DOC_CONTENT_LENGTH}")
            combined_content = combined_content[:MAX_DOC_CONTENT_LENGTH] + "\n\n...(documentation truncated due to length limits)"
            
        return combined_content

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
        self.gitlab.create_note_award_emoji(project.project_id, mr.iid, note_id, "eyes", discussion_id=discussion_id)

        # Switch to MR branch
        try:
            self.logger.info(f"[{project.name}] Preparing repository (fetch/checkout/pull/rebase)")
            git.fetch()
            git.checkout(mr.source_branch)
            git.pull("origin", mr.source_branch)
        except Exception as e:
            self.logger.error(f"[{project.name}] Git preparation failed: {str(e)}")
            self.gitlab.create_note_award_emoji(project.project_id, mr.iid, note_id, "x", discussion_id=discussion_id)
            self.discord.notify_error(
                project.name,
                f"Git preparation failed on branch `{mr.source_branch}` (fetch/checkout/pull)",
                details=str(e),
            )
            self.state.set_processing(project.project_id, False)
            return False

        # Build prompt for Claude
        continue_instruction = ""
        has_unpushed = git.has_unpushed_to_remote()
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

        prompt = f"""{self._get_instructions(continue_instruction, is_mr_comment=True)}

        === PROJECT DOCUMENTATION ===
        {doc_content}

        === TASK ===
        You are working on a merge request titled: {mr.title}
        Branch: {mr.source_branch}

        A reviewer left this feedback:
        {comment}"""

        # Snapshot HEAD so we can detect whether the LLM made any new commits
        pre_ai_commit = git.get_current_commit()

        # Run AI tool
        try:
            self.logger.info(f"[{project.name}] Starting AI tool for merge request !{mr.iid}")
            success, output = self._run_ai_tool_with_failover(prompt, project.path)
            
            if not success:
                self.logger.error(f"[{project.name}] AI tool failed for MR !{mr.iid}: {output}")
                self.gitlab.create_note_award_emoji(project.project_id, mr.iid, note_id, "x", discussion_id=discussion_id)
                self.discord.notify_error(
                    project.name,
                    f"AI tool failed for merge request !{mr.iid}",
                    details=output,
                )
                return False

            self.logger.info(f"[{project.name}] AI tool completed successfully for MR !{mr.iid}")

            # Cleanup processing emoji if it was set
            self.gitlab.delete_note_award_emoji(project.project_id, mr.iid, note_id, "eyes")

            # Determine whether the LLM actually produced any work
            post_ai_commit = git.get_current_commit()
            has_new_commits = post_ai_commit and post_ai_commit != pre_ai_commit
            has_uncommitted = git.has_uncommitted_changes()
            llm_made_changes = has_new_commits or has_uncommitted
            has_done_command = "/done" in output

            if not llm_made_changes and not has_done_command:
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
                        project.project_id, mr.iid, note_id, "x", discussion_id=discussion_id
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

            # If the LLM already pushed, we skip git.push
            if not git.has_unpushed_to_remote():
                self.logger.info(f"[{project.name}] No unpushed work found for MR !{mr.iid} - assuming already pushed by AI tool.")
            else:
                # Push changes
                if not git.push("origin", mr.source_branch):
                    self.logger.error(f"[{project.name}] Failed to push changes for MR !{mr.iid}")
                    self.gitlab.create_note_award_emoji(project.project_id, mr.iid, note_id, "x", discussion_id=discussion_id)
                    self.discord.notify_error(
                        project.name,
                        f"Failed to push changes for MR !{mr.iid}",
                    )
                    return False

            success = self.gitlab.create_note_award_emoji(
                project.project_id, 
                mr.iid,
                note_id, 
                "white_check_mark",
                discussion_id=discussion_id
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
        except GitLabAPIError as api_err:
            self.logger.error(f"[{project.name}] GitLab API Error during comment processing: {api_err}")
            self.gitlab.create_note_award_emoji(project.project_id, mr.iid, note_id, "x", discussion_id=discussion_id)
            self.discord.notify_error(
                project.name,
                f"GitLab API Error during comment processing (MR !{mr.iid})",
                details=f"Status Code: {api_err.status_code}\nMessage: {api_err.message}",
            )
            return False
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
    "MAX_TITLE_LENGTH",
    "MAX_DESCRIPTION_LENGTH",
    "MAX_SLUG_LENGTH",
    "MAX_BRANCH_LENGTH",
    "CLAUDE_CLI_TIMEOUT_SECONDS",
    "AI_TOOL_ERROR_PATTERNS",
    "SILENCE_TIMEOUT",
]
