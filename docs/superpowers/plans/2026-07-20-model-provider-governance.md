# Model Provider Governance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let organization administrators securely configure, verify, route, rotate, and retire multiple model providers from the frontend, including built-in templates and custom OpenAI-compatible endpoints.

**Architecture:** Replace the hardcoded DeepSeek/LiteLLM presentation layer with an organization-scoped provider registry while preserving the existing `ModelConfig`, `LLMGateway`, call ledger, and server-managed DeepSeek credential path. Provider secrets remain write-only and encrypted at rest. A single OpenAI-compatible adapter handles built-in and custom providers; provider-specific behavior is data-driven by trusted templates rather than Agent code.

**Tech Stack:** FastAPI, Pydantic v2, SQLAlchemy async, Alembic, httpx, Fernet credential encryption, React 18, TypeScript, TanStack Query, Ant Design, Vitest, pytest.

## Global Constraints

- Desktop only; no mobile layout work in this phase.
- Every provider, secret, model catalog, route, and call lookup is scoped by `org_id`.
- API keys are accepted only in write requests and are never returned by APIs, logs, events, validation errors, browser storage, URLs, or query keys.
- Custom endpoints must use HTTPS, contain no URL credentials, reject redirects, and reject loopback, private, link-local, reserved, multicast, unspecified, and cloud metadata addresses after DNS resolution.
- Unverified providers cannot be selected for a new primary or fallback route.
- A provider referenced by an Agent route cannot be deleted until its routes are migrated.
- Existing `env:DEEPSEEK_API_KEY` remains a read-only server credential source and can be replaced by an organization-level encrypted key.
- Local tests and desktop acceptance must pass before production deployment.

---

### Task 1: Persist the organization provider registry and route references

**Files:**
- Create: `backend/migrations/versions/20260720_0400_model_provider_registry.py`
- Modify: `backend/app/models/configuration.py`
- Modify: `backend/app/models/__init__.py`
- Modify: `backend/app/schemas/configuration.py`
- Test: `backend/tests/test_model_provider_models.py`

**Interfaces:**
- Produces: `ModelProvider` and nullable `ModelConfig.primary_provider_id` / `fallback_provider_id`.
- Preserves: existing model names and existing organization routes.

- [x] **Step 1: Write failing persistence tests**

```python
async def test_model_provider_is_unique_per_org(session, org, admin):
    provider = ModelProvider(
        org_id=org.id,
        code="deepseek",
        display_name="DeepSeek",
        provider_type="preset",
        protocol="openai_compatible",
        base_url="https://api.deepseek.com",
        credential_source="environment",
        created_by_id=admin.id,
        updated_by_id=admin.id,
    )
    session.add(provider)
    await session.commit()
    assert provider.verification_status == "pending"
```

Also test the organization/code uniqueness constraint and the route foreign keys.

- [x] **Step 2: Run the focused test and verify it fails**

Run: `cd backend && python -m pytest tests/test_model_provider_models.py -q`

Expected: FAIL because `ModelProvider` and provider route references do not exist.

- [x] **Step 3: Add the migration and ORM model**

Use `down_revision = "20260720_0300"` because the identity-governance chain now
occupies revisions `0100` through `0300`. The provider table must contain:

```python
class ModelProvider(Base, TimestampMixin):
    id: Mapped[int]
    org_id: Mapped[int]
    code: Mapped[str]
    display_name: Mapped[str]
    provider_type: Mapped[str]          # preset | custom_openai
    template_code: Mapped[str | None]
    protocol: Mapped[str]               # openai_compatible | legacy_litellm
    base_url: Mapped[str | None]
    enabled: Mapped[bool]
    sort_order: Mapped[int]
    credential_source: Mapped[str]      # none | encrypted | environment
    encrypted_api_key: Mapped[str | None]
    key_last_four: Mapped[str | None]
    key_fingerprint: Mapped[str | None]
    verification_status: Mapped[str]    # pending | verified | error
    verified_at: Mapped[datetime | None]
    verification_error_code: Mapped[str | None]
    models: Mapped[list[str] | None]
    models_updated_at: Mapped[datetime | None]
    created_by_id: Mapped[int | None]
    updated_by_id: Mapped[int | None]
```

