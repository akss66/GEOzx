import { Button, Input } from "antd";
import { useState } from "react";

import { permanentlyDeleteUser, previewUserDeletion } from "../../api/auth";
import type { UserDetail } from "../../types";
import {
  extractGovernanceErrorCode,
  formatDateTime,
  formatDeletionBlocker,
  formatGovernanceError,
} from "./userGovernance";

const PREVIEW_RESET_CODES = new Set([
  "USER_DELETION_PREVIEW_EXPIRED",
  "USER_DELETION_PREVIEW_INVALID",
  "USER_DELETION_PREVIEW_STALE",
  "USER_DELETION_PREVIEW_USED",
]);

export function PermanentDeletePanel({
  user,
  onDeleted,
}: {
  user: UserDetail;
  onDeleted: (userId: number) => void;
}) {
  const [preview, setPreview] = useState<Awaited<ReturnType<typeof previewUserDeletion>> | null>(null);
  const [confirmEmail, setConfirmEmail] = useState("");
  const [secondaryPassword, setSecondaryPassword] = useState("");
  const [previewing, setPreviewing] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [feedback, setFeedback] = useState<{ tone: "neutral" | "error"; text: string } | null>(null);

  function clearSensitiveInputs() {
    setConfirmEmail("");
    setSecondaryPassword("");
  }

  function resetFlow() {
    setPreview(null);
    clearSensitiveInputs();
  }

  async function handlePreview() {
    setPreviewing(true);
    setFeedback(null);
    clearSensitiveInputs();
    try {
      const nextPreview = await previewUserDeletion(user.id);
      setPreview(nextPreview);
    } catch (error) {
      setFeedback({
        tone: "error",
        text: formatGovernanceError(error, "删除影响预览暂时不可用，请稍后重试。"),
      });
    } finally {
      setPreviewing(false);
    }
  }

  async function handleDelete() {
    if (!preview) return;
    setDeleting(true);
    setFeedback(null);
    try {
      await permanentlyDeleteUser(user.id, {
        preview_token: preview.preview_token,
        target_email: confirmEmail,
        secondary_password: secondaryPassword,
      });
      clearSensitiveInputs();
      setPreview(null);
      onDeleted(user.id);
    } catch (error) {
      clearSensitiveInputs();
      const code = extractGovernanceErrorCode(error);
      if (code && PREVIEW_RESET_CODES.has(code)) {
        setPreview(null);
      }
      setFeedback({
        tone: "error",
        text: formatGovernanceError(error, "永久删除失败，成员名册没有被修改。"),
      });
    } finally {
      setDeleting(false);
    }
  }

  return (
    <section className="tz-delete-panel" aria-label="永久删除流程">
      <header>
        <div>
          <h4>永久删除</h4>
          <p>这是不可逆操作。删除前必须先获取影响预览，再核对目标邮箱和执行人二级密码。</p>
        </div>
        <div className="tz-delete-panel__actions">
          <Button danger type={preview ? "default" : "primary"} onClick={handlePreview} loading={previewing}>
            {preview ? "重新获取删除预览" : "获取删除预览"}
          </Button>
        </div>
      </header>

      {feedback ? (
        <p className="tz-inline-feedback is-error" role="alert">
          {feedback.text}
        </p>
      ) : null}

      {preview ? (
        <div className="tz-delete-preview">
          <div className="tz-delete-preview__summary">
            <div>
              <span>不可逆影响预览</span>
              <strong>{user.display_name}</strong>
              <small>{user.email}</small>
            </div>
            <div>
              <span>预览有效期</span>
              <strong>{formatDateTime(preview.expires_at)}</strong>
            </div>
          </div>

          <div className="tz-delete-preview__counts">
            {Object.entries(preview.counts).map(([label, value]) => (
              <div key={label}>
                <span>{label}</span>
                <strong>{value}</strong>
              </div>
            ))}
          </div>

          {preview.blockers.length ? (
            <ul className="tz-delete-preview__blockers">
              {preview.blockers.map((blocker) => (
                <li key={blocker}>{formatDeletionBlocker(blocker)}</li>
              ))}
            </ul>
          ) : null}

          <div className="tz-form-grid tz-form-grid--delete">
            <label className="tz-field">
              <span>确认目标邮箱</span>
              <Input
                aria-label="确认目标邮箱"
                value={confirmEmail}
                onChange={(event) => setConfirmEmail(event.target.value)}
              />
            </label>

            <label className="tz-field">
              <span>执行人二级密码</span>
              <Input.Password
                aria-label="执行人二级密码"
                value={secondaryPassword}
                onChange={(event) => setSecondaryPassword(event.target.value)}
              />
            </label>
          </div>

          <div className="tz-delete-preview__actions">
            <Button onClick={resetFlow}>
              关闭删除流程
            </Button>
            <Button
              danger
              type="primary"
              onClick={handleDelete}
              loading={deleting}
              disabled={!preview.allowed}
            >
              确认永久删除
            </Button>
          </div>
        </div>
      ) : null}
    </section>
  );
}
