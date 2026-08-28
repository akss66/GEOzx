# Task 17 Review

## Review Scope

- Brief: `.superpowers/sdd/2026-08-11-wechat-official-account-agent/task-17-brief.md`
- Report: `.superpowers/sdd/2026-08-11-wechat-official-account-agent/task-17-report.md`
- Original review package: `.superpowers/sdd/2026-08-11-wechat-official-account-agent/task-17-review-package.txt`
- Fix Round1 review package: `.superpowers/sdd/2026-08-11-wechat-official-account-agent/task-17-fix-round1-review-package.diff`
- Current implementation and focused regression tests in backend/frontend worktree

## Original Task 17 Review Summary

This file was missing when the re-review started, so it is recreated here to preserve the review trail.

### Verdict

- Spec compliance: Reject
- Code quality: Reject

### Findings

1. Critical - WeChat workspace handoff trusted artifact shape instead of same-turn, same-skill, same-account lineage.
Evidence:
- The original projection path only checked for artifact fields such as `article_id`.
- That allowed unrelated or stale artifacts to light up the article workspace on the current turn.
User impact:
- Operators could open or resume the wrong article workspace after reload or account changes.

2. Important - Downstream sync/provider failure detail could be masked by the generic draft title/report path.
Evidence:
- The frontend consumed generic artifact projection fields instead of a dedicated workspace projection.
User impact:
- The work log could show a misleading title while hiding the actual provider or sync failure state.

3. Minor - Frontend stage mapping and reload state handling were too permissive.
Evidence:
- UI logic still depended on ad hoc `article_action` or generic artifact state.
- Workspace reload behavior was not tightly allowlisted by platform.
User impact:
- The UI could show fake stages or preserve the wrong workspace across account/platform transitions.

## Fix Round1 Scoped Re-Review

### Exact Scope Analyzed

- Backend conversation projection for WeChat article workspaces
- Shared conversation schemas
- Frontend work-turn projection and TurnStream rendering
- Workspace reload persistence in BrainHome/current workspace store
- Targeted unit/integration coverage plus the updated E2E spec content

### Original Findings Status

1. Critical - ADDRESSED
Evidence:
- `backend/app/api/conversations.py` now appends a dedicated `wechat_article_workspace` projection from the work turn content path.
- `_wechat_article_workspace_projection(...)` enforces lineage against the same turn, same `wechat_article_production` skill, same `skill_run_id`, same thread account, and matching article identity before projecting a workspace.
- `frontend/src/components/brain/workTurnProjection.ts` now consumes the dedicated projection instead of inferring handoff from generic artifact shape.
Risk reduction:
- The article workspace only appears when the current turn actually owns that article for the current account.

2. Important - ADDRESSED
Evidence:
- `backend/app/schemas/conversation.py` keeps `report` on the dedicated WeChat workspace model, while the generic artifact projection no longer carries that field.
- `frontend/src/components/brain/workTurnProjection.ts` maps downstream activity from `wechat_article_workspace.current_action`; the UI no longer relies on generic artifact report/title as the source of truth.
Risk reduction:
- Provider/sync failures can surface from the dedicated workspace state instead of being overwritten by draft-title-oriented artifact rendering.

3. Minor - ADDRESSED
Evidence:
- `frontend/src/components/brain/workTurnProjection.ts` no longer exposes fake durable stages such as `generate_images`, `sync_draft`, or `draft_sync_completed`.
- `frontend/src/components/brain/TurnStream.tsx` passes `thread.account_id` into `projectWorkTurn(...)`, and the helper rejects mismatched workspace projections.
- `frontend/src/stores/currentWorkspace.ts` restores persisted workspace only for an explicit platform allowlist, with targeted tests covering keep/drop behavior.
Risk reduction:
- Reload and account switching are fail-closed, and the timeline no longer invents durable-step semantics that the backend does not guarantee.

### Validation Performed

Focused backend regression:

```powershell
.\backend\.venv\Scripts\python.exe -m pytest backend\tests\test_conversation_api.py -q
```

Result: `55 passed in 24.74s`

Focused frontend regression:

```powershell
cd frontend
npm.cmd test -- --run src/components/brain/workTurnProjection.test.ts src/components/brain/TurnStream.test.tsx src/pages/BrainHome.test.tsx src/stores/currentWorkspace.test.ts
```

Result: `135 passed`

Static E2E review:

- `frontend/e2e/wechat-article-flow.spec.ts` now asserts actual business outcomes: account selection, handoff visibility, reload persistence, generate-all action, conflict handling, preview, explicit sync, and account-switch cleanup.
- Per re-review scope, the full browser E2E was not rerun locally because parent-level CI already recorded a dedicated green run and the task explicitly excluded a full rerun.

### Residual Risk

- No open defect is confirmed in the scoped Fix Round1 diff.
- Remaining runtime confidence for the browser path depends on the parent-provided CI pass plus future integrated runs, because this scoped re-review intentionally did not rerun the full E2E flow locally.

### Final Re-Review Verdict

- Spec compliance: Approve
- Code quality: Approve
- Open counts: Critical 0 / Important 0 / Minor 0
