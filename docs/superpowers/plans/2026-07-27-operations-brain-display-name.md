# 运营大脑对外称谓统一 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将所有用户可见的“主 Agent”统一为“运营大脑”，同时保留内部架构术语和用户原始内容。

**Architecture:** 静态产品文案从源头改名；新增前端系统文案展示规范化函数，兼容历史任务但不处理用户消息；新增后端模型对外身份指令，集中约束模型输出。API 结构、Agent code、数据库字段和内部角色名称保持不变。

**Tech Stack:** React 18、TypeScript、Vitest、Testing Library、FastAPI、Python、pytest

## Global Constraints

- 面向用户的名称固定为“运营大脑”。
- `00-decision`、组件名、函数名、变量名、文件名、CSS 类名和数据库字段不重命名。
- 用户消息、用户备注和其他用户原始内容不得执行称谓替换。
- 内部 Prompt 可以继续使用“主 Agent”描述职责，但模型对外输出必须使用“运营大脑”。
- 不修改 API 数据结构，不新增数据库迁移，不批量改写历史数据。
- 工作区已有未提交的 AI COO 改动；禁止覆盖、回退或整文件暂存这些改动。

---

### Task 1: 前端系统文案展示边界

**Files:**
- Create: `frontend/src/utils/operationsBrainCopy.ts`
- Create: `frontend/src/utils/operationsBrainCopy.test.ts`
- Modify: `frontend/src/pages/BrainHome.tsx`
- Modify: `frontend/src/components/accounts/AccountViews.tsx`
- Modify: `frontend/src/components/brain/BrainComposer.tsx`

**Interfaces:**
- Produces: `OPERATIONS_BRAIN_DISPLAY_NAME: "运营大脑"`
- Produces: `presentOperationsBrainSystemCopy(value: string): string`
- Consumes: 只接收系统生成的 Agent 名称、状态、事件、摘要和模型回复；不得传入用户消息。

- [ ] **Step 1: 写入失败的展示规范化测试**

```ts
import { describe, expect, it } from "vitest";
import { presentOperationsBrainSystemCopy } from "./operationsBrainCopy";

describe("presentOperationsBrainSystemCopy", () => {
  it("normalizes legacy system identity copy", () => {
    expect(presentOperationsBrainSystemCopy("主 Agent 正在推进"))
      .toBe("运营大脑正在推进");
    expect(presentOperationsBrainSystemCopy("主Agent已完成"))
      .toBe("运营大脑已完成");
  });

  it("leaves unrelated copy unchanged", () => {
    expect(presentOperationsBrainSystemCopy("账号定位专家已完成"))
      .toBe("账号定位专家已完成");
  });
});
```

- [ ] **Step 2: 运行测试并确认因模块不存在而失败**

Run:

```powershell
pnpm.cmd exec vitest run src/utils/operationsBrainCopy.test.ts
```

Expected: FAIL，提示无法解析 `./operationsBrainCopy`。

- [ ] **Step 3: 实现最小展示规范化函数**

```ts
export const OPERATIONS_BRAIN_DISPLAY_NAME = "运营大脑" as const;

export function presentOperationsBrainSystemCopy(value: string) {
  return value.replace(/主\s*Agent/g, OPERATIONS_BRAIN_DISPLAY_NAME);
}
```

- [ ] **Step 4: 只在受信任系统字段接入规范化**

在 `BrainHome.tsx` 中：

- `UserMessage` 继续只调用 `cleanBrainCopy(content)`，保持用户原文。
- `formatAgentContent` 在解析 JSON 前调用 `presentOperationsBrainSystemCopy`。
- `RuntimeStatusMessage`、`task.current_focus`、主 Agent 的 `agentName` 和系统生命周期消息调用 `presentOperationsBrainSystemCopy`。
- `pendingAgentMessage().agentName` 直接使用 `OPERATIONS_BRAIN_DISPLAY_NAME`。

在 `AccountViews.tsx` 中，对 `account.current_task.current_focus` 使用 `presentOperationsBrainSystemCopy`。

在 `BrainComposer.tsx` 中，对工具调用的 `output_summary`、`input_summary` 使用 `presentOperationsBrainSystemCopy`，默认文案直接改为“确认后运营大脑将继续当前工作流。”

