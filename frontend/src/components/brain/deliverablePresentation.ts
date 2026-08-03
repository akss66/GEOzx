import {
  KNOWN_ARTIFACT_TYPES,
  type Artifact,
  type ContentArtifactFormat,
  type KnownArtifactType,
} from "../../types";

type PrimaryActionKind = "open";
type SecondaryActionKind = "edit" | "export" | "regenerate";

export interface DeliverablePresentation {
  typeLabel: string;
  completionLabel: string;
  primaryAction: { kind: PrimaryActionKind; label: string };
  secondaryActions: Array<{ kind: SecondaryActionKind; label: string }>;
}

const DEFAULT_SECONDARY_ACTIONS: DeliverablePresentation["secondaryActions"] = [
  { kind: "edit", label: "提出修改" },
  { kind: "export", label: "导出内容" },
  { kind: "regenerate", label: "重新生成" },
];

const PRESENTATIONS: Partial<Record<KnownArtifactType, DeliverablePresentation>> = {
  account_inspection_report: fixedPresentation("账号诊断", "已完成当前账号运营诊断"),
  account_positioning: fixedPresentation("账号定位方案", "已整理当前账号定位方向"),
  positioning_strategy: fixedPresentation("账号定位方案", "已整理当前账号定位方向"),
  visual_brief: fixedPresentation("视觉制作说明", "已整理画面与素材要求"),
  art_prompt: fixedPresentation("视觉制作说明", "已整理画面与素材要求"),
  video_asset: fixedPresentation("视频素材清单", "已整理可用视频素材"),
  edited_video: fixedPresentation("成片制作清单", "已整理剪辑与交付要求"),
  publish_calendar: fixedPresentation("发布准备清单", "已完成发布前检查"),
  platform_publish_receipt: fixedPresentation("发布记录", "已记录本次发布结果"),
  review_report: fixedPresentation("运营复盘", "已完成本周期数据复盘"),
  engagement_review: fixedPresentation("互动复盘", "已整理近期互动反馈"),
  ad_plan: fixedPresentation("投放计划", "已整理投放目标与预算建议"),
  cs_record: fixedPresentation("用户互动记录", "已整理用户反馈与回复建议"),
  operation_execution_plan: fixedPresentation("本周运营执行计划", "已整理本周执行步骤"),
} satisfies Partial<Record<KnownArtifactType, DeliverablePresentation>>;

const CONTENT_FORMATS: Record<ContentArtifactFormat, string> = {
  spoken: "口播拍摄稿",
  storyboard: "分镜拍摄稿",
  product_video: "产品视频拍摄稿",
  image_post: "图文发布稿",
  live_flow: "直播流程与话术稿",
};

export function presentDeliverable(artifact: Artifact): DeliverablePresentation {
  const presentation = artifact.artifact_type === "video_script"
    ? scriptPresentation(artifact)
    : artifact.artifact_type === "topic_plan"
      ? topicPresentation(artifact)
      : artifact.artifact_type === "content_calendar"
        ? calendarPresentation(artifact)
        : isKnownArtifactType(artifact.artifact_type)
          ? PRESENTATIONS[artifact.artifact_type] ?? genericReportPresentation()
          : genericReportPresentation();
  return clonePresentation(presentation);
}

function fixedPresentation(typeLabel: string, completionLabel: string): DeliverablePresentation {
  return presentation(typeLabel, completionLabel, `查看${typeLabel}`);
}

function scriptPresentation(artifact: Artifact): DeliverablePresentation {
  const typeLabel = contentFormatLabel(artifact.presentation_format);
  return presentation(typeLabel, `已生成可直接拍摄的${typeLabel}`, `查看${typeLabel}`);
}

function topicPresentation(artifact: Artifact): DeliverablePresentation {
  const count = listSectionCount(artifact, "topics");
  return count == null
    ? presentation("选题清单", "已完成选题规划", "查看选题清单")
    : presentation("选题清单", `已规划 ${count} 个可执行选题`, `查看 ${count} 个选题`);
}

function calendarPresentation(artifact: Artifact): DeliverablePresentation {
  const days = positiveIntegerSection(artifact, "days", 90);
  return days == null
    ? presentation("内容排期表", "已完成内容排期", "查看内容排期")
    : presentation("内容排期表", `已排好未来 ${days} 天内容`, `查看 ${days} 天排期`);
}

function genericReportPresentation(): DeliverablePresentation {
  return presentation("账号运营分析", "已完成账号运营分析", "查看账号运营分析");
}

function presentation(typeLabel: string, completionLabel: string, label: string): DeliverablePresentation {
  return {
    typeLabel,
    completionLabel,
    primaryAction: { kind: "open", label },
    secondaryActions: DEFAULT_SECONDARY_ACTIONS,
  };
}

function clonePresentation(presentation: DeliverablePresentation): DeliverablePresentation {
  return {
    ...presentation,
    primaryAction: { ...presentation.primaryAction },
    secondaryActions: presentation.secondaryActions.map((action) => ({ ...action })),
  };
}

function listSectionCount(artifact: Artifact, key: string) {
  const content = artifact.sections.find((section) => section.key === key)?.content;
  return Array.isArray(content) && content.length > 0 ? content.length : null;
}

function positiveIntegerSection(artifact: Artifact, key: string, max: number) {
  const value = artifact.sections.find((section) => section.key === key)?.content;
  const parsed = typeof value === "number"
    ? value
    : typeof value === "string" && /^\d+$/.test(value.trim())
      ? Number(value)
      : null;
  return parsed != null && Number.isInteger(parsed) && parsed > 0 && parsed <= max ? parsed : null;
}

function isKnownArtifactType(value: Artifact["artifact_type"]): value is KnownArtifactType {
  return (KNOWN_ARTIFACT_TYPES as readonly string[]).includes(value);
}

function contentFormatLabel(value: unknown) {
  return typeof value === "string" && value in CONTENT_FORMATS
    ? CONTENT_FORMATS[value as ContentArtifactFormat]
    : CONTENT_FORMATS.storyboard;
}
