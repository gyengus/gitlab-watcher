---
date: 2026-04-13
topic: "Fix missing eyes emoji on MR comment processing"
status: validated
---

## Problem Statement

When a new comment is detected on an MR, the watcher processes it with an AI tool but never adds the 👀 ("eyes") emoji at the start of processing. It only adds ✅ or ❌ at the end. The `SKIP_EMOJIS` list in `watcher.py` already includes `"eyes"`, meaning the system is designed to use it as a "being processed" marker — but the step that actually adds it is missing.

**Impact:** If the watcher restarts mid-processing, the comment has no emoji marker and will be re-processed on the next poll cycle (in-memory `_processed_notes` set is lost on restart).

## Constraints

- Must not break existing emoji skip logic
- Must not break tests
- Must be added before AI tool runs (so it protects against restart re-processing)
- Should handle the case where the emoji API call fails gracefully (like other emoji calls in the codebase)

## Approach

Add `create_note_award_emoji(project_id, mr_iid, note_id, "eyes")` call in `processor.py` `process_comment()` method, right after the Discord "Processing Comment" notification and before git preparation. This is the natural "I'm starting work" point in the flow.

**Why this position:**
- After the comment is identified and logged (so we know what we're working on)
- Before any expensive operations (git checkout, AI tool) to maximize the protection window
- Consistent with the existing pattern where emojis mark processing state

## Architecture

No structural changes. Single line addition in an existing method.

## Components

**Modified: `processor.py` → `process_comment()` method**

Add eyes emoji creation call between the Discord notification (~line 527-531) and git preparation (~line 534). The call follows the same pattern as the existing ✅ and ❌ emoji additions:

- Call: `self.gitlab.create_note_award_emoji(project.project_id, mr.iid, note_id, "eyes")`
- If it fails (returns False), log but continue — the emoji is a nice-to-have marker, not critical for the flow
- The existing `SKIP_EMOJIS` list in `watcher.py` already includes `"eyes"`, so if the watcher restarts and re-scans, it will see the 👀 and skip the note

## Data Flow

```
Comment detected (watcher.py)
  → Discord "Processing Comment" notification
  → 👀 emoji added to note  ← NEW STEP
  → Git checkout + pull
  → AI tool runs
  → Push changes
  → ✅ or ❌ emoji added (replaces 👀 visually in GitLab UI)
```

## Error Handling

- If `create_note_award_emoji("eyes")` fails: log warning, continue processing normally
- The eyes emoji is a dedup guard, not a requirement — the `state.set_processing(True)` lock and `_processed_notes` set still provide basic protection
- On success path: ✅ emoji is added after, which GitLab shows alongside or replaces the 👀 (GitLab UI behavior: both emojis visible as separate reactions)

## Testing Strategy

- Update `TestProcessComment` in `tests/test_processor.py` to verify `create_note_award_emoji` is called with `"eyes"` before AI tool runs
- Verify existing test that ✅ is still added on success and ❌ on failure still passes
- Verify the eyes emoji call happens even if it returns False (processing continues)

## Open Questions

None — the fix is straightforward. The `"eyes"` emoji is already in `SKIP_EMOJIS`, confirming this was always the intended behavior.