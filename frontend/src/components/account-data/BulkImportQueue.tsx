import { UploadOutlined } from "@ant-design/icons";
import {
  type ChangeEvent,
  type DragEvent,
  type KeyboardEvent,
  useEffect,
  useRef,
  useState,
} from "react";

import {
  createAccountDataImportJob,
  getAccountDataImportJob,
  retryAccountDataImportFile,
  type AccountDataImportFileStatus,
  type AccountDataImportJob,
} from "../../api/accountData";
import { presentApiError } from "../../api/errors";
import { getTemplateLabel } from "./statusMeta";

const ACTIVE_STATUSES = new Set(["queued", "processing"]);

const FILE_STATUS_LABELS: Record<AccountDataImportFileStatus, string> = {
  queued: "等待处理",
  processing: "正在识别",
  completed: "已写入",
  partially_completed: "部分写入",
  failed: "导入失败",
};

function requestId() {
  return globalThis.crypto?.randomUUID?.()
    ?? `bulk-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function mergeJob(
  jobs: AccountDataImportJob[],
  incoming: AccountDataImportJob,
) {
  const index = jobs.findIndex((item) => item.id === incoming.id);
  if (index < 0) return [...jobs, incoming];
  return jobs.map((item) => (item.id === incoming.id ? incoming : item));
}

function failureMessage(errorPayload: Record<string, unknown>) {
  if (typeof errorPayload.message === "string") return errorPayload.message;
  const failures = errorPayload.failures;
  if (Array.isArray(failures) && failures.length > 0) {
    const first = failures[0];
    if (
      first
      && typeof first === "object"
      && "message" in first
      && typeof first.message === "string"
    ) {
      return first.message;
    }
  }
  return null;
}

export function BulkImportQueue({
  accountId,
  onTerminal,
}: {
  accountId: number;
  onTerminal: () => void;
}) {
  const [jobs, setJobs] = useState<AccountDataImportJob[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [retryingFileId, setRetryingFileId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const notifiedRef = useRef(new Set<number>());

  useEffect(() => {
    const activeJobs = jobs.filter((job) => ACTIVE_STATUSES.has(job.status));
    if (activeJobs.length === 0) return;
    const timer = window.setInterval(() => {
      void Promise.all(
        activeJobs.map(async (job) => {
          try {
            const next = await getAccountDataImportJob(accountId, job.id);
            setJobs((current) => mergeJob(current, next));
          } catch (pollError) {
            setError(presentApiError(pollError, "导入进度暂时无法刷新。").message);
          }
        }),
      );
    }, 1200);
    return () => window.clearInterval(timer);
  }, [accountId, jobs]);

  useEffect(() => {
    jobs.forEach((job) => {
      if (
        !ACTIVE_STATUSES.has(job.status)
        && !notifiedRef.current.has(job.id)
      ) {
        notifiedRef.current.add(job.id);
        onTerminal();
      }
    });
  }, [jobs, onTerminal]);

  async function submitFiles(fileList: FileList | File[]) {
    const files = Array.from(fileList);
    if (files.length === 0) return;
    setSubmitting(true);
    setError(null);
    try {
      const job = await createAccountDataImportJob(
        accountId,
        files,
        requestId(),
      );
      setJobs((current) => mergeJob(current, job));
    } catch (submitError) {
      setError(presentApiError(submitError, "这些文件暂时无法加入导入队列。").message);
    } finally {
      setSubmitting(false);
    }
  }

  function handleChange(event: ChangeEvent<HTMLInputElement>) {
    if (event.target.files) void submitFiles(event.target.files);
    event.target.value = "";
  }

  function handleDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    void submitFiles(event.dataTransfer.files);
  }

  function handleKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      inputRef.current?.click();
    }
  }

  async function retry(jobId: number, fileId: number) {
    setRetryingFileId(fileId);
    setError(null);
    try {
      const job = await retryAccountDataImportFile(accountId, jobId, fileId);
      setJobs((current) => mergeJob(current, job));
      notifiedRef.current.delete(job.id);
    } catch (retryError) {
      setError(presentApiError(retryError, "该文件暂时无法重试。").message);
    } finally {
      setRetryingFileId(null);
    }
  }

  return (
    <section className="account-data-bulk-import" aria-labelledby="bulk-import-title">
      <header className="account-data-section-head">
        <div>
          <span>批量导入</span>
          <h2 id="bulk-import-title">继续添加账号数据文件</h2>
          <p>可一次拖入多个 Excel 或 CSV。系统会识别每个文件和工作表，成功项直接写入，失败项单独重试。</p>
        </div>
      </header>

      <div
        className="account-data-dropzone"
        role="button"
        tabIndex={0}
        aria-label="拖入或选择账号数据文件"
        onClick={() => inputRef.current?.click()}
        onKeyDown={handleKeyDown}
        onDragOver={(event) => event.preventDefault()}
        onDrop={handleDrop}
      >
        <UploadOutlined aria-hidden />
        <strong>{submitting ? "正在加入队列…" : "拖入文件，或点击选择"}</strong>
        <span>支持同时选择多个 .xlsx、.csv 文件，单次最多 20 个</span>
        <input
          ref={inputRef}
          type="file"
          multiple
          accept=".xlsx,.csv"
          aria-label="选择账号数据文件"
          disabled={submitting}
          onChange={handleChange}
        />
      </div>

      {error ? <div className="account-data-feedback is-error" role="alert">{error}</div> : null}

      <div className="account-data-import-queue" aria-live="polite">
        {jobs.flatMap((job) => job.files.map((file) => {
          const canRetry = file.status === "failed" || file.status === "partially_completed";
          const message = failureMessage(file.error_payload);
          return (
            <article
              className={`account-data-import-file is-${file.status}`}
              key={`${job.id}:${file.id}`}
            >
              <div className="account-data-import-file__main">
                <div>
                  <strong>{file.filename}</strong>
                  <span>{`${Math.max(1, Math.round(file.byte_size / 1024))} KB`}</span>
                </div>
                <span className="account-data-import-file__status">
                  {FILE_STATUS_LABELS[file.status]}
                </span>
              </div>
              {file.datasets.length > 0 ? (
                <ul>
                  {file.datasets.map((dataset) => (
                    <li key={dataset.id}>
                      <strong>{getTemplateLabel(dataset.template_code)}</strong>
                      <span>
                        {`${dataset.sheet_name ?? "默认工作表"} · ${dataset.row_count} 行`}
                      </span>
                    </li>
                  ))}
                </ul>
              ) : null}
              {message ? <p className="account-data-import-file__error">{message}</p> : null}
              {canRetry ? (
                <button
                  type="button"
                  aria-label={`重试 ${file.filename}`}
                  disabled={retryingFileId === file.id}
                  onClick={() => void retry(job.id, file.id)}
                >
                  {retryingFileId === file.id ? "正在重试…" : "重试此文件"}
                </button>
              ) : null}
            </article>
          );
        }))}
      </div>
    </section>
  );
}
