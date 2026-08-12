import { Button, Modal } from "antd";
import { useRef, type RefObject } from "react";

import {
  canConfirmWechatDraftSync,
  requiresWechatDraftSyncManualReview,
  type WechatArticleDraftSyncContext,
} from "../../services/wechatArticle";

interface WechatSyncConfirmationProps {
  context: WechatArticleDraftSyncContext | null;
  open: boolean;
  submitting?: boolean;
  triggerRef?: RefObject<HTMLButtonElement | null>;
  onCancel: () => void;
  onConfirm: () => void;
}

export default function WechatSyncConfirmation({
  context,
  open,
  submitting = false,
  triggerRef,
  onCancel,
  onConfirm,
}: WechatSyncConfirmationProps) {
  const cancelButtonRef = useRef<HTMLElement | null>(null);
  const canConfirm = canConfirmWechatDraftSync(context);
  const needsManualReview = requiresWechatDraftSyncManualReview(context);

  return (
    <Modal
      title="同步确认"
      open={open}
      onCancel={onCancel}
      footer={null}
      destroyOnHidden
      closable={false}
      afterOpenChange={(nextOpen) => {
        if (nextOpen) {
          cancelButtonRef.current?.focus();
          return;
        }
        triggerRef?.current?.focus();
      }}
    >
      {context ? (
        <div className="wechat-sync-dialog">
          <dl>
            <div>
              <dt>目标账号</dt>
              <dd>{context.targetAccount.name}</dd>
            </div>
            <div>
              <dt>文章标题</dt>
              <dd>{context.articleTitle}</dd>
            </div>
            <div>
              <dt>不可变版本</dt>
              <dd>{context.articleVersionId}</dd>
            </div>
            <div>
              <dt>已备图片</dt>
              <dd>{context.imageCount}</dd>
            </div>
          </dl>

          <section className="wechat-sync-dialog__facts">
            <h3>同步前确认</h3>
            <p>未解决事实条目：{context.readiness.unresolvedClaimCount}</p>
            {context.readiness.blockers.length > 0 ? (
              <ul>
                {context.readiness.blockers.map((issue) => (
                  <li key={`${issue.code}-${issue.claimId ?? "global"}`}>{issue.message}</li>
                ))}
              </ul>
            ) : (
              <p>当前版本没有阻断项。</p>
            )}
            {context.readiness.warnings.length > 0 ? (
              <ul className="wechat-sync-dialog__warnings">
                {context.readiness.warnings.map((issue) => (
                  <li key={`${issue.code}-${issue.claimId ?? "warning"}`}>{issue.message}</li>
                ))}
              </ul>
            ) : null}
            <p>
              最近远端状态：
              {context.remote
                ? `${context.remote.status} / ${context.remote.operationType}`
                : "尚无历史同步记录"}
            </p>
            {context.remote?.errorCode ? (
              <p className="wechat-sync-dialog__remote-error">
                远端冲突：{context.remote.errorCode}。请先核对最新草稿，再重新创建同步。
              </p>
            ) : null}
            {needsManualReview ? (
              <p className="wechat-sync-dialog__blocking-note">
                请先查看冲突或人工对账，确认远端草稿状态后再继续同步。
              </p>
            ) : null}
            {!canConfirm ? (
              <p className="wechat-sync-dialog__blocking-note">当前状态不允许直接确认，请先处理阻断项后再同步。</p>
            ) : null}
          </section>

          <div className="wechat-sync-dialog__actions">
            <Button
              autoFocus
              ref={(node) => {
                cancelButtonRef.current = node;
                if (open && node) {
                  node.focus();
                }
              }}
              onClick={onCancel}
            >
              取消
            </Button>
            <Button
              type="primary"
              loading={submitting}
              onClick={onConfirm}
              disabled={!canConfirm}
            >
              {`确认同步到公众号「${context.targetAccount.name}」草稿箱`}
            </Button>
          </div>
        </div>
      ) : null}
    </Modal>
  );
}
