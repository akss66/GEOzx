import {
  BookOutlined,
  CheckOutlined,
  CloseOutlined,
  EditOutlined,
  LinkOutlined,
  InboxOutlined,
  PlusOutlined,
  RobotOutlined,
  SearchOutlined,
} from "@ant-design/icons";
import { App, Button, Form, Input, Popconfirm, Select, Skeleton } from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";

import {
  approveKnowledgeSuggestion,
  archiveKnowledge,
  createKnowledge,
  listKnowledge,
  listKnowledgeCitations,
  listKnowledgeSuggestions,
  rejectKnowledgeSuggestion,
  updateKnowledge,
} from "../api/knowledge";
import { presentApiError } from "../api/errors";
import { getWorkspaceContext } from "../api/shell";
import { OperationalState } from "../components/ui";
import { useCurrentWorkspace } from "../stores/currentWorkspace";
import type {
  KnowledgeCategory,
  KnowledgeEntry,
  KnowledgeSuggestion,
} from "../types";

const CATEGORIES: Array<{ key: KnowledgeCategory; label: string; short: string }> = [
  { key: "hot_content", label: "内容方法", short: "爆款结构与内容规律" },
  { key: "user_persona", label: "用户画像", short: "受众、需求与决策特征" },
  { key: "prompt_library", label: "提示词库", short: "视觉与生成规范" },
  { key: "script_library", label: "话术脚本", short: "脚本、评论与客服表达" },
];

type LibraryMode = "library" | "suggestions";

interface EditorValues {
  category: KnowledgeCategory;
  title: string;
  content: string;
  tags?: string;
  source_type: "manual" | "deliverable" | "external";
  source_label: string;
  source_url?: string;
  scope: "client" | "project";
}

