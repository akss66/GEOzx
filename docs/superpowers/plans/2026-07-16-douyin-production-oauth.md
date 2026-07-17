# Douyin Production OAuth Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bind real Douyin accounts through the official OAuth flow on `https://tzxai.top` and securely retain credentials for profile and metric synchronization.

**Architecture:** Keep the existing `PlatformIntegration`, `PlatformAccountAuth`, and account matrix APIs. Add application-layer encrypted token columns, correct the official HTTP contract, refresh expiring tokens before sync, and preserve legacy secret-reference fallback during migration.

**Tech Stack:** FastAPI, SQLAlchemy async, Alembic, httpx, cryptography/Fernet, pytest, React.

## Global Constraints

- Production callback is exactly `https://tzxai.top/platform-integrations/douyin/oauth/callback`.
- Secrets and OAuth tokens never leave the backend or enter logs/events.
- New authorization starts with `user_info`; unapproved scopes are not requested.
- Webhooks, JSBridge, content publishing, and browser automation are outside this release.

---

### Task 1: Encrypted account credentials

**Files:**
- Create: `backend/app/core/credential_crypto.py`
- Modify: `backend/app/config.py`
- Modify: `backend/app/models/platform.py`
- Modify: `backend/pyproject.toml`
- Create: `backend/migrations/versions/20260716_0100_encrypted_platform_tokens.py`
- Test: `backend/tests/test_credential_crypto.py`

**Interfaces:**
- Produces: `encrypt_credential(value: str) -> str` and `decrypt_credential(value: str) -> str`.
- Produces: nullable `access_token_encrypted` and `refresh_token_encrypted` model fields.

- [ ] Write tests proving ciphertext differs from plaintext, decrypts correctly, and fails closed without a key.
- [ ] Run `pytest tests/test_credential_crypto.py -v` and verify failure before implementation.
- [ ] Add `CREDENTIAL_ENCRYPTION_KEY`, Fernet helpers, model columns, dependency, and migration.
- [ ] Run `pytest tests/test_credential_crypto.py -v` and verify all tests pass.

### Task 2: Official Douyin HTTP contract

**Files:**
- Modify: `backend/app/integrations/douyin.py`
- Test: `backend/tests/test_douyin_integration.py`

**Interfaces:**
- Produces: `refresh_douyin_access_token(client_key: str, refresh_token: str) -> dict[str, Any]`.
- Corrects: `fetch_douyin_user_info` to POST form data to `/oauth/userinfo/`.

- [ ] Write transport tests for the exact URL, HTTP method, form fields, and response extraction.
- [ ] Run the focused tests and verify they fail against the current GET `/oauth/oauth/userinfo` implementation.
- [ ] Correct the user-info request and add the refresh-token call.
- [ ] Run the focused tests and verify they pass.

### Task 3: OAuth callback persistence and profile hydration

**Files:**
- Modify: `backend/app/api/platform_integrations.py`
- Test: `backend/tests/test_platform_integrations_api.py`

**Interfaces:**
- Consumes: encrypted credential helpers and official Douyin helpers.
- Produces: callback persistence with no plaintext leakage and a connected matrix account.

- [ ] Change callback tests to assert encrypted persistence and absence of plaintext/ref placeholders.
- [ ] Run the callback tests and verify failure.
- [ ] Persist tokens, fetch the profile when `user_info` is granted, and update account/auth profile fields.
- [ ] Run callback tests and verify pass.

### Task 4: Refresh-aware data sync

**Files:**
- Modify: `backend/app/api/platform_integrations.py`
- Test: `backend/tests/test_platform_integrations_api.py`

**Interfaces:**
- Produces: an internal resolver that decrypts current credentials and rotates them when the access token is near expiry.

- [ ] Write tests for encrypted-token sync and expired-token refresh.
- [ ] Run the tests and verify failure.
- [ ] Implement decrypt, refresh, expiry updates, and legacy secret-reference fallback.
- [ ] Run the focused platform integration suite and verify pass.

### Task 5: Production configuration and verification

**Files:**
- Modify: `.env.example`
- Modify: `docker-compose.prod.yml`
- Verify: server `/home/admin/dyflow/.env` and PostgreSQL migration state.

**Interfaces:**
- Consumes: `CREDENTIAL_ENCRYPTION_KEY` and existing `DOUYIN_CLIENT_KEY`/`DOUYIN_CLIENT_SECRET`.
- Produces: a production callback that can retain and use real authorization credentials.

- [ ] Add non-secret configuration names and root-domain examples.
- [ ] Run backend tests, Ruff, and frontend build.
- [ ] Deploy changed backend files, rebuild containers, and run Alembic upgrade.
- [ ] Verify health, HTTPS callback reachability, and that no secret is returned.
- [ ] Change the Douyin console callback to the exact production URL and complete one real scan.
