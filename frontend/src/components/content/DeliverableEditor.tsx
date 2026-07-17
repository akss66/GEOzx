import { Form, Input, InputNumber } from "antd";
import { useEffect } from "react";

import type { Deliverable } from "../../types";
import { deliverableLabel } from "./contentPresentation";

interface FieldDefinition {
  key: string;
  label: string;
  kind: "text" | "multiline" | "list" | "number";
}

type DeliverableFormValues = Record<string, string | number | undefined>;

const FIELDS: Partial<Record<Deliverable["type"], FieldDefinition[]>> = {
  positioning_strategy: [
    { key: "account_persona", label: "账号人设", kind: "multiline" },
    { key: "target_audience", label: "目标人群", kind: "multiline" },
    { key: "differentiation", label: "差异化方向", kind: "list" },
    { key: "content_pillars", label: "内容支柱", kind: "list" },
  ],
  video_script: [
    { key: "title", label: "脚本标题", kind: "text" },
    { key: "hook", label: "开场钩子", kind: "multiline" },
    { key: "scenes", label: "镜头结构", kind: "list" },
    { key: "duration_seconds", label: "建议时长（秒）", kind: "number" },
    { key: "bgm_suggestion", label: "音乐建议", kind: "text" },
  ],
  art_prompt: [
    { key: "visual_style", label: "视觉风格", kind: "multiline" },
    { key: "prompts", label: "画面提示", kind: "list" },
    { key: "negative_prompt", label: "排除内容", kind: "multiline" },
    { key: "aspect_ratio", label: "画幅", kind: "text" },
  ],
  edited_video: [
    { key: "cut_plan", label: "剪辑结构", kind: "list" },
    { key: "captions", label: "字幕重点", kind: "list" },
    { key: "transitions", label: "转场节奏", kind: "text" },
    { key: "deliverables", label: "成片清单", kind: "list" },
    { key: "platform_variants", label: "平台版本", kind: "list" },
  ],
  review_report: [
    { key: "period", label: "复盘周期", kind: "text" },
    { key: "summary", label: "核心结论", kind: "multiline" },
    { key: "highlights", label: "表现亮点", kind: "list" },
    { key: "issues", label: "主要问题", kind: "list" },
    { key: "optimization_suggestions", label: "优化建议", kind: "list" },
  ],
};

export function DeliverableEditor({
  deliverable,
  saving,
  onSave,
}: {
  deliverable: Deliverable;
  saving: boolean;
  onSave: (payload: Record<string, unknown>, note: string) => void;
}) {
  const [form] = Form.useForm<DeliverableFormValues>();
  const fields = FIELDS[deliverable.type];

  useEffect(() => {
    form.resetFields();
    form.setFieldsValue(toFormValues(deliverable, fields ?? []));
  }, [deliverable, fields, form]);

  if (!fields) {
    return (
      <div className="content-inspector-empty">
        <strong>{deliverableLabel(deliverable.type)}暂不支持文本修订</strong>
        <p>视频素材和未注册成果需要通过对应专家或工具生成新版本，不能直接修改结构数据。</p>
      </div>
    );
  }

  return (
    <Form
      form={form}
      layout="vertical"
      requiredMark={false}
      className="deliverable-editor"
      onFinish={(values) => {
        const payload = { ...deliverable.payload };
        fields.forEach((field) => {
          const value = values[field.key];
          payload[field.key] = field.kind === "list" ? lines(value) : value;
        });
        onSave(payload, String(values.revision_note ?? "").trim());
      }}
    >
      <div className="deliverable-editor__intro">
        <strong>修订 {deliverableLabel(deliverable.type)} · v{deliverable.version}</strong>
        <p>保存后生成新版本，当前版本保留在历史记录中。</p>
      </div>
      {fields.map((field) => (
        <Form.Item
          key={field.key}
          name={field.key}
          label={field.label}
          rules={[{ required: !["bgm_suggestion", "negative_prompt"].includes(field.key), message: `请填写${field.label}` }]}
        >
          {field.kind === "number" ? (
            <InputNumber min={1} max={600} style={{ width: "100%" }} />
          ) : field.kind === "multiline" || field.kind === "list" ? (
            <Input.TextArea
              autoSize={{ minRows: field.kind === "list" ? 4 : 3, maxRows: 10 }}
              placeholder={field.kind === "list" ? "每行一项" : undefined}
            />
          ) : (
            <Input />
          )}
        </Form.Item>
      ))}
      <Form.Item name="revision_note" label="修订说明">
        <Input placeholder="说明这次调整了什么" maxLength={1000} />
      </Form.Item>
      <button type="submit" className="content-primary-action" disabled={saving}>
        {saving ? "正在保存..." : "保存为新版本"}
      </button>
    </Form>
  );
}

function toFormValues(deliverable: Deliverable, fields: FieldDefinition[]) {
  return Object.fromEntries(
    fields.map((field) => {
      const value = deliverable.payload[field.key];
      if (field.kind === "list") {
        return [field.key, Array.isArray(value) ? value.map(String).join("\n") : ""];
      }
      return [field.key, typeof value === "number" ? value : String(value ?? "")];
    }),
  ) as DeliverableFormValues;
}

function lines(value: unknown) {
  return String(value ?? "")
    .split("\n")
    .map((item) => item.trim())
    .filter(Boolean);
}
