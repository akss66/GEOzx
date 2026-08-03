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

describe("ArtifactCard", () => {
  afterEach(cleanup);

  it("renders business sections and hides internal schema, checklist, and raw-log copy", () => {
    render(<ArtifactCard artifact={reviewArtifact} onAction={vi.fn()} />);

    expect(screen.getByRole("heading", { name: "账号诊断" })).toBeInTheDocument();
    expect(screen.getByText("已完成当前账号运营诊断")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "查看账号诊断" })).toBeInTheDocument();
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

  it("uses concrete operations labels and requires a concrete revision note", () => {
    const onAction = vi.fn();
    render(<ArtifactCard artifact={reviewArtifact} onAction={onAction} />);

    fireEvent.click(screen.getByRole("button", { name: "查看账号诊断" }));
    fireEvent.click(screen.getByRole("button", { name: "确认当前内容" }));
    fireEvent.click(screen.getByRole("button", { name: "确认并继续规划" }));
    fireEvent.click(screen.getByRole("button", { name: "提出修改" }));
    expect(screen.getByRole("button", { name: "提交修改" })).toBeDisabled();
    fireEvent.change(screen.getByRole("textbox", { name: "修改说明" }), {
      target: { value: "请补充三个可执行选题。" },
    });
    fireEvent.click(screen.getByRole("button", { name: "提交修改" }));

    expect(onAction).toHaveBeenCalledWith({ type: "view_full_report", artifact: reviewArtifact });
    expect(onAction).toHaveBeenCalledWith({ type: "accept", artifact: reviewArtifact });
    expect(onAction).toHaveBeenCalledWith({ type: "accept_and_continue", artifact: reviewArtifact });
    expect(onAction).toHaveBeenCalledWith({
      type: "request_revision",
      artifact: reviewArtifact,
      note: "请补充三个可执行选题。",
    });
    expect(screen.queryByText(/采用成果/)).not.toBeInTheDocument();
    expect(screen.queryByText(/正式成果/)).not.toBeInTheDocument();
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
});
