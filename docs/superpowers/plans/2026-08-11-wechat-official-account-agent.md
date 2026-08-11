# WeChat Official Account Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (\`- [ ]\`) syntax for tracking.

**Goal:** Extend the existing main Agent so an operator can create one evidence-grounded WeChat long-form article, manage its images and versions, preview it, and explicitly sync an immutable version to an authorized WeChat Official Account draft box.

**Architecture:** Keep ConversationThread, WorkTurn, SkillRun, ContentItem, Deliverable, PlatformIntegration, PlatformAccountAuth, MaterialAsset, Redis/ARQ, and the current React application as the system of record. Add bounded WeChat component authorization, brand-knowledge binding, structured article working-copy/version models, image-generation orchestration, a constrained WeChat renderer, and an idempotent draft-sync boundary. All external calls remain server-side and all user-visible work stays attached to the originating WorkTurn.

**Tech Stack:** Python 3.11, FastAPI, Pydantic 2, SQLAlchemy Async, Alembic, PostgreSQL, Redis, ARQ, httpx, React 18, TypeScript 5.6, Vite 6, Ant Design, TanStack Query, Zustand, Vitest, Playwright.

## Global Constraints

- The only production account connection is WeChat Open Platform third-party authorization.
- One account has at most one active primary brand knowledge base.
- A shared organization knowledge base may be attached in addition to the primary brand base.
- Product facts, cases, promises, prices, and numeric claims require verified citations.
- Image prompts are hidden until the operator requests them.
- Initial drafts plan image slots but do not spend image-generation credits automatically.
- The first release syncs drafts only and never invokes freepublish_submit.
- Every WeChat write requires an immutable article version, explicit user confirmation, account scope, and an idempotency key.
- Local working-copy autosave never silently overwrites a newer copy.
- A remotely edited WeChat draft is never silently overwritten.
- A quality-review outage is represented as unavailable, never as score 0.
- User-visible copy names the concrete object and action; do not add generic “成果” or “采用” actions.
- No new framework, task queue, database, or parallel Agent runtime.
- Preserve unrelated working-tree changes, especially docs/ideas and docs/intent.

---

## 1. Dependency Graph and Delivery Slices

    Slice A: WeChat authorization foundation
      Task 1 → Task 2 → Task 3 → Task 4

    Slice B: Brand knowledge scope
      Task 5 → Task 6 → Task 7

    Slice C: Article domain and media
      Task 8 → Task 9 → Task 10 → Task 11

    Slice D: Durable WeChat draft sync
      Task 12 → Task 13

    Slice E: Main Agent and product UX
      Task 14 → Task 15 → Task 16 → Task 17

    Slice F: Production evidence and handoff
      Task 18

Tasks within a slice are sequential. After Slice A and Slice B pass their integration gates, the article-domain work can proceed without a live WeChat account by using mock transports. Real WeChat authorization and draft creation occur only in Task 18 after explicit production approval.

## 2. File Responsibility Map

### Backend

- backend/app/models/platform.py: organization platform configuration and per-account authorization.
- backend/app/models/knowledge.py: knowledge bases, bindings, entries, suggestions, and citations.
- backend/app/models/wechat_article.py: article working copies, image slots, and remote draft mappings.
- backend/app/models/content.py: ContentItem and immutable Deliverable integration.
- backend/app/models/publishing.py: durable external-write ledger.
- backend/app/schemas/platform.py: authorization session, callback, and capability contracts.
- backend/app/schemas/knowledge.py: knowledge-base and binding contracts.
- backend/app/schemas/wechat_article.py: ArticleBrief, ArticleDocument, versions, image slots, preview, and sync contracts.
- backend/app/services/wechat_component.py: component ticket, component token, authorization, and authorizer token lifecycle.
- backend/app/services/wechat_capabilities.py: account capability probe and normalized snapshot.
- backend/app/services/knowledge_workspace.py: scoped retrieval and citations.
- backend/app/services/wechat_articles.py: working-copy autosave, immutable versions, diffs, and local conflicts.
- backend/app/services/wechat_renderer.py: constrained document-to-WeChat HTML rendering.
- backend/app/services/image_generation.py: provider-neutral image generation and selection.
- backend/app/services/wechat_drafts.py: body-image upload, cover upload, remote conflict detection, and draft calls.
- backend/app/api/platform_integrations.py: admin authorization and public encrypted callbacks.
- backend/app/api/knowledge.py: knowledge-base and account-binding endpoints.
- backend/app/api/wechat_articles.py: article, image, preview, version, and sync endpoints.
- backend/app/orchestrator/skills/wechat_article_production.py: public Skill contract.
- backend/app/orchestrator/skill_runtime.py: bounded execution path for the new Skill.

### Frontend

- frontend/src/types/wechatArticle.ts: isolated WeChat article contracts.
- frontend/src/services/wechatArticle.ts: article and image API client.
- frontend/src/services/wechatIntegration.ts: authorization and capability client.
- frontend/src/pages/Accounts.tsx: WeChat authorization entry and capability status.
- frontend/src/pages/Knowledge.tsx: brand knowledge-base binding and verified-fact management.
- frontend/src/pages/WechatArticleWorkspace.tsx: editor, preview, and version surface.
- frontend/src/components/wechat-article/: focused editor, image-slot, conflict, and sync components.
- frontend/src/pages/BrainHome.tsx: WorkTurn-to-article handoff and real-time state.

### Documentation and tests

- backend/tests/test_wechat_*.py: unit and integration coverage.
- frontend/src/**/*.test.tsx: component and service coverage.
- frontend/e2e/wechat-article-flow.spec.ts: browser flow.
- docs/adr/0004-wechat-third-party-platform-and-brand-knowledge-scope.md: durable architecture decision.
- docs/runbooks/wechat-official-account-rollout.md: production configuration, smoke test, and rollback.

---

### Task 1: Add WeChat platform identity and component credential storage

**Files:**
- Modify: backend/app/models/enums.py
- Modify: backend/app/models/platform.py
- Modify: backend/app/models/__init__.py
- Create: backend/migrations/versions/20260811_0100_wechat_component_credentials.py
- Create: backend/tests/test_wechat_component_models.py

**Interfaces:**
- Produces: Platform.WECHAT_OFFICIAL_ACCOUNT.
- Produces: WechatComponentCredential with platform_integration_id, encrypted ticket/token, timestamps, and last_error.
- Consumes: existing PlatformIntegration and credential encryption conventions.

- [ ] **Step 1: Write the failing model test**

~~~python
def test_wechat_component_credential_contract():
    assert Platform.WECHAT_OFFICIAL_ACCOUNT.value == "wechat_official_account"
    row = WechatComponentCredential(platform_integration_id=7)
    assert row.platform_integration_id == 7
    assert row.component_verify_ticket_encrypted is None
    assert row.component_access_token_encrypted is None
~~~

