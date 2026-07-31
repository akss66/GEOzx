import { Button } from "antd";

export function ImportCommitBar({
  totalCount,
  blockingCount,
  committing,
  disabled,
  onCommit,
}: {
  totalCount: number;
  blockingCount: number;
  committing: boolean;
  disabled: boolean;
  onCommit: () => void;
}) {
  return (
    <div className="account-data-commit-bar">
      <div>
        <strong>{blockingCount > 0 ? `仍有 ${blockingCount} 条需要处理` : "全部数据已通过校验"}</strong>
        <span>
          {blockingCount > 0
            ? "解决校验错误或匹配冲突后，才会写入当前账号。"
            : `确认后将把 ${totalCount} 条数据写入当前账号。`}
        </span>
      </div>
      <Button
        type="primary"
        loading={committing}
        disabled={disabled}
        onClick={onCommit}
      >
        {`确认写入 ${totalCount} 条`}
      </Button>
    </div>
  );
}
