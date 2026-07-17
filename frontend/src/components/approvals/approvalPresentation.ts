import type {
  ApprovalKind,
  ApprovalQueueItem,
  ApprovalRiskLevel,
  Deliverable,
  DeliverableAcceptance,
  PublishPackage,
} from "../../types";

export type ApprovalFilter = "all" | "high_risk" | "content" | "external";

export const APPROVAL_KIND_LABEL: Record<ApprovalKind, string> = {
  gate: "质量门",
  tool_call: "外部动作",
  deliverable: "成果验收",
};

export const APPROVAL_RISK_LABEL: Record<ApprovalRiskLevel, string> = {
  low: "低风险",
  medium: "中风险",
  high: "高风险",
  critical: "关键风险",
};

export function filterApprovalItems(
  items: ApprovalQueueItem[],
  filter: ApprovalFilter,
) {
  if (filter === "high_risk") {
    return items.filter((item) => ["critical", "high"].includes(item.risk_level));
  }
  if (filter === "content") {
    return items.filter((item) => item.kind === "gate" || item.kind === "deliverable");
  }
  if (filter === "external") {
    return items.filter((item) => item.kind === "tool_call");
  }
  return items;
}

export function readApprovalDeliverable(item: ApprovalQueueItem): Deliverable | null {
  const value = item.preview.deliverable;
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const row = value as Partial<Deliverable>;
  if (
    typeof row.id !== "number" ||
    typeof row.type !== "string" ||
    typeof row.version !== "number" ||
    !row.payload ||
    typeof row.payload !== "object"
  ) return null;
  return row as Deliverable;
}

export function readApprovalAcceptance(
  item: ApprovalQueueItem,
): DeliverableAcceptance | null {
  const value = item.preview.acceptance;
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const row = value as Partial<DeliverableAcceptance>;
  if (typeof row.id !== "number" || typeof row.task_id !== "number") return null;
  return row as DeliverableAcceptance;
}

export function readApprovalPublishPackage(
  item: ApprovalQueueItem,
): PublishPackage | null {
  const value = item.preview.publish_package;
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const row = value as Partial<PublishPackage>;
  if (
    typeof row.title !== "string" ||
    !Array.isArray(row.material_ids) ||
    !Array.isArray(row.manual_steps)
  ) return null;
  return row as PublishPackage;
}

export function approvalFindingCopy(code: string, message: string) {
  const copy: Record<string, string> = {
    "account.required": "必须选择明确的发布账号。",
    "account.authorization_required": "发布账号尚未完成授权。",
    "title.long": "标题偏长，发布前需要再次确认。",
    "material.required": "至少需要一项已就绪素材。",
    "material.not_ready": "发布素材尚未就绪。",
    "material.ok": "发布素材已就绪。",
    "schedule.past": "计划发布时间已经过期。",
    "schedule.too_soon": "计划发布时间距离当前时间不足两小时。",
  };
  return copy[code] ?? message;
}

export function relativeApprovalTime(value: string) {
  const minutes = Math.max(0, Math.floor((Date.now() - new Date(value).getTime()) / 60000));
  if (minutes < 1) return "刚刚进入队列";
  if (minutes < 60) return `等待 ${minutes} 分钟`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `等待 ${hours} 小时`;
  return `等待 ${Math.floor(hours / 24)} 天`;
}