- [ ] **Step 2: Run the test and confirm RED**

Run:

    cd backend
    uv run pytest tests/test_wechat_component_models.py -q

Expected: import or enum-member failure because the model does not exist.

- [ ] **Step 3: Add the minimal enum, model, export, and migration**

The new model is one-to-one with PlatformIntegration:

~~~python
class WechatComponentCredential(Base, TimestampMixin):
    __tablename__ = "wechat_component_credentials"
    __table_args__ = (
        UniqueConstraint("platform_integration_id", name="uq_wechat_component_integration"),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    platform_integration_id: Mapped[int] = mapped_column(
        ForeignKey("platform_integrations.id", ondelete="CASCADE"),
        nullable=False,
    )
    component_verify_ticket_encrypted: Mapped[str | None] = mapped_column(Text)
    ticket_received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    component_access_token_encrypted: Mapped[str | None] = mapped_column(Text)
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
~~~

Migration down_revision is 20260805_0300.

- [ ] **Step 4: Run model and migration tests**

Run:

    uv run pytest tests/test_wechat_component_models.py -q
    uv run alembic upgrade head
    uv run alembic downgrade 20260805_0300
    uv run alembic upgrade head

Expected: PASS and reversible migration.

- [ ] **Step 5: Run backend static checks**

Run:

    uv run ruff check app/models tests/test_wechat_component_models.py
    uv run mypy app/models/platform.py

- [ ] **Step 6: Commit**

    git add backend/app/models backend/migrations/versions/20260811_0100_wechat_component_credentials.py backend/tests/test_wechat_component_models.py
    git commit -m "feat: add WeChat component credential model"

---

### Task 2: Implement the WeChat component-token and authorizer-token service

**Files:**
- Create: backend/app/services/wechat_component.py
- Modify: backend/app/schemas/platform.py
- Create: backend/tests/test_wechat_component_service.py

**Interfaces:**
- Consumes: PlatformIntegration, WechatComponentCredential, PlatformAccountAuth.
- Produces: WechatOpenPlatformClient.
- Produces: get_component_access_token(session, integration_id) -> str.
- Produces: get_authorizer_access_token(session, account_id) -> str.
- Produces: exchange_authorization_code(...) -> WechatAuthorizationGrant.

- [ ] **Step 1: Write failing token-lifecycle tests**

~~~python
async def test_component_token_refreshes_before_expiry(session, mock_transport):
    token = await service.get_component_access_token(session, integration.id)
    assert token == "component-token"
    assert mock_transport.calls == ["/cgi-bin/component/api_component_token"]


async def test_authorizer_refresh_token_is_rotated_and_encrypted(session, mock_transport):
    token = await service.get_authorizer_access_token(session, account.id)
    assert token == "authorizer-token-2"
    assert auth.refresh_token_encrypted != "refresh-token-2"
~~~

- [ ] **Step 2: Run the tests and confirm RED**

    cd backend
    uv run pytest tests/test_wechat_component_service.py -q

Expected: module-not-found failure.

- [ ] **Step 3: Define strict response contracts**

~~~python
class WechatAuthorizationGrant(BaseModel):
    authorizer_appid: str
    authorizer_access_token: str | None
    authorizer_refresh_token: str | None
    expires_in: int | None
    func_info: list[int]
~~~

Validate every WeChat response before persistence. Convert nonzero errcode to WechatIntegrationError carrying code, retryability, rid, and endpoint.

- [ ] **Step 4: Implement cache and refresh rules**

- Refresh component and authorizer access tokens five minutes before expiry.
- Encrypt every persisted token with backend/app/core/credential_crypto.py.
- Never log request bodies containing secrets.
- Use an async per-token lock so concurrent callers perform one refresh.
- Preserve a rotated authorizer_refresh_token when WeChat returns one.

- [ ] **Step 5: Run focused and static checks**

    uv run pytest tests/test_wechat_component_service.py -q
    uv run ruff check app/services/wechat_component.py app/schemas/platform.py tests/test_wechat_component_service.py
    uv run mypy app/services/wechat_component.py

- [ ] **Step 6: Commit**

    git add backend/app/services/wechat_component.py backend/app/schemas/platform.py backend/tests/test_wechat_component_service.py
    git commit -m "feat: manage WeChat component and authorizer tokens"

---

### Task 3: Add authorization sessions and encrypted WeChat callbacks

**Files:**
- Modify: backend/app/api/platform_integrations.py
- Modify: backend/app/schemas/platform.py
- Create: backend/tests/test_wechat_authorization_api.py
- Create: docs/adr/0004-wechat-third-party-platform-and-brand-knowledge-scope.md

**Interfaces:**
- Consumes: get_component_access_token and exchange_authorization_code from Task 2.
- Produces: POST /platform-integrations/wechat/authorization-sessions.
- Produces: GET /platform-integrations/wechat/oauth/callback.
- Produces: POST /platform-integrations/wechat/events.

- [ ] **Step 1: Write failing API contract tests**

~~~python
async def test_create_authorization_session_returns_official_url(client, admin):
    response = await client.post(
        "/platform-integrations/wechat/authorization-sessions",
        json={"knowledge_base_id": 12},
        headers=admin.headers,
    )
    assert response.status_code == 201
    assert "pre_auth_code=" in response.json()["authorization_url"]


async def test_invalid_callback_signature_is_rejected(client):
    response = await client.post(
        "/platform-integrations/wechat/events?msg_signature=bad",
        content="<xml />",
    )
    assert response.status_code == 401
~~~

- [ ] **Step 2: Run the tests and confirm RED**

    cd backend
    uv run pytest tests/test_wechat_authorization_api.py -q

Expected: 404 for the new routes.

- [ ] **Step 3: Implement signed authorization state**

State must contain state_id, org_id, initiated_by_id, optional client/project/knowledge-base IDs, issued_at, and expires_at. Persist only a hash of the one-time state and consume it once.

- [ ] **Step 4: Implement encrypted event handling**

The public event route must:

1. validate timestamp and nonce;
2. validate msg_signature;
3. AES-decrypt the body;
4. deduplicate component ticket, authorized, updateauthorized, and unauthorized events;
5. persist only normalized non-secret event data;
6. return success immediately.

- [ ] **Step 5: Implement authorization callback**

On success:

- exchange authorization_code;
- upsert Account and PlatformAccountAuth;
- set external_open_id to authorizer_appid;
- persist encrypted refresh token;
- persist func_info as sorted scopes;
- redirect to a frontend result route without tokens.

- [ ] **Step 6: Record the architecture decision**

ADR 0004 must explain why third-party authorization replaces per-account AppSecret and why brand knowledge is bound independently from platform accounts.

- [ ] **Step 7: Verify**

    uv run pytest tests/test_wechat_authorization_api.py tests/test_wechat_component_service.py -q
    uv run ruff check app/api/platform_integrations.py app/schemas/platform.py tests/test_wechat_authorization_api.py

- [ ] **Step 8: Commit**

    git add backend/app/api/platform_integrations.py backend/app/schemas/platform.py backend/tests/test_wechat_authorization_api.py docs/adr/0004-wechat-third-party-platform-and-brand-knowledge-scope.md
    git commit -m "feat: add WeChat third-party authorization flow"

---

### Task 4: Probe and expose per-account WeChat capabilities

**Files:**
- Create: backend/app/services/wechat_capabilities.py
- Modify: backend/app/api/platform_integrations.py
- Modify: backend/app/schemas/platform.py
- Create: backend/tests/test_wechat_capabilities.py

**Interfaces:**
- Consumes: authorizer access token from Task 2.
- Produces: probe_wechat_capabilities(session, account_id) -> WechatCapabilitySnapshot.
- Produces: GET /accounts/{accountId}/platform-capabilities.

- [ ] **Step 1: Write failing normalization tests**

~~~python
def test_capability_snapshot_never_enables_publish_in_v1():
    snapshot = normalize_capabilities(
        func_info=[1, 7, 11, 100],
        account_profile={"verify_type_info": {"id": 0}},
    )
    assert snapshot.draft_add.can_use is True
    assert snapshot.freepublish.can_use is False
    assert snapshot.freepublish.reason == "disabled_by_product_policy"
~~~

- [ ] **Step 2: Run and confirm RED**

    cd backend
    uv run pytest tests/test_wechat_capabilities.py -q

- [ ] **Step 3: Implement the typed capability matrix**

~~~python
class CapabilityState(BaseModel):
    can_use: bool
    reason: str | None = None
    permission_ids: list[int] = Field(default_factory=list)


class WechatCapabilitySnapshot(BaseModel):
    account_id: int
    upload_article_image: CapabilityState
    add_permanent_material: CapabilityState
    draft_add: CapabilityState
    draft_get: CapabilityState
    draft_update: CapabilityState
    analytics: CapabilityState
    freepublish: CapabilityState
    checked_at: datetime
~~~

The service must combine third-party platform permission, granted func_info, account qualification, live probe results, and product policy.

- [ ] **Step 4: Verify API scope and copy**

Test 403 for an inaccessible account, 409 for an unauthorized account, and actionable reason codes for missing permissions.

- [ ] **Step 5: Run checks and commit**

    uv run pytest tests/test_wechat_capabilities.py tests/test_wechat_authorization_api.py -q
    uv run ruff check app/services/wechat_capabilities.py app/api/platform_integrations.py app/schemas/platform.py
    git add backend/app/services/wechat_capabilities.py backend/app/api/platform_integrations.py backend/app/schemas/platform.py backend/tests/test_wechat_capabilities.py
    git commit -m "feat: expose WeChat account capabilities"

---

### Task 5: Add brand knowledge bases and account bindings

**Files:**
- Modify: backend/app/models/knowledge.py
- Modify: backend/app/models/__init__.py
- Create: backend/migrations/versions/20260811_0200_brand_knowledge_bases.py
- Create: backend/tests/test_brand_knowledge_models.py

**Interfaces:**
- Produces: KnowledgeBase.
- Produces: AccountKnowledgeBinding.
- Extends: KnowledgeEntry with knowledge_base_id, entry_kind, verification fields, source_attachment_id, claim permission, and validity dates.

- [ ] **Step 1: Write failing database-invariant tests**

~~~python
async def test_account_has_only_one_active_primary_brand_binding(session):
    session.add_all([
        AccountKnowledgeBinding(
            org_id=1,
            account_id=4,
            knowledge_base_id=10,
            binding_type="primary_brand",
            status="active",
        ),
        AccountKnowledgeBinding(
            org_id=1,
            account_id=4,
            knowledge_base_id=11,
            binding_type="primary_brand",
            status="active",
        ),
    ])
    with pytest.raises(IntegrityError):
        await session.commit()
~~~

- [ ] **Step 2: Run and confirm RED**

    cd backend
    uv run pytest tests/test_brand_knowledge_models.py -q

- [ ] **Step 3: Implement models and partial unique indexes**

KnowledgeBase.kind is brand or organization_shared. A brand base requires client_id; an organization_shared base requires client_id null. AccountKnowledgeBinding enforces one active primary_brand binding per account with a PostgreSQL partial unique index.

- [ ] **Step 4: Add backward-compatible KnowledgeEntry migration**

- Add nullable knowledge_base_id first.
- Keep current client_id/project_id behavior readable.
- Do not auto-bind legacy entries to arbitrary accounts.
- Add indexes for org_id, knowledge_base_id, verification_status, and entry_kind.
- Make downgrade restore the prior schema without deleting legacy rows.

- [ ] **Step 5: Verify migration and commit**

    uv run pytest tests/test_brand_knowledge_models.py -q
    uv run alembic upgrade head
    uv run alembic downgrade 20260811_0100
    uv run alembic upgrade head
    git add backend/app/models/knowledge.py backend/app/models/__init__.py backend/migrations/versions/20260811_0200_brand_knowledge_bases.py backend/tests/test_brand_knowledge_models.py
    git commit -m "feat: add brand knowledge scopes"

---

### Task 6: Add knowledge-base APIs and safe account binding

**Files:**
- Modify: backend/app/schemas/knowledge.py
- Modify: backend/app/services/knowledge_workspace.py
- Modify: backend/app/api/knowledge.py
- Create: backend/tests/test_brand_knowledge_api.py

**Interfaces:**
- Produces: CRUD endpoints for knowledge bases and paginated entries.
- Produces: PUT/GET/DELETE /accounts/{accountId}/knowledge-binding.
- Produces: require_account_knowledge_scope(session, user, account_id, writable).

- [ ] **Step 1: Write failing scope tests**

~~~python
async def test_binding_rejects_cross_org_knowledge_base(client, operator):
    response = await client.put(
        f"/accounts/{account.id}/knowledge-binding",
        json={"knowledge_base_id": foreign_base.id},
        headers=operator.headers,
    )
    assert response.status_code == 404


async def test_unbinding_does_not_delete_historical_citations(client, operator):
    response = await client.delete(
        f"/accounts/{account.id}/knowledge-binding",
        headers=operator.headers,
    )
    assert response.status_code == 204
    assert await citation_count(session) == 1
~~~

- [ ] **Step 2: Run and confirm RED**

    cd backend
    uv run pytest tests/test_brand_knowledge_api.py -q

- [ ] **Step 3: Define typed requests and pagination**

~~~python
class BindAccountKnowledgeRequest(BaseModel):
    knowledge_base_id: int = Field(gt=0)


class KnowledgeEntryPayload(BaseModel):
    schema_version: Literal[1] = 1
    kind: Literal["product_fact", "policy", "case", "brand_voice"]
~~~

List responses include data and pagination. Product-fact payloads use discriminated Pydantic schemas and reject malformed values at the API boundary.

- [ ] **Step 4: Implement authorization and role rules**

- Read: members with account access.
- Write entries: lead, operator, editor.
- Verify/reject facts: lead or reviewer.
- Bind/unbind: lead or admin.

- [ ] **Step 5: Verify and commit**

    uv run pytest tests/test_brand_knowledge_api.py tests/test_brand_knowledge_models.py -q
    uv run ruff check app/schemas/knowledge.py app/services/knowledge_workspace.py app/api/knowledge.py
    git add backend/app/schemas/knowledge.py backend/app/services/knowledge_workspace.py backend/app/api/knowledge.py backend/tests/test_brand_knowledge_api.py
    git commit -m "feat: manage account-bound brand knowledge"

---

### Task 7: Resolve scoped knowledge for Agents and record exact citations

**Files:**
- Modify: backend/app/services/knowledge_workspace.py
- Modify: backend/app/orchestrator/agent_harness.py
- Modify: backend/app/services/agent_workspace.py
- Create: backend/tests/test_account_bound_knowledge_retrieval.py

**Interfaces:**
- Consumes: account binding from Task 6.
- Produces: list_agent_knowledge_for_account(session, org_id, account_id, project_id, limit).
- Produces: evidence records containing entry ID, entry version, source, and claim permission.

- [ ] **Step 1: Write failing precedence and isolation tests**

~~~python
async def test_agent_reads_primary_brand_then_shared_but_not_other_brand(session):
    rows = await list_agent_knowledge_for_account(
        session,
        org_id=org.id,
        account_id=account.id,
        project_id=project.id,
        limit=24,
    )
    assert [row.id for row in rows] == [account_rule.id, brand_fact.id, shared_policy.id]
    assert other_brand_fact.id not in {row.id for row in rows}
~~~

- [ ] **Step 2: Run and confirm RED**

    cd backend
    uv run pytest tests/test_account_bound_knowledge_retrieval.py -q

- [ ] **Step 3: Implement deterministic scope resolution**

Order results as account-specific rules, verified primary-brand entries, verified organization-shared entries. Exclude expired, rejected, draft, and disallowed external claims from product-claim context.

- [ ] **Step 4: Harden prompt boundaries**

Wrap retrieved content as untrusted evidence. Do not concatenate documents into system instructions. Preserve entry ID and version in the model context and citations.

- [ ] **Step 5: Verify and commit**

    uv run pytest tests/test_account_bound_knowledge_retrieval.py tests/test_brand_knowledge_api.py -q
    uv run ruff check app/services/knowledge_workspace.py app/orchestrator/agent_harness.py app/services/agent_workspace.py
    git add backend/app/services/knowledge_workspace.py backend/app/orchestrator/agent_harness.py backend/app/services/agent_workspace.py backend/tests/test_account_bound_knowledge_retrieval.py
    git commit -m "feat: ground agents in account-bound knowledge"

---

### Task 8: Add structured WeChat article, working-copy, slot, and mapping models

**Files:**
- Modify: backend/app/models/enums.py
- Modify: backend/app/models/content.py
- Create: backend/app/models/wechat_article.py
- Modify: backend/app/models/__init__.py
- Create: backend/app/schemas/wechat_article.py
- Create: backend/migrations/versions/20260811_0300_wechat_article_domain.py
- Create: backend/tests/test_wechat_article_models.py

**Interfaces:**
- Produces: ArticleBrief and ArticleDocument schemas.
- Produces: ArticleWorkingCopy, ArticleImageSlot, WechatDraftMapping.
- Produces: DeliverableType.WECHAT_ARTICLE, WECHAT_IMAGE_PLAN, WECHAT_RENDERED_ARTICLE.

- [ ] **Step 1: Write failing document-schema tests**

~~~python
def test_article_document_rejects_raw_html_and_unknown_blocks():
    with pytest.raises(ValidationError):
        ArticleDocument.model_validate({
            "title": "标题",
            "digest": "摘要",
            "blocks": [{"type": "rawHtml", "html": "<script>alert(1)</script>"}],
        })
~~~

- [ ] **Step 2: Run and confirm RED**

    cd backend
    uv run pytest tests/test_wechat_article_models.py -q

- [ ] **Step 3: Define discriminated document blocks**

Supported blocks are heading, paragraph, quote, list, callout, imageSlot, divider, and cta. Every block has a stable block_id. ImageSlot blocks reference a stable slot key, not a URL.

- [ ] **Step 4: Add persistence and constraints**

- One working copy per ContentItem.
- lock_version starts at 1.
- Stable image-slot key is unique within a ContentItem.
- WechatDraftMapping is unique by org_id, account_id, content_item_id.
- Every working copy and slot carries org/account lineage through the owning ContentItem.

- [ ] **Step 5: Verify migration and commit**

    uv run pytest tests/test_wechat_article_models.py -q
    uv run alembic upgrade head
    uv run alembic downgrade 20260811_0200
    uv run alembic upgrade head
    git add backend/app/models backend/app/schemas/wechat_article.py backend/migrations/versions/20260811_0300_wechat_article_domain.py backend/tests/test_wechat_article_models.py
    git commit -m "feat: add structured WeChat article domain"

---

### Task 9: Implement working-copy autosave, immutable versions, and local conflicts

**Files:**
- Create: backend/app/services/wechat_articles.py
- Create: backend/app/api/wechat_articles.py
- Modify: backend/app/main.py
- Create: backend/tests/test_wechat_article_api.py

**Interfaces:**
- Consumes: ArticleDocument and article models from Task 8.
- Produces: create_article, update_working_copy, freeze_article_version, diff_versions.
- Produces: article CRUD, working-copy, version, diff, and preview route skeletons.

- [ ] **Step 1: Write failing optimistic-lock tests**

~~~python
async def test_stale_working_copy_returns_structured_409(client, editor):
    response = await client.patch(
        f"/wechat-articles/{article.id}/working-copy",
        json={"expected_lock_version": 3, "document": changed_document},
        headers=editor.headers,
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "ARTICLE_VERSION_CONFLICT"
    assert response.json()["error"]["details"]["currentLockVersion"] == 4
~~~

- [ ] **Step 2: Run and confirm RED**

    cd backend
    uv run pytest tests/test_wechat_article_api.py -q

- [ ] **Step 3: Implement account-scoped CRUD**

All reads and writes resolve ContentItem, Account, org_id, and user access in one query boundary. A missing or inaccessible object returns 404.

- [ ] **Step 4: Implement version triggers**

Create immutable Deliverables for:

- first AI draft;
- completed whole-article AI rewrite;
- explicit save version;
- pre-sync freeze;
- successful sync snapshot.

Autosave only updates ArticleWorkingCopy and increments lock_version.

- [ ] **Step 5: Implement deterministic diffs**

Return block-level added, removed, moved, and changed results plus a text semantic-change ratio. Do not compute diffs from rendered HTML.

- [ ] **Step 6: Verify and commit**

    uv run pytest tests/test_wechat_article_api.py tests/test_wechat_article_models.py -q
    uv run ruff check app/services/wechat_articles.py app/api/wechat_articles.py app/main.py
    git add backend/app/services/wechat_articles.py backend/app/api/wechat_articles.py backend/app/main.py backend/tests/test_wechat_article_api.py
    git commit -m "feat: version WeChat article working copies"

---

### Task 10: Render safe WeChat HTML and enforce the pre-sync fact gate

**Files:**
- Create: backend/app/services/wechat_renderer.py
- Modify: backend/app/schemas/wechat_article.py
- Modify: backend/app/services/wechat_articles.py
- Create: backend/tests/test_wechat_renderer.py

**Interfaces:**
- Produces: render_wechat_article(document, asset_map) -> RenderedWechatArticle.
- Produces: validate_article_for_sync(...) -> ArticleSyncReadiness.
- Consumes: exact KnowledgeCitation versions from Task 7.

- [ ] **Step 1: Write failing renderer-security tests**

~~~python
def test_renderer_removes_script_handlers_and_external_images():
    rendered = render_wechat_article(document, asset_map={})
    assert "<script" not in rendered.html
    assert "onclick=" not in rendered.html
    assert "https://outside.example/image.png" not in rendered.html
~~~

- [ ] **Step 2: Write failing fact-gate test**

~~~python
async def test_unresolved_product_claim_blocks_sync(session):
    readiness = await validate_article_for_sync(session, version_id=version.id)
    assert readiness.can_sync is False
    assert readiness.blockers[0].code == "UNRESOLVED_PRODUCT_CLAIM"
~~~

- [ ] **Step 3: Run and confirm RED**

    cd backend
    uv run pytest tests/test_wechat_renderer.py -q

- [ ] **Step 4: Implement constrained rendering**

- Apply an explicit element, attribute, style, and URL allowlist.
- Validate title up to 32 Chinese characters, author up to 16, digest up to 120, and content under the current official limits.
- Refuse unresolved image-slot URLs.
- Return normalized_html and content_hash.

- [ ] **Step 5: Implement readiness results**

~~~python
class ArticleSyncReadiness(BaseModel):
    can_sync: bool
    blockers: list[ReadinessIssue]
    warnings: list[ReadinessIssue]
    citation_count: int
    unresolved_claim_count: int
~~~

Quality-review outages add the warning QUALITY_REVIEW_UNAVAILABLE and never create a numeric zero score.

- [ ] **Step 6: Verify and commit**

    uv run pytest tests/test_wechat_renderer.py tests/test_wechat_article_api.py -q
    uv run ruff check app/services/wechat_renderer.py app/services/wechat_articles.py app/schemas/wechat_article.py
    git add backend/app/services/wechat_renderer.py backend/app/services/wechat_articles.py backend/app/schemas/wechat_article.py backend/tests/test_wechat_renderer.py
    git commit -m "feat: render and validate WeChat articles"

---

### Task 11: Add provider-neutral image generation and image-slot selection

**Files:**
- Create: backend/app/services/image_generation.py
- Modify: backend/app/api/wechat_articles.py
- Modify: backend/app/schemas/wechat_article.py
- Create: backend/tests/test_wechat_article_images.py
- Modify: backend/app/models/wechat_article.py

**Interfaces:**
- Produces: ImageGenerationProvider.generate.
- Produces: batch and single-slot generation endpoints.
- Produces: prompt-on-demand, upload, and selection endpoints.
- Consumes: existing MaterialAsset storage.

- [ ] **Step 1: Write failing batch-idempotency tests**

~~~python
async def test_generate_all_skips_ready_and_uploaded_slots(service):
    result = await service.generate_all(article.id, idempotency_key="article-7-images-v1")
    assert result.requested_slot_ids == [planned_slot.id]
    assert ready_slot.id not in result.requested_slot_ids
    assert uploaded_slot.id not in result.requested_slot_ids
~~~

- [ ] **Step 2: Run and confirm RED**

    cd backend
    uv run pytest tests/test_wechat_article_images.py -q

- [ ] **Step 3: Define the provider interface**

~~~python
class ImageGenerationProvider(Protocol):
    async def generate(
        self,
        *,
        prompt: str,
        aspect_ratio: str,
        reference_material_ids: tuple[int, ...],
        idempotency_key: str,
    ) -> ImageGenerationResult: ...
~~~

- [ ] **Step 4: Implement slot transitions**

Allowed transitions:

    planned → generating → ready → selected
    generating → failed
    failed → generating
    selected → ready when replaced

Uploading a user image validates MIME, decoded image dimensions, size, org/account scope, and creates a MaterialAsset.

- [ ] **Step 5: Enforce prompt disclosure**

Article responses expose hasPrompt but not prompt. GET prompt checks article access and records a non-sensitive audit event.

- [ ] **Step 6: Verify and commit**

    uv run pytest tests/test_wechat_article_images.py tests/test_wechat_article_api.py -q
    uv run ruff check app/services/image_generation.py app/api/wechat_articles.py app/schemas/wechat_article.py app/models/wechat_article.py
    git add backend/app/services/image_generation.py backend/app/api/wechat_articles.py backend/app/schemas/wechat_article.py backend/app/models/wechat_article.py backend/tests/test_wechat_article_images.py
    git commit -m "feat: generate and select WeChat article images"

---

### Task 12: Implement the typed WeChat draft client and remote-hash normalization

**Files:**
- Create: backend/app/services/wechat_drafts.py
- Create: backend/tests/test_wechat_draft_client.py
- Modify: backend/app/schemas/wechat_article.py

**Interfaces:**
- Consumes: authorizer token from Task 2 and rendered article from Task 10.
- Produces: upload_article_image, add_permanent_cover, get_draft, add_draft, update_draft.
- Produces: normalize_remote_draft and compute_remote_hash.

- [ ] **Step 1: Write failing external-response tests**

~~~python
async def test_draft_add_validates_media_id(mock_transport):
    mock_transport.respond({"errcode": 0})
    with pytest.raises(WechatIntegrationError, match="missing media_id"):
        await client.add_draft(package)
~~~

- [ ] **Step 2: Write failing remote-hash test**

~~~python
def test_remote_hash_ignores_attribute_order_but_not_content_changes():
    assert compute_remote_hash(remote_a) == compute_remote_hash(remote_a_reordered)
    assert compute_remote_hash(remote_a) != compute_remote_hash(remote_b)
~~~

- [ ] **Step 3: Run and confirm RED**

    cd backend
    uv run pytest tests/test_wechat_draft_client.py -q

- [ ] **Step 4: Implement typed server-side calls**

- Upload body images before rendering final content URLs.
- Upload the selected cover as permanent material.
- Validate every response and preserve errcode, errmsg, rid, endpoint, retryability.
- Never send raw system object-storage URLs as WeChat body images.

- [ ] **Step 5: Implement canonical remote normalization**

Normalize title, author, digest, sanitized content, cover media ID, comment flags, and content-source URL. Remove insignificant whitespace and attribute ordering only.

- [ ] **Step 6: Verify and commit**

    uv run pytest tests/test_wechat_draft_client.py -q
    uv run ruff check app/services/wechat_drafts.py app/schemas/wechat_article.py tests/test_wechat_draft_client.py
    git add backend/app/services/wechat_drafts.py backend/app/schemas/wechat_article.py backend/tests/test_wechat_draft_client.py
    git commit -m "feat: add typed WeChat draft client"

---

### Task 13: Add durable, idempotent draft-sync jobs and remote conflict handling

**Files:**
- Modify: backend/app/models/publishing.py
- Modify: backend/app/schemas/publishing.py
- Modify: backend/app/services/publishing.py
- Modify: backend/app/api/wechat_articles.py
- Create: backend/tests/test_wechat_draft_sync.py
- Create: backend/migrations/versions/20260811_0400_wechat_draft_sync_jobs.py

**Interfaces:**
- Consumes: readiness gate from Task 10 and draft client from Task 12.
- Produces: POST /wechat-articles/{articleId}/draft-syncs.
- Produces: GET /wechat-draft-syncs/{syncId}.
- Extends: PlatformPublishJob with operation_type draft_sync and WeChat-specific terminal states without breaking Douyin jobs.

- [ ] **Step 1: Write failing idempotency test**

~~~python
async def test_same_idempotency_key_returns_same_sync_job(client, operator):
    first = await create_sync(client, operator, key="sync-article-9-v3")
    second = await create_sync(client, operator, key="sync-article-9-v3")
    assert first["id"] == second["id"]
    assert wechat_mock.draft_add_calls == 1
~~~

- [ ] **Step 2: Write failing remote-conflict test**

~~~python
async def test_remote_change_blocks_default_update(client, operator):
    response = await create_sync(
        client,
        operator,
        expected_remote_hash="old-hash",
        conflict_strategy="fail",
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "WECHAT_DRAFT_CONFLICT"
~~~

- [ ] **Step 3: Run and confirm RED**

    cd backend
    uv run pytest tests/test_wechat_draft_sync.py -q

- [ ] **Step 4: Add backward-compatible job fields and states**

Add operation_type, external_media_id, article_version_id, expected_remote_hash, observed_remote_hash, and request_digest. Keep current Douyin fields and API behavior unchanged.

- [ ] **Step 5: Implement the sync transaction**

1. require account and article scope;
2. require explicit approval snapshot;
3. freeze or verify immutable version;
4. validate readiness;
5. compare request digest for reused idempotency key;
6. upload selected assets;
7. read and compare remote draft when updating;
8. add, update, or create a new remote draft according to strategy;
9. persist media_id, remote hash, capability snapshot, and status;
10. emit WorkTurn progress and completion.

Only retry transport errors, rate limits with a retry hint, and 5xx responses. Business conflicts, permissions, validation, and missing facts are non-retryable.

- [ ] **Step 6: Verify migration, regression, and commit**

    uv run pytest tests/test_wechat_draft_sync.py tests/test_content_publishing_skill.py tests/test_wechat_draft_client.py -q
    uv run alembic upgrade head
    uv run ruff check app/models/publishing.py app/schemas/publishing.py app/services/publishing.py app/api/wechat_articles.py
    git add backend/app/models/publishing.py backend/app/schemas/publishing.py backend/app/services/publishing.py backend/app/api/wechat_articles.py backend/tests/test_wechat_draft_sync.py backend/migrations/versions/20260811_0400_wechat_draft_sync_jobs.py
    git commit -m "feat: sync immutable articles to WeChat drafts"

---

### Task 14: Register and execute the WeChat article-production Skill

**Files:**
- Create: backend/app/orchestrator/skills/wechat_article_production.py
- Modify: backend/app/orchestrator/skills/registry.py
- Modify: backend/app/orchestrator/skills/public_catalog.py
- Modify: backend/app/orchestrator/capability_router.py
- Modify: backend/app/orchestrator/skill_runtime.py
- Create: backend/tests/test_wechat_article_skill.py

**Interfaces:**
- Produces: WECHAT_ARTICLE_PRODUCTION_SKILL version 1.
- Produces: deterministic route for WeChat article requests.
- Consumes: scoped knowledge, article service, renderer, image slots, and draft-sync readiness.

- [ ] **Step 1: Write failing Skill contract test**

~~~python
def test_wechat_article_skill_is_public_only_for_wechat_accounts():
    skill = skill_registry.get("wechat_article_production")
    assert skill.supported_platforms == frozenset({"wechat_official_account"})
    assert skill.approval_policy == "explicit_before_external_write"
    assert skill.expert_codes == (
        "02-content-director",
        "05-editor",
        "03-art-director",
    )
~~~

- [ ] **Step 2: Write failing runtime test**

~~~python
async def test_skill_waits_for_missing_primary_cta_without_creating_duplicate_turns(runtime):
    result = await runtime.execute(request_with_missing_cta)
    assert result.status == "waiting_user"
    assert result.interrupt["required_fields"] == ["primary_cta"]
    assert await count_work_turns(session) == 1
~~~

- [ ] **Step 3: Run and confirm RED**

    cd backend
    uv run pytest tests/test_wechat_article_skill.py -q

- [ ] **Step 4: Define input and output contracts**

The input includes ArticleBrief, account/thread/turn/run lineage, working-copy ID, and requested action. The output includes article ID, current version, image-slot summary, citation summary, readiness, and explicit user decisions.

- [ ] **Step 5: Implement bounded stages**

    brief_resolution
      → scoped_knowledge
      → content_strategy
      → article_editing
      → visual_planning
      → compliance_and_fact_gate
      → render_preview
      → waiting_user

Main Agent commentary reflects these business stages. Expert and Tool details remain folded.

- [ ] **Step 6: Enforce authority boundaries**

- Main Agent does not create final article text itself.
- Image generation runs only after an explicit user action.
- Draft sync runs only after explicit sync confirmation.
- Quality-review unavailable remains a named state without numeric score.

- [ ] **Step 7: Run backend regression and commit**

    uv run pytest tests/test_wechat_article_skill.py tests/test_main_agent_worker_contract.py tests/test_main_agent_v3_integration.py -q
    uv run ruff check app/orchestrator/skills app/orchestrator/capability_router.py app/orchestrator/skill_runtime.py
    git add backend/app/orchestrator backend/tests/test_wechat_article_skill.py
    git commit -m "feat: orchestrate WeChat article production"

---

### Task 15: Add frontend authorization, capability, and brand-binding clients

**Files:**
- Create: frontend/src/types/wechatArticle.ts
- Create: frontend/src/services/wechatIntegration.ts
- Create: frontend/src/services/wechatIntegration.test.ts
- Modify: frontend/src/pages/Accounts.tsx
- Modify: frontend/src/pages/Knowledge.tsx
- Modify: frontend/src/pages/Accounts.test.tsx
- Modify: frontend/src/pages/Knowledge.test.tsx

**Interfaces:**
- Consumes: authorization, capability, knowledge-base, and binding endpoints.
- Produces: authorize-WeChat action, capability status, and one-primary-brand binding UI.

- [ ] **Step 1: Write failing service tests**

~~~typescript
it("creates a WeChat authorization session without exposing secrets", async () => {
  const result = await createWechatAuthorizationSession({ knowledgeBaseId: 12 });
  expect(result.authorizationUrl).toContain("pre_auth_code=");
  expect(JSON.stringify(result)).not.toContain("access_token");
});
~~~

- [ ] **Step 2: Write failing UI tests**

Verify:

- “授权微信公众号” opens the official URL;
- capability rows show available, missing permission, or account qualification;
- freepublish displays “首版未开启” even if granted;
- binding a second primary brand requires replacing the current binding explicitly.

- [ ] **Step 3: Run and confirm RED**

    cd frontend
    npm test -- --run src/services/wechatIntegration.test.ts src/pages/Accounts.test.tsx src/pages/Knowledge.test.tsx

- [ ] **Step 4: Implement typed services and UI**

Do not add WeChat secrets to frontend types, query cache, localStorage, or error telemetry. Capability copy uses server reason codes mapped to actionable Chinese text.

- [ ] **Step 5: Verify and commit**

    npm test -- --run src/services/wechatIntegration.test.ts src/pages/Accounts.test.tsx src/pages/Knowledge.test.tsx
    npm run lint
    npm run build
    git add frontend/src/types/wechatArticle.ts frontend/src/services/wechatIntegration.ts frontend/src/services/wechatIntegration.test.ts frontend/src/pages/Accounts.tsx frontend/src/pages/Accounts.test.tsx frontend/src/pages/Knowledge.tsx frontend/src/pages/Knowledge.test.tsx
    git commit -m "feat: authorize and bind WeChat accounts"

---

### Task 16: Build the article workspace, image interactions, and conflict surfaces

**Files:**
- Create: frontend/src/services/wechatArticle.ts
- Create: frontend/src/services/wechatArticle.test.ts
- Create: frontend/src/pages/WechatArticleWorkspace.tsx
- Create: frontend/src/components/wechat-article/ArticleEditor.tsx
- Create: frontend/src/components/wechat-article/ArticleImageSlot.tsx
- Create: frontend/src/components/wechat-article/ArticleVersionConflict.tsx
- Create: frontend/src/components/wechat-article/WechatSyncConfirmation.tsx
- Create: frontend/src/pages/WechatArticleWorkspace.test.tsx

**Interfaces:**
- Consumes: article, version, image, preview, and sync APIs.
- Produces: edit/preview/version views with autosave, image-slot actions, and explicit sync confirmation.

- [ ] **Step 1: Write failing user-flow tests**

~~~typescript
it("hides prompts until requested and can generate all pending images", async () => {
  render(<WechatArticleWorkspace articleId={9} />);
  expect(screen.queryByText(/prompt/i)).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "一键生成全部配图" }));
  expect(generateAllImages).toHaveBeenCalledWith(9, expect.any(String));
  await user.click(screen.getAllByRole("button", { name: "获取提示词" })[0]);
  expect(await screen.findByText("画面提示词")).toBeVisible();
});
~~~

