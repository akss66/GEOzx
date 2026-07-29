# 运营大脑生产整改增量设计

> 日期：2026-07-29
>
> 基线：`2026-07-28-main-agent-v2-operating-loop-design.md`
>
> 状态：用户在生产验收后要求直接整改

## 1. 生产根因

生产验收确认前端仍保留两条发送路径：

- 普通消息在没有活动 ConversationThread 时调用旧 `/brain/messages`。
- 只有快捷能力调用新的 `/brain/conversations/{thread_id}/turns`。

旧路径继续复用 `BrainTask` 并进入完整 AI COO 图，因此普通消息、选题、脚本、
发布准备和数据复盘仍可能共享旧任务、生成未请求的策略并移动历史成果。新的 Turn
链路虽然解决了归属问题，但目前只完整注册“一键账号体检”，普通回答仍是固定话术，
其余核心运营能力没有独立 Skill 契约。

## 2. 本轮目标

本轮关闭用户入口的双运行时分流，所有新消息统一遵循：

```text
ConversationThread
  -> ConversationTurn
  -> AgentRun
  -> ANSWER / QUERY / SkillRun / Approval
  -> 本 Turn 的回复与成果
```

旧 `/brain/messages` 仅保留历史兼容和旧任务恢复，不再由运营大脑输入框创建新请求。

首批公开能力固定为：

- `account_data_query`：读取并解释账号数据，不创建策略或正式任务。
- `account_inspection`：一键账号体检。
- `topic_planning`：选题策划。
- `script_generation`：脚本生成。
- `publishing_preparation`：发布准备，创建待人工确认的发布准备记录，不实际发布。
- `performance_review`：数据复盘。

## 3. 运行规则

1. 每条用户消息只创建一个 Turn 和一个 AgentRun。
2. 新对话必须创建新的 Thread；不得通过清空本地 task id 模拟新对话。
3. 普通回答使用主模型，但只能说明 Registry 中真实公开的能力。
4. 数据查询的默认回复必须是业务摘要，原始 Tool JSON 只能保存在技术账本。
5. 正式专业成果只能由对应专家产生：
   - 选题、脚本：编导文案专家。
   - 发布准备、数据复盘：账号运营专家。
   - 账号体检：定位、编导、运营专家的固定复合图。
6. 上述 Skill 不进入策略图，因此“不要生成策略”由代码边界保证。
7. Skill 输出类型固定，不能用复盘报告替代脚本或发布包。
8. 发布准备只创建待人工确认的准备记录；没有审批不得创建外部发布动作。
9. 默认 UI 只显示业务摘要、阶段、专家和成果；Tool 原始结果、模型协议和内部字段
   只能出现在展开后的技术详情。
10. 账号、Thread、Turn、Run、SkillRun、Artifact 和审批来源必须全部一致。

## 4. 成果契约

| Skill | 专家 | 成果类型 | 必要字段 |
| --- | --- | --- | --- |
| account_inspection | 定位、编导、运营 | account_inspection_report | 周期、数据完整性、指标、问题、建议、证据 |
| topic_planning | 编导 | topic_plan | 主题、至少 3 个选题、受众、痛点、钩子、验证指标 |
| script_generation | 编导 | video_script | 标题、钩子、分段脚本、时长、行动引导、风险提示 |
| publishing_preparation | 运营 | publish_package | 3 个标题、5 个话题、封面文案、时间、检查清单、审批状态 |
| performance_review | 运营 | review_report | 数据周期、事实、推断、缺失数据、3 个问题、验证方案、证据 |

每个正式成果在来源 Turn 和成果中心引用同一个 artifact id。

## 5. 状态与失败

- ANSWER/QUERY 完成时：Turn 与 AgentRun 同时完成，不创建 BrainTask。
- Skill 完成时：SkillRun、AgentRun、BrainTask 和回复在同一收口步骤写入终态。
- 等待人工确认时：SkillRun/AgentRun 为 `waiting_permission`，BrainTask 为
  `pending_confirmation`，前端显示明确操作。
- 专家或工具失败时：本 Turn 收口为可操作的失败，不重新播放历史消息，不自动重跑
  已产生不确定副作用的步骤。
- 运行达到时限时：终止当前 Skill 并写入稳定错误码，不让 task 保持 running。

## 6. 前端

- 普通发送、快捷能力和重新生成都复用 Conversation API。
- 用户消息与回答从提交开始就使用同一个 Turn 布局，完成后不得跳位。
- SSE 的 `message_start/delta/done` 绑定 `thread_id + turn_id + client_message_id`。
- “新对话”创建并选中一个新的空 Thread。
- 技术日志显示本 Turn 的路由、Run、Skill、专家、工具、质量与耗时；默认折叠。
- 能力菜单只展示已发布且可执行的 Registry 项。

## 7. 验收场景

按同一账号连续执行：

1. “你能做什么”——模型回答，且只列出公开能力。
2. “读取最近30天数据，只查询”——可读摘要，无策略、无原始 JSON。
3. “一键账号体检，不生成30天策略”——只生成体检报告。
4. “给下周3个选题，不写脚本”——只生成选题成果。
5. “直接写60秒脚本”——只生成脚本成果。
6. “准备发布包，不实际发布”——生成发布包和待确认记录，无外部发布。
7. “复盘最近30天，区分事实、推断、缺失数据”——只生成复盘成果。
8. 切换账号——聊天、成果和执行详情不串号。
9. 新对话——旧记录保留，新消息进入新 Thread。

上述任一步都不得生成用户未请求的 30 天策略，不得把上一 Turn 的成果挂到当前 Turn。

