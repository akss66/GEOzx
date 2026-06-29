import { PlusOutlined, WifiOutlined } from "@ant-design/icons";
import {
  App as AntApp,
  Button,
  Empty,
  Form,
  Input,
  Modal,
  Select,
  Tag,
  Typography,
} from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import {
  createContentItem,
  listContentItems,
  startPipeline,
} from "../api/orchestrator";
import { listProjects } from "../api/workspace";
import { PageHeader, StatusBadge } from "../components/ui";
import { useEventStream } from "../hooks/useEventStream";
import type { CardStatus } from "../mock/data";
import type { ContentItem, ContentStage, ContentStatus } from "../types";
import { DeliverableDrawer } from "./DeliverableDrawer";

interface StageDef {
  key: ContentStage;
  index: string;
  name: string;
}

// 主链路六阶段（与后端 PIPELINE 一致）。投流/客服是并行/独立模块，不在主链路看板。
const STAGES: StageDef[] = [
  { key: "positioning", index: "01", name: "账号定位" },
  { key: "content_direction", index: "02", name: "编导文案" },
  { key: "art_direction", index: "03", name: "美术提示词" },
  { key: "video_creation", index: "04", name: "视频创作" },
  { key: "editing", index: "05", name: "剪辑" },
  { key: "operation", index: "06", name: "运营分发" },
];

// 内容状态 → 状态标记（色盲安全的颜色 + 图标 + 文字）。
const STATUS_BADGE: Record<ContentStatus, CardStatus> = {
  draft: "review",
  in_progress: "running",
  blocked: "blocked",
  published: "done",
  archived: "done",
};

function Card({ item, onClick }: { item: ContentItem; onClick: () => void }) {
  return (
    <article
      className="dy-rise"
      onClick={onClick}
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
        {item.title}
      </div>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <StatusBadge status={STATUS_BADGE[item.status]} />
        <span className="dy-tabular" style={{ fontSize: 11.5, color: "var(--dy-faint)" }}>
          #{item.id}
        </span>
      </div>
    </article>
  );
}

export default function PipelineBoard() {
  const { message } = AntApp.useApp();
  const qc = useQueryClient();
  const [projectId, setProjectId] = useState<number | undefined>(undefined);
  const [modalOpen, setModalOpen] = useState(false);
  const [selected, setSelected] = useState<ContentItem | null>(null);
  const [form] = Form.useForm<{ title: string }>();

  const projectsQuery = useQuery({ queryKey: ["projects"], queryFn: listProjects });

  // 默认选中第一个项目
  const projects = projectsQuery.data ?? [];
  const activeProject = projectId ?? projects[0]?.id;

  const itemsQuery = useQuery({
    queryKey: ["content-items", activeProject],
    queryFn: () => listContentItems(activeProject),
    enabled: activeProject != null,
  });

  // WebSocket：编排事件到达即刷新看板（实时无需手动刷新）。
  const { connected } = useEventStream(() => {
    qc.invalidateQueries({ queryKey: ["content-items"] });
    qc.invalidateQueries({ queryKey: ["gates"] });
  });

  const createMutation = useMutation({
    mutationFn: async (title: string) => {
      const ci = await createContentItem({ project_id: activeProject!, title });
      await startPipeline(ci.id);
    },
    onSuccess: () => {
      message.success("内容已创建并启动流水线");
      setModalOpen(false);
      form.resetFields();
      qc.invalidateQueries({ queryKey: ["content-items"] });
    },
    onError: () => message.error("创建失败，请重试"),
  });

  const items = itemsQuery.data ?? [];

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <PageHeader
        title="内容流水线"
        subtitle="八个 Agent 协同 · 上游产出自动触发下游 · 质量门把关"
        extra={
          <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
            <Tag
              icon={<WifiOutlined />}
              color={connected ? "success" : "default"}
              style={{ marginInlineEnd: 0 }}
            >
              {connected ? "实时已连接" : "实时未连接"}
            </Tag>
            <Select
              style={{ minWidth: 200 }}
              placeholder="选择项目"
              value={activeProject}
              onChange={setProjectId}
              loading={projectsQuery.isLoading}
              options={projects.map((p) => ({ label: p.name, value: p.id }))}
            />
            <Button
              type="primary"
              icon={<PlusOutlined />}
              disabled={activeProject == null}
              onClick={() => setModalOpen(true)}
            >
              新建内容
            </Button>
          </div>
        }
      />

      {activeProject == null ? (
        <Empty
          description={projectsQuery.isLoading ? "加载中…" : "暂无项目，请先在项目中创建"}
          style={{ marginTop: 80 }}
        />
      ) : (
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
            const cards = items.filter((c) => c.current_stage === stage.key);
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
                    cards.map((c) => (
                      <Card key={c.id} item={c} onClick={() => setSelected(c)} />
                    ))
                  )}
                </div>
              </section>
            );
          })}
        </div>
      )}

      <Modal
        title="新建内容并启动流水线"
        open={modalOpen}
        onCancel={() => setModalOpen(false)}
        onOk={() => form.submit()}
        confirmLoading={createMutation.isPending}
        okText="创建并启动"
        destroyOnClose
      >
        <Typography.Paragraph type="secondary" style={{ fontSize: 13 }}>
          创建后将自动触发编排：定位 → 编导，随后在强制质量门处等待人工审批。
        </Typography.Paragraph>
        <Form
          form={form}
          layout="vertical"
          requiredMark={false}
          onFinish={(v) => createMutation.mutate(v.title)}
        >
          <Form.Item
            name="title"
            label="内容标题"
            rules={[{ required: true, message: "请输入内容标题" }]}
          >
            <Input placeholder="例如：618 新品开箱：三分钟看懂值不值" maxLength={300} />
          </Form.Item>
        </Form>
      </Modal>

      <DeliverableDrawer item={selected} onClose={() => setSelected(null)} />
    </div>
  );
}
