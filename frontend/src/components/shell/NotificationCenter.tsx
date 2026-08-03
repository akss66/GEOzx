import { BellOutlined, CheckOutlined } from "@ant-design/icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import dayjs from "dayjs";
import { useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import {
  getUnreadNotificationCount,
  listNotifications,
  markNotificationRead,
  type ShellNotification,
} from "../../api/shell";
import { presentApiError } from "../../api/errors";
import { useDismissibleLayer } from "../../hooks/useDismissibleLayer";
import { OperationalState } from "../ui";

export function NotificationCenter() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLElement>(null);
  useDismissibleLayer({ open, onDismiss: () => setOpen(false), panelRef, triggerRef });
  const countQuery = useQuery({
    queryKey: ["notifications", "unread-count"],
    queryFn: getUnreadNotificationCount,
    refetchInterval: 30_000,
  });
  const listQuery = useQuery({
    queryKey: ["notifications", "list"],
    queryFn: listNotifications,
    enabled: open,
  });
  const readMutation = useMutation({
    mutationFn: markNotificationRead,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["notifications"] });
    },
  });

  const openNotice = (notice: ShellNotification) => {
    if (!notice.read_at) readMutation.mutate(notice.id);
    setOpen(false);
    if (notice.path) navigate(notice.path);
  };
  const unreadCount = countQuery.data ?? 0;
  const failedQuery = listQuery.isError ? listQuery : countQuery.isError ? countQuery : null;
  const failure = failedQuery
    ? presentApiError(failedQuery.error, "通知暂时不可用，请稍后重新加载。")
    : null;

  return (
    <div className="tz-notification-center">
      <button
        ref={triggerRef}
        type="button"
        className="tz-shell-icon"
        aria-label="通知"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        <BellOutlined />
        {unreadCount > 0 ? <span className="tz-notification-badge">{Math.min(unreadCount, 99)}</span> : null}
      </button>
      {open ? (
        <section ref={panelRef} className="tz-notification-panel" aria-label="通知中心">
          <header><strong>通知</strong>{unreadCount > 0 ? <span>{unreadCount} 条未读</span> : <CheckOutlined />}</header>
          <div>
            {failure ? (
              <OperationalState
                kind="error"
                compact
                title="通知加载失败"
                description={failure.message}
                diagnostic={failure.diagnostic}
                actionLabel="重新加载"
                actionLoading={listQuery.isFetching || countQuery.isFetching}
                onAction={() => {
                  void Promise.all([listQuery.refetch(), countQuery.refetch()]);
                }}
              />
            ) : listQuery.isLoading ? (
              <p className="tz-notification-empty">正在加载...</p>
            ) : listQuery.data?.length ? (
              listQuery.data.map((notice) => (
                <button
                  key={notice.id}
                  type="button"
                  className={notice.read_at ? "is-read" : ""}
                  onClick={() => openNotice(notice)}
                >
                  <span className="tz-notification-status" />
                  <span><strong>{notice.title}</strong>{notice.body ? <small>{notice.body}</small> : null}</span>
                  <time>{dayjs(notice.created_at).format("MM-DD HH:mm")}</time>
                </button>
              ))
            ) : (
              <p className="tz-notification-empty">没有新通知</p>
            )}
          </div>
        </section>
      ) : null}
    </div>
  );
}
