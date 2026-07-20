import {
  AlertOutlined,
  CheckCircleOutlined,
  PlusOutlined,
  SafetyCertificateOutlined,
  SearchOutlined,
  TeamOutlined,
} from "@ant-design/icons";
import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, Input, Modal, Skeleton, Tabs } from "antd";
import { useEffect, useMemo, useState } from "react";

import {
  createUser,
  getSecondaryPasswordStatus,
  getUserAccessCatalog,
  getUserDetail,
  listUsers,
  resetUserPassword,
  setSecondaryPassword,
  updateUser,
  updateUserAccess,
} from "../api/auth";
import { presentApiError } from "../api/errors";
import { MemberAccess } from "../components/users/MemberAccess";
import { MemberActivity } from "../components/users/MemberActivity";
import { MemberOverview } from "../components/users/MemberOverview";
import { MemberSecurity } from "../components/users/MemberSecurity";
import { formatGovernanceError, getAccessibleAccounts, hasAccessAnomaly } from "../components/users/userGovernance";
import { OperationalState } from "../components/ui";
import { useAuth } from "../stores/auth";
import type {
  CreateUserInput,
  Role,
  User,
  UserDetail,
} from "../types";

type StatusFilter = "all" | "active" | "inactive";
type RoleFilter = "all" | Role;
type AnomalyFilter = "all" | "anomalies";
type TabKey = "overview" | "access" | "security" | "activity";

type CreateDraft = {
  display_name: string;
  email: string;
  password: string;
  role: Role;
};

const DEFAULT_CREATE_DRAFT: CreateDraft = {
  display_name: "",
  email: "",
  password: "",
  role: "user",
};

