import {
  LogoutOutlined,
  MenuOutlined,
  QuestionCircleOutlined,
} from "@ant-design/icons";
import { useQuery } from "@tanstack/react-query";
import { Button, Drawer, Skeleton, Tooltip } from "antd";
import { useEffect, useState } from "react";
import { Outlet, useLocation, useNavigate } from "react-router-dom";

import { getWorkspaceContext } from "../api/shell";
import { presentApiError } from "../api/errors";
import { useAuth } from "../stores/auth";
import {
  resolveWorkspaceAccount,
  useCurrentWorkspace,
} from "../stores/currentWorkspace";
import { AccountContext } from "./shell/AccountContext";
import { GlobalAgentLauncher } from "./shell/GlobalAgentLauncher";
import { GlobalSearch } from "./shell/GlobalSearch";
import { ShellNavigation } from "./shell/navigation";
import { NotificationCenter } from "./shell/NotificationCenter";
import { shellPresentationForPath } from "./shell/shellPresentation";
import { WorkspaceSwitcher } from "./shell/WorkspaceSwitcher";
import { OperationalState } from "./ui";

export { buildAppShellMenuItems } from "./shell/navigation";

export const APP_SHELL_BRAND_TITLE = "同舟行AI新媒体平台";

export function selectedNavigationKey(pathname: string) {
  if (pathname === "/" || pathname === "/brain") return "/";
  if (pathname === "/pipeline") return "/tasks";
  if (pathname === "/accounts" || pathname.startsWith("/accounts/")) return "/accounts";
  return pathname;
}

