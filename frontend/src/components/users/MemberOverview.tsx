import { Button, Input } from "antd";
import { useEffect, useState } from "react";

import type { Role, UserDetail } from "../../types";

type OverviewDraft = {
  display_name: string;
  email: string;
  role: Role;
};

type OverviewMetrics = Array<{
  label: string;
  value: string | number;
  help?: string;
}>;

export function MemberOverview({
  detail,
  metrics,
  onSave,
  onToggleActive,
}: {
  detail: UserDetail;
  metrics: OverviewMetrics;
  onSave: (draft: OverviewDraft) => Promise<void>;
  onToggleActive: (nextActive: boolean) => Promise<void>;
}) {
  const [draft, setDraft] = useState<OverviewDraft>({
    display_name: detail.display_name,
    email: detail.email,
    role: detail.role,
  });
  const [saving, setSaving] = useState(false);
  const [toggling, setToggling] = useState(false);
  const [feedback, setFeedback] = useState<{ tone: "neutral" | "success" | "error"; text: string } | null>(null);

  useEffect(() => {
    setDraft({
      display_name: detail.display_name,
      email: detail.email,
      role: detail.role,
    });
    setFeedback(null);
  }, [detail.id, detail.display_name, detail.email, detail.role]);

  const dirty = draft.display_name !== detail.display_name
    || draft.email !== detail.email
    || draft.role !== detail.role;

  async function handleSave() {
    setSaving(true);
    setFeedback(null);
    try {
      await onSave(draft);
      setFeedback({ tone: "success", text: "成员资料已保存。" });
    } catch (error) {
      setFeedback({ tone: "error", text: error instanceof Error ? error.message : "成员资料保存失败。" });
    } finally {
      setSaving(false);
    }
  }

  async function handleToggleActive() {
    setToggling(true);
    setFeedback(null);
    try {
      await onToggleActive(!detail.is_active);
      setFeedback({
        tone: "success",
        text: detail.is_active ? "成员已禁用。" : "成员已重新启用。",
      });
    } catch (error) {
      setFeedback({ tone: "error", text: error instanceof Error ? error.message : "成员状态修改失败。" });
    } finally {
      setToggling(false);
    }
  }

  return (
    <section className="tz-member-tab-panel tz-member-overview">
      <div className="tz-inline-metrics" aria-label="成员关键统计">
        {metrics.map((metric) => (
          <div key={metric.label} className="tz-inline-metric">
            <span>{metric.label}</span>
            <strong>{metric.value}</strong>
            {metric.help ? <small>{metric.help}</small> : null}
          </div>
        ))}
      </div>

      <div className="tz-workbench-block">
        <header className="tz-workbench-block__header">
          <div>
            <h3>身份资料</h3>
            <p>编辑显示名称、登录邮箱和系统角色。状态切换是独立动作，不会连带覆盖其他配置。</p>
          </div>
          <div className="tz-workbench-block__actions">
            <Button onClick={handleToggleActive} loading={toggling} danger={detail.is_active}>
              {detail.is_active ? "禁用成员" : "启用成员"}
            </Button>
            <Button type="primary" onClick={handleSave} disabled={!dirty} loading={saving}>
              保存成员资料
            </Button>
          </div>
        </header>

        <div className="tz-form-grid">
          <label className="tz-field">
            <span>显示名称</span>
            <Input
              aria-label="显示名称"
              value={draft.display_name}
              onChange={(event) => setDraft((current) => ({ ...current, display_name: event.target.value }))}
            />
          </label>

          <label className="tz-field">
            <span>登录邮箱</span>
            <Input
              aria-label="登录邮箱"
              type="email"
              value={draft.email}
              onChange={(event) => setDraft((current) => ({ ...current, email: event.target.value }))}
            />
          </label>

          <label className="tz-field">
            <span>系统身份</span>
            <select
              aria-label="系统身份"
              className="tz-native-select"
              value={draft.role}
              onChange={(event) => setDraft((current) => ({ ...current, role: event.target.value as Role }))}
            >
              <option value="user">成员</option>
              <option value="admin">管理员</option>
            </select>
          </label>
        </div>

        {feedback ? (
          <p
            className={`tz-inline-feedback is-${feedback.tone}`}
            role={feedback.tone === "error" ? "alert" : "status"}
          >
            {feedback.text}
          </p>
        ) : null}
      </div>
    </section>
  );
}
