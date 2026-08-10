# 主 Agent Thinking Orbs 状态呈现设计

## 背景

运营大脑目前通过静态头像、阶段文字和执行步骤表达运行状态。用户希望在主 Agent 的不同执行阶段引入 `thinking-orbs`，让运行过程更容易感知，同时保留现有中文状态信息与品牌识别。

本设计只处理主 Agent 对话区的状态呈现，不改变 Agent Runtime、SSE 事件、WorkTurn 生命周期、业务路由或全局导航。

## 目标

- 用户无需刷新或展开技术日志，即可从动画和中文状态文字理解当前阶段。
- 同一个 WorkTurn 从开始到结束保持为同一块对话内容，不新增第二套气泡或独立加载卡片。
- Thinking Orb 是状态的辅助表达，不替代可读文字，不暴露模型思维链。
- 保持页面顶部运营大脑品牌头像、右上角用户/账号头像及其他全局身份标识不变。
- 在低性能设备、隐藏标签页和减少动态效果模式下保持可用。

## 采用方案

### 方案 A：仅在活跃 WorkTurn 内替换头像（采用）

当前执行中的主 Agent 头像替换为 64px Thinking Orb；WorkTurn 进入等待、完成、失败或取消后，恢复原运营大脑头像。页面顶部和右上角头像不变。

优点：状态与正在执行的工作绑定，视觉干扰最少，不会让全局品牌头像不停变化，也不会出现头像与 Orb 重复表达。

### 方案 B：头像旁追加 20px Orb（不采用）

保留头像，并在旁边增加小型 Orb。信息更保守，但视觉元素重复，容易变成“头像 + 状态标签 + 动画 + 状态文字”四层表达。

### 方案 C：页面顶部全局 Orb（不采用）

把顶部运营大脑头像替换为 Orb。虽然显眼，但无法明确动画属于哪个 WorkTurn，也会破坏用户已经建立的品牌识别。

## 范围边界

### 包含

- 当前活跃 WorkTurn 的主 Agent 头像位置。
- `TurnPhase` 到 Thinking Orb 状态的确定性映射。
- 中文状态文字、ARIA 标签、减少动态效果和静态头像降级。
- 组件级、映射级、页面级和构建性能验证。

### 不包含

- 页面顶部运营大脑品牌头像。
- 右上角用户或账号头像。
- 专家 Agent 头像。
- 已结束的历史 WorkTurn 动画。
- 新增或修改后端阶段、SSE 事件、模型提示词和技术日志。
- 用动画推断或展示隐藏思维过程。

## 状态映射

现有 `TurnPhase` 是唯一业务阶段来源。前端不根据文案猜测状态。

| WorkTurn 状态 | TurnPhase | Orb 状态 | 用户可见文字 |
| --- | --- | --- | --- |
| `working` | `understanding` | `listening` | 正在理解你的需求 |
| `working` | `reading_data` | `searching` | 正在核对已导入的数据范围 |
| `working` | `consulting_experts` | `weaving` | 正在分析账号的主要问题 |
| `working` | `quality_review` | `solving` | 正在核验结论与数据依据 |
| `working` | `composing_artifact` | `composing` | 正在整理优先运营建议 |
| `working` | 阶段缺失或未知 | `working` | 使用现有活动文字或“正在分析账号情况” |
| 非活跃 | `waiting_approval` | 不显示 Orb | 等待你的确认 |
| 非活跃 | `completed` | 不显示 Orb | 已完成 |
| 非活跃 | `failed` | 不显示 Orb | 本次分析未完成 |
| 非活跃 | 任意暂停、阻塞或取消状态 | 不显示 Orb | 使用现有明确状态文字 |

`connecting`、`breathing` 和 `shaping` 暂不绑定业务阶段。只有后端以后提供明确的“重连”“空闲”或“方案塑形”状态时才启用，避免前端制造不存在的业务语义。

## 组件设计

### `MainAgentStatusAvatar`

新增一个专用于 WorkTurn 的状态头像组件，职责只有三个：

1. 接收 `active`、`phase` 和可访问状态文字。
2. 活跃时渲染 `ThinkingOrb`，非活跃时复用现有 `AgentAvatar`。
3. 组件加载或 Canvas 渲染条件不满足时回退为现有头像。

该组件不修改通用 `AgentAvatar`，因此顶部品牌区、全局入口和专家头像不会受到影响。

