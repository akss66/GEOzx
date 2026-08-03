# Main Agent Work Turn UX Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将运营大脑前端统一为一个持续更新的单回合工作单，并以具体运营产物和业务动作替代“成果/采用/脚本”等抽象概念。

**Architecture:** 保留后端现有 Artifact 兼容模型，本切片先建立用户侧 `DeliverablePresentation` 和统一 `WorkTurnCard`。历史 Turn、乐观 Turn、实时 Turn 和完成 Turn 使用同一投影与组件树；专家和技术日志采用两级渐进披露。

**Tech Stack:** React 18、TypeScript 5.6、TanStack Query、Zustand、Vitest、Testing Library、Playwright、现有 FastAPI Artifact API。

## Global Constraints

- 用户只看到一个主 Agent，不渲染专家聊天气泡。
- 一个用户请求只渲染一个 WorkTurn，不追加重复主 Agent 回复。
- 用户侧不得出现“成果”“采用成果”“脚本生成中”。
- 内容类型必须显示为口播拍摄稿、分镜拍摄稿、产品视频拍摄稿、图文发布稿或直播流程与话术稿。
- 主按钮必须说明真实下一步动作。
- 不同账号的对话、缓存、方案与内容完全隔离。
- 所有组件改动先写失败测试，完成后运行前端全量测试与生产构建。

---

### Task 1: 建立运营产物展示契约

**Files:**
- Create: `frontend/src/components/brain/deliverablePresentation.ts`
- Create: `frontend/src/components/brain/deliverablePresentation.test.ts`
- Modify: `frontend/src/components/brain/ArtifactCard.tsx`
- Modify: `frontend/src/components/brain/ArtifactCard.test.tsx`

**Interfaces:**
- Consumes: `Artifact.artifact_type`、`Artifact.title`、`Artifact.sections`。
- Produces: `presentDeliverable(artifact: Artifact): DeliverablePresentation`。

- [ ] **Step 1: 写展示映射失败测试**

```ts
expect(presentDeliverable(artifact("video_script"))).toMatchObject({
  typeLabel: "口播拍摄稿",
  completionLabel: "已生成 5 条可直接拍摄的口播稿",
  primaryAction: { kind: "open", label: "查看 5 条拍摄稿" },
});
```

- [ ] **Step 2: 运行测试确认映射尚不存在**

Run: `cd frontend && npm test -- deliverablePresentation.test.ts`
Expected: FAIL with module or export missing.

- [ ] **Step 3: 实现强类型展示契约**

```ts
export interface DeliverablePresentation {
  typeLabel: string;
  completionLabel: string;
  primaryAction: { kind: "open" | "plan" | "shoot" | "schedule" | "review"; label: string };
  secondaryActions: Array<{ kind: "edit" | "export" | "regenerate"; label: string }>;
}

export function presentDeliverable(artifact: Artifact): DeliverablePresentation {
  return PRESENTATIONS[artifact.artifact_type] ?? genericReportPresentation(artifact);
}
```

- [ ] **Step 4: 改造 ArtifactCard 只使用具体业务文案**

删除卡片中的“正式成果”“采用成果”“仅采用报告”，使用 `completionLabel` 和动作映射；状态标签只显示“草稿”“待你确认”“已完成”“需要修改”。

- [ ] **Step 5: 运行组件测试与可访问性断言**

Run: `cd frontend && npm test -- deliverablePresentation.test.ts ArtifactCard.test.tsx`
Expected: PASS，且渲染文本中不存在“采用成果”。

- [ ] **Step 6: 提交**

```bash
git add frontend/src/components/brain/deliverablePresentation.ts frontend/src/components/brain/deliverablePresentation.test.ts frontend/src/components/brain/ArtifactCard.tsx frontend/src/components/brain/ArtifactCard.test.tsx
git commit -m "feat: present main agent outputs as concrete operations work"
```

### Task 2: 建立统一 WorkTurn 投影

**Files:**
- Create: `frontend/src/components/brain/workTurnProjection.ts`
- Create: `frontend/src/components/brain/workTurnProjection.test.ts`
- Modify: `frontend/src/components/brain/conversationTurnProjection.ts`
- Modify: `frontend/src/types.ts`

**Interfaces:**
- Consumes: `ConversationTurn`、`TurnProjection[]`、流式事件。
- Produces: `projectWorkTurn(turn: ConversationTurn): WorkTurnViewModel`。

- [ ] **Step 1: 写四种来源投影为同一结构的失败测试**

```ts
for (const source of [historyTurn, optimisticTurn, streamingTurn, completedTurn]) {
  expect(projectWorkTurn(source)).toMatchObject({
    userMessage: expect.any(String),
    assistant: { identity: "运营大脑", steps: expect.any(Array) },
  });
}
```

- [ ] **Step 2: 运行投影测试确认当前路径结构不一致**

