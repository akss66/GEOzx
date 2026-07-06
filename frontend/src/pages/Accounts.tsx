import {
  CheckCircleFilled,
  ClockCircleFilled,
  ExclamationCircleFilled,
  LinkOutlined,
  PlusOutlined,
  QrcodeOutlined,
  SettingOutlined,
  SyncOutlined,
} from "@ant-design/icons";
import {
  App as AntApp,
  Button,
  Empty,
  Form,
  Input,
  Modal,
  QRCode,
  Space,
  Select,
  Table,
  Tag,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState, type ReactNode } from "react";

import {
  createAccount,
  createAccountGroup,
  createDouyinAuthorizeUrl,
  createDouyinScanAddUrl,
  createDouyinTrialWhitelistUrl,
  getAccountMatrix,
  listAccountGroups,
  listAccounts,
  listPlatformIntegrations,
  listProjects,
  syncDouyinAccountMetrics,
  updateAccountIntegration,
  updatePlatformIntegration,
} from "../api/workspace";
import { PageHeader, Panel } from "../components/ui";
import { useAuth } from "../stores/auth";
import { PLATFORM_COLORS } from "../theme/tokens";
import type {
  Account,
  AccountGroup,
  AccountStatus,
  AuthStatus,
  DataSyncStatus,
  GroupDimension,
  IntegrationStatus,
  Platform,
  PlatformIntegration,
  PlatformIntegrationStatus,
  PlatformMatrixSummary,
  Project,
  UpdatePlatformIntegrationInput,
} from "../types";

const PLATFORM_OPTIONS: { label: string; value: Platform }[] = [
  { label: "抖音", value: "douyin" },
  { label: "小红书", value: "xiaohongshu" },
  { label: "视频号", value: "shipinhao" },
];

const DIMENSION_LABEL: Record<GroupDimension, string> = {
  track: "赛道",
  persona: "人设",
  platform: "平台",
};

const STATUS_LABEL: Record<AccountStatus, string> = {
  active: "正常",
  inactive: "停用",
  banned: "封禁",
};

const INTEGRATION_LABEL: Record<IntegrationStatus, string> = {
  oauth_ready: "待授权",
  connected: "已接入",
  manual: "半自动",
  disabled: "停用",
};

const AUTH_LABEL: Record<AuthStatus, string> = {
  unauthorized: "未授权",
  authorized: "有效",
  expired: "过期",
  manual: "半自动",
};

const SYNC_LABEL: Record<DataSyncStatus, string> = {
  not_configured: "未配置",
  pending: "待回流",
  syncing: "同步中",
  healthy: "正常",
  failed: "失败",
  manual: "半自动",
};

const PLATFORM_INTEGRATION_LABEL: Record<PlatformIntegrationStatus, string> = {
  not_configured: "未配置",
  configured: "已配置",
  pending_review: "待审核",
  connected: "已接通",
  disabled: "停用",
};

const DEFAULT_DOUYIN_SECRET_REF = "vault://dyflow/douyin/client-secret";

const PLATFORM_DEFAULT_STATUS: Record<Platform, PlatformMatrixSummary> = {
  douyin: {
    platform: "douyin",
    total: 0,
    active: 0,
    integration_status: "oauth_ready",
    auth_status: "unauthorized",
    data_sync_status: "not_configured",
  },
  xiaohongshu: {
    platform: "xiaohongshu",
    total: 0,
    active: 0,
    integration_status: "manual",
    auth_status: "manual",
    data_sync_status: "manual",
  },
  shipinhao: {
    platform: "shipinhao",
    total: 0,
    active: 0,
    integration_status: "manual",
    auth_status: "manual",
    data_sync_status: "manual",
  },
};

interface AccountFormValues {
  nickname?: string;
  platform: Platform;
  group_id?: number;
  project_id?: number;
  external_account_id?: string;
}

interface PlatformIntegrationFormValues {
  status: PlatformIntegrationStatus;
  client_key?: string;
  client_secret_ref?: string;
  redirect_uri?: string;
  js_sdk_domain?: string;
  scopes?: string[];
  note?: string;
}

interface MatrixProjectSection {
  id: number | null;
  name: string;
  groups: MatrixGroupNode[];
}

interface MatrixGroupNode {
  id: number | null;
  name: string;
  dimension: GroupDimension | "ungrouped";
  platforms: {
    platform: Platform;
    accounts: Account[];
  }[];
}

export function buildMatrixSections(
  accounts: Account[],
  projects: Project[],
  groups: AccountGroup[],
): MatrixProjectSection[] {
  const projectName = new Map(projects.map((project) => [project.id, project.name]));
  const groupById = new Map(groups.map((group) => [group.id, group]));
  const projectBuckets = new Map<number | null, Account[]>();

  accounts.forEach((account) => {
    const key = account.project_id ?? null;
    projectBuckets.set(key, [...(projectBuckets.get(key) ?? []), account]);
  });

  return Array.from(projectBuckets.entries()).map(([projectId, projectAccounts]) => ({
    id: projectId,
    name: projectId == null ? "未绑定项目" : projectName.get(projectId) ?? `项目 #${projectId}`,
    groups: buildGroupNodes(projectAccounts, groupById),
  }));
}