- [ ] **Step 2: Write failing local-conflict test**

Verify HTTP 409 renders latest editor, “查看差异”, “基于新版本继续修改”, and “放弃本地修改”; no automatic retry sends an overwrite.

- [ ] **Step 3: Run and confirm RED**

    cd frontend
    npm test -- --run src/services/wechatArticle.test.ts src/pages/WechatArticleWorkspace.test.tsx

- [ ] **Step 4: Implement the service and workspace**

- Debounce autosave by 2 seconds.
- Send expectedLockVersion on every save.
- Keep editor, preview, and versions in one workspace.
- Preserve stable image slots across document refreshes.
- Display exact save state: “正在保存”, “已保存”, “存在新版本”.

- [ ] **Step 5: Implement concrete sync confirmation**

Display target account, title, immutable version, image count, unresolved facts, last remote state, and operation type. The final button is “确认同步到公众号「名称」草稿箱”.

- [ ] **Step 6: Verify accessibility and build**

    npm test -- --run src/services/wechatArticle.test.ts src/pages/WechatArticleWorkspace.test.tsx
    npm run lint
    npm run build

Check keyboard access, focus return after dialogs, visible focus, labels, status announcements, and 390-pixel layout.

- [ ] **Step 7: Commit**

    git add frontend/src/services/wechatArticle.ts frontend/src/services/wechatArticle.test.ts frontend/src/pages/WechatArticleWorkspace.tsx frontend/src/pages/WechatArticleWorkspace.test.tsx frontend/src/components/wechat-article
    git commit -m "feat: add WeChat article workspace"

