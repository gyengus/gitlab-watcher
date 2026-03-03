# Switchable Ollama Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make Claude CLI launch mode configurable via config file, supporting ollama, direct, and custom modes.

**Architecture:** Add two new config fields (`claude_mode`, `claude_custom_command`) that flow from config.py → watcher.py → processor.py. The `_run_claude` method builds the command based on mode.

**Tech Stack:** Python dataclasses, subprocess, pytest

---

### Task 1: Add config fields for Claude mode

**Files:**
- Modify: `src/gitlab_watcher/config.py`
- Test: `tests/test_config.py`

**Step 1: Write the failing test**

```python
# tests/test_config.py - add to existing file

def test_load_config_with_claude_mode(tmp_path: Path) -> None:
    """Test loading config with CLAUDE_MODE and CLAUDE_CUSTOM_COMMAND."""
    config_file = tmp_path / "gitlab_watcher.conf"
    config_file.write_text("""
GITLAB_URL="https://git.example.com"
GITLAB_TOKEN="test-token"
CLAUDE_MODE="direct"
CLAUDE_CUSTOM_COMMAND="my-tool {prompt}"
PROJECT_DIRS=(
    "{}"
)
""".format(tmp_path / "project"))

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / "CLAUDE.md").write_text("Project ID: 42\n")

    config = load_config(str(config_file))

    assert config.claude_mode == "direct"
    assert config.claude_custom_command == "my-tool {prompt}"


def test_load_config_default_claude_mode(tmp_path: Path) -> None:
    """Test default CLAUDE_MODE is ollama."""
    config_file = tmp_path / "gitlab_watcher.conf"
    config_file.write_text("""
GITLAB_URL="https://git.example.com"
GITLAB_TOKEN="test-token"
PROJECT_DIRS=(
    "{}"
)
""".format(tmp_path / "project"))

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / "CLAUDE.md").write_text("Project ID: 42\n")

    config = load_config(str(config_file))

    assert config.claude_mode == "ollama"
    assert config.claude_custom_command == ""
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_config.py::test_load_config_with_claude_mode tests/test_config.py::test_load_config_default_claude_mode -v`
Expected: FAIL with AttributeError

**Step 3: Add fields to Config dataclass**

```python
# src/gitlab_watcher/config.py - modify Config dataclass

@dataclass
class Config:
    """Global configuration loaded from bash-style config file."""

    gitlab_url: str = ""
    gitlab_token: str = ""
    discord_webhook: str = ""
    label_in_progress: str = "In progress"
    label_review: str = "Review"
    gitlab_username: str = "claude"
    poll_interval: int = 30
    project_dirs: list[str] = field(default_factory=list)
    projects: list[ProjectConfig] = field(default_factory=list)
    claude_mode: str = "ollama"
    claude_custom_command: str = ""
```

**Step 4: Update load_config to parse new fields**

```python
# src/gitlab_watcher/config.py - modify load_config function

# Add these lines after poll_interval line (around line 178):
    claude_mode=str(raw_config.get("CLAUDE_MODE", "ollama")),
    claude_custom_command=str(raw_config.get("CLAUDE_CUSTOM_COMMAND", "")),
```

**Step 5: Run test to verify it passes**

Run: `pytest tests/test_config.py::test_load_config_with_claude_mode tests/test_config.py::test_load_config_default_claude_mode -v`
Expected: PASS

**Step 6: Commit**

```bash
git add src/gitlab_watcher/config.py tests/test_config.py
git commit -m "feat: add CLAUDE_MODE and CLAUDE_CUSTOM_COMMAND config options"
```

---

### Task 2: Update Processor to support Claude modes

**Files:**
- Modify: `src/gitlab_watcher/processor.py`
- Test: `tests/test_processor.py`

**Step 1: Write the failing tests**

