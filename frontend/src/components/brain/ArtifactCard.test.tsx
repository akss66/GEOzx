// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Artifact } from "../../types";
import { ArtifactCard } from "./ArtifactCard";

const reviewArtifact = {
  id: 5001,
  account_id: 3,
  thread_id: 81,
  turn_id: 101,
  run_id: 7001,
  skill_run_id: 4001,
  task_id: 21,
  artifact_type: "account_inspection_report",
  presentation: {
    type_label: "账号诊断",
    completion_label: "已完成当前账号运营诊断",
    status_label: "待确认",
    detail_action_label: "查看账号诊断",
  },
  next_actions: [
    {
      code: "generate_next_iteration",
      label: "生成下一轮优化方案",
      requires_confirmation: false,
    },
    { code: "request_revision", label: "提出修改", requires_confirmation: false },
    { code: "export", label: "导出内容", requires_confirmation: false },
  ],
  title: "账号体检报告",
  version: 1,
  status: "ready_for_review",
  summary: "账号具备增长基础，但内容结构仍需收敛。",
  sections: [
    { key: "core_conclusion", title: "核心结论", content: "优先收敛内容主题。" },
    { key: "data_period", title: "数据周期", content: "2026-07-01 至 2026-07-21" },
    { key: "key_metrics", title: "关键数据", content: ["完播率 32%", "互动率 4.8%"] },
    { key: "issues", title: "主要问题", content: ["选题分散"] },
    { key: "optimization_suggestions", title: "优化建议", content: ["建立两个内容支柱"] },
    { key: "participating_experts", title: "调用专家", content: ["内容策略专家"] },
    { key: "acceptance_checklist", title: "验收清单", content: "Confirm that this item is ready" },
    { key: "raw_tool_logs", title: "原始日志", content: "Traceback: secret-token" },
  ],
  evidence_refs: [
    { kind: "specialist", id: 1, label: "内容策略专家" },
    { kind: "metric_snapshot", id: 2, label: "近 21 天账号指标" },
    { kind: "raw_tool_log", id: 3, label: "Traceback: secret-token" },
  ],
  evidence_summary: {
    total: 2,
    groups: [
      { kind: "specialist", label: "专家分析", count: 1, metric_count: 0, period: null },
      { kind: "metric_snapshot", label: "账号指标快照", count: 1, metric_count: 2, period: "近 21 天" },
    ],
  },
  quality: { score: 92, passed: true, issues: [] },
  created_at: "2026-07-28T00:00:00Z",
} satisfies Artifact;

const accountAnalysisArtifact = {
  ...reviewArtifact,
  id: 5002,
  artifact_type: "account_analysis_answer",
  status: "accepted",
  summary: "近 30 天播放量较上一周期增长，但互动效率下降。",
  next_actions: [],
  sections: [
    { key: "conclusion", title: "结论", content: "近 30 天播放量增长 24%，但互动率下降 0.8 个百分点。" },
    {
      key: "key_facts",
      title: "关键事实",
      content: [{
        metric_code: "views",
        label: "播放量",
        unit: "次",
        current_value: 12400,
        previous_value: 10000,
        relative_change: 0.24,
        direction: "up",
        sample_count: 14,
        evidence_hashes: ["sha256:raw-secret"],
      }],
    },
    { key: "interpretation", title: "数据解读", content: ["流量规模扩大，但内容互动承接变弱。"] },
    {
      key: "recommendations",
      title: "建议",
      content: [{
        action: "连续 7 天测试强互动提问式结尾",
        rationale: "当前互动率较上一周期下降",
        validation_metric: "互动率",
        observation_days: 7,
      }],
    },
    { key: "data_limits", title: "数据限制", content: ["当前没有成交数据，不能判断商业转化。"] },
    { key: "next_action", title: "下一步", content: "先执行 7 天互动率提升实验。" },
    { key: "participating_experts", title: "参与专家", content: ["运营执行专家"] },
    { key: "critic", title: "质量审核", content: { passed: true, score: 94 } },
  ],
  evidence_refs: [
    { kind: "field_observation", id: 91, label: "content_hash=sha256:raw-secret" },
    { kind: "field_observation", id: 92, label: "播放量 · 2026-07-01 至 2026-07-30" },
  ],
  evidence_summary: {
    total: 14,
    groups: [{
      kind: "field_observation",
      label: "账号数据字段",
      count: 14,
      metric_count: 2,
      period: "2026-07-01 至 2026-07-30",
    }],
  },
  quality: { score: 94, passed: true, issues: [] },
} satisfies Artifact;

