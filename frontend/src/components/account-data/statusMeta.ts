import type {
  AccountDataCoverage,
  AccountDataImportBatchStatus,
  AccountDataSourceKind,
} from "../../api/accountData";

const templateLabels: Record<string, string> = {
  douyin_daily_play_v1: "抖音日播放数据",
  douyin_single_content_v1: "抖音单作品分析",
  douyin_period_aggregate_v1: "抖音阶段汇总",
  douyin_work_list_v1: "抖音作品列表",
  manual_account_period_v1: "人工账号周期数据",
  manual_audience_dimension_v1: "人工粉丝画像",
  manual_benchmark_v1: "人工对标基准",
};

export function getTemplateLabel(templateCode: string) {
  return templateLabels[templateCode] ?? "其他账号数据";
}

export function getSourceKindLabel(sourceKind: AccountDataSourceKind) {
  if (sourceKind === "official_api") return "平台接口";
  if (sourceKind === "platform_export") return "平台导出";
  if (sourceKind === "screenshot_verified") return "截图佐证";
  return "人工录入";
}

export function getCoverageLabel(coverage: AccountDataCoverage) {
  if (coverage === "available") return "已有可用数据";
  if (coverage === "partial") return "已有部分数据";
  return "尚未导入";
}

export function getBatchStatusLabel(status: AccountDataImportBatchStatus) {
  if (status === "uploaded") return "已上传";
  if (status === "preview_ready") return "待写入";
  if (status === "committed") return "已确认";
  if (status === "revoked") return "已撤销";
  return "导入失败";
}

export function getBatchStatusDescription(status: AccountDataImportBatchStatus) {
  if (status === "uploaded") {
    return "文件已接收，正在等待生成可校验的导入预览。";
  }
  if (status === "preview_ready") {
    return "校验失败或待人工确认的行会阻止写入。";
  }
  if (status === "committed") {
    return "该批次已经确认写入并进入历史记录。";
  }
  if (status === "revoked") {
    return "该批次已撤销，仅保留留痕与原始证据。";
  }
  return "该批次处理失败，请修正来源文件后重新上传。";
}
