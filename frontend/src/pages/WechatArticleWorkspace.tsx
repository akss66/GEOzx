import { useQuery } from "@tanstack/react-query";
import { Button, Empty, Spin, Typography } from "antd";
import { useEffect, useMemo, useRef, useState, type Dispatch, type SetStateAction } from "react";
import { useParams } from "react-router-dom";

import { presentApiError } from "../api/errors";
import ArticleEditor from "../components/wechat-article/ArticleEditor";
import ArticleImageSlot from "../components/wechat-article/ArticleImageSlot";
import ArticleVersionConflict from "../components/wechat-article/ArticleVersionConflict";
import WechatSyncConfirmation from "../components/wechat-article/WechatSyncConfirmation";
import { OperationalState, PageHeader } from "../components/ui";
import {
  WechatArticleVersionConflictError,
  createWechatArticleVersion,
  createWechatDraftSync,
  generateAllWechatArticleImages,
  generateWechatArticleImage,
  getWechatArticleDraftSyncContext,
  getWechatArticleImagePrompt,
  getWechatArticlePreview,
  getWechatArticleWorkingCopy,
  saveWechatArticleWorkingCopy,
  selectWechatArticleImageMaterial,
  uploadWechatArticleImage,
  type WechatArticleDocument,
  type WechatArticleDraftSyncContext,
  type WechatArticleImageSlot,
  type WechatArticleWorkingCopy,
} from "../services/wechatArticle";
import "../styles/wechat-article-workspace.css";

type SaveState = "idle" | "saving" | "saved" | "conflict";
type SurfaceTab = "editor" | "preview" | "versions";

interface ConflictState {
  currentLockVersion: number;
  localDocument: WechatArticleDocument;
  serverCopy: WechatArticleWorkingCopy | null;
}

