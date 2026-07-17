import {
  CheckCircleFilled,
  ClockCircleOutlined,
  ExclamationCircleFilled,
  PlayCircleOutlined,
} from "@ant-design/icons";
import { Button, Empty, Progress, Tag } from "antd";

import type { Account, AccountGroup, Platform, Project } from "../../types";

const PLATFORM_LABEL: Record<Platform, string> = {
  douyin: "抖音",
  xiaohongshu: "小红书",
  shipinhao: "视频号",
};

interface AccountCollectionProps {
  accounts: Account[];
  projects: Project[];
  groups: AccountGroup[];
  currentAccountId: number | null;
  onSelectAccount: (accountId: number) => void;
}

export function AccountSummaryStrip({ accounts }: { accounts: Account[] }) {
  const authorized = accounts.filter((account) => account.auth_status === "authorized").length;
  const syncing = accounts.filter((account) => account.data_sync_status === "healthy").length;
  const running = accounts.filter((account) => account.current_task).length;
  const risks = accounts.reduce((total, account) => total + (account.risk_count ?? 0), 0);

  return (
    <div className="account-summary-strip" aria-label="账号矩阵概览">
      <SummaryMetric label="矩阵账号" value={accounts.length} hint="当前筛选范围" />
      <SummaryMetric label="官方授权" value={authorized} hint="可读取真实账号数据" tone="success" />
      <SummaryMetric label="任务运行中" value={running} hint="已绑定运营任务" />
      <SummaryMetric label="待处理风险" value={risks} hint="需要人工关注" tone={risks > 0 ? "warning" : "default"} />
      <SummaryMetric label="数据正常" value={syncing} hint="最近同步无异常" tone="success" />
    </div>
  );
}

export function AccountCardsView(props: AccountCollectionProps) {
  const groupName = new Map(props.groups.map((group) => [group.id, group.name]));
  const projectName = new Map(props.projects.map((project) => [project.id, project.name]));

  if (props.accounts.length === 0) return <Empty description="当前筛选下没有账号" />;

  return (
    <div className="account-card-grid">
      {props.accounts.map((account) => (
        <article
          className={`account-object-card${props.currentAccountId === account.id ? " is-current" : ""}`}
          key={account.id}
        >
          <div className="account-object-card__header">
            <AccountIdentity account={account} />
            <Tag bordered={false}>{PLATFORM_LABEL[account.platform]}</Tag>
          </div>
          <p className="account-object-card__positioning">
            {account.positioning_summary || "尚未沉淀账号定位，建议先调用账号定位专家。"}
          </p>
          <div className="account-object-card__meta">
            <span>{account.project_id ? projectName.get(account.project_id) ?? `项目 #${account.project_id}` : "未绑定项目"}</span>
            <span>{account.group_id ? groupName.get(account.group_id) ?? `分组 #${account.group_id}` : "未分组"}</span>
          </div>
          <TaskSnapshot account={account} />
          <div className="account-object-card__footer">
            <AccountSignals account={account} />
            <Button
              size="small"
              type={props.currentAccountId === account.id ? "primary" : "default"}
              icon={<CheckCircleFilled />}
              onClick={() => props.onSelectAccount(account.id)}
            >
              {props.currentAccountId === account.id ? "当前账号" : "设为当前"}
            </Button>
          </div>
        </article>
      ))}
    </div>
  );
}

