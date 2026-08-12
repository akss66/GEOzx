import { Input } from "antd";

import type { WechatArticleDocument } from "../../services/wechatArticle";

const { TextArea } = Input;

export interface ArticleEditorProps {
  document: WechatArticleDocument;
  disabled?: boolean;
  onChange: (next: WechatArticleDocument) => void;
}

export default function ArticleEditor({ document, disabled = false, onChange }: ArticleEditorProps) {
  const body = document.blocks
    .map((block) => (typeof block.text === "string" ? block.text : ""))
    .join("\n\n");

  return (
    <section className="wechat-article-editor" aria-labelledby="wechat-article-editor-title">
      <header className="wechat-article-section-head">
        <div>
          <p>文章编辑</p>
          <h2 id="wechat-article-editor-title">逐段维护标题、摘要与正文</h2>
        </div>
      </header>
      <div className="wechat-article-editor__fields">
        <label className="wechat-article-field">
          <span>标题</span>
          <Input
            aria-label="标题"
            disabled={disabled}
            value={document.title}
            onChange={(event) => onChange({ ...document, title: event.target.value })}
          />
        </label>
        <label className="wechat-article-field">
          <span>摘要</span>
          <TextArea
            aria-label="摘要"
            autoSize={{ minRows: 3, maxRows: 6 }}
            disabled={disabled}
            value={document.digest}
            onChange={(event) => onChange({ ...document, digest: event.target.value })}
          />
        </label>
        <label className="wechat-article-field">
          <span>作者</span>
          <Input
            aria-label="作者"
            disabled={disabled}
            value={document.author ?? ""}
            onChange={(event) => onChange({ ...document, author: event.target.value || null })}
          />
        </label>
        <label className="wechat-article-field">
          <span>正文</span>
          <TextArea
            aria-label="正文"
            autoSize={{ minRows: 12, maxRows: 20 }}
            disabled={disabled}
            value={body}
            onChange={(event) => onChange({
              ...document,
              blocks: document.blocks.length > 0
                ? document.blocks.map((block, index) => (
                  index === 0 ? { ...block, text: event.target.value } : block
                ))
                : [{ type: "paragraph", blockId: "body", text: event.target.value }],
            })}
          />
        </label>
      </div>
    </section>
  );
}