export default function Knowledge() {
  const { message } = App.useApp();
  const queryClient = useQueryClient();
  const workspace = useCurrentWorkspace();
  const [mode, setMode] = useState<LibraryMode>("library");
  const [category, setCategory] = useState<KnowledgeCategory>("hot_content");
  const [search, setSearch] = useState("");
  const [selectedEntryId, setSelectedEntryId] = useState<number | null>(null);
  const [selectedSuggestionId, setSelectedSuggestionId] = useState<number | null>(null);
  const [editing, setEditing] = useState<"new" | "edit" | null>(null);
  const [reviewNote, setReviewNote] = useState("");
  const [form] = Form.useForm<EditorValues>();

  const contextQuery = useQuery({
    queryKey: ["workspace-context", workspace.clientId, workspace.projectId],
    queryFn: () => getWorkspaceContext(workspace.clientId, workspace.projectId),
  });
  const client = contextQuery.data?.selected_client
    ?? contextQuery.data?.clients.find((item) => item.id === workspace.clientId)
    ?? null;
  const project = contextQuery.data?.selected_project
    ?? contextQuery.data?.projects.find((item) => item.id === workspace.projectId)
    ?? null;

  const entriesQuery = useQuery({
    queryKey: ["knowledge", client?.id, project?.id, category],
    queryFn: () => listKnowledge(client!.id, project?.id, category),
    enabled: Boolean(client),
  });
  const suggestionsQuery = useQuery({
    queryKey: ["knowledge-suggestions", client?.id, project?.id],
    queryFn: () => listKnowledgeSuggestions(client!.id, project?.id),
    enabled: Boolean(client),
  });

  const entries = useMemo(() => {
    const needle = search.trim().toLocaleLowerCase();
    if (!needle) return entriesQuery.data ?? [];
    return (entriesQuery.data ?? []).filter((entry) =>
      [entry.title, entry.content, ...(entry.tags ?? [])]
        .join(" ")
        .toLocaleLowerCase()
        .includes(needle),
    );
  }, [entriesQuery.data, search]);
  const suggestions = suggestionsQuery.data ?? [];
  const firstEntryId = entries[0]?.id ?? null;
  const firstSuggestionId = suggestions[0]?.id ?? null;
  const selectedEntry = entries.find((item) => item.id === selectedEntryId) ?? entries[0] ?? null;
  const selectedSuggestion = suggestions.find((item) => item.id === selectedSuggestionId)
    ?? suggestions[0]
    ?? null;

  useEffect(() => {
    setSelectedEntryId(firstEntryId);
    setEditing(null);
  }, [category, client?.id, firstEntryId, project?.id]);
  useEffect(() => {
    setSelectedSuggestionId(firstSuggestionId);
  }, [client?.id, firstSuggestionId, project?.id]);

  const citationsQuery = useQuery({
    queryKey: ["knowledge-citations", selectedEntry?.id, client?.id, project?.id],
    queryFn: () => listKnowledgeCitations(selectedEntry!.id, client!.id, project?.id),
    enabled: Boolean(mode === "library" && selectedEntry && client),
  });
  const libraryFailure = mode === "library" && entriesQuery.isError
    ? presentApiError(entriesQuery.error, "知识文档暂时不可用，请稍后重新加载。")
    : mode === "suggestions" && suggestionsQuery.isError
      ? presentApiError(suggestionsQuery.error, "待确认建议暂时不可用，请稍后重新加载。")
      : null;
  const citationFailure = citationsQuery.isError
    ? presentApiError(citationsQuery.error, "引用记录暂时不可用，请稍后重试。")
    : null;

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["knowledge"] });
    queryClient.invalidateQueries({ queryKey: ["knowledge-suggestions"] });
  };
  const saveMutation = useMutation({
    mutationFn: (values: EditorValues) => {
      const tags = parseTags(values.tags);
      if (editing === "edit" && selectedEntry) {
        return updateKnowledge(selectedEntry.id, {
          title: values.title,
          content: values.content,
          tags,
          source_type: values.source_type,
          source_label: values.source_label,
          source_url: values.source_url || null,
        });
      }
      return createKnowledge({
        client_id: client!.id,
        project_id: values.scope === "project" ? project?.id ?? null : null,
        category: values.category,
        title: values.title,
        content: values.content,
        tags,
        source_type: values.source_type,
        source_label: values.source_label,
        source_url: values.source_url || null,
      });
    },
    onSuccess: (entry) => {
      message.success(editing === "edit" ? "知识已更新并生成新版本" : "知识已写入当前范围");
      setEditing(null);
      setCategory(entry.category);
      setSelectedEntryId(entry.id);
      invalidate();
    },
    onError: (error) => message.error(presentApiError(error).message),
  });
  const archiveMutation = useMutation({
    mutationFn: archiveKnowledge,
    onSuccess: () => {
      message.success("知识已归档，历史引用仍然保留");
      setSelectedEntryId(null);
      invalidate();
    },
    onError: (error) => message.error(presentApiError(error).message),
  });
  const approveMutation = useMutation({
    mutationFn: (id: number) => approveKnowledgeSuggestion(id, reviewNote),
    onSuccess: ({ entry }) => {
      message.success("建议已确认并写入知识库");
      setMode("library");
      setCategory(entry.category);
      setSelectedEntryId(entry.id);
      setReviewNote("");
      invalidate();
    },
    onError: (error) => message.error(presentApiError(error).message),
  });
  const rejectMutation = useMutation({
    mutationFn: (id: number) => rejectKnowledgeSuggestion(id, reviewNote),
    onSuccess: () => {
      message.success("建议已驳回，不会写入知识库");
      setReviewNote("");
      invalidate();
    },
    onError: (error) => message.error(presentApiError(error).message),
  });

  const startCreate = () => {
    if (!client) return message.warning("请先选择客户");
    form.setFieldsValue({
      category,
      title: "",
      content: "",
      tags: "",
      source_type: "manual",
      source_label: "运营团队整理",
      source_url: "",
      scope: project ? "project" : "client",
    });
    setEditing("new");
  };
  const startEdit = () => {
    if (!selectedEntry) return;
    form.setFieldsValue({
      category: selectedEntry.category,
      title: selectedEntry.title,
      content: selectedEntry.content,
      tags: (selectedEntry.tags ?? []).join("，"),
      source_type: selectedEntry.source_type === "agent" ? "manual" : selectedEntry.source_type,
      source_label: selectedEntry.source_label,
      source_url: selectedEntry.source_url ?? "",
      scope: selectedEntry.project_id == null ? "client" : "project",
    });
    setEditing("edit");
  };

  if (contextQuery.isLoading) return <Skeleton active paragraph={{ rows: 12 }} />;

  if (contextQuery.isError) {
    const failure = presentApiError(
      contextQuery.error,
      "知识库工作上下文暂时不可用，请稍后重新加载。",
    );
    return (
      <main className="knowledge-studio knowledge-studio--state">
        <OperationalState
          kind="error"
          title="知识库上下文加载失败"
          description={`${failure.message} 已选择的客户和项目不会被修改。`}
          diagnostic={failure.diagnostic}
          actionLabel="重新加载"
          actionLoading={contextQuery.isFetching}
          onAction={() => void contextQuery.refetch()}
        />
      </main>
    );
  }

  return (
    <main className="knowledge-studio">
      <KnowledgeNavigation
        clientName={client?.name}
        projectName={project?.name}
        mode={mode}
        category={category}
        suggestionCount={suggestions.length}
        onMode={setMode}
        onCategory={(next) => { setMode("library"); setCategory(next); }}
      />

      <section className="knowledge-index">
        <header>
          <div>
            <span>{mode === "library" ? categoryLabel(category) : "AGENT INBOX"}</span>
            <h2>{mode === "library" ? "知识文档" : "待确认建议"}</h2>
          </div>
          {mode === "library" ? (
            <Button type="text" aria-label="新增知识" icon={<PlusOutlined />} onClick={startCreate} />
          ) : null}
        </header>
        {mode === "library" ? (
          <Input
            aria-label="搜索知识"
            prefix={<SearchOutlined />}
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="搜索标题、正文或标签"
          />
        ) : null}
        <div className="knowledge-index__list">
          {mode === "library" ? entries.map((entry) => (
            <button
              type="button"
              key={entry.id}
              className={entry.id === selectedEntry?.id && editing == null ? "is-active" : ""}
              onClick={() => { setSelectedEntryId(entry.id); setEditing(null); }}
            >
              <strong>{entry.title}</strong>
              <span>{entry.project_id == null ? "客户通用" : project?.name ?? "当前项目"}</span>
              <small>V{entry.version} · {entry.source_label}</small>
            </button>
          )) : suggestions.map((suggestion) => (
            <button
              type="button"
              key={suggestion.id}
              className={suggestion.id === selectedSuggestion?.id ? "is-active" : ""}
              onClick={() => setSelectedSuggestionId(suggestion.id)}
            >
              <strong>{suggestion.title}</strong>
              <span>{agentLabel(suggestion.source_agent_code)}</span>
              <small>{formatDate(suggestion.created_at)}</small>
            </button>
          ))}
        </div>
      </section>

      <section className="knowledge-canvas">
        {!client ? (
          <KnowledgeGate />
        ) : libraryFailure ? (
          <KnowledgeError
            title={mode === "library" ? "知识文档加载失败" : "待确认建议加载失败"}
            message={libraryFailure.message}
            diagnostic={libraryFailure.diagnostic}
            loading={mode === "library" ? entriesQuery.isFetching : suggestionsQuery.isFetching}
            onRetry={() => void (mode === "library" ? entriesQuery.refetch() : suggestionsQuery.refetch())}
          />
        ) : mode === "suggestions" ? (
          selectedSuggestion ? (
            <SuggestionDocument
              suggestion={selectedSuggestion}
              note={reviewNote}
              approving={approveMutation.isPending}
              rejecting={rejectMutation.isPending}
              onNote={setReviewNote}
              onApprove={() => approveMutation.mutate(selectedSuggestion.id)}
              onReject={() => rejectMutation.mutate(selectedSuggestion.id)}
            />
          ) : <KnowledgeEmpty kind="suggestion" onCreate={startCreate} />
        ) : editing ? (
          <KnowledgeEditor
            form={form}
            projectAvailable={Boolean(project)}
            saving={saveMutation.isPending}
            onCancel={() => setEditing(null)}
            onSave={(values) => saveMutation.mutate(values)}
          />
        ) : selectedEntry ? (
          <KnowledgeDocument
            entry={selectedEntry}
            projectName={project?.name}
            citations={citationsQuery.data ?? []}
            loadingCitations={citationsQuery.isLoading}
            citationsError={citationFailure}
            retryingCitations={citationsQuery.isFetching}
            onRetryCitations={() => void citationsQuery.refetch()}
            onEdit={startEdit}
            onArchive={() => archiveMutation.mutate(selectedEntry.id)}
          />
        ) : entriesQuery.isLoading ? (
          <Skeleton active paragraph={{ rows: 10 }} />
        ) : <KnowledgeEmpty kind="entry" onCreate={startCreate} />}
      </section>
    </main>
  );
}

