import {
  ApiOutlined,
  CheckCircleFilled,
  ClockCircleFilled,
  ExclamationCircleFilled,
  SendOutlined,
  SwapOutlined,
} from "@ant-design/icons";
import { useMutation, useQuery } from "@tanstack/react-query";
import {
  App as AntApp,
  Button,
  Empty,
  Input,
  Progress,
  Segmented,
  Space,
  Tag,
  Typography,
} from "antd";
import { useMemo, useState } from "react";

import {
  confirmBrainTask,
  draftBrainTask,
  listBrainTasks,
} from "../api/brain";
import { listAccounts } from "../api/workspace";
import { AgentOrchestration } from "../components/brain/AgentOrchestration";
import { PageHeader, Panel } from "../components/ui";
import { useCurrentWorkspace } from "../stores/currentWorkspace";
import { silverTagStyle } from "../theme/styles";
import type {
  Account,
  BrainTask,
  BrainTaskGroupBy,
  BrainTaskStatus,
  OrchestrationPlanStep,
} from "../types";

const STATUS_LABEL: Record<BrainTaskStatus, string> = {
  draft: "草稿",
  pending_confirmation: "待确认",
  running: "执行中",
  pending_acceptance: "待验收",
  completed: "已完成",
  failed: "失败",
};

const STATUS_ORDER: BrainTaskStatus[] = [
  "pending_confirmation",
  "running",
  "pending_acceptance",
  "completed",
  "failed",
  "draft",
];

const STATUS_TONE: Record<BrainTaskStatus, string> = {
  draft: "var(--dy-faint)",
  pending_confirmation: "var(--dy-warning)",
  running: "var(--dy-info)",
  pending_acceptance: "var(--dy-warning)",
  completed: "var(--dy-success)",
  failed: "var(--dy-error)",
};

