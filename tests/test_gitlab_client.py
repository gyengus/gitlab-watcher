"""Tests for GitLab client."""

from unittest.mock import Mock, patch

import pytest
import requests

from gitlab_watcher.gitlab_client import GitLabClient, Issue, MergeRequest, Note


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

        result = client.update_issue_labels(42, 1, ["In progress", "bug"])

        assert result is False

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

        mr = client.create_merge_request(
            42,
            source_branch="feature-branch",
            target_branch="master",
            title="New MR",
            description="Description",
        )

        assert mr is None

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

        with pytest.raises(RuntimeError, match="Request failed after 3 retries"):
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
        result = client.update_issue_labels(42, 1, ["bug"])

        assert result is False
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