import type { BrainTask, BudgetStatus } from "../../types";

export { businessToolName } from "../presentation/toolNames";

export function formatCost(value: number) {
  return `$${value.toFixed(value >= 100 ? 2 : 4)}`;
}

export function formatPercent(value: number | null) {
  if (value == null) return "未设置";
  return `${Number(value.toFixed(2))}%`;
}

export function budgetStatusCopy(status: BudgetStatus) {
  if (status === "exceeded") return "已超预算";
  if (status === "warning") return "接近预算";
  if (status === "healthy") return "预算健康";
  return "未设置预算";
}

export function taskTypeCopy(type: BrainTask["type"]) {
  const labels: Record<BrainTask["type"], string> = {
    content_creation: "内容生产",
    account_diagnosis: "账号诊断",
    review_optimization: "运营复盘",
    matrix_distribution: "矩阵分发",
  };
  return labels[type];
}

export function taskStatusCopy(status: string) {
  const labels: Record<string, string> = {
    pending_confirmation: "待确认",
    running: "执行中",
    waiting_approval: "待审批",
    pending_acceptance: "待验收",
    completed: "已完成",
    failed: "失败",
    cancelled: "已取消",
  };
  return labels[status] ?? "处理中";
}

export function safeTaskTitle(title: string, taskId: number) {
  const normalizedTitle = title.trim();
  if (!normalizedTitle) return `历史运营任务 #${taskId}`;
  const suspiciousCharacters = normalizedTitle.match(/[?\uFFFD]/gu)?.length ?? 0;
  const isCorrupt = suspiciousCharacters >= 3 && suspiciousCharacters / normalizedTitle.length >= 0.3;
  return isCorrupt ? `历史运营任务 #${taskId}` : normalizedTitle;
}
