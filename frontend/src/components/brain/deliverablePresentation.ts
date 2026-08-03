import type { Artifact } from "../../types";

type PrimaryActionKind = "open" | "plan" | "shoot" | "schedule" | "review";
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

const PRESENTATIONS: Record<string, DeliverablePresentation> = {
  account_inspection_report: presentation("账号诊断", "已完成当前账号运营诊断", "open", "查看账号诊断"),
  account_positioning: presentation("账号定位方案", "已整理当前账号定位方向", "plan", "查看定位方案"),
  positioning_strategy: presentation("账号定位方案", "已整理当前账号定位方向", "plan", "查看定位方案"),
  topic_plan: presentation("选题清单", "已规划 5 个可执行选题", "plan", "查看 5 个选题"),
  video_script: presentation("口播拍摄稿", "已生成 5 条可直接拍摄的口播稿", "open", "查看 5 条拍摄稿"),
  visual_brief: presentation("视觉拍摄方案", "已整理拍摄画面与素材要求", "shoot", "查看拍摄方案"),
  art_prompt: presentation("视觉拍摄方案", "已整理拍摄画面与素材要求", "shoot", "查看拍摄方案"),
  video_asset: presentation("拍摄素材清单", "已整理可用拍摄素材", "shoot", "查看素材清单"),
  edited_video: presentation("成片制作清单", "已整理剪辑与交付要求", "review", "查看成片清单"),
  content_calendar: presentation("内容排期表", "已排好未来 7 天内容", "schedule", "查看 7 天排期"),
  publish_calendar: presentation("发布准备清单", "已完成发布前检查", "review", "查看发布前检查"),
  platform_publish_receipt: presentation("发布记录", "已记录本次发布结果", "review", "查看发布记录"),
  review_report: presentation("运营复盘", "已完成本周期数据复盘", "review", "查看复盘建议"),
  engagement_review: presentation("互动复盘", "已整理近期互动反馈", "review", "查看互动建议"),
  ad_plan: presentation("投放计划", "已整理投放目标与预算建议", "plan", "查看投放计划"),
  cs_record: presentation("用户互动记录", "已整理用户反馈与回复建议", "review", "查看互动记录"),
  operation_execution_plan: presentation("本周运营执行计划", "已整理本周执行步骤", "plan", "查看本周执行计划"),
};

export function presentDeliverable(artifact: Artifact): DeliverablePresentation {
  const presentation = PRESENTATIONS[artifact.artifact_type] ?? genericReportPresentation(artifact);
  return {
    ...presentation,
    primaryAction: { ...presentation.primaryAction },
    secondaryActions: presentation.secondaryActions.map((action) => ({ ...action })),
  };
}

function presentation(
  typeLabel: string,
  completionLabel: string,
  kind: PrimaryActionKind,
  label: string,
): DeliverablePresentation {
  return {
    typeLabel,
    completionLabel,
    primaryAction: { kind, label },
    secondaryActions: DEFAULT_SECONDARY_ACTIONS,
  };
}

function genericReportPresentation(artifact: Artifact): DeliverablePresentation {
  const title = artifact.title.replace(/\s+/g, " ").trim();
  const typeLabel = hasChinese(title) && !title.includes("成果") ? title : "运营工作单";
  const completionLabel = artifact.sections.length > 0
    ? `已整理 ${artifact.sections.length} 项运营信息`
    : "已整理当前运营信息";

  return presentation(typeLabel, completionLabel, "open", "查看运营详情");
}

function hasChinese(value: string) {
  return /[\u3400-\u9fff]/.test(value);
}
