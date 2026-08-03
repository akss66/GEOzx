import {
  CloseOutlined,
  FileOutlined,
  ReloadOutlined,
} from "@ant-design/icons";
import { Button, Spin } from "antd";

export interface DraftAttachment {
  key: string;
  filename: string;
  file: File;
  threadId: number;
  id: number | null;
  status: "uploading" | "ready" | "error" | "removing";
  error?: string;
}

export function AttachmentTray({
  attachments,
  onRemove,
  onRetry,
}: {
  attachments: DraftAttachment[];
  onRemove: (attachment: DraftAttachment) => void;
  onRetry: (attachment: DraftAttachment) => void;
}) {
  if (attachments.length === 0) return null;
  return (
    <ul className="dy-brain-attachment-tray" aria-label="待发送附件">
      {attachments.map((attachment) => (
        <li key={attachment.key} data-status={attachment.status}>
          <FileOutlined aria-hidden="true" />
          <span title={attachment.filename}>{attachment.filename}</span>
          {attachment.status === "uploading" || attachment.status === "removing" ? (
            <Spin size="small" aria-label={`${attachment.filename}处理中`} />
          ) : null}
          {attachment.status === "error" ? (
            <Button
              type="text"
              size="small"
              icon={<ReloadOutlined />}
              aria-label={`重试 ${attachment.filename}`}
              onClick={() => onRetry(attachment)}
            />
          ) : null}
          <Button
            type="text"
            size="small"
            icon={<CloseOutlined />}
            aria-label={`移除 ${attachment.filename}`}
            disabled={attachment.status === "uploading" || attachment.status === "removing"}
            onClick={() => onRemove(attachment)}
          />
          {attachment.error ? <small role="alert">{attachment.error}</small> : null}
        </li>
      ))}
    </ul>
  );
}
