import type {
  AccountDataDatasetInventoryItem,
  AccountDataDatasetStatus,
  AccountDataStatus,
  AccountDataStatusSource,
} from "../../api/accountData";
import {
  getSourceKindLabel,
  getTemplateLabel,
} from "./statusMeta";

const domains = [
  { key: "account_metrics", label: "账号概览" },
  { key: "content_metrics", label: "作品表现" },
  { key: "audience_profiles", label: "粉丝画像" },
  { key: "benchmarks", label: "对标基准" },
] as const;

function formatSourcePeriod(source: AccountDataStatusSource | undefined) {
  if (!source) return "尚无已确认来源";
  if (source.period_start && source.period_end) {
    return `${source.period_start} 至 ${source.period_end}`;
  }
  const date = new Date(source.committed_at);
  if (Number.isNaN(date.getTime())) return "已确认";
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(date);
}

function getDatasetStatusLabel(status: AccountDataDatasetStatus) {
  if (status === "available") return "已有可用数据";
  if (status === "stale") return "数据需要更新";
  if (status === "processing") return "正在导入";
  if (status === "failed") return "最近导入失败";
  return "尚未导入";
}

function inventoryFallback(
  status: AccountDataStatus,
  dataDomain: string,
): AccountDataDatasetInventoryItem {
  const source = status.sources.find((item) => item.data_domain === dataDomain) ?? null;
  return {
    data_domain: dataDomain,
    status: status.coverage[dataDomain] === "available" ? "available" : "not_imported",
    confirmed_period_start: source?.period_start ?? null,
    confirmed_period_end: source?.period_end ?? null,
    latest_source: source,
  };
}

type DataCoverageOverviewProps = {
  status: AccountDataStatus;
  onImportDomain: (domain: string) => void;
  onAnalyze: (domain: string) => void;
};

export function DataCoverageOverview({
  status,
  onImportDomain,
  onAnalyze,
}: DataCoverageOverviewProps) {
  const inventory = domains.map((domain) => (
    status.dataset_inventory?.find((item) => item.data_domain === domain.key)
    ?? inventoryFallback(status, domain.key)
  ));
  const availableCount = inventory.filter(
    (item) => item.status === "available" || item.status === "stale",
  ).length;
  const hasExplicitInventory = Boolean(status.dataset_inventory?.length);
  const conclusion = availableCount === 0
    ? "当前账号还没有可供运营分析的数据。"
    : hasExplicitInventory
      ? `已导入 ${availableCount}/${status.dataset_inventory!.length} 类数据，当前账号已有可用数据`
      : `已导入 ${availableCount} 类数据，当前账号已有可用数据`;

  return (
    <section className="account-data-overview" aria-labelledby="account-data-overview-title">
      <div className="account-data-overview-head">
        <div>
          <span>数据健康度</span>
          <h2 id="account-data-overview-title">{conclusion}</h2>
          <p>
            账号数据由多类平台导出共同组成；这里只统计已确认写入的数据，
            待确认或处理中的文件不会提前进入分析口径。
          </p>
        </div>
        {availableCount === 0 ? (
          <button type="button" onClick={() => onImportDomain("account_metrics")}>
            导入第一份数据
          </button>
        ) : null}
      </div>

      <div className="account-data-domain-grid">
        {domains.map((domain) => {
          const dataset = inventory.find((item) => item.data_domain === domain.key)!;
          const source = dataset.latest_source ?? undefined;
          const needsImport = dataset.status !== "available";
          return (
            <article key={domain.key} className={`account-data-domain-row is-${dataset.status}`}>
              <div>
                <span>{domain.label}</span>
                <strong>{getDatasetStatusLabel(dataset.status)}</strong>
              </div>
              <div>
                <span>数据时间</span>
                <strong>
                  {dataset.confirmed_period_start && dataset.confirmed_period_end
                    ? `${dataset.confirmed_period_start} 至 ${dataset.confirmed_period_end}`
                    : formatSourcePeriod(source)}
                </strong>
              </div>
              <div>
                <span>最近来源</span>
                <strong>
                  {source
                    ? `${getSourceKindLabel(source.source_kind)} · ${getTemplateLabel(source.template_code)}`
                    : "尚未导入"}
                </strong>
              </div>
              <button
                type="button"
                onClick={() => {
                  if (needsImport) onImportDomain(domain.key);
                  else onAnalyze(domain.key);
                }}
              >
                {needsImport ? "添加此类数据" : "交给运营大脑分析"}
              </button>
            </article>
          );
        })}
      </div>
    </section>
  );
}
