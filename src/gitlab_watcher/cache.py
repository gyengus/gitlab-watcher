"""Simple time-based cache implementation."""

from datetime import datetime, timedelta
from typing import Any, Generic, Optional, TypeVar

T = TypeVar('T')


class TimedCache(Generic[T]):
    """Simple time-based cache that expires entries after a TTL.

    This cache stores values with timestamps and automatically invalidates
    entries that exceed the time-to-live duration.
    """

    def __init__(self, ttl_seconds: float = 30.0) -> None:
        """Initialize the cache.

        Args:
            ttl_seconds: Time-to-live in seconds for cache entries
        """
        self._cache: dict[str, tuple[datetime, T]] = {}
        self._ttl = timedelta(seconds=ttl_seconds)

    def get(self, key: str) -> Optional[T]:
        """Get a value from cache if not expired.

        Args:
            key: Cache key

        Returns:
            Cached value if found and not expired, None otherwise
        """
        if key not in self._cache:
            return None

        timestamp, value = self._cache[key]

        # Check if entry has expired
        if datetime.now() - timestamp > self._ttl:
            del self._cache[key]
            return None

        return value

    def set(self, key: str, value: T) -> None:
        """Store a value in cache.

        Args:
            key: Cache key
            value: Value to cache
        """
        self._cache[key] = (datetime.now(), value)

    def invalidate(self, key: str) -> None:
        """Remove a specific entry from cache.

        Args:
            key: Cache key to invalidate
        """
        self._cache.pop(key, None)

    def clear(self) -> None:
        """Remove all entries from cache."""
        self._cache.clear()

    def size(self) -> int:
        """Return number of entries in cache (including expired ones).

        Returns:
            Number of cached entries
        """
        return len(self._cache)


__all__ = ["TimedCache"]