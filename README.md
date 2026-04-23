# GitLab Watcher

Monitor GitLab projects and automatically process issues and merge requests using AI tools. It features built-in support for automated branch management, AI-driven code generation, Discord notifications, and automatic failover to backup AI models when primary models encounter service issues.

## What the Watcher Does

### Issue Processing (Automatic)

When an issue is assigned to the configured user without workflow labels:

1. Adds "In progress" label
2. Creates branch `<issue-id>-<slug>` from default branch
3. Runs AI tool with the issue description (with automatic failover to backup models if primary fails)
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
- **AI Tools**: Claude CLI (`claude`) or OpenCode (`opencode`) must be installed and available in your PATH. Note: Plugins that modify agent names (like `oh-my-opencode`) may cause issues in non-interactive background mode.

## Installation

```bash
# From PyPI (recommended)
pip install gitlab-watcher
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

### Logging

You can set the log verbosity in `gitlab-watcher.conf`:

```bash
# LOG_LEVEL options: DEBUG, INFO, WARNING, ERROR, CRITICAL
LOG_LEVEL="DEBUG"
```

Command line `--verbose` or `-v` flags will override this and force DEBUG level.


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

# Maximum execution time for AI tool in seconds (default: 3600 / 1 hour)
AI_TOOL_TIMEOUT=3600

# Path to log file (default: /var/log/gitlab-watcher.log)
LOG_FILE="/var/log/gitlab-watcher.log"

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

## AI Configuration & Rules

### Recommended: Configure AI Coding Rules

### Automatic Failover Model Support

The watcher includes an automatic failover feature that switches to a backup AI model when the primary model encounters service issues (like 524 errors, rate limits, or service unavailability). This ensures continuous operation for hobby projects.

#### Configuration

Add the failover model to your `config.conf`:

```bash
# Primary AI tool mode
AI_TOOL_MODE="opencode"

# Optional: Failover model for service issues
AI_TOOL_FAILOVER_MODEL="openrouter/google/gemma-3-12b-it:free"

# Custom command with model placeholder support
AI_TOOL_CUSTOM_COMMAND="opencode --model {model} run {prompt} --thinking --log-level DEBUG"
```

#### How It Works

1. **Primary Attempt**: Uses the configured AI tool with the default model
2. **Error Detection**: Automatically detects provider errors (524, rate limits, service unavailable, etc.)
3. **Failover Trigger**: On service errors, switches to the configured failover model
4. **Success Handling**: If failover succeeds, logs the success and continues processing
5. **Failure Handling**: If both models fail, sends Discord notification and waits for next cycle

#### Recommended Models for Hobby Projects

**Free Models (for failover):**
- `openrouter/google/gemma-3-12b-it:free` - Good coding model
- `openrouter/google/gemma-3-27b-it:free` - More capable, still free
- `openrouter/meta-llama/llama-3.2-3b-instruct:free` - Small but capable

**Affordable Models (for primary use):**
- `openrouter/deepseek/deepseek-chat-v3.1` - Very capable, low cost
- `openrouter/anthropic/claude-3.5-haiku` - Fast and capable, moderate cost

#### Supported Modes

| Mode | Model Support | Description |
|------|---------------|-------------|
| `ollama` | No | Model switching via CLI not supported |
| `direct` | No | Model switching via CLI not supported |
| `opencode` | Yes | Uses `--model` flag when specified |
| `opencode-custom` | Yes | Supports `{model}` placeholder substitution |
| `custom` | Yes | Supports `{model}` placeholder substitution |

**Important**: Leave `AI_TOOL_FAILOVER_MODEL` empty if you don't want failover functionality. The watcher will maintain backward compatibility with existing configurations.

For optimal AI-assisted development, it's highly recommended to configure AI coding rules at both global and project levels:

#### Global Rules (System-wide)
Create `~/.config/opencode/AGENTS.md` or `~/.claude/CLAUDE.md` with general coding guidelines that apply to all projects:

```markdown
# Global AI Rules

## Core Requirements
- Run tests after all code changes
- Use conventional commit messages (if applicable to your workflow)
- Follow security best practices
- Include proper error handling

## Testing Requirements
- Minimum 85% test coverage
- Fix any failing tests immediately
- Run `pytest` after completing any task
```

#### Project-specific Rules
Add `CLAUDE.md` or `AGENTS.md` to your project directory for project-specific guidelines:

```markdown
# Project ID: 31