- [ ] **Step 5: 添加历史兼容且不改用户原文的页面测试**

在 `BrainHome.test.tsx` 现有对话测试附近加入：

```tsx
it("normalizes legacy system identity copy without rewriting the user message", async () => {
  mocks.taskWithRuntime.current_focus = "主 Agent 正在理解目标";
  mocks.taskWithRuntime.brief.goal = "请解释主 Agent 和专家的分工";
  localStorage.setItem(
    "tongzhouxing_brain_active_tasks",
    JSON.stringify({ version: 1, accounts: { 3: 12 } }),
  );

  renderBrainHome();

  const conversation = await screen.findByLabelText("运营大脑对话流");
  expect(conversation).toHaveTextContent("请解释主 Agent 和专家的分工");
  expect(screen.getByText("运营大脑正在理解目标")).toBeInTheDocument();
});
```

测试结束后恢复共享 mock 字段，避免污染后续用例。

- [ ] **Step 6: 运行聚焦测试**

Run:

```powershell
pnpm.cmd exec vitest run src/utils/operationsBrainCopy.test.ts src/pages/BrainHome.test.tsx src/components/brain/BrainComposer.test.tsx
```

Expected: PASS。

- [ ] **Step 7: 保存点**

仅暂存新建且干净的 utility 文件。`BrainHome.tsx`、`BrainHome.test.tsx` 等已有用户改动的文件暂不整文件暂存。

```powershell
git add -- frontend/src/utils/operationsBrainCopy.ts frontend/src/utils/operationsBrainCopy.test.ts
git diff --cached --check
git commit -m "feat: normalize legacy operations brain copy"
```

---

### Task 2: 前端静态产品文案统一

**Files:**
- Modify: `frontend/src/components/agents/AgentAvatar.tsx`
- Modify: `frontend/src/pages/BrainHome.tsx`
- Modify: `frontend/src/components/brain/DecisionRequest.tsx`
- Modify: `frontend/src/components/brain/BrainComposer.tsx`
- Modify: `frontend/src/components/brain/AgentOrchestration.tsx`
- Modify: `frontend/src/components/accounts/AccountViews.tsx`
- Modify: `frontend/src/components/content/ContentRail.tsx`
- Modify: `frontend/src/components/experts/ExpertArtifact.tsx`
- Modify: `frontend/src/components/shell/GlobalAgentLauncher.tsx`
- Modify: `frontend/src/pages/Config.tsx`
- Modify: `frontend/src/pages/ReviewDashboard.tsx`
- Modify: `frontend/src/pages/Login.tsx`
- Modify: `frontend/src/pages/BrainHome.test.tsx`
- Modify: `frontend/src/components/brain/AgentOrchestration.test.tsx`
- Modify: `frontend/src/components/brain/BrainComposer.test.tsx`

**Interfaces:**
- Consumes: `OPERATIONS_BRAIN_DISPLAY_NAME` 与 `presentOperationsBrainSystemCopy`。
- Produces: 前端用户界面和无障碍树不再把平台身份显示为“主 Agent”。

- [ ] **Step 1: 先更新关键行为断言**

将现有断言改为：

```tsx
expect(within(conversation).getByText("运营大脑")).toBeInTheDocument();
expect(screen.getAllByRole("img", { name: "运营大脑" }).length)
  .toBeGreaterThan(0);
expect(screen.getByRole("button", { name: /发送给运营大脑/ }))
  .toBeDisabled();
expect(screen.getByRole("region", { name: "运营大脑输入区" }))
  .toBeInTheDocument();
```

`AgentOrchestration.test.tsx` 同步断言“运营大脑”和“输入运营目标后，这里会显示运营大脑调度专家的过程”。

- [ ] **Step 2: 运行测试并确认旧文案导致失败**

Run:

```powershell
pnpm.cmd exec vitest run src/pages/BrainHome.test.tsx src/components/brain/AgentOrchestration.test.tsx src/components/brain/BrainComposer.test.tsx
```

Expected: FAIL，缺少“运营大脑”相关标题、按钮或无障碍名称。

