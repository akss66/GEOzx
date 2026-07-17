import {
  CheckCircleFilled,
  EditOutlined,
  PlayCircleOutlined,
} from "@ant-design/icons";
import { Button, Segmented, Skeleton, Tag } from "antd";
import { useEffect, useMemo, useState } from "react";

import type { ContentWorkspace, Deliverable } from "../../types";
import {
  CONTENT_STAGES,
  displayContentTitle,
  deliverableLabel,
  deliverableSections,
  deliverableStatusLabel,
  latestDeliverables,
  stageLabel,
  statusLabel,
} from "./contentPresentation";

export type ContentInspectorMode =
  | "materials"
  | "versions"
  | "stages"
  | "agents"
  | "approvals"
  | "publish";

const INSPECTOR_OPTIONS: { label: string; value: ContentInspectorMode }[] = [
  { label: "素材", value: "materials" },
  { label: "版本", value: "versions" },
  { label: "阶段", value: "stages" },
  { label: "Agent", value: "agents" },
  { label: "审批", value: "approvals" },
  { label: "发布准备", value: "publish" },
];

export function ContentCanvas({
  workspace,
  loading,
  starting,
  canOperate,
  inspectorMode,
  onStart,
  onOpenInspector,
  onEdit,
}: {
  workspace: ContentWorkspace | null;
  loading: boolean;
  starting: boolean;
  canOperate: boolean;
  inspectorMode: ContentInspectorMode | null;
  onStart: () => void;
  onOpenInspector: (mode: ContentInspectorMode) => void;
  onEdit: (deliverable: Deliverable) => void;
}) {
  const current = useMemo(
    () => latestDeliverables(workspace?.deliverables ?? []),
    [workspace?.deliverables],
  );
  const [selectedType, setSelectedType] = useState<string | null>(null);

  useEffect(() => {
    setSelectedType(current.at(-1)?.type ?? null);
  }, [workspace?.content_item.id, current]);

  if (loading) {
    return (
      <main className="content-canvas content-canvas--loading">
        <Skeleton active paragraph={{ rows: 10 }} />
      </main>
    );
  }
  if (!workspace) {
    return (
      <main className="content-canvas content-canvas--empty">
        <span className="content-canvas__monogram">稿</span>
        <strong>选择一条内容进入工作画布</strong>
        <p>这里会集中显示脚本、视觉方案、素材、成片和发布包，不再把工作拆成六列卡片。</p>
      </main>
    );
  }

  const deliverable = current.find((item) => item.type === selectedType) ?? current.at(-1) ?? null;
  const currentStageIndex = CONTENT_STAGES.findIndex(
    (item) => item.key === workspace.content_item.current_stage,
  );

  return (
    <main className="content-canvas">
      <header className="content-canvas__header">
        <div className="content-canvas__eyebrow">
          <span>{workspace.project_name}</span>
          <i />
          <span>{workspace.account?.nickname ?? "未绑定账号"}</span>
          <Tag bordered={false}>{statusLabel(workspace.content_item.status)}</Tag>
        </div>
        <h1>{displayContentTitle(workspace.content_item.title)}</h1>
        <p>当前阶段：{stageLabel(workspace.content_item.current_stage)}</p>
      </header>

      <div className="content-stage-line" aria-label="生产阶段">
        {CONTENT_STAGES.map((stage, index) => (
          <div
            key={stage.key}
            className={`content-stage-line__step${index <= currentStageIndex ? " is-reached" : ""}${index === currentStageIndex ? " is-current" : ""}`}
          >
            <span>{index < currentStageIndex ? <CheckCircleFilled /> : index + 1}</span>
            <small>{stage.short}</small>
          </div>
        ))}
      </div>

      <div className="content-canvas__tools">
        <Segmented
          value={inspectorMode ?? undefined}
          options={INSPECTOR_OPTIONS}
          onChange={(value) => onOpenInspector(value as ContentInspectorMode)}
        />
      </div>

      {current.length === 0 ? (
        <section className="content-document-empty">
          <span className="content-document-empty__index">01</span>
          <h2>这条内容还没有正式成果</h2>
          <p>
            启动后，专家输出会以可读、可编辑、可版本化的文档出现在这里。流程遇到质量门时会进入人工审批。
          </p>
          <Button
            type="primary"
            icon={<PlayCircleOutlined />}
            loading={starting}
            disabled={!canOperate}
            onClick={onStart}
          >
            启动 Agent 生产
          </Button>
        </section>
      ) : (
        <article className="content-document">
          <nav className="content-document__tabs" aria-label="正式成果">
            {current.map((item) => (
              <button
                type="button"
                key={item.id}
                className={deliverable?.id === item.id ? "is-active" : ""}
                onClick={() => setSelectedType(item.type)}
              >
                {deliverableLabel(item.type)}
                <small>v{item.version}</small>
              </button>
            ))}
          </nav>

          {deliverable ? (
            <div className="content-document__body">
              <header>
                <div>
                  <span>{deliverableLabel(deliverable.type)}</span>
                  <Tag bordered={false}>{deliverableStatusLabel(deliverable.status)}</Tag>
                </div>
                <Button
                  icon={<EditOutlined />}
                  disabled={!canOperate}
                  onClick={() => onEdit(deliverable)}
                >
                  修订
                </Button>
              </header>
              {deliverableSections(deliverable).map((section) => (
                <section key={section.label} className="content-document-section">
                  <h2>{section.label}</h2>
                  {section.value ? <p>{section.value}</p> : null}
                  {section.items ? (
                    <ol>
                      {section.items.map((item, index) => <li key={`${index}-${item}`}>{item}</li>)}
                    </ol>
                  ) : null}
                  {section.metrics ? (
                    <dl>
                      {section.metrics.map((metric) => (
                        <div key={metric.label}><dt>{metric.label}</dt><dd>{metric.value}</dd></div>
                      ))}
                    </dl>
                  ) : null}
                </section>
              ))}
            </div>
          ) : null}
        </article>
      )}
    </main>
  );
}
