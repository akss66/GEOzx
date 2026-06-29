import { PlusOutlined } from "@ant-design/icons";
import { Button, Segmented, Table, Tag } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useState } from "react";

import { PageHeader, PlatformTag } from "../components/ui";
import {
  ACCOUNTS,
  ACCOUNT_GROUPS,
  type AccountRow,
} from "../mock/data";

const STATUS_TAG: Record<AccountRow["status"], { color: string; label: string }> = {
  active: { color: "green", label: "正常" },
  inactive: { color: "default", label: "停用" },
  banned: { color: "red", label: "封禁" },
};

const fmt = (n: number) => (n >= 10000 ? `${(n / 10000).toFixed(1)}w` : String(n));

export default function Accounts() {
  const [group, setGroup] = useState<string>("all");
  const [selected, setSelected] = useState<number[]>([]);

  const rows = group === "all" ? ACCOUNTS : ACCOUNTS.filter((a) => a.group === group);

  const columns: ColumnsType<AccountRow> = [
    {
      title: "账号",
      dataIndex: "nickname",
      render: (v: string) => <span style={{ fontWeight: 500 }}>{v}</span>,
    },
    {
      title: "平台",
      dataIndex: "platform",
      width: 90,
      render: (p: AccountRow["platform"]) => <PlatformTag platform={p} />,
    },
    { title: "分组", dataIndex: "group", width: 120, render: (g: string) => <Tag>{g}</Tag> },
    {
      title: "粉丝",
      dataIndex: "followers",
      width: 100,
      align: "right",
      sorter: (a, b) => a.followers - b.followers,
      render: (v: number) => <span className="dy-tabular">{fmt(v)}</span>,
    },
    {
      title: "近 7 日发布",
      dataIndex: "posts7d",
      width: 110,
      align: "right",
      render: (v: number) => <span className="dy-tabular">{v}</span>,
    },
    {
      title: "平均播放",
      dataIndex: "avgPlay",
      width: 110,
      align: "right",
      sorter: (a, b) => a.avgPlay - b.avgPlay,
      render: (v: number) => <span className="dy-tabular">{fmt(v)}</span>,
    },
    {
      title: "状态",
      dataIndex: "status",
      width: 90,
      render: (s: AccountRow["status"]) => (
        <Tag color={STATUS_TAG[s].color} style={{ marginInlineEnd: 0 }}>
          {STATUS_TAG[s].label}
        </Tag>
      ),
    },
  ];

  return (
    <div>
      <PageHeader
        title="账号矩阵"
        subtitle={`${ACCOUNTS.length} 个在管账号 · 按赛道 / 人设 / 平台分组 · 任务可批量下发`}
        extra={
          <Button type="primary" icon={<PlusOutlined />}>
            授权账号
          </Button>
        }
      />

      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          marginBottom: 14,
          gap: 12,
          flexWrap: "wrap",
        }}
      >
        <Segmented
          value={group}
          onChange={setGroup}
          options={[
            { label: "全部分组", value: "all" },
            ...ACCOUNT_GROUPS.map((g) => ({ label: g.name, value: g.name })),
          ]}
        />
        {selected.length > 0 && (
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <span style={{ fontSize: 13, color: "var(--dy-muted)" }}>
              已选 {selected.length} 个
            </span>
            <Button size="small">批量下发任务</Button>
            <Button size="small">批量排期发布</Button>
          </div>
        )}
      </div>

      <Table
        rowKey="id"
        columns={columns}
        dataSource={rows}
        pagination={false}
        rowSelection={{
          selectedRowKeys: selected,
          onChange: (keys) => setSelected(keys as number[]),
        }}
        style={{
          background: "var(--dy-surface)",
          borderRadius: 12,
          border: "1px solid var(--dy-border-subtle)",
          overflow: "hidden",
        }}
      />
    </div>
  );
}
