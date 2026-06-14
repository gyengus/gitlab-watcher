"""Protocol definitions for dependency injection."""

from pathlib import Path
from typing import Protocol


class GitOperations(Protocol):
    """Protocol for Git operations.

    This protocol defines the interface for Git operations,
    allowing for dependency injection and easier testing.
    """

    def fetch(self, remote: str = "origin") -> bool:
        """Fetch from remote.

        Args:
            remote: Remote name

        Returns:
            True if successful, False otherwise
        """
        ...

    def checkout(self, branch: str, create: bool = False) -> tuple[bool, str]:
        """Checkout a branch.

        Args:
            branch: Branch name
            create: Create branch if it doesn't exist

        Returns:
            Tuple of (success, error_message)
        """
        ...

    def pull(self, remote: str = "origin", branch: str | None = None) -> bool:
        """Pull from remote.

        Args:
            remote: Remote name
            branch: Branch name (optional)

        Returns:
            True if successful, False otherwise
        """
        ...

    def push(
        self,
        remote: str = "origin",
        branch: str | None = None,
        set_upstream: bool = False,
    ) -> bool:
        """Push to remote.

        Args:
            remote: Remote name
            branch: Branch name (optional, uses current branch if None)
            set_upstream: Set upstream flag

        Returns:
            True if successful, False otherwise
        """
        ...

    def delete_branch(self, branch: str, force: bool = False) -> bool:
        """Delete a branch.

        Args:
            branch: Branch name
            force: Force delete

        Returns:
            True if successful, False otherwise
        """
        ...

    def get_current_branch(self) -> str | None:
        """Get current branch name.

        Returns:
            Current branch name or None if not on a branch
        """
        ...

    def get_remote_url(self) -> str | None:
        """Get remote URL.

        Returns:
            Remote URL or None if not configured
        """
        ...

    def has_unpushed_work(self, default_branch: str) -> bool:
        """Check if current branch has commits beyond the default branch.

        Args:
            default_branch: The default branch to compare against

        Returns:
            True if there are commits ahead of the default branch
        """
        ...

    def has_unpushed_to_remote(self) -> bool:
        """Check if the current branch has commits not yet pushed to its upstream.

        Returns:
            True if there are commits ahead of the remote-tracking branch, False otherwise
        """
        ...

    def get_current_commit(self) -> str:
        """Return the current HEAD commit hash.

        Returns:
            Full SHA-1 hash of HEAD, or empty string on failure.
        """
        ...

    @staticmethod
    def generate_slug(title: str, max_length: int = 30) -> str:
        """Generate a URL-safe slug from a title.

        Args:
            title: The title to slugify
            max_length: Maximum length of the slug

        Returns:
            A URL-safe slug
        """
        ...


__all__ = ["GitOperations"]