export function AccountProjectsView(props: AccountCollectionProps) {
  const groups = buildProjectGroups(props.accounts, props.projects);
  const groupName = new Map(props.groups.map((group) => [group.id, group.name]));

  if (props.accounts.length === 0) return <Empty description="当前筛选下没有账号" />;

  return (
    <div className="account-project-list">
      {groups.map((project) => (
        <section className="account-project-band" key={project.id ?? "unbound"}>
          <header className="account-project-band__header">
            <div>
              <span className="account-project-band__eyebrow">客户 / 项目</span>
              <h3>{project.name}</h3>
            </div>
            <span>{project.accounts.length} 个账号</span>
          </header>
          <div className="account-project-band__rows">
            {project.accounts.map((account) => (
              <div className="account-project-row" key={`${project.id ?? "unbound"}-${account.id}`}>
                <AccountIdentity account={account} />
                <div className="account-project-row__positioning">
                  <span>账号定位</span>
                  <strong>{account.positioning_summary || "待账号定位专家分析"}</strong>
                </div>
                <div className="account-project-row__task">
                  <span>当前任务</span>
                  <strong>{account.current_task?.title ?? "暂无运行任务"}</strong>
                </div>
                <div className="account-project-row__group">
                  <span>分组</span>
                  <strong>{account.group_id ? groupName.get(account.group_id) ?? `#${account.group_id}` : "未分组"}</strong>
                </div>
                <AccountSignals account={account} />
                <Button
                  size="small"
                  type={props.currentAccountId === account.id ? "primary" : "text"}
                  icon={<CheckCircleFilled />}
                  onClick={() => props.onSelectAccount(account.id)}
                >
                  {props.currentAccountId === account.id ? "当前" : "切换"}
                </Button>
              </div>
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}

function SummaryMetric({
  label,
  value,
  hint,
  tone = "default",
}: {
  label: string;
  value: number;
  hint: string;
  tone?: "default" | "success" | "warning";
}) {
  return (
    <div className={`account-summary-metric account-summary-metric--${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
      <small>{hint}</small>
    </div>
  );
}

function AccountIdentity({ account }: { account: Account }) {
  return (
    <div className="account-identity">
      {account.avatar_url ? (
        <img src={account.avatar_url} alt="" className="account-identity__avatar" />
      ) : (
        <span className="account-identity__avatar account-identity__avatar--fallback">
          {account.nickname.slice(0, 1)}
        </span>
      )}
      <div>
        <strong>{account.nickname}</strong>
        <span>{account.external_account_id || "未绑定平台 ID"}</span>
      </div>
    </div>
  );
}

function TaskSnapshot({ account }: { account: Account }) {
  if (!account.current_task) {
    return (
      <div className="account-task-snapshot account-task-snapshot--idle">
        <ClockCircleOutlined />
        <span>当前没有运行中的运营任务</span>
      </div>
    );
  }
  return (
    <div className="account-task-snapshot">
      <div>
        <PlayCircleOutlined />
        <strong>{account.current_task.title}</strong>
        <span>{account.current_task.current_focus || "主 Agent 正在推进"}</span>
      </div>
      <Progress percent={account.current_task.progress} showInfo={false} size="small" />
    </div>
  );
}

function AccountSignals({ account }: { account: Account }) {
  const riskCount = account.risk_count ?? 0;
  const publishCapability = account.publish_capability ?? "unavailable";
  const publishLabel =
    publishCapability === "prepare_only"
      ? "可准备发布包"
      : publishCapability === "manual_only"
        ? "人工发布"
        : "暂不可发布";
  return (
    <div className="account-signal-row">
      <span className={account.auth_status === "authorized" ? "is-success" : "is-muted"}>
        <CheckCircleFilled /> {account.auth_status === "authorized" ? "已授权" : "未授权"}
      </span>
      <span className={account.data_sync_status === "healthy" ? "is-success" : "is-muted"}>
        <ClockCircleOutlined /> {formatSyncTime(account.last_sync_at)}
      </span>
      <span className={publishCapability === "prepare_only" ? "is-success" : "is-muted"}>
        <PlayCircleOutlined /> {publishLabel}
      </span>
      {riskCount > 0 && (
        <span className="is-warning">
          <ExclamationCircleFilled /> {riskCount} 项风险
        </span>
      )}
    </div>
  );
}

function formatSyncTime(value?: string | null): string {
  if (!value) return "待同步";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "已同步";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);
}

function buildProjectGroups(accounts: Account[], projects: Project[]) {
  const projectName = new Map(projects.map((project) => [project.id, project.name]));
  const buckets = new Map<number | null, Account[]>();
  accounts.forEach((account) => {
    const ids = account.project_ids?.length
      ? account.project_ids
      : account.project_id
        ? [account.project_id]
        : [null];
    ids.forEach((id) => buckets.set(id, [...(buckets.get(id) ?? []), account]));
  });
  return Array.from(buckets.entries()).map(([id, rows]) => ({
    id,
    name: id == null ? "未绑定项目" : projectName.get(id) ?? `项目 #${id}`,
    accounts: rows,
  }));
}
