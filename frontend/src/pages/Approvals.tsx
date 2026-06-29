import { CheckOutlined, CloseOutlined, WarningFilled } from "@ant-design/icons";
import { App as AntApp, Button, Empty, Tag } from "antd";
import { useState } from "react";

import { PageHeader } from "../components/ui";
import { PENDING_GATES, type Gate } from "../mock/data";

export default function Approvals() {
  const { message } = AntApp.useApp();
  const [gates, setGates] = useState<Gate[]>(PENDING_GATES);

  const decide = (id: number, ok: boolean) => {
    setGates((prev) => prev.filter((g) => g.id !== id));
    message.success(ok ? "已通过，链路继续流转" : "已打回，已通知对应 Agent");
  };

  return (
    <div>
      <PageHeader
        title="质量门审批"
        subtitle="人在关键处把关 · 脚本合规 / 发布前 / 大额投放 强制人工"
        extra={
          <Tag color="warning" style={{ marginInlineEnd: 0 }}>
            待处理 {gates.length}
          </Tag>
        }
      />

      {gates.length === 0 ? (
        <Empty description="全部处理完毕 · 链路畅通" style={{ marginTop: 80 }} />
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 12, maxWidth: 880 }}>
          {gates.map((g) => (
            <div
              key={g.id}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 16,
                padding: "16px 18px",
                background: "var(--dy-surface)",
                border: "1px solid",
                borderColor: g.forced ? "rgba(214,161,38,0.3)" : "var(--dy-border-subtle)",
                borderRadius: 12,
              }}
            >
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
                  <Tag
                    color={g.forced ? "warning" : "default"}
                    icon={g.forced ? <WarningFilled /> : undefined}
                    style={{ marginInlineEnd: 0 }}
                  >
                    {g.gate}
                  </Tag>
                  {g.forced && (
                    <span style={{ fontSize: 12, color: "var(--dy-warning)" }}>强制人工</span>
                  )}
                  <span style={{ fontSize: 12, color: "var(--dy-faint)", marginLeft: "auto" }}>
                    等待 {g.waiting}
                  </span>
                </div>
                <div style={{ fontSize: 14, fontWeight: 500, color: "var(--dy-text)" }}>
                  {g.title}
                </div>
                <div style={{ fontSize: 12.5, color: "var(--dy-muted)", marginTop: 4 }}>
                  @{g.account} · 内容 #{g.contentId}
                  {g.risk && (
                    <span style={{ color: "var(--dy-warning)", marginLeft: 8 }}>
                      ⚠ {g.risk}
                    </span>
                  )}
                </div>
              </div>
              <div style={{ display: "flex", gap: 8, flex: "none" }}>
                <Button
                  danger
                  icon={<CloseOutlined />}
                  onClick={() => decide(g.id, false)}
                >
                  打回
                </Button>
                <Button
                  type="primary"
                  icon={<CheckOutlined />}
                  onClick={() => decide(g.id, true)}
                >
                  通过
                </Button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
