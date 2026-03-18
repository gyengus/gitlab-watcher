# GitLab Watcher

Monitor GitLab projects and automatically process issues and merge requests using AI tools.

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

Copy the example config to the default location:

```bash
mkdir -p ~/.config/gitlab-watcher
cp gitlab-watcher.conf ~/.config/gitlab-watcher/config.conf
```

Edit the config file to match your environment. See `gitlab-watcher.conf` for all available options.

Each project must have a `PROJECT.md` file with a Project ID:

```markdown
Project ID: 31
```

## What the Watcher Does

### Issue Processing (Automatic)

When an issue is assigned to the configured user without workflow labels:

1. Adds "In progress" label
2. Creates branch `<issue-id>-<slug>` from master
3. Runs AI tool with the issue description
4. Pushes changes and creates merge request
5. Moves issue to "Review" label

### MR Comment Processing (Automatic)

When a new comment appears on an open MR (not from the bot user):

1. Checks out the MR's source branch
2. Runs AI tool with the comment as feedback
3. Pushes the changes to the remote branch

### Post-Merge Cleanup (Automatic)

When an MR is merged:

1. Updates master branch
2. Deletes the merged feature branch
3. Sends Discord notification

## Supported AI Tools

The watcher supports multiple AI tools:

| Mode | Description |
|------|-------------|
| `ollama` | Ollama with Claude (default) |
| `direct` | Direct Claude CLI |
| `opencode` | Opencode CLI |
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