---

### Task 17: Integrate the workspace with the main Agent WorkTurn and real-time recovery

**Files:**
- Modify: frontend/src/pages/BrainHome.tsx
- Modify: frontend/src/components/brain/WorkTurnCard.tsx
- Modify: frontend/src/components/brain/WorkTurnProgress.tsx
- Modify: frontend/src/types.ts
- Modify: frontend/src/pages/BrainHome.test.tsx
- Modify: frontend/src/components/brain/WorkTurnCard.test.tsx
- Create: frontend/e2e/wechat-article-flow.spec.ts

**Interfaces:**
- Consumes: Skill output and article workspace from Tasks 14 and 16.
- Produces: one WorkTurn that progresses from goal to article, image plan, readiness, and draft sync without page refresh.

- [ ] **Step 1: Write failing WorkTurn projection tests**

Verify one user message creates one assistant WorkTurn, “文章初稿已生成” links to the article workspace, and completion replaces the running body in place rather than appending a second Agent message.

- [ ] **Step 2: Write failing recovery test**

Refresh during image generation and during draft sync. The same turn_id, article_id, generation job, and sync job must be restored.

- [ ] **Step 3: Run and confirm RED**

    cd frontend
    npm test -- --run src/pages/BrainHome.test.tsx src/components/brain/WorkTurnCard.test.tsx

