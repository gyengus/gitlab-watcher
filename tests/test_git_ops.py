"""Tests for Git operations."""

import subprocess
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from gitlab_watcher.git_ops import GitOps


@pytest.fixture
def git_ops(tmp_path: Path) -> GitOps:
    """Create a GitOps instance for testing."""
    return GitOps(tmp_path)


class TestGitOps:
    """Tests for Git operations."""

    def test_generate_slug_simple(self) -> None:
        """Test slug generation with simple title."""
        slug = GitOps.generate_slug("Add new feature")
        assert slug == "add-new-feature"

    def test_generate_slug_special_chars(self) -> None:
        """Test slug generation with special characters."""
        slug = GitOps.generate_slug("Fix bug #123!!!")
        assert slug == "fix-bug-123"

    def test_generate_slug_truncation(self) -> None:
        """Test slug truncation."""
        long_title = "This is a very long title that should be truncated"
        slug = GitOps.generate_slug(long_title, max_length=20)
        assert len(slug) == 20

    def test_generate_slug_consecutive_hyphens(self) -> None:
        """Test that consecutive hyphens are removed."""
        slug = GitOps.generate_slug("Fix -- multiple --- hyphens")
        assert "--" not in slug

    def test_generate_slug_leading_trailing_hyphens(self) -> None:
        """Test that leading and trailing hyphens are removed."""
        slug = GitOps.generate_slug("  test  ")
        assert slug == "test"

    def test_generate_slug_uppercase(self) -> None:
        """Test that uppercase is converted to lowercase."""
        slug = GitOps.generate_slug("UPPERCASE Title")
        assert slug == "uppercase-title"

    @patch("subprocess.run")
    def test_fetch_success(self, mock_run: Mock, git_ops: GitOps) -> None:
        """Test successful fetch."""
        mock_run.return_value = Mock(returncode=0)

        result = git_ops.fetch("origin")

        assert result is True
        mock_run.assert_called_once()

    @patch("subprocess.run")
    def test_fetch_failure(self, mock_run: Mock, git_ops: GitOps) -> None:
        """Test failed fetch."""
        mock_run.side_effect = subprocess.CalledProcessError(1, "git")

        result = git_ops.fetch("origin")

        assert result is False

    @patch("subprocess.run")
    def test_checkout_existing_branch(self, mock_run: Mock, git_ops: GitOps) -> None:
        """Test checking out an existing branch."""
        # Mock current branch and exists check
        mock_run.side_effect = [
            Mock(stdout="main\n", returncode=0),  # rev-parse --abbrev-ref HEAD
            Mock(returncode=0),  # checkout main
        ]

        success, error = git_ops.checkout("feature")

        assert success is True
        assert error == ""
        # Second call is the checkout
        args = mock_run.call_args_list[1][0][0]
        assert "checkout" in args
        assert "feature" in args
        assert "-b" not in args

    @patch("subprocess.run")
    def test_checkout_create_branch(self, mock_run: Mock, git_ops: GitOps) -> None:
        """Test creating and checking out a new branch."""
        # Mock current branch, exists check, and checkout -b
        mock_run.side_effect = [
            Mock(stdout="main\n", returncode=0),  # get_current_branch
            subprocess.CalledProcessError(1, "git"),  # branch_exists -> False
            Mock(returncode=0),  # checkout -b
        ]

        success, error = git_ops.checkout("feature", create=True)

        assert success is True
        assert error == ""
        args = mock_run.call_args_list[2][0][0]
        assert "checkout" in args
        assert "-b" in args
        assert "feature" in args

    @patch("subprocess.run")
    def test_checkout_failure(self, mock_run: Mock, git_ops: GitOps) -> None:
        """Test failed checkout."""
        mock_run.side_effect = [
            Mock(stdout="main\n", returncode=0),  # get_current_branch
            subprocess.CalledProcessError(1, "git", stderr="Branch not found"),
        ]

        success, error = git_ops.checkout("nonexistent")

        assert success is False
        assert "Branch not found" in error

    @patch("subprocess.run")
    def test_pull_success(self, mock_run: Mock, git_ops: GitOps) -> None:
        """Test successful pull."""
        mock_run.return_value = Mock(returncode=0)

        result = git_ops.pull("origin", "main")

        assert result is True
        args = mock_run.call_args[0][0]
        assert "pull" in args
        assert "origin" in args
        assert "main" in args

    @patch("subprocess.run")
    def test_pull_without_branch(self, mock_run: Mock, git_ops: GitOps) -> None:
        """Test pull without specifying branch."""
        mock_run.return_value = Mock(returncode=0)

        result = git_ops.pull("origin")

        assert result is True
        args = mock_run.call_args[0][0]
        assert "pull" in args

    @patch("subprocess.run")
    def test_pull_failure(self, mock_run: Mock, git_ops: GitOps) -> None:
        """Test failed pull."""
        mock_run.side_effect = subprocess.CalledProcessError(1, "git")

        result = git_ops.pull("origin")

        assert result is False

    @patch("subprocess.run")
    def test_push_success(self, mock_run: Mock, git_ops: GitOps) -> None:
        """Test successful push."""
        mock_run.return_value = Mock(returncode=0)

        result = git_ops.push("origin", "feature")

        assert result is True
        args = mock_run.call_args[0][0]
        assert "push" in args
        assert "origin" in args
        assert "feature" in args

    @patch("subprocess.run")
    def test_push_with_upstream(self, mock_run: Mock, git_ops: GitOps) -> None:
        """Test push with upstream flag."""
        mock_run.return_value = Mock(returncode=0)

        result = git_ops.push("origin", "feature", set_upstream=True)

        assert result is True
        args = mock_run.call_args[0][0]
        assert "-u" in args

    @patch("subprocess.run")
    def test_push_failure(self, mock_run: Mock, git_ops: GitOps) -> None:
        """Test failed push."""
        mock_run.side_effect = subprocess.CalledProcessError(1, "git")

        result = git_ops.push("origin", "feature")

        assert result is False

    @patch("subprocess.run")
    def test_delete_branch(self, mock_run: Mock, git_ops: GitOps) -> None:
        """Test deleting a branch."""
        mock_run.return_value = Mock(returncode=0)

        result = git_ops.delete_branch("feature")

        assert result is True
        args = mock_run.call_args[0][0]
        assert "branch" in args
        assert "-d" in args
        assert "feature" in args

    @patch("subprocess.run")
    def test_delete_branch_force(self, mock_run: Mock, git_ops: GitOps) -> None:
        """Test force deleting a branch."""
        mock_run.return_value = Mock(returncode=0)

        result = git_ops.delete_branch("feature", force=True)

        assert result is True
        args = mock_run.call_args[0][0]
        assert "-D" in args

    @patch("subprocess.run")
    def test_branch_exists_true(self, mock_run: Mock, git_ops: GitOps) -> None:
        """Test checking if branch exists."""
        mock_run.return_value = Mock(returncode=0, stdout="abc123")

        result = git_ops.branch_exists("main")

        assert result is True

    @patch("subprocess.run")
    def test_branch_exists_false(self, mock_run: Mock, git_ops: GitOps) -> None:
        """Test checking if branch doesn't exist."""
        mock_run.side_effect = subprocess.CalledProcessError(1, "git")

        result = git_ops.branch_exists("nonexistent")

        assert result is False

    @patch("subprocess.run")
    def test_get_current_branch(self, mock_run: Mock, git_ops: GitOps) -> None:
        """Test getting current branch name."""
        mock_run.return_value = Mock(stdout="feature-branch\n", returncode=0)

        result = git_ops.get_current_branch()

        assert result == "feature-branch"

    @patch("subprocess.run")
    def test_get_current_branch_failure(self, mock_run: Mock, git_ops: GitOps) -> None:
        """Test getting current branch when it fails."""
        mock_run.side_effect = subprocess.CalledProcessError(1, "git")

        result = git_ops.get_current_branch()

        assert result is None

    @patch("subprocess.run")
    def test_get_remote_url(self, mock_run: Mock, git_ops: GitOps) -> None:
        """Test getting remote URL."""
        mock_run.return_value = Mock(
            stdout="https://github.com/user/repo.git\n", returncode=0
        )

        result = git_ops.get_remote_url("origin")

        assert result == "https://github.com/user/repo.git"

    @patch("subprocess.run")
    def test_get_remote_url_failure(self, mock_run: Mock, git_ops: GitOps) -> None:
        """Test getting remote URL when it fails."""
        mock_run.side_effect = subprocess.CalledProcessError(1, "git")

        result = git_ops.get_remote_url("origin")

        assert result is None

    @patch("subprocess.run")
    def test_get_remote_url_empty(self, mock_run: Mock, git_ops: GitOps) -> None:
        """Test getting remote URL when empty."""
        mock_run.return_value = Mock(stdout="\n", returncode=0)

        result = git_ops.get_remote_url("origin")

        assert result is None
    @patch("subprocess.run")
    def test_checkout_already_on_branch(self, mock_run: Mock, git_ops: GitOps) -> None:
        """Test checkout when already on the target branch."""
        mock_run.return_value = Mock(stdout="feature\n", returncode=0)

        success, error = git_ops.checkout("feature")

        assert success is True
        assert error == ""
        # Only rev-parse called, no checkout
        assert mock_run.call_count == 1
        assert "rev-parse" in mock_run.call_args[0][0]
