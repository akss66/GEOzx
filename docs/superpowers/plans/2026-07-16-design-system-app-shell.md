# Design System and App Shell Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the approved warm-paper design foundation and a production-ready App Shell with client/project/account context, scoped access, global search, notifications, and a restrained global Agent launcher while preserving existing routes and Douyin OAuth data.

**Architecture:** Add `Client`, client/project membership, and `ProjectAccount` as compatibility-safe domain extensions. Keep `Account.project_id` during the transition and dual-read/dual-write it while new code uses `project_ids`. Expose one context aggregation API for the shell, then split the frontend shell into focused components using new `tz-*` styles layered after legacy CSS.

**Tech Stack:** FastAPI, SQLAlchemy 2 async, Alembic, Pydantic v2, PostgreSQL/SQLite tests, React 18, TypeScript, React Router, TanStack Query, Zustand, Ant Design primitives, Vitest, Testing Library, Playwright.

## Global Constraints

- Production must render real data only; no mock clients, tasks, notifications, accounts, or statuses.
- Preserve current route URLs and existing Douyin OAuth/token records.
- Keep the current primary navigation names and order for this slice.
- Use warm brand frame `#F0E8DC`, work surface `#FBF8F2`, ink `#191714`, and brand red `#D3131A`.
- Ship light theme only; remove the non-functional theme toggle from the shell.
- Client/project scope must be enforced by backend dependencies, not only hidden in the frontend.
- The Agent may suggest context changes but cannot switch client, project, platform, or account without confirmation.
- Every user-facing Chinese string touched by this slice must be valid UTF-8; no mojibake or raw internal state labels.
- Do not deploy before high-fidelity approval, localhost acceptance, and explicit user approval.

---

## File Structure

### Backend domain and APIs

- Create `backend/app/models/client.py`: `Client`, `ClientMembership`, `ProjectMembership`, `ProjectAccount`, `Notification`.
- Modify `backend/app/models/workspace.py`: add transitional nullable `client_id`, project-account compatibility relationships.
- Modify `backend/app/models/identity.py`: membership relationships only; keep global `admin/user` role stable.
- Modify `backend/app/models/enums.py`: add `ClientStatus`, `WorkspaceRole`, `NotificationStatus`.
- Modify `backend/app/models/__init__.py`: export all new models.
- Create `backend/app/schemas/client.py`: client, membership, context, search, and notification contracts.
- Create `backend/app/core/workspace_access.py`: resolve accessible clients/projects and require scoped access.
- Create `backend/app/api/clients.py`: client CRUD and membership administration.
- Create `backend/app/api/workspace_context.py`: aggregate current shell context.
- Create `backend/app/api/search.py`: permission-filtered client/project/account search.
- Create `backend/app/api/notifications.py`: list/count/read notification API.
- Modify `backend/app/api/projects.py`: client ownership and scoped reads.
- Modify `backend/app/api/accounts.py`: `client_id`, `project_ids`, and legacy `project_id` compatibility.
- Modify `backend/app/main.py`: register new routers and correct application metadata strings.
- Create `backend/migrations/versions/20260716_0200_client_workspace_shell.py`: additive schema and data migration.

### Frontend shell and foundation

- Create `frontend/src/styles/foundation.css`: approved design tokens, typography, focus, motion, and surfaces.
- Create `frontend/src/styles/app-shell.css`: responsive shell layout and component states.
- Modify `frontend/src/index.css`: remove conflicting root theme declarations only after new tokens are imported; leave page-specific legacy rules for later slices.
- Modify `frontend/src/theme/tokens.ts`: fixed light Ant Design theme mapped to approved tokens.
- Modify `frontend/src/main.tsx`: remove theme-store wiring and import new styles after legacy CSS.
- Modify `frontend/src/types.ts`: client, project membership, context, search, notification, and account project fields.
- Create `frontend/src/api/shell.ts`: context, search, and notifications API.
- Modify `frontend/src/api/workspace.ts`: client CRUD and account/project compatibility contracts.
- Modify `frontend/src/stores/currentWorkspace.ts`: client/project/platform/account context with cascading resets.
- Create `frontend/src/components/shell/navigation.tsx`: navigation data and accessible renderer.
- Create `frontend/src/components/shell/WorkspaceSwitcher.tsx`: client/project picker.
- Create `frontend/src/components/shell/AccountContext.tsx`: platform/account picker.
- Create `frontend/src/components/shell/GlobalSearch.tsx`: searchable command dialog.
- Create `frontend/src/components/shell/NotificationCenter.tsx`: real notification list and unread state.
- Create `frontend/src/components/shell/GlobalAgentLauncher.tsx`: restrained launcher and context summary; no fake Agent answers.
- Modify `frontend/src/components/AppShell.tsx`: compose focused shell components.
- Modify `frontend/src/appRoutes.ts`: keep route compatibility and split admin labels into expert/model destinations only when those pages exist.