- [ ] **Step 4: Implement the semantic projection**

Visible stages:

- 正在确认文章目标
- 正在读取品牌知识
- 正在生成文章初稿
- 已规划配图位置
- 正在生成所选图片
- 正在检查公众号格式
- 等待你确认同步
- 正在同步微信草稿
- 微信草稿已同步

Technical traces remain collapsed. Preserve the existing top-right controls and current thinking-orb behavior.

- [ ] **Step 5: Add E2E mock flow**

The Playwright test covers article creation, generate-all, prompt reveal, version conflict, preview, confirmation, sync, refresh recovery, and account switching without cross-account remnants.

- [ ] **Step 6: Run frontend gates**

    npm test
    npm run lint
    npm run build
    npm run check:main-agent-bundle
    npm run test:e2e -- wechat-article-flow.spec.ts

- [ ] **Step 7: Commit**

    git add frontend/src/pages/BrainHome.tsx frontend/src/components/brain frontend/src/types.ts frontend/src/pages/BrainHome.test.tsx frontend/e2e/wechat-article-flow.spec.ts
    git commit -m "feat: surface WeChat production in main agent"

---

### Task 18: Add product metrics, production runbook, security verification, and real smoke test

**Files:**
- Modify: backend/app/services/wechat_component.py
- Modify: backend/app/services/wechat_drafts.py
- Modify: backend/app/services/wechat_articles.py
- Create: backend/tests/test_wechat_observability.py
- Create: docs/runbooks/wechat-official-account-rollout.md
- Modify: frontend/e2e/wechat-article-flow.spec.ts

