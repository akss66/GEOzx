import {
  AimOutlined,
  ArrowDownOutlined,
  ArrowRightOutlined,
  ArrowUpOutlined,
  BarChartOutlined,
  CheckCircleFilled,
  ClockCircleOutlined,
  DatabaseOutlined,
  EditOutlined,
  LineChartOutlined,
  SendOutlined,
  SyncOutlined,
} from "@ant-design/icons";
import { App as AntApp, Button, Form, InputNumber, Modal, Segmented, Skeleton, Tag } from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import ReactECharts from "echarts-for-react";
import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { sendOptimizationSuggestionToBrain } from "../api/feedback";
import {
  getReviewWorkspace,
  type ReviewGoalInput,
  type ReviewPeriodDays,
  type ReviewWorkspace,
  upsertReviewGoal,
} from "../api/metrics";
import { useCurrentWorkspace } from "../stores/currentWorkspace";
import { useThemeMode } from "../stores/theme";
import { chartBase } from "../theme/echarts";
import { CHART_COLORS } from "../theme/tokens";
import type { ContentStage, OptimizationSuggestion } from "../types";

import "../styles/review-dashboard.css";

const PERIOD_OPTIONS = [
  { label: "近 7 天", value: 7 },
  { label: "近 30 天", value: 30 },
  { label: "近 90 天", value: 90 },
];

const STAGE_LABEL: Record<ContentStage, string> = {
  positioning: "定位",
  content_direction: "内容策略",
  art_direction: "视觉",
  video_creation: "视频",
  editing: "剪辑",
  operation: "运营",
  advertising: "投流",
  customer_service: "客服",
};

export default function ReviewDashboard() {
  const { message } = AntApp.useApp();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const accountId = useCurrentWorkspace((state) => state.accountId);
  const [days, setDays] = useState<ReviewPeriodDays>(30);
  const [goalOpen, setGoalOpen] = useState(false);
  const [goalForm] = Form.useForm<ReviewGoalInput>();

  const workspaceQuery = useQuery({
    queryKey: ["review-workspace", accountId, days],
    queryFn: () => getReviewWorkspace(accountId!, days),
    enabled: accountId != null,
  });
  const goalMutation = useMutation({
    mutationFn: (input: ReviewGoalInput) => upsertReviewGoal(accountId!, input),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["review-workspace", accountId, days] });
      setGoalOpen(false);
      message.success("周期目标已更新");
    },
    onError: () => {
      message.error("周期目标保存失败，请检查账号权限后重试");
    },
  });
  const nextCycleMutation = useMutation({
    mutationFn: sendOptimizationSuggestionToBrain,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["brain-tasks"] });
      message.success("建议已交给运营大脑");
      navigate("/brain");
    },
  });

  const openGoal = () => {
    const goal = workspaceQuery.data?.goal;
    goalForm.resetFields();
    goalForm.setFieldsValue({
      period_days: days,
      target_play: goal?.target_play ?? undefined,
      target_completion_rate:
        goal?.target_completion_rate != null
          ? Number((goal.target_completion_rate * 100).toFixed(2))
          : undefined,
      target_follower_delta: goal?.target_follower_delta ?? undefined,
    });
    setGoalOpen(true);
  };

  const saveGoal = async () => {
    const values = await goalForm.validateFields();
    if (
      values.target_play == null
      && values.target_completion_rate == null
      && values.target_follower_delta == null
    ) {
      message.warning("至少填写一项周期目标");
      return;
    }
    goalMutation.mutate({
      period_days: days,
      target_play: values.target_play || undefined,
      target_completion_rate:
        values.target_completion_rate != null
          ? values.target_completion_rate / 100
          : undefined,
      target_follower_delta: values.target_follower_delta || undefined,
    });
  };

  return (
    <div className="review-page">
      <ReviewHeader days={days} onDaysChange={setDays} />

      {accountId == null ? (
        <ReviewAccountRequired />
      ) : workspaceQuery.isLoading ? (
        <ReviewLoading />
      ) : workspaceQuery.isError || !workspaceQuery.data ? (
        <ReviewError onRetry={() => workspaceQuery.refetch()} />
      ) : workspaceQuery.data.data_status.has_data ? (
        <ReviewReport
          workspace={workspaceQuery.data}
          onOpenGoal={openGoal}
          onCreateNextCycle={(suggestionId) => nextCycleMutation.mutate(suggestionId)}
          sendingSuggestionId={
            nextCycleMutation.isPending ? nextCycleMutation.variables : undefined
          }
        />
      ) : (
        <ReviewDataMissing workspace={workspaceQuery.data} onOpenGoal={openGoal} />
      )}

      <Modal
        title={`设置近 ${days} 天目标`}
        open={goalOpen}
        onCancel={() => setGoalOpen(false)}
        footer={[
          <Button key="cancel" onClick={() => setGoalOpen(false)}>
            取消
          </Button>,
          <Button
            key="save"
            type="primary"
            loading={goalMutation.isPending}
            onClick={saveGoal}
          >
            保存目标
          </Button>,
        ]}
        className="review-goal-modal"
      >
        <p className="review-goal-intro">
          目标只用于当前账号和当前统计周期。至少填写一项，系统会根据真实回流计算整体完成度。
        </p>
        <Form form={goalForm} layout="vertical" requiredMark={false}>
          <Form.Item name="period_days" hidden>
            <InputNumber />
          </Form.Item>
          <Form.Item name="target_play" label="目标播放量">
            <InputNumber min={1} step={1000} precision={0} placeholder="例如 100000" />
          </Form.Item>
          <Form.Item name="target_completion_rate" label="目标平均完播率">
            <InputNumber min={0.01} max={100} step={1} suffix="%" placeholder="例如 35" />
          </Form.Item>
          <Form.Item name="target_follower_delta" label="目标净增粉丝">
            <InputNumber min={1} step={10} precision={0} placeholder="例如 500" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}