export default function WechatArticleWorkspace() {
  const params = useParams<{ articleId: string }>();
  const articleId = Number(params.articleId);
  const [document, setDocument] = useState<WechatArticleDocument | null>(null);
  const [workingCopy, setWorkingCopy] = useState<WechatArticleWorkingCopy | null>(null);
  const [imageSlots, setImageSlots] = useState<WechatArticleImageSlot[]>([]);
  const [surfaceTab, setSurfaceTab] = useState<SurfaceTab>("editor");
  const [saveState, setSaveState] = useState<SaveState>("idle");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [announcement, setAnnouncement] = useState("");
  const [promptBySlot, setPromptBySlot] = useState<Record<string, string>>({});
  const [busySlotId, setBusySlotId] = useState<number | null>(null);
  const [conflictState, setConflictState] = useState<ConflictState | null>(null);
  const [syncContext, setSyncContext] = useState<WechatArticleDraftSyncContext | null>(null);
  const [syncOpen, setSyncOpen] = useState(false);
  const [syncSubmitting, setSyncSubmitting] = useState(false);
  const hasLocalChangesRef = useRef(false);
  const latestSaveTokenRef = useRef(0);
  const lockVersionRef = useRef(0);
  const conflictActiveRef = useRef(false);
  const preserveLocalDocumentRef = useRef(false);
  const syncTriggerRef = useRef<HTMLButtonElement | null>(null);

  const workingCopyQuery = useQuery({
    queryKey: ["wechat-article-working-copy", articleId],
    queryFn: () => getWechatArticleWorkingCopy(articleId),
    enabled: Number.isFinite(articleId) && articleId > 0,
    retry: false,
  });
  const previewQuery = useQuery({
    queryKey: ["wechat-article-preview", articleId],
    queryFn: () => getWechatArticlePreview(articleId),
    enabled: Number.isFinite(articleId) && articleId > 0,
    retry: false,
  });

  useEffect(() => {
    conflictActiveRef.current = conflictState !== null;
  }, [conflictState]);

  useEffect(() => {
    if (!workingCopyQuery.data) return;
    setWorkingCopy(workingCopyQuery.data);

    if (conflictActiveRef.current) {
      setConflictState((previous) => {
        if (!previous) return previous;
        if (previous.serverCopy?.lockVersion === workingCopyQuery.data.lockVersion) return previous;
        return {
          ...previous,
          serverCopy: workingCopyQuery.data,
        };
      });
      return;
    }

    if (preserveLocalDocumentRef.current) {
      lockVersionRef.current = workingCopyQuery.data.lockVersion;
      preserveLocalDocumentRef.current = false;
      setSaveState("idle");
      setErrorMessage(null);
      return;
    }

    setDocument(workingCopyQuery.data.document);
    setImageSlots((previous) => mergeImageSlots(previous, workingCopyQuery.data.imageSlots));
    lockVersionRef.current = workingCopyQuery.data.lockVersion;
    hasLocalChangesRef.current = false;
    setConflictState(null);
    setSaveState("idle");
    setErrorMessage(null);
  }, [workingCopyQuery.data]);

  useEffect(() => {
    if (!document || !workingCopy || !hasLocalChangesRef.current || conflictState !== null) return;

    const token = ++latestSaveTokenRef.current;
    setSaveState("saving");
    setAnnouncement("正在保存");
    const timer = window.setTimeout(async () => {
      try {
        const saved = await saveWechatArticleWorkingCopy(articleId, {
          expectedLockVersion: lockVersionRef.current,
          document,
        });
        if (token !== latestSaveTokenRef.current) return;
        lockVersionRef.current = saved.lockVersion;
        setWorkingCopy(saved);
        setImageSlots((previous) => mergeImageSlots(previous, saved.imageSlots));
        hasLocalChangesRef.current = false;
        setSaveState("saved");
        setAnnouncement("已保存");
      } catch (error) {
        if (error instanceof WechatArticleVersionConflictError) {
          setConflictState({
            currentLockVersion: error.currentLockVersion,
            localDocument: document,
            serverCopy: null,
          });
          setSurfaceTab("editor");
          setSaveState("conflict");
          setAnnouncement("存在新版本");
          void workingCopyQuery.refetch();
          return;
        }
        setSaveState("idle");
        setAnnouncement("");
        setErrorMessage(presentApiError(error).message);
      }
    }, 2000);

    return () => window.clearTimeout(timer);
  }, [articleId, conflictState, document, workingCopy, workingCopyQuery]);

  const previewMarkup = useMemo(() => {
    if (previewQuery.data?.renderedHtml) return previewQuery.data.renderedHtml;
    if (!document) return "";
    const body = document.blocks
      .map((block) => `<p>${escapeHtml(typeof block.text === "string" ? block.text : "")}</p>`)
      .join("");
    return `<h1>${escapeHtml(document.title)}</h1>${document.digest ? `<p>${escapeHtml(document.digest)}</p>` : ""}${body}`;
  }, [document, previewQuery.data?.renderedHtml]);

  const saveLabel = saveState === "saving"
    ? "正在保存"
    : saveState === "saved"
      ? "已保存"
      : saveState === "conflict"
        ? "存在新版本"
        : "未保存";

  if (!Number.isFinite(articleId) || articleId <= 0) {
    return (
      <OperationalState
        kind="error"
        title="当前文章不可访问"
        description="链接中的文章编号无效，请从账号工作台重新进入。"
      />
    );
  }

  if (workingCopyQuery.isError) {
    const failure = presentApiError(workingCopyQuery.error, "当前文章暂时无法载入。");
    return (
      <OperationalState
        kind="error"
        title="当前文章不可访问"
        description={failure.message}
        diagnostic={failure.diagnostic}
        actionLabel="重新加载"
        onAction={() => void workingCopyQuery.refetch()}
      />
    );
  }

  if (workingCopyQuery.isLoading || !document || !workingCopy) {
    return (
      <div className="wechat-article-loading">
        <Spin />
      </div>
    );
  }

  return (
    <div className="wechat-article-page" data-testid="wechat-article-workspace" data-layout="workspace">
      <span className="wechat-article-live" aria-live="polite">{announcement}</span>
      <PageHeader
        title={document.title || "未命名文章"}
        subtitle={`目标账号：${workingCopy.accountName}`}
        extra={(
          <div className="wechat-article-page__actions">
            <span className={`wechat-article-save-state is-${saveState}`}>{saveLabel}</span>
            <Button
              ref={syncTriggerRef}
              onClick={() => void openSyncConfirmation(articleId, setSyncContext, setSyncOpen, setErrorMessage)}
            >
              创建版本并同步到公众号草稿箱
            </Button>
          </div>
        )}
      />

      <section className="wechat-article-workspace">
        <div className="wechat-article-main">
          {conflictState ? (
            <ArticleVersionConflict
              currentLockVersion={conflictState.currentLockVersion}
              onViewDiff={() => setSurfaceTab("versions")}
              onReload={() => void continueWithLatestVersion(workingCopyQuery.refetch, conflictState, {
                setWorkingCopy,
                setConflictState,
                setSaveState,
                setErrorMessage,
                setAnnouncement,
                lockVersionRef,
                hasLocalChangesRef,
                preserveLocalDocumentRef,
              })}
              onDiscard={() => void discardLocalChanges(workingCopyQuery.refetch, {
                setWorkingCopy,
                setDocument,
                setImageSlots,
                setConflictState,
                setSaveState,
                setErrorMessage,
                setAnnouncement,
                lockVersionRef,
                hasLocalChangesRef,
              })}
            />
          ) : null}

          {errorMessage ? <p className="wechat-article-inline-error">{errorMessage}</p> : null}

          <nav className="wechat-article-tabs" aria-label="工作台视图">
            <button type="button" className={surfaceTab === "editor" ? "is-active" : ""} onClick={() => setSurfaceTab("editor")}>编辑</button>
            <button type="button" className={surfaceTab === "preview" ? "is-active" : ""} onClick={() => setSurfaceTab("preview")}>预览</button>
            <button type="button" className={surfaceTab === "versions" ? "is-active" : ""} onClick={() => setSurfaceTab("versions")}>版本</button>
          </nav>

          {surfaceTab === "editor" ? (
            <ArticleEditor
              document={document}
              onChange={(next) => {
                hasLocalChangesRef.current = true;
                latestSaveTokenRef.current += 1;
                setConflictState(null);
                setSaveState("idle");
                setDocument(next);
              }}
            />
          ) : null}

          {surfaceTab === "preview" ? (
            <section className="wechat-article-preview" aria-labelledby="wechat-article-preview-title">
              <header className="wechat-article-section-head">
                <div>
                  <p>页面预览</p>
                  <h2 id="wechat-article-preview-title">公众号稿件版式预览</h2>
                </div>
              </header>
              <div className="wechat-article-preview__paper" dangerouslySetInnerHTML={{ __html: previewMarkup }} />
            </section>
          ) : null}

          {surfaceTab === "versions" ? (
            <section className="wechat-article-versions" aria-labelledby="wechat-article-versions-title">
              <header className="wechat-article-section-head">
                <div>
                  <p>版本状态</p>
                  <h2 id="wechat-article-versions-title">当前锁版本与冲突对照</h2>
                </div>
              </header>
              <dl>
                <div>
                  <dt>当前锁版本</dt>
                  <dd>{lockVersionRef.current}</dd>
                </div>
                <div>
                  <dt>来源版本</dt>
                  <dd>{workingCopy.basedOnDeliverableId ?? "未创建"}</dd>
                </div>
              </dl>
              {conflictState ? (
                <div className="wechat-article-compare" aria-live="polite">
                  <article className="wechat-article-compare__column">
                    <h3>本地待保存</h3>
                    <strong>{conflictState.localDocument.title || "未命名文章"}</strong>
                    <p>{conflictState.localDocument.digest || "当前本地修改还没有摘要。"}</p>
                  </article>
                  <article className="wechat-article-compare__column">
                    <h3>服务器最新</h3>
                    {conflictState.serverCopy ? (
                      <>
                        <strong>{conflictState.serverCopy.document.title || "未命名文章"}</strong>
                        <p>{conflictState.serverCopy.document.digest || "服务器最新版本还没有摘要。"}</p>
                        <span>锁版本 {conflictState.serverCopy.lockVersion}</span>
                      </>
                    ) : (
                      <p>正在拉取服务器最新版本，请稍候。</p>
                    )}
                  </article>
                </div>
              ) : (
                <p className="wechat-inline-note">如出现新版本冲突，请先查看本地与服务器差异，再决定如何继续。</p>
              )}
            </section>
          ) : null}
        </div>

        <aside className="wechat-article-side">
          <section className="wechat-article-side__panel" aria-label="配图计划">
            <header className="wechat-article-section-head">
              <div>
                <p>配图计划</p>
                <h2>稳定槽位与提示词</h2>
              </div>
              <Button
                onClick={() => void generateAll(articleId, workingCopyQuery.refetch, setAnnouncement, setErrorMessage)}
              >
                一键生成全部配图
              </Button>
            </header>
            {imageSlots.length > 0 ? (
              <div className="wechat-image-slot-list">
                {imageSlots.map((slot) => (
                  <ArticleImageSlot
                    key={slot.stableKey}
                    slot={slot}
                    prompt={promptBySlot[slot.stableKey] ?? null}
                    busy={busySlotId === slot.id}
                    onRequestPrompt={() => void loadPrompt(articleId, slot, setBusySlotId, setPromptBySlot, setErrorMessage)}
                    onCopyPrompt={() => void copyPrompt(promptBySlot[slot.stableKey] ?? "", setAnnouncement)}
                    onGenerate={() => void regenerateSlot(articleId, slot, setBusySlotId, workingCopyQuery.refetch, setAnnouncement, setErrorMessage)}
                    onUpload={(file) => void uploadSlotImage(articleId, slot, file, setBusySlotId, setImageSlots, setAnnouncement, setErrorMessage)}
                  />
                ))}
              </div>
            ) : (
              <Empty description="当前版本还没有配图槽位。" />
            )}
          </section>

          <section className="wechat-article-side__panel" aria-label="摘要信息">
            <header className="wechat-article-section-head">
              <div>
                <p>摘要信息</p>
                <h2>事实、摘要与状态</h2>
              </div>
            </header>
            <dl className="wechat-article-facts">
              <div>
                <dt>事实条目</dt>
                <dd>{document.claims.length}</dd>
              </div>
              <div>
                <dt>图片槽位</dt>
                <dd>{imageSlots.length}</dd>
              </div>
              <div>
                <dt>账号</dt>
                <dd>{workingCopy.accountName}</dd>
              </div>
            </dl>
            <Typography.Paragraph className="wechat-article-summary">
              {document.digest || "当前没有摘要，请先补充摘要。"}
            </Typography.Paragraph>
          </section>
        </aside>
      </section>

      <WechatSyncConfirmation
        open={syncOpen}
        context={syncContext}
        submitting={syncSubmitting}
        triggerRef={syncTriggerRef}
        onCancel={() => setSyncOpen(false)}
        onConfirm={() => void confirmSync(articleId, syncContext, setSyncSubmitting, setAnnouncement, setErrorMessage, setSyncOpen)}
      />
    </div>
  );
}