Apply unique constraints on `(org_id, code)` and `(org_id, id)`. Add composite,
restricted foreign keys from `model_configs (org_id, provider_id)` to providers
so provider deletion cannot orphan routes and a route cannot reference another
organization's provider. Actor attribution uses `SET NULL` so system backfills
and later permanent member deletion preserve the organization provider.

- [x] **Step 4: Backfill compatibility providers and routes**

For every existing organization, including historical organizations with no
remaining users, create a `deepseek` provider using
`credential_source="environment"`. Create a disabled `legacy-litellm` provider
only when an existing route contains a `litellm:` model. Backfill provider IDs
without rewriting model names.

- [x] **Step 5: Run migration and model tests**

Run: `cd backend && python -m pytest tests/test_model_provider_models.py -q`

Expected: PASS.

Run: `cd backend && python -m alembic upgrade head`

Expected: schema upgrades to `20260720_0400`; existing model routes still resolve.

- [x] **Step 6: Commit the registry persistence increment**

```bash
git add backend/migrations/versions/20260720_0400_model_provider_registry.py backend/app/models backend/app/schemas/configuration.py backend/tests/test_model_provider_models.py
git commit -m "feat: persist organization model providers"
```

### Task 2: Build provider templates, secret lifecycle, and safe endpoint validation

**Files:**
- Create: `backend/app/services/model_provider_registry.py`
- Create: `backend/app/core/outbound_url.py`
- Modify: `backend/app/core/credential_crypto.py`
- Test: `backend/tests/test_model_provider_registry.py`
- Test: `backend/tests/test_outbound_url_security.py`
- Test: `backend/tests/test_credential_crypto.py`

**Interfaces:**
- Produces built-in templates: `deepseek`, `openai`, `qwen`, `doubao`, `zhipu`, and `moonshot`.
- Produces: `validate_public_https_url`, `encrypt_provider_key`, `decrypt_provider_key`, and `provider_public_row`.

- [x] **Step 1: Write failing security and secret tests**

Cover HTTPS enforcement, URL credentials, IP literals, localhost, RFC1918, IPv6 local ranges, metadata IPs, DNS resolving to private addresses, redirect rejection, key encryption round-trip, key tail, keyed fingerprint, and response serialization without ciphertext.

```python
@pytest.mark.parametrize("url", [
    "http://api.example.com/v1",
    "https://127.0.0.1/v1",
    "https://169.254.169.254/latest/meta-data",
    "https://user:pass@api.example.com/v1",
])
async def test_rejects_unsafe_provider_urls(url):
    with pytest.raises(UnsafeOutboundURLError):
        await validate_public_https_url(url)
```

- [x] **Step 2: Run focused tests and verify missing helpers fail**

Run: `cd backend && python -m pytest tests/test_model_provider_registry.py tests/test_outbound_url_security.py tests/test_credential_crypto.py -q`

Expected: FAIL because registry and outbound validation do not exist.

- [x] **Step 3: Implement immutable provider templates**

Templates provide display name, trusted default Base URL, OpenAI-compatible protocol metadata, and common model names; they contain no credentials. Custom provider codes are normalized to lower-case slugs and are unique only within the current organization.

- [x] **Step 4: Implement write-only encrypted credentials**

Use the existing Fernet encryption boundary. Store only ciphertext, last four characters, and a domain-separated keyed fingerprint. Updating a key resets verification to `pending`; deleting a key clears all key metadata and resets verification. Public serializers must not include `encrypted_api_key` or any field capable of reconstructing it.

- [x] **Step 5: Implement outbound URL validation**

Parse with a structured URL parser. Require `https`, reject user info and fragments, resolve every A/AAAA result immediately before outbound access, and reject any non-global address. Disable redirects and cap connection, read, total time, and response size. Revalidate custom endpoints before every verify, discovery, and runtime request.