---

### Task 1: High-Fidelity App Shell Approval Gate

**Files:**
- Create: `.superpowers/brainstorm/<active-session>/content/app-shell-high-fidelity-v1.html` (ignored prototype artifact)
- Reference: `DESIGN.md`
- Reference: `docs/superpowers/specs/2026-07-16-system-experience-restructure-design.md`

**Interfaces:**
- Consumes: approved global navigation, warm-paper frame, white work surface, client/project switcher, platform/account context.
- Produces: one approved desktop shell and one approved mobile shell used as visual acceptance references for Tasks 7-12.

- [ ] **Step 1: Render the desktop high-fidelity shell**

Include real navigation labels, the current Douyin account state, global search, unread notifications, user menu, and the Brain empty state. Do not include fake metrics or fake tasks.

- [ ] **Step 2: Render the mobile high-fidelity shell**

Show collapsed navigation, client/project context, account context, notifications, and the Agent launcher at 390px width without overlapping controls.

- [ ] **Step 3: Record the user decision**

Update the plan execution notes with the approved prototype filename and requested changes. No production code starts until the user explicitly approves both viewports.

### Task 2: Add Client and Scoped Access Domain

**Files:**
- Create: `backend/app/models/client.py`
- Modify: `backend/app/models/enums.py`
- Modify: `backend/app/models/identity.py`
- Modify: `backend/app/models/workspace.py`
- Modify: `backend/app/models/__init__.py`
- Test: `backend/tests/test_models.py`

**Interfaces:**
- Produces: `Client`, `ClientMembership`, `ProjectMembership`, `ProjectAccount`, `WorkspaceRole`.
- Invariant: non-null `Project.client_id` and `Account.client_id` identify ownership for all production/API-created rows; the ORM columns remain nullable during the compatibility window so untouched legacy tests and modules keep running. `ProjectAccount` identifies participation; legacy `Account.project_id` remains temporarily readable.

- [ ] **Step 1: Write failing model tests**

```python
def test_client_project_account_and_membership_graph(session):
    org = Org(name="同舟行")
    user = User(org=org, email="operator@example.com", hashed_password="x", display_name="运营")
    client = Client(org=org, name="云帆科技")
    project = Project(org=org, client=client, name="品牌增长")
    account = Account(org=org, client=client, platform=Platform.DOUYIN, nickname="阿k桑")
    project.accounts.append(account)
    client.memberships.append(ClientMembership(user=user, role=WorkspaceRole.OPERATOR))
    session.add(org)
    session.commit()

    assert project.client_id == client.id
    assert account.client_id == client.id
    assert project.accounts == [account]
    assert client.memberships[0].role == WorkspaceRole.OPERATOR
```

- [ ] **Step 2: Run the model test and verify failure**

Run: `cd backend && pytest tests/test_models.py::test_client_project_account_and_membership_graph -v`

Expected: FAIL because `Client` and `WorkspaceRole` do not exist.

- [ ] **Step 3: Implement additive models**

Define roles exactly as:

```python
class WorkspaceRole(enum.StrEnum):
    LEAD = "lead"
    OPERATOR = "operator"
    EDITOR = "editor"
    REVIEWER = "reviewer"

class ClientStatus(enum.StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"
```

Use unique constraints on `(client_id, user_id)`, `(project_id, user_id)`, and `(project_id, account_id)`. Add transitional nullable `client_id` to `Project` and `Account`; client-aware APIs must always infer or require a client, while untouched legacy construction remains valid during later module migrations. Retain `Account.project_id` with a deprecation comment and no new product behavior depending on it.

