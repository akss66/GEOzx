import { Button, Checkbox } from "antd";
import { useEffect, useMemo, useState } from "react";

import type { UserAccessCatalog, UserDetail, WorkspaceRole } from "../../types";
import {
  clampSelectedAccounts,
  areAccessDraftsEqual,
  detailToAccessDraft,
  formatGovernanceError,
  getAccessibleAccounts,
  getEffectiveAccounts,
  hasAvailableAccountCatalog,
  hasAvailableUserAccessDetail,
  summarizeScopeMode,
  WORKSPACE_ROLE_OPTIONS,
  type AccessDraft,
} from "./userGovernance";

export function MemberAccess({
  detail,
  catalog,
  onSave,
}: {
  detail: UserDetail;
  catalog: UserAccessCatalog;
  onSave: (draft: AccessDraft) => Promise<void>;
}) {
  const initialDraft = detailToAccessDraft(detail);
  const [baseline, setBaseline] = useState<AccessDraft>(initialDraft);
  const [draft, setDraft] = useState<AccessDraft>(initialDraft);
  const [saving, setSaving] = useState(false);
  const [feedback, setFeedback] = useState<{ tone: "neutral" | "success" | "error"; text: string } | null>(null);
  const catalogClients = Array.isArray(catalog.clients) ? catalog.clients : [];
  const catalogProjects = Array.isArray(catalog.projects) ? catalog.projects : [];
  const accessDetailAvailable = hasAvailableUserAccessDetail(detail);
  const accountCatalogAvailable = hasAvailableAccountCatalog(catalog);
  const catalogAccounts = accountCatalogAvailable ? catalog.accounts : [];

  const accessibleAccounts = useMemo(() => {
    const next = getAccessibleAccounts({
      has_global_access: detail.has_global_access,
      client_memberships: draft.clients.map((item) => ({ client_id: item.client_id, client_name: "", role: item.role })),
      project_memberships: draft.projects.map((item) => ({
        project_id: item.project_id,
        project_name: "",
        client_id: null,
        client_name: null,
        role: item.role,
      })),
    }, catalog);

    return next;
  }, [catalog, detail.has_global_access, draft.clients, draft.projects]);

  const effectiveAccounts = useMemo(
    () => getEffectiveAccounts(draft, catalog),
    [catalog, draft],
  );

  const dirty = !areAccessDraftsEqual(baseline, draft);

  useEffect(() => {
    if (!accessDetailAvailable) return;
    if (!accountCatalogAvailable) return;
    if (draft.account_scope_mode !== "selected") return;
    const nextIds = clampSelectedAccounts(draft.account_ids, accessibleAccounts);
    if (nextIds.length === draft.account_ids.length) return;
    setDraft((current) => ({ ...current, account_ids: nextIds }));
  }, [accessibleAccounts, accessDetailAvailable, accountCatalogAvailable, draft.account_ids, draft.account_scope_mode]);

  async function handleSave() {
    setSaving(true);
    setFeedback({ tone: "neutral", text: "正在保存资源权限…" });
    let accountIds = [...draft.account_ids];
    if (accountCatalogAvailable) {
      accountIds = draft.account_scope_mode === "selected"
        ? clampSelectedAccounts(draft.account_ids, accessibleAccounts)
        : [];
    }
    const nextDraft = {
      ...draft,
      account_ids: accountIds,
    };
    try {
      await onSave(nextDraft);
      setBaseline(nextDraft);
      setDraft(nextDraft);
      setFeedback({ tone: "success", text: "资源权限已保存。" });
    } catch (error) {
      setFeedback({
        tone: "error",
        text: formatGovernanceError(error, "资源权限保存失败，请稍后重试。"),
      });
    } finally {
      setSaving(false);
    }
  }

  function toggleClient(clientId: number, checked: boolean) {
    setDraft((current) => ({
      ...current,
      clients: checked
        ? [...current.clients, { client_id: clientId, role: "operator" }]
        : current.clients.filter((item) => item.client_id !== clientId),
    }));
  }

  function toggleProject(projectId: number, checked: boolean) {
    setDraft((current) => ({
      ...current,
      projects: checked
        ? [...current.projects, { project_id: projectId, role: "operator" }]
        : current.projects.filter((item) => item.project_id !== projectId),
    }));
  }

  function updateClientRole(clientId: number, role: WorkspaceRole) {
    setDraft((current) => ({
      ...current,
      clients: current.clients.map((item) => (item.client_id === clientId ? { ...item, role } : item)),
    }));
  }

  function updateProjectRole(projectId: number, role: WorkspaceRole) {
    setDraft((current) => ({
      ...current,
      projects: current.projects.map((item) => (item.project_id === projectId ? { ...item, role } : item)),
    }));
  }

  function toggleScopedAccount(accountId: number, checked: boolean) {
    setDraft((current) => ({
      ...current,
      account_ids: checked
        ? [...current.account_ids, accountId]
        : current.account_ids.filter((item) => item !== accountId),
    }));
  }

  if (!accessDetailAvailable && !detail.has_global_access) {
    return (
      <section className="tz-member-tab-panel tz-member-access">
        <div className="tz-workbench-block">
          <header className="tz-workbench-block__header">
            <div>
              <h3>资源权限</h3>
              <p>当前详情来自兼容接口，不能安全重建完整授权草稿。</p>
            </div>
            <span className="tz-dirty-indicator" role="status">兼容只读</span>
          </header>
          <div className="tz-account-empty" role="status">
            <strong>成员权限详情暂不可用</strong>
            <p>当前服务未返回完整的客户、项目和账号授权数据。为避免覆盖已有权限，资源权限暂时只读。</p>
          </div>
        </div>
      </section>
    );
  }

  if (detail.has_global_access) {
    return (
      <section className="tz-member-tab-panel tz-member-access">
        <div className="tz-workbench-block">
          <header className="tz-workbench-block__header">
            <div>
              <h3>资源权限</h3>
              <p>该成员拥有全局访问权限，客户、项目和账号范围不能在此修改。</p>
            </div>
            <span className="tz-dirty-indicator" role="status">全局访问（只读）</span>
          </header>

          <section className="tz-effective-accounts" aria-label="最终生效账号">
            <header>
              <strong>最终生效账号</strong>
              <span>{accountCatalogAvailable ? `${catalogAccounts.length} 个账号` : "暂不可核对"}</span>
            </header>
            {!accountCatalogAvailable ? (
              <p className="tz-access-meta">账号目录暂不可用，无法核对最终生效账号。</p>
            ) : catalogAccounts.length ? (
              <div className="tz-effective-accounts__list">
                {catalogAccounts.map((account) => (
                  <span key={account.id} className="tz-account-pill">
                    {account.nickname}
                  </span>
                ))}
              </div>
            ) : (
              <p className="tz-access-meta">当前目录中没有账号。</p>
            )}
          </section>
        </div>
      </section>
    );
  }

  return (
    <section className="tz-member-tab-panel tz-member-access">
      <div className="tz-workbench-block">
        <header className="tz-workbench-block__header">
          <div>
            <h3>资源权限</h3>
            <p>账号白名单只会收窄已有客户或项目权限，不会额外授予任何账号访问权。</p>
          </div>
          <div className="tz-workbench-block__actions">
            <span className={`tz-dirty-indicator${dirty ? " is-dirty" : ""}`}>
              {dirty ? "有未保存更改" : "已与服务端同步"}
            </span>
            <Button type="primary" onClick={handleSave} disabled={!dirty} loading={saving}>
              保存资源权限
            </Button>
          </div>
        </header>

        <div className="tz-access-layout">
          <section className="tz-access-column">
            <header>
              <strong>客户角色</strong>
              <span>客户角色定义默认职责，项目角色可单独覆盖。</span>
            </header>
            <div className="tz-access-rows">
              {catalogClients.map((client) => {
                const membership = draft.clients.find((item) => item.client_id === client.id);
                return (
                  <div key={client.id} className={`tz-access-row${membership ? " is-selected" : ""}`}>
                    <Checkbox
                      checked={Boolean(membership)}
                      onChange={(event) => toggleClient(client.id, event.target.checked)}
                    >
                      {client.name}
                    </Checkbox>
                    {membership ? (
                      <select
                        aria-label={`${client.name} 角色`}
                        className="tz-native-select"
                        value={membership.role}
                        onChange={(event) => updateClientRole(client.id, event.target.value as WorkspaceRole)}
                      >
                        {WORKSPACE_ROLE_OPTIONS.map((option) => (
                          <option key={option.value} value={option.value}>{option.label}</option>
                        ))}
                      </select>
                    ) : (
                      <span className="tz-access-meta">未授权</span>
                    )}
                  </div>
                );
              })}
            </div>
          </section>

          <section className="tz-access-column">
            <header>
              <strong>项目角色覆盖</strong>
              <span>仅在项目职责不同于客户默认角色时配置。</span>
            </header>
            <div className="tz-access-rows">
              {catalogProjects.map((project) => {
                const membership = draft.projects.find((item) => item.project_id === project.id);
                return (
                  <div key={project.id} className={`tz-access-row${membership ? " is-selected" : ""}`}>
                    <Checkbox
                      checked={Boolean(membership)}
                      onChange={(event) => toggleProject(project.id, event.target.checked)}
                    >
                      {project.name}
                    </Checkbox>
                    {membership ? (
                      <select
                        aria-label={`${project.name} 角色`}
                        className="tz-native-select"
                        value={membership.role}
                        onChange={(event) => updateProjectRole(project.id, event.target.value as WorkspaceRole)}
                      >
                        {WORKSPACE_ROLE_OPTIONS.map((option) => (
                          <option key={option.value} value={option.value}>{option.label}</option>
                        ))}
                      </select>
                    ) : (
                      <span className="tz-access-meta">沿用客户角色</span>
                    )}
                  </div>
                );
              })}
            </div>
          </section>
        </div>

        <section className="tz-scope-panel">
          <header>
            <div>
              <strong>账号范围</strong>
              <span>当前模式：{summarizeScopeMode(draft.account_scope_mode)}</span>
            </div>
            <div className="tz-scope-mode">
              <label>
                <input
                  checked={draft.account_scope_mode === "all_accessible"}
                  disabled={!accountCatalogAvailable}
                  type="radio"
                  name={`scope-${detail.id}`}
                  aria-label="全部可见账号"
                  onChange={() => setDraft((current) => ({ ...current, account_scope_mode: "all_accessible", account_ids: [] }))}
                />
                <span>全部可见账号</span>
              </label>
              <label>
                <input
                  checked={draft.account_scope_mode === "selected"}
                  disabled={!accountCatalogAvailable}
                  type="radio"
                  name={`scope-${detail.id}`}
                  aria-label="仅指定账号"
                  onChange={() => setDraft((current) => ({ ...current, account_scope_mode: "selected" }))}
                />
                <span>仅指定账号</span>
              </label>
            </div>
          </header>

          <div className="tz-account-catalog">
            {!accountCatalogAvailable ? (
              <div className="tz-account-empty" role="status">
                <strong>账号目录暂不可用</strong>
                <p>已保存的 {draft.account_ids.length} 个账号白名单会保持不变；目录恢复前不能修改账号范围。</p>
              </div>
            ) : draft.account_scope_mode === "selected" ? (
              accessibleAccounts.length ? (
                accessibleAccounts.map((account) => (
                  <label key={account.id} className="tz-account-option">
                    <input
                      checked={draft.account_ids.includes(account.id)}
                      type="checkbox"
                      aria-label={account.nickname}
                      onChange={(event) => toggleScopedAccount(account.id, event.target.checked)}
                    />
                    <span>
                      <strong>{account.nickname}</strong>
                      <small>{account.platform} · {account.status}</small>
                    </span>
                  </label>
                ))
              ) : (
                <div className="tz-account-empty" role="status">
                  <strong>无账号可见</strong>
                  <p>该成员当前没有客户或项目可见范围，因此白名单中没有可选账号。</p>
                </div>
              )
            ) : (
              <div className="tz-account-empty" role="status">
                <strong>当前继承全部可见账号</strong>
                <p>如需进一步收窄访问范围，请切换到“仅指定账号”。</p>
              </div>
            )}
          </div>
        </section>

        <section className="tz-effective-accounts" aria-label="最终生效账号">
          <header>
            <strong>最终生效账号</strong>
            <span>{accountCatalogAvailable ? `${effectiveAccounts.length} 个账号` : "暂不可核对"}</span>
          </header>
          {!accountCatalogAvailable ? (
            <p className="tz-access-meta">账号目录暂不可用；已保存的账号白名单保持不变，当前无法核对最终生效账号。</p>
          ) : effectiveAccounts.length ? (
            <div className="tz-effective-accounts__list">
              {effectiveAccounts.map((account) => (
                <span key={account.id} className="tz-account-pill">
                  {account.nickname}
                </span>
              ))}
            </div>
          ) : (
            <p className="tz-access-meta">当前没有可生效的账号访问权限。</p>
          )}
        </section>

        {feedback ? (
          <p
            className={`tz-inline-feedback is-${feedback.tone}`}
            role={feedback.tone === "error" ? "alert" : "status"}
          >
            {feedback.text}
          </p>
        ) : null}
      </div>
    </section>
  );
}
