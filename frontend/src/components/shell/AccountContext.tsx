import { CheckOutlined, DownOutlined } from "@ant-design/icons";
import { useMemo, useState } from "react";

import {
  listSelectableWorkspaceAccounts,
  resolveWorkspaceAccount,
} from "../../stores/currentWorkspace";
import type { Account, Platform } from "../../types";

export function AccountContext({
  accounts,
  platform,
  accountId,
  onChange,
}: {
  accounts: Account[];
  platform: Platform;
  accountId: number | null;
  onChange: (accountId: number | null) => void;
}) {
  const [open, setOpen] = useState(false);
  const availableAccounts = useMemo(
    () => listSelectableWorkspaceAccounts(accounts, platform),
    [accounts, platform],
  );
  const current = resolveWorkspaceAccount(accounts, platform, accountId);

  return (
    <div className="tz-account-context">
      <button
        type="button"
        className="tz-account-trigger"
        aria-label="当前账号"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        <span className="tz-platform-mark">抖</span>
        <strong>{current?.nickname ?? "选择抖音账号"}</strong>
        {current?.auth_status === "authorized" ? <span className="tz-live-dot" /> : null}
        <DownOutlined />
      </button>
      {open ? (
        <section className="tz-account-panel" role="dialog" aria-label="切换当前账号">
          <header><strong>当前工作账号</strong><span>抖音</span></header>
          {availableAccounts.length === 0 ? (
            <p>当前客户暂无可用抖音账号</p>
          ) : availableAccounts.map((account) => (
            <button
              type="button"
              key={account.id}
              onClick={() => { onChange(account.id); setOpen(false); }}
            >
              <span className="tz-account-avatar">{account.nickname.slice(0, 1)}</span>
              <span><strong>{account.nickname}</strong><small>{account.auth_status === "authorized" ? "已授权" : "待授权"}</small></span>
              {account.id === accountId ? <CheckOutlined /> : null}
            </button>
          ))}
        </section>
      ) : null}
    </div>
  );
}
