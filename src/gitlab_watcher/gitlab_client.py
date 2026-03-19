"""GitLab API client with retry logic."""

import time
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import quote

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .cache import TimedCache
from .exceptions import (
    GitLabAPIError,
    GitLabAuthenticationError,
    GitLabConnectionError,
    GitLabForbiddenError,
    GitLabNotFoundError,
    GitLabRateLimitError,
)


# Default configuration values
DEFAULT_TIMEOUT = 30.0
DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_DELAY = 1.0
DEFAULT_POOL_CONNECTIONS = 10
DEFAULT_POOL_MAXSIZE = 20
DEFAULT_CACHE_TTL = 30.0


@dataclass
class Issue:
    """Represents a GitLab issue."""

    iid: int
    title: str
    description: str
    web_url: str
    labels: list[str]


@dataclass
class MergeRequest:
    """Represents a GitLab merge request."""

    iid: int
    title: str
    web_url: str
    source_branch: str
    state: str


@dataclass
class Note:
    """Represents a GitLab note (comment)."""

    id: int
    body: str
    author_username: str


class GitLabClient:
    """GitLab API client with automatic retries, connection pooling, and caching."""

    def __init__(
        self,
        url: str,
        token: str,
        max_retries: int = DEFAULT_MAX_RETRIES,
        retry_delay: float = DEFAULT_RETRY_DELAY,
        timeout: float = DEFAULT_TIMEOUT,
        pool_connections: int = DEFAULT_POOL_CONNECTIONS,
        pool_maxsize: int = DEFAULT_POOL_MAXSIZE,
        cache_ttl: float = DEFAULT_CACHE_TTL,
    ) -> None:
        """Initialize GitLab client.

        Args:
            url: GitLab instance URL (e.g., https://git.example.com)
            token: Personal access token
            max_retries: Maximum number of retries on 5xx errors
            retry_delay: Delay between retries in seconds
            timeout: Request timeout in seconds
            pool_connections: Number of connection pool connections
            pool_maxsize: Maximum connections in pool
            cache_ttl: Cache time-to-live in seconds
        """
        self.base_url = url.rstrip("/")
        self._token = token  # Private to avoid accidental logging
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.timeout = timeout
        self._cache: TimedCache[dict[str, Any]] = TimedCache(ttl_seconds=cache_ttl)

        # Configure session with connection pooling and retry strategy
        self.session = requests.Session()
        self.session.headers.update({"PRIVATE-TOKEN": token})

        # Configure retry strategy for HTTP errors
        retry_strategy = Retry(
            total=max_retries,
            backoff_factor=retry_delay,
            status_forcelist=[429, 500, 502, 503, 504],
        )

        # Configure adapter with connection pooling
        adapter = HTTPAdapter(
            max_retries=retry_strategy,
            pool_connections=pool_connections,
            pool_maxsize=pool_maxsize,
        )
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

    def __repr__(self) -> str:
        """Return string representation without sensitive data."""
        return f"GitLabClient(url={self.base_url!r})"

    def _api_url(self, project_id: int, endpoint: str) -> str:
        """Build full API URL for a project endpoint."""
        return f"{self.base_url}/api/v4/projects/{project_id}{endpoint}"

    def _request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        """Make HTTP request with timeout and retry logic for 5xx errors."""
        # Set default timeout if not provided
        kwargs.setdefault("timeout", self.timeout)

        last_error: Optional[Exception] = None

        for attempt in range(self.max_retries):
            try:
                response = self.session.request(method, url, **kwargs)

                # Retry on 5xx errors
                if response.status_code >= 500:
                    last_error = Exception(
                        f"Server error {response.status_code}: {response.text}"
                    )
                    time.sleep(self.retry_delay * (attempt + 1))
                    continue

                if response.status_code == 401:
                    raise GitLabAuthenticationError()
                elif response.status_code == 403:
                    raise GitLabForbiddenError(url)
                elif response.status_code == 404:
                    raise GitLabNotFoundError("Resource", url)
                elif response.status_code == 429:
                    retry_after_str = response.headers.get("Retry-After")
                    retry_after = int(retry_after_str) if retry_after_str and retry_after_str.isdigit() else None
                    raise GitLabRateLimitError(retry_after)
                elif response.status_code >= 400:
                    raise GitLabAPIError(response.status_code, response.text)

                return response

            except requests.RequestException as e:
                last_error = e
                time.sleep(self.retry_delay * (attempt + 1))

        raise GitLabConnectionError(
            f"Request failed after {self.max_retries} retries. Last error: {last_error}"
        )

    def get_issues(
        self,
        project_id: int,
        state: str = "opened",
        assignee_username: Optional[str] = None,
    ) -> list[Issue]:
        """Get issues for a project."""
        endpoint = f"/issues?state={state}"
        if assignee_username:
            endpoint += f"&assignee_username={quote(assignee_username)}"

        response = self._request("GET", self._api_url(project_id, endpoint))
        data = response.json()

        return [
            Issue(
                iid=item["iid"],
                title=item.get("title", ""),
                description=item.get("description", "") or "",
                web_url=item.get("web_url", ""),
                labels=item.get("labels", []),
            )
            for item in data
        ]

    def get_merge_requests(
        self,
        project_id: int,
        state: str = "opened",
        author_username: Optional[str] = None,
    ) -> list[MergeRequest]:
        """Get merge requests for a project."""
        endpoint = f"/merge_requests?state={state}"
        if author_username:
            endpoint += f"&author_username={quote(author_username)}"

        response = self._request("GET", self._api_url(project_id, endpoint))
        data = response.json()

        return [
            MergeRequest(
                iid=item["iid"],
                title=item.get("title", ""),
                web_url=item.get("web_url", ""),
                source_branch=item.get("source_branch", ""),
                state=item.get("state", ""),
            )
            for item in data
        ]

    def get_merge_request(self, project_id: int, mr_iid: int) -> Optional[MergeRequest]:
        """Get a specific merge request with caching."""
        cache_key = f"mr_{project_id}_{mr_iid}"
        cached = self._cache.get(cache_key)

        if cached is not None:
            return MergeRequest(
                iid=cached["iid"],
                title=cached.get("title", ""),
                web_url=cached.get("web_url", ""),
                source_branch=cached.get("source_branch", ""),
                state=cached.get("state", ""),
            )

        try:
            response = self._request("GET", self._api_url(project_id, f"/merge_requests/{mr_iid}"))
        except GitLabNotFoundError:
            return None
            
        data = response.json()

        if "iid" not in data:
            return None

        # Cache the raw response
        self._cache.set(cache_key, data)

        return MergeRequest(
            iid=data["iid"],
            title=data.get("title", ""),
            web_url=data.get("web_url", ""),
            source_branch=data.get("source_branch", ""),
            state=data.get("state", ""),
        )

    def get_notes(
        self,
        project_id: int,
        mr_iid: int,
        sort: str = "desc",
    ) -> list[Note]:
        """Get notes (comments) for a merge request with caching."""
        cache_key = f"notes_{project_id}_{mr_iid}"
        cached = self._cache.get(cache_key)

        if cached is not None:
            return [
                Note(
                    id=item["id"],
                    body=item.get("body", ""),
                    author_username=item.get("author", {}).get("username", ""),
                )
                for item in cached
            ]

        endpoint = f"/merge_requests/{mr_iid}/notes?sort={sort}"

        response = self._request("GET", self._api_url(project_id, endpoint))
        data = response.json()

        # Cache the raw response
        self._cache.set(cache_key, data)

        return [
            Note(
                id=item["id"],
                body=item.get("body", ""),
                author_username=item.get("author", {}).get("username", ""),
            )
            for item in data
        ]

    def update_issue_labels(
        self,
        project_id: int,
        issue_iid: int,
        labels: list[str],
    ) -> bool:
        """Update labels on an issue."""
        labels_str = ",".join(labels)
        endpoint = f"/issues/{issue_iid}"

        response = self._request(
            "PUT",
            self._api_url(project_id, endpoint),
            data={"labels": labels_str},
        )

        return response.status_code == 200

    def create_merge_request(
        self,
        project_id: int,
        source_branch: str,
        target_branch: str,
        title: str,
        description: str,
    ) -> Optional[MergeRequest]:
        """Create a new merge request."""
        endpoint = "/merge_requests"

        response = self._request(
            "POST",
            self._api_url(project_id, endpoint),
            data={
                "source_branch": source_branch,
                "target_branch": target_branch,
                "title": title,
                "description": description,
            },
        )

        data = response.json()

        if "iid" not in data:
            return None

        return MergeRequest(
            iid=data["iid"],
            title=data.get("title", ""),
            web_url=data.get("web_url", ""),
            source_branch=data.get("source_branch", ""),
            state=data.get("state", ""),
        )

    def invalidate_cache(self, key: Optional[str] = None) -> None:
        """Invalidate cache entries.

        Args:
            key: Specific cache key to invalidate, or None to clear all
        """
        if key is not None:
            self._cache.invalidate(key)
        else:
            self._cache.clear()


__all__ = [
    "GitLabClient",
    "Issue",
    "MergeRequest",
    "Note",
    "DEFAULT_TIMEOUT",
    "DEFAULT_MAX_RETRIES",
    "DEFAULT_RETRY_DELAY",
    "DEFAULT_POOL_CONNECTIONS",
    "DEFAULT_POOL_MAXSIZE",
    "DEFAULT_CACHE_TTL",
]