# Identity Governance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver complete administrator CRUD, client/project/account authorization, independent secondary passwords, and protected permanent deletion for organization members.

**Architecture:** Extend the existing `User`, membership, and workspace-access services instead of replacing them. Add explicit creator ownership to deletable business roots, calculate deletion impact in one service, and enforce a two-phase preview/execute protocol with hashed per-admin secondary credentials.

**Tech Stack:** FastAPI, Pydantic v2, SQLAlchemy async, Alembic, bcrypt, React 18, TypeScript, TanStack Query, Ant Design, Vitest, pytest.

## Global Constraints

- Desktop only; no mobile layout work in this phase.
- Administrators cannot disable or permanently delete themselves or the last active administrator.
- Secondary passwords are per administrator, irreversibly hashed, rate limited, and unavailable for deletion for 10 minutes after set/reset.
- Permanent deletion requires a fresh impact preview, exact target email, and the acting administrator's secondary password.
- Client, project, and account scope is enforced by backend APIs; frontend hiding is not an authorization boundary.
- Unauthorized cross-organization resources are returned as not found.
- Local tests and desktop acceptance must pass before production deployment.

---

### Task 1: Persist security credentials, account scope, and creator ownership

**Files:**
- Create: `backend/migrations/versions/20260720_0100_identity_governance.py`
- Modify: `backend/app/models/identity.py`
- Modify: `backend/app/models/client.py`
- Modify: `backend/app/models/brain.py`
- Modify: `backend/app/models/content.py`
- Modify: `backend/app/models/llm.py`
- Modify: `backend/app/models/__init__.py`
- Test: `backend/tests/test_identity_governance_models.py`

**Interfaces:**
- Produces: `AdminSecurityCredential`, `AccountMembership`, `User.account_scope_mode`, and nullable `created_by_id` fields used by deletion accounting.
- Preserves: existing members default to `all_accessible`; existing business rows keep `created_by_id=None`.

- [ ] **Step 1: Write failing model tests**

```python
async def test_identity_governance_models_persist(session, admin, member, account):
    member.account_scope_mode = "selected"
    session.add(AccountMembership(user_id=member.id, account_id=account.id))
    session.add(AdminSecurityCredential(user_id=admin.id, password_hash="hash"))
    await session.commit()
    assert member.account_scope_mode == "selected"
```

- [ ] **Step 2: Run the focused test and verify missing models fail**

Run: `cd backend && python -m pytest tests/test_identity_governance_models.py -q`

Expected: FAIL because `AdminSecurityCredential` and `AccountMembership` do not exist.

- [ ] **Step 3: Add migration and ORM models**

The migration must:

```python
revision = "20260720_0100"
down_revision = "20260717_0300"

op.add_column(
    "users",
    sa.Column("account_scope_mode", sa.String(length=32), nullable=False,
              server_default="all_accessible"),
)
op.create_table(
    "admin_security_credentials",
    sa.Column("id", BigIntPK, primary_key=True),
    sa.Column("user_id", BigIntPK, nullable=False, unique=True),
    sa.Column("password_hash", sa.String(255), nullable=False),
    sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("delete_available_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("failed_attempts", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
)
op.create_table(
    "account_memberships",
    sa.Column("id", BigIntPK, primary_key=True),
    sa.Column("user_id", BigIntPK, nullable=False),
    sa.Column("account_id", BigIntPK, nullable=False),
    sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
    sa.UniqueConstraint("user_id", "account_id"),
)
```

Add nullable creator foreign keys with `ondelete="RESTRICT"` to `brain_tasks`, `content_items`, and `llm_calls`. Existing records remain unowned and are never guessed during deletion. The restricted foreign keys intentionally prevent a direct user-row deletion from bypassing the protected deletion service.

- [ ] **Step 4: Run model tests and migration smoke test**

Run: `cd backend && python -m pytest tests/test_identity_governance_models.py -q`

Expected: PASS.

Run: `cd backend && python -m alembic upgrade head`

Expected: schema upgrades to `20260720_0100` without data loss.

- [ ] **Step 5: Commit the persistence increment**

```bash
git add backend/migrations/versions/20260720_0100_identity_governance.py backend/app/models backend/tests/test_identity_governance_models.py
git commit -m "feat: persist identity governance controls"
```

