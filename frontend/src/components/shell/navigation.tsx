import {
  ApiOutlined,
  AppstoreOutlined,
  AuditOutlined,
  BookOutlined,
  DeploymentUnitOutlined,
  ForkOutlined,
  FundProjectionScreenOutlined,
  LineChartOutlined,
  NodeIndexOutlined,
  TeamOutlined,
  WalletOutlined,
  WarningOutlined,
} from "@ant-design/icons";
import { Menu } from "antd";
import type { MenuProps } from "antd";

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
    { key: "/risks", icon: <WarningOutlined />, label: "风险队列" },
    { type: "group", label: "系统资产" },
    { key: "/cost", icon: <WalletOutlined />, label: "使用成本" },
    { key: "/knowledge", icon: <BookOutlined />, label: "知识库" },
  ];

  if (isAdmin) {
    items.push(
      { type: "group", label: "管理中心" },
      { key: "/users", icon: <TeamOutlined />, label: "用户管理" },
      { key: "/config", icon: <ApiOutlined />, label: "专家管理" },
      { key: "/models", icon: <NodeIndexOutlined />, label: "模型基础设施" },
    );
  }
  return items;
}

export function ShellNavigation({
  isAdmin,
  selectedKey,
  onNavigate,
}: {
  isAdmin: boolean;
  selectedKey: string;
  onNavigate: (path: string) => void;
}) {
  return (
    <Menu
      mode="inline"
      selectedKeys={[selectedKey]}
      items={buildAppShellMenuItems(isAdmin)}
      onClick={({ key }) => onNavigate(key)}
      className="tz-shell-nav"
    />
  );
}