function ReviewHeader({
  days,
  onDaysChange,
}: {
  days: ReviewPeriodDays;
  onDaysChange: (days: ReviewPeriodDays) => void;
}) {
  return (
    <header className="review-page-header">
      <div>
        <h1>运营复盘</h1>
        <p>用真实数据解释发生了什么，并把结论交给下一轮运营。</p>
      </div>
      <Segmented
        value={days}
        options={PERIOD_OPTIONS}
        onChange={(value) => onDaysChange(Number(value) as ReviewPeriodDays)}
        aria-label="复盘周期"
      />
    </header>
  );
}

function ReviewAccountRequired() {
  return (
    <section className="review-command-state" aria-label="需要选择账号">
      <span className="review-command-icon"><AimOutlined /></span>
      <div>
        <h2>先选择一个抖音账号</h2>
        <p>运营复盘必须绑定真实账号。请从顶部选择账号，再读取它的目标、内容表现和数据回流。</p>
      </div>
    </section>
  );
}

function ReviewLoading() {
  return (
    <main className="review-report review-report--loading">
      <Skeleton active title={{ width: "62%" }} paragraph={{ rows: 3 }} />
      <Skeleton active title={{ width: "24%" }} paragraph={{ rows: 5 }} />
    </main>
  );
}

function ReviewError({ onRetry }: { onRetry: () => void }) {
  return (
    <section className="review-command-state" role="alert">
      <span className="review-command-icon"><SyncOutlined /></span>
      <div>
        <h2>暂时无法读取复盘数据</h2>
        <p>账号上下文仍然保留，可以重试，不会切换到其他账号的数据。</p>
        <Button onClick={onRetry}>重新读取</Button>
      </div>
    </section>
  );
}

function ReviewDataMissing({
  workspace,
  onOpenGoal,
}: {
  workspace: ReviewWorkspace;
  onOpenGoal: () => void;
}) {
  return (
    <main className="review-report review-report--empty">
      <section className="review-empty-conclusion">
        <div className="review-context-line">
          <Tag>抖音</Tag>
          <strong>{workspace.account.nickname}</strong>
          <span>近 {workspace.period.days} 天</span>
        </div>
        <h2>{workspace.conclusion}</h2>
        <p>页面不会渲染空图表。下面列出当前缺少什么，以及下一步怎样让数据进入复盘。</p>
      </section>
      <section className="review-readiness" aria-label="数据准备情况">
        <header>
          <div>
            <h3>数据准备情况</h3>
            <p>每一项都来自真实账号状态和服务端记录。</p>
          </div>
          <Button icon={<EditOutlined />} onClick={onOpenGoal}>设置周期目标</Button>
        </header>
        <div className="review-readiness-list">
          <ReadinessItem
            ready={workspace.account.auth_status === "authorized"}
            title="账号授权"
            detail={workspace.account.auth_status === "authorized" ? "官方授权有效" : "尚未完成官方授权"}
          />
          <ReadinessItem
            ready={workspace.account.data_sync_status === "healthy"}
            title="数据回流"
            detail={`当前状态：${syncStatusLabel(workspace.account.data_sync_status)}`}
          />
          <ReadinessItem
            ready={workspace.goal.status !== "not_configured"}
            title="周期目标"
            detail={workspace.goal.summary}
          />
        </div>
        <div className="review-missing-reasons">
          {workspace.data_status.missing_reasons.map((reason) => (
            <span key={reason}>{reason}</span>
          ))}
        </div>
      </section>
    </main>
  );
}