**Interfaces:**
- Consumes: all prior slices.
- Produces: operational metrics, alerts, rollout/rollback steps, and final release evidence.

- [ ] **Step 1: Write failing observability tests**

~~~python
async def test_sync_logs_endpoint_errcode_rid_latency_without_tokens(caplog):
    await sync_service.sync(job.id)
    text = caplog.text
    assert "draft/add" in text
    assert "rid-123" in text
    assert "authorizer-token" not in text
    assert "refresh-token" not in text
~~~

- [ ] **Step 2: Run the focused test and confirm RED**

    cd backend
    uv run pytest tests/test_wechat_observability.py -q

Expected: failure because the WeChat services do not yet emit the required structured, redacted events.

- [ ] **Step 3: Add product events**

Record:

- wechat.authorization.started/completed/failed/revoked;
- wechat.capabilities.checked;
- wechat.article.created/initial_draft_ready/version_saved;
- wechat.images.generate_all_requested/image_selected;
- wechat.draft.sync_requested/conflicted/completed/failed;
- article key-interaction count;
- initial-to-sync semantic change ratio.

- [ ] **Step 4: Add production alerts**

- component ticket older than 20 minutes;
- repeated component or authorizer token refresh failure;
- five-minute draft-sync failure rate above 5%;
- reused idempotency key with a different request digest;
- any scope mismatch or cross-org denial anomaly.

