import { CheckCircleFilled, ExclamationCircleFilled } from "@ant-design/icons";
import { Button } from "antd";

import type { AccountDataImportRow } from "../../api/accountData";

type Column = { key: string; label: string; numeric?: boolean };

const TEMPLATE_COLUMNS: Record<string, Column[]> = {
  douyin_daily_play_v1: [
    { key: "stat_date", label: "日期" },
    { key: "play", label: "播放量", numeric: true },
  ],
  douyin_single_content_v1: [
    { key: "title", label: "作品" },
    { key: "published_at", label: "发布时间" },
    { key: "play", label: "播放量", numeric: true },
  ],
  douyin_period_aggregate_v1: [
    { key: "period_start", label: "开始日期" },
    { key: "period_end", label: "结束日期" },
    { key: "publish_count", label: "发布数", numeric: true },
    { key: "median_play", label: "播放中位数", numeric: true },
  ],
  douyin_work_list_v1: [
    { key: "title", label: "作品" },
    { key: "published_at", label: "发布时间" },
    { key: "play", label: "播放量", numeric: true },
  ],
};

function rowStatusMeta(status: AccountDataImportRow["status"]) {
  if (status === "ready") return { label: "可写入", tone: "is-ready" };
  if (status === "committed") return { label: "已写入", tone: "is-committed" };
  if (status === "revoked") return { label: "已撤销", tone: "is-revoked" };
  if (status === "invalid") return { label: "校验失败", tone: "is-invalid" };
  return { label: "待人工确认", tone: "is-pending" };
}

function readValue(row: AccountDataImportRow, key: string) {
  const value = row.normalized_values[key] ?? row.raw_values[key];
  return typeof value === "string" || typeof value === "number" ? String(value) : "—";
}

function listMessages(items: Array<Record<string, unknown>>) {
  return items
    .map((item) => item.message)
    .filter((value): value is string => typeof value === "string" && value.length > 0);
}

export function ImportPreviewTable({
  loading = false,
  templateCode,
  rows,
  resolvingRowNumber,
  onResolveRow,
}: {
  loading?: boolean;
  templateCode: string;
  rows: AccountDataImportRow[];
  resolvingRowNumber: number | null;
  onResolveRow: (rowNumber: number, selectedContentId: number) => void;
}) {
  const columns = TEMPLATE_COLUMNS[templateCode] ?? TEMPLATE_COLUMNS.douyin_work_list_v1;
  return (
    <div className="account-data-preview-table-wrap">
      <table
        className="account-data-preview-table"
        aria-label="导入数据校验表"
        aria-busy={loading}
      >
        <thead>
          <tr>
            <th>状态</th>
            {columns.map((column) => (
              <th key={column.key} className={column.numeric ? "is-numeric" : undefined}>
                {column.label}
              </th>
            ))}
            <th>校验结果</th>
            <th>处理</th>
          </tr>
        </thead>
        <tbody>
          {loading ? (
            <tr className="account-data-preview-loading-row">
              <td colSpan={columns.length + 3}>正在加载校验数据…</td>
            </tr>
          ) : null}
          {rows.map((row) => {
            const status = rowStatusMeta(row.status);
            const errors = listMessages(row.field_errors);
            const warnings = listMessages(row.warnings);
            const needsResolution = row.status === "needs_resolution";
            return (
              <tr key={row.id}>
                <td>
                  <span className={`account-data-row-state ${status.tone}`}>
                    {row.status === "ready" || row.status === "committed"
                      ? <CheckCircleFilled />
                      : <ExclamationCircleFilled />}
                    {status.label}
                  </span>
                </td>
                {columns.map((column) => (
                  <td key={column.key} className={column.numeric ? "is-numeric" : undefined}>
                    {readValue(row, column.key)}
                  </td>
                ))}
                <td>
                  {errors.length > 0 ? (
                    <ul className="account-data-validation-list is-error">
                      {errors.map((message) => <li key={message}>{message}</li>)}
                    </ul>
                  ) : null}
                  {warnings.length > 0 ? (
                    <ul className="account-data-validation-list">
                      {warnings.map((message) => <li key={message}>{message}</li>)}
                    </ul>
                  ) : null}
                  {errors.length === 0 && warnings.length === 0
                    ? <span className="account-data-validation-ok">校验通过</span>
                    : null}
                </td>
                <td>
                  {needsResolution ? (
                    <div className="account-data-candidate-list">
                      {row.candidate_content_ids.map((candidateId) => (
                        <Button
                          key={candidateId}
                          size="small"
                          loading={resolvingRowNumber === row.row_number}
                          onClick={() => onResolveRow(row.row_number, candidateId)}
                          aria-label={`选用候选作品 #${candidateId}`}
                        >
                          {`选用 #${candidateId}`}
                        </Button>
                      ))}
                    </div>
                  ) : <span className="account-data-resolution-note">无需处理</span>}
                  <details className="account-data-original-row">
                    <summary>查看原始数据</summary>
                    <div>
                      {Object.entries(row.raw_values).map(([key, value]) => (
                        <div key={key}>
                          <strong>{key}</strong>
                          <span>
                            {typeof value === "string" || typeof value === "number"
                              ? String(value)
                              : "—"}
                          </span>
                        </div>
                      ))}
                    </div>
                  </details>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