- [x] **Step 6: Run service security tests**

Run: `cd backend && python -m pytest tests/test_model_provider_registry.py tests/test_outbound_url_security.py tests/test_credential_crypto.py -q`

Expected: PASS; tests assert no plaintext key appears in serialized rows or errors.

- [x] **Step 7: Commit the provider security boundary**

```bash
git add backend/app/services/model_provider_registry.py backend/app/core/outbound_url.py backend/app/core/credential_crypto.py backend/tests
git commit -m "feat: secure model provider credentials"
```

### Task 3: Expose provider CRUD, verification, and model discovery APIs

**Files:**
- Create: `backend/app/api/model_providers.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/schemas/configuration.py`
- Modify: `backend/app/services/model_provider_registry.py`
- Test: `backend/tests/test_model_providers_api.py`

**Interfaces:**
- Produces API:
  - `GET /model-providers/templates`
  - `GET /model-providers`
  - `POST /model-providers`
  - `GET /model-providers/{id}`
  - `PATCH /model-providers/{id}`
  - `PUT /model-providers/{id}/credential`
  - `DELETE /model-providers/{id}/credential`
  - `POST /model-providers/{id}/verify`
  - `POST /model-providers/{id}/discover-models`
  - `PUT /model-providers/{id}/models`
  - `DELETE /model-providers/{id}`

- [x] **Step 1: Write failing API and tenant-isolation tests**

Test admin-only access, cross-organization `404`, write-only API key responses, duplicate code, invalid URL, verify status mapping, model discovery fallback, credential rotation, referenced-provider deletion conflict, and sanitized audit events.

```python
async def test_provider_response_never_returns_api_key(client, admin_token):
    response = await client.put(
        "/model-providers/1/credential",
        headers=_auth(admin_token),
        json={"api_key": "sk-sensitive-value"},
    )
    assert response.status_code == 200
    assert "sk-sensitive-value" not in response.text
    assert "encrypted_api_key" not in response.text
```

- [x] **Step 2: Run API tests and verify endpoints are absent**

Run: `cd backend && python -m pytest tests/test_model_providers_api.py -q`

Expected: FAIL with `404` responses.

- [x] **Step 3: Implement strict request and public response contracts**

Use bounded strings and lists. A provider response exposes status, endpoint, key configured flag, key tail, fingerprint prefix, model catalog, timestamps, and route references; it never exposes a secret or ciphertext. Map upstream errors to stable codes: `authentication_failed`, `endpoint_unreachable`, `protocol_incompatible`, `timeout`, and `model_unavailable`.

- [x] **Step 4: Implement verification and discovery**

Verification performs a minimal compatible request and records latency plus sanitized status. Discovery calls `/models` with redirects disabled and a bounded response. When discovery is unsupported, return a stable `discovery_unsupported` result without erasing manually maintained models.

- [x] **Step 5: Protect destructive actions and write audits**

Deleting a provider returns `409` with affected Agent names when routes reference it. Audit create, update, enable/disable, key set/rotate/delete, verify, model update, and delete using IDs and safe metadata only.

- [x] **Step 6: Run provider API tests**

Run: `cd backend && python -m pytest tests/test_model_providers_api.py -q`

Expected: PASS.

- [x] **Step 7: Commit provider APIs**

```bash
git add backend/app/api/model_providers.py backend/app/main.py backend/app/schemas/configuration.py backend/app/services/model_provider_registry.py backend/tests/test_model_providers_api.py
git commit -m "feat: add model provider administration APIs"
```

### Task 4: Route the LLM gateway through the provider registry

**Files:**
- Create: `backend/app/llm/adapters/openai_compatible.py`
- Modify: `backend/app/llm/adapters/deepseek.py`
- Modify: `backend/app/llm/gateway.py`
- Modify: `backend/app/services/model_infrastructure.py`
- Modify: `backend/app/api/model_configs.py`
- Modify: `backend/app/schemas/configuration.py`
- Test: `backend/tests/test_llm_gateway.py`
- Test: `backend/tests/test_model_configs_api.py`

