"""Custom exceptions for GitLab Watcher."""


class GitLabWatcherError(Exception):
    """Base exception for GitLab Watcher errors."""
    pass


class GitLabError(GitLabWatcherError):
    """Base exception for GitLab client errors."""

    def __init__(self, message: str) -> None:
        """Initialize GitLab error.

        Args:
            message: Error message
        """
        self.message = message
        super().__init__(message)


class GitLabConnectionError(GitLabError):
    """Network connection error when communicating with GitLab."""

    def __init__(self, message: str = "Failed to connect to GitLab") -> None:
        """Initialize connection error.

        Args:
            message: Error message
        """
        super().__init__(message)


class GitLabAPIError(GitLabError):
    """GitLab API returned an error response."""

    def __init__(self, status_code: int, message: str) -> None:
        """Initialize API error.

        Args:
            status_code: HTTP status code
            message: Error message
        """
        self.status_code = status_code
        super().__init__(f"GitLab API error {status_code}: {message}")


class GitLabNotFoundError(GitLabAPIError):
    """Resource not found (HTTP 404)."""

    def __init__(self, resource_type: str, resource_id: str | int) -> None:
        """Initialize not found error.

        Args:
            resource_type: Type of resource (e.g., "issue", "merge request")
            resource_id: Resource identifier
        """
        super().__init__(404, f"{resource_type} {resource_id} not found")


class GitLabRateLimitError(GitLabAPIError):
    """Rate limit exceeded (HTTP 429)."""

    def __init__(self, retry_after: int | None = None) -> None:
        """Initialize rate limit error.

        Args:
            retry_after: Seconds to wait before retrying
        """
        self.retry_after = retry_after
        message = "Rate limit exceeded"
        if retry_after:
            message += f", retry after {retry_after} seconds"
        super().__init__(429, message)


class GitLabAuthenticationError(GitLabAPIError):
    """Authentication failed (HTTP 401)."""

    def __init__(self) -> None:
        """Initialize authentication error."""
        super().__init__(401, "Authentication failed. Check your GitLab token.")


class GitLabForbiddenError(GitLabAPIError):
    """Access forbidden (HTTP 403)."""

    def __init__(self, resource: str = "resource") -> None:
        """Initialize forbidden error.

        Args:
            resource: The resource that was accessed
        """
        super().__init__(403, f"Access forbidden to {resource}")


__all__ = [
    "GitLabWatcherError",
    "GitLabError",
    "GitLabConnectionError",
    "GitLabAPIError",
    "GitLabNotFoundError",
    "GitLabRateLimitError",
    "GitLabAuthenticationError",
    "GitLabForbiddenError",
]