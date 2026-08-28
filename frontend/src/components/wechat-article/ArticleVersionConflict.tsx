import { Button } from "antd";

interface ArticleVersionConflictProps {
  currentLockVersion: number;
  onViewDiff: () => void;
  onReload: () => void;
  onDiscard: () => void;
}

export default function ArticleVersionConflict({
  currentLockVersion,
  onViewDiff,
  onReload,
  onDiscard,
}: ArticleVersionConflictProps) {
  return (
    <section className="wechat-article-conflict" aria-live="polite">
      <div>
        <strong>存在新版本</strong>
        <p>当前草稿已经被更新到锁版本 {currentLockVersion}，系统不会自动覆盖。</p>
      </div>
      <div className="wechat-article-conflict__actions">
        <Button onClick={onViewDiff}>查看差异</Button>
        <Button onClick={onReload}>基于新版本继续修改</Button>
        <Button danger onClick={onDiscard}>放弃本地修改</Button>
      </div>
    </section>
  );
}