- [ ] **Step 4: Run model tests**

Run: `cd backend && pytest tests/test_models.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/models backend/tests/test_models.py
git commit -m "feat: add client workspace domain"
```

### Task 3: Add Compatibility-Safe Database Migration

**Files:**
- Create: `backend/migrations/versions/20260716_0200_client_workspace_shell.py`
- Test: `backend/tests/test_migrations.py`

**Interfaces:**
- Consumes: Task 2 model names and enum values.
- Produces: upgraded production data with one default client per existing org and preserved OAuth-linked account IDs.

- [ ] **Step 1: Write migration structure test**

```python
def test_client_workspace_migration_is_additive():
    module = importlib.import_module(
        "migrations.versions.20260716_0200_client_workspace_shell"
    )
    assert module.down_revision == "20260716_0100"
    source = inspect.getsource(module.upgrade)
    assert 'create_table("clients"' in source
    assert 'create_table("project_accounts"' in source
    assert 'drop_column("accounts", "project_id")' not in source
```

- [ ] **Step 2: Run test and verify failure**

Run: `cd backend && pytest tests/test_migrations.py::test_client_workspace_migration_is_additive -v`

Expected: FAIL because the migration module does not exist.

- [ ] **Step 3: Implement migration**

The migration must:

1. Create client and membership tables.
2. Add nullable `client_id` to projects/accounts.
3. Insert one `默认客户` per existing org.
4. Backfill every project/account to that org's default client.
5. Copy existing non-null `accounts.project_id` values into `project_accounts`.
6. Grant every existing user client access (`lead` for admins, `operator` for users) so current users are not locked out.
7. Verify no production project/account remains without `client_id`; keep the physical columns nullable during the compatibility window.
8. Keep `accounts.project_id` intact. A later cleanup migration will add the database non-null constraint and remove the legacy foreign key after all modules use the new relation.

- [ ] **Step 4: Verify migration graph and SQL**

Run: `cd backend && alembic heads`

Expected: exactly one head, `20260716_0200`.

Run against a disposable database: `cd backend && alembic upgrade head`

Expected: exit code 0 and existing account rows retain their IDs.

- [ ] **Step 5: Commit**

```bash
git add backend/migrations/versions/20260716_0200_client_workspace_shell.py backend/tests/test_migrations.py
git commit -m "feat: migrate existing workspaces to clients"
```

### Task 4: Implement Scoped Access Resolution

**Files:**
- Create: `backend/app/core/workspace_access.py`
- Test: `backend/tests/test_workspace_access.py`

**Interfaces:**
- Produces:
  - `async accessible_client_ids(session, user) -> set[int]`
  - `async require_client_access(session, user, client_id, roles=None) -> Client`
  - `async require_project_access(session, user, project_id, roles=None) -> Project`
- Rule: global admins can access their org; exact project membership role takes precedence over inherited client membership for project actions.

- [ ] **Step 1: Write failing access tests**

```python
@pytest.mark.asyncio
async def test_member_cannot_read_unassigned_client(session, member):
    client = Client(org_id=member.org_id, name="未授权客户")
    session.add(client)
    await session.commit()

    with pytest.raises(HTTPException) as exc:
        await require_client_access(session, member, client.id)
    assert exc.value.status_code == 404

@pytest.mark.asyncio
async def test_project_membership_overrides_client_role(session, member):
    client, project = await make_client_project(session, member.org_id)
    session.add(ClientMembership(client=client, user=member, role=WorkspaceRole.OPERATOR))
    session.add(ProjectMembership(project=project, user=member, role=WorkspaceRole.REVIEWER))
    await session.commit()

    resolved = await require_project_access(
        session, member, project.id, roles={WorkspaceRole.REVIEWER}
    )
    assert resolved.id == project.id
```

- [ ] **Step 2: Run tests and verify failure**

Run: `cd backend && pytest tests/test_workspace_access.py -v`

Expected: FAIL because the access helpers do not exist.

- [ ] **Step 3: Implement minimal access helpers**

Return 404 for inaccessible resources to avoid revealing that another client's resource exists. Return 403 only when the resource is visible but the resolved workspace role cannot perform the requested action.

