"""Tests for cache implementation."""

import time

import pytest

from gitlab_watcher.cache import TimedCache


class TestTimedCache:
    """Tests for the TimedCache class."""

    def test_set_and_get(self) -> None:
        """Test basic set and get operations."""
        cache: TimedCache[str] = TimedCache(ttl_seconds=10.0)
        cache.set("key1", "value1")
        result = cache.get("key1")
        assert result == "value1"

    def test_get_missing_key(self) -> None:
        """Test getting a missing key returns None."""
        cache: TimedCache[str] = TimedCache(ttl_seconds=10.0)
        result = cache.get("nonexistent")
        assert result is None

    def test_cache_expiration(self) -> None:
        """Test that entries expire after TTL."""
        cache: TimedCache[str] = TimedCache(ttl_seconds=0.1)
        cache.set("key1", "value1")

        # Should be present immediately
        assert cache.get("key1") == "value1"

        # Wait for expiration
        time.sleep(0.2)

        # Should be expired
        result = cache.get("key1")
        assert result is None

    def test_invalidate_specific_key(self) -> None:
        """Test invalidating a specific key."""
        cache: TimedCache[str] = TimedCache(ttl_seconds=10.0)
        cache.set("key1", "value1")
        cache.set("key2", "value2")

        cache.invalidate("key1")

        assert cache.get("key1") is None
        assert cache.get("key2") == "value2"

    def test_clear_all(self) -> None:
        """Test clearing all entries."""
        cache: TimedCache[str] = TimedCache(ttl_seconds=10.0)
        cache.set("key1", "value1")
        cache.set("key2", "value2")

        cache.clear()

        assert cache.get("key1") is None
        assert cache.get("key2") is None

    def test_size(self) -> None:
        """Test cache size reporting."""
        cache: TimedCache[str] = TimedCache(ttl_seconds=10.0)
        assert cache.size() == 0

        cache.set("key1", "value1")
        assert cache.size() == 1

        cache.set("key2", "value2")
        assert cache.size() == 2

    def test_update_existing_key(self) -> None:
        """Test updating an existing key."""
        cache: TimedCache[str] = TimedCache(ttl_seconds=10.0)
        cache.set("key1", "value1")
        cache.set("key1", "value2")

        assert cache.get("key1") == "value2"

    def test_different_value_types(self) -> None:
        """Test cache with different value types."""
        # String cache
        str_cache: TimedCache[str] = TimedCache(ttl_seconds=10.0)
        str_cache.set("key", "string")
        assert str_cache.get("key") == "string"

        # Dict cache
        dict_cache: TimedCache[dict[str, int]] = TimedCache(ttl_seconds=10.0)
        dict_cache.set("key", {"a": 1, "b": 2})
        result = dict_cache.get("key")
        assert result == {"a": 1, "b": 2}

        # List cache
        list_cache: TimedCache[list[int]] = TimedCache(ttl_seconds=10.0)
        list_cache.set("key", [1, 2, 3])
        result = list_cache.get("key")
        assert result == [1, 2, 3]

    def test_expiration_removes_from_cache(self) -> None:
        """Test that expired entries are removed from cache."""
        cache: TimedCache[str] = TimedCache(ttl_seconds=0.1)
        cache.set("key1", "value1")

        assert cache.size() == 1

        # Wait for expiration
        time.sleep(0.2)

        # Accessing expired entry should remove it
        cache.get("key1")
        assert cache.size() == 0