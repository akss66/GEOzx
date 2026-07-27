import { FileTextOutlined, PlusOutlined } from "@ant-design/icons";
import { Button, Input, Skeleton } from "antd";
import { useState } from "react";

import type { ContentItem } from "../../types";
import { displayContentTitle, stageLabel, statusLabel } from "./contentPresentation";

export function ContentRail({
  items,
  selectedId,
  loading,
  canCreate,
  creating,
  onSelect,
  onCreate,
}: {
  items: ContentItem[];
  selectedId: number | null;
  loading: boolean;
  canCreate: boolean;
  creating: boolean;
  onSelect: (item: ContentItem) => void;
  onCreate: (title: string) => void;
}) {
  const [composerOpen, setComposerOpen] = useState(false);
  const [title, setTitle] = useState("");

  const submit = () => {
    const next = title.trim();
    if (!next) return;
    onCreate(next);
    setTitle("");
    setComposerOpen(false);
  };

  return (
    <aside className="content-rail" aria-label="内容对象">
      <header className="content-rail__header">
        <div>
          <span>内容对象</span>
          <strong>{items.length}</strong>
        </div>
        <Button
          type="text"
          icon={<PlusOutlined />}
          aria-label="新建内容"
          disabled={!canCreate}
          onClick={() => setComposerOpen((value) => !value)}
        />
      </header>

      {composerOpen ? (
        <div className="content-rail__composer">
          <Input.TextArea
            autoFocus
            value={title}
            maxLength={300}
            autoSize={{ minRows: 2, maxRows: 4 }}
            placeholder="写下这条内容的工作标题"
            onChange={(event) => setTitle(event.target.value)}
            onPressEnter={(event) => {
              if (event.shiftKey) return;
              event.preventDefault();
              submit();
            }}
          />
          <div>
            <Button size="small" onClick={() => setComposerOpen(false)}>取消</Button>
            <Button size="small" type="primary" loading={creating} onClick={submit}>
              创建工作区
            </Button>
          </div>
        </div>
      ) : null}

      <div className="content-rail__list">
        {loading ? (
          Array.from({ length: 5 }).map((_, index) => (
            <Skeleton.Button key={index} active block />
          ))
        ) : items.length === 0 ? (
          <div className="content-rail__empty">
            <FileTextOutlined />
            <strong>项目里还没有内容</strong>
            <span>从一个明确主题开始，运营大脑和专家会在这里留下正式成果。</span>
            {canCreate ? (
              <Button type="link" onClick={() => setComposerOpen(true)}>创建第一条内容</Button>
            ) : null}
          </div>
        ) : (
          items.map((item) => (
            <button
              type="button"
              key={item.id}
              className={`content-rail-item${selectedId === item.id ? " is-active" : ""}`}
              onClick={() => onSelect(item)}
            >
              <span className="content-rail-item__marker" />
              <span className="content-rail-item__copy">
                <strong>{displayContentTitle(item.title)}</strong>
                <small>{stageLabel(item.current_stage)} · {statusLabel(item.status)}</small>
              </span>
            </button>
          ))
        )}
      </div>
    </aside>
  );
}