- [ ] **Step 4: Run tests**

Run: `cd backend && pytest tests/test_workspace_access.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/core/workspace_access.py backend/tests/test_workspace_access.py
git commit -m "feat: enforce client and project access"
```

### Task 5: Add Client, Context, and Account Compatibility APIs

**Files:**
- Create: `backend/app/schemas/client.py`
- Create: `backend/app/api/clients.py`
- Create: `backend/app/api/workspace_context.py`
- Modify: `backend/app/api/projects.py`
- Modify: `backend/app/api/accounts.py`
- Modify: `backend/app/schemas/workspace.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_workspace_context_api.py`
- Modify test: `backend/tests/test_workspace_api.py`

**Interfaces:**
- `GET /clients -> ClientOut[]`
- `POST /clients -> ClientOut` (admin)
- `PATCH /clients/{id} -> ClientOut` (admin)
- `GET /workspace-context?client_id=&project_id= -> WorkspaceContextOut`
- `AccountOut` adds `client_id: int | None` and `project_ids: list[int]` while retaining `project_id: int | None`; API-created accounts always receive a non-null client.

- [ ] **Step 1: Write failing API tests**

```python
@pytest.mark.asyncio
async def test_workspace_context_is_permission_filtered(client, admin, member):
    admin_token = await _token(client, "admin@test.com", "admin-pw-123")
    member_token = await _token(client, "user@test.com", "user-pw-123")
    allowed = (await client.post(
        "/clients", headers=_auth(admin_token), json={"name": "云帆科技"}
    )).json()
    hidden = (await client.post(
        "/clients", headers=_auth(admin_token), json={"name": "其他客户"}
    )).json()
    await grant_client(client, admin_token, allowed["id"], member.id, "operator")

    response = await client.get("/workspace-context", headers=_auth(member_token))
    assert response.status_code == 200
    assert [row["id"] for row in response.json()["clients"]] == [allowed["id"]]
    assert hidden["id"] not in {row["id"] for row in response.json()["clients"]}
```

Also test that assigning an account to project B appends `project_ids` and does not destroy its project A relation.

- [ ] **Step 2: Run tests and verify failure**

Run: `cd backend && pytest tests/test_workspace_context_api.py tests/test_workspace_api.py -v`

Expected: new context tests FAIL; existing workspace tests continue passing before implementation.

- [ ] **Step 3: Implement schemas and APIs**

Use this response contract:

```python
class WorkspaceContextOut(BaseModel):
    clients: list[ClientOut]
    selected_client: ClientOut | None
    projects: list[ProjectOut]
    selected_project: ProjectOut | None
    accounts: list[AccountOut]
```

`GET /workspace-context` returns only accessible clients. If a supplied client/project is inaccessible, return 404. When legacy create calls omit `client_id`, infer the org's single/default active client. Account mutations dual-write `project_accounts`; when exactly one project is selected, update legacy `project_id` to that project, otherwise leave the existing legacy value until the Account Matrix slice removes it.

- [ ] **Step 4: Run focused tests**

Run: `cd backend && pytest tests/test_workspace_context_api.py tests/test_workspace_api.py tests/test_platform_integrations_api.py -v`

Expected: PASS, including existing Douyin account authorization tests.

- [ ] **Step 5: Commit**

```bash
git add backend/app/api backend/app/schemas backend/app/main.py backend/tests/test_workspace_context_api.py backend/tests/test_workspace_api.py
git commit -m "feat: expose scoped workspace context"
```

### Task 6: Add Real Notifications and First-Stage Search

**Files:**
- Modify: `backend/app/models/client.py`
- Modify: `backend/app/schemas/client.py`
- Create: `backend/app/api/notifications.py`
- Create: `backend/app/api/search.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_shell_services_api.py`

**Interfaces:**
- `GET /notifications?unread_only=false -> NotificationOut[]`
- `GET /notifications/unread-count -> {"count": int}`
- `PATCH /notifications/{id}/read -> NotificationOut`
- `GET /search?q=<2+ chars> -> SearchResultOut[]`
- Search kinds in this slice: `client`, `project`, `account`.

- [ ] **Step 1: Write failing notification/search tests**