export default function BrainHome() {
  const { message } = AntApp.useApp();
  const [goal, setGoal] = useState("");
  const [draft, setDraft] = useState<BrainTask | null>(null);
  const [previewGoal, setPreviewGoal] = useState("");
  const [localTasks, setLocalTasks] = useState<BrainTask[]>([]);
  const [groupBy, setGroupBy] = useState<BrainTaskGroupBy>("status");
  const { accountId, setAccountId } = useCurrentWorkspace();

  const accountsQuery = useQuery({ queryKey: ["accounts"], queryFn: () => listAccounts() });
  const tasksQuery = useQuery({ queryKey: ["brain-tasks"], queryFn: listBrainTasks });

  const douyinAccounts = useMemo(
    () => (accountsQuery.data ?? []).filter((account) => account.platform === "douyin"),
    [accountsQuery.data],
  );
  const authorizedAccounts = useMemo(
    () =>
      douyinAccounts.filter(
        (account) =>
          account.status === "active" &&
          (account.auth_status === "authorized" || account.auth_status === "manual"),
      ),
    [douyinAccounts],
  );
  const activeAccount = useMemo(
    () => authorizedAccounts.find((account) => account.id === accountId) ?? null,
    [accountId, authorizedAccounts],
  );

  const draftMutation = useMutation({
    mutationFn: draftBrainTask,
    onSuccess: (task) => {
      setDraft(task);
      message.success("任务 Brief 已生成，确认后开始调度专家团");
    },
    onError: (error) => message.error(getErrorMessage(error)),
  });

  const confirmMutation = useMutation({
    mutationFn: confirmBrainTask,
    onSuccess: (task) => {
      setLocalTasks((prev) => [task, ...prev.filter((item) => item.id !== task.id)]);
      setDraft(null);
      setGoal("");
      message.success("主 Agent 已开始调度专家团");
    },
    onError: (error) => message.error(getErrorMessage(error)),
  });

  const tasks = useMemo(
    () => mergeTasks(localTasks, tasksQuery.data ?? []),
    [localTasks, tasksQuery.data],
  );
  const accountScopedTasks = useMemo(
    () =>
      activeAccount
        ? tasks.filter((task) => task.brief.account_ids.includes(activeAccount.id))
        : tasks,
    [activeAccount, tasks],
  );
  const confirmableTask =
    draft ??
    accountScopedTasks.find((task) => task.status === "pending_confirmation") ??
    null;
  const activeTask =
    draft ??
    accountScopedTasks.find((task) => task.status === "running") ??
    accountScopedTasks[0] ??
    null;
  const groupedTasks = useMemo(
    () => groupTasks(accountScopedTasks, groupBy),
    [accountScopedTasks, groupBy],
  );
  const orchestrationGoal = previewGoal || activeTask?.brief.goal || "";

  const createDraft = () => {
    const trimmed = goal.trim();
    if (!activeAccount) {
      message.warning("先选择一个已授权的抖音账号");
      return;
    }
    if (!trimmed) {
      message.warning("先写下要交给主 Agent 的运营目标");
      return;
    }

    setPreviewGoal(trimmed);
    draftMutation.mutate({
      goal: trimmed,
      project_id: activeAccount.project_id,
      account_group_id: activeAccount.group_id,
      platforms: ["douyin"],
      account_ids: [activeAccount.id],
    });
  };

  return (
    <div className="dy-brain-page">
      <PageHeader
        title="运营大脑"
        subtitle="先锁定账号，再把运营目标交给主 Agent；专家团负责拆解、生成、校验与交付。"
        extra={
          <Space size={8} wrap>
            <Tag style={{ marginInlineEnd: 0, ...silverTagStyle }}>AI + Agent + 运营</Tag>
            <Tag style={{ marginInlineEnd: 0 }}>当前平台：抖音</Tag>
          </Space>
        }
      />

      <div className="dy-brain-layout">
        <main className="dy-brain-main">
          <AccountContextCard
            activeAccount={activeAccount}
            accounts={authorizedAccounts}
            loading={accountsQuery.isLoading}
            onSelect={setAccountId}
          />

          <Panel>
            <div className="dy-brain-command">
              <div>
                <div className="dy-brain-kicker">主 Agent 输入</div>
                <h2>从一句话，到一整套执行</h2>
                <p>
                  主 Agent 会先生成 Brief，再调用账号定位、内容策略、脚本与运营专家。
                  高风险动作会停在人工验收。
                </p>
              </div>

              <Input.TextArea
                value={goal}
                onChange={(event) => setGoal(event.target.value)}
                rows={5}
                maxLength={420}
                showCount
                placeholder="例如：分析这个抖音账号最近内容方向，给出一轮低风险冷启动选题、脚本结构和发布后复盘指标。"
                className="dy-brain-input"
              />

              <div className="dy-brain-command-footer">
                <div className="dy-brain-prompts">
                  {["账号定位诊断", "冷启动内容", "脚本生成", "发布前检查"].map((item) => (
                    <button
                      key={item}
                      type="button"
                      onClick={() => setGoal((prev) => appendGoal(prev, item))}
                    >
                      {item}
                    </button>
                  ))}
                </div>
                <Button
                  type="primary"
                  size="large"
                  icon={<SendOutlined />}
                  loading={draftMutation.isPending}
                  disabled={!activeAccount}
                  onClick={createDraft}
                >
                  交给主 Agent
                </Button>
              </div>
            </div>
          </Panel>

          <Panel title="专家团编排">
            <AgentOrchestration goal={orchestrationGoal} />
          </Panel>

          {confirmableTask && (
            <BriefPanel
              task={confirmableTask}
              confirming={confirmMutation.isPending}
              onConfirm={() => confirmMutation.mutate(confirmableTask)}
            />
          )}

          <Panel
            title="账号任务流"
            extra={
              <Segmented
                size="small"
                value={groupBy}
                onChange={(value) => setGroupBy(value as BrainTaskGroupBy)}
                options={[
                  { label: "状态", value: "status" },
                  { label: "项目", value: "project" },
                  { label: "账号组", value: "account_group" },
                  { label: "类型", value: "task_type" },
                ]}
              />
            }
          >
            {tasksQuery.isLoading ? (
              <TaskSkeleton />
            ) : accountScopedTasks.length === 0 ? (
              <Empty description="这个账号还没有运营大脑任务" />
            ) : (
              <div className="dy-brain-task-groups">
                {groupedTasks.map((group) => (
                  <section key={group.label}>
                    <div className="dy-brain-group-label">
                      {group.label} · {group.items.length}
                    </div>
                    <div className="dy-brain-task-list">
                      {group.items.map((task) => (
                        <TaskRow key={task.id} task={task} />
                      ))}
                    </div>
                  </section>
                ))}
              </div>
            )}
          </Panel>
        </main>

        <aside className="dy-brain-side">
          <RiskRail task={activeTask} />
          <DispatchPanel task={activeTask} />
        </aside>
      </div>
    </div>
  );
}