export function platformSummaryByKey(rows: PlatformMatrixSummary[]) {
  return new Map(rows.map((row) => [row.platform, row]));
}

export function platformIntegrationByKey(rows: PlatformIntegration[]) {
  return new Map(rows.map((row) => [row.platform, row]));
}

export default function Accounts() {
  const { message } = AntApp.useApp();
  const qc = useQueryClient();
  const isAdmin = useAuth((s) => s.user?.role === "admin");

  const [projectId, setProjectId] = useState<number | null>(null);
  const [dimension, setDimension] = useState<GroupDimension | "all">("all");
  const [platform, setPlatform] = useState<Platform | "all">("all");
  const [groupId, setGroupId] = useState<number | null>(null);
  const [selected, setSelected] = useState<number[]>([]);
  const [modalOpen, setModalOpen] = useState(false);
  const [integrationPlatform, setIntegrationPlatform] = useState<Platform | null>(null);
  const [whitelistUrl, setWhitelistUrl] = useState<string | null>(null);
  const [form] = Form.useForm<AccountFormValues>();
  const [integrationForm] = Form.useForm<PlatformIntegrationFormValues>();
  const accountModalPlatform = Form.useWatch("platform", form);
  const isDouyinScanAdd = accountModalPlatform === "douyin";

  const projectsQuery = useQuery({ queryKey: ["projects"], queryFn: listProjects });
  const groupsQuery = useQuery({ queryKey: ["account-groups"], queryFn: listAccountGroups });
  const accountsQuery = useQuery({
    queryKey: ["accounts", { projectId }],
    queryFn: () => listAccounts({ projectId: projectId ?? undefined }),
  });
  const matrixQuery = useQuery({
    queryKey: ["account-matrix", { projectId }],
    queryFn: () => getAccountMatrix(projectId ?? undefined),
  });
  const platformIntegrationsQuery = useQuery({
    queryKey: ["platform-integrations"],
    queryFn: listPlatformIntegrations,
  });

  const groupName = useMemo(() => {
    const map = new Map<number, string>();
    (groupsQuery.data ?? []).forEach((g) => map.set(g.id, g.name));
    return map;
  }, [groupsQuery.data]);
  const groupById = useMemo(() => {
    const map = new Map<number, GroupDimension>();
    (groupsQuery.data ?? []).forEach((g) => map.set(g.id, g.dimension));
    return map;
  }, [groupsQuery.data]);
  const projectName = useMemo(() => {
    const map = new Map<number, string>();
    (projectsQuery.data ?? []).forEach((project) => map.set(project.id, project.name));
    return map;
  }, [projectsQuery.data]);

  const rows = useMemo(() => {
    const all = accountsQuery.data ?? [];
    return all.filter((account) => {
      if (groupId != null && account.group_id !== groupId) return false;
      if (platform !== "all" && account.platform !== platform) return false;
      if (dimension !== "all") {
        const accountDimension =
          account.group_id == null ? null : groupById.get(account.group_id) ?? null;
        if (accountDimension !== dimension) return false;
      }
      return true;
    });
  }, [accountsQuery.data, dimension, groupById, groupId, platform]);

  const matrixSections = useMemo(
    () => buildMatrixSections(rows, projectsQuery.data ?? [], groupsQuery.data ?? []),
    [groupsQuery.data, projectsQuery.data, rows],
  );
  const platformSummaries = useMemo(
    () => platformSummaryByKey(matrixQuery.data?.platforms ?? []),
    [matrixQuery.data?.platforms],
  );
  const platformIntegrations = useMemo(
    () => platformIntegrationByKey(platformIntegrationsQuery.data ?? []),
    [platformIntegrationsQuery.data],
  );

  useEffect(() => {
    setSelected((prev) => {
      const visibleIds = new Set(rows.map((account) => account.id));
      const next = prev.filter((id) => visibleIds.has(id));
      return next.length === prev.length ? prev : next;
    });
  }, [rows]);

  const openAccountModal = (initialPlatform?: Platform) => {
    form.resetFields();
    if (initialPlatform) form.setFieldsValue({ platform: initialPlatform });
    setModalOpen(true);
  };

  const openIntegrationModal = (target: Platform) => {
    const integration = platformIntegrations.get(target);
    integrationForm.setFieldsValue({
      status: integration?.status ?? "not_configured",
      client_key: integration?.client_key ?? undefined,
      client_secret_ref:
        integration?.client_secret_configured || target !== "douyin"
          ? undefined
          : DEFAULT_DOUYIN_SECRET_REF,
      redirect_uri: integration?.redirect_uri ?? undefined,
      js_sdk_domain: integration?.js_sdk_domain ?? undefined,
      scopes: integration?.scopes ?? [],
      note: integration?.note ?? undefined,
    });
    setIntegrationPlatform(target);
  };

  const createMutation = useMutation({
    mutationFn: createAccount,
    onSuccess: () => {
      message.success("账号已加入矩阵");
      setModalOpen(false);
      form.resetFields();
      qc.invalidateQueries({ queryKey: ["accounts"] });
      qc.invalidateQueries({ queryKey: ["account-matrix"] });
    },
    onError: () => message.error("账号接入失败，请重试"),
  });

  const douyinScanAddMutation = useMutation({
    mutationFn: createDouyinScanAddUrl,
    onSuccess: (result) => {
      window.open(result.authorization_url, "_blank", "noopener,noreferrer");
      message.success("已打开抖音扫码授权页");
      setModalOpen(false);
      form.resetFields();
    },
    onError: () => message.error("扫码添加链接生成失败，请先检查抖音平台配置"),
  });

  const integrationMutation = useMutation({
    mutationFn: ({
      id,
      patch,
    }: {
      id: number;
      patch: Parameters<typeof updateAccountIntegration>[1];
    }) => updateAccountIntegration(id, patch),
    onSuccess: () => {
      message.success("接入状态已更新");
      qc.invalidateQueries({ queryKey: ["accounts"] });
      qc.invalidateQueries({ queryKey: ["account-matrix"] });
    },
    onError: () => message.error("状态更新失败，请重试"),
  });

  const platformIntegrationMutation = useMutation({
    mutationFn: ({
      target,
      patch,
    }: {
      target: Platform;
      patch: UpdatePlatformIntegrationInput;
    }) => updatePlatformIntegration(target, patch),
    onSuccess: () => {
      message.success("平台接入配置已保存");
      setIntegrationPlatform(null);
      integrationForm.resetFields();
      qc.invalidateQueries({ queryKey: ["platform-integrations"] });
    },
    onError: () => message.error("平台接入配置保存失败，请检查必填项"),
  });

  const douyinAuthorizeMutation = useMutation({
    mutationFn: createDouyinAuthorizeUrl,
    onSuccess: (result) => {
      window.open(result.authorization_url, "_blank", "noopener,noreferrer");
      message.success("已打开抖音官方授权页");
    },
    onError: () => message.error("授权链接生成失败，请先检查平台接入配置"),
  });

  const douyinSyncMutation = useMutation({
    mutationFn: syncDouyinAccountMetrics,
    onSuccess: (result) => {
      message.success(`抖音数据已同步：${result.snapshot_count} 条作品快照`);
      qc.invalidateQueries({ queryKey: ["accounts"] });
      qc.invalidateQueries({ queryKey: ["account-matrix"] });
      qc.invalidateQueries({ queryKey: ["platform-integrations"] });
    },
    onError: () => message.error("抖音数据同步失败，请检查账号授权 token 和开放平台数据权限"),
  });

  const douyinWhitelistMutation = useMutation({
    mutationFn: createDouyinTrialWhitelistUrl,
    onSuccess: (result) => {
      setWhitelistUrl(result.authorization_url);
      message.success("白名单授权二维码已生成");
    },
    onError: () => message.error("白名单授权二维码生成失败，请检查抖音平台配置"),
  });

  const columns: ColumnsType<Account> = [
    {
      title: "账号",
      dataIndex: "nickname",
      render: (value: string, account) => (
        <div style={{ display: "grid", gap: 3 }}>
          <span style={{ fontWeight: 600, color: "var(--dy-text)" }}>{value}</span>
          <span className="dy-tabular" style={{ fontSize: 12, color: "var(--dy-faint)" }}>
            {account.external_account_id ?? "未绑定外部 ID"}
          </span>
        </div>
      ),
    },
    {
      title: "平台",
      dataIndex: "platform",
      width: 96,
      render: (value: Platform) => <PlatformBadge platform={value} />,
    },
    {
      title: "归属",
      width: 210,
      render: (_, account) => (
        <div style={{ display: "grid", gap: 5 }}>
          <span style={{ fontSize: 12.5, color: "var(--dy-text)" }}>
            {account.project_id == null ? "未绑定项目" : projectName.get(account.project_id) ?? `项目 #${account.project_id}`}
          </span>
          <span style={{ fontSize: 12, color: "var(--dy-faint)" }}>
            {account.group_id == null ? "未分组" : groupName.get(account.group_id) ?? `分组 #${account.group_id}`}
          </span>
        </div>
      ),
    },
    {
      title: "账号状态",
      dataIndex: "status",
      width: 104,
      render: (value: AccountStatus) => <StatePill label={STATUS_LABEL[value]} tone={accountStatusTone(value)} />,
    },
    {
      title: "接入",
      dataIndex: "integration_status",
      width: 104,
      render: (value: IntegrationStatus) => (
        <StatePill label={INTEGRATION_LABEL[value]} tone={integrationTone(value)} />
      ),
    },
    {
      title: "授权",
      dataIndex: "auth_status",
      width: 96,
      render: (value: AuthStatus) => <StatePill label={AUTH_LABEL[value]} tone={authTone(value)} />,
    },
    {
      title: "数据回流",
      dataIndex: "data_sync_status",
      width: 116,
      render: (value: DataSyncStatus) => <StatePill label={SYNC_LABEL[value]} tone={syncTone(value)} />,
    },
    {
      title: "动作",
      width: 168,
      render: (_, account) =>
        isAdmin ? (
          <AccountActions
            account={account}
            loading={integrationMutation.isPending}
            authorizeLoading={douyinAuthorizeMutation.isPending}
            syncLoading={douyinSyncMutation.isPending}
            onOfficialAuthorize={() => douyinAuthorizeMutation.mutate(account.id)}
            onSyncMetrics={() => douyinSyncMutation.mutate(account.id)}
            onMarkAuthorized={() =>
              integrationMutation.mutate({
                id: account.id,
                patch: {
                  integration_status: "connected",
                  auth_status: "authorized",
                  data_sync_status: "pending",
                  note: "手动标记授权完成",
                },
              })
            }
            onMarkHealthy={() =>
              integrationMutation.mutate({
                id: account.id,
                patch: {
                  data_sync_status: "healthy",
                  note: "手动标记数据回流正常",
                },
              })
            }
          />
        ) : (
          <span style={{ color: "var(--dy-faint)" }}>只读</span>
        ),
    },
  ];

  const loading =
    accountsQuery.isLoading ||
    matrixQuery.isLoading ||
    groupsQuery.isLoading ||
    projectsQuery.isLoading;

  return (
    <div>
      <PageHeader
        title="账号矩阵"
        subtitle="平台接入、账号归属、授权状态与数据回流的统一控制台"
        extra={
          isAdmin && (
            <Button type="primary" icon={<PlusOutlined />} onClick={() => openAccountModal()}>
              接入账号
            </Button>
          )
        }
      />

      <PlatformAccessBoard
        summaries={platformSummaries}
        integrations={platformIntegrations}
        isAdmin={isAdmin}
        onAddAccount={openAccountModal}
        onConfigure={openIntegrationModal}
        onWhitelist={() => douyinWhitelistMutation.mutate()}
        whitelistLoading={douyinWhitelistMutation.isPending}
      />

      <Panel style={{ marginBottom: 16 }}>
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))",
            gap: 12,
          }}
        >
          <FilterField label="项目 / 品牌">
            <Select
              allowClear
              loading={projectsQuery.isLoading}
              placeholder="全部项目"
              value={projectId ?? undefined}
              onChange={(value) => setProjectId(value ?? null)}
              options={(projectsQuery.data ?? []).map((project) => ({
                label: project.name,
                value: project.id,
              }))}
            />
          </FilterField>
          <FilterField label="分组维度">
            <Select
              value={dimension}
              onChange={(value) => setDimension(value)}
              options={[
                { label: "全部维度", value: "all" },
                { label: "赛道", value: "track" },
                { label: "人设", value: "persona" },
                { label: "平台组", value: "platform" },
              ]}
            />
          </FilterField>
          <FilterField label="平台">
            <Select
              value={platform}
              onChange={(value) => setPlatform(value)}
              options={[{ label: "全部平台", value: "all" }, ...PLATFORM_OPTIONS]}
            />
          </FilterField>
          <FilterField label="账号组">
            <Select
              allowClear
              loading={groupsQuery.isLoading}
              placeholder="全部账号组"
              value={groupId ?? undefined}
              onChange={(value) => setGroupId(value ?? null)}
              options={(groupsQuery.data ?? []).map((group) => ({
                label: `${group.name} · ${DIMENSION_LABEL[group.dimension]}`,
                value: group.id,
              }))}
            />
          </FilterField>
        </div>
      </Panel>

      <MatrixBoard sections={matrixSections} loading={loading} />

      <Panel
        title={`账号明细 · ${rows.length}`}
        extra={
          selected.length > 0 ? (
            <span style={{ fontSize: 12, color: "var(--dy-muted)" }}>
              已选 {selected.length} 个账号
            </span>
          ) : null
        }
      >
        <Table
          rowKey="id"
          columns={columns}
          dataSource={rows}
          loading={accountsQuery.isLoading}
          pagination={{ pageSize: 12, showSizeChanger: false }}
          rowSelection={{
            selectedRowKeys: selected,
            onChange: (keys) => setSelected(keys as number[]),
          }}
        />
      </Panel>

      <Modal
        title={isDouyinScanAdd ? "抖音扫码添加账号" : "接入矩阵账号"}
        open={modalOpen}
        onCancel={() => setModalOpen(false)}
        onOk={() => form.submit()}
        confirmLoading={createMutation.isPending || douyinScanAddMutation.isPending}
        okText={isDouyinScanAdd ? "打开抖音扫码" : "加入矩阵"}
        destroyOnHidden
      >
        <Form
          form={form}
          layout="vertical"
          requiredMark={false}
          onFinish={(value) => {
            if (value.platform === "douyin") {
              douyinScanAddMutation.mutate({
                nickname: blankToNull(value.nickname),
                group_id: value.group_id ?? null,
                project_id: value.project_id ?? null,
              });
              return;
            }
            const nickname = blankToNull(value.nickname);
            if (!nickname) {
              message.error("请输入账号昵称");
              return;
            }
            createMutation.mutate({
              nickname,
              platform: value.platform,
              group_id: value.group_id ?? null,
              project_id: value.project_id ?? null,
              external_account_id: blankToNull(value.external_account_id),
            });
          }}
        >
          <Form.Item
            name="nickname"
            label={isDouyinScanAdd ? "账号昵称（可选）" : "账号昵称"}
            rules={[
              {
                validator: async (_, value) => {
                  if (form.getFieldValue("platform") === "douyin" || value?.trim()) return;
                  throw new Error("请输入账号昵称");
                },
              },
            ]}
          >
            <Input
              placeholder={isDouyinScanAdd ? "扫码成功后可自动创建，也可先备注昵称" : "例如：数码菌"}
              maxLength={200}
            />
          </Form.Item>
          <Form.Item
            name="platform"
            label="平台"
            rules={[{ required: true, message: "请选择平台" }]}
          >
            <Select options={PLATFORM_OPTIONS} placeholder="选择平台" />
          </Form.Item>
          {!isDouyinScanAdd && (
            <Form.Item name="external_account_id" label="外部账号 ID">
              <Input placeholder="平台侧账号 ID，授权完成后可自动回填" maxLength={128} />
            </Form.Item>
          )}
          <Form.Item name="project_id" label="项目 / 品牌">
            <Select
              allowClear
              loading={projectsQuery.isLoading}
              placeholder="绑定项目（可选）"
              options={(projectsQuery.data ?? []).map((project) => ({
                label: project.name,
                value: project.id,
              }))}
            />
          </Form.Item>
          <Form.Item name="group_id" label="账号组">
            <GroupSelect />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title={
          integrationPlatform == null
            ? "平台接入配置"
            : `${platformLabel(integrationPlatform)}接入配置`
        }
        open={integrationPlatform != null}
        onCancel={() => setIntegrationPlatform(null)}
        onOk={() => integrationForm.submit()}
        confirmLoading={platformIntegrationMutation.isPending}
        okText="保存配置"
        destroyOnHidden
      >
        <Form
          form={integrationForm}
          layout="vertical"
          requiredMark={false}
          onFinish={(value) => {
            if (integrationPlatform == null) return;
            platformIntegrationMutation.mutate({
              target: integrationPlatform,
              patch: normalizePlatformIntegrationPatch(
                value,
                platformIntegrations.get(integrationPlatform),
              ),
            });
          }}
        >
          <Form.Item name="status" label="接入状态" initialValue="not_configured">
            <Select
              options={[
                { label: "未配置", value: "not_configured" },
                { label: "已配置", value: "configured" },
                { label: "待平台审核", value: "pending_review" },
                { label: "已接通", value: "connected" },
                { label: "停用", value: "disabled" },
              ]}
            />
          </Form.Item>
          <Form.Item name="client_key" label="ClientKey">
            <Input placeholder="开放平台应用 ClientKey" maxLength={128} autoComplete="off" />
          </Form.Item>
          <Form.Item
            name="client_secret_ref"
            label="ClientSecret"
            extra="只在保存时提交；接口不会回显 Secret，留空表示不修改已保存的 Secret。"
          >
            <Input.Password
              placeholder="输入新的 ClientSecret 或密钥存储引用"
              maxLength={256}
              autoComplete="new-password"
            />
          </Form.Item>
          <Form.Item name="redirect_uri" label="授权回调地址">
            <Input placeholder="https://example.com/douyin/callback" maxLength={500} />
          </Form.Item>
          <Form.Item name="js_sdk_domain" label="JS SDK / H5 安全域名">
            <Input placeholder="https://example.com" maxLength={500} />
          </Form.Item>
          <Form.Item name="scopes" label="已申请权限">
            <Select
              mode="tags"
              tokenSeparators={[",", "，", " "]}
              placeholder="例如 user_info、video.list"
              options={[
                { label: "user_info", value: "user_info" },
                { label: "video.list", value: "video.list" },
                { label: "fans.data", value: "fans.data" },
                { label: "h5.share", value: "h5.share" },
              ]}
            />
          </Form.Item>
          <Form.Item name="note" label="备注">
            <Input.TextArea rows={3} maxLength={1000} placeholder="平台审核、权限申请、回调域配置说明" />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="抖音测试白名单授权"
        open={whitelistUrl != null}
        onCancel={() => setWhitelistUrl(null)}
        footer={[
          <Button key="close" onClick={() => setWhitelistUrl(null)}>
            关闭
          </Button>,
          <Button
            key="copy"
            icon={<LinkOutlined />}
            onClick={() => {
              if (!whitelistUrl) return;
              void navigator.clipboard?.writeText(whitelistUrl);
              message.success("授权链接已复制");
            }}
          >
            复制链接
          </Button>,
        ]}
      >
        {whitelistUrl && (
          <div style={{ display: "grid", justifyItems: "center", gap: 14 }}>
            <QRCode value={whitelistUrl} size={220} />
            <div
              style={{
                width: "100%",
                fontSize: 12,
                color: "var(--dy-faint)",
                wordBreak: "break-all",
                lineHeight: 1.6,
              }}
            >
              {whitelistUrl}
            </div>
          </div>
        )}
      </Modal>
    </div>
  );
}

