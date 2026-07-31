import type {
  AccountDataCoverage,
  AccountDataStatus,
  AccountDataStatusSource,
} from "../../api/accountData";
import {
  getCoverageLabel,
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
  const availableCount = Object.values(status.coverage).filter(
    (coverage) => coverage !== "missing",
  ).length;
  const conclusion = availableCount === 0
    ? "当前账号还没有可供运营分析的数据。"
    : availableCount === domains.length
      ? "当前账号已有完整可用数据"
      : "当前账号已有部分可用数据";

  return (
    <section className="account-data-overview" aria-labelledby="account-data-overview-title">
      <div className="account-data-overview-head">
        <div>
          <span>数据健康度</span>
          <h2 id="account-data-overview-title">{conclusion}</h2>
          <p>这里只统计已确认写入的数据，待确认批次不会提前进入分析口径。</p>
        </div>
        {availableCount === 0 ? (
          <button type="button" onClick={() => onImportDomain("account_metrics")}>
            导入第一份数据
          </button>
        ) : null}
      </div>

      <div className="account-data-domain-grid">
        {domains.map((domain) => {
          const coverage = status.coverage[domain.key] ?? "missing";
          const source = status.sources.find((item) => item.data_domain === domain.key);
          return (
            <article key={domain.key} className={`account-data-domain-row is-${coverage}`}>
              <div>
                <span>{domain.label}</span>
                <strong>{getCoverageLabel(coverage as AccountDataCoverage)}</strong>
              </div>
              <div>
                <span>数据时间</span>
                <strong>{formatSourcePeriod(source)}</strong>
              </div>
              <div>
                <span>最近来源</span>
                <strong>
                  {source
                    ? `${getSourceKindLabel(source.source_kind)} · ${getTemplateLabel(source.template_code)}`
                    : "待补齐"}
                </strong>
              </div>
              <button
                type="button"
                onClick={() => {
                  if (coverage === "missing") onImportDomain(domain.key);
                  else onAnalyze(domain.key);
                }}
              >
                {coverage === "missing" ? "补齐数据" : "交给运营大脑分析"}
              </button>
            </article>
          );
        })}
      </div>
    </section>
  );
}
