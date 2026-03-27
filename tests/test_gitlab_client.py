"""Tests for GitLab client."""

from unittest.mock import Mock, patch

import pytest
import requests

from gitlab_watcher.gitlab_client import GitLabClient, Issue, MergeRequest, Note
from gitlab_watcher.exceptions import (
    GitLabAPIError,
    GitLabConnectionError,
    GitLabNotFoundError,
)


@pytest.fixture
def client() -> GitLabClient:
    """Create a GitLab client for testing."""
    return GitLabClient(url="https://git.example.com", token="test-token")


class TestGitLabClient:
    """Tests for GitLab client."""

    def test_api_url(self, client: GitLabClient) -> None:
        """Test API URL construction."""
        url = client._api_url(42, "/issues")
        assert url == "https://git.example.com/api/v4/projects/42/issues"

    def test_api_url_trailing_slash(self) -> None:
        """Test that trailing slash is removed from base URL."""
        client = GitLabClient(url="https://git.example.com/", token="test-token")
        url = client._api_url(42, "/issues")
        assert url == "https://git.example.com/api/v4/projects/42/issues"

    @patch("requests.Session.request")
    def test_get_issues(self, mock_request: Mock, client: GitLabClient) -> None:
        """Test getting issues."""
        mock_request.return_value = Mock(
            status_code=200,
            json=lambda: [
                {
                    "iid": 1,
                    "title": "Test Issue",
                    "description": "Description",
                    "web_url": "https://git.example.com/project/issues/1",
                    "labels": ["bug"],
                }
            ],
        )

        issues = client.get_issues(42, assignee_username="claude")

        assert len(issues) == 1
        assert issues[0].iid == 1
        assert issues[0].title == "Test Issue"

    @patch("requests.Session.request")
    def test_get_issues_empty_description(
        self, mock_request: Mock, client: GitLabClient
    ) -> None:
        """Test getting issues with empty description."""
        mock_request.return_value = Mock(
            status_code=200,
            json=lambda: [
                {
                    "iid": 1,
                    "title": "Test Issue",
                    "description": None,
                    "web_url": "https://git.example.com/project/issues/1",
                    "labels": [],
                }
            ],
        )

        issues = client.get_issues(42)

        assert len(issues) == 1
        assert issues[0].description == ""

    @patch("requests.Session.request")
    def test_get_merge_requests(
        self, mock_request: Mock, client: GitLabClient
    ) -> None:
        """Test getting merge requests."""
        mock_request.return_value = Mock(
            status_code=200,
            json=lambda: [
                {
                    "iid": 1,
                    "title": "Test MR",
                    "web_url": "https://git.example.com/project/merge_requests/1",
                    "source_branch": "feature-branch",
                    "state": "opened",
                }
            ],
        )

        mrs = client.get_merge_requests(42, author_username="claude")

        assert len(mrs) == 1
        assert mrs[0].iid == 1
        assert mrs[0].source_branch == "feature-branch"

    @patch("requests.Session.request")
    def test_get_merge_request(self, mock_request: Mock, client: GitLabClient) -> None:
        """Test getting a specific merge request."""
        mock_request.return_value = Mock(
            status_code=200,
            json=lambda: {
                "iid": 1,
                "title": "Test MR",
                "web_url": "https://git.example.com/project/merge_requests/1",
                "source_branch": "feature-branch",
                "state": "opened",
            },
        )

        mr = client.get_merge_request(42, 1)

        assert mr is not None
        assert mr.iid == 1
        assert mr.source_branch == "feature-branch"

    @patch("requests.Session.request")
    def test_get_merge_request_not_found(
        self, mock_request: Mock, client: GitLabClient
    ) -> None:
        """Test getting a non-existent merge request."""
        mock_request.return_value = Mock(
            status_code=404,
            json=lambda: {"message": "Not Found"},
        )

        mr = client.get_merge_request(42, 999)

        assert mr is None

    @patch("requests.Session.request")
    def test_get_notes(self, mock_request: Mock, client: GitLabClient) -> None:
        """Test getting notes."""
        mock_request.return_value = Mock(
            status_code=200,
            json=lambda: [
                {
                    "id": 123,
                    "body": "Test comment",
                    "author": {"username": "reviewer"},
                }
            ],
        )

        notes = client.get_notes(42, 1)

        assert len(notes) == 1
        assert notes[0].id == 123
        assert notes[0].body == "Test comment"
        assert notes[0].author_username == "reviewer"

    @patch("requests.Session.request")
    def test_update_issue_labels(
        self, mock_request: Mock, client: GitLabClient
    ) -> None:
        """Test updating issue labels."""
        mock_request.return_value = Mock(status_code=200)

        result = client.update_issue_labels(42, 1, ["In progress", "bug"])

        assert result is True

    @patch("requests.Session.request")
    def test_update_issue_labels_failure(
        self, mock_request: Mock, client: GitLabClient
    ) -> None:
        """Test updating issue labels failure."""
        mock_request.return_value = Mock(status_code=400)

        with pytest.raises(GitLabAPIError):
            client.update_issue_labels(42, 1, ["In progress", "bug"])

    @patch("requests.Session.request")
    def test_create_merge_request(self, mock_request: Mock, client: GitLabClient) -> None:
        """Test creating merge request."""
        mock_request.return_value = Mock(
            status_code=201,
            json=lambda: {
                "iid": 1,
                "title": "New MR",
                "web_url": "https://git.example.com/project/merge_requests/1",
                "source_branch": "feature-branch",
                "state": "opened",
            },
        )

        mr = client.create_merge_request(
            42,
            source_branch="feature-branch",
            target_branch="master",
            title="New MR",
            description="Description",
        )

        assert mr is not None
        assert mr.iid == 1
        assert mr.source_branch == "feature-branch"

    @patch("requests.Session.request")
    def test_create_merge_request_failure(
        self, mock_request: Mock, client: GitLabClient
    ) -> None:
        """Test creating merge request failure."""
        mock_request.return_value = Mock(
            status_code=400,
            json=lambda: {"message": "Bad Request"},
        )

        with pytest.raises(GitLabAPIError):
            client.create_merge_request(
                42,
                source_branch="feature-branch",
                target_branch="master",
                title="New MR",
                description="Description",
            )

    @patch("requests.Session.request")
    def test_retry_on_5xx(self, mock_request: Mock, client: GitLabClient) -> None:
        """Test retry on 5xx errors."""
        # First two calls return 500, third succeeds
        mock_request.side_effect = [
            Mock(status_code=500, text="Internal Server Error"),
            Mock(status_code=502, text="Bad Gateway"),
            Mock(
                status_code=200,
                json=lambda: [],
            ),
        ]

        issues = client.get_issues(42)

        assert len(issues) == 0
        assert mock_request.call_count == 3

    @patch("requests.Session.request")
    def test_max_retries_exceeded(self, mock_request: Mock, client: GitLabClient) -> None:
        """Test that exception is raised after max retries."""
        mock_request.return_value = Mock(status_code=500, text="Internal Server Error")

        with pytest.raises(GitLabConnectionError, match="Request failed after 3 retries"):
            client.get_issues(42)

    @patch("requests.Session.request")
    def test_retry_on_network_error(self, mock_request: Mock, client: GitLabClient) -> None:
        """Test retry on network errors."""
        # First two calls raise exception, third succeeds
        mock_request.side_effect = [
            requests.ConnectionError("Network error"),
            requests.Timeout("Timeout"),
            Mock(status_code=200, json=lambda: []),
        ]

        issues = client.get_issues(42)

        assert len(issues) == 0
        assert mock_request.call_count == 3

    @patch("requests.Session.request")
    def test_no_retry_on_4xx(self, mock_request: Mock, client: GitLabClient) -> None:
        """Test no retry on 4xx errors."""
        mock_request.return_value = Mock(
            status_code=404,
            json=lambda: {"message": "Not Found"},
        )

        # Should return empty list for 404 (no retry)
        # Actually get_issues returns the JSON response regardless of status
        # Let's test with a method that checks status
        with pytest.raises(GitLabNotFoundError):
            client.update_issue_labels(42, 1, ["bug"])

        assert mock_request.call_count == 1


