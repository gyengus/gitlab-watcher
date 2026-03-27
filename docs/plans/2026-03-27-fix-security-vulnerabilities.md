# Plan: Fix Security Vulnerabilities

The user identified two identical security alerts from GitHub code scanning. Based on a thorough review of the codebase, two critical vulnerability types were identified:

1.  **OS Command Injection (CWE-78)**: In `src/gitlab_watcher/processor.py`'s `_run_claude` method, user-controlled input (issue descriptions and comments) is used to build command-line arguments for AI tools. While it uses a list for `subprocess.run`, the string replacement for `{prompt}` in custom commands and the lack of `--` separators for other modes could lead to command or argument injection.
2.  **Log Injection (CWE-117)**: In `src/gitlab_watcher/watcher.py`, untrusted data such as issue titles and comment bodies from GitLab are logged directly using f-strings without sanitization. This allows malicious users to inject newlines and spoof log entries.

The "two identical errors" likely refer to the two different paths reaching the `_run_claude` sink in `processor.py` (from `process_issue` and `process_comment`) or the two similar logging sites in `watcher.py`.

## Proposed Changes

### [processor.py](file:///mnt/data/dev/ai/agents-workdir/gitlab-watcher/src/gitlab_watcher/processor.py)
#### [MODIFY] [processor.py](file:///mnt/data/dev/ai/agents-workdir/gitlab-watcher/src/gitlab_watcher/processor.py)
- Improve `_sanitize_prompt` to be more robust.
- Add `--` (end of options) separator before the prompt argument in all AI tool execution modes to prevent argument injection.
- Improve the custom command execution to avoid naive string `replace` which can be tricky with shell-like strings.
- Sanitize the `issue.title` (validated_title) to ensure no newlines are present before logging.

### [watcher.py](file:///mnt/data/dev/ai/agents-workdir/gitlab-watcher/src/gitlab_watcher/watcher.py)
#### [MODIFY] [watcher.py](file:///mnt/data/dev/ai/agents-workdir/gitlab-watcher/src/gitlab_watcher/watcher.py)
- Sanitize `issue.title` and `latest_note.body` before logging to prevent log injection.
- Ensure all logging of external data uses a shared sanitization utility or consistent replacement.

### [logging_utils.py](file:///mnt/data/dev/ai/agents-workdir/gitlab-watcher/src/gitlab_watcher/logging_utils.py)
#### [MODIFY] [logging_utils.py](file:///mnt/data/dev/ai/agents-workdir/gitlab-watcher/src/gitlab_watcher/logging_utils.py)
- Add a utility function to sanitize strings for logging (remove newlines and control characters).

## Verification Plan

### Automated Tests
- Run existing tests to ensure no regressions: `pytest`
- I will attempt to add a security-specific test case that tries to inject newlines into logs and verify they are handled.

### Manual Verification
- I will simulate an issue with a multi-line title and verify that the logs remain clean and single-lined for that entry.
- I will simulate a "custom" AI command and verify it correctly handles prompts starting with hyphens or containing shell metacharacters.
