import {
  DeleteOutlined,
  DownloadOutlined,
  HistoryOutlined,
  MoreOutlined,
  StopOutlined,
} from "@ant-design/icons";
import { Button } from "antd";
import { Fragment, useState } from "react";

import type {
  AccountDataImportArtifact,
  AccountDataImportBatch,
  AccountDataImportBatchSummary,
} from "../../api/accountData";
import {
  getBatchStatusLabel,
  getSourceKindLabel,
  getTemplateLabel,
} from "./statusMeta";

type Confirmation =
  | {
      action: "revoke" | "delete";
      batchId: number;
    }
  | null;

function formatDate(value: string | null) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(date);
}

function formatDateTime(value: string | null) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function formatPeriod(item: AccountDataImportBatchSummary) {
  if (!item.period_start && !item.period_end) return "—";
  return `${formatDate(item.period_start)} – ${formatDate(item.period_end)}`;
}

export function ImportBatchHistory({
  items,
  detailsById,
  activeBatchId,
  revokingBatchId,
  deletingBatchId,
  revokeError,
  onOpenBatch,
  onDownloadArtifact,
  onRevoke,
  onDelete,
}: {
  items: AccountDataImportBatchSummary[];
  detailsById: Map<number, AccountDataImportBatch>;
  activeBatchId: number | null;
  revokingBatchId: number | null;
  deletingBatchId: number | null;
  revokeError: string | null;
  onOpenBatch: (batchId: number) => void;
  onDownloadArtifact: (artifact: AccountDataImportArtifact) => void;
  onRevoke: (batchId: number) => void;
  onDelete: (batchId: number) => void;
}) {
  const [confirmation, setConfirmation] = useState<Confirmation>(null);
  const [openMenuBatchId, setOpenMenuBatchId] = useState<number | null>(null);

  return (
    <section className="account-data-history" aria-label="导入历史">
      <header className="account-data-section-head">
        <div>
          <span>批次留痕</span>
          <h2>导入记录</h2>
          <p>按批次查看数据来源、覆盖周期和操作人；危险操作统一收进更多菜单。</p>
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
          <p>上传、确认或人工补录后的批次会保留在这里，便于追溯与撤销。</p>
        </div>
      ) : (
        <div className="account-data-history-table-wrap">
          <table className="account-data-history-table" aria-label="导入记录">
            <thead>
              <tr>
                <th scope="col">批次</th>
                <th scope="col">数据类型</th>
                <th scope="col">来源</th>
                <th scope="col">数据周期</th>
                <th scope="col">记录数</th>
                <th scope="col">状态</th>
                <th scope="col">创建人 / 时间</th>
                <th scope="col">操作</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => {
                const deleting = deletingBatchId === item.id;
                const artifact = detailsById.get(item.id)?.artifacts[0] ?? null;
                const confirming =
                  confirmation?.batchId === item.id ? confirmation.action : null;

                return (
                  <Fragment key={item.id}>
                    <tr
                      className={`account-data-history-row${
                        activeBatchId === item.id ? " is-active" : ""
                      }`}
                    >
                      <td data-label="批次">
                        <strong>{`批次 ${item.id}`}</strong>
                      </td>
                      <td data-label="数据类型">{getTemplateLabel(item.template_code)}</td>
                      <td data-label="来源">{getSourceKindLabel(item.source_kind)}</td>
                      <td data-label="数据周期">{formatPeriod(item)}</td>
                      <td data-label="记录数">{item.row_count.toLocaleString("zh-CN")}</td>
                      <td data-label="状态">
                        <span className={`account-data-history-status is-${item.status}`}>
                          {getBatchStatusLabel(item.status)}
                        </span>
                      </td>
                      <td data-label="创建人 / 时间">
                        <span className="account-data-history-creator">
                          <strong>{item.created_by_name || "已删除成员"}</strong>
                          <small>{formatDateTime(item.created_at)}</small>
                        </span>
                      </td>
                      <td data-label="操作">
                        <div className="account-data-history-actions">
                          <Button
                            type="link"
                            size="small"
                            disabled={deleting}
                            aria-label={`查看批次 ${item.id}`}
                            onClick={() => {
                              setOpenMenuBatchId(null);
                              onOpenBatch(item.id);
                            }}
                          >
                            查看
                          </Button>
                          <div className="account-data-history-more">
                            <Button
                              type="text"
                              size="small"
                              icon={<MoreOutlined />}
                              disabled={deleting}
                              aria-label={`更多操作 批次 ${item.id}`}
                              aria-expanded={openMenuBatchId === item.id}
                              aria-haspopup="menu"
                              onClick={() =>
                                setOpenMenuBatchId((current) =>
                                  current === item.id ? null : item.id,
                                )
                              }
                            >
                              更多
                            </Button>
                            {openMenuBatchId === item.id ? (
                              <div
                                className="account-data-history-menu"
                                role="menu"
                                aria-label={`批次 ${item.id} 更多操作`}
                              >
                                {artifact ? (
                                  <button
                                    type="button"
                                    role="menuitem"
                                    onClick={() => {
                                      setOpenMenuBatchId(null);
                                      onDownloadArtifact(artifact);
                                    }}
                                  >
                                    <DownloadOutlined />
                                    下载原文件
                                  </button>
                                ) : null}
                                {item.status === "committed" ? (
                                  <button
                                    type="button"
                                    role="menuitem"
                                    onClick={() => {
                                      setOpenMenuBatchId(null);
                                      setConfirmation({
                                        action: "revoke",
                                        batchId: item.id,
                                      });
                                    }}
                                  >
                                    <StopOutlined />
                                    撤销写入
                                  </button>
                                ) : null}
                                <button
                                  type="button"
                                  role="menuitem"
                                  className="is-danger"
                                  onClick={() => {
                                    setOpenMenuBatchId(null);
                                    setConfirmation({
                                      action: "delete",
                                      batchId: item.id,
                                    });
                                  }}
                                >
                                  <DeleteOutlined />
                                  永久删除
                                </button>
                              </div>
                            ) : null}
                          </div>
                        </div>
                      </td>
                    </tr>
                    {confirming ? (
                      <tr className="account-data-history-confirm-row">
                        <td colSpan={8}>
                          <div className="account-data-destructive-confirm">
                            <div>
                              <strong>
                                {confirming === "revoke"
                                  ? "确认撤销这次写入？"
                                  : "确认永久删除这个批次？"}
                              </strong>
                              <span>
                                {confirming === "revoke"
                                  ? "已写入的数据会撤销，批次与原始文件仍会保留。"
                                  : item.status === "committed"
                                    ? "将先撤销该批次产生的数据，再永久删除原文件和历史记录。"
                                    : "将永久删除原文件、预览数据和历史记录，且无法恢复。"}
                              </span>
                            </div>
                            <Button
                              size="small"
                              danger
                              loading={
                                confirming === "revoke"
                                  ? revokingBatchId === item.id
                                  : deleting
                              }
                              aria-label={
                                confirming === "revoke"
                                  ? `确认撤销批次 ${item.id}`
                                  : `确认永久删除批次 ${item.id}`
                              }
                              onClick={() => {
                                if (confirming === "revoke") {
                                  onRevoke(item.id);
                                } else {
                                  onDelete(item.id);
                                }
                                setConfirmation(null);
                              }}
                            >
                              {confirming === "revoke" ? "确认撤销" : "确认永久删除"}
                            </Button>
                            <Button size="small" onClick={() => setConfirmation(null)}>
                              取消
                            </Button>
                          </div>
                        </td>
                      </tr>
                    ) : null}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