**Interfaces:**
- Produces registry-backed runtime resolution by `(org_id, provider_id, model_name)`.
- Preserves existing `LLMGateway.chat()` and `chat_stream()` caller signatures.

- [ ] **Step 1: Write failing gateway and route tests**

Prove that two organizations using the same provider code decrypt different keys, unverified providers cannot be assigned, verified custom providers work for complete and stream calls, disabled/error providers fail visibly, environment DeepSeek remains compatible, and fallback routes use their own provider.

- [ ] **Step 2: Run focused tests and verify hardcoded routing fails**

Run: `cd backend && python -m pytest tests/test_llm_gateway.py tests/test_model_configs_api.py -q`

Expected: FAIL because `provider_code_for_model()` still hardcodes DeepSeek/LiteLLM.

- [ ] **Step 3: Implement the generic OpenAI-compatible adapter**

Extract the existing DeepSeek HTTP behavior into `OpenAICompatibleAdapter`. It receives the already validated provider runtime, never reads arbitrary environment names from database input, disables redirects, uses bounded timeouts, and supports complete plus streaming responses. Keep `DeepSeekAdapter` as a compatibility wrapper until all imports migrate.

- [ ] **Step 4: Resolve routes structurally**

Return a structured candidate from route resolution:

```python
@dataclass(frozen=True)
class ModelTarget:
    provider_id: int
    provider_code: str
    model: str
```

Remove model-name inference for new routes. Legacy rows are handled only by migration/backward-compatibility resolution. Record actual provider code and model in `LLMCall`.

- [ ] **Step 5: Update route validation APIs**

`PUT /model-infrastructure/routes/{agent_code}` accepts primary and fallback provider IDs plus model names. Both providers must belong to the current organization, be enabled and verified, and list the selected model. Preserve existing route IDs and routing parameters.

- [ ] **Step 6: Run gateway and route tests**

Run: `cd backend && python -m pytest tests/test_llm_gateway.py tests/test_model_configs_api.py -q`

Expected: PASS with DeepSeek environment compatibility and organization isolation.

- [ ] **Step 7: Commit runtime integration**

```bash
git add backend/app/llm backend/app/services/model_infrastructure.py backend/app/api/model_configs.py backend/app/schemas/configuration.py backend/tests
git commit -m "feat: route agents through model providers"
```

### Task 5: Extend frontend provider and route contracts

