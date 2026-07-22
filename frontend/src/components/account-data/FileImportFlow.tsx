import { UploadOutlined } from "@ant-design/icons";
import { Button } from "antd";
import type { ChangeEvent } from "react";

import type { AccountDataImportBatch } from "../../api/accountData";
import { ImportPreviewTable } from "./ImportPreviewTable";

interface FlowFeedback {
  tone: "error" | "success";
  title: string;
  description: string;
}

const STEP_TITLES = [
  "1. 选择来源与模板",
  "2. 上传原始文件",
  "3. 校对行级数据",
  "4. 确认写入",
] as const;

export function FileImportFlow({
  batch,
  feedback,
  uploading,
  resolvingRowNumber,
  committing,
  canCommit,
  onFileSelected,
  onResolveRow,
  onCommit,
}: {
  batch: AccountDataImportBatch | null;
  feedback: FlowFeedback | null;
  uploading: boolean;
  resolvingRowNumber: number | null;
  committing: boolean;
  canCommit: boolean;
  onFileSelected: (file: File) => void;
  onResolveRow: (rowNumber: number, selectedContentId: number) => void;
  onCommit: () => void;
}) {
  function handleChange(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (file) {
      onFileSelected(file);
      event.target.value = "";
    }
  }

  return (
    <section className="account-data-flow" aria-label="文件导入流程">
      <header className="account-data-section-head">
        <div>
          <span>导入工作台</span>
          <h2>文件导入</h2>
          <p>仅支持当前已验证的抖音导出模板，导入过程不会脱离当前账号上下文。</p>
        </div>
        <label className="account-data-upload-trigger">
          <UploadOutlined />
          <span>{uploading ? "正在上传…" : "导入数据"}</span>
          <input
            aria-label="选择导入文件"
            type="file"
            accept=".xlsx,.csv"
            disabled={uploading}
            onChange={handleChange}
          />
        </label>
      </header>

      <ol className="account-data-step-strip">
        {STEP_TITLES.map((title, index) => {
          const isActive = batch
            ? index < 2 || (index === 2 && batch.rows.length > 0) || batch.status !== "preview_ready"
            : index === 0;
          return (
            <li key={title} className={isActive ? "is-active" : undefined}>
              {title}
            </li>
          );
        })}
      </ol>

      <div className="account-data-template-hint">
        <strong>已支持模板</strong>
        <span>抖音作品列表</span>
        <span>抖音日播放</span>
        <span>抖音单作品分析</span>
        <span>抖音阶段聚合</span>
      </div>

      {feedback ? (
        <div className={`account-data-feedback is-${feedback.tone}`} role="status">
          <strong>{feedback.title}</strong>
          <p>{feedback.description}</p>
        </div>
      ) : null}

      {batch ? (
        <div className="account-data-preview-block">
          <div className="account-data-preview-summary">
            <div>
              <span>当前批次</span>
              <strong>{batch.template_code}</strong>
            </div>
            <div>
              <span>记录数</span>
              <strong>{batch.row_count}</strong>
            </div>
            <div>
              <span>原始文件</span>
              <strong>{batch.artifacts[0]?.filename ?? "未附带文件"}</strong>
            </div>
          </div>
          <ImportPreviewTable
            batch={batch}
            resolvingRowNumber={resolvingRowNumber}
            onResolveRow={onResolveRow}
          />
          <div className="account-data-preview-actions">
            <Button
              type="primary"
              loading={committing}
              disabled={!canCommit || batch.status !== "preview_ready"}
              onClick={onCommit}
            >
              确认导入
            </Button>
            <span>
              {batch.status === "preview_ready"
                ? "校验失败或待人工确认的行会阻止写入。"
                : batch.status === "committed"
                  ? "该批次已经确认写入并进入历史记录。"
                  : "该批次已撤销，只保留留痕与原始证据。"}
            </span>
          </div>
        </div>
      ) : (
        <div className="account-data-empty-inline">
          <strong>还没有导入预览</strong>
          <p>先选择当前账号的原始导出文件，系统会在页面内完成识别、校验和写入。</p>
        </div>
      )}
    </section>
  );
}