function AccountContextCard({
  activeAccount,
  accounts,
  loading,
  onSelect,
}: {
  activeAccount: Account | null;
  accounts: Account[];
  loading: boolean;
  onSelect: (accountId: number | null) => void;
}) {
  if (activeAccount) {
    return (
      <Panel>
        <div className="dy-brain-account-ready">
          <div>
            <div className="dy-brain-kicker">当前账号</div>
            <h2>{activeAccount.nickname}</h2>
            <p>抖音已授权 · 数据同步 {syncLabel(activeAccount.data_sync_status)}</p>
          </div>
          <div className="dy-brain-account-actions">
            <Tag style={{ marginInlineEnd: 0 }}>#{activeAccount.id}</Tag>
            <Button icon={<SwapOutlined />} onClick={() => onSelect(null)}>
              切换账号
            </Button>
          </div>
        </div>
      </Panel>
    );
  }

  return (
    <Panel>
      <div className="dy-brain-account-gate">
        <div className="dy-brain-gate-icon">
          <ApiOutlined />
        </div>
        <div>
          <div className="dy-brain-kicker">开始前</div>
          <h2>先选择一个已授权抖音账号</h2>
          <p>运营大脑会把所有分析、内容和复盘都绑定到当前账号，避免任务脱离真实运营对象。</p>
          {loading ? (
            <div className="dy-brain-account-options">
              <span>正在读取账号矩阵...</span>
            </div>
          ) : accounts.length > 0 ? (
            <div className="dy-brain-account-options">
              {accounts.map((account) => (
                <button key={account.id} type="button" onClick={() => onSelect(account.id)}>
                  {account.nickname}
                </button>
              ))}
            </div>
          ) : (
            <div className="dy-brain-account-options">
              <span>暂无可用账号，请先在账号矩阵完成抖音授权。</span>
            </div>
          )}
        </div>
      </div>
    </Panel>
  );
}

function BriefPanel({
  task,
  confirming,
  onConfirm,
}: {
  task: BrainTask;
  confirming: boolean;
  onConfirm: () => void;
}) {
  const brief = task.brief;
  return (
    <Panel
      title="待确认 Brief"
      extra={
        <Button type="primary" loading={confirming} onClick={onConfirm}>
          确认并执行
        </Button>
      }
      style={{ borderColor: "rgba(212,163,42,0.36)" }}
    >
      <div className="dy-brain-brief">
        <Typography.Paragraph style={{ margin: 0, color: "var(--dy-text)" }}>
          {brief.goal}
        </Typography.Paragraph>
        <div className="dy-brain-info-grid">
          <Info label="平台" value="抖音" />
          <Info label="账号 ID" value={brief.account_ids.join(" / ")} />
          <Info label="账号组" value={brief.account_group_name ?? "未绑定账号组"} />
          <Info label="内容目标" value={brief.content_goal} />
        </div>
        <PlanPreview steps={task.plan.steps} />
        <TagList title="风险约束" items={brief.risk_constraints} tone="warning" />
        <TagList title="预计产出" items={brief.expected_outputs} />
        <TagList title="人工确认" items={brief.confirmation_actions} tone="warning" />
      </div>
    </Panel>
  );
}