function KnowledgeNavigation({ clientName, projectName, mode, category, suggestionCount, onMode, onCategory }: {
  clientName?: string; projectName?: string; mode: LibraryMode; category: KnowledgeCategory;
  suggestionCount: number; onMode: (mode: LibraryMode) => void; onCategory: (category: KnowledgeCategory) => void;
}) {
  return (
    <aside className="knowledge-navigation">
      <header><span>共享资产</span><h1>知识库</h1><p>{clientName ?? "尚未选择客户"}<br />{projectName ?? "客户通用知识"}</p></header>
      <nav>
        <button type="button" className={mode === "library" ? "is-active" : ""} onClick={() => onMode("library")}><BookOutlined /><span><strong>已确认知识</strong><small>Agent 可引用</small></span></button>
        <button type="button" className={mode === "suggestions" ? "is-active" : ""} onClick={() => onMode("suggestions")}><RobotOutlined /><span><strong>待确认建议</strong><small>确认后才写入</small></span><b>{suggestionCount}</b></button>
      </nav>
      <div className="knowledge-navigation__collections"><span>集合</span>{CATEGORIES.map((item) => (
        <button type="button" key={item.key} className={mode === "library" && category === item.key ? "is-active" : ""} onClick={() => onCategory(item.key)}><strong>{item.label}</strong><small>{item.short}</small></button>
      ))}</div>
    </aside>
  );
}