async function continueWithLatestVersion(
  refetch: () => Promise<{ data?: WechatArticleWorkingCopy }>,
  conflictState: ConflictState,
  handlers: {
    setWorkingCopy: (value: WechatArticleWorkingCopy) => void;
    setConflictState: (value: ConflictState | null) => void;
    setSaveState: (value: SaveState) => void;
    setErrorMessage: (value: string | null) => void;
    setAnnouncement: (value: string) => void;
    lockVersionRef: { current: number };
    hasLocalChangesRef: { current: boolean };
    preserveLocalDocumentRef: { current: boolean };
  },
) {
  const latest = conflictState.serverCopy ?? (await refetch()).data;
  if (!latest) return;

  handlers.setWorkingCopy(latest);
  handlers.lockVersionRef.current = latest.lockVersion;
  handlers.hasLocalChangesRef.current = false;
  handlers.preserveLocalDocumentRef.current = true;
  handlers.setConflictState(null);
  handlers.setSaveState("idle");
  handlers.setErrorMessage(null);
  handlers.setAnnouncement("已对齐服务器最新版本，请继续修改后再保存");
}

async function discardLocalChanges(
  refetch: () => Promise<{ data?: WechatArticleWorkingCopy }>,
  handlers: {
    setWorkingCopy: (value: WechatArticleWorkingCopy) => void;
    setDocument: (value: WechatArticleDocument) => void;
    setImageSlots: (value: WechatArticleImageSlot[]) => void;
    setConflictState: (value: ConflictState | null) => void;
    setSaveState: (value: SaveState) => void;
    setErrorMessage: (value: string | null) => void;
    setAnnouncement: (value: string) => void;
    lockVersionRef: { current: number };
    hasLocalChangesRef: { current: boolean };
  },
) {
  const latest = (await refetch()).data;
  if (!latest) return;

  handlers.setWorkingCopy(latest);
  handlers.setDocument(latest.document);
  handlers.setImageSlots(latest.imageSlots);
  handlers.lockVersionRef.current = latest.lockVersion;
  handlers.hasLocalChangesRef.current = false;
  handlers.setConflictState(null);
  handlers.setSaveState("idle");
  handlers.setErrorMessage(null);
  handlers.setAnnouncement("已恢复服务器最新版本");
}

