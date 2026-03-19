# GitLab Watcher

Monitor GitLab projects and automatically process issues and merge requests using AI tools. It features built-in support for automated branch management, AI-driven code generation, and Discord notifications.

## What the Watcher Does

### Issue Processing (Automatic)

When an issue is assigned to the configured user without workflow labels:

1. Adds "In progress" label
2. Creates branch `<issue-id>-<slug>` from default branch
3. Runs AI tool with the issue description
4. Pushes changes and creates merge request
5. Moves issue to "Review" label
6. Sends optional Discord notification

### MR Comment Processing (Automatic)

When a new comment appears on an open MR (not from the bot user):

1. Checks out the MR's source branch
2. Runs AI tool with the comment as feedback
3. Pushes the changes to the remote branch
4. Notifies Discord channels about applied changes

### Post-Merge Cleanup (Automatic)

When an MR is merged:

1. Updates default branch (e.g., master/main)
2. Deletes the merged feature branch
3. Sends Discord notification to confirm cleanup

## Prerequisites

- **Python 3.11** or higher
- **Git** installed and available in your system path
- **GitLab Access Token** with API access
- **Claude CLI** (or `opencode`) must be installed and available in your PATH. (`ollama` is optional but supported for launching Claude).

## Installation

```bash
# From PyPI (recommended)
pip install gitlab-watcher

# From source (development mode)
git clone https://git.gyengus.hu/gyengus/gitlab-watcher.git
cd gitlab-watcher
pip install -e ".[dev]"
```

## Usage

```bash
# Run with default config
gitlab-watcher

# Custom config file
gitlab-watcher -c /path/to/config.conf

# Verbose mode
gitlab-watcher --verbose
```

## Configuration

Create the default configuration directory:

```bash
mkdir -p ~/.config/gitlab-watcher
```

Create `~/.config/gitlab-watcher/config.conf` with your environment details:

```bash
# Your GitLab instance URL
GITLAB_URL="https://git.example.com"

# Personal or Group Access Token
GITLAB_TOKEN="your_token_here"

# (Optional) Discord Webhook for detailed event notifications
DISCORD_WEBHOOK="https://discord.com/api/webhooks/your_webhook_id"

# Monitoring interval in seconds
POLL_INTERVAL=30

# AI Tool implementation style (ollama, direct, opencode, custom)
AI_TOOL_MODE="ollama"

# List of absolute paths to project directories to monitor
PROJECT_DIRS=(
  "/path/to/project1"
  "/path/to/project2"
)
```

*(Note: If `GITLAB_URL` and `GITLAB_TOKEN` are not provided in the configuration, the watcher will attempt to extract them from your git remotes automatically.)*

Each monitored project directory must have a `PROJECT.md`, `AGENTS.md`, or `CLAUDE.md` file with a corresponding Project ID:

```markdown
Project ID: 31
```

## Supported AI Tools

The watcher supports multiple AI tools:

| Mode | Description |
|------|-------------|
| `ollama` | Launches Claude via Ollama (requires both `ollama` and `claude`) (default) |
| `direct` | Direct Claude CLI execution (`claude`) |
| `opencode` | Opencode CLI execution (`opencode`) |
| `custom` | Custom command for any AI tool |

Configure in `config.conf`:

```bash
AI_TOOL_MODE="ollama"  # or "direct", "opencode", "custom"
```

## Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Run tests with coverage
pytest --cov=gitlab_watcher
```