Run: `cd frontend && npm test -- workTurnProjection.test.ts conversationTurnProjection.test.ts`
Expected: FAIL because `WorkTurnViewModel` does not exist.

- [ ] **Step 3: 定义统一视图模型**

```ts
export interface WorkTurnViewModel {
  key: string;
  turnId: number | null;
  userMessage: string;
  status: "working" | "waiting_user" | "completed" | "blocked" | "failed" | "cancelled";
  currentActivity: string | null;
  assistantText: string | null;
  steps: Array<{ code: string; label: string; state: "done" | "active" | "waiting" | "failed"; detail?: string }>;
  experts: Array<{ name: string; status: string }>;
  deliverableIds: number[];
}
```

- [ ] **Step 4: 将流式 sequence 合并移入统一投影**

同一 `messageId` 只接受更大的 `stream_seq`；终态后忽略迟到 delta；乐观 Turn 获得服务端 ID 时保留同一个 `key`。

- [ ] **Step 5: 运行投影与类型检查**

Run: `cd frontend && npm test -- workTurnProjection.test.ts conversationTurnProjection.test.ts && npx tsc --noEmit`
Expected: PASS.

- [ ] **Step 6: 提交**

```bash
git add frontend/src/components/brain/workTurnProjection.ts frontend/src/components/brain/workTurnProjection.test.ts frontend/src/components/brain/conversationTurnProjection.ts frontend/src/types.ts
git commit -m "refactor: unify main agent work turn projection"
```

### Task 3: 用单回合工作单替换双套聊天 UI

**Files:**
- Create: `frontend/src/components/brain/WorkTurnCard.tsx`
- Create: `frontend/src/components/brain/WorkTurnCard.test.tsx`
- Create: `frontend/src/components/brain/WorkTurnProgress.tsx`
- Create: `frontend/src/components/brain/ProcessDisclosure.tsx`
- Modify: `frontend/src/components/brain/TurnStream.tsx`
- Modify: `frontend/src/components/brain/TurnStream.test.tsx`

**Interfaces:**
- Consumes: `WorkTurnViewModel`、`Artifact[]`。
- Produces: 每个 Turn 唯一的 `WorkTurnCard` DOM 根节点。

- [ ] **Step 1: 写唯一工作回合失败测试**

```ts
render(<TurnStream turns={[runningTurn]} />);
expect(screen.getAllByTestId("work-turn")).toHaveLength(1);
expect(screen.getAllByText("运营大脑")).toHaveLength(1);
expect(screen.queryByText("思考中")).not.toBeInTheDocument();
```

- [ ] **Step 2: 运行测试确认当前 TurnStream 仍分段渲染**

Run: `cd frontend && npm test -- TurnStream.test.tsx WorkTurnCard.test.tsx`
Expected: FAIL.

- [ ] **Step 3: 实现 WorkTurnCard 固定结构**

组件顺序固定为用户消息、主 Agent 身份、当前活动、步骤、专家摘要、具体交付区、业务动作。`status="working"` 时只更新这些子区域，不切换组件。

- [ ] **Step 4: 实现两级过程披露**

第一级“查看过程”展示专家与证据摘要；第二级“技术日志”按需渲染 Tool、模型、耗时和内部 ID。默认 DOM 不渲染完整技术日志。

- [ ] **Step 5: 删除 TurnStream 中旧气泡和重复状态路径**

保留 `TurnStream` 作为列表容器，只负责 `turns.map(projectWorkTurn).map(WorkTurnCard)`。

- [ ] **Step 6: 运行组件测试**

Run: `cd frontend && npm test -- WorkTurnCard.test.tsx TurnStream.test.tsx`
Expected: PASS，工作中和完成后 DOM 根节点不变化。

- [ ] **Step 7: 提交**

```bash
git add frontend/src/components/brain/WorkTurnCard.tsx frontend/src/components/brain/WorkTurnCard.test.tsx frontend/src/components/brain/WorkTurnProgress.tsx frontend/src/components/brain/ProcessDisclosure.tsx frontend/src/components/brain/TurnStream.tsx frontend/src/components/brain/TurnStream.test.tsx
git commit -m "feat: render one continuous main agent work turn"
```

### Task 4: 修复乐观消息对齐与原位流式更新

**Files:**
- Modify: `frontend/src/stores/brainConversation.ts`
- Modify: `frontend/src/pages/BrainHome.tsx`
- Modify: `frontend/src/pages/BrainHome.test.tsx`
- Modify: `frontend/src/components/brain/conversationTurnProjection.test.ts`

**Interfaces:**
- Consumes: `client_message_id`、服务端 Turn、流式 `message_id + stream_seq`。
- Produces: `upsertTurnByClientMessageId(...)` 和稳定 UI key。

- [ ] **Step 1: 写发送后立即与历史消息对齐的失败测试**

提交“你好”后立即断言新消息与历史用户消息拥有相同 `data-layout="user-message"`，服务端返回后节点不被替换。

- [ ] **Step 2: 运行 BrainHome 测试确认乐观路径使用独立结构**

