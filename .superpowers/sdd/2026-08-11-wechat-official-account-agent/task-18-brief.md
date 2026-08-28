# Task 18 Brief - Product metrics, rollout, security, and smoke gate

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
