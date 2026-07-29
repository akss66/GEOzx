import { DeleteOutlined, MessageOutlined } from "@ant-design/icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { App, Button, Drawer, Empty, List, Popconfirm, Skeleton, Typography } from "antd";

import { deleteConversation, listConversations } from "../../api/brain";

interface ConversationHistoryDrawerProps {
  accountId: number | null;
  activeThreadId: number | null;
  open: boolean;
  onClose: () => void;
  onSelect: (threadId: number) => void;
  onDeleted: (threadId: number) => void;
}

export function ConversationHistoryDrawer({
  accountId,
  activeThreadId,
  open,
  onClose,
  onSelect,
  onDeleted,
}: ConversationHistoryDrawerProps) {
  const { message } = App.useApp();
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: ["brain-conversations", accountId],
    queryFn: () => listConversations(accountId!),
    enabled: open && accountId != null,
  });
  const deletion = useMutation({
    mutationFn: (threadId: number) => deleteConversation(threadId),
    onSuccess: async (_, threadId) => {
      await queryClient.invalidateQueries({
        queryKey: ["brain-conversations", accountId],
      });
      onDeleted(threadId);
      message.success("历史会话已永久删除");
    },
    onError: () => {
      message.error("删除失败，请稍后重试");
    },
  });

  return (
    <Drawer
      title="历史会话"
      width={460}
      open={open}
      onClose={onClose}
      className="tz-brain-history-drawer"
    >
      <Typography.Paragraph type="secondary">
        仅显示当前账号下由你创建的会话。切换账号后，历史记录会自动隔离。
      </Typography.Paragraph>
      {query.isLoading ? <Skeleton active /> : null}
      {query.isError ? (
        <Empty
          description="历史会话暂时无法加载"
          image={Empty.PRESENTED_IMAGE_SIMPLE}
        >
          <Button onClick={() => void query.refetch()}>重新加载</Button>
        </Empty>
      ) : null}
      {!query.isLoading && !query.isError && query.data?.length === 0 ? (
        <Empty description="当前账号还没有历史会话" />
      ) : null}
      <List
        dataSource={query.data ?? []}
        renderItem={(thread) => (
          <List.Item
            className={thread.id === activeThreadId ? "is-active" : undefined}
            actions={[
              <Popconfirm
                key="delete"
                title="永久删除这条历史会话？"
                description="对话消息和技术执行日志都会永久删除，且无法恢复。"
                okText="永久删除"
                cancelText="取消"
                okButtonProps={{ danger: true }}
                onConfirm={() => deletion.mutate(thread.id)}
              >
                <Button
                  type="text"
                  danger
                  aria-label={`删除会话 ${thread.title || thread.last_message}`}
                  icon={<DeleteOutlined />}
                  loading={deletion.isPending && deletion.variables === thread.id}
                />
              </Popconfirm>,
            ]}
          >
            <List.Item.Meta
              avatar={<MessageOutlined />}
              title={(
                <Button
                  type="link"
                  onClick={() => {
                    onSelect(thread.id);
                    onClose();
                  }}
                >
                  {thread.title || "未命名会话"}
                </Button>
              )}
              description={(
                <>
                  <div>{thread.last_message || "尚无消息"}</div>
                  <small>
                    {thread.turn_count} 轮 · {new Date(thread.updated_at).toLocaleString("zh-CN")}
                  </small>
                </>
              )}
            />
          </List.Item>
        )}
      />
    </Drawer>
  );
}