async function generateAll(
  articleId: number,
  refetch: () => Promise<unknown>,
  setAnnouncement: (value: string) => void,
  setErrorMessage: (value: string | null) => void,
) {
  try {
    await generateAllWechatArticleImages(articleId, buildIdempotencyKey("wechat-image-all"));
    setAnnouncement("已提交全部配图生成");
    setErrorMessage(null);
    await refetch();
  } catch (error) {
    setErrorMessage(presentApiError(error, "配图生成暂时不可用。").message);
  }
}

async function loadPrompt(
  articleId: number,
  slot: WechatArticleImageSlot,
  setBusySlotId: (value: number | null) => void,
  setPromptBySlot: Dispatch<SetStateAction<Record<string, string>>>,
  setErrorMessage: (value: string | null) => void,
) {
  try {
    setBusySlotId(slot.id);
    const prompt = await getWechatArticleImagePrompt(articleId, slot.id);
    setPromptBySlot((previous) => ({ ...previous, [slot.stableKey]: prompt.prompt }));
    setErrorMessage(null);
  } catch (error) {
    setErrorMessage(presentApiError(error, "提示词暂时不可读取。").message);
  } finally {
    setBusySlotId(null);
  }
}

async function regenerateSlot(
  articleId: number,
  slot: WechatArticleImageSlot,
  setBusySlotId: (value: number | null) => void,
  refetch: () => Promise<unknown>,
  setAnnouncement: (value: string) => void,
  setErrorMessage: (value: string | null) => void,
) {
  try {
    setBusySlotId(slot.id);
    await generateWechatArticleImage(articleId, slot.id, buildIdempotencyKey("wechat-image-slot"));
    setAnnouncement("已提交图片生成");
    setErrorMessage(null);
    await refetch();
  } catch (error) {
    setErrorMessage(presentApiError(error, "该图片槽位暂时无法生成。").message);
  } finally {
    setBusySlotId(null);
  }
}