- [ ] **Step 5: Write the runbook**

Include:

- third-party platform configuration;
- callback URL, Token, and EncodingAESKey setup;
- secret-reference names without values;
- permission request and capability-check procedure;
- database migration and worker restart order;
- feature flag rollout;
- rollback that disables new authorization and draft sync without deleting tokens or mappings;
- test-account authorization and revocation;
- evidence collection;
- explicit prohibition on freepublish_submit.

- [ ] **Step 6: Run all automated gates**

Backend:

    cd backend
    uv run pytest
    uv run ruff check app tests
    uv run ruff format --check app tests
    uv run mypy app

Frontend:

    cd frontend
    npm test
    npm run lint
    npm run build
    npm run check:main-agent-bundle
    npm run test:e2e

- [ ] **Step 7: Perform security review**

Search staged changes and runtime logs for component_appsecret, component_verify_ticket, authorizer_access_token, authorizer_refresh_token, EncodingAESKey, access_token query strings, and private keys. Verify all public callback payloads are untrusted, validated, and idempotent.

- [ ] **Step 8: Request explicit real-account approval**

Do not perform the next step without user approval naming the test organization and公众号.

- [ ] **Step 9: Run the controlled real smoke test**

1. authorize the test公众号;
2. confirm capabilities;
3. bind a verified test brand knowledge base;
4. create one article with one cover and two body images;
5. freeze the reviewed version;
6. confirm and sync to draft;
7. inspect the WeChat后台 draft;
8. repeat the request and confirm no duplicate draft;
9. modify remote draft and confirm conflict behavior;
10. confirm no publish API call occurred.