function KnowledgeDocument({ entry, projectName, citations, loadingCitations, citationsError, retryingCitations, onRetryCitations, onEdit, onArchive }: {
  entry: KnowledgeEntry; projectName?: string; citations: Array<{ id: number; agent_code: string; context: string; task_id: number | null; created_at: string }>;
  loadingCitations: boolean; citationsError: ReturnType<typeof presentApiError> | null; retryingCitations: boolean;
  onRetryCitations: () => void; onEdit: () => void; onArchive: () => void;
}) {
  return (
    <article className="knowledge-document">
      <header><div><span>{categoryLabel(entry.category)} · V{entry.version}</span><h2>{entry.title}</h2><p>{entry.project_id == null ? "客户通用" : projectName ?? "当前项目"} · 更新于 {formatDate(entry.updated_at)}</p></div><div><Button icon={<EditOutlined />} onClick={onEdit}>编辑</Button><Popconfirm title="归档这条知识？" description="历史引用会继续保留。" okText="确认归档" cancelText="取消" onConfirm={onArchive}><Button type="text" danger icon={<InboxOutlined />} aria-label="归档知识" /></Popconfirm></div></header>
      <div className="knowledge-document__layout">
        <div className="knowledge-document__content"><p>{entry.content}</p>{entry.tags?.length ? <div>{entry.tags.map((tag) => <span key={tag}>#{tag}</span>)}</div> : null}</div>
        <aside>
          <section><span>来源</span><strong>{sourceLabel(entry.source_type)}</strong><p>{entry.source_label}</p>{entry.source_url ? <a href={entry.source_url} target="_blank" rel="noreferrer"><LinkOutlined /> 查看原始来源</a> : null}</section>
          <section><span>引用记录</span>{loadingCitations ? <Skeleton active paragraph={{ rows: 2 }} /> : citationsError ? <OperationalState compact kind="error" title="引用记录加载失败" description={citationsError.message} diagnostic={citationsError.diagnostic} actionLabel="重试" actionLoading={retryingCitations} onAction={onRetryCitations} /> : citations.length ? citations.map((citation) => <div key={citation.id}><strong>{agentLabel(citation.agent_code)}</strong><p>{citation.context}</p><small>{formatDate(citation.created_at)}{citation.task_id ? ` · 任务 #${citation.task_id}` : ""}</small></div>) : <p>尚未被 Agent 引用</p>}</section>
        </aside>
      </div>
    </article>
  );
}

function SuggestionDocument({ suggestion, note, approving, rejecting, onNote, onApprove, onReject }: {
  suggestion: KnowledgeSuggestion; note: string; approving: boolean; rejecting: boolean; onNote: (value: string) => void; onApprove: () => void; onReject: () => void;
}) {
  return (
    <article className="knowledge-document is-suggestion">
      <header><div><span>AGENT SUGGESTION</span><h2>{suggestion.title}</h2><p>{agentLabel(suggestion.source_agent_code)} · {suggestion.source_label}</p></div><strong>待确认</strong></header>
      <div className="knowledge-suggestion-copy"><p>{suggestion.content}</p>{suggestion.tags?.length ? <div>{suggestion.tags.map((tag) => <span key={tag}>#{tag}</span>)}</div> : null}</div>
      <footer><Input.TextArea aria-label="确认意见" value={note} onChange={(event) => onNote(event.target.value)} autoSize={{ minRows: 2, maxRows: 5 }} placeholder="可选：记录采用或驳回理由" /><div><Button icon={<CloseOutlined />} loading={rejecting} onClick={onReject}>驳回</Button><Popconfirm title="确认写入正式知识库？" description="确认后 Agent 才能在后续任务中引用。" okText="确认写入" cancelText="再看看" onConfirm={onApprove}><Button type="primary" icon={<CheckOutlined />} loading={approving} aria-label="采用建议">采用建议</Button></Popconfirm></div></footer>
    </article>
  );
}

function KnowledgeEditor({ form, projectAvailable, saving, onCancel, onSave }: {
  form: ReturnType<typeof Form.useForm<EditorValues>>[0]; projectAvailable: boolean; saving: boolean; onCancel: () => void; onSave: (values: EditorValues) => void;
}) {
  return (
    <article className="knowledge-editor"><header><span>DOCUMENT EDITOR</span><h2>编辑知识文档</h2><p>保存后生成新版本；Agent 来源必须从建议队列确认。</p></header><Form form={form} layout="vertical" requiredMark={false} onFinish={onSave}><div className="knowledge-editor__meta"><Form.Item name="category" label="集合" rules={[{ required: true }]}><Select options={CATEGORIES.map((item) => ({ label: item.label, value: item.key }))} /></Form.Item><Form.Item name="scope" label="适用范围" rules={[{ required: true }]}><Select options={[{ label: "当前客户通用", value: "client" }, { label: "仅当前项目", value: "project", disabled: !projectAvailable }]} /></Form.Item><Form.Item name="source_type" label="来源类型" rules={[{ required: true }]}><Select options={[{ label: "人工整理", value: "manual" }, { label: "已有成果", value: "deliverable" }, { label: "外部资料", value: "external" }]} /></Form.Item></div><Form.Item name="title" label="标题" rules={[{ required: true, message: "请输入标题" }]}><Input maxLength={300} /></Form.Item><Form.Item name="content" label="正文" rules={[{ required: true, message: "请输入可供 Agent 使用的正文" }]}><Input.TextArea autoSize={{ minRows: 10, maxRows: 20 }} maxLength={50_000} /></Form.Item><div className="knowledge-editor__meta"><Form.Item name="source_label" label="来源说明" rules={[{ required: true, message: "请说明来源" }]}><Input maxLength={300} /></Form.Item><Form.Item name="source_url" label="来源链接"><Input /></Form.Item><Form.Item name="tags" label="标签"><Input placeholder="用逗号分隔" /></Form.Item></div><footer><Button onClick={onCancel}>取消</Button><Button type="primary" htmlType="submit" loading={saving}>保存知识</Button></footer></Form></article>
  );
}

function KnowledgeGate() { return <div className="knowledge-gate"><span>01</span><h2>先选择知识所属客户</h2><p>知识、账号和 Agent 上下文必须在同一客户范围内，系统不会自动读取其他客户资料。</p></div>; }
function KnowledgeError({ title, message: description, diagnostic, loading, onRetry }: {
  title: string; message: string; diagnostic?: string | null; loading: boolean; onRetry: () => void;
}) { return <OperationalState kind="error" title={title} description={`${description} 现有知识和当前筛选不会受影响。`} diagnostic={diagnostic} actionLabel="重新加载" actionLoading={loading} onAction={onRetry} />; }
function KnowledgeEmpty({ kind, onCreate }: { kind: "entry" | "suggestion"; onCreate: () => void }) { return <div className="knowledge-gate"><span>00</span><h2>{kind === "entry" ? "这个集合还没有知识" : "没有等待确认的建议"}</h2><p>{kind === "entry" ? "从一条经过验证的运营经验开始，后续 Agent 才能有依据地复用。" : "Agent 提出的沉淀建议会先出现在这里，不会自动写入。"}</p>{kind === "entry" ? <Button type="primary" onClick={onCreate}>新建第一条知识</Button> : null}</div>; }

function parseTags(value?: string) { const tags = value?.split(/[,，\s]+/).map((item) => item.trim()).filter(Boolean) ?? []; return tags.length ? tags : null; }
function categoryLabel(value: KnowledgeCategory) { return CATEGORIES.find((item) => item.key === value)?.label ?? value; }
function sourceLabel(value: string) { return ({ manual: "人工整理", agent: "Agent 建议", deliverable: "正式成果", external: "外部资料" } as Record<string, string>)[value] ?? "已记录来源"; }
function agentLabel(code: string) { return ({ "01-positioning": "账号定位专家", "02-content-director": "编导文案专家", "03-art-director": "美术提示词专家", "04-video-creator": "视频创作专家", "05-editor": "剪辑专家", "06-operator": "账号运营专家", "07-advertiser": "投流专家", "08-customer-service": "客服反馈专家" } as Record<string, string>)[code] ?? "专家 Agent"; }
function formatDate(value: string) { return new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }).format(new Date(value)); }
