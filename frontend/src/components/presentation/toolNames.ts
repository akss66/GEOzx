const BUSINESS_TOOL_NAMES: Record<string, string> = {
  account_context: "账号上下文",
  profile_snapshot: "账号资料快照",
  brief_builder: "任务目标整理",
  compliance_check: "合规检查",
  publish_package_prepare: "发布包准备",
  task_planning: "执行计划",
  data_sync: "数据同步",
  material_search: "素材检索",
  content_generation: "内容生成",
};

export function businessToolName(toolCode: string, toolName: string) {
  const normalizedCode = toolCode.trim().toLowerCase().replaceAll("-", "_");
  const mappedByCode = BUSINESS_TOOL_NAMES[normalizedCode];
  if (mappedByCode) return mappedByCode;

  const normalizedName = toolName.trim().toLowerCase().replaceAll(" ", "_");
  const mappedByName = BUSINESS_TOOL_NAMES[normalizedName];
  if (mappedByName) return mappedByName;

  return /[\u3400-\u9fff]/u.test(toolName) ? toolName.trim() : "运营工具";
}