Run: `cd frontend && npm test -- BrainHome.test.tsx conversationTurnProjection.test.ts`
Expected: FAIL.

- [ ] **Step 3: 以 client_message_id 原位升级乐观 Turn**

```ts
upsertTurnByClientMessageId(threadId, clientMessageId, (current) => ({
  ...current,
  ...serverTurn,
  client_message_id: clientMessageId,
}));
```

- [ ] **Step 4: 将文本 delta 与阶段进度同时归并到 WorkTurn**

普通回答显示 token delta；Skill 工作只更新 `currentActivity` 和步骤，不生成第二个“正在思考”组件。

- [ ] **Step 5: 运行测试与类型检查**

Run: `cd frontend && npm test -- BrainHome.test.tsx conversationTurnProjection.test.ts && npx tsc --noEmit`
Expected: PASS.

- [ ] **Step 6: 提交**

```bash
git add frontend/src/stores/brainConversation.ts frontend/src/pages/BrainHome.tsx frontend/src/pages/BrainHome.test.tsx frontend/src/components/brain/conversationTurnProjection.test.ts
git commit -m "fix: keep optimistic and streamed turns in one layout"
```

### Task 5: 将成果中心重命名为“方案与内容”

**Files:**
- Modify: `frontend/src/components/brain/ArtifactCenter.tsx`
- Modify: `frontend/src/components/brain/ArtifactCenter.test.tsx`
- Modify: `frontend/src/pages/BrainHome.tsx`
- Modify: `frontend/src/pages/BrainHome.test.tsx`
- Modify: `frontend/src/api/brain.ts`

**Interfaces:**
- Consumes: 现有 `/artifacts` API。
- Produces: 用户侧“方案与内容”分类与筛选；内部 API 名称暂不破坏兼容性。

- [ ] **Step 1: 写信息架构失败测试**

断言顶部为“对话 / 方案与内容 / 抖音数据 / 待处理”，页面中不存在独立“成果”标签。

- [ ] **Step 2: 运行测试确认旧标签存在**

Run: `cd frontend && npm test -- ArtifactCenter.test.tsx BrainHome.test.tsx`
Expected: FAIL.

- [ ] **Step 3: 按业务类型分组列表**

分组固定为“诊断与复盘、对标分析、选题、拍摄稿、发布安排”；列表项显示版本、数据周期、更新时间和具体下一步。

- [ ] **Step 4: 保留来源跳转与同版本引用**

从“方案与内容”返回对话时定位原 Turn，不复制 Artifact 数据；账号切换时清空选中项和查询缓存。

- [ ] **Step 5: 运行测试**

Run: `cd frontend && npm test -- ArtifactCenter.test.tsx BrainHome.test.tsx brain.test.ts`
Expected: PASS.

- [ ] **Step 6: 提交**

```bash
git add frontend/src/components/brain/ArtifactCenter.tsx frontend/src/components/brain/ArtifactCenter.test.tsx frontend/src/pages/BrainHome.tsx frontend/src/pages/BrainHome.test.tsx frontend/src/api/brain.ts
git commit -m "feat: replace artifact center with plans and content"
```

### Task 6: 完成视觉、响应式和端到端验收

**Files:**
- Modify: `frontend/src/styles/brain-v2.css`
- Create: `frontend/e2e/main-agent-work-turn.spec.ts`
- Modify: `frontend/src/components/brain/WorkTurnCard.test.tsx`

**Interfaces:**
- Consumes: Tasks 1-5 的统一组件。
- Produces: 桌面和窄屏一致的视觉层级及 E2E 回归门。

- [ ] **Step 1: 写桌面与窄屏 E2E 断言**

```ts
await expect(page.getByTestId("work-turn")).toHaveCount(1);
await expect(page.getByRole("tab", { name: "方案与内容" })).toBeVisible();
await expect(page.getByText("查看 5 条拍摄稿")).toBeVisible();
```

- [ ] **Step 2: 实现无气泡堆叠的视觉规则**

用户消息右对齐但不使用红色侧边条；主 Agent 使用头像、名称和正文的无框结构；步骤区和交付区只使用一层边框；移动端所有区域单列。

- [ ] **Step 3: 验证键盘和读屏行为**

“查看过程”“技术日志”和版本历史使用原生 button，包含 `aria-expanded`；焦点顺序为用户消息后的主 Agent 内容、交付动作、输入框。

- [ ] **Step 4: 运行前端全量质量门**

Run: `cd frontend && npm test && npm run lint && npm run build && npm run test:e2e -- main-agent-work-turn.spec.ts`
Expected: all PASS，生产构建成功。

- [ ] **Step 5: 提交**

```bash
git add frontend/src/styles/brain-v2.css frontend/e2e/main-agent-work-turn.spec.ts frontend/src/components/brain/WorkTurnCard.test.tsx
git commit -m "test: lock main agent worker interaction experience"
```