```python
@pytest.mark.asyncio
async def test_search_never_returns_unassigned_client(client, member, session):
    visible, hidden = await seed_visible_and_hidden_clients(session, member)
    token = await _token(client, "user@test.com", "user-pw-123")
    response = await client.get("/search?q=客户", headers=_auth(token))
    ids = {(row["kind"], row["id"]) for row in response.json()}
    assert ("client", visible.id) in ids
    assert ("client", hidden.id) not in ids

@pytest.mark.asyncio
async def test_notification_can_only_be_read_by_owner(client, member, session):
    notice = Notification(org_id=member.org_id, user_id=member.id, type="task.completed", title="任务完成")
    session.add(notice)
    await session.commit()
    token = await _token(client, "user@test.com", "user-pw-123")
    response = await client.patch(f"/notifications/{notice.id}/read", headers=_auth(token))
    assert response.status_code == 200
    assert response.json()["read_at"] is not None
```

- [ ] **Step 2: Run tests and verify failure**

Run: `cd backend && pytest tests/test_shell_services_api.py -v`

Expected: FAIL because routes do not exist.

- [ ] **Step 3: Implement services**

Search uses case-insensitive contains matching, caps results at 20, and emits paths such as `/accounts?account=<id>`. Notification payload is JSON but API never returns secrets or raw token data.

- [ ] **Step 4: Run tests**

Run: `cd backend && pytest tests/test_shell_services_api.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/models/client.py backend/app/schemas/client.py backend/app/api/notifications.py backend/app/api/search.py backend/app/main.py backend/tests/test_shell_services_api.py
git commit -m "feat: add shell notifications and search"
```

### Task 7: Replace the Theme Foundation

**Files:**
- Create: `frontend/src/styles/foundation.css`
- Create: `frontend/src/styles/app-shell.css`
- Modify: `frontend/src/theme/tokens.ts`
- Modify: `frontend/src/main.tsx`
- Modify: `frontend/src/index.css`
- Delete after references are removed: `frontend/src/stores/theme.ts`
- Delete: `frontend/src/stores/theme.test.ts`
- Test: `frontend/src/theme/tokens.test.ts`

**Interfaces:**
- Produces CSS variables named `--tz-brand-red`, `--tz-canvas-warm`, `--tz-work-surface`, `--tz-ink`, `--tz-line`, `--tz-radius-control`, `--tz-radius-object`, `--tz-radius-floating`.
- Produces `buildTheme()` with no dark-mode argument.

- [ ] **Step 1: Write failing token test**

```ts
import { describe, expect, it } from "vitest";
import { buildTheme } from "./tokens";

describe("theme tokens", () => {
  it("uses the approved light brand palette", () => {
    const theme = buildTheme();
    expect(theme.token?.colorPrimary).toBe("#D3131A");
    expect(theme.token?.colorText).toBe("#191714");
    expect(theme.token?.borderRadius).toBe(10);
  });
});
```

- [ ] **Step 2: Run test and verify failure**

Run: `cd frontend && npm.cmd test -- src/theme/tokens.test.ts`

Expected: FAIL because current theme requires a mode and uses graphite as primary.

- [ ] **Step 3: Implement light-only foundation**

Import order in `main.tsx` must be:

```ts
import "./index.css";
import "./styles/foundation.css";
import "./styles/app-shell.css";
```

Remove `useThemeMode`, the `data-theme` effect, and the moon control dependency. Keep legacy page CSS temporarily so later module slices remain usable.

- [ ] **Step 4: Run token tests and build**

Run: `cd frontend && npm.cmd test -- src/theme/tokens.test.ts && npm.cmd run build`

