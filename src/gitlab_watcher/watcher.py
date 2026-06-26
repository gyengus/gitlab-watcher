"""Main watcher loop."""

import logging
import re
import time
import os
import sys
try:
    import fcntl
except ImportError:
    fcntl = None
import urllib.parse
import socket
import ipaddress
from pathlib import Path
from typing import Any, Optional

from .config import DEFAULT_CONFIG_PATH, Config, ProjectConfig, load_config
from .constants import (
    POSITIVE_REVIEW_PATTERNS,
)
from .discord import DiscordWebhook
from .exceptions import GitLabError
from .git_ops import GitOps
from .gitlab_client import GitLabClient, Issue, MergeRequest
from .logging_utils import SensitiveDataFilter, sanitize_for_log
from .processor import Processor
from .state import StateManager


class Watcher:
    """Main watcher class that monitors GitLab projects."""

    def __init__(
        self,
        config_path: str = DEFAULT_CONFIG_PATH,
        verbose: bool = False,
        *,
        gitlab: Optional[GitLabClient] = None,
        discord: Optional[DiscordWebhook] = None,
        processor: Optional[Processor] = None,
        state: Optional[StateManager] = None,
        disable_lock: bool = False,
        work_dir: Optional[str | Path] = None,
    ) -> None:
        """Initialize watcher.

        Args:
            config_path: Path to configuration file
            verbose: Enable verbose logging
            gitlab: Optional GitLab client (for testing)
            discord: Optional Discord webhook (for testing)
            processor: Optional processor (for testing)
            state: Optional state manager (for testing)
        """
        self.config = load_config(config_path)
        self.verbose = verbose
        
        # Determine work_dir
        if work_dir:
            self.work_dir = Path(work_dir)
        elif state and hasattr(state, "work_dir"):
            self.work_dir = state.work_dir
        else:
            # Use a separate directory for tests to avoid interfering with production logs/state
            if "pytest" in sys.modules or "PYTEST_CURRENT_TEST" in os.environ:
                self.work_dir = Path("/tmp/test-watcher")
            else:
                self.work_dir = Path("/tmp/gitlab-watcher")

        # Setup logging
        if verbose:
            log_level = logging.DEBUG
        else:
            level_map = {"DEBUG": logging.DEBUG, "INFO": logging.INFO, "WARNING": logging.WARNING, "ERROR": logging.ERROR, "CRITICAL": logging.CRITICAL}
            log_level = level_map.get(self.config.log_level, logging.INFO)
        log_format = "%(asctime)s [%(process)d] [%(levelname)s] %(message)s"
        
        # Only configure basic logging if not already configured (avoids issues in tests)
        if not logging.getLogger().handlers:
            logging.basicConfig(
                level=log_level,
                format=log_format,
            )
        
        self.logger = logging.getLogger("gitlab_watcher")
        self.logger.setLevel(log_level)
        self.logger.propagate = False # Prevent messages from propagating to root logger

        # Setup file logging with fallback
        self._log_handlers: list[logging.Handler] = []
        log_path = Path(self.config.log_file)
        handler_path = None

        try:
            # Ensure the directory exists and has restricted permissions (0700)
            os.makedirs(log_path.parent, mode=0o700, exist_ok=True)
            try:
                st = log_path.parent.stat()
                # Security: Verify directory is owned by current user
                if os.name != 'nt' and st.st_uid != os.getuid():
                     self.logger.warning(f"Log directory {log_path.parent} is not owned by current user! This might be a security risk.")

                # SEC-02 fix: Always attempt to restrict permissions if they are too broad (readable/writable by others)
                if os.name != 'nt' and (st.st_mode & 0o077):
                    self.logger.warning(f"Log directory {log_path.parent} has insecure permissions ({oct(st.st_mode)}). Attempting to restrict to 0700.")
                    os.chmod(log_path.parent, 0o700)
            except OSError as e:
                self.logger.error(f"Failed to secure log directory permissions for {log_path.parent}: {e}")
                pass

            # Security: Use os.open with O_NOFOLLOW to prevent symlink attacks.
            # SEC-01 fix: Open and hold the file descriptor to avoid TOCTOU bypass.
            flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            
            fd = os.open(str(log_path), flags, 0o600)
            try:
                # Ensure permissions are restricted even if file already existed
                if hasattr(os, "fchmod"):
                    os.fchmod(fd, 0o600)
                
                # Wrap fd with os.fdopen to use with FileHandler/StreamHandler
                f = os.fdopen(fd, 'a', encoding='utf-8')
                handler_path = f # Store file object for handler
            except Exception:
                os.close(fd)
                raise
            
        except (PermissionError, OSError) as e:
            # Fallback to work directory
            fallback_dir = self.work_dir
            if os.name != 'nt' and fallback_dir.is_symlink():
                 raise RuntimeError(f"Security risk: {fallback_dir} is a symbolic link.")
            os.makedirs(fallback_dir, mode=0o700, exist_ok=True)
            
            try:
                st = fallback_dir.stat()
                if os.name != 'nt' and (st.st_mode & 0o077):
                    if st.st_uid == os.getuid():
                        os.chmod(fallback_dir, 0o700)
                    else:
                        self.logger.warning(f"Fallback log directory {fallback_dir} has insecure permissions ({oct(st.st_mode)}) and is not owned by current user. Cannot restrict permissions.")
            except OSError as e:
                self.logger.warning(f"Failed to restrict permissions for fallback log directory {fallback_dir}: {e}")

            fallback_path = fallback_dir / "watcher.log"
            try:
                # Same restricted permissions for fallback with O_NOFOLLOW
                if fallback_path.is_symlink():
                     raise PermissionError(f"Security risk: {fallback_path} is a symbolic link and will not be opened.")

                flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
                if hasattr(os, "O_NOFOLLOW"):
                    flags |= os.O_NOFOLLOW

                fd = os.open(str(fallback_path), flags, 0o600)
                try:
                    if hasattr(os, "fchmod"):
                        os.fchmod(fd, 0o600)
                    
                    f = os.fdopen(fd, 'a', encoding='utf-8')
                    handler_path = f
                except Exception:
                    os.close(fd)
                    raise
                
                self.logger.warning(
                    f"Could not use log file {log_path} ({e}). "
                    f"Falling back to {fallback_path}"
                )
            except (PermissionError, OSError) as e2:
                self.logger.error(f"Failed to setup file logging: {e2}")

        if handler_path:
            # SEC-01: Pass file object to StreamHandler to use the secured fd
            file_handler = logging.StreamHandler(handler_path)
            file_handler.setFormatter(logging.Formatter(log_format))
            # Add to our specific logger instead of root logger to avoid global leak in tests
            self.logger.addHandler(file_handler)
            self._log_handlers.append(file_handler)

        # Add sensitive data filter
        self._sensitive_filter = SensitiveDataFilter()
        self.logger.addFilter(self._sensitive_filter)
        
        # In production mode (not tests), we might want to add to root logger
        # but for now let's keep it to our logger to fix the memory leak.
        # If the user really wants root coverage, they can add it once in cli.py.

        # Ensure work directory exists and has restricted permissions (0700)
        try:
            if os.name != 'nt' and self.work_dir.is_symlink():
                raise RuntimeError(f"Security risk: {self.work_dir} is a symbolic link and will not be used.")
            if not self.work_dir.exists():
                # Security: Atomic creation with restricted permissions (0700)
                os.makedirs(self.work_dir, mode=0o700, exist_ok=True)
            
            # Security: Explicitly verify it's a directory and not a symlink
            if self.work_dir.is_symlink():
                 raise RuntimeError(f"Security risk: {self.work_dir} is a symbolic link and will not be used.")
            
            # Ensure permissions are restricted even if it already existed
            st = self.work_dir.stat()
            if os.name != 'nt' and st.st_uid != os.getuid():
                raise PermissionError(f"Directory {self.work_dir} is owned by UID {st.st_uid}, not current UID {os.getuid()}. Aborting for security.")
            
            if os.name != 'nt' and st.st_uid == os.getuid():
                os.chmod(self.work_dir, 0o700)
            
            # Always attempt to restrict permissions if they are too broad
            if os.name != 'nt' and (st.st_mode & 0o022):
                self.logger.warning(f"Work directory {self.work_dir} has insecure permissions ({oct(st.st_mode)}). Attempting to restrict to 0700.")
                os.chmod(self.work_dir, 0o700)
        except (OSError, PermissionError) as e:
            self.logger.error(f"Failed to secure work directory permissions for {self.work_dir}: {e}")
            raise

        # Initialize or use injected state manager
        self.state = state or StateManager(self.work_dir)

        # Get GitLab credentials
        gitlab_url = self.config.gitlab_url
        gitlab_token = os.environ.get("GITLAB_TOKEN") or self.config.gitlab_token

        # Validate GitLab Token immediately to prevent header injection
        if gitlab_token:
            if not re.match(r"^[a-zA-Z0-9_\-\.]+$", gitlab_token):
                raise ValueError("Invalid characters in GITLAB_TOKEN. Only alphanumeric, underscores, hyphens, and dots are allowed.")
            if len(gitlab_token) < 8:
                raise ValueError("GITLAB_TOKEN is too short. Minimum 8 characters required.")

        # Try to extract from git remote if not in config
        if not gitlab_url or not gitlab_token:
            first_project = self.config.projects[0]
            extracted_url, _ = self._extract_from_remote(first_project.path)
            if not gitlab_url:
                gitlab_url = extracted_url

        if not gitlab_url:
            raise ValueError("GitLab URL must be set in config or extractable from git remote")
        
        # Validate GitLab URL
        parsed_url = urllib.parse.urlparse(gitlab_url)
        if parsed_url.scheme not in ("https", "http"):
            raise ValueError(f"Invalid GitLab URL scheme: {parsed_url.scheme}. Only https and http are supported.")
        if not parsed_url.netloc:
            raise ValueError(f"Invalid GitLab URL: {gitlab_url}. Missing hostname.")
        
        # Resolve hostname and check for private/loopback IPs
        hostname = parsed_url.hostname.lower() if parsed_url.hostname else ""
        try:
            # Use socket.getaddrinfo to resolve both IPv4 and IPv6
            addr_info = socket.getaddrinfo(hostname, None)
            allowed_ips = []
            for result in addr_info:
                ip_addr = result[4][0]
                ip = ipaddress.ip_address(ip_addr)
                # BUG-01 fix: Allow private IPs by default, only blocking loopback, link-local, unspecified, and reserved IPs.
                # This ensures compatibility with self-hosted GitLab instances on intranets while preventing SSRF.
                if ip.is_loopback or ip.is_link_local or ip.is_unspecified or ip.is_reserved:
                    raise ValueError(f"GitLab URL hostname resolves to a forbidden IP: {ip_addr}")
                allowed_ips.append(ip_addr)
            
            if not allowed_ips:
                 raise ValueError(f"Could not resolve GitLab hostname: {hostname}")
            
            # GW-02 fix: Pin the hostname resolution to the first safe IP found
            # to prevent DNS rebinding attacks.
            self._pin_hostname(hostname, allowed_ips[0])

        except (socket.gaierror, ValueError) as e:
            # If resolution fails, we don't necessarily block it if it's not a loopback IP
            # but we should be wary of names like 'metadata.google.internal'
            forbidden_names = {
                "localhost", "instance-data", # AWS/GCP/Azure metadata
                "metadata.google.internal", "169.254.169.254", # AWS metadata IP
            }
            if hostname in forbidden_names or hostname.startswith("127.") or hostname == "::1":
                 raise ValueError(f"GitLab URL hostname is forbidden for security: {hostname}")
            
            if isinstance(e, ValueError):
                 raise e
            self.logger.debug(f"Could not resolve GitLab hostname {hostname} for SSRF check: {e}")
            
        if not gitlab_token:
            raise ValueError(
                "GitLab token not found. "
                "Please provide 'GITLAB_TOKEN' in your configuration file "
                "or as an environment variable."
            )
        
        # Initialize or use injected dependencies
        self.gitlab = gitlab or GitLabClient(url=gitlab_url, token=gitlab_token, ssl_verify=self.config.gitlab_ssl_verify)
        
        # Auto-detect username from GitLab API
        self.gitlab_username = self.config.gitlab_username
        if not self.gitlab_username or self.gitlab_username == "OpenCode":
            try:
                # In tests, mock_gitlab might not return what we expect if not configured
                user_info = self.gitlab.get_current_user()
                if isinstance(user_info, dict) and "username" in user_info:
                    self.gitlab_username = user_info["username"]
                    self.logger.info(f"Auto-detected GitLab username: {self.gitlab_username}")
            except Exception as e:
                # Don't let auto-detection failure break the watcher
                self.logger.warning(f"Could not auto-detect GitLab username: {e}")

        self.discord = discord or DiscordWebhook(
            webhook_url=self.config.discord_webhook
        )
        self.processor = processor or Processor(
            gitlab=self.gitlab,
            discord=self.discord,
            state=self.state,
            gitlab_username=self.gitlab_username,
            label_in_progress=self.config.label_in_progress,
            label_review=self.config.label_review,
            ai_tool_mode=self.config.ai_tool_mode,
            ai_tool_custom_command=self.config.ai_tool_custom_command,
            ai_tool_timeout=self.config.ai_tool_timeout,
            ai_tool_failover_model=self.config.ai_tool_failover_model,
            default_branch=self.config.default_branch,
        )
        
        # In-memory deduplication for recently processed notes (solves API lag)
        self._processed_notes: set[int] = set()
        self._last_cache_clear_time = time.time()
        
        # Lock file to prevent multiple instances
        self._lock_file = None
        if not disable_lock:
            self._acquire_lock()

    def _log_project_info(self, project_id: int, level: int, message: str, *args: Any, **kwargs: Any) -> None:
        """Log a message with project context."""
        project_name = next(
            (p.name for p in self.config.projects if p.project_id == project_id),
            str(project_id),
        )
        self.logger.log(level, f"[{project_name}] {message}", *args, **kwargs)

    def _log_info(self, project_id: int, message: str, *args: Any, **kwargs: Any) -> None:
        """Log an INFO message with project context."""
        self._log_project_info(project_id, logging.INFO, message, *args, **kwargs)

    def _log_debug(self, project_id: int, message: str, *args: Any, **kwargs: Any) -> None:
        """Log a DEBUG message with project context."""
        self._log_project_info(project_id, logging.DEBUG, message, *args, **kwargs)

    def _log_warning(self, project_id: int, message: str, *args: Any, **kwargs: Any) -> None:
        """Log a WARNING message with project context."""
        self._log_project_info(project_id, logging.WARNING, message, *args, **kwargs)

    def _log_error(self, project_id: int, message: str, *args: Any, **kwargs: Any) -> None:
        """Log an ERROR message with project context."""
        self._log_project_info(project_id, logging.ERROR, message, *args, **kwargs)

    def _acquire_lock(self) -> None:
        """Acquire a file lock to prevent multiple instances from running."""
        if fcntl is None:
            self.logger.debug("Skipping instance lock (fcntl not available)")
            return

        lock_path = self.work_dir / "gitlab-watcher.lock"
        fd = None
        try:
            # Security: Use os.open with O_CREAT | O_WRONLY | O_TRUNC | O_NOFOLLOW to ensure secure permissions and avoid symlink attacks
            flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            fd = os.open(str(lock_path), flags, 0o600)
            
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._lock_file = os.fdopen(fd, "w", encoding="utf-8")
            self._lock_file.write(str(os.getpid()))
            self._lock_file.flush()
            self.logger.debug(f"Acquired instance lock at {lock_path}")
        except (IOError, BlockingIOError):
            if fd is not None:
                os.close(fd)
            print(f"Error: Another instance of gitlab-watcher is already running (locked {lock_path})", file=sys.stderr)
            sys.exit(1)
        except Exception as e:
            if fd is not None and self._lock_file is None:
                os.close(fd)
            self.logger.warning(f"Could not acquire instance lock: {e}")
        except BaseException:
            # Handle KeyboardInterrupt etc.
            if fd is not None and self._lock_file is None:
                os.close(fd)
            raise

    def _pin_hostname(self, hostname: str, ip: str) -> None:
        """Pin a hostname to a specific IP address to prevent DNS rebinding."""
        import socket
        if not hasattr(socket, "_original_getaddrinfo"):
            socket._original_getaddrinfo = socket.getaddrinfo
            socket._pinned_hosts = {}

            def pinned_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
                h = host.lower() if host else ""
                if h in socket._pinned_hosts:
                    res = socket._original_getaddrinfo(socket._pinned_hosts[h], port, 0, type, proto, flags)
                    return [r for r in res if family == 0 or r[0] == family]
                return socket._original_getaddrinfo(host, port, family, type, proto, flags)

            socket.getaddrinfo = pinned_getaddrinfo
        
        socket._pinned_hosts[hostname.lower()] = ip

    def _extract_from_remote(self, repo_path: Path) -> tuple[str | None, None]:
        """Extract GitLab URL from git remote.
        Note: Token extraction is disabled for security reasons.

        Args:
            repo_path: Path to git repository

        Returns:
            Tuple of (url, None) or (None, None) if not found
        """
        git = GitOps(repo_path)
        remote_url = git.get_remote_url()

        if not remote_url:
            return None, None

        # Extract URL
        host = None
        # Try https:// format
        url_match = re.match(r"https?://([^@]+@)?([^/:]+)", remote_url)
        if url_match:
            host = url_match.group(2)
        else:
            # Try git@host:repo or ssh://git@host[:port]/repo format
            ssh_match = re.match(r"(?:ssh://)?git@([^:/]+)", remote_url)
            if ssh_match:
                host = ssh_match.group(1)

        if not host:
            return None, None

        return f"https://{host}", None

    def _get_stuck_issue(self, project: ProjectConfig, issues: list[Issue], open_mrs: list[MergeRequest]) -> Optional[tuple[Issue, bool]]:
        """Identify issues that are either in backlog or stuck 'In progress'."""
        project_name = project.name

        # Identify which MRs are actually relevant to the issues we're looking at
        # (branch name matches "{issue_iid}-*")
        issue_iids = {issue.iid for issue in issues}
        relevant_mrs = [
            mr for mr in open_mrs 
            if any(mr.source_branch.startswith(f"{iid}-") for iid in issue_iids)
        ]

        # MR-01 fix: Prioritize already "In progress" issues to avoid concurrent processing
        # on the same repository. If any issue is already being worked on, only consider
        # those for potential retry; do not pick up new backlog issues.
        ip_issues = [i for i in issues if self.config.label_in_progress in i.labels and self.config.label_review not in i.labels]
        if ip_issues:
            issues = ip_issues

        for issue in issues:
            has_in_progress = self.config.label_in_progress in issue.labels
            has_review = self.config.label_review in issue.labels

            if not has_in_progress and not has_review:
                self._log_info(project.project_id, "Found backlog issue #%s: %s", issue.iid, sanitize_for_log(issue.title))
                return issue, False

            # Retry: "In progress" but not "Review" (likely timed out or crashed)
            if has_in_progress and not has_review:
                matching_mrs = [mr for mr in open_mrs if mr.source_branch.startswith(f"{issue.iid}-")] if open_mrs else []

                if not matching_mrs:
                    self._log_info(project.project_id, "Retrying stuck issue #%s (In progress but no MR found)", issue.iid)
                    return issue, True
                
                mr = matching_mrs[0]
                git = GitOps(project.path)
                
                # Prioritize checking for unpushed local commits first
                if git.has_unpushed_commits(mr.source_branch):
                    self._log_info(project.project_id, "Retrying stuck issue #%s (In progress with unpushed commits on branch %s)", issue.iid, mr.source_branch)
                    return issue, True

                # Only fetch commits if no unpushed local work is detected
                try:
                    mr_commits = self.gitlab.get_merge_request_commits(project.project_id, mr.iid)
                except Exception as e:
                    self._log_warning(project.project_id, "Could not fetch commits for MR !%s: %s", mr.iid, e)
                    mr_commits = []

                if not mr_commits:
                    self._log_info(project.project_id, "Retrying stuck issue #%s (In progress but MR has no commits)", issue.iid)
                    return issue, True

                continue
        return None

    def check_issues(self, project: ProjectConfig) -> None:
        """Check for new issues to process."""
        if not self.gitlab_username:
            if not getattr(self, "_warned_username", False):
                self._log_warning(project.project_id, "Skipping issue check: gitlab_username is not set.")
                self._warned_username = True
            return

        if self.state.is_processing(project.project_id):
            return

        issues = self.gitlab.get_issues(
            project_id=project.project_id,
            state="opened",
            assignee_username=self.gitlab_username,
        )
        if not issues:
            return

        # Fetch open MRs to check for stuck issues
        open_mrs = self.gitlab.get_merge_requests(
            project_id=project.project_id,
            state="opened",
            author_username=self.gitlab_username,
        )

        stuck_data = self._get_stuck_issue(project, issues, open_mrs)
        if stuck_data:
            issue, is_retry = stuck_data
            self.state.set_processing(project.project_id, True)
            self.processor.process_issue(project, issue, is_retry=is_retry)

    def _handle_merge_cleanup(self, project: ProjectConfig, state: Any, open_mrs: list[MergeRequest]) -> bool:
        """Check all tracked MRs and cleanup those that are merged or closed."""
        tracked_iids = list(state.tracked_mrs.keys())
        if not tracked_iids:
            return False

        # PERF-01 fix: Use the already fetched list of open MRs to avoid redundant API calls.
        open_iids = {mr.iid for mr in open_mrs}

        for iid_str in tracked_iids:
            iid = int(iid_str)
            # If it's still in the open_mrs list, it's not merged or closed yet.
            if iid in open_iids:
                continue

            self.gitlab.invalidate_cache(f"mr_{project.project_id}_{iid}")
            mr = self.gitlab.get_merge_request(project.project_id, iid)

            if mr is None:
                # Remove tracked MR if it no longer exists or is inaccessible
                self.state.remove_tracked_mr(project.project_id, iid)
                continue

            if mr.state in ["merged", "closed"]:
                action = "merged" if mr.state == "merged" else "closed"
                self._log_info(project.project_id, "MR !%s was %s", iid, action)

                mr_data = state.tracked_mrs.get(iid_str, {})
                branch = mr_data.get("branch") or ""

                # Cleanup if we have a known branch for this MR
                if not branch:
                    self._log_info(project.project_id, "MR !%s merged/closed but no branch info found — skipping cleanup", iid)
                    self.state.remove_tracked_mr(project.project_id, iid)
                    continue

                try:
                    self.processor.cleanup_after_merge(
                        project=project,
                        branch=branch,
                        mr_title=mr.title,
                        mr_url=mr.web_url,
                        mr_iid=iid,
                    )
                except Exception as e:
                    self._log_error(project.project_id, f"Cleanup failed: {e}")
                finally:
                    self.state.remove_tracked_mr(project.project_id, iid)
                return True
        return False

    def _find_and_process_comment(self, project: ProjectConfig, mr: MergeRequest) -> bool:
        """Find the first valid human comment on an MR and process it."""
        notes = self.gitlab.get_notes(project.project_id, mr.iid)
        notes = sorted(notes, key=lambda n: n.id)
        
        state = self.state.load(project.project_id)
        
        # Track last_processed_note_id per-MR to avoid comments on one MR skipping comments on another
        mr_id_str = str(mr.iid)
        mr_state = state.tracked_mrs.get(mr_id_str, {}) if hasattr(state, "tracked_mrs") and hasattr(state.tracked_mrs, "get") else {}
        last_processed_id = mr_state.get("last_processed_note_id") if isinstance(mr_state, dict) else None
        if last_processed_id is None:
            last_processed_id = 0

        for note in notes:
            if note.system or note.author_username == self.gitlab_username:
                continue

            is_retry_request = bool(re.search(r"(?i)\bretry\b", note.body))
            SUCCESS_EMOJIS = ["white_check_mark", "heavy_check_mark", "check", "ballot_box_with_check"]
            FAILURE_EMOJIS = ["x", "no_entry"]
            STOP_EMOJIS = SUCCESS_EMOJIS + FAILURE_EMOJIS

            # Fetch emojis once if needed
            note_emojis = self.gitlab.get_note_emojis(project.project_id, mr.iid, note.id) if is_retry_request or note.id > last_processed_id else []

            if is_retry_request:
                # If a success or failure emoji is already present on this note, do not retry it.
                # This prevents infinite loops where a "retry" comment is processed,
                # gets a success or failure emoji, and is repeatedly processed in subsequent cycles.
                if any(e in note_emojis for e in STOP_EMOJIS):
                    is_retry_request = False

            # Skip already handled via persistent state
            if note.id <= last_processed_id and not is_retry_request:
                continue

            # Remove "eyes" from skip list so we can recover interrupted processes
            SKIP_EMOJIS = ["x", "no_entry"] + SUCCESS_EMOJIS
            
            # Use in-memory cache first
            is_skipped = note.id in self._processed_notes
            
            if not is_skipped:
                has_emojis = any(e in note_emojis for e in SKIP_EMOJIS)
                is_skipped = has_emojis
            
            if is_skipped and not is_retry_request:
                # If it's effectively skipped but we haven't updated the persistent state, do it now
                if note.id > last_processed_id:
                    self.state.update_mr_last_processed_note(project.project_id, mr.iid, note.id)
                continue
            
            if is_retry_request and is_skipped:
                 self._log_info(project.project_id, "Retry request detected for note %s on MR !%s. Clearing previous status.", note.id, mr.iid)
                 self.state.set_processing(project.project_id, False)
                 self._processed_notes.discard(note.id)
                 # Explicitly remove success emojis if a retry is requested
                 self.gitlab.delete_note_award_emojis(project.project_id, mr.iid, note.id, SUCCESS_EMOJIS)
                 is_skipped = False
            
            is_positive_review = any(re.search(pattern, note.body, re.IGNORECASE) for pattern in POSITIVE_REVIEW_PATTERNS)
            if is_positive_review:
                self._log_info(project.project_id, "Comment on MR !%s indicates positive review — skipping", mr.iid)
                self._processed_notes.add(note.id)
                # CODE-01 fix: Pass discussion_id to create_note_award_emoji
                self.gitlab.create_note_award_emoji(
                    project.project_id, mr.iid, note.id, "white_check_mark", 
                    discussion_id=note.discussion_id
                )
                self.state.update_mr_last_processed_note(project.project_id, mr.iid, note.id)
                continue


            self._log_info(project.project_id, "New comment on MR !%s: %s", mr.iid, note.body[:100])
            self.state.set_processing(project.project_id, True)
            self._processed_notes.add(note.id)
            self.processor.process_comment(project, mr, note.id, note.body, discussion_id=note.discussion_id)
            self.state.update_mr_state(project.project_id, mr.iid, mr.state, mr.source_branch)
            self.state.update_mr_last_processed_note(project.project_id, mr.iid, note.id)
            return True
        return False

    def check_mr_status(self, project: ProjectConfig) -> None:
        """Check MR status for comments and merge cleanup."""
        if not self.gitlab_username:
            if not getattr(self, "_warned_username", False):
                self._log_warning(project.project_id, "Skipping MR check: gitlab_username is not set.")
                self._warned_username = True
            return

        if self.state.is_processing(project.project_id):
            self._log_debug(project.project_id, "Project is currently processing, skipping MR check.")
            return

        # Periodically clear processed notes cache (every 24 hours)
        if time.time() - self._last_cache_clear_time > 86400:
            self.logger.info("Clearing _processed_notes cache (24h period reached)")
            self._processed_notes.clear()
            self._last_cache_clear_time = time.time()

        self._log_debug(project.project_id, "Checking for open MRs and comments...")
        state = self.state.load(project.project_id)

        mrs = self.gitlab.get_merge_requests(
            project_id=project.project_id,
            state="opened",
            author_username=self.gitlab_username,
        )

        if self._handle_merge_cleanup(project, state, mrs):
            return

        if not mrs:
            return

        for mr in mrs:
            self.state.add_tracked_mr(project.project_id, mr.iid, mr.source_branch)

        for mr in mrs:
            if self._find_and_process_comment(project, mr):
                return




    def run(self) -> None:
        """Run the main watcher loop."""
        # Print summary
        print("GitLab Watcher started")
        print(f"Monitoring {len(self.config.projects)} project(s):")
        for project in self.config.projects:
            print(f"  - {project.name} (ID: {project.project_id})")
        print(f"Logs: {self.work_dir}")

        # Initialize state for all projects (resets processing flag)
        for project in self.config.projects:
            self.state.init_state(project.project_id)

        try:
            # Main loop
            while True:
                try:
                    for project in self.config.projects:
                        self.check_mr_status(project)
                        self.check_issues(project)

                    time.sleep(self.config.poll_interval)

                except KeyboardInterrupt:
                    break
                except GitLabError as e:
                    self.logger.error(f"GitLab API Error: {sanitize_for_log(e.message)}")
                    time.sleep(self.config.poll_interval)
                except Exception as e:
                    self.logger.error(f"Error in main loop: {sanitize_for_log(str(e))}")
                    time.sleep(self.config.poll_interval)
        finally:
            # Ensure all pending state is saved before shutdown
            print("\nShutting down...")
            self.stop()

    def stop(self) -> None:
        """Stop the watcher and cleanup resources."""
        # Restore original getaddrinfo to prevent global test pollution
        import socket
        if hasattr(socket, "_pinned_hosts") and hasattr(socket, "_original_getaddrinfo"):
            socket._pinned_hosts.clear()
            socket.getaddrinfo = socket._original_getaddrinfo
            del socket._original_getaddrinfo, socket._pinned_hosts

        # Release lock file
        if self._lock_file and fcntl:
            try:
                fcntl.flock(self._lock_file, fcntl.LOCK_UN)
                self._lock_file.close()
            except Exception:
                pass
            self._lock_file = None

        if hasattr(self, "state"):
            self.state.force_save_all()
            self.state.stop()
        
        # Remove our handlers from the logger
        if hasattr(self, "_log_handlers"):
            for handler in self._log_handlers:
                self.logger.removeHandler(handler)
                # RES-01 fix: Explicitly close the underlying stream for StreamHandler
                if hasattr(handler, "stream") and handler.stream:
                    try:
                        handler.stream.close()
                    except Exception:
                        pass
                handler.close()
            self._log_handlers.clear()
        
        if hasattr(self, "_sensitive_filter"):
            self.logger.removeFilter(self._sensitive_filter)


__all__ = ["Watcher"]
