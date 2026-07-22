import { CheckCircleFilled, ExclamationCircleFilled } from "@ant-design/icons";
import { Button } from "antd";

import type { AccountDataImportBatch, AccountDataImportRow } from "../../api/accountData";

function rowStatusMeta(status: AccountDataImportRow["status"]) {
  if (status === "ready") return { label: "可提交", tone: "is-ready" };
  if (status === "committed") return { label: "已写入", tone: "is-committed" };
  if (status === "revoked") return { label: "已撤销", tone: "is-revoked" };
  if (status === "invalid") return { label: "校验失败", tone: "is-invalid" };
  return { label: "待人工确认", tone: "is-pending" };
}

function readValue(
  row: AccountDataImportRow,
  key: string,
) {
  const value = row.normalized_values[key] ?? row.raw_values[key];
  return typeof value === "string" || typeof value === "number" ? String(value) : "—";
}

function listMessages(items: Array<Record<string, unknown>>) {
  return items
    .map((item) => item.message)
    .filter((value): value is string => typeof value === "string" && value.length > 0);
}

export function ImportPreviewTable({
  batch,
  resolvingRowNumber,
  onResolveRow,
}: {
  batch: AccountDataImportBatch;
  resolvingRowNumber: number | null;
  onResolveRow: (rowNumber: number, selectedContentId: number) => void;
}) {
  return (
    <div className="account-data-preview-table-wrap">
      <table className="account-data-preview-table">
        <thead>
          <tr>
            <th className="is-sticky">状态</th>
            <th className="is-sticky second">身份 / 匹配</th>
            <th>规范化字段</th>
            <th>校验摘要</th>
            <th>人工处理</th>
          </tr>
        </thead>
        <tbody>
          {batch.rows.map((row) => {
            const status = rowStatusMeta(row.status);
            const errors = listMessages(row.field_errors);
            const warnings = listMessages(row.warnings);
            const needsResolution = row.status === "needs_resolution";
            return (
              <tr key={row.id}>
                <td className="is-sticky">
                  <span className={`account-data-row-state ${status.tone}`}>
                    {row.status === "ready" || row.status === "committed" ? (
                      <CheckCircleFilled />
                    ) : (
                      <ExclamationCircleFilled />
                    )}
                    {status.label}
                  </span>
                </td>
                <td className="is-sticky second">
                  <div className="account-data-row-identity">
                    <strong>{readValue(row, "title")}</strong>
                    <span>{readValue(row, "published_at")}</span>
                    {row.platform_content_record_id ? (
                      <small>已关联作品 #{row.platform_content_record_id}</small>
                    ) : null}
                  </div>
                </td>
                <td>
                  <dl className="account-data-field-list">
                    <div>
                      <dt>标题</dt>
                      <dd>{readValue(row, "title")}</dd>
                    </div>
                    <div>
                      <dt>发布时间</dt>
                      <dd>{readValue(row, "published_at")}</dd>
                    </div>
                    <div>
                      <dt>播放量</dt>
                      <dd>{readValue(row, "play")}</dd>
                    </div>
                  </dl>
                  <details className="account-data-original-row">
                    <summary>查看原始行</summary>
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
                  {errors.length === 0 && warnings.length === 0 ? (
                    <span className="account-data-validation-ok">本行已通过基础校验。</span>
                  ) : null}
                </td>
                <td>
                  {needsResolution ? (
                    <div className="account-data-candidate-list">
                      <p>候选作品冲突，提交前必须人工确认。</p>
                      <div>
                        {row.candidate_content_ids.map((candidateId) => (
                          <Button
                            key={candidateId}
                            size="small"
                            loading={resolvingRowNumber === row.row_number}
                            onClick={() => onResolveRow(row.row_number, candidateId)}
                            aria-label={`选用候选作品 #${candidateId}`}
                          >
                            {`选用候选作品 #${candidateId}`}
                          </Button>
                        ))}
                      </div>
                    </div>
                  ) : (
                    <span className="account-data-resolution-note">
                      {row.resolution_outcome ?? "无需人工处理"}
                    </span>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
