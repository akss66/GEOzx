import {
  ApiOutlined,
  AppstoreOutlined,
  AuditOutlined,
  BookOutlined,
  BulbOutlined,
  DeploymentUnitOutlined,
  DownOutlined,
  ForkOutlined,
  FundProjectionScreenOutlined,
  LineChartOutlined,
  LogoutOutlined,
  MoonOutlined,
  PlusOutlined,
  SearchOutlined,
  TeamOutlined,
  UserOutlined,
  WalletOutlined,
} from "@ant-design/icons";
import { useQuery } from "@tanstack/react-query";
import { Avatar, Button, Empty, Input, Layout, Menu, Popover, Tag, Tooltip, Typography } from "antd";
import type { MenuProps } from "antd";
import { useEffect, useMemo } from "react";
import { Outlet, useLocation, useNavigate } from "react-router-dom";

import { listAccounts } from "../api/workspace";
import { useEventStream } from "../hooks/useEventStream";
import { useAuth } from "../stores/auth";
import { useCurrentWorkspace } from "../stores/currentWorkspace";
import { useThemeMode } from "../stores/theme";
import type { Account, AuthStatus, Platform } from "../types";

const { Sider, Header, Content } = Layout;

const PLATFORM_LABEL: Record<Platform, string> = {
  douyin: "抖音",
  xiaohongshu: "小红书",
  shipinhao: "视频号",
};

const AUTH_LABEL: Record<AuthStatus, string> = {
  unauthorized: "待授权",
  authorized: "已授权",
  expired: "已过期",
  manual: "手动维护",
};

const AUTH_TONE: Record<AuthStatus, "success" | "warning" | "error" | "default"> = {
  unauthorized: "warning",
  authorized: "success",
  expired: "error",
  manual: "default",
};

export function buildAppShellMenuItems(isAdmin: boolean): NonNullable<MenuProps["items"]> {
  const items: NonNullable<MenuProps["items"]> = [
    { type: "group", label: "AI 运营" },
    { key: "/", icon: <DeploymentUnitOutlined />, label: "运营大脑" },
    { key: "/agents", icon: <ForkOutlined />, label: "专家团" },
    { type: "group", label: "运营执行" },
    { key: "/accounts", icon: <AppstoreOutlined />, label: "账号矩阵" },
    { key: "/tasks", icon: <FundProjectionScreenOutlined />, label: "内容生产" },
    { key: "/approvals", icon: <AuditOutlined />, label: "人工审批" },
    { key: "/review", icon: <LineChartOutlined />, label: "运营复盘" },
    { type: "group", label: "系统资产" },
    { key: "/cost", icon: <WalletOutlined />, label: "使用成本" },
    { key: "/knowledge", icon: <BookOutlined />, label: "知识库" },
  ];

  if (isAdmin) {
    items.push(
      { type: "group", label: "管理员" },
      { key: "/users", icon: <TeamOutlined />, label: "用户管理" },
      { key: "/config", icon: <ApiOutlined />, label: "Agent 配置" },
    );
  }

  return items;
}

export function AppShell() {
  const { user, logout } = useAuth();
  const { mode, toggle } = useThemeMode();
  const navigate = useNavigate();
  const location = useLocation();
  const { connected } = useEventStream();
  const isAdmin = user?.role === "admin";
  const selectedKey = location.pathname === "/brain" ? "/" : location.pathname;

  const items = buildAppShellMenuItems(isAdmin);

  return (
    <Layout className="dy-app-canvas dy-shell">
      <Sider width={236} className="dy-shell-sider">
        <button type="button" className="dy-shell-brand" onClick={() => navigate("/")}>
          <img src="/logo.png" alt="同舟行" className="dy-shell-brand-mark" />
          <span className="dy-shell-brand-copy">
            <strong>同舟行</strong>
            <small>AI+Agent+运营</small>
          </span>
        </button>

        <Menu
          mode="inline"
          selectedKeys={[selectedKey]}
          items={items}
          onClick={({ key }) => navigate(key)}
          className="dy-shell-menu"
        />
      </Sider>

      <Layout className="dy-shell-main">
        <Header className="dy-shell-header">
          <CurrentAccountSwitcher />

          <div className="dy-shell-header-tools">
            <Input
              aria-label="搜索任务、账号或专家"
              prefix={<SearchOutlined className="dy-shell-search-icon" />}
              placeholder="搜索任务、账号或专家"
              className="dy-shell-search"
            />

            <Tooltip title={connected ? "实时事件流已连接" : "实时事件流未连接"}>
              <span className="dy-shell-live" data-connected={connected}>
                <span />
                实时
              </span>
            </Tooltip>

            <Tooltip title={mode === "dark" ? "切换浅色" : "切换深色"}>
              <Button
                type="text"
                size="small"
                aria-label="切换主题"
                icon={mode === "dark" ? <BulbOutlined /> : <MoonOutlined />}
                onClick={toggle}
                className="dy-shell-icon-button"
              />
            </Tooltip>

            <Popover
              placement="bottomRight"
              trigger="click"
              content={
                <div className="dy-user-popover">
                  <div>
                    <strong>{user?.display_name}</strong>
                    <span>{user?.email}</span>
                  </div>
                  <Button
                    type="text"
                    icon={<LogoutOutlined />}
                    onClick={() => {
                      logout();
                      navigate("/login");
                    }}
                  >
                    退出登录
                  </Button>
                </div>
              }
            >
              <button type="button" className="dy-shell-user">
                <Avatar size={30} className="dy-shell-avatar">
                  {user?.display_name?.[0] ?? "?"}
                </Avatar>
                <span>{user?.display_name}</span>
                <Tag className="dy-shell-role">{isAdmin ? "管理员" : "成员"}</Tag>
              </button>
            </Popover>

            <Button
              type="text"
              size="small"
              icon={<LogoutOutlined />}
              onClick={() => {
                logout();
                navigate("/login");
              }}
              className="dy-shell-logout"
            >
              退出
            </Button>
          </div>
        </Header>

        <Content className="dy-shell-content">
          <main className="dy-shell-page">
            <Outlet />
          </main>
        </Content>
      </Layout>
    </Layout>
  );
}

