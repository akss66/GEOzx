import type {
  ContentStage,
  ContentStatus,
  Deliverable,
  DeliverableStatus,
  DeliverableType,
} from "../../types";

export interface ContentDocumentSection {
  label: string;
  value?: string;
  items?: string[];
  metrics?: { label: string; value: string }[];
}

export const CONTENT_STAGES: { key: ContentStage; label: string; short: string }[] = [
  { key: "positioning", label: "账号定位", short: "定位" },
  { key: "content_direction", label: "脚本策划", short: "脚本" },
  { key: "art_direction", label: "视觉方案", short: "视觉" },
  { key: "video_creation", label: "素材生成", short: "素材" },
  { key: "editing", label: "剪辑成片", short: "成片" },
  { key: "operation", label: "发布准备", short: "发布" },
];

const STATUS_LABELS: Record<ContentStatus, string> = {
  draft: "草稿",
  in_progress: "生产中",
  blocked: "等待处理",
  published: "已发布",
  archived: "已归档",
};

const DELIVERABLE_LABELS: Record<DeliverableType, string> = {
  positioning_strategy: "账号定位",
  topic_plan: "选题方案",
  publish_calendar: "发布日历",
  video_script: "视频脚本",
  art_prompt: "视觉方案",
  video_asset: "视频素材",
  edited_video: "剪辑成片",
  review_report: "运营复盘",
  ad_plan: "投放方案",
  cs_record: "互动记录",
};

const DELIVERABLE_STATUS_LABELS: Record<DeliverableStatus, string> = {
  draft: "草稿",
  pending_review: "待审核",
  approved: "已采用",
  rejected: "已驳回",
  superseded: "历史版本",
};

export function stageLabel(stage: ContentStage) {
  return CONTENT_STAGES.find((item) => item.key === stage)?.label ?? stage;
}

export function statusLabel(status: ContentStatus) {
  return STATUS_LABELS[status];
}

export function deliverableLabel(type: DeliverableType) {
  return DELIVERABLE_LABELS[type];
}

export function deliverableStatusLabel(status: DeliverableStatus) {
  return DELIVERABLE_STATUS_LABELS[status];
}

export function displayContentTitle(title: string) {
  const normalized = title.trim();
  if (!normalized) return "未命名内容";
  if (/^[?\uFFFD，,。.!！:：;；_\-\s]+$/u.test(normalized)) {
    return "标题编码异常";
  }
  return normalized;
}

export function canOperateContent(
  contentAccountId: number | null,
  activeAccountId: number | null,
) {
  return contentAccountId != null && contentAccountId === activeAccountId;
}

export function latestDeliverables(deliverables: Deliverable[]) {
  const latest = new Map<DeliverableType, Deliverable>();
  deliverables
    .filter((item) => item.status !== "superseded")
    .forEach((item) => {
      const current = latest.get(item.type);
      if (!current || item.version > current.version) latest.set(item.type, item);
    });
  return Array.from(latest.values()).sort(
    (left, right) => deliverableOrder(left.type) - deliverableOrder(right.type),
  );
}

export function deliverableSections(deliverable: Deliverable): ContentDocumentSection[] {
  const payload = deliverable.payload;
  switch (deliverable.type) {
    case "positioning_strategy":
      return compactSections([
        textSection("账号人设", payload.account_persona),
        textSection("目标人群", payload.target_audience),
        listSection("差异化方向", payload.differentiation),
        listSection("内容支柱", payload.content_pillars),
      ]);
    case "video_script":
      return compactSections([
        textSection("标题", payload.title),
        textSection("开场钩子", payload.hook),
        listSection("镜头结构", payload.scenes),
        textSection("建议时长", numberWithUnit(payload.duration_seconds, "秒")),
        textSection("音乐建议", payload.bgm_suggestion),
      ]);
    case "art_prompt":
      return compactSections([
        textSection("视觉风格", payload.visual_style),
        listSection("画面提示", payload.prompts),
        textSection("排除内容", payload.negative_prompt),
        textSection("画幅", payload.aspect_ratio),
      ]);
    case "video_asset":
      return compactSections([
        textSection("生成工具", payload.tool),
        listSection("镜头素材", payload.clips),
        textSection("分辨率", payload.resolution),
        textSection("制作备注", payload.notes),
      ]);
    case "edited_video":
      return compactSections([
        listSection("剪辑结构", payload.cut_plan),
        listSection("字幕重点", payload.captions),
        textSection("转场节奏", payload.transitions),
        listSection("成片清单", payload.deliverables),
        listSection("平台版本", payload.platform_variants),
      ]);
    case "review_report":
      return compactSections([
        textSection("复盘周期", payload.period),
        textSection("核心结论", payload.summary),
        metricsSection("关键指标", payload.key_metrics),
        listSection("表现亮点", payload.highlights),
        listSection("主要问题", payload.issues),
        listSection("优化建议", payload.optimization_suggestions),
      ]);
    default:
      return Object.entries(payload).map(([key, value]) => ({
        label: readableKey(key),
        ...(Array.isArray(value)
          ? { items: value.map(readableValue) }
          : { value: readableValue(value) }),
      }));
  }
}

function deliverableOrder(type: DeliverableType) {
  return Object.keys(DELIVERABLE_LABELS).indexOf(type);
}

function compactSections(sections: (ContentDocumentSection | null)[]) {
  return sections.filter((section): section is ContentDocumentSection => section != null);
}

function textSection(label: string, value: unknown): ContentDocumentSection | null {
  if (value == null || value === "") return null;
  return { label, value: readableValue(value) };
}

function listSection(label: string, value: unknown): ContentDocumentSection | null {
  if (!Array.isArray(value) || value.length === 0) return null;
  return { label, items: value.map(readableValue) };
}

function metricsSection(label: string, value: unknown): ContentDocumentSection | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  return {
    label,
    metrics: Object.entries(value).map(([key, metric]) => ({
      label: readableKey(key),
      value: readableValue(metric),
    })),
  };
}

function numberWithUnit(value: unknown, unit: string) {
  return typeof value === "number" ? `${value}${unit}` : value;
}

function readableKey(key: string) {
  const labels: Record<string, string> = {
    play: "播放量",
    completion_rate: "完播率",
    engagement_rate: "互动率",
    title: "标题",
    body: "正文",
    topics: "话题",
  };
  return labels[key] ?? key.replaceAll("_", " ");
}

function readableValue(value: unknown): string {
  if (value == null) return "";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (Array.isArray(value)) return value.map(readableValue).join("、");
  if (typeof value === "object") {
    return Object.entries(value as Record<string, unknown>)
      .map(([key, item]) => `${readableKey(key)}：${readableValue(item)}`)
      .join("；");
  }
  return String(value);
}