function ReadinessItem({ ready, title, detail }: { ready: boolean; title: string; detail: string }) {
  return (
    <div className="review-readiness-item" data-ready={ready}>
      {ready ? <CheckCircleFilled /> : <ClockCircleOutlined />}
      <div><strong>{title}</strong><span>{detail}</span></div>
    </div>
  );
}

function ReviewReport({
  workspace,
  onOpenGoal,
  onCreateNextCycle,
  sendingSuggestionId,
}: {
  workspace: ReviewWorkspace;
  onOpenGoal: () => void;
  onCreateNextCycle: (suggestionId: number) => void;
  sendingSuggestionId?: number;
}) {
  return (
    <main className="review-report">
      <ReviewConclusion workspace={workspace} onOpenGoal={onOpenGoal} />
      <ReviewChanges workspace={workspace} />
      <ReviewAttribution workspace={workspace} />
      <ReviewEvidence workspace={workspace} />
      <ReviewSuggestions
        workspace={workspace}
        onCreateNextCycle={onCreateNextCycle}
        sendingSuggestionId={sendingSuggestionId}
      />
    </main>
  );
}

function ReviewConclusion({
  workspace,
  onOpenGoal,
}: {
  workspace: ReviewWorkspace;
  onOpenGoal: () => void;
}) {
  const goal = workspace.goal;
  const isStale = (workspace.data_status.days_since_observed ?? 0) > 7;
  const hasConflicts = workspace.data_status.conflict_count > 0;
  const suppressConclusion = isStale || hasConflicts;
  const goalConfigured = goal.status !== "not_configured";
  return (
    <section className="review-conclusion">
      <div className="review-conclusion-copy">
        <div className="review-context-line">
          <Tag>抖音</Tag>
          <strong>{workspace.account.nickname}</strong>
          <span>{formatPeriod(workspace)}</span>
        </div>
        <h2>
          {suppressConclusion
            ? "数据需要更新确认后，再形成新的运营结论"
            : workspace.conclusion}
        </h2>
        {suppressConclusion ? (
          <div className="review-quality-strip" aria-label="数据质量提醒">
            <div>
              {isStale ? <strong>数据已过期</strong> : null}
              {hasConflicts ? (
                <strong>{workspace.data_status.conflict_count} 项待处理冲突</strong>
              ) : null}
              <span>现有证据仍可查看，但暂不据此生成确定性建议。</span>
            </div>
            <Link to={`/accounts/${workspace.account.id}/data`}>更新数据</Link>
          </div>
        ) : null}
        <div className="review-source-line">
          <DatabaseOutlined />
          <span>{workspace.data_status.sources.map(metricSourceLabel).join("、")}</span>
          <span>数据截至 {workspace.data_status.latest_stat_date}</span>
          <span>同步于 {formatDateTime(workspace.data_status.latest_synced_at)}</span>
        </div>
      </div>
      <aside
        className="review-goal-summary"
        data-configured={goalConfigured}
        aria-label="目标完成度"
      >
        {goalConfigured ? (
          <>
            <header>
              <span>目标完成度</span>
              <Button type="text" icon={<EditOutlined />} onClick={onOpenGoal}>
                调整目标
              </Button>
            </header>
            <strong>
              {goal.achievement_percent != null
                ? `${goal.achievement_percent.toFixed(1)}%`
                : "待积累数据"}
            </strong>
            <div className="review-progress-track" aria-hidden="true">
              <span style={{ width: `${Math.min(goal.achievement_percent ?? 0, 100)}%` }} />
            </div>
            <p>{goal.summary}</p>
            <div className="review-goal-components">
              {goal.components.map((component) => (
                <span key={component.metric}>
                  <small>{component.label}</small>
                  <b>{component.achievement_percent.toFixed(0)}%</b>
                </span>
              ))}
            </div>
          </>
        ) : (
          <div className="review-goal-empty">
            <AimOutlined />
            <span>运营周期目标</span>
            <strong>为近 {workspace.period.days} 天设定衡量标准</strong>
            <p>可设置播放量、平均完播率或净增粉丝，复盘会自动计算完成度。</p>
            <Button type="primary" icon={<EditOutlined />} onClick={onOpenGoal}>
              设置周期目标
            </Button>
          </div>
        )}
      </aside>
    </section>
  );
}

