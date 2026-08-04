# Weekly Operation Package Design

## Goal

Turn the exact weekly Douyin operation request into an evidence-backed package containing five distinct filming-ready scripts and a seven-day calendar, with one human confirmation that creates five manual-publish schedule entries and never publishes to the platform.

## Scope

This slice owns the operation/composite runtime, existing operating Skill schemas, server-only child lineage, the final `publishing_preparation` approval effect, and related tests. It does not change pending-work projections, database models or migrations, Task 6 execution ownership, or WebSocket behavior.

The existing standalone `script_generation` capability remains valid. Batch fields extend its report instead of replacing the singleton fields consumed by current APIs.

## Architecture

Keep the current child graph:

1. `topic_planning`
2. `script_generation`
3. `visual_brief_generation`
4. `content_calendar_planning`
5. `publishing_preparation`

The root operation Skill continues to orchestrate; each specialist remains the formal producer of its own artifact. Low-risk child artifacts remain truthfully `PENDING_REVIEW`. A server-only lineage capability allows the next child in the same operation execution to consume them without changing their public status. Only the final publishing-preparation artifact enters `waiting_permission`.

After the user confirms the final package, the existing approval transaction creates five `ContentScheduleEntry` rows for the five publish slots, approves the final artifact, and resumes the operation parent. The two review/buffer slots do not create schedule entries. No path in this workflow calls `platform.content_publish`.

## Strong Contracts

### Topic plan

`TopicPlanningReport` contains typed `TopicPlanItem` values with:

- `topic_id`
- `title`
- `angle`
- `format`

Operation mode requires exactly five non-empty, distinct topics. IDs are stable within the plan. If the specialist omits IDs, the runtime assigns `topic-01` through `topic-05` in plan order. Missing or duplicate content fails quality; the runtime does not copy or invent topics to obtain a passing count.

### Script package

`ScriptGenerationReport` keeps the existing singleton fields as a compatibility view of the first script and adds typed `FilmingScript` items with:

- `script_id`
- `topic_id`
- `title`
- `hook`
- `voiceover`
- `shot_list`
- `duration_seconds`
- `cta`
- `constraints_hit`

Operation mode requires exactly one script per source `topic_id`. All five scripts must be complete and distinct. Standalone script generation may return one item.

An `OFFER_TERMS` revision must preserve all five topic IDs, regenerate the script package and downstream artifacts as new deliverable versions, retain previous versions, include the raw constraint in the targeted script's `constraints_hit`, and reuse the prior evidence without another account-data read.

### Visual production requirements

The visual report contains one typed visual item per script and preserves both `script_id` and `topic_id`. Every item includes filming/shot requirements and asset needs. Missing, duplicate, or unbound visual items fail quality.

### Seven-day calendar

The calendar contains exactly seven consecutive dated slots in `Asia/Shanghai`:

- five `publish` slots, each bound to one unique `script_id` and a timezone-aware `scheduled_at`;
- two `review_buffer` slots for review or contingency work, with no fake content and no `script_id`.

The five publish slots cover every script exactly once.

### Final package

`PublishingPreparationReport` contains a typed `WeeklyOperationPackage` with:

- `schema_version=1`;
- source artifact IDs and versions;
- evidence references;
- topics, scripts, visual items, and calendar slots;
- quality results from every stage;
- participating experts;
- a manual-publish checklist;
- public next-step action codes with explicit Chinese labels.

Next steps describe real business actions such as filming and manually publishing. They never expose runtime statuses, checkpoint names, or internal orchestration terms.

## Deterministic Quality Gate

Every batch report contains a structured quality result:

- `version` for the deterministic checker;
- `status`: `passed` or `needs_review`;
- `score` and `threshold`;
- `checks[]`, each with `code`, `passed`, `message`, and `item_ids`.

The checker validates cardinality, required fields, ID uniqueness, one-to-one mappings, constraint hits, and cross-artifact bindings.