```python
# tests/test_processor.py - add new test class after TestProcessorRunClaude

class TestProcessorClaudeModes:
    """Tests for different Claude CLI modes."""

    @patch("subprocess.run")
    def test_run_claude_ollama_mode(
        self,
        mock_run: Mock,
        gitlab_client: GitLabClient,
        discord_webhook: DiscordWebhook,
        state_manager: StateManager,
        project_config: ProjectConfig,
    ) -> None:
        """Test ollama mode uses 'ollama launch claude' command."""
        mock_run.return_value = Mock(returncode=0, stdout="Done", stderr="")

        processor = Processor(
            gitlab=gitlab_client,
            discord=discord_webhook,
            state=state_manager,
            gitlab_username="claude",
            label_in_progress="In progress",
            label_review="Review",
            claude_mode="ollama",
        )

        success, output = processor._run_claude("Fix the bug", project_config.path)

        assert success is True
        args = mock_run.call_args[0][0]
        assert args[0] == "ollama"
        assert args[1] == "launch"
        assert args[2] == "claude"

    @patch("subprocess.run")
    def test_run_claude_direct_mode(
        self,
        mock_run: Mock,
        gitlab_client: GitLabClient,
        discord_webhook: DiscordWebhook,
        state_manager: StateManager,
        project_config: ProjectConfig,
    ) -> None:
        """Test direct mode uses 'claude' command directly."""
        mock_run.return_value = Mock(returncode=0, stdout="Done", stderr="")

        processor = Processor(
            gitlab=gitlab_client,
            discord=discord_webhook,
            state=state_manager,
            gitlab_username="claude",
            label_in_progress="In progress",
            label_review="Review",
            claude_mode="direct",
        )

        success, output = processor._run_claude("Fix the bug", project_config.path)

        assert success is True
        args = mock_run.call_args[0][0]
        assert args[0] == "claude"
        assert args[1] == "-p"
        assert "ollama" not in args

    @patch("subprocess.run")
    def test_run_claude_custom_mode(
        self,
        mock_run: Mock,
        gitlab_client: GitLabClient,
        discord_webhook: DiscordWebhook,
        state_manager: StateManager,
        project_config: ProjectConfig,
    ) -> None:
        """Test custom mode uses configured command."""
        mock_run.return_value = Mock(returncode=0, stdout="Done", stderr="")

        processor = Processor(
            gitlab=gitlab_client,
            discord=discord_webhook,
            state=state_manager,
            gitlab_username="claude",
            label_in_progress="In progress",
            label_review="Review",
            claude_mode="custom",
            claude_custom_command="my-ai --prompt {prompt} --dir {cwd}",
        )

        success, output = processor._run_claude("Fix the bug", project_config.path)

        assert success is True
        args = mock_run.call_args[0][0]
        assert args[0] == "my-ai"
        assert args[1] == "--prompt"
        assert args[2] == "Fix the bug"
        assert args[3] == "--dir"
        assert str(project_config.path) in args

    def test_run_claude_custom_mode_missing_command(
        self,
        gitlab_client: GitLabClient,
        discord_webhook: DiscordWebhook,
        state_manager: StateManager,
        project_config: ProjectConfig,
    ) -> None:
        """Test custom mode returns error when command not set."""
        processor = Processor(
            gitlab=gitlab_client,
            discord=discord_webhook,
            state=state_manager,
            gitlab_username="claude",
            label_in_progress="In progress",
            label_review="Review",
            claude_mode="custom",
            claude_custom_command="",
        )

        success, output = processor._run_claude("Fix the bug", project_config.path)

        assert success is False
        assert "CLAUDE_CUSTOM_COMMAND" in output

    def test_run_claude_invalid_mode(
        self,
        gitlab_client: GitLabClient,
        discord_webhook: DiscordWebhook,
        state_manager: StateManager,
        project_config: ProjectConfig,
    ) -> None:
        """Test invalid mode returns error."""
        processor = Processor(
            gitlab=gitlab_client,
            discord=discord_webhook,
            state=state_manager,
            gitlab_username="claude",
            label_in_progress="In progress",
            label_review="Review",
            claude_mode="invalid",
        )

        success, output = processor._run_claude("Fix the bug", project_config.path)

        assert success is False
        assert "Unknown CLAUDE_MODE" in output
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_processor.py::TestProcessorClaudeModes -v`
Expected: FAIL with TypeError (missing constructor arguments)

**Step 3: Update Processor constructor**

```python
# src/gitlab_watcher/processor.py - modify __init__

def __init__(
    self,
    gitlab: GitLabClient,
    discord: DiscordWebhook,
    state: StateManager,
    gitlab_username: str,
    label_in_progress: str,
    label_review: str,
    claude_mode: str = "ollama",
    claude_custom_command: str = "",
) -> None:
    """Initialize processor.

    Args:
        gitlab: GitLab API client
        discord: Discord webhook client
        state: State manager
        gitlab_username: GitLab username for filtering comments
        label_in_progress: Label for in-progress issues
        label_review: Label for issues under review
        claude_mode: Claude CLI mode (ollama, direct, custom)
        claude_custom_command: Custom command for custom mode
    """
    self.gitlab = gitlab
    self.discord = discord
    self.state = state
    self.gitlab_username = gitlab_username
    self.label_in_progress = label_in_progress
    self.label_review = label_review
    self.claude_mode = claude_mode
    self.claude_custom_command = claude_custom_command
```

**Step 4: Update _run_claude method**

