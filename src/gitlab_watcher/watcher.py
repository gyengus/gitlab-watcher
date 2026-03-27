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
        log_level = logging.DEBUG if verbose else logging.INFO
        logging.basicConfig(
            level=log_level,
            format="%(asctime)s [%(levelname)s] %(message)s",
        )
        self.logger = logging.getLogger(__name__)

        # Add sensitive data filter to prevent token leakage in logs
        sensitive_filter = SensitiveDataFilter()
        self.logger.addFilter(sensitive_filter)
        # Also add to root logger for comprehensive coverage
        logging.getLogger().addFilter(sensitive_filter)

        # Create work directory
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

        if not gitlab_url or not gitlab_token:
            raise ValueError("GitLab URL and token must be set in config or git remote")

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
        url_match = re.match(r"https?://([^@]+@)?([^/]+)", remote_url)
        if not url_match:
            return None, None

        host = url_match.group(2)
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
            author_username=self.config.gitlab_username,
        )

        if not mrs:
            return

        mr = mrs[0]  # Get first open MR

        # Get latest comments
        notes = self.gitlab.get_notes(project.project_id, mr.iid)
        latest_note = notes[0] if notes else None

        # Save the old note_id BEFORE updating state
        old_note_id = state.last_note_id

        # Update state
        self.state.update_mr_state(
            project.project_id,
            mr.iid,
            mr.state,
            latest_note.id if latest_note else 0,
            mr.source_branch,
        )

        # Check for new comments (not by Claude)
        if (
            latest_note
            and latest_note.id != old_note_id
            and latest_note.author_username != self.config.gitlab_username
        ):
            self._log(
                project.project_id,
                f"New comment on MR !{mr.iid}: {sanitize_for_log(latest_note.body)}",
            )
            self.state.set_processing(project.project_id, True)
            self.processor.process_comment(project, mr, latest_note.body)

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
            self.state.force_save_all()


__all__ = ["Watcher"]
