import { HistoryOutlined, ReloadOutlined, RollbackOutlined } from "@ant-design/icons";
import { App as AntApp, Button, Drawer, Empty, Spin, Tag } from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  listDeliverableHistory,
  rerunStage,
  rollbackDeliverable,
} from "../api/orchestrator";
import type {
  ContentItem,
  ContentStage,
  Deliverable,
  DeliverableStatus,
  DeliverableType,
} from "../types";

const TYPE_LABEL: Record<DeliverableType, string> = {
  positioning_strategy: "定位策略",
  topic_plan: "选题方案",
  publish_calendar: "发布日历",
  video_script: "视频脚本",
  art_prompt: "美术提示词",
  video_asset: "视频素材",
  edited_video: "成片",
  review_report: "复盘报告",
  ad_plan: "投放计划",
  cs_record: "客服记录",
};

// 交付物 type → 产出它的阶段（用于"重跑该阶段"）。
const TYPE_TO_STAGE: Partial<Record<DeliverableType, ContentStage>> = {
  positioning_strategy: "positioning",
  video_script: "content_direction",
  art_prompt: "art_direction",
  video_asset: "video_creation",
  edited_video: "editing",
  review_report: "operation",
};

const STATUS_TAG: Record<DeliverableStatus, { color: string; label: string }> = {
  draft: { color: "blue", label: "草稿" },
  pending_review: { color: "gold", label: "待审" },
  approved: { color: "green", label: "生效" },
  rejected: { color: "red", label: "驳回" },
  superseded: { color: "default", label: "已被取代" },
};

export function DeliverableDrawer({
  item,
  onClose,
}: {
  item: ContentItem | null;
  onClose: () => void;
}) {
  const { message } = AntApp.useApp();
  const qc = useQueryClient();

  const historyQuery = useQuery({
    queryKey: ["deliverable-history", item?.id],
    queryFn: () => listDeliverableHistory(item!.id),
    enabled: item != null,
  });

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["deliverable-history", item?.id] });
    qc.invalidateQueries({ queryKey: ["content-items"] });
  };

  const rollbackMutation = useMutation({
    mutationFn: rollbackDeliverable,
    onSuccess: () => {
      message.success("已回滚到该版本");
      invalidate();
    },
    onError: () => message.error("回滚失败"),
  });

  const rerunMutation = useMutation({
    mutationFn: (stage: ContentStage) => rerunStage(item!.id, stage),
    onSuccess: () => {
      message.success("已重跑，生成新版本");
      invalidate();
    },
    onError: () => message.error("重跑失败"),
  });

  // 按 type 分组
  const grouped = (historyQuery.data ?? []).reduce<Record<string, Deliverable[]>>((acc, d) => {
    (acc[d.type] ??= []).push(d);
    return acc;
  }, {});

  return (
    <Drawer
      title={item ? `交付物历史 · #${item.id}` : "交付物历史"}
      open={item != null}
      onClose={onClose}
      width={520}
    >
      {item && (
        <div style={{ fontSize: 13, color: "var(--dy-muted)", marginBottom: 16 }}>{item.title}</div>
      )}
      {historyQuery.isLoading ? (
        <div style={{ display: "grid", placeItems: "center", padding: 40 }}>
          <Spin />
        </div>
      ) : Object.keys(grouped).length === 0 ? (
        <Empty description="暂无交付物" />
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
          {Object.entries(grouped).map(([type, versions]) => {
            const stage = TYPE_TO_STAGE[type as DeliverableType];
            return (
              <section key={type}>
                <header
                  style={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                    marginBottom: 8,
                  }}
                >
                  <span style={{ fontSize: 13.5, fontWeight: 600, color: "var(--dy-text)" }}>
                    {TYPE_LABEL[type as DeliverableType] ?? type}
                  </span>
                  {stage && (
                    <Button
                      size="small"
                      icon={<ReloadOutlined />}
                      loading={rerunMutation.isPending}
                      onClick={() => rerunMutation.mutate(stage)}
                    >
                      重跑
                    </Button>
                  )}
                </header>
                <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                  {versions.map((d) => (
                    <div
                      key={d.id}
                      style={{
                        border: "1px solid var(--dy-border-subtle)",
                        borderRadius: 10,
                        padding: 12,
                        background:
                          d.status === "superseded" ? "transparent" : "var(--dy-surface)",
                        opacity: d.status === "superseded" ? 0.7 : 1,
                      }}
                    >
                      <div
                        style={{
                          display: "flex",
                          alignItems: "center",
                          gap: 8,
                          marginBottom: 8,
                        }}
                      >
                        <HistoryOutlined style={{ color: "var(--dy-faint)" }} />
                        <span className="dy-tabular" style={{ fontWeight: 500 }}>
                          v{d.version}
                        </span>
                        <Tag color={STATUS_TAG[d.status].color} style={{ marginInlineEnd: 0 }}>
                          {STATUS_TAG[d.status].label}
                        </Tag>
                        <span
                          className="dy-tabular"
                          style={{ fontSize: 11.5, color: "var(--dy-faint)", marginLeft: "auto" }}
                        >
                          {d.agent_code}
                        </span>
                        {d.status === "superseded" && (
                          <Button
                            size="small"
                            type="text"
                            icon={<RollbackOutlined />}
                            loading={rollbackMutation.isPending}
                            onClick={() => rollbackMutation.mutate(d.id)}
                          >
                            回滚
                          </Button>
                        )}
                      </div>
                      <pre
                        style={{
                          margin: 0,
                          fontSize: 11.5,
                          lineHeight: 1.5,
                          color: "var(--dy-muted)",
                          whiteSpace: "pre-wrap",
                          wordBreak: "break-word",
                          maxHeight: 160,
                          overflow: "auto",
                        }}
                      >
                        {JSON.stringify(d.payload, null, 2)}
                      </pre>
                    </div>
                  ))}
                </div>
              </section>
            );
          })}
        </div>
      )}
    </Drawer>
  );
}
