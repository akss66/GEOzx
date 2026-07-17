import {
  CheckCircleOutlined,
  MailOutlined,
  PlusOutlined,
  SafetyCertificateOutlined,
  SaveOutlined,
  SearchOutlined,
  StopOutlined,
  TeamOutlined,
  UserOutlined,
} from "@ant-design/icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  App as AntApp,
  Button,
  Checkbox,
  Form,
  Input,
  Modal,
  Popconfirm,
  Select,
  Skeleton,
} from "antd";
import { useEffect, useMemo, useState } from "react";

import {
  createUser,
  getUserAccessCatalog,
  getUserDetail,
  listUsers,
  updateUser,
  updateUserAccess,
} from "../api/auth";
import { presentApiError } from "../api/errors";
import { OperationalState } from "../components/ui";
import type {
  CreateUserInput,
  Role,
  UpdateUserAccessInput,
  User,
  UserAccessCatalog,
  WorkspaceRole,
} from "../types";

const WORKSPACE_ROLE_OPTIONS = [
  { value: "lead", label: "负责人" },
  { value: "operator", label: "运营" },
  { value: "editor", label: "内容" },
  { value: "reviewer", label: "审核" },
] satisfies Array<{ value: WorkspaceRole; label: string }>;

interface IdentityForm {
  email: string;
  display_name: string;
  role: Role;
}

interface CreateForm extends IdentityForm {
  password: string;
}

