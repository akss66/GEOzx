import { UploadOutlined } from "@ant-design/icons";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Button } from "antd";
import { type ChangeEvent, useEffect, useState } from "react";

import {
  getAccountDataImportRows,
  type AccountDataImportBatch,
  type AccountDataImportRowView,
} from "../../api/accountData";
import { ImportCommitBar } from "./ImportCommitBar";
import { ImportPreviewTable } from "./ImportPreviewTable";
import { ImportProgress } from "./ImportProgress";
import { ImportSummary } from "./ImportSummary";

interface FlowFeedback {
  tone: "error" | "success";
  title: string;
  description: string;
}

export function ImportWorkspace({
  accountId,
  batch,
  feedback,
  uploading,
  resolvingRowNumber,
  committing,
  onFileSelected,
  onResolveRow,
  onCommit,
}: {
  accountId: number;
  batch: AccountDataImportBatch | null;
  feedback: FlowFeedback | null;
  uploading: boolean;
  resolvingRowNumber: number | null;
  committing: boolean;
  onFileSelected: (file: File) => void;
  onResolveRow: (rowNumber: number, selectedContentId: number) => void;
  onCommit: () => void;
}) {
  const [rowPage, setRowPage] = useState(1);
  const [rowView, setRowView] = useState<AccountDataImportRowView>("all");
  const [lastSelectedFile, setLastSelectedFile] = useState<File | null>(null);
  const batchId = batch?.id ?? null;

  useEffect(() => {
    setRowPage(1);
    setRowView("all");
  }, [accountId, batchId]);

  const rowsQuery = useQuery({
    enabled: batchId != null && batch?.status === "preview_ready",
    queryKey: ["account-data-import-rows", accountId, batchId, rowPage, rowView],
    queryFn: () => getAccountDataImportRows(
      accountId,
      batchId!,
      { page: rowPage, pageSize: 50, view: rowView },
    ),
    placeholderData: keepPreviousData,
    retry: false,
    staleTime: Number.POSITIVE_INFINITY,
  });

  function handleChange(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (file) {
      setLastSelectedFile(file);
      onFileSelected(file);
      event.target.value = "";
    }
  }

  const rowData = rowsQuery.data ?? null;
  const totalCount = rowData?.total_count ?? batch?.row_count ?? 0;
  const blockingCount = rowData?.blocking_count ?? 0;
  const hasUploadError = feedback?.tone === "error" && !batch;

  return (
    <section className="account-data-flow account-data-import-review" aria-label="文件导入流程">
      <header className="account-data-section-head">
        <div>
          <span>导入工作台</span>
          <h2>{batch ? "核对本次导入" : "导入账号数据"}</h2>
          <p>系统会自动识别模板、逐行校验，只有通过校验的数据才能正式写入。</p>
        </div>
        <label className="account-data-upload-trigger">
          <UploadOutlined />
          <span>{uploading ? "正在上传…" : batch ? "更换文件" : "选择文件"}</span>
          <input
            aria-label="选择导入文件"
            type="file"
            accept=".xlsx,.csv"
            disabled={uploading}
            onChange={handleChange}
          />
        </label>
      </header>

      <ImportProgress status={batch?.status ?? null} hasUploadError={hasUploadError} />

      {feedback ? (
        <div
          className={`account-data-feedback is-${feedback.tone}`}
          role={feedback.tone === "error" ? "alert" : "status"}
        >
          <strong>{feedback.title}</strong>
          <p>{feedback.description}</p>
          {hasUploadError && lastSelectedFile ? (
            <div className="account-data-upload-recovery">
              <span>{lastSelectedFile.name}</span>
              <span>此次上传未写入任何账号数据。</span>
              <Button
                size="small"
                aria-label={`重新上传 ${lastSelectedFile.name}`}
                onClick={() => onFileSelected(lastSelectedFile)}
              >
                重新上传
              </Button>
            </div>
          ) : null}
        </div>
      ) : null}

      {batch ? (
        <>
          <ImportSummary batch={batch} rowPage={rowData} />
          {batch.status === "preview_ready" ? (
            <>
              <div className="account-data-row-toolbar">
                <div role="group" aria-label="导入行筛选">
                  <button
                    type="button"
                    className={rowView === "all" ? "is-active" : undefined}
                    onClick={() => {
                      setRowPage(1);
                      setRowView("all");
                    }}
                  >
                    {`全部 ${rowData?.total_count ?? batch.row_count}`}
                  </button>
                  <button
                    type="button"
                    className={rowView === "ready" ? "is-active" : undefined}
                    onClick={() => {
                      setRowPage(1);
                      setRowView("ready");
                    }}
                  >
                    {`可写入 ${rowData?.ready_count ?? 0}`}
                  </button>
                  <button
                    type="button"
                    className={rowView === "needs_work" ? "is-active" : undefined}
                    onClick={() => {
                      setRowPage(1);
                      setRowView("needs_work");
                    }}
                  >
                    {`需处理 ${rowData?.blocking_count ?? 0}`}
                  </button>
                </div>
                {rowsQuery.isFetching ? <span>正在更新…</span> : null}
              </div>
              {rowsQuery.isLoading ? (
                <ImportPreviewTable
                  loading
                  templateCode={batch.template_code}
                  rows={[]}
                  resolvingRowNumber={resolvingRowNumber}
                  onResolveRow={onResolveRow}
                />
              ) : rowsQuery.isError ? (
                <div className="account-data-empty-inline" role="alert">
                  <strong>行数据加载失败</strong>
                  <Button onClick={() => void rowsQuery.refetch()}>重新加载</Button>
                </div>
              ) : (
                <>
                  <ImportPreviewTable
                    loading={false}
                    templateCode={batch.template_code}
                    rows={rowData?.items ?? []}
                    resolvingRowNumber={resolvingRowNumber}
                    onResolveRow={onResolveRow}
                  />
                  <nav className="account-data-pagination" aria-label="导入行分页">
                    <span>{`第 ${rowData?.page ?? 1} / ${rowData?.total_pages ?? 1} 页`}</span>
                    <div>
                      <Button
                        disabled={(rowData?.page ?? 1) <= 1}
                        onClick={() => setRowPage((current) => Math.max(1, current - 1))}
                      >
                        上一页
                      </Button>
                      <Button
                        disabled={(rowData?.page ?? 1) >= (rowData?.total_pages ?? 1)}
                        onClick={() => setRowPage((current) => current + 1)}
                      >
                        下一页
                      </Button>
                    </div>
                  </nav>
                </>
              )}
              <ImportCommitBar
                totalCount={totalCount}
                blockingCount={blockingCount}
                committing={committing}
                disabled={
                  !rowData
                  || blockingCount > 0
                  || totalCount === 0
                  || batch.status !== "preview_ready"
                }
                onCommit={onCommit}
              />
            </>
          ) : null}
        </>
      ) : (
        <div className="account-data-empty-inline">
          <strong>先上传抖音导出的原始文件</strong>
          <p>支持 Excel 和 CSV。上传后会在这里显示识别结果、问题行和确认入口。</p>
        </div>
      )}
    </section>
  );
}
