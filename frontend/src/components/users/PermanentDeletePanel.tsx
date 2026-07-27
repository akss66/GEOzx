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

const DELETION_COUNT_LABELS: Record<string, string> = {
  users: "成员账号",
  brain_tasks: "运营大脑任务",
  content_items: "内容条目",
  matrix_distribution_plans: "矩阵分发计划",
  knowledge_entries: "知识库条目",
  llm_calls: "模型调用记录",
  task_briefs: "任务上下文",
  orchestration_plans: "Agent 编排计划",
  agent_invocations: "专家调用记录",
  agent_tool_calls: "工具调用记录",
  deliverable_acceptances: "交付物验收记录",
  deliverables: "交付物",
  agent_tasks: "专家任务",
  gate_approvals: "人工审批记录",
  compliance_checks: "合规检查记录",
  material_assets: "素材资产",
  optimization_suggestions: "优化建议",
  metric_snapshots: "数据快照",
  matrix_distribution_items: "矩阵分发子任务",
  knowledge_citations: "知识引用",
  knowledge_suggestions: "知识沉淀建议",
  knowledge_reviews_redacted: "已脱敏知识审核记录",
  client_memberships: "客户权限关系",
  project_memberships: "项目权限关系",
  account_memberships: "账号权限关系",
  notifications: "通知",
  admin_security_credentials: "管理员安全凭据",
  data_import_batches_created_by_redacted: "已脱敏数据导入批次",
  events: "审计事件",
  cost_records: "成本记录",
};

export function PermanentDeletePanel({
  user,
  onDeleted,
}: {
  user: UserDetail;
  onDeleted: (userId: number) => void;
}) {
  const [preview, setPreview] = useState<Awaited<ReturnType<typeof previewUserDeletion>> | null>(null);
  const [secondaryPassword, setSecondaryPassword] = useState("");
  const [previewing, setPreviewing] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [feedback, setFeedback] = useState<{ tone: "neutral" | "error"; text: string } | null>(null);

  function clearSensitiveInputs() {
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
      setPreview(await previewUserDeletion(user.id));
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
          <p>这是不可逆操作。先查看完整影响范围，再输入当前管理员自己的二级密码确认。</p>
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
                <span>{DELETION_COUNT_LABELS[label] ?? "其他关联记录"}</span>
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
              <span>执行人二级密码</span>
              <Input.Password
                aria-label="执行人二级密码"
                name="admin_secondary_password_for_deletion"
                autoComplete="off"
                data-1p-ignore="true"
                data-lpignore="true"
                value={secondaryPassword}
                onChange={(event) => setSecondaryPassword(event.target.value)}
              />
            </label>
          </div>

          <div className="tz-delete-preview__actions">
            <Button onClick={resetFlow}>关闭删除流程</Button>
            <Button
              danger
              type="primary"
              onClick={handleDelete}
              loading={deleting}
              disabled={!preview.allowed || secondaryPassword.length === 0}
            >
              确认永久删除
            </Button>
          </div>
        </div>
      ) : null}
    </section>
  );
}