- [ ] **Step 10: Commit**

    git add backend/app/services/wechat_component.py backend/app/services/wechat_drafts.py backend/app/services/wechat_articles.py backend/tests/test_wechat_observability.py docs/runbooks/wechat-official-account-rollout.md frontend/e2e/wechat-article-flow.spec.ts
    git commit -m "docs: add WeChat rollout and production evidence"

---

## 3. Verification Checkpoints

### Checkpoint A: Authorization foundation

Tasks 1–4 are complete when a mock公众号 can authorize, refresh tokens, expose capability reasons, and revoke authorization with no secret leakage.

### Checkpoint B: Knowledge isolation

Tasks 5–7 are complete when two accounts bound to different brands cannot retrieve each other’s knowledge and every accepted product claim records an exact entry/version citation.

### Checkpoint C: Article workspace backend

Tasks 8–11 are complete when an article can be created, autosaved, versioned, rendered, image-planned, image-generated, and blocked from sync for unresolved facts.

### Checkpoint D: Draft sync

Tasks 12–13 are complete when mock WeChat add/update operations are idempotent and both local and remote conflicts are explicit non-retryable states.

### Checkpoint E: Main Agent UX

Tasks 14–17 are complete when the entire mock user journey runs inside one recoverable WorkTurn with no abstract acceptance language and no duplicate UI.

### Checkpoint F: Production readiness

Task 18 is complete when all automated gates pass, the runbook is reviewed, and a separately approved real-account smoke test has evidence and rollback instructions.

## 4. Plan Self-Review

### Spec coverage

- Third-party authorization: Tasks 1–4.
- Brand knowledge and binding: Tasks 5–7.
- Structured brief, document, versions, and local conflicts: Tasks 8–10.
- Image slots, one-click generation, prompt-on-demand, uploads: Task 11.
- WeChat images, covers, remote conflicts, and idempotent sync: Tasks 12–13.
- Main Agent Skill and authority boundary: Task 14.
- Account, knowledge, article, and WorkTurn UX: Tasks 15–17.
- Metrics, security, rollout, and real evidence: Task 18.
- Automatic publishing and automatic review remain out of scope in every slice.

### Type consistency

- Platform value is wechat_official_account everywhere.
- Per-account external ID is authorizer_appid.
- Article version is an immutable Deliverable.
- ArticleWorkingCopy uses lock_version and expectedLockVersion.
- Remote WeChat consistency uses remote_hash and expectedRemoteHash.
- Every external write uses idempotency_key.
- The public Skill code is wechat_article_production.

### Scope discipline

- No task invokes freepublish_submit.
- No task adds multi-article, repost, paid-reading, ad, or automatic analytics workflows.
- Real WeChat writes occur only at the final, separately approved smoke-test step.
- Existing Douyin authorization and publishing behavior receives regression tests before shared model changes are accepted.
