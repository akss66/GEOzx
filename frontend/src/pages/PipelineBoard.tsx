import { PlusOutlined, WarningFilled } from "@ant-design/icons";
import { Button, Segmented, Tag, Typography } from "antd";
import { useState } from "react";

import { PageHeader, PlatformTag, StatusBadge } from "../components/ui";
import {
  CONTENT_CARDS,
  STAGES,
  type ContentCard,
  type Platform,
} from "../mock/data";

function Card({ card }: { card: ContentCard }) {
  return (
    <article
      className="dy-rise"
      style={{
        background: "var(--dy-elevated)",
        border: "1px solid var(--dy-border-subtle)",
        borderRadius: 10,
        padding: 12,
        cursor: "pointer",
      }}
    >
      <div
        style={{
          fontSize: 13,
          fontWeight: 500,
          color: "var(--dy-text)",
          lineHeight: 1.45,
          marginBottom: 10,
          display: "-webkit-box",
          WebkitLineClamp: 2,
          WebkitBoxOrient: "vertical",
          overflow: "hidden",
        }}
      >
        {card.title}
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 10 }}>
        <PlatformTag platform={card.platform} />
        <Typography.Text style={{ fontSize: 12, color: "var(--dy-muted)" }}>
          @{card.account}
        </Typography.Text>
      </div>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
        }}
      >
        <StatusBadge status={card.status} />
        <span className="dy-tabular" style={{ fontSize: 11.5, color: "var(--dy-faint)" }}>
          v{card.version} · ${card.cost.toFixed(2)}
        </span>
      </div>
      {card.gate && (
        <div style={{ marginTop: 10 }}>
          <Tag
            color={card.status === "blocked" ? "warning" : "default"}
            icon={card.status === "blocked" ? <WarningFilled /> : undefined}
            style={{ marginInlineEnd: 0, fontSize: 11 }}
          >
            {card.gate}
          </Tag>
        </div>
      )}
    </article>
  );
}

export default function PipelineBoard() {
  const [platform, setPlatform] = useState<Platform | "all">("all");

  const filtered =
    platform === "all"
      ? CONTENT_CARDS
      : CONTENT_CARDS.filter((c) => c.platform === platform);

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <PageHeader
        title="内容流水线"
        subtitle="八个 Agent 协同 · 上游产出自动触发下游 · 质量门把关"
        extra={
          <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
            <Segmented
              value={platform}
              onChange={(v) => setPlatform(v as Platform | "all")}
              options={[
                { label: "全部平台", value: "all" },
                { label: "抖音", value: "douyin" },
                { label: "小红书", value: "xiaohongshu" },
                { label: "视频号", value: "shipinhao" },
              ]}
            />
            <Button type="primary" icon={<PlusOutlined />}>
              新建内容
            </Button>
          </div>
        }
      />

      <div
        style={{
          display: "flex",
          gap: 14,
          overflowX: "auto",
          paddingBottom: 8,
          flex: 1,
          alignItems: "flex-start",
        }}
      >
        {STAGES.map((stage) => {
          const cards = filtered.filter((c) => c.stage === stage.key);
          return (
            <section
              key={stage.key}
              style={{
                width: 276,
                flex: "none",
                background: "var(--dy-surface)",
                border: "1px solid var(--dy-border-subtle)",
                borderRadius: 12,
                padding: 12,
                maxHeight: "100%",
                display: "flex",
                flexDirection: "column",
              }}
            >
              <header
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 8,
                  marginBottom: 12,
                  paddingInline: 2,
                }}
              >
                <span
                  className="dy-tabular"
                  style={{
                    fontSize: 11,
                    fontWeight: 700,
                    color: "var(--dy-accent)",
                    background: "var(--dy-accent-wash)",
                    borderRadius: 5,
                    padding: "2px 6px",
                  }}
                >
                  {stage.index}
                </span>
                <span style={{ fontSize: 13.5, fontWeight: 600, color: "var(--dy-text)" }}>
                  {stage.name}
                </span>
                <span
                  className="dy-tabular"
                  style={{ marginLeft: "auto", fontSize: 12, color: "var(--dy-faint)" }}
                >
                  {cards.length}
                </span>
              </header>
              <div
                style={{
                  display: "flex",
                  flexDirection: "column",
                  gap: 10,
                  overflowY: "auto",
                }}
              >
                {cards.length === 0 ? (
                  <div
                    style={{
                      fontSize: 12,
                      color: "var(--dy-faint)",
                      textAlign: "center",
                      padding: "20px 0",
                    }}
                  >
                    暂无在产内容
                  </div>
                ) : (
                  cards.map((c) => <Card key={c.id} card={c} />)
                )}
              </div>
            </section>
          );
        })}
      </div>
    </div>
  );
}