function PlatformAccessBoard({
  summaries,
  integrations,
  isAdmin,
  onAddAccount,
  onConfigure,
  onWhitelist,
  whitelistLoading,
}: {
  summaries: Map<Platform, PlatformMatrixSummary>;
  integrations: Map<Platform, PlatformIntegration>;
  isAdmin: boolean;
  onAddAccount: (platform: Platform) => void;
  onConfigure: (platform: Platform) => void;
  onWhitelist: () => void;
  whitelistLoading: boolean;
}) {
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))",
        gap: 12,
        marginBottom: 16,
      }}
    >
      {PLATFORM_OPTIONS.map((option) => {
        const summary = summaries.get(option.value) ?? PLATFORM_DEFAULT_STATUS[option.value];
        const integration = integrations.get(option.value);
        return (
          <Panel key={option.value}>
            <div style={{ display: "grid", gap: 14 }}>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12 }}>
                <PlatformBadge platform={option.value} />
                <span className="dy-tabular" style={{ fontSize: 22, fontWeight: 650, color: "var(--dy-text)" }}>
                  {summary.total}
                </span>
              </div>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 8 }}>
                <Signal label="接入" value={INTEGRATION_LABEL[summary.integration_status]} tone={integrationTone(summary.integration_status)} />
                <Signal label="授权" value={AUTH_LABEL[summary.auth_status]} tone={authTone(summary.auth_status)} />
                <Signal label="回流" value={SYNC_LABEL[summary.data_sync_status]} tone={syncTone(summary.data_sync_status)} />
              </div>
              <div
                style={{
                  display: "grid",
                  gap: 5,
                  paddingTop: 2,
                  borderTop: "1px solid var(--dy-border-subtle)",
                }}
              >
                <ConfigLine
                  label="应用"
                  value={
                    PLATFORM_INTEGRATION_LABEL[integration?.status ?? "not_configured"]
                  }
                  tone={platformIntegrationTone(integration?.status ?? "not_configured")}
                />
                <ConfigLine
                  label="密钥"
                  value={integration?.client_secret_configured ? "已保存" : "未保存"}
                  tone={integration?.client_secret_configured ? "success" : "warning"}
                />
                <span
                  className="dy-tabular"
                  style={{
                    minWidth: 0,
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                    fontSize: 12,
                    color: "var(--dy-faint)",
                  }}
                  title={integration?.client_key ?? "未配置 ClientKey"}
                >
                  {integration?.client_key ?? "未配置 ClientKey"}
                </span>
              </div>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10 }}>
                <span style={{ fontSize: 12, color: "var(--dy-faint)" }}>
                  正常 {summary.active} / 总计 {summary.total}
                </span>
                {isAdmin && (
                  <div style={{ display: "flex", gap: 6 }}>
                    {option.value === "douyin" && (
                      <Button
                        size="small"
                        icon={<QrcodeOutlined />}
                        loading={whitelistLoading}
                        onClick={onWhitelist}
                      >
                        白名单
                      </Button>
                    )}
                    <Button
                      size="small"
                      icon={<SettingOutlined />}
                      onClick={() => onConfigure(option.value)}
                    >
                      配置
                    </Button>
                    <Button size="small" icon={<PlusOutlined />} onClick={() => onAddAccount(option.value)}>
                      账号
                    </Button>
                  </div>
                )}
              </div>
            </div>
          </Panel>
        );
      })}
    </div>
  );
}