function PlanPreview({ steps }: { steps: OrchestrationPlanStep[] }) {
  return (
    <div className="dy-brain-plan-preview">
      {steps.map((step, index) => (
        <article key={step.id}>
          <div className="dy-tabular">{String(index + 1).padStart(2, "0")}</div>
          <strong>{step.agent_name}</strong>
          <span>{step.expected_output}</span>
          {step.human_gate && <em>需人工验收</em>}
        </article>
      ))}
    </div>
  );
}

function TaskRow({ task }: { task: BrainTask }) {
  const isMatrixTask = task.type === "matrix_distribution";
  const matrixAccounts = task.brief.account_ids.length
    ? `账号 ${task.brief.account_ids.join(" / ")}`
    : "账号待绑定";
  const matrixPlatforms = task.brief.platforms.map(platformLabel).join(" / ");
  const preparesPublishPackage = task.plan.steps.some((step) =>
    step.tool_codes?.includes("publish_package_prepare"),
  );

  return (
    <article className="dy-brain-task-row">
      <div>
        <div className="dy-brain-task-title">
          <span>{task.title}</span>
          <StatusPill status={task.status} />
        </div>
        <p>{task.current_focus}</p>
        {isMatrixTask && (
          <Space size={[6, 6]} wrap style={{ marginTop: 8 }}>
            <Tag color="processing" style={{ marginInlineEnd: 0 }}>
              矩阵计划
            </Tag>
            <Tag style={{ marginInlineEnd: 0 }}>{matrixPlatforms || "平台待绑定"}</Tag>
            <Tag style={{ marginInlineEnd: 0 }}>{matrixAccounts}</Tag>
            <Tag style={{ marginInlineEnd: 0 }}>
              {preparesPublishPackage ? "发布包准备" : "任务拆解"}
            </Tag>
          </Space>
        )}
      </div>
      <Progress
        percent={task.progress}
        size="small"
        showInfo={false}
        strokeColor="var(--dy-accent)"
        trailColor="var(--dy-border)"
      />
      <span className="dy-brain-risk-count">风险 {task.risk_count}</span>
    </article>
  );
}

function RiskRail({ task }: { task: BrainTask | null }) {
  return (
    <Panel title="关键风险">
      {!task ? (
        <Empty description="暂无任务风险" image={Empty.PRESENTED_IMAGE_SIMPLE} />
      ) : (
        <div className="dy-brain-risk-list">
          {task.brief.risk_constraints.map((risk) => (
            <div key={risk}>
              <ExclamationCircleFilled />
              <span>{risk}</span>
            </div>
          ))}
          <div className="dy-brain-focus">
            <Info label="当前焦点" value={task.current_focus} />
          </div>
        </div>
      )}
    </Panel>
  );
}

function DispatchPanel({ task }: { task: BrainTask | null }) {
  return (
    <Panel title="专家调度">
      {!task ? (
        <Empty description="暂无调度记录" image={Empty.PRESENTED_IMAGE_SIMPLE} />
      ) : (
        <div className="dy-brain-dispatch">
          {task.plan.steps.slice(0, 5).map((step) => (
            <div key={step.id}>
              <DispatchIcon status={step.status} />
              <div>
                <strong>{step.agent_name}</strong>
                <p>{step.intent}</p>
                <span>{step.execution_kind ?? "agent_step"}</span>
              </div>
            </div>
          ))}
          <div className="dy-brain-cost">
            预计模型成本：${Number(task.plan.estimated_cost).toFixed(2)}
          </div>
        </div>
      )}
    </Panel>
  );
}

function DispatchIcon({ status }: { status: OrchestrationPlanStep["status"] }) {
  if (status === "done") return <CheckCircleFilled style={{ color: "var(--dy-success)" }} />;
  if (status === "blocked") return <ClockCircleFilled style={{ color: "var(--dy-warning)" }} />;
  return <span className="dy-brain-dispatch-dot" />;
}