- [ ] **Step 3: 修改静态产品文案**

逐项将用户可见的“主 Agent”改为“运营大脑”：

- `AgentAvatar` 的 `00-decision` 默认 `aria-label`。
- `BrainHome` 的警告、页头副标题、欢迎区、决策记录、状态文案和临时消息名。
- `DecisionRequest` 的 `aria-label` 与方向输入占位符。
- `BrainComposer` 的区域名、发送按钮名和确认提示。
- `AgentOrchestration` 的空状态、身份标题和等待状态。
- `AccountViews`、`ContentRail`、`ExpertArtifact` 的状态与交接动作。
- `GlobalAgentLauncher` 的面板、标题、打开/关闭按钮、输入占位符和提交动作。
- `Config`、`ReviewDashboard`、`Login` 的说明和成功提示。

- [ ] **Step 4: 扫描前端生产源码**

Run:

```powershell
rg -n "主\s*Agent|主Agent" frontend/src --glob "!**/*.test.*"
```

Expected: 不再出现用户可见硬编码；仅允许变量、函数、类名等非字符串内部标识。

- [ ] **Step 5: 运行前端聚焦测试**

Run:

```powershell
pnpm.cmd exec vitest run src/pages/BrainHome.test.tsx src/components/brain/AgentOrchestration.test.tsx src/components/brain/BrainComposer.test.tsx src/components/AppShell.test.tsx
```

Expected: PASS。

- [ ] **Step 6: 保存点**

只提交原本干净且本任务独占的前端文件。对已有用户改动的 `BrainHome.tsx`、`BrainHome.test.tsx` 不整文件暂存，并在最终交付中明确列出。

---

### Task 3: 后端系统文案与模型对外身份

**Files:**
- Create: `backend/app/orchestrator/agent_identity.py`
- Create: `backend/tests/test_agent_identity.py`
- Modify: `backend/app/api/brain.py`
- Modify: `backend/app/orchestrator/brain_runtime.py`
- Modify: `backend/app/orchestrator/brain_intelligence.py`
- Modify: `backend/app/orchestrator/brain_planner.py`
- Modify: `backend/app/services/agent_workspace.py`
- Modify: `backend/tests/test_brain_api.py`
- Modify: `backend/tests/test_brain_intelligence.py`
- Modify: `backend/tests/test_prompt_registry.py`

**Interfaces:**
- Produces: `OPERATIONS_BRAIN_DISPLAY_NAME = "运营大脑"`
- Produces: `with_operations_brain_public_identity(prompt: str) -> str`
- Consumes: 所有发送给主 Agent 模型的 system prompt。

- [ ] **Step 1: 写入失败的模型身份指令测试**

```python
from app.orchestrator.agent_identity import (
    OPERATIONS_BRAIN_DISPLAY_NAME,
    with_operations_brain_public_identity,
)


def test_appends_public_identity_without_renaming_internal_role() -> None:
    prompt = with_operations_brain_public_identity("你是主 Agent，负责调度专家。")

    assert "你是主 Agent" in prompt
    assert "面向用户时统一使用“运营大脑”" in prompt
    assert OPERATIONS_BRAIN_DISPLAY_NAME == "运营大脑"
```

- [ ] **Step 2: 运行测试并确认因模块不存在而失败**

Run:

```powershell
python -m pytest backend/tests/test_agent_identity.py -q
```

Expected: FAIL，提示无法导入 `app.orchestrator.agent_identity`。

- [ ] **Step 3: 实现集中模型身份指令**

```python
OPERATIONS_BRAIN_DISPLAY_NAME = "运营大脑"

_PUBLIC_IDENTITY_INSTRUCTION = """
## 对外称谓
面向用户时统一使用“运营大脑”指代你自己，不展示“主 Agent”这一内部架构术语。
用户原始输入必须原样保留，不得改写用户对该术语的引用。
""".strip()


def with_operations_brain_public_identity(prompt: str) -> str:
    return f"{prompt.rstrip()}\n\n{_PUBLIC_IDENTITY_INSTRUCTION}"
```

- [ ] **Step 4: 接入所有主 Agent 模型调用**

