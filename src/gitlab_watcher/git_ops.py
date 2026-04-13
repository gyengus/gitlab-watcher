"""Git operations via subprocess."""

import subprocess
from pathlib import Path


class GitOps:
    """Git operations using subprocess."""

    def __init__(self, repo_path: Path) -> None:
        """Initialize Git operations for a repository.

        Args:
            repo_path: Path to the git repository
        """
        self.repo_path = repo_path

    def _run(self, *args: str, check: bool = True, timeout: int = 60) -> subprocess.CompletedProcess[str]:
        """Run a git command in the repository with a timeout."""
        return subprocess.run(
            ["git"] + list(args),
            cwd=self.repo_path,
            capture_output=True,
            text=True,
            check=check,
            timeout=timeout,
        )

    def fetch(self, remote: str = "origin") -> bool:
        """Fetch from remote."""
        try:
            self._run("fetch", remote)
            return True
        except subprocess.CalledProcessError:
            return False

    def checkout(self, branch: str, create: bool = False) -> tuple[bool, str]:
        """Checkout a branch, optionally creating it.

        If already on the branch, does nothing.
        If create=True and branch exists, just switches to it.
        """
        try:
            current = self.get_current_branch()
            if current == branch:
                return True, ""

            if create:
                # Try checking out normally first (if it exists)
                try:
                    self._run("checkout", branch)
                    return True, ""
                except subprocess.CalledProcessError:
                    # If normal checkout fails, try creating it
                    self._run("checkout", "-b", branch)
            else:
                self._run("checkout", branch)
            return True, ""
        except subprocess.CalledProcessError as e:
            error_msg = e.stderr.strip() if e.stderr else str(e)
            return False, error_msg

    def pull(self, remote: str = "origin", branch: str | None = None) -> bool:
        """Pull from remote."""
        try:
            if branch:
                self._run("pull", remote, branch)
            else:
                self._run("pull")
            return True
        except subprocess.CalledProcessError:
            return False

    def push(
        self,
        remote: str = "origin",
        branch: str | None = None,
        set_upstream: bool = False,
    ) -> bool:
        """Push to remote."""
        try:
            args = ["push"]
            if set_upstream and branch:
                args.extend(["-u", remote, branch])
            elif branch:
                args.extend([remote, branch])
            else:
                args.append(remote)

            self._run(*args)
            return True
        except subprocess.CalledProcessError:
            return False

    def delete_branch(self, branch: str, force: bool = False) -> bool:
        """Delete a local branch."""
        try:
            args = ["branch"]
            if force:
                args.append("-D")
            else:
                args.append("-d")
            args.append(branch)

            self._run(*args, check=False)
            return True
        except subprocess.CalledProcessError:
            return False

    def has_unpushed_work(self, default_branch: str) -> bool:
        """Check if the current branch has commits beyond the default branch.

        Args:
            default_branch: The default branch to compare against

        Returns:
            True if there are commits ahead of the default branch, False otherwise
        """
        try:
            result = self._run("log", f"{default_branch}..HEAD", "--oneline", check=False)
            return bool(result.stdout.strip())
        except Exception:
            return False

    def branch_exists(self, branch: str) -> bool:
        """Check if a branch exists locally."""
        try:
            result = self._run("rev-parse", "--verify", branch, check=False)
            return result.returncode == 0
        except Exception:
            return False

    def get_current_branch(self) -> str | None:
        """Get the current branch name."""
        try:
            result = self._run("rev-parse", "--abbrev-ref", "HEAD")
            return result.stdout.strip() or None
        except subprocess.CalledProcessError:
            return None

    def get_remote_url(self, remote: str = "origin") -> str | None:
        """Get the remote URL."""
        try:
            result = self._run("config", "--get", f"remote.{remote}.url")
            return result.stdout.strip() or None
        except subprocess.CalledProcessError:
            return None

    @staticmethod
    def generate_slug(title: str, max_length: int = 30) -> str:
        """Generate a URL-safe slug from a title.

        Args:
            title: The title to slugify
            max_length: Maximum length of the slug

        Returns:
            A URL-safe slug
        """
        slug = title.lower()
        # Replace non-alphanumeric with hyphens
        slug = "".join(c if c.isalnum() else "-" for c in slug)
        # Remove consecutive hyphens
        while "--" in slug:
            slug = slug.replace("--", "-")
        # Remove leading/trailing hyphens
        slug = slug.strip("-")
        # Truncate
        return slug[:max_length]