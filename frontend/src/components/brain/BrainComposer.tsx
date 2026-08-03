import {
  CloseOutlined,
  EditOutlined,
  SafetyCertificateOutlined,
  SendOutlined,
  StopOutlined,
} from "@ant-design/icons";
import { Button, Input } from "antd";
import { useEffect, useRef, useState } from "react";

import type { ConversationApproval, PublicSkill } from "../../types";
import { presentOperationsBrainSystemCopy } from "../../utils/operationsBrainCopy";
import {
  CapabilityLauncher,
  type CapabilityLauncherContextCallbacks,
} from "./CapabilityLauncher";
import { AttachmentTray, type DraftAttachment } from "./AttachmentTray";

export function BrainComposer({
  value,
  disabled,
  loading,
  pendingPermission,
  approvalComment,
  approving,
  promptChips = [],
  skills = [],
  onSelectSkill,
  onAddFilesAndMaterials,
  onAddAccountDataPackage,
  onAddHistoricalArtifacts,
  onSelectAccount,
  attachments = [],
  attachmentBusy = false,
  onFilesSelected,
  onRemoveAttachment,
  onRetryAttachment,
  onChange,
  onApprovalCommentChange,
  onApprovePermission,
  onSubmit,
  onStop,
}: {
  value: string;
  disabled: boolean;
  loading: boolean;
  pendingPermission: ConversationApproval | null;
  approvalComment: string;
  approving: boolean;
  promptChips?: string[];
  skills?: PublicSkill[];
  onSelectSkill?: (skillCode: string) => void;
  onAddFilesAndMaterials?: CapabilityLauncherContextCallbacks["onAddFilesAndMaterials"];
  onAddAccountDataPackage?: CapabilityLauncherContextCallbacks["onAddAccountDataPackage"];
  onAddHistoricalArtifacts?: CapabilityLauncherContextCallbacks["onAddHistoricalArtifacts"];
  onSelectAccount?: CapabilityLauncherContextCallbacks["onSelectAccount"];
  attachments?: DraftAttachment[];
  attachmentBusy?: boolean;
  onFilesSelected?: (files: File[]) => void;
  onRemoveAttachment?: (attachment: DraftAttachment) => void;
  onRetryAttachment?: (attachment: DraftAttachment) => void;
  onChange: (value: string) => void;
  onApprovalCommentChange: (value: string) => void;
  onApprovePermission: (toolCallId: number, approved: boolean, comment?: string) => void;
  onSubmit: () => void;
  onStop?: () => void;
}) {
  const [editingRequirement, setEditingRequirement] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    setEditingRequirement(false);
  }, [pendingPermission?.id]);

  const mode = pendingPermission ? "permission" : "message";
  const comment = approvalComment.trim() || undefined;

  return (
    <section
      className="dy-brain-chat-composer"
      aria-label="运营大脑输入区"
      data-mode={mode}
    >
      <div className="dy-brain-composer-box" data-mode={mode}>
        {pendingPermission ? (
          <div className="tz-brain-permission-mode">
            <div className="tz-brain-permission-copy">
              <span className="tz-brain-permission-icon" aria-hidden="true">
                <SafetyCertificateOutlined />
              </span>
              <div>
                <span className="tz-brain-permission-eyebrow">需要你的确认</span>
                <strong>{permissionName(pendingPermission)}</strong>
                <p>{permissionPurpose(pendingPermission)}</p>
              </div>
            </div>

            {editingRequirement ? (
              <Input.TextArea
                aria-label="修改要求"
                value={approvalComment}
                rows={2}
                maxLength={500}
                autoFocus
                placeholder="写下希望专家如何调整；驳回后会按此要求重做"
                onChange={(event) => onApprovalCommentChange(event.target.value)}
              />
            ) : null}

            <div className="tz-brain-permission-actions">
              <Button
                aria-label="修改要求"
                icon={<EditOutlined />}
                onClick={() => setEditingRequirement((open) => !open)}
              >
                修改要求
              </Button>
              <Button
                aria-label="驳回并重做"
                danger
                icon={<CloseOutlined />}
                loading={approving}
                onClick={() => onApprovePermission(pendingPermission.id, false, comment)}
              >
                驳回并重做
              </Button>
              <Button
                aria-label="允许"
                type="primary"
                loading={approving}
                onClick={() => onApprovePermission(pendingPermission.id, true, comment)}
              >
                允许
              </Button>
            </div>
          </div>
        ) : (
          <>
            <CapabilityLauncher
              skills={skills}
              disabled={disabled || loading || attachmentBusy}
              onSelectSkill={onSelectSkill}
              onAddFilesAndMaterials={() => {
                onAddFilesAndMaterials?.();
                fileInputRef.current?.click();
              }}
              onAddAccountDataPackage={onAddAccountDataPackage}
              onAddHistoricalArtifacts={onAddHistoricalArtifacts}
              onSelectAccount={onSelectAccount}
            />
            <input
              ref={fileInputRef}
              type="file"
              multiple
              hidden
              aria-label="选择对话附件"
              accept=".txt,.csv,.json,.pdf,.xlsx,.png,.jpg,.jpeg,.webp"
              onChange={(event) => {
                const files = Array.from(event.currentTarget.files ?? []);
                if (files.length > 0) onFilesSelected?.(files);
                event.currentTarget.value = "";
              }}
            />
            <AttachmentTray
              attachments={attachments}
              onRemove={onRemoveAttachment ?? (() => undefined)}
              onRetry={onRetryAttachment ?? (() => undefined)}
            />
            <Input.TextArea
              aria-label="运营大脑消息"
              value={value}
              disabled={disabled || loading}
              onChange={(event) => onChange(event.target.value)}
              autoSize={{ minRows: 1, maxRows: 6 }}
              data-autosize-min-rows="1"
              data-autosize-max-rows="6"
              maxLength={420}
              showCount
              className="dy-brain-input"
              onKeyDown={(event) => {
                if (
                  event.key !== "Enter"
                  || event.shiftKey
                  || event.nativeEvent.isComposing
                  || disabled
                  || loading
                  || attachmentBusy
                ) return;
                event.preventDefault();
                onSubmit();
              }}
            />
            <div className="dy-brain-composer-tools">
              <div className="dy-brain-prompts">
                {promptChips.map((item) => (
                  <button key={item} type="button" onClick={() => onChange(appendPrompt(value, item))}>
                    {item}
                  </button>
                ))}
              </div>
              {loading && onStop ? (
                <Button
                  type="primary"
                  size="large"
                  aria-label="停止生成"
                  className="dy-brain-stop-button"
                  icon={<StopOutlined />}
                  onClick={onStop}
                />
              ) : (
                <Button
                  type="primary"
                  size="large"
                  aria-label="发送给运营大脑"
                  className="dy-brain-send-button"
                  icon={<SendOutlined />}
                  disabled={disabled || attachmentBusy || value.trim().length === 0}
                  onClick={onSubmit}
                />
              )}
            </div>
          </>
        )}
      </div>
    </section>
  );
}

function appendPrompt(value: string, prompt: string) {
  const trimmed = value.trim();
  return trimmed ? `${trimmed}\n${prompt}` : prompt;
}

function permissionName(toolCall: ConversationApproval) {
  const names: Record<string, string> = {
    brief_builder: "整理任务目标",
    compliance_precheck: "执行合规预检查",
    material_validator: "校验素材与封面",
    publish_package_prepare: "生成发布包并进入人工审批",
  };
  if (names[toolCall.tool_code]) return names[toolCall.tool_code];
  return toolCall.tool_name || toolCall.tool_code || "执行高风险动作";
}

function permissionPurpose(toolCall: ConversationApproval) {
  return presentOperationsBrainSystemCopy(
    toolCall.output_summary
      || toolCall.input_summary
      || "确认后运营大脑将继续当前工作流。",
  );
}