export default function Users() {
  const queryClient = useQueryClient();
  const { message } = AntApp.useApp();
  const [selectedUserId, setSelectedUserId] = useState<number | null>(null);
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<"all" | "active" | "inactive">("all");
  const [createOpen, setCreateOpen] = useState(false);
  const [clientRoles, setClientRoles] = useState<Record<number, WorkspaceRole>>({});
  const [projectRoles, setProjectRoles] = useState<Record<number, WorkspaceRole>>({});
  const [identityForm] = Form.useForm<IdentityForm>();
  const [createForm] = Form.useForm<CreateForm>();

  const usersQuery = useQuery({ queryKey: ["users"], queryFn: listUsers });
  const catalogQuery = useQuery({
    queryKey: ["user-access-catalog"],
    queryFn: getUserAccessCatalog,
  });
  const detailQuery = useQuery({
    queryKey: ["user-detail", selectedUserId],
    queryFn: () => getUserDetail(selectedUserId!),
    enabled: selectedUserId != null,
  });

  const detail = detailQuery.data;
  const detailFailure = detailQuery.isError
    ? presentApiError(detailQuery.error, "成员详情暂时不可用。")
    : null;
  const catalogFailure = catalogQuery.isError
    ? presentApiError(catalogQuery.error, "客户与项目授权目录暂时不可用。")
    : null;
  useEffect(() => {
    if (!detail) return;
    identityForm.setFieldsValue({
      email: detail.email,
      display_name: detail.display_name,
      role: detail.role,
    });
    setClientRoles(Object.fromEntries(
      detail.client_memberships.map((item) => [item.client_id, item.role]),
    ));
    setProjectRoles(Object.fromEntries(
      detail.project_memberships.map((item) => [item.project_id, item.role]),
    ));
  }, [detail, identityForm]);

  const refreshUser = async (userId: number) => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["users"] }),
      queryClient.invalidateQueries({ queryKey: ["user-detail", userId] }),
    ]);
  };

  const createMutation = useMutation({
    mutationFn: (input: CreateUserInput) => createUser(input),
    onSuccess: async (user) => {
      setCreateOpen(false);
      createForm.resetFields();
      await queryClient.invalidateQueries({ queryKey: ["users"] });
      setSelectedUserId(user.id);
      message.success("成员已创建");
    },
    onError: () => message.error("创建失败，请检查邮箱是否已存在"),
  });

  const identityMutation = useMutation({
    mutationFn: (values: IdentityForm) => updateUser(selectedUserId!, values),
    onSuccess: async () => {
      await refreshUser(selectedUserId!);
      message.success("成员资料已保存");
    },
    onError: () => message.error("资料保存失败"),
  });

  const statusMutation = useMutation({
    mutationFn: (isActive: boolean) => updateUser(selectedUserId!, { is_active: isActive }),
    onSuccess: async (user) => {
      await refreshUser(user.id);
      message.success(user.is_active ? "成员已启用" : "成员已停用");
    },
    onError: () => message.error("状态修改失败，不能停用自己或最后一个管理员"),
  });

  const accessMutation = useMutation({
    mutationFn: (input: UpdateUserAccessInput) => updateUserAccess(selectedUserId!, input),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["user-detail", selectedUserId] });
      message.success("资源授权已保存");
    },
    onError: () => message.error("授权保存失败，请确认资源仍然可用"),
  });

  const filteredUsers = useMemo(() => {
    const keyword = search.trim().toLowerCase();
    return (usersQuery.data ?? []).filter((user) => {
      const matchesKeyword = !keyword || `${user.display_name} ${user.email}`.toLowerCase().includes(keyword);
      const matchesStatus = statusFilter === "all"
        || (statusFilter === "active" ? user.is_active : !user.is_active);
      return matchesKeyword && matchesStatus;
    });
  }, [search, statusFilter, usersQuery.data]);

  const groupedProjects = useMemo(() => {
    const groups = new Map<number | null, UserAccessCatalog["projects"]>();
    for (const project of catalogQuery.data?.projects ?? []) {
      const existing = groups.get(project.client_id) ?? [];
      existing.push(project);
      groups.set(project.client_id, existing);
    }
    return groups;
  }, [catalogQuery.data]);

  const saveAccess = () => {
    accessMutation.mutate({
      clients: Object.entries(clientRoles).map(([clientId, role]) => ({
        client_id: Number(clientId),
        role,
      })),
      projects: Object.entries(projectRoles).map(([projectId, role]) => ({
        project_id: Number(projectId),
        role,
      })),
    });
  };

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
          <span className="tz-user-eyebrow">组织与访问控制</span>
          <h1>成员与权限</h1>
          <p>管理系统身份，并按客户与项目分配运营职责。</p>
        </div>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
          邀请成员
        </Button>
      </header>

      <section className="tz-user-console">
        <aside className="tz-member-roster" aria-label="成员名册">
          <header>
            <div><TeamOutlined /><strong>成员名册</strong></div>
            <span>{usersQuery.data?.length ?? 0} 人</span>
          </header>
          <Input
            allowClear
            prefix={<SearchOutlined />}
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="搜索姓名或邮箱"
            aria-label="搜索成员"
          />
          <div className="tz-roster-filters" role="group" aria-label="成员状态筛选">
            {(["all", "active", "inactive"] as const).map((value) => (
              <button
                key={value}
                type="button"
                className={statusFilter === value ? "is-active" : ""}
                onClick={() => setStatusFilter(value)}
              >
                {{ all: "全部", active: "启用", inactive: "停用" }[value]}
              </button>
            ))}
          </div>
          <div className="tz-roster-list">
            {usersQuery.isLoading ? <Skeleton active paragraph={{ rows: 5 }} /> : null}
            {!usersQuery.isLoading && filteredUsers.length === 0 ? (
              <p className="tz-user-empty-copy">没有符合条件的成员</p>
            ) : null}
            {filteredUsers.map((user) => (
              <MemberRow
                key={user.id}
                user={user}
                selected={selectedUserId === user.id}
                onSelect={() => setSelectedUserId(user.id)}
              />
            ))}
          </div>
        </aside>

        <main className="tz-member-inspector">
          {selectedUserId == null ? (
            <div className="tz-user-welcome">
              <span><TeamOutlined /></span>
              <strong>选择一位成员开始管理</strong>
              <p>资料、客户范围和项目级职责会在这里统一显示。</p>
            </div>
          ) : detailQuery.isError && detailFailure ? (
            <OperationalState
              kind="error"
              title="成员详情加载失败"
              description={`${detailFailure.message} 当前选择和成员名册不会被修改。`}
              diagnostic={detailFailure.diagnostic}
              actionLabel="重试"
              onAction={() => void detailQuery.refetch()}
            />
          ) : detailQuery.isLoading || !detail ? (
            <div className="tz-user-detail-loading"><Skeleton active paragraph={{ rows: 9 }} /></div>
          ) : (
            <>
              <header className="tz-member-detail-header">
                <span className="tz-member-hero-avatar">{detail.display_name.slice(0, 1)}</span>
                <div>
                  <span className="tz-member-kicker">{detail.role === "admin" ? "系统管理员" : "组织成员"}</span>
                  <h2>{detail.display_name}</h2>
                  <p>{detail.email}</p>
                </div>
                <span className={`tz-member-state ${detail.is_active ? "is-active" : "is-inactive"}`}>
                  {detail.is_active ? <CheckCircleOutlined /> : <StopOutlined />}
                  {detail.is_active ? "启用中" : "已停用"}
                </span>
              </header>

              <section className="tz-member-section">
                <div className="tz-member-section-heading">
                  <div><UserOutlined /><span><strong>身份资料</strong><small>登录身份与系统级角色</small></span></div>
                </div>
                <Form
                  form={identityForm}
                  layout="vertical"
                  requiredMark={false}
                  className="tz-member-identity-form"
                  onFinish={(values) => identityMutation.mutate(values)}
                >
                  <Form.Item name="display_name" label="姓名" rules={[{ required: true }]}>
                    <Input prefix={<UserOutlined />} />
                  </Form.Item>
                  <Form.Item name="email" label="邮箱" rules={[{ required: true, type: "email" }]}>
                    <Input prefix={<MailOutlined />} />
                  </Form.Item>
                  <Form.Item name="role" label="系统角色">
                    <Select options={[
                      { value: "user", label: "成员" },
                      { value: "admin", label: "管理员" },
                    ]} />
                  </Form.Item>
                  <div className="tz-member-form-actions">
                    <Button
                      htmlType="submit"
                      icon={<SaveOutlined />}
                      loading={identityMutation.isPending}
                    >
                      保存资料
                    </Button>
                    <Popconfirm
                      title={detail.is_active ? "停用这位成员？" : "重新启用这位成员？"}
                      description={detail.is_active ? "停用后，该成员将无法继续登录。" : "启用后，该成员可以重新登录。"}
                      okText={detail.is_active ? "确认停用" : "确认启用"}
                      cancelText="取消"
                      onConfirm={() => statusMutation.mutate(!detail.is_active)}
                    >
                      <Button danger={detail.is_active} icon={<StopOutlined />}>
                        {detail.is_active ? "停用成员" : "启用成员"}
                      </Button>
                    </Popconfirm>
                  </div>
                </Form>
              </section>

              <section className="tz-member-section tz-member-access-section">
                <div className="tz-member-section-heading">
                  <div><SafetyCertificateOutlined /><span><strong>资源授权</strong><small>客户角色为默认权限，项目角色可单独覆盖</small></span></div>
                  {!detail.has_global_access && catalogQuery.isSuccess ? (
                    <Button
                      type="primary"
                      icon={<SaveOutlined />}
                      onClick={saveAccess}
                      loading={accessMutation.isPending}
                    >
                      保存授权
                    </Button>
                  ) : null}
                </div>

                {detail.has_global_access ? (
                  <div className="tz-global-access-note">
                    <SafetyCertificateOutlined />
                    <span><strong>全局访问权限</strong><small>系统管理员可以访问组织内所有客户、项目和管理配置。</small></span>
                  </div>
                ) : catalogQuery.isError && catalogFailure ? (
                  <OperationalState
                    kind="error"
                    title="授权资源加载失败"
                    description={`${catalogFailure.message} 成员身份资料仍可查看，现有授权不会被修改。`}
                    diagnostic={catalogFailure.diagnostic}
                    actionLabel="重试"
                    onAction={() => void catalogQuery.refetch()}
                  />
                ) : catalogQuery.isLoading ? (
                  <div className="tz-user-access-loading">
                    <Skeleton active paragraph={{ rows: 4 }} />
                  </div>
                ) : (
                  <div className="tz-access-groups">
                    <AccessGroup
                      title="客户范围"
                      description="勾选客户后，成员可进入该客户及其项目。"
                    >
                      {(catalogQuery.data?.clients ?? []).map((client) => (
                        <AccessRow
                          key={client.id}
                          label={client.name}
                          checked={clientRoles[client.id] != null}
                          role={clientRoles[client.id] ?? "operator"}
                          onChecked={(checked) => setClientRoles((current) => {
                            const next = { ...current };
                            if (checked) next[client.id] = "operator";
                            else delete next[client.id];
                            return next;
                          })}
                          onRole={(role) => setClientRoles((current) => ({ ...current, [client.id]: role }))}
                        />
                      ))}
                    </AccessGroup>
                    <AccessGroup
                      title="项目覆盖"
                      description="只在职责不同于客户默认角色时设置。"
                    >
                      {(catalogQuery.data?.clients ?? []).flatMap((client) => {
                        const projects = groupedProjects.get(client.id) ?? [];
                        if (projects.length === 0) return [];
                        return [
                          <div className="tz-project-access-cluster" key={client.id}>
                            <span className="tz-project-client-label">{client.name}</span>
                            {projects.map((project) => (
                              <AccessRow
                                key={project.id}
                                label={project.name}
                                checked={projectRoles[project.id] != null}
                                role={projectRoles[project.id] ?? "operator"}
                                onChecked={(checked) => setProjectRoles((current) => {
                                  const next = { ...current };
                                  if (checked) next[project.id] = "operator";
                                  else delete next[project.id];
                                  return next;
                                })}
                                onRole={(role) => setProjectRoles((current) => ({ ...current, [project.id]: role }))}
                              />
                            ))}
                          </div>,
                        ];
                      })}
                    </AccessGroup>
                  </div>
                )}
              </section>
            </>
          )}
        </main>
      </section>

      <Modal
        title="邀请成员"
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        onOk={() => createForm.submit()}
        confirmLoading={createMutation.isPending}
        okText="创建成员"
        cancelText="取消"
      >
        <Form
          form={createForm}
          layout="vertical"
          requiredMark={false}
          initialValues={{ role: "user" }}
          onFinish={(values) => createMutation.mutate(values)}
        >
          <Form.Item name="display_name" label="姓名" rules={[{ required: true }]}>
            <Input prefix={<UserOutlined />} />
          </Form.Item>
          <Form.Item name="email" label="邮箱" rules={[{ required: true, type: "email" }]}>
            <Input prefix={<MailOutlined />} />
          </Form.Item>
          <Form.Item name="password" label="初始密码" rules={[{ required: true, min: 8, message: "至少 8 位" }]}>
            <Input.Password />
          </Form.Item>
          <Form.Item name="role" label="系统角色">
            <Select options={[
              { value: "user", label: "成员" },
              { value: "admin", label: "管理员" },
            ]} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}

