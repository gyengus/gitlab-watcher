"""Tests for logging utilities."""

import logging

import pytest

from gitlab_watcher.logging_utils import SensitiveDataFilter


class TestSensitiveDataFilter:
    """Tests for the SensitiveDataFilter class."""

    def setup_method(self) -> None:
        """Set up test fixtures."""
        self.filter = SensitiveDataFilter()

    def test_filter_valid_message(self) -> None:
        """Test valid message passes filter unchanged."""
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Normal log message",
            args=(),
            exc_info=None,
        )
        result = self.filter.filter(record)
        assert result is True
        assert record.msg == "Normal log message"

    def test_filter_masks_long_token(self) -> None:
        """Test long alphanumeric tokens are masked."""
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Token: abcdefghijklmnopqrstuvwxyz123456",
            args=(),
            exc_info=None,
        )
        self.filter.filter(record)
        assert "***TOKEN***" in record.msg
        assert "abcdefghijklmnopqrstuvwxyz123456" not in record.msg

    def test_filter_masks_url_with_credentials(self) -> None:
        """Test URLs with credentials are masked."""
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="URL: https://user:secret-token@git.example.com/project",
            args=(),
            exc_info=None,
        )
        self.filter.filter(record)
        assert "secret-token" not in record.msg
        assert "***@" in record.msg
        assert "user" not in record.msg

    def test_filter_masks_url_with_token_only(self) -> None:
        """Test URLs with token only are masked."""
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="URL: https://glpat-abcdefghij1234567890@git.example.com/project",
            args=(),
            exc_info=None,
        )
        self.filter.filter(record)
        assert "glpat-abcdefghij1234567890" not in record.msg
        assert "***@" in record.msg

    def test_filter_masks_args(self) -> None:
        """Test args are also masked."""
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Token: %s",
            args=("abcdefghijklmnopqrstuvwxyz123456",),
            exc_info=None,
        )
        self.filter.filter(record)
        assert "***TOKEN***" in str(record.args[0])
        assert "abcdefghijklmnopqrstuvwxyz123456" not in str(record.args[0])

    def test_filter_preserves_short_tokens(self) -> None:
        """Test short strings are not masked."""
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Short: abc",
            args=(),
            exc_info=None,
        )
        self.filter.filter(record)
        assert record.msg == "Short: abc"

    def test_filter_handles_multiple_patterns(self) -> None:
        """Test multiple sensitive patterns are masked."""
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Token: abcdefghijklmnopqrstuvwxyz123456 URL: https://user:pass@host.com",
            args=(),
            exc_info=None,
        )
        self.filter.filter(record)
        assert "***TOKEN***" in record.msg
        assert "***@" in record.msg
        assert "abcdefghijklmnopqrstuvwxyz123456" not in record.msg
        assert "pass" not in record.msg
        assert "user" not in record.msg

    def test_filter_with_non_string_args(self) -> None:
        """Test filter handles non-string args."""
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Count: %d",
            args=(42,),
            exc_info=None,
        )
        result = self.filter.filter(record)
        assert result is True
        assert record.args == (42,)