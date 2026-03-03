# Claude CLI Mode Configuration Design

**Date:** 2026-03-03
**Issue:** #4 - Ollama should be switchable

## Problem

The `_run_claude` method in `processor.py` hardcodes the use of `ollama launch claude`. Users should be able to configure how Claude CLI is invoked.

## Solution

Add mode-based configuration with three options:

| Mode | Command |
|------|---------|
| `ollama` (default) | `ollama launch claude -- -p --permission-mode acceptEdits "<prompt>"` |
| `direct` | `claude -p --permission-mode acceptEdits "<prompt>"` |
| `custom` | User-defined command with `{prompt}` and `{cwd}` placeholders |

## Configuration

```bash
# Config file (~/.claude/config/gitlab_watcher.conf)
CLAUDE_MODE=ollama           # ollama, direct, or custom
CLAUDE_CUSTOM_COMMAND=""     # only for custom mode
```

## Implementation

### config.py
- Add `claude_mode: str = "ollama"` to `Config` dataclass
- Add `claude_custom_command: str = ""` to `Config` dataclass
- Parse `CLAUDE_MODE` and `CLAUDE_CUSTOM_COMMAND` from config file

### processor.py
- Add `claude_mode` and `claude_custom_command` parameters to constructor
- Modify `_run_claude` to build command based on mode
- Support `{prompt}` and `{cwd}` placeholders in custom mode

### watcher.py
- Pass `claude_mode` and `claude_custom_command` from config to Processor

### test_processor.py
- Test all three modes
- Test error cases (missing custom command, invalid mode)

## Files Changed

- `src/gitlab_watcher/config.py`
- `src/gitlab_watcher/processor.py`
- `src/gitlab_watcher/watcher.py`
- `tests/test_processor.py`