```python
# src/gitlab_watcher/processor.py - replace _run_claude method

def _run_claude(self, prompt: str, repo_path: Path) -> tuple[bool, str]:
    """Run Claude CLI with a prompt based on configured mode.

    Args:
        prompt: The prompt for Claude
        repo_path: Path to the repository

    Returns:
        Tuple of (success, output)
    """
    # Build command based on mode
    if self.claude_mode == "ollama":
        cmd = ["ollama", "launch", "claude", "--", "-p", "--permission-mode", "acceptEdits", prompt]
    elif self.claude_mode == "direct":
        cmd = ["claude", "-p", "--permission-mode", "acceptEdits", prompt]
    elif self.claude_mode == "custom":
        if not self.claude_custom_command:
            return False, "CLAUDE_CUSTOM_COMMAND not set for custom mode"
        cmd_str = self.claude_custom_command.replace("{prompt}", prompt).replace("{cwd}", str(repo_path))
        cmd = cmd_str.split()
    else:
        return False, f"Unknown CLAUDE_MODE: {self.claude_mode}"

    try:
        env = {"CLAUDECODE": ""}
        result = subprocess.run(
            cmd,
            cwd=repo_path,
            capture_output=True,
            text=True,
            env=env,
            timeout=600,
        )
        return result.returncode == 0, result.stdout + result.stderr
    except subprocess.TimeoutExpired:
        return False, "Claude timed out"
    except FileNotFoundError:
        return False, "Claude CLI not found"
```

**Step 5: Run test to verify it passes**

Run: `pytest tests/test_processor.py::TestProcessorClaudeModes -v`
Expected: PASS

**Step 6: Run all processor tests**

Run: `pytest tests/test_processor.py -v`
Expected: PASS (ensure no regression)

**Step 7: Commit**

```bash
git add src/gitlab_watcher/processor.py tests/test_processor.py
git commit -m "feat: add Claude CLI mode support to Processor"
```

---

### Task 3: Wire config to Watcher

**Files:**
- Modify: `src/gitlab_watcher/watcher.py`
- Test: `tests/test_watcher.py`

**Step 1: Write the failing test**

```python
# tests/test_watcher.py - add to TestWatcherInit class

def test_watcher_passes_claude_mode_to_processor(
    self,
    config_file: Path,
    mock_gitlab: MagicMock,
    mock_discord: MagicMock,
    state_manager: StateManager,
) -> None:
    """Test that Watcher passes claude_mode to Processor."""
    # Update config to include CLAUDE_MODE
    content = config_file.read_text()
    content = content.replace('GITLAB_TOKEN="test-token"', 'GITLAB_TOKEN="test-token"\nCLAUDE_MODE="direct"')
    config_file.write_text(content)

    watcher = Watcher(
        config_path=str(config_file),
        gitlab=mock_gitlab,
        discord=mock_discord,
        state=state_manager,
    )

    assert watcher.processor.claude_mode == "direct"
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_watcher.py::TestWatcherInit::test_watcher_passes_claude_mode_to_processor -v`
Expected: FAIL with AttributeError or assertion error

**Step 3: Update Watcher to pass config to Processor**

```python
# src/gitlab_watcher/watcher.py - modify Processor instantiation

self.processor = processor or Processor(
    gitlab=self.gitlab,
    discord=self.discord,
    state=self.state,
    gitlab_username=self.config.gitlab_username,
    label_in_progress=self.config.label_in_progress,
    label_review=self.config.label_review,
    claude_mode=self.config.claude_mode,
    claude_custom_command=self.config.claude_custom_command,
)
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_watcher.py::TestWatcherInit::test_watcher_passes_claude_mode_to_processor -v`
Expected: PASS

**Step 5: Run all tests**

Run: `pytest -v`
Expected: PASS (ensure no regression)

**Step 6: Commit**

```bash
git add src/gitlab_watcher/watcher.py tests/test_watcher.py
git commit -m "feat: pass Claude mode config from Watcher to Processor"
```

---

### Task 4: Final verification and coverage

**Step 1: Run full test suite with coverage**

Run: `pytest --cov=gitlab_watcher --cov-report=term-missing`
Expected: All tests pass, coverage >= 90%

**Step 2: Run linting/type checking if available**

Run: `ruff check src/` or `flake8 src/` if configured
Expected: No errors

**Step 3: Final commit (if any fixes needed)**

```bash
git add .
git commit -m "test: ensure coverage for Claude mode feature"
```

---

## Summary

| Task | Files Changed | Tests Added |
|------|---------------|-------------|
| 1. Config fields | config.py, test_config.py | 2 |
| 2. Processor modes | processor.py, test_processor.py | 5 |
| 3. Watcher wiring | watcher.py, test_watcher.py | 1 |
| 4. Verification | - | - |

**Total new tests: 8**