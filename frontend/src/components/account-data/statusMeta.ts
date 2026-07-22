import type { AccountDataImportBatchStatus } from "../../api/accountData";

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