export default function Users() {
  const queryClient = useQueryClient();
  const currentUser = useAuth((state) => state.user);
  const [selectedUserId, setSelectedUserId] = useState<number | null>(null);
  const [activeTab, setActiveTab] = useState<TabKey>("overview");
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [roleFilter, setRoleFilter] = useState<RoleFilter>("all");
  const [anomalyFilter, setAnomalyFilter] = useState<AnomalyFilter>("all");
  const [createOpen, setCreateOpen] = useState(false);
  const [createDraft, setCreateDraft] = useState<CreateDraft>(DEFAULT_CREATE_DRAFT);
  const [createFeedback, setCreateFeedback] = useState<string | null>(null);

  const usersQuery = useQuery({
    queryKey: ["users"],
    queryFn: listUsers,
  });

  const users = useMemo(() => usersQuery.data ?? [], [usersQuery.data]);

  const detailQueries = useQueries({
    queries: users.map((user) => ({
      queryKey: ["user-detail", user.id],
      queryFn: () => getUserDetail(user.id),
      enabled: usersQuery.isSuccess,
      retry: false,
    })),
  });

  const detailStateByUserId = useMemo(() => {
    const mapping = new Map<number, (typeof detailQueries)[number]>();
    users.forEach((user, index) => {
      mapping.set(user.id, detailQueries[index]);
    });
    return mapping;
  }, [detailQueries, users]);

  const catalogQuery = useQuery({
    queryKey: ["user-access-catalog"],
    queryFn: getUserAccessCatalog,
    enabled: usersQuery.isSuccess,
  });

  const secondaryStatusQuery = useQuery({
    queryKey: ["secondary-password-status"],
    queryFn: getSecondaryPasswordStatus,
    enabled: currentUser?.role === "admin",
  });

  const createMutation = useMutation({
    mutationFn: (input: CreateUserInput) => createUser(input),
  });

  const updateUserMutation = useMutation({
    mutationFn: ({ userId, input }: { userId: number; input: Partial<User> & { role?: Role } }) => updateUser(userId, input),
  });

  const accessMutation = useMutation({
    mutationFn: ({ userId, input }: Parameters<typeof updateUserAccess>[0] extends number
      ? { userId: number; input: Parameters<typeof updateUserAccess>[1] }
      : never) => updateUserAccess(userId, input),
  });

  const secondaryPasswordMutation = useMutation({
    mutationFn: setSecondaryPassword,
  });

  const resetPasswordMutation = useMutation({
    mutationFn: ({ userId, input }: { userId: number; input: Parameters<typeof resetUserPassword>[1] }) =>
      resetUserPassword(userId, input),
  });

  const filteredUsers = useMemo(() => {
    const keyword = search.trim().toLowerCase();
    return users.filter((user) => {
      const detail = detailStateByUserId.get(user.id)?.data;
      const matchesKeyword = !keyword
        || `${user.display_name} ${user.email}`.toLowerCase().includes(keyword);
      const matchesStatus = statusFilter === "all"
        || (statusFilter === "active" ? user.is_active : !user.is_active);
      const matchesRole = roleFilter === "all" || user.role === roleFilter;
      const matchesAnomaly = anomalyFilter === "all"
        || (detail ? hasAccessAnomaly(detail) : false);
      return matchesKeyword && matchesStatus && matchesRole && matchesAnomaly;
    });
  }, [anomalyFilter, detailStateByUserId, roleFilter, search, statusFilter, users]);

  const resolvedSelectedUserId = useMemo(() => {
    if (!filteredUsers.length) return null;
    if (selectedUserId && filteredUsers.some((user) => user.id === selectedUserId)) {
      return selectedUserId;
    }
    return filteredUsers[0].id;
  }, [filteredUsers, selectedUserId]);

  useEffect(() => {
    if (usersQuery.isLoading) return;
    if (selectedUserId !== resolvedSelectedUserId) {
      setSelectedUserId(resolvedSelectedUserId);
    }
  }, [resolvedSelectedUserId, selectedUserId, usersQuery.isLoading]);

  const selectedUser = users.find((user) => user.id === resolvedSelectedUserId) ?? null;
  const selectedDetailQuery = resolvedSelectedUserId != null
    ? detailStateByUserId.get(resolvedSelectedUserId) ?? null
    : null;
  const selectedDetail = (selectedDetailQuery?.data as UserDetail | undefined) ?? null;

  const selectedDetailError = selectedDetailQuery?.isError
    ? presentApiError(selectedDetailQuery.error, "成员详情暂时不可用。")
    : null;
  const inspectorLoading = usersQuery.isLoading
    || (selectedUser != null && !selectedDetail && !selectedDetailError);

  const catalogError = catalogQuery.isError
    ? presentApiError(catalogQuery.error, "资源授权目录暂时不可用。")
    : null;

  const secondaryStatusError = secondaryStatusQuery.isError
    ? presentApiError(secondaryStatusQuery.error, "二级密码状态暂时不可用。").message
    : null;

  const loadedDetails = detailQueries
    .map((query) => query.data as UserDetail | undefined)
    .filter((detail): detail is UserDetail => Boolean(detail));

  const summaryMetrics = useMemo(() => {
    const unassignedCount = loadedDetails.filter((detail) => hasAccessAnomaly(detail)).length;
    return [
      { label: "成员总数", value: users.length },
      { label: "启用中", value: users.filter((user) => user.is_active).length },
      { label: "管理员", value: users.filter((user) => user.role === "admin").length },
      { label: "未分配资源", value: unassignedCount, help: "按已加载成员详情统计" },
      { label: "安全锁定", value: "—", help: "当前接口未提供成员级锁定统计" },
    ];
  }, [loadedDetails, users]);

  const selectedMetrics = useMemo(() => {
    if (!selectedDetail || !catalogQuery.data) return [];
    const accessibleAccounts = getAccessibleAccounts(selectedDetail, catalogQuery.data);
    const effectiveAccounts = selectedDetail.account_scope_mode === "selected"
      ? accessibleAccounts.filter((account) => selectedDetail.account_ids.includes(account.id))
      : accessibleAccounts;
    return [
      { label: "客户授权", value: selectedDetail.client_memberships.length },
      { label: "项目覆盖", value: selectedDetail.project_memberships.length },
      { label: "可见账号", value: accessibleAccounts.length },
      { label: "最终生效账号", value: effectiveAccounts.length },
      { label: "授权状态", value: hasAccessAnomaly(selectedDetail) ? "异常" : "正常" },
    ];
  }, [catalogQuery.data, selectedDetail]);

  async function refreshUser(userId: number) {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["users"] }),
      queryClient.invalidateQueries({ queryKey: ["user-detail", userId] }),
    ]);
  }

  async function handleCreateUser() {
    setCreateFeedback(null);
    try {
      const created = await createMutation.mutateAsync(createDraft);
      setCreateOpen(false);
      setCreateDraft(DEFAULT_CREATE_DRAFT);
      await queryClient.invalidateQueries({ queryKey: ["users"] });
      setSelectedUserId(created.id);
      setActiveTab("overview");
    } catch (error) {
      setCreateFeedback(formatGovernanceError(error, "新建成员失败，请检查输入后重试。"));
    }
  }

  async function handleSaveIdentity(draft: { display_name: string; email: string; role: Role }) {
    if (!selectedUserId) return;
    await updateUserMutation.mutateAsync({
      userId: selectedUserId,
      input: draft,
    });
    await refreshUser(selectedUserId);
  }

  async function handleToggleUser(nextActive: boolean) {
    if (!selectedUserId) return;
    try {
      await updateUserMutation.mutateAsync({
        userId: selectedUserId,
        input: { is_active: nextActive },
      });
      await refreshUser(selectedUserId);
    } catch (error) {
      throw new Error(formatGovernanceError(error, "成员状态修改失败，请稍后重试。"));
    }
  }

  async function handleSaveAccess(input: Parameters<typeof updateUserAccess>[1]) {
    if (!selectedUserId) return;
    await accessMutation.mutateAsync({ userId: selectedUserId, input });
    await queryClient.invalidateQueries({ queryKey: ["user-detail", selectedUserId] });
  }

  async function handleSetSecondaryPassword(input: { current_password: string; secondary_password: string }) {
    try {
      await secondaryPasswordMutation.mutateAsync(input);
      await queryClient.invalidateQueries({ queryKey: ["secondary-password-status"] });
    } catch (error) {
      throw new Error(formatGovernanceError(error, "二级密码设置失败，请稍后重试。"));
    }
  }

  async function handleResetPassword(input: { new_password: string }) {
    if (!selectedUserId) return;
    try {
      await resetPasswordMutation.mutateAsync({ userId: selectedUserId, input });
    } catch (error) {
      throw new Error(formatGovernanceError(error, "成员登录密码重置失败，请稍后重试。"));
    }
  }

  function handleDeletedUser(userId: number) {
    const currentUsers = queryClient.getQueryData<User[]>(["users"]) ?? users;
    const currentIndex = currentUsers.findIndex((user) => user.id === userId);
    const nextUsers = currentUsers.filter((user) => user.id !== userId);
    const nextSelected = nextUsers[currentIndex] ?? nextUsers[Math.max(0, currentIndex - 1)] ?? null;

    queryClient.setQueryData(["users"], nextUsers);
    queryClient.removeQueries({ queryKey: ["user-detail", userId], exact: true });
    setSelectedUserId(nextSelected?.id ?? null);
    setActiveTab("overview");
  }

  if (usersQuery.isError) {
    const failure = presentApiError(usersQuery.error, "成员名册暂时不可用。");
    return (
      <div className="tz-user-workspace">
        <OperationalState
          kind="error"
          title="成员名册加载失败"
          description={failure.message}
          diagnostic={failure.diagnostic}
          actionLabel="重新加载"
          onAction={() => void usersQuery.refetch()}
        />
      </div>
    );
  }

  return (
    <div className="tz-user-workspace">
      <header className="tz-user-page-header">
        <div>
          <span className="tz-user-eyebrow">成员治理工作台</span>
          <h1>成员与权限</h1>
          <p>用一张紧凑桌面工作台统一处理成员身份、资源范围、登录安全和危险操作。</p>
        </div>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
          新建成员
        </Button>
      </header>

      <section className="tz-summary-strip" aria-label="成员治理概览">
        {summaryMetrics.map((metric) => (
          <div key={metric.label} className="tz-summary-strip__item">
            <span>{metric.label}</span>
            <strong>{metric.value}</strong>
            {metric.help ? <small>{metric.help}</small> : null}
          </div>
        ))}
      </section>

      <section className="tz-user-console">
        <aside className="tz-member-roster" aria-label="成员名册">
          <header>
            <div>
              <TeamOutlined />
              <strong>成员名册</strong>
            </div>
            <span>{users.length} 人</span>
          </header>

          <Input
            allowClear
            prefix={<SearchOutlined />}
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="搜索姓名或邮箱"
            aria-label="搜索成员"
          />

          <div className="tz-filter-grid">
            <label className="tz-filter-field">
              <span>状态</span>
              <select
                aria-label="状态筛选"
                className="tz-native-select"
                value={statusFilter}
                onChange={(event) => setStatusFilter(event.target.value as StatusFilter)}
              >
                <option value="all">全部</option>
                <option value="active">启用</option>
                <option value="inactive">停用</option>
              </select>
            </label>

            <label className="tz-filter-field">
              <span>系统角色</span>
              <select
                aria-label="系统角色筛选"
                className="tz-native-select"
                value={roleFilter}
                onChange={(event) => setRoleFilter(event.target.value as RoleFilter)}
              >
                <option value="all">全部</option>
                <option value="admin">管理员</option>
                <option value="user">成员</option>
              </select>
            </label>

            <label className="tz-filter-field">
              <span>授权异常</span>
              <select
                aria-label="授权异常筛选"
                className="tz-native-select"
                value={anomalyFilter}
                onChange={(event) => setAnomalyFilter(event.target.value as AnomalyFilter)}
              >
                <option value="all">全部</option>
                <option value="anomalies">仅异常</option>
              </select>
            </label>
          </div>

          <div className="tz-roster-list" role="list" aria-label="成员名册列表">
            {usersQuery.isLoading ? <Skeleton active paragraph={{ rows: 5 }} /> : null}
            {!usersQuery.isLoading && filteredUsers.length === 0 ? (
              <p className="tz-user-empty-copy">没有符合条件的成员</p>
            ) : null}
            {filteredUsers.map((user) => {
              const detail = detailStateByUserId.get(user.id)?.data as UserDetail | undefined;
              return (
                <button
                  key={user.id}
                  type="button"
                  className={`tz-member-row${resolvedSelectedUserId === user.id ? " is-selected" : ""}`}
                  aria-label={`${user.display_name} ${user.email}`}
                  aria-pressed={resolvedSelectedUserId === user.id}
                  onClick={() => setSelectedUserId(user.id)}
                >
                  <span className="tz-member-avatar">{user.display_name.slice(0, 1)}</span>
                  <span className="tz-member-row-copy">
                    <strong>{user.display_name}</strong>
                    <small>{user.email}</small>
                  </span>
                  <span className="tz-member-row-meta">
                    <small>{user.role === "admin" ? "管理员" : "成员"}</small>
                    <span className={`tz-member-status-dot${user.is_active ? " is-active" : " is-inactive"}`} />
                    {detail && hasAccessAnomaly(detail) ? (
                      <span className="tz-member-anomaly">
                        <AlertOutlined />
                        未分配
                      </span>
                    ) : null}
                  </span>
                </button>
              );
            })}
          </div>
        </aside>

        <main className="tz-member-inspector">
          {!selectedUser && !usersQuery.isLoading ? (
            <OperationalState
              kind="empty"
              title="当前筛选下没有可查看的成员"
              description="请调整搜索或筛选条件，或者新建一位成员。"
            />
          ) : selectedDetailError ? (
            <OperationalState
              kind="error"
              title="成员详情加载失败"
              description={selectedDetailError.message}
              diagnostic={selectedDetailError.diagnostic}
              actionLabel="重试"
              onAction={() => {
                if (!resolvedSelectedUserId) return;
                void queryClient.invalidateQueries({ queryKey: ["user-detail", resolvedSelectedUserId] });
              }}
            />
          ) : (
            <>
              {inspectorLoading || !selectedDetail ? (
                <div className="tz-user-detail-loading">
                  <Skeleton active paragraph={{ rows: 3 }} />
                </div>
              ) : (
                <header className="tz-member-detail-header">
                  <div className="tz-member-detail-header__identity">
                    <span className="tz-member-hero-avatar">{selectedDetail.display_name.slice(0, 1)}</span>
                    <div>
                      <span className="tz-member-kicker">
                        {selectedDetail.role === "admin" ? "系统管理员" : "组织成员"}
                      </span>
                      <h2>{selectedDetail.display_name}</h2>
                      <p>{selectedDetail.email}</p>
                    </div>
                  </div>
                  <div className="tz-member-detail-header__state">
                    <span className={`tz-member-state ${selectedDetail.is_active ? "is-active" : "is-inactive"}`}>
                      {selectedDetail.is_active ? <CheckCircleOutlined /> : <AlertOutlined />}
                      {selectedDetail.is_active ? "启用中" : "已停用"}
                    </span>
                    {hasAccessAnomaly(selectedDetail) ? (
                      <span className="tz-member-state is-anomaly">
                        <SafetyCertificateOutlined />
                        授权异常
                      </span>
                    ) : null}
                  </div>
                </header>
              )}

              <Tabs
                activeKey={activeTab}
                onChange={(key) => setActiveTab(key as TabKey)}
                className="tz-user-tabs"
                items={[
                  {
                    key: "overview",
                    label: "概览",
                    children: inspectorLoading || !selectedDetail ? (
                      <div className="tz-user-access-loading">
                        <Skeleton active paragraph={{ rows: 8 }} />
                      </div>
                    ) : (
                      <MemberOverview
                        detail={selectedDetail}
                        metrics={selectedMetrics}
                        onSave={handleSaveIdentity}
                        onToggleActive={handleToggleUser}
                      />
                    ),
                  },
                  {
                    key: "access",
                    label: "资源权限",
                    children: inspectorLoading || !selectedDetail || catalogQuery.isLoading || !catalogQuery.data ? (
                      <div className="tz-user-access-loading">
                        <Skeleton active paragraph={{ rows: 8 }} />
                      </div>
                    ) : catalogError ? (
                      <OperationalState
                        compact
                        kind="error"
                        title="资源权限目录加载失败"
                        description={catalogError.message}
                        diagnostic={catalogError.diagnostic}
                        actionLabel="重试"
                        onAction={() => void catalogQuery.refetch()}
                      />
                    ) : (
                      <MemberAccess
                        detail={selectedDetail}
                        catalog={catalogQuery.data}
                        onSave={handleSaveAccess}
                      />
                    ),
                  },
                  {
                    key: "security",
                    label: "安全与登录",
                    children: inspectorLoading || !selectedDetail ? (
                      <div className="tz-user-access-loading">
                        <Skeleton active paragraph={{ rows: 8 }} />
                      </div>
                    ) : (
                      <MemberSecurity
                        selectedUser={selectedDetail}
                        currentUser={currentUser}
                        secondaryStatus={secondaryStatusQuery.data ?? null}
                        secondaryLoading={secondaryStatusQuery.isLoading}
                        secondaryError={secondaryStatusError}
                        secondaryRefreshing={secondaryStatusQuery.isFetching}
                        onRetrySecondaryStatus={() => void secondaryStatusQuery.refetch()}
                        onSetSecondaryPassword={handleSetSecondaryPassword}
                        onResetPassword={handleResetPassword}
                        onDeleted={handleDeletedUser}
                      />
                    ),
                  },
                  {
                    key: "activity",
                    label: "操作记录",
                    children: inspectorLoading || !selectedDetail ? (
                      <div className="tz-user-access-loading">
                        <Skeleton active paragraph={{ rows: 6 }} />
                      </div>
                    ) : (
                      <MemberActivity />
                    ),
                  },
                ]}
              />
            </>
          )}
        </main>
      </section>

      <Modal
        title="新建成员"
        open={createOpen}
        onCancel={() => {
          setCreateOpen(false);
          setCreateFeedback(null);
        }}
        onOk={() => void handleCreateUser()}
        confirmLoading={createMutation.isPending}
        okText="创建成员"
        cancelText="取消"
      >
        <div className="tz-modal-form">
          <label className="tz-field">
            <span>显示名称</span>
            <Input
              value={createDraft.display_name}
              onChange={(event) => setCreateDraft((current) => ({ ...current, display_name: event.target.value }))}
            />
          </label>
          <label className="tz-field">
            <span>登录邮箱</span>
            <Input
              type="email"
              value={createDraft.email}
              onChange={(event) => setCreateDraft((current) => ({ ...current, email: event.target.value }))}
            />
          </label>
          <label className="tz-field">
            <span>初始密码</span>
            <Input.Password
              value={createDraft.password}
              onChange={(event) => setCreateDraft((current) => ({ ...current, password: event.target.value }))}
            />
          </label>
          <label className="tz-field">
            <span>系统身份</span>
            <select
              className="tz-native-select"
              value={createDraft.role}
              onChange={(event) => setCreateDraft((current) => ({ ...current, role: event.target.value as Role }))}
            >
              <option value="user">成员</option>
              <option value="admin">管理员</option>
            </select>
          </label>
        </div>
        {createFeedback ? <p className="tz-inline-feedback is-error" role="alert">{createFeedback}</p> : null}
      </Modal>
    </div>
  );
}
