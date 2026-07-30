# Task 8 Brief — ConversationTurn 单一投影、可靠流式与专业日志

## Ownership and workflow

负责计划 Task 8 的后端投影/schema、前端 BrainHome/TurnStream/event reducer/types/styles 和测试。不得修改 ArtifactCenter/TurnArtifact 的正式 Artifact 身份。你不是代码库唯一执行者，不得回退他人提交。

严格 TDD；先锁定重复、错位、流式丢帧、跨 Thread 污染，再改 UI。完成后运行前后端定向、TypeScript、build、Ruff/diff-check，提交代码和 Task 8 报告。

## Single source of truth

- ConversationThread.turns / ConversationTurn 是聊天区唯一持久来源。
- 移除 BrainHome 当前聊天渲染中的 legacy ConversationStream/BrainRuntime、独立 PendingConversation、active Task localStorage 回退和重复工具栏。
- pending/running/done/error 使用同一个 `TurnArticle`。
- 新 Thread 创建后立即 `setQueryData(["brain-conversation", thread.id], thread)`。
- HTTP submission 按 `turn.id` 或 `client_message_id` 归并，不能追加第二个用户消息。
- React key 使用 `thread_id + client_message_id`，绑定服务器 turn_id 前后不 remount/跳位。

建议新增纯函数投影：

```ts
type TurnIdentity = {
  threadId: number;
  turnId: number | null;
  clientMessageId: string;
};
```

领域键：`${threadId}:${turnId ?? "pending"}:${clientMessageId}`。

## Streaming contract

当前硬根因：后端 synthetic start/delta/done 复用同一个持久 Event.id，而 useEventStream 对所有数字 id 全局去重，start 后丢掉 delta/done。

修复：

- durable `event.id` 只做持久事件 checkpoint。
- message_start/message_delta 是 ephemeral frame，不加入 durable seen set。
- message_done/failed/completed 等 durable event 才按 event.id 去重。
- 后端 realtime payload 增加 `stream_seq`：
  - start=0
  - delta=1..N
  - done 为终态完整 content
- reducer 忽略 sequence <= lastSequence；late start 不清空；done without start 可完成；done 后 late delta 忽略；done 完整 content 覆盖 delta。
- 不得按 delta 文本去重。
- 事件必须先匹配 thread，再匹配 client，双方有 turn id 时还必须相同。
- 重连 refetch 当前 Conversation；durable GET 覆盖 live overlay；不跨 Thread 保留 live state。

## Turn state and technical details

- 后端 `ConversationTurnOut` 与前端 `ConversationTurn` 暴露模型已有的 `status`。
- 技术日志不能从 `intent.status` 猜状态。
- 默认只显示参与专家、总体状态、质量门摘要、正式成果。
- 展开才显示 route、skill、tool、critic、状态和 Run/SkillRun/Invocation ID。
- `_execution_summary_projection()` 不得因 invocation 为空而丢 tool-only/critic-only summary。
- Critic 摘要只返回 allowlist 字段，不暴露 Prompt、原始 Tool 输入输出、异常堆栈、密钥。
- Task 9 前不显示伪造耗时。

## Artifact identity

- Turn projection 的 `artifact_id` 和成果中心必须指向同一个正式 Artifact.id。
- 删除 legacy DeliverableAcceptance 聊天渲染，不创建第二套成果 ID。

## Required tests

1. POST 后缓存与 pending 同 client id 只渲染一次。
2. 新 Thread 首次 pending 使用 TurnArticle。
3. optimistic 绑定服务器 ID 后 key/位置稳定。
4. start→相同文本 delta→delta→done 正确输出。
5. delta→late start 不清空。
6. done without start 可完成。
7. done 后 late delta 忽略。
8. start/delta/done 复用 transport id 也不丢 frame。
9. durable done 重连重复只处理一次。
10. 跨 Thread/错误 Turn/错误 client 全部忽略。
11. 重连 durable GET 覆盖临时内容。
12. 默认专家摘要；展开 route/tool/critic/status。
13. tool-only/critic-only 有 execution summary。
14. Turn/成果中心同 Artifact id。
15. active task localStorage 不再激活 legacy UI。
16. BrainHome 不再调用 listBrainTasks/getBrainTaskRuntime 渲染聊天。

## Out of scope

- Task 9 才加入真实耗时与模型调用计数。
- 不改 ArtifactCenter/TurnArtifact。
- 不部署生产。