在 `brain_intelligence.py` 和 `brain_runtime.py` 向模型传递 system message 时，用 `with_operations_brain_public_identity(prompt.content)` 包装；Prompt 本体和 `prompt_hash` 保持不变。

`brain_planner.py` 的旧规划 Prompt 尾部加入同一对外称谓指令，或调用该函数包装返回字符串。

- [ ] **Step 5: 修改后端用户可见系统文案**

在以下输出中把“主 Agent”改为“运营大脑”：

- `brain.py`：任务焦点、计划摘要、内容目标和恢复状态。
- `brain_runtime.py`：`current_focus`、timeline message、`agent_name` 和权限/调度提示。
- `brain_intelligence.py`：会返回 API 的可用性错误。
- `brain_planner.py`：可见计划摘要和校验错误。
- `agent_workspace.py`：确认动作和入口提示。

模块 docstring、注释、内部类型名和架构描述保持不变。

- [ ] **Step 6: 更新后端行为测试**

更新 `test_brain_api.py` 中用户可见断言：

```python
assert confirmed_body["current_focus"] == "运营大脑已完成普通对话，未启动专家工作流"
```

在 `test_brain_intelligence.py` 捕获 system prompt 后增加：

```python
assert "面向用户时统一使用“运营大脑”" in captured["system"]
```

Prompt 标题仍断言 `# 同舟行主 Agent`，证明内部角色未被重命名。

- [ ] **Step 7: 运行后端聚焦测试**

Run:

```powershell
python -m pytest backend/tests/test_agent_identity.py backend/tests/test_brain_intelligence.py backend/tests/test_brain_api.py backend/tests/test_prompt_registry.py -q
```

Expected: PASS。

- [ ] **Step 8: 扫描后端产品输出**

Run:

```powershell
rg -n "主\s*Agent|主Agent" backend/app/api backend/app/orchestrator backend/app/services --glob "*.py"
```

逐条确认剩余命中仅为 docstring、注释、内部 Prompt 或明确的内部架构错误；任何会进入 API payload 的字符串必须改成“运营大脑”。

- [ ] **Step 9: 保存点**

```powershell
git add -- backend/app/orchestrator/agent_identity.py backend/tests/test_agent_identity.py
git diff --cached --check
git commit -m "feat: define operations brain public identity"
```

已有用户改动的后端文件不整文件暂存。

---

### Task 4: 全量验证与浏览器验收

**Files:**
- Verify: `frontend/src/**`
- Verify: `backend/app/**`
- Verify: `backend/tests/**`

**Interfaces:**
- Consumes: Task 1–3 的所有对外命名规则。
- Produces: 可复核的测试、构建、扫描和浏览器证据。

- [ ] **Step 1: 运行前端全量测试**

```powershell
Set-Location frontend
pnpm.cmd test
```

Expected: 所有测试通过，无失败或跳过。

- [ ] **Step 2: 运行前端生产构建**

```powershell
Set-Location frontend
pnpm.cmd build
```

Expected: TypeScript 与 Vite 构建成功。

- [ ] **Step 3: 运行后端相关全量测试**

```powershell
python -m pytest backend/tests/test_agent_identity.py backend/tests/test_brain_intelligence.py backend/tests/test_brain_api.py backend/tests/test_workspace_api.py -q
```

Expected: 所有选定测试通过。

- [ ] **Step 4: 浏览器验收**

在真实浏览器中验证：

1. 欢迎区头像旁显示“运营大脑”。
2. 输入区无障碍名称和发送按钮使用“运营大脑”。
3. 进入对话后，Agent 标题、系统状态和历史旧文案显示“运营大脑”。
4. 用户发送包含“主 Agent”的测试消息后，用户气泡保持原文。
5. 全局 Agent 入口、专家交接入口和方案选择不显示旧称谓。
6. 控制台无本次改动引入的新错误。

- [ ] **Step 5: 最终边界检查**

```powershell
git diff --check
git status --short
git diff -- frontend/src backend/app backend/tests
```

确认没有覆盖用户原有 AI COO 改动；最终报告分别列出已提交的新文件和因与用户改动重叠而保留在工作区的文件。