async function uploadSlotImage(
  articleId: number,
  slot: WechatArticleImageSlot,
  file: File,
  setBusySlotId: (value: number | null) => void,
  setImageSlots: Dispatch<SetStateAction<WechatArticleImageSlot[]>>,
  setAnnouncement: (value: string) => void,
  setErrorMessage: (value: string | null) => void,
) {
  try {
    setBusySlotId(slot.id);
    const uploaded = await uploadWechatArticleImage(articleId, slot.id, file);
    const selected = await selectWechatArticleImageMaterial(articleId, slot.id, {
      materialId: uploaded.materialId,
      expectedLockVersion: slot.lockVersion,
    });
    setImageSlots((previous) => previous.map((item) => item.id === slot.id ? selected : item));
    setAnnouncement("已更新图片选择");
    setErrorMessage(null);
  } catch (error) {
    setErrorMessage(presentApiError(error, "上传图片后未能完成选图。").message);
  } finally {
    setBusySlotId(null);
  }
}

async function openSyncConfirmation(
  articleId: number,
  setSyncContext: (value: WechatArticleDraftSyncContext | null) => void,
  setSyncOpen: (value: boolean) => void,
  setErrorMessage: (value: string | null) => void,
) {
  try {
    const version = await createWechatArticleVersion(articleId);
    const context = await getWechatArticleDraftSyncContext(articleId, version.id);
    setSyncContext(context);
    setSyncOpen(true);
    setErrorMessage(null);
  } catch (error) {
    setErrorMessage(presentApiError(error, "同步确认上下文暂时不可用。").message);
  }
}

async function confirmSync(
  articleId: number,
  context: WechatArticleDraftSyncContext | null,
  setSyncSubmitting: (value: boolean) => void,
  setAnnouncement: (value: string) => void,
  setErrorMessage: (value: string | null) => void,
  setSyncOpen: (value: boolean) => void,
) {
  if (!context) return;
  try {
    setSyncSubmitting(true);
    await createWechatDraftSync(articleId, {
      articleVersionId: context.articleVersionId,
      idempotencyKey: buildIdempotencyKey("wechat-sync"),
      expectedRemoteHash: context.remote?.remoteHash ?? null,
      conflictStrategy: "fail",
    });
    setAnnouncement("已提交公众号草稿同步");
    setErrorMessage(null);
    setSyncOpen(false);
  } catch (error) {
    setErrorMessage(presentApiError(error, "草稿同步暂时不可提交。").message);
  } finally {
    setSyncSubmitting(false);
  }
}

function mergeImageSlots(previous: WechatArticleImageSlot[], incoming: WechatArticleImageSlot[]) {
  const byKey = new Map(previous.map((slot) => [slot.stableKey, slot]));
  return incoming.map((slot) => {
    const retained = byKey.get(slot.stableKey);
    return retained ? { ...slot, selectedMaterialId: slot.selectedMaterialId ?? retained.selectedMaterialId } : slot;
  });
}

function buildIdempotencyKey(prefix: string) {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return `${prefix}-${crypto.randomUUID()}`;
  }
  return `${prefix}-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function copyPrompt(prompt: string, setAnnouncement: (value: string) => void) {
  if (!prompt) return;
  void navigator.clipboard?.writeText(prompt);
  setAnnouncement("已复制提示词");
}

function escapeHtml(value: string) {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll("\"", "&quot;")
    .replaceAll("'", "&#39;");
}
