# 同舟行全系统开源融合设计

## 目标

把 OpenManus、Claude-Code、SYNAPSEAUTOMATION、social-auto-upload、Douyin_TikTok_Download_API 中适合同舟行的思想融合成系统底座，而不是停留在某个页面的 UI 借鉴。

核心原则：

- 以官方抖音 OAuth/OpenAPI 为生产接入路径，不复制非官方签名、反爬、绕过限制的代码。
- 以 Agent 执行链为系统主干，传统表单和后台列表只作为辅助视图。
- 每个模块都绑定 `platform + account_id`，先选账号，再启动运营动作。
- 高风险动作进入人工确认，不直接自动发布或投流。
- 每个 Agent 的工具动作都可追踪、可复盘、可计费、可审批。

## 开源项目映射

### OpenManus

用于全系统 Agent 执行模型：

- 主 Agent 拆解任务，专家 Agent 分步执行。
- 每一步有 `planned / running / done / blocked / failed` 状态。
- 每个工具调用落入 `AgentToolCall` 账本。
- 未来加入最大步数、卡住检测、重复输出检测、预算保护。

### Claude-Code

用于专家团协作和可观察性：

- 主 Agent 像 coordinator，向专家团派发任务。
- 专家卡片展示身份、当前任务、产出摘要、失败原因。
- 工具调用需要有权限模式：`auto / confirm / manual`。
- Trace 面板展示 Agent、工具、人工门、交付物、返工链路。

### SYNAPSEAUTOMATION

用于任务和审批体系：

- 任务必须绑定账号、平台、素材、发布意图。
- 任务状态和人工确认状态分离。
- 内容生产、人工审批、运营复盘共享同一套任务上下文。
- 并发和重复任务后续通过锁与队列控制。

### social-auto-upload

用于内容生产和发布准备：

- 素材校验：视频/图片格式、标题长度、正文长度、发布时间。
- 发布参数结构：标题、正文、话题、封面、定时发布时间。
- 当前阶段只做发布准备和人工审批，不做 Creator Center 浏览器自动发布。

### Douyin_TikTok_Download_API

用于数据结构参考：

- 参考作品、作者、评论、指标的标准化字段。
- 只作为数据适配设计参考。
- 不复制 `A-Bogus/X-Bogus`、签名绕过、反爬相关实现。

## 系统级模块落点

### 运营大脑

主 Agent 输入、账号上下文、专家调度、任务 Brief、执行 Trace、人工确认入口。

### 专家团

展示每个专家 Agent 的身份、当前任务、可用工具、最近调用、成本和失败记录。

### 账号矩阵

管理平台账号、授权状态、数据同步状态、账号上下文快照。所有运营任务必须从这里选择当前账号。

### 内容生产

把脚本、视觉提示词、素材校验、发布参数准备接入 Agent 工具账本。

### 人工审批

聚合所有 `permission_mode=confirm` 或 `requires_human_confirmation=true` 的工具调用、质量门和交付物。

### 运营复盘

按当前账号读取任务、内容、工具调用、交付物、成本、指标，形成单账号复盘链路。

### 使用成本

统计 Agent 调用、工具调用、模型 token、素材生成成本。

### 知识库

沉淀账号定位、爆款案例、脚本片段、复盘结论，供主 Agent 和专家 Agent 检索。

### 管理员

用户管理负责权限；Agent 配置负责模型、工具、自动化级别、人工确认策略。

## 当前验收切片

本切片实现 `AgentToolCall` 系统底座：

- 新增后端 `agent_tool_calls` 表。
- 新增 `/brain/tasks/{task_id}/tool-calls` API。
- 现有运营大脑 pipeline 自动同步工具调用记录。
- 前端完整 Trace 模式显示工具节点。
- 后续模块复用同一结构，不再各自造一套执行日志。

## 后续切片

1. 专家团页面接入 `AgentToolCall`，显示每个专家的工具和失败记录。
2. 人工审批页面接入 `requires_human_confirmation` 队列。
3. 内容生产加入素材校验和发布参数模型。
4. 运营复盘按账号聚合任务、工具、交付物和指标。
5. 使用成本纳入工具调用与模型成本。
