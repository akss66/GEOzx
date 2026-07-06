import { DeleteOutlined, EditOutlined, PlusOutlined } from "@ant-design/icons";
import {
  App as AntApp,
  Button,
  Empty,
  Form,
  Input,
  Modal,
  Popconfirm,
  Select,
  Spin,
  Tabs,
  Tag,
} from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import {
  createKnowledge,
  deleteKnowledge,
  listKnowledge,
  updateKnowledge,
} from "../api/knowledge";
import { PageHeader } from "../components/ui";
import type { KnowledgeCategory, KnowledgeEntry } from "../types";

const CATS: { key: KnowledgeCategory; label: string }[] = [
  { key: "hot_content", label: "爆款库" },
  { key: "user_persona", label: "用户画像" },
  { key: "prompt_library", label: "提示词库" },
  { key: "script_library", label: "话术库" },
];

interface FormValues {
  category: KnowledgeCategory;
  title: string;
  tags?: string;
  note?: string;
}

export default function Knowledge() {
  const { message } = AntApp.useApp();
  const qc = useQueryClient();
  const [active, setActive] = useState<KnowledgeCategory>("hot_content");
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<KnowledgeEntry | null>(null);
  const [form] = Form.useForm<FormValues>();

  const query = useQuery({
    queryKey: ["knowledge", active],
    queryFn: () => listKnowledge(active),
  });

  const invalidate = () => qc.invalidateQueries({ queryKey: ["knowledge"] });

  const saveMutation = useMutation({
    mutationFn: (v: FormValues) => {
      const tags = v.tags
        ? v.tags.split(/[,，\s]+/).map((t) => t.trim()).filter(Boolean)
        : null;
      const payload = v.note ? { note: v.note } : {};
      if (editing) {
        return updateKnowledge(editing.id, { title: v.title, tags, payload });
      }
      return createKnowledge({ category: v.category, title: v.title, tags, payload });
    },
    onSuccess: () => {
      message.success(editing ? "已更新" : "已添加到知识库");
      setModalOpen(false);
      setEditing(null);
      form.resetFields();
      invalidate();
    },
    onError: () => message.error("保存失败，请重试"),
  });

  const deleteMutation = useMutation({
    mutationFn: deleteKnowledge,
    onSuccess: () => {
      message.success("已删除");
      invalidate();
    },
    onError: () => message.error("删除失败"),
  });

  const openCreate = () => {
    setEditing(null);
    form.resetFields();
    form.setFieldsValue({ category: active });
    setModalOpen(true);
  };

  const openEdit = (k: KnowledgeEntry) => {
    setEditing(k);
    form.setFieldsValue({
      category: k.category,
      title: k.title,
      tags: (k.tags ?? []).join(", "),
      note: typeof k.payload?.note === "string" ? k.payload.note : "",
    });
    setModalOpen(true);
  };

  const renderList = () => {
    if (query.isLoading) {
      return (
        <div style={{ display: "grid", placeItems: "center", padding: 60 }}>
          <Spin />
        </div>
      );
    }
    const items = query.data ?? [];
    if (items.length === 0) {
      return <Empty description="该分类暂无条目" style={{ marginTop: 60 }} />;
    }
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
            }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
              <span
                style={{
                  fontSize: 14,
                  fontWeight: 500,
                  color: "var(--dy-text)",
                  lineHeight: 1.45,
                }}
              >
                {k.title}
              </span>
              <div style={{ display: "flex", gap: 4, flex: "none" }}>
                <Button
                  type="text"
                  size="small"
                  icon={<EditOutlined />}
                  onClick={() => openEdit(k)}
                />
                <Popconfirm
                  title="删除该条目？"
                  onConfirm={() => deleteMutation.mutate(k.id)}
                  okText="删除"
                  cancelText="取消"
                >
                  <Button type="text" size="small" danger icon={<DeleteOutlined />} />
                </Popconfirm>
              </div>
            </div>
            {typeof k.payload?.note === "string" && k.payload.note && (
              <div style={{ fontSize: 12.5, color: "var(--dy-muted)", marginTop: 8 }}>
                {k.payload.note}
              </div>
            )}
            {k.tags && k.tags.length > 0 && (
              <div style={{ marginTop: 10, display: "flex", gap: 6, flexWrap: "wrap" }}>
                {k.tags.map((t) => (
                  <Tag key={t} style={{ marginInlineEnd: 0 }}>
                    {t}
                  </Tag>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>
    );
  };

  return (
    <div>
      <PageHeader
        title="共享知识库"
        subtitle="爆款结构 / 用户画像 / 提示词 / 话术 · 全体 Agent 可读可写，沉淀复用"
        extra={
          <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
            新增条目
          </Button>
        }
      />
      <Tabs
        activeKey={active}
        onChange={(k) => setActive(k as KnowledgeCategory)}
        items={CATS.map((c) => ({ key: c.key, label: c.label, children: renderList() }))}
      />

      <Modal
        title={editing ? "编辑知识条目" : "新增知识条目"}
        open={modalOpen}
        onCancel={() => {
          setModalOpen(false);
          setEditing(null);
        }}
        onOk={() => form.submit()}
        confirmLoading={saveMutation.isPending}
        okText={editing ? "保存" : "添加"}
        destroyOnHidden
      >
        <Form
          form={form}
          layout="vertical"
          requiredMark={false}
          onFinish={(v) => saveMutation.mutate(v)}
        >
          <Form.Item
            name="category"
            label="分类"
            rules={[{ required: true, message: "请选择分类" }]}
          >
            <Select
              disabled={!!editing}
              options={CATS.map((c) => ({ label: c.label, value: c.key }))}
            />
          </Form.Item>
          <Form.Item
            name="title"
            label="标题"
            rules={[{ required: true, message: "请输入标题" }]}
          >
            <Input placeholder="例如：对比实测类爆款结构：钩子-冲突-反转-结论" maxLength={300} />
          </Form.Item>
          <Form.Item name="note" label="内容 / 说明">
            <Input.TextArea rows={4} placeholder="该条目的具体内容，供 Agent 参考复用" />
          </Form.Item>
          <Form.Item name="tags" label="标签" extra="多个标签用逗号或空格分隔">
            <Input placeholder="数码, 测评" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
