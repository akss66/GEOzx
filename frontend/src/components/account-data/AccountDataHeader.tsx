import { Button } from "antd";

import type {
  AccountDataImportBatchSummary,
  AccountDataStatus,
} from "../../api/accountData";
import type { Account } from "../../types";
import { PlatformTag } from "../ui";

const accountStatusLabels = {
  active: "正常",
  inactive: "停用",
  banned: "封禁",
} as const;

function formatConfirmedAt(value: string | null) {
  if (!value) return "暂无已确认数据";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "暂无已确认数据";
  return `最近确认 ${new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(date)}`;
}

type AccountDataHeaderProps = {
  account: Account;
  status: AccountDataStatus;
  pendingBatch: AccountDataImportBatchSummary | null;
  updating?: boolean;
  onUpdateData: () => void;
};

export function AccountDataHeader({
  account,
  status,
  pendingBatch,
  updating = false,
  onUpdateData,
}: AccountDataHeaderProps) {
  return (
    <header className="account-data-header">
      <div className="account-data-account">
        {account.avatar_url ? (
          <img src={account.avatar_url} alt="" className="account-data-avatar" />
        ) : (
          <span className="account-data-avatar account-data-avatar--fallback">
            {account.nickname.slice(0, 1)}
          </span>
        )}
        <div>
          <span>账号数据中心</span>
          <strong>{account.nickname}</strong>
          <div className="account-data-account-meta">
            <PlatformTag platform={account.platform} />
            <span>{accountStatusLabels[account.status]}</span>
            <span aria-label="数据确认状态">{formatConfirmedAt(status.latest_confirmed_at)}</span>
            {pendingBatch ? <b>有 1 个批次等待确认</b> : null}
          </div>
        </div>
      </div>
      <Button type="primary" loading={updating} onClick={onUpdateData}>
        更新数据
      </Button>
    </header>
  );
}