Script duplication uses a deterministic normalized value built from `hook + voiceover`. Normalization applies Unicode NFKC, lowercase conversion, removal of whitespace, and removal of both ASCII and Chinese punctuation. Exact matches fail. High similarity uses `difflib.SequenceMatcher` with a fixed threshold of `0.92`, applied only when both normalized values contain at least 40 characters so short hooks do not create false positives. Tests cover Chinese/English text, full-width/half-width punctuation, whitespace, five copied scripts, and short distinct text.

Malformed specialist output remains a `PENDING_REVIEW` artifact with explicit failed checks and causes the child to return `needs_review`. Missing fields may be represented as missing quality findings, but no template filler may turn them into a passing deliverable.

## Evidence and Provenance

The root performs the single audited `account.data_context` read established by Task 8A. Topic planning consumes the preloaded result. Every downstream payload carries the same evidence references, and the final package exposes them.

Each internal lineage reference freezes:

- artifact ID and version;
- source child SkillRun ID;
- parent operation SkillRun ID.

The resolver also checks organization, thread, turn, run, task, account, content item, child completion, parent link, and exact artifact provenance while the runtime root scope is locked. It accepts only the declared dependency artifact and only when invoked with the private server context. Any client-supplied pending artifact ID, missing lineage field, cross-scope artifact, stale version, or unrelated child fails closed.

The public confirmed-artifact resolver remains unchanged and continues to require `APPROVED`. Internal consumption never mutates or represents intermediate artifacts as approved.

## Single Approval and Scheduling

`publishing_preparation` builds only a manual-publish checklist and final package. Its prepare tool is the sole `before_finish` approval in the operation graph.

Approval runs under the existing composite runtime-root row lock. Before any insert, it validates that:

- the tool call, child, parent, run, task, account, content item, and artifact share one scope;
- the source deliverable is the final publishing-preparation artifact;
- the package has passed quality;
- it contains exactly five valid publish slots and two buffer slots.

Within that same transaction it creates exactly five schedule rows, each referencing the final artifact and its version. The locked approval state is the concurrency guard: a second request cannot perform the effect after the first changes the child/tool state. A replay returns the durable result. Tests use two independent sessions or requests to prove concurrent confirmation leaves exactly five rows.

Only after schedule creation succeeds is the final artifact marked `APPROVED`. Rejection creates no schedule rows and blocks the parent. Buffer slots never create schedule rows.

## Recovery and Revision

Child and root recovery reuse persisted graph nodes, artifacts, server lineage, and the single account-data tool ledger. A retry cannot create a second artifact for the same child SkillRun or a second set of schedule entries.

An `OFFER_TERMS` partial revision reuses topic/evidence checkpoints and starts at script generation. New child SkillRuns write new deliverable versions on the same content stream; old versions remain queryable and are not overwritten. The final package records the source versions used.

## User-Facing Copy

Successful preparation says the system has prepared five filming scripts and a seven-day arrangement. The only confirmation copy is equivalent to:

> 确认这份 7 天安排并创建 5 条手动发布任务。

After approval, the operation response directs the user to film or manually publish the five items and later record each one as published. It does not say that content has been published and does not expose internal terms.

## Test Strategy

Tests are written and observed failing before production changes:

1. Strengthen worker Scenario A with real account/content and benchmark evidence, exact five-topic/script/visual mappings, seven slots, one approval, two independent approval requests, five schedule entries, zero platform-publish calls, and business-facing copy.
2. Strengthen Scenario B to run the partial revision, retain topic IDs and evidence refs, produce new script/downstream versions, preserve old versions, hit the price constraint, and keep the account-data tool count at one.
3. Add schema tests for singleton compatibility and batch validation.
4. Add deterministic quality tests, including five copied scripts and normalization/short-text boundaries.
5. Add lineage tests for valid same-parent pending consumption and fail-closed client, cross-account, cross-run, cross-task, stale-version, and unrelated-child cases.
6. Add final approval tests for rejection, replay, and true two-session concurrency.
7. Run operation, operating, artifact, revision, worker-contract, Ruff, and Mypy checks.

