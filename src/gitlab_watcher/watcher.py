"""Main watcher loop."""

import logging
import re
import time
import os
import sys
import fcntl
import urllib.parse
from pathlib import Path
from typing import Any, Optional

from .config import DEFAULT_CONFIG_PATH, Config, ProjectConfig, load_config
from .constants import (
    POSITIVE_REVIEW_PATTERNS,
)
from .discord import DiscordWebhook
from .exceptions import GitLabError
from .git_ops import GitOps
from .gitlab_client import GitLabClient, Issue, MergeRequest
from .logging_utils import SensitiveDataFilter, sanitize_for_log
from .processor import Processor
from .state import StateManager


class Watcher:
    """Main watcher class that monitors GitLab projects."""

    def __init__(
        self,
        config_path: str = DEFAULT_CONFIG_PATH,
        verbose: bool = False,
        *,
        gitlab: Optional[GitLabClient] = None,
        discord: Optional[DiscordWebhook] = None,
        processor: Optional[Processor] = None,
        state: Optional[StateManager] = None,
        disable_lock: bool = False,
    ) -> None:
        """Initialize watcher.

        Args:
            config_path: Path to configuration file
            verbose: Enable verbose logging
            gitlab: Optional GitLab client (for testing)
            discord: Optional Discord webhook (for testing)
            processor: Optional processor (for testing)
            state: Optional state manager (for testing)
        """
        self.config = load_config(config_path)
        self.verbose = verbose

        # Setup logging
        if verbose:
            log_level = logging.DEBUG
        else:
            level_map = {"DEBUG": logging.DEBUG, "INFO": logging.INFO, "WARNING": logging.WARNING, "ERROR": logging.ERROR, "CRITICAL": logging.CRITICAL}
            log_level = level_map.get(self.config.log_level, logging.INFO)
        log_format = "%(asctime)s [%(process)d] [%(levelname)s] %(message)s"
        
        # Only configure basic logging if not already configured (avoids issues in tests)
        if not logging.getLogger().handlers:
            logging.basicConfig(
                level=log_level,
                format=log_format,
            )
        
        self.logger = logging.getLogger("gitlab_watcher")
        self.logger.setLevel(log_level)
        self.logger.propagate = False # Prevent messages from propagating to root logger

        # Setup file logging with fallback
        self._log_handlers: list[logging.Handler] = []
        log_path = Path(self.config.log_file)
        handler_path = None

        try:
            # Ensure the directory exists
            log_path.parent.mkdir(parents=True, exist_ok=True)
            
            # Security: Use os.open with O_NOFOLLOW to prevent symlink attacks.
            # Create file with restricted permissions (0600) if it doesn't exist.
            fd = os.open(
                log_path, 
                os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 
                0o600
            )
            # Ensure permissions are restricted even if file already existed
            try:
                os.fchmod(fd, 0o600)
            except OSError:
                # Might fail on some filesystems, but we tried
                pass
            os.close(fd)
            
            handler_path = log_path
        except (PermissionError, OSError) as e:
            # Fallback to work directory in /tmp
            fallback_dir = Path("/tmp/gitlab-watcher")
            fallback_dir.mkdir(parents=True, exist_ok=True)
            fallback_path = fallback_dir / "watcher.log"
            try:
                # Same restricted permissions for fallback with O_NOFOLLOW
                fd = os.open(
                    fallback_path, 
                    os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 
                    0o600
                )
                try:
                    os.fchmod(fd, 0o600)
                except OSError:
                    pass
                os.close(fd)
                
                handler_path = fallback_path
                self.logger.warning(
                    f"Could not use log file {log_path} ({e}). "
                    f"Falling back to {fallback_path}"
                )
            except (PermissionError, OSError) as e2:
                self.logger.error(f"Failed to setup file logging: {e2}")

        if handler_path:
            file_handler = logging.FileHandler(handler_path)
            file_handler.setFormatter(logging.Formatter(log_format))
            # Add to our specific logger instead of root logger to avoid global leak in tests
            self.logger.addHandler(file_handler)
            self._log_handlers.append(file_handler)

        # Add sensitive data filter
        self._sensitive_filter = SensitiveDataFilter()
        self.logger.addFilter(self._sensitive_filter)
        
        # In production mode (not tests), we might want to add to root logger
        # but for now let's keep it to our logger to fix the memory leak.
        # If the user really wants root coverage, they can add it once in cli.py.

        # Create work directory (for state files)
        self.work_dir = Path("/tmp/gitlab-watcher")
        self.work_dir.mkdir(parents=True, exist_ok=True)

        # Initialize or use injected state manager
        self.state = state or StateManager(self.work_dir)

        # Get GitLab credentials
        gitlab_url = self.config.gitlab_url
        gitlab_token = os.environ.get("GITLAB_TOKEN") or self.config.gitlab_token

        # Try to extract from git remote if not in config
        if not gitlab_url or not gitlab_token:
            first_project = self.config.projects[0]
            extracted_url, _ = self._extract_from_remote(first_project.path)
            if not gitlab_url:
                gitlab_url = extracted_url

        if not gitlab_url:
            raise ValueError("GitLab URL must be set in config or extractable from git remote")
        
        # Validate GitLab URL
        parsed_url = urllib.parse.urlparse(gitlab_url)
        if parsed_url.scheme not in ("https", "http"):
            raise ValueError(f"Invalid GitLab URL scheme: {parsed_url.scheme}. Only https and http are supported.")
        if not parsed_url.netloc:
            raise ValueError(f"Invalid GitLab URL: {gitlab_url}. Missing hostname.")
        
        # SSRF protection: block loopback and metadata service IPs
        forbidden_hosts = {
            "localhost", "127.0.0.1", "::1", 
            "169.254.169.254", "instance-data", # AWS/GCP/Azure metadata
            "metadata.google.internal",
        }
        hostname = parsed_url.hostname.lower() if parsed_url.hostname else ""
        if hostname in forbidden_hosts:
            raise ValueError(f"GitLab URL hostname is forbidden for security: {hostname}")
            
        if not gitlab_token:
            raise ValueError(
                "GitLab token not found. "
                "Please provide 'GITLAB_TOKEN' in your configuration file "
                "or as an environment variable."
            )
        
        # Validate GitLab Token to prevent header injection
        # Apply to the final token regardless of source (config, env, or remote)
        if not re.match(r"^[a-zA-Z0-9_\-]+$", gitlab_token):
            raise ValueError("Invalid characters in GITLAB_TOKEN. Only alphanumeric, underscores, and hyphens are allowed.")

        # Initialize or use injected dependencies
        self.gitlab = gitlab or GitLabClient(url=gitlab_url, token=gitlab_token)
        
        # Auto-detect username from GitLab API
        self.gitlab_username = self.config.gitlab_username
        try:
            # In tests, mock_gitlab might not return what we expect if not configured
            user_info = self.gitlab.get_current_user()
            if isinstance(user_info, dict) and "username" in user_info:
                self.gitlab_username = user_info["username"]
                self.logger.info(f"Auto-detected GitLab username: {self.gitlab_username}")
        except Exception as e:
            # Don't let auto-detection failure break the watcher
            self.logger.warning(f"Could not auto-detect GitLab username: {e}")

        self.discord = discord or DiscordWebhook(
            webhook_url=self.config.discord_webhook
        )
        self.processor = processor or Processor(
            gitlab=self.gitlab,
            discord=self.discord,
            state=self.state,
            gitlab_username=self.gitlab_username,
            label_in_progress=self.config.label_in_progress,
            label_review=self.config.label_review,
            ai_tool_mode=self.config.ai_tool_mode,
            ai_tool_custom_command=self.config.ai_tool_custom_command,
            ai_tool_timeout=self.config.ai_tool_timeout,
            ai_tool_failover_model=self.config.ai_tool_failover_model,
            default_branch=self.config.default_branch,
        )
        
        # In-memory deduplication for recently processed notes (solves API lag)
        self._processed_notes: set[int] = set()
        self._last_cache_clear_time = time.time()
        
        # Lock file to prevent multiple instances
        self._lock_file = None
        if not disable_lock:
            self._acquire_lock()

    def _log_project_info(self, project_id: int, level: int, message: str, *args: Any, **kwargs: Any) -> None:
        """Log a message with project context."""
        project_name = next(
            (p.name for p in self.config.projects if p.project_id == project_id),
            str(project_id),
        )
        self.logger.log(level, f"[{project_name}] {message}", *args, **kwargs)

    def _log_info(self, project_id: int, message: str, *args: Any, **kwargs: Any) -> None:
        """Log an INFO message with project context."""
        self._log_project_info(project_id, logging.INFO, message, *args, **kwargs)

    def _log_debug(self, project_id: int, message: str, *args: Any, **kwargs: Any) -> None:
        """Log a DEBUG message with project context."""
        self._log_project_info(project_id, logging.DEBUG, message, *args, **kwargs)

    def _log_warning(self, project_id: int, message: str, *args: Any, **kwargs: Any) -> None:
        """Log a WARNING message with project context."""
        self._log_project_info(project_id, logging.WARNING, message, *args, **kwargs)

    def _log_error(self, project_id: int, message: str, *args: Any, **kwargs: Any) -> None:
        """Log an ERROR message with project context."""
        self._log_project_info(project_id, logging.ERROR, message, *args, **kwargs)

    def _acquire_lock(self) -> None:
        """Acquire a file lock to prevent multiple instances from running."""
        lock_path = self.work_dir / "gitlab-watcher.lock"
        try:
            self._lock_file = open(lock_path, "w")
            fcntl.flock(self._lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._lock_file.write(str(os.getpid()))
            self._lock_file.flush()
            self.logger.debug(f"Acquired instance lock at {lock_path}")
        except (IOError, BlockingIOError):
            print(f"Error: Another instance of gitlab-watcher is already running (locked {lock_path})", file=sys.stderr)
            sys.exit(1)
        except Exception as e:
            self.logger.warning(f"Could not acquire instance lock: {e}")

    def _extract_from_remote(self, repo_path: Path) -> tuple[str | None, None]:
        """Extract GitLab URL from git remote.
        Note: Token extraction is disabled for security reasons.

        Args:
            repo_path: Path to git repository

        Returns:
            Tuple of (url, None) or (None, None) if not found
        """
        git = GitOps(repo_path)
        remote_url = git.get_remote_url()

        if not remote_url:
            return None, None

        # Extract URL
        host = None
        # Try https:// format
        url_match = re.match(r"https?://([^@]+@)?([^/:]+)", remote_url)
        if url_match:
            host = url_match.group(2)
        else:
            # Try git@host:repo or ssh://git@host[:port]/repo format
            ssh_match = re.match(r"(?:ssh://)?git@([^:/]+)", remote_url)
            if ssh_match:
                host = ssh_match.group(1)

        if not host:
            return None, None

        return f"https://{host}", None

    def _get_stuck_issue(self, project: ProjectConfig, issues: list[Issue], open_mrs: list[MergeRequest]) -> Optional[tuple[Issue, bool]]:
        """Identify issues that are either in backlog or stuck 'In progress'."""
        project_name = project.name

        # Identify which MRs are actually relevant to the issues we're looking at
        # (branch name matches "{issue_iid}-*")
        issue_iids = {issue.iid for issue in issues}
        relevant_mrs = [
            mr for mr in open_mrs 
            if any(mr.source_branch.startswith(f"{iid}-") for iid in issue_iids)
        ]

        # Pre-fetch commits for only relevant open MRs to avoid N+1 queries
        mr_commits_cache = {}
        for mr in relevant_mrs:
            try:
                mr_commits_cache[mr.iid] = self.gitlab.get_merge_request_commits(project.project_id, mr.iid)
            except Exception as e:
                self._log_warning(project.project_id, "Could not pre-fetch commits for MR !%s: %s", mr.iid, e)
                mr_commits_cache[mr.iid] = []

        for issue in issues:
            has_in_progress = self.config.label_in_progress in issue.labels
            has_review = self.config.label_review in issue.labels

            if not has_in_progress and not has_review:
                self._log_info(project.project_id, "Found backlog issue #%s: %s", issue.iid, sanitize_for_log(issue.title))
                return issue, False

            # Retry: "In progress" but no MR exists or MR is empty (likely timed out)
            if has_in_progress and not has_review:
                matching_mrs = [mr for mr in open_mrs if mr.source_branch.startswith(f"{issue.iid}-")] if open_mrs else []

                if not matching_mrs:
                    self._log_info(project.project_id, "Retrying stuck issue #%s (In progress but no MR found)", issue.iid)
                    return issue, True
                
                # Check if MR has commits from cache
                mr = matching_mrs[0]
                mr_commits = mr_commits_cache.get(mr.iid, [])
                if not mr_commits:
                    self._log_info(project.project_id, "Retrying stuck issue #%s (In progress but MR has no commits)", issue.iid)
                    return issue, True
        return None

    def check_issues(self, project: ProjectConfig) -> None:

        """Check for new issues to process."""
        if self.state.is_processing(project.project_id):
            return

        issues = self.gitlab.get_issues(
            project_id=project.project_id,
            state="opened",
            assignee_username=self.gitlab_username,
        )
        if not issues:
            return

        # Fetch open MRs to check for stuck issues
        open_mrs = self.gitlab.get_merge_requests(
            project_id=project.project_id,
            state="opened",
            author_username=self.gitlab_username,
        )

        stuck_data = self._get_stuck_issue(project, issues, open_mrs)
        if stuck_data:
            issue, is_retry = stuck_data
            self.state.set_processing(project.project_id, True)
            self.processor.process_issue(project, issue, is_retry=is_retry)

    def _handle_merge_cleanup(self, project: ProjectConfig, state: Any) -> bool:
        """Check all tracked MRs and cleanup those that are merged or closed."""
        tracked_iids = list(state.tracked_mrs.keys())
        project_name = project.name
        for iid_str in tracked_iids:
            iid = int(iid_str)
            mr = self.gitlab.get_merge_request(project.project_id, iid)

            if mr and mr.state in ["merged", "closed"]:
                action = "merged" if mr.state == "merged" else "closed"
                self._log_info(project.project_id, "MR !%s was %s", iid, action)

                mr_data = state.tracked_mrs.get(iid_str, {})
                branch = mr_data.get("branch") or ""
                created_by_watcher = mr_data.get("created_by_watcher", False)

                if not created_by_watcher:
                    self._log_info(project.project_id, "MR !%s merged/closed but not created by watcher — skipping cleanup", iid)
                    self.state.remove_tracked_mr(project.project_id, iid)
                    return True

                self.processor.cleanup_after_merge(
                    project=project,
                    branch=branch,
                    mr_title=mr.title,
                    mr_url=mr.web_url,
                    mr_iid=iid,
                )
                self.state.remove_tracked_mr(project.project_id, iid)
                return True
        return False

    def _find_and_process_comment(self, project: ProjectConfig, mr: MergeRequest) -> bool:
        """Find the first valid human comment on an MR and process it."""
        notes = self.gitlab.get_notes(project.project_id, mr.iid)
        notes = sorted(notes, key=lambda n: n.id)
        
        state = self.state.load(project.project_id)
        last_processed_id = state.last_processed_note_id or 0

        for note in notes:
            if note.system or note.author_username == self.gitlab_username:
                continue

            is_retry_request = bool(re.search(r"(?i)\bretry\b", note.body))

            # Skip already handled via persistent state, unless it's an explicit retry request
            if note.id <= last_processed_id and not is_retry_request:
                continue

            SUCCESS_EMOJIS = ["white_check_mark", "heavy_check_mark", "check", "ballot_box_with_check"]
            SKIP_EMOJIS = ["eyes", "x", "no_entry"] + SUCCESS_EMOJIS
            has_emojis = any(e in note.award_emojis for e in SKIP_EMOJIS)
            
            if not has_emojis and note.id not in self._processed_notes:
                refreshed_emojis = self.gitlab.get_note_emojis(project.project_id, mr.iid, note.id)
                has_emojis = any(e in refreshed_emojis for e in SKIP_EMOJIS)
            
            is_skipped = has_emojis or note.id in self._processed_notes
            
            if is_skipped and not is_retry_request:
                # If it's effectively skipped but we haven't updated the persistent state, do it now
                if note.id > last_processed_id:
                    self.state.update_last_processed_note(project.project_id, note.id)
                continue
            
            if is_retry_request and is_skipped:
                 self._log_info(project.project_id, "Retry request detected for note %s on MR !%s. Clearing previous status.", note.id, mr.iid)
                 self.state.set_processing(project.project_id, False)
                 self._processed_notes.discard(note.id)
                 # Explicitly remove success emojis if a retry is requested
                 self.gitlab.delete_note_award_emojis(project.project_id, mr.iid, note.id, SUCCESS_EMOJIS)
                 is_skipped = False
            
            is_positive_review = any(re.search(pattern, note.body, re.IGNORECASE) for pattern in POSITIVE_REVIEW_PATTERNS)
            if is_positive_review:
                self._log_info(project.project_id, "Comment on MR !%s indicates positive review — skipping", mr.iid)
                self._processed_notes.add(note.id)
                self.gitlab.create_note_award_emoji(project.project_id, mr.iid, note.id, "white_check_mark")
                self.state.update_last_processed_note(project.project_id, note.id)
                continue


            self._log_info(project.project_id, "New comment on MR !%s: %s", mr.iid, note.body[:100])
            self.state.set_processing(project.project_id, True)
            self._processed_notes.add(note.id)
            self.processor.process_comment(project, mr, note.id, note.body, discussion_id=note.discussion_id)
            self.state.update_mr_state(project.project_id, mr.iid, mr.state, mr.source_branch)
            self.state.update_last_processed_note(project.project_id, note.id)
            return True
        return False

    def check_mr_status(self, project: ProjectConfig) -> None:
        """Check MR status for comments and merge cleanup."""
        if self.state.is_processing(project.project_id):
            self._log_debug(project.project_id, "Project is currently processing, skipping MR check.")
            return

        # Periodically clear processed notes cache (every 24 hours)
        if time.time() - self._last_cache_clear_time > 86400:
            self.logger.info("Clearing _processed_notes cache (24h period reached)")
            self._processed_notes.clear()
            self._last_cache_clear_time = time.time()

        self._log_debug(project.project_id, "Checking for open MRs and comments...")
        state = self.state.load(project.project_id)

        if self._handle_merge_cleanup(project, state):
            return

        mrs = self.gitlab.get_merge_requests(
            project_id=project.project_id,
            state="opened",
            author_username=self.gitlab_username,
        )

        if not mrs:
            return

        for mr in mrs:
            self.state.add_tracked_mr(project.project_id, mr.iid, mr.source_branch)

        for mr in mrs:
            if self._find_and_process_comment(project, mr):
                return




    def run(self) -> None:
        """Run the main watcher loop."""
        # Print summary
        print("GitLab Watcher started")
        print(f"Monitoring {len(self.config.projects)} project(s):")
        for project in self.config.projects:
            print(f"  - {project.name} (ID: {project.project_id})")
        print(f"Logs: {self.work_dir}")

        # Initialize state for all projects (resets processing flag)
        for project in self.config.projects:
            self.state.init_state(project.project_id)

        try:
            # Main loop
            while True:
                try:
                    for project in self.config.projects:
                        self.check_mr_status(project)
                        self.check_issues(project)

                    time.sleep(self.config.poll_interval)

                except KeyboardInterrupt:
                    break
                except GitLabError as e:
                    self.logger.error(f"GitLab API Error: {sanitize_for_log(e.message)}")
                    time.sleep(self.config.poll_interval)
                except Exception as e:
                    self.logger.error(f"Error in main loop: {sanitize_for_log(str(e))}")
                    time.sleep(self.config.poll_interval)
        finally:
            # Ensure all pending state is saved before shutdown
            print("\nShutting down...")
            self.stop()

    def stop(self) -> None:
        """Stop the watcher and cleanup resources."""
        # Release lock file
        if self._lock_file:
            try:
                fcntl.flock(self._lock_file, fcntl.LOCK_UN)
                self._lock_file.close()
            except Exception:
                pass
            self._lock_file = None

        if hasattr(self, "state"):
            self.state.force_save_all()
            self.state.stop()
        
        # Remove our handlers from the logger
        if hasattr(self, "_log_handlers"):
            for handler in self._log_handlers:
                self.logger.removeHandler(handler)
                handler.close()
            self._log_handlers.clear()
        
        if hasattr(self, "_sensitive_filter"):
            self.logger.removeFilter(self._sensitive_filter)


__all__ = ["Watcher"]
