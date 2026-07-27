import { CheckCircleFilled, ExclamationCircleFilled } from "@ant-design/icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { App as AntApp, Button, Checkbox, Form, Input, Select, Switch } from "antd";
import { useEffect, useMemo, useState } from "react";

import { checkPublishReadiness } from "../../api/orchestrator";
import {
  createPublishJob,
  listPublishJobs,
} from "../../services/publishing";
import { useCurrentWorkspace } from "../../stores/currentWorkspace";
import type { ContentWorkspace, PublishReadiness } from "../../types";
import type { PublishJob } from "../../types/publishing";
import { PublishJobPanel } from "./PublishJobPanel";

interface PublishForm {
  title: string;
  body: string;
  topics: string;
  scheduled_at: string;
  material_ids: number[];
  cover_material_id: number | null;
  visibility: "public" | "friends" | "private";
  allow_comment: boolean;
}

export function PublishPreparation({
  workspace,
  canOperate,
}: {
  workspace: ContentWorkspace;
  canOperate: boolean;
}) {
  if (!workspace.account || !canOperate) {
    return (
      <div className="content-inspector-empty">
        <strong>{workspace.account ? "当前账号与内容账号不一致" : "这条内容尚未绑定账号"}</strong>
        <p>
          {workspace.account
            ? `请先从顶部明确选择“${workspace.account.nickname}”，再进入发布准备。`
            : "发布包必须绑定明确的平台账号。请返回账号矩阵或新建内容时选择当前账号。"}
        </p>
      </div>
    );
  }
  return <PublishPreparationForm workspace={workspace} account={workspace.account} />;
}

function PublishPreparationForm({
  workspace,
  account,
}: {
  workspace: ContentWorkspace;
  account: NonNullable<ContentWorkspace["account"]>;
}) {
  const { message } = AntApp.useApp();
  const qc = useQueryClient();
  const [form] = Form.useForm<PublishForm>();
  const [result, setResult] = useState<PublishReadiness | null>(null);
  const clientId = useCurrentWorkspace((state) => state.clientId);
  const projectId = useCurrentWorkspace((state) => state.projectId);
  const readyMaterials = useMemo(
    () => workspace.materials.filter((material) => material.status === "ready"),
    [workspace.materials],
  );
  const publishJobsKey = ["publish-jobs", account.id] as const;
  const jobsQuery = useQuery({
    queryKey: publishJobsKey,
    queryFn: () => listPublishJobs({ accountId: account.id, limit: 50 }),
    refetchInterval: (query) =>
      shouldPollJobs(query.state.data as PublishJob[] | undefined) ? 5000 : false,
  });
  const activeJob = useMemo(
    () => latestJobForContent(
      jobsQuery.data ?? [],
      workspace.content_item.id,
      workspace.publish_tool_calls.map((toolCall) => toolCall.id),
    ),
    [jobsQuery.data, workspace.content_item.id, workspace.publish_tool_calls],
  );

  useEffect(() => {
    form.setFieldsValue({
      title: workspace.content_item.title,
      body: "",
      topics: "",
      scheduled_at: "",
      material_ids: [],
      cover_material_id: null,
      visibility: "public",
      allow_comment: true,
    });
    setResult(null);
  }, [form, workspace.content_item.id, workspace.content_item.title]);

  const createJobMutation = useMutation({
    mutationFn: (next: PublishReadiness) =>
      createPublishJob({
        account_id: account.id,
        active_client_id: clientId,
        active_project_id: projectId,
        tool_call_id: next.tool_call.id,
        idempotency_key: publishJobKey(workspace.content_item.id, next.tool_call.id),
        publish_package: next.package,
      }),
    onSuccess: (job) => {
      qc.setQueryData<PublishJob[]>(publishJobsKey, (current = []) =>
        [job, ...current.filter((item) => item.id !== job.id)],
      );
    },
  });

  const mutation = useMutation({
    mutationFn: (values: PublishForm) =>
      checkPublishReadiness(workspace.content_item.id, {
        platform: account.platform,
        title: values.title.trim(),
        body: values.body.trim(),
        topics: lines(values.topics),
        scheduled_at: values.scheduled_at
          ? new Date(values.scheduled_at).toISOString()
          : null,
        material_ids: values.material_ids,
        cover_material_id: values.cover_material_id,
        visibility: values.visibility,
        allow_comment: values.allow_comment,
      }),
    onSuccess: async (next) => {
      setResult(next);
      qc.invalidateQueries({ queryKey: ["content-workspace", workspace.content_item.id] });
      qc.invalidateQueries({ queryKey: ["pending-tool-call-approvals"] });
      if (!next.ready) {
        message.warning("发布准备存在阻断项，请调整后重试");
        return;
      }
      try {
        await createJobMutation.mutateAsync(next);
        message.success("发布任务已创建，等待人工确认");
      } catch {
        message.error("发布包已生成，但发布任务创建失败，请重试");
      }
    },
    onError: () => message.error("发布准备检查失败"),
  });

  const updateJob = (job: PublishJob) => {
    qc.setQueryData<PublishJob[]>(publishJobsKey, (current = []) =>
      [job, ...current.filter((item) => item.id !== job.id)],
    );
  };

  return (
    <div className="publish-preparation">
      <div className="content-inspector-summary">
        <span>目标账号</span>
        <strong>{account.nickname}</strong>
        <small>抖音 · 人工发布清单</small>
      </div>
      <Form form={form} layout="vertical" requiredMark={false} onFinish={(values) => mutation.mutate(values)}>
        <Form.Item name="title" label="标题" rules={[{ required: true, message: "请填写标题" }]}>
          <Input maxLength={120} showCount />
        </Form.Item>
        <Form.Item name="body" label="正文"><Input.TextArea autoSize={{ minRows: 4, maxRows: 8 }} maxLength={2000} /></Form.Item>
        <Form.Item name="topics" label="话题"><Input.TextArea autoSize={{ minRows: 2, maxRows: 4 }} placeholder="每行一个话题，不需要输入 #" /></Form.Item>
        <Form.Item name="material_ids" label="发布素材" rules={[{ required: true, message: "至少选择一项已就绪素材" }]}>
          <Checkbox.Group className="publish-material-options">
            {readyMaterials.map((material) => (
              <Checkbox key={material.id} value={material.id}>
                {material.kind === "video" ? "视频" : "图片"} #{material.id}
              </Checkbox>
            ))}
          </Checkbox.Group>
        </Form.Item>
        {readyMaterials.length === 0 ? <p className="content-inline-warning">当前没有已就绪素材，暂时不能生成发布包。</p> : null}
        <Form.Item name="cover_material_id" label="封面">
          <Select allowClear placeholder="选择封面素材" options={readyMaterials.filter((item) => item.kind === "image").map((item) => ({ label: `图片 #${item.id}`, value: item.id }))} />
        </Form.Item>
        <Form.Item name="scheduled_at" label="计划发布时间"><Input type="datetime-local" /></Form.Item>
        <Form.Item name="visibility" label="可见范围">
          <Select options={[{ label: "公开", value: "public" }, { label: "仅朋友", value: "friends" }, { label: "私密", value: "private" }]} />
        </Form.Item>
        <Form.Item name="allow_comment" label="允许评论" valuePropName="checked"><Switch /></Form.Item>
        <Button
          type="primary"
          htmlType="submit"
          block
          loading={mutation.isPending || createJobMutation.isPending}
          disabled={readyMaterials.length === 0}
        >
          检查并生成发布包
        </Button>
      </Form>

      {result ? (
        <section className={`publish-result publish-result--${result.risk}`}>
          <header>
            {result.ready ? <CheckCircleFilled /> : <ExclamationCircleFilled />}
            <strong>{result.ready ? "发布包已准备" : "发布准备被阻断"}</strong>
          </header>
          <div className="publish-result__findings">
            {result.findings.map((finding) => (
              <div key={finding.code} data-level={finding.level}>
                <span>{finding.level === "pass" ? "通过" : finding.level === "warn" ? "注意" : "阻断"}</span>
                <p>{findingCopy(finding.code, finding.message)}</p>
              </div>
            ))}
          </div>
          {result.ready ? <p>已进入人工审批。审批通过后可拉起抖音完成投稿。</p> : null}
        </section>
      ) : null}

      {activeJob ? (
        <PublishJobPanel job={activeJob} onJobChange={updateJob} />
      ) : jobsQuery.isError ? (
        <p className="content-inline-warning">发布任务状态暂时无法读取，请稍后重试。</p>
      ) : null}
    </div>
  );
}

