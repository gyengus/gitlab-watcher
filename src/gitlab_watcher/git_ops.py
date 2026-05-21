"""Git operations via subprocess."""

import subprocess
import time
import logging
import re
from pathlib import Path


class GitOps:
    """Git operations using subprocess."""

    def __init__(self, repo_path: Path) -> None:
        """Initialize Git operations for a repository.

        Args:
            repo_path: Path to the git repository
        """
        self.repo_path = repo_path
        self.logger = logging.getLogger(__name__)

    def _validate_arg(self, arg: str, is_message: bool = False, command: str | None = None) -> str:
        """Validate a git argument to prevent injection."""
        if not arg:
            return arg
        
        if is_message:
            # For messages, we are more lenient but still prevent obvious injections
            if "\0" in arg:
                raise ValueError("Null character in git message")
            return arg
            
        # Command-specific allowlist of safe flags
        SAFE_COMMANDS = {
            "rev-parse": {"--abbrev-ref", "--verify", "--", "HEAD"},
            "checkout": {"-b", "--"},
            "pull": {"--"},
            "push": {"-u", "--"},
            "branch": {"-d", "-D", "--"},
            "log": {"--oneline", "-n", "--"},
            "status": {"--porcelain", "--"},
            "add": {"--"},
            "commit": {"-m", "--"},
            "config": {"--get"}
        }
        
        # Structural arguments (branches, remotes, paths)
        # Disallow leading hyphens to prevent flag injection, unless it's a known safe flag for this command
        if arg.startswith("-"):
            if command and command in SAFE_COMMANDS and arg in SAFE_COMMANDS[command]:
                return arg
            # Global allowlist for common structural elements
            if arg in {"--", "@{u}"}:
                 pass
            else:
                raise ValueError(f"Git argument cannot start with a hyphen: {arg}")
            
        # Allow alphanumeric, hyphens, underscores, dots, forward slashes, @{u}, and colons
        # Tightened regex: no spaces allowed in structural arguments
        if not re.match(r"^[a-zA-Z0-9\-_./@{}:]+$", arg):
            # Allow @{u}..HEAD which is used in has_unpushed_work
            if not re.match(r"^[a-zA-Z0-9\-_./@{}:.]+$", arg):
                raise ValueError(f"Invalid characters in git argument: {arg}")
        return arg

    def _run(
        self, 
        *args: str, 
        check: bool = True, 
        timeout: int = 60,
        capture_output: bool = True
    ) -> subprocess.CompletedProcess[str]:
        """Run a git command in the repository with a timeout."""
        if not args:
            return subprocess.CompletedProcess(args, 0, "", "")

        main_command = args[0]
        # Validate the main command itself
        if main_command.startswith("-") or not re.match(r"^[a-z\-]+$", main_command):
             raise ValueError(f"Invalid git command: {main_command}")

        # Detect if we are in a commit command to be more lenient with the message
        is_commit = main_command == "commit"
        
        validated_args = [main_command]
        for i, arg in enumerate(args[1:], 1):
            is_message = is_commit and i > 1 and args[i-1] == "-m"
            validated_args.append(self._validate_arg(arg, is_message=is_message, command=main_command))
        
        stdout = subprocess.PIPE if capture_output else subprocess.DEVNULL
        stderr = subprocess.PIPE if capture_output else subprocess.DEVNULL
        
        return subprocess.run(
            ["git"] + validated_args,
            cwd=self.repo_path,
            stdout=stdout,
            stderr=stderr,
            text=True,
            check=check,
            timeout=timeout,
        )

    def fetch(self, remote: str = "origin") -> bool:
        """Fetch from remote."""
        try:
            self._run("fetch", remote, capture_output=False)
            return True
        except subprocess.CalledProcessError as e:
            self.logger.error(f"Git fetch failed: {str(e)}")
            return False

    def checkout(self, branch: str, create: bool = False) -> tuple[bool, str]:
        """Checkout a branch, optionally creating it.

        If already on the branch, does nothing.
        If create=True and branch exists, just switches to it.
        """
        try:
            current = self.get_current_branch()
            if current == branch:
                return True, ""

            if create:
                # Try checking out normally first (if it exists)
                try:
                    self._run("checkout", "--", branch, capture_output=False)
                    return True, ""
                except subprocess.CalledProcessError:
                    # If normal checkout fails, try creating it
                    self._run("checkout", "-b", branch, "--", capture_output=False)
            else:
                self._run("checkout", "--", branch, capture_output=False)
            return True, ""
        except subprocess.CalledProcessError as e:
            error_msg = e.stderr.strip() if e.stderr else str(e)
            return False, error_msg

    def pull(self, remote: str = "origin", branch: str | None = None) -> bool:
        """Pull from remote."""
        try:
            if branch:
                self._run("pull", remote, "--", branch, capture_output=False)
            else:
                self._run("pull", capture_output=False)
            return True
        except subprocess.CalledProcessError as e:
            self.logger.error(f"Git pull failed: {str(e)}")
            return False

    def push(
        self,
        remote: str = "origin",
        branch: str | None = None,
        set_upstream: bool = False,
        retries: int = 3,
        retry_delay: int = 10,
    ) -> bool:
        """Push to remote with exponential back-off on failure.

        Args:
            remote: Remote name (default "origin").
            branch: Branch to push; if ``None`` pushes the current branch.
            set_upstream: Whether to add ``-u`` flag (sets upstream).
            retries: Number of retry attempts on failure (default 3).
            retry_delay: Initial delay in seconds for back-off (default 10).
        """
        args = ["push"]
        if set_upstream and branch:
            args.extend(["-u", remote, branch])
        elif branch:
            args.extend([remote, branch])
        else:
            args.append(remote)
        
        args.append("--")

        attempt = 0
        while attempt <= retries:
            try:
                self._run(*args, capture_output=False)
                return True
            except subprocess.CalledProcessError as e:
                attempt += 1
                if attempt > retries:
                    self.logger.error(f"Git push failed after {retries} retries: {str(e)}")
                    return False
                
                # Exponential back-off: 10s, 20s, 40s...
                backoff_delay = retry_delay * (2 ** (attempt - 1))
                self.logger.warning(f"Git push failed (attempt {attempt}/{retries}). Retrying in {backoff_delay}s... Error: {str(e)}")
                time.sleep(backoff_delay)
                continue
        return False

    def delete_branch(self, branch: str, force: bool = False) -> bool:
        """Delete a local branch."""
        try:
            args = ["branch"]
            if force:
                args.append("-D")
            else:
                args.append("-d")
            args.extend(["--", branch])

            self._run(*args, check=False, capture_output=False)
            return True
        except subprocess.CalledProcessError as e:
            self.logger.error(f"Git branch delete failed: {str(e)}")
            return False

    def has_unpushed_work(self, default_branch: str) -> bool:
        """Check if the current branch has commits beyond the default branch.

        Args:
            default_branch: The default branch to compare against

        Returns:
            True if there are commits ahead of the default branch, False otherwise
        """
        try:
            # Validate branch name
            self._validate_arg(default_branch)
            # Limit log to avoid large memory buffering
            result = self._run("log", f"{default_branch}..HEAD", "--oneline", "-n", "100", "--", check=False)
            return bool(result.stdout.strip())
        except (subprocess.CalledProcessError, ValueError, Exception):
            return False

    def has_unpushed_to_remote(self) -> bool:
        """Check if the current branch has commits not yet pushed to its upstream.

        Returns:
            True if there are commits ahead of the remote-tracking branch, False otherwise
        """
        try:
            # Check if there's an upstream configured first
            rev_parse = self._run("rev-parse", "--abbrev-ref", "--", "@{u}", check=False)
            if rev_parse.returncode != 0:
                return False

            # Limit log to avoid large memory buffering
            result = self._run("log", "@{u}..HEAD", "--oneline", "-n", "100", "--", check=False)
            return bool(result.stdout.strip())
        except Exception:
            return False

    def branch_exists(self, branch: str) -> bool:
        """Check if a branch exists locally."""
        try:
            # Validate branch name
            self._validate_arg(branch)
            result = self._run("rev-parse", "--verify", "--", branch, check=False)
            return result.returncode == 0
        except (subprocess.CalledProcessError, ValueError, Exception):
            return False

    def get_current_branch(self) -> str | None:
        """Get the current branch name."""
        try:
            result = self._run("rev-parse", "--abbrev-ref", "--", "HEAD")
            return result.stdout.strip() or None
        except subprocess.CalledProcessError:
            return None

    def has_uncommitted_changes(self) -> bool:
        """Check if there are any uncommitted changes (staged or unstaged).
        
        This uses `git status --porcelain` to detect modified, added, deleted,
        or renamed files that haven't been committed yet.

        Example:
            >>> git.has_uncommitted_changes()
            True
        
        Returns:
            True if there are uncommitted changes, False otherwise
        """
        try:
            # Check for unstaged changes
            result = self._run("status", "--porcelain", "--", check=False)
            return bool(result.stdout.strip())
        except Exception:
            return False

    def add(self, path: str = ".") -> bool:
        """Add files to the staging area.
        
        Example:
            >>> git.add("src/main.py")
            True
            >>> git.add(".")  # Add all changes
            True
        
        Args:
            path: Path to add (default: ".")
            
        Returns:
            True if successful, False otherwise
        """
        try:
            self._run("add", "--", path, capture_output=False)
            return True
        except subprocess.CalledProcessError:
            return False
    
    def commit(self, message: str) -> bool:
        """Create a commit with the given message.
        
        Example:
            >>> git.commit("Fix: resolve authentication bug")
            True
        
        Args:
            message: Commit message
            
        Returns:
            True if successful, False otherwise
        """
        try:
            self._run("commit", "-m", message, "--", capture_output=False)
            return True
        except subprocess.CalledProcessError:
            return False

    def get_remote_url(self, remote: str = "origin") -> str | None:
        """Get the remote URL."""
        try:
            # Validate remote name
            self._validate_arg(remote)
            result = self._run("config", "--get", f"remote.{remote}.url")
            return result.stdout.strip() or None
        except (subprocess.CalledProcessError, ValueError):
            return None

    def get_current_commit(self) -> str:
        """Return the current HEAD commit hash.

        Example:
            >>> git.get_current_commit()
            'a7b1c3d...'

        Returns:
            Full SHA-1 hash of HEAD, or empty string on failure.
        """
        try:
            result = self._run("rev-parse", "--", "HEAD", check=False)
            return result.stdout.strip() if result.returncode == 0 else ""
        except Exception:
            return ""

    @staticmethod
    def generate_slug(title: str, max_length: int = 30) -> str:
        """Generate a URL-safe slug from a title.

        Args:
            title: The title to slugify
            max_length: Maximum length of the slug

        Returns:
            A URL-safe slug
        """
        slug = title.lower()
        # Replace non-alphanumeric with hyphens
        slug = "".join(c if c.isalnum() else "-" for c in slug)
        # Remove consecutive hyphens
        while "--" in slug:
            slug = slug.replace("--", "-")
        # Remove leading/trailing hyphens
        slug = slug.strip("-")
        # Truncate
        return slug[:max_length]