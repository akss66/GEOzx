import { DownloadOutlined, HistoryOutlined, StopOutlined } from "@ant-design/icons";
import { Button } from "antd";
import { useState } from "react";

import type {
  AccountDataImportArtifact,
  AccountDataImportBatch,
  AccountDataImportBatchSummary,
} from "../../api/accountData";
import { getBatchStatusLabel } from "./statusMeta";

function sourceLabel(sourceKind: AccountDataImportBatchSummary["source_kind"]) {
  if (sourceKind === "official_api") return "官方接口";
  if (sourceKind === "platform_export") return "平台导出";
  if (sourceKind === "screenshot_verified") return "截图核验";
  return "人工录入";
}

function formatDateTime(value: string | null) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

export function ImportBatchHistory({
  items,
  detailsById,
  activeBatchId,
  revokingBatchId,
  revokeError,
  onOpenBatch,
  onDownloadArtifact,
  onRevoke,
}: {
  items: AccountDataImportBatchSummary[];
  detailsById: Map<number, AccountDataImportBatch>;
  activeBatchId: number | null;
  revokingBatchId: number | null;
  revokeError: string | null;
  onOpenBatch: (batchId: number) => void;
  onDownloadArtifact: (artifact: AccountDataImportArtifact) => void;
  onRevoke: (batchId: number) => void;
}) {
  const [confirmingBatchId, setConfirmingBatchId] = useState<number | null>(null);

  return (
    <section className="account-data-history" aria-label="导入历史">
      <header className="account-data-section-head">
        <div>
          <span>批次留痕</span>
          <h2>导入历史</h2>
          <p>保留来源、时间窗、原始文件与撤销记录，便于复核当前账号的数据来路。</p>
        </div>
        <HistoryOutlined className="account-data-section-icon" />
      </header>

      {revokeError ? (
        <div className="account-data-feedback is-error" role="alert">
          <strong>当前操作未完成</strong>
          <p>{revokeError}</p>
        </div>
      ) : null}

      {items.length === 0 ? (
        <div className="account-data-empty-inline">
          <strong>暂无导入历史</strong>
          <p>确认写入后的批次会保留在这里，后续可追溯来源、下载原文件或执行撤销。</p>
        </div>
      ) : (
        <div className="account-data-history-list">
          {items.map((item) => {
            const detail = detailsById.get(item.id);
            const firstArtifact = detail?.artifacts[0] ?? null;
            const confirming = confirmingBatchId === item.id;
            return (
              <article
                key={item.id}
                className={`account-data-history-item${
                  activeBatchId === item.id ? " is-active" : ""
                }`}
              >
                <div className="account-data-history-copy">
                  <div>
                    <span>{`批次 ${item.id}`}</span>
                    <strong>{getBatchStatusLabel(item.status)}</strong>
                  </div>
                  <p>{`${sourceLabel(item.source_kind)} · ${item.template_code}`}</p>
                  <small>
                    {`创建 ${formatDateTime(item.created_at)} · 窗口 ${formatDateTime(item.period_start)} - ${formatDateTime(item.period_end)}`}
                  </small>
                </div>
                <div className="account-data-history-actions">
                  <Button size="small" onClick={() => onOpenBatch(item.id)}>
                    {item.status === "preview_ready" ? "查看预览" : "查看详情"}
                  </Button>
                  {firstArtifact ? (
                    <Button
                      size="small"
                      icon={<DownloadOutlined />}
                      aria-label={`下载原文件 ${firstArtifact.filename}`}
                      onClick={() => onDownloadArtifact(firstArtifact)}
                    >
                      下载原文件
                    </Button>
                  ) : null}
                  {item.status === "committed" ? (
                    confirming ? (
                      <div className="account-data-revoke-confirm">
                        <span>确认撤销这次写入？</span>
                        <Button
                          size="small"
                          danger
                          loading={revokingBatchId === item.id}
                          aria-label={`确认撤销批次 ${item.id}`}
                          onClick={() => onRevoke(item.id)}
                        >
                          确认撤销
                        </Button>
                        <Button size="small" onClick={() => setConfirmingBatchId(null)}>
                          取消
                        </Button>
                      </div>
                    ) : (
                      <Button
                        size="small"
                        icon={<StopOutlined />}
                        aria-label={`撤销批次 ${item.id}`}
                        onClick={() => setConfirmingBatchId(item.id)}
                      >
                        撤销导入
                      </Button>
                    )
                  ) : null}
                </div>
              </article>
            );
          })}
        </div>
      )}
    </section>
  );
}