Expected: PASS and Vite build exit code 0.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/styles frontend/src/theme frontend/src/main.tsx frontend/src/index.css frontend/src/stores/theme.ts frontend/src/stores/theme.test.ts
git commit -m "feat: establish warm paper design foundation"
```

### Task 8: Expand the Current Workspace Store and API Client

**Files:**
- Modify: `frontend/src/types.ts`
- Create: `frontend/src/api/shell.ts`
- Modify: `frontend/src/api/workspace.ts`
- Modify: `frontend/src/stores/currentWorkspace.ts`
- Modify: `frontend/src/stores/currentWorkspace.test.ts`
- Create: `frontend/src/api/shell.test.ts`

**Interfaces:**
- State: `{ clientId, projectId, platform, accountId }`.
- Actions: `setClientId`, `setProjectId`, `setPlatform`, `setAccountId`, `hydrate`, `clear`.
- API: `getWorkspaceContext`, `searchWorkspace`, `listNotifications`, `getUnreadCount`, `markNotificationRead`.

- [ ] **Step 1: Write failing cascading-context tests**

```ts
it("clears project and account when the client changes", async () => {
  installLocalStorage();
  const { useCurrentWorkspace } = await import("./currentWorkspace");
  useCurrentWorkspace.getState().hydrate({ clientId: 1, projectId: 2, platform: "douyin", accountId: 3 });
  useCurrentWorkspace.getState().setClientId(9);
  expect(useCurrentWorkspace.getState()).toMatchObject({
    clientId: 9,
    projectId: null,
    platform: "douyin",
    accountId: null,
  });
});
```

- [ ] **Step 2: Run tests and verify failure**

Run: `cd frontend && npm.cmd test -- src/stores/currentWorkspace.test.ts src/api/shell.test.ts`

Expected: FAIL because client/project state and shell API do not exist.

- [ ] **Step 3: Implement types, API, and store**

Persist under the existing `tongzhouxing_current_workspace` key with a versioned payload:

```ts
interface StoredWorkspaceV2 {
  version: 2;
  clientId: number | null;
  projectId: number | null;
  platform: Platform;
  accountId: number | null;
}
```

Migrate the old `{platform, accountId}` payload in memory; validation against `/workspace-context` decides whether the old account remains selected.

- [ ] **Step 4: Run tests**

Run: `cd frontend && npm.cmd test -- src/stores/currentWorkspace.test.ts src/api/shell.test.ts`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/types.ts frontend/src/api/shell.ts frontend/src/api/shell.test.ts frontend/src/api/workspace.ts frontend/src/stores/currentWorkspace.ts frontend/src/stores/currentWorkspace.test.ts
git commit -m "feat: add client-aware workspace context"
```

### Task 9: Split and Rebuild the App Shell

**Files:**
- Create: `frontend/src/components/shell/navigation.tsx`
- Create: `frontend/src/components/shell/WorkspaceSwitcher.tsx`
- Create: `frontend/src/components/shell/AccountContext.tsx`
- Modify: `frontend/src/components/AppShell.tsx`
- Modify: `frontend/src/components/AppShell.test.tsx`
- Test: `frontend/src/components/shell/WorkspaceSwitcher.test.tsx`

**Interfaces:**
- `WorkspaceSwitcher({context, value, onChange})` emits only user-confirmed client/project changes.
- `AccountContext({accounts, platform, accountId, onChange})` emits only accounts from the current context.
- Navigation keeps current member route order and separates admin items.

- [ ] **Step 1: Write failing shell tests**

```tsx
it("shows valid Chinese navigation and no theme toggle", async () => {
  renderShell();
  expect(screen.getByText("运营大脑")).toBeInTheDocument();
  expect(screen.getByText("账号矩阵")).toBeInTheDocument();
  expect(screen.queryByLabelText("切换主题")).not.toBeInTheDocument();
  expect(document.body.textContent).not.toContain("????");
});

it("asks for confirmation before changing away from an active account", async () => {
  renderWorkspaceSwitcher({ accountId: 3 });
  await user.click(screen.getByRole("button", { name: /切换客户/ }));
  await user.click(screen.getByText("山海餐饮"));
  expect(screen.getByText("切换后将清除当前账号上下文")).toBeInTheDocument();
});
```

- [ ] **Step 2: Run tests and verify failure**

Run: `cd frontend && npm.cmd test -- src/components/AppShell.test.tsx src/components/shell/WorkspaceSwitcher.test.tsx`

Expected: FAIL against the monolithic mojibake shell.

- [ ] **Step 3: Implement focused shell composition**

`AppShell.tsx` should only compose:

