# Task 14 Report — Register and execute the WeChat article-production Skill

## Status

DONE_WITH_CONCERNS

## TDD evidence

All production behavior was introduced after a focused failing test:

- Contract/routing RED: `uv run pytest tests/test_wechat_article_skill.py -q` initially reported 3 failed, 2 passed because the Skill was unregistered, natural-language routing returned `None`, and explicit cross-platform selection was classified as unknown. Registration, public policy, exact approval literal, and deterministic WeChat-only routing then made the slice green.
- Missing brief RED: the missing `brief.primary_cta` validation error escaped from `WechatArticleProductionInput`. The implementation now preserves only missing-field validation state, requests one durable clarification interrupt, pauses the same WorkTurn, and does not create another turn.
- Production RED: complete input returned `failed` from the intentionally missing production branch. The implementation now runs the bounded business stages, resolves scoped knowledge, invokes exactly the declared experts, creates a structured immutable article version, plans image slots, computes readiness, renders a safe preview, and pauses for the next explicit article action.
- Platform/action gate RED: direct runtime invocation accepted a non-WeChat account until article creation, and external action names were mistaken for a missing article brief. The implementation now rejects incompatible platforms before SkillRun creation and lists action-specific required fields without performing a side effect.
- Recovery RED: replaying a waiting article failed while reconstructing frozen internal input. The reconstruction is now compatible and replay returns the exact existing result without duplicating experts, SkillRun, interrupt, version, or image slot.
- Image-action RED: the runtime had no injected image provider or positive action path. The confirmed action now calls the existing `WechatArticleImageService` with the scoped working copy and idempotency key.
- Draft-sync RED: the runtime had no Task 13 draft-sync boundary. The confirmed action now requires the immutable version, confirmation boolean, and idempotency key, then calls `prepare_wechat_draft_sync_job` followed by `execute_wechat_draft_sync_job`; no `freepublish_submit` path exists.
- Image-slot persistence RED: Art Director slot placement was persisted only in `ArticleImageSlot`, not in the document AST. The minimal fix inserts validated `ImageSlotBlock` nodes at declared placements while keeping unselected slots out of preview rendering.

## Changed files

- `backend/app/orchestrator/skills/wechat_article_production.py` — strict input/output/image-plan schemas and exact SkillDefinition.
- `backend/app/orchestrator/skills/registry.py` — registers version 1.
- `backend/app/orchestrator/skills/public_catalog.py` — publishes only through account-scoped composer/artifact surfaces with WeChat article aliases.
- `backend/app/orchestrator/capability_router.py` — deterministic natural-language WeChat article route with platform filtering.
- `backend/app/orchestrator/skill_runtime.py` — reuses the single durable runtime for bounded stages, experts, interrupts, lineage, recovery, image generation, and Task 13 draft sync.
- `backend/app/schemas/skills.py` — minimal backward-compatible approval literal extension.
- `backend/tests/test_wechat_article_skill.py` — contract, route, waiting, action gate, side-effect, quality, lineage, and recovery coverage.

## Verification

- `cd backend && uv run pytest tests/test_wechat_article_skill.py tests/test_main_agent_worker_contract.py tests/test_main_agent_v3_integration.py -q` — 32 passed.
- `cd backend && uv run ruff check app/orchestrator/skills app/orchestrator/capability_router.py app/orchestrator/skill_runtime.py app/schemas/skills.py` — all checks passed.
- Related new files pass `ruff format --check`; the entire pre-existing `skill_runtime.py` has one unrelated baseline formatter delta near legacy account-analysis code, which this task deliberately did not modify.
- `git diff --check` — clean.
- Secret-pattern review found no credentials or private keys in the diff.

## Security, lineage, and idempotency self-review

- Skill execution validates user/org/thread/turn/run through the existing runtime and validates account platform before creating a SkillRun.
- CapabilityRequest account/thread/turn/run/message lineage remains exact; image and sync actions bind the working copy and immutable version to the selected account.
- Cross-account working-copy use returns a fail-closed scoped-result mismatch before image provider or draft service invocation.
- Scoped knowledge uses `list_agent_knowledge_for_account`; recorded citations remain immutable article-version evidence.
- Missing brief fields create exactly one durable `TurnInterrupt` on the existing turn. Recovery returns the persisted waiting result and does not repeat experts or writes.
- Initial production never calls image generation or draft sync. Image generation requires a separate action and idempotency key. Draft sync requires a separate immutable-version confirmation and the Task 13 idempotent path.
- Quality-review outage is represented as `{status: "unavailable"}` plus `QUALITY_REVIEW_UNAVAILABLE`; no numeric zero or `quality_score=0` is synthesized.
- User-facing messages name the article title and concrete action. No generic result/adoption wording was introduced.
- Existing Douyin/Xiaohongshu/Shipinhao tests and old approval policies remain green.

