/* eslint-disable react-refresh/only-export-components -- tested matrix normalization helpers are colocated with the page */
import {
  AppstoreOutlined,
  ApartmentOutlined,
  CheckCircleFilled,
  CheckOutlined,
  ClockCircleFilled,
  DatabaseOutlined,
  DeleteOutlined,
  ExclamationCircleFilled,
  FolderOpenOutlined,
  LinkOutlined,
  PauseOutlined,
  PlusOutlined,
  QrcodeOutlined,
  SafetyCertificateOutlined,
  SettingOutlined,
  SyncOutlined,
  TableOutlined,
  TeamOutlined,
  UngroupOutlined,
} from "@ant-design/icons";
import {
  App as AntApp,
  Button,
  Empty,
  Form,
  Input,
  Modal,
  QRCode,
  Radio,
  Segmented,
  Space,
  Select,
  Table,
  Tag,
  Tooltip,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";

import {
  batchUpdateAccounts,
  createClient,
  createAccount,
  createAccountGroup,
  createProject,
  createDouyinAuthorizeUrl,
  createDouyinIncrementalAuthorizeUrl,
  createDouyinScanAddUrl,
  createDouyinTrialWhitelistUrl,
  deleteAccount,
  getAccountMatrix,
  getDouyinAccountCapabilities,
  listAccountGroups,
  listAccounts,
  listClients,
  listPlatformIntegrations,
  listProjects,
  replaceAccountAssignments,
  syncDouyinAccountMetrics,
  updateAccountIntegration,
  updateClient,
  updatePlatformIntegration,
  updateProject,
} from "../api/workspace";
import { presentApiError } from "../api/errors";
import {
  createWechatAuthorizationSession,
  getWechatAccountCapabilities,
  isOfficialWechatAuthorizationUrl,
} from "../services/wechatIntegration";
import { OperationalState, PageHeader, Panel } from "../components/ui";
import {
  AccountCardsView,
  AccountProjectsView,
  AccountSummaryStrip,
} from "../components/accounts/AccountViews";
import WorkspaceStructureManager from "../components/accounts/WorkspaceStructureManager";
import { getAccountActionMode } from "../components/accounts/accountActionMode";
import { useAuth } from "../stores/auth";
import {
  loadAccountMatrixPreferences,
  saveAccountMatrixPreferences,
  type AccountMatrixView,
} from "../stores/accountMatrixPreferences";
import { useCurrentWorkspace } from "../stores/currentWorkspace";
import { PLATFORM_COLORS } from "../theme/tokens";
import "../styles/accounts-v2.css";
import type {
  Account,
  AccountAssignmentsInput,
  AccountGroup,
  AccountStatus,
  AuthStatus,
  Client,
  DataSyncStatus,
  DouyinCapability,
  DouyinCapabilityKey,
  GroupDimension,
  IntegrationStatus,
  Platform,
  PlatformIntegration,
  PlatformIntegrationStatus,
  PlatformMatrixSummary,
  Project,
  UpdatePlatformIntegrationInput,
} from "../types";
import type {
  WechatCapabilityKey,
  WechatCapabilityState,
} from "../types/wechatArticle";

const PLATFORM_OPTIONS: { label: string; value: Platform }[] = [
  { label: "抖音", value: "douyin" },
  { label: "小红书", value: "xiaohongshu" },
  { label: "视频号", value: "shipinhao" },
  { label: "微信公众号", value: "wechat_official_account" },
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

const PUBLISH_CAPABILITY_LABEL = {
  prepare_only: "可准备发布包",
  manual_only: "人工发布",
  unavailable: "暂不可用",
} as const;

const DEFAULT_DOUYIN_SECRET_REF = "vault://dyflow/douyin/client-secret";
const DEFAULT_DOUYIN_REDIRECT_URI =
  "https://tzxai.top/platform-integrations/douyin/oauth/callback";
const DEFAULT_DOUYIN_JS_SDK_DOMAIN = "tzxai.top";

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
  wechat_official_account: {
    platform: "wechat_official_account",
    total: 0,
    active: 0,
    integration_status: "oauth_ready",
    auth_status: "unauthorized",
    data_sync_status: "not_configured",
  },
};

interface AccountFormValues {
  nickname?: string;
  platform: Platform;
  douyin_add_mode?: "scan" | "manual";
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

type AccountAssignmentFormValues = AccountAssignmentsInput;

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
  const navigate = useNavigate();
  const isAdmin = useAuth((s) => s.user?.role === "admin");
  const currentAccountId = useCurrentWorkspace((s) => s.accountId);
  const setCurrentPlatform = useCurrentWorkspace((s) => s.setPlatform);
  const setCurrentAccountId = useCurrentWorkspace((s) => s.setAccountId);

  const [initialPreferences] = useState(loadAccountMatrixPreferences);
  const [view, setView] = useState<AccountMatrixView>(initialPreferences.view);
  const [projectId, setProjectId] = useState<number | null>(initialPreferences.projectId);
  const [dimension, setDimension] = useState<GroupDimension | "all">(
    initialPreferences.dimension,
  );
  const [platform, setPlatform] = useState<Platform | "all">(initialPreferences.platform);
  const [groupId, setGroupId] = useState<number | null>(initialPreferences.groupId);
  const [selected, setSelected] = useState<number[]>([]);
  const [batchModal, setBatchModal] = useState<"project" | "group" | null>(null);
  const [batchValue, setBatchValue] = useState<number | null>(null);
  const [modalOpen, setModalOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<Account | null>(null);
  const [capabilityAccount, setCapabilityAccount] = useState<Account | null>(null);
  const [platformBoardOpen, setPlatformBoardOpen] = useState(false);
  const [workspaceManagerOpen, setWorkspaceManagerOpen] = useState(false);
  const [assignmentTarget, setAssignmentTarget] = useState<Account | null>(null);
  const [integrationPlatform, setIntegrationPlatform] = useState<Platform | null>(null);
  const [whitelistUrl, setWhitelistUrl] = useState<string | null>(null);
  const [form] = Form.useForm<AccountFormValues>();
  const [integrationForm] = Form.useForm<PlatformIntegrationFormValues>();
  const [assignmentForm] = Form.useForm<AccountAssignmentFormValues>();
  const accountModalPlatform = Form.useWatch("platform", form);
  const assignmentClientIds =
    Form.useWatch("client_ids", assignmentForm) ?? [];
  const assignmentProjectIds =
    Form.useWatch("project_ids", assignmentForm) ?? [];
  const douyinAddMode = Form.useWatch("douyin_add_mode", form) ?? "scan";
  const isDouyin = accountModalPlatform === "douyin";
  const isDouyinScanAdd = isDouyin && douyinAddMode === "scan";

  const clientsQuery = useQuery({ queryKey: ["clients"], queryFn: listClients });
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
    enabled: isAdmin,
  });
  const douyinCapabilitiesQuery = useQuery({
    queryKey: ["douyin-account-capabilities", capabilityAccount?.id],
    queryFn: () => getDouyinAccountCapabilities(capabilityAccount!.id),
    enabled: isAdmin && capabilityAccount?.platform === "douyin",
    retry: false,
  });
  const wechatCapabilitiesQuery = useQuery({
    queryKey: ["wechat-account-capabilities", capabilityAccount?.id],
    queryFn: () => getWechatAccountCapabilities(capabilityAccount!.id),
    enabled: isAdmin && capabilityAccount?.platform === "wechat_official_account",
    retry: false,
  });

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
  const clientName = useMemo(() => {
    const map = new Map<number, string>();
    (clientsQuery.data ?? []).forEach((client) => map.set(client.id, client.name));
    return map;
  }, [clientsQuery.data]);

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

  useEffect(() => {
    saveAccountMatrixPreferences({ view, projectId, dimension, platform, groupId });
  }, [dimension, groupId, platform, projectId, view]);

  const openAccountModal = (initialPlatform?: Platform) => {
    form.resetFields();
    form.setFieldsValue({
      platform: initialPlatform,
      douyin_add_mode: initialPlatform === "douyin" ? "scan" : undefined,
    });
    setModalOpen(true);
  };

  const openAssignmentModal = (account: Account) => {
    const clientIds = account.client_ids?.length
      ? account.client_ids
      : account.client_id != null
        ? [account.client_id]
        : [];
    const projectIds = account.project_ids?.length
      ? account.project_ids
      : account.project_id != null
        ? [account.project_id]
        : [];
    assignmentForm.setFieldsValue({
      client_ids: clientIds,
      project_ids: projectIds,
      default_client_id: account.client_id ?? clientIds[0],
      default_project_id: account.project_id ?? projectIds[0] ?? null,
    });
    setAssignmentTarget(account);
  };

  const selectWorkspaceAccount = (accountId: number) => {
    const account = rows.find((item) => item.id === accountId);
    if (!account) return;
    setCurrentPlatform(account.platform);
    setCurrentAccountId(account.id);
    message.success(`已切换到 ${account.nickname}`);
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
      redirect_uri:
        integration?.redirect_uri ??
        (target === "douyin" ? DEFAULT_DOUYIN_REDIRECT_URI : undefined),
      js_sdk_domain:
        integration?.js_sdk_domain ??
        (target === "douyin" ? DEFAULT_DOUYIN_JS_SDK_DOMAIN : undefined),
      scopes: integration?.scopes ?? [],
      note: integration?.note ?? undefined,
    });
    setIntegrationPlatform(target);
  };

  const openAccountDataCenter = (account: Account) => {
    setCurrentPlatform(account.platform);
    setCurrentAccountId(account.id);
    navigate(`/accounts/${account.id}/data`);
  };

  const createMutation = useMutation({
    mutationFn: createAccount,
    onSuccess: () => {
      message.success("账号已加入矩阵");
      setModalOpen(false);
      form.resetFields();
      qc.invalidateQueries({ queryKey: ["accounts"] });
      qc.invalidateQueries({ queryKey: ["shell-accounts"] });
      qc.invalidateQueries({ queryKey: ["account-matrix"] });
    },
    onError: () => message.error("账号接入失败，请重试"),
  });

  const createManualDouyinMutation = useMutation({
    mutationFn: async (value: AccountFormValues) => {
      const nickname = blankToNull(value.nickname) ?? "抖音开发测试账号";
      const account = await createAccount({
        nickname,
        platform: "douyin",
        group_id: value.group_id ?? null,
        project_id: value.project_id ?? null,
        external_account_id:
          blankToNull(value.external_account_id) ??
          `manual-douyin-${Date.now().toString(36)}`,
      });
      return updateAccountIntegration(account.id, {
        integration_status: "manual",
        auth_status: "manual",
        data_sync_status: "manual",
        note: "本地开发模式账号：用于运营大脑验收，不代表抖音官方 OAuth 已授权。",
      });
    },
    onSuccess: (account) => {
      setCurrentAccountId(account.id);
      message.success("已创建抖音开发模式账号，可用于运营大脑本地验收");
      setModalOpen(false);
      form.resetFields();
      qc.invalidateQueries({ queryKey: ["accounts"] });
      qc.invalidateQueries({ queryKey: ["shell-accounts"] });
      qc.invalidateQueries({ queryKey: ["account-matrix"] });
    },
    onError: () => message.error("开发模式账号创建失败，请重试"),
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

  const batchMutation = useMutation({
    mutationFn: (patch: {
      group_id?: number | null;
      project_id?: number | null;
      status?: AccountStatus;
    }) => batchUpdateAccounts({ account_ids: selected, ...patch }),
    onSuccess: (updated) => {
      message.success(`已更新 ${updated.length} 个账号`);
      setSelected([]);
      setBatchModal(null);
      setBatchValue(null);
      qc.invalidateQueries({ queryKey: ["accounts"] });
      qc.invalidateQueries({ queryKey: ["shell-accounts"] });
      qc.invalidateQueries({ queryKey: ["account-matrix"] });
    },
    onError: () => message.error("批量更新失败，请检查账号与项目归属"),
  });

  const refreshWorkspaceStructure = () => {
    void Promise.all([
      qc.invalidateQueries({ queryKey: ["clients"] }),
      qc.invalidateQueries({ queryKey: ["projects"] }),
      qc.invalidateQueries({ queryKey: ["accounts"] }),
      qc.invalidateQueries({ queryKey: ["shell-accounts"] }),
      qc.invalidateQueries({ queryKey: ["account-matrix"] }),
      qc.invalidateQueries({ queryKey: ["workspace-context"] }),
    ]);
  };

  const createClientMutation = useMutation({
    mutationFn: createClient,
    onSuccess: () => {
      message.success("客户已创建");
      refreshWorkspaceStructure();
    },
    onError: (error) => message.error(presentApiError(error, "客户创建失败").message),
  });

  const updateClientMutation = useMutation({
    mutationFn: ({ id, patch }: { id: number; patch: { name?: string; status?: Client["status"] } }) =>
      updateClient(id, patch),
    onSuccess: () => {
      message.success("客户已更新");
      refreshWorkspaceStructure();
    },
    onError: (error) => message.error(presentApiError(error, "客户更新失败").message),
  });

  const createProjectMutation = useMutation({
    mutationFn: createProject,
    onSuccess: () => {
      message.success("项目已创建");
      refreshWorkspaceStructure();
    },
    onError: (error) => message.error(presentApiError(error, "项目创建失败").message),
  });

  const updateProjectMutation = useMutation({
    mutationFn: ({
      id,
      patch,
    }: {
      id: number;
      patch: { name?: string; description?: string; status?: Project["status"] };
    }) => updateProject(id, patch),
    onSuccess: () => {
      message.success("项目已更新");
      refreshWorkspaceStructure();
    },
    onError: (error) => message.error(presentApiError(error, "项目更新失败").message),
  });

  const assignmentMutation = useMutation({
    mutationFn: ({
      accountId,
      input,
    }: {
      accountId: number;
      input: AccountAssignmentsInput;
    }) => replaceAccountAssignments(accountId, input),
    onSuccess: (account) => {
      message.success(`已更新 ${account.nickname} 的客户与项目归属`);
      setAssignmentTarget(null);
      assignmentForm.resetFields();
      refreshWorkspaceStructure();
    },
    onError: (error) =>
      message.error(presentApiError(error, "账号归属保存失败，请核对客户与项目关系").message),
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

  const wechatAuthorizeMutation = useMutation({
    mutationFn: () => createWechatAuthorizationSession({}),
    onSuccess: (result) => {
      if (!isOfficialWechatAuthorizationUrl(result.authorizationUrl)) {
        message.error("微信授权地址校验失败，请联系管理员检查开放平台组件配置");
        return;
      }
      window.open(result.authorizationUrl, "_blank", "noopener,noreferrer");
      message.success("已打开微信公众号官方授权页");
    },
    onError: (error) => {
      const status = (error as { response?: { status?: number } })?.response?.status;
      message.error(
        status === 503
          ? "微信开放平台组件尚未配置，请联系管理员配置 Component AppID、AppSecret 与授权事件接收地址"
          : "微信公众号授权暂时不可用，请稍后重试",
      );
    },
  });

  const douyinIncrementalAuthorizeMutation = useMutation({
    mutationFn: ({
      accountId,
      capabilityKey,
    }: {
      accountId: number;
      capabilityKey: DouyinCapabilityKey;
    }) => createDouyinIncrementalAuthorizeUrl(accountId, capabilityKey),
    onSuccess: (result) => {
      window.open(result.authorization_url, "_blank", "noopener,noreferrer");
      message.success("已打开抖音补充授权页面");
    },
    onError: (error) => {
      const failure = presentApiError(error, "补充授权链接生成失败");
      message.error(failure.message);
    },
  });

  const douyinSyncMutation = useMutation({
    mutationFn: syncDouyinAccountMetrics,
    onSuccess: (result) => {
      if (result.data_sync_status === "pending" && result.snapshot_count === 0) {
        message.success("抖音账号资料已同步；作品数据等待投稿任务权限");
      } else {
        message.success(`抖音数据已同步：${result.snapshot_count} 条作品快照`);
      }
      qc.invalidateQueries({ queryKey: ["accounts"] });
      qc.invalidateQueries({ queryKey: ["shell-accounts"] });
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

  const deleteMutation = useMutation({
    mutationFn: (account: Account) => deleteAccount(account.id),
    onSuccess: (_, account) => {
      if (currentAccountId === account.id) {
        setCurrentAccountId(null);
      }
      setSelected((ids) => ids.filter((id) => id !== account.id));
      setDeleteTarget(null);
      message.success(`已从账号矩阵删除 ${account.nickname}`);
      void Promise.all([
        qc.invalidateQueries({ queryKey: ["accounts"] }),
        qc.invalidateQueries({ queryKey: ["shell-accounts"] }),
        qc.invalidateQueries({ queryKey: ["account-matrix"] }),
        qc.invalidateQueries({ queryKey: ["platform-integrations"] }),
        qc.invalidateQueries({ queryKey: ["workspace-context"] }),
      ]);
    },
    onError: (error) => {
      const failure = presentApiError(error, "账号删除失败，请稍后重试。");
      message.error(failure.message);
    },
  });

  const columns: ColumnsType<Account> = [
    {
      title: "账号",
      dataIndex: "nickname",
      width: 280,
      render: (value: string, account) => (
        <div className="account-identity">
          {account.avatar_url ? (
            <img src={account.avatar_url} alt="" className="account-identity__avatar" />
          ) : (
            <span className="account-identity__avatar account-identity__avatar--fallback">
              {value.slice(0, 1)}
            </span>
          )}
          <div>
            <strong>{value}</strong>
            <span>{account.external_account_id ?? "未绑定平台 ID"}</span>
          </div>
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
      width: 220,
      render: (_, account) => {
        const clientIds = account.client_ids?.length
          ? account.client_ids
          : account.client_id != null
            ? [account.client_id]
            : [];
        const projectIds = account.project_ids?.length
          ? account.project_ids
          : account.project_id != null
            ? [account.project_id]
            : [];
        return (
          <div style={{ display: "grid", gap: 5, justifyItems: "start" }}>
            <span style={{ fontSize: 12.5, color: "var(--dy-text)" }}>
              {clientIds.length
                ? clientIds.map((id) => clientName.get(id) ?? `客户 #${id}`).join("、")
                : "未绑定客户"}
            </span>
            <span style={{ fontSize: 12, color: "var(--dy-faint)" }}>
              {projectIds.length
                ? projectIds.map((id) => projectName.get(id) ?? `项目 #${id}`).join("、")
                : "未绑定项目"}
            </span>
            {isAdmin ? (
              <Button
                type="link"
                size="small"
                style={{ height: "auto", padding: 0 }}
                onClick={() => openAssignmentModal(account)}
              >
                编辑归属
              </Button>
            ) : null}
          </div>
        );
      },
    },
    {
      title: "定位 / 当前任务",
      width: 300,
      render: (_, account) => (
        <div style={{ display: "grid", gap: 4 }}>
          <span
            title={account.positioning_summary ?? undefined}
            style={{ color: "var(--dy-text)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
          >
            {account.positioning_summary || "待账号定位专家分析"}
          </span>
          <span
            title={account.current_task?.title}
            style={{ color: "var(--dy-muted)", fontSize: 12, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
          >
            {account.current_task
              ? `${account.current_task.title} · ${account.current_task.progress}%`
              : "暂无运行任务"}
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
      title: "授权 / 数据",
      width: 130,
      render: (_, account) => (
        <div style={{ display: "grid", gap: 5, justifyItems: "start" }}>
          <StatePill label={AUTH_LABEL[account.auth_status]} tone={authTone(account.auth_status)} />
          <StatePill label={SYNC_LABEL[account.data_sync_status]} tone={syncTone(account.data_sync_status)} />
        </div>
      ),
    },
    {
      title: "风险",
      width: 80,
      render: (_, account) =>
        (account.risk_count ?? 0) > 0 ? (
          <StatePill label={`${account.risk_count} 项`} tone="warning" />
        ) : (
          <StatePill label="正常" tone="success" />
        ),
    },
    {
      title: "发布能力",
      width: 124,
      render: (_, account) => {
        const capability = account.publish_capability ?? "unavailable";
        return (
          <StatePill
            label={PUBLISH_CAPABILITY_LABEL[capability]}
            tone={
              capability === "prepare_only"
                ? "success"
                : capability === "manual_only"
                  ? "warning"
                  : "muted"
            }
          />
        );
      },
    },
    {
      title: "动作",
      width: 240,
      render: (_, account) => (
        <AccountActions
          account={account}
          canManage={isAdmin}
          authorizeLoading={douyinAuthorizeMutation.isPending}
          syncLoading={douyinSyncMutation.isPending}
          onOpenDataCenter={() => openAccountDataCenter(account)}
          onOfficialAuthorize={() => douyinAuthorizeMutation.mutate(account.id)}
          onWechatAuthorize={() => wechatAuthorizeMutation.mutate()}
          onSyncMetrics={() => douyinSyncMutation.mutate(account.id)}
          onInspectCapabilities={() => setCapabilityAccount(account)}
          wechatAuthorizeLoading={wechatAuthorizeMutation.isPending}
          onDelete={() => setDeleteTarget(account)}
        />
      ),
    },
  ];

  const loading =
    accountsQuery.isLoading ||
    matrixQuery.isLoading ||
    clientsQuery.isLoading ||
    groupsQuery.isLoading ||
    projectsQuery.isLoading;
  const failedQuery = [
    accountsQuery,
    matrixQuery,
    clientsQuery,
    groupsQuery,
    projectsQuery,
  ].find((query) => query.isError);

  if (failedQuery) {
    const failure = presentApiError(
      failedQuery.error,
      "账号矩阵暂时不可用，请稍后重新加载。",
    );
    return (
      <div>
        <PageHeader
          title="账号矩阵"
          subtitle="围绕客户、项目和平台组织账号，让每个账号都带着定位、任务与真实状态工作"
        />
        <OperationalState
          kind="error"
          title="账号矩阵加载失败"
          description={`${failure.message} 当前筛选和顶部账号选择均会保留。`}
          diagnostic={failure.diagnostic}
          actionLabel="重新加载"
          actionLoading={failedQuery.isFetching}
          onAction={() => {
            void Promise.all([
              accountsQuery.refetch(),
              matrixQuery.refetch(),
              clientsQuery.refetch(),
              groupsQuery.refetch(),
              projectsQuery.refetch(),
            ]);
          }}
        />
      </div>
    );
  }

  return (
    <div>
      <PageHeader
        title="账号矩阵"
        subtitle="围绕客户、项目和平台组织账号，让每个账号都带着定位、任务与真实状态工作"
        extra={
          <Space>
            {isAdmin && (
              <>
                <Button icon={<ApartmentOutlined />} onClick={() => setWorkspaceManagerOpen(true)}>
                  客户与项目
                </Button>
                <Button icon={<SettingOutlined />} onClick={() => setPlatformBoardOpen(true)}>
                  平台接入
                </Button>
                <Button type="primary" icon={<PlusOutlined />} onClick={() => openAccountModal()}>
                  接入账号
                </Button>
              </>
            )}
          </Space>
        }
      />

      <AccountSummaryStrip accounts={rows} />

      <div className="account-workbench-toolbar">
        <div className="account-workbench-toolbar__filters">
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
        <Segmented<AccountMatrixView>
          className="account-workbench-toolbar__views"
          value={view}
          onChange={setView}
          options={[
            { label: "表格", value: "table", icon: <TableOutlined /> },
            { label: "矩阵", value: "matrix", icon: <UngroupOutlined /> },
            { label: "账号卡", value: "cards", icon: <AppstoreOutlined /> },
            { label: "按项目", value: "projects", icon: <ApartmentOutlined /> },
          ]}
        />
      </div>

      {isAdmin && selected.length > 0 && (
        <div className="account-batch-bar">
          <strong>已选择 {selected.length} 个账号</strong>
          <Space size={6}>
            <Button
              size="small"
              icon={<FolderOpenOutlined />}
              onClick={() => {
                setBatchValue(null);
                setBatchModal("project");
              }}
            >
              绑定项目
            </Button>
            <Button
              size="small"
              icon={<TeamOutlined />}
              onClick={() => {
                setBatchValue(null);
                setBatchModal("group");
              }}
            >
              移动分组
            </Button>
            <Button
              size="small"
              icon={<CheckOutlined />}
              loading={batchMutation.isPending}
              onClick={() => batchMutation.mutate({ status: "active" })}
            >
              启用
            </Button>
            <Button
              size="small"
              icon={<PauseOutlined />}
              loading={batchMutation.isPending}
              onClick={() => batchMutation.mutate({ status: "inactive" })}
            >
              停用
            </Button>
            <Button size="small" type="text" onClick={() => setSelected([])}>
              取消选择
            </Button>
          </Space>
        </div>
      )}

      <div className="account-workbench-surface">
        {view === "matrix" && (
          <MatrixBoard
            sections={matrixSections}
            loading={loading}
            currentAccountId={currentAccountId}
            onSelectAccount={selectWorkspaceAccount}
          />
        )}
        {view === "cards" && (
          <AccountCardsView
            accounts={rows}
            projects={projectsQuery.data ?? []}
            groups={groupsQuery.data ?? []}
            currentAccountId={currentAccountId}
            onSelectAccount={selectWorkspaceAccount}
          />
        )}
        {view === "projects" && (
          <AccountProjectsView
            accounts={rows}
            projects={projectsQuery.data ?? []}
            groups={groupsQuery.data ?? []}
            currentAccountId={currentAccountId}
            onSelectAccount={selectWorkspaceAccount}
          />
        )}
        {view === "table" && (
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
        )}
      </div>

      <Modal
        title={deleteTarget ? `删除账号“${deleteTarget.nickname}”？` : "删除账号"}
        open={deleteTarget !== null}
        onCancel={() => setDeleteTarget(null)}
        onOk={() => {
          if (deleteTarget) deleteMutation.mutate(deleteTarget);
        }}
        confirmLoading={deleteMutation.isPending}
        okText="确认删除"
        cancelText="取消"
        okButtonProps={{ danger: true }}
        closable={!deleteMutation.isPending}
        maskClosable={!deleteMutation.isPending}
        destroyOnHidden
      >
        <p style={{ marginTop: 0 }}>
          该账号会从同舟行账号矩阵中移除，关联授权令牌与平台配置也会一并删除。此操作不可撤销。
        </p>
        <p style={{ marginBottom: 0, color: "var(--dy-muted)" }}>
          <strong style={{ color: "var(--dy-text)" }}>不会删除抖音平台上的账号本身</strong>
          ，如需撤回平台授权，请在抖音开放平台另行处理。
        </p>
      </Modal>

      <Modal
        title={batchModal === "project" ? "批量绑定项目" : "批量移动分组"}
        open={batchModal !== null}
        onCancel={() => {
          setBatchModal(null);
          setBatchValue(null);
        }}
        onOk={() => {
          if (batchModal === "project") batchMutation.mutate({ project_id: batchValue });
          if (batchModal === "group") batchMutation.mutate({ group_id: batchValue });
        }}
        confirmLoading={batchMutation.isPending}
        okText="应用到所选账号"
        destroyOnHidden
      >
        <p style={{ color: "var(--dy-muted)", marginTop: 0 }}>
          将同时更新 {selected.length} 个账号；留空表示移除当前归属。
        </p>
        <Select
          allowClear
          showSearch
          optionFilterProp="label"
          style={{ width: "100%" }}
          placeholder={batchModal === "project" ? "选择项目" : "选择账号组"}
          value={batchValue ?? undefined}
          onChange={(value) => setBatchValue(value ?? null)}
          options={
            batchModal === "project"
              ? (projectsQuery.data ?? []).map((project) => ({
                  label: project.name,
                  value: project.id,
                }))
              : (groupsQuery.data ?? []).map((group) => ({
                  label: `${group.name} · ${DIMENSION_LABEL[group.dimension]}`,
                  value: group.id,
                }))
          }
        />
      </Modal>

      <Modal
        title="客户与项目"
        open={workspaceManagerOpen}
        onCancel={() => setWorkspaceManagerOpen(false)}
        footer={null}
        width={900}
        destroyOnHidden
      >
        <WorkspaceStructureManager
          clients={clientsQuery.data ?? []}
          projects={projectsQuery.data ?? []}
          pending={
            createClientMutation.isPending ||
            updateClientMutation.isPending ||
            createProjectMutation.isPending ||
            updateProjectMutation.isPending
          }
          onCreateClient={(name) => createClientMutation.mutate({ name })}
          onUpdateClient={(id, patch) => updateClientMutation.mutate({ id, patch })}
          onCreateProject={(input) => createProjectMutation.mutate(input)}
          onUpdateProject={(id, patch) => updateProjectMutation.mutate({ id, patch })}
        />
      </Modal>

      <Modal
        title={assignmentTarget ? `客户与项目归属 · ${assignmentTarget.nickname}` : "客户与项目归属"}
        open={assignmentTarget != null}
        onCancel={() => {
          setAssignmentTarget(null);
          assignmentForm.resetFields();
        }}
        onOk={() => assignmentForm.submit()}
        confirmLoading={assignmentMutation.isPending}
        okText="保存归属"
        width={720}
        destroyOnHidden
      >
        <Form
          form={assignmentForm}
          layout="vertical"
          requiredMark={false}
          onValuesChange={(changed) => {
            if (!("client_ids" in changed)) return;
            const selectedClientIds = (changed.client_ids ?? []) as number[];
            const allowedProjectIds = new Set(
              (projectsQuery.data ?? [])
                .filter(
                  (project) =>
                    project.client_id != null && selectedClientIds.includes(project.client_id),
                )
                .map((project) => project.id),
            );
            const nextProjectIds = (
              assignmentForm.getFieldValue("project_ids") ?? []
            ).filter((id: number) => allowedProjectIds.has(id));
            const currentDefaultClient = assignmentForm.getFieldValue("default_client_id");
            const currentDefaultProject = assignmentForm.getFieldValue("default_project_id");
            assignmentForm.setFieldsValue({
              project_ids: nextProjectIds,
              default_client_id: selectedClientIds.includes(currentDefaultClient)
                ? currentDefaultClient
                : selectedClientIds[0] ?? null,
              default_project_id: nextProjectIds.includes(currentDefaultProject)
                ? currentDefaultProject
                : nextProjectIds[0] ?? null,
            });
          }}
          onFinish={(value) => {
            if (!assignmentTarget) return;
            assignmentMutation.mutate({
              accountId: assignmentTarget.id,
              input: {
                client_ids: value.client_ids,
                project_ids: value.project_ids ?? [],
                default_client_id: value.default_client_id ?? null,
                default_project_id: value.default_project_id ?? null,
              },
            });
          }}
        >
          <Form.Item
            name="client_ids"
            label="关联客户"
            extra="可选。账号可以不绑定归属直接工作，也可以关联多个客户并设置一个默认客户。"
          >
            <Select
              mode="multiple"
              optionFilterProp="label"
              placeholder="选择一个或多个客户"
              options={(clientsQuery.data ?? [])
                .filter((client) => client.status === "active")
                .map((client) => ({ label: client.name, value: client.id }))}
            />
          </Form.Item>
          <Form.Item
            name="default_client_id"
            label="默认客户"
          >
            <Select
              allowClear
              disabled={assignmentClientIds.length === 0}
              placeholder={assignmentClientIds.length ? "选择默认客户" : "未绑定客户"}
              options={(clientsQuery.data ?? [])
                .filter((client) => assignmentClientIds.includes(client.id))
                .map((client) => ({ label: client.name, value: client.id }))}
            />
          </Form.Item>
          <Form.Item
            name="project_ids"
            label="关联项目"
            extra="只显示属于已选客户的项目；账号可以参与多个项目。"
          >
            <Select
              mode="multiple"
              optionFilterProp="label"
              placeholder="选择一个或多个项目"
              options={(projectsQuery.data ?? [])
                .filter(
                  (project) =>
                    project.status !== "archived" &&
                    project.client_id != null &&
                    assignmentClientIds.includes(project.client_id),
                )
                .map((project) => ({
                  label: `${project.name} · ${clientName.get(project.client_id!) ?? "客户"}`,
                  value: project.id,
                }))}
            />
          </Form.Item>
          <Form.Item name="default_project_id" label="默认项目">
            <Select
              allowClear
              placeholder="未选择时仅使用默认客户上下文"
              options={(projectsQuery.data ?? [])
                .filter((project) => assignmentProjectIds.includes(project.id))
                .map((project) => ({ label: project.name, value: project.id }))}
            />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="平台接入"
        open={platformBoardOpen}
        onCancel={() => setPlatformBoardOpen(false)}
        footer={null}
        width={1120}
        destroyOnHidden
      >
        <PlatformAccessBoard
          summaries={platformSummaries}
          integrations={platformIntegrations}
          isAdmin={isAdmin}
          onAddAccount={openAccountModal}
          onConfigure={openIntegrationModal}
          onWhitelist={() => douyinWhitelistMutation.mutate()}
          whitelistLoading={douyinWhitelistMutation.isPending}
        />
      </Modal>

      <Modal
        title={isDouyinScanAdd ? "抖音扫码添加账号" : "接入矩阵账号"}
        open={modalOpen}
        onCancel={() => setModalOpen(false)}
        onOk={() => form.submit()}
        confirmLoading={
          createMutation.isPending ||
          douyinScanAddMutation.isPending ||
          createManualDouyinMutation.isPending
        }
        okText={isDouyinScanAdd ? "打开抖音扫码" : "加入矩阵"}
        destroyOnHidden
      >
        <Form
          form={form}
          layout="vertical"
          requiredMark={false}
          onFinish={(value) => {
            if (value.platform === "douyin") {
              if ((value.douyin_add_mode ?? "scan") === "manual") {
                createManualDouyinMutation.mutate(value);
                return;
              }
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
          {isDouyin && (
            <Form.Item
              name="douyin_add_mode"
              label="抖音接入方式"
              initialValue="scan"
              extra={
                douyinAddMode === "manual"
                  ? "用于本地开发和运营大脑验收，不代表抖音官方 OAuth 已授权。"
                  : "需要可公网访问的 HTTPS 回调域名，并已在抖音开放平台配置。"
              }
            >
              <Radio.Group
                optionType="button"
                buttonStyle="solid"
                options={[
                  { label: "扫码官方授权", value: "scan" },
                  { label: "本地开发账号", value: "manual" },
                ]}
              />
            </Form.Item>
          )}
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
          <Form.Item
            name="scopes"
            label="开放平台已开通权限"
            extra="这里只登记已在抖音开放平台审核通过的权限；账号授权会按实际任务增量申请。"
          >
            <Select
              mode="tags"
              tokenSeparators={[",", "，", " "]}
              placeholder="例如 user_info、h5.share"
              options={[
                { label: "user_info", value: "user_info" },
                { label: "h5.share", value: "h5.share" },
                { label: "open.get.ticket", value: "open.get.ticket" },
                { label: "aweme.share", value: "aweme.share" },
                { label: "task.posting.create", value: "task.posting.create" },
                { label: "posting.behavior", value: "posting.behavior" },
                { label: "task.posting.user_verification", value: "task.posting.user_verification" },
                { label: "js.ticket", value: "js.ticket" },
                { label: "jump.basic", value: "jump.basic" },
              ]}
            />
          </Form.Item>
          <Form.Item name="note" label="备注">
            <Input.TextArea rows={3} maxLength={1000} placeholder="平台审核、权限申请、回调域配置说明" />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title={capabilityAccount?.platform === "wechat_official_account"
          ? `微信能力 · ${capabilityAccount.nickname}`
          : capabilityAccount ? `抖音能力 · ${capabilityAccount.nickname}` : "平台能力"}
        open={capabilityAccount != null}
        onCancel={() => setCapabilityAccount(null)}
        footer={null}
        width={680}
        destroyOnHidden
      >
        {capabilityAccount?.platform === "wechat_official_account" ? (
          wechatCapabilitiesQuery.isLoading ? (
            <div role="status" style={{ padding: "32px 0", textAlign: "center", color: "var(--dy-muted)" }}>
              正在核对微信公众号授权与能力...
            </div>
          ) : wechatCapabilitiesQuery.isError ? (
            <OperationalState
              kind="error"
              title="微信能力状态暂时不可用"
              description="请确认公众号已完成官方授权；若组件未配置，请联系管理员配置微信开放平台组件。"
              actionLabel="重新检查"
              onAction={() => void wechatCapabilitiesQuery.refetch()}
            />
          ) : (
            <div style={{ display: "grid", gap: 10 }} aria-live="polite">
              {WECHAT_CAPABILITY_ROWS.map((row) => (
                <WechatCapabilityRow
                  key={row.key}
                  label={row.label}
                  state={wechatCapabilitiesQuery.data?.[row.key]}
                  freepublish={row.key === "freepublish"}
                />
              ))}
            </div>
          )
        ) : douyinCapabilitiesQuery.isLoading ? (
          <div style={{ padding: "32px 0", textAlign: "center", color: "var(--dy-muted)" }}>
            正在核对开放平台与账号授权状态...
          </div>
        ) : douyinCapabilitiesQuery.isError ? (
          <OperationalState
            kind="error"
            title="能力状态暂时不可用"
            description={presentApiError(
              douyinCapabilitiesQuery.error,
              "请确认账号已完成抖音官方授权后重试。",
            ).message}
            actionLabel="重新检查"
            onAction={() => void douyinCapabilitiesQuery.refetch()}
          />
        ) : (
          <div style={{ display: "grid", gap: 10 }}>
            {(douyinCapabilitiesQuery.data?.capabilities ?? []).map((capability) => (
              <DouyinCapabilityRow
                key={capability.key}
                capability={capability}
                authorizing={douyinIncrementalAuthorizeMutation.isPending}
                onAuthorize={() => {
                  if (!capabilityAccount) return;
                  douyinIncrementalAuthorizeMutation.mutate({
                    accountId: capabilityAccount.id,
                    capabilityKey: capability.key,
                  });
                }}
              />
            ))}
          </div>
        )}
      </Modal>

      <Modal
        title="抖音测试白名单（仅需一次）"
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
            <div style={{ lineHeight: 1.7 }}>
              这里只添加测试资格，不会把抖音号添加到账号矩阵。完成后请点击“添加账号”重新扫码授权。
            </div>
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
                        测试白名单
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
                      添加账号
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
  currentAccountId,
  onSelectAccount,
}: {
  sections: MatrixProjectSection[];
  loading: boolean;
  currentAccountId: number | null;
  onSelectAccount: (accountId: number) => void;
}) {
  if (loading) {
    return (
      <div className="account-matrix-view account-matrix-view--loading" aria-hidden />
    );
  }

  return (
    <div className="account-matrix-view">
      {sections.length === 0 ? (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前筛选下暂无账号" />
      ) : (
        <div className="account-matrix-view__sections">
          {sections.map((section) => (
            <MatrixProject
              key={section.id ?? "unbound"}
              section={section}
              currentAccountId={currentAccountId}
              onSelectAccount={onSelectAccount}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function MatrixProject({
  section,
  currentAccountId,
  onSelectAccount,
}: {
  section: MatrixProjectSection;
  currentAccountId: number | null;
  onSelectAccount: (accountId: number) => void;
}) {
  const accountCount = section.groups.reduce(
    (sum, group) => sum + group.platforms.reduce((total, node) => total + node.accounts.length, 0),
    0,
  );

  return (
    <section className="account-matrix-project">
      <header className="account-matrix-project__header">
        <div>
          <span>客户 / 项目</span>
          <strong>{section.name}</strong>
        </div>
        <span>{accountCount} 个账号</span>
      </header>
      <div className="account-matrix-grid account-matrix-grid--header" aria-hidden>
        <span>账号分组</span>
        {PLATFORM_OPTIONS.map((option) => (
          <span key={option.value}>{option.label}</span>
        ))}
        <span>合计</span>
      </div>
      <div className="account-matrix-project__rows">
        {section.groups.map((group) => (
          <div className="account-matrix-grid account-matrix-grid--row" key={group.id ?? "ungrouped"}>
            <div className="account-matrix-group-label">
              <strong>{group.name}</strong>
              <span>
                {group.dimension === "ungrouped" ? "未分组" : DIMENSION_LABEL[group.dimension]}
              </span>
            </div>
            {PLATFORM_OPTIONS.map((option) => {
              const accounts =
                group.platforms.find((node) => node.platform === option.value)?.accounts ?? [];
              return (
                <div className="account-matrix-cell" key={option.value}>
                  {accounts.length === 0 ? (
                    <span className="account-matrix-cell__empty">—</span>
                  ) : (
                    accounts.map((account) => (
                      <button
                        type="button"
                        key={account.id}
                        title={account.nickname}
                        className={`account-matrix-account${currentAccountId === account.id ? " is-current" : ""}`}
                        onClick={() => onSelectAccount(account.id)}
                      >
                        <span>{account.nickname.slice(0, 1)}</span>
                        <strong>{account.nickname}</strong>
                        <i className={account.auth_status === "authorized" ? "is-authorized" : ""} />
                      </button>
                    ))
                  )}
                </div>
              );
            })}
            <strong className="account-matrix-row-total">
              {group.platforms.reduce((total, node) => total + node.accounts.length, 0)}
            </strong>
          </div>
        ))}
      </div>
    </section>
  );
}

function AccountActions({
  account,
  canManage,
  authorizeLoading,
  syncLoading,
  onOpenDataCenter,
  onOfficialAuthorize,
  onWechatAuthorize,
  onSyncMetrics,
  onInspectCapabilities,
  onDelete,
  wechatAuthorizeLoading,
}: {
  account: Account;
  canManage: boolean;
  authorizeLoading: boolean;
  syncLoading: boolean;
  onOpenDataCenter: () => void;
  onOfficialAuthorize: () => void;
  onWechatAuthorize: () => void;
  onSyncMetrics: () => void;
  onInspectCapabilities: () => void;
  onDelete: () => void;
  wechatAuthorizeLoading: boolean;
}) {
  const mode = getAccountActionMode(account);
  const primaryAction = canManage && account.platform === "wechat_official_account"
    ? account.auth_status === "authorized" ? null : (
      <Button
        size="small"
        type="primary"
        loading={wechatAuthorizeLoading}
        aria-label={`授权微信公众号 ${account.nickname}`}
        onClick={onWechatAuthorize}
      >
        授权微信公众号
      </Button>
    )
    : canManage
    ? mode === "official_authorize" ? (
      <Button
        size="small"
        type="primary"
        loading={authorizeLoading}
        onClick={onOfficialAuthorize}
      >
        官方授权
      </Button>
    ) : mode === "sync_metrics" ? (
      <Button
        size="small"
        icon={<SyncOutlined />}
        loading={syncLoading}
        onClick={onSyncMetrics}
      >
        同步数据
      </Button>
    ) : (
      <span style={{ fontSize: 12, color: "var(--dy-faint)" }}>待接入</span>
    )
    : null;

  return (
    <Space size={6}>
      <Tooltip title="数据中心 / 导入数据">
        <Button
          size="small"
          icon={<DatabaseOutlined />}
          aria-label={`打开数据中心 ${account.nickname}`}
          onClick={onOpenDataCenter}
        >
          数据中心
        </Button>
      </Tooltip>
      {primaryAction}
      {canManage && account.platform === "douyin" && account.auth_status === "authorized" ? (
        <Tooltip title="查看官方能力状态">
          <Button
            size="small"
            type="text"
            icon={<SafetyCertificateOutlined />}
            aria-label={`查看抖音能力 ${account.nickname}`}
            onClick={onInspectCapabilities}
          />
        </Tooltip>
      ) : null}
      {canManage && account.platform === "wechat_official_account" && account.auth_status === "authorized" ? (
        <Tooltip title="查看微信公众号能力状态">
          <Button
            size="small"
            type="text"
            icon={<SafetyCertificateOutlined />}
            aria-label={`查看微信能力 ${account.nickname}`}
            onClick={onInspectCapabilities}
          />
        </Tooltip>
      ) : null}
      {canManage ? (
        <Tooltip title="删除账号">
          <Button
            size="small"
            type="text"
            danger
            icon={<DeleteOutlined />}
            aria-label={`删除账号 ${account.nickname}`}
            onClick={onDelete}
          />
        </Tooltip>
      ) : null}
    </Space>
  );
}

const WECHAT_CAPABILITY_ROWS: Array<{ key: WechatCapabilityKey; label: string }> = [
  { key: "uploadArticleImage", label: "上传图文图片" },
  { key: "addPermanentMaterial", label: "新增永久素材" },
  { key: "draftAdd", label: "新建草稿" },
  { key: "draftGet", label: "读取草稿" },
  { key: "draftUpdate", label: "更新草稿" },
  { key: "analytics", label: "内容数据分析" },
  { key: "freepublish", label: "发布文章" },
];

function WechatCapabilityRow({
  label,
  state,
  freepublish,
}: {
  label: string;
  state?: WechatCapabilityState;
  freepublish: boolean;
}) {
  const status = freepublish
    ? "首版未开启"
    : state?.canUse
      ? "可用"
      : ({
          component_permission_missing: "开放平台组件缺少权限",
          account_permission_missing: "公众号尚未授权所需权限",
          unsupported_account_type: "公众号类型暂不支持",
          account_not_verified: "公众号未认证",
          account_qualification_unknown: "公众号资质未知",
          account_not_authorized: "需要重新授权公众号",
          live_probe_failed: "实时探测失败，请稍后重试",
        } as Record<string, string>)[state?.reason ?? ""] ?? "能力状态未知";
  return (
    <section style={{ borderBottom: "1px solid var(--dy-border)", padding: "10px 0", display: "flex", justifyContent: "space-between", gap: 12 }}>
      <strong>{label}</strong>
      <Tag color={!freepublish && state?.canUse ? "success" : "default"}>{status}</Tag>
    </section>
  );
}

function DouyinCapabilityRow({
  capability,
  authorizing,
  onAuthorize,
}: {
  capability: DouyinCapability;
  authorizing: boolean;
  onAuthorize: () => void;
}) {
  const status = {
    ready: { label: "已可用", color: "success" },
    needs_app_permission: { label: "开放平台待开通", color: "warning" },
    needs_account_authorization: { label: "账号待补充授权", color: "processing" },
  }[capability.status];
  const missingScopes = capability.status === "needs_app_permission"
    ? capability.missing_app_scopes
    : capability.missing_user_scopes;

  return (
    <section
      style={{
        border: "1px solid var(--dy-border)",
        borderRadius: 10,
        padding: "14px 16px",
        display: "grid",
        gap: 8,
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
        <div>
          <strong>{capability.label}</strong>
          <div style={{ marginTop: 4, color: "var(--dy-muted)", fontSize: 13 }}>
            {capability.description}
          </div>
        </div>
        <Tag color={status.color}>{status.label}</Tag>
      </div>
      {missingScopes.length > 0 ? (
        <div style={{ color: "var(--dy-muted)", fontSize: 12 }}>
          缺少权限：<code>{missingScopes.join(", ")}</code>
        </div>
      ) : null}
      {capability.status === "needs_app_permission" ? (
        <div style={{ fontSize: 12 }}>
          请先在抖音开放平台申请并审核通过，再到“平台接入”登记权限。
        </div>
      ) : capability.status === "needs_account_authorization" ? (
        <div>
          <Button size="small" loading={authorizing} onClick={onAuthorize}>
            补充授权
          </Button>
        </div>
      ) : null}
    </section>
  );
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
    <span style={{ display: "inline-flex", alignItems: "center", gap: 5, color, fontSize: 12, whiteSpace: "nowrap" }}>
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
