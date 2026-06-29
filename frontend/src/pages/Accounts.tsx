import { PlusOutlined } from "@ant-design/icons";
import {
  App as AntApp,
  Button,
  Form,
  Input,
  Modal,
  Segmented,
  Select,
  Table,
  Tag,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import {
  createAccount,
  createAccountGroup,
  listAccountGroups,
  listAccounts,
} from "../api/workspace";
import { PageHeader, PlatformTag } from "../components/ui";
import { useAuth } from "../stores/auth";
import type { Account, AccountStatus, GroupDimension, Platform } from "../types";

const STATUS_TAG: Record<AccountStatus, { color: string; label: string }> = {
  active: { color: "green", label: "正常" },
  inactive: { color: "default", label: "停用" },
  banned: { color: "red", label: "封禁" },
};

const DIMENSION_LABEL: Record<GroupDimension, string> = {
  track: "赛道",
  persona: "人设",
  platform: "平台",
};

const PLATFORM_OPTIONS: { label: string; value: Platform }[] = [
  { label: "抖音", value: "douyin" },
  { label: "小红书", value: "xiaohongshu" },
  { label: "视频号", value: "shipinhao" },
];

interface AccountFormValues {
  nickname: string;
  platform: Platform;
  group_id?: number;
  external_account_id?: string;
}

export default function Accounts() {
  const { message } = AntApp.useApp();
  const qc = useQueryClient();
  const isAdmin = useAuth((s) => s.user?.role === "admin");

  const [group, setGroup] = useState<string>("all");
  const [selected, setSelected] = useState<number[]>([]);
  const [modalOpen, setModalOpen] = useState(false);
  const [form] = Form.useForm<AccountFormValues>();

  const groupsQuery = useQuery({ queryKey: ["account-groups"], queryFn: listAccountGroups });
  const accountsQuery = useQuery({ queryKey: ["accounts"], queryFn: () => listAccounts() });

  const groupName = useMemo(() => {
    const map = new Map<number, string>();
    (groupsQuery.data ?? []).forEach((g) => map.set(g.id, g.name));
    return map;
  }, [groupsQuery.data]);

  const rows = useMemo(() => {
    const all = accountsQuery.data ?? [];
    return group === "all" ? all : all.filter((a) => a.group_id === Number(group));
  }, [accountsQuery.data, group]);

  const createMutation = useMutation({
    mutationFn: createAccount,
    onSuccess: () => {
      message.success("账号已授权接入");
      setModalOpen(false);
      form.resetFields();
      qc.invalidateQueries({ queryKey: ["accounts"] });
    },
    onError: () => message.error("授权失败，请重试"),
  });

  const columns: ColumnsType<Account> = [
    {
      title: "账号",
      dataIndex: "nickname",
      render: (v: string) => <span style={{ fontWeight: 500 }}>{v}</span>,
    },
    {
      title: "平台",
      dataIndex: "platform",
      width: 90,
      render: (p: Platform) => <PlatformTag platform={p} />,
    },
    {
      title: "分组",
      dataIndex: "group_id",
      width: 130,
      render: (gid: number | null) =>
        gid != null ? <Tag>{groupName.get(gid) ?? `#${gid}`}</Tag> : <span style={{ color: "var(--dy-faint)" }}>—</span>,
    },
    {
      title: "外部账号 ID",
      dataIndex: "external_account_id",
      render: (v: string | null) =>
        v ? (
          <span className="dy-tabular" style={{ fontSize: 12.5, color: "var(--dy-muted)" }}>{v}</span>
        ) : (
          <span style={{ color: "var(--dy-faint)" }}>未绑定</span>
        ),
    },
    {
      title: "状态",
      dataIndex: "status",
      width: 90,
      render: (s: AccountStatus) => (
        <Tag color={STATUS_TAG[s].color} style={{ marginInlineEnd: 0 }}>
          {STATUS_TAG[s].label}
        </Tag>
      ),
    },
  ];

  const accounts = accountsQuery.data ?? [];

  return (
    <div>
      <PageHeader
        title="账号矩阵"
        subtitle={`${accounts.length} 个在管账号 · 按赛道 / 人设 / 平台分组 · 任务可批量下发`}
        extra={
          isAdmin && (
            <Button type="primary" icon={<PlusOutlined />} onClick={() => setModalOpen(true)}>
              授权账号
            </Button>
          )
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
          onChange={(v) => setGroup(v as string)}
          options={[
            { label: "全部分组", value: "all" },
            ...(groupsQuery.data ?? []).map((g) => ({ label: g.name, value: String(g.id) })),
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
        loading={accountsQuery.isLoading}
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

      <Modal
        title="授权账号接入"
        open={modalOpen}
        onCancel={() => setModalOpen(false)}
        onOk={() => form.submit()}
        confirmLoading={createMutation.isPending}
        okText="接入"
        destroyOnClose
      >
        <Form
          form={form}
          layout="vertical"
          requiredMark={false}
          onFinish={(v) => createMutation.mutate(v)}
        >
          <Form.Item
            name="nickname"
            label="账号昵称"
            rules={[{ required: true, message: "请输入账号昵称" }]}
          >
            <Input placeholder="例如：数码菌" maxLength={200} />
          </Form.Item>
          <Form.Item
            name="platform"
            label="平台"
            rules={[{ required: true, message: "请选择平台" }]}
          >
            <Select options={PLATFORM_OPTIONS} placeholder="选择平台" />
          </Form.Item>
          <Form.Item name="group_id" label="分组">
            <GroupSelect />
          </Form.Item>
          <Form.Item name="external_account_id" label="外部账号 ID">
            <Input placeholder="平台侧账号 ID（可选）" maxLength={128} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}

/** 分组下拉：复用查询缓存，并支持就地创建新分组。 */
function GroupSelect(props: { value?: number; onChange?: (v: number | undefined) => void }) {
  const { message } = AntApp.useApp();
  const qc = useQueryClient();
  const groupsQuery = useQuery({ queryKey: ["account-groups"], queryFn: listAccountGroups });
  const [name, setName] = useState("");

  const createMutation = useMutation({
    mutationFn: () => createAccountGroup({ name: name.trim(), dimension: "track" }),
    onSuccess: (g) => {
      setName("");
      qc.invalidateQueries({ queryKey: ["account-groups"] });
      props.onChange?.(g.id);
      message.success("分组已创建");
    },
    onError: () => message.error("创建分组失败"),
  });

  return (
    <Select
      placeholder="选择分组（可选）"
      allowClear
      value={props.value}
      onChange={(v) => props.onChange?.(v)}
      loading={groupsQuery.isLoading}
      options={(groupsQuery.data ?? []).map((g) => ({
        label: `${g.name} · ${DIMENSION_LABEL[g.dimension]}`,
        value: g.id,
      }))}
      popupRender={(menu) => (
        <>
          {menu}
          <div style={{ display: "flex", gap: 6, padding: 8 }}>
            <Input
              size="small"
              placeholder="新建分组名"
              value={name}
              onChange={(e) => setName(e.target.value)}
              onPressEnter={(e) => {
                e.preventDefault();
                if (name.trim()) createMutation.mutate();
              }}
            />
            <Button
              size="small"
              type="text"
              icon={<PlusOutlined />}
              loading={createMutation.isPending}
              disabled={!name.trim()}
              onClick={() => createMutation.mutate()}
            >
              新建
            </Button>
          </div>
        </>
      )}
    />
  );
}
