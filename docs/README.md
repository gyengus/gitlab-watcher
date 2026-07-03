# GitLab Watcher - Detailed Documentation

This documentation contains a comprehensive technical description of the GitLab Watcher project.

---

## Table of Contents

1. [Project Overview and Goal](#1-project-overview-and-goal)
2. [Detailed Architecture Description](#2-detailed-architecture-description)
3. [Installation Guide](#3-installation-guide)
4. [Configuration Options](#4-configuration-options)
5. [Operational Processes](#5-operational-processes)
6. [State Management Details](#6-state-management-details)
7. [API Integrations](#7-api-integrations)
8. [Error Handling and Recovery Mechanisms](#8-error-handling-and-recovery-mechanisms)
9. [Developer Guide](#9-developer-guide)
10. [Potential Development Directions](#10-potential-development-directions)

---

## 1. Project Overview and Goal

### 1.1 Objective

**GitLab Watcher** is a Python-based daemon that automates software development workflows in a GitLab environment. The system uses Claude CLI for AI-assisted coding, allowing automatic processing of issues and responding to merge request comments.

### 1.2 Key Features

| Feature | Description |
|---------|--------|
| **Issue Processing** | Automatic processing of issues assigned to the configured user |
| **MR Comment Processing** | Automatic response to merge request comments |
| **Post-Merge Cleanup** | Automatic cleanup after merged MRs (branch deletion, master update) |
| **Discord Notifications** | Real-time notifications via a Discord webhook |
| **State Persistence** | State persistence to track processes |

### 1.3 Tech Stack

- **Python 3.11+**: Base programming language
- **Click**: CLI framework
- **requests**: HTTP client for GitLab API communication
- **subprocess**: Git operations and Claude CLI execution
- **dataclasses**: Data structure definitions

### 1.4 Requirements

- Python 3.11 or newer
- Git installed and configured
- GitLab access (Personal Access Token)
- Claude CLI or Ollama (optional Discord webhook)

---

## 2. Detailed Architecture Description

### 2.1 Modules Overview

```
src/gitlab_watcher/
├── __init__.py          # Package initialization, version definition
├── __main__.py          # python -m gitlab_watcher entry point
├── cli.py               # Click CLI entry point
├── watcher.py           # Main monitoring loop
├── processor.py         # Business logic (issue/MR processing)
├── gitlab_client.py     # GitLab API client
├── git_ops.py           # Git operations wrapper
├── config.py            # Configuration management
├── state.py             # State persistence
└── discord.py           # Discord webhook notifications
```

### 2.2 Detailed Module Descriptions

#### 2.2.1 `cli.py` - Command Line Interface

```python
@click.command()
@click.option("--config", "-c", default=DEFAULT_CONFIG_PATH, help="Path to config file")
@click.option("--verbose", "-v", is_flag=True, help="Verbose output")
def main(config: str, verbose: bool) -> None:
    """GitLab Watcher - Monitor projects and process issues/MRs."""
    watcher = Watcher(config_path=config, verbose=verbose)
    watcher.run()
```

**Features:**
- Simple CLI interface using the Click framework
- Configuration file path customization
- Verbose mode for debugging purposes

#### 2.2.2 `watcher.py` - Central Coordinator

The `Watcher` class is the central coordinator of the system, which:
- Initializes all dependencies (GitLab client, Discord webhook, State manager, Processor)
- Executes the main monitoring loop
- Handles issue and MR status checks

**Key Methods:**

| Method | Responsibility |
|---------|------------|
| `__init__()` | Initialization, configuration loading, dependency injection |
| `_extract_from_remote()` | Extract GitLab URL and token from git remote URL |
| `check_issues()` | Find and process new issues |
| `check_mr_status()` | Check MR status (merge, comment) |
| `run()` | Main loop |

**Dependency Injection:**
The `Watcher` supports dependency injection, making it testable:

```python
def __init__(
    self,
    config_path: str = DEFAULT_CONFIG_PATH,
    verbose: bool = False,
    *,
    gitlab: Optional[GitLabClient] = None,
    discord: Optional[DiscordWebhook] = None,
    processor: Optional[Processor] = None,
    state: Optional[StateManager] = None,
) -> None:
```

#### 2.2.3 `processor.py` - Business Logic

The `Processor` class is responsible for the actual processing of issues and MR comments.

**Key Methods:**

| Method | Description |
|---------|--------|
| `_run_claude()` | Execute Claude CLI with a given prompt |
| `process_issue()` | Process issue: create branch, run Claude, create MR |
| `process_comment()` | Process MR comment: check out branch, run Claude, push |
| `cleanup_after_merge()` | Clean up after merge: update master, delete branch |

**AI Tool Modes:**

The system supports four modes for running the AI tool:

| Mode | Command | Description |
|-----|---------|--------|
| `ollama` | `ollama launch claude -- -p --permission-mode acceptEdits "<prompt>"` | Default mode, via Ollama container |
| `direct` | `claude -p --permission-mode acceptEdits "<prompt>"` | Direct Claude CLI call |
| `opencode` | `opencode "<prompt>"` | Using Opencode CLI |
| `custom` | User-defined command | Flexible, custom configuration for any AI tool |

**Prompt Structure:**

For issue processing:
```text
You are working on issue #{issue.iid}: {issue.title}

Issue description:
{issue.description}

Please complete this task. Make the necessary changes and commit them.
Write commit messages in English.
Do not use conventional commit prefixes like feat:, fix:, etc.
Do not add Co-Authored-By signature to commits.
```

For MR comment processing:
```text
You are working on a merge request titled: {mr.title}
Branch: {mr.source_branch}

A reviewer left this feedback:
{comment}

Please address this feedback. Make the necessary changes and commit them.
Write commit messages in English.
Do not use conventional commit prefixes like feat:, fix:, etc.
Do not add Co-Authored-By signature to commits.
```

#### 2.2.4 `gitlab_client.py` - GitLab API Client

The `GitLabClient` class implements the GitLab REST API v4 interface.

**Data Structures:**

```python
@dataclass
class Issue:
    iid: int
    title: str
    description: str
    web_url: str
    labels: list[str]

@dataclass
class MergeRequest:
    iid: int
    title: str
    web_url: str
    source_branch: str
    state: str

@dataclass
class Note:
    id: int
    body: str
    author_username: str
```

**API Methods:**

| Method | Endpoint | Description |
|---------|---------|--------|
| `get_issues()` | `GET /projects/:id/issues` | List issues |
| `get_merge_requests()` | `GET /projects/:id/merge_requests` | List MRs |
| `get_merge_request()` | `GET /projects/:id/merge_requests/:iid` | Fetch a single MR |
| `get_notes()` | `GET /projects/:id/merge_requests/:iid/notes` | List comments |
| `update_issue_labels()` | `PUT /projects/:id/issues/:iid` | Update issue labels |
| `create_merge_request()` | `POST /projects/:id/merge_requests` | Create MR |

**Retry Logic:**

The client implements automatic retries for 5xx errors:

```python
def _request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
    """Make HTTP request with retry logic for 5xx errors."""
    last_error: Optional[Exception] = None

    for attempt in range(self.max_retries):
        try:
            response = self.session.request(method, url, **kwargs)
            if response.status_code >= 500:
                last_error = Exception(f"Server error {response.status_code}")
                time.sleep(self.retry_delay * (attempt + 1))
                continue
            return response
        except requests.RequestException as e:
            last_error = e
            time.sleep(self.retry_delay * (attempt + 1))

    raise RuntimeError(f"Request failed after {self.max_retries} retries: {last_error}")
```

#### 2.2.5 `git_ops.py` - Git Operations

The `GitOps` class wraps Git commands using subprocess calls.

**Methods:**

| Method | Git Command | Description |
|---------|-------------|--------|
| `fetch(remote)` | `git fetch <remote>` | Update remote repository |
| `checkout(branch, create)` | `git checkout [-b] <branch>` | Switch/create branch |
| `pull(remote, branch)` | `git pull [<remote> [<branch>]]` | Pull changes |
| `push(remote, branch, set_upstream)` | `git push [-u] <remote> <branch>` | Push changes |
| `delete_branch(branch, force)` | `git branch -D|-d <branch>` | Delete branch |
| `branch_exists(branch)` | `git rev-parse --verify <branch>` | Check if branch exists |
| `get_current_branch()` | `git rev-parse --abbrev-ref HEAD` | Get current branch name |
| `get_remote_url(remote)` | `git config --get remote.<remote>.url` | Get remote URL |
| `generate_slug(title)` | - | Generate URL-friendly slug (static) |

**Slug Generation:**

For automatic generation of branch names:

```python
@staticmethod
def generate_slug(title: str, max_length: int = 30) -> str:
    slug = title.lower()
    slug = "".join(c if c.isalnum() else "-" for c in slug)
    while "--" in slug:
        slug = slug.replace("--", "-")
    slug = slug.strip("-")
    return slug[:max_length]
```

Example: `"Fix bug #123!!!"` → `"fix-bug-123"`

#### 2.2.6 `config.py` - Configuration Management

Configuration is loaded from Bash-style files.

**Data Structures:**

```python
@dataclass
class ProjectConfig:
    project_id: int
    path: Path
    name: str

@dataclass
class Config:
    gitlab_url: str = ""
    gitlab_token: str = ""
    discord_webhook: str = ""
    label_in_progress: str = "In progress"
    label_review: str = "Review"
    gitlab_username: str = "claude"
    poll_interval: int = 30
    ai_tool_mode: str = "ollama"
    ai_tool_custom_command: str = ""
    project_dirs: list[str] = field(default_factory=list)
    projects: list[ProjectConfig] = field(default_factory=list)
```

**Configuration File Format:**

```bash
# Basic settings
GITLAB_URL="https://git.example.com"
GITLAB_TOKEN="your-token"
DISCORD_WEBHOOK="https://discord.com/api/webhooks/..."

# Workflow labels
LABEL_IN_PROGRESS="In progress"
LABEL_REVIEW="Review"

# User and timing
GITLAB_USERNAME="claude"
POLL_INTERVAL=30

# AI tool mode
AI_TOOL_MODE="ollama"
AI_TOOL_CUSTOM_COMMAND=""

# Projects
PROJECT_DIRS=(
  "/path/to/project1"
  "/path/to/project2"
)
```

**Project Discovery:**

The system automatically discovers projects based on `PROJECT.md` files in the `PROJECT_DIRS` directories. The line `Project ID: <number>` in the file defines the GitLab project ID.

Supported formats:
- `Project ID: 31`
- `Project ID: **31**`
- `project_id: 31`

#### 2.2.7 `state.py` - State Management

The `StateManager` class persists project states into JSON files.

**State Structure:**

```python
@dataclass
class ProjectState:
    last_mr_iid: Optional[int] = None      # Last MR IID
    last_mr_state: Optional[str] = None     # Last MR status
    last_note_id: int = 0                    # Last processed comment ID
    last_branch: Optional[str] = None        # Last branch name
    processing: bool = False                 # Processing in progress indicator
```

**Key Methods:**

| Method | Description |
|---------|--------|
| `load(project_id)` | Load state (cached) |
| `init_state(project_id)` | Initialization on startup (processing=False) |
| `save(project_id)` | Save state to file |
| `is_processing(project_id)` | Check processing state |
| `set_processing(project_id, bool)` | Set processing flag |
| `update_mr_state(...)` | Update MR state |
| `reset(project_id)` | Reset state completely |

**File Location:**

```
/tmp/gitlab-watcher/
├── state_42.json    # State of project 42
├── state_31.json    # State of project 31
└── ...
```

#### 2.2.8 `discord.py` - Discord Notifications

The `DiscordWebhook` class sends Discord webhook messages.

**Notification Types:**

| Method | Emoji | Event |
|---------|-------|---------|
| `notify_issue_started()` | 🚀 | Issue processing start |
| `notify_mr_created()` | ✅ | MR creation |
| `notify_changes_applied()` | ✅ | Comment-based changes applied |
| `notify_mr_merged()` | ✅ | MR merge |
| `notify_cleanup_complete()` | 🧹 | Cleanup complete |
| `notify_error()` | ❌ | On error |

---

## 3. Installation Guide

### 3.1 System Requirements

- Python 3.11 or newer
- Git (installed and available in PATH)
- Claude CLI or Ollama (for AI execution)

### 3.2 Installation from Source

```bash
# Clone the repository
git clone https://git.gyengus.hu/gyengus/gitlab-watcher.git
cd gitlab-watcher

# Install in development mode (recommended)
pip install -e ".[dev]"

# Or normal installation
pip install .
```

### 3.3 Dependencies

Based on `pyproject.toml`:

**Main Dependencies:**
- `click>=8.0.0` - CLI framework
- `requests>=2.28.0` - HTTP client

**Development Dependencies:**
- `pytest>=7.0.0` - Test framework
- `pytest-cov>=4.0.0` - Code coverage

### 3.4 Setting up Configuration

1. Create the configuration directory and file:

```bash
mkdir -p ~/.config/gitlab-watcher
cp gitlab-watcher.conf ~/.config/gitlab-watcher/config.conf
```

2. Fill in the configuration:

```bash
# GitLab connection
GITLAB_URL="https://your-gitlab-instance.com"
GITLAB_TOKEN="your-personal-access-token"

# Discord webhook (optional)
DISCORD_WEBHOOK="https://discord.com/api/webhooks/..."

# Workflow labels (customizable)
LABEL_IN_PROGRESS="In progress"
LABEL_REVIEW="Review"

# Monitored user
GITLAB_USERNAME="claude"

# Polling interval (seconds)
POLL_INTERVAL=30

# AI tool mode: ollama, direct, opencode, custom
AI_TOOL_MODE="ollama"

# Projects
PROJECT_DIRS=(
  "/path/to/project1"
  "/path/to/project2"
)
```

3. Create a `PROJECT.md` file for each project:

```markdown
Project ID: 42

# Project Documentation...

## Build Commands
...
```

### 3.5 Running

```bash
# With default configuration
gitlab-watcher

# With custom configuration
gitlab-watcher -c /path/to/config.conf

# In verbose mode
gitlab-watcher --verbose
```

---

## 4. Configuration Options

### 4.1 Full Configuration Reference

| Variable | Type | Default | Description |
|---------|-------|-----------------|--------|
| `GITLAB_URL` | string | - | GitLab server URL |
| `GITLAB_TOKEN` | string | - | Personal Access Token |
| `DISCORD_WEBHOOK` | string | "" | Discord webhook URL (optional) |
| `LABEL_IN_PROGRESS` | string | "In progress" | Name of the "In progress" label |
| `LABEL_REVIEW` | string | "Review" | Name of the "Review" label |
| `GITLAB_USERNAME` | string | "claude" | Monitored GitLab username |
| `POLL_INTERVAL` | int | 30 | Polling interval (seconds) |
| `AI_TOOL_MODE` | string | "ollama" | AI tool mode |
| `AI_TOOL_CUSTOM_COMMAND` | string | "" | Custom command (for custom mode) |
| `PROJECT_DIRS` | array | [] | List of project directories |

### 4.2 Obtaining a GitLab Token

1. Log in to GitLab
2. Go to **Settings > Access Tokens**
3. Create a new token with the following scopes:
   - `api` - Full API access
   - `write_repository` - Repository write access

### 4.3 Automatic Discovery of GitLab URL and Token

If `GITLAB_URL` and `GITLAB_TOKEN` are not specified in the configuration, the system attempts to extract them from the git remote URL:

```
https://token@git.example.com/group/project.git  → URL: https://git.example.com, Token: token
https://user:token@git.example.com/group/project.git  → URL: https://git.example.com, Token: token
```

### 4.4 AI Tool Modes

#### Ollama Mode (Default)

```bash
AI_TOOL_MODE="ollama"
```

Prerequisite: Ollama installed and `claude` model present.

#### Direct Mode

```bash
AI_TOOL_MODE="direct"
```

Direct Claude CLI call. Prerequisite: `claude` command available in PATH.

#### Opencode Mode

```bash
AI_TOOL_MODE="opencode"
```

Using Opencode CLI. Prerequisite: `opencode` command available in PATH.

#### Custom Mode

```bash
AI_TOOL_MODE="custom"
# Note: Separating flags and values is recommended for security
AI_TOOL_CUSTOM_COMMAND="my-ai-tool --prompt {prompt} --workdir {cwd}"
```

Defining a custom command for any AI tool. Available placeholders:
- `{prompt}` - The prompt text (required)
- `{cwd}` - The path of the working directory (optional)
- `{agent}` - The resolved agent name (optional, parsed from labels)

**Important:** The working directory is automatically set before running the command.
Only use the `{cwd}` placeholder if the AI tool requires an explicit directory parameter.

Examples:
```bash
# The tool operates in the current directory - {cwd} not needed
AI_TOOL_MODE="custom"
AI_TOOL_CUSTOM_COMMAND="my-claude --prompt {prompt}"

# The tool requires an explicit directory parameter
AI_TOOL_MODE="custom"
AI_TOOL_CUSTOM_COMMAND="my-opencode --task {prompt} --workspace {cwd}"

# OpenCode custom command with agent support
AI_TOOL_MODE="opencode-custom"
AI_TOOL_CUSTOM_COMMAND="opencode --agent {agent} run {prompt}"
```

#### Selecting OpenCode Agents via Labels

You can dynamically select which OpenCode agent should process a specific issue or merge request by using GitLab labels:

1. **Issue Labeling**: Assign a label in the format `agent:[Agent Name]` (or `agent-[Agent Name]`, e.g., `agent:Senior frontend developer`) to the issue.
2. **Mutual Exclusion**: If multiple agent labels are assigned, the watcher automatically retains the first one and removes the other redundant ones from the issue to ensure clarity.
3. **MR Label Inheritance**: When the MR is created for the issue, it automatically inherits the selected agent label.
4. **Dynamic MR Comment Handling**: You can change the agent on the MR at any time (e.g. from `agent:Senior frontend developer` to `agent:Senior software testing expert`). The watcher will always use the agent currently labeled on the MR when processing new reviewer comments.
5. **Custom Commands**: You can use the `{agent}` placeholder in your custom command template. If no agent label is present on the task, the placeholder evaluates to an empty string `""` and the watcher lets OpenCode default to its own configured agent.
6. **Discord Notifications**: The name of the selected agent is displayed in the Discord start notifications and comment processing messages.

---

## 5. Operational Processes

### 5.1 Main Monitoring Loop

```
┌─────────────────────────────────────┐
│           Watcher.run()             │
│                                     │
│  ┌─────────────────────────────────┐│
│  │  for each project:              ││
│  │    check_mr_status(project)     ││
│  │    check_issues(project)        ││
│  └─────────────────────────────────┘│
│                │                    │
│                ▼                    │
│         sleep(POLL_INTERVAL)        │
│                                     │
└─────────────────────────────────────┘
```

### 5.2 Issue Processing Flow

```
┌────────────────────────────────────────────────────────────────┐
│                    Issue Processing                            │
│ -------------------------------------------------------------- │
│                                                                │
│  1. check_issues()                                             │
│     ├── Get issues assigned to GITLAB_USERNAME                 │
│     ├── Filter: no "In progress" AND no "Review" label         │
│     └── First matching issue → process_issue()                 │
│                                                                │
│  2. process_issue()                                            │
│     ├── Set processing flag = True                             │
│     ├── Add "In progress" label to issue                       │
│     ├── Discord: "Starting Issue" notification                 │
│     ├── Git: fetch, checkout master, pull                      │
│     ├── Git: checkout -b {iid}-{slug}                          │
│     ├── Run Claude CLI with issue description                  │
│     ├── Git: push -u origin {branch}                           │
│     ├── GitLab: create_merge_request()                         │
│     ├── GitLab: update_issue_labels(["Review"])                │
│     ├── Discord: "MR Created" notification                     │
│     └── Set processing flag = False                            │
│                                                                │
│  Branch naming: {issue_iid}-{slugified_title}                  │
│  Example: 42-fix-login-bug                                     │
│                                                                │
└────────────────────────────────────────────────────────────────┘
```

### 5.3 MR Comment Processing Flow

```
┌────────────────────────────────────────────────────────────────┐
│                 MR Comment Processing                           │
│ -------------------------------------------------------------- │
│                                                                │
│  1. check_mr_status()                                          │
│     ├── Get open MRs by GITLAB_USERNAME                        │
│     ├── Get latest note on MR                                  │
│     ├── Update state (mr_iid, mr_state, note_id, branch)       │
│     └── If new note AND not from GITLAB_USERNAME:              │
│         └── process_comment()                                  │
│                                                                │
│  2. process_comment()                                          │
│     ├── Set processing flag = True                             │
│     ├── Discord: "Processing Comment" notification             │
│     ├── Git: fetch, checkout {source_branch}                   │
│     ├── Git: pull origin {source_branch}                       │
│     ├── Run Claude CLI with comment text                       │
│     ├── Git: push origin {source_branch}                       │
│     ├── Discord: "Changes Applied" notification                │
│     └── Set processing flag = False                            │
│                                                                │
└────────────────────────────────────────────────────────────────┘
```

### 5.4 Post-Merge Cleanup Flow

```
┌────────────────────────────────────────────────────────────────┐
│                   Post-Merge Cleanup                            │
│ -------------------------------------------------------------- │
│                                                                │
│  1. check_mr_status()                                          │
│     ├── If state.last_mr_iid is set:                           │
│     │   └── Get MR by iid                                      │
│     └── If MR state == "merged":                               │
│         └── cleanup_after_merge()                              │
│                                                                │
│  2. cleanup_after_merge()                                      │
│     ├── Discord: "MR Merged" notification                      │
│     ├── Git: checkout master                                   │
│     ├── Git: pull                                              │
│     ├── Git: delete branch -D {branch}                         │
│     ├── Discord: "Cleanup complete" notification               │
│     └── State: reset()                                         │
│                                                                │
└────────────────────────────────────────────────────────────────┘
```

### 5.5 Workflow Lifecycle Diagram

```
    ┌─────────┐         ┌─────────────┐         ┌───────────┐
    │  Issue  │         │ Branch + MR │         │   Merged  │
    │ (new)   │ ──────> │   (open)    │ ──────> │  (closed) │
    └─────────┘         └─────────────┘         └───────────┘
         │                     │                       │
         │                     │                       │
         ▼                     ▼                       ▼
    ┌────────────┐        ┌──────────────┐     ┌──────────────┐
    │   Add      │        │  Process MR  │     │   Cleanup    │
    │ "In        │        │  comments    │     │   branch     │
    │ progress"  │        │  (iterate)   │     │   delete     │
    └────────────┘        └──────────────┘     └──────────────┘
         │                     │                       │
         │                     │                       │
         ▼                     ▼                       ▼
    ┌────────────┐        ┌──────────────┐     ┌──────────────┐
    │   Add      │        │   Update     │     │  Reset       │
    │  "Review"  │        │   MR code    │     │  state       │
    └────────────┘        └──────────────┘     └──────────────┘
```

---

## 6. State Management Details

### 6.1 State File Structure

The state is stored in JSON files:

```json
{
  "last_mr_iid": 42,
  "last_mr_state": "opened",
  "last_note_id": 12345,
  "last_branch": "31-add-new-feature",
  "processing": false
}
```

### 6.2 State Flow

```
┌─────────────────────────────────────────────────────────────┐
│                      State Transitions                      │
│ ----------------------------------------------------------- │
│                                                             │
│  Start → init_state()                                       │
│            ├── Load from file (if exists)                   │
│            ├── Reset processing=False                       │
│            └── Save to file                                 │
│                                                             │
│  Process Issue Start:                                       │
│            set_processing(project_id, True)                 │
│                                                             │
│  Process Issue End:                                         │
│            update_mr_state(iid, "opened", note_id, branch)  │
│            set_processing(project_id, False)                │
│                                                             │
│  Process Comment Start:                                     │
│            set_processing(project_id, True)                 │
│                                                             │
│  Process Comment End:                                       │
│            set_processing(project_id, False)                │
│                                                             │
│  Merge Detected:                                            │
│            cleanup_after_merge()                            │
│            reset(project_id)                                │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### 6.3 Crash Recovery

The `processing` flag allows crash recovery:

- **On startup**: `init_state()` for all projects → `processing=False`
- **During processing**: `processing=True` → prevents re-starting processes
- **Upon completion**: `processing=False`

This ensures that a partially processed issue is not re-processed upon restart after a crash.

---

## 7. API Integrations

### 7.1 GitLab API v4

The system uses the GitLab REST API v4.

**Endpoints Used:**

| Endpoint | Method | Usage |
|---------|---------|-----------|
| `/projects/:id/issues` | GET | List issues |
| `/projects/:id/issues/:iid` | PUT | Update issue labels |
| `/projects/:id/merge_requests` | GET, POST | List, create MRs |
| `/projects/:id/merge_requests/:iid` | GET | Fetch a single MR |
| `/projects/:id/merge_requests/:iid/notes` | GET | List comments |

**Query Parameters:**

```python
# Fetch issues
get_issues(
    project_id=42,
    state="opened",
    assignee_username="claude"
)

# Fetch MRs
get_merge_requests(
    project_id=42,
    state="opened",
    author_username="claude"
)

# Fetch comments
get_notes(
    project_id=42,
    mr_iid=1,
    sort="desc"  # Descending order (newest first)
)
```

### 7.2 AI Tool CLI Integration

The AI tool call takes place in the `Processor._run_claude()` method:

```python
def _run_claude(self, prompt: str, repo_path: Path) -> tuple[bool, str]:
    # Build command based on mode
    if self.ai_tool_mode == "ollama":
        cmd = ["ollama", "launch", "claude", "--", "-p", "--permission-mode", "acceptEdits", prompt]
    elif self.ai_tool_mode == "direct":
        cmd = ["claude", "-p", "--permission-mode", "acceptEdits", prompt]
    elif self.ai_tool_mode == "custom":
        cmd = [part.replace("{prompt}", prompt).replace("{cwd}", str(repo_path))
               for part in shlex.split(self.ai_tool_custom_command)]
    elif self.ai_tool_mode == "opencode":
        cmd = ["opencode", prompt]
    elif self.ai_tool_mode == "opencode-custom":
        cmd = [part.replace("{prompt}", prompt).replace("{cwd}", str(repo_path))
               for part in shlex.split(self.ai_tool_custom_command)]

    env = {"CLAUDECODE": ""}  # Set environment variable

    result = subprocess.run(
        cmd,
        cwd=repo_path,
        capture_output=True,
        text=True,
        env=env,
        timeout=600,  # 10 minutes timeout
    )
    return result.returncode == 0, result.stdout + result.stderr
```

**Important Parameters:**
- `--permission-mode acceptEdits`: Automatic editing permission
- `-p`: Non-interactive mode
- `CLAUDECODE=""`: Prevent environment variable conflicts
- 600-second timeout: For longer running operations

### 7.3 Discord Webhook

The Discord webhook uses simple JSON POST requests:

```python
response = requests.post(
    webhook_url,
    json={"content": message},
    headers={"Content-Type": "application/json"},
    timeout=10,
)
# Successful response: HTTP 204 (No Content)
```

---

## 8. Error Handling and Recovery Mechanisms

### 8.1 GitLab API Errors

The `GitLabClient` implements retry logic:

```python
# Default: 3 retries, 1 second delay
client = GitLabClient(
    url="https://git.example.com",
    token="token",
    max_retries=3,
    retry_delay=1.0,
)

# Retry Conditions:
# - 5xx server errors
# - Network errors (ConnectionError, Timeout)
# NO retry on 4xx client errors
```

### 8.2 Git Operations Errors

GitOps methods use boolean return values:

```python
# Successful operation
if git.checkout(branch, create=True):
    # proceed
else:
    # error handling
```

Errors are logged and the process is safely aborted.

### 8.3 Claude CLI Errors

```python
try:
    result = subprocess.run(cmd, cwd=repo_path, timeout=600, ...)
    return result.returncode == 0, result.stdout + result.stderr
except subprocess.TimeoutExpired:
    return False, "Claude timed out"
except FileNotFoundError:
    return False, "Claude CLI not found"
```

**Types of Errors:**
- Timeout (after 600s)
- CLI not found
- Non-zero exit code

### 8.5 Main Loop Error Handling

```python
while True:
    try:
        for project in self.config.projects:
            self.check_mr_status(project)
            self.check_issues(project)
        time.sleep(self.config.poll_interval)
    except KeyboardInterrupt:
        print("\nShutting down...")
        break
    except Exception as e:
        self.logger.error(f"Error in main loop: {e}")
        time.sleep(self.config.poll_interval)  # Continue after sleeping
```

---

## 9. Developer Guide

### 9.1 Project Structure

```
gitlab-watcher/
├── src/gitlab_watcher/      # Source code
│   ├── __init__.py
│   ├── __main__.py
│   ├── cli.py
│   ├── watcher.py
│   ├── processor.py
│   ├── gitlab_client.py
│   ├── git_ops.py
│   ├── config.py
│   ├── state.py
│   └── discord.py
├── tests/                    # Tests
│   ├── test_watcher.py
│   ├── test_processor.py
│   ├── test_gitlab_client.py
│   ├── test_git_ops.py
│   ├── test_config.py
│   ├── test_discord.py
│   └── test_config_extra.py
├── docs/                     # Documentation
│   └── plans/
├── pyproject.toml            # Project configuration
├── README.md                 # Quick start
└── CLAUDE.md                 # Project ID and developer notes
```

### 9.2 Testing

**Running Tests:**

```bash
# All tests
pytest

# Verbose output
pytest -v

# Coverage report
pytest --cov=gitlab_watcher --cov-report=term-missing

# A single test file
pytest tests/test_watcher.py

# A single test
pytest tests/test_watcher.py::TestWatcherCheckIssues::test_check_issues_with_backlog_issue
```

**Test Structure:**

Tests use pytest fixtures:

```python
@pytest.fixture
def gitlab_client() -> GitLabClient:
    return GitLabClient(url="https://git.example.com", token="test-token")

@pytest.fixture
def state_manager(tmp_path: Path) -> StateManager:
    return StateManager(tmp_path / "work")

@pytest.fixture
def processor(gitlab_client, discord_webhook, state_manager) -> Processor:
    return Processor(
        gitlab=gitlab_client,
        discord=discord_webhook,
        state=state_manager,
        gitlab_username="claude",
        label_in_progress="In progress",
        label_review="Review",
    )
```

**Mocked Tests:**

External dependencies (GitLab API, Git, Claude CLI) are mocked:

```python
@patch("subprocess.run")
def test_run_claude_success(mock_run, processor, project_config):
    mock_run.return_value = Mock(returncode=0, stdout="Done", stderr="")
    success, output = processor._run_claude("Fix the bug", project_config.path)
    assert success is True
```

### 9.3 Code Quality

**Type Hints:**

The project uses comprehensive type annotations following modern standards:

```python
def process_issue(
    self,
    project: ProjectConfig,
    issue: Issue,
) -> bool:
    ...
```

**Docstrings:**

All public methods have docstrings in Google style:

```python
def _run_claude(self, prompt: str, repo_path: Path) -> tuple[bool, str]:
    """Run Claude CLI with a prompt based on configured mode.

    Args:
        prompt: The prompt for Claude
        repo_path: Path to the repository

    Returns:
        Tuple of (success, output)
    """
```

### 9.4 Dependency Injection

The `Watcher` class supports dependency injection for testability:

```python
# Normal usage
watcher = Watcher(config_path="config.conf")

# Testing with mocks
mock_gitlab = MagicMock(spec=GitLabClient)
mock_discord = MagicMock(spec=DiscordWebhook)
mock_processor = MagicMock(spec=Processor)
state_manager = StateManager(temp_dir)

watcher = Watcher(
    config_path="config.conf",
    gitlab=mock_gitlab,
    discord=mock_discord,
    processor=mock_processor,
    state=state_manager,
)
```

### 9.5 Setting up the Development Environment

```bash
# Clone the repository
git clone https://git.gyengus.hu/gyengus/gitlab-watcher.git
cd gitlab-watcher

# Create virtual environment
python -m venv venv
source venv/bin/activate

# Install development dependencies
pip install -e ".[dev]"

# Run tests
pytest
```

---

## 10. Potential Development Directions

### 10.1 Short-term Improvements

| Feature | Description | Priority |
|---------|--------|-----------|
| **Logging Improvements** | Structured logging, log file rotation | High |
| **Error Notifications** | More detailed error messages on Discord | High |
| **Configuration Validation** | Early detection of configuration errors | Medium |
| **Retry Policy** | Customizable retry strategy | Medium |

### 10.2 Medium-term Improvements

| Feature | Description |
|---------|--------|
| **Multiple GitLab Instances** | Monitoring multiple GitLab servers |
| **Database-backed State** | SQLite/PostgreSQL instead of JSON |
| **Web UI** | Simple web interface for monitoring |
| **API Endpoint** | REST API for status querying |
| **Metrics Export** | Prometheus/Grafana compatible metrics |

### 10.3 Long-term Improvements

| Feature | Description |
|---------|--------|
| **Plugin System** | Customizable processing modules |
| **Multi-language Support** | Support for multiple AI models (GPT, Gemini, etc.) |
| **Kubernetes Deployment** | Containerized deployment |
| **GitLab Webhook Integration** | Real-time events via webhook |

### 10.4 Known Limitations

1. **Linear Processing**: Only one issue/MR process runs at a time per project
2. **No Priority**: No prioritization of issue processing order
3. **No Rate Limiting**: GitLab API calls are not rate-limited
4. **Single-threaded**: No parallel processing

### 10.5 Suggested Refactorings

```python
# Currently: Watcher calls GitLab API directly
# Suggested: Introduce service layer

class IssueService:
    def get_backlog_issues(self, project_id: int) -> list[Issue]:
        ...

class MergeRequestService:
    def get_open_mrs(self, project_id: int) -> list[MergeRequest]:
        ...

# Benefits:
# - Better testability
# - Easier mocking
# - Clearer separation of concerns
```

---

## Appendix

### A. Example Configuration File

```bash
# ~/.config/gitlab-watcher/config.conf

# GitLab connection
GITLAB_URL="https://git.example.com"
GITLAB_TOKEN="glpat-xxxxxxxxxxxx"

# Discord notifications (optional)
DISCORD_WEBHOOK="https://discord.com/api/webhooks/123456/abcdef"

# Workflow labels
LABEL_IN_PROGRESS="In progress"
LABEL_REVIEW="Review"

# Monitored user
GITLAB_USERNAME="claude"

# Polling interval (seconds)
POLL_INTERVAL=30

# AI tool mode: ollama, direct, custom, opencode, opencode-custom
AI_TOOL_MODE="ollama"

# Custom command (for custom or opencode-custom mode)
AI_TOOL_CUSTOM_COMMAND=""

# Projects
PROJECT_DIRS=(
  "/home/user/projects/my-project"
  "/home/user/projects/another-project"
)
```

### B. Example PROJECT.md File

```markdown
# My Project

Project ID: 42

## Build

```bash
make build
```

## Test

```bash
make test
```

## Architecture

This project uses...
```

### C. Environment Variables

| Variable | Description |
|---------|--------|
| `CLAUDECODE` | For Claude CLI compatibility (set to empty) |

---

## Contact

- **Repository**: https://git.gyengus.hu/gyengus/gitlab-watcher
- **Issues**: https://git.gyengus.hu/gyengus/gitlab-watcher/issues
- **Author**: Gyengus

---

*Documentation Version: 1.0.0*
*Last Updated: 2026-03-11*