class TestDataClasses:
    """Tests for data classes."""

    def test_issue_dataclass(self) -> None:
        """Test Issue dataclass."""
        issue = Issue(
            iid=1,
            title="Test",
            description="Desc",
            web_url="https://example.com",
            labels=["bug"],
        )
        assert issue.iid == 1
        assert issue.title == "Test"
        assert issue.description == "Desc"
        assert issue.web_url == "https://example.com"
        assert issue.labels == ["bug"]

    def test_merge_request_dataclass(self) -> None:
        """Test MergeRequest dataclass."""
        mr = MergeRequest(
            iid=1,
            title="Test MR",
            web_url="https://example.com",
            source_branch="feature",
            state="opened",
        )
        assert mr.iid == 1
        assert mr.title == "Test MR"
        assert mr.source_branch == "feature"
        assert mr.state == "opened"

    def test_note_dataclass(self) -> None:
        """Test Note dataclass."""
        note = Note(id=1, body="Comment", author_username="user")
        assert note.id == 1
        assert note.body == "Comment"
        assert note.author_username == "user"

    def test_repr_shows_url(self, client: GitLabClient) -> None:
        """Test that __repr__ shows the URL."""
        from gitlab_watcher.logging_utils import sanitize_for_log
        repr_str = sanitize_for_log(repr(client))
        assert "https://git.example.com" in repr_str

    def test_repr_hides_token_extended(self) -> None:
        """Test that __repr__ does not expose the token even if longer."""
        from gitlab_watcher.logging_utils import sanitize_for_log
        client = GitLabClient(url="https://git.example.com", token="super-secret-token-12345")
        repr_str = sanitize_for_log(repr(client))
        assert "super-secret-token-12345" not in repr_str
        assert "git.example.com" in repr_str
        assert "GitLabClient" in repr_str


