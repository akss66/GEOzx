# SDD ledger — plan: docs/superpowers/plans/2026-08-11-wechat-official-account-agent.md

Workspace: C:/Users/AKSSINA/Desktop/Workplace/GEOzx/.worktrees/wechat-official-agent
Branch: codex/wechat-official-agent-implementation
Base: b9e16c5be31d072381c9d1c0ced3d49b4b9ad59d

Baseline:
- Backend: 1758 passed, 20 skipped, 5 warnings in 542.37s.
- Frontend: 82 files, 544 tests passed in 29.62s; existing jsdom and React Router warnings recorded.
- Dependency install: uv sync and npm ci succeeded; npm reported 2 existing moderate vulnerabilities, not auto-fixed.

Task 1: dispatched from base b9e16c5be31d072381c9d1c0ced3d49b4b9ad59d
Task 1: fix round 1/5 (PostgreSQL migration round trip verified; no code change; 0 open findings)
Task 1: complete (commit 5155c38f27ee39672c8ab0e704ba4f35fb7ac056, spec and quality review clean)
Task 2: dispatched from base 5155c38f27ee39672c8ab0e704ba4f35fb7ac056
Task 2: fix round 1/5 in progress (Critical 1, High 1, Medium 2 from review; base defc4c2833ad969bbeeba20425be74b593ce230c)
Task 2: fix round 1/5 (4 addressed, 1 new Critical open; commit 24f149c5cda918d5beba4e707ccf174a33bc1926)
Task 2: fix round 2/5 in progress (token refresh must never restore revoked authorization)
Task 2: fix round 2/5 (remaining Critical addressed, no new breakage; commit 273705f482b221f966673ca60b270008a7cce68a)
Task 2: complete (commits defc4c2833ad969bbeeba20425be74b593ce230c..273705f482b221f966673ca60b270008a7cce68a, spec and quality review clean)
Task 3: dispatched from base 273705f482b221f966673ca60b270008a7cce68a
Task 3: user decision — use server runtime WECHAT_COMPONENT_VERIFY_TOKEN and WECHAT_COMPONENT_ENCODING_AES_KEY; never persist, log, or expose them.
Task 3: fix round 1/5 in progress (6 Important findings from security review; base fb8d6e2)
Task 3: minor (deferred): account upsert lacks database uniqueness on scoped external identity; carry to final migration review.
Task 3: fix round 1/5 (6 Important addressed, no new breakage; commit a9c2094)
Task 3: complete (commits fb8d6e2..a9c2094, spec and quality review clean; 1 deferred Minor)
Task 4: dispatched from base a9c209438fb8e9614e1014666cff80e26b6617d5
Task 4: fix round 1/5 in progress (3 Required findings: shared permission intersection, live authorizer-info fail-closed, missing component scopes fail-closed; base 75d4eee)
Task 4: fix round 1/5 (3 addressed, 0 open; commit f4f5223)
Task 4: complete (commits 75d4eee..f4f5223, spec and quality review clean)
Task 5: dispatched from base f4f5223a3c8d66e6ac7233839235afeeffca9169
Task 5: fix round 1/5 in progress (Important 1: binding/base kind and client scope not enforced; Minor 1: tests lack real FK parents; base 46a4707)
Task 5: user decision — approved adding backend/app/models/workspace.py and accounts(id, client_id) composite unique constraint for database-enforced binding scope.
Task 5: fix round 1/5 (Important 1 and Minor 1 addressed, 0 open; commit 71fe12d)
Task 5: complete (commits 46a4707..71fe12d, spec and quality review clean)
Task 6: dispatched from base 71fe12da2c3e0a60780c69ded6a5884f6db4abd6
Task 6: fix round 1/5 in progress (Important 2: reviewer edit bypass, concurrent rebind 500; Minor 1: external-claim flag drift; base 17003af)
Task 6: fix round 1/5 (Important 2 and Minor 1 addressed, 0 open; commit 869dabf)
Task 6: complete (commits 17003af..869dabf, spec and quality review clean)
Task 7: dispatched from base 869dabf8888527afbb907ad25169e622b34dc6b4
Task 7: user decision — approved expanding scope with immutable KnowledgeCitation snapshot columns, migration 20260811_0250, and migration/model tests; legacy citations remain NULL/unknown and external-claim gates fail closed.
Task 7: fix round 1/5 in progress (Important 2: base entries incorrectly project-filtered; post-LIMIT claim filtering starves valid evidence; base 9824a64)
Task 7: user decision — approved expanding scope for organization-shared KnowledgeEntry support with nullable client scope, derived base kind, migration 20260811_0260, database constraints, API wiring, and end-to-end tests.
Task 7: fix round 1/5 (original Important 2 addressed; 1 new Important open: NULL knowledge_base_kind bypasses CHECK/composite FKs; commit cd016e0)
Task 7: fix round 2/5 in progress (close SQL three-valued NULL-kind raw insert/update bypass; base cd016e0)
Task 7: fix round 2/5 (remaining Important addressed, 0 open; commit d3dbafe)
Task 7: complete (commits 9824a64..d3dbafe, spec and quality review clean)
Task 8: dispatched from base d3dbafef57b0c53974e14fddbe0f8c51c2d308ee
Task 8: migration-chain decision — because user-approved Tasks 7 added revisions 0250 and 0260, revision 0300 must down-revise 0260 and its direct round-trip gate is downgrade 0260 (not stale plan revision 0200).
Task 8: fix round 1/5 in progress (Important 3: WeChat deliverables not registered in shared payload/artifact pipeline; ArticleWorkingCopy document can bypass ArticleDocument validation; working-copy/image-slot lineage permits account_id=NULL; base f5ecf0b)
Task 8: minor (deferred): model declares based_on_deliverable_id index but migration 0300 does not create it; carry to final migration review.
Task 8: fix round 1/5 (2 addressed, 1 open — direct SQL/Core insert can still persist invalid ArticleDocument JSON; commit 078a39c)
Task 8: fix round 2/5 in progress (close database-level ArticleDocument validation bypass; base 078a39c)
Task 8: fix round 2/5 (remaining Important addressed, 0 open; commit a508cae)
Task 8: complete (commits f5ecf0b..a508cae, spec and quality review clean; 1 deferred Minor)
Task 9: dispatched from base a508cae8afba51e76a90b31460015451431ce79e
Task 9: version-stream decision — use agent_code='02-content-director' for the unified immutable article stream; serialize allocation by locking ContentItem, allocate max(version)+1, and map residual unique races to structured 409 without retry/overwrite.
Task 9: fix round 1/5 in progress (Important 2: unique-race 409 reports stale currentVersion; immutable snapshots bypass shared strict deliverable validation; base 523532b)
Task 9: minor (deferred): diff tests do not lock in moved+changed and CJK/punctuation determinism; carry to final review.
Task 9: fix round 1/5 (2 addressed, 0 open; commit f3f0c84)
Task 9: complete (commits 523532b..f3f0c84, spec and quality review clean; 1 deferred Minor)
Task 10: dispatched from base f3f0c843248b3994613f44d793296f4f46958f83
Task 10: lineage decision — add structured ArticleDocument.claims, exact ArticleVersionCitation mappings, immutable KnowledgeCitation effective/expires snapshots, and migration 0330; readiness consumes only version mappings/snapshots and never account-global/current-entry evidence.
Task 10: renderer-limit decision — normalized HTML must be strictly under 20,000 Unicode code points and strictly under 1,048,576 UTF-8 bytes; title<=32, author<=16, digest<=120; secondary sources cite the currently unreachable official Add_draft page, and Task18 must reverify against the live official endpoint.
Task 10: fix round 1/5 in progress (Critical 1: password-only URL credentials bypass renderer allowlist; Important 1: create_article citation failure can leave pending partial rows in caller transaction; base 9e7bc90)
Task 10: fix round 1/5 (Critical 1 and Important 1 addressed, 0 open; commit 5de357f)
Task 10: complete (commits 9e7bc90..5de357f, spec and quality review clean)
Task 11: dispatched from base 5de357f8e8fce138153d23da295965842aaca8eb
Task 11: complete (commit ca99cb2, Task8-11 70 passed, independent review clean; provider-neutral generation returns 503 until a deployment provider is injected, while upload/select remain available)
Task 12: dispatched from base ca99cb2458a27373682c40711c35d802c4f5345f
Task 12: fix round 1/5 (Critical token sanitization and Important HTML whitespace/remote optional-field findings addressed by 05216a5; canonicalization false-positive remained)
Task 12: fix round 2/5 (remaining canonicalization finding addressed by 8cae295; independent fix review clean)
Task 12: complete (commits a1f87a0..8cae295, 42 focused + 59 regression passed; Task18 live official response/HTML verification remains mandatory)
Task 13: dispatched from base 8cae295f7e77b0726bdbcbaab4acda8cd6d36845
Task 13: approval/idempotency decision - the authenticated POST is the explicit draft-sync approval and must persist an immutable actor/version/account/request-digest snapshot; any same-key different-digest request is rejected before external calls. Every external write has a committed intent and result Event; an intent without result is reconciliation-required and is never blindly replayed.
Task 12: documentation boundary - official WeChat draft/material pages are the authority but were not retrievable from the current tooling on 2026-08-12; implement the plan's typed endpoints and fail-closed response contracts, record official URLs in the report, and require Task18 live capability/contract verification before production enablement.
Task 12: fix round 1/5 in progress (Critical 1: JSON/colon/query-style token leaks were not redacted from errmsg/rid; Important 2: canonical hash dropped visible inline whitespace, and draft/get rejected empty optional remote strings; base a1f87a0)
Task 12: fix round 1/5 implemented and self-verified (3 addressed locally; commit 05216a5; focused 38 passed, renderer/API/images 59 passed, Ruff/format/Mypy/diff-check clean; independent fix re-review pending)
Task 11: dependency decision — approved Pillow>=11.3,<13 for fail-closed decoded-image validation; no handwritten JPEG/WebP parser. Durable image idempotency/cost audit uses pre-call request Event + post-call result Event with SHA-256 keys and non-sensitive payloads.
Task 4: approved capability contract — content permission IDs 11 or 100 in both component/account grants; analytics ID 7; OA type {0,1,2}; verified id >=0; read-only authorizer-info/material-count/draft-count probes; fail closed; freepublish always disabled.

