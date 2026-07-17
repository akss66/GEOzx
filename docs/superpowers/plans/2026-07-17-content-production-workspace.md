# Content Production Workspace Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the legacy six-column pipeline board with a real, permission-scoped desktop content workspace that exposes editable versioned deliverables, materials, stages, approvals, Agent activity, and publish preparation without mock fallbacks.

**Architecture:** Keep `ContentItem`, `Deliverable`, `AgentTask`, `GateApproval`, `MaterialAsset`, and `AgentToolCall` as the system of record. Add one aggregate read model for the selected content item, create deliverable revisions instead of mutating history, and render the workspace through focused frontend components rather than the legacy drawer.

**Tech Stack:** FastAPI, SQLAlchemy async, Pydantic, React, TypeScript, TanStack Query, Ant Design, Vitest, Pytest, Playwright.

## Global Constraints

- Desktop only for this phase; mobile remains deferred.
- Current project and account come from the global workspace context; never select the first project or account implicitly.
- Production pages contain no mock task, deliverable, approval, material, or publish state.
- Publishing remains `manual_checklist` and requires human confirmation.
- All content reads and writes enforce client/project access and workspace role on the API.
- A deliverable edit creates a new version and supersedes the prior live version.

---

### Task 1: Permission-scoped content read model

**Files:**
- Modify: `backend/app/api/orchestrator.py`
- Modify: `backend/app/schemas/orchestrator.py`
- Test: `backend/tests/test_content_workspace_api.py`

**Interfaces:**
- Produces: `GET /content-items/{content_item_id}/workspace -> ContentWorkspaceOut`
- Produces: project-scoped `GET /content-items`
- Consumes: `require_project_access` and `accessible_project_ids`

- [ ] Write tests proving an assigned member sees only accessible content and another-org or unassigned content returns `404`.
- [ ] Run the focused tests and confirm they fail against the current unscoped endpoints.
- [ ] Add `ContentWorkspaceOut` with project, account, task, deliverable, material, gate, compliance, and publish-tool-call data.
- [ ] Apply project access to list, create, start, board, history, readiness, rerun, and rollback operations.
- [ ] Run the focused and existing orchestrator/publish tests.

### Task 2: Versioned deliverable editing

**Files:**
- Modify: `backend/app/api/orchestrator.py`
- Modify: `backend/app/schemas/orchestrator.py`
- Test: `backend/tests/test_content_workspace_api.py`

**Interfaces:**
- Produces: `POST /deliverables/{deliverable_id}/revisions -> DeliverableOut`
- Consumes: `validate_payload(DeliverableType, dict)`

- [ ] Write a failing test proving edits produce `vN+1`, preserve the prior version, and reject invalid payloads.
- [ ] Implement schema validation, prior-version superseding, audit event creation, and role checks for lead/operator/editor.
- [ ] Run revision and rollback tests.

### Task 3: Content workspace frontend shell

**Files:**
- Replace: `frontend/src/pages/PipelineBoard.tsx`
- Create: `frontend/src/components/content/ContentWorkspace.tsx`
- Create: `frontend/src/components/content/ContentRail.tsx`
- Create: `frontend/src/components/content/ContentCanvas.tsx`
- Create: `frontend/src/components/content/contentPresentation.ts`
- Create: `frontend/src/styles/content-workspace.css`
- Modify: `frontend/src/api/orchestrator.ts`
- Modify: `frontend/src/types.ts`
- Test: `frontend/src/components/content/contentPresentation.test.ts`

**Interfaces:**
- Consumes: `getContentWorkspace`, `createContentItem`, `startPipeline`
- Produces: selected content rail, central typed deliverable canvas, explicit no-project/no-content states

- [ ] Add failing presentation tests for stage labels, status labels, latest deliverable selection, and typed sections.
- [ ] Implement the three-pane adaptive desktop workspace with only the center canvas visible by default.
- [ ] Bind project/account to `useCurrentWorkspace` without first-project fallback.
- [ ] Remove the legacy `DeliverableDrawer` from the route.

### Task 4: On-demand inspectors and real actions

**Files:**
- Create: `frontend/src/components/content/ContentInspector.tsx`
- Create: `frontend/src/components/content/DeliverableEditor.tsx`
- Create: `frontend/src/components/content/PublishPreparation.tsx`
- Modify: `frontend/src/api/orchestrator.ts`
- Test: `frontend/src/api/orchestrator.test.ts`

**Interfaces:**
- Produces: inspector modes `materials | versions | stages | agents | approvals | publish`
- Consumes: revision, rerun, rollback, publish-readiness APIs

- [ ] Add API tests for workspace loading and revision creation.
- [ ] Render materials, versions, tasks, gates, compliance, and publish tool calls from the aggregate response.
- [ ] Add type-aware deliverable editing and create revisions on save.
- [ ] Generate publish packages from real material selections and surface the pending human confirmation.

### Task 5: Verification and documentation

**Files:**
- Modify: `SPEC.md`
- Modify: `tasks/current.md`

- [ ] Run focused backend tests and Ruff on touched files.
- [ ] Run all frontend unit tests and production build.
- [ ] Validate at 1440x900 with real localhost data: no project, empty project, selected content, inspector switching, and publish preparation.
- [ ] Confirm there is no mock copy, raw JSON, horizontal overflow, or console error.
- [ ] Save acceptance screenshots under `.artifacts/` and submit P4 for user approval before deployment.
