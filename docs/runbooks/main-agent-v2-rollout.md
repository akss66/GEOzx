# Main Agent V2 Rollout Runbook

## Boundary

- Service path: `backend` conversation APIs and turn execution for `POST /brain/conversations/{thread_id}/turns`
- Control plane: `main_agent_v2_enabled`
- Data plane: `conversation_threads`, `conversation_turns`, `agent_runs`, `skill_runs`, runtime `events`, and skill artifacts
- Dependency edges: legacy `POST /brain/messages`, runtime event persistence, `account.data_context`, composite `account_inspection`, and frontend turn projection

## Confirmed Facts

- `main_agent_v2_enabled` is a global backend setting in `backend/app/config.py`.
- Disabled mode returns typed `503 MAIN_AGENT_V2_DISABLED` from the new Turn endpoint and does not disable legacy `POST /brain/messages`.
- Enabled mode still restricts the new Conversation and Turn endpoints to `UserRole.ADMIN`; non-admin callers receive typed `403 MAIN_AGENT_V2_ROLLOUT_RESTRICTED`.
- Completed Turn diagnostics now emit one allowlisted JSON log event named `main_agent_turn_completed`.
- The diagnostic log only carries `thread_id`, `turn_id`, `run_id`, `mode`, `skill_run_id`, `task_id`, `artifact_ids`, and `status`.

## Assumptions

- Database backup, migration apply, and deploy/restart commands vary by environment and must use the existing release pipeline.

## Preflight

1. Keep production flag off until rollout owner approves.
2. Capture a database backup before applying the additive Main Agent V2 migration set.
3. Apply the release candidate in a non-production environment first.
4. Verify rollback baseline:
   - `POST /brain/conversations/{thread_id}/turns` returns `503` with `MAIN_AGENT_V2_DISABLED` when the flag is off.
   - `POST /brain/messages` still responds on the legacy path when the flag is off.

## Backup And Upgrade

1. Export a restore-tested database backup before any migration or app restart.
2. Apply only the additive migration set that introduced Thread, Turn, SkillRun, and provenance columns.
3. Restart backend processes with `main_agent_v2_enabled=false`.
4. Run focused verification before enabling traffic:
   - `cd backend && uv run pytest tests/test_conversation_api.py -k feature_flag -v`
   - `cd backend && uv run pytest tests/test_conversation_api.py -v`
5. Do not attempt schema rollback after production writes land unless:
   - the flag is already off,
   - no active release is reading the new lineage fields,
   - and the new Thread/Turn/SkillRun rows are exported or accepted as discardable.

## Controlled Enablement

1. Turn `main_agent_v2_enabled=true` only in the target environment used by administrators.
2. Limit first traffic slice to one administrator and one authorized account.
3. Keep non-admin users on the legacy frontend path; the backend now enforces this during rollout.
4. Widening beyond administrators requires a separate approved code or configuration change. Do not treat the current rollout guard as something to bypass operationally.
5. Widen traffic only after route mix, failures, approvals, and provenance all stay within threshold.

## Observability Checks

Filter logs by the JSON message field containing `"event":"main_agent_turn_completed"`.

- Route distribution:
  - Verify every sampled new Turn has exactly one completion log.
  - Verify `mode` matches expected user intent mix: `answer`, `clarify`, `query`, `skill`, `task`, or `action`.
- Success and error rate:
  - Expected steady-state: `failed` + `blocked` under 5% of rollout turns over 15 minutes.
  - Disable the flag if failures or blocks exceed 10% over 15 minutes, or if duplicate completion logs appear for one `run_id`.
- Retry and idempotency:
  - One `run_id` must map to one completion log.
  - Repeated requests with the same `client_message_id` must not create duplicate terminal logs or duplicate visible outputs.
- Approval pressure:
  - Watch `waiting_permission` and `waiting_decision`.
  - Investigate any approval-waiting Turn older than 15 minutes during the initial rollout.
- Provenance completeness:
  - `query` route: expect `skill_run_id` set, `task_id=null`, `artifact_ids=[]`.
  - `skill` route with artifact creation: expect non-null `skill_run_id`, non-null `task_id`, and non-empty `artifact_ids`.
  - `task` or `action` route: expect non-null `task_id`.
  - Sample a completed artifact-producing Turn and confirm the logged `artifact_ids` resolve to the same artifact shown in the account Artifact Center.

## Frontend Fallback

1. Keep the legacy BrainTask projection path available during rollout.
2. If the new Turn endpoint returns `503 MAIN_AGENT_V2_DISABLED` or `403 MAIN_AGENT_V2_ROLLOUT_RESTRICTED`, the frontend must fall back to the legacy `POST /brain/messages` flow and render the legacy BrainTask projection.
3. After disabling the flag, verify the frontend no longer attempts new Turn creation and that legacy BrainTask history remains readable.

## Disable And Rollback

1. Immediate rollback:
   - set `main_agent_v2_enabled=false`,
   - restart or reload backend configuration,
   - confirm new Turn creation is blocked with `503 MAIN_AGENT_V2_DISABLED`,
   - confirm legacy `POST /brain/messages` still works.
2. Keep existing Main Agent V2 data in place during an application rollback. The migrations are additive and historical rows are part of the audit trail.
3. Schema rollback is a separate operation and is not part of incident mitigation. Only consider it after the application has fully returned to legacy mode and no consumer depends on new lineage records.

## Incident Notes

- Smallest safe action for elevated failure rate is flag disablement, not schema rollback.
- If diagnostic logs are missing, treat the rollout as unobservable and disable the flag.
- This task does not deploy production. Production enablement requires a separate launch approval.
