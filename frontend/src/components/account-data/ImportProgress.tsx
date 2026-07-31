import type { AccountDataImportBatchStatus } from "../../api/accountData";

const STEPS = ["选择文件", "自动识别", "校验数据", "确认写入"] as const;

function getStepClass(
  index: number,
  status: AccountDataImportBatchStatus | null,
  hasUploadError: boolean,
) {
  if (hasUploadError) {
    if (index === 0) return "is-complete";
    if (index === 1) return "is-error";
    return "is-upcoming";
  }
  if (!status) return index === 0 ? "is-current" : "is-upcoming";
  if (status === "uploaded") {
    if (index === 0) return "is-complete";
    if (index === 1) return "is-current";
    return "is-upcoming";
  }
  if (status === "failed") {
    if (index === 0) return "is-complete";
    if (index === 1) return "is-error";
    return "is-upcoming";
  }
  if (status === "preview_ready") {
    if (index < 2) return "is-complete";
    if (index === 2) return "is-current";
    return "is-upcoming";
  }
  return "is-complete";
}

export function ImportProgress({
  status,
  hasUploadError = false,
}: {
  status: AccountDataImportBatchStatus | null;
  hasUploadError?: boolean;
}) {
  return (
    <ol className="account-data-import-progress" aria-label="导入进度">
      {STEPS.map((label, index) => (
        <li key={label} className={getStepClass(index, status, hasUploadError)}>
          <span aria-hidden="true">{index + 1}</span>
          <strong>{label}</strong>
        </li>
      ))}
    </ol>
  );
}