### Task 2: Add secondary-password security service

**Files:**
- Create: `backend/app/services/admin_security.py`
- Modify: `backend/app/schemas/auth.py`
- Modify: `backend/app/api/users.py`
- Test: `backend/tests/test_admin_secondary_password.py`

**Interfaces:**
- Produces: `set_secondary_password(session, actor, current_password, secondary_password)` and `verify_secondary_password(session, actor, secondary_password)`.
- Produces API: `PUT /users/me/secondary-password` and `GET /users/me/secondary-password/status`.

- [ ] **Step 1: Write abuse-case tests first**

```python
async def test_secondary_password_requires_current_password(client, admin_token):
    response = await client.put(
        "/users/me/secondary-password",
        headers=_auth(admin_token),
        json={"current_password": "wrong", "secondary_password": "delete-pass-123"},
    )
    assert response.status_code == 401

async def test_secondary_password_has_ten_minute_cooldown(client, admin_token):
    response = await set_secondary_password(client, admin_token)
    assert response.json()["deletion_available"] is False
```

- [ ] **Step 2: Run tests and verify endpoints are absent**

Run: `cd backend && python -m pytest tests/test_admin_secondary_password.py -q`

Expected: FAIL with `404` responses.

- [ ] **Step 3: Implement schemas and service**

```python
class SetSecondaryPasswordRequest(BaseModel):
    current_password: str = Field(min_length=8, max_length=128)
    secondary_password: str = Field(min_length=8, max_length=128)

class SecondaryPasswordStatusOut(BaseModel):
    configured: bool
    deletion_available: bool
    delete_available_at: datetime | None
    locked_until: datetime | None
```

Use existing password verification and hashing helpers. On set/reset, set `delete_available_at = now + timedelta(minutes=10)`. On five failed verifications, set `locked_until = now + timedelta(minutes=15)`. Never place either password in an `Event` payload.

- [ ] **Step 4: Run security tests**

Run: `cd backend && python -m pytest tests/test_admin_secondary_password.py -q`

Expected: PASS for current-password verification, cooldown, lockout, and successful verification reset.

- [ ] **Step 5: Commit the secondary-password increment**

```bash
git add backend/app/services/admin_security.py backend/app/schemas/auth.py backend/app/api/users.py backend/tests/test_admin_secondary_password.py
git commit -m "feat: protect destructive admin actions"
```

### Task 3: Implement three-level workspace access

**Files:**
- Modify: `backend/app/core/workspace_access.py`
- Modify: `backend/app/services/user_management.py`
- Modify: `backend/app/schemas/auth.py`
- Modify: `backend/app/api/users.py`
- Test: `backend/tests/test_workspace_access.py`
- Test: `backend/tests/test_users_management_api.py`

**Interfaces:**
- Produces: `accessible_account_ids(session, user, client_id=None, project_id=None) -> set[int] | None`, where `None` means unrestricted administrator access.
- Extends `UpdateUserAccessRequest` with `account_scope_mode` and `account_ids`.

- [ ] **Step 1: Write failing account-scope tests**

```python
async def test_selected_account_scope_filters_workspace(session, member, first_account, second_account):
    member.account_scope_mode = "selected"
    session.add(AccountMembership(user_id=member.id, account_id=first_account.id))
    await session.commit()
    assert await accessible_account_ids(session, member) == {first_account.id}
```

Also test that selected accounts must belong to an accessible client or project and to the same organization.

- [ ] **Step 2: Run focused tests and verify failure**

Run: `cd backend && python -m pytest tests/test_workspace_access.py tests/test_users_management_api.py -q`

Expected: FAIL because account scope is not applied.

- [ ] **Step 3: Implement access calculation and atomic replacement**

```python
class UpdateUserAccessRequest(BaseModel):
    clients: list[ClientAccessInput] = Field(default_factory=list, max_length=500)
    projects: list[ProjectAccessInput] = Field(default_factory=list, max_length=1000)
    account_scope_mode: Literal["all_accessible", "selected"] = "all_accessible"
    account_ids: list[int] = Field(default_factory=list, max_length=5000)
```

When mode is `selected`, reject duplicate, cross-organization, or out-of-scope account IDs. Replace client, project, and account memberships in one transaction and include only IDs and roles in the audit payload.