## Concerns

- The WeChat executor adds a substantial private branch to the existing `SkillRuntime`. It intentionally does not create a parallel runtime. Extracting it further would require exposing runtime-private stage, lease, expert, interrupt, and closure boundaries; that refactor is better handled as a separately specified behavior-preserving task.
- Production provider wiring for image generation and WeChat draft clients must supply the existing adapters when constructing `SkillRuntime`; absent dependencies fail closed as named unavailable states.

## Commit

Atomic commit: `feat: orchestrate WeChat article production` (SHA reported with the handoff because a commit cannot contain its own hash).

## Fix round 1/5 — durable clarification resume

Independent review found two gaps in the shipped clarification path. Both fixes followed RED/GREEN TDD against the real persistence boundaries:

- Resolution validation RED: strict clarification schemas using JSON Schema `required` accepted `{}` and missing required fields. The focused run reported 2 failures while a schema-less legacy interrupt remained compatible. `resolve_interrupt` now validates only clarification object schemas that declare a well-formed non-empty `required` list backed by `properties`; missing, unexpected, type/enum/const-mismatched values fail with `INTERRUPT_RESOLUTION_INVALID` before any status mutation. Legacy schemas without this contract remain unchanged.
- Worker recovery RED: `resolve_interrupt -> queued AgentRun -> _execute_v2_conversation_run -> execute_conversation_turn -> SkillRuntime` returned the original waiting result because the answer existed only under `resume_interrupt`. The resolver now recognizes only the same-run/same-thread/same-turn `wechat_article_production` SkillRun, merges only `primary_cta` into its frozen pending brief, revalidates the complete `ArticleBrief`, persists JSON-mode server-owned `trusted_structured_input`, and advances that SkillRun for recovery. The runtime accepts only the exact frozen-to-resolved transition tied to that persisted clarification; all other changed structured input still raises `SKILL_RECOVERY_INPUT_CONFLICT`.
- JSON boundary RED: the server-owned trusted input was converted back to Python-mode `AnyHttpUrl` before constructing the JSON-valued `CapabilityRequest`. `_capability_request_payload` now emits the validated model in JSON mode. The real worker test proves URL-bearing CTA input crosses this boundary.
- Domain validation RED: a present but empty `primary_cta` leaked a Pydantic error. It now returns HTTP 422 without resolving or versioning the interrupt.

Fix-round scope approved by the controller:

- `backend/app/services/turn_interrupts.py` — transactional resolution validation and scoped trusted-input projection.
- `backend/app/services/turn_execution.py` — one-line JSON serialization correction at the trusted-input boundary.
- `backend/tests/test_turn_interrupts_api.py` — required-field and legacy compatibility regression tests.
- Task 14-owned Runtime, WeChat Skill contract/helper, and test files.

Fix-round verification:

- Focused strict/legacy interrupt slice: 3 passed.
- Focused real worker recovery and invalid CTA slice: 2 passed.
- Capability request, conversation submission, turn execution, and full interrupt API regression: 129 passed.
- Required Task 14 gate: 34 passed in 18.10s.
- Ruff check passed for every changed production and test file; Task 14-owned new/modified files pass Ruff format checks.
- Full-file Ruff format checks still report unrelated pre-existing formatting deltas in the legacy portions of `skill_runtime.py`, `turn_execution.py`, `turn_interrupts.py`, and the old interrupt test file; no bulk formatting was applied.
- Targeted mypy reports 35 baseline errors in the pre-existing large Runtime/service files (34 in `skill_runtime.py`, one existing SQLAlchemy `rowcount` typing error in `turn_interrupts.py`); no error points to a newly added fix-round line.
- `git diff --check` is clean, and no credential/private-key pattern is present in the fix diff.

Fix-round self-review:

- The resolution cannot replace account, org, run, thread, skill code, requested action, or other frozen input. The pure WeChat helper accepts exactly `{primary_cta}` and reconstructs the complete input from server-frozen state.
- Invalid resolution is rejected before the interrupt update statement, so the interrupt remains pending and the original waiting runtime state is preserved.
- A successful resume retains one ConversationTurn, one AgentRun, one SkillRun, and exactly the three declared expert invocations. Replaying the worker result performs no duplicate expert or artifact work.
- No new runtime, queue, dependency, image-generation side effect, draft-sync side effect, or external publish path was added.