function MemberRow({ user, selected, onSelect }: { user: User; selected: boolean; onSelect: () => void }) {
  return (
    <button
      type="button"
      className={`tz-member-row${selected ? " is-selected" : ""}`}
      onClick={onSelect}
      aria-label={`${user.display_name} ${user.email}`}
    >
      <span className="tz-member-avatar">{user.display_name.slice(0, 1)}</span>
      <span className="tz-member-row-copy">
        <strong>{user.display_name}</strong>
        <small>{user.email}</small>
      </span>
      <span className="tz-member-row-meta">
        <small>{user.role === "admin" ? "管理员" : "成员"}</small>
        <i className={user.is_active ? "is-active" : "is-inactive"} />
      </span>
    </button>
  );
}

function AccessGroup({
  title,
  description,
  children,
}: {
  title: string;
  description: string;
  children: React.ReactNode;
}) {
  return (
    <section className="tz-access-group">
      <header><strong>{title}</strong><span>{description}</span></header>
      <div>{children}</div>
    </section>
  );
}

function AccessRow({
  label,
  checked,
  role,
  onChecked,
  onRole,
}: {
  label: string;
  checked: boolean;
  role: WorkspaceRole;
  onChecked: (checked: boolean) => void;
  onRole: (role: WorkspaceRole) => void;
}) {
  return (
    <div className={`tz-access-row${checked ? " is-selected" : ""}`}>
      <Checkbox checked={checked} onChange={(event) => onChecked(event.target.checked)} aria-label={label}>
        {label}
      </Checkbox>
      {checked ? (
        <Select
          value={role}
          options={WORKSPACE_ROLE_OPTIONS}
          onChange={onRole}
          aria-label={`${label}角色`}
          popupMatchSelectWidth={false}
        />
      ) : <span className="tz-access-unassigned">未授权</span>}
    </div>
  );
}
