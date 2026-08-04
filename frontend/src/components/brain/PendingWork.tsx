import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { App as AntApp, Button } from "antd";
import { useNavigate } from "react-router-dom";

import {
  completePendingShootTask,
  getAccountPendingWork,
  pendingWorkQueryKey,
  publishPendingScheduleEntry,
  type PendingWorkCompletion,
  type PendingWorkItem,
} from "../../api/pendingWork";
import { presentApiError } from "../../api/errors";

interface PendingWorkProps {
  accountId: number | null;
  onOpenSource: (target: { threadId: number; turnId: number }) => void;
}

export function PendingWork({ accountId, onOpenSource }: PendingWorkProps) {
  const { message } = AntApp.useApp();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: accountId == null
      ? ["account-pending-work", "unselected"]
      : pendingWorkQueryKey(accountId),
    queryFn: () => getAccountPendingWork(accountId!),
    enabled: accountId != null,
    staleTime: 10_000,
  });
  const completion = useMutation({
    mutationFn: async (item: PendingWorkItem) => {
      if (accountId == null) throw new Error("没有选择账号");
      const resourceId = Number(item.id.split(":", 2)[1]);
      if (!Number.isSafeInteger(resourceId) || resourceId <= 0) {
        throw new Error("待处理事项标识无效");
      }
      if (item.kind === "shoot_task") {
        return completePendingShootTask(accountId, resourceId);
      }
      if (item.kind === "manual_publish") {
        return publishPendingScheduleEntry(accountId, resourceId);
      }
      throw new Error("当前事项不能在这里完成");
    },
    onSuccess: async (result: PendingWorkCompletion) => {
      await queryClient.invalidateQueries({ queryKey: pendingWorkQueryKey(result.account_id) });
      message.success(result.next_step_after_completion);
    },
    onError: (error) => message.error(
      presentApiError(error, "待处理事项更新失败，请重试。").message,
    ),
  });

  if (accountId == null) {
    return <div className="tz-pending-work__empty">选择账号后查看待处理事项</div>;
  }
  if (query.isLoading) {
    return (
      <div
        className="tz-pending-work__loading"
        role="status"
        aria-label="正在读取待处理事项"
        aria-busy="true"
      >
        正在读取需要你处理的工作…
      </div>
    );
  }
  if (query.isError) {
    return (
      <div className="tz-pending-work__error" role="alert">
        <strong>待处理事项加载失败</strong>
        <p>当前账号不会显示其他账号或其他成员的个人事项。</p>
        <Button onClick={() => void query.refetch()}>重试</Button>
      </div>
    );
  }

  const groups = (query.data?.groups ?? []).filter((group) => group.count > 0);
  if (groups.length === 0) {
    return (
      <div className="tz-pending-work__empty" role="status">
        <strong>当前没有需要你处理的事项</strong>
        <span>运营大脑会在需要补充信息或人工执行时放到这里。</span>
      </div>
    );
  }

  const openTarget = (item: PendingWorkItem) => {
    if (item.target.type === "conversation_turn") {
      onOpenSource({
        threadId: item.target.thread_id,
        turnId: item.target.turn_id,
      });
      return;
    }
    if (item.target.type === "account_data") {
      navigate(`/accounts/${accountId}/data`);
      return;
    }
    navigate("/tasks");
  };

  return (
    <section className="tz-pending-work" aria-label="待处理工作">
      <header className="tz-pending-work__header">
        <div>
          <span className="dy-brain-kicker">人工工作台</span>
          <h2>待处理</h2>
        </div>
        <p>这里只显示需要你本人补充、确认或执行的工作。</p>
      </header>
      <div className="tz-pending-work__groups">
        {groups.map((group) => (
          <section className="tz-pending-work__group" key={group.kind}>
            <header>
              <h3>{group.label}</h3>
              <span aria-label={`${group.label} ${group.count} 项`}>{group.count}</span>
            </header>
            <ul>
              {group.items.map((item) => (
                <li key={item.id} className="tz-pending-work__item">
                  <div className="tz-pending-work__copy">
                    <strong>{item.reason}</strong>
                    {item.due_at ? <time dateTime={item.due_at}>{formatDueAt(item.due_at)}</time> : null}
                    <span>完成后：{item.next_step_after_completion}</span>
                  </div>
                  <div className="tz-pending-work__actions">
                    <Button onClick={() => openTarget(item)}>{item.action_label}</Button>
                    {item.kind === "shoot_task" ? (
                      <Button
                        type="primary"
                        loading={completion.isPending && completion.variables?.id === item.id}
                        onClick={() => completion.mutate(item)}
                      >
                        标记拍摄完成
                      </Button>
                    ) : null}
                    {item.kind === "manual_publish" ? (
                      <Button
                        type="primary"
                        loading={completion.isPending && completion.variables?.id === item.id}
                        onClick={() => completion.mutate(item)}
                      >
                        记录已发布
                      </Button>
                    ) : null}
                  </div>
                </li>
              ))}
            </ul>
          </section>
        ))}
      </div>
    </section>
  );
}

function formatDueAt(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "时间待确认";
  return `截止 ${new Intl.DateTimeFormat("zh-CN", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date)}`;
}
