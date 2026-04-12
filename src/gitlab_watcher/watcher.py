"""Main watcher loop."""

import logging
import re
import time
from pathlib import Path
from typing import Optional

from .config import DEFAULT_CONFIG_PATH, Config, ProjectConfig, load_config
from .discord import DiscordWebhook
from .exceptions import GitLabError
from .git_ops import GitOps
from .gitlab_client import GitLabClient
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
        log_format = "%(asctime)s [%(levelname)s] %(message)s"
        
        # Only configure basic logging if not already configured (avoids issues in tests)
        if not logging.getLogger().handlers:
            logging.basicConfig(
                level=log_level,
                format=log_format,
            )
        
        self.logger = logging.getLogger("gitlab_watcher")
        self.logger.setLevel(log_level)
        self.logger.propagate = True # Allow root logger to catch if configured

        # Setup file logging with fallback
        self._log_handlers: list[logging.Handler] = []
        log_path = Path(self.config.log_file)
        handler_path = None

        try:
            # Ensure the directory exists
            log_path.parent.mkdir(parents=True, exist_ok=True)
            # Check if file is writable (or can be created)
            with open(log_path, "a"):
                pass
            handler_path = log_path
        except (PermissionError, OSError) as e:
            # Fallback to work directory in /tmp
            fallback_dir = Path("/tmp/gitlab-watcher")
            fallback_dir.mkdir(parents=True, exist_ok=True)
            fallback_path = fallback_dir / "watcher.log"
            try:
                with open(fallback_path, "a"):
                    pass
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
        gitlab_token = self.config.gitlab_token

        # Try to extract from git remote if not in config
        if not gitlab_url or not gitlab_token:
            first_project = self.config.projects[0]
            gitlab_url, gitlab_token = self._extract_from_remote(first_project.path)

        if not gitlab_url:
            raise ValueError("GitLab URL must be set in config or extractable from git remote")
        if not gitlab_token:
            raise ValueError(
                f"GitLab token not found for {gitlab_url}. "
                "If using SSH remotes, please provide the 'gitlab_token' in your configuration file."
            )

        # Initialize or use injected dependencies
        self.gitlab = gitlab or GitLabClient(url=gitlab_url, token=gitlab_token)
        self.discord = discord or DiscordWebhook(
            webhook_url=self.config.discord_webhook
        )
        self.processor = processor or Processor(
            gitlab=self.gitlab,
            discord=self.discord,
            state=self.state,
            gitlab_username=self.config.gitlab_username,
            label_in_progress=self.config.label_in_progress,
            label_review=self.config.label_review,
            ai_tool_mode=self.config.ai_tool_mode,
            ai_tool_custom_command=self.config.ai_tool_custom_command,
            ai_tool_timeout=self.config.ai_tool_timeout,
            default_branch=self.config.default_branch,
        )

    def _extract_from_remote(self, repo_path: Path) -> tuple[str | None, str | None]:
        """Extract GitLab URL and token from git remote.

        Args:
            repo_path: Path to git repository

        Returns:
            Tuple of (url, token) or (None, None) if not found
        """
        git = GitOps(repo_path)
        remote_url = git.get_remote_url()

        if not remote_url:
            return None, None

        # Parse URL like: https://token@git.example.com/...
        # or: https://user:token@git.example.com/...

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

        url = f"https://{host}"

        # Extract token
        token_match = re.match(r"https?://[^:]*:([^@]+)@", remote_url)
        if token_match:
            token = token_match.group(1)
        else:
            # Try format: https://token@host
            token_match = re.match(r"https?://([^@]+)@", remote_url)
            token = token_match.group(1) if token_match else None

        return url, token

    def _log(self, project_id: int, message: str) -> None:
        """Log a message for a project."""
        project_name = next(
            (p.name for p in self.config.projects if p.project_id == project_id),
            str(project_id),
        )
        self.logger.info("[%s] %s", project_name, message)

    def check_issues(self, project: ProjectConfig) -> None:
        """Check for new issues to process."""
        if self.state.is_processing(project.project_id):
            return

        issues = self.gitlab.get_issues(
            project_id=project.project_id,
            state="opened",
            assignee_username=self.config.gitlab_username,
        )

        if not issues:
            return

        # Find first issue without workflow labels (backlog)
        for issue in issues:
            has_in_progress = self.config.label_in_progress in issue.labels
            has_review = self.config.label_review in issue.labels

            if not has_in_progress and not has_review:
                self._log(
                    project.project_id,
                    f"Found backlog issue #{issue.iid}: {sanitize_for_log(issue.title)}",
                )
                self.state.set_processing(project.project_id, True)
                self.processor.process_issue(project, issue)
                break

    def check_mr_status(self, project: ProjectConfig) -> None:
        """Check MR status for comments and merge cleanup."""
        if self.state.is_processing(project.project_id):
            return

        self.logger.debug(f"[{project.name}] Checking for open MRs and comments...")
        state = self.state.load(project.project_id)


        # Check for merge cleanup BEFORE checking for open MRs
        if state.last_mr_iid is not None:
            mr = self.gitlab.get_merge_request(project.project_id, state.last_mr_iid)

            if mr and mr.state == "merged":
                self._log(project.project_id, f"MR !{state.last_mr_iid} was merged")

                self.processor.cleanup_after_merge(
                    project=project,
                    branch=state.last_branch or "",
                    mr_title=mr.title,
                    mr_url=mr.web_url,
                )
                return

        # Check for open MRs
        mrs = self.gitlab.get_merge_requests(
            project_id=project.project_id,
            state="opened",
        )

        if not mrs:
            return

        mr = mrs[0]  # Get first open MR

        # Get comments and sort them locally to be sure (oldest first)
        notes = self.gitlab.get_notes(project.project_id, mr.iid)
        notes = sorted(notes, key=lambda n: n.id)
        
        # Save the old note_id
        old_note_id = state.last_note_id

        # Find and process the FIRST new valid comment
        for note in notes:
            if note.id <= old_note_id:
                continue

            # NEW: Check for emojis to avoid re-processing or ID shielding
            if "eyes" in note.award_emojis or "white_check_mark" in note.award_emojis:
                # Still update state to the latest processed/skipped ID
                self.state.update_mr_state(project.project_id, mr.iid, mr.state, note.id, mr.source_branch)
                continue

            # Found the FIRST new valid human comment
            self._log(
                project.project_id,
                f"New comment on MR !{mr.iid}: {sanitize_for_log(note.body)}",
            )
            
            # Put EYE emoji to show we saw it
            self.gitlab.create_note_award_emoji(project.project_id, mr.iid, note.id, "eyes")
            
            # Update state BEFORE processing (to lock this note and the MR)
            self.state.update_mr_state(
                project.project_id,
                mr.iid,
                mr.state,
                note.id,
                mr.source_branch,
            )
            self.state.set_processing(project.project_id, True)
            
            # Process the comment and RETURN (process only ONE comment per poll cycle)
            self.processor.process_comment(project, mr, note.id, note.body)
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
