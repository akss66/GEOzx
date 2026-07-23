import { CheckOutlined, FileImageOutlined, PlusOutlined } from "@ant-design/icons";
import { Button } from "antd";
import { type ChangeEvent, type FormEvent, useEffect, useMemo, useState } from "react";

import type {
  AccountDataImportBatch,
  ManualAudienceItem,
  ManualBenchmarkMetric,
  ManualPreviewPayload,
} from "../../api/accountData";
import { getBatchStatusDescription, getBatchStatusLabel } from "./statusMeta";

type ManualDomain = ManualPreviewPayload["data_domain"];

interface FlowFeedback {
  tone: "error" | "success";
  title: string;
  description: string;
}

const DOMAIN_OPTIONS: Array<{ value: ManualDomain; label: string }> = [
  { value: "account_period_totals", label: "账号周期" },
  { value: "audience_dimension", label: "粉丝画像" },
  { value: "benchmark", label: "对标基准" },
];

const TEMPLATE_BY_DOMAIN: Record<ManualDomain, string> = {
  account_period_totals: "manual_account_period_v1",
  audience_dimension: "manual_audience_dimension_v1",
  benchmark: "manual_benchmark_v1",
};

function optionalNumber(value: string) {
  if (value.trim() === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function inputNumber(form: HTMLFormElement, name: string) {
  return optionalNumber(new FormData(form).get(name)?.toString() ?? "");
}

export function ManualDataEntry({
  batch,
  feedback,
  creating,
  confirming,
  committing,
  canCommit,
  onPreview,
  onConfirmRow,
  onCommit,
}: {
  batch: AccountDataImportBatch | null;
  feedback: FlowFeedback | null;
  creating: boolean;
  confirming: boolean;
  committing: boolean;
  canCommit: boolean;
  onPreview: (payload: ManualPreviewPayload, screenshot: File | null) => void;
  onConfirmRow: (rowNumber: number) => void;
  onCommit: () => void;
}) {
  const [domain, setDomain] = useState<ManualDomain>("account_period_totals");
  const [screenshot, setScreenshot] = useState<File | null>(null);
  const [audienceItems, setAudienceItems] = useState<ManualAudienceItem[]>([
    { label: "", value: "", ratio: null },
  ]);
  const [benchmarkMetrics, setBenchmarkMetrics] = useState<ManualBenchmarkMetric[]>([
    { metric_code: "", metric_value: null, sample_size: null },
  ]);

  const screenshotUrl = useMemo(
    () => (screenshot ? URL.createObjectURL(screenshot) : null),
    [screenshot],
  );

  useEffect(() => () => {
    if (screenshotUrl) URL.revokeObjectURL(screenshotUrl);
  }, [screenshotUrl]);

  function handleScreenshot(event: ChangeEvent<HTMLInputElement>) {
    setScreenshot(event.target.files?.[0] ?? null);
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const formData = new FormData(form);
    const base = {
      data_domain: domain,
      stat_date: formData.get("stat_date")?.toString() ?? "",
      period_start: formData.get("period_start")?.toString() || null,
      period_end: formData.get("period_end")?.toString() || null,
    } as const;

    let payload: ManualPreviewPayload;
    if (domain === "account_period_totals") {
      payload = {
        ...base,
        data_domain: domain,
        account_metrics: {
          follower_count: inputNumber(form, "follower_count"),
          follower_delta: inputNumber(form, "follower_delta"),
          total_play: inputNumber(form, "total_play"),
          total_exposure: inputNumber(form, "total_exposure"),
          engagement_rate: (() => {
            const percent = inputNumber(form, "engagement_rate");
            return percent == null ? null : percent / 100;
          })(),
        },
      };
    } else if (domain === "audience_dimension") {
      payload = {
        ...base,
        data_domain: domain,
        dimension: formData.get("dimension")?.toString().trim() ?? "",
        total_audience: inputNumber(form, "total_audience"),
        audience_items: audienceItems.filter((item) => item.label && item.value),
      };
    } else {
      payload = {
        ...base,
        data_domain: domain,
        benchmark_code: formData.get("benchmark_code")?.toString().trim() ?? "",
        benchmark_metrics: benchmarkMetrics.filter((item) => item.metric_code),
      };
    }
    onPreview(payload, screenshot);
  }

  const visibleBatch = batch?.template_code === TEMPLATE_BY_DOMAIN[domain] ? batch : null;
  const visibleFeedback = batch && !visibleBatch ? null : feedback;
  const pendingConfirmation = visibleBatch?.rows.find(
    (row) => row.status === "needs_resolution",
  );

  return (
    <section className="account-data-flow account-data-manual" aria-label="人工录入流程">
      <header className="account-data-section-head">
        <div>
          <span>人工数据工作台</span>
          <h2>人工录入与截图核验</h2>
          <p>补齐无法导出的账号诊断、粉丝画像和对标数据，所有字段都会保留来源与确认记录。</p>
        </div>
      </header>

      <div className="account-data-domain-tabs" role="tablist" aria-label="人工数据类型">
        {DOMAIN_OPTIONS.map((option) => (
          <button
            key={option.value}
            type="button"
            role="tab"
            aria-selected={domain === option.value}
            className={domain === option.value ? "is-active" : undefined}
            onClick={() => setDomain(option.value)}
          >
            {option.label}
          </button>
        ))}
      </div>

      {visibleFeedback ? (
        <div className={`account-data-feedback is-${visibleFeedback.tone}`} role="status">
          <strong>{visibleFeedback.title}</strong>
          <p>{visibleFeedback.description}</p>
        </div>
      ) : null}

      <form className="account-data-manual-grid" onSubmit={handleSubmit}>
        <div className="account-data-manual-fields">
          <div className="account-data-field-grid three">
            <label>
              <span>统计日期</span>
              <input aria-label="统计日期" name="stat_date" type="date" required />
            </label>
            <label>
              <span>周期开始</span>
              <input aria-label="周期开始" name="period_start" type="date" />
            </label>
            <label>
              <span>周期结束</span>
              <input aria-label="周期结束" name="period_end" type="date" />
            </label>
          </div>

          {domain === "account_period_totals" ? (
            <div className="account-data-field-grid">
              <label><span>粉丝总数</span><input aria-label="粉丝总数" name="follower_count" type="number" min="0" /></label>
              <label><span>粉丝净增</span><input aria-label="粉丝净增" name="follower_delta" type="number" /></label>
              <label><span>播放量</span><input aria-label="播放量" name="total_play" type="number" min="0" /></label>
              <label><span>曝光量</span><input aria-label="曝光量" name="total_exposure" type="number" min="0" /></label>
              <label><span>互动率（%）</span><input aria-label="互动率" name="engagement_rate" type="number" min="0" max="100" step="0.01" /></label>
            </div>
          ) : null}

          {domain === "audience_dimension" ? (
            <div className="account-data-manual-list">
              <div className="account-data-field-grid">
                <label><span>画像维度</span><input aria-label="画像维度" name="dimension" placeholder="例如：年龄、地区、设备" required /></label>
                <label><span>样本人数</span><input aria-label="样本人数" name="total_audience" type="number" min="0" /></label>
              </div>
              {audienceItems.map((item, index) => (
                <div className="account-data-repeat-row" key={`audience-${index}`}>
                  <input aria-label={`画像标签 ${index + 1}`} value={item.label} placeholder="标签" onChange={(event) => setAudienceItems((items) => items.map((current, itemIndex) => itemIndex === index ? { ...current, label: event.target.value } : current))} />
                  <input aria-label={`画像值 ${index + 1}`} value={item.value} placeholder="值" onChange={(event) => setAudienceItems((items) => items.map((current, itemIndex) => itemIndex === index ? { ...current, value: event.target.value } : current))} />
                  <input aria-label={`画像占比 ${index + 1}`} type="number" min="0" max="100" step="0.01" placeholder="占比 %" value={item.ratio == null ? "" : item.ratio * 100} onChange={(event) => setAudienceItems((items) => items.map((current, itemIndex) => itemIndex === index ? { ...current, ratio: optionalNumber(event.target.value) == null ? null : Number(event.target.value) / 100 } : current))} />
                </div>
              ))}
              <Button type="text" icon={<PlusOutlined />} onClick={() => setAudienceItems((items) => [...items, { label: "", value: "", ratio: null }])}>增加画像项</Button>
            </div>
          ) : null}

          {domain === "benchmark" ? (
            <div className="account-data-manual-list">
              <label className="account-data-wide-field"><span>基准名称</span><input aria-label="基准名称" name="benchmark_code" placeholder="例如：同行创作者周诊断" required /></label>
              {benchmarkMetrics.map((item, index) => (
                <div className="account-data-repeat-row" key={`benchmark-${index}`}>
                  <input aria-label={`指标代码 ${index + 1}`} value={item.metric_code} placeholder="指标代码" onChange={(event) => setBenchmarkMetrics((items) => items.map((current, itemIndex) => itemIndex === index ? { ...current, metric_code: event.target.value } : current))} />
                  <input aria-label={`指标值 ${index + 1}`} type="number" step="any" value={item.metric_value ?? ""} placeholder="指标值" onChange={(event) => setBenchmarkMetrics((items) => items.map((current, itemIndex) => itemIndex === index ? { ...current, metric_value: optionalNumber(event.target.value) } : current))} />
                  <input aria-label={`样本量 ${index + 1}`} type="number" min="0" value={item.sample_size ?? ""} placeholder="样本量" onChange={(event) => setBenchmarkMetrics((items) => items.map((current, itemIndex) => itemIndex === index ? { ...current, sample_size: optionalNumber(event.target.value) } : current))} />
                </div>
              ))}
              <Button type="text" icon={<PlusOutlined />} onClick={() => setBenchmarkMetrics((items) => [...items, { metric_code: "", metric_value: null, sample_size: null }])}>增加基准指标</Button>
            </div>
          ) : null}

          <Button type="primary" htmlType="submit" loading={creating}>生成录入预览</Button>
        </div>

        <aside className="account-data-evidence-pane">
          <div>
            <FileImageOutlined />
            <strong>截图证据（可选）</strong>
            <p>当前未接视觉识别时，系统不会猜测字段；请对照截图人工填写。</p>
          </div>
          <label className="account-data-evidence-upload">
            <span>选择截图</span>
            <input aria-label="上传数据截图" type="file" accept="image/png,image/jpeg,image/webp" onChange={handleScreenshot} />
          </label>
          {screenshotUrl ? <img src={screenshotUrl} alt="待核对的数据截图" /> : null}
          <span>{screenshot?.name ?? visibleBatch?.artifacts[0]?.filename ?? "未附加截图"}</span>
        </aside>
      </form>

      {visibleBatch ? (
        <div className="account-data-manual-preview">
          <div>
            <span>当前预览</span>
            <strong>{visibleBatch.template_code}</strong>
            <p>{`${getBatchStatusLabel(visibleBatch.status)}：${getBatchStatusDescription(visibleBatch.status)}`}</p>
          </div>
          <div className="account-data-manual-actions">
            {pendingConfirmation ? (
              <Button loading={confirming} icon={<CheckOutlined />} onClick={() => onConfirmRow(pendingConfirmation.row_number)}>确认截图数据</Button>
            ) : null}
            <Button type="primary" loading={committing} disabled={!canCommit} onClick={onCommit}>确认写入</Button>
          </div>
        </div>
      ) : null}
    </section>
  );
}
