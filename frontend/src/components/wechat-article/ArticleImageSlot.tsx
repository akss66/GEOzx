import { Button } from "antd";

import type { WechatArticleImageSlot } from "../../services/wechatArticle";

export interface ArticleImageSlotProps {
  slot: WechatArticleImageSlot;
  prompt: string | null;
  busy?: boolean;
  onRequestPrompt: () => void;
  onCopyPrompt: () => void;
  onGenerate: () => void;
  onUpload: (file: File) => void;
}

export default function ArticleImageSlot({
  slot,
  prompt,
  busy = false,
  onRequestPrompt,
  onCopyPrompt,
  onGenerate,
  onUpload,
}: ArticleImageSlotProps) {
  return (
    <article className="wechat-image-slot">
      <div className="wechat-image-slot__meta">
        <div>
          <strong>{slot.purpose}</strong>
          <p>{slot.visualBrief}</p>
        </div>
        <span>{slot.aspectRatio}</span>
      </div>
      <div className="wechat-image-slot__status">
        <span>状态</span>
        <strong>{slot.selectedMaterialId ? "已选图" : "待补图"}</strong>
      </div>
      {prompt ? (
        <div className="wechat-image-slot__prompt">
          <span>画面提示词</span>
          <p>{prompt}</p>
        </div>
      ) : (
        <p className="wechat-image-slot__hint">提示词默认隐藏，只有操作后才会显示。</p>
      )}
      <div className="wechat-image-slot__actions">
        <Button onClick={onGenerate} loading={busy}>
          {slot.selectedMaterialId ? "重新生成" : "生成该图片"}
        </Button>
        <Button onClick={onRequestPrompt} disabled={!slot.hasPrompt}>
          获取提示词
        </Button>
        <Button onClick={onCopyPrompt} disabled={!prompt}>
          复制提示词
        </Button>
        <label className="wechat-image-slot__upload">
          <input
            type="file"
            accept="image/*"
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) onUpload(file);
              event.currentTarget.value = "";
            }}
          />
          <span>上传自己的图片</span>
        </label>
      </div>
    </article>
  );
}