function CurrentAccountSwitcher() {
  const navigate = useNavigate();
  const { platform, accountId, setAccountId } = useCurrentWorkspace();
  const accountsQuery = useQuery({ queryKey: ["shell-accounts"], queryFn: () => listAccounts() });
  const douyinAccounts = useMemo(
    () => (accountsQuery.data ?? []).filter((account) => account.platform === "douyin"),
    [accountsQuery.data],
  );
  const currentAccount = douyinAccounts.find((account) => account.id === accountId) ?? null;

  useEffect(() => {
    if (accountId != null && accountsQuery.data && !douyinAccounts.some((account) => account.id === accountId)) {
      setAccountId(null);
    }
  }, [accountId, accountsQuery.data, douyinAccounts, setAccountId]);

  return (
    <Popover
      placement="bottomLeft"
      trigger="click"
      content={
        <AccountSwitcherPanel
          accounts={douyinAccounts}
          loading={accountsQuery.isLoading}
          selectedAccount={currentAccount}
          onSelect={setAccountId}
          onManage={() => navigate("/accounts")}
        />
      }
    >
      <button type="button" className="dy-account-context">
        <span className="dy-account-platform">{PLATFORM_LABEL[platform]}</span>
        <span className="dy-account-divider" />
        {currentAccount ? (
          <>
            <span className="dy-account-name">{currentAccount.nickname}</span>
            <StatusDot status={currentAccount.auth_status} />
          </>
        ) : (
          <span className="dy-account-empty">选择抖音账号</span>
        )}
        <DownOutlined className="dy-account-caret" />
      </button>
    </Popover>
  );
}

function AccountSwitcherPanel({
  accounts,
  loading,
  selectedAccount,
  onSelect,
  onManage,
}: {
  accounts: Account[];
  loading: boolean;
  selectedAccount: Account | null;
  onSelect: (accountId: number | null) => void;
  onManage: () => void;
}) {
  return (
    <section className="dy-account-panel">
      <header>
        <div>
          <Typography.Text className="dy-account-panel-title">当前工作账号</Typography.Text>
          <Typography.Text className="dy-account-panel-subtitle">先选账号，再开始 Agent 运营任务</Typography.Text>
        </div>
        <Tag className="dy-platform-live">抖音</Tag>
      </header>

      <div className="dy-platform-row" aria-label="平台切换">
        <button type="button" className="is-active">抖音</button>
        <button type="button" disabled>小红书</button>
        <button type="button" disabled>视频号</button>
      </div>

      <div className="dy-account-list">
        {loading ? (
          <div className="dy-account-skeleton" aria-busy="true" />
        ) : accounts.length === 0 ? (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无抖音账号" />
        ) : (
          accounts.map((account) => (
            <button
              key={account.id}
              type="button"
              className="dy-account-option"
              data-selected={selectedAccount?.id === account.id}
              onClick={() => onSelect(account.id)}
            >
              <span className="dy-account-option-avatar">
                <UserOutlined />
              </span>
              <span className="dy-account-option-main">
                <strong>{account.nickname}</strong>
                <small>{account.external_account_id ?? "未绑定外部 ID"}</small>
              </span>
              <StatusTag status={account.auth_status} />
            </button>
          ))
        )}
      </div>

      <footer>
        <Button type="text" icon={<PlusOutlined />} onClick={onManage}>
          去账号矩阵添加或重新授权
        </Button>
      </footer>
    </section>
  );
}

function StatusDot({ status }: { status: AuthStatus }) {
  return (
    <span className="dy-account-status-dot" data-status={status}>
      {AUTH_LABEL[status]}
    </span>
  );
}

function StatusTag({ status }: { status: AuthStatus }) {
  return (
    <Tag className="dy-auth-tag" data-tone={AUTH_TONE[status]}>
      {AUTH_LABEL[status]}
    </Tag>
  );
}