export function AppShell() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);
  const workspace = useCurrentWorkspace();
  const isAdmin = user?.role === "admin";
  const selectedKey = selectedNavigationKey(location.pathname);
  const contextQuery = useQuery({
    queryKey: ["workspace-context", workspace.clientId, workspace.projectId],
    queryFn: () => getWorkspaceContext(workspace.clientId, workspace.projectId),
  });

  useEffect(() => {
    const context = contextQuery.data;
    if (!context) return;
    const clientId = context.selected_client?.id ?? null;
    const projectId = workspace.projectId != null && context.projects.some(
      (project) => project.id === workspace.projectId,
    ) ? workspace.projectId : null;
    const accountId = resolveWorkspaceAccount(
      context.accounts,
      workspace.platform,
      workspace.accountId,
    )?.id ?? null;
    if (
      clientId !== workspace.clientId ||
      projectId !== workspace.projectId ||
      accountId !== workspace.accountId
    ) {
      workspace.hydrate({ clientId, projectId, platform: workspace.platform, accountId });
    }
  }, [contextQuery.data, workspace]);

  const navigateFromMenu = (path: string) => {
    setMobileNavOpen(false);
    navigate(path);
  };
  const signOut = () => {
    workspace.clear();
    logout();
    navigate("/login");
  };
  const context = contextQuery.data;
  const shellPresentation = shellPresentationForPath(location.pathname);
  const selectedAccount = context
    ? resolveWorkspaceAccount(context.accounts, workspace.platform, workspace.accountId)
    : null;
  const contextFailure = contextQuery.isError
    ? presentApiError(
        contextQuery.error,
        "工作上下文暂时不可用，请稍后重新加载。",
      )
    : null;

  const navigation = (
    <>
      <button type="button" className="tz-shell-brand" onClick={() => navigateFromMenu("/")}>
        <img src="/logo.png" alt="" className="tz-shell-brand-mark" />
        <span><strong>{APP_SHELL_BRAND_TITLE}</strong><small>AI + Agent + 运营</small></span>
      </button>
      <div className="tz-shell-workspace">
        {context ? (
          <WorkspaceSwitcher
            context={context}
            clientId={workspace.clientId}
            projectId={workspace.projectId}
            accountId={workspace.accountId}
            onClientChange={workspace.setClientId}
            onProjectChange={workspace.setProjectId}
          />
        ) : <Skeleton.Button active block className="tz-workspace-skeleton" />}
      </div>
      <nav aria-label="系统导航" className="tz-shell-nav-scroll">
        <ShellNavigation
          isAdmin={isAdmin}
          selectedKey={selectedKey}
          onNavigate={navigateFromMenu}
        />
      </nav>
      <div className="tz-shell-profile">
        <span className="tz-shell-profile-avatar">{user?.display_name?.slice(0, 1) || "管"}</span>
        <span className="tz-shell-profile-copy">
          <strong>{user?.display_name || (isAdmin ? "系统管理员" : "成员")}</strong>
          <small>{user?.email}</small>
        </span>
        <Tooltip title="退出登录">
          <Button type="text" aria-label="退出登录" icon={<LogoutOutlined />} onClick={signOut} />
        </Tooltip>
      </div>
    </>
  );

  return (
    <div className="tz-shell">
      <aside className="tz-shell-sidebar">{navigation}</aside>
      <Drawer
        placement="left"
        width={280}
        open={mobileNavOpen}
        onClose={() => setMobileNavOpen(false)}
        className="tz-mobile-drawer"
        styles={{ body: { padding: 0 } }}
      >
        {navigation}
      </Drawer>

      <div className={`tz-shell-main${shellPresentation.raiseGlobalAgent ? " has-raised-agent-launcher" : ""}`}>
        <header className="tz-shell-header">
          <Button
            type="text"
            className="tz-mobile-menu"
            aria-label="打开导航"
            icon={<MenuOutlined />}
            onClick={() => setMobileNavOpen(true)}
          />
          <div className="tz-shell-breadcrumbs">
            <strong>{context?.selected_client?.name ?? "选择客户"}</strong>
            <span>/</span>
            <span>{context?.selected_project?.name ?? "未选择项目"}</span>
          </div>
          {context ? (
            <AccountContext
              accounts={context.accounts}
              platform={workspace.platform}
              accountId={workspace.accountId}
              onChange={workspace.setAccountId}
            />
          ) : null}
          <div className="tz-shell-actions">
            <GlobalSearch />
            <NotificationCenter />
            <Tooltip title="帮助">
              <button
                type="button"
                className="tz-shell-icon"
                aria-label="帮助"
                onClick={() => setHelpOpen(true)}
              >
                <QuestionCircleOutlined />
              </button>
            </Tooltip>
          </div>
        </header>
        <main className="tz-shell-page">
          {contextFailure ? (
            <OperationalState
              kind="error"
              title="工作上下文加载失败"
              description={`${contextFailure.message} 已选择的客户、项目和账号不会被修改。`}
              diagnostic={contextFailure.diagnostic}
              actionLabel="重新加载"
              actionLoading={contextQuery.isFetching}
              onAction={() => void contextQuery.refetch()}
            />
          ) : <Outlet />}
        </main>
        {shellPresentation.showGlobalAgent ? (
          <GlobalAgentLauncher
            clientName={context?.selected_client?.name}
            projectName={context?.selected_project?.name}
            accountName={selectedAccount?.nickname}
          />
        ) : null}
      </div>
      <Drawer
        title="平台帮助"
        placement="right"
        width={380}
        open={helpOpen}
        onClose={() => setHelpOpen(false)}
      >
        <div className="tz-help-content">
          <section>
            <strong>快速开始</strong>
            <p>先在顶部确认客户、项目和账号，再进入运营大脑描述目标。系统会按需调度专家，并在外部写入或高风险动作前请求确认。</p>
          </section>
          <section>
            <strong>快捷键</strong>
            <p><kbd>/</kbd> 打开全局搜索；<kbd>Esc</kbd> 关闭当前浮层。</p>
          </section>
          <section>
            <strong>遇到问题</strong>
            <p>请保留页面中的诊断编号和发生时间，交给平台管理员定位。</p>
          </section>
        </div>
      </Drawer>
    </div>
  );
}
