import {
  ApiOutlined,
  AppstoreOutlined,
  AuditOutlined,
  BulbOutlined,
  CustomerServiceOutlined,
  DashboardOutlined,
  DatabaseOutlined,
  LineChartOutlined,
  LogoutOutlined,
  MoonOutlined,
  PartitionOutlined,
  RiseOutlined,
  TeamOutlined,
  WalletOutlined,
} from "@ant-design/icons";
import { Avatar, Dropdown, Layout, Menu, Tag, Tooltip, Typography } from "antd";
import type { MenuProps } from "antd";
import { Outlet, useLocation, useNavigate } from "react-router-dom";

import { useEventStream } from "../hooks/useEventStream";
import { useAuth } from "../stores/auth";
import { useThemeMode } from "../stores/theme";

const { Sider, Header, Content } = Layout;

export function AppShell() {
  const { user, logout } = useAuth();
  const { mode, toggle } = useThemeMode();
  const navigate = useNavigate();
  const location = useLocation();
  const { connected } = useEventStream();
  const isAdmin = user?.role === "admin";

  const items: NonNullable<MenuProps["items"]> = [
    { type: "group", label: "概览" },
    { key: "/", icon: <DashboardOutlined />, label: "指挥台" },
    { type: "group", label: "生产" },
    { key: "/pipeline", icon: <PartitionOutlined />, label: "内容流水线" },
    { key: "/approvals", icon: <AuditOutlined />, label: "质量门审批" },
    { type: "group", label: "运营" },
    { key: "/customer-service", icon: <CustomerServiceOutlined />, label: "客服" },
    { key: "/advertising", icon: <RiseOutlined />, label: "投流" },
    { type: "group", label: "数据" },
    { key: "/review", icon: <LineChartOutlined />, label: "复盘看板" },
    { key: "/cost", icon: <WalletOutlined />, label: "成本" },
    { type: "group", label: "资产" },
    { key: "/accounts", icon: <AppstoreOutlined />, label: "账号矩阵" },
    { key: "/knowledge", icon: <DatabaseOutlined />, label: "知识库" },
  ];
  if (isAdmin) {
    items.push(
      { type: "group", label: "系统" },
      { key: "/config", icon: <ApiOutlined />, label: "Agent 配置" },
      { key: "/users", icon: <TeamOutlined />, label: "用户管理" },
    );
  }

  const userMenu: MenuProps["items"] = [
    {
      key: "logout",
      icon: <LogoutOutlined />,
      label: "退出登录",
      onClick: () => {
        logout();
        navigate("/login");
      },
    },
  ];

  return (
    <Layout style={{ minHeight: "100vh" }}>
      <Sider width={216} style={{ borderRight: "1px solid var(--dy-border-subtle)" }}>
        <div
          style={{
            height: 56,
            display: "flex",
            alignItems: "center",
            gap: 10,
            padding: "0 20px",
          }}
        >
          <img
            src="/logo.png"
            alt="同舟行"
            style={{ width: 24, height: 24, borderRadius: 6, objectFit: "contain" }}
          />
          <span style={{ fontWeight: 700, fontSize: 16, letterSpacing: 0.4 }}>
            同舟行
          </span>
        </div>
        <Menu
          mode="inline"
          selectedKeys={[location.pathname]}
          items={items}
          onClick={({ key }) => navigate(key)}
          style={{ borderInlineEnd: "none", paddingTop: 4 }}
        />
      </Sider>
      <Layout>
        <Header
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "flex-end",
            gap: 18,
            borderBottom: "1px solid var(--dy-border-subtle)",
          }}
        >
          <Tooltip title={connected ? "事件流已连接" : "事件流未连接"}>
            <span
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 7,
                fontSize: 12,
                color: "var(--dy-muted)",
              }}
            >
              <span
                style={{
                  width: 7,
                  height: 7,
                  borderRadius: "50%",
                  background: connected ? "var(--dy-success)" : "var(--dy-faint)",
                  boxShadow: connected ? "0 0 0 3px rgba(63,185,80,0.18)" : "none",
                }}
              />
              实时
            </span>
          </Tooltip>
          <Tooltip title={mode === "dark" ? "切换浅色" : "切换深色"}>
            <span
              role="button"
              tabIndex={0}
              aria-label="切换主题"
              onClick={toggle}
              onKeyDown={(e) => e.key === "Enter" && toggle()}
              style={{ cursor: "pointer", fontSize: 16, color: "var(--dy-muted)" }}
            >
              {mode === "dark" ? <BulbOutlined /> : <MoonOutlined />}
            </span>
          </Tooltip>
          <Dropdown menu={{ items: userMenu }} placement="bottomRight">
            <span style={{ display: "flex", alignItems: "center", gap: 9, cursor: "pointer" }}>
              <Avatar size={28} style={{ background: "#5b8cff", fontSize: 13 }}>
                {user?.display_name?.[0] ?? "?"}
              </Avatar>
              <Typography.Text style={{ fontSize: 13 }}>
                {user?.display_name}
              </Typography.Text>
              <Tag
                color={isAdmin ? "blue" : "default"}
                style={{ marginInlineEnd: 0, fontSize: 11 }}
              >
                {isAdmin ? "管理员" : "成员"}
              </Tag>
            </span>
          </Dropdown>
        </Header>
        <Content style={{ padding: 24, overflow: "auto" }}>
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  );
}