function ConfigLine({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone: "success" | "warning" | "error" | "muted";
}) {
  return (
    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
      <span style={{ fontSize: 12, color: "var(--dy-faint)" }}>{label}</span>
      <StatePill label={value} tone={tone} />
    </div>
  );
}

function MatrixBoard({
  sections,
  loading,
}: {
  sections: MatrixProjectSection[];
  loading: boolean;
}) {
  if (loading) {
    return (
      <Panel style={{ marginBottom: 16 }}>
        <div aria-hidden style={{ height: 130, borderRadius: 10, background: "var(--dy-elevated)", opacity: 0.72 }} />
      </Panel>
    );
  }

  return (
    <Panel title="矩阵结构" style={{ marginBottom: 16 }}>
      {sections.length === 0 ? (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前筛选下暂无账号" />
      ) : (
        <div style={{ display: "grid", gap: 12 }}>
          {sections.map((section) => (
            <MatrixProject key={section.id ?? "unbound"} section={section} />
          ))}
        </div>
      )}
    </Panel>
  );
}

function MatrixProject({ section }: { section: MatrixProjectSection }) {
  const accountCount = section.groups.reduce(
    (sum, group) => sum + group.platforms.reduce((total, node) => total + node.accounts.length, 0),
    0,
  );

  return (
    <section style={{ display: "grid", gap: 10 }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
        <span style={{ fontSize: 13.5, fontWeight: 600, color: "var(--dy-text)" }}>{section.name}</span>
        <span className="dy-tabular" style={{ fontSize: 12, color: "var(--dy-faint)" }}>
          {accountCount} 个账号
        </span>
      </div>
      <div style={{ display: "grid", gap: 8 }}>
        {section.groups.map((group) => (
          <div
            key={group.id ?? "ungrouped"}
            style={{
              display: "grid",
              gridTemplateColumns: "minmax(120px, 168px) minmax(0, 1fr)",
              gap: 10,
              alignItems: "start",
            }}
          >
            <div style={{ minWidth: 0 }}>
              <div style={{ fontSize: 12.5, color: "var(--dy-text)", fontWeight: 500 }}>{group.name}</div>
              <div style={{ fontSize: 12, color: "var(--dy-faint)", marginTop: 2 }}>
                {group.dimension === "ungrouped" ? "未分组" : DIMENSION_LABEL[group.dimension]}
              </div>
            </div>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              {group.platforms.map((node) => (
                <div
                  key={node.platform}
                  style={{
                    minWidth: 176,
                    border: "1px solid var(--dy-border-subtle)",
                    borderRadius: 8,
                    padding: "8px 10px",
                    background: "var(--dy-elevated)",
                  }}
                >
                  <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
                    <PlatformBadge platform={node.platform} />
                    <span className="dy-tabular" style={{ fontSize: 12, color: "var(--dy-faint)" }}>
                      {node.accounts.length}
                    </span>
                  </div>
                  <div style={{ display: "grid", gap: 4, marginTop: 7 }}>
                    {node.accounts.slice(0, 3).map((account) => (
                      <span
                        key={account.id}
                        title={account.nickname}
                        style={{
                          minWidth: 0,
                          overflow: "hidden",
                          textOverflow: "ellipsis",
                          whiteSpace: "nowrap",
                          fontSize: 12.5,
                          color: "var(--dy-muted)",
                        }}
                      >
                        {account.nickname}
                      </span>
                    ))}
                    {node.accounts.length > 3 && (
                      <span style={{ fontSize: 12, color: "var(--dy-faint)" }}>
                        +{node.accounts.length - 3} 个
                      </span>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

function AccountActions({
  account,
  loading,
  authorizeLoading,
  syncLoading,
  onOfficialAuthorize,
  onSyncMetrics,
  onMarkAuthorized,
  onMarkHealthy,
}: {
  account: Account;
  loading: boolean;
  authorizeLoading: boolean;
  syncLoading: boolean;
  onOfficialAuthorize: () => void;
  onSyncMetrics: () => void;
  onMarkAuthorized: () => void;
  onMarkHealthy: () => void;
}) {
  if (account.auth_status !== "authorized") {
    if (account.platform === "douyin") {
      return (
        <Space size={6} wrap>
          <Button size="small" type="primary" loading={authorizeLoading} onClick={onOfficialAuthorize}>
            官方授权
          </Button>
          <Button size="small" loading={loading} onClick={onMarkAuthorized}>
            手动标记
          </Button>
        </Space>
      );
    }
    return (
      <Button size="small" loading={loading} onClick={onMarkAuthorized}>
        标记授权
      </Button>
    );
  }
  if (account.platform === "douyin") {
    return (
      <Space size={6} wrap>
        <Button size="small" icon={<SyncOutlined />} loading={syncLoading} onClick={onSyncMetrics}>
          同步数据
        </Button>
        {account.data_sync_status !== "healthy" ? (
          <Button size="small" loading={loading} onClick={onMarkHealthy}>
            手动标记
          </Button>
        ) : null}
      </Space>
    );
  }
  if (account.data_sync_status !== "healthy") {
    return (
      <Button size="small" icon={<SyncOutlined />} loading={loading} onClick={onMarkHealthy}>
        回流正常
      </Button>
    );
  }
  return <span style={{ fontSize: 12, color: "var(--dy-faint)" }}>已就绪</span>;
}

function FilterField({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label style={{ display: "grid", gap: 6, minWidth: 0 }}>
      <span style={{ fontSize: 12, color: "var(--dy-faint)" }}>{label}</span>
      {children}
    </label>
  );
}

function Signal({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone: "success" | "warning" | "error" | "muted";
}) {
  return (
    <div style={{ display: "grid", gap: 4, minWidth: 0 }}>
      <span style={{ fontSize: 11.5, color: "var(--dy-faint)" }}>{label}</span>
      <StatePill label={value} tone={tone} />
    </div>
  );
}

function StatePill({
  label,
  tone,
}: {
  label: string;
  tone: "success" | "warning" | "error" | "muted";
}) {
  const color = {
    success: "var(--dy-success)",
    warning: "var(--dy-warning)",
    error: "var(--dy-error)",
    muted: "var(--dy-muted)",
  }[tone];
  const icon = {
    success: <CheckCircleFilled />,
    warning: <ClockCircleFilled />,
    error: <ExclamationCircleFilled />,
    muted: <span style={{ width: 6, height: 6, borderRadius: "50%", background: color }} />,
  }[tone];
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 5, color, fontSize: 12 }}>
      <span style={{ display: "inline-flex", fontSize: 11 }}>{icon}</span>
      {label}
    </span>
  );
}

function PlatformBadge({ platform }: { platform: Platform }) {
  return (
    <Tag
      style={{
        marginInlineEnd: 0,
        color: PLATFORM_COLORS[platform],
        borderColor: "var(--dy-border)",
        background: "transparent",
      }}
    >
      {platformLabel(platform)}
    </Tag>
  );
}

function buildGroupNodes(
  accounts: Account[],
  groupById: Map<number, AccountGroup>,
): MatrixGroupNode[] {
  const buckets = new Map<number | null, Account[]>();
  accounts.forEach((account) => {
    const key = account.group_id ?? null;
    buckets.set(key, [...(buckets.get(key) ?? []), account]);
  });

  return Array.from(buckets.entries()).map(([groupId, groupAccounts]) => {
    const group = groupId == null ? null : groupById.get(groupId) ?? null;
    return {
      id: groupId,
      name: group?.name ?? "未分组账号",
      dimension: group?.dimension ?? "ungrouped",
      platforms: PLATFORM_OPTIONS.map((option) => ({
        platform: option.value,
        accounts: groupAccounts.filter((account) => account.platform === option.value),
      })).filter((node) => node.accounts.length > 0),
    };
  });
}

function accountStatusTone(value: AccountStatus) {
  if (value === "active") return "success";
  if (value === "banned") return "error";
  return "muted";
}

function integrationTone(value: IntegrationStatus) {
  if (value === "connected") return "success";
  if (value === "oauth_ready") return "warning";
  if (value === "disabled") return "error";
  return "muted";
}

function authTone(value: AuthStatus) {
  if (value === "authorized") return "success";
  if (value === "expired") return "error";
  if (value === "unauthorized") return "warning";
  return "muted";
}

function syncTone(value: DataSyncStatus) {
  if (value === "healthy") return "success";
  if (value === "pending" || value === "syncing") return "warning";
  if (value === "failed") return "error";
  return "muted";
}

function platformIntegrationTone(value: PlatformIntegrationStatus) {
  if (value === "connected" || value === "configured") return "success";
  if (value === "pending_review" || value === "not_configured") return "warning";
  if (value === "disabled") return "error";
  return "muted";
}

export function normalizePlatformIntegrationPatch(
  value: PlatformIntegrationFormValues,
  current?: PlatformIntegration,
): UpdatePlatformIntegrationInput {
  const clientKey = blankToNull(value.client_key);
  const hasSecret = Boolean(value.client_secret_ref?.trim() || current?.client_secret_configured);
  const status =
    value.status === "not_configured" && clientKey && hasSecret ? "configured" : value.status;

  return {
    status,
    client_key: clientKey,
    ...(value.client_secret_ref?.trim()
      ? { client_secret_ref: value.client_secret_ref.trim() }
      : {}),
    redirect_uri: blankToNull(value.redirect_uri),
    js_sdk_domain: blankToNull(value.js_sdk_domain),
    scopes: (value.scopes ?? []).map((scope) => scope.trim()).filter(Boolean),
    note: blankToNull(value.note),
  };
}

function blankToNull(value?: string) {
  const next = value?.trim();
  return next ? next : null;
}

function platformLabel(platform: Platform) {
  return PLATFORM_OPTIONS.find((option) => option.value === platform)?.label ?? platform;
}

/** 分组下拉：复用查询缓存，并支持就地创建新分组。 */
function GroupSelect(props: { value?: number; onChange?: (value: number | undefined) => void }) {
  const { message } = AntApp.useApp();
  const qc = useQueryClient();
  const groupsQuery = useQuery({ queryKey: ["account-groups"], queryFn: listAccountGroups });
  const [name, setName] = useState("");

  const createMutation = useMutation({
    mutationFn: () => createAccountGroup({ name: name.trim(), dimension: "track" }),
    onSuccess: (group) => {
      setName("");
      qc.invalidateQueries({ queryKey: ["account-groups"] });
      props.onChange?.(group.id);
      message.success("分组已创建");
    },
    onError: () => message.error("创建分组失败"),
  });

  return (
    <Select
      placeholder="选择分组（可选）"
      allowClear
      value={props.value}
      onChange={(value) => props.onChange?.(value)}
      loading={groupsQuery.isLoading}
      options={(groupsQuery.data ?? []).map((group) => ({
        label: `${group.name} · ${DIMENSION_LABEL[group.dimension]}`,
        value: group.id,
      }))}
      popupRender={(menu) => (
        <>
          {menu}
          <div style={{ display: "flex", gap: 6, padding: 8 }}>
            <Input
              size="small"
              placeholder="新建分组名"
              value={name}
              onChange={(event) => setName(event.target.value)}
              onPressEnter={(event) => {
                event.preventDefault();
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