function lines(value: string) {
  return value.split("\n").map((item) => item.trim().replace(/^#/, "")).filter(Boolean);
}

function findingCopy(code: string, fallback: string) {
  const labels: Record<string, string> = {
    "account.required": "必须先为内容选择明确的发布账号。",
    "account.missing": "所选账号已不存在或当前无权访问。",
    "account.platform_mismatch": "所选账号与发布平台不一致。",
    "account.authorization_required": "所选账号尚未完成授权，暂时不能生成发布包。",
    "title.required": "必须填写标题。",
    "title.ok": "标题已填写。",
    "title.long": "标题偏长，建议控制在 30 个字以内。",
    "body.long": "正文较长，发布前请再次确认。",
    "material.required": "至少需要一项已就绪的视频或图片素材。",
    "material.missing": "选择的素材不存在。",
    "material.not_ready": "选择的素材尚未就绪。",
    "material.ok": "素材可以用于发布。",
    "cover.missing": "图文内容需要选择封面。",
    "cover.ok": "封面素材已就绪。",
    "schedule.past": "计划发布时间必须晚于当前时间。",
    "schedule.too_soon": "计划发布时间至少需要提前两小时。",
    "schedule.ok": "计划发布时间有效。",
  };
  return labels[code] ?? fallback;
}

function publishJobKey(contentItemId: number, toolCallId: number) {
  return `content:${contentItemId}:tool:${toolCallId}`;
}

function latestJobForContent(
  jobs: PublishJob[],
  contentItemId: number,
  workspaceToolCallIds: number[],
) {
  const prefix = `content:${contentItemId}:tool:`;
  const toolCallIds = new Set(workspaceToolCallIds);
  return jobs
    .filter((job) =>
      job.idempotency_key.startsWith(prefix)
      || (job.tool_call_id != null && toolCallIds.has(job.tool_call_id)),
    )
    .sort((left, right) => Date.parse(right.updated_at) - Date.parse(left.updated_at))[0] ?? null;
}

function shouldPollJobs(jobs: PublishJob[] | undefined) {
  return Boolean(jobs?.some((job) =>
    ["task_created", "handoff_ready", "user_publishing", "waiting_bind", "bound", "observing"]
      .includes(job.status),
  ));
}
