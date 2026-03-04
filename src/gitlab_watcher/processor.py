"""Issue and MR processing logic."""

import shlex
import subprocess
from pathlib import Path

from .config import ProjectConfig
from .discord import DiscordWebhook
from .git_ops import GitOps
from .gitlab_client import GitLabClient, Issue, MergeRequest, Note
from .state import StateManager


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
        claude_mode: str = "ollama",
        claude_custom_command: str = "",
    ) -> None:
        """Initialize processor.

        Args:
            gitlab: GitLab API client
            discord: Discord webhook client
            state: State manager
            gitlab_username: GitLab username for filtering comments
            label_in_progress: Label for in-progress issues
            label_review: Label for issues under review
            claude_mode: Claude CLI mode ("ollama", "direct", or "custom")
            claude_custom_command: Custom command for Claude CLI (used when mode is "custom")
        """
        self.gitlab = gitlab
        self.discord = discord
        self.state = state
        self.gitlab_username = gitlab_username
        self.label_in_progress = label_in_progress
        self.label_review = label_review
        self.claude_mode = claude_mode
        self.claude_custom_command = claude_custom_command

    def _run_claude(self, prompt: str, repo_path: Path) -> tuple[bool, str]:
        """Run Claude CLI with a prompt based on configured mode.

        Args:
            prompt: The prompt for Claude
            repo_path: Path to the repository

        Returns:
            Tuple of (success, output)
        """
        # Build command based on mode
        if self.claude_mode == "ollama":
            cmd = ["ollama", "launch", "claude", "--", "-p", "--permission-mode", "acceptEdits", prompt]
        elif self.claude_mode == "direct":
            cmd = ["claude", "-p", "--permission-mode", "acceptEdits", prompt]
        elif self.claude_mode == "custom":
            if not self.claude_custom_command:
                return False, "CLAUDE_CUSTOM_COMMAND not set for custom mode"
            # Split first, then substitute to preserve multi-word values
            cmd_parts = shlex.split(self.claude_custom_command)
            cmd = [part.replace("{prompt}", prompt).replace("{cwd}", str(repo_path)) for part in cmd_parts]
        else:
            return False, f"Unknown CLAUDE_MODE: {self.claude_mode}"

        try:
            env = {"CLAUDECODE": ""}
            result = subprocess.run(
                cmd,
                cwd=repo_path,
                capture_output=True,
                text=True,
                env=env,
                timeout=600,
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
        git = GitOps(project.path)

        # Generate branch name
        slug = GitOps.generate_slug(issue.title)
        branch = f"{issue.iid}-{slug}"

        self.discord.notify_issue_started(
            project.name,
            issue.title,
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
        git.checkout("master")
        git.pull()

        if not git.checkout(branch, create=True):
            self.discord.notify_error(
                project.name,
                f"Could not create branch `{branch}`",
            )
            self.state.set_processing(project.project_id, False)
            return False

        # Build prompt for Claude
        prompt = f"""You are working on issue #{issue.iid}: {issue.title}

Issue description:
{issue.description}

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
                target_branch="master",
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
        git = GitOps(project.path)

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
        """Cleanup after MR merge: switch to master, delete branch.

        Args:
            project: Project configuration
            branch: The merged branch name
            mr_title: The MR title
            mr_url: The MR URL
        """
        git = GitOps(project.path)

        self.discord.notify_mr_merged(project.name, mr_title, mr_url)

        # Switch to master and pull
        git.checkout("master")
        git.pull()

        # Delete branch
        if branch:
            git.delete_branch(branch, force=True)
            self.discord.notify_cleanup_complete(project.name, branch)

        # Reset state
        self.state.reset(project.project_id)