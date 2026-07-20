import type { UserAccessCatalog, WorkspaceRole } from "../../types";
import {
  clampSelectedAccounts,
  getAccessibleAccounts,
  hasAvailableAccountCatalog,
  WORKSPACE_ROLE_OPTIONS,
  type AccessDraft,
} from "./userGovernance";

function getDraftAccessibleAccounts(draft: AccessDraft, catalog: UserAccessCatalog) {
  return getAccessibleAccounts({
    has_global_access: false,
    client_memberships: draft.clients.map((item) => ({
      client_id: item.client_id,
      client_name: "",
      role: item.role,
    })),
    project_memberships: draft.projects.map((item) => ({
      project_id: item.project_id,
      project_name: "",
      client_id: null,
      client_name: null,
      role: item.role,
    })),
  }, catalog);
}

export function InitialMemberAccess({
  catalog,
  draft,
  disabled = false,
  onChange,
}: {
  catalog: UserAccessCatalog;
  draft: AccessDraft;
  disabled?: boolean;
  onChange: (draft: AccessDraft) => void;
}) {
  const clients = Array.isArray(catalog.clients) ? catalog.clients : [];
  const projects = Array.isArray(catalog.projects) ? catalog.projects : [];
  const accountCatalogAvailable = hasAvailableAccountCatalog(catalog);
  const accessibleAccounts = getDraftAccessibleAccounts(draft, catalog);

  function commitMembershipChange(nextDraft: AccessDraft) {
    const nextAccessible = getDraftAccessibleAccounts(nextDraft, catalog);
    onChange({
      ...nextDraft,
      account_ids: accountCatalogAvailable
        ? clampSelectedAccounts(nextDraft.account_ids, nextAccessible)
        : [...nextDraft.account_ids],
    });
  }

  function updateClientMembership(clientId: number, checked: boolean) {
    commitMembershipChange({
      ...draft,
      clients: checked
        ? [...draft.clients, { client_id: clientId, role: "operator" }]
        : draft.clients.filter((item) => item.client_id !== clientId),
    });
  }

  function updateProjectMembership(projectId: number, checked: boolean) {
    commitMembershipChange({
      ...draft,
      projects: checked
        ? [...draft.projects, { project_id: projectId, role: "operator" }]
        : draft.projects.filter((item) => item.project_id !== projectId),
    });
  }

  function updateClientRole(clientId: number, role: WorkspaceRole) {
    onChange({
      ...draft,
      clients: draft.clients.map((item) => item.client_id === clientId ? { ...item, role } : item),
    });
  }

  function updateProjectRole(projectId: number, role: WorkspaceRole) {
    onChange({
      ...draft,
      projects: draft.projects.map((item) => item.project_id === projectId ? { ...item, role } : item),
    });
  }

  return (
    <section className="tz-create-access" aria-label="初始资源授权">
      <header>
        <h3>初始资源授权</h3>
        <p>客户或项目授权决定可访问范围；账号白名单只能在此范围内进一步收窄。</p>
      </header>

      <div className="tz-create-access__grid">
        <fieldset className="tz-create-access__group">
          <legend>客户角色</legend>
          {clients.length ? clients.map((client) => {
            const membership = draft.clients.find((item) => item.client_id === client.id);
            return (
              <div key={client.id} className="tz-create-access__row">
                <label>
                  <input
                    aria-label={`授权客户 ${client.name}`}
                    checked={Boolean(membership)}
                    disabled={disabled}
                    type="checkbox"
                    onChange={(event) => updateClientMembership(client.id, event.target.checked)}
                  />
                  <span>{client.name}</span>
                </label>
                {membership ? (
                  <select
                    aria-label={`${client.name} 初始角色`}
                    className="tz-native-select"
                    disabled={disabled}
                    value={membership.role}
                    onChange={(event) => updateClientRole(client.id, event.target.value as WorkspaceRole)}
                  >
                    {WORKSPACE_ROLE_OPTIONS.map((option) => (
                      <option key={option.value} value={option.value}>{option.label}</option>
                    ))}
                  </select>
                ) : <span className="tz-access-meta">未授权</span>}
              </div>
            );
          }) : <p className="tz-access-meta">当前目录没有可授权客户。</p>}
        </fieldset>

        <fieldset className="tz-create-access__group">
          <legend>项目角色</legend>
          {projects.length ? projects.map((project) => {
            const membership = draft.projects.find((item) => item.project_id === project.id);
            return (
              <div key={project.id} className="tz-create-access__row">
                <label>
                  <input
                    aria-label={`授权项目 ${project.name}`}
                    checked={Boolean(membership)}
                    disabled={disabled}
                    type="checkbox"
                    onChange={(event) => updateProjectMembership(project.id, event.target.checked)}
                  />
                  <span>{project.name}</span>
                </label>
                {membership ? (
                  <select
                    aria-label={`${project.name} 初始角色`}
                    className="tz-native-select"
                    disabled={disabled}
                    value={membership.role}
                    onChange={(event) => updateProjectRole(project.id, event.target.value as WorkspaceRole)}
                  >
                    {WORKSPACE_ROLE_OPTIONS.map((option) => (
                      <option key={option.value} value={option.value}>{option.label}</option>
                    ))}
                  </select>
                ) : <span className="tz-access-meta">沿用客户角色</span>}
              </div>
            );
          }) : <p className="tz-access-meta">当前目录没有可授权项目。</p>}
        </fieldset>
      </div>

      <fieldset className="tz-create-access__scope">
        <legend>账号范围</legend>
        <div className="tz-scope-mode">
          <label>
            <input
              aria-label="新成员全部可见账号"
              checked={draft.account_scope_mode === "all_accessible"}
              disabled={disabled || !accountCatalogAvailable}
              name="create-account-scope"
              type="radio"
              onChange={() => onChange({ ...draft, account_scope_mode: "all_accessible", account_ids: [] })}
            />
            <span>全部可见账号</span>
          </label>
          <label>
            <input
              aria-label="新成员仅指定账号"
              checked={draft.account_scope_mode === "selected"}
              disabled={disabled || !accountCatalogAvailable}
              name="create-account-scope"
              type="radio"
              onChange={() => onChange({ ...draft, account_scope_mode: "selected" })}
            />
            <span>仅指定账号</span>
          </label>
        </div>
        {!accountCatalogAvailable ? (
          <p className="tz-access-meta">账号目录暂不可用；当前不能设置账号范围，客户与项目授权仍可配置。</p>
        ) : draft.account_scope_mode === "selected" ? (
          accessibleAccounts.length ? (
            <div className="tz-create-access__accounts">
              {accessibleAccounts.map((account) => (
                <label key={account.id} className="tz-account-option">
                  <input
                    aria-label={`初始账号 ${account.nickname}`}
                    checked={draft.account_ids.includes(account.id)}
                    disabled={disabled}
                    type="checkbox"
                    onChange={(event) => onChange({
                      ...draft,
                      account_ids: event.target.checked
                        ? [...draft.account_ids, account.id]
                        : draft.account_ids.filter((id) => id !== account.id),
                    })}
                  />
                  <span>{account.nickname}</span>
                </label>
              ))}
            </div>
          ) : <p className="tz-access-meta">无账号可见；保存后该成员不会获得账号访问权限。</p>
        ) : <p className="tz-access-meta">将继承上述客户或项目授权范围内的全部账号。</p>}
      </fieldset>
    </section>
  );
}
