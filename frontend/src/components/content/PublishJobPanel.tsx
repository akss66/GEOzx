import {
  CheckCircleFilled,
  ClockCircleOutlined,
  CloseCircleOutlined,
  ExportOutlined,
  QrcodeOutlined,
  ReloadOutlined,
} from "@ant-design/icons";
import QRCode from "qrcode";
import { useState } from "react";

import {
  cancelPublishJob,
  markPublishJobLaunched,
  preparePublishHandoff,
  retryPublishJob,
} from "../../services/publishing";
import type {
  PublishHandoff,
  PublishJob,
} from "../../types/publishing";

interface PublishJobPanelProps {
  job: PublishJob;
  onJobChange: (job: PublishJob) => void;
  prepareHandoff?: (jobId: number) => Promise<PublishHandoff>;
  markLaunched?: (jobId: number) => Promise<PublishJob>;
  retryJob?: (jobId: number) => Promise<PublishJob>;
  cancelJob?: (jobId: number) => Promise<PublishJob>;
  openSchema?: (schemaUrl: string) => void;
}

export function PublishJobPanel({
  job,
  onJobChange,
  prepareHandoff = preparePublishHandoff,
  markLaunched = markPublishJobLaunched,
  retryJob = retryPublishJob,
  cancelJob = cancelPublishJob,
  openSchema = openDouyinSchema,
}: PublishJobPanelProps) {
  const [qrDataUrl, setQrDataUrl] = useState<string | null>(null);
  const [schemaUrl, setSchemaUrl] = useState<string | null>(null);
  const [expiresAt, setExpiresAt] = useState<string | null>(null);
  const [busy, setBusy] = useState<
    "handoff" | "launch" | "retry" | "cancel" | null
  >(null);
  const [error, setError] = useState<string | null>(null);

  async function beginHandoff() {
    setBusy("handoff");
    setError(null);
    try {
      const handoff = await prepareHandoff(job.id);
      const nextQrDataUrl = await QRCode.toDataURL(handoff.schema_url, {
        errorCorrectionLevel: "M",
        margin: 2,
        width: 320,
        color: {
          dark: "#171614",
          light: "#ffffff",
        },
      });
      setQrDataUrl(nextQrDataUrl);
      setSchemaUrl(handoff.schema_url);
      setExpiresAt(handoff.expires_at);
      onJobChange(handoff.job);
    } catch (cause) {
      setError(publishingErrorMessage(cause));
    } finally {
      setBusy(null);
    }
  }

  async function launchDouyin() {
    if (!schemaUrl) return;
    openSchema(schemaUrl);
    setBusy("launch");
    setError(null);
    try {
      onJobChange(await markLaunched(job.id));
    } catch (cause) {
      setError(publishingErrorMessage(cause));
    } finally {
      setBusy(null);
    }
  }

  async function retry() {
    setBusy("retry");
    setError(null);
    try {
      const next = await retryJob(job.id);
      setQrDataUrl(null);
      setSchemaUrl(null);
      setExpiresAt(null);
      onJobChange(next);
    } catch (cause) {
      setError(publishingErrorMessage(cause));
    } finally {
      setBusy(null);
    }
  }

  async function cancel() {
    if (!window.confirm("确定取消这次抖音投稿任务吗？审批和操作记录仍会保留。")) {
      return;
    }
    setBusy("cancel");
    setError(null);
    try {
      onJobChange(await cancelJob(job.id));
    } catch (cause) {
      setError(publishingErrorMessage(cause));
    } finally {
      setBusy(null);
    }
  }

  const canCancel = [
    "pending_approval",
    "task_created",
    "handoff_ready",
    "waiting_bind",
  ].includes(job.status);

  return (
    <section className="publish-job-panel" data-status={job.status}>
      <header className="publish-job-panel__header">
        <div>
          <span className="publish-job-panel__eyebrow">抖音官方投稿</span>
          <h3>{statusTitle(job)}</h3>
        </div>
        <StatusMark status={job.status} />
      </header>

      <p className="publish-job-panel__description">{statusDescription(job)}</p>

      {qrDataUrl ? (
        <div className="publish-job-panel__handoff">
          <img src={qrDataUrl} alt="抖音投稿二维码" />
          <div>
            <strong>请使用抖音客户端扫码完成投稿</strong>
            <p>素材与文案会交给抖音官方发布页，最终发布仍由你确认。</p>
            {expiresAt ? <small>二维码有效至 {formatTime(expiresAt)}</small> : null}
            {schemaUrl ? (
              <button
                type="button"
                className="publish-job-panel__direct"
                onClick={launchDouyin}
                disabled={busy !== null}
              >
                <ExportOutlined />
                {busy === "launch" ? "正在打开..." : "尝试直接打开抖音"}
              </button>
            ) : null}
          </div>
        </div>
      ) : null}

      {job.status === "bound" ||
      job.status === "observing" ||
      job.status === "completed" ? (
        <div className="publish-job-panel__success">
          <CheckCircleFilled />
          <div>
            <strong>官方回流已建立</strong>
            <span>后续数据会归入当前账号、客户和项目上下文。</span>
          </div>
        </div>
      ) : null}

      {error ? <div className="publish-job-panel__error">{error}</div> : null}

      <div className="publish-job-panel__actions">
        {!qrDataUrl && ["task_created", "handoff_ready"].includes(job.status) ? (
          <button
            type="button"
            className="publish-job-panel__primary"
            onClick={beginHandoff}
            disabled={busy !== null}
          >
            <QrcodeOutlined />
            {busy === "handoff" ? "正在准备..." : "生成抖音投稿二维码"}
          </button>
        ) : null}
        {["failed", "expired"].includes(job.status) ? (
          <button
            type="button"
            className="publish-job-panel__primary"
            onClick={retry}
            disabled={busy !== null}
          >
            <ReloadOutlined />
            {busy === "retry" ? "正在重置..." : "重新准备"}
          </button>
        ) : null}
        {canCancel ? (
          <button
            type="button"
            className="publish-job-panel__secondary"
            onClick={cancel}
            disabled={busy !== null}
          >
            {busy === "cancel" ? "正在取消..." : "取消任务"}
          </button>
        ) : null}
      </div>
    </section>
  );
}

