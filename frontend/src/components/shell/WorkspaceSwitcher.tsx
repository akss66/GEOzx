import { CheckOutlined, DownOutlined } from "@ant-design/icons";
import { useMemo, useState } from "react";

import type { WorkspaceContext } from "../../api/shell";

export function WorkspaceSwitcher({
  context,
  clientId,
  projectId,
  accountId,
  onClientChange,
  onProjectChange,
}: {
  context: WorkspaceContext;
  clientId: number | null;
  projectId: number | null;
  accountId: number | null;
  onClientChange: (clientId: number) => void;
  onProjectChange: (projectId: number | null) => void;
}) {
  const [open, setOpen] = useState(false);
  const [pendingClientId, setPendingClientId] = useState<number | null>(null);
  const currentClient = useMemo(
    () => context.clients.find((client) => client.id === clientId) ?? context.selected_client,
    [clientId, context.clients, context.selected_client],
  );
  const currentProject = useMemo(
    () => context.projects.find((project) => project.id === projectId) ?? context.selected_project,
    [context.projects, context.selected_project, projectId],
  );

  const chooseClient = (nextClientId: number) => {
    if (nextClientId === clientId) return;
    if (accountId != null) {
      setPendingClientId(nextClientId);
      return;
    }
    onClientChange(nextClientId);
    setOpen(false);
  };

  return (
    <div className="tz-workspace-switcher">
      <button
        type="button"
        className="tz-workspace-trigger"
        aria-label="客户与项目"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        <span className="tz-workspace-monogram">舟</span>
        <span className="tz-workspace-copy">
          <strong>{currentClient?.name ?? "选择客户"}</strong>
          <small>{currentProject?.name ?? "尚未选择项目"}</small>
        </span>
        <DownOutlined />
      </button>

      {open ? (
        <section className="tz-switcher-panel" role="dialog" aria-label="切换客户与项目">
          {pendingClientId != null ? (
            <div className="tz-switch-confirmation">
              <strong>切换后将清除当前账号上下文</strong>
              <p>新的客户拥有独立项目、账号和知识，切换后需要重新选择账号。</p>
              <div>
                <button type="button" onClick={() => setPendingClientId(null)}>取消</button>
                <button
                  type="button"
                  className="is-primary"
                  onClick={() => {
                    onClientChange(pendingClientId);
                    setPendingClientId(null);
                    setOpen(false);
                  }}
                >
                  确认切换
                </button>
              </div>
            </div>
          ) : (
            <>
              <div className="tz-switcher-section">
                <span className="tz-switcher-label">客户</span>
                {context.clients.length === 0 ? <p>暂无可访问客户</p> : context.clients.map((client) => (
                  <button type="button" key={client.id} onClick={() => chooseClient(client.id)}>
                    <span>{client.name}</span>
                    {client.id === clientId ? <CheckOutlined /> : null}
                  </button>
                ))}
              </div>
              <div className="tz-switcher-section">
                <span className="tz-switcher-label">项目</span>
                <button type="button" onClick={() => { onProjectChange(null); setOpen(false); }}>
                  <span>不限定项目</span>
                  {projectId == null ? <CheckOutlined /> : null}
                </button>
                {context.projects.map((project) => (
                  <button
                    type="button"
                    key={project.id}
                    onClick={() => { onProjectChange(project.id); setOpen(false); }}
                  >
                    <span>{project.name}</span>
                    {project.id === projectId ? <CheckOutlined /> : null}
                  </button>
                ))}
              </div>
            </>
          )}
        </section>
      ) : null}
    </div>
  );
}