function ReviewChanges({ workspace }: { workspace: ReviewWorkspace }) {
  return (
    <section className="review-section">
      <SectionHeader title="关键变化" description="与上一等长周期比较，先看方向，再看绝对值。" />
      <div className="review-change-list">
        {workspace.changes.map((change) => (
          <article key={change.metric} className="review-change-row" data-direction={change.direction}>
            <span className="review-change-icon">{directionIcon(change.direction)}</span>
            <div><strong>{change.label}</strong><p>{change.summary}</p></div>
            <div className="review-change-values">
              <b>{formatChangeValue(change.metric, change.current)}</b>
              <span>{change.previous == null ? "首次基线" : `上期 ${formatChangeValue(change.metric, change.previous)}`}</span>
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}

function ReviewAttribution({ workspace }: { workspace: ReviewWorkspace }) {
  return (
    <section className="review-section">
      <SectionHeader title="内容归因" description="把变化落到具体作品，而不是停留在平均数。" />
      <div className="review-attribution-list">
        {workspace.attributions.map((item, index) => (
          <article key={`${item.content_item_id ?? item.title}-${index}`} className="review-attribution-row">
            <span className="review-attribution-rank">{String(index + 1).padStart(2, "0")}</span>
            <div className="review-attribution-copy">
              <div><strong>{item.title}</strong><Tag>{item.role === "driver" ? "增长驱动" : "优化样本"}</Tag></div>
              <p>{item.reason}</p>
            </div>
            <dl>
              <div><dt>播放</dt><dd>{formatNumber(item.play)}</dd></div>
              <div><dt>完播</dt><dd>{formatPercent(item.completion_rate)}</dd></div>
              <div><dt>互动</dt><dd>{formatPercent(item.engagement_rate)}</dd></div>
            </dl>
          </article>
        ))}
      </div>
    </section>
  );
}

function ReviewEvidence({ workspace }: { workspace: ReviewWorkspace }) {
  const mode = useThemeMode((state) => state.mode);
  const base = chartBase(mode);
  const option = useMemo(() => ({
    ...base,
    animationDuration: 420,
    grid: { left: 8, right: 18, top: 28, bottom: 8, containLabel: true },
    tooltip: { ...base.tooltip, trigger: "axis" },
    legend: { ...base.legend, data: ["播放量", "曝光量"], right: 0, top: 0 },
    xAxis: {
      type: "category",
      data: workspace.trend.map((item) => item.date),
      ...base.categoryAxis,
    },
    yAxis: { type: "value", ...base.valueAxis },
    series: [
      {
        name: "曝光量",
        type: "line",
        data: workspace.trend.map((item) => item.exposure),
        symbol: "none",
        smooth: true,
        lineStyle: { width: 1.5, color: CHART_COLORS[5] },
      },
      {
        name: "播放量",
        type: "line",
        data: workspace.trend.map((item) => item.play),
        symbol: "none",
        smooth: true,
        lineStyle: { width: 2.5, color: CHART_COLORS[0] },
      },
    ],
  }), [base, workspace.trend]);

  return (
    <section className="review-section">
      <SectionHeader title="数据证据" description="图表只承担证据角色，原始快照保留来源与时间。" />
      <div className="review-evidence-layout">
        <div className="review-evidence-chart">
          <ReactECharts option={option} style={{ height: 280 }} notMerge />
        </div>
        <div className="review-evidence-ledger">
          {workspace.evidence.slice(0, 5).map((snapshot) => (
            <article key={snapshot.id}>
              <div><strong>{snapshot.title ?? `内容 #${snapshot.content_item_id ?? snapshot.id}`}</strong><span>{snapshot.stat_date}</span></div>
              <p>{formatNumber(snapshot.play)} 播放 · {formatPercent(snapshot.completion_rate)} 完播 · {metricSourceLabel(snapshot.source)}</p>
            </article>
          ))}
        </div>
      </div>
    </section>
  );
}

function ReviewSuggestions({
  workspace,
  onCreateNextCycle,
  sendingSuggestionId,
}: {
  workspace: ReviewWorkspace;
  onCreateNextCycle: (suggestionId: number) => void;
  sendingSuggestionId?: number;
}) {
  return (
    <section className="review-section review-section--suggestions">
      <SectionHeader title="Agent 建议" description="建议保留来源，确认后才进入下一轮正式任务。" />
      {workspace.suggestions.length === 0 ? (
        <div className="review-suggestion-empty">
          <BarChartOutlined />
          <div><strong>暂无可执行建议</strong><p>运营专家完成复盘报告后，建议会在这里进入下一轮闭环。</p></div>
        </div>
      ) : (
        <div className="review-suggestion-list">
          {workspace.suggestions.map((suggestion) => (
            <SuggestionRow
              key={suggestion.id}
              suggestion={suggestion}
              loading={sendingSuggestionId === suggestion.id}
              onCreate={() => onCreateNextCycle(suggestion.id)}
            />
          ))}
        </div>
      )}
    </section>
  );
}

function SuggestionRow({
  suggestion,
  loading,
  onCreate,
}: {
  suggestion: OptimizationSuggestion;
  loading: boolean;
  onCreate: () => void;
}) {
  return (
    <article className="review-suggestion-row">
      <div className="review-suggestion-icon"><LineChartOutlined /></div>
      <div className="review-suggestion-copy">
        <div><Tag>{suggestion.target_stage ? STAGE_LABEL[suggestion.target_stage] : "待判断"}</Tag><span>来源：{suggestion.content_title}</span></div>
        <strong>{suggestion.suggestion}</strong>
        <p>{suggestion.note || "等待运营负责人确认是否进入下一轮。"}</p>
      </div>
      <Button type="primary" icon={<SendOutlined />} loading={loading} onClick={onCreate}>
        创建下一轮任务
      </Button>
    </article>
  );
}

function SectionHeader({ title, description }: { title: string; description: string }) {
  return (
    <header className="review-section-header">
      <h3>{title}</h3>
      <p>{description}</p>
    </header>
  );
}

function directionIcon(direction: ReviewWorkspace["changes"][number]["direction"]) {
  if (direction === "up") return <ArrowUpOutlined />;
  if (direction === "down") return <ArrowDownOutlined />;
  return <ArrowRightOutlined />;
}

function formatNumber(value: number) {
  return new Intl.NumberFormat("zh-CN", { notation: value >= 10000 ? "compact" : "standard", maximumFractionDigits: 1 }).format(value);
}

function formatPercent(value: number) {
  return `${(value * 100).toFixed(1)}%`;
}

function formatChangeValue(metric: string, value: number) {
  return metric === "completion_rate" ? `${value.toFixed(1)}%` : formatNumber(value);
}

function formatPeriod(workspace: ReviewWorkspace) {
  return `${workspace.period.current_start} 至 ${workspace.period.current_end}`;
}

function formatDateTime(value: string | null) {
  if (!value) return "尚未同步";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function metricSourceLabel(source: ReviewWorkspace["data_status"]["sources"][number]) {
  return {
    douyin: "抖音回流",
    xiaohongshu: "小红书回流",
    shipinhao: "视频号回流",
    manual: "人工录入",
    demo: "示例数据",
    platform_export: "平台导出",
    screenshot_verified: "截图核验",
    manual_entry: "人工录入",
    official_api: "官方接口",
  }[source];
}

function syncStatusLabel(status: string) {
  return {
    healthy: "回流正常",
    pending: "等待首次回流",
    syncing: "同步中",
    failed: "同步失败",
    not_configured: "尚未配置",
    manual: "人工维护",
  }[status] ?? status;
}