function StatusMark({ status }: { status: PublishJob["status"] }) {
  if (["bound", "observing", "completed"].includes(status)) {
    return (
      <span className="publish-job-panel__status is-success">
        <CheckCircleFilled />
        已绑定
      </span>
    );
  }
  if (["failed", "expired", "cancelled"].includes(status)) {
    return (
      <span className="publish-job-panel__status is-error">
        <CloseCircleOutlined />
        {status === "cancelled" ? "已取消" : "需处理"}
      </span>
    );
  }
  return (
    <span className="publish-job-panel__status">
      <ClockCircleOutlined />
      进行中
    </span>
  );
}

function statusTitle(job: PublishJob) {
  const titles: Record<PublishJob["status"], string> = {
    draft: "发布任务草稿",
    pending_approval: "等待人工审批",
    task_created: "发布包已批准",
    handoff_ready: "投稿交接已就绪",
    user_publishing: "正在抖音完成发布",
    waiting_bind: "等待抖音回调",
    bound: "作品已绑定",
    observing: "正在观察作品数据",
    completed: "发布闭环已完成",
    failed: "投稿准备失败",
    expired: "投稿二维码已过期",
    cancelled: "投稿任务已取消",
  };
  return titles[job.status];
}

function statusDescription(job: PublishJob) {
  if (job.status === "pending_approval") {
    return "发布包已经冻结账号、素材与文案上下文，通过人工审批后才能提交至抖音。";
  }
  if (job.status === "task_created") {
    return "发布包已通过审批。生成二维码后，使用抖音客户端完成最终发布。";
  }
  if (job.status === "handoff_ready") {
    return "官方投稿交接已生成。页面刷新后可重新生成二维码，并继续在抖音客户端确认。";
  }
  if (job.status === "waiting_bind" || job.status === "user_publishing") {
    return "系统正在等待抖音确认作品身份。请勿重复创建同一投稿任务。";
  }
  if (job.status === "bound") {
    return "抖音已经返回作品身份，系统已将作品与当前发布任务绑定。";
  }
  if (job.status === "observing") {
    return "作品身份已确认，系统正在接收并归集后续表现数据。";
  }
  if (job.status === "completed") {
    return "本次投稿、作品绑定和数据观察链路已经完成。";
  }
  if (job.status === "failed") {
    return job.last_error_message || "抖音投稿准备没有完成，可以重新准备。";
  }
  if (job.status === "expired") {
    return "安全票据已经过期，请重新生成投稿二维码。";
  }
  if (job.status === "cancelled") {
    return "任务已取消，审批、发布包与审计记录仍然保留。";
  }
  return "发布任务正在准备中。";
}

function publishingErrorMessage(error: unknown) {
  if (error && typeof error === "object") {
    const response = (error as {
      response?: {
        data?: {
          error?: {
            message?: unknown;
          };
        };
      };
    }).response;
    const message = response?.data?.error?.message;
    if (typeof message === "string" && message.trim()) {
      return message.trim();
    }
  }
  return "抖音投稿准备失败，请稍后重试。";
}

function formatTime(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function openDouyinSchema(schemaUrl: string) {
  window.location.assign(schemaUrl);
}