function StatusPill({ status }: { status: BrainTaskStatus }) {
  return (
    <span className="dy-brain-status" style={{ color: STATUS_TONE[status] }}>
      <span style={{ background: STATUS_TONE[status] }} />
      {STATUS_LABEL[status]}
    </span>
  );
}

function TagList({ title, items, tone }: { title: string; items: string[]; tone?: "warning" }) {
  return (
    <div>
      <div className="dy-brain-tag-title">{title}</div>
      <Space size={[6, 6]} wrap>
        {items.map((item) => (
          <Tag
            key={item}
            style={{
              marginInlineEnd: 0,
              ...(tone === "warning"
                ? {
                    color: "var(--dy-warning)",
                    borderColor: "rgba(212,163,42,0.38)",
                    background: "transparent",
                  }
                : silverTagStyle),
            }}
          >
            {item}
          </Tag>
        ))}
      </Space>
    </div>
  );
}

function Info({ label, value }: { label: string; value: string }) {
  return (
    <div className="dy-brain-info">
      <span>{label}</span>
      <Typography.Text ellipsis={{ tooltip: value }}>{value}</Typography.Text>
    </div>
  );
}

function TaskSkeleton() {
  return (
    <div className="dy-brain-task-list">
      {Array.from({ length: 4 }).map((_, index) => (
        <div key={index} className="dy-brain-skeleton" />
      ))}
    </div>
  );
}

function groupTasks(tasks: BrainTask[], groupBy: BrainTaskGroupBy) {
  if (groupBy === "status") {
    return STATUS_ORDER.map((status) => ({
      label: STATUS_LABEL[status],
      items: tasks.filter((task) => task.status === status),
    })).filter((group) => group.items.length > 0);
  }

  const getKey = (task: BrainTask) => {
    if (groupBy === "project") return task.brief.project_name ?? "未绑定项目";
    if (groupBy === "account_group") return task.brief.account_group_name ?? "未绑定账号组";
    return TASK_TYPE_LABEL[task.type];
  };

  return Array.from(
    tasks.reduce((acc, task) => {
      const key = getKey(task);
      acc.set(key, [...(acc.get(key) ?? []), task]);
      return acc;
    }, new Map<string, BrainTask[]>()),
    ([label, items]) => ({ label, items }),
  );
}

function mergeTasks(localTasks: BrainTask[], serverTasks: BrainTask[]) {
  const byId = new Map<number, BrainTask>();
  [...localTasks, ...serverTasks].forEach((task) => byId.set(task.id, task));
  return Array.from(byId.values()).sort((a, b) => b.id - a.id);
}

function appendGoal(current: string, fragment: string) {
  const trimmed = current.trim();
  return trimmed ? `${trimmed}；${fragment}` : fragment;
}

function syncLabel(status: Account["data_sync_status"]) {
  const labels: Record<Account["data_sync_status"], string> = {
    not_configured: "未配置",
    pending: "待同步",
    syncing: "同步中",
    healthy: "正常",
    failed: "失败",
    manual: "手动",
  };
  return labels[status] ?? status;
}

function platformLabel(platform: BrainTask["brief"]["platforms"][number]) {
  const labels: Record<BrainTask["brief"]["platforms"][number], string> = {
    douyin: "抖音",
    xiaohongshu: "小红书",
    shipinhao: "视频号",
  };
  return labels[platform] ?? platform;
}

function getErrorMessage(error: unknown) {
  if (
    typeof error === "object" &&
    error != null &&
    "response" in error &&
    typeof (error as { response?: { data?: { detail?: unknown } } }).response?.data?.detail ===
      "string"
  ) {
    return (error as { response: { data: { detail: string } } }).response.data.detail;
  }
  return "操作失败，请稍后重试";
}

const TASK_TYPE_LABEL: Record<BrainTask["type"], string> = {
  content_creation: "内容生产",
  account_diagnosis: "账号诊断",
  review_optimization: "复盘优化",
  matrix_distribution: "矩阵分发",
};