**Files:**
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/api/modelInfrastructure.ts`
- Modify: `frontend/src/api/modelInfrastructure.test.ts`

**Interfaces:**
- Produces typed provider template, provider detail, credential, verification, model discovery, and route target APIs.

- [ ] **Step 1: Write failing API client tests**

Tests cover provider CRUD, credential body submission, model discovery, and referenced-provider deletion error parsing.

```typescript
it("submits API keys only in a write body", async () => {
  await replaceModelProviderCredential(7, "sk-sensitive");
  expect(apiPut).toHaveBeenCalledWith(
    "/model-providers/7/credential",
    { api_key: "sk-sensitive" },
  );
});
```

- [ ] **Step 2: Run focused tests and verify missing exports**

Run: `cd frontend && npm.cmd test -- src/api/modelInfrastructure.test.ts`

Expected: FAIL because provider registry methods and structured routes do not exist.

- [ ] **Step 3: Implement typed contracts and client methods**

Remove the closed `"deepseek" | "litellm"` provider union. Use numeric provider IDs and server-provided codes. Do not persist API keys in component state beyond the open editor, React Query caches, LocalStorage, SessionStorage, or URL parameters.

- [ ] **Step 4: Run frontend API tests**

Run: `cd frontend && npm.cmd test -- src/api/modelInfrastructure.test.ts`

Expected: PASS.

- [ ] **Step 5: Commit frontend contracts**

```bash
git add frontend/src/types.ts frontend/src/api/modelInfrastructure.ts frontend/src/api/modelInfrastructure.test.ts
git commit -m "feat: expose model provider registry client"
```

### Task 6: Rebuild the model infrastructure workbench

**Files:**
- Modify: `frontend/src/pages/ModelInfrastructure.tsx`
- Create: `frontend/src/components/models/ProviderRegistry.tsx`
- Create: `frontend/src/components/models/ProviderEditor.tsx`
- Create: `frontend/src/components/models/ProviderCredentialPanel.tsx`
- Create: `frontend/src/components/models/ProviderVerification.tsx`
- Create: `frontend/src/components/models/AgentRouteTable.tsx`
- Modify: `frontend/src/styles/model-infrastructure.css`
- Modify: `frontend/src/pages/ModelInfrastructure.test.tsx`

**Interfaces:**
- Consumes Task 5 contracts.
- Produces a desktop provider registry with write-only credential handling and route-aware deletion.

- [ ] **Step 1: Write failing interaction tests**

Test adding from each built-in template, creating a custom OpenAI-compatible provider, editing endpoint/name, saving/replacing/removing a key, clearing the key input after mutation, verification states, discovery/manual models, route assignment grouped by provider, failed-provider impact, and referenced-provider deletion guidance.

- [ ] **Step 2: Run UI tests and verify failure**

Run: `cd frontend && npm.cmd test -- src/pages/ModelInfrastructure.test.tsx`

Expected: FAIL because the current page exposes only DeepSeek/LiteLLM reference selection.

- [ ] **Step 3: Implement provider registry and editor**

Use the approved high-fidelity desktop system: compact list/detail workbench, restrained red accent, strong typography, and no nested decorative cards. Provider status is one of 未配置、待验证、可用、异常、停用. Use a dedicated key replacement action; never render a fake filled password field.

- [ ] **Step 4: Implement verification, models, and route impact**

Show safe error summaries, latency, model count, and last verification time. Preserve manual model entries when discovery is unsupported. Route selectors group models by provider and disable unverified choices with a reason. Provider deletion shows affected experts inline rather than in a raw browser dialog.

- [ ] **Step 5: Run UI tests and production build**

Run: `cd frontend && npm.cmd test -- src/pages/ModelInfrastructure.test.tsx`

Expected: PASS.

Run: `cd frontend && npm.cmd run build`

Expected: TypeScript and Vite build succeed.

- [ ] **Step 6: Commit the workbench**

```bash
git add frontend/src/pages/ModelInfrastructure.tsx frontend/src/components/models frontend/src/styles/model-infrastructure.css frontend/src/pages/ModelInfrastructure.test.tsx
git commit -m "feat: rebuild model infrastructure workbench"
```

### Task 7: Full model-governance verification

**Files:**
- Modify: `tasks/current.md`

**Interfaces:**
- Validates all previous tasks as one releasable increment.

- [ ] **Step 1: Run backend quality gates**

Run: `cd backend && python -m pytest -q`

Expected: all tests pass.

Run: `cd backend && python -m ruff check app tests`

Expected: no violations.

- [ ] **Step 2: Run frontend quality gates**

Run: `cd frontend && npm.cmd test`

Expected: all tests pass.

Run: `cd frontend && npm.cmd run build`

Expected: build succeeds.

- [ ] **Step 3: Run secret and endpoint safety checks**

Run: `git grep -n -E "(api_key|encrypted_api_key).*(console|logger|localStorage|sessionStorage)" -- backend frontend`

Expected: no secret logging or browser persistence.

Run: `cd frontend && npm.cmd audit --audit-level=high`

Expected: no reachable high or critical production vulnerabilities.

- [ ] **Step 4: Perform desktop acceptance**

Locally verify: add a DeepSeek provider, save a disposable key, confirm only its tail is displayed, verify connection, discover or manually add models, bind a primary and fallback Agent route, stream a test invocation, rotate the key, disable the provider and observe route impact, reject an unsafe custom URL, and block deletion while referenced.

- [ ] **Step 5: Update current status and commit**

```bash
git add tasks/current.md
git commit -m "docs: record model governance acceptance"
```