Task 13: fix round 1/5 (Critical crash recovery and Important POST role privacy addressed; commit c4dab49; independent fix review clean)
Task 13: complete (commits 15bcaaf..c4dab49; 31 focused, regressions 76 and 73 passed)
Task 14: dispatched from base c4dab49ead68f540e1b59f1956aed80ce7cbeb14

Task 14: fix round 1/5 in progress (Important 2: clarification resolution is not projected into resumed frozen Skill input; empty/missing required fields can resolve interrupt; base df9e5cd)

Task 14: fix round 1/5 (2 Important addressed, 0 open; commit 78216a4; independent fix re-review clean)
Task 14: complete (commits df9e5cd..78216a4; 34 focused and 129 related regressions passed)
Task 15: dispatched from base 78216a4e366ce1b91d496af149de00a246a706e4

Task 15: fix round 1/5 in progress (Important 3: knowledge base pagination truncates choices; real 422 component setup error is generic; WeChat leaks into legacy publish-preparation flow; base c885af9)

Task 15: fix round 1/5 (3 Important addressed, 0 open; commit 29be8b6; independent fix re-review clean)
Task 15: complete (commits c885af9..29be8b6; 37 implementation and 29 independent focused regressions passed)
Task 16: dispatched from base 29be8b6efea3a59024a230d0c44a9d0b316bb471

Task 16: backend scope expansion approved (working-copy account projection + explicit immutable-version draft-sync context; read-only/fail-closed/no WeChat side effect)

Task 16: fix round 1/5 in progress (Important 1: remote conflict/reconciliation displayed but not folded into readiness; base 09094bb)

Task 16: fix round 1/5 (remote conflict/reconciliation fail-closed addressed; commit 2c063f0; independent re-review clean)
Task 16: complete (commits 09094bb..2c063f0; frontend 28, backend targeted 7; review 0 Critical/Important)

Task 17: dispatched from base 2c063f0c2b4bfa239fe8862e6e206b7b633fc747

Task 17: fix round 1/5 in progress (Critical real backend projection contract mismatch; Important E2E never reaches business assertion; Minor account defense; base baa8751)

Task 17: fix round 1/5 implemented (original Critical/Important/Minor addressed locally; commit 87cdcbb; independent scoped re-review pending)
