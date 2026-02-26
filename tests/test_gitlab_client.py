"""Tests for GitLab client."""

from unittest.mock import Mock, patch

import pytest

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
    def test_get_merge_requests(self, mock_request: Mock, client: GitLabClient) -> None:
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
    def test_update_issue_labels(self, mock_request: Mock, client: GitLabClient) -> None:
        """Test updating issue labels."""
        mock_request.return_value = Mock(status_code=200)

        result = client.update_issue_labels(42, 1, ["In progress", "bug"])

        assert result is True

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