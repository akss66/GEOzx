import { CheckOutlined, DownOutlined } from "@ant-design/icons";
import { useEffect, useMemo, useState } from "react";

import { getAccountAvatar } from "../../api/workspace";
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
        {current ? (
          <AccountAvatar key={current.id} account={current} compact />
        ) : (
          <span className="tz-platform-mark">抖</span>
        )}
        <strong>{current?.nickname ?? "选择抖音账号"}</strong>
        {current?.auth_status === "authorized" ? <span className="tz-live-dot" /> : null}
        <DownOutlined />
      </button>
      {open ? (
        <section className="tz-account-panel" role="dialog" aria-label="切换当前账号">
          <header><strong>当前工作账号</strong><span>抖音</span></header>
          {availableAccounts.length === 0 ? (
            <p>暂无可用抖音账号，请先在账号矩阵接入账号</p>
          ) : availableAccounts.map((account) => (
            <button
              type="button"
              key={account.id}
              onClick={() => { onChange(account.id); setOpen(false); }}
            >
              <AccountAvatar account={account} />
              <span><strong>{account.nickname}</strong><small>{account.auth_status === "authorized" ? "已授权" : "待授权"}</small></span>
              {account.id === accountId ? <CheckOutlined /> : null}
            </button>
          ))}
        </section>
      ) : null}
    </div>
  );
}

function AccountAvatar({ account, compact = false }: { account: Account; compact?: boolean }) {
  const [source, setSource] = useState<string | null>(
    compact ? null : (account.avatar_url ?? null),
  );
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    setFailed(false);
    if (!compact) {
      setSource(account.avatar_url ?? null);
      return;
    }
    setSource(null);
    if (!account.avatar_url) {
      return;
    }

    const controller = new AbortController();
    let objectUrl: string | null = null;
    void getAccountAvatar(account.id, controller.signal)
      .then((blob) => {
        if (controller.signal.aborted) {
          return;
        }
        objectUrl = URL.createObjectURL(blob);
        setSource(objectUrl);
      })
      .catch(() => {
        if (!controller.signal.aborted) {
          setFailed(true);
        }
      });
    return () => {
      controller.abort();
      if (objectUrl) {
        URL.revokeObjectURL(objectUrl);
      }
    };
  }, [account.avatar_url, account.id, compact]);

  return (
    <span className={`tz-account-avatar${compact ? " is-compact" : ""}`}>
      {source && !failed ? (
        <img
          src={source}
          alt={account.nickname}
          onError={() => setFailed(true)}
        />
      ) : (
        <span aria-hidden="true">{account.nickname.slice(0, 1)}</span>
      )}
    </span>
  );
}
