import { Tag } from "antd";
import type { ReactNode } from "react";

import { silverTagStyle } from "../theme/styles";
import { PageHeader } from "./ui";

/** 即将上线模块的占位页：说明定位、所属阶段、规划能力。 */
export function ModulePlaceholder({
  title,
  subtitle,
  phase,
  features,
  icon,
}: {
  title: string;
  subtitle: string;
  phase: string;
  features: string[];
  icon?: ReactNode;
}) {
  return (
    <div>
      <PageHeader
        title={title}
        subtitle={subtitle}
        extra={
          <Tag style={{ marginInlineEnd: 0, ...silverTagStyle }}>
            {phase}
          </Tag>
        }
      />
      <div
        style={{
          background: "var(--dy-surface)",
          border: "1px solid var(--dy-border-subtle)",
          borderRadius: 24,
          padding: "40px 32px",
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          gap: 20,
          textAlign: "center",
        }}
      >
        <div style={{ fontSize: 40, color: "var(--dy-faint)" }}>{icon}</div>
        <div style={{ fontSize: 14, color: "var(--dy-muted)", maxWidth: 520, lineHeight: 1.6 }}>
          该模块为独立运营模块，不属于内容生产主链路，将在 {phase} 阶段实现。规划能力：
        </div>
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))",
            gap: 12,
            width: "100%",
            maxWidth: 720,
          }}
        >
          {features.map((f) => (
            <div
              key={f}
              style={{
                background: "var(--dy-elevated)",
                border: "1px solid var(--dy-border-subtle)",
                borderRadius: 18,
                padding: "14px 16px",
                fontSize: 13,
                color: "var(--dy-text)",
                textAlign: "left",
              }}
            >
              {f}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
