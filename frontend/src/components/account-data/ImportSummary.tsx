import type { AccountDataImportBatch, AccountDataImportRowPage } from "../../api/accountData";
import { getTemplateLabel } from "./statusMeta";

function formatPeriod(start: string | null, end: string | null) {
  if (!start && !end) return "未识别";
  if (start === end || !end) return start ?? end ?? "未识别";
  return `${start ?? "—"} 至 ${end}`;
}

export function ImportSummary({
  batch,
  rowPage,
}: {
  batch: AccountDataImportBatch;
  rowPage: AccountDataImportRowPage | null;
}) {
  const total = rowPage?.total_count ?? batch.row_count;
  return (
    <section className="account-data-import-summary" aria-label="导入摘要">
      <div>
        <span>数据类型</span>
        <strong>{getTemplateLabel(batch.template_code)}</strong>
      </div>
      <div>
        <span>原始文件</span>
        <strong>{batch.artifacts[0]?.filename ?? "未附带文件"}</strong>
      </div>
      <div>
        <span>数据周期</span>
        <strong>{formatPeriod(batch.period_start, batch.period_end)}</strong>
      </div>
      <div>
        <span>校验结果</span>
        <strong>
          {rowPage
            ? `${total} 条 · 可写入 ${rowPage.ready_count} · 需处理 ${rowPage.blocking_count}`
            : `${total} 条`}
        </strong>
      </div>
      <details className="account-data-technical-details">
        <summary>技术校验详情</summary>
        <dl>
          <div><dt>模板编码</dt><dd>{batch.template_code}</dd></div>
          <div><dt>批次编号</dt><dd>{batch.id}</dd></div>
          <div><dt>来源类型</dt><dd>{batch.source_kind}</dd></div>
        </dl>
      </details>
    </section>
  );
}
