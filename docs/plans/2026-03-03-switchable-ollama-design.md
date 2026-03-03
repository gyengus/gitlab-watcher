# Design: Switchable Ollama Configuration

## Issue
#4 - Ollama should be switchable by the config file

## Goal
Allow users to configure how Claude CLI is launched, supporting three modes:
1. **ollama** - Use Ollama wrapper (default, current behavior)
2. **direct** - Use Claude CLI directly without Ollama
3. **custom** - Use a user-defined command

## Configuration

### Config File Format
```bash
# Mode selection: ollama (default), direct, custom
CLAUDE_MODE=ollama

# Custom command (only used when CLAUDE_MODE=custom)
# Placeholders: {prompt}, {cwd}
CLAUDE_CUSTOM_COMMAND=""
```

### Example Usage
```bash
# Default Ollama mode
CLAUDE_MODE=ollama

# Direct Claude CLI
CLAUDE_MODE=direct

# Custom command
CLAUDE_MODE=custom
CLAUDE_CUSTOM_COMMAND="my-ai-tool --prompt '{prompt}' --workspace {cwd}"
```

## Implementation

### 1. config.py
Add two new fields to `Config` dataclass:
- `claude_mode: str = "ollama"`
- `claude_custom_command: str = ""`

Update `load_config()` to parse `CLAUDE_MODE` and `CLAUDE_CUSTOM_COMMAND`.

### 2. processor.py
Modify `_run_claude()` method:
- Build command based on `claude_mode`
- Support placeholder substitution for custom mode: `{prompt}`, `{cwd}`
- Return meaningful error for invalid mode or missing custom command

Extend constructor to accept `claude_mode` and `claude_custom_command`.

### 3. watcher.py
Pass `claude_mode` and `claude_custom_command` from config to Processor.

### 4. Tests
New test cases in `test_processor.py`:
- `test_run_claude_ollama_mode` - verifies ollama command
- `test_run_claude_direct_mode` - verifies direct claude command
- `test_run_claude_custom_mode` - verifies custom command with placeholders
- `test_run_claude_custom_mode_missing_command` - error handling
- `test_run_claude_invalid_mode` - error handling

## Command Templates

| Mode | Command |
|------|---------|
| ollama | `ollama launch claude -- -p --permission-mode acceptEdits "<prompt>"` |
| direct | `claude -p --permission-mode acceptEdits "<prompt>"` |
| custom | User-defined with `{prompt}` and `{cwd}` placeholders |

## Backward Compatibility
Default mode is `ollama`, matching current behavior. Existing configs work without changes.