- [ ] **Step 4: Apply account filtering to account, brain, content, review, cost, and knowledge access helpers**

All endpoints that accept an account ID must use the shared helper. No route may duplicate account-access logic.

- [ ] **Step 5: Run authorization tests**

Run: `cd backend && python -m pytest tests/test_workspace_access.py tests/test_users_management_api.py tests/test_accounts_api.py -q`

Expected: PASS, including cross-organization `404` behavior.

- [ ] **Step 6: Commit account-scope enforcement**

```bash
git add backend/app/core/workspace_access.py backend/app/services/user_management.py backend/app/schemas/auth.py backend/app/api/users.py backend/tests
git commit -m "feat: enforce account-level member access"
```

### Task 4: Complete user CRUD and two-phase permanent deletion

**Files:**
- Create: `backend/app/services/user_deletion.py`
- Modify: `backend/app/services/user_management.py`
- Modify: `backend/app/schemas/auth.py`
- Modify: `backend/app/api/users.py`
- Modify: creator-setting routes in `backend/app/api/brain.py`, `backend/app/api/orchestrator.py`, `backend/app/api/knowledge.py`, and `backend/app/api/matrix_distribution.py`
- Test: `backend/tests/test_user_deletion_api.py`
- Test: `backend/tests/test_users_management_api.py`

**Interfaces:**
- Produces API: `POST /users/{id}/reset-password`, `POST /users/{id}/deletion-preview`, and `DELETE /users/{id}/permanent`.
- Produces: `build_deletion_impact()` and `execute_permanent_deletion()`.

- [ ] **Step 1: Write failing lifecycle and deletion tests**

Cover named test cases for stale previews, mismatched target email, incorrect secondary passwords, transactional rollback, deletion of explicitly owned assets with a sanitized receipt, self-deletion rejection, and last-active-administrator protection. Each case must construct its own target member and assert the stable response code plus post-transaction database state.

- [ ] **Step 2: Run focused tests and verify endpoint failures**

Run: `cd backend && python -m pytest tests/test_user_deletion_api.py tests/test_users_management_api.py -q`

Expected: FAIL with missing endpoint/service errors.

- [ ] **Step 3: Add request and response contracts**

```python
class UserDeletionImpactOut(BaseModel):
    target_user_id: int
    target_email: str
    counts: dict[str, int]
    preview_token: str
    expires_at: datetime
    allowed: bool
    blockers: list[str]

class PermanentDeleteUserRequest(BaseModel):
    preview_token: str = Field(min_length=20, max_length=2048)
    target_email: EmailStr
    secondary_password: str = Field(min_length=8, max_length=128)
```

Sign the preview token with the server JWT secret and include target user ID, actor ID, organization ID, expiry, and a deterministic hash of the impact counts.

- [ ] **Step 4: Implement explicit impact accounting and transaction deletion**

Delete owned root rows in dependency order. Delete event rows whose payload identifies the target as actor, target, creator, reviewer, or approver. Finally delete the user. Add one `user.permanently_deleted` receipt containing only actor ID, operation ID, timestamp, and category counts.

- [ ] **Step 5: Set creator ownership at creation boundaries**

All new `BrainTask`, `ContentItem`, `MatrixDistributionPlan`, `KnowledgeEntry`, and `LLMCall` records created from an authenticated request must record the acting user. Runtime-created descendants inherit deletion through their owned root.

- [ ] **Step 6: Run lifecycle tests**

Run: `cd backend && python -m pytest tests/test_user_deletion_api.py tests/test_users_management_api.py tests/test_brain_api.py tests/test_knowledge_api.py -q`

Expected: PASS with no orphaned foreign keys.

- [ ] **Step 7: Commit full backend CRUD**

```bash
git add backend/app backend/tests
git commit -m "feat: complete protected user lifecycle"
```

### Task 5: Extend frontend contracts and API client

