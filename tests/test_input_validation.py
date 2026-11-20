"""Parametrized boundary and negative input validation tests."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from gitlab_watcher.config import load_config
from gitlab_watcher.gitlab_client import GitLabClient
from gitlab_watcher.logging_utils import mask_output_secrets
from gitlab_watcher.processor import Processor


def _write_conf(tmp_path: Path, extra_lines: str = "") -> Path:
    """Create a minimal valid config + project directory for testing."""
    proj = tmp_path / "proj"
    proj.mkdir(exist_ok=True)
    (proj / "PROJECT.md").write_text("Project ID: 10\n", encoding="utf-8")
    conf = tmp_path / "config.conf"
    conf.write_text(
        f'GITLAB_URL="https://git.example.com"\n'
        f'GITLAB_TOKEN="glpat-1234567890abcdefghij"\n'
        f'PROJECT_DIRS=("{proj}")\n'
        f"{extra_lines}\n",
        encoding="utf-8",
    )
    return conf


class TestConfigInputValidation:
    """Boundary and invalid value tests for configuration loading."""

    @pytest.mark.parametrize("level", [10, 20, 30, 40, 50])
    def test_min_access_level_valid_boundaries(self, tmp_path: Path, level: int) -> None:
        """Valid GitLab access levels (10..50) are accepted."""
        conf = _write_conf(tmp_path, f"MIN_ACCESS_LEVEL={level}")
        cfg = load_config(str(conf))
        assert cfg.min_access_level == level

    @pytest.mark.parametrize("invalid_level", ["0", "9", "51", "-5", "abc"])
    def test_min_access_level_invalid_values_raise(self, tmp_path: Path, invalid_level: str) -> None:
        """Out-of-range or non-integer MIN_ACCESS_LEVEL raises ValueError."""
        conf = _write_conf(tmp_path, f"MIN_ACCESS_LEVEL={invalid_level}")
        with pytest.raises(ValueError):
            load_config(str(conf))

    @pytest.mark.parametrize("bad_int_key", ["POLL_INTERVAL=notanint", "AI_TOOL_TIMEOUT=xyz"])
    def test_non_integer_numeric_config_raises(self, tmp_path: Path, bad_int_key: str) -> None:
        """Non-integer values for integer config keys raise ValueError."""
        conf = _write_conf(tmp_path, bad_int_key)
        with pytest.raises(ValueError):
            load_config(str(conf))

    @pytest.mark.parametrize("bad_work_dir", ["/etc/gitlab-watcher", "/root/watcher", "/var/log/watcher"])
    def test_sensitive_work_dir_rejected(self, tmp_path: Path, bad_work_dir: str) -> None:
        """WORK_DIR pointing to sensitive or unsafe system paths raises ValueError."""
        conf = _write_conf(tmp_path, f'WORK_DIR="{bad_work_dir}"')
        with pytest.raises(ValueError):
            load_config(str(conf))


class TestGitLabClientValidation:
    """Negative and edge-case tests for GitLabClient."""

    @pytest.mark.parametrize(
        "bad_url",
        ["", "ftp://git.example.com", "https://git.example.com?q=1", "not-a-url"],
    )
    def test_invalid_gitlab_url_rejected(self, bad_url: str) -> None:
        """Invalid GitLab URLs raise ValueError."""
        with pytest.raises(ValueError):
            GitLabClient(url=bad_url, token="glpat-1234567890abcdefghij")

    @pytest.mark.parametrize("bad_token", ["", "token with spaces", "tok$en!", "tok\nen"])
    def test_invalid_gitlab_token_rejected(self, bad_token: str) -> None:
        """Tokens with invalid characters or empty value raise ValueError."""
        with pytest.raises(ValueError):
            GitLabClient(url="https://git.example.com", token=bad_token)

    @pytest.mark.parametrize("bad_project_id", [0, -1, -999])
    def test_invalid_project_id_rejected(self, bad_project_id: int) -> None:
        """Non-positive project IDs raise ValueError."""
        client = GitLabClient(url="https://git.example.com", token="glpat-1234567890abcdefghij")
        with pytest.raises(ValueError):
            client._api_url(bad_project_id, "/issues")

    def test_member_access_level_negative_cache_and_empty_username(self) -> None:
        """Empty username returns None immediately; non-member result is cached."""
        client = GitLabClient(url="https://git.example.com", token="glpat-1234567890abcdefghij")
        assert client.get_project_member_access_level(42, "") is None

        with patch.object(client, "_request_all", return_value=[]) as mock_req:
            assert client.get_project_member_access_level(42, "outsider") is None
            assert client.get_project_member_access_level(42, "outsider") is None
            mock_req.assert_called_once()

    @pytest.mark.parametrize(
        ("status_code", "headers", "expected_exc_name"),
        [
            (401, {}, "GitLabAuthenticationError"),
            (403, {}, "GitLabForbiddenError"),
            (404, {}, "GitLabNotFoundError"),
            (429, {"Retry-After": "12"}, "GitLabRateLimitError"),
            (400, {}, "GitLabAPIError"),
        ],
    )
    def test_request_maps_http_error_codes(
        self, status_code: int, headers: dict[str, str], expected_exc_name: str
    ) -> None:
        """HTTP 4xx/429 responses map to specific GitLabError subclasses."""
        client = GitLabClient(url="https://git.example.com", token="glpat-1234567890abcdefghij")
        mock_resp = MagicMock(status_code=status_code, headers=headers, text="err")
        with patch.object(client.session, "request", return_value=mock_resp):
            with pytest.raises(Exception) as exc_info:
                client._request("GET", "https://git.example.com/api/v4/user")
            assert type(exc_info.value).__name__ == expected_exc_name

    def test_note_emojis_and_discussion_reply_methods(self) -> None:
        """Note emoji, MR emoji, and discussion reply methods handle success and errors."""
        client = GitLabClient(url="https://git.example.com", token="glpat-1234567890abcdefghij")
        with patch.object(client, "_request") as mock_req:
            mock_req.return_value = MagicMock(json=lambda: [{"id": 1, "name": "eyes"}])
            assert client.get_note_emojis(42, 1, 10) == ["eyes"]
            assert client.create_note_reply(42, 1, "disc1", "ok") is True
            assert client.create_note_reply(42, 1, "", "ok") is False
            assert client.create_note_award_emoji(42, 1, 10, "eyes", discussion_id="disc1") is True
            assert client.delete_note_award_emojis(42, 1, 10, ["eyes"]) is True
            assert client.delete_note_award_emojis(42, 1, 10, []) is True


class TestOutputMaskingAndCommandValidation:
    """Tests for mask_output_secrets and Processor._build_ai_command negative cases."""

    @pytest.mark.parametrize(
        ("raw", "must_hide", "must_keep"),
        [
            (
                "Error with glpat-1234567890abcdefghij in test_watcher_ignores_comment",
                "glpat-1234567890abcdefghij",
                "test_watcher_ignores_comment",
            ),
            (
                "Commit a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2 failed with Bearer secret.jwt.token",
                "secret.jwt.token",
                "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2",
            ),
            (
                "Clone https://user:mypassword@git.example.com/repo.git",
                "mypassword",
                "git.example.com/repo.git",
            ),
        ],
    )
    def test_mask_output_secrets_preserves_identifiers(
        self, raw: str, must_hide: str, must_keep: str
    ) -> None:
        """mask_output_secrets hides real secrets without mangling identifiers or commit SHAs."""
        masked = mask_output_secrets(raw)
        assert must_hide not in masked
        assert must_keep in masked

    @pytest.mark.parametrize(
        "bad_custom_cmd",
        [
            "opencode run --prompt={prompt}",
            "opencode run {prompt} && rm -rf /",
            "opencode run {prompt} --bad$flag",
        ],
    )
    def test_build_ai_command_rejects_malformed_custom_commands(
        self, tmp_path: Path, bad_custom_cmd: str
    ) -> None:
        """Embedded placeholders and shell metacharacters are rejected."""
        proc = Processor(
            gitlab=MagicMock(),
            discord=MagicMock(),
            state=MagicMock(),
            gitlab_username="bot",
            label_in_progress="In progress",
            label_review="Review",
            ai_tool_mode="custom",
            ai_tool_custom_command=bad_custom_cmd,
        )
        with patch.object(proc, "_validate_ai_binary", side_effect=lambda x: x):
            with pytest.raises(ValueError):
                proc._build_ai_command("hello", tmp_path)


class TestWatcherLifecycleAndSSRF:
    """Tests for Watcher SSRF DNS checks, hostname pinning, lock file, and main loop."""

    @pytest.mark.parametrize(
        "resolved_ip",
        ["127.0.0.1", "::1", "169.254.169.254", "::ffff:127.0.0.1"],
    )
    def test_ssrf_forbidden_resolved_ips_rejected(self, tmp_path: Path, resolved_ip: str) -> None:
        """Watcher rejects GitLab hostnames resolving to loopback, link-local, or IPv4-mapped loopback IPs."""
        from gitlab_watcher.watcher import Watcher

        conf = _write_conf(tmp_path)
        fake_addr = [(2, 1, 6, "", (resolved_ip, 443))]
        with patch("socket.getaddrinfo", return_value=fake_addr):
            with pytest.raises(ValueError, match="forbidden IP"):
                Watcher(config_path=str(conf), disable_lock=True, work_dir=tmp_path / "work")

    def test_watcher_pin_hostname_and_lock_and_run_loop(self, tmp_path: Path) -> None:
        """Watcher acquires lock, pins hostname, handles loop errors, and cleans up on stop()."""
        from gitlab_watcher.exceptions import GitLabAPIError
        from gitlab_watcher.watcher import Watcher

        conf = _write_conf(tmp_path)
        work_dir = tmp_path / "work"
        fake_addr = [(2, 1, 6, "", ("93.184.216.34", 443))]
        mock_gitlab = MagicMock(spec=GitLabClient)
        mock_gitlab.get_current_user.return_value = {"username": "bot"}

        with patch("socket.getaddrinfo", return_value=fake_addr):
            watcher = Watcher(
                config_path=str(conf),
                gitlab=mock_gitlab,
                discord=MagicMock(),
                processor=MagicMock(),
                disable_lock=False,
                work_dir=work_dir,
            )
            try:
                assert (work_dir / "gitlab-watcher.lock").exists()
                with (
                    patch.object(watcher, "check_mr_status", side_effect=[GitLabAPIError(500, "err"), KeyboardInterrupt()]),
                    patch("time.sleep"),
                ):
                    watcher.run()
            finally:
                watcher.stop()