## AI Coding Rules
### Language Specific
- Python: Use Objects.requireNonNull() for null checks
- JavaScript: Use Optional chaining

### Testing Requirements
- Run pytest after all code changes
- Maintain 90%+ test coverage

### Commit Guidelines
- Use conventional commits: feat:, fix:, etc.
```

### How It Works

1. **Automatic Rule Loading**: OpenCode automatically loads rules from:
   - Project `AGENTS.md` (preferred)
   - Project `CLAUDE.md` (fallback)
   - Global `~/.config/opencode/AGENTS.md`
   - Global `~/.claude/CLAUDE.md`

2. **Rule Priority**: Project-specific rules override global rules
3. **No Manual Configuration Required**: The GitLab Watcher automatically uses these rules when processing issues and MR comments

### Contributing Guidelines

For detailed development guidelines, testing requirements, and troubleshooting tips, see [CONTRIBUTING.md](CONTRIBUTING.md).

## Supported AI Tools

The watcher supports multiple AI tools:

| Mode | Description | Model Support |
|------|-------------|---------------|
| `ollama` | Launches Claude via Ollama (requires both `ollama` and `claude`) (default) | No |
| `direct` | Direct Claude CLI execution (`claude`) | No |
| `opencode` | Opencode CLI execution using `run` subcommand | Yes (`--model` flag) |
| `custom` | Custom command for any AI tool | Yes (`{model}` placeholder) |

### Model Parameter Support

For `opencode` and `custom` modes, you can specify models dynamically:

```bash
# Opencode mode with model parameter
AI_TOOL_CUSTOM_COMMAND="opencode --model {model} run {prompt} --thinking --log-level DEBUG"

# Custom command with model and working directory placeholders
AI_TOOL_CUSTOM_COMMAND="my-ai-tool --model {model} --prompt \"{prompt}\" --cwd {cwd}"
```

The `{model}` placeholder will be replaced with the specified model name, `{prompt}` with the issue description, and `{cwd}` with the working directory path.

Configure in `config.conf`:

```bash
AI_TOOL_MODE="ollama"  # or "direct", "opencode", "custom"
AI_TOOL_TIMEOUT=3600   # default is 1 hour
```

### Timeout & Error Diagnostics

If an AI tool exceeds the configured `AI_TOOL_TIMEOUT` or fails with an error, the watcher will attempt to capture and display the partial output (`stdout`/`stderr`) generated before termination. These details are sent to Discord in a formatted code block and logged locally for troubleshooting.

## Troubleshooting

### "Default agent not found" (OpenCode)
If you are using `oh-my-opencode` or similar plugins that add rejtett (hidden) characters to agent names, OpenCode might fail to find the agent in non-interactive mode.
**Solution**: Define an explicit `default_agent` in your `~/.config/opencode/opencode.json` without hidden characters or special sorting prefixes.

### Discord Log Formatting
The watcher sanitizes output but preserves newlines for stack traces. If your Discord messages are too long, they will be automatically truncated to fit within Discord's 2000-character limit.

### Failover Model Issues
If the failover feature isn't working as expected:

1. **Enable Debug Logging**: Set `LOG_LEVEL="DEBUG"` to see detailed failover attempts
2. **Check Error Patterns**: The watcher only triggers failover on specific provider errors (524, rate limits, service unavailable, etc.)
3. **Verify Model Format**: Ensure the failover model name is correct and accessible via your AI tool
4. **Test Manually**: Try running the AI tool directly with the failover model to ensure it works

To test failover manually:
```bash
# Test with opencode mode
opencode --model openrouter/google/gemma-3-12b-it:free run "test prompt" --thinking

# Test with custom command
opencode --model openrouter/google/gemma-3-12b-it:free run {prompt} --thinking --log-level DEBUG
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

## Support & Donations

If you find this tool helpful and would like to support its continued development, crypto donations are very much appreciated!

- **Bitcoin (BTC):** `bc1qx4q5epl7nsyu9mum8edrvp2my8tut0enrz7kcn`
- **Dogecoin (DOGE):** `DS62HBswTfAJLadRcMyrUJq6CAE8XV6SqC`
- **EVM (ETH/BSC/Polygon):** `0x9F0a70A7306DF3fc072446cAF540F6766a4CC4E8`
- **Web3 Domain:** `gyengus.sendme1satoshi.wallet`

Thank you for your support!