import {
  BulbOutlined,
  DashboardOutlined,
  LogoutOutlined,
  MoonOutlined,
  SettingOutlined,
  TeamOutlined,
} from "@ant-design/icons";
import { Avatar, Dropdown, Layout, Menu, Tag, Tooltip, Typography } from "antd";
import type { MenuProps } from "antd";
import { Outlet, useLocation, useNavigate } from "react-router-dom";

import { useAuth } from "../stores/auth";
import { useThemeMode } from "../stores/theme";

const { Sider, Header, Content } = Layout;

export function AppShell() {
  const { user, logout } = useAuth();
  const { mode, toggle } = useThemeMode();
  const navigate = useNavigate();
  const location = useLocation();
  const isAdmin = user?.role === "admin";

  const menuItems: MenuProps["items"] = [
    { key: "/", icon: <DashboardOutlined />, label: "工作台" },
    ...(isAdmin
      ? [
          { key: "/users", icon: <TeamOutlined />, label: "用户管理" },
          { key: "/settings", icon: <SettingOutlined />, label: "系统配置" },
        ]
      : []),
  ];

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
      <Sider theme={mode} width={208} style={{ borderRight: "1px solid var(--dy-border)" }}>
        <div
          style={{
            height: 56,
            display: "flex",
            alignItems: "center",
            gap: 8,
            padding: "0 20px",
            fontWeight: 700,
            letterSpacing: 0.5,
            fontSize: 17,
          }}
        >
          DyFlow
        </div>
        <Menu
          theme={mode}
          mode="inline"
          selectedKeys={[location.pathname]}
          items={menuItems}
          onClick={({ key }) => navigate(key)}
          style={{ borderInlineEnd: "none" }}
        />
      </Sider>
      <Layout>
        <Header
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "flex-end",
            gap: 16,
            paddingInline: 20,
            borderBottom: "1px solid var(--dy-border)",
            background: "transparent",
          }}
        >
          <Tooltip title={mode === "dark" ? "切换浅色" : "切换深色"}>
            <span
              role="button"
              tabIndex={0}
              onClick={toggle}
              onKeyDown={(e) => e.key === "Enter" && toggle()}
              style={{ cursor: "pointer", fontSize: 16, opacity: 0.75 }}
              aria-label="切换主题"
            >
              {mode === "dark" ? <BulbOutlined /> : <MoonOutlined />}
            </span>
          </Tooltip>
          <Dropdown menu={{ items: userMenu }} placement="bottomRight">
            <span style={{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer" }}>
              <Avatar size={28} style={{ background: "#4c8dff" }}>
                {user?.display_name?.[0] ?? "?"}
              </Avatar>
              <span>
                <Typography.Text style={{ marginInlineEnd: 6 }}>
                  {user?.display_name}
                </Typography.Text>
                <Tag color={isAdmin ? "blue" : "default"} style={{ marginInlineEnd: 0 }}>
                  {isAdmin ? "管理员" : "成员"}
                </Tag>
              </span>
            </span>
          </Dropdown>
        </Header>
        <Content style={{ padding: 24 }}>
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  );
}
