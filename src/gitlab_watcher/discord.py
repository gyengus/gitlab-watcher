"""Discord webhook notifications."""

from dataclasses import dataclass

import requests


@dataclass
class DiscordWebhook:
    """Discord webhook client."""

    webhook_url: str

    def send(self, content: str) -> bool:
        """Send a message to Discord.

        Args:
            content: The message content

        Returns:
            True if successful, False otherwise
        """
        if not self.webhook_url:
            return True  # No webhook configured, skip silently

        try:
            response = requests.post(
                self.webhook_url,
                json={"content": content},
                headers={"Content-Type": "application/json"},
                timeout=10,
            )
            return response.status_code == 204
        except requests.RequestException:
            return False

    def notify_issue_started(
        self,
        project_name: str,
        issue_title: str,
        issue_url: str,
        branch: str,
    ) -> bool:
        """Notify that issue processing has started."""
        return self.send(
            f"🚀 **Starting Issue** [{project_name}]\n"
            f"[{issue_title}]({issue_url})\n\n"
            f"Branch: `{branch}`"
        )

    def notify_mr_created(
        self,
        project_name: str,
        issue_title: str,
        mr_url: str,
        issue_iid: int,
    ) -> bool:
        """Notify that an MR has been created."""
        return self.send(
            f"✅ **MR Created** [{project_name}]\n"
            f"[{issue_title}]({mr_url})\n\n"
            f"Issue #{issue_iid} completed. Waiting for review."
        )

    def notify_changes_applied(
        self,
        project_name: str,
        mr_title: str,
        mr_url: str,
    ) -> bool:
        """Notify that MR feedback has been applied."""
        return self.send(
            f"✅ **Changes Applied** [{project_name}]\n"
            f"[{mr_title}]({mr_url})\n\n"
            f"Feedback has been addressed and changes pushed."
        )

    def notify_mr_merged(
        self,
        project_name: str,
        mr_title: str,
        mr_url: str,
    ) -> bool:
        """Notify that an MR has been merged."""
        return self.send(
            f"✅ **MR Merged** [{project_name}]\n"
            f"[{mr_title}]({mr_url})\n\n"
            f"MR was merged. Running cleanup..."
        )

    def notify_cleanup_complete(
        self,
        project_name: str,
        branch: str,
    ) -> bool:
        """Notify that cleanup has completed."""
        return self.send(
            f"🧹 **Cleanup complete** [{project_name}]\n"
            f"Branch `{branch}` deleted, master updated."
        )

    def notify_error(
        self,
        project_name: str,
        message: str,
        details: str | None = None,
    ) -> bool:
        """Notify about an error."""
        content = f"❌ **Error** [{project_name}]\n{message}"
        if details:
            content += f"\n\nError: {details}"
        return self.send(content)