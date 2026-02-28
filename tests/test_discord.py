"""Tests for Discord webhook notifications."""

from unittest.mock import Mock, patch

import pytest

from gitlab_watcher.discord import DiscordWebhook


class TestDiscordWebhook:
    """Tests for Discord webhook client."""

    def test_send_without_webhook_url(self) -> None:
        """Test that send returns True when no webhook URL is configured."""
        webhook = DiscordWebhook(webhook_url="")
        result = webhook.send("Test message")
        assert result is True

    @patch("requests.post")
    def test_send_success(self, mock_post: Mock) -> None:
        """Test successful message send."""
        mock_post.return_value = Mock(status_code=204)

        webhook = DiscordWebhook(webhook_url="https://discord.com/api/webhooks/123/abc")
        result = webhook.send("Test message")

        assert result is True
        mock_post.assert_called_once_with(
            "https://discord.com/api/webhooks/123/abc",
            json={"content": "Test message"},
            headers={"Content-Type": "application/json"},
            timeout=10,
        )

    @patch("requests.post")
    def test_send_failure(self, mock_post: Mock) -> None:
        """Test failed message send."""
        mock_post.return_value = Mock(status_code=400)

        webhook = DiscordWebhook(webhook_url="https://discord.com/api/webhooks/123/abc")
        result = webhook.send("Test message")

        assert result is False

    @patch("requests.post")
    def test_send_exception(self, mock_post: Mock) -> None:
        """Test send with network exception."""
        import requests

        mock_post.side_effect = requests.RequestException("Network error")

        webhook = DiscordWebhook(webhook_url="https://discord.com/api/webhooks/123/abc")
        result = webhook.send("Test message")

        assert result is False

    @patch("requests.post")
    def test_notify_issue_started(self, mock_post: Mock) -> None:
        """Test issue started notification."""
        mock_post.return_value = Mock(status_code=204)

        webhook = DiscordWebhook(webhook_url="https://discord.com/api/webhooks/123/abc")
        result = webhook.notify_issue_started(
            project_name="test-project",
            issue_title="Fix bug",
            issue_url="https://git.example.com/issues/1",
            branch="1-fix-bug",
        )

        assert result is True
        call_args = mock_post.call_args
        message = call_args.kwargs["json"]["content"]
        assert "Starting Issue" in message
        assert "test-project" in message
        assert "Fix bug" in message
        assert "1-fix-bug" in message

    @patch("requests.post")
    def test_notify_mr_created(self, mock_post: Mock) -> None:
        """Test MR created notification."""
        mock_post.return_value = Mock(status_code=204)

        webhook = DiscordWebhook(webhook_url="https://discord.com/api/webhooks/123/abc")
        result = webhook.notify_mr_created(
            project_name="test-project",
            issue_title="Fix bug",
            mr_url="https://git.example.com/merge_requests/1",
            issue_iid=1,
        )

        assert result is True
        call_args = mock_post.call_args
        message = call_args.kwargs["json"]["content"]
        assert "MR Created" in message
        assert "#1" in message

    @patch("requests.post")
    def test_notify_changes_applied(self, mock_post: Mock) -> None:
        """Test changes applied notification."""
        mock_post.return_value = Mock(status_code=204)

        webhook = DiscordWebhook(webhook_url="https://discord.com/api/webhooks/123/abc")
        result = webhook.notify_changes_applied(
            project_name="test-project",
            mr_title="Fix bug",
            mr_url="https://git.example.com/merge_requests/1",
        )

        assert result is True
        call_args = mock_post.call_args
        message = call_args.kwargs["json"]["content"]
        assert "Changes Applied" in message

    @patch("requests.post")
    def test_notify_mr_merged(self, mock_post: Mock) -> None:
        """Test MR merged notification."""
        mock_post.return_value = Mock(status_code=204)

        webhook = DiscordWebhook(webhook_url="https://discord.com/api/webhooks/123/abc")
        result = webhook.notify_mr_merged(
            project_name="test-project",
            mr_title="Fix bug",
            mr_url="https://git.example.com/merge_requests/1",
        )

        assert result is True
        call_args = mock_post.call_args
        message = call_args.kwargs["json"]["content"]
        assert "MR Merged" in message

    @patch("requests.post")
    def test_notify_cleanup_complete(self, mock_post: Mock) -> None:
        """Test cleanup complete notification."""
        mock_post.return_value = Mock(status_code=204)

        webhook = DiscordWebhook(webhook_url="https://discord.com/api/webhooks/123/abc")
        result = webhook.notify_cleanup_complete(
            project_name="test-project",
            branch="1-fix-bug",
        )

        assert result is True
        call_args = mock_post.call_args
        message = call_args.kwargs["json"]["content"]
        assert "Cleanup complete" in message
        assert "1-fix-bug" in message

    @patch("requests.post")
    def test_notify_error(self, mock_post: Mock) -> None:
        """Test error notification."""
        mock_post.return_value = Mock(status_code=204)

        webhook = DiscordWebhook(webhook_url="https://discord.com/api/webhooks/123/abc")
        result = webhook.notify_error(
            project_name="test-project",
            message="Something went wrong",
        )

        assert result is True
        call_args = mock_post.call_args
        message = call_args.kwargs["json"]["content"]
        assert "Error" in message
        assert "Something went wrong" in message

    @patch("requests.post")
    def test_notify_error_with_details(self, mock_post: Mock) -> None:
        """Test error notification with details."""
        mock_post.return_value = Mock(status_code=204)

        webhook = DiscordWebhook(webhook_url="https://discord.com/api/webhooks/123/abc")
        result = webhook.notify_error(
            project_name="test-project",
            message="Something went wrong",
            details="Stack trace here",
        )

        assert result is True
        call_args = mock_post.call_args
        message = call_args.kwargs["json"]["content"]
        assert "Stack trace here" in message