```tsx
<div className="tz-shell">
  <ShellNavigation />
  <div className="tz-shell__main">
    <ShellHeader>
      <WorkspaceSwitcher />
      <AccountContext />
      <ShellActions />
    </ShellHeader>
    <main className="tz-shell__page"><Outlet /></main>
  </div>
</div>
```

Do not move page-specific layout into the shell. Preserve logout in the user menu and keep an explicit text logout action available on narrow screens.

- [ ] **Step 4: Run shell tests and build**

Run: `cd frontend && npm.cmd test -- src/components/AppShell.test.tsx src/components/shell/WorkspaceSwitcher.test.tsx && npm.cmd run build`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/AppShell.tsx frontend/src/components/AppShell.test.tsx frontend/src/components/shell
git commit -m "feat: rebuild client-aware app shell"
```

### Task 10: Add Global Search and Notification Center

**Files:**
- Create: `frontend/src/components/shell/GlobalSearch.tsx`
- Create: `frontend/src/components/shell/GlobalSearch.test.tsx`
- Create: `frontend/src/components/shell/NotificationCenter.tsx`
- Create: `frontend/src/components/shell/NotificationCenter.test.tsx`
- Modify: `frontend/src/components/AppShell.tsx`

**Interfaces:**
- Search opens from its header button and `/` shortcut when focus is not in an editable element.
- Selecting a result updates context first, then navigates.
- Notifications use real API data and show a zero state without a decorative illustration.

- [ ] **Step 1: Write failing interaction tests**

```tsx
it("updates context before navigating to an account result", async () => {
  renderGlobalSearch({ result: accountSearchResult });
  await user.type(screen.getByRole("searchbox"), "阿k桑");
  await user.click(await screen.findByText("阿k桑"));
  expect(setClientId).toHaveBeenCalledWith(4);
  expect(setProjectId).toHaveBeenCalledWith(8);
  expect(setAccountId).toHaveBeenCalledWith(12);
  expect(navigate).toHaveBeenCalledWith("/accounts?account=12");
});

it("marks a notification read after it opens", async () => {
  renderNotificationCenter({ notification: pendingApproval });
  await user.click(screen.getByRole("button", { name: /通知/ }));
  await user.click(screen.getByText("脚本等待审批"));
  expect(markNotificationRead).toHaveBeenCalledWith(pendingApproval.id);
});
```

- [ ] **Step 2: Run tests and verify failure**

Run: `cd frontend && npm.cmd test -- src/components/shell/GlobalSearch.test.tsx src/components/shell/NotificationCenter.test.tsx`

Expected: FAIL because components do not exist.

- [ ] **Step 3: Implement components**

Debounce search by 200ms and require two non-whitespace characters. Render type labels in Chinese. Do not show an unread badge when count is zero.

- [ ] **Step 4: Run tests and build**

Run: `cd frontend && npm.cmd test -- src/components/shell/GlobalSearch.test.tsx src/components/shell/NotificationCenter.test.tsx && npm.cmd run build`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/shell/GlobalSearch.tsx frontend/src/components/shell/GlobalSearch.test.tsx frontend/src/components/shell/NotificationCenter.tsx frontend/src/components/shell/NotificationCenter.test.tsx frontend/src/components/AppShell.tsx
git commit -m "feat: add shell search and notifications"
```

### Task 11: Add the Restrained Global Agent Launcher

**Files:**
- Create: `frontend/src/components/shell/GlobalAgentLauncher.tsx`
- Create: `frontend/src/components/shell/GlobalAgentLauncher.test.tsx`
- Modify: `frontend/src/components/AppShell.tsx`

**Interfaces:**
- Opens from floating button or `Ctrl/Cmd + K`.
- Displays current client/project/account context.
- Offers “仅讨论” and “创建正式任务”.
- Reuses existing Brain draft/task APIs; it must not invent an Agent response when the API has not returned one.

- [ ] **Step 1: Write failing launcher tests**

```tsx
it("requires confirmation before applying an Agent-suggested context switch", async () => {
  renderLauncher({ suggestion: { clientId: 7, projectId: 9, accountId: 11 } });
  await user.click(screen.getByText("切换到建议的工作上下文"));
  expect(setClientId).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "确认切换" }));
  expect(setClientId).toHaveBeenCalledWith(7);
});
```