describe("ArtifactCard", () => {
  afterEach(cleanup);

  it("renders account analysis as a readable answer with evidence separated from technical details", () => {
    render(<ArtifactCard artifact={accountAnalysisArtifact} onAction={vi.fn()} />);

    expect(screen.getByRole("heading", { name: "账号数据分析" })).toBeInTheDocument();
    expect(screen.getByText("近 30 天播放量增长 24%，但互动率下降 0.8 个百分点。")).toBeInTheDocument();
    expect(screen.getByText("关键事实")).toBeInTheDocument();
    expect(screen.getByText(/播放量.*12,400.*较上一周期.*24%/)).toBeInTheDocument();
    expect(screen.getByText("数据解读")).toBeInTheDocument();
    expect(screen.getByText("下一步建议")).toBeInTheDocument();
    expect(screen.getByText("数据限制")).toBeInTheDocument();
    expect(screen.getByText("下一步")).toBeInTheDocument();
    expect(screen.queryByText("参与专家")).not.toBeInTheDocument();
    expect(screen.queryByText(/采用成果|正式成果/)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "查看分析依据" }));
    expect(screen.getByText(/已核验 2 类指标、14 条数据记录/)).toBeInTheDocument();
    expect(screen.queryByText(/raw-secret|content_hash|sha256/)).not.toBeInTheDocument();
  });

  it("renders business sections and hides internal schema, checklist, and raw-log copy", () => {
    render(<ArtifactCard artifact={reviewArtifact} onAction={vi.fn()} />);

    expect(screen.getByRole("heading", { name: "账号诊断" })).toBeInTheDocument();
    expect(screen.getByText("已完成当前账号运营诊断")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "查看账号诊断" })).not.toBeInTheDocument();
    expect(screen.getByText("V1")).toBeInTheDocument();
    expect(screen.getByText("核心结论")).toBeInTheDocument();
    expect(screen.getByText("数据周期")).toBeInTheDocument();
    expect(screen.getByText("关键数据")).toBeInTheDocument();
    expect(screen.getByText("主要问题")).toBeInTheDocument();
    expect(screen.getByText("优化建议")).toBeInTheDocument();
    expect(screen.getByText("调用专家")).toBeInTheDocument();
    expect(screen.queryByText("key_metrics")).not.toBeInTheDocument();
    expect(screen.queryByText("optimization_suggestions")).not.toBeInTheDocument();
    expect(screen.queryByText(/Confirm that this item/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Traceback/)).not.toBeInTheDocument();
  });

  it("keeps evidence closed by default and reveals only sanitized evidence with specialist labels", () => {
    render(<ArtifactCard artifact={reviewArtifact} onAction={vi.fn()} />);

    expect(screen.queryByText("近 21 天账号指标")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "查看生成依据" }));

    expect(screen.getByText("专家分析：1 条")).toBeInTheDocument();
    expect(screen.getByText("账号指标快照：1 条，覆盖 2 项指标，近 21 天")).toBeInTheDocument();
    expect(screen.getByText("质量通过（92 分）")).toBeInTheDocument();
    expect(screen.getByText("近 21 天账号指标")).not.toBeVisible();
    fireEvent.click(screen.getByText("技术依据（2 条）"));
    expect(screen.getByText("近 21 天账号指标")).toBeInTheDocument();
    expect(screen.queryByText(/Traceback/)).not.toBeInTheDocument();
  });

  it("sanitizes banned business copy in evidence group periods", () => {
    render(<ArtifactCard
      artifact={{
        ...reviewArtifact,
        evidence_summary: {
          total: 1,
          groups: [{
            kind: "metric_snapshot",
            label: "账号指标快照",
            count: 1,
            metric_count: 0,
            period: "采用成果/正式成果/脚本生成中",
          }],
        },
      }}
      onAction={vi.fn()}
    />);

    fireEvent.click(screen.getByRole("button", { name: "查看生成依据" }));

    expect(screen.queryByText(/采用成果|正式成果|脚本生成中|成果/)).not.toBeInTheDocument();
    expect(screen.getByText("账号指标快照：1 条，运营内容/运营内容/运营内容")).toBeInTheDocument();
  });

  it("paginates raw evidence inside technical details", () => {
    const evidenceRefs = Array.from({ length: 21 }, (_, index) => ({
      kind: "field_observation",
      id: index + 1,
      label: `field_observation #${index + 1}`,
    }));
    render(<ArtifactCard
      artifact={{
        ...reviewArtifact,
        evidence_refs: evidenceRefs,
        evidence_summary: {
          total: 21,
          groups: [{
            kind: "field_observation",
            label: "账号数据字段",
            count: 21,
            metric_count: 2,
            period: "2026-07-01 至 2026-07-30",
          }],
        },
      }}
      onAction={vi.fn()}
    />);

    fireEvent.click(screen.getByRole("button", { name: "查看生成依据" }));
    expect(screen.getByText("账号数据字段：21 条，覆盖 2 项指标，2026-07-01 至 2026-07-30")).toBeInTheDocument();
    expect(screen.getByText("field_observation #1")).not.toBeVisible();
    fireEvent.click(screen.getByText("技术依据（21 条）"));
    expect(screen.getByText("field_observation #1")).toBeInTheDocument();
    expect(screen.queryByText("field_observation #11")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "下一页" }));
    expect(screen.getByText("field_observation #11")).toBeInTheDocument();
    expect(screen.queryByText("field_observation #1")).not.toBeInTheDocument();
  });

  it("renders only server-advertised business actions and requires a concrete revision note", () => {
    const onAction = vi.fn();
    const artifactWithDetails = {
      ...reviewArtifact,
      sections: [...reviewArtifact.sections, { key: "execution_detail", title: "执行细节", content: "先完成账号检查。" }],
    };
    render(<ArtifactCard artifact={artifactWithDetails} onAction={onAction} />);

    const viewButton = screen.getByRole("button", { name: "查看账号诊断" });
    fireEvent.click(viewButton);
    expect(viewButton).not.toBeInTheDocument();
    expect(document.getElementById("artifact-details-5001-1")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "生成下一轮优化方案" }));
    fireEvent.click(screen.getByRole("button", { name: "提出修改" }));
    expect(screen.getByRole("region", { name: "修改运营内容" })).toBeInTheDocument();
    fireEvent.change(screen.getByRole("textbox", { name: "修改核心结论" }), {
      target: { value: "聚焦本地获客内容。" },
    });
    expect(screen.getByRole("button", { name: "提交修改" })).toBeDisabled();
    fireEvent.change(screen.getByRole("textbox", { name: "修改说明" }), {
      target: { value: "请补充三个可执行选题。" },
    });
    fireEvent.click(screen.getByRole("button", { name: "提交修改" }));

    expect(onAction).toHaveBeenCalledWith({ type: "view_full_report", artifact: artifactWithDetails });
    expect(onAction).toHaveBeenCalledWith(expect.objectContaining({
      type: "execute",
      artifact: artifactWithDetails,
      action: artifactWithDetails.next_actions[1],
      input: expect.objectContaining({
        note: "请补充三个可执行选题。",
        payload: expect.objectContaining({ core_conclusion: "聚焦本地获客内容。" }),
      }),
      idempotencyKey: expect.any(String),
    }));
    const revisionCall = onAction.mock.calls
      .map(([value]) => value)
      .find((value) => value.type === "execute" && value.action.code === "request_revision");
    expect(revisionCall.input.payload).not.toHaveProperty("acceptance_checklist");
    expect(revisionCall.input.payload).not.toHaveProperty("raw_tool_logs");
    expect(onAction).toHaveBeenCalledWith(expect.objectContaining({
      type: "execute",
      artifact: artifactWithDetails,
      action: artifactWithDetails.next_actions[0],
      input: {},
      idempotencyKey: expect.any(String),
    }));
    expect(screen.queryByRole("button", { name: "确认当前内容" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "确认并准备下一步建议" })).not.toBeInTheDocument();
    expect(screen.queryByText(/采用成果/)).not.toBeInTheDocument();
    expect(screen.queryByText(/正式成果/)).not.toBeInTheDocument();
    expect(screen.queryByLabelText("修改成果")).not.toBeInTheDocument();
  });

  it("confirms side effects and collects schedule details before executing", async () => {
    const onAction = vi.fn();
    const operationalArtifact: Artifact = {
      ...reviewArtifact,
      artifact_type: "content_calendar",
      next_actions: [
        { code: "create_shoot_task", label: "创建拍摄任务", requires_confirmation: true },
        { code: "add_to_schedule", label: "加入内容排期", requires_confirmation: true },
      ],
    };
    render(<ArtifactCard artifact={operationalArtifact} onAction={onAction} />);

    fireEvent.click(screen.getByRole("button", { name: "创建拍摄任务" }));
    fireEvent.click(await screen.findByRole("button", { name: "确认执行" }));
    expect(onAction).toHaveBeenCalledWith(expect.objectContaining({
      type: "execute",
      artifact: operationalArtifact,
      action: operationalArtifact.next_actions[0],
      input: { confirmed: true },
      idempotencyKey: expect.any(String),
    }));

    fireEvent.click(screen.getByRole("button", { name: "加入内容排期" }));
    expect(screen.getByRole("region", { name: "设置内容排期" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "确认排期" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("计划发布时间"), {
      target: { value: "2026-08-10T09:30" },
    });
    fireEvent.click(screen.getByRole("button", { name: "确认排期" }));
    const confirmScheduleButtons = await screen.findAllByRole("button", { name: "确认排期" });
    fireEvent.click(confirmScheduleButtons.at(-1)!);
    expect(onAction).toHaveBeenCalledWith(expect.objectContaining({
      type: "execute",
      artifact: operationalArtifact,
      action: operationalArtifact.next_actions[1],
      input: expect.objectContaining({ confirmed: true, timezone: expect.any(String) }),
    }));
  });

  it("only offers the one-way detail action when extra business details exist", () => {
    render(<ArtifactCard artifact={reviewArtifact} onAction={vi.fn()} />);

    expect(screen.queryByRole("button", { name: "查看账号诊断" })).not.toBeInTheDocument();
  });

  it("shows one status and a separate V2 progress row while a revision is pending", () => {
    render(<ArtifactCard artifact={{ ...reviewArtifact, status: "revision_requested" }} onAction={vi.fn()} />);

    expect(screen.getByText("正在准备 V2 更新内容")).toBeInTheDocument();
    expect(screen.queryByText("正式成果 V1")).not.toBeInTheDocument();
    expect(screen.queryByText("重做中")).not.toBeInTheDocument();
  });

  it("projects unsafe title, summary, nested keys, and critic details into safe Chinese business copy", () => {
    render(<ArtifactCard
      artifact={{
        ...reviewArtifact,
        title: "raw prompt: secret-token",
        summary: "Traceback: secret-token",
        sections: [
          { key: "key_metrics", title: "key_metrics", content: {
            engagement_rate: "4.8%",
            passed: true,
            score: 92,
            iterations: 3,
            trace: "secret-token",
          } },
          { key: "issues", title: "主要问题", content: "Traceback: secret-token" },
          { key: "critic", title: "critic", content: { passed: true, score: 92 } },
        ],
      }}
      onAction={vi.fn()}
    />);

    expect(screen.getByRole("heading", { name: "账号诊断" })).toBeInTheDocument();
    expect(screen.getByText("当前运营内容已完成安全核验。")) .toBeInTheDocument();
    expect(screen.getByText("互动率：4.8%")).toBeInTheDocument();
    expect(screen.getByText("质量审核")).toBeInTheDocument();
    expect(screen.getByText("质量审核已完成，详细依据请查看生成依据。")) .toBeInTheDocument();
    expect(screen.queryByText(/secret-token|Traceback|raw prompt|passed|score|iterations|key_metrics|critic/)).not.toBeInTheDocument();
  });

  it("replaces banned business copy in summaries, section titles, nested values, and accessible names", () => {
    render(<ArtifactCard
      artifact={{
        ...reviewArtifact,
        title: "脚本生成中",
        summary: "脚本生成中，采用成果即将可用",
        sections: [{
          key: "业务说明",
          title: "正式成果",
          content: {
            "采用成果": ["脚本生成中", { "正式成果": "采用成果" }],
          },
        }],
      }}
      onAction={vi.fn()}
    />);

    fireEvent.click(screen.getByRole("button", { name: "查看账号诊断" }));

    expect(screen.queryByText(/脚本生成中|正式成果|采用成果|成果/)).not.toBeInTheDocument();
    expect(screen.getByRole("article")).not.toHaveAccessibleName(/脚本生成中|正式成果|采用成果|成果/);
  });
});
