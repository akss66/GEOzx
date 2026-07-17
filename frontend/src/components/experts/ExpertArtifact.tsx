import {
  BookOutlined,
  CheckOutlined,
  CopyOutlined,
  EditOutlined,
  SwapOutlined,
} from "@ant-design/icons";
import { Button, Popconfirm } from "antd";
import type { ReactNode } from "react";

import type { AgentDirectRun } from "../../types";

const FIELD_LABELS: Record<string, string> = {
  account_persona: "账号定位",
  target_audience: "目标人群",
  differentiation: "差异化方向",
  content_pillars: "内容支柱",
  title: "标题",
  hook: "开场钩子",
  scenes: "镜头结构",
  duration_seconds: "建议时长",
  bgm_suggestion: "音乐建议",
  visual_style: "视觉风格",
  prompts: "画面提示",
  negative_prompt: "排除内容",
  aspect_ratio: "画幅",
  cut_plan: "剪辑结构",
  captions: "字幕重点",
  transitions: "转场节奏",
  deliverables: "成片清单",
  platform_variants: "平台版本",
  period: "分析周期",
  summary: "核心结论",
  key_metrics: "关键指标",
  highlights: "表现亮点",
  issues: "主要问题",
  optimization_suggestions: "优化建议",
  objective: "投放目标",
  budget_strategy: "预算策略",
  creative_directions: "素材方向",
  risk_controls: "风险控制",
  measurement: "衡量方式",
  common_questions: "高频问题",
  sentiment: "反馈情绪",
  response_guidelines: "回复原则",
  content_opportunities: "内容机会",
};

export function ExpertArtifact({
  run,
  adopting,
  handingOff,
  suggesting,
  onAdopt,
  onRevise,
  onHandoff,
  onSuggest,
  onCopy,
}: {
  run: AgentDirectRun;
  adopting: boolean;
  handingOff: boolean;
  suggesting: boolean;
  onAdopt: () => void;
  onRevise: () => void;
  onHandoff: () => void;
  onSuggest: () => void;
  onCopy: () => void;
}) {
  const approved = run.acceptance.status === "approved";
  return (
    <article className="expert-artifact">
      <header>
        <div>
          <span>FORMAL OUTPUT · V{run.deliverable.version}</span>
          <h3>{run.acceptance.title}</h3>
          <p>{formatDate(run.deliverable.created_at)} · {run.invocation.agent_name}</p>
        </div>
        <strong className={approved ? "is-approved" : ""}>
          {approved ? "已采用" : "待确认"}
        </strong>
      </header>

      <div className="expert-artifact__body">
        {Object.entries(run.deliverable.payload).map(([key, value]) => (
          <section key={key}>
            <h4>{FIELD_LABELS[key] ?? readableKey(key)}</h4>
            <ArtifactValue value={value} field={key} />
          </section>
        ))}
      </div>

      {(run.knowledge_sources ?? []).length > 0 ? (
        <section className="expert-artifact__sources">
          <span>本次参考来源</span>
          {(run.knowledge_sources ?? []).map((source) => (
            <div key={source.id}>
              <strong>{source.title}</strong>
              <small>{source.source_label} · V{source.version}</small>
            </div>
          ))}
        </section>
      ) : null}

      <footer>
        <Popconfirm
          title="确认采用这份成果？"
          description="采用后会写入当前项目与账号的正式成果记录。"
          okText="确认采用"
          cancelText="再看看"
          disabled={approved}
          onConfirm={onAdopt}
        >
          <Button
            type="primary"
            icon={<CheckOutlined />}
            loading={adopting}
            disabled={approved}
            aria-label={approved ? "已采用" : "采用成果"}
          >
            {approved ? "已采用" : "采用成果"}
          </Button>
        </Popconfirm>
        <Button icon={<EditOutlined />} onClick={onRevise}>继续修改</Button>
        <Button icon={<SwapOutlined />} loading={handingOff} onClick={onHandoff}>
          交给主 Agent
        </Button>
        <Popconfirm
          title="将这份成果建议沉淀到知识库？"
          description="成果只会进入待确认建议，需人工审核后才会成为正式知识。"
          okText="确认送审"
          cancelText="取消"
          onConfirm={onSuggest}
        >
          <Button
            icon={<BookOutlined />}
            loading={suggesting}
            aria-label="建议沉淀到知识库"
          >
            建议沉淀
          </Button>
        </Popconfirm>
        <Button icon={<CopyOutlined />} onClick={onCopy}>复制全文</Button>
      </footer>
    </article>
  );
}

function ArtifactValue({ value, field }: { value: unknown; field: string }) {
  if (Array.isArray(value)) {
    return (
      <ul>
        {value.map((item, index) => <li key={`${field}-${index}`}>{renderScalar(item)}</li>)}
      </ul>
    );
  }
  if (value && typeof value === "object") {
    return (
      <dl>
        {Object.entries(value as Record<string, unknown>).map(([key, item]) => (
          <div key={key}><dt>{FIELD_LABELS[key] ?? readableKey(key)}</dt><dd>{renderScalar(item)}</dd></div>
        ))}
      </dl>
    );
  }
  return <p>{renderScalar(value, field)}</p>;
}

function renderScalar(value: unknown, field?: string): ReactNode {
  if (value == null || value === "") return "未填写";
  if (typeof value === "boolean") return value ? "是" : "否";
  if (typeof value === "number") return field === "duration_seconds" ? `${value} 秒` : String(value);
  if (typeof value === "string") return value;
  if (Array.isArray(value)) return value.map(String).join("、");
  if (typeof value === "object") {
    return Object.entries(value as Record<string, unknown>)
      .map(([key, item]) => `${FIELD_LABELS[key] ?? readableKey(key)}：${String(item)}`)
      .join("；");
  }
  return String(value);
}

function readableKey(key: string) {
  return key.replaceAll("_", " ");
}

function formatDate(value: string) {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

export function artifactToText(run: AgentDirectRun) {
  const lines = [run.acceptance.title];
  Object.entries(run.deliverable.payload).forEach(([key, value]) => {
    lines.push(`${FIELD_LABELS[key] ?? readableKey(key)}：${String(renderScalar(value))}`);
  });
  return lines.join("\n\n");
}