- [ ] **Step 2: Run test and verify failure**

Run: `cd frontend && npm.cmd test -- src/components/shell/GlobalAgentLauncher.test.tsx`

Expected: FAIL because component does not exist.

- [ ] **Step 3: Implement launcher**

The launcher is a floating panel, not a second full Brain implementation. “创建正式任务” navigates to `/brain` with a typed draft payload in router state; “仅讨论” stays in the panel and uses the existing non-task chat path only if that API exists. If no non-task API exists, disable “仅讨论” with the truthful label `运营大脑模块完成后开放` rather than faking a response.

- [ ] **Step 4: Run test and build**

Run: `cd frontend && npm.cmd test -- src/components/shell/GlobalAgentLauncher.test.tsx && npm.cmd run build`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/shell/GlobalAgentLauncher.tsx frontend/src/components/shell/GlobalAgentLauncher.test.tsx frontend/src/components/AppShell.tsx
git commit -m "feat: add global agent launcher"
```

### Task 12: Responsive and End-to-End Acceptance

**Files:**
- Create: `frontend/e2e/app-shell.spec.ts`
- Modify: `frontend/playwright.config.ts`
- Modify: `tasks/current.md`

**Interfaces:**
- Desktop target: 1440x900 and 1920x1080.
- Mobile target: 390x844.
- Verifies shell only; business module redesign remains out of scope.

- [ ] **Step 1: Add Playwright acceptance cases**

```ts
test("desktop shell switches context without overlap", async ({ page }) => {
  await loginAsAdmin(page);
  await page.goto("/");
  await expect(page.getByRole("navigation")).toBeVisible();
  await expect(page.getByRole("button", { name: /客户与项目/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /当前账号/ })).toBeVisible();
  await expect(page.locator("body")).not.toContainText("????");
});

test("mobile shell exposes navigation and approvals without horizontal overflow", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await loginAsAdmin(page);
  await page.goto("/");
  await page.getByRole("button", { name: "打开导航" }).click();
  await expect(page.getByText("人工审批")).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth);
  expect(overflow).toBe(false);
});
```

- [ ] **Step 2: Run complete backend verification**

Run: `cd backend && ruff check app tests && pytest -q`

Expected: no lint errors and all tests PASS.

- [ ] **Step 3: Run complete frontend verification**

Run: `cd frontend && npm.cmd run lint && npm.cmd test && npm.cmd run build && npm.cmd run test:e2e -- app-shell.spec.ts`

Expected: all commands exit 0.

- [ ] **Step 4: Perform visual verification**

Use Playwright screenshots at 1440x900, 1920x1080, and 390x844. Verify no overlap, clipping, mojibake, fake data, or unreadable muted text. Verify the navigation, search, notifications, user menu, context switchers, and Agent launcher are keyboard reachable.

- [ ] **Step 5: Update task status and commit**

Mark only completed Slice 1 items in `tasks/current.md`.

```bash
git add frontend/e2e/app-shell.spec.ts frontend/playwright.config.ts tasks/current.md
git commit -m "test: verify responsive app shell"
```

- [ ] **Step 6: Localhost acceptance gate**

Start backend and frontend locally, provide `http://localhost:5173`, and wait for explicit user approval. Do not deploy in this task.

---

## Rollback Strategy

1. Database migration is additive; rollback must not run after new client/project associations are used in production without first exporting them.
2. Existing `accounts.project_id`, OAuth account IDs, and encrypted token columns remain untouched during this slice.
3. Frontend shell changes are isolated behind `AppShell`; reverting its commits restores the old shell without reverting OAuth or business data.
4. Production deployment uses the existing image tag rollback procedure and runs read-only smoke checks before any user writes.

## Definition of Done

- High-fidelity desktop and mobile shell approved before implementation.
- Existing users can still log in and access their migrated default client.
- Existing Douyin account remains authorized and selectable.
- Client/project/account switching is scoped and confirmation-safe.
- Navigation contains valid Chinese text and no stale theme toggle.
- Search and notifications use real API data.
- Global Agent launcher never fakes an answer or silently switches context.
- Backend and frontend full test suites pass.
- Localhost desktop/mobile acceptance passes.
- Production remains unchanged until explicit approval.
