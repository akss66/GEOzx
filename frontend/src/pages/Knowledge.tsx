import { Tabs, Tag } from "antd";

import { PageHeader } from "../components/ui";
import { KNOWLEDGE, type KnowledgeItem } from "../mock/data";

const CATS: { key: KnowledgeItem["category"]; label: string }[] = [
  { key: "hot_content", label: "爆款库" },
  { key: "user_persona", label: "用户画像" },
  { key: "prompt_library", label: "提示词库" },
  { key: "script_library", label: "话术库" },
];

function List({ category }: { category: KnowledgeItem["category"] }) {
  const items = KNOWLEDGE.filter((k) => k.category === category);
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "repeat(auto-fill, minmax(320px, 1fr))",
        gap: 12,
      }}
    >
      {items.map((k) => (
        <div
          key={k.id}
          className="dy-rise"
          style={{
            background: "var(--dy-surface)",
            border: "1px solid var(--dy-border-subtle)",
            borderRadius: 12,
            padding: 16,
            cursor: "pointer",
          }}
        >
          <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
            <span style={{ fontSize: 14, fontWeight: 500, color: "var(--dy-text)", lineHeight: 1.45 }}>
              {k.title}
            </span>
            <Tag style={{ marginInlineEnd: 0, flex: "none", height: 22 }}>{k.tag}</Tag>
          </div>
          <div style={{ fontSize: 12, color: "var(--dy-muted)", marginTop: 10 }}>
            {k.metric}
          </div>
        </div>
      ))}
    </div>
  );
}

export default function Knowledge() {
  return (
    <div>
      <PageHeader
        title="共享知识库"
        subtitle="爆款结构 / 用户画像 / 提示词 / 话术 · 全体 Agent 可读可写，沉淀复用"
      />
      <Tabs
        items={CATS.map((c) => ({
          key: c.key,
          label: c.label,
          children: <List category={c.key} />,
        }))}
      />
    </div>
  );
}
