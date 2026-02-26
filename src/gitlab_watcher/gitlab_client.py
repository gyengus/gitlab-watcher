"""GitLab API client with retry logic."""

import time
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import quote

import requests


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
    """GitLab API client with automatic retries."""

    def __init__(
        self,
        url: str,
        token: str,
        max_retries: int = 3,
        retry_delay: float = 1.0,
    ) -> None:
        """Initialize GitLab client.

        Args:
            url: GitLab instance URL (e.g., https://git.example.com)
            token: Personal access token
            max_retries: Maximum number of retries on 5xx errors
            retry_delay: Delay between retries in seconds
        """
        self.base_url = url.rstrip("/")
        self.token = token
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.session = requests.Session()
        self.session.headers.update({"PRIVATE-TOKEN": token})

    def _api_url(self, project_id: int, endpoint: str) -> str:
        """Build full API URL for a project endpoint."""
        return f"{self.base_url}/api/v4/projects/{project_id}{endpoint}"

    def _request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        """Make HTTP request with retry logic for 5xx errors."""
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

                return response

            except requests.RequestException as e:
                last_error = e
                time.sleep(self.retry_delay * (attempt + 1))

        raise RuntimeError(f"Request failed after {self.max_retries} retries: {last_error}")

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
        """Get a specific merge request."""
        response = self._request("GET", self._api_url(project_id, f"/merge_requests/{mr_iid}"))
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

    def get_notes(
        self,
        project_id: int,
        mr_iid: int,
        sort: str = "desc",
    ) -> list[Note]:
        """Get notes (comments) for a merge request."""
        endpoint = f"/merge_requests/{mr_iid}/notes?sort={sort}"

        response = self._request("GET", self._api_url(project_id, endpoint))
        data = response.json()

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