**Files:**
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/api/auth.ts`
- Test: `frontend/src/api/auth.test.ts`

**Interfaces:**
- Produces typed methods: `setSecondaryPassword`, `getSecondaryPasswordStatus`, `resetUserPassword`, `previewUserDeletion`, and `permanentlyDeleteUser`.
- Extends `UserDetail` and `UpdateUserAccessInput` with account scope.

- [ ] **Step 1: Write failing API contract tests**

```typescript
it("never places destructive credentials in a query string", async () => {
  await permanentlyDeleteUser(7, input);
  expect(apiDelete).toHaveBeenCalledWith("/users/7/permanent", { data: input });
});
```

- [ ] **Step 2: Run the API tests and verify missing exports**

Run: `cd frontend && npm.cmd test -- src/api/auth.test.ts`

Expected: FAIL because the methods and types do not exist.

- [ ] **Step 3: Implement typed API methods**

Keep all passwords in request bodies. Do not cache secondary-password input or permanent-delete request bodies in React Query keys.

- [ ] **Step 4: Run API tests**

Run: `cd frontend && npm.cmd test -- src/api/auth.test.ts`

Expected: PASS.

- [ ] **Step 5: Commit frontend contracts**

```bash
git add frontend/src/types.ts frontend/src/api/auth.ts frontend/src/api/auth.test.ts
git commit -m "feat: expose member governance APIs"
```

### Task 6: Rebuild the member governance workbench

**Files:**
- Modify: `frontend/src/pages/Users.tsx`
- Create: `frontend/src/components/users/MemberOverview.tsx`
- Create: `frontend/src/components/users/MemberAccess.tsx`
- Create: `frontend/src/components/users/MemberSecurity.tsx`
- Create: `frontend/src/components/users/MemberActivity.tsx`
- Create: `frontend/src/components/users/PermanentDeletePanel.tsx`
- Modify: `frontend/src/styles/user-workspace.css`
- Modify: `frontend/src/pages/Users.test.tsx`

**Interfaces:**
- Consumes Task 5 API contracts.
- Produces four-tab desktop member workbench with controlled destructive flows.

- [ ] **Step 1: Write failing interaction tests**

Tests must prove:

```typescript
expect(await screen.findByRole("tab", { name: "概览" })).toBeInTheDocument();
expect(screen.getByRole("tab", { name: "资源权限" })).toBeInTheDocument();
expect(screen.getByRole("tab", { name: "安全与登录" })).toBeInTheDocument();
expect(screen.getByRole("tab", { name: "操作记录" })).toBeInTheDocument();
```

Also verify first-member auto-selection, account-scope toggling, secondary-password setup, stale preview recovery, and successful permanent deletion removing the member from the roster.

- [ ] **Step 2: Run UI tests and verify failure**

Run: `cd frontend && npm.cmd test -- src/pages/Users.test.tsx`

Expected: FAIL because tabs and governance components are absent.

- [ ] **Step 3: Split the oversized page into focused components**

`Users.tsx` owns list selection and server queries. Each tab owns only its form state. `PermanentDeletePanel` clears email and secondary-password fields on close, success, and error.

- [ ] **Step 4: Implement desktop layout and states**

Use the approved high-fidelity design tokens. Keep the member roster compact, avoid nested cards, and use a dedicated dangerous-operation band. Show loading, empty, error, locked, cooldown, dirty, and success states without raw JSON.

- [ ] **Step 5: Run UI tests and production build**

Run: `cd frontend && npm.cmd test -- src/pages/Users.test.tsx`

Expected: PASS.

Run: `cd frontend && npm.cmd run build`

Expected: TypeScript and Vite build succeed.

- [ ] **Step 6: Commit the workbench**

```bash
git add frontend/src/pages/Users.tsx frontend/src/components/users frontend/src/styles/user-workspace.css frontend/src/pages/Users.test.tsx
git commit -m "feat: rebuild member governance workbench"
```

### Task 7: Full identity-governance verification

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

- [ ] **Step 3: Run security checks**

Run: `cd frontend && npm.cmd audit --audit-level=high`

Expected: no reachable high or critical production vulnerabilities.

Run: `git grep -n -E "(secondary_password|api_key).*(console|logger|localStorage|sessionStorage)" -- backend frontend`

Expected: no secret logging or browser persistence.

- [ ] **Step 4: Perform desktop acceptance**

Verify locally: create member, edit profile, assign customer/project/account access, reset password, disable and re-enable, configure secondary password, wait or override time in test environment, preview deletion, reject wrong email/password, and permanently delete a disposable member.

- [ ] **Step 5: Update current status and commit**

```bash
git add tasks/current.md
git commit -m "docs: record identity governance acceptance"
```
