import { Button, Input, Skeleton } from "antd";
import { useState } from "react";

import type { User, UserDetail } from "../../types";
import { OperationalState } from "../ui";
import { PermanentDeletePanel } from "./PermanentDeletePanel";
import { formatDateTime } from "./userGovernance";

export function MemberSecurity({
  selectedUser,
  currentUser,
  secondaryStatus,
  secondaryLoading,
  secondaryError,
  secondaryRefreshing,
  onRetrySecondaryStatus,
  onSetSecondaryPassword,
  onResetPassword,
  onDeleted,
}: {
  selectedUser: UserDetail;
  currentUser: User | null;
  secondaryStatus: {
    configured: boolean;
    deletion_available: boolean;
    delete_available_at: string | null;
    locked_until: string | null;
  } | null;
  secondaryLoading: boolean;
  secondaryError: string | null;
  secondaryRefreshing: boolean;
  onRetrySecondaryStatus: () => void;
  onSetSecondaryPassword: (input: { current_password: string; secondary_password: string }) => Promise<void>;
  onResetPassword: (input: { new_password: string }) => Promise<void>;
  onDeleted: (userId: number) => void;
}) {
  const [passwordDraft, setPasswordDraft] = useState("");
  const [passwordSaving, setPasswordSaving] = useState(false);
  const [secondaryDraft, setSecondaryDraft] = useState({
    current_password: "",
    secondary_password: "",
  });
  const [secondarySaving, setSecondarySaving] = useState(false);
  const [feedback, setFeedback] = useState<{ tone: "success" | "error"; text: string } | null>(null);

  const isCurrentAdministrator =
    currentUser?.role === "admin" && currentUser.id === selectedUser.id;
  const isAnotherMember = currentUser?.id !== selectedUser.id;

  async function handleResetPassword() {
    setPasswordSaving(true);
    setFeedback(null);
    try {
      await onResetPassword({ new_password: passwordDraft });
      setPasswordDraft("");
      setFeedback({ tone: "success", text: "成员登录密码已重置。" });
    } catch (error) {
      setFeedback({
        tone: "error",
        text: error instanceof Error ? error.message : "登录密码重置失败。",
      });
    } finally {
      setPasswordSaving(false);
    }
  }

  async function handleSetSecondaryPassword() {
    setSecondarySaving(true);
    setFeedback(null);
    try {
      await onSetSecondaryPassword(secondaryDraft);
      setSecondaryDraft({ current_password: "", secondary_password: "" });
      setFeedback({ tone: "success", text: "当前管理员的二级密码已设置或重置。" });
    } catch (error) {
      setFeedback({
        tone: "error",
        text: error instanceof Error ? error.message : "二级密码更新失败。",
      });
    } finally {
      setSecondarySaving(false);
    }
  }

  return (
    <section className="tz-member-tab-panel tz-member-security">
      {isCurrentAdministrator ? (
        <div className="tz-workbench-block">
          <header className="tz-workbench-block__header">
            <div>
              <h3>当前管理员的二级密码</h3>
              <p>
                二级密码只属于当前登录管理员，用于永久删除等危险操作，系统不会回显原密码。
                如果已经忘记，可使用当前登录密码直接设置一个新的二级密码。
              </p>
            </div>
          </header>

          {secondaryError ? (
            <OperationalState
              compact
              kind="error"
              title="二级密码状态暂不可用"
              description={secondaryError}
              actionLabel="重试"
              actionLoading={secondaryRefreshing}
              onAction={onRetrySecondaryStatus}
            />
          ) : secondaryLoading || !secondaryStatus ? (
            <Skeleton active paragraph={{ rows: 4 }} />
          ) : (
            <>
              <div className="tz-inline-metrics" aria-label="二级密码状态">
                <div className="tz-inline-metric">
                  <span>已配置</span>
                  <strong>{secondaryStatus.configured ? "是" : "否"}</strong>
                </div>
                <div className="tz-inline-metric">
                  <span>可用于删除</span>
                  <strong>{secondaryStatus.deletion_available ? "可用" : "冷却中"}</strong>
                  <small>{formatDateTime(secondaryStatus.delete_available_at)}</small>
                </div>
                <div className="tz-inline-metric">
                  <span>锁定到</span>
                  <strong>{formatDateTime(secondaryStatus.locked_until)}</strong>
                </div>
                <div className="tz-inline-metric">
                  <span>安全说明</span>
                  <strong>仅当前管理员可用</strong>
                </div>
              </div>

              <div className="tz-form-grid tz-form-grid--security">
                <label className="tz-field">
                  <span>当前登录密码</span>
                  <Input.Password
                    aria-label="当前登录密码"
                    name="admin_current_login_password"
                    autoComplete="current-password"
                    value={secondaryDraft.current_password}
                    onChange={(event) =>
                      setSecondaryDraft((current) => ({
                        ...current,
                        current_password: event.target.value,
                      }))
                    }
                  />
                </label>
                <label className="tz-field">
                  <span>新的二级密码</span>
                  <Input.Password
                    aria-label="新的二级密码"
                    name="admin_new_secondary_password"
                    autoComplete="new-password"
                    value={secondaryDraft.secondary_password}
                    onChange={(event) =>
                      setSecondaryDraft((current) => ({
                        ...current,
                        secondary_password: event.target.value,
                      }))
                    }
                  />
                </label>
                <div className="tz-security-actions">
                  <Button
                    type="primary"
                    onClick={handleSetSecondaryPassword}
                    disabled={!secondaryDraft.current_password || !secondaryDraft.secondary_password}
                    loading={secondarySaving}
                  >
                    设置二级密码
                  </Button>
                </div>
              </div>
            </>
          )}
        </div>
      ) : null}

      {isAnotherMember ? (
        <>
          <div className="tz-workbench-block">
            <header className="tz-workbench-block__header">
              <div>
                <h3>成员登录安全</h3>
                <p>管理员可以为该成员设置新的登录密码。新密码不会回显，也不会影响管理员自己的安全凭据。</p>
              </div>
            </header>

            <div className="tz-form-grid tz-form-grid--security">
              <label className="tz-field">
                <span>新的登录密码</span>
                <Input.Password
                  aria-label="新的登录密码"
                  name="member_new_login_password"
                  autoComplete="new-password"
                  value={passwordDraft}
                  onChange={(event) => setPasswordDraft(event.target.value)}
                />
              </label>
              <div className="tz-security-actions">
                <Button
                  type="primary"
                  onClick={handleResetPassword}
                  disabled={!passwordDraft}
                  loading={passwordSaving}
                >
                  重置该成员登录密码
                </Button>
              </div>
            </div>
          </div>

          <div className="tz-workbench-block tz-workbench-block--danger">
            <PermanentDeletePanel user={selectedUser} onDeleted={onDeleted} />
          </div>
        </>
      ) : null}

      {feedback ? (
        <p
          className={`tz-inline-feedback is-${feedback.tone}`}
          role={feedback.tone === "error" ? "alert" : "status"}
        >
          {feedback.text}
        </p>
      ) : null}
    </section>
  );
}
