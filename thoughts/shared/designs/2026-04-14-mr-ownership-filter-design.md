---
date: 2026-04-14
topic: "MR Ownership Filter — Only Cleanup Watcher-Created MRs"
status: validated
---

## Problem Statement

The watcher currently cleans up **any** merged MR it tracks, regardless of who created it. This causes destructive side effects when:

- A human creates an MR using the same GitLab account the bot runs under
- The watcher discovers an open MR during startup and adds it to `tracked_mrs`
- That MR gets merged (by the human, naturally)
- The watcher detects "merged" state and triggers full cleanup: force-deleting the local branch, switching to master, and potentially resetting state

The watcher should **only** clean up MRs that it created itself through the issue-processing flow.

## Constraints

- **State files are on disk** — any new fields need migration handling for existing state
- **The `MergeRequest` dataclass is used throughout** — adding `author` is a safe extension but must not break existing callers
- **Backward compatibility** — existing tracked MRs in state files don't have `created_by_watcher` flag; they must be handled gracefully (assume `false`)
- **The watcher must still track non-watcher MRs** for comment processing — it just shouldn't cleanup them

## Approach

**Mark MRs with a `created_by_watcher` flag at the point of creation, and skip cleanup for MRs that lack this flag.**

This is better than filtering by author because:
- A shared service account means author-based filtering is unreliable
- The watcher may legitimately process comments on MRs it didn't create — it should still track those
- A creation tag is explicit and unambiguous

I considered and rejected:
- **Author-based filtering only** — fails when bot and human share an account
- **Separate tracking dicts for "owned" vs "watched" MRs** — over-engineering, one flag is simpler
- **Not tracking non-watcher MRs at all** — would break comment processing on externally-created MRs

## Architecture

The change is localized to three areas:

1. **State model** — Add `created_by_watcher` field to tracked MR entries
2. **MR creation point** — Set the flag to `true` when the watcher creates an MR via issue processing
3. **Cleanup gate** — Check the flag before triggering cleanup on merged MRs

No new files, no new classes. Minimal, surgical change.

## Components

### `MergeRequest` dataclass (gitlab_client.py)

- Add `author` field of type `str` (username)
- Extract `data.get("author", {}).get("username", "")` from API responses
- This is useful for debugging/logging even though the primary filter is the state flag

### `ProjectState.tracked_mrs` (state.py)

Each entry changes from:
```
{"branch": "42-fix-bug"}
```
to:
```
{"branch": "42-fix-bug", "created_by_watcher": false}
```

**Migration:** When loading state files that lack the `created_by_watcher` key, default to `false`. This ensures existing tracked MRs (which may or may not be watcher-created) are treated conservatively — they won't be cleaned up. The user can manually merge and clean those up once.

### `add_tracked_mr()` (state.py)

- Add parameter `created_by_watcher: bool = False`
- Store the flag in the tracked MR entry
- The default is `false` so callers that don't specify it are safe

### `process_issue()` → MR creation (processor.py)

- When `add_tracked_mr()` is called after creating an MR from an issue, pass `created_by_watcher=True`
- This is the **only** place where the flag should be `true`

### `check_mr_status()` (watcher.py)

- When fetching open MRs for tracking, call `add_tracked_mr()` with `created_by_watcher=False` (the default)
- **Add the critical gate**: before calling `cleanup_after_merge()`, check `mr_data.get("created_by_watcher", False)`
- If `False`, remove from tracked MRs (stop tracking) but do NOT run cleanup (no branch deletion, no state reset)
- If `True`, proceed with existing cleanup logic

### `update_mr_state()` (state.py)

- Preserve the `created_by_watcher` flag when updating MR state entries
- Currently overwrites the entire entry — must merge with existing data

## Data Flow

### Watcher-created MR (issue processing):

```
Issue detected → branch created → Claude runs → push → MR created
    → add_tracked_mr(iid, branch, created_by_watcher=True)
    → [MR is merged]
    → check_mr_status finds merged MR with created_by_watcher=True
    → cleanup_after_merge() runs (branch deleted, state cleaned)
```

### Human-created MR (discovered during tracking):

```
check_mr_status fetches open MRs → add_tracked_mr(iid, branch, created_by_watcher=False)
    → [MR is merged]
    → check_mr_status finds merged MR with created_by_watcher=False
    → remove_tracked_mr() only (NO branch deletion, NO state reset)
    → log: "MR {iid} merged but not created by watcher — skipping cleanup"
```

### Legacy state (no flag):

```
State loaded from disk → created_by_watcher defaults to False
    → Behaves like human-created MR (conservative, safe)
```

## Error Handling

- **Missing `created_by_watcher` in state file:** Handled by `dict.get("created_by_watcher", False)` — defaults to `False`, which is the safe default (no cleanup)
- **State migration:** The existing migration logic in `_load_from_file` already handles missing `tracked_mrs` key. The new `created_by_watcher` field is additive — old state files work without changes
- **Race condition:** If an MR is created and merged between poll cycles, the `created_by_watcher` flag is set at creation time before the MR enters tracking, so this is safe

## Testing Strategy

- **Unit test: `add_tracked_mr` with flag** — Verify that `created_by_watcher=True` is stored correctly
- **Unit test: `add_tracked_mr` default** — Verify that `created_by_watcher=False` is the default
- **Unit test: cleanup gate** — Verify that `check_mr_status` skips cleanup for `created_by_watcher=False` MRs and proceeds for `True`
- **Unit test: legacy state migration** — Verify that state files without `created_by_watcher` default to `False`
- **Unit test: `update_mr_state` preserves flag** — Verify that updating MR state doesn't lose the `created_by_watcher` field
- **Integration test: full flow** — Create issue → process → MR created → merge → cleanup runs. Create MR manually → merge → cleanup skipped.

## Open Questions

None — the approach is straightforward and the design is complete.