### `workTurnOrbState`

新增纯函数，将 `TurnPhase | undefined` 映射为 Thinking Orb 支持的状态。映射集中管理并独立测试，`WorkTurnCard` 不维护分散的条件判断。

### `WorkTurnCard`

只替换身份栏中当前的主 Agent 头像渲染点。现有 `aria-busy`、活动文字、进度步骤、成果和技术日志披露结构全部保留。

## 数据流

1. SSE 或历史接口更新 `ConversationTurn`。
2. `projectWorkTurn` 根据真实后端字段产生 `WorkTurnViewModel.status` 和 `phase`。
3. `presentWorkTurn` 继续产生中文活动文字。
4. `MainAgentStatusAvatar` 使用同一个 `status + phase` 决定是否显示 Orb 及其动画类型。
5. WorkTurn 进入非活跃状态后，同一 DOM 位置恢复静态运营大脑头像。

为了让映射拥有明确输入，`WorkTurnViewModel` 将显式保留可选 `phase` 字段；这只是前端投影字段，不改变服务端协议。

## 视觉与交互规则

- 64px 是组件库专门为聊天头像设计的规格，但通过现有头像容器限制最终布局尺寸，不能撑高消息行或造成跳动。
- 一个页面同一时间最多允许一个活跃 WorkTurn Orb。若异常数据出现多个运行回合，只为当前线程最后一个活跃回合显示动画，其余回退静态头像。
- Orb 与现有中文活动文字同时出现。禁止额外显示“思考中”或“正在思考”，避免重复表达。
- Orb 使用单色自动主题，颜色不自行扩展为红色、渐变或发光效果。
- 完成和失败不播放庆祝或错误动画，避免历史消息持续运动。

## 可访问性与性能

- 为 Orb 提供中文 `aria-label`，内容与当前活动文字一致。
- 保留 `role="status"` 和 `aria-live="polite"` 的文字播报；Orb 不创建第二个 live region。
- 依赖库自带 `prefers-reduced-motion` 静态帧、离屏暂停、隐藏标签页暂停以及 DPR 上限。
- 仅活动 WorkTurn 加载 Orb；历史 WorkTurn 使用现有图片头像。
- 前端 bundle 检查必须覆盖新增依赖，防止状态动画显著扩大主 Agent 首屏包。

## 错误与降级

- 阶段为空或新增未知阶段：使用 `working` Orb 和现有兜底活动文字。
- 非浏览器环境、测试环境或 Canvas 不可用：显示现有静态头像。
- 组件异常不得阻断 WorkTurn 的文字、进度、成果或操作按钮。
- 依赖加载失败时不展示空白头像，不改变工作回合高度。

## 测试与验收

### 映射测试

- 每个现有 `TurnPhase` 映射到唯一预期 Orb 状态。
- 未知或缺失阶段映射到 `working`。
- 等待、完成、失败、阻塞和取消状态不显示 Orb。

### 组件测试

- 活跃 WorkTurn 只出现一个 Thinking Orb 和一条状态文字。
- 状态变化时复用同一个 WorkTurn，不生成额外消息或气泡。
- 完成后 Orb 被静态运营大脑头像替代。
- 页面顶部品牌头像、右上角用户/账号头像和专家头像未改变。
- 减少动态效果和 Canvas 降级仍可识别运营大脑身份。

### 页面与性能验证

- 在 320px、768px、1024px 和 1440px 宽度验证头像对齐和布局稳定。
- 验证明暗主题、键盘导航、屏幕阅读器标签和隐藏标签页恢复。
- 运行前端单元测试、类型检查、构建、主 Agent bundle 检查和浏览器验收。

## 依赖与许可证

使用 npm 包 `thinking-orbs`。该库提供 React `ThinkingOrb` 组件、九种状态、20px/64px 两个专门调校的尺寸、自动主题、减少动态效果和离屏暂停能力，采用 MIT 许可证。

## 与 V4.1 质量评测计划的关系

Thinking Orbs 是独立的用户可见 UI 子项目，不修改已经确认的 V4.1 质量评测基线约束。实施顺序为：

1. 独立完成 Thinking Orbs 设计、计划、实现和前端验收。
2. 回到 `2026-08-10-main-agent-v4-1-quality-evaluation.md`，继续执行质量评测任务。

两部分使用独立提交，便于单独回滚和审查。
