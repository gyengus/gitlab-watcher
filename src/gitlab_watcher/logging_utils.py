"""Logging utilities for sensitive data filtering."""

import logging
import re
from typing import Any


class SensitiveDataFilter(logging.Filter):
    """Filter to mask sensitive data in log messages."""

    SENSITIVE_PATTERNS = [
        # GitLab tokens (typically 20+ alphanumeric characters, sometimes with hyphens/underscores)
        (r'([a-zA-Z0-9_-]{20,})', '***TOKEN***'),
        # URLs with authentication (user:pass@ or token@)
        (r'https://[^:]+:[^@]+@', 'https://***:***@'),
        # URLs with token only (token@)
        (r'https://[^@]+@(?=[^/])', 'https://***@'),
    ]

    def filter(self, record: logging.LogRecord) -> bool:
        """Filter sensitive data from log record.

        Args:
            record: The log record to filter

        Returns:
            Always True (filtering is done in-place)
        """
        # Filter the main message
        if record.msg:
            record.msg = self._mask_sensitive(str(record.msg))

        # Filter args if present
        if record.args:
            record.args = tuple(
                self._mask_sensitive(str(arg)) if isinstance(arg, str) else arg
                for arg in record.args
            )

        return True

    def _mask_sensitive(self, text: str) -> str:
        """Mask sensitive data in text.

        Args:
            text: The text to mask

        Returns:
            Text with sensitive data masked
        """
        for pattern, replacement in self.SENSITIVE_PATTERNS:
            text = re.sub(pattern, replacement, text)
        return text


def sanitize_for_log(text: str, preserve_newlines: bool = False) -> str:
    """Sanitize string for logging to prevent log injection.

    Removes tabs and non-printable characters. Optionally preserves newlines.

    Args:
        text: The string to sanitize
        preserve_newlines: If True, keep \n and \r characters. Defaults to False.

    Returns:
        The sanitized string
    """
    if not text:
        return ""

    if not preserve_newlines:
        # Replace newlines, carriage returns, and tabs with spaces
        text = re.sub(r"[\r\n\t]+", " ", text)
    else:
        # Replace only tabs with spaces
        text = text.replace("\t", " ")

    # Remove non-printable characters except newlines/CR if preserved
    if preserve_newlines:
        text = "".join(c for c in text if c.isprintable() or c in "\r\n")
    else:
        text = "".join(c for c in text if c.isprintable())

    return text.strip()


__all__ = ["SensitiveDataFilter", "sanitize_for_log"]