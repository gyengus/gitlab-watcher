# AGENTS.md

Project ID: 32

This file provides guidance to agents (such as Claude Code or OpenCode) when working with code in this repository.

## Project Overview

GitLab Watcher is a Python daemon that monitors GitLab projects and automatically processes issues and merge requests using Claude CLI (via `ollama launch claude`). It acts as an AI-assisted development workflow automation tool.

## Development Commands

```bash
# Install in development mode with dev dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Run tests with coverage
pytest --cov=gitlab_watcher

# Run a single test file
pytest tests/test_watcher.py

# Run a single test
pytest tests/test_watcher.py::TestStateManager::test_save_and_load
```

## Architecture

The codebase follows a layered architecture with clear separation of concerns:

```
cli.py          → Entry point (Click CLI)
watcher.py      → Main loop, orchestrates all monitoring
processor.py    → Business logic for processing issues/MR comments
gitlab_client.py → GitLab API client (issues, MRs, notes, labels)
git_ops.py      → Git operations wrapper (subprocess-based)
config.py       → Bash-style config file parsing + project discovery
state.py        → Per-project state persistence (JSON files)
discord.py      → Discord webhook notifications
```

### Key Flow

1. **Issue Processing**: When an issue assigned to the configured user lacks workflow labels:
   - Adds "In progress" label → Creates branch `{iid}-{slug}` → Runs Claude CLI → Pushes → Creates MR → Moves to "Review"

2. **MR Comment Processing**: When a new comment appears on an open MR (not from bot user):
   - Checks out MR's source branch → Runs Claude CLI with comment as feedback → Pushes changes

3. **Post-Merge Cleanup**: When MR is detected as merged or closed:
   - Switches to master → Pulls → Deletes feature branch → Resets state
   - Cleanup is performed if the MR was created by the watcher OR if the branch name is known to the watcher.

### Configuration Discovery

Projects are discovered by scanning `PROJECT_DIRS` from the config file. Each project directory must contain a `PROJECT.md`, `AGENTS.md`, or `CLAUDE.md` file with a `Project ID: <number>` entry. The watcher extracts the GitLab server URL from git remote URLs if not explicitly configured, but for security reasons, the `GITLAB_TOKEN` must always be provided in the configuration file.

### State Management

Per-project state is stored in `/tmp/gitlab-watcher/state_{project_id}.json`:
- Tracks MR IIDs, last processed note IDs, current branches
- `processing` flag prevents concurrent operations on same project
- On startup, `init_state()` resets the processing flag (crash recovery)

### AI Tool Integration

The `_run_ai_tool()` method in `processor.py` supports multiple AI tools:
- **ollama**: `ollama launch claude -- -p --permission-mode acceptEdits "<prompt>"`
- **direct**: `claude -p --permission-mode acceptEdits "<prompt>"`
- **opencode**: `opencode "<prompt>"`
- **custom**: Configurable command with `{prompt}` and `{cwd}` placeholders
- **opencode-custom**: Configurable opencode command with `{prompt}` and `{cwd}` placeholders

All modes use a 1-hour overall timeout and a 30-minute silence timeout, and `CLAUDECODE=""` environment variable to avoid conflicts.

### Development Guidelines

See [CONTRIBUTING.md](CONTRIBUTING.md) for comprehensive development guidelines including:
- Testing requirements (run all tests after each task, maintain 85%+ coverage)
- Commit message guidelines (avoid conventional prefixes, use English)
- Code quality standards
- Troubleshooting tips