class TestGitLabClientConfiguration:
    """Tests for GitLab client configuration options."""

    def test_default_timeout_is_set(self) -> None:
        """Test that default timeout is configured."""
        from gitlab_watcher.gitlab_client import DEFAULT_TIMEOUT

        client = GitLabClient(url="https://git.example.com", token="test-token")
        assert client.timeout == DEFAULT_TIMEOUT

    def test_custom_timeout_is_set(self) -> None:
        """Test that custom timeout is configured."""
        client = GitLabClient(
            url="https://git.example.com",
            token="test-token",
            timeout=60.0,
        )
        assert client.timeout == 60.0

    def test_session_has_adapter_mounted(self) -> None:
        """Test that HTTP adapter is mounted on session."""
        client = GitLabClient(url="https://git.example.com", token="test-token")

        # Check that adapters are mounted for both http and https
        assert "https://" in client.session.adapters
        assert "http://" in client.session.adapters

    def test_adapter_has_pool_configuration(self) -> None:
        """Test that adapter has connection pool configuration."""
        client = GitLabClient(
            url="https://git.example.com",
            token="test-token",
            pool_connections=5,
            pool_maxsize=10,
        )

        adapter = client.session.get_adapter("https://")
        assert adapter is not None

    @patch("requests.Session.request")
    def test_timeout_is_passed_to_request(self, mock_request: Mock) -> None:
        """Test that timeout is passed to request."""
        mock_request.return_value = Mock(status_code=200, json=lambda: [])

        client = GitLabClient(
            url="https://git.example.com",
            token="test-token",
            timeout=45.0,
        )
        client.get_issues(42)

        # Check that timeout was passed to the request
        call_kwargs = mock_request.call_args[1]
        assert "timeout" in call_kwargs
        assert call_kwargs["timeout"] == 45.0


class TestGitLabClientConfiguration:
    """Tests for GitLab client configuration."""

    def test_default_timeout(self) -> None:
        """Test that default timeout is set."""
        from gitlab_watcher.gitlab_client import DEFAULT_TIMEOUT
        client = GitLabClient(url="https://git.example.com", token="test-token")
        assert client.timeout == DEFAULT_TIMEOUT

    def test_custom_timeout(self) -> None:
        """Test that custom timeout can be set."""
        client = GitLabClient(
            url="https://git.example.com",
            token="test-token",
            timeout=60.0,
        )
        assert client.timeout == 60.0

    def test_connection_pooling_configured(self) -> None:
        """Test that connection pooling is configured."""
        client = GitLabClient(url="https://git.example.com", token="test-token")
        # Check that adapters are mounted for both http and https
        assert "https://" in client.session.adapters
        assert "http://" in client.session.adapters

    @patch("requests.Session.request")
    def test_request_uses_default_timeout(self, mock_request: Mock) -> None:
        """Test that requests use default timeout."""
        from gitlab_watcher.gitlab_client import DEFAULT_TIMEOUT
        mock_request.return_value = Mock(status_code=200, json=lambda: [])

        client = GitLabClient(url="https://git.example.com", token="test-token")
        client._request("GET", "https://git.example.com/api/v4/test")

        call_kwargs = mock_request.call_args[1]
        assert call_kwargs["timeout"] == DEFAULT_TIMEOUT

    @patch("requests.Session.request")
    def test_request_uses_custom_timeout(self, mock_request: Mock) -> None:
        """Test that requests use custom timeout when provided."""
        mock_request.return_value = Mock(status_code=200, json=lambda: [])

        client = GitLabClient(
            url="https://git.example.com",
            token="test-token",
            timeout=45.0,
        )
        client._request("GET", "https://git.example.com/api/v4/test")

        call_kwargs = mock_request.call_args[1]
        assert call_kwargs["timeout"] == 45.0