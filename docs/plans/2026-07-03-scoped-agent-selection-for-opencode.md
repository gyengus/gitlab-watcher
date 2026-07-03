# Plan: GitLab-Free Scoped Agent Selection for OpenCode

This document describes the plan and implementation details for selecting specific OpenCode agents dynamically using GitLab labels, designed to work seamlessly on both GitLab Free and Premium/Ultimate instances.

## Goal
Implement a GitLab-Free compatible scoped agent selection system that allows developers to assign a specific agent to an issue or merge request using regular labels (e.g. `agent:[Agent Name]`), cleans up redundant agent labels to simulate premium scoped label behavior, propagates the choice to the merge request, and displays the agent name in Discord notifications.

## Proposed Changes

### 1. GitLab API Client (`src/gitlab_watcher/gitlab_client.py`)
- **Modify `MergeRequest`**: Add `labels: list[str] = field(default_factory=list)` to the `MergeRequest` dataclass.
- **Modify Endpoints**:
  - Update `get_merge_requests` and `get_merge_request` to parse the `labels` field from the API JSON response.
  - Update `create_merge_request` to accept an optional `labels` parameter and pass it to the GitLab API.
- **Add New Method**: Add `update_merge_request_labels(self, project_id, mr_iid, labels)` to allow modifying MR labels after creation.

### 2. State Management (`src/gitlab_watcher/state.py`)
- **Modify `TrackedMRInfo`**: Add `agent: str` to the tracked merge request typed dictionary.
- **Modify `add_tracked_mr`**: Accept an optional `agent` parameter. Avoid redundant disk I/O by only triggering `self.force_save()` if the agent name actually changed.

### 3. Discord Notifications (`src/gitlab_watcher/discord.py`)
- **Modify `notify_issue_started`**: Accept an optional `agent` parameter and, if provided, include `Agent: `[Agent Name]`` in the Discord notification content.

### 4. Processor Logic (`src/gitlab_watcher/processor.py`)
- **Add Helper `_extract_agent_from_labels`**: Extract the agent name from a list of labels using the pattern `agent:[Agent Name]` or `agent-[Agent Name]` (case-insensitive). Filter out redundant agent labels to enforce watcher-side mutual exclusion.
- **Modify `process_issue`**:
  - Extract and clean up agent labels at the start.
  - Call GitLab API to remove redundant agent labels if any were found.
  - Pass the agent to the Discord notification and the AI execution flows.
  - Pass the selected agent label when creating the MR.
  - Save the selected agent to the local state file under the tracked MR.
- **Modify `process_comment`**:
  - Clean up redundant agent labels on the MR.
  - Resolve the agent name with priority: MR current labels -> State file fallback (`tracked_mrs`) -> None (OpenCode default).
  - Include the active agent in the initial processing Discord notification.
- **Modify Command Builder**:
  - Extend `_run_ai_tool_with_failover`, `_run_ai_tool`, and `_build_ai_command` signatures to accept `agent`.
  - For `opencode` mode, append `["--agent", agent]` if the agent is defined.
  - For custom commands (`opencode-custom` and `custom`), substitute the `{agent}` placeholder with the agent name (or empty string if not defined).

## Verification Plan

### Automated Tests
Added comprehensive test coverage across the modified modules:
- **`tests/test_gitlab_client.py`**:
  - `test_update_merge_request_labels`: Verify updating MR labels sends the correct PUT request.
  - `test_create_merge_request_with_labels`: Verify labels are passed when creating a new MR.
- **`tests/test_discord.py`**:
  - `test_notify_issue_started_with_agent` and `test_notify_issue_started_retry_with_agent`: Verify Discord messages format the agent name correctly.
- **`tests/test_processor.py`**:
  - `test_extract_agent_from_labels`: Verify that single, dash-prefixed, case-insensitive, and multiple/redundant agent labels are parsed and cleaned correctly.
  - `test_build_ai_command_with_agent`: Verify `--agent` argument is built in `opencode` mode.
  - `test_build_ai_command_custom_with_agent_placeholder`: Verify `{agent}` placeholder resolves correctly.
  - `test_process_issue_with_agent_label`: Verify end-to-end issue processing: cleaning labels, Discord notification, passing to AI tool, MR creation with labels, and state persistence.
  - `test_process_comment_with_agent_label_and_state_fallback`: Verify MR label resolution, redundant label cleanup, state fallback, and Discord update.

### Verification Run
Executed the full test suite to guarantee coverage and no regressions:
```bash
pytest --cov=gitlab_watcher
```
All 281 tests passed with core modules